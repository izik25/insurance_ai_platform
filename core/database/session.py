"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import TypeEngine

from core.config.settings import get_settings
from core.database.models import Base
from core.exceptions import ConfigurationError


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    if not settings.database_url:
        raise ConfigurationError("DATABASE_URL is not set")
    return create_engine(settings.database_url)


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session, committing on success and rolling back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _add_missing_columns(engine: Engine) -> None:
    """`Base.metadata.create_all()` only creates whole tables that don't yet
    exist - it never ALTERs a table that's already there. Most new tables
    added by the taxonomy/matching upgrade are brand new (create_all()
    handles them fine), but a couple of columns get added, in later
    iterations, to tables that already exist (e.g. `document_matches` since
    Stage 4, or `document_classifications` itself once it's live and has
    real rows). There's no Alembic in this project by design (see
    PROJECT_OVERVIEW.md), so this is a small, idempotent, additive-only
    stand-in that runs across every mapped table: only ever adds a column
    that's missing, never drops/alters an existing one.
    """
    engine_inspector = inspect(engine)
    existing_tables = set(engine_inspector.get_table_names())
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all() just created it fresh, with every column.
            existing_columns = {c["name"] for c in engine_inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_type: TypeEngine = column.type.compile(dialect=engine.dialect)
                connection.execute(
                    text(
                        f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS '
                        f'"{column.name}" {column_type}'
                    )
                )


def init_db() -> None:
    """Create all tables that don't already exist, then additively patch in
    any new nullable columns on tables that did."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)

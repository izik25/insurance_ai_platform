"""Domain models — data shapes shared across every pipeline stage."""

from core.models.document import DocumentIdentity
from core.models.enums import DocumentType, ExtractionMethod

__all__ = ["DocumentIdentity", "DocumentType", "ExtractionMethod"]

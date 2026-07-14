"""Company plugin registry — discovers and looks up insurance-company plugins."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from core.exceptions import CompanyNotRegisteredError, DuplicateCompanyError
from core.plugins.base import CompanyPlugin
from core.utils.logging import get_logger

logger = get_logger(__name__)


class CompanyRegistry:
    """In-memory registry mapping company_id -> CompanyPlugin."""

    def __init__(self) -> None:
        self._plugins: dict[str, CompanyPlugin] = {}

    def register(self, plugin: CompanyPlugin) -> None:
        company_id = plugin.config.company_id
        if company_id in self._plugins:
            raise DuplicateCompanyError(f"Company '{company_id}' is already registered")
        self._plugins[company_id] = plugin
        logger.info("Registered company plugin: %s", company_id)

    def get(self, company_id: str) -> CompanyPlugin:
        try:
            return self._plugins[company_id]
        except KeyError as exc:
            raise CompanyNotRegisteredError(f"No plugin registered for '{company_id}'") from exc

    def list_companies(self) -> list[str]:
        return sorted(self._plugins)


def discover_plugins(registry: CompanyRegistry, package_name: str = "companies") -> None:
    """Import every submodule of `package_name` and let it self-register.

    Each company package is expected to expose a module-level `register(registry)`
    function that builds its CompanyPlugin and calls `registry.register(...)`.
    """
    package: ModuleType = importlib.import_module(package_name)
    for module_info in pkgutil.iter_modules(package.__path__, prefix=f"{package_name}."):
        module = importlib.import_module(module_info.name)
        register_fn = getattr(module, "register", None)
        if callable(register_fn):
            register_fn(registry)

"""Plugin contracts and registry for insurance-company modules.

Everything a company module (`companies/<name>/`) needs to conform to the
platform lives here. No company package may import from another company
package — only from `core`.
"""

from core.plugins.base import (
    BaseDownloader,
    BaseExtractor,
    BaseParser,
    BaseRules,
    CompanyConfig,
    CompanyPlugin,
)
from core.plugins.registry import CompanyRegistry, discover_plugins

__all__ = [
    "BaseDownloader",
    "BaseExtractor",
    "BaseParser",
    "BaseRules",
    "CompanyConfig",
    "CompanyPlugin",
    "CompanyRegistry",
    "discover_plugins",
]

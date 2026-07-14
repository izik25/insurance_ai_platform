"""Exception hierarchy shared across the platform.

Every custom exception in the codebase must derive from PlatformError so
callers can catch platform-specific failures with a single except clause.
"""

from __future__ import annotations


class PlatformError(Exception):
    """Base class for all platform-specific exceptions."""


class ConfigurationError(PlatformError):
    """Raised when application configuration is missing or invalid."""


class StorageError(PlatformError):
    """Raised when a storage backend operation fails."""


class CompanyNotRegisteredError(PlatformError):
    """Raised when looking up a company plugin that was never registered."""


class DuplicateCompanyError(PlatformError):
    """Raised when a company plugin is registered more than once."""

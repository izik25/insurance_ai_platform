"""File storage abstraction.

Pipeline code depends only on `StorageBackend`, never on a concrete
implementation — swapping `LocalFileStorage` for a future S3/Azure-backed
implementation (needed for the SaaS deployment) requires no caller changes.
"""

from core.storage.base import StorageBackend
from core.storage.local import LocalFileStorage

__all__ = ["StorageBackend", "LocalFileStorage"]

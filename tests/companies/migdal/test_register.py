from __future__ import annotations

from companies.migdal import register
from core.plugins.registry import CompanyRegistry


def test_register_wires_migdal_into_registry() -> None:
    registry = CompanyRegistry()

    register(registry)

    plugin = registry.get("migdal")
    assert plugin.config.display_name == "מגדל"
    assert registry.list_companies() == ["migdal"]

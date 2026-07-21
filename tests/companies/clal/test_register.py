from __future__ import annotations

from companies.clal import register
from core.plugins.registry import CompanyRegistry


def test_register_wires_clal_into_registry() -> None:
    registry = CompanyRegistry()

    register(registry)

    plugin = registry.get("clal")
    assert plugin.config.display_name == "כלל"
    assert registry.list_companies() == ["clal"]

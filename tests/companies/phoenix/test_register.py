from __future__ import annotations

from companies.phoenix import register
from core.plugins.registry import CompanyRegistry


def test_register_wires_phoenix_into_registry() -> None:
    registry = CompanyRegistry()

    register(registry)

    plugin = registry.get("phoenix")
    assert plugin.config.display_name == "הפניקס"
    assert registry.list_companies() == ["phoenix"]

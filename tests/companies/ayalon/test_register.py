from __future__ import annotations

from companies.ayalon import register
from core.plugins.registry import CompanyRegistry


def test_register_wires_ayalon_into_registry() -> None:
    registry = CompanyRegistry()

    register(registry)

    plugin = registry.get("ayalon")
    assert plugin.config.display_name == "איילון"
    assert registry.list_companies() == ["ayalon"]

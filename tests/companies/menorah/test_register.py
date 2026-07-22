from __future__ import annotations

from companies.menorah import register
from core.plugins.registry import CompanyRegistry


def test_register_wires_menorah_into_registry() -> None:
    registry = CompanyRegistry()

    register(registry)

    plugin = registry.get("menorah")
    assert plugin.config.display_name == "מנורה מבטחים"
    assert registry.list_companies() == ["menorah"]

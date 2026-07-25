from __future__ import annotations

from companies.hachshara import register
from core.plugins.registry import CompanyRegistry


def test_register_wires_hachshara_into_registry() -> None:
    registry = CompanyRegistry()

    register(registry)

    plugin = registry.get("hachshara")
    assert plugin.config.display_name == 'הכשרה חברה לביטוח בע"מ'
    assert registry.list_companies() == ["hachshara"]

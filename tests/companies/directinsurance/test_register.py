from __future__ import annotations

from companies.directinsurance import register
from core.plugins.registry import CompanyRegistry


def test_register_wires_directinsurance_into_registry() -> None:
    registry = CompanyRegistry()

    register(registry)

    plugin = registry.get("directinsurance")
    assert plugin.config.display_name == "ביטוח ישיר"
    assert registry.list_companies() == ["directinsurance"]

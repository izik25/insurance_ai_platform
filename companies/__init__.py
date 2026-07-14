"""Insurance-company plugins.

Every subpackage here is one independent insurance company. Each must
expose a module-level `register(registry: CompanyRegistry)` function (see
`core.plugins.registry.discover_plugins`). No subpackage may import from
another — only from `core`. `template_company` (Stage 2) is the reference
implementation to copy when adding a new company.
"""

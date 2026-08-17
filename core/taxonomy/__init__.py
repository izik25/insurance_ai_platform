"""Insurance coverage taxonomy: main_category -> coverage_family -> coverage_subtype -> coverage_variant.

Config-driven (core/taxonomy/data/taxonomy.v*.yaml), mirroring the
CompanyRegistry pattern in core/plugins/registry.py - a versioned, human-
edited data file loaded once per process, not a database table.
"""

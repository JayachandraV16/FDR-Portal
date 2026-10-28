"""Read-only views derived from existing records: release state, alerts, exceptions and report aggregates.

Nothing in this package writes to the database or stores anything. Every number it returns is computed
on request from the authoritative tables, restricted to what the caller may see, and reports whether it
looked at everything (`complete`/`exact`) or only a bounded part.
"""

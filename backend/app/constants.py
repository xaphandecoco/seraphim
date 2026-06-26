"""Application-wide constants for Project Seraphim.

This module holds simple, import-time constants that multiple modules may need
without triggering circular imports.  It must not import from any other app.*
module.
"""

# Names of custom_field_def entries whose non-empty value indicates that a
# contact is "connected" to a community group.  The S23 nightly recompute job
# iterates contacts.custom_data and marks is_connected=True when any of these
# field names carries a non-empty / non-null value.
DEFAULT_CONNECTED_FIELD_NAMES: frozenset[str] = frozenset(
    {"community_leader", "community"}
)

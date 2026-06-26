"""Migration services package for CiviCRM import pipeline.

Sub-modules
-----------
normalize  -- pure sync helpers: normalize_option, split_multi, coerce, name_key
reader     -- async XLSX row iterator (openpyxl, thread-backed)
mapper     -- dataclasses + row-mapping functions (contact / event / participant)

Circular-import rule (CN-25): no sub-module in this package may import from
any router module.  DB calls are prohibited in normalize and mapper.
"""

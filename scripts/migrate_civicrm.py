#!/usr/bin/env python3
"""CLI for CiviCRM -> Seraphim spreadsheet migration.

Usage
-----
python scripts/migrate_civicrm.py \\
    --entity contacts \\
    --file path/to/contacts.xlsx \\
    --map config/civicrm_contacts.map.example.yml \\
    [--dry-run] \\
    [--by-user-email admin@example.com] \\
    [--strict]

Entity choices: contacts, events, participants, links

The --map YAML file must have top-level keys:
  column_map:  {source_header: {target, data_type, ...}}
  options:     {date_format, default_event_type, ...}

The mode field written to ImportBatch.mode is derived from --dry-run:
  --dry-run present -> mode = "dry"
  otherwise         -> mode = "live"

Exit codes
----------
0  Success (or dry-run completed without errors).
1  --strict is set and batch.error_count > 0, or a fatal exception occurred.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import warnings
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure app.* imports resolve when running from the repo root.
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

import yaml  # noqa: E402 — must come after sys.path manipulation


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate_civicrm.py",
        description="Import CiviCRM spreadsheet data into Seraphim.",
    )
    parser.add_argument(
        "--entity",
        required=True,
        choices=["contacts", "events", "participants", "links"],
        help="Which entity type to import.",
    )
    parser.add_argument(
        "--file",
        required=True,
        metavar="XLSX_PATH",
        help="Path to the source XLSX file.",
    )
    parser.add_argument(
        "--map",
        required=True,
        metavar="MAP_YAML",
        help="Path to the column-map YAML file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Parse and validate rows but roll back all core table writes. "
            "ImportBatch + ImportRowResult rows are still committed so the "
            "batch remains UI-inspectable."
        ),
    )
    parser.add_argument(
        "--by-user-email",
        metavar="EMAIL",
        default=None,
        help=(
            "Email address of the admin user to record as created_by. "
            "If not found in the database a warning is printed and "
            "created_by_id is left as NULL."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit with code 1 if the batch contains any row-level errors.",
    )
    return parser


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def _load_map(map_path: str) -> tuple[dict, dict]:
    """Load column_map and options from a YAML map file.

    Returns
    -------
    (column_map, options)
    """
    path = Path(map_path)
    if not path.exists():
        print(f"ERROR: map file not found: {map_path}", file=sys.stderr)
        sys.exit(1)

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        print(f"ERROR: map file {map_path!r} must be a YAML mapping.", file=sys.stderr)
        sys.exit(1)

    column_map: dict = data.get("column_map") or {}
    options: dict = data.get("options") or {}

    if not column_map:
        print(
            f"WARNING: column_map in {map_path!r} is empty — no columns will be mapped.",
            file=sys.stderr,
        )

    return column_map, options


# ---------------------------------------------------------------------------
# User resolver
# ---------------------------------------------------------------------------

async def _resolve_user_id(email: str, db_factory) -> int | None:
    """Resolve an email to User.id.  Warns (does not crash) if not found."""
    from sqlalchemy import select
    from app.models import User

    async with db_factory() as db:
        result = await db.execute(select(User.id).where(User.email == email))
        row = result.one_or_none()

    if row is None:
        warnings.warn(
            f"User with email {email!r} not found in the database. "
            "created_by_id will be set to NULL.",
            stacklevel=2,
        )
        return None

    return row[0]


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def _print_summary(
    entity: str,
    mode: str,
    batch_id: int,
    counts: dict,
    status: str,
) -> None:
    """Print a structured summary table after run_phase completes."""
    sep = "-" * 60
    print(sep)
    print("  Migration Summary")
    print(sep)
    print(f"  {'Entity':<20}: {entity}")
    print(f"  {'Mode':<20}: {mode}")
    print(f"  {'Batch ID':<20}: {batch_id}")
    print(f"  {'Status':<20}: {status}")
    print(sep)
    print(f"  {'Total rows':<20}: {counts.get('total_rows', 0)}")
    print(f"  {'Created':<20}: {counts.get('created_count', 0)}")
    print(f"  {'Updated':<20}: {counts.get('updated_count', 0)}")
    print(f"  {'Skipped':<20}: {counts.get('skipped_count', 0)}")
    print(f"  {'Errors':<20}: {counts.get('error_count', 0)}")
    print(f"  {'Review':<20}: {counts.get('review_count', 0)}")
    print(sep)


# ---------------------------------------------------------------------------
# Async main
# ---------------------------------------------------------------------------

async def _async_main(args: argparse.Namespace) -> int:
    """Core async logic.  Returns exit code (0 = success, 1 = error)."""

    # 1. Load YAML map
    column_map, options = _load_map(args.map)

    # 2. Derive mode from --dry-run
    mode = "dry" if args.dry_run else "live"
    options = dict(options)  # copy so we don't mutate the parsed YAML
    options["mode"] = mode

    # 3. Import app modules (after sys.path is patched)
    from app.database import async_session
    from app.services.migration.runner import run_phase

    # 4. Resolve --by-user-email to User.id
    created_by_id: int | None = None
    if args.by_user_email:
        created_by_id = await _resolve_user_id(args.by_user_email, async_session)

    # 5. Run the phase
    xlsx_path = str(Path(args.file).resolve())
    print(
        f"Starting migration: entity={args.entity!r}  file={xlsx_path!r}  "
        f"mode={mode!r}  created_by_id={created_by_id}"
    )

    batch_id = await run_phase(
        phase=args.entity,
        xlsx_path=xlsx_path,
        column_map=column_map,
        options=options,
        dry_run=args.dry_run,
        created_by_id=created_by_id,
        db_factory=async_session,
    )

    # 6. Fetch batch summary counts from DB for the summary table
    from sqlalchemy import select
    from app.models import ImportBatch

    async with async_session() as db:
        result = await db.execute(
            select(ImportBatch).where(ImportBatch.id == batch_id)
        )
        batch = result.scalar_one_or_none()

    if batch is None:
        print(f"ERROR: batch_id={batch_id} not found after run_phase.", file=sys.stderr)
        return 1

    counts = {
        "total_rows": batch.total_rows or 0,
        "created_count": batch.created_count or 0,
        "updated_count": batch.updated_count or 0,
        "skipped_count": batch.skipped_count or 0,
        "error_count": batch.error_count or 0,
        "review_count": batch.review_count or 0,
    }

    _print_summary(
        entity=args.entity,
        mode=mode,
        batch_id=batch_id,
        counts=counts,
        status=batch.status,
    )

    # 7. --strict: exit 1 if any row errors
    if args.strict and counts["error_count"] > 0:
        print(
            f"STRICT mode: {counts['error_count']} row error(s) detected — exiting with code 1.",
            file=sys.stderr,
        )
        return 1

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(_async_main(args))
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

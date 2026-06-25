#!/usr/bin/env python
"""Consent backfill script for FR Transition.

Run from the backend/ directory:

    python scripts/backfill_consent.py

The script:
1. Calls remap_subjects() to ensure subjects are linked to Contact rows.
2. Calls backfill_consent() to create BiometricConsent stubs for active
   remapped subjects that have no consent record, and upserts the
   'enroll_without_consent' AdminSetting to {'value': True}.
3. Prints a summary of both operations to stdout.

No live CompreFace or Claude API calls are made.
"""

import asyncio
import sys

# Ensure the backend app is importable when invoked as
# `python scripts/backfill_consent.py` from backend/.
import os

# Prepend the backend directory (parent of scripts/) so `app.*` imports work.
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.database import async_session  # noqa: E402 — path manipulation must come first
from app.services.fr_transition import ConsentBackfillReport, RemapReport, backfill_consent, remap_subjects  # noqa: E402


async def main() -> None:
    async with async_session() as db:
        print("=== FR Transition Backfill ===")
        print()

        print("Step 1: Remapping subjects …")
        remap: RemapReport = await remap_subjects(db)
        print(f"  subjects_remapped        : {remap['subjects_remapped']}")
        print(f"  subjects_orphaned        : {remap['subjects_orphaned']}")
        print(f"  detections_event_remapped: {remap['detections_event_remapped']}")
        print(f"  detections_name_remapped : {remap['detections_name_remapped']}")
        if remap["orphaned_subject_ids"]:
            print(f"  orphaned_subject_ids     : {remap['orphaned_subject_ids']}")
        print()

        print("Step 2: Backfilling consent …")
        consent: ConsentBackfillReport = await backfill_consent(db)
        print(f"  consent_rows_created     : {consent['consent_rows_created']}")
        print(f"  already_had_consent      : {consent['already_had_consent']}")
        print(f"  enroll_without_consent_set: {consent['enroll_without_consent_set']}")
        print()

        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())

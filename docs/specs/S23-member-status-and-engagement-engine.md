# S23 — Member Status & Engagement Engine
**Phase:** C — Face-native (PRIORITY) · **Depends on:** S01 (schema inversion: `contacts` table + derived snapshot columns C2; `participants` table; `events` table; `audit_log`), S03 (contact CRUD + `ContactDetailPage` badge slot + `StatusBadge` primitive), S04 (`events.session_type`/`service_time` + recurring series, so that `events.start_at` is authoritative), S05 (bulk participants at scale so the recompute has real data to read), S16 (APScheduler host + `job_runs` table + `scheduler.py`) · **Effort:** M · **Status:** Not started

> **S16 spec not yet on disk (2026-06-21 status):** S16 is defined in `00-MASTER.md` §3 but its spec file has not been written. S23 appends two APScheduler jobs to S16's `backend/app/services/scheduler.py`. If S23 merges before S16, place the job-registration block in `backend/app/main.py` behind a `HAS_SCHEDULER` guard constant and the `job_runs` INSERT guarded by a `has_table` check; move both to `scheduler.py` when S16 lands. The on-demand endpoint and all snapshot-column logic are fully independent of S16.
>
> **Snapshot column ownership (00-MASTER ruling C2):** All seven derived snapshot columns (`last_attended_at`, `attendance_count`, `weeks_absent`, `tier`, `is_active`, `is_regular`, `is_connected`) are **created by S01** (all nullable, no default, no backfill). S23 is their **sole writer** at runtime. S03 only reads them. If S01 has not yet been patched for C2 (likely at S23 implementation time), S23 ships a guard migration that adds any missing columns idempotently (§3.2).

---

## 1. Goal & rationale

Light North Caloocan tracks ~1,440 contacts and ~33.6k attendance rows. The pastoral team makes week-to-week decisions about follow-up, outreach priority, and community health by answering: "Who has been absent more than 4 weeks? Who is a Regular Attendee? Who belongs to a cell group?" Today those answers live in CiviCRM custom-field data and a manually-maintained Google Sheet updated by the n8n `CiviCRM Scheduler/Updater` workflow (`fKkPUolayRyZrjao`) — specifically the `Rescan EOW` (Monday 00:00) and `Rescan EOM` (monthly) cron triggers.

S23 replaces that n8n cron and gives every part of the new CRM a single, trusted, pre-computed status snapshot so that:

- **Reports (S14)** can count "Tier 1 + Connected" contacts in one indexed scan, not a correlated 33.6k-row join.
- **Advanced search (S09)** can filter `is_active = true` or `tier = 'tier2'` as regular indexed column predicates.
- **Contact profiles (S03)** show `Tier 2 — Active · Regular · Connected` badges that are always current after a recompute.
- **Automation rules (S17)** can fire on `tier` transitions without an expensive live subquery.
- **Dashboard tiles (S14 §C "Status")** are a fast `GROUP BY tier` aggregate, not a correlated subquery per contact.

Canonical definitions (from `docs/crm-research/reports.md §B`, confirmed in `decisions.md` Rounds 7–8, and `n8n-analysis.md` §3):

| Derived field | Definition | Algorithm |
|---|---|---|
| `last_attended_at` | Max `events.start_at` WHERE `participants.status = 'attended'` AND `participants.contact_id = ?` | `MAX(events.start_at)` JOIN via `participants` |
| `attendance_count` | Lifetime count of `participants` rows WHERE `status = 'attended'` AND `contact_id = ?` | `COUNT(p.id)` |
| `weeks_absent` | `floor((today_midnight - last_attended_at).days / 7)` in integer weeks; `NULL` if never attended | Integer floor division; `today_midnight` = start of today in naive UTC |
| `tier` | Bucketed from `weeks_absent`: `tier0`=0, `tier1`=1–4, `tier2`=5–8, `tier3`=9–12, `inactive`=13+ | Pure Python CASE — no SQL dialect dependency |
| `is_active` | `tier IN ('tier0', 'tier1', 'tier2', 'tier3')` — attended within 12 weeks | Derived from tier; NULL when tier is NULL |
| `is_regular` | `attendance_count > 9` | Boolean comparison; False when count=0 |
| `is_connected` | Contact has a non-null value in any `custom_data` key whose `custom_field_def.name` is in the configured `connected_field_names` set (default: `community_leader`, `community`) | JSONB key-existence check (dialect-aware) |

**n8n cron cadences to replicate (from `n8n-analysis.md` §3, workflow `fKkPUolayRyZrjao`):**
- `Rescan EOW`: every Monday at 00:00 — full recompute for all contacts.
- `Rescan EOM`: monthly at midnight first of month — same full recompute (idempotent with EOW; needed for monthly dashboard freeze point).

The on-demand endpoint lets an admin trigger an immediate recompute at any time (post data import, post migration, post merge).

---

## 2. Scope

### In scope

- **APScheduler nightly jobs** — `recompute_member_status_eow` (Monday 00:00 UTC) and `recompute_member_status_eom` (1st of month 00:00 UTC) registered in S16's `backend/app/services/scheduler.py`. Each writes a `job_runs` row.
- **On-demand recompute endpoint** `POST /analytics/recompute-member-status` (admin-only). Accepts optional `{"contact_ids": [int, ...]}` body (max 500) for partial recompute, or omit body for full recompute. Returns `{contact_count, duration_ms, job_run_id}`. Synchronous (returns when done — < 5s at 1,440 contacts).
- **Member status summary endpoint** `GET /analytics/member-status-summary` (admin + volunteer; widened to viewer post-S15). Returns fast `GROUP BY` aggregate: tier counts, active/inactive/regular/connected counts, `last_recomputed_at`.
- **Recompute service** `backend/app/services/member_status_service.py` — all computation logic, no side effects except a single bulk UPDATE + `job_runs` + `audit_log`.
- **Guard migration** — adds the seven snapshot columns to `contacts` if absent; adds five indexes; idempotent whether S01 C2 patch has run or not.
- **`CONNECTED_FIELD_NAMES` constant** in `backend/app/constants.py` — the default set of `custom_field_def.name` values that mark a contact as Connected; configurable via `admin_settings` key `connected_field_names`.
- **`admin_settings` keys** `connected_field_names` (JSONB list) and `tier_boundaries_weeks` (JSONB list; transparency only — code uses these as fallback defaults) — no schema change.
- **Frontend badge enablement** — no new files; S03's `StatusBadge` badge slot lights up with real values. S23 defines exact label strings and Tailwind token colors for the badge component to implement.
- **S09 field registry additions** — documents (and adds if hard-coded) the seven derived fields to the advanced search field registry.
- **Audit log entry** per full recompute (`action='member_status.recompute'`, `actor_id=None` for system actor) via the shared `app/services/audit.py` `record(...)` helper (owner: S02/S01).
- **`job_runs` write** per full recompute (guarded by `has_table('job_runs')` if S16 not yet landed).
- Backend pytest coverage: full recompute path, partial path, all tier-boundary values, connected/regular edge cases, `job_runs` guard, endpoint auth, idempotency.
- Frontend vitest: `StatusBadge` label rendering, null/undefined graceful handling.

### Out of scope (explicit)

- **Real-time or live-computed tier columns** — snapshot-only by design.
- **Automation rule trigger on tier change** — S17 detects changes and fires rules; S23 only writes the snapshot.
- **Community Leader count report tile** ("44 leaders with CGs") — S14 dashboard tile; S23 computes `is_connected`, S14 counts it.
- **Sending notifications on tier change** — S18/S17.
- **SSE/websocket push when recompute finishes** — endpoint is synchronous.
- **Materialized views or separate tier-distribution tables** — `GROUP BY tier` on 1,440 contacts is sub-millisecond.
- **Per-contact tier change history table** — one `audit_log` aggregate entry per run; no per-contact before/after.
- **Configuring tier week-boundaries at runtime via UI** — `admin_settings` key seeds the values for transparency; v1 code reads the constant as fallback; a settings UI slider is deferred.
- **Household/organization tier derivation** — `contact_type != 'individual'` contacts get NULL across all derived columns.
- **`recompute_all` batched chunking at scale (> 5k contacts)** — deferred; noted in §10.

---

## 3. Data model changes

### 3.1 Context: derived snapshot columns (C2 ruling)

Per `00-MASTER.md` §2.1 ruling C2, the seven derived columns belong to `contacts` and are **created by S01**. S23 is their sole runtime writer. The canonical definitions:

| Column | SQLAlchemy type | Nullable | Default | Notes |
|---|---|---|---|---|
| `last_attended_at` | `DateTime` | yes | `NULL` | Naive UTC. `MAX(events.start_at)` for attended participants. |
| `attendance_count` | `Integer` | yes | `NULL` | Lifetime attended participant rows. 0 is a valid backfilled value. |
| `weeks_absent` | `Integer` | yes | `NULL` | Floor-divided integer. NULL = never attended. |
| `tier` | `String(10)` | yes | `NULL` | Enum values: `'tier0'`, `'tier1'`, `'tier2'`, `'tier3'`, `'inactive'`. NULL = never attended. |
| `is_active` | `Boolean` | yes | `NULL` | True if tier in (tier0–tier3). NULL when tier is NULL. |
| `is_regular` | `Boolean` | yes | `NULL` | True if `attendance_count > 9`. False (not NULL) when count = 0. |
| `is_connected` | `Boolean` | yes | `NULL` | True if contact has a non-null `custom_data` value for any configured connected field. |

**`backend/app/models.py` additions** (add to `Contact` model post-S01; no-op if already present):

```python
# backend/app/models.py — Contact model (post-S01 rename from CiviCRMMember)
# Derived snapshot columns — written ONLY by the S23 recompute job
last_attended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
attendance_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
weeks_absent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
tier: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
is_active: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
is_regular: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
is_connected: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
```

### 3.2 Guard migration (S23 migration — idempotent add)

**File:** `backend/alembic/versions/<real_revision_id>_s23_member_status_snapshot_guard.py`

`down_revision` = real `alembic heads` at implementation time (run `alembic heads` to resolve; do not invent ids).

**Upgrade** — uses `sa.inspect(op.get_bind())` to check for existing columns and indexes; adds only those that are absent. No `server_default`. No backfill. No table rewrite.

```python
import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {col["name"] for col in inspector.get_columns("contacts")}

    column_defs = [
        ("last_attended_at", sa.DateTime(),    True),
        ("attendance_count",  sa.Integer(),     True),
        ("weeks_absent",      sa.Integer(),     True),
        ("tier",              sa.String(10),    True),
        ("is_active",         sa.Boolean(),     True),
        ("is_regular",        sa.Boolean(),     True),
        ("is_connected",      sa.Boolean(),     True),
    ]
    for col_name, col_type, nullable in column_defs:
        if col_name not in existing_cols:
            op.add_column("contacts", sa.Column(col_name, col_type, nullable=nullable))

    # Indexes — guard with try/except on Postgres; on SQLite these are always created
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("contacts")}

    index_specs = [
        ("ix_contacts_tier",             ["tier"]),
        ("ix_contacts_is_active",        ["is_active"]),
        ("ix_contacts_is_regular",       ["is_regular"]),
        ("ix_contacts_is_connected",     ["is_connected"]),
        ("ix_contacts_last_attended_at", ["last_attended_at"]),
    ]
    for idx_name, cols in index_specs:
        if idx_name not in existing_indexes:
            op.create_index(idx_name, "contacts", cols)


def downgrade() -> None:
    # Drop indexes only — columns are left in place (dropping columns is destructive
    # and not required for rollback; NULL snapshot columns are safe).
    for idx_name in [
        "ix_contacts_last_attended_at",
        "ix_contacts_is_connected",
        "ix_contacts_is_regular",
        "ix_contacts_is_active",
        "ix_contacts_tier",
    ]:
        op.drop_index(idx_name, table_name="contacts", if_exists=True)
```

**Indexes added by this migration:**

| Index name | Table | Column(s) | Rationale |
|---|---|---|---|
| `ix_contacts_tier` | `contacts` | `tier` | S14 `GROUP BY tier`; S09 `tier = ?` filter |
| `ix_contacts_is_active` | `contacts` | `is_active` | S14 Active/Inactive counts |
| `ix_contacts_is_regular` | `contacts` | `is_regular` | S14 Regular/Non-Regular counts |
| `ix_contacts_is_connected` | `contacts` | `is_connected` | S14 Connected counts |
| `ix_contacts_last_attended_at` | `contacts` | `last_attended_at` | S09 recency filter; recompute sort |

### 3.3 No new tables

S23 creates **no new tables**. It writes into `contacts` (existing post-S01) and `job_runs` (S16-owned, guarded). `audit_log` (S01/S02-owned) receives system-actor entries.

### 3.4 `admin_settings` keys (no schema change)

S23 reads two optional keys from the existing `admin_settings` table:

| Key | Default (JSON) | Description |
|---|---|---|
| `connected_field_names` | `["community_leader", "community"]` | List of `custom_field_def.name` values whose non-null `custom_data` value marks a contact as Connected. Admin can extend without a code deploy. |
| `tier_boundaries_weeks` | `[0, 4, 8, 12]` | Upper bounds (inclusive) for tier0, tier1, tier2, tier3 respectively; `weeks_absent > 12` = inactive. Seeded for transparency; code reads these but also embeds the same values as constants. |

---

## 4. Backend

### 4.1 Endpoints

| METHOD | Path | Role | Request body | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/analytics/recompute-member-status` | admin | `{"contact_ids": [int, ...] \| null}` (optional; omit or `null` = full recompute) | `{"contact_count": int, "duration_ms": int, "job_run_id": int \| null}` | Synchronous. Full recompute at 1,440 contacts < 5s. Partial < 200ms. |
| `GET` | `/analytics/member-status-summary` | admin, volunteer (widen to viewer post-S15 ruling C20) | — | `{"tier_counts": {"tier0":n,...,"inactive":n,"null":n}, "is_active_count": int, "is_inactive_count": int, "is_regular_count": int, "is_connected_count": int, "total_contacts": int, "last_recomputed_at": str \| null}` | Fast `GROUP BY` on contacts. Used by S14 dashboard tiles. `last_recomputed_at` from `job_runs`; null if S16 not yet landed. |

Both endpoints are added to **`backend/app/routers/analytics.py`** (existing file, post-S01 refactored to import `Contact`, `Participant`, `Event` instead of CiviCRM models).

### 4.2 Service: `backend/app/services/member_status_service.py`

**Create this new file.** All functions are `async`, all public functions are type-hinted.

#### Data structures

```python
# backend/app/services/member_status_service.py
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update as sa_update, bindparam
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminSetting, Contact, JobRun, Participant, Event
from app.utils.db_helpers import has_table  # helper checked below
from app.constants import DEFAULT_CONNECTED_FIELD_NAMES

logger = logging.getLogger(__name__)


@dataclass
class ContactSnapshot:
    contact_id: int
    last_attended_at: Optional[datetime]
    attendance_count: int
    weeks_absent: Optional[int]
    tier: Optional[str]
    is_active: Optional[bool]
    is_regular: bool
    is_connected: bool


@dataclass
class RecomputeResult:
    contact_count: int
    duration_ms: int
    job_run_id: Optional[int]
```

#### Pure helper: `_tier_from_weeks_absent`

```python
def _tier_from_weeks_absent(weeks: Optional[int]) -> Optional[str]:
    """Deterministic tier bucketing. Returns None if contact has never attended.

    Boundaries (from reports.md §B, decisions.md Round 7):
      weeks == 0           → tier0   (attended this week)
      1 <= weeks <= 4      → tier1
      5 <= weeks <= 8      → tier2
      9 <= weeks <= 12     → tier3
      weeks >= 13          → inactive
    """
    if weeks is None:
        return None
    if weeks == 0:
        return "tier0"
    if weeks <= 4:
        return "tier1"
    if weeks <= 8:
        return "tier2"
    if weeks <= 12:
        return "tier3"
    return "inactive"
```

**Edge cases for `_tier_from_weeks_absent`:**
- `weeks = 0` → `tier0` — `last_attended_at` is within 0–6 days ago (i.e., `floor(days/7) == 0`).
- `weeks = 12` → `tier3` (still active). `weeks = 13` → `inactive` (12+ weeks boundary = 13 full weeks absent, meaning last attended ≥ 91 days ago).
- NULL `last_attended_at` → `weeks_absent = None` → `tier = None` → `is_active = None`.
- Contact with `attendance_count = 0` and no rows in `participants` → treated identically to NULL (never attended): `weeks_absent=NULL`, `tier=NULL`, `is_active=NULL`, `is_regular=False` (per CN-21). `is_connected` is computed independently of attendance.
- **UI rendering (per CN-21):** a NULL `tier` is rendered as **"Unrated"** in any contact-facing surface (e.g. S03 contact detail/list, S09 filters). S09's `tier IS NULL` filter finds these unrated contacts.
- `today_midnight` is computed once per recompute call as `datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)` — prevents drift if a recompute straddles midnight.

#### Helper: `_connected_field_names`

```python
async def _connected_field_names(db: AsyncSession) -> list[str]:
    """
    Returns the list of custom_field_def.name values that mark a contact as Connected.
    Reads admin_settings key 'connected_field_names'; falls back to DEFAULT_CONNECTED_FIELD_NAMES.
    """
    row = await db.get(AdminSetting, "connected_field_names")
    if row and isinstance(row.value, list) and row.value:
        return list(row.value)
    return list(DEFAULT_CONNECTED_FIELD_NAMES)
```

`backend/app/constants.py` (create or extend — do not duplicate if file already exists):

```python
# backend/app/constants.py
DEFAULT_CONNECTED_FIELD_NAMES: frozenset[str] = frozenset({"community_leader", "community"})
```

#### Core computation: `compute_snapshot_for_contacts`

Read-only (no DB writes). Returns a `dict[int, ContactSnapshot]` keyed by `contact_id`. Used internally by both recompute variants and directly in tests.

```python
async def compute_snapshot_for_contacts(
    db: AsyncSession,
    contact_ids: Optional[list[int]],
    today_midnight: Optional[datetime] = None,
) -> dict[int, ContactSnapshot]:
    """
    Compute derived snapshot fields for a set of contacts (or all if contact_ids is None).
    Pure computation: no DB writes.

    Args:
        db: async session (read-only; caller commits nothing here).
        contact_ids: if None, compute for ALL non-deleted individual contacts.
        today_midnight: injected by callers / tests for determinism; defaults to now.
    Returns:
        dict mapping contact_id → ContactSnapshot.
    """
```

**Algorithm:**

**Step 1 — Build contact target set.**

```python
if contact_ids is not None:
    target_filter = Contact.id.in_(contact_ids)
    all_ids: list[int] = list(contact_ids)
else:
    id_rows = await db.execute(
        select(Contact.id)
        .where(Contact.is_deleted == False, Contact.contact_type == "individual")
    )
    all_ids = [r[0] for r in id_rows.all()]
    target_filter = Contact.id.in_(all_ids)
```

**Step 2 — Attendance aggregate (one query for all targets).**

```python
agg_stmt = (
    select(
        Participant.contact_id,
        func.max(Event.start_at).label("last_attended_at"),
        func.count(Participant.id).label("attendance_count"),
    )
    .join(Event, Event.id == Participant.event_id)
    .where(
        Participant.status == "attended",
        Participant.contact_id.in_(all_ids),
    )
    .group_by(Participant.contact_id)
)
agg_rows = await db.execute(agg_stmt)
agg_by_contact: dict[int, tuple[Optional[datetime], int]] = {
    row.contact_id: (row.last_attended_at, row.attendance_count)
    for row in agg_rows.all()
}
```

This single aggregate covers all four attendance sources (face/manual/zoom/community_report) because all write to the `participants` table with `status='attended'`.

**Step 3 — Connected check (dialect-aware).**

The `is_connected` flag requires checking whether `contacts.custom_data` contains a non-null value for any key in `connected_field_names`. Implementation is split by dialect:

- **PostgreSQL (prod):** Use native JSONB `has_key` operator (`Contact.custom_data.has_key(field_name)`) for each field_name, OR-combined. This is purely an existence check; a null JSON value (`{"community": null}`) does NOT mark a contact as connected — use `Contact.custom_data[field_name].as_string() != None` check, or load the full dict and check in Python.
- **SQLite (tests):** The `has_key` JSONB operator is unavailable. Load `Contact.custom_data` as a dict via Python after fetching the contact row.

**Recommended implementation (works on both dialects):** fetch `custom_data` for all target contacts in one query and check in Python:

```python
field_names = await _connected_field_names(db)
cd_rows = await db.execute(
    select(Contact.id, Contact.custom_data)
    .where(target_filter)
)
connected_ids: set[int] = set()
for contact_id, custom_data in cd_rows.all():
    if custom_data:
        for fn in field_names:
            val = custom_data.get(fn)
            # Non-null, non-empty value means connected
            if val is not None and val != "" and val != [] and val != 0:
                connected_ids.add(contact_id)
                break
```

Note: a value of `0` (integer zero) is treated as not-connected because `contact_reference` fields store app-minted integer contact IDs, and `0` is not a valid contact id. A list of `[]` (empty multiselect) is not connected.

**Step 4 — Build snapshots in Python.**

```python
if today_midnight is None:
    today_midnight = datetime.now(timezone.utc).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0
    )

snapshots: dict[int, ContactSnapshot] = {}
for cid in all_ids:
    last_attended_at, count = agg_by_contact.get(cid, (None, 0))

    if last_attended_at is not None:
        days = (today_midnight - last_attended_at).days
        weeks_absent = days // 7
    else:
        weeks_absent = None

    tier = _tier_from_weeks_absent(weeks_absent)
    is_active = (tier is not None) or None  # None when tier is None
    # Correct: is_active = None if tier is None else True
    is_active_val: Optional[bool] = None if tier is None else True

    snapshots[cid] = ContactSnapshot(
        contact_id=cid,
        last_attended_at=last_attended_at,
        attendance_count=count,
        weeks_absent=weeks_absent,
        tier=tier,
        is_active=is_active_val,
        is_regular=(count > 9),
        is_connected=(cid in connected_ids),
    )

return snapshots
```

**Performance:** At 1,440 contacts + 33.6k participants, Steps 2 and 3 each require one DB round-trip. The attendance aggregate query uses `ix_participants_contact_id` (S05-owned index) to scan efficiently by contact. Python-side loop over 1,440 items is O(n) and sub-millisecond.

#### Full recompute: `recompute_all`

```python
async def recompute_all(db: AsyncSession) -> RecomputeResult:
    """
    Compute derived snapshot fields for ALL non-deleted individual contacts
    and write them in a single set-based bulk UPDATE.
    Writes job_runs (guarded) and audit_log entries.
    """
    started_at = _utc_now()
    t0 = _monotonic_ms()

    snapshots = await compute_snapshot_for_contacts(db, contact_ids=None)
    contact_count = len(snapshots)

    # Bulk UPDATE via SQLAlchemy Core executemany (one round-trip at 1,440 rows)
    if snapshots:
        stmt = (
            sa_update(Contact)
            .where(Contact.id == bindparam("_id"))
            .values(
                last_attended_at=bindparam("last_attended_at"),
                attendance_count=bindparam("attendance_count"),
                weeks_absent=bindparam("weeks_absent"),
                tier=bindparam("tier"),
                is_active=bindparam("is_active"),
                is_regular=bindparam("is_regular"),
                is_connected=bindparam("is_connected"),
            )
            .execution_options(synchronize_session=False)
        )
        rows = [
            {
                "_id": snap.contact_id,
                "last_attended_at": snap.last_attended_at,
                "attendance_count": snap.attendance_count,
                "weeks_absent": snap.weeks_absent,
                "tier": snap.tier,
                "is_active": snap.is_active,
                "is_regular": snap.is_regular,
                "is_connected": snap.is_connected,
            }
            for snap in snapshots.values()
        ]
        await db.execute(stmt, rows)

    await db.commit()

    duration_ms = _monotonic_ms() - t0

    # Write job_runs (guarded for S16 absence)
    job_run_id: Optional[int] = None
    try:
        if await has_table(db, "job_runs"):
            jr = JobRun(
                job_name="recompute_member_status",
                started_at=started_at,
                finished_at=_utc_now(),
                status="success",
                detail={"contact_count": contact_count, "duration_ms": duration_ms},
            )
            db.add(jr)
            await db.commit()
            await db.refresh(jr)
            job_run_id = jr.id
    except Exception as exc:
        logger.warning("job_runs write failed (S16 not yet landed?): %s", exc)

    # Audit log (S01/S02 table; guard similarly)
    try:
        from app.services.audit import record as audit_record
        await audit_record(
            db=db,
            actor_id=None,  # system actor
            action="member_status.recompute",
            entity="contacts",
            entity_id=None,
            before=None,
            after={"contact_count": contact_count, "duration_ms": duration_ms},
        )
    except Exception as exc:
        logger.warning("audit_log write failed: %s", exc)

    return RecomputeResult(
        contact_count=contact_count,
        duration_ms=duration_ms,
        job_run_id=job_run_id,
    )
```

**Transaction semantics:** all snapshot writes are in one `await db.commit()`. If the bulk UPDATE fails, no partial write is committed. `job_runs` and `audit_log` are committed in a separate step so a failure there does not roll back the data.

**Error path:** if the recompute raises an unhandled exception, the caller (the APScheduler wrapper `_run_recompute`) catches it, writes a `job_runs` row with `status='error'` and `detail.error = str(exc)` in a **new** session (the failed session is closed), then re-raises for APScheduler's misfire log.

#### Partial recompute: `recompute_contacts`

```python
async def recompute_contacts(
    db: AsyncSession,
    contact_ids: list[int],
) -> RecomputeResult:
    """
    Compute derived snapshot fields for a specific list of contacts (max 500).
    Does NOT write job_runs (partial recomputes are triggered inline from
    imports / merge / form; they appear in the audit_log only).
    """
```

Same logic as `recompute_all` but passes `contact_ids` to `compute_snapshot_for_contacts`. Validates `len(contact_ids) <= 500` (router enforces this; service logs a warning and clamps). No `job_runs` write. Does write an `audit_log` entry with `entity_id=contact_ids[0]` if `len == 1`, else `None`.

#### APScheduler job registration (S16 host — `backend/app/services/scheduler.py`)

**Modify** (append to S16's file):

```python
# backend/app/services/scheduler.py (S16's file — S23 appends this block)
from apscheduler.triggers.cron import CronTrigger
from app.services.member_status_service import recompute_all

import logging
logger = logging.getLogger(__name__)


async def _run_recompute() -> None:
    """APScheduler job wrapper: owns the DB session lifecycle."""
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            result = await recompute_all(db)
            logger.info(
                "recompute_member_status done: %d contacts in %dms",
                result.contact_count,
                result.duration_ms,
            )
        except Exception as exc:
            logger.error("recompute_member_status failed: %s", exc, exc_info=True)
            # Error job_run written inside recompute_all on exception path


# Called once from S16's scheduler-setup function:
def register_s23_jobs(scheduler) -> None:
    scheduler.add_job(
        _run_recompute,
        trigger=CronTrigger(day_of_week="mon", hour=0, minute=0),
        id="recompute_member_status_eow",
        replace_existing=True,
        misfire_grace_time=3600,  # 1-hour grace: if app was down at midnight
    )
    scheduler.add_job(
        _run_recompute,
        trigger=CronTrigger(day=1, hour=0, minute=0),
        id="recompute_member_status_eom",
        replace_existing=True,
        misfire_grace_time=3600,
    )
```

Two separate `add_job` calls (EOW Monday + EOM 1st of month) appear as distinct entries in S16's job-runs viewer. Both share the `_run_recompute` wrapper.

**If S16 has not yet landed:** add the following to `backend/app/main.py` inside `lifespan()` after DB setup, behind a `HAS_SCHEDULER` guard:

```python
# backend/app/main.py — fallback if S16 scheduler not yet present
HAS_SCHEDULER = False  # set True when S16 lands
if HAS_SCHEDULER:
    from app.services.member_status_service import _run_recompute
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    _sched = AsyncIOScheduler()
    _sched.add_job(_run_recompute, CronTrigger(day_of_week="mon", hour=0, minute=0),
                   id="recompute_member_status_eow", replace_existing=True, misfire_grace_time=3600)
    _sched.add_job(_run_recompute, CronTrigger(day=1, hour=0, minute=0),
                   id="recompute_member_status_eom", replace_existing=True, misfire_grace_time=3600)
    _sched.start()
    yield
    _sched.shutdown()
```

### 4.3 Router: additions to `backend/app/routers/analytics.py`

**Modify** the existing file (post-S01: already imports `Contact`, `Participant`, `Event`; does not reference `push_status`).

Add at the bottom:

```python
# backend/app/routers/analytics.py — S23 additions

from typing import Optional
from pydantic import BaseModel
from fastapi import Body, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.member_status_service import (
    RecomputeResult,
    recompute_all,
    recompute_contacts,
)
from app.dependencies import require_admin  # S01/S15 dep
from app.database import get_db
from app.models import Contact


class RecomputeRequest(BaseModel):
    contact_ids: Optional[list[int]] = None


class RecomputeResponse(BaseModel):
    contact_count: int
    duration_ms: int
    job_run_id: Optional[int]


class MemberStatusSummaryResponse(BaseModel):
    tier_counts: dict[str, int]
    is_active_count: int
    is_inactive_count: int
    is_regular_count: int
    is_connected_count: int
    total_contacts: int
    last_recomputed_at: Optional[str]  # ISO datetime string or null


@router.post("/recompute-member-status", response_model=RecomputeResponse)
async def recompute_member_status(
    body: RecomputeRequest = Body(default_factory=RecomputeRequest),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
) -> RecomputeResponse:
    """Trigger an immediate recompute of derived member-status snapshot columns."""
    if body.contact_ids is not None:
        if len(body.contact_ids) > 500:
            raise HTTPException(
                status_code=422,
                detail="contact_ids max 500 per call; omit for full recompute",
            )
        result: RecomputeResult = await recompute_contacts(db, body.contact_ids)
    else:
        result = await recompute_all(db)
    return RecomputeResponse(
        contact_count=result.contact_count,
        duration_ms=result.duration_ms,
        job_run_id=result.job_run_id,
    )
    # TODO(S16): consider wiring a "Run status recompute" button in the Settings → System page.


@router.get("/member-status-summary", response_model=MemberStatusSummaryResponse)
async def member_status_summary(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),  # widen to require_reporting post-S15 ruling C20
) -> MemberStatusSummaryResponse:
    """Fast GROUP BY aggregate over contacts.tier/is_active/is_regular/is_connected."""
    base_filter = (Contact.is_deleted == False, Contact.contact_type == "individual")

    # Tier distribution
    tier_result = await db.execute(
        select(Contact.tier, func.count(Contact.id).label("n"))
        .where(*base_filter)
        .group_by(Contact.tier)
    )
    tier_counts: dict[str, int] = {}
    for row in tier_result.all():
        tier_counts[row.tier if row.tier is not None else "null"] = row.n

    # Boolean summaries — single pass
    bool_result = await db.execute(
        select(
            func.count(Contact.id).filter(Contact.is_active == True).label("active"),
            func.count(Contact.id).filter(Contact.is_active == False).label("inactive"),
            func.count(Contact.id).filter(Contact.is_regular == True).label("regular"),
            func.count(Contact.id).filter(Contact.is_connected == True).label("connected"),
            func.count(Contact.id).label("total"),
        )
        .where(*base_filter)
    )
    row = bool_result.one()

    # last_recomputed_at from job_runs (guard for S16 absence)
    last_run_at: Optional[str] = None
    try:
        from app.models import JobRun
        lr = await db.execute(
            select(JobRun.finished_at)
            .where(JobRun.job_name == "recompute_member_status", JobRun.status == "success")
            .order_by(JobRun.finished_at.desc())
            .limit(1)
        )
        lr_row = lr.scalar_one_or_none()
        if lr_row:
            last_run_at = lr_row.isoformat()
    except Exception:
        pass  # S16 not yet landed; return null

    return MemberStatusSummaryResponse(
        tier_counts=tier_counts,
        is_active_count=row.active,
        is_inactive_count=row.inactive,
        is_regular_count=row.regular,
        is_connected_count=row.connected,
        total_contacts=row.total,
        last_recomputed_at=last_run_at,
    )
```

### 4.4 S09 search field registry additions

If `backend/app/services/search_service.py` (S09) hard-codes a `FIELD_REGISTRY` list, S23 extends it:

```python
# In search_service.py FIELD_REGISTRY (S09's file) — add these entries:
{"name": "tier",             "label": "Tier",            "type": "enum",
 "options": ["tier0","tier1","tier2","tier3","inactive"], "ops": ["eq","in","is_null"]},
{"name": "is_active",        "label": "Active",          "type": "boolean", "ops": ["eq","is_null"]},
{"name": "is_regular",       "label": "Regular",         "type": "boolean", "ops": ["eq","is_null"]},
{"name": "is_connected",     "label": "Connected",       "type": "boolean", "ops": ["eq","is_null"]},
{"name": "attendance_count", "label": "Attendance Count","type": "integer",
 "ops": ["eq","gt","lt","gte","lte","is_null"]},
{"name": "last_attended_at", "label": "Last Attended",   "type": "date",
 "ops": ["eq","gt","lt","gte","lte","is_null"]},
{"name": "weeks_absent",     "label": "Weeks Absent",    "type": "integer",
 "ops": ["eq","gt","lt","gte","lte","is_null"]},
```

If S09 auto-discovers columns from the ORM model via reflection, no change is needed — the columns exist on `Contact` after S23's migration.

### 4.5 DB utility: `has_table`

Add to `backend/app/utils/db_helpers.py` (create if not yet created):

```python
# backend/app/utils/db_helpers.py
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def has_table(db: AsyncSession, table_name: str) -> bool:
    """Returns True if the named table exists in the current DB."""
    conn = await db.connection()
    result = await conn.run_sync(
        lambda sync_conn: sa.inspect(sync_conn).has_table(table_name)
    )
    return result
```

### 4.6 File-by-file summary

| Action | File | What changes |
|---|---|---|
| **CREATE** | `backend/app/services/member_status_service.py` | New service: `ContactSnapshot`, `RecomputeResult`, `_tier_from_weeks_absent`, `_connected_field_names`, `compute_snapshot_for_contacts`, `recompute_all`, `recompute_contacts` |
| **CREATE** | `backend/alembic/versions/<rev>_s23_member_status_snapshot_guard.py` | Guard migration: adds 7 snapshot columns to `contacts` if absent + 5 indexes; idempotent up; downgrade drops indexes only |
| **MODIFY** | `backend/app/models.py` | Add 7 snapshot columns to `Contact` model (no-op at ORM level if S01 C2 patch already added them; migration is the gate) |
| **MODIFY** | `backend/app/routers/analytics.py` | Add `POST /analytics/recompute-member-status` + `GET /analytics/member-status-summary` + three Pydantic schemas |
| **MODIFY or CREATE** | `backend/app/constants.py` | Add `DEFAULT_CONNECTED_FIELD_NAMES: frozenset[str]` (create file if not yet created by S01/S02) |
| **MODIFY** | `backend/app/services/scheduler.py` (S16 file) | Append `_run_recompute`, `register_s23_jobs`; call `register_s23_jobs(scheduler)` from S16's setup function |
| **MODIFY** | `backend/app/services/search_service.py` (S09 file) | Extend `FIELD_REGISTRY` with 7 derived fields if it is a hard-coded list |
| **CREATE** | `backend/app/utils/db_helpers.py` | `has_table` async helper (create file if not yet created) |
| **MODIFY** | `backend/tests/conftest.py` | Ensure `Contact` model (with 7 snapshot columns) is included in `create_all`; no migration needed for tests (SQLite uses `create_all`) |

---

## 5. Frontend

S23 is primarily a **backend + data pipeline sprint**. No new pages, routes, or components are created. The frontend impact is limited to ensuring S03's existing badge slots and type definitions correctly reflect the computed values once the recompute job runs.

### 5.1 `StatusBadge` (S03 primitive — `frontend/src/components/ui/StatusBadge.tsx`)

**Modify** to confirm/add tier label strings and Tailwind token colors (no hardcoded hex):

```tsx
// frontend/src/components/ui/StatusBadge.tsx
// S23 defines the canonical tier label strings; S03 implements the component.

const TIER_LABELS: Record<string, string> = {
  tier0:    "This Week",
  tier1:    "1–4 Weeks Absent",
  tier2:    "5–8 Weeks Absent",
  tier3:    "9–12 Weeks Absent",
  inactive: "Inactive (12+)",
};

const TIER_COLORS: Record<string, string> = {
  tier0:    "bg-primary/10 text-primary",
  tier1:    "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  tier2:    "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
  tier3:    "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
  inactive: "bg-destructive/10 text-destructive",
};

// When tier === null → show "No attendance" in muted style (not as an error).
// Boolean badges:
//   is_active  true→"Active"  false→"Inactive"  null→omit or "—"
//   is_regular true→"Regular" false→"Non-Regular" null→omit
//   is_connected true→"Connected" false→"Not Connected" null→omit
```

No hardcoded hex colors anywhere. All dark-mode variants use Tailwind `dark:` prefix or token classes.

### 5.2 TypeScript `Contact` interface (`frontend/src/types/index.ts`)

**Modify** — add the seven derived fields to the `Contact` (or `ContactDetail`) interface. All are nullable:

```ts
// frontend/src/types/index.ts — ContactDetail / ContactListItem
interface ContactDetail {
  // ... existing core fields ...
  last_attended_at: string | null;   // ISO datetime string
  attendance_count: number | null;
  weeks_absent: number | null;
  tier: "tier0" | "tier1" | "tier2" | "tier3" | "inactive" | null;
  is_active: boolean | null;
  is_regular: boolean | null;
  is_connected: boolean | null;
}
```

### 5.3 `ContactDetailPage` and `ContactsPage` (S03 files)

**Verify** (no code changes expected if S03 implemented the badge slot correctly):
- `ContactDetailPage` reads `contact.tier`, `contact.is_active`, `contact.is_regular`, `contact.is_connected`; no "coming soon" placeholder visible after a recompute run.
- `ContactsPage` DataTable tier chip column renders when value is non-null; shows "—" or "No attendance" when null.

### 5.4 TanStack Query keys

S23 adds no new query keys. The `member-status-summary` endpoint is consumed by S14's `DashboardPage`. S14 owns the query key:

```ts
// S14 (DashboardPage.tsx) will add:
useQuery({
  queryKey: ["member-status-summary"],
  queryFn: () => api.get("/analytics/member-status-summary").then(r => r.data),
  staleTime: 5 * 60 * 1000,  // 5 min; summary is refreshed by recompute, not live
})
```

S23 documents this key so the S14 implementer wires it without re-inventing the endpoint.

### 5.5 Role gating

- `POST /analytics/recompute-member-status` → admin-only on backend; frontend trigger button lives in S16's "Settings → System Status" page (optional for S23 — see §10.7).
- `GET /analytics/member-status-summary` → admin + volunteer pre-S15; post-S15 ruling C20 widened to include `viewer` (aggregate counts only, no PII).

### 5.6 File-by-file

| Action | File | What changes |
|---|---|---|
| **MODIFY** | `frontend/src/components/ui/StatusBadge.tsx` | Add/confirm `TIER_LABELS` + `TIER_COLORS` maps; add `is_active`/`is_regular`/`is_connected` boolean badge variants; null handling |
| **MODIFY** | `frontend/src/types/index.ts` | Add 7 derived fields to `ContactDetail` / `ContactListItem` interfaces (all nullable) |
| **VERIFY** | `frontend/src/pages/ContactDetailPage.tsx` | Badge slot reads snapshot columns; null renders gracefully |
| **VERIFY** | `frontend/src/pages/ContactsPage.tsx` | DataTable tier chip column renders non-null values; null shows fallback |

No new routes. No new Zustand state.

---

## 6. Migration / data

### 6.1 Initial backfill on first deploy

After the guard migration adds the columns (all `NULL`), the snapshot is empty until the first recompute. The deploy action sequence:

1. `alembic upgrade head` — adds columns + indexes. No data change. ~100ms.
2. Admin navigates to Settings → System (S16) or calls `POST /analytics/recompute-member-status` (no body) — full backfill. At ~1,440 contacts + 33.6k participant rows, completes in < 5 seconds.
3. All contact badges, search filters, and report tiles are now populated.

**Optional auto-seed:** S16's APScheduler startup sequence can call `recompute_all` once at app boot if `contacts.tier` is universally `NULL` (checked via `SELECT COUNT(*) FROM contacts WHERE tier IS NOT NULL = 0`). Whether to do this is an S16 implementation decision; S23 exposes `recompute_all` as an async function callable from any async context.

### 6.2 Post-import trigger (contract with S06 and S10)

S06 (ETL migration) and S10 (import wizard) each import `participants` rows that change derived statuses. Both **must** call `recompute_all(db)` at the end of a successful live import run. S23 documents this contract; S06 and S10 implement it. A 1,440-contact recompute adds < 5s to the import flow.

### 6.3 Post-merge trigger (contract with S11)

S11 (find & merge duplicates): after reassigning loser→survivor FKs in `participants`, the survivor's attendance changes. S11 **must** call `await recompute_contacts(db, [survivor_id])` after the FK reassignment commits. S23 exposes this function; S11 imports it from `app.services.member_status_service`.

### 6.4 Contacts with no attendance

Contacts created via S03 form or migrated without events will have `attendance_count=0`, `last_attended_at=NULL`, `weeks_absent=NULL`, `tier=NULL`, `is_active=NULL`, `is_regular=False`, `is_connected=<computed>`. S03's badge slot must display gracefully when `tier=NULL`: show "No attendance" in muted styling (not an error state).

### 6.5 Source coverage for `last_attended_at`

The attendance aggregate uses `events.start_at` (the event's scheduled start time) rather than `participants.created_at` (when the row was inserted). Rationale: `start_at` is the canonical "when did the event happen" timestamp — a participant row inserted after-the-fact by an import will correctly use the event's date. This means S23 depends on `events.start_at` being accurately set; S04 must ensure `start_at` is required (not nullable) on `events`.

---

## 7. Acceptance criteria

1. After `POST /analytics/recompute-member-status` (no body) runs on a DB containing at least one `participants` row with `status='attended'`, the corresponding `contacts.last_attended_at` equals the `MAX(events.start_at)` for that contact's attended `participants` rows.
2. A contact with `attendance_count = 9` has `is_regular = False`; a contact with `attendance_count = 10` has `is_regular = True`.
3. Tier assignment is correct at every boundary:
   - `weeks_absent = 0` → `tier = 'tier0'`
   - `weeks_absent = 1` → `tier = 'tier1'`
   - `weeks_absent = 4` → `tier = 'tier1'`
   - `weeks_absent = 5` → `tier = 'tier2'`
   - `weeks_absent = 8` → `tier = 'tier2'`
   - `weeks_absent = 9` → `tier = 'tier3'`
   - `weeks_absent = 12` → `tier = 'tier3'`
   - `weeks_absent = 13` → `tier = 'inactive'`
   - `weeks_absent = 52` → `tier = 'inactive'`
4. A contact with `last_attended_at = NULL` (never attended) has `tier = NULL`, `is_active = NULL`, `weeks_absent = NULL`, `attendance_count = 0`, `is_regular = False`.
5. A contact whose `custom_data` contains `{"community_leader": 42}` (non-null integer) has `is_connected = True`. A contact with `{"community_leader": null}` or without the key has `is_connected = False`.
6. `GET /analytics/member-status-summary` returns a `tier_counts` dict whose values sum to `total_contacts` (no contacts are double-counted; NULL tier counted under key `"null"`).
7. `POST /analytics/recompute-member-status` with `{"contact_ids": [1, 2, 3]}` updates only those three contacts; a fourth contact with different data is unchanged.
8. `POST /analytics/recompute-member-status` with `{"contact_ids": [<501 ids>]}` returns HTTP 422.
9. `POST /analytics/recompute-member-status` requires admin role; a `volunteer` token receives HTTP 403.
10. After recompute, `GET /contacts` (S09 advanced search) with filter `{"field": "tier", "op": "eq", "value": "tier1"}` returns only contacts with `tier = 'tier1'`.
11. `ContactDetailPage` for a contact with `tier='tier2'`, `is_active=True`, `is_regular=True`, `is_connected=False` shows four badges with the correct labels ("5–8 Weeks Absent", "Active", "Regular", "Not Connected") and no hardcoded hex colors.
12. The guard migration is idempotent: running it twice (or running it when S01 already added the columns) completes without error and does not create duplicate indexes.
13. A `job_runs` row is written per full recompute with `job_name='recompute_member_status'`, `status='success'`, and `detail.contact_count` matching the processed contact count.
14. An `audit_log` row is written per full recompute with `action='member_status.recompute'` and `actor_id=NULL`.
15. Contacts with `contact_type != 'individual'` (household, organization) are **not updated** by the full recompute (their snapshot columns remain NULL).
16. Running `recompute_all` twice with the same data returns identical snapshots (idempotent).
17. All backend tests pass: `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test pytest tests/ -q`. Frontend `npm run build` and `npm run lint` are clean.

---

## 8. Test plan

### Backend pytest (`backend/tests/`)

**File: `backend/tests/test_member_status_service.py`** (new file)

```python
import pytest
from datetime import datetime, timedelta, timezone

from app.services.member_status_service import (
    _tier_from_weeks_absent,
    compute_snapshot_for_contacts,
    recompute_all,
    recompute_contacts,
)


# --- Pure function tests (no DB) ---

@pytest.mark.parametrize("weeks,expected", [
    (None, None),
    (0,    "tier0"),
    (1,    "tier1"),
    (4,    "tier1"),
    (5,    "tier2"),
    (8,    "tier2"),
    (9,    "tier3"),
    (12,   "tier3"),
    (13,   "inactive"),
    (52,   "inactive"),
    (100,  "inactive"),
])
def test_tier_from_weeks_absent(weeks, expected):
    assert _tier_from_weeks_absent(weeks) == expected


# --- Integration tests (require db_session fixture from conftest) ---

@pytest.mark.asyncio
async def test_recompute_basic(db_session):
    """One contact with 2 attended participants → correct snapshot."""
    # Arrange: insert contact, event (start_at=10 days ago), 2 participants with status='attended'
    # Act: await recompute_all(db_session)
    # Assert:
    #   contact.attendance_count == 2
    #   contact.weeks_absent == 1 (10 days // 7 == 1)
    #   contact.tier == 'tier1'
    #   contact.is_active == True
    #   contact.is_regular == False  # count <= 9


@pytest.mark.asyncio
async def test_recompute_never_attended(db_session):
    """Contact with no participants → all NULL except is_regular=False."""
    # Insert contact with no participants
    # Run recompute
    # Assert: last_attended_at=None, attendance_count=0, weeks_absent=None,
    #          tier=None, is_active=None, is_regular=False


@pytest.mark.asyncio
async def test_is_regular_boundary(db_session):
    """10 attended → is_regular=True. 9 attended → is_regular=False."""
    # Insert two contacts: one with 9 participants, one with 10
    # Recompute; assert is_regular values accordingly


@pytest.mark.asyncio
async def test_is_connected_community_leader(db_session):
    """custom_data = {"community_leader": 5} → is_connected=True."""
    # Insert contact with custom_data={"community_leader": 5}
    # Recompute; assert is_connected=True


@pytest.mark.asyncio
async def test_is_not_connected_null_value(db_session):
    """custom_data = {"community_leader": null} → is_connected=False."""
    # Insert contact with custom_data={"community_leader": None}
    # Recompute; assert is_connected=False


@pytest.mark.asyncio
async def test_is_not_connected_missing_key(db_session):
    """custom_data without connected keys → is_connected=False."""


@pytest.mark.asyncio
async def test_partial_recompute(db_session):
    """Partial recompute updates only the specified contact_ids."""
    # Insert 2 contacts with different attendance
    # Run recompute_contacts(db, [contact_a.id])
    # Assert contact_a updated, contact_b still NULL


@pytest.mark.asyncio
async def test_recompute_skips_non_individual(db_session):
    """Org/household contacts are NOT updated by full recompute."""
    # Insert contact with contact_type='organization' and some attendance
    # Recompute; assert org contact snapshot columns remain NULL


@pytest.mark.asyncio
async def test_recompute_idempotent(db_session):
    """Running recompute twice yields identical results."""
    # Setup; recompute twice; compare snapshots


@pytest.mark.asyncio
async def test_recompute_all_four_sources(db_session):
    """Participants with source in face/manual/zoom/community_report all counted."""
    # Insert participants with each source value; all status='attended'
    # Recompute; assert attendance_count == 4


@pytest.mark.asyncio
async def test_tier0_this_week(db_session, monkeypatch):
    """last_attended_at = 2 days ago → weeks_absent=0 → tier=tier0."""
    # Monkeypatch datetime.now in the service to a fixed 'today'
    # Insert participant with start_at = today - 2 days
    # Recompute; assert tier='tier0'


@pytest.mark.asyncio
async def test_tier_boundary_week_calc(db_session, monkeypatch):
    """Verify floor division: 6 days = 0 weeks, 7 days = 1 week."""
    # last_attended_at = today - 6 days → weeks_absent = 0 → tier0
    # last_attended_at = today - 7 days → weeks_absent = 1 → tier1
```

**File: `backend/tests/test_analytics_recompute_endpoint.py`** (new file)

```python
import pytest


@pytest.mark.asyncio
async def test_recompute_requires_admin(client, volunteer_token):
    resp = await client.post(
        "/analytics/recompute-member-status",
        headers={"Authorization": f"Bearer {volunteer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_recompute_endpoint_full(client, admin_token):
    resp = await client.post(
        "/analytics/recompute-member-status",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "contact_count" in data
    assert "duration_ms" in data
    assert data["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_recompute_endpoint_partial(client, admin_token, seeded_contact_id):
    resp = await client.post(
        "/analytics/recompute-member-status",
        json={"contact_ids": [seeded_contact_id]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["contact_count"] == 1


@pytest.mark.asyncio
async def test_recompute_over_limit(client, admin_token):
    resp = await client.post(
        "/analytics/recompute-member-status",
        json={"contact_ids": list(range(501))},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_member_status_summary(client, admin_token):
    resp = await client.get(
        "/analytics/member-status-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "tier_counts" in data
    assert "total_contacts" in data
    # tier_counts values sum to total_contacts
    assert sum(data["tier_counts"].values()) == data["total_contacts"]


@pytest.mark.asyncio
async def test_member_status_summary_no_s16(client, admin_token):
    """last_recomputed_at is null if job_runs table absent (pre-S16)."""
    # Mock has_table to return False for 'job_runs'
    resp = await client.get(
        "/analytics/member-status-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    # last_recomputed_at may be null — must not 500
```

**Migration test** (add to existing `test_migrations.py` or equivalent):

```python
def test_guard_migration_idempotent(test_db_url):
    """Running the S23 guard migration twice does not raise."""
    # Apply S23 migration
    # Apply S23 migration again
    # Assert no IntegrityError / OperationalError
```

### Frontend vitest (`frontend/src/`)

**File: `frontend/src/components/ui/StatusBadge.test.tsx`** (new file or extend existing)

```tsx
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "./StatusBadge";

test("tier0 renders 'This Week' with primary color class", () => {
  render(<StatusBadge tier="tier0" />);
  expect(screen.getByText("This Week")).toBeInTheDocument();
});

test("tier2 renders '5–8 Weeks Absent' with yellow token class", () => {
  render(<StatusBadge tier="tier2" />);
  expect(screen.getByText("5–8 Weeks Absent")).toBeInTheDocument();
  // badge element has bg-yellow-100 class (not a hardcoded hex)
});

test("inactive renders 'Inactive (12+)' with destructive token class", () => {
  render(<StatusBadge tier="inactive" />);
  expect(screen.getByText("Inactive (12+)")).toBeInTheDocument();
});

test("null tier renders null/empty gracefully, no crash", () => {
  const { container } = render(<StatusBadge tier={null} />);
  // Either empty or "No attendance" label — no throw
  expect(container).toBeTruthy();
});

test("is_regular=true renders 'Regular'", () => {
  render(<StatusBadge isRegular={true} />);
  expect(screen.getByText("Regular")).toBeInTheDocument();
});

test("is_connected=false renders 'Not Connected'", () => {
  render(<StatusBadge isConnected={false} />);
  expect(screen.getByText("Not Connected")).toBeInTheDocument();
});
```

**Contact TypeScript interface test** (type-only, verified by `npm run build`):

```ts
// Ensure the ContactDetail type accepts all 7 derived fields as nullable
const c: ContactDetail = {
  // ...required fields...
  last_attended_at: null,
  attendance_count: null,
  weeks_absent: null,
  tier: null,
  is_active: null,
  is_regular: null,
  is_connected: null,
};
```

---

## 9. Rollout / rollback / risks

### Rollout sequence

1. Deploy **S16** first (APScheduler host + `job_runs` table). S23 can merge before S16 (with `HAS_SCHEDULER=False` guard), but S16 enables the nightly cron.
2. Run `alembic upgrade head` — guard migration adds 7 columns + 5 indexes. No data changes. No downtime.
3. After deploy, admin triggers `POST /analytics/recompute-member-status` (no body). Completes in < 5s.
4. Verify `GET /analytics/member-status-summary` returns non-zero tier counts.
5. Spot-check `ContactDetailPage` for a known contact; confirm tier badge renders.
6. The Monday 00:00 UTC cron fires automatically each week thereafter.

### Rollback

- **Migration downgrade (`alembic downgrade -1`):** drops the five indexes only. The seven columns remain (nullable, NULL values) — dropping columns in Postgres requires a table lock and is not needed for safety. Badges show "No attendance" (all NULL), which is the same as pre-S23.
- **Code rollback:** revert `member_status_service.py` + the two analytics endpoints + `constants.py` additions. APScheduler jobs stop registering. Snapshot columns retain last-written values (stale but harmless). No cascading failures.
- **APScheduler deregistration:** removing `register_s23_jobs()` call from `scheduler.py` stops future cron runs without affecting stored column values.

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| S01 C2 patch not applied (snapshot columns absent) | Medium | Guard migration adds them idempotently; no code breaks regardless |
| S16 APScheduler not yet landed at S23 merge time | Medium | `HAS_SCHEDULER` guard in `main.py`; on-demand endpoint works independently of S16 |
| `custom_data` JSONB key-existence differs between SQLite (tests) and Postgres (prod) | High | Python-side dict check on deserialized JSONB works on both dialects; no dialect-specific JSONB operator needed |
| Tier boundary drift if `datetime.now()` is close to midnight | Low | Use `today_midnight` (start-of-day UTC, computed once per run), not `datetime.now()` directly; inject via parameter for testability |
| Full recompute holds a DB write lock for > 1s on large instances | Low | At 1,440 contacts + 33.6k rows: bulk UPDATE in < 1s. For > 10k contacts: add batched chunking with `asyncio.sleep(0)` yields (§10.4) |
| `connected_field_names` admin_settings key not seeded | Low | Falls back to `DEFAULT_CONNECTED_FIELD_NAMES` constant; no crash |
| `events.start_at` null for some events | Low | S04 must make `start_at` non-nullable; if NULL rows slip in, `MAX(events.start_at)` skips them (aggregate of NULL = NULL), which is safe |
| APScheduler misfire if app is down at Monday midnight | Low | `misfire_grace_time=3600` (1 hour); job fires on next startup within the grace window |

---

## 10. Open questions & pending owner artifacts

1. **`connected_field_names` configuration:** Exact `custom_field_def.name` values that mark a contact as Connected. Current assumption: `community_leader` and `community`. If S02 seeds different names (from the authoritative custom-field list), update `DEFAULT_CONNECTED_FIELD_NAMES` and the `admin_settings` seed. **Owner must confirm exact field names from the CiviCRM custom-group export.** (Pending artifact from `decisions.md §Pending artifacts #2`.)

2. **Cron timezone:** The n8n workflow fires Monday `0 0 * * 1`. Is this UTC or Philippine Time (UTC+8)? Monday 00:00 PHT = Sunday 16:00 UTC. Until confirmed, spec uses Monday 00:00 UTC (Monday 8AM PHT), which is reasonable for a weekly job. **Owner to confirm preferred timezone.** If PHT: set `CronTrigger(day_of_week="mon", hour=0, minute=0, timezone="Asia/Manila")`.

3. **EOM cron semantics:** "Rescan EOM" in n8n runs `daily noon` (not on a specific day). Current spec uses `day=1, hour=0, minute=0` (1st of month at midnight UTC = first day the new month's data is available). If the intent is "last day of month" or "daily noon", adjust the CronTrigger. **Owner to confirm.**

4. **Batching for scale (> 5k contacts):** If the contact base grows past ~10k, the single-session bulk UPDATE may hold a write lock for > 1s. Recommendation: add batched recompute logic in chunks of 500 with `asyncio.sleep(0)` yield between batches. Gate on `len(all_ids) > 5000` in `recompute_all`. Deferred for v1; added as a follow-up task.

5. **Households and organizations:** Current spec skips non-individual contacts. If household units ever get their own attendance tracking (e.g., a household "attended as a unit"), the recompute must include `contact_type='household'`. Deferred.

6. **`is_connected` multiselect edge case:** If `community` is defined as a multiselect field (`is_multi=True`), `custom_data["community"]` will be a `list[int]`. The `is_connected` check must treat `[]` (empty list) as not-connected and any non-empty list as connected. The current Python-side check `val != []` handles this correctly, but tests should explicitly cover a contact with `{"community": [5]}` and `{"community": []}`.

7. **Optional S16 settings-page trigger button:** S16's "Settings → System Status" page may want a "Run status recompute" button that calls `POST /analytics/recompute-member-status`. S23 exposes the endpoint; S16 implementer decides whether to wire a button. The `// TODO(S16): wire trigger button` comment is already in the router.

8. **`last_recomputed_at` display on S14 dashboard:** The `GET /analytics/member-status-summary` returns `null` before S16 lands (no `job_runs` table). S14's dashboard tile for "Last updated" must display "Unknown" or "—" when `null`, not crash. S14 implementer to handle gracefully.

9. **`admin_settings` seeding:** On first deploy, `admin_settings` rows for `connected_field_names` and `tier_boundaries_weeks` are not automatically seeded. S16's setup checklist or S23's migration `upgrade()` can add them as defaults. Recommend: add a thin `admin_settings` upsert in the guard migration's `upgrade()` body for these two keys (idempotent via `ON CONFLICT DO NOTHING`).

10. **Face attendance in `participants`:** S07's face enrollment flow writes `participants` rows with `source='face'`, `status='attended'`. S23's `compute_snapshot_for_contacts` correctly picks these up (it filters only on `status='attended'`, source-agnostic). No special handling needed.

---

## Cross-sprint dependencies and shared-model touchpoints (for the master to reconcile)

- **S01 (C2 patch REQUIRED):** S01 must be patched to add the 7 snapshot columns to `contacts` alongside the core columns. S23's guard migration is a safety net only — the canonical owner is S01. Master must track C2 patch status.

- **S03 reads these columns:** S03's `ContactDetailPage` badge slot reads the snapshot columns (S03 spec §2 "derived-status badges read-only"). After S23 ships and a recompute runs, the slot renders real values. S03's tests must include fixture data with non-null snapshot columns to assert the badge renders.

- **S04 (`events.start_at` required):** S23's attendance aggregate uses `MAX(events.start_at)`. If S04 allows nullable `start_at`, some events produce `NULL` in the aggregate, causing incorrect `last_attended_at` values. S04 must declare `start_at NOT NULL`. **Master: add this as a cross-sprint constraint in S04.**

- **S05 (`ix_participants_contact_id`):** S23's attendance aggregate relies on this index for efficiency. S05 must add it. Master tracks this index as S05-owned.

- **S09 field registry:** S09's `FIELD_REGISTRY` must include the 7 derived fields. If S09 lands before S23, registry has stub entries (columns exist but all NULL — `is_null` filter still works). S23 makes non-null filters meaningful.

- **S11 merge must call `recompute_contacts`:** After loser→survivor FK reassignment, S11 must call `await recompute_contacts(db, [survivor_id])`. S23 exports this function; S11 must import it. **Master: add this as a post-S23 patch requirement in S11.**

- **S06 and S10 post-import trigger:** Both import sprints must call `recompute_all(db)` at the end of a successful live run. **Master: add this as a post-S23 patch requirement in S06 and S10.**

- **S14 reads these columns for dashboard tiles:** S14's tier-distribution tile (`"Tier Level Summary"`, `reports.md §C.3`) is a `GROUP BY tier` query on `contacts.tier`. S14 depends on S23 having populated the column. Master must sequence S23 before or concurrent with S14's reporting tiles. Tiles degrade gracefully when columns are all NULL. **NULL-tier handling (per CN-21):** never-attended contacts carry `tier=NULL` (the `"null"` key in `tier_counts`). S14 reports must **exclude NULL-tier contacts from the per-tier count tiles** (tier0–tier3, inactive) but **include them in the total-contact count** — so the per-tier tiles need not sum to the total. (The summary endpoint already returns the NULL bucket under key `"null"` and counts it in `total_contacts`; S14 simply does not render a tier tile for it.)

- **S16 owns APScheduler + `job_runs`:** S23 registers two cron jobs in S16's scheduler. S16 must be merged first for the cron to fire. If merged out of order, the `HAS_SCHEDULER` guard in `main.py` provides safe fallback.

- **S17 tier-transition automation:** S17's ECA rules engine may fire on `tier` changes. S23 does not detect changes per-contact (no before/after); S17 must query or listen for tier changes. If S17 needs change detection, consider either: (a) S17 reads `contacts.tier` before recompute and compares after, or (b) S23 emits an `audit_log` entry per changed contact (not in current scope — deferred to S17 as a targeted enhancement).

- **S22 community_report attendance:** S22 writes `participants` rows with `source='community_report'`, `status='attended'`. S23 picks these up automatically (no source filter in the aggregate). No code change needed; S22 landing before or after S23 is compatible.

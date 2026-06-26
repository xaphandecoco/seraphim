# S14 — Reporting & Analytics Dashboard
**Phase:** E — Insight & access · **Depends on:** S01, S03, S04, S05, S23 · **Effort:** XL · **Status:** Not started

> **Hard dependencies (build order):**
> - **S01** — `Contact`/`Event`/`Participant` rename + `push_status` dropped. The current `analytics.py` imports `CiviCRMEvent`, `Attendance`, selects `push_status` (analytics.py:14, 131, 144, 146) — all break until S01 lands. S14's router must be rebased on S01.
> - **S03** — `contacts.contact_subtype`, `is_regular`, `is_connected`, `is_active`, `tier`, `last_attended_at`, `attendance_count`, `weeks_absent`, `custom_data` JSONB, `is_deleted`, `created_at` (snapshot slots created here, per ruling C2 / 00-MASTER.md §8).
> - **S04** — `events.event_type`, `session_time`, `start_at`. **(per CN-15)** There is **no `session_type` column** — it does not exist on `events`. S14 derives the reporting session bucket from `events.event_type` + `events.session_time` via a static Python mapping (`SESSION_CATEGORIES` in `reporting.py`); no `session_type` column and no guard migration to add one. S04 owns `event_type` + `session_time`.
> - **S05** — `participants.status`, `source`, `contact_id`, `event_id`; the `StreamingResponse`/generator export pattern S05 establishes.
> - **S23** — sole writer of the seven derived snapshot columns (`tier`, `is_active`, `is_regular`, `is_connected`, `last_attended_at`, `attendance_count`, `weeks_absent`). S14 only reads them. Graceful NULL fallback required pre-S23.
>
> **Soft dependencies (degrade gracefully, do not block):**
> - **S02** — `custom_field_def`/`custom_field_group` drive discoverable ministry/zone/PEPSOL/leader-rollup dimensions. Tiles show `"(none)"` without S02 data.
> - **S15** — introduces the `viewer` role. **(per CN-17)** `require_reporting` is a forward-compatible shim (`= require_volunteer`) that **S15 absorbs/patches** (via `require_role(...)`) to add `viewer`; S14 must not create a permanent version. `ReportingRoute` guard activates automatically when S15 adds `isViewer` to `authStore`.
> - **S16** — `job_runs` table used by `AttendanceGridTile` `last_updated_at`. Guard: if table absent, return `null`.
> - **S22** — `community_report` table feeds `monthly-community-reports`. Guarded on table existence; tile activates automatically when S22 lands.
>
> **Charter:** Rebuild the church's complete Google-Sheet attendance analytics suite (source: `docs/crm-research/reports.md`) — session taxonomy (Morning Prayer / Powerhouse / Community / Events / Sunday 8AM·10AM·3PM, Unique vs Total), summary tiles (Contact Info / Active Status / Tier Level × Connected / Leaders with CGs / Ministry / Zone / PEPSOL), weekly+monthly attendance grids, SMA averages (4/8/12-wk, 3/6/9/12-mo), Monthly Report (prev vs current + diff + vs 12-mo avg), Zone distribution, Monthly Forecasting (projected vs actual + next month). Replaces BOTH the CiviCRM data layer AND the Google-Sheet reporting layer. Adds per-report CSV streaming exports and a tabbed `DashboardPage` with Recharts tiles.

---

## 1. Goal & rationale

Light North Caloocan currently runs two external reporting systems in parallel: CiviCRM's built-in report screens and a Google-Sheet attendance dashboard (`docs/crm-research/reports.md`) fed from CiviCRM via the n8n "CiviCRM Scheduler/Updater" workflow (`fKkPUolayRyZrjao`). The pastoral and admin team uses this to answer week-over-week and month-over-month questions: who is Tier 1 vs Tier 3, which zones are growing, how does this Sunday compare to last month's average?

S14 eliminates both external dependencies:

1. **Session taxonomy reporting** — attendance by session category (Morning Prayer, Powerhouse, Community, Events, Sunday 8AM/10AM/3PM), each counting Unique (distinct people) and Total (all check-in rows). This is the foundational aggregate from which all other reports derive.

2. **KPI summary tiles** — seven purpose-built aggregate tiles covering Contact Info, Active/Inactive Status, Tier Level (Tier 1–3 / Inactive × Connected / Not Connected), Total Leaders with CGs, Ministry Information (per-ministry volunteer counts), Zone/Community distribution, and PEPSOL discipleship pathway counts.

3. **Attendance time-series grids** — weekly and monthly matrices (rows = session metrics, columns = time periods) with conditional cell coloring (high/low).

4. **Analytics** — Simple Moving Averages (4/8/12-week; 3/6/9/12-month), Monthly Report (previous vs current, diff, diff vs 12-month avg), Zone attendance by month, Community Reports attendance by category.

5. **Monthly Forecasting** — projected vs actual this month (linear trend or SMA-12 fallback) and next-month forecast per metric.

6. **Per-report CSV streaming exports** — one export endpoint per report, using the S05 streaming generator pattern.

**Why purpose-built, not a generic engine** (locked, decisions.md Round 5): we ship fixed, fast, indexed aggregate SQL. The visual-rules generality lives in S17. Adding a new report = one descriptor + one query function + one tile. No framework overhead.

**S23 snapshot contract (ruling C19, 00-MASTER.md §8):** S23 is the sole writer of the seven derived snapshot columns. S14 only reads them. Where they are NULL (pre-S23 or freshly migrated), tiles apply a documented graceful fallback.

The existing `backend/app/routers/analytics.py` (analytics.py:1–184) contains four recognition-operational endpoints (`/attendance-by-event`, `/tier-distribution`, `/volunteer-stats`, `/queue-health`) and two broken CSV exports (they import `CiviCRMEvent`/`Attendance` and select `push_status`, which S01 deletes). S14 extends this file with all ministry-reporting endpoints and fixes the broken exports.

---

## 2. Scope

### In scope

- **`ReportRegistry`** — a structured catalog of all report keys, labels, chart types, filter requirements, and export paths. Self-describing via `GET /analytics/reports`.
- **Session taxonomy endpoint** `GET /analytics/session-summary` — Section A: counts for all 8 session categories (Morning Prayer, Powerhouse, Community, Events, Sunday All, 8AM, 10AM, 3PM), each Unique + Total, for a given date range + bucket.
- **Seven summary tile endpoints** (Section B): contact-info, status-summary, tier-summary, leaders-with-cgs, ministry-breakdown, zone-breakdown, pepsol-breakdown.
- **Attendance grid endpoint** `GET /analytics/attendance-grid` (Section C) — weekly or monthly matrix, session rows × period columns, conditional coloring.
- **SMA averages endpoint** `GET /analytics/sma-averages` — 4/8/12-wk and 3/6/9/12-mo SMAs per named metric.
- **Monthly report endpoint** `GET /analytics/monthly-report` — prev month vs current month, diff, diff vs 12-month avg.
- **Monthly zone endpoint** `GET /analytics/monthly-zone` — per-zone prev vs current, diff, distribution %.
- **Monthly community reports endpoint** `GET /analytics/monthly-community-reports` — community-report attendance by category; gracefully degrades when S22 table absent.
- **Monthly forecasting endpoint** `GET /analytics/monthly-forecast` — linear trend + SMA-12 fallback for this month's projection and next month's forecast.
- **General trend endpoint** `GET /analytics/attendance-trend` — retained/rewired for backwards-compat + simple chart use.
- **Segment breakdown endpoint** `GET /analytics/attendance-by-segment` — distinct attendees per whitelisted dimension (core + custom_field).
- **Assimilation funnel endpoint** `GET /analytics/assimilation-funnel` — New Friend → Regular Attendee → Regular Member ladder.
- **Leader rollups endpoint** `GET /analytics/leader-rollups` — per-contact_reference field, top leaders by count.
- **Per-report CSV streaming exports** — one `GET /analytics/export/<key>` per endpoint above (Section A + C + D + E), reusing S05's `StreamingResponse`/generator pattern.
- **`require_reporting` dependency shim** — forward-compatible `= require_volunteer` (admits `{admin, volunteer}`); S15 absorbs/patches it to add `viewer` (per CN-17). S14 must not create a permanent bespoke version.
- **Reporting indexes Alembic migration** — existence-guarded, covering `participants`, `events`, `contacts`. No business table changes.
- **`backend/app/services/reporting.py`** — all query functions; router stays thin.
- **Frontend `DashboardPage.tsx` overhaul** — two tabs (Ministry Reports default; Operations for existing recognition tiles, admin-only). 18 self-fetching tile components, shared filter bar, URL-param filter state.
- **`ReportingRoute.tsx`** — new route guard (admin OR isViewer forward-compat stub).
- **`ReportFilterBar.tsx`** and `useReportFilters.ts` hook — shared date range / bucket / event_type filter, URL search-params state.
- **`downloadCsv.ts`** — lift and improve the inline helper in `DashboardPage.tsx:9–19`.
- **Fix broken legacy exports** — remove `push_status` from `export/attendance` header (S01 deleted the column).
- **`frontend/src/services/reports.ts`** + **`frontend/src/types/reports.ts`** — typed API wrappers and TypeScript interfaces.

### Out of scope

- Generic/ad-hoc report builder, pivot tables, user-defined report definitions (locked, decisions.md Round 5).
- Saved/scheduled/emailed report snapshots (S16/S17 escape hatch).
- New chart libraries — Recharts is present (`DashboardPage.tsx:3`); reuse `frontend/src/lib/chartColors.ts`.
- Materialized views or pre-aggregation tables (live aggregation with indexes is sufficient at 1,440 contacts / 33.6k participants; pre-aggregation is an S16 escape hatch if volume grows past 100k).
- PDF export (CSV streaming only for v1).
- Finance, IT/Network, SOLA workflow reports (out of CRM scope, n8n-analysis.md).
- Recognition-tier/queue-health/volunteer-performance tiles — stay in existing Operations tab; not touched here.
- Writing or modifying derived snapshot columns — that is S23 exclusively.
- Computing `community_report` rows — that is S22. S14 only reads them.

---

## 3. Data model changes

S14 is **read-only against the canonical model**. It adds no new business tables. It adds:

1. **Reporting indexes** for aggregate performance at 33.6k+ participants.
2. A guard migration that adds `session_time` to `events` if S04 has not yet run (idempotent; no-ops if already present). **(per CN-15)** No `session_type` column is created — it does not exist; the reporting session bucket is a static Python mapping over `event_type` + `session_time`.
3. `Index(...)` entries in `models.py` `__table_args__` (so SQLite `create_all` builds them for tests).

### Columns assumed present (owned by S04 — listed for cross-sprint reference only)

| Table | Column | Type | Null | Notes |
|---|---|---|---|---|
| `events` | `event_type` | `String(100)` | NULL | One of the 6 canonical values; first half of the `SESSION_CATEGORIES` mapping key (per CN-15). |
| `events` | `session_time` | `String(20)` | NULL | **(per CN-15 + 2026-06-22 ruling)** Sunday-service split key; `8AM\|10AM\|3PM` (NULL except for Sunday events); second half of the `SESSION_CATEGORIES` mapping key. There is **no `session_type` column.** |

If `session_time` is absent at migration time, the guard migration adds it as a nullable `String` column with no default (matching S04's spec) so S14 can run. The S04 migration will detect existing columns and skip (idempotent). **(per CN-15) Do NOT add a `session_type` column — the reporting bucket is derived in Python from `event_type` + `session_time`.**

### Columns assumed present (owned by S01/S03 — derived snapshot slots)

| Table | Column | Type | Null | Notes |
|---|---|---|---|---|
| `contacts` | `last_attended_at` | `DateTime` | NULL | Snapshot: max event start_at of attended participants. |
| `contacts` | `attendance_count` | `Integer` | NULL | Snapshot: lifetime attended participant count. |
| `contacts` | `weeks_absent` | `Integer` | NULL | Snapshot: floor((today - last_attended_at) / 7). |
| `contacts` | `tier` | `String(20)` | NULL | `tier0\|tier1\|tier2\|tier3\|inactive`. |
| `contacts` | `is_active` | `Boolean` | NULL | True = Tier 0–3. |
| `contacts` | `is_regular` | `Boolean` | NULL | True = attendance_count > 9. |
| `contacts` | `is_connected` | `Boolean` | NULL | True = has community/community_leader assignment. |

### New indexes (one Alembic migration; existence-guarded, idempotent)

| Table | Index name | Columns | Type | Rationale |
|---|---|---|---|---|
| `participants` | `ix_participants_event_id` | `(event_id)` | btree | Join to events for taxonomy grouping. |
| `participants` | `ix_participants_contact_id` | `(contact_id)` | btree | Join to contacts for segment/funnel queries. |
| `participants` | `ix_participants_status_event` | `(status, event_id)` | btree | Trend queries filter `status='attended'` then group by event. |
| `participants` | `ix_participants_source` | `(source)` | btree | Filter by source (face/zoom/name_list/manual/community_report). |
| `events` | `ix_events_start_at` | `(start_at)` | btree | Date-range filter + time bucketing (hot; growing per AGENTS.md). |
| `events` | `ix_events_type_start` | `(event_type, start_at)` | btree | Split by event_type + date; also powers `SESSION_CATEGORIES` taxonomy aggregates (per CN-15). |
| `events` | `ix_events_session_time` | `(session_time, start_at)` | btree | Second mapping key for `SESSION_CATEGORIES` + Sunday service splits 8AM/10AM/3PM (per CN-15; replaces the former `session_type` index). |
| `contacts` | `ix_contacts_subtype` | `(contact_subtype)` WHERE `is_deleted=false` | partial btree (Postgres) | Assimilation funnel + contact_info tile. |
| `contacts` | `ix_contacts_created_at` | `(created_at)` | btree | "new this month" counts. |
| `contacts` | `ix_contacts_tier` | `(tier)` WHERE `is_deleted=false` | partial btree | Tier-level summary tile. |
| `contacts` | `ix_contacts_is_active` | `(is_active)` WHERE `is_deleted=false` | partial btree | Status tile. |
| `contacts` | `ix_contacts_is_regular` | `(is_regular)` WHERE `is_deleted=false` | partial btree | Contact-info tile. |
| `contacts` | `ix_contacts_is_connected` | `(is_connected)` WHERE `is_deleted=false` | partial btree | Connected/not-connected columns. |
| `contacts` | `ix_contacts_custom_data_gin` | `(custom_data)` | GIN (Postgres only) | Segment grouping on JSONB + leader-rollup containment queries. |

### Alembic migration plan

**One new migration**, filename `backend/alembic/versions/<rev>_s14_reporting_indexes.py`. `down_revision` = head after S05 or S23 whichever lands last.

```python
# backend/alembic/versions/<rev>_s14_reporting_indexes.py
from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    def _idx(name, table, cols, **kw):
        existing = {i["name"] for i in insp.get_indexes(table)}
        if name in existing:
            return
        op.create_index(name, table, cols, **kw)

    # Guard: add session_time if S04 has not landed yet.
    # (per CN-15) NO session_type column — it does not exist; the reporting
    # session bucket is derived in Python via SESSION_CATEGORIES (event_type + session_time).
    event_cols = {c["name"] for c in insp.get_columns("events")}
    if "session_time" not in event_cols:
        op.add_column("events", sa.Column("session_time", sa.String(20), nullable=True))

    # participants
    _idx("ix_participants_event_id",      "participants", ["event_id"])
    _idx("ix_participants_contact_id",    "participants", ["contact_id"])
    _idx("ix_participants_status_event",  "participants", ["status", "event_id"])
    _idx("ix_participants_source",        "participants", ["source"])

    # events
    _idx("ix_events_start_at",       "events", ["start_at"])
    _idx("ix_events_type_start",     "events", ["event_type", "start_at"])
    _idx("ix_events_session_time",   "events", ["session_time", "start_at"])  # per CN-15 (was session_type)

    # contacts — partial on Postgres, plain btree on SQLite
    if bind.dialect.name == "postgresql":
        _idx("ix_contacts_subtype",      "contacts", ["contact_subtype"],
             postgresql_where=sa.text("is_deleted = false"))
        _idx("ix_contacts_tier",         "contacts", ["tier"],
             postgresql_where=sa.text("is_deleted = false"))
        _idx("ix_contacts_is_active",    "contacts", ["is_active"],
             postgresql_where=sa.text("is_deleted = false"))
        _idx("ix_contacts_is_regular",   "contacts", ["is_regular"],
             postgresql_where=sa.text("is_deleted = false"))
        _idx("ix_contacts_is_connected", "contacts", ["is_connected"],
             postgresql_where=sa.text("is_deleted = false"))
        _idx("ix_contacts_custom_data_gin", "contacts", ["custom_data"],
             postgresql_using="gin")
    else:
        for iname, col in [
            ("ix_contacts_subtype",      "contact_subtype"),
            ("ix_contacts_tier",         "tier"),
            ("ix_contacts_is_active",    "is_active"),
            ("ix_contacts_is_regular",   "is_regular"),
            ("ix_contacts_is_connected", "is_connected"),
        ]:
            _idx(iname, "contacts", [col])

    _idx("ix_contacts_created_at", "contacts", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for name, table in [
        ("ix_participants_event_id",     "participants"),
        ("ix_participants_contact_id",   "participants"),
        ("ix_participants_status_event", "participants"),
        ("ix_participants_source",       "participants"),
        ("ix_events_start_at",           "events"),
        ("ix_events_type_start",         "events"),
        ("ix_events_session_time",       "events"),  # per CN-15
        ("ix_contacts_subtype",          "contacts"),
        ("ix_contacts_tier",             "contacts"),
        ("ix_contacts_is_active",        "contacts"),
        ("ix_contacts_is_regular",       "contacts"),
        ("ix_contacts_is_connected",     "contacts"),
        ("ix_contacts_created_at",       "contacts"),
    ]:
        existing = {i["name"] for i in insp.get_indexes(table)}
        if name in existing:
            op.drop_index(name, table_name=table)

    if bind.dialect.name == "postgresql":
        gin = {i["name"] for i in insp.get_indexes("contacts")}
        if "ix_contacts_custom_data_gin" in gin:
            op.drop_index("ix_contacts_custom_data_gin", table_name="contacts")
```

**Data backfill:** none (indexes only).

**`models.py` additions:** add `Index(...)` entries to `Contact`, `Event`, `Participant` `__table_args__` mirroring the migration so `create_all` builds them for SQLite tests (following the `uq_task_action_approval` partial-index pattern at models.py:175–184). GIN index uses `postgresql_using="gin"` — SQLAlchemy emits it only for Postgres; SQLite `create_all` falls back to plain btree. Do not add columns to models that are already declared — only add Index objects.

---

## 4. Backend

All endpoints extend `backend/app/routers/analytics.py` (`APIRouter(prefix="/analytics", tags=["analytics"])`, analytics.py:16). No `/api` prefix (nginx strips it, AGENTS.md:94). All handlers are `async def` with full type hints.

### Role dependency (add to `backend/app/dependencies.py`)

**(per CN-17)** `require_reporting` is a **forward-compatible shim**, defined as `require_reporting = require_volunteer` (i.e. admit `{admin, volunteer}`). **S15 absorbs and patches it** to allow the `viewer` role via its `require_role("admin", "volunteer", "viewer")` factory. S14 must **NOT** create a permanent standalone version, and must **NOT** introduce a duplicate function — S15 owns the canonical form.

```python
# Forward-compatible shim (per CN-17). S15 patches this to include "viewer"
# via require_role(...). Do NOT create a permanent bespoke implementation.
require_reporting = require_volunteer
```

Existing operational endpoints (`/attendance-by-event`, `/tier-distribution`, `/volunteer-stats`, `/queue-health`) keep `require_admin`. All new ministry endpoints use `require_reporting`.

### Endpoint table

| METHOD | Path | Role | Key query params | Response schema | Notes |
|---|---|---|---|---|---|
| GET | `/analytics/reports` | reporting | — | `ReportCatalogResponse` | Self-describing catalog; frontend dropdown source. |
| GET | `/analytics/session-summary` | reporting | `start: date`, `end: date`, `bucket: week\|month` | `SessionSummaryResponse` | Section A: 8 categories × Unique + Total per period. |
| GET | `/analytics/contact-info` | reporting | — | `ContactInfoResponse` | Section B tile 1: All / Regular / Connected breakdowns. |
| GET | `/analytics/status-summary` | reporting | — | `StatusSummaryResponse` | Section B tile 2: Active / Inactive. |
| GET | `/analytics/tier-summary` | reporting | — | `TierSummaryResponse` | Section B tile 3: Tier 1/2/3/Inactive × Connected/Not. |
| GET | `/analytics/leaders-with-cgs` | reporting | — | `LeadersWithCGsResponse` | Section B tile 4: count of community-group leaders. |
| GET | `/analytics/ministry-breakdown` | reporting | — | `MinistryBreakdownResponse` | Section B tile 5: volunteer count per ministry value. |
| GET | `/analytics/zone-breakdown` | reporting | — | `ZoneBreakdownResponse` | Section B tile 6: member count per zone/community. |
| GET | `/analytics/pepsol-breakdown` | reporting | — | `PepsolBreakdownResponse` | Section B tile 7: PEPSOL discipleship pathway counts. |
| GET | `/analytics/attendance-grid` | reporting | `bucket: week\|month`, `periods: int=12` | `AttendanceGridResponse` | Section C: matrix session rows × period columns + conditional coloring. |
| GET | `/analytics/sma-averages` | reporting | `metric: str`, `as_of: date` | `SMAResponse` | Section D: 7 SMA windows for the named metric. |
| GET | `/analytics/monthly-report` | reporting | `year: int`, `month: int` | `MonthlyReportResponse` | Section D: prev vs current, diff, diff vs 12-mo avg. |
| GET | `/analytics/monthly-zone` | reporting | `year: int`, `month: int` | `MonthlyZoneResponse` | Section D: per-zone prev vs current, diff, distribution %. |
| GET | `/analytics/monthly-community-reports` | reporting | `year: int`, `month: int` | `MonthlyCommunityReportsResponse` | Section D: community-report rows by zone/category; graceful S22 guard. |
| GET | `/analytics/monthly-forecast` | reporting | `year: int`, `month: int` | `MonthlyForecastResponse` | Section E: projected vs actual + next-month forecast. |
| GET | `/analytics/attendance-trend` | reporting | `start`, `end`, `bucket`, `event_type`, `split_by_event_type: bool` | `AttendanceTrendResponse` | General trend (backwards-compat; rewired to Participant). |
| GET | `/analytics/attendance-by-segment` | reporting | `dimension: str`, `start`, `end`, `event_type` | `SegmentBreakdownResponse` | Distinct attendees per whitelisted dimension. |
| GET | `/analytics/assimilation-funnel` | reporting | `as_of: date` | `AssimilationFunnelResponse` | New Friend → Regular Attendee → Regular Member ladder. |
| GET | `/analytics/leader-rollups` | reporting | `relationship: str`, `limit: int=50`, `offset: int=0` | `LeaderRollupResponse` | Leader count by contact_reference field, paginated. |
| GET | `/analytics/export/session-summary` | reporting | same as session-summary | `text/csv` StreamingResponse | — |
| GET | `/analytics/export/attendance-grid` | reporting | same as grid | `text/csv` StreamingResponse | — |
| GET | `/analytics/export/monthly-report` | reporting | same as monthly-report | `text/csv` StreamingResponse | — |
| GET | `/analytics/export/monthly-zone` | reporting | same as monthly-zone | `text/csv` StreamingResponse | — |
| GET | `/analytics/export/monthly-forecast` | reporting | same as forecast | `text/csv` StreamingResponse | — |
| GET | `/analytics/export/attendance-trend` | reporting | same as trend | `text/csv` StreamingResponse | — |
| GET | `/analytics/export/attendance-by-segment` | reporting | same as segment | `text/csv` StreamingResponse | — |
| GET | `/analytics/export/assimilation-funnel` | reporting | same as funnel | `text/csv` StreamingResponse | — |
| GET | `/analytics/export/leader-rollups` | reporting | same (no pagination) | `text/csv` StreamingResponse | — |

### Pydantic response schemas (add to `backend/app/schemas.py`)

```python
from __future__ import annotations
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel

# ---- Report catalog ----
class DimensionOption(BaseModel):
    key: str        # "contact_subtype" | "cf:community"
    label: str
    source: Literal["core", "custom_field"]

class ReportDescriptor(BaseModel):
    key: str
    title: str
    description: str
    chart: Literal["line","area","bar","stacked-bar","horizontal-bar",
                   "grid","funnel","table","kpi"]
    dimensions: list[DimensionOption] = []
    filters: list[str] = []
    export_path: str | None = None

class ReportCatalogResponse(BaseModel):
    reports: list[ReportDescriptor]

# ---- Section A: Session summary ----
class SessionMetric(BaseModel):
    category: str   # "morning_prayer"|"powerhouse"|"community"|"events"|
                    # "sunday_all"|"sunday_8am"|"sunday_10am"|"sunday_3pm"
    label: str      # Human label
    unique_count: int
    total_count: int
    new_contacts: int = 0  # first-timers for community and events categories

class PeriodSessionRow(BaseModel):
    period: str     # ISO "2026-03" or "2026-W12"
    metrics: list[SessionMetric]

class SessionSummaryResponse(BaseModel):
    bucket: str     # "week"|"month"
    start: date
    end: date
    periods: list[PeriodSessionRow]
    total_unique: int       # DISTINCT contact_id across all categories in range
    total_database: int     # COUNT(contacts) WHERE is_deleted=false (snapshot)
    total_with_community: int  # total_unique + community_report attendee count

# ---- Section B: Summary tiles ----
class ContactInfoResponse(BaseModel):
    all_contacts: int
    regular_count: int
    regular_pct: float
    non_regular_count: int
    non_regular_pct: float
    connected_regular_count: int
    connected_regular_pct: float
    not_connected_regular_count: int
    not_connected_regular_pct: float

class StatusSummaryResponse(BaseModel):
    active_count: int
    inactive_count: int
    active_regular_count: int
    inactive_regular_count: int

class TierCell(BaseModel):
    tier: str    # "tier1"|"tier2"|"tier3"|"inactive"
    label: str   # "1–4 wks"|"5–8 wks"|"9–12 wks"|"12+ wks (Inactive)"
    all_members: int
    connected: int
    not_connected: int

class TierSummaryResponse(BaseModel):
    rows: list[TierCell]     # always 4 rows in order tier1/tier2/tier3/inactive
    total_all: int
    total_connected: int
    total_not_connected: int

class LeadersWithCGsResponse(BaseModel):
    count: int
    note: str | None = None  # "community_leader field not configured" if absent

class MinistryRow(BaseModel):
    ministry: str
    count: int

class MinistryBreakdownResponse(BaseModel):
    rows: list[MinistryRow]
    total: int
    note: str | None = None  # "ministry field not configured" if absent

class ZoneRow(BaseModel):
    zone: str
    count: int
    pct: float

class ZoneBreakdownResponse(BaseModel):
    rows: list[ZoneRow]
    total: int
    note: str | None = None

class PepsolRow(BaseModel):
    stage: str   # "encounter_graduate"|"prepare_to_serve"|"sol1"|"sol2"|"sol3"|"graduate"
    label: str   # canonical display label
    count: int

class PepsolBreakdownResponse(BaseModel):
    rows: list[PepsolRow]
    total: int
    note: str | None = None

# ---- Section C: Attendance grid ----
class GridCell(BaseModel):
    value: int
    flag: Literal["high", "low", "normal"] = "normal"

class GridRow(BaseModel):
    metric_key: str   # e.g. "sunday_8am_unique" | "powerhouse_total"
    metric_label: str
    cells: list[GridCell]  # ordered oldest→newest

class AttendanceGridResponse(BaseModel):
    bucket: str
    period_labels: list[str]   # ordered oldest→newest (ISO month or ISO week)
    rows: list[GridRow]
    last_updated_at: datetime | None  # from job_runs WHERE job_name='recompute_member_status'

# ---- Section D: SMA averages ----
class SMASeries(BaseModel):
    window: str       # "4wk"|"8wk"|"12wk"|"3mo"|"6mo"|"9mo"|"12mo"
    value: float | None  # null when < window data points exist

class SMAResponse(BaseModel):
    metric: str
    as_of: date
    averages: list[SMASeries]

# ---- Monthly report ----
class MonthlyMetricRow(BaseModel):
    metric_key: str
    metric_label: str
    prev_month: int
    current_month: int
    diff: int
    diff_pct: float | None
    avg_12mo: float | None
    diff_vs_12mo_avg: float | None   # (current - avg_12mo) / avg_12mo × 100

class MonthlyReportResponse(BaseModel):
    year: int
    month: int
    prev_year: int
    prev_month_num: int
    rows: list[MonthlyMetricRow]

# ---- Monthly zone ----
class MonthlyZoneRow(BaseModel):
    zone: str
    prev_month: int
    current_month: int
    diff: int
    distribution_pct: float

class MonthlyZoneResponse(BaseModel):
    year: int
    month: int
    rows: list[MonthlyZoneRow]
    total_prev: int
    total_current: int

# ---- Monthly community reports ----
class MonthlyCommunityReportRow(BaseModel):
    category: str  # "Kids"|"Youth"|"Young Adults"|"Young Professionals"|"Young Couples"|
                   # "Adult"|"Adult Men"|"Adult Women"|"Outreach"|"Ministry"|"Administrative"
    count: int

class MonthlyCommunityReportsResponse(BaseModel):
    year: int
    month: int
    rows: list[MonthlyCommunityReportRow]
    total: int
    note: str | None  # "Community report data not yet available" when S22 table absent

# ---- Monthly forecast ----
class ForecastRow(BaseModel):
    metric_key: str
    metric_label: str
    projected_this_month: float
    actual_this_month: int
    diff: int
    diff_pct: float | None
    next_month_forecast: float

class MonthlyForecastResponse(BaseModel):
    year: int
    month: int
    rows: list[ForecastRow]
    method: Literal["linear_trend", "sma_12mo"]

# ---- General trend (retained) ----
class TrendPoint(BaseModel):
    period: str
    count: int
    series: str | None = None

class AttendanceTrendResponse(BaseModel):
    bucket: str
    start: date
    end: date
    points: list[TrendPoint]
    series_keys: list[str]

# ---- Segment breakdown ----
class SegmentBucket(BaseModel):
    segment: str
    count: int

class SegmentBreakdownResponse(BaseModel):
    dimension: str
    label: str
    start: date
    end: date
    buckets: list[SegmentBucket]
    total: int

# ---- Assimilation funnel ----
class FunnelStage(BaseModel):
    stage: str    # "new_friend"|"regular_attendee"|"regular_member"
    label: str
    count: int
    new_this_month: int

class AssimilationFunnelResponse(BaseModel):
    as_of: date
    stages: list[FunnelStage]   # always 3 in ladder order
    total: int

# ---- Leader rollups ----
class LeaderRow(BaseModel):
    leader_contact_id: int
    leader_name: str
    count: int

class LeaderRollupResponse(BaseModel):
    relationship: str
    label: str
    rows: list[LeaderRow]
    total_leaders: int
    has_more: bool
```

### Services / workers / business rules

**CREATE `backend/app/services/reporting.py`** (~650 LOC). The router calls service functions; no business logic in the router. All functions are `async def` with full type hints. No N+1 queries.

#### Session taxonomy constants

**(per CN-15)** The reporting session bucket is a **static Python mapping over `events.event_type` + `events.session_time`** — there is **no `session_type` column**. Each `_Cat` maps a bucket key to an `(event_type, session_time)` filter tuple (each side `None` = "any").

```python
# reporting.py — top of file
from typing import NamedTuple

class _Cat(NamedTuple):
    key: str
    label: str
    event_type: str | None     # (per CN-15) matches events.event_type; None = any
    session_time: str | None   # (per CN-15 + 2026-06-22 ruling) matches events.session_time ∈ {"8AM","10AM","3PM"}; None = any

# (per CN-15) Static SESSION_CATEGORIES mapping — derives the reporting session
# bucket from (event_type, session_time). There is NO session_type column;
# session_time is the single canonical column (2026-06-22
# ruling, values "8AM"/"10AM"/"3PM", Sunday-only). Non-Sunday buckets are keyed
# by event_type alone (session_time = None). event_type values are the 6 canonical
# strings (master CN-05).
SESSION_CATEGORIES: list[_Cat] = [
    _Cat("morning_prayer",  "Morning Prayer",     "Prayer Meeting",     None),
    _Cat("powerhouse",      "Powerhouse",         "Powerhouse",         None),
    _Cat("community",       "Community",          "Community Meeting",  None),
    _Cat("events",          "Events",             "Event",              None),
    _Cat("sunday_all",      "Sunday Service All", "Sunday Celebration", None),
    _Cat("sunday_8am",      "Sunday 8AM",         "Sunday Celebration", "8AM"),
    _Cat("sunday_10am",     "Sunday 10AM",        "Sunday Celebration", "10AM"),
    _Cat("sunday_3pm",      "Sunday 3PM",         "Sunday Celebration", "3PM"),
]
```
> The `event_type`/`session_time` token values above must match S04's committed casing/vocabulary; reconcile at implementation (§10). The bucket is **derived in Python from these two columns** (per CN-15), never from a `session_type` column.

#### A. Session summary — `session_summary(db, start, end, bucket)`

**Algorithm:**
1. Determine bucket expression: on Postgres `date_trunc('month', events.start_at)` or `date_trunc('week', events.start_at)`; on SQLite `strftime('%Y-%m', events.start_at)` or `strftime('%Y-W%W', events.start_at)`.
2. For each `_Cat`, execute a single aggregate query:
   ```sql
   SELECT
       <bucket_expr> AS period,
       COUNT(DISTINCT p.contact_id) AS unique_count,
       COUNT(p.id)                  AS total_count
   FROM participants p
   JOIN events e ON e.id = p.event_id
   WHERE p.status = 'attended'
     AND e.start_at BETWEEN :start AND :end
     AND (<event_type filter if not None>)     -- per CN-15
     AND (<session_time filter if not None>)   -- per CN-15
   GROUP BY period
   ORDER BY period
   ```
3. Fill period gaps in Python (months or weeks with zero participants return zeroes, not absent rows).
4. `sunday_all_unique` must not double-count a contact who attends both 8AM and 10AM on the same day or in the same week. Because unique_count uses `COUNT(DISTINCT p.contact_id)` with `event_type='Sunday Celebration'` (per CN-15) and no session_time filter, DISTINCT already handles this correctly — one contact attending two Sunday sessions in the same bucket appears once.
5. Compute rollup lines:
   - `total_unique` = `COUNT(DISTINCT p.contact_id)` across all categories in the range (one query with no event_type/session_time filter and `status='attended'`).
   - `total_database` = `COUNT(c.id) WHERE c.is_deleted=false` (period-independent; scalar).
   - `total_with_community` = `total_unique` + distinct contacts from `community_report` rows in the range (if S22 `community_report` table exists; otherwise equals `total_unique`).
6. Return `SessionSummaryResponse` with nested `periods` list.

**SQLite compatibility:** use `strftime`; `FILTER (WHERE ...)` aggregate not supported in SQLite — use `SUM(CASE WHEN ... THEN 1 ELSE 0 END)` instead; `COUNT(DISTINCT ...)` is supported in SQLite.

**Edge cases:**
- Date range with zero events → all categories return zero for every period (no 404).
- `community` category: if no events with `event_type='Community Meeting'` exist yet, returns zeros (per CN-15).
- `events` category matches `event_type='Event'` (per CN-15; match S04's committed casing for the canonical value `"Event"` — use `ILIKE` or normalize; document which casing S04 commits to and match it).

#### B. Summary tile queries

**`contact_info(db)`** — reads S23 snapshot columns:
```sql
-- Postgres (FILTER aggregate):
SELECT
  COUNT(*)                                                  AS all_contacts,
  COUNT(*) FILTER (WHERE is_regular = true)                AS regular_count,
  COUNT(*) FILTER (WHERE is_regular = false)               AS non_regular_count,
  COUNT(*) FILTER (WHERE is_regular = true AND is_connected = true)  AS conn_reg,
  COUNT(*) FILTER (WHERE is_regular = true AND is_connected = false OR
                          is_regular = true AND is_connected IS NULL) AS not_conn_reg
FROM contacts
WHERE is_deleted = false
```
**Graceful fallback when snapshot NULL:** if `COUNT(*) FILTER (WHERE is_regular IS NOT NULL) = 0`, compute `is_regular` inline as `attendance_count > 9`. If `attendance_count` is also NULL, treat as non-regular. Never return 500.

**`status_summary(db)`:** counts `is_active = true/false` from contacts `WHERE is_deleted=false`. Treat `is_active IS NULL` as inactive (pre-S23). Also count intersections with `is_regular`.

**`tier_summary(db)`:** group by `tier` for `is_deleted=false AND tier IS NOT NULL AND tier != 'tier0'`. Always return exactly four rows in canonical order `tier1/tier2/tier3/inactive` — pad with zeroes if a tier has no contacts.

**`leaders_with_cgs(db)`:** discover the `contact_reference` custom field named `community_leader` from `custom_field_def WHERE name='community_leader' AND data_type='contact_reference' AND is_active=true`. Count contacts referenced as leaders by searching `contacts.custom_data` for that field name having a non-null value. On Postgres: `COUNT(*) WHERE custom_data->>'community_leader' IS NOT NULL AND custom_data->>'community_leader' != '' AND is_deleted=false`. On SQLite: `WHERE json_extract(custom_data, '$.community_leader') IS NOT NULL`. If field not configured: return `count=0, note="community_leader field not configured"`.

**`ministry_breakdown(db)`:** discover `custom_field_def WHERE name='ministry' AND is_active=true`. If `data_type='multiselect'`: on Postgres use `jsonb_array_elements_text(contacts.custom_data->'ministry')` unnest + GROUP BY; on SQLite: pull `custom_data` for all contacts and expand in Python. Include all option values from `custom_field_def.options` even if count is 0 (so zero-count ministries appear). Order by option `weight`. Canonical ministry list from reports.md §C.5 for label mapping: Service Pastors, Ushering, IT-Multimedia/Monitoring/Reports, Music, Dance, Parking, Prayer Gatherings, Welcome Center, Admin Office, Stewardship, Housekeeping, Property Custodian, Arise & Build, Grievance, Learning & Dev, Community Heads, Outreach Facilitators, Missions, Visitations.

**`zone_breakdown(db)`:** same pattern for the zone/community custom field (name `community` or `zone` — discover from `custom_field_def`). Include a `"No Community"` bucket for contacts with null/empty zone. Distribution `pct = count / total × 100`. Zone labels from reports.md §C.6: Kids, Youth, Young Adults, Young Professionals, Young Couples, Adult, Adult Men, Adult Women.

**`pepsol_breakdown(db)`:** discover the PEPSOL stage custom field (name `pepsol` or `discipleship_stage`). Fixed stage order from reports.md §C.7: Encounter Graduate → Prepare to Serve → SOL 1 → SOL 2 → SOL 3 → Graduate. Return all stages even if count=0.

#### C. Attendance grid — `attendance_grid(db, bucket, periods)`

1. Determine the most recent `periods` time buckets ending today/now.
2. For each bucket, for each `SESSION_CATEGORY`: compute `unique_count` and `total_count`.
3. Structure: rows = one `GridRow` per metric (16 rows = 8 categories × 2 [unique/total] + rollup rows for `total_unique`, `total_database`). Columns = period buckets, oldest first.
4. **Conditional coloring:** within each `GridRow`, compute `mean` and `std` of all cell values. Flag `"high"` if `value > mean + 0.5 * std`, `"low"` if `value < mean - 0.5 * std`, else `"normal"`. Skip coloring if all cells are zero (would produce NaN std).
5. `last_updated_at`: query `SELECT MAX(finished_at) FROM job_runs WHERE job_name='recompute_member_status' AND status='success'`. Guard with `try/except NoSuchTableError` → return `None` if `job_runs` absent (S16 not yet landed).

#### D. SMA averages — `sma_averages(db, metric_key, as_of)`

- `metric_key` must be one of the SESSION_CATEGORIES keys appended with `_unique` or `_total` (e.g. `sunday_all_unique`). Unknown key → 422.
- Pull the last 52 weekly or 15 monthly period counts (one DB round-trip; no per-window query).
- For each window (`4wk/8wk/12wk/3mo/6mo/9mo/12mo`): `value = mean of the last N completed buckets before as_of`. If fewer data points than window size, return `value=null`.
- Algorithm: fetch all periods in descending order, slice, compute mean in Python. No external ML library.

#### D. Monthly report — `monthly_report(db, year, month)`

- Determine `prev_month` = preceding calendar month (handle January → December prior year).
- For each metric (SESSION_CATEGORIES, both unique + total + rollup lines):
  - `current_month` = aggregate count for the given `year-month`.
  - `prev_month` = count for the preceding month.
  - `avg_12mo` = mean of the 12 calendar months ending with `prev_month` (i.e. not including current). If fewer than 12 months of data exist, use available months.
  - `diff = current_month - prev_month`.
  - `diff_pct = (diff / prev_month × 100)` if `prev_month > 0` else `null`.
  - `diff_vs_12mo_avg = (current_month - avg_12mo) / avg_12mo × 100` if `avg_12mo > 0` else `null`.
- Return rows in canonical session-taxonomy order.

#### D. Monthly zone — `monthly_zone(db, year, month)`

- Zone field discovery same as `zone_breakdown`.
- For each contact, derive zone from `contacts.custom_data` at the time of the query (not historical snapshots — no zone history table).
- For a given month: `current_month = COUNT(DISTINCT p.contact_id) WHERE p.status='attended' AND month(e.start_at) = month AND c.custom_data->>'zone' = zone_value`.
- `prev_month` = same for the preceding month.
- `distribution_pct = current_month / total_current × 100` (use `SUM` of all zones as denominator to handle the `"No Community"` bucket correctly).
- Return `"No Community"` bucket for contacts with null/empty zone value.

#### D. Monthly community reports — `monthly_community_reports(db, year, month)`

- Source: `community_report` table (S22). Group by `zone` mapped to canonical category labels.
- Guard on table existence: `SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name='community_report')`. On SQLite, catch `OperationalError` on first query attempt.
- If table absent → return `rows=[], total=0, note="Community report data not yet available"`.
- When table present: `SELECT zone, SUM(attendee_count) FROM community_report WHERE YEAR(created_at)=:year AND MONTH(created_at)=:month GROUP BY zone`.

#### E. Monthly forecast — `monthly_forecast(db, year, month)`

- For each metric:
  - `actual_this_month` = count so far in `{year}-{month}` (may be partial if current month).
  - Pull the last 6 completed calendar months of data.
  - **Method `linear_trend`:** if ≥ 3 data points, fit OLS slope over 6-point series (x = month index 0..5, y = counts). Extrapolate: `projected_this_month = y_at_month_6` using the fit. `next_month_forecast = y_at_month_7`.
  - **Fallback `sma_12mo`:** if < 3 data points, use the 12-month SMA value as both this-month projection and next-month forecast.
  - `diff = actual_this_month - projected_this_month` (round to int).
  - `diff_pct = diff / projected_this_month × 100` if projected > 0 else `null`.
- Implementation: pure Python arithmetic on fetched lists; no external ML library.
- `method` field: `"linear_trend"` or `"sma_12mo"` per fallback outcome.

#### General trend, segment, funnel, leader rollups

**`attendance_trend(db, start, end, bucket, event_type, split_by_event_type)`:**
`SELECT date_trunc(bucket, e.start_at), COUNT(p.id) ... JOIN events e ON e.id=p.event_id WHERE p.status='attended' AND e.start_at BETWEEN ... [AND e.event_type=:event_type] GROUP BY period [, e.event_type]`. Gap-fill zeroes in Python for missing periods. Rewired to use `Participant`/`Event` models (S01 renames).

**`attendance_by_segment(db, dimension, start, end, event_type)`:**
- **Whitelist** `CORE_DIMENSIONS = {"contact_subtype", "gender", "age_band", "event_type"}` + any active `custom_field_def WHERE entity='contact' AND data_type IN ('select','multiselect') AND is_active=true` as `"cf:<name>"`. Non-whitelisted → raise `ValueError` (router maps to 422).
- Distinct-attendee count per bucket: `COUNT(DISTINCT p.contact_id)`.
- For `multiselect`: Postgres `jsonb_array_elements_text` unnest; SQLite: Python expansion after `json_extract`.
- `"(none)"` bucket for NULL/empty values.
- Cap 30 buckets + `"(other)"` rollup for overflow.

**`assimilation_funnel(db, as_of)`:**
Three stages in canonical order: `new_friend / regular_attendee / regular_member`. Derive from `contacts.contact_subtype WHERE is_deleted=false`. `new_this_month` = contacts in that subtype with `created_at` in the calendar month of `as_of`. Always return all three stages even if count=0. Soft-deleted contacts excluded.

**`leader_rollups(db, relationship, limit, offset)`:**
- `relationship` must be an active `contact_reference` custom field name from `custom_field_def`. Non-whitelisted → 422.
- Reverse lookup: `json_extract(custom_data, '$.<relationship>')` (Postgres: `custom_data->>'<relationship>'`). GROUP BY the referenced contact ID. Count = number of contacts referencing that leader ID.
- Resolve leader name from `contacts`: `LEFT JOIN` on id; soft-deleted → `"(deleted contact)"`.
- Order by count DESC. `limit` clamped ≤ 200. Return `has_more = total_distinct_leaders > offset + limit`.

#### Performance

At 33.6k participants + 1,440 contacts, all aggregates are sub-100ms on Postgres with the specified indexes. SMA/forecast are Python arithmetic on ≤60 data points. CSV exports reuse the same service function results via `StreamingResponse` + async generator (S05 pattern). No N+1 queries: batch name resolution in `leader_rollups` uses `ANY(:ids)` array join.

#### SQLite compatibility (test suite)

| Postgres construct | SQLite equivalent used in code |
|---|---|
| `date_trunc('month', col)` | `strftime('%Y-%m', col)` |
| `date_trunc('week', col)` | `strftime('%Y-W%W', col)` |
| `COUNT(*) FILTER (WHERE ...)` | `SUM(CASE WHEN ... THEN 1 ELSE 0 END)` |
| `jsonb_array_elements_text(col->'key')` | Python expand after `json_extract(col,'$.key')` |
| `col @> '["val"]'::jsonb` | `json_extract(col,'$.key') LIKE ...` |
| Partial index WHERE clause | Plain btree on SQLite |

Detect dialect: `db.get_bind().dialect.name == "postgresql"` (or pass a `is_sqlite: bool` flag from the DB engine in the service layer).

### File-by-file change list (backend)

| Action | File | What changes |
|---|---|---|
| CREATE | `backend/app/services/reporting.py` | All query functions, `SESSION_CATEGORIES`, `CORE_DIMENSIONS`, `AGE_BANDS`, `ReportRegistry`. ~650 LOC. |
| MODIFY | `backend/app/routers/analytics.py` | Replace stale model imports (`CiviCRMEvent`→`Event`, `Attendance`→`Participant`, `Log`→`Log`). Add all 27 new endpoints (ministry + exports). Remove `push_status` from `export_attendance_csv` select/header (analytics.py:131,144). Add `CustomFieldDef` import. |
| MODIFY | `backend/app/schemas.py` | Add all response schemas listed above. |
| MODIFY | `backend/app/dependencies.py` | Add `require_reporting = require_volunteer` shim after `require_volunteer` (per CN-17 — forward-compatible; S15 patches it to add `viewer`; do NOT write a permanent bespoke function). |
| MODIFY | `backend/app/models.py` | Add `Index(...)` entries to `Contact`, `Event`, `Participant` `__table_args__`. GIN on Postgres, btree on SQLite. |
| CREATE | `backend/alembic/versions/<rev>_s14_reporting_indexes.py` | Index + column-guard migration from §3. |
| CREATE | `backend/tests/test_reporting.py` | Full test file per §8. |

---

## 5. Frontend

### Pages / routes / components

**MODIFY `frontend/src/pages/DashboardPage.tsx`** (route `/dashboard`, App.tsx:62):
- Add two tabs: **Ministry Reports** (default, visible to admin + viewer) and **Operations** (admin-only, contains existing recognition tiles moved here unchanged).
- Ministry Reports tab: renders `<ReportFilterBar>` + a `grid grid-cols-1 md:grid-cols-2 gap-4` of all ministry tile components.
- Replace the inline `downloadCsv` at DashboardPage.tsx:9–19 with the shared `downloadCsv` from `@/lib/downloadCsv`.
- The Operations tab renders `null` for non-admin (isAdmin guard in JSX — no separate route).

**MODIFY `frontend/src/App.tsx`:**
- Change `/dashboard` guard from `AdminRoute` to `ReportingRoute` (App.tsx:62).

**CREATE `frontend/src/components/layout/ReportingRoute.tsx`:**
Mirrors `AdminRoute.tsx` but passes children when `isAdmin || isViewer`. `isViewer` is read from `authStore` with `useAuthStore((s) => s.isViewer ?? false)` — defaulting to `false` means admin-only until S15 adds the field. No behavior change before S15.

**CREATE `frontend/src/components/reports/ReportFilterBar.tsx`:**
- Fields: date-range start/end (`<input type="date">`), event-type select (options from `GET /analytics/reports` catalog), bucket select (Week / Month).
- Pushes all state to URL search params via `useSearchParams` (shareable/bookmarkable).
- Layout: stacks vertically on mobile (`flex flex-col gap-2`), horizontal row on `md:` (`md:flex-row md:items-end`).
- Emits no callbacks; consumers read via `useReportFilters`.

**CREATE `frontend/src/hooks/useReportFilters.ts`:**
Reads `useSearchParams()`, parses and validates date strings and enum values, returns a stable typed `ReportFilters` object. Re-derives only when search-param string changes (avoid re-renders on unrelated navigation).

```typescript
export interface ReportFilters {
  start: string;       // ISO date "YYYY-MM-DD"
  end: string;
  bucket: 'week' | 'month';
  eventType: string | undefined;
}
export function useReportFilters(): ReportFilters { ... }
```

**Ministry tile components** — each self-fetching (independent `useQuery`, no tile crash blocks others):

| File | Chart type | TanStack Query key |
|---|---|---|
| `frontend/src/pages/reports/SessionSummaryTile.tsx` | Table + bar per period | `['report','session-summary', filters]` |
| `frontend/src/pages/reports/ContactInfoTile.tsx` | KPI numbers + small pie | `['report','contact-info']` |
| `frontend/src/pages/reports/StatusTile.tsx` | KPI pairs | `['report','status-summary']` |
| `frontend/src/pages/reports/TierSummaryTile.tsx` | Grouped bar (all/connected/not-connected per tier) | `['report','tier-summary']` |
| `frontend/src/pages/reports/LeadersWithCGsTile.tsx` | Single KPI number | `['report','leaders-with-cgs']` |
| `frontend/src/pages/reports/MinistryBreakdownTile.tsx` | Horizontal bar | `['report','ministry-breakdown']` |
| `frontend/src/pages/reports/ZoneBreakdownTile.tsx` | Horizontal bar + % labels | `['report','zone-breakdown']` |
| `frontend/src/pages/reports/PepsolBreakdownTile.tsx` | Horizontal bar / funnel | `['report','pepsol-breakdown']` |
| `frontend/src/pages/reports/AttendanceGridTile.tsx` | Grid table with conditional cell coloring | `['report','attendance-grid', {bucket, periods}]` |
| `frontend/src/pages/reports/SmaAveragesTile.tsx` | Table (7 SMA columns × N metrics) | `['report','sma-averages', {metric, asOf}]` |
| `frontend/src/pages/reports/MonthlyReportTile.tsx` | Table (prev/current/diff/vs-avg) | `['report','monthly-report', {year, month}]` |
| `frontend/src/pages/reports/MonthlyZoneTile.tsx` | Table + distribution % bar | `['report','monthly-zone', {year, month}]` |
| `frontend/src/pages/reports/MonthlyCommunityReportsTile.tsx` | Table | `['report','monthly-community-reports', {year, month}]` |
| `frontend/src/pages/reports/MonthlyForecastTile.tsx` | Line chart + projection table | `['report','monthly-forecast', {year, month}]` |
| `frontend/src/pages/reports/AttendanceTrendTile.tsx` | Line/area chart | `['report','attendance-trend', filters]` |
| `frontend/src/pages/reports/AssimilationFunnelTile.tsx` | Horizontal stacked bar | `['report','funnel', {asOf}]` |
| `frontend/src/pages/reports/LeaderRollupTile.tsx` | Table + bar | `['report','leader-rollups', {relationship, limit, offset}]` |
| `frontend/src/pages/reports/SegmentBreakdownTile.tsx` | Horizontal bar | `['report','segment', {dimension, ...filters}]` |

**Shared tile wrapper pattern** (inline in each tile, consistent structure):
```tsx
<div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
  <div className="flex items-center justify-between mb-3">
    <h2 className="text-sm font-bold text-foreground">{title}</h2>
    <button onClick={handleExport} aria-label={`Export ${title} CSV`}>
      <Download size={13} />
    </button>
  </div>
  {isLoading ? <LoadingState /> : isError ? <ErrorState onRetry={refetch} /> :
   data?.rows?.length === 0 ? <EmptyState message="No data in this range" /> :
   <...chart or table...>}
</div>
```

**`AttendanceGridTile` conditional coloring:** cells with `flag="high"` get `className="bg-primary/10 text-primary font-semibold"`; `flag="low"` get `className="bg-destructive/10 text-destructive"`; `"normal"` gets no background modifier. No raw hex. Horizontal scroll on mobile via `overflow-x-auto` wrapper. On mobile, default to showing the 4 most-recent period columns (user can scroll right).

**CREATE `frontend/src/services/reports.ts`:**
Typed `api.get(...)` wrappers for all ministry report endpoints (one function per endpoint). Export helper functions that return `Blob` for CSV endpoints (via `{ responseType: 'blob' }`).

```typescript
// Example signatures:
export async function getSessionSummary(params: SessionSummaryParams): Promise<SessionSummaryResponse>
export async function getContactInfo(): Promise<ContactInfoResponse>
export async function getAttendanceGrid(params: GridParams): Promise<AttendanceGridResponse>
export async function exportSessionSummaryCsv(params: SessionSummaryParams): Promise<Blob>
// ...etc
```

**CREATE `frontend/src/types/reports.ts`:**
TypeScript interfaces mirroring all Pydantic response schemas (manual; not generated). Exported individually, not as a barrel-all namespace.

**CREATE `frontend/src/lib/downloadCsv.ts`** (extract from DashboardPage.tsx:9–19):
```typescript
import { api } from '@/services/api';
import { toast } from 'sonner';

export function downloadCsv(url: string, filename: string): void {
  api.get(url, { responseType: 'blob' })
    .then((res) => {
      const blob = new Blob([res.data], { type: 'text/csv' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
    })
    .catch((err) =>
      toast.error(err.response?.data?.detail || 'Export failed')
    );
}
```

**MODIFY `frontend/src/lib/chartColors.ts`:**
Add semantic constants alongside the existing recognition-tier constants (do not change existing exports — backwards compatible):
```typescript
export const SERIES_COLORS: string[] = [
  'hsl(221 83% 53%)',   // blue-600
  'hsl(142 71% 45%)',   // green-500
  'hsl(38 92% 50%)',    // amber-500
  'hsl(0 84% 60%)',     // red-500
  'hsl(271 91% 65%)',   // violet-500
  'hsl(199 89% 48%)',   // sky-500
];
export const FUNNEL_STAGE_COLORS: string[] = [
  'hsl(221 83% 53%)',  // New Friend — blue
  'hsl(142 71% 45%)',  // Regular Attendee — green
  'hsl(48 90% 62%)',   // Regular Member — gold
];
export const TIER_REPORT_COLORS: Record<string,string> = {
  tier1: 'hsl(142 71% 45%)',   // green
  tier2: 'hsl(38 92% 50%)',    // amber
  tier3: 'hsl(25 95% 53%)',    // orange
  inactive: 'hsl(0 84% 60%)',  // red
};
export const ZONE_COLORS: string[] = SERIES_COLORS; // reuse pool
```

### TanStack Query configuration

- `staleTime`: inherit global 5-minute default from `main.tsx:16–23`.
- `placeholderData: (prev) => prev` on all report queries (prevents flash-to-empty on filter change; user sees stale data while new data loads).
- `retry: 1` (global default).
- Query keys always include the full filter object so filter changes invalidate the correct cache entry.

### Zustand

No new Zustand stores. Filter state lives in URL search params. Auth/role read from existing `authStore` (`isAdmin`; `isViewer` added by S15, read defensively).

### Role gating

- Route `/dashboard` uses `ReportingRoute` (admin OR viewer).
- Operations tab: rendered only when `isAdmin` guard in JSX.
- Ministry tiles: visible to admin and (future) viewer.
- Backend enforces `require_reporting` per-endpoint (defense in depth).

### UX loading / empty / error states

- **Loading:** each tile renders `<LoadingState />` from `frontend/src/components/ui/StateViews.tsx` inside its card body. Tiles load independently; a slow tile does not block others.
- **Empty:** `<EmptyState message="No attendance recorded in this range" />` for zero-data responses. Standardize on the shared `EmptyState` component (frontend.md §B), not inline text.
- **Error:** `<ErrorState onRetry={refetch} />` from `StateViews.tsx`; exports call `toast.error(err.response?.data?.detail || 'Export failed')` (AGENTS.md:85).
- **S23 not-yet-run indicator:** `ContactInfoTile`, `StatusTile`, `TierSummaryTile` and `AttendanceGridTile` display a subtle banner (`text-xs text-foreground/50`) reading "Status data not yet computed — run the member status job" when `last_updated_at` is null or when snapshot columns are all NULL.

### Design tokens

All tile cards: `rounded-2xl border border-border bg-card p-4 shadow-sm` (matching DashboardPage.tsx:78). Chart fills from `chartColors.ts`. No raw hex anywhere. Conditional coloring uses `bg-primary/10`, `bg-destructive/10` (token-based). Dark mode works automatically via token classes.

### Mobile-first / desktop

- Tile grid: `grid grid-cols-1 md:grid-cols-2 gap-4`.
- `AttendanceGridTile` + `MonthlyReportTile`: `overflow-x-auto` with a wide inner table for horizontal scroll on mobile.
- `ReportFilterBar`: vertical stack on mobile, `md:flex-row` row layout.
- `ResponsiveContainer` for all Recharts charts. Explicit `height` (160–220px) set per tile based on data density.
- Bottom `<BottomNav>` tab updated to include a "Reports" entry for non-admin users once S15 ships (S14 ships it under the existing "Dashboard" admin tab; no BottomNav change in S14).

### File-by-file change list (frontend)

| Action | File | Notes |
|---|---|---|
| MODIFY | `frontend/src/pages/DashboardPage.tsx` | Add tabs; move existing tiles to Operations tab; render Ministry tiles; replace inline downloadCsv. |
| MODIFY | `frontend/src/App.tsx` | Change `/dashboard` guard from `AdminRoute` to `ReportingRoute`. |
| CREATE | `frontend/src/components/layout/ReportingRoute.tsx` | Admin OR isViewer (forward-compat stub). |
| CREATE | `frontend/src/components/reports/ReportFilterBar.tsx` | Date range + bucket + event_type. URL params. |
| CREATE | `frontend/src/hooks/useReportFilters.ts` | Parse and stabilize URL search params → `ReportFilters`. |
| CREATE | `frontend/src/lib/downloadCsv.ts` | Extracted + improved from DashboardPage.tsx:9–19. |
| MODIFY | `frontend/src/lib/chartColors.ts` | Add `SERIES_COLORS`, `FUNNEL_STAGE_COLORS`, `TIER_REPORT_COLORS`, `ZONE_COLORS`. |
| CREATE | `frontend/src/services/reports.ts` | Typed API wrappers for all ministry endpoints. |
| CREATE | `frontend/src/types/reports.ts` | TypeScript interfaces. |
| CREATE | `frontend/src/pages/reports/SessionSummaryTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/ContactInfoTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/StatusTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/TierSummaryTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/LeadersWithCGsTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/MinistryBreakdownTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/ZoneBreakdownTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/PepsolBreakdownTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/AttendanceGridTile.tsx` | Conditional coloring; horizontal scroll; mobile 4-col default. |
| CREATE | `frontend/src/pages/reports/SmaAveragesTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/MonthlyReportTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/MonthlyZoneTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/MonthlyCommunityReportsTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/MonthlyForecastTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/AttendanceTrendTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/AssimilationFunnelTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/LeaderRollupTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/SegmentBreakdownTile.tsx` | — |
| CREATE | `frontend/src/pages/reports/__tests__/` | All frontend test files (§8). |

---

## 6. Migration / data

No data migration. S14 reads the canonical schema established by S01/S03/S04/S05/S23. The only schema delta is the index migration in §3 (additive, reversible, existence-guarded, idempotent).

**S23 data dependency:** Contact-info, status, and tier tiles read S23 snapshot columns. These are NULL until S23 has run its first nightly job. The graceful fallback (§4 service business rules) ensures tiles render before S23 lands. Tiles display the "not yet computed" banner when `last_updated_at` is NULL.

**S22 data dependency:** Monthly community-reports tile reads the `community_report` table (S22). The service function guards on table existence and returns the `note` field when unavailable — tile degrades to "Data not yet available" with no error.

**Custom-field data dependency:** Ministry, zone, PEPSOL tiles require `custom_field_def` rows (S02) and populated `contacts.custom_data` (S06/S03 manual edit). Before data is loaded, tiles return zero-count rows for all configured options, not errors.

**Legacy export cleanup:** `backend/app/routers/analytics.py` `export_attendance_csv` currently selects `Attendance.push_status` (analytics.py:130–131) and writes it as a column header. S01 drops this column. S14 removes `push_status` from the select and from the header row — this is a coordinated change with S01 and must be gated accordingly (safe to do in S14 if S01 has already landed; guarded by the model import change anyway).

---

## 7. Acceptance criteria

1. `GET /analytics/reports` returns a catalog entry for every report key: `session-summary`, `contact-info`, `status-summary`, `tier-summary`, `leaders-with-cgs`, `ministry-breakdown`, `zone-breakdown`, `pepsol-breakdown`, `attendance-grid`, `sma-averages`, `monthly-report`, `monthly-zone`, `monthly-community-reports`, `monthly-forecast`, `attendance-trend`, `attendance-by-segment`, `assimilation-funnel`, `leader-rollups`. Each entry has a non-empty `export_path`.

2. `GET /analytics/session-summary?bucket=month&start=2026-01-01&end=2026-06-30` returns for each of the 8 session categories a `unique_count` (COUNT DISTINCT contact_id) and `total_count` (COUNT rows). Months with zero events for that category return zeroes (not absent rows).

3. A contact attending both the 8AM and 10AM Sunday service in the same month appears exactly once in `sunday_all_unique` for that month (DISTINCT enforced at `event_type='Sunday Celebration'` level per CN-15, not per session_time).

4. `GET /analytics/contact-info` returns the correct regular / non-regular / connected breakdowns when S23 snapshot columns are populated. When all `is_regular` are NULL, the endpoint falls back to `attendance_count > 9` and returns 200 (not 500).

5. `GET /analytics/tier-summary` returns exactly four rows in the order `tier1 / tier2 / tier3 / inactive`, each with `all_members / connected / not_connected`. Rows for tiers with zero contacts still appear with zero values.

6. `GET /analytics/attendance-grid?bucket=month&periods=12` returns a matrix with rows for each session metric (8 categories × 2 = 16 metric rows + rollup rows) and exactly 12 period-label columns, oldest first. Each cell has a `flag` field (`high / low / normal`).

7. A cell with a value more than 0.5 standard deviations above its row mean is flagged `"high"`. A cell more than 0.5 std below is flagged `"low"`. A row where all values are zero has all cells flagged `"normal"` (no NaN division).

8. `GET /analytics/sma-averages?metric=sunday_all_unique&as_of=2026-06-01` returns all 7 SMA windows. Windows where fewer data points exist than the window size return `null` (not 0). The 12-week SMA is null if fewer than 12 completed weeks of data exist.

9. `GET /analytics/monthly-report?year=2026&month=6` returns one row per session metric. The `avg_12mo` field is the simple mean of data from June 2025 through May 2026 (12 months prior to current; current month excluded). `diff = current_month - prev_month`.

10. `GET /analytics/monthly-forecast?year=2026&month=6` returns `projected_this_month` and `next_month_forecast` for each metric. `method="linear_trend"` when ≥ 3 prior months exist; `method="sma_12mo"` otherwise. The response is mathematically consistent with a least-squares fit over the most recent 6 completed months.

11. `GET /analytics/monthly-community-reports?year=2026&month=6` returns `note="Community report data not yet available"` and empty rows when the `community_report` table does not exist (S22 not landed). Does not return 500.

12. `GET /analytics/attendance-by-segment?dimension=cf:community` returns 200 when a `select` or `multiselect` custom field named `community` exists and is active; returns 422 for any non-whitelisted dimension.

13. A contact with a multiselect `ministry=["Worship","Ushering"]` appears in both the `Worship` and `Ushering` rows of `GET /analytics/ministry-breakdown` (counted in each bucket independently).

14. `GET /analytics/assimilation-funnel` always returns exactly three stages in canonical order (`new_friend → regular_attendee → regular_member`). Soft-deleted contacts are excluded. Stages with zero contacts return `count=0`.

15. `GET /analytics/leader-rollups?relationship=invited_by` returns leaders sorted descending by count. A reference to a soft-deleted contact resolves to the string `"(deleted contact)"` and is still counted. `limit=9999` is clamped to ≤ 200. A non-whitelisted `relationship` returns 422.

16. All ministry report endpoints return 200 for `admin` and `volunteer` tokens (the `require_reporting` shim `= require_volunteer` per CN-17) and, after S15 patches the shim, also `viewer` tokens; 401 for unauthenticated callers. Existing operational endpoints (`/tier-distribution`, `/queue-health`, etc.) still require admin.

17. Every ministry endpoint has a corresponding `GET /analytics/export/<key>` returning `Content-Type: text/csv`, `Content-Disposition: attachment; filename=<key>.csv`, a header row, and data rows matching the JSON endpoint's data for the same params.

18. `GET /analytics/export/attendance` (the existing legacy export) does NOT include `push_status` in the header row or any data row (S01 column deleted).

19. `/dashboard` renders two tabs: Ministry Reports (default, visible to admin + viewer) and Operations (admin-only). Each ministry tile independently loads, shows `LoadingState` during fetch, `EmptyState` on zero data, `ErrorState` with retry on network failure. No tile crash blocks the others.

20. `AttendanceGridTile` cells flagged `"high"` have a green token tint (`bg-primary/10`); cells flagged `"low"` have a red token tint (`bg-destructive/10`). No hardcoded hex. Dark mode tested.

21. Changing any filter in `ReportFilterBar` updates the URL query string and triggers only the affected tiles to refetch (verified by query-key array change). Previously-loaded tiles retain their last data (`placeholderData`) during the refetch instead of flashing empty.

22. The index migration applies cleanly on Postgres (`alembic upgrade head` twice = idempotent). The GIN index is created only on Postgres (not SQLite). `alembic downgrade -1` drops exactly the indexes this migration created (no orphan indexes).

23. `ruff check app` passes on all backend additions. `npm run build` and `npm run lint` pass with zero errors on all new frontend files.

24. The `session_time` guard column is added by the migration only if absent (idempotent whether S04 has or has not run). **(per CN-15)** No `session_type` column is created.

---

## 8. Test plan

### Backend — `backend/tests/test_reporting.py`

**Environment:** `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db`, `REDIS_URL=memory://`, `ENVIRONMENT=test`.

**Shared fixture** (`@pytest.fixture(scope="function")`): seeds the SQLite test DB with:
- 12 contacts: mix of `contact_subtype` (new_friend/regular_attendee/regular_member), `tier` (tier0/tier1/tier2/tier3/inactive/NULL), `is_active`/`is_regular`/`is_connected` values; 1 `is_deleted=true`. Include 3 contacts with `custom_data={"ministry":["Ushering","Music"],"community":"Young Adults","invited_by":<leaderA_id>}` (multiselect ministry), 2 with `invited_by=<leaderA_id>`, 1 with `invited_by=<leaderB_id>`.
- `custom_field_def` rows: `ministry` (multiselect), `community` (select), `invited_by` (contact_reference), `community_leader` (contact_reference).
- 3 `event_series`. 9 `events` spanning 3 calendar months with `(event_type, session_time)` combinations (per CN-15) covering `Prayer Meeting`, `Powerhouse`, `Sunday Celebration` (session_time `8AM`/`10AM`/`3PM`), `Community Meeting`, `Event`.
- 20 `participants` rows: mostly `status='attended'`, a few `status='registered'` (excluded from unique/total counts).
- 1 admin `User`, 1 volunteer `User`.

**Tests (one assertion per `assert`, descriptive names):**

- `test_reports_catalog_all_keys` — `GET /analytics/reports`: all 18 expected keys present; each has a non-empty `export_path` field.
- `test_session_summary_unique_leq_total` — for every category and every period, `unique_count <= total_count`.
- `test_session_summary_sunday_deduplication` — seed one contact attending both 8AM and 10AM events in the same month; `sunday_all_unique=1` for that month.
- `test_session_summary_gap_fill` — a month with zero Sunday events returns a period row with `unique_count=0` for `sunday_all`, not an absent row.
- `test_session_summary_new_contacts` — seed one contact created this month who attended; appears in `new_contacts` count.
- `test_contact_info_regular_connected_counts` — counts match seeded snapshot values; `pct` values sum to 100.
- `test_contact_info_fallback_when_snapshot_null` — set all `is_regular=None`; tile returns 200 and falls back to `attendance_count > 9`; no 500.
- `test_contact_info_excludes_deleted` — soft-deleted contact not counted in `all_contacts`.
- `test_status_tile_active_inactive_totals` — correct active/inactive; soft-deleted excluded.
- `test_tier_summary_always_four_rows` — even when no tier2 contacts exist, four rows returned in canonical order.
- `test_tier_summary_zero_row_present` — a tier with zero contacts has `all_members=0, connected=0, not_connected=0`.
- `test_leaders_with_cgs_count` — seed 2 contacts with `community_leader` populated; returns `count=2`.
- `test_leaders_with_cgs_no_field` — drop the `custom_field_def` row; returns `count=0, note=...`.
- `test_ministry_breakdown_multiselect` — contact with `ministry=["Ushering","Music"]` counted in both rows.
- `test_zone_breakdown_distribution_pct_sums_100` — assert `sum(r.pct for r in rows) ≈ 100.0` (float tolerance ±0.5).
- `test_zone_breakdown_no_community_bucket` — contacts with no community value appear in `"No Community"` row.
- `test_attendance_grid_dimensions` — 16 metric rows (8 × 2), `periods=3` → 3 period-label columns.
- `test_attendance_grid_conditional_coloring_high` — seed a month with 3× the mean value; that cell has `flag="high"`.
- `test_attendance_grid_all_zeros_no_nan` — all periods empty; all cells have `flag="normal"`.
- `test_attendance_grid_last_updated_at_null_when_no_job_runs` — `job_runs` table absent or empty; returns `last_updated_at=null`.
- `test_sma_null_insufficient_data` — only 2 months of data; 12-month SMA returns `null`.
- `test_sma_value_correct` — seed 12 months; 12-month SMA value = arithmetic mean of the 12 values (±0.1 tolerance).
- `test_monthly_report_structure` — all session-taxonomy rows present; `diff = current_month - prev_month`.
- `test_monthly_report_avg_12mo_excludes_current` — the 12-month average does not include the current month.
- `test_monthly_forecast_linear_trend_method` — with 6+ months data: `method="linear_trend"`.
- `test_monthly_forecast_sma_fallback` — with < 3 months data: `method="sma_12mo"`.
- `test_monthly_community_reports_graceful_table_absent` — mock table not existing; returns `note` field, HTTP 200.
- `test_attendance_trend_gap_fill` — a week with no events returns a zero-count point.
- `test_segment_distinct_attendees` — one contact attending 2 events in range; segment count = 1.
- `test_segment_invalid_dimension_422` — returns HTTP 422.
- `test_segment_multiselect_both_buckets` — contact with `ministry=["Ushering","Music"]` increments both buckets.
- `test_assimilation_funnel_ladder_order` — always three stages in canonical order.
- `test_assimilation_funnel_new_this_month` — contact created this month increments `new_this_month`.
- `test_assimilation_funnel_excludes_deleted` — soft-deleted contact not counted.
- `test_leader_rollups_sorted_by_count_desc` — leaderA (count=2) before leaderB (count=1).
- `test_leader_rollups_deleted_leader_label` — soft-deleted leader displays `"(deleted contact)"`, still counted.
- `test_leader_rollups_limit_clamped` — `limit=9999` returns ≤200 rows; `has_more` is correct.
- `test_leader_rollups_invalid_relationship_422` — non-whitelisted field name → 422.
- `test_export_endpoints_csv_headers` — parametrized over all 9 export keys: returns `text/csv`, non-empty body with header row matching the data endpoint's fields.
- `test_export_attendance_no_push_status` — `/analytics/export/attendance` header row does not contain the string `push_status`.
- `test_reporting_access_control_unauthenticated` — 401.
- `test_reporting_access_control_volunteer_403` — volunteer token on ministry endpoints → 403.
- `test_reporting_access_control_admin_200` — admin token on all ministry endpoints → 200.
- `test_operational_endpoints_still_require_admin` — `/analytics/tier-distribution` returns 403 for volunteer.
- `test_migration_index_exists_on_test_db` — `inspect(engine).get_indexes('participants')` contains `ix_participants_status_event`.

### Frontend — `frontend/src/pages/reports/__tests__/`

- `reportFilterBar.test.tsx` — editing start/end date updates URL search params; bucket toggle changes URL; filter state emits correct `ReportFilters` object.
- `sessionSummaryTile.test.tsx` — mocked `GET /analytics/session-summary` returns 8 category rows; empty response → `EmptyState` text present; no crash on partial/missing data.
- `attendanceGridTile.test.tsx` — cells with `flag="high"` have `bg-primary/10` class; cells with `flag="low"` have `bg-destructive/10`; month headers render in correct order; horizontal scroll wrapper present.
- `monthlyReportTile.test.tsx` — prev/current/diff/avg-12mo columns render; negative diff rendered with minus sign; null `avg_12mo` renders as dash (`—`).
- `monthlyForecastTile.test.tsx` — projected vs actual renders; `method` badge shows `linear_trend` or `sma_12mo`.
- `assimilationFunnelTile.test.tsx` — three stages in order; `new_this_month` badge renders; zero-count stage still present.
- `leaderRollupTile.test.tsx` — relationship select renders; table rows sorted by count desc; `"(deleted contact)"` string renders for soft-deleted leader.
- `segmentBreakdownTile.test.tsx` — dimension select triggers query key change; bars render per bucket; `(none)` bucket renders when present.
- `downloadCsv.test.ts` — when `api.get` rejects with `{ response: { data: { detail: 'Server error' } } }`, `toast.error` called with `'Server error'`; `URL.revokeObjectURL` is called after download.
- `dashboardTabs.test.tsx` — Ministry tab is default; Operations tab not rendered when `isAdmin=false`; Ministry tab visible when `isAdmin=false` (simulating viewer); tile error boundary prevents other tiles from crashing.
- `reportingRoute.test.tsx` — renders children for admin; renders children for isViewer=true (forward compat); redirects to `/login` when unauthenticated.
- `npm run build` (CI gate) — clean TypeScript compile, zero type errors on all new files. No `any` type regressions.
- `npm run lint` (CI gate) — ESLint passes; no unused imports in new tiles.

---

## 9. Rollout / rollback / risks

### Rollout

1. S01 must be merged and deployed first (repoints analytics.py imports).
2. S03/S04/S05 must be merged and deployed (contacts / events / participants tables exist).
3. Deploy backend with S14's `analytics.py` changes → run `alembic upgrade head` (index migration: sub-second on 1,440 contacts / 33.6k participants). GIN index over ~1,440 contacts builds in milliseconds.
4. Deploy frontend bundle.
5. Ministry tiles are inert (zero data) until S23 has run its first nightly recompute. Graceful degradation ensures no errors.
6. Community-reports tile is inert until S22 lands. Graceful degradation via table-existence guard.

### Rollback

- `alembic downgrade -1` drops only the S14 indexes (existence-guarded; no business data altered).
- Reverting frontend bundle hides all new tiles. No state cleanup required.
- The legacy analytics endpoints (`/attendance-by-event` etc.) are unchanged and continue to serve the Operations tab.

### Risks

1. **S01 must land first.** `analytics.py` imports `CiviCRMEvent`/`Attendance`/`push_status` (analytics.py:14, 131, 144, 146). S14's router edits fail to import without S01. Mitigation: enforce build order in the sprint merge queue; add a CI check that S14 is not merged before S01.

2. **S04 `event_type`/`session_time` columns may not exist.** S14 adds a guard that creates `session_time` as a nullable column if absent (**per CN-15: no `session_type` column** — the reporting bucket is a static Python mapping over `event_type` + `session_time`). The session taxonomy data will be all NULL/empty until S04 backfills or seeds events. Acceptance criteria #2–3 are fully verifiable only after S04 has created real event data.

3. **S23 snapshot columns NULL.** Handled by graceful fallback in `contact_info`/`status_summary`/`tier_summary` (§4). Tiles display a "not yet computed" banner (not an error).

4. **`contact_reference` storage type contract.** Leader rollups depend on S02/S03 storing contact_reference values in `custom_data` as JSON **numbers** (integer IDs). The service casts defensively but the Postgres GIN containment fast-path (`@>`) requires the number form. Verify S02's `validate_and_coerce` stores numeric IDs (not string-coerced IDs) before running leader rollups on Postgres.

5. **Ministry/zone/PEPSOL field names unknown until S02 seed.** Tile queries discover field names from `custom_field_def` at request time. Until the owner confirms the exact field names, tiles show `"No data — custom field not configured"` (not an error). The `ReportRegistry` catalog entries for these tiles can evolve without API changes.

6. **S22 community_report table not yet written.** `monthly-community-reports` guards on table existence. No code change to S14 when S22 lands — tile activates automatically.

7. **Volume growth mitigation.** At 1,440 contacts / 33.6k participants, all live aggregates with the specified indexes are sub-100ms on Postgres. If volume exceeds 100k contacts, the documented escape hatch is a `job_runs`-driven nightly pre-aggregation table (S16 scope) with the same API contract.

8. **`events` `event_type` case-sensitivity (per CN-15).** S04 must confirm whether `event_type` for one-off events is stored as `"Event"` (title case) or `"event"` (lowercase). The `SESSION_CATEGORIES` constant keys on `event_type` + `session_time` (there is **no `session_type` column**); match S04's committed casing for each canonical `event_type` value. Verify S04's schema before running the session taxonomy queries.

---

## 10. Open questions & pending owner artifacts

### Pending owner artifacts (block "final" status, not the build)

1. **Custom-field exact names** — the exact `custom_field_def.name` values for Community/Zone (`community`? `zone`?), Ministry (`ministry`?), PEPSOL stage (`pepsol`? `discipleship_stage`?) must be confirmed so tile queries discover them. Tiles degrade to `"custom field not configured"` until confirmed; add the canonical names when S02 seeds them.

2. **Leader relationship set** — which of Invited By / Consolidated By / Community Leader / Ministry Leader / Network Leader / Lifegroup Leader the owner wants surfaced as rollup tiles. The `LeaderRollupTile` auto-discovers all `contact_reference` fields from `custom_field_def`; the tile ordering and default selection need the owner's priority list.

3. **Reports screenshot** — the owner's current Google-Sheet layout (referenced in framework.md:111 as pending artifact #3) may reveal sub-groupings, KPI ordering, or additional rollup rows not captured in reports.md. The `ReportRegistry` + self-describing catalog means adding a new tile requires only one descriptor + one query function + one tile component with no framework changes.

4. **Forecasting method confirmation** — the spec implements simple OLS linear trend + SMA-12 fallback. If the owner uses a seasonal adjustment or a specific formula from their Google Sheet, the `monthly_forecast` service function should be updated. This is isolated to one function with no API contract change.

5. **Ministry list canonical casing** — reports.md §C.5 lists: Service Pastors, Ushering, IT-Multimedia/Monitoring/Reports, Music, Dance, Parking, Prayer Gatherings, Welcome Center, Admin Office, Stewardship, Housekeeping, Property Custodian, Arise & Build, Grievance, Learning & Dev, Community Heads, Outreach Facilitators, Missions, Visitations. These must match the `options` array in the `ministry` custom field def seeded in S02.

### Open questions for master doc

- **`require_reporting` ownership (RESOLVED — per CN-17):** S14 ships `require_reporting` as a **forward-compatible shim = `require_volunteer`** (admits `{admin, volunteer}`). **S15 absorbs and patches it** to allow `viewer` via its `require_role("admin", "volunteer", "viewer")` factory. S14 must **NOT** create a permanent bespoke version, and S15 must **NOT** duplicate the function.

- **`DashboardPage` vs `ReportsPage` naming:** the route `/dashboard` / file `DashboardPage.tsx` is the current target (App.tsx:62). If S15 or S20 decides to split the recognition-ops dashboard from the ministry reporting page, the route split is the right time to rename. S14 keeps both under `/dashboard` in tabs; no rename in S14.

- **Operations tab relocation:** whether the recognition ops tiles (`/tier-distribution`, `/queue-health`, `/attendance-by-event`) eventually move to the S16 System Status page. Current plan: keep on `/dashboard` under admin-only Operations tab; S16 may relocate. 00-MASTER.md to confirm.

- **Session bucket derivation (RESOLVED — per CN-15):** there is **no `session_type` column**. S04 owns `event_type` (the canonical 6-value category: `Sunday Celebration / Prayer Meeting / Powerhouse / Community Meeting / Conference / Event`) and `session_time`. S14's reporting bucket (`morning_prayer / powerhouse / sunday / community / events`) is a **static Python mapping** (`SESSION_CATEGORIES`) over `(event_type, session_time)` — no new column. S14 confirms with S04 the exact `event_type`/`session_time` token casing before finalizing the taxonomy queries.

### Cross-sprint dependencies / shared-model touchpoints

| Sprint | Nature | Concrete contract |
|---|---|---|
| **S01** | Hard | `Contact`/`Event`/`Participant` model renames. `push_status` column dropped. `analytics.py` imports + `export/attendance` CSV header must be updated as part of S14. |
| **S02** | Soft-but-needed | `custom_field_def` / `custom_field_group` drive ministry, zone, PEPSOL, and leader-rollup dimensions. Tiles degrade to zero-count without S02 data. `entity` must be lowercase `contact` (ruling C8). |
| **S03** | Hard | `contacts.contact_subtype`, `is_regular`, `is_connected`, `is_active`, `tier`, `last_attended_at`, `attendance_count`, `weeks_absent`, `custom_data` JSONB, `is_deleted`, `created_at`. All summary tiles read these. |
| **S04** | Hard | `events.event_type` + `session_time` + `start_at`. **(per CN-15)** Session taxonomy is a static Python mapping over `event_type` + `session_time` — **no `session_type` column**. S04 owns these columns; S14 guard migration adds `session_time` as nullable if absent. |
| **S05** | Hard | `participants.status`, `source`, `contact_id`, `event_id`. `StreamingResponse`/generator export pattern. `DataTable`/`Pagination` frontend primitives. |
| **S09** | Reuse contract | `build_contact_query` criteria compiler (00-MASTER.md §5 reuse contracts) may be imported by the segment query builder in `reporting.py` rather than duplicated. Coordinate import path with S09's implementation. |
| **S15** | Soft | Introduces `viewer` role. **(per CN-17)** S15 absorbs/patches the `require_reporting` shim (`= require_volunteer`) to add `viewer` via `require_role(...)`; S14 must not pre-empt this. `ReportingRoute` guard activates when S15 adds `isViewer` to `authStore`. |
| **S16** | Future | `job_runs` table used by `AttendanceGridTile.last_updated_at`. Guard with `try/except NoSuchTableError`; return `null` when absent. |
| **S22** | Soft | `community_report` table feeds `monthly-community-reports` tile. S14 guards on table existence; tile activates automatically when S22 lands. |
| **S23** | Hard | Seven derived snapshot columns on `contacts`. S14 reads them as pre-computed fast paths. Graceful NULL fallback on all tile queries that use them. The `AttendanceGridResponse.last_updated_at` comes from `job_runs WHERE job_name='recompute_member_status'` (S23's job name per its spec §2). |

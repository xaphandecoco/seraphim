# S22 — Attendance Intake & AI Name-Matching
**Phase:** C — Face-native (PRIORITY) · **Depends on:** S03 (contact CRUD + `contacts` table; `app/services/audit.py` `record()` helper), S04 (events CRUD + `events.event_type`/`session_time` columns + `participants` table with `source` String(30)) · **Effort:** XL · **Status:** Not started

> **Why this sprint matters to shipped specs:** `name_alias`, `name_match_review_queue`, and `community_report` are referenced (canonical-only) by S05, S06, S10, S11 (FK-reassignment manifests), and by S18 (Zoom + community-report notify). S22 must land **before S10's participant-import path and before S18**. This spec is the system's "attendance backbone" — every non-face-recognition attendance source (Zoom, name-list, community report) flows through the matching service built here.

> **n8n ground truth:** Primary source is `docs/crm-research/n8n/3yaS8JZfct8BVe8Z.json` (Gforms AI V2, 138 nodes — the community/event report form with the AI Comparison agent), `docs/crm-research/n8n/jdVHzcMWXdANwG8R.json` (New Friend V2, 28 nodes — Invited By / Consolidated By resolution), `docs/crm-research/n8n/fKkPUolayRyZrjao.json` (CiviCRM Scheduler/Updater, 92 nodes — AI Agent1 alias re-scan + all cron cadences), and `docs/crm-research/n8n/_prompts_and_calls.txt` (verbatim agent prompts). All LLM calls in n8n used OpenRouter/OpenAI — the rebuild uses **Claude** (Anthropic) per project conventions.

---

## 1. Goal & rationale

**The problem.** Three of the four attendance sources depend on matching a raw person name to a `contacts.id`. Today that matching lives entirely inside n8n:

- `3yaS8JZfct8BVe8Z` (Gforms AI V2): Google Form submission → "Comparison" AI agent batches the `Attendees` textarea name-list (one name per line), searches CiviCRM, then "AI Agent1" adds each attendance row. The Switch node routes by `Community/Ministry/Zone` (32 community/ministry/zone labels confirmed from the form JSON). Finance branches (Liquidation/BRF) are **out of scope**.
- `jdVHzcMWXdANwG8R` (New Friend V2): "Get Invited By" and "Get Consolidated By" agents search CiviCRM for free-text names with the heuristic: **"check first/last name; best to look for the first 3 letters; if unsure → contact ID '1'"** (verbatim from `_prompts_and_calls.txt`). This "ID 1" fallback silently discards data.
- `fKkPUolayRyZrjao` (CiviCRM Scheduler): "AI Agent1" re-scans previously unmatched names against a "common names" list stored in a Google Sheet context, then retries CiviCRM search. This is the original alias-dictionary concept.
- `sJ7EgCZkk9wKSj3D` (Morning Prayer): "Comparison Agent" batches Zoom display names 10-20 at a time, searches CiviCRM surname-first. Ambiguous batches drop to 10.

None of these have: persistence of learned aliases, a structured human review queue, audit trails, or deterministic fallback before the LLM. The "ID 1 if unsure" pattern loses data permanently.

**The charter.** Build a **native, deterministic-first, AI-augmented name-matching service** used by every non-face attendance source:

1. **Name-match SERVICE** (`backend/app/services/name_match.py`) implementing a four-stage pipeline: normalize → alias dictionary → deterministic fuzzy (Filipino/surname-first-3 aware) → Claude fallback → human review queue. Exported as a library; no router imports.
2. **Three new ORM models** in `backend/app/models.py`: `NameAlias`, `NameMatchReviewQueue`, `CommunityReport`. One Alembic migration.
3. **Attendance-by-name-list intake endpoint** (`POST /attendance/name-list`): accept event + name list → run pipeline → bulk-insert matched `participants` + queue unmatched.
4. **Community-Report submission form** replacing the Google Form + Sheet — event meta + attendee name list + topics/prayer/remarks + photos. Triggers name-list processing automatically.
5. **Review-queue UI** — admin/volunteer page to resolve unmatched/ambiguous names and teach aliases.

Reused by: S06 (people-link resolution during migration, source=`"migration"`), S10 (import-wizard participant-name matching), S13 (newcomer "Invited By"/"Consolidated By" resolution, source=`"newcomer"`), S18 (Zoom attendee name resolution, source=`"zoom"`).

---

## 2. Scope

### In scope

- **`backend/app/services/name_match.py`** (new) — canonical matching service. Public API: `normalize_name`, `lookup_alias`, `lookup_deterministic`, `lookup_claude`, `match_name`, `match_name_batch`, `teach_alias_from_resolution`. All async. No circular imports — imports models and `anthropic`, not routers.
- **Three new ORM models** in `backend/app/models.py`: `NameAlias`, `NameMatchReviewQueue`, `CommunityReport`.
- **One Alembic migration** (`backend/alembic/versions/<rev>_s22_name_matching_tables.py`) covering all three tables.
- **`POST /attendance/name-list`** (add to `backend/app/routers/attendance.py`) — volunteer+, accepts `event_id` + `attendee_names: list[str]` + `source`. Runs pipeline; bulk-inserts via `ON CONFLICT DO NOTHING`; queues unmatched.
- **Community-report endpoints** (new router `backend/app/routers/community_reports.py`): `POST /community-reports`, `GET /community-reports`, `GET /community-reports/{id}`, `PATCH /community-reports/{id}`, `DELETE /community-reports/{id}` (soft-delete = `status='archived'`), `POST /community-reports/{id}/process`.
- **`name_alias` CRUD** (new router `backend/app/routers/name_aliases.py`): `GET /name-aliases`, `POST /name-aliases`, `POST /name-aliases/teach`, `PATCH /name-aliases/{id}`, `DELETE /name-aliases/{id}`.
- **Review-queue endpoints** (new router `backend/app/routers/name_match.py`): `GET /name-match/review-queue`, `POST /name-match/review-queue/{id}/resolve`, `POST /name-match/review-queue/{id}/unmatch`, `POST /name-match/review-queue/{id}/skip`, `POST /name-match/review-queue/reprocess-aliases`.
- **Pydantic schemas** in `backend/app/schemas.py`: all request/response DTOs listed in §4.1.
- **Frontend pages**: `NameMatchReviewPage` (`/name-match/review`), `CommunityReportsPage` (`/community-reports`), `CommunityReportFormPage` (`/community-reports/new`), `CommunityReportDetailPage` (`/community-reports/:id`).
- **Name-list intake sub-panel** on `EventDetailPage` (S04 extension, collapsible).
- **`backend/requirements.txt`** — add `jellyfish>=0.12.0` and `anthropic>=0.40.0` (both absent today; verified from `backend/requirements.txt` current contents).
- **Unit + integration tests**: `backend/tests/test_name_match.py`, `backend/tests/test_community_reports.py`, frontend vitest for new pages.

### Out of scope (explicit)

- **Zoom attendance pull** — S18 (S22 exports `match_name_batch` for S18 to call directly with `source="zoom"`).
- **Scheduled cron wiring** of EOW reprocess cadence — S16 (`job_runs`) calls `POST /name-match/review-queue/reprocess-aliases` on the `0 0 * * 1` schedule (n8n `fKkPUolayRyZrjao` "Rescan EOW" expression).
- **Google Chat notification** after community-report processing — S18.
- **People-link resolution in migration XLSX** — S06 calls `match_name()` as a library; S22 does not ship ETL logic.
- **Finance liquidation / BRF form** — explicitly excluded (n8n `3yaS8JZfct8BVe8Z` finance branch; the `Switch1` node routes to finance — skip all those branches).
- **Full visual rules engine** — S17.
- **Face-recognition attendance** — S07 owns face→participant creation.
- **Public (unauthenticated) community-report submission** — requires a logged-in user (volunteer or admin) in v1. S13/S19 can extend later.
- **Self-learning aliases without human confirmation** — every alias creation requires explicit human action (review-queue resolve) or admin `POST /name-aliases`.
- **Morning Prayer Zoom pull** — S18.

---

## 3. Data model changes

All JSON columns use the module-level `JSONB` alias from `backend/app/models.py:22` (`JSON().with_variant(_PG_JSONB, "postgresql")`). Timestamps are naive UTC via `utc_now()` (defined at `backend/app/models.py:27`). FK `ondelete` is declared explicitly on every FK column. This sprint owns **one Alembic migration** covering all three new tables.

### 3.1 `name_alias` (new table)

The persistent "common names" alias dictionary. Every row maps one normalized alias string to a single contact. Replaces the Google-Sheet-stored alias list used by n8n's `AI Agent1`.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK (autoincrement) | no | — | App-minted |
| `alias_text` | String(255) | no | — | **Normalized** lowercase alias (output of `normalize_name()`); NOT the original spelling |
| `contact_id` | Integer FK→`contacts.id` ON DELETE CASCADE | no | — | The contact this alias resolves to |
| `source` | String(30) | no | `'manual'` | `learned` (from review-queue resolution) or `manual` (admin-created) |
| `created_by_id` | Integer FK→`users.id` ON DELETE SET NULL | yes | NULL | Who created/approved the alias |
| `created_at` | DateTime naive UTC | no | `utc_now()` | |

Constraints / indexes:
- `UNIQUE(alias_text)` constraint named `uq_name_alias_text` — one canonical contact per normalized alias.
- `Index("ix_name_alias_contact_id", "contact_id")` — for S11 merge FK manifest + cascade-delete on contact remove.

```python
class NameAlias(Base):
    __tablename__ = "name_alias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias_text: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    created_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        Index("ix_name_alias_contact_id", "contact_id"),
    )
```

**Backfill:** none — the alias dictionary starts empty. Owner must provide the Google-Sheet "common names" list as a CSV for bulk-import (pending artifact §10.1).

**Downgrade:** `DROP TABLE name_alias` (no other table in this migration depends on it; drop last in downgrade).

---

### 3.2 `name_match_review_queue` (new table)

Holds every name that the matching pipeline could not resolve with high confidence. Replaces the "contact ID 1 if unsure" fallback from n8n.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK (autoincrement) | no | — | App-minted |
| `source` | String(40) | no | — | Context: `community_report`, `zoom`, `name_list`, `migration`, `newcomer` |
| `raw_name` | String(512) | no | — | Exact original string before normalization |
| `normalized_name` | String(512) | yes | NULL | Output of `normalize_name(raw_name)`; stored to avoid re-normalizing |
| `raw_payload` | JSONB | no | `{}` | Full context: `{event_id, community_report_id, import_batch_id, …}` |
| `event_id` | Integer FK→`events.id` ON DELETE SET NULL | yes | NULL | Event the attendance is for (nullable for non-event sources) |
| `community_report_id` | Integer FK→`community_report.id` ON DELETE SET NULL | yes | NULL | Back-link if from community report |
| `candidate_contact_ids` | JSONB | no | `[]` | Ordered list of `{contact_id, display_name, score, stage}` from pipeline |
| `match_stage_reached` | String(30) | yes | NULL | Furthest stage that produced candidates: `fuzzy`, `alias`, `claude`, `none` |
| `status` | String(20) | no | `'pending'` | `pending \| matched \| unmatched \| skipped` |
| `resolved_contact_id` | Integer FK→`contacts.id` ON DELETE SET NULL | yes | NULL | Set on human resolution |
| `resolved_by_id` | Integer FK→`users.id` ON DELETE SET NULL | yes | NULL | Who resolved |
| `resolved_at` | DateTime naive UTC | yes | NULL | |
| `created_at` | DateTime naive UTC | no | `utc_now()` | |

Constraints / indexes:
- `Index("ix_nmrq_status", "status")` — hot filter column.
- `Index("ix_nmrq_event_id", "event_id")`.
- `Index("ix_nmrq_community_report_id", "community_report_id")`.
- `Index("ix_nmrq_resolved_contact_id", "resolved_contact_id")` — S11 FK manifest.

```python
class NameMatchReviewQueue(Base):
    __tablename__ = "name_match_review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    raw_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    community_report_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("community_report.id", ondelete="SET NULL"), nullable=True
    )
    candidate_contact_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    match_stage_reached: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resolved_contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        Index("ix_nmrq_status", "status"),
        Index("ix_nmrq_event_id", "event_id"),
        Index("ix_nmrq_community_report_id", "community_report_id"),
        Index("ix_nmrq_resolved_contact_id", "resolved_contact_id"),
    )
```

**Downgrade:** `DROP TABLE name_match_review_queue` first (references `community_report`).

---

### 3.3 `community_report` (new table)

Leader-submitted event + attendee name list + qualitative data + photos. Direct replacement for the n8n `3yaS8JZfct8BVe8Z` Google Form → Sheet → AI flow. Feeds both `participants` (source=`community_report`) and the S14 "Reports Attendance" lines.

The n8n Event Form fields (confirmed from `3yaS8JZfct8BVe8Z.json` → "Event Form" node `formFields`) map to columns:

| n8n Field | Column | Notes |
|---|---|---|
| Event Title | `event_title` + `event_id` FK | Leader picks from events list or types free text |
| Date of Activity | `date_of_activity` | Date (stored as DateTime, use `.date()`) |
| Time of Activity | `time_of_activity` | Free text ("10AM/3PM etc.") |
| Location of Activity | `location` | Free text |
| Community/Ministry/Zone | `zone` | 32 options from n8n form; free text + future dropdown |
| Number of Attendees Including the Leader | `attendee_count` | Integer sanity check vs. name list length |
| Event Leader | `event_leader_name` + `event_leader_contact_id` FK | Run through matching service |
| Attendees | `attendee_names` JSONB | List of raw name strings (one per form line) |
| Topics Discussed | `topics` | Text |
| Prayer Items to be Included in Church | `prayer_items` | Text |
| Remarks, Comments & Praise Reports | `remarks` | Text |
| Photo Documentation | `photo_paths` JSONB | Paths returned by `/uploads/` infra |

Full schema:

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK (autoincrement) | no | — | |
| `event_id` | Integer FK→`events.id` ON DELETE SET NULL | yes | NULL | Linked event (nullable — leader may submit before event exists) |
| `event_title` | String(512) | yes | NULL | Raw title from form (stored even after `event_id` resolved) |
| `date_of_activity` | DateTime | no | — | Date-only semantics; query as `.date()` |
| `time_of_activity` | String(50) | yes | NULL | Raw time string ("10AM", "2:00 PM") |
| `location` | String(255) | yes | NULL | |
| `zone` | String(255) | yes | NULL | Community / Ministry / Zone label |
| `submitted_by_contact_id` | Integer FK→`contacts.id` ON DELETE SET NULL | yes | NULL | The leader contact (S11 FK manifest) |
| `submitted_by_user_id` | Integer FK→`users.id` ON DELETE SET NULL | yes | NULL | System user who submitted (may differ from contact) |
| `attendee_count` | Integer | yes | NULL | Self-reported count; cross-check vs. `len(attendee_names)` |
| `event_leader_name` | String(512) | yes | NULL | Raw leader name before resolution |
| `event_leader_contact_id` | Integer FK→`contacts.id` ON DELETE SET NULL | yes | NULL | Resolved leader contact (S11 FK manifest) |
| `attendee_names` | JSONB | no | `[]` | Ordered list of raw name strings |
| `topics` | Text | yes | NULL | |
| `prayer_items` | Text | yes | NULL | |
| `remarks` | Text | yes | NULL | |
| `photo_paths` | JSONB | no | `[]` | List of storage paths (authenticated via `/storage/`) |
| `match_status` | String(30) | no | `'pending'` | `pending \| processing \| complete \| partial` |
| `matched_count` | Integer | no | `0` | Auto-matched participant rows created |
| `review_count` | Integer | no | `0` | Names queued for human review |
| `status` | String(20) | no | `'submitted'` | `submitted \| reviewed \| archived` |
| `created_at` | DateTime naive UTC | no | `utc_now()` | |
| `updated_at` | DateTime naive UTC | no | `utc_now()` | `onupdate=utc_now` |

Constraints / indexes:
- `Index("ix_community_report_date", "date_of_activity")` — range queries for S14.
- `Index("ix_community_report_event_id", "event_id")`.
- `Index("ix_community_report_submitted_by_contact_id", "submitted_by_contact_id")` — S11 FK manifest.
- `Index("ix_community_report_status", "status")`.

```python
class CommunityReport(Base):
    __tablename__ = "community_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    event_title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    date_of_activity: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    time_of_activity: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zone: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    submitted_by_contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    submitted_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    attendee_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_leader_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    event_leader_contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    attendee_names: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    topics: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prayer_items: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_paths: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    match_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="submitted")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        Index("ix_community_report_date", "date_of_activity"),
        Index("ix_community_report_event_id", "event_id"),
        Index("ix_community_report_submitted_by_contact_id", "submitted_by_contact_id"),
        Index("ix_community_report_status", "status"),
    )
```

---

### 3.4 Alembic migration plan

**One migration file** covering all three tables in FK-dependency order:

1. Create `name_alias` — depends on `contacts` (S01), `users` (existing).
2. Create `community_report` — depends on `events` (S01/S04), `contacts` (S01), `users` (existing). Note two FKs to `contacts` (`submitted_by_contact_id`, `event_leader_contact_id`).
3. Create `name_match_review_queue` — depends on `contacts`, `users`, `events`, **and `community_report`** (must be created second).

> **Ordering is load-bearing (per CN-22).** This exact order — (1) `name_alias`, (2) `community_report`, (3) `name_match_review_queue` — is mandatory. `name_match_review_queue` carries an FK `community_report_id → community_report.id`, so reversing steps 2 and 3 (creating the review queue before `community_report`) raises a FK / "relation does not exist" error at migration time. Do not reorder.

Migration filename: `backend/alembic/versions/<rev>_s22_name_matching_tables.py`. **Never invent a revision ID** — assign `down_revision` to the actual `alembic heads` value at implementation time.

**Downgrade order** (reverse of creation): `DROP TABLE name_match_review_queue` → `DROP TABLE community_report` → `DROP TABLE name_alias`.

**Backfill:** none. All three tables start empty.

**`participants.source` extension:** The canonical `participants.source` column (from S01/S04) is `String(30)` with no DB-level enum. S22 requires the value `"community_report"`. If S01 shipped without it, no DDL change is needed — add a comment in the migration confirming the value is writable. The master must update S01's scope to include `"community_report"` in the declared `source` vocabulary (`face|manual|zoom|name_list|community_report|import`).

---

## 4. Backend

### 4.1 Endpoint table

| Method | Path | Role | Request body / params | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/attendance/name-list` | volunteer+ | `NameListIntakeRequest` | `NameListIntakeResponse` | Run pipeline; bulk-insert matched participants |
| `GET` | `/name-match/review-queue` | volunteer+ | `?status=&source=&event_id=&page=1&page_size=50` | `PaginatedReviewQueueResponse` | List items, newest first |
| `POST` | `/name-match/review-queue/{id}/resolve` | volunteer+ | `ReviewQueueResolveRequest` | `ReviewQueueItem` | Assign contact; optionally teach alias |
| `POST` | `/name-match/review-queue/{id}/unmatch` | volunteer+ | `{reason?: str}` | `ReviewQueueItem` | Mark as definitively no-match |
| `POST` | `/name-match/review-queue/{id}/skip` | volunteer+ | `{}` | `ReviewQueueItem` | Defer; leaves `status=pending` |
| `POST` | `/name-match/review-queue/reprocess-aliases` | admin | `{source?: str, event_id?: int}` | `{processed: int, auto_matched: int, still_pending: int}` | Re-run alias+Claude on all pending; S16 calls this on schedule |
| `GET` | `/name-aliases` | admin | `?search=&page=1&page_size=50` | `PaginatedNameAliasResponse` | List aliases |
| `POST` | `/name-aliases` | admin | `NameAliasCreate` | `NameAliasResponse` | Admin-create alias |
| `POST` | `/name-aliases/teach` | volunteer+ | `NameAliasTeach` | `NameAliasResponse` | Create alias from resolved review-queue item |
| `PATCH` | `/name-aliases/{id}` | admin | `NameAliasUpdate` | `NameAliasResponse` | Edit (swap contact) |
| `DELETE` | `/name-aliases/{id}` | admin | — | `204` | Remove alias |
| `POST` | `/community-reports` | volunteer+ | `CommunityReportCreate` | `CommunityReportResponse` | Submit; triggers async matching |
| `GET` | `/community-reports` | volunteer+ | `?status=&zone=&event_id=&date_from=&date_to=&page=1&page_size=50` | `PaginatedCommunityReportResponse` | Paginated list |
| `GET` | `/community-reports/{id}` | volunteer+ | — | `CommunityReportDetailResponse` | Full detail incl. review-queue items for this report |
| `PATCH` | `/community-reports/{id}` | admin | `CommunityReportUpdate` | `CommunityReportResponse` | Edit status/corrections |
| `DELETE` | `/community-reports/{id}` | admin | — | `204` | Soft delete (`status='archived'`) |
| `POST` | `/community-reports/{id}/process` | admin | `{}` | `NameListIntakeResponse` | (Re-)trigger name-list processing for a report |

All endpoints: `Depends(require_volunteer)` minimum; admin-only ones `Depends(require_admin)`. All registered with `dependencies=[Depends(check_setup_complete)]` in `main.py`. The `require_volunteer` and `require_admin` dependencies are defined at `backend/app/dependencies.py:45,56`.

---

### 4.2 Name-matching service — `backend/app/services/name_match.py`

This module is the core of S22. **Every path that converts a raw name string to a `contact_id`** goes through it. It is a library module — stateless, no in-memory request-level caches, imports from `app.models` and external libraries only (no router imports to avoid circular dependencies).

#### 4.2.1 Shared types

```python
from typing import TypedDict, Optional

class MatchCandidate(TypedDict):
    contact_id: int
    display_name: str
    score: float
    stage: str  # "deterministic" | "alias" | "claude"

class NameMatchResult(TypedDict):
    raw_name: str
    normalized: str
    contact_id: Optional[int]
    confidence: str        # "high" | "medium" | "low"
    stage: str             # "deterministic" | "alias" | "claude" | "review_queue"
    candidates: list[MatchCandidate]
    review_queue_id: Optional[int]  # set when auto_enqueue=True and no high-confidence match
```

#### 4.2.2 Stage 1 — `normalize_name(raw: str) -> str`

Not async (pure CPU). Operations in order:

1. Strip leading/trailing whitespace.
2. Lowercase.
3. Unicode normalization: `unicodedata.normalize("NFD", s)` then strip combining characters — removes diacritics (`ñ→n`, `é→e`). Handles Filipino names with written accents.
4. Collapse multiple internal whitespace → single space.
5. **Honorific prefix removal** (case-insensitive, must be followed by a space): `tita, kuya, ate, bro, sis, pastor, ptr, rev, dtr, dr, mr, mrs, ms, sir, ma`. Strip the prefix and leading space. Example: `"Ptr. Rodel Aquino"` → strip `"ptr"` → `"rodel aquino"` (the period is handled by step 6).
6. Remove punctuation other than space and hyphen (commas, periods). Converts `"Cruz, Juan"` → `"cruz juan"`, `"Ptr."` → `""` (already stripped by step 5).
7. **Single-character word removal** (handles accidental initials like `"M. Cruz"` → `"cruz"`).
8. Collapse whitespace again after strip operations.

**Output:** lowercase, ASCII-compatible, whitespace-squashed string stored in `name_alias.alias_text` and compared in queries.

Key test vectors (from `_prompts_and_calls.txt` example names):
- `"Ptr. Rodel Aquino Jr."` → `"rodel aquino"` (honorific, suffix stripped)
- `"EVANGELISTA, Guada"` → `"evangelista guada"` (comma, uppercase)
- `"Tita Cely"` → `"cely"` (honorific)
- `"Ate Marie"` → `"marie"` (honorific)
- `"Monica Fainsan - Light NC"` → `"monica fainsan light nc"` (hyphen kept, non-punct stripped)
- `"Marichu P."` → `"marichu"` (single-char word removed)
- `"Niño"` → `"nino"` (diacritic)

#### 4.2.3 Stage 2 — `lookup_deterministic(normalized: str, db: AsyncSession, min_score: float = 0.88) -> list[MatchCandidate]`

Replicates the n8n agents' "first 3 letters" surname heuristic (`_prompts_and_calls.txt`).

Algorithm:
1. Split `normalized` on whitespace → `tokens`. If empty, return `[]`.
2. **Surname-first-3 heuristic**: take the last token as `surname_candidate`. Build `prefix3 = surname_candidate[:3] + '%'`.
3. Query (SQLAlchemy async):
   ```sql
   SELECT id, first_name, last_name, nickname
   FROM contacts
   WHERE is_deleted = false
     AND (lower(last_name) LIKE :prefix3 OR lower(first_name) LIKE :prefix3)
   LIMIT 200
   ```
4. For each candidate contact `c`:
   - Compute `score_a = jaro_winkler_similarity(normalized, normalize_name(f"{c.first_name} {c.last_name}"))`
   - Compute `score_b = jaro_winkler_similarity(normalized, normalize_name(f"{c.last_name} {c.first_name}"))` (reversed — common in Filipino lists: "Dela Cruz, Juan")
   - If `c.nickname`: also score `jaro_winkler_similarity(normalized, normalize_name(c.nickname))`
   - `score = max(score_a, score_b, nickname_score_if_any)`
5. Collect all candidates with `score >= min_score`, sorted descending.
6. **Ambiguity rules:**
   - Top score ≥ 0.95 AND (no second candidate OR second score ≤ 0.88): **high-confidence single match**.
   - Top score ≥ 0.88 AND (no second OR second < top − 0.07): **medium-confidence** (add to candidates; continue to alias/Claude).
   - Else: **ambiguous** (all candidates; continue).

**Performance:** ≤200 DB rows; jaro-winkler on 200 strings is <1ms. Batch of 50 names (typical community-report size): 50 × 200 = 10k comparisons, <50ms total. Use `jellyfish.jaro_winkler_similarity` (add `jellyfish>=0.12.0` to `requirements.txt`); do **not** use `difflib.SequenceMatcher`.

#### 4.2.4 Stage 3 — `lookup_alias(normalized: str, db: AsyncSession) -> Optional[MatchCandidate]`

```python
async def lookup_alias(normalized: str, db: AsyncSession) -> Optional[MatchCandidate]:
    result = await db.execute(
        select(NameAlias).where(NameAlias.alias_text == normalized).limit(1)
    )
    row = result.scalar_one_or_none()
    if row:
        return MatchCandidate(contact_id=row.contact_id, display_name="", score=1.0, stage="alias")
    return None
```

O(1) exact-match lookup (unique index on `alias_text`). This is the analog of n8n `AI Agent1`'s "check the common names list" step. Returns `score=1.0` because an alias represents accumulated human judgement — always wins over fuzzy.

#### 4.2.5 Stage 4 — `lookup_claude(raw_name: str, normalized: str, candidates: list[MatchCandidate], db: AsyncSession) -> Optional[MatchCandidate]`

Called only when stages 3 + 2 produce no high-confidence result.

**Prompt** (derived directly from the n8n agents in `_prompts_and_calls.txt`; adapted for structured JSON output):

```
You are a church attendance data assistant. Match the submitted name to the correct member.

Localization: Philippines. Names are Filipino. Heuristics:
- Nicknames are common (e.g., "Cely" = "Celestina", "Boy" may be a nickname for any name).
- First 3 letters of surname are often enough ("San" = "Santos").
- Multilingual name variants and spelling variants are common.
- Abbreviated forms and initials are common ("Mar C." = "Maria Cruz").
- Honorifics (Ate, Kuya, Tita, Bro, Sis, Pastor, Ptr, Rev, Dtr, Dr) should be ignored.
- Name order may be reversed (Last, First is common in forms).

Submitted name (raw): "{raw_name}"
Normalized form: "{normalized}"

Candidates from database (fuzzy search results):
{candidates_json}

Return ONLY a JSON object:
{{"contact_id": <int or null>, "confidence": "high"|"medium"|"low", "reason": "<one sentence>"}}

Rules:
- confidence="high" if you are >90% sure this is a match: return the contact_id.
- confidence="medium" if likely but uncertain: return the contact_id.
- confidence="low" if no good match or unsure: return {{"contact_id": null, "confidence": "low", "reason": "No confident match found"}}.
- NEVER return a contact_id not present in the candidates list above.
- NEVER guess. A null is correct when unsure.
```

**Implementation:**
- Use `anthropic.AsyncAnthropic()`. Model: `claude-haiku-3-5` (fast, low cost for small structured task). Configurable via `admin_settings` key `name_match.claude_model`, default `"claude-haiku-3-5"`.
- `max_tokens=256`.
- Parse JSON from `response.content[0].text`; validate `contact_id` is in the candidates list (reject hallucinated IDs — any returned ID not in candidates falls through to review queue).
- `high` → matched at Claude. `medium` → add to candidates with `stage="claude"`, proceed to review queue. `low` or null → proceed to review queue.
- On any `anthropic.APIError`: log warning; fall through to review queue (never raise to caller).
- **Semaphore guard:** `asyncio.Semaphore(5)` module-level — max 5 concurrent Claude calls per process.
- **Test bypass:** at import, check `os.environ.get("ENVIRONMENT") == "test"` OR check `admin_settings` key `name_match.claude_enabled`. If disabled: skip Claude entirely, return `None`. This must be set in `backend/tests/conftest.py`.

#### 4.2.6 Top-level matcher — `match_name` and `match_name_batch`

```python
async def match_name(
    raw_name: str,
    db: AsyncSession,
    source: str,
    event_id: Optional[int] = None,
    community_report_id: Optional[int] = None,
    payload: Optional[dict] = None,
    auto_enqueue: bool = True,
) -> NameMatchResult:
```

**Full pipeline (stage order justification: alias before fuzzy — alias is human-confirmed O(1), highest-confidence signal):**

1. `normalized = normalize_name(raw_name)`.
2. **Stage 3 first** (alias): `alias_result = await lookup_alias(normalized, db)`. If found → return `NameMatchResult(contact_id=alias_result.contact_id, confidence="high", stage="alias", candidates=[alias_result])`. Done.
3. **Stage 2** (fuzzy): `fuzzy_candidates = await lookup_deterministic(normalized, db)`. Apply ambiguity rules from §4.2.3.
4. If single high-confidence fuzzy match → return `NameMatchResult(contact_id=..., confidence="high", stage="deterministic")`. Done.
5. **Stage 4** (Claude, only if not yet matched): `claude_result = await lookup_claude(raw_name, normalized, fuzzy_candidates, db)`. If `confidence="high"` → return matched at Claude. `medium` → merge candidate into `fuzzy_candidates` with `stage="claude"`.
6. If no high-confidence result: if `auto_enqueue=True`, insert `NameMatchReviewQueue` row with all candidates, `normalized_name`, `event_id`, `community_report_id`, `raw_payload=payload or {}`, `match_stage_reached`. Return `NameMatchResult(contact_id=None, stage="review_queue", review_queue_id=<new_id>)`.

```python
async def match_name_batch(
    names: list[str],
    db: AsyncSession,
    source: str,
    event_id: Optional[int] = None,
    community_report_id: Optional[int] = None,
) -> list[NameMatchResult]:
```

Calls `match_name` per item. Claude calls are gathered with the semaphore. Results in same order as inputs.

#### 4.2.7 Bulk participant insert after matching

After `match_name_batch`, the intake handler separates results:
- **matched** (`contact_id` is set): build `Participant` rows with `source=source`, `status='attended'`, `event_id=event_id`. Use S05's set-based bulk insert:
  ```python
  await db.execute(
      insert(Participant).values(batch_rows).on_conflict_do_nothing(
          index_elements=["event_id", "contact_id"]
      )
  )
  ```
  Count skips by comparing row count before/after (or use RETURNING).
- **unmatched**: review-queue row already created in `match_name` when `auto_enqueue=True`.

Update `community_report.matched_count` and `community_report.review_count` in the same transaction when `community_report_id` is set.

#### 4.2.8 Alias reprocess job — `POST /name-match/review-queue/reprocess-aliases`

Re-runs Stage 3 + Stage 4 against all `status="pending"` rows (optionally filtered by `source`/`event_id`). Replicates n8n `AI Agent1` "re-check unmatched names against common names" (`fKkPUolayRyZrjao`, cadence `0 0 * * 1` Mon 00:00 — S16 calls this endpoint on that schedule).

For each pending row:
1. `alias_result = await lookup_alias(row.normalized_name, db)`. If found AND confidence high → auto-resolve: create `Participant` row, set `status="matched"`, `resolved_contact_id`, `resolved_at=utc_now()`, `resolved_by_id=None` (system-resolved).
2. Else: `claude_result = await lookup_claude(...)`. If `high` → auto-resolve same as above. If `medium` → update `candidate_contact_ids` (merge new candidate) and leave `status="pending"` for human review (better candidates visible in UI).

Response: `{"processed": int, "auto_matched": int, "still_pending": int}`.

### 4.3 Pydantic schemas (add to `backend/app/schemas.py`)

```python
# --- Name-list intake ---
class NameListIntakeRequest(BaseModel):
    event_id: int
    attendee_names: list[str] = Field(..., min_length=1, max_length=500)
    source: Literal["name_list", "community_report"] = "name_list"
    community_report_id: Optional[int] = None

class NameMatchResultItem(BaseModel):
    raw_name: str
    normalized: str
    contact_id: Optional[int]
    contact_display_name: Optional[str]
    confidence: str
    stage: str
    review_queue_id: Optional[int]

class NameListIntakeResponse(BaseModel):
    total: int
    matched: int
    skipped_existing: int
    review_queue: int
    results: list[NameMatchResultItem]

# --- Review queue ---
class MatchCandidateSchema(BaseModel):
    contact_id: int
    display_name: str
    score: float
    stage: str

class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    raw_name: str
    normalized_name: Optional[str]
    event_id: Optional[int]
    event_title: Optional[str]  # joined
    community_report_id: Optional[int]
    candidate_contact_ids: list[MatchCandidateSchema]
    match_stage_reached: Optional[str]
    status: str
    resolved_contact_id: Optional[int]
    resolved_contact_name: Optional[str]  # joined
    resolved_by_id: Optional[int]
    resolved_at: Optional[datetime]
    created_at: datetime

class PaginatedReviewQueueResponse(BaseModel):
    items: list[ReviewQueueItem]
    total: int
    page: int
    page_size: int

class ReviewQueueResolveRequest(BaseModel):
    contact_id: int
    teach_alias: bool = False
    alias_text: Optional[str] = None  # defaults to normalized_name if teach_alias=True

# --- Name aliases ---
class NameAliasCreate(BaseModel):
    alias_text: str  # will be run through normalize_name before storing
    contact_id: int

class NameAliasUpdate(BaseModel):
    contact_id: int

class NameAliasTeach(BaseModel):
    review_queue_id: int
    contact_id: int
    alias_text: Optional[str] = None  # defaults to normalized_name from queue item

class NameAliasResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    alias_text: str
    contact_id: int
    contact_display_name: Optional[str]  # joined
    source: str
    created_at: datetime

class PaginatedNameAliasResponse(BaseModel):
    items: list[NameAliasResponse]
    total: int
    page: int
    page_size: int

# --- Community reports ---
class CommunityReportCreate(BaseModel):
    event_id: Optional[int] = None
    event_title: Optional[str] = None
    date_of_activity: date
    time_of_activity: Optional[str] = None
    location: Optional[str] = None
    zone: Optional[str] = None
    attendee_count: Optional[int] = None
    event_leader_name: Optional[str] = None
    attendee_names: list[str] = Field(default_factory=list, max_length=500)
    topics: Optional[str] = None
    prayer_items: Optional[str] = None
    remarks: Optional[str] = None
    photo_paths: list[str] = Field(default_factory=list)
    submitted_by_contact_id: Optional[int] = None

    @model_validator(mode="after")
    def require_event_id_or_title(self) -> "CommunityReportCreate":
        if not self.event_id and not self.event_title:
            raise ValueError("At least one of event_id or event_title is required.")
        return self

class CommunityReportUpdate(BaseModel):
    status: Optional[str] = None
    zone: Optional[str] = None
    topics: Optional[str] = None
    prayer_items: Optional[str] = None
    remarks: Optional[str] = None
    event_id: Optional[int] = None

class CommunityReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_id: Optional[int]
    event_title: Optional[str]
    date_of_activity: datetime
    time_of_activity: Optional[str]
    location: Optional[str]
    zone: Optional[str]
    submitted_by_contact_id: Optional[int]
    submitted_by_name: Optional[str]   # joined
    attendee_count: Optional[int]
    attendee_names: list[str]
    matched_count: int
    review_count: int
    match_status: str
    status: str
    topics: Optional[str]
    prayer_items: Optional[str]
    remarks: Optional[str]
    photo_paths: list[str]
    created_at: datetime

class CommunityReportDetailResponse(CommunityReportResponse):
    review_queue_items: list[ReviewQueueItem]  # items for this report
    matched_contacts: list[dict]               # [{contact_id, display_name}]

class PaginatedCommunityReportResponse(BaseModel):
    items: list[CommunityReportResponse]
    total: int
    page: int
    page_size: int
```

### 4.4 Business rules — `POST /attendance/name-list`

1. Validate `event_id` exists in `events` and `is_active=True`. 404 if missing; 400 if not active.
2. If `community_report_id` provided: validate exists and status in `('submitted', 'pending')`.
3. Clamp `len(attendee_names)` ≤ 500; reject > 500 with 422.
4. For ≤ 50 names: run `match_name_batch` synchronously; block until all stages including Claude complete before returning.
5. For > 50 names: run alias + deterministic stages synchronously; return the partial result immediately; dispatch Claude calls as `asyncio.create_task` (or APScheduler one-shot if S16 available). Note in response `{"claude_pending": true}` so the UI knows to poll.
6. Bulk insert matched participants; count `skipped_existing` via pre/post counts.
7. Emit `audit_log`: `action="attendance.name_list_intake"`, `entity="event"`, `entity_id=event_id`, `after={"matched": n, "queued": m, "skipped": k}`.
8. Return `NameListIntakeResponse`.

### 4.5 Business rules — `POST /community-reports`

1. Validate `event_id` or `event_title` present (enforced by Pydantic validator in schema).
2. If `submitted_by_contact_id` is None: look up `contacts` by `contacts.email = current_user.email` to auto-populate (JOIN users→contacts by email). Leave NULL if no contact found.
3. Save `CommunityReport` row with `match_status="pending"`.
4. If `event_leader_name` provided: run `match_name(event_leader_name, source="community_report", community_report_id=report.id)` and set `event_leader_contact_id` if confidence="high".
5. If `attendee_names` non-empty: call the name-list service function directly (not the HTTP endpoint) with `source="community_report"`, `event_id=report.event_id`, `community_report_id=report.id`. Update `match_status`, `matched_count`, `review_count`. Set `match_status="complete"` if `review_count == 0`, `"partial"` if `review_count > 0`.
6. Return `CommunityReportResponse`.

### 4.6 Business rules — `POST /name-match/review-queue/{id}/resolve`

1. Load `NameMatchReviewQueue` row; 404 if not found.
2. Validate `contact_id` exists in `contacts` and `is_deleted=False`.
3. If status is already `matched` or `unmatched`: return 409 Conflict.
4. Set `resolved_contact_id=contact_id`, `resolved_by_id=current_user.id`, `resolved_at=utc_now()`, `status="matched"`.
5. If queue item has `event_id` and `status` was `pending`: `INSERT INTO participants (event_id, contact_id, source, status, registered_by_id) VALUES (...) ON CONFLICT DO NOTHING`.
6. If `teach_alias=True`: normalize `alias_text or queue_item.normalized_name`; upsert into `name_alias` via `INSERT ... ON CONFLICT(alias_text) DO UPDATE SET contact_id=EXCLUDED.contact_id` (allows correcting a wrong alias).
7. Emit `audit_log`: `action="name_match.resolved"`, `entity="name_match_review_queue"`, `entity_id=queue_item.id`.
8. Return updated `ReviewQueueItem`.

### 4.7 File-by-file create / modify

**New files:**

- `backend/app/services/name_match.py` — exports `normalize_name`, `lookup_alias`, `lookup_deterministic`, `lookup_claude`, `match_name`, `match_name_batch`, `teach_alias_from_resolution`. Imports: `jellyfish`, `anthropic`, `sqlalchemy.ext.asyncio.AsyncSession`, `app.models` (NameAlias, NameMatchReviewQueue, Contact). No router imports.
- `backend/app/routers/name_match.py` — router prefix `/name-match`. Review-queue CRUD + `reprocess-aliases`. Import `match_name_batch`, `lookup_alias`, `lookup_claude` from `services.name_match`.
- `backend/app/routers/name_aliases.py` — router prefix `/name-aliases`. Admin CRUD + `teach`. Import `normalize_name`, `teach_alias_from_resolution` from `services.name_match`.
- `backend/app/routers/community_reports.py` — router prefix `/community-reports`. Submit / list / get / update / soft-delete / reprocess. Import `match_name_batch`, `match_name` from `services.name_match`.
- `backend/alembic/versions/<rev>_s22_name_matching_tables.py` — three tables in dependency order (§3.4).
- `backend/tests/test_name_match.py` — see §8.1.
- `backend/tests/test_community_reports.py` — see §8.1.

**Modify existing files:**

- `backend/app/models.py` — add `NameAlias`, `NameMatchReviewQueue`, `CommunityReport` class definitions (§3.1–3.3). Import `Text` from sqlalchemy is already present; confirm `Date` import for `date_of_activity` (use `DateTime` not `Date` to match existing pattern — see `utc_now()` comment in models).
- `backend/app/schemas.py` — add all DTOs from §4.3. Add `from datetime import date` import if not already present.
- `backend/app/routers/attendance.py` — add `POST /attendance/name-list` endpoint after existing `list_attendance`. Import `NameListIntakeRequest`, `NameListIntakeResponse` from `app.schemas`; import `match_name_batch` from `app.services.name_match`.
- `backend/app/main.py` — register three new routers:
  ```python
  from app.routers import name_match as name_match_router, name_aliases, community_reports
  # ...
  app.include_router(name_match_router.router, dependencies=[Depends(check_setup_complete)])
  app.include_router(name_aliases.router, dependencies=[Depends(check_setup_complete)])
  app.include_router(community_reports.router, dependencies=[Depends(check_setup_complete)])
  ```
- `backend/requirements.txt` — add `jellyfish>=0.12.0` and `anthropic>=0.40.0` (both absent from current `requirements.txt`). Run `pip-audit` after adding.
- `backend/tests/conftest.py` — in the DB fixture setup, insert an `AdminSetting` row: `key="name_match.claude_enabled", value={"enabled": false}` so tests never call Anthropic. The `name_match.py` service checks this key at call time.

---

## 5. Frontend

### 5.1 New pages and routes

| Route | File | Guard | Description |
|---|---|---|---|
| `/name-match/review` | `pages/NameMatchReviewPage.tsx` | ProtectedRoute (volunteer+) | Paginated review queue + resolve modal |
| `/community-reports` | `pages/CommunityReportsPage.tsx` | ProtectedRoute (volunteer+) | List of submitted community reports |
| `/community-reports/new` | `pages/CommunityReportFormPage.tsx` | ProtectedRoute (volunteer+) | Submit new community report |
| `/community-reports/:id` | `pages/CommunityReportDetailPage.tsx` | ProtectedRoute (volunteer+) | Report detail + matched/pending summary |

Add to `frontend/src/App.tsx` route list. Add nav entries to `BottomNav.tsx` "More" sheet:
- `/name-match/review` — admin-only (badge with pending count).
- `/community-reports` — volunteer-visible.

### 5.2 TanStack Query keys

```typescript
// Review queue
['name-match', 'review-queue', filters]       // list
['name-match', 'review-queue', id]            // single item (used post-mutation)

// Name aliases
['name-aliases', filters]

// Community reports
['community-reports', filters]
['community-reports', id]
```

Mutations invalidate their respective list keys. After queue resolve/unmatch: also invalidate `['participants', eventId]` if the item had an `event_id`. After community-report submit: invalidate `['community-reports', filters]`.

### 5.3 `NameMatchReviewPage.tsx`

**Layout (mobile-first, 375px base):**
- Top: filter bar — status select (All / Pending / Matched / Unmatched), source filter select, event filter (autocomplete against events list), raw-name search field. All inline on desktop; collapsible row on mobile.
- Center: scrollable card list. Each card: `raw_name` (bold), `normalized_name` (small, `text-muted-foreground`), source chip, event title if present, status badge, candidate count chip, "Resolve" / "Skip" / "No Match" buttons.
- Pagination: Prev/Next + page size selector (25/50).
- `pending_count` badge on nav entry.

**`ResolveMatchModal.tsx`** (new component, `components/name-match/ResolveMatchModal.tsx`):
- Shows `raw_name` (large, `text-foreground`), `normalized_name` (small, muted), source context, event title.
- Up to 5 candidate contact cards (display name, score percentage, stage chip). Clicking a card selects it.
- Free-text search field → `MemberSearchModal`-style lookup (reuse or adapt the existing `MemberSearchModal` from `components/tasks/MemberSearchModal.tsx`) to find a contact not in candidates.
- Checkbox: "Remember this match (teach alias)" — pre-checked when `match_stage_reached` is `none` or `claude` (reviewer explicitly teaching something new).
- Alias text field (pre-filled with `normalized_name`, editable when checkbox is checked).
- Buttons: **Match** (primary, green), **No Match** (secondary), **Skip** (ghost text button).
- On success: `toast.success("Matched to: {contact_display_name}")`, close modal, invalidate query keys.
- On error: `toast.error(err.response?.data?.detail || 'Resolution failed')`.

**Empty state:** `EmptyState` component (`components/ui/EmptyState.tsx`) — "No pending names to review. All caught up!".

**Loading:** `LoadingState` skeleton cards (not spinner).

**Error:** `ErrorState` with retry button.

**Role gating:** Volunteers see resolve/skip/unmatch; viewer role (S15) cannot reach this page — backend enforces `require_volunteer`.

### 5.4 `CommunityReportFormPage.tsx`

Single-page form (not multi-step) with four labelled sections:

**Section A — Event Info (grid: 1-col mobile, 2-col `md:` desktop):**
- Event picker: autocomplete `<input>` calling `GET /events?search=…`; if not found, free-text `event_title` fallback.
- Date of Activity (date `<input type="date">`).
- Time of Activity (text input, placeholder "10AM, 2:00 PM etc.").
- Location (text input).
- Zone / Community / Ministry: text input with datalist from `admin_settings` key `community_report.zones` if present, else free text. Default options match the 32 n8n form labels.
- Number of Attendees (number input).
- Event Leader (text input — name to be matched server-side).

**Section B — Attendees:**
- `<textarea>` (large, min 8 rows). Placeholder: "Enter one name per line.\nJuan dela Cruz\nMaria Santos\nNicknames are OK — we'll match automatically."
- Character/line count indicator (e.g. "12 names").
- Parsed name chips preview: split by `\n`, trim, filter empty. Show as removable chips below textarea. Removing a chip removes that line.

**Section C — Qualitative:**
- `topics` textarea — label "Topics Discussed" (required by n8n form).
- `prayer_items` textarea — label "Prayer Items to be Included in Church".
- `remarks` textarea — label "Remarks, Comments & Praise Reports".

**Section D — Photos:**
- Multi-file upload (`accept="image/*"` + `multiple`). POST each file to `/uploads/faces` equivalent (reuse `StorageUpload` service from existing `uploads` infra; returns a path). Show thumbnail grid. Store returned paths in form state.
- Helper text: "Photos are for documentation purposes. They are NOT used for facial recognition enrollment."

**Submit behavior:**
- `POST /community-reports` with collected form state.
- On success: redirect to `/community-reports/:id`; `toast.success("Report submitted — matching {n} names…")`.
- On error: `toast.error(err.response?.data?.detail || 'Submission failed')`.
- Form state in `useState` (not Zustand; ephemeral). No auto-save in v1.
- Submit button disabled + inline spinner while loading.

### 5.5 `CommunityReportDetailPage.tsx`

- Header: date, zone label, event title (linked to `/events/:id`), submitted-by name, `status` badge.
- `match_status` badge: `pending` (amber), `processing` (blue spinner), `complete` (green), `partial` (orange).
- Two panels (side-by-side on `md:`, stacked on mobile):
  - **Matched ({matched_count})**: list of contact chips — `{display_name}` linked to `/contacts/:id`. Loaded from `CommunityReportDetailResponse.matched_contacts`.
  - **Pending Review ({review_count})**: if `review_count > 0`, show count + link button "Review in queue →" → `/name-match/review?community_report_id={id}`. Or list the raw names with status "unresolved".
- Qualitative section: Topics / Prayer Items / Remarks — collapsible on mobile (`<details>/<summary>`).
- Photos grid: thumbnails via `/storage/{path}` authenticated URLs (use the existing `StorageImage` pattern from `storage.ts`).
- Admin-only: "Re-process" button → `POST /community-reports/{id}/process`, shows `NameListIntakeResponse` summary in a toast.

### 5.6 Name-list intake panel on `EventDetailPage` (S04 extension)

Add a collapsible "Attendance by Name List" `<details>` section at the bottom of `EventDetailPage.tsx` (the S04 page). Contents:
- `<textarea>` — same one-per-line UX as community-report form.
- "Submit" button → `POST /attendance/name-list` with `event_id` from route param.
- Inline result summary: "{n} matched, {m} in review queue" with link to `/name-match/review?event_id={id}`.
- Uses `useMutation` from TanStack Query; invalidates `['participants', eventId]` on success.

### 5.7 API service files

**New: `frontend/src/services/nameMatch.ts`**
```typescript
import { api } from './api';

export const nameMatchApi = {
  listReviewQueue: (params?: Record<string, unknown>) =>
    api.get('/name-match/review-queue', { params }),
  resolveItem: (id: number, data: { contact_id: number; teach_alias: boolean; alias_text?: string }) =>
    api.post(`/name-match/review-queue/${id}/resolve`, data),
  unmatchItem: (id: number, reason?: string) =>
    api.post(`/name-match/review-queue/${id}/unmatch`, { reason }),
  skipItem: (id: number) =>
    api.post(`/name-match/review-queue/${id}/skip`, {}),
  reprocessAliases: (params?: { source?: string; event_id?: number }) =>
    api.post('/name-match/review-queue/reprocess-aliases', params ?? {}),
  listAliases: (params?: Record<string, unknown>) =>
    api.get('/name-aliases', { params }),
  createAlias: (data: { alias_text: string; contact_id: number }) =>
    api.post('/name-aliases', data),
  teachAlias: (data: { review_queue_id: number; contact_id: number; alias_text?: string }) =>
    api.post('/name-aliases/teach', data),
  updateAlias: (id: number, data: { contact_id: number }) =>
    api.patch(`/name-aliases/${id}`, data),
  deleteAlias: (id: number) =>
    api.delete(`/name-aliases/${id}`),
};
```

**New: `frontend/src/services/communityReports.ts`**
```typescript
import { api } from './api';

export const communityReportsApi = {
  list: (params?: Record<string, unknown>) => api.get('/community-reports', { params }),
  get: (id: number) => api.get(`/community-reports/${id}`),
  create: (data: unknown) => api.post('/community-reports', data),
  update: (id: number, data: unknown) => api.patch(`/community-reports/${id}`, data),
  process: (id: number) => api.post(`/community-reports/${id}/process`, {}),
};
```

**Extend `frontend/src/services/attendance.ts`** (create this file if it doesn't exist yet post-S05):
```typescript
submitNameList: (data: { event_id: number; attendee_names: string[]; source?: string }) =>
  api.post('/attendance/name-list', data),
```

### 5.8 TypeScript types — `frontend/src/types/nameMatch.ts` (new)

```typescript
export interface MatchCandidate {
  contact_id: number;
  display_name: string;
  score: number;
  stage: 'deterministic' | 'alias' | 'claude';
}

export interface ReviewQueueItem {
  id: number;
  source: string;
  raw_name: string;
  normalized_name: string | null;
  raw_payload: Record<string, unknown>;
  event_id: number | null;
  event_title?: string | null;
  community_report_id: number | null;
  candidate_contact_ids: MatchCandidate[];
  match_stage_reached: string | null;
  status: 'pending' | 'matched' | 'unmatched' | 'skipped';
  resolved_contact_id: number | null;
  resolved_contact_name?: string | null;
  resolved_by_id: number | null;
  resolved_at: string | null;
  created_at: string;
}

export interface NameAlias {
  id: number;
  alias_text: string;
  contact_id: number;
  contact_display_name?: string | null;
  source: 'learned' | 'manual';
  created_at: string;
}

export interface CommunityReport {
  id: number;
  event_id: number | null;
  event_title: string | null;
  date_of_activity: string;
  time_of_activity: string | null;
  location: string | null;
  zone: string | null;
  submitted_by_contact_id: number | null;
  submitted_by_name?: string | null;
  attendee_count: number | null;
  attendee_names: string[];
  matched_count: number;
  review_count: number;
  match_status: 'pending' | 'processing' | 'complete' | 'partial';
  status: 'submitted' | 'reviewed' | 'archived';
  topics: string | null;
  prayer_items: string | null;
  remarks: string | null;
  photo_paths: string[];
  created_at: string;
}

export interface CommunityReportDetail extends CommunityReport {
  review_queue_items: ReviewQueueItem[];
  matched_contacts: Array<{ contact_id: number; display_name: string }>;
}

export interface NameListIntakeResponse {
  total: number;
  matched: number;
  skipped_existing: number;
  review_queue: number;
  results: Array<{
    raw_name: string;
    normalized: string;
    contact_id: number | null;
    contact_display_name: string | null;
    confidence: string;
    stage: string;
    review_queue_id: number | null;
  }>;
}
```

### 5.9 Role gating and UX tokens

- **Viewer role (S15 pre-wiring):** Community reports list is volunteer-visible (backend enforces `require_volunteer`; S15 will add the viewer tier). Review queue and alias CRUD are volunteer+/admin only.
- **Empty states:** `<EmptyState>` from `components/ui/` with contextual message.
- **Loading:** skeleton cards (not spinners) for list pages; inline spinner on form submit button.
- **Error:** `<ErrorState>` with retry on list pages; `toast.error(err.response?.data?.detail || '…')` on mutations.
- **Dark mode:** all new components use token classes only: `bg-card`, `text-foreground`, `border-border`, `bg-muted`, `text-muted-foreground`. No hardcoded hex.
- **Mobile-first:** review-queue cards full-width; resolve modal is a bottom-sheet on mobile (`fixed inset-0` overlay + `bottom-0` sliding panel), centered modal on `md:` breakpoint. Community-report form: single column on mobile, 2-col grid on `md:` for Section A.
- **Status badges** reuse `<StatusBadge>` from S03 (`components/ui/StatusBadge.tsx`).

---

## 6. Migration / data

**No data migration required for S22 itself** — all three tables start empty.

**n8n alias data:** the n8n `AI Agent1` (`fKkPUolayRyZrjao`) checked a "common names" list stored in a Google Sheet context. That list was not exported in the workflow JSON. The owner must provide this list as a CSV/TSV (alias → contact_id or contact name) for bulk-import into `name_alias`. A one-off admin script or repeated `POST /name-aliases` calls can populate it. This is a **pending owner artifact** (§10.1) but not a blocker for the sprint — the table starts empty and grows via review-queue resolutions.

**`participants.source` extension:** the S01-defined `participants.source` column is `String(30)` — no DB-level enum. Adding `"community_report"` as a written value requires no DDL; the S22 migration adds a comment confirming this. The master document must update S01's scope to declare `community_report` in the `source` vocabulary.

**Historical community reports:** existing Google Sheet submissions are out of scope. They would need a one-off S06-style import to create participant rows with `source='import'`.

---

## 7. Acceptance criteria

1. `POST /attendance/name-list` with a 50-name list for a valid event returns within 2 seconds (including alias + deterministic stages; Claude async if > 50 names). Response `matched + skipped_existing + review_queue == total`.
2. A name whose normalized form exactly matches a `name_alias.alias_text` row resolves at Stage 3 (alias), `stage="alias"`, `confidence="high"`, no review-queue row created, participant inserted.
3. A name with a clear exact-match contact (normalized full name, jaro-winkler ≥ 0.95, no second candidate ≥ 0.88) resolves at Stage 2 (deterministic), `stage="deterministic"`, `confidence="high"`, no review-queue row created.
4. A name with no match at any stage creates a `NameMatchReviewQueue` row with `status="pending"`, `event_id` populated, `normalized_name` set.
5. Alias lookup runs **before** deterministic fuzzy (stage ordering). Given an alias `"mar san" → ContactB` and contacts A=`"Maria Santos"` + B=`"Mario Santos"` with similar fuzzy scores, submitting `"Mar San"` returns `ContactB` from alias stage, not ContactA from fuzzy.
6. Resolving a review-queue row with `teach_alias=True` creates a `name_alias` row with `alias_text=normalize_name(raw_name)` and `contact_id=resolved_contact_id`; subsequent submission of the same name resolves at alias stage.
7. `POST /community-reports` with `attendee_names=["Juan Cruz", "Maria Santos"]` creates a `CommunityReport` row, triggers processing; `matched_count + review_count == 2` in response.
8. `POST /community-reports` without both `event_id` and `event_title` returns 422 with `detail` citing the field.
9. `POST /name-match/review-queue/reprocess-aliases` on a pending queue item whose `normalized_name` now exists in `name_alias` auto-resolves it: creates a `participants` row, sets `status="matched"`.
10. `normalize_name("Ptr. Rodel Aquino Jr.")` returns `"rodel aquino"` (honorific stripped, suffix stripped, lowercased).
11. `normalize_name("EVANGELISTA, Guada")` returns `"evangelista guada"` (comma → space, lowercased). The deterministic stage scores this correctly against a contact `first_name="Guada" last_name="Evangelista"`.
12. All three tables are created by `alembic upgrade head` cleanly on both SQLite and Postgres; `alembic downgrade -1` drops them cleanly in reverse order.
13. A `viewer`-role user (S15 pre-wiring, enforced by backend) receives 403 on `POST /name-match/review-queue/{id}/resolve`.
14. With `name_match.claude_enabled=false` in `admin_settings`, calling `match_name` with an ambiguous name creates a review-queue row and does NOT make any HTTP call to Anthropic (asserted via mock in tests).
15. The name-list intake is idempotent: submitting the same name for the same event twice results in `matched=1, skipped_existing=1` (second call) — no duplicate participant rows.
16. `match_name(raw_name=..., db=..., source="migration")` can be called directly as a library function from S06 ETL code without importing from any router module (no circular import).
17. Community-report form submission with photo files stores the returned paths in `community_report.photo_paths` and those paths are served via `/storage/` in the detail page.
18. The `NameMatchReviewPage` renders pending items, filters by source/event/status, and the resolve modal shows `candidate_contact_ids` with display names.

---

## 8. Test plan

### 8.1 Backend pytest (`backend/tests/`)

**New file: `backend/tests/test_name_match.py`**

```python
# --- normalize_name ---
def test_normalize_strips_honorific_ptr():
    assert normalize_name("Ptr. Rodel") == "rodel"

def test_normalize_handles_comma_reversed():
    assert normalize_name("Cruz, Juan") == "cruz juan"

def test_normalize_strips_accent_diacritic():
    assert normalize_name("Niño") == "nino"

def test_normalize_collapses_spaces():
    assert normalize_name("  Juan   Cruz  ") == "juan cruz"

def test_normalize_strips_ate_prefix():
    assert normalize_name("Ate Marie") == "marie"

def test_normalize_strips_initial_word():
    assert normalize_name("Marichu P.") == "marichu"

def test_normalize_uppercase_comma():
    assert normalize_name("EVANGELISTA, Guada") == "evangelista guada"

def test_normalize_suffix_stripped():
    assert normalize_name("Ptr. Rodel Aquino Jr.") == "rodel aquino"

# --- lookup_alias ---
@pytest.mark.asyncio
async def test_alias_exact_match(db_session):
    # Insert NameAlias(alias_text="cely", contact_id=<contact.id>)
    result = await lookup_alias("cely", db_session)
    assert result is not None
    assert result["stage"] == "alias"
    assert result["score"] == 1.0

@pytest.mark.asyncio
async def test_alias_miss_returns_none(db_session):
    assert await lookup_alias("zzznomatch", db_session) is None

# --- lookup_deterministic ---
@pytest.mark.asyncio
async def test_deterministic_high_confidence_match(db_session):
    # Seed contact first_name="Juan" last_name="Cruz"
    results = await lookup_deterministic("juan cruz", db_session)
    assert len(results) >= 1
    assert results[0]["score"] >= 0.95

@pytest.mark.asyncio
async def test_deterministic_no_prefix_match(db_session):
    results = await lookup_deterministic("xyz nobody", db_session)
    assert results == []

@pytest.mark.asyncio
async def test_deterministic_reversed_name(db_session):
    # Contact first_name="Juan" last_name="Cruz"; query "Cruz Juan"
    results = await lookup_deterministic("cruz juan", db_session)
    assert len(results) >= 1

# --- match_name (integration) ---
@pytest.mark.asyncio
async def test_match_name_alias_wins_over_fuzzy(db_session):
    # Setup: Contact A "Maria Santos", Contact B "Mario Santos" (similar scores)
    # Alias "mar san" → Contact B
    result = await match_name("Mar San", db_session, source="name_list", auto_enqueue=False)
    assert result["contact_id"] == contact_b.id
    assert result["stage"] == "alias"

@pytest.mark.asyncio
async def test_match_name_creates_review_queue(db_session):
    result = await match_name("Completely Fake Person", db_session, source="name_list",
                               event_id=None, auto_enqueue=True)
    assert result["contact_id"] is None
    assert result["review_queue_id"] is not None
    row = await db_session.get(NameMatchReviewQueue, result["review_queue_id"])
    assert row.status == "pending"

@pytest.mark.asyncio
async def test_match_name_skips_claude_when_disabled(db_session, monkeypatch):
    # Insert admin_setting name_match.claude_enabled = {"enabled": false}
    # monkeypatch anthropic.AsyncAnthropic to raise AssertionError if called
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda: (_ for _ in ()).throw(AssertionError("Claude called")))
    result = await match_name("Ambiguous Name Here", db_session, source="name_list", auto_enqueue=True)
    # Should not raise; should go to review queue
    assert result["stage"] == "review_queue"

# --- POST /attendance/name-list ---
@pytest.mark.asyncio
async def test_name_list_intake_creates_participants(client, db_session):
    event = ...  # seed Event
    contact = ...  # seed Contact "Juan Cruz"
    r = await client.post("/attendance/name-list", json={
        "event_id": event.id, "attendee_names": ["Juan Cruz"], "source": "name_list"
    }, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["matched"] == 1
    assert data["review_queue"] == 0

@pytest.mark.asyncio
async def test_name_list_intake_idempotent(client, db_session):
    # Submit same name twice for same event
    await client.post("/attendance/name-list", ...)
    r2 = await client.post("/attendance/name-list", ...)
    assert r2.json()["matched"] == 0
    assert r2.json()["skipped_existing"] == 1

@pytest.mark.asyncio
async def test_name_list_intake_unmatched_queued(client, db_session):
    r = await client.post("/attendance/name-list", json={
        "event_id": event.id, "attendee_names": ["Zzznoexist Person"]
    }, headers=auth_headers)
    assert r.json()["review_queue"] == 1

# --- POST /community-reports ---
@pytest.mark.asyncio
async def test_community_report_create_empty_names(client):
    r = await client.post("/community-reports", json={
        "event_title": "Youth CG",
        "date_of_activity": "2026-06-21",
        "attendee_names": []
    }, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["match_status"] == "complete"  # 0 names = trivially complete

@pytest.mark.asyncio
async def test_community_report_requires_event_id_or_title(client):
    r = await client.post("/community-reports", json={
        "date_of_activity": "2026-06-21"
    }, headers=auth_headers)
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_community_report_preserves_photo_paths(client):
    r = await client.post("/community-reports", json={
        "event_title": "CG Meeting",
        "date_of_activity": "2026-06-21",
        "photo_paths": ["photos/cr/img1.jpg", "photos/cr/img2.jpg"]
    }, headers=auth_headers)
    assert r.json()["photo_paths"] == ["photos/cr/img1.jpg", "photos/cr/img2.jpg"]

# --- Review queue resolution ---
@pytest.mark.asyncio
async def test_resolve_creates_participant(client, db_session):
    # Setup: name-list intake with unmatched name → queue row
    queue_row = ...
    r = await client.post(f"/name-match/review-queue/{queue_row.id}/resolve",
                          json={"contact_id": contact.id, "teach_alias": False},
                          headers=auth_headers)
    assert r.json()["status"] == "matched"
    # Verify participant created
    participant = await db_session.execute(select(Participant).where(...))
    assert participant.scalar_one_or_none() is not None

@pytest.mark.asyncio
async def test_resolve_teaches_alias(client, db_session):
    r = await client.post(f"/name-match/review-queue/{queue_row.id}/resolve",
                          json={"contact_id": contact.id, "teach_alias": True},
                          headers=auth_headers)
    alias = await db_session.execute(select(NameAlias).where(NameAlias.contact_id == contact.id))
    assert alias.scalar_one_or_none() is not None

@pytest.mark.asyncio
async def test_resolve_conflict_already_matched(client, db_session):
    # First resolution OK, second should return 409
    await client.post(f"/name-match/review-queue/{queue_row.id}/resolve", ...)
    r2 = await client.post(f"/name-match/review-queue/{queue_row.id}/resolve", ...)
    assert r2.status_code == 409

# --- reprocess-aliases ---
@pytest.mark.asyncio
async def test_reprocess_auto_resolves_when_alias_added(client, db_session):
    # Setup: pending queue item for "cely"; add alias "cely" → contact
    # Call reprocess-aliases
    r = await client.post("/name-match/review-queue/reprocess-aliases", json={},
                          headers=admin_headers)
    assert r.json()["auto_matched"] >= 1

# --- RBAC ---
@pytest.mark.asyncio
async def test_viewer_cannot_resolve(client, viewer_token):
    # Assuming S15 viewer role pre-wired; backend returns 403
    r = await client.post(f"/name-match/review-queue/{queue_row.id}/resolve",
                          json={"contact_id": contact.id, "teach_alias": False},
                          headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 403
```

**New file: `backend/tests/test_community_reports.py`**

- `test_community_report_list_paginated` — GET list returns `{items, total, page, page_size}`.
- `test_community_report_get_detail` — GET detail returns `review_queue_items` and `matched_contacts`.
- `test_community_report_patch_status` — PATCH status to `reviewed`; confirm updated.
- `test_community_report_soft_delete` — DELETE sets `status='archived'`; does not remove row.
- `test_community_report_reprocess` — POST /process triggers name-list processing; returns `NameListIntakeResponse`.
- `test_community_report_filter_by_zone` — GET with `?zone=Youth` returns only matching reports.

### 8.2 Frontend vitest

**`frontend/src/__tests__/NameMatchReviewPage.test.tsx`** (new):
- Renders queue list from mocked API (3 pending items).
- Filter by status="matched" calls API with `?status=matched`.
- Clicking "Resolve" button opens `ResolveMatchModal`.
- Submitting resolution calls `nameMatchApi.resolveItem` with correct payload; shows `toast.success`.
- Empty state renders when API returns 0 items.

**`frontend/src/__tests__/CommunityReportFormPage.test.tsx`** (new):
- Validates that submitting without `event_id` or `event_title` shows inline error (422 response from API → toast).
- Attendee textarea splits by `\n`; removing a chip removes the line.
- Submit calls `communityReportsApi.create` with correct payload; redirects on success.

**`frontend/src/services/__tests__/nameMatch.test.ts`** (new): unit tests for `nameMatchApi` wrappers (axios mock).

### 8.3 Build verification

`npm run build` and `npm run lint` must pass with zero new TypeScript errors. `ruff check app` must pass. `pytest tests/ -q` with `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test` must pass (all new + existing 355 backend tests).

---

## 9. Rollout / rollback / risks

### Rollout

1. Add `jellyfish>=0.12.0` and `anthropic>=0.40.0` to `backend/requirements.txt`; rebuild the backend Docker image; run `pip-audit` to verify no new CVEs.
2. Run `alembic upgrade head` — creates three empty tables. No existing functionality changes.
3. Register three new routers in `main.py`. Deploy backend.
4. Deploy frontend — new routes and nav entries; no existing page is modified except `EventDetailPage` (name-list panel, additive), `App.tsx` (new routes), and `BottomNav.tsx` (new nav entries).
5. Admin populates initial `name_alias` table from owner-provided CSV (§10.1) via `POST /name-aliases` or bulk script.
6. Add `admin_settings` key `name_match.claude_model = "claude-haiku-3-5"` (or desired model) and `name_match.claude_enabled = true` via the settings UI.
7. Volunteers begin using community-report form; admin monitors review queue.

### Rollback

- **Code rollback:** revert the backend + frontend deploy. The three tables remain (no harm — empty). Router registrations removed; endpoints return 404.
- **Schema rollback:** `alembic downgrade -1` drops `name_match_review_queue`, `community_report`, `name_alias` in that order. No other table has an FK into these at creation time (they have FKs TO other tables). Take a `pg_dump` before downgrading if any aliases or resolutions have been entered.
- **Partial rollback:** `jellyfish` and `anthropic` can remain in requirements without harm; they are only used by the new service.

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Claude API latency on large name lists (>50 names) | Medium | Async background enrichment for >50 names; return deterministic+alias results immediately; `claude_pending: true` in response; UI polls or re-fetches |
| Claude hallucinating a contact_id not in candidates | Low-Medium | Validate returned `contact_id` is in the candidates list; reject hallucinated IDs; fall through to review queue |
| Filipino names with no match (first-timers or misspellings) | High (expected) | Review queue is designed for exactly this case; `reprocess-aliases` reduces backlog over time |
| Review queue backlog growing faster than resolution | Medium | Surface `pending_count` badge in nav; S16 schedules `reprocess-aliases` nightly (EOW Mon 00:00) to auto-resolve newly-taught aliases |
| `jellyfish` library absent from requirements.txt | Confirmed missing | Add `jellyfish>=0.12.0`; run `pip-audit` |
| `anthropic` library absent from requirements.txt | Confirmed missing | Add `anthropic>=0.40.0`; run `pip-audit` |
| Duplicate `participants` rows if name-list submitted twice | None | `ON CONFLICT(event_id, contact_id) DO NOTHING` is idempotent by design (same pattern as S05 bulk insert) |
| S06/S10/S18 importing `match_name` before S22 merges | Low | Those sprints are sequenced after S22; build fails at import time if S22 not merged — forces correct ordering |
| `community_report.id` FK in `name_match_review_queue` — table must be created first | Real | Migration creation order in §3.4 is explicit; Alembic `upgrade` runs in order; `downgrade` drops queue first |

---

## 10. Open questions & pending owner artifacts

1. **Name alias CSV from n8n.** The n8n `AI Agent1` (`fKkPUolayRyZrjao`) checked a "common names" Google Sheet list. The owner must export this as CSV (alias text → contact ID or contact name) for bulk-import into `name_alias`. Blocking for alias-stage effectiveness at launch; non-blocking for the sprint itself. Owner action required post-build.

2. **Community-report zone vocabulary.** The n8n Switch node (`3yaS8JZfct8BVe8Z`) routes by community/zone. The `Community/Ministry/Zone` form field (`3yaS8JZfct8BVe8Z.json` → "Community/Ministry/Zone" node) has 32 confirmed options (from form JSON: Tagalog Service 8AM, Main Service 10AM, Afternoon Service 3PM, Ushering Ministry, IT-Multimedia, IT-Attendance Monitoring, IT-Reports Coordinators, IT-Reports Analysts, IT-System Administrators, Music Ministry, Dance Ministry, Parking Ministry, Welcome Center, Admin Office-Secretary/-Treasurer/-Auditor, Stewardship Committee, Housekeeping Ministry, Property Custodian, Arise and Build, Grievance Committee, Learning and Development, Community-Adult Men/-Adult Women/-Young Adults, Youth Campus, Community-Youth/-Kids, Visitations Ministry, Outreaches, Missions, Prayer Gatherings). Provide these as a controlled list via `admin_settings` key `community_report.zones` so the form can render a select instead of free text. Workaround in v1: free text with datalist.

3. **Claude model selection.** Default `claude-haiku-3-5` (fast, low cost). If accuracy for ambiguous Filipino names is more important than cost, `claude-sonnet-4-5` or `claude-opus-4-5` are significantly more accurate. Update `name_match.claude_model` in admin_settings post-launch based on observed accuracy. Confirm with owner.

4. **Batch-mode Claude calls.** The n8n agents processed "batches of 20, drop to 10 if ambiguous" (`sJ7EgCZkk9wKSj3D` Comparison Agent prompt). This spec calls Claude once per individual unresolved name. For cost optimization, aggregate up to 20 unresolved names into a single Claude prompt with a structured JSON array response. Deferred to post-launch optimization.

5. **Public (unauthenticated) community-report submission.** The n8n form was public (anyone with the Google Form URL). v1 requires login. If community leaders who are contacts but not system users need to submit, extend via S13's public newcomer form pattern or S19 API key. Note for master doc: S13 or S19 may add a public variant.

6. **`participants.source` column value `"community_report"`.** Confirm S01 spec declares this value in the `source` vocabulary (`String(30)`, no DDL enum). If not, flag S01 for update. S22's migration adds a comment confirming the value is writable without DDL change.

7. **`session_time` on events.** S04 owns `events.session_time` (`8AM|10AM|3PM`, the single canonical column — no `session_type`/`service_time`, per CN-15). S22's community-report form has a "Time of Activity" field that can auto-link to a Sunday service time. The community-report form should let the submitter select the service time when submitting a Sunday service report; this linkage works once S04 has landed.

8. **Newcomer "Invited By"/"Consolidated By" matching (S13).** The n8n New Friend V2 workflow (`jdVHzcMWXdANwG8R`) resolves these two contact-reference fields by name. S13 will call `match_name(source="newcomer")` from this service. The `contact_reference` custom field type (S02) is the storage; S22 provides the matching. No action in this sprint — confirm with S13 that `match_name` is the right entry point.

---

**Cross-sprint dependencies / shared-model touchpoints for master reconciliation:**

- **S11 FK manifest** must include: `name_alias.contact_id`, `name_match_review_queue.resolved_contact_id`, `community_report.submitted_by_contact_id`, `community_report.event_leader_contact_id`. These four FKs must be in S11's `CONTACTS_FK_MAP` so contact merges reassign them from loser to survivor.
- **S06** imports `match_name` from `app.services.name_match` with `source="migration"` for people-link resolution. S06's `migration_link_review` table is separate from `name_match_review_queue` — do not conflate.
- **S13** calls `match_name(..., source="newcomer")` for Invited By and Consolidated By resolution in the newcomer form. S22 must be merged before S13 implements this call.
- **S16** schedules `POST /name-match/review-queue/reprocess-aliases` at `0 0 * * 1` (Mon 00:00 — replicating n8n `AI Agent1` re-scan cron).
- **S18** imports `match_name_batch(..., source="zoom")` for Zoom attendee name resolution.
- **S01** must confirm `participants.source String(30)` allows `"community_report"`. Update S01 scope section if not already declared.
- **S04** must add `session_time` to `events` before S22's community-report form can auto-link submission time to a service slot.
- **S14** consumes `community_report` for "Reports Attendance" report lines; `community_report.zone` maps to the reporting zone groups. S14 must join `community_report` by zone.

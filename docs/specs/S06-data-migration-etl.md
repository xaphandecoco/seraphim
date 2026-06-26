# S06 — Data Migration ETL

**Phase:** B — Core CRM · **Depends on:** S01 (schema inversion, `external_id`, app-minted PKs, `Contact`/`Event`/`Participant` renames, CiviCRM excision), S02 (custom-field engine: `custom_field_def`, `validate_and_coerce`), S03 (Contact CRUD + `GET /contacts` search), S04 (Event CRUD), S05 (bulk-participant `bulk_upsert_participants` helper + streaming-CSV generator), S22 (name-matching service: `MatchService.resolve`, `name_match_review_queue`) · **Effort:** XL · **Status:** Not started

> **Sequencing (CN-11):** S06 depends on and must be implemented/sequenced **AFTER S22**. The people-link resolution phase calls S22's `MatchService.resolve` (which uses S22's `match_name`) during contact-reference resolution; S22 must land first. The phase-label grouping is aspirational — the dependency graph is authoritative, and S06 ships after S22 in practice.

> **Scope discipline:** S06 *consumes* the schema and write paths delivered by S01–S05. It adds only migration-staging tables, the CLI script, and a read-only review UI. If a referenced symbol is absent at implementation time, treat it as a defect in the upstream sprint, not as licence to invent it here. S06 does **not** call the CiviCRM API — `CiviCRMClient` is deleted in S01; this sprint reads **files only**. S06 is **not** the general-purpose CSV/XLSX Import Wizard — that is S10.

---

## 1. Goal & rationale

Light North Caloocan is performing a **big-bang cutover** from CiviCRM (locked decision, `decisions.md` Round 1). The freeze-window migration must move approximately **1,440 contacts** and **~33,600 attendance rows** from CiviCRM XLSX re-exports — which the owner will regenerate with the real numeric **Contact ID** and real numeric **Event ID** (`decisions.md` Rounds 3 and 4) — into Seraphim's app-minted schema, preserving the old CiviCRM identifiers as `contacts.external_id` / `events.external_id` (nullable `UNIQUE`, added by S01).

**Why this sprint is necessary and distinct from S10:**
S10 is the general-purpose browser-upload wizard for ongoing data entry (skip/update/fill semantics, CSV only). S06 is a one-shot, operator-run CLI tailored precisely to CiviCRM XLSX shape: four phases with strict dependency ordering (contacts → events → participants → people-links), Filipino-name-aware matching for `contact_reference` fields, and integration with S22's canonical matching service. The CLI runs on the Unraid host where the export files reside; no browser upload is needed.

**The ETL must be:**

1. **Idempotent and re-runnable** — the operator runs a dry-run, reviews the report, fixes the export or the column-map, and re-runs until counts are clean. Running twice must never duplicate contacts, events, or participants. Contacts and events are upserted on `external_id`; participants use `INSERT ... ON CONFLICT (event_id, contact_id) DO NOTHING`.

2. **Column-mapping driven** — email and extra custom-field columns are **not guaranteed** in every export (`decisions.md` Round 4). The importer accepts whatever columns exist via an explicit YAML `column_map`; it never hard-codes XLSX header positions or row offsets.

3. **Faithful to CiviCRM's data shape** — splits comma-separated multi-values into `multiselect` custom-field JSON arrays; normalises inconsistent option values (`DEPARO`/`Deparo` → one canonical value per field); resolves **people-link fields** (Invited By, Consolidated By, Community Leader, Ministry Leader, Network Leader, Lifegroup Leader) from raw name strings to `contact_reference` custom-field values via the S22 name-matching service. Ambiguous or unmatched links land in `name_match_review_queue` (S22) — never silently dropped, never mis-assigned to the legacy "use ID 1 if unsure" bucket.

4. **Auditable and reversible by batch** — each run creates an `import_batch` row with per-row `import_row_result`s, downloadable as a streaming CSV report. Operators can correlate any data discrepancy to a specific batch + row.

This sprint is the data-bridge that makes the S21 cutover executable.

---

## 2. Scope

### In scope

- A **CLI ETL script** `scripts/migrate_civicrm.py` that reads CiviCRM XLSX re-exports plus a YAML column-mapping file and loads data in strict dependency order: contacts → events → participants → people-links.
- **Dry-run mode** (`--dry-run`) that parses, validates, resolves, and reports **without writing** core rows (it still commits `import_batch` + `import_row_result` rows in a separate audit session so the report is fully inspectable in the UI post-run).
- **Idempotent upsert** of contacts keyed on `external_id`; same for events. Idempotent insert of participants keyed on `UNIQUE(event_id, contact_id)`.
- **Custom-field mapping** into `contacts.custom_data` JSONB via `custom_field_def` records created in S02. Multi-value comma split for `multiselect`, option-value normalization, type coercion (date / number / checkbox). Unknown target field names fail the batch immediately (before any writes) with a `BatchFatalError`.
- **People-link resolution** (Invited By, Consolidated By, Community Leader, Ministry Leader, Network Leader, Lifegroup Leader) via S22's `MatchService.resolve(raw_name, event_id=None, source="migration")`: single-match writes the `contact_reference` immediately; ambiguous or unmatched calls create a `name_match_review_queue` row with `raw_payload={"batch_id": N, "field_name": "...", "source_contact_id": M}`.
- **Staging / audit tables** `import_batch` and `import_row_result` with a single Alembic migration.
- **Read-only admin backend endpoints** under `/migration` for batch list, batch detail, per-row results, streaming CSV download, and migration summary.
- **Read-only admin frontend pages** `/settings/migration` (batch list) and `/settings/migration/:batchId` (per-row report + counts + CSV download + deep-link to S22 review queue).
- **Four example YAML column-map files** in `config/` directory.
- Backend pytest and frontend vitest test coverage (see §8).

### Out of scope

- The general-purpose browser-upload **CSV/XLSX Import Wizard** (skip/update/fill semantics) → **S10**.
- **Find & Merge Duplicates** → **S11**. S06 does not deduplicate contacts; if `external_id` is distinct, both rows load.
- Migrating **face data / `compreface_subjects` / `face_samples`** — face enrollment is recognition-driven, bootstrapped from photos in **S07**.
- Migrating CiviCRM activities, groups, contributions, financial data.
- **Building** `custom_field_group` / `custom_field_def` — defined in S02's admin UI before migration runs.
- Any live CiviCRM API call (`CiviCRMClient` is deleted in S01; `civicrm.py` is gone).
- The **name-matching algorithm, `name_alias` table, and `name_match_review_queue` table** — all owned by **S22**. S06 only calls `MatchService.resolve` and deep-links to S22's review UI.
- Streaming export at scale of core entities (owned by **S05**; S06 reuses the CSV streaming helper for its own `import_row_result` download).
- The `/name-match/review` review-queue page (owned by **S22**).

---

## 3. Data model changes

S06 adds **exactly two staging/audit tables**. It must not alter `contacts`, `events`, `participants`, `custom_field_group`, `custom_field_def`, or `name_match_review_queue`. All JSONB columns use `JSON().with_variant(JSONB, "postgresql")` per `models.py:22` (`JSONB = JSON().with_variant(_PG_JSONB, "postgresql")`). Timestamps are naive UTC via `utc_now()` at `models.py:27`.

### 3.1 `import_batch`

One row per ETL run (dry-run or live).

| Column | SA Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `Integer`, PK | no | identity | app-minted |
| `source_filename` | `String(512)` | yes | — | original XLSX filename(s), comma-joined if multiple |
| `entity` | `String(20)` | no | — | `contacts` \| `events` \| `participants` \| `links` |
| `mode` | `String(10)` | no | — | `dry_run` \| `live` |
| `status` | `String(20)` | no | `'running'` | `running` \| `completed` \| `failed` |
| `column_map` | `JSONB` | no | `'{}'` | exact column_map used — preserves reproducibility |
| `options` | `JSONB` | no | `'{}'` | normalization rules, defaults, multi_value_fields, etc. |
| `total_rows` | `Integer` | no | `0` | rows read from the worksheet (excluding header) |
| `created_count` | `Integer` | no | `0` | rows inserted as new |
| `updated_count` | `Integer` | no | `0` | rows upserted / modified |
| `skipped_count` | `Integer` | no | `0` | rows unchanged on idempotent re-run |
| `error_count` | `Integer` | no | `0` | rows with hard parse/FK errors |
| `review_count` | `Integer` | no | `0` | people-links queued to `name_match_review_queue` |
| `started_at` | `DateTime` naive UTC | no | `utc_now` | |
| `finished_at` | `DateTime` | yes | — | set when status leaves `running` |
| `created_by_id` | `Integer`, FK→`users.id` ON DELETE SET NULL | yes | — | null when CLI runs headless |

Indexes:
- `ix_import_batch_entity_status` on `(entity, status)`
- `ix_import_batch_started_at` on `(started_at DESC)`

### 3.2 `import_row_result`

Per-row outcome for a batch. Drives the drill-down UI and downloadable report.

| Column | SA Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `Integer`, PK | no | identity | |
| `batch_id` | `Integer`, FK→`import_batch.id` ON DELETE CASCADE | no | — | |
| `row_number` | `Integer` | no | — | 1-based sheet row (header = 0; first data row = 1) |
| `external_id` | `String(64)` | yes | — | source Contact ID / Event ID from this row, if present |
| `outcome` | `String(20)` | no | — | `created` \| `updated` \| `skipped` \| `error` \| `review` |
| `entity_id` | `Integer` | yes | — | app-minted id of the affected row; null on dry-run or error |
| `message` | `Text` | yes | — | human-readable reason (e.g. "normalized DEPARO→Deparo", "unknown Contact ID 9999") |
| `raw` | `JSONB` | yes | — | source row dict (sensitive columns whitelisted by column-map; truncated at 2 KB) |
| `created_at` | `DateTime` naive UTC | no | `utc_now` | |

Indexes:
- `ix_import_row_result_batch` on `(batch_id)`
- `ix_import_row_result_outcome` on `(batch_id, outcome)`

### 3.3 No changes to `name_match_review_queue`

Owned and created by S22. S06 writes rows into it via `MatchService.resolve(...)` but never creates or alters the table or its model.

### 3.4 Alembic migration plan

**One new file:** `backend/alembic/versions/g4b5c6d7e8f9_add_migration_etl_tables.py`

`down_revision` = latest applied head at implementation time (run `alembic heads` to verify; never edit applied migrations per AGENTS.md:100).

**Upgrade ops (in order):**
```python
op.create_table(
    "import_batch",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("source_filename", sa.String(512), nullable=True),
    sa.Column("entity", sa.String(20), nullable=False),
    sa.Column("mode", sa.String(10), nullable=False),
    sa.Column("status", sa.String(20), nullable=False, server_default="running"),
    sa.Column("column_map", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False, server_default="{}"),
    sa.Column("options", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False, server_default="{}"),
    sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("started_at", sa.DateTime(), nullable=False),
    sa.Column("finished_at", sa.DateTime(), nullable=True),
    sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
)
op.create_table(
    "import_row_result",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("batch_id", sa.Integer(), sa.ForeignKey("import_batch.id", ondelete="CASCADE"), nullable=False),
    sa.Column("row_number", sa.Integer(), nullable=False),
    sa.Column("external_id", sa.String(64), nullable=True),
    sa.Column("outcome", sa.String(20), nullable=False),
    sa.Column("entity_id", sa.Integer(), nullable=True),
    sa.Column("message", sa.Text(), nullable=True),
    sa.Column("raw", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)
op.create_index("ix_import_batch_entity_status", "import_batch", ["entity", "status"])
op.create_index("ix_import_batch_started_at", "import_batch", ["started_at"])
op.create_index("ix_import_row_result_batch", "import_row_result", ["batch_id"])
op.create_index("ix_import_row_result_outcome", "import_row_result", ["batch_id", "outcome"])
```

**Downgrade ops (child table first):**
```python
op.drop_table("import_row_result")
op.drop_table("import_batch")
```
Indexes drop implicitly with their tables. Downgrade is for dev only; in prod these tables are the permanent audit trail.

No data backfill — empty staging tables from creation.

---

## 4. Backend

All routes follow the no-`/api`-prefix convention (nginx and Vite dev proxy strip it). Admin role enforced per-route via `Depends(require_admin)` from `backend/app/dependencies.py`. All public functions are type-hinted; all I/O is `async`/`await`. Pydantic v2 `ConfigDict(from_attributes=True)` on all ORM-read schemas.

### 4.1 Endpoints

New router `backend/app/routers/migration.py` (prefix `/migration`), registered in `main.py` under `dependencies=[Depends(check_setup_complete)]` alongside the other CRM routers.

| METHOD | Path | Role | Request body / params | Response model | Notes |
|---|---|---|---|---|---|
| `GET` | `/migration/batches` | admin | `?entity=&mode=&status=&limit=50&offset=0` | `ImportBatchListResponse` | Newest first; clamp `limit` ≤ 100; optional filters on entity/mode/status |
| `GET` | `/migration/batches/{batch_id}` | admin | — | `ImportBatchDetailResponse` | Includes `pending_review_count` (live count from `name_match_review_queue`) |
| `GET` | `/migration/batches/{batch_id}/rows` | admin | `?outcome=&limit=50&offset=0` | `ImportRowResultListResponse` | Per-row drill-down; optional filter by outcome; 50-per-page default |
| `GET` | `/migration/batches/{batch_id}/report.csv` | admin | — | `text/csv` streaming | `Content-Disposition: attachment; filename="batch_{id}_report.csv"` |
| `GET` | `/migration/summary` | admin | — | `MigrationSummaryResponse` | Latest completed batch per entity + pending review count |

> The review-queue endpoints (`GET /name-match/review`, etc.) are owned by S22's router. S06 does not duplicate them. The migration UI deep-links to `/name-match/review?source=migration&batch_id={id}`.

**Pydantic schemas** (add to `backend/app/schemas.py`):

```python
class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_filename: Optional[str]
    entity: str
    mode: str
    status: str
    column_map: dict
    options: dict
    total_rows: int
    created_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    review_count: int
    started_at: datetime
    finished_at: Optional[datetime]
    created_by_id: Optional[int]

class ImportBatchListResponse(BaseModel):
    items: list[ImportBatchOut]
    total: int
    limit: int
    offset: int

class ImportBatchDetailResponse(ImportBatchOut):
    pending_review_count: int   # live: SELECT count(*) FROM name_match_review_queue
                                #   WHERE raw_payload->>'batch_id' = str(batch_id)
                                #   AND status = 'pending'

class ImportRowResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    batch_id: int
    row_number: int
    external_id: Optional[str]
    outcome: str
    entity_id: Optional[int]
    message: Optional[str]
    # raw omitted from list — available on single-row detail endpoint if added later
    created_at: datetime

class ImportRowResultListResponse(BaseModel):
    items: list[ImportRowResultOut]
    total: int
    limit: int
    offset: int

class MigrationSummaryResponse(BaseModel):
    contacts: Optional[ImportBatchOut]
    events: Optional[ImportBatchOut]
    participants: Optional[ImportBatchOut]
    links: Optional[ImportBatchOut]
    pending_reviews: int
```

**Router implementation sketch** (`backend/app/routers/migration.py`):

```python
router = APIRouter(prefix="/migration", tags=["migration"])

@router.get("/batches", response_model=ImportBatchListResponse)
async def list_batches(
    entity: Optional[str] = None,
    mode: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    limit = min(max(limit, 1), 100)
    q = select(ImportBatch).order_by(ImportBatch.started_at.desc())
    if entity: q = q.where(ImportBatch.entity == entity)
    if mode:   q = q.where(ImportBatch.mode == mode)
    if status: q = q.where(ImportBatch.status == status)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return ImportBatchListResponse(items=rows, total=total, limit=limit, offset=offset)

@router.get("/batches/{batch_id}", response_model=ImportBatchDetailResponse)
async def get_batch(batch_id: int, db: AsyncSession = Depends(get_db), _user=Depends(require_admin)):
    batch = await db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    pending = (await db.execute(
        select(func.count()).where(
            NameMatchReviewQueue.raw_payload["batch_id"].astext == str(batch_id),
            NameMatchReviewQueue.status == "pending",
        )
    )).scalar_one()
    return ImportBatchDetailResponse(**batch.__dict__, pending_review_count=pending)

@router.get("/batches/{batch_id}/rows", response_model=ImportRowResultListResponse)
async def list_rows(
    batch_id: int,
    outcome: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    limit = min(max(limit, 1), 200)
    q = select(ImportRowResult).where(ImportRowResult.batch_id == batch_id)
    if outcome: q = q.where(ImportRowResult.outcome == outcome)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(ImportRowResult.row_number).offset(offset).limit(limit))).scalars().all()
    return ImportRowResultListResponse(items=rows, total=total, limit=limit, offset=offset)

@router.get("/batches/{batch_id}/report.csv")
async def download_report(batch_id: int, db: AsyncSession = Depends(get_db), _user=Depends(require_admin)):
    batch = await db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    from app.services.migration.streaming_report import stream_batch_csv
    return StreamingResponse(
        stream_batch_csv(batch_id, db),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="batch_{batch_id}_report.csv"'},
    )

@router.get("/summary", response_model=MigrationSummaryResponse)
async def get_summary(db: AsyncSession = Depends(get_db), _user=Depends(require_admin)):
    async def latest(entity: str) -> Optional[ImportBatch]:
        row = (await db.execute(
            select(ImportBatch)
            .where(ImportBatch.entity == entity, ImportBatch.status == "completed")
            .order_by(ImportBatch.started_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        return row
    contacts = await latest("contacts")
    events   = await latest("events")
    parts    = await latest("participants")
    links    = await latest("links")
    pending  = (await db.execute(
        select(func.count()).where(NameMatchReviewQueue.status == "pending")
    )).scalar_one()
    return MigrationSummaryResponse(contacts=contacts, events=events, participants=parts, links=links, pending_reviews=pending)
```

### 4.2 Services package `backend/app/services/migration/`

#### `reader.py`

XLSX reading using `openpyxl` (add `openpyxl==3.1.5` to `requirements.txt`):

```python
from typing import Any, AsyncIterator
import asyncio, openpyxl

async def read_rows(path: str) -> AsyncIterator[dict[str, Any]]:
    """
    Yield worksheet rows as {header: cell_value} dicts.
    - openpyxl.load_workbook(read_only=True, data_only=True): streaming SAX parser;
      critical for the ~33.6k-row attendance sheet.
    - Trims string whitespace; empty string → None; entirely-blank rows skipped.
    - Wraps in asyncio.to_thread because openpyxl is synchronous I/O.
    """
    def _iter():
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(next(rows))]
        for i, row in enumerate(rows, start=1):
            values = [
                v.strip() if isinstance(v, str) else v
                for v in row
            ]
            if all(v is None or v == "" for v in values):
                continue
            yield {headers[j]: (values[j] if values[j] != "" else None) for j in range(len(headers))}
        wb.close()
    async for row in _async_wrap(_iter()):
        yield row

async def _async_wrap(sync_iter):
    """Wrap a synchronous iterator to yield in an executor."""
    import asyncio
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    sentinel = object()
    def _producer():
        for item in sync_iter:
            asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
        asyncio.run_coroutine_threadsafe(queue.put(sentinel), loop).result()
    await asyncio.to_thread(_producer)
    while True:
        item = await queue.get()
        if item is sentinel:
            break
        yield item
```

#### `normalize.py`

Pure functions — the testable core of the ETL. No database calls. All synchronous.

```python
import unicodedata, re
from typing import Any

def normalize_option(value: str | None, aliases: dict[str, str]) -> str | None:
    """
    Case-fold + whitespace-strip + apply alias map.
    'DEPARO' → 'Deparo'; ' Deparo ' → 'Deparo'.
    Alias map keys are lower-case; matching is case-insensitive on the input.
    """
    if value is None:
        return None
    stripped = value.strip()
    return aliases.get(stripped.lower(), stripped)

def split_multi(value: str | None, delimiter: str = ",") -> list[str]:
    """
    Split on delimiter, strip each part, de-duplicate preserving first-occurrence order, drop blanks.
    split_multi("Worship, Ushering , Worship") == ["Worship", "Ushering"]
    split_multi(None) == []
    """
    if not value:
        return []
    seen: dict[str, None] = {}
    result = []
    for part in str(value).split(delimiter):
        s = part.strip()
        if s and s not in seen:
            seen[s] = None
            result.append(s)
    return result

def coerce(value: Any, data_type: str) -> Any:
    """
    Coerce a raw cell value to the target data_type.
    data_type values: text | textarea | select | multiselect | date | number | checkbox | contact_reference
    Raises ValueError with context on parse failure.
    date: parses M/D/YYYY, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS (CiviCRM formats).
    number: int if whole number, else float.
    checkbox: truthy on 'yes','true','1','x','checked','y' (case-insensitive).
    text/textarea/select: stripped str; None if blank.
    contact_reference: returned unchanged (name string; resolved by loader in links phase).
    multiselect: returns the raw value; split_multi called by mapper.
    """
    if value is None:
        return None
    if data_type in ("text", "textarea", "select", "contact_reference"):
        return str(value).strip() or None
    if data_type == "multiselect":
        return str(value).strip() or None  # split_multi applied by mapper
    if data_type == "number":
        try:
            f = float(value)
            return int(f) if f == int(f) else f
        except (TypeError, ValueError):
            raise ValueError(f"Cannot parse {value!r} as number")
    if data_type == "checkbox":
        return str(value).strip().lower() in ("yes", "true", "1", "x", "checked", "y")
    if data_type == "date":
        from datetime import datetime
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(str(value).strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise ValueError(f"Cannot parse {value!r} as date (expected M/D/YYYY or YYYY-MM-DD)")
    return value  # passthrough for unknown types

def name_key(first: str, last: str) -> str:
    """
    Produce a stable comparison key: case-fold, accent-strip (NFKD), whitespace-collapse.
    name_key("José", "Dela Cruz") == "jose dela cruz"
    """
    raw = f"{first} {last}"
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_only.lower()).strip()
```

#### `mapper.py`

Applies `column_map` to a raw row dict, producing typed `{core, custom_data, links}` for contacts, or analogous shapes for events/participants.

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ContactMapped:
    row_number: int
    external_id: str | None
    core: dict[str, Any]          # keys: first_name, last_name, suffix, gender, birth_date,
                                  #       phone, email, street_address, contact_type, contact_subtype
    custom_data: dict[str, Any]   # keyed by field_def.name
    links: dict[str, str]         # field_name → raw name string (contact_reference fields)
    raw: dict[str, Any]           # original row (truncated at 2 KB for storage)

@dataclass
class EventMapped:
    row_number: int
    external_id: str | None
    core: dict[str, Any]          # title, event_type, session_time, occurrence_date,
                                  # start_at, end_at, location
    raw: dict[str, Any]

@dataclass
class ParticipantRow:
    row_number: int
    contact_external_id: str | None
    event_external_id: str | None
    status: str                   # attended | registered | no_show | cancelled
    source: str = "name_list"     # always "name_list" for migration imports
    raw: dict[str, Any] = field(default_factory=dict)

class BatchFatalError(Exception):
    """Raised before any writes; must halt the entire batch."""

class MappingError:
    def __init__(self, row_number: int, message: str, raw: dict):
        self.row_number = row_number
        self.message = message
        self.raw = raw

CORE_CONTACT_FIELDS = frozenset({
    "external_id", "first_name", "last_name", "suffix", "gender",
    "birth_date", "phone", "email", "street_address",
    "contact_type", "contact_subtype",
})

async def map_contact_row(
    raw: dict[str, Any],
    row_number: int,
    column_map: dict[str, str],
    options: dict[str, Any],
    field_defs_by_name: dict[str, Any],  # name → CustomFieldDef ORM row; loaded once per batch
) -> ContactMapped | MappingError:
    """
    Apply column_map to raw row dict.

    column_map value formats:
      "external_id"           → core contact field
      "first_name"            → core contact field
      "custom_data.barangay"  → custom field; target name = "barangay"
      "links.invited_by"      → people-link field; goes into .links dict, not .custom_data
      (unmapped columns are ignored)

    BatchFatalError (raised before batch starts, not here) if any custom_data.X target
    is not in field_defs_by_name. This is called per-row; the batch-level preflight
    runs in runner.py before row iteration.

    Row-level MappingError: missing required core field (external_id absent), coerce failure.
    """
    normalization = options.get("normalization", {})
    multi_value_fields = set(options.get("multi_value_fields", []))
    default_contact_type = options.get("default_contact_type", "individual")

    mapped_row: dict[str, Any] = {}
    for header, target in column_map.items():
        val = raw.get(header)
        mapped_row[target] = val

    external_id = mapped_row.get("external_id")
    if external_id is None:
        return MappingError(row_number, "Missing required external_id (Contact ID column not mapped or empty)", raw)
    external_id = str(external_id).strip()

    core: dict[str, Any] = {}
    custom_data: dict[str, Any] = {}
    links: dict[str, str] = {}

    for target, val in mapped_row.items():
        if target in CORE_CONTACT_FIELDS:
            if target == "birth_date" and val is not None:
                try:
                    from app.services.migration.normalize import coerce
                    val = coerce(val, "date")
                except ValueError as e:
                    return MappingError(row_number, f"birth_date: {e}", raw)
            core[target] = val
        elif target.startswith("custom_data."):
            field_name = target[len("custom_data."):]
            fd = field_defs_by_name.get(field_name)
            if fd is None:
                raise BatchFatalError(f"Column mapped to unknown custom field '{field_name}'. Define it in S02 admin before running migration.")
            from app.services.migration.normalize import coerce, split_multi, normalize_option
            aliases = normalization.get(field_name, {})
            # Normalize + coerce
            if fd.data_type == "multiselect" or field_name in multi_value_fields:
                parts = split_multi(str(val) if val is not None else None)
                val = [normalize_option(p, aliases) for p in parts]
            elif fd.data_type == "select":
                val = normalize_option(str(val).strip() if val else None, aliases)
            elif fd.data_type not in ("text", "textarea", "contact_reference"):
                try:
                    val = coerce(val, fd.data_type)
                except ValueError as e:
                    return MappingError(row_number, f"Field '{field_name}': {e}", raw)
            custom_data[field_name] = val
        elif target.startswith("links."):
            link_name = target[len("links."):]
            if val and str(val).strip():
                links[link_name] = str(val).strip()
        # else: skip unmapped / unrecognized target prefixes

    if "contact_type" not in core or not core.get("contact_type"):
        core["contact_type"] = default_contact_type

    # Truncate raw to 2 KB to keep import_row_result table manageable
    import json
    raw_str = json.dumps(raw, default=str)
    raw_stored = json.loads(raw_str[:2048]) if len(raw_str) > 2048 else raw

    return ContactMapped(
        row_number=row_number,
        external_id=external_id,
        core=core,
        custom_data=custom_data,
        links=links,
        raw=raw_stored,
    )

async def map_event_row(
    raw: dict[str, Any],
    row_number: int,
    column_map: dict[str, str],
    options: dict[str, Any],
) -> EventMapped | MappingError:
    """
    column_map targets: external_id (required), title, event_type, session_time,
    occurrence_date, start_at, end_at, location.
    """
    from app.services.migration.normalize import coerce
    mapped_row = {target: raw.get(header) for header, target in column_map.items()}
    external_id = mapped_row.get("external_id")
    if external_id is None:
        return MappingError(row_number, "Missing required external_id (Event ID column not mapped or empty)", raw)
    core = {k: v for k, v in mapped_row.items() if k != "external_id"}
    # Coerce date/datetime fields
    for field in ("occurrence_date", "start_at", "end_at"):
        if core.get(field):
            try:
                core[field] = coerce(core[field], "date")
            except ValueError as e:
                return MappingError(row_number, f"{field}: {e}", raw)
    return EventMapped(row_number=row_number, external_id=str(external_id).strip(), core=core, raw=raw)

async def map_participant_row(
    raw: dict[str, Any],
    row_number: int,
    column_map: dict[str, str],
    options: dict[str, Any],
) -> ParticipantRow | MappingError:
    """
    column_map targets: contact_external_id (required), event_external_id (required), status (optional).
    """
    mapped_row = {target: raw.get(header) for header, target in column_map.items()}
    contact_eid = mapped_row.get("contact_external_id")
    event_eid   = mapped_row.get("event_external_id")
    if contact_eid is None:
        return MappingError(row_number, "Missing contact_external_id (Contact ID column)", raw)
    if event_eid is None:
        return MappingError(row_number, "Missing event_external_id (Event ID column)", raw)
    # Map CiviCRM status strings to canonical values
    STATUS_MAP = {
        "attended": "attended", "attend": "attended",
        "registered": "registered", "register": "registered",
        "no-show": "no_show", "no_show": "no_show", "noshow": "no_show",
        "cancelled": "cancelled", "canceled": "cancelled",
    }
    raw_status = str(mapped_row.get("status") or "").strip().lower()
    status = STATUS_MAP.get(raw_status, options.get("participant_default_status", "attended"))
    return ParticipantRow(
        row_number=row_number,
        contact_external_id=str(contact_eid).strip(),
        event_external_id=str(event_eid).strip(),
        status=status,
        raw=raw,
    )
```

#### `loader.py`

Idempotent database writers. All DB I/O uses `AsyncSession`.

```python
from typing import Literal
from sqlalchemy import select, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

Outcome = Literal["created", "updated", "skipped", "error", "review"]

async def upsert_contact(
    session: AsyncSession,
    mapped: "ContactMapped",
    dry_run: bool,
) -> tuple[Outcome, int | None, str]:
    """
    Upsert keyed on external_id.
    dry_run=True: SELECT to determine outcome; no INSERT/UPDATE.
    custom_data is MERGED: read existing JSONB, apply only mapped keys, write back
    (preserves keys set by other sprints / earlier phases).

    Postgres: INSERT INTO contacts(...) ON CONFLICT (external_id) DO UPDATE SET ...
    SQLite (tests): SELECT-then-insert/update (SQLite 3.24+ supports ON CONFLICT but
    the aiosqlite driver used in tests has dialect quirks — fall back to ORM for tests).
    """
    from app.models import Contact  # name after S01 rename
    existing = (await session.execute(
        select(Contact).where(Contact.external_id == mapped.external_id)
    )).scalar_one_or_none()

    if dry_run:
        if existing is None:
            return "created", None, "Would create"
        # Check if any field differs
        changed = any(getattr(existing, k) != v for k, v in mapped.core.items() if k in Contact.__table__.columns)
        return ("updated" if changed else "skipped"), existing.id, ("Would update" if changed else "No change")

    messages: list[str] = []
    if existing is None:
        obj = Contact(**{k: v for k, v in mapped.core.items() if k != "external_id"})
        obj.external_id = mapped.external_id
        obj.custom_data = mapped.custom_data
        session.add(obj)
        await session.flush()
        return "created", obj.id, "Created"
    else:
        for k, v in mapped.core.items():
            if k not in ("external_id",):
                setattr(existing, k, v)
        # Merge custom_data
        merged = dict(existing.custom_data or {})
        merged.update(mapped.custom_data)
        existing.custom_data = merged
        norms = [m for m in messages if m]
        return "updated", existing.id, "; ".join(norms) if norms else "Updated"

async def upsert_event(
    session: AsyncSession,
    mapped: "EventMapped",
    dry_run: bool,
) -> tuple[Outcome, int | None, str]:
    """Same pattern as upsert_contact, keyed on external_id."""
    from app.models import Event  # name after S01 rename
    existing = (await session.execute(
        select(Event).where(Event.external_id == mapped.external_id)
    )).scalar_one_or_none()
    if dry_run:
        if existing is None:
            return "created", None, "Would create"
        return ("updated", existing.id, "Would update")
    if existing is None:
        obj = Event(**mapped.core)
        obj.external_id = mapped.external_id
        session.add(obj)
        await session.flush()
        return "created", obj.id, "Created"
    for k, v in mapped.core.items():
        setattr(existing, k, v)
    return "updated", existing.id, "Updated"

async def bulk_insert_participants(
    session: AsyncSession,
    rows: list["ParticipantRow"],
    contact_id_map: dict[str, int],   # external_id → app_id, pre-loaded
    event_id_map: dict[str, int],     # external_id → app_id, pre-loaded
    dry_run: bool,
) -> list[tuple[Outcome, int | None, str]]:
    """
    Resolve contact/event ids from pre-loaded dicts.
    Calls app.services.participants.bulk_upsert_participants (S05) with
    ON CONFLICT (event_id, contact_id) DO NOTHING.
    Returns list of (outcome, entity_id, message) parallel to input rows.
    dry_run: resolves but does not insert; counts existing conflicts.
    """
    from app.services.participants import bulk_upsert_participants  # S05
    resolved: list[dict] = []
    results: list[tuple[Outcome, int | None, str]] = []
    for row in rows:
        contact_id = contact_id_map.get(row.contact_external_id or "")
        event_id   = event_id_map.get(row.event_external_id or "")
        if contact_id is None:
            results.append(("error", None, f"Unknown Contact ID '{row.contact_external_id}'"))
        elif event_id is None:
            results.append(("error", None, f"Unknown Event ID '{row.event_external_id}'"))
        else:
            resolved.append({"contact_id": contact_id, "event_id": event_id, "status": row.status, "source": "name_list"})
            results.append(("created", None, ""))  # placeholder; updated after bulk call
    if not dry_run and resolved:
        inserted_ids = await bulk_upsert_participants(session, resolved)
        # Back-fill entity_ids and outcomes from inserted_ids (S05 returns list of ids or None for DO-NOTHING)
        resolved_idx = 0
        for i, (outcome, eid, msg) in enumerate(results):
            if outcome != "error":
                app_id = inserted_ids[resolved_idx] if resolved_idx < len(inserted_ids) else None
                results[i] = ("created" if app_id else "skipped", app_id, "Inserted" if app_id else "Already exists")
                resolved_idx += 1
    return results

async def write_people_link(
    session: AsyncSession,
    source_contact_id: int,
    field_name: str,
    resolved_contact_id: int,
) -> None:
    """
    Merge resolved_contact_id into contacts.custom_data[field_name].
    Skips if already set to the same value.
    """
    from app.models import Contact
    obj = await session.get(Contact, source_contact_id)
    if obj is None:
        return
    current = dict(obj.custom_data or {})
    if current.get(field_name) == resolved_contact_id:
        return  # idempotent
    current[field_name] = resolved_contact_id
    obj.custom_data = current
```

#### `runner.py`

Phase orchestrator. CLI calls `run_phase(...)`.

```python
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession

async def run_phase(
    phase: Literal["contacts", "events", "participants", "links"],
    xlsx_path: str,
    column_map: dict,
    options: dict,
    dry_run: bool,
    created_by_id: int | None,
    db_factory,   # async context manager returning AsyncSession
) -> int:         # returns import_batch.id
    """
    Full lifecycle:
    1. Create import_batch(status='running').
    2. Preflight: validate all custom_data.* targets exist in custom_field_def.
       BatchFatalError → batch.status='failed', commit, re-raise.
    3. For 'participants': pre-load {external_id→app_id} dicts for contacts + events.
    4. Stream rows from reader.read_rows → mapper.map_*_row.
       Accumulate import_row_result in a buffer.
       Commit in chunks of 500 (contacts/events) or 1000 (participants).
    5. For 'links': per contact, call MatchService.resolve per link field.
       write_people_link on SINGLE; name_match_review_queue row on AMBIGUOUS/UNMATCHED.
    6. For dry-run: wrap core writes in a savepoint that rolls back;
       batch + row-results committed in a separate audit session.
    7. batch.status='completed', finished_at=utc_now(), commit.
    7b. CN-24: on a successful **live** (non-dry-run) run, call
        `await member_status_service.recompute_all_contacts(db)` and include the
        updated counts in the run response. Skipped for dry-run.
    8. On exception: batch.status='failed', commit, re-raise.
    """
    from app.models import ImportBatch, ImportRowResult
    from app.services.migration import reader, mapper, loader
    from app.services.migration.normalize import utc_now
    from app.utils.time import utc_now  # or datetime.now(timezone.utc).replace(tzinfo=None)

    CHUNK = 500 if phase in ("contacts", "events") else 1000

    async with db_factory() as db:
        batch = ImportBatch(
            source_filename=xlsx_path,
            entity=phase,
            mode="dry_run" if dry_run else "live",
            status="running",
            column_map=column_map,
            options=options,
            started_at=utc_now(),
            created_by_id=created_by_id,
        )
        db.add(batch)
        await db.commit()
        await db.refresh(batch)
        batch_id = batch.id

        try:
            # PREFLIGHT: validate all custom_data.* targets
            if phase == "contacts":
                custom_targets = [
                    v[len("custom_data."):] for v in column_map.values()
                    if v.startswith("custom_data.")
                ]
                if custom_targets:
                    from app.models import CustomFieldDef
                    from sqlalchemy import select
                    result = await db.execute(
                        select(CustomFieldDef).where(CustomFieldDef.name.in_(custom_targets))
                    )
                    found_names = {fd.name for fd in result.scalars().all()}
                    missing = set(custom_targets) - found_names
                    if missing:
                        raise mapper.BatchFatalError(
                            f"Column(s) mapped to undefined custom fields: {sorted(missing)}. "
                            "Define them in S02 custom-field admin before running migration."
                        )
                    field_defs_by_name = {fd.name: fd for fd in result.scalars().all()}
                else:
                    field_defs_by_name = {}

            # PRE-LOAD id maps for participants phase
            contact_id_map: dict[str, int] = {}
            event_id_map: dict[str, int] = {}
            if phase == "participants":
                from app.models import Contact, Event
                contacts_result = await db.execute(select(Contact.external_id, Contact.id).where(Contact.external_id.isnot(None)))
                contact_id_map = {row[0]: row[1] for row in contacts_result.all()}
                events_result = await db.execute(select(Event.external_id, Event.id).where(Event.external_id.isnot(None)))
                event_id_map = {row[0]: row[1] for row in events_result.all()}

            # LINKS phase: pre-load contacts with link columns
            link_fields = {v[len("links."):]: k for k, v in column_map.items() if v.startswith("links.")}

            row_buffer: list[ImportRowResult] = []
            row_count = 0

            async def flush_buffer():
                for rr in row_buffer:
                    db.add(rr)
                await db.commit()
                row_buffer.clear()

            async for raw_row in reader.read_rows(xlsx_path):
                row_count += 1

                if phase == "contacts":
                    mapped = await mapper.map_contact_row(raw_row, row_count, column_map, options, field_defs_by_name)
                    if isinstance(mapped, mapper.MappingError):
                        rr = ImportRowResult(batch_id=batch_id, row_number=row_count,
                                             external_id=None, outcome="error", message=mapped.message, raw=mapped.raw)
                    else:
                        if dry_run:
                            outcome, eid, msg = await loader.upsert_contact(db, mapped, dry_run=True)
                        else:
                            sp = await db.begin_nested()
                            try:
                                outcome, eid, msg = await loader.upsert_contact(db, mapped, dry_run=False)
                                await sp.commit()
                            except Exception as e:
                                await sp.rollback()
                                outcome, eid, msg = "error", None, str(e)
                        rr = ImportRowResult(batch_id=batch_id, row_number=row_count,
                                             external_id=mapped.external_id, outcome=outcome,
                                             entity_id=eid, message=msg, raw=mapped.raw)

                elif phase == "events":
                    mapped = await mapper.map_event_row(raw_row, row_count, column_map, options)
                    if isinstance(mapped, mapper.MappingError):
                        rr = ImportRowResult(batch_id=batch_id, row_number=row_count,
                                             external_id=None, outcome="error", message=mapped.message, raw=mapped.raw)
                    else:
                        outcome, eid, msg = await loader.upsert_event(db, mapped, dry_run=dry_run)
                        rr = ImportRowResult(batch_id=batch_id, row_number=row_count,
                                             external_id=mapped.external_id, outcome=outcome,
                                             entity_id=eid, message=msg, raw=mapped.raw)

                elif phase == "participants":
                    mapped = await mapper.map_participant_row(raw_row, row_count, column_map, options)
                    if isinstance(mapped, mapper.MappingError):
                        rr = ImportRowResult(batch_id=batch_id, row_number=row_count,
                                             external_id=None, outcome="error", message=mapped.message, raw=mapped.raw)
                    else:
                        # bulk handled in chunks below; row-level result set in flush
                        # For simplicity in the per-row model, call individually
                        results = await loader.bulk_insert_participants(db, [mapped], contact_id_map, event_id_map, dry_run)
                        outcome, eid, msg = results[0]
                        rr = ImportRowResult(batch_id=batch_id, row_number=row_count,
                                             external_id=mapped.contact_external_id,
                                             outcome=outcome, entity_id=eid, message=msg, raw=mapped.raw)

                elif phase == "links":
                    # In links phase: for each row, process only link columns
                    contact_eid = raw_row.get(next(iter(column_map), ""))  # first mapped col = external_id
                    contact_eid_str = str(contact_eid).strip() if contact_eid else None
                    contact_app_id = contact_id_map.get(contact_eid_str or "")
                    if contact_app_id is None:
                        rr = ImportRowResult(batch_id=batch_id, row_number=row_count,
                                             external_id=contact_eid_str, outcome="error",
                                             message=f"Unknown contact external_id '{contact_eid_str}'", raw=raw_row)
                    else:
                        msgs = []
                        final_outcome: Outcome = "skipped"
                        for link_field, header in link_fields.items():
                            raw_name = raw_row.get(header)
                            if not raw_name or not str(raw_name).strip():
                                continue
                            from app.models import Contact
                            contact_obj = await db.get(Contact, contact_app_id)
                            # Skip if already resolved
                            existing_val = (contact_obj.custom_data or {}).get(link_field)
                            if existing_val is not None:
                                # Check if a resolved queue row exists for this name
                                msgs.append(f"{link_field}: already resolved, skipping")
                                continue
                            from app.services.matching import MatchService, MatchResult  # S22
                            match_svc = MatchService(db)
                            match_result, match_data = await match_svc.resolve(
                                raw_name=str(raw_name).strip(),
                                event_id=None,
                                source="migration",
                                raw_payload={"batch_id": batch_id, "field_name": link_field, "source_contact_id": contact_app_id},
                            )
                            if match_result == MatchResult.SINGLE:
                                if not dry_run:
                                    await loader.write_people_link(db, contact_app_id, link_field, match_data)
                                msgs.append(f"{link_field}→contact:{match_data}")
                                final_outcome = "updated"
                            else:
                                msgs.append(f"{link_field}: {match_result.value} → review queue")
                                final_outcome = "review"
                        rr = ImportRowResult(batch_id=batch_id, row_number=row_count,
                                             external_id=contact_eid_str, outcome=final_outcome,
                                             entity_id=contact_app_id, message="; ".join(msgs) or "No links", raw=raw_row)

                row_buffer.append(rr)
                if len(row_buffer) >= CHUNK:
                    await flush_buffer()

            # Final flush
            if row_buffer:
                await flush_buffer()

            # Tally counts from the DB for this batch
            from sqlalchemy import func, case
            tally = (await db.execute(
                select(
                    func.count().label("total"),
                    func.sum(case((ImportRowResult.outcome == "created", 1), else_=0)).label("created"),
                    func.sum(case((ImportRowResult.outcome == "updated", 1), else_=0)).label("updated"),
                    func.sum(case((ImportRowResult.outcome == "skipped", 1), else_=0)).label("skipped"),
                    func.sum(case((ImportRowResult.outcome == "error",   1), else_=0)).label("errors"),
                    func.sum(case((ImportRowResult.outcome == "review",  1), else_=0)).label("reviews"),
                ).where(ImportRowResult.batch_id == batch_id)
            )).one()

            batch.total_rows    = row_count
            batch.created_count = tally.created or 0
            batch.updated_count = tally.updated or 0
            batch.skipped_count = tally.skipped or 0
            batch.error_count   = tally.errors  or 0
            batch.review_count  = tally.reviews or 0
            batch.status        = "completed"
            batch.finished_at   = utc_now()
            await db.commit()

            # CN-24: after a successful LIVE run, recompute derived member status
            # for all contacts and surface the updated counts in the run response.
            if not dry_run:
                from app.services import member_status_service
                recompute_counts = await member_status_service.recompute_all_contacts(db)
                await db.commit()
                # recompute_counts is returned alongside batch_id to the CLI/caller
                # (e.g. logged in the run summary). At ~1,440 contacts this is < 5s.

            return batch_id

        except mapper.BatchFatalError:
            batch.status = "failed"
            batch.finished_at = utc_now()
            await db.commit()
            raise
        except Exception:
            batch.status = "failed"
            batch.finished_at = utc_now()
            await db.commit()
            raise
```

#### `streaming_report.py`

```python
import csv, io
from typing import AsyncIterator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ImportBatch, ImportRowResult

async def stream_batch_csv(batch_id: int, session: AsyncSession) -> AsyncIterator[str]:
    """
    Stream import_row_result rows for batch_id as CSV lines.
    Uses server-side cursor (stream_results=True) — never loads all 33k rows into RAM.
    Yields header line then one data line per row.
    """
    header = ["batch_id", "row_number", "external_id", "outcome", "entity_id", "message", "created_at"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    yield buf.getvalue()
    buf.seek(0); buf.truncate(0)

    q = (
        select(ImportRowResult)
        .where(ImportRowResult.batch_id == batch_id)
        .order_by(ImportRowResult.row_number)
        .execution_options(stream_results=True, max_row_buffer=500)
    )
    async for row in await session.stream_scalars(q):
        writer.writerow([
            row.batch_id, row.row_number, row.external_id,
            row.outcome, row.entity_id, row.message,
            row.created_at.isoformat() if row.created_at else "",
        ])
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
```

### 4.3 CLI script `scripts/migrate_civicrm.py`

```python
#!/usr/bin/env python3
"""
Seraphim Data Migration CLI — CiviCRM XLSX re-exports → Seraphim CRM.
Usage:
  python scripts/migrate_civicrm.py \
    --entity {contacts|events|participants|links} \
    --file exports/contacts.xlsx \
    --map config/contacts.map.yml \
    [--dry-run] \
    [--by-user-email admin@lightnc.org] \
    [--strict]

  --strict  Exit non-zero if error_count > 0 (useful in CI / automated rehearsals).
"""
import argparse, asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import yaml
from app.database import async_session
from app.services.migration.runner import run_phase
from app.models import User
from sqlalchemy import select

async def main():
    parser = argparse.ArgumentParser(description="Seraphim ETL — CiviCRM XLSX → Seraphim")
    parser.add_argument("--entity", required=True, choices=["contacts","events","participants","links"])
    parser.add_argument("--file",   required=True, help="Path to XLSX export file")
    parser.add_argument("--map",    required=True, help="Path to YAML column-map file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--by-user-email", default=None, help="Seraphim user email to credit (optional)")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any row errors")
    args = parser.parse_args()

    with open(args.map) as f:
        cfg = yaml.safe_load(f)
    column_map = cfg.get("column_map", {})
    options    = cfg.get("options", {})

    created_by_id = None
    if args.by_user_email:
        async with async_session() as db:
            row = (await db.execute(select(User).where(User.email == args.by_user_email))).scalar_one_or_none()
            if row:
                created_by_id = row.id
            else:
                print(f"WARNING: user '{args.by_user_email}' not found; running headless.")

    batch_id = await run_phase(
        phase=args.entity,
        xlsx_path=args.file,
        column_map=column_map,
        options=options,
        dry_run=args.dry_run,
        created_by_id=created_by_id,
        db_factory=async_session,
    )

    async with async_session() as db:
        from app.models import ImportBatch
        batch = await db.get(ImportBatch, batch_id)
        mode  = "DRY-RUN" if batch.mode == "dry_run" else "LIVE"
        print(f"\n{'='*60}")
        print(f"  Seraphim Migration — {args.entity.upper()} | {mode}")
        print(f"{'='*60}")
        print(f"  Batch ID   : {batch_id}")
        print(f"  Status     : {batch.status}")
        print(f"  Total rows : {batch.total_rows}")
        print(f"  Created    : {batch.created_count}")
        print(f"  Updated    : {batch.updated_count}")
        print(f"  Skipped    : {batch.skipped_count}")
        print(f"  Errors     : {batch.error_count}")
        print(f"  Reviews    : {batch.review_count}")
        print(f"\n  Report UI  : /settings/migration/{batch_id}")
        if batch.review_count:
            print(f"  Review URL : /name-match/review?source=migration&batch_id={batch_id}")
        print(f"{'='*60}\n")

    if args.strict and batch.error_count > 0:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
```

### 4.4 Column-map YAML format

Four example files in `config/`. The format is:

```yaml
# config/civicrm_contacts.map.example.yml
column_map:
  "Contact ID":       external_id          # REQUIRED anchor
  "First Name":       first_name
  "Last Name":        last_name
  "Suffix":           suffix
  "Gender":           gender
  "Birth Date":       birth_date
  "Phone (Home)":     phone
  "Email":            email                # optional — contacts.email is nullable
  "Street Address":   street_address
  "Contact Type":     contact_type
  "Contact Subtype":  contact_subtype
  "Barangay":         custom_data.barangay
  "PEPSOL":           custom_data.pepsol
  "Ministry":         custom_data.ministry          # multiselect — split by comma
  "Community":        custom_data.community         # select
  "Invited By":       links.invited_by              # contact_reference → links phase
  "Consolidated By":  links.consolidated_by
  "Community Leader": links.community_leader
  "Ministry Leader":  links.ministry_leader
  "Network Leader":   links.network_leader
  "Lifegroup Leader": links.lifegroup_leader

options:
  default_contact_type: individual
  multi_value_fields:
    - ministry
  normalization:
    barangay:
      deparo:              Deparo
      "bagong silang":     "Bagong Silang"
      camarin:             Camarin
      "greater grace":     "Greater Grace"
    pepsol:
      "sol1":              "SOL 1"
      "sol 1":             "SOL 1"
      "sol2":              "SOL 2"
      "sol3":              "SOL 3"
      "enc":               Encounter
      "encounter graduate": "Encounter Graduate"
      "p2s":               "Prepare to Serve"
```

```yaml
# config/civicrm_events.map.example.yml
column_map:
  "Event ID":         external_id
  "Event Title":      title
  "Event Type":       event_type
  "Session Time":     session_time
  "Event Date":       occurrence_date
  "Start Date":       start_at
  "End Date":         end_at
  "Location":         location
options:
  default_event_type: "Sunday Celebration"
```

```yaml
# config/civicrm_participants.map.example.yml
column_map:
  "Contact ID":  contact_external_id    # REQUIRED
  "Event ID":    event_external_id      # REQUIRED
  "Status":      status                 # optional — defaults to 'attended'
options:
  participant_default_status: attended
```

```yaml
# config/civicrm_links.map.example.yml
column_map:
  "Contact ID":       external_id
  "Invited By":       links.invited_by
  "Consolidated By":  links.consolidated_by
  "Community Leader": links.community_leader
  "Ministry Leader":  links.ministry_leader
  "Network Leader":   links.network_leader
  "Lifegroup Leader": links.lifegroup_leader
options: {}
```

### 4.5 Business rules and edge cases

**Email optional.** `contacts.email` is nullable (S01). S06 never uses email as a match key — `external_id` is the sole anchor.

**Participant status mapping.** CiviCRM status strings are case-insensitively mapped: `Attended`→`attended`, `Registered`→`registered`, `No-show`/`No Show`→`no_show`, `Cancelled`/`Canceled`→`cancelled`. If no status column is present (or value is blank), apply `options.participant_default_status` (default `attended`). Historical CiviCRM attendance exports with no status column all become `attended`.

**The 222 title-collision duplicates** (`decisions.md` Round 3) — rows where multiple events share the same title but have distinct numeric Event IDs. Because the participants phase looks up the event by the real numeric Event ID column (never by title), these resolve to distinct `events` rows and distinct `participants` rows. `ON CONFLICT DO NOTHING` on `UNIQUE(event_id, contact_id)` handles within-event dedup. An explicit test covers this.

**`custom_data` MERGE.** On re-run, the loader reads the existing `custom_data` dict, updates only the keys present in the current column_map, and writes back the merged dict. This prevents clobbering custom-field values set by S07 (face enrollment consent dates), S22 (resolved people-links from a previous run), or an earlier partial migration.

**People-link self-reference.** A contact whose people-link resolves to themselves is technically valid; the link is written and the row message notes "self-reference on field {field_name}".

**People-link re-run protection.** Before calling `MatchService.resolve`, the links-phase loader checks whether `contacts.custom_data[field_name]` is already set to a non-None value. If so, and if the `name_match_review_queue` has a `resolved` row for the same `(source="migration", raw_name=...)`, it skips — outcome `skipped`, message "already resolved".

**BatchFatalError vs row-level errors:**
- `BatchFatalError`: unknown mapped field, unreadable file, DB connection failure — sets `batch.status='failed'`, commits the batch row, halts immediately. No partial writes in the core tables.
- Row-level errors: unknown `external_id` reference, coerce failure, validation failure on non-required field — `outcome='error'` on that row; batch continues; `status='completed'` at the end even if `error_count > 0`. Use `--strict` to exit non-zero.

**Participant chunk insert strategy.** The 33.6k-row participants phase calls `bulk_upsert_participants` (S05) in chunks of 1,000 rows, committing each chunk. On re-run, committed rows hit `ON CONFLICT DO NOTHING` and become `skipped`. A failure at row 30,000 preserves rows 1–29,000; re-run recovers cleanly.

**Audit logging.** When `audit_log` table is available (S17), `write_people_link` should emit `action='migration.link_write'`. Guard behind a capability check so S06 ships without a hard S17 dependency:
```python
try:
    await audit_service.record(session, actor_id=None, action="migration.link_write",
                               entity="contact", entity_id=source_contact_id,
                               after={"field": field_name, "value": resolved_contact_id})
except Exception:
    pass  # audit_log table may not exist yet (S17 not merged)
```

**Performance targets:**
- Contacts phase (~1,440 rows): < 5 s.
- Events phase (< ~1,000 events): < 3 s.
- Participants phase (~33,600 rows): **< 30 s** (chunked 1,000-row set-based inserts via S05 helper; pre-loaded id-map dicts; no per-row ORM flush loop).
- Links phase (up to ~8 × 1,440 = ~11,520 name lookups): deterministic fuzzy runs in-memory; Claude API fallback (S22) adds latency for ambiguous names. CLI warns operator.

### 4.6 File-by-file change list

| Action | Path | Notes |
|---|---|---|
| CREATE | `backend/app/routers/migration.py` | 5 endpoints |
| MODIFY | `backend/app/main.py` | register `migration.router` with `dependencies=[Depends(check_setup_complete)]` |
| CREATE | `backend/app/services/migration/__init__.py` | empty package marker |
| CREATE | `backend/app/services/migration/reader.py` | streaming openpyxl XLSX reader |
| CREATE | `backend/app/services/migration/normalize.py` | pure transform functions |
| CREATE | `backend/app/services/migration/mapper.py` | column_map application, `BatchFatalError`, `ContactMapped`, `EventMapped`, `ParticipantRow`, `MappingError` |
| CREATE | `backend/app/services/migration/loader.py` | idempotent DB writers |
| CREATE | `backend/app/services/migration/runner.py` | phase orchestrator |
| CREATE | `backend/app/services/migration/streaming_report.py` | CSV streaming for report endpoint |
| MODIFY | `backend/app/models.py` | add `ImportBatch`, `ImportRowResult` models; reuse module-level `JSONB` alias (line 22) and `utc_now` (line 27) |
| MODIFY | `backend/app/schemas.py` | add `ImportBatchOut`, `ImportBatchListResponse`, `ImportBatchDetailResponse`, `ImportRowResultOut`, `ImportRowResultListResponse`, `MigrationSummaryResponse` |
| CREATE | `backend/alembic/versions/g4b5c6d7e8f9_add_migration_etl_tables.py` | single migration for both tables |
| CREATE | `scripts/migrate_civicrm.py` | CLI entrypoint (chmod +x) |
| CREATE | `config/civicrm_contacts.map.example.yml` | contacts column-map template |
| CREATE | `config/civicrm_events.map.example.yml` | events column-map template |
| CREATE | `config/civicrm_participants.map.example.yml` | participants column-map template |
| CREATE | `config/civicrm_links.map.example.yml` | people-links column-map template |
| MODIFY | `backend/requirements.txt` | add `openpyxl==3.1.5`; PyYAML already present |

---

## 5. Frontend

Admin-only screens, mobile-first with desktop table affordances at `md:` breakpoint. All API calls via `frontend/src/services/api.ts` (axios, Bearer token from `authStore`, `baseURL '/api'`); toasts via `sonner` surfacing `err.response?.data?.detail`; design tokens only (`bg-card`, `text-foreground`, `bg-background`, `border-border`, `text-foreground/50`, `bg-primary`, `text-primary-foreground`, `bg-destructive/10 text-destructive`) — no hardcoded hex. Reuse `components/ui/StateViews.tsx` (`LoadingState`, `EmptyState`, `ErrorState`) and `components/ui/ConfirmDialog.tsx` per `frontend.md`.

### 5.1 New pages and routes

| Route | Component | Guard | Purpose |
|---|---|---|---|
| `/settings/migration` | `MigrationPage` (new) | `AdminRoute` | Batch list (entity / mode / status / counts / started_at) |
| `/settings/migration/:batchId` | `MigrationReportPage` (new) | `AdminRoute` | Batch summary + per-row results + CSV download + link to S22 review queue |

Register both in `frontend/src/App.tsx` — flat router, `<AdminRoute>` wrapping, same pattern as `/settings/users` (App.tsx:50-65). Add a **"Migration"** entry under the admin "More" sheet in `frontend/src/components/layout/BottomNav.tsx`.

No S06-owned review-queue page. People-link resolution happens at `/name-match/review?source=migration&batch_id={id}` (S22's page). `MigrationReportPage` shows a pending count chip and a **"Review People Links →"** button that navigates there.

### 5.2 `MigrationPage`

TanStack Query key: `['migration-batches', {entity, mode, status, offset}]`.

```tsx
// frontend/src/pages/MigrationPage.tsx (sketch)
// - useQuery for GET /migration/batches?limit=50&offset={offset}
// - refetchInterval: 5000 when any batch has status === 'running', else undefined
// - Mobile: stacked cards. Desktop (md:): DataTable (reuse S03 DataTable) with columns:
//   Entity | Mode badge | Status badge | Created | Updated | Skipped | Errors | Reviews | Started
// - Status badges: 'running'→pulsing ring (animate-pulse) using ring-primary;
//   'completed'→text-green-600 (or bg-primary/10 text-primary); 'failed'→bg-destructive/10 text-destructive
// - Mode badges: 'dry_run'→text-foreground/50 bg-secondary; 'live'→bg-primary/10 text-primary
// - Row click → navigate('/settings/migration/' + batch.id)
// - EmptyState: "No migration runs yet. SSH into the Unraid host and run:
//               python scripts/migrate_civicrm.py --help"  (renders <code> block)
// - ErrorState: surface err.response?.data?.detail with retry
```

### 5.3 `MigrationReportPage`

TanStack Query keys: `['migration-batch', batchId]`, `['migration-rows', batchId, {outcome, offset}]`.

Layout (mobile-first, `bg-background`):

1. **Summary header card** (`bg-card rounded-xl p-4`):
   - Entity, mode badge, status badge, started/finished timestamps.
   - Five-chip stat row: Created (`bg-primary/10 text-primary`) / Updated (`bg-secondary text-secondary-foreground`) / Skipped (`text-foreground/50`) / Errors (`bg-destructive/10 text-destructive`) / Reviews (`bg-secondary text-secondary-foreground`).
   - `pending_review_count` displayed; if `> 0`, show **"Review People Links → {N} pending"** button (`bg-primary text-primary-foreground rounded-lg px-3 py-2 text-sm`) linking to `/name-match/review?source=migration&batch_id={batchId}`.
   - If `status === 'running'`: pulsing spinner in top-right; auto-refetch every 3 s (`refetchInterval: 3000`).

2. **Outcome filter tabs** (text tabs, not pills; same pattern as any tabbed filter in the app):
   - All / Created / Updated / Skipped / Errors / Reviews.
   - Selecting a tab sets `?outcome=` param and invalidates the rows query.

3. **Results table / card list**:
   - Mobile (`< md`): stacked cards with row #, external_id chip, outcome badge, message.
   - Desktop (`md:`): proper `<table>` with sortable columns: Row # | External ID | Outcome | Entity ID | Message | Created At.
   - Pagination: Prev / Next, page size 50 — exact pattern from `LogsPage.tsx:95-114` (the only existing paginator in the app).
   - Outcome badges: `created`→green, `updated`→blue, `skipped`→muted, `error`→destructive, `review`→amber.

4. **"Download report CSV"** button: `api.get('/migration/batches/{batchId}/report.csv', { responseType: 'blob' })` — same blob-download pattern as `DashboardPage.tsx:9-19` `downloadCsv` helper.

5. Back to `/settings/migration` navigation link at top.

### 5.4 Typed API service `frontend/src/services/migration.ts`

```typescript
import api from './api';
import type { ImportBatch, ImportBatchDetail, ImportRowResult, ImportRowResultList, MigrationSummary } from '../types/migration';

export const listBatches = (params: { entity?: string; mode?: string; status?: string; limit?: number; offset?: number }) =>
    api.get<{ items: ImportBatch[]; total: number; limit: number; offset: number }>('/migration/batches', { params });

export const getBatch = (batchId: number) =>
    api.get<ImportBatchDetail>(`/migration/batches/${batchId}`);

export const listRows = (batchId: number, params: { outcome?: string; limit?: number; offset?: number }) =>
    api.get<ImportRowResultList>(`/migration/batches/${batchId}/rows`, { params });

export const downloadReport = async (batchId: number) => {
    const res = await api.get(`/migration/batches/${batchId}/report.csv`, { responseType: 'blob' });
    const url = URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url; a.download = `batch_${batchId}_report.csv`;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
};

export const getMigrationSummary = () =>
    api.get<MigrationSummary>('/migration/summary');
```

### 5.5 TypeScript types `frontend/src/types/migration.ts`

```typescript
export interface ImportBatch {
    id: number;
    source_filename: string | null;
    entity: 'contacts' | 'events' | 'participants' | 'links';
    mode: 'dry_run' | 'live';
    status: 'running' | 'completed' | 'failed';
    column_map: Record<string, string>;
    options: Record<string, unknown>;
    total_rows: number;
    created_count: number;
    updated_count: number;
    skipped_count: number;
    error_count: number;
    review_count: number;
    started_at: string;
    finished_at: string | null;
    created_by_id: number | null;
}

export interface ImportBatchDetail extends ImportBatch {
    pending_review_count: number;
}

export interface ImportRowResult {
    id: number;
    batch_id: number;
    row_number: number;
    external_id: string | null;
    outcome: 'created' | 'updated' | 'skipped' | 'error' | 'review';
    entity_id: number | null;
    message: string | null;
    created_at: string;
}

export interface ImportRowResultList {
    items: ImportRowResult[];
    total: number;
    limit: number;
    offset: number;
}

export interface MigrationSummary {
    contacts: ImportBatch | null;
    events: ImportBatch | null;
    participants: ImportBatch | null;
    links: ImportBatch | null;
    pending_reviews: number;
}
```

### 5.6 Role gating

Use `useAuthStore((s) => s.isAdmin)` from `frontend/src/store/authStore.ts`. When S15 lands the `viewer` role, extend `isAdmin` → `role === 'admin'` (migration is admin-only; viewers must not access). `AdminRoute` in `frontend/src/components/layout/AdminRoute.tsx` handles redirect.

### 5.7 UX states

- **Loading:** `<LoadingState />` from `StateViews.tsx`.
- **Empty batch list:** `<EmptyState>` with code block showing CLI `--help` command.
- **Empty row results for an outcome filter:** `<EmptyState>` — "No {outcome} rows in this batch."
- **Error:** `<ErrorState>` with `retry()` callback calling `queryClient.invalidateQueries`; toast surfaces `err.response?.data?.detail`.
- **Running batch:** pulsing ring (`animate-pulse`) on status badge; `refetchInterval: 3000` until `status !== 'running'`.

### 5.8 File-by-file change list

| Action | Path |
|---|---|
| CREATE | `frontend/src/pages/MigrationPage.tsx` |
| CREATE | `frontend/src/pages/MigrationReportPage.tsx` |
| CREATE | `frontend/src/services/migration.ts` |
| CREATE | `frontend/src/types/migration.ts` |
| MODIFY | `frontend/src/App.tsx` — two new `<AdminRoute>` routes |
| MODIFY | `frontend/src/components/layout/BottomNav.tsx` — "Migration" in admin More sheet |
| REUSE (no change) | `frontend/src/components/ui/StateViews.tsx`, `ConfirmDialog.tsx`, `services/api.ts`, S03's `DataTable` + `Pagination`, `store/authStore.ts` |

---

## 6. Migration / data (this sprint IS the migration)

### 6.1 Pre-conditions (must be complete before the live run)

1. Owner re-exports from CiviCRM including **real numeric Contact ID** (on contacts export) and **real numeric Event ID** (on attendance/participants export), and all custom fields where available → XLSX files on the Unraid host at `exports/`.
2. Admin defines all `custom_field_group` and `custom_field_def` records via S02's admin UI: Barangay, PEPSOL, Ministry, Community, plus the six people-link `contact_reference` fields (Invited By, Consolidated By, Community Leader, Ministry Leader, Network Leader, Lifegroup Leader). **Must be complete before any migration phase runs.**
3. Author the four column-map YAML files from the example templates. Populate `normalization` aliases for all known dirty option values (start with `DEPARO`→`Deparo`; scan the export's unique column values for others).
4. Confirm S22's `name_alias` table has been seeded with the church's "common names" list (Filipino nicknames, first-3-letters heuristics). The links phase is more accurate with a populated alias table.

### 6.2 Full migration run sequence (dry-run then live)

```bash
# --- Phase 1: Contacts ---
# Dry run: expect ~1,440 'created', 0 'error'
python scripts/migrate_civicrm.py --entity contacts --file exports/contacts.xlsx --map config/contacts.map.yml --dry-run
# Inspect /settings/migration/{id}. When clean:
python scripts/migrate_civicrm.py --entity contacts --file exports/contacts.xlsx --map config/contacts.map.yml

# --- Phase 2: Events ---
# Dry run: expect N 'created' (distinct event IDs in attendance export)
python scripts/migrate_civicrm.py --entity events   --file exports/events.xlsx   --map config/events.map.yml   --dry-run
python scripts/migrate_civicrm.py --entity events   --file exports/events.xlsx   --map config/events.map.yml

# --- Phase 3: Participants (requires contacts + events loaded) ---
# Dry run: expect ~33,600 'created'; error rows cite specific unknown external_ids
python scripts/migrate_civicrm.py --entity participants --file exports/attendance.xlsx --map config/participants.map.yml --dry-run
python scripts/migrate_civicrm.py --entity participants --file exports/attendance.xlsx --map config/participants.map.yml

# --- Phase 4: People links (reads same contacts XLSX; requires contacts loaded; S22 must be merged) ---
# Dry run: check review_count; navigate to /name-match/review?source=migration&batch_id={id}
python scripts/migrate_civicrm.py --entity links    --file exports/contacts.xlsx  --map config/links.map.yml    --dry-run
python scripts/migrate_civicrm.py --entity links    --file exports/contacts.xlsx  --map config/links.map.yml
# After live run: navigate to /name-match/review?source=migration and resolve the queue
```

### 6.3 Post-run verification checklist (for S21 parallel-verification)

- `contacts` row count ≈ CiviCRM export row count.
- `events` row count ≈ distinct event IDs in the attendance export.
- `participants` row count ≈ total attendance rows minus intentional `ON CONFLICT DO NOTHING` dedup count.
- All `import_batch` rows for entity `participants` have `error_count == 0` (or every error row is traced and accepted).
- Navigate to `/name-match/review?source=migration` and work the queue until pending_reviews == 0.
- Re-running any phase shows all rows as `updated` or `skipped`, zero `created` (idempotency check).

### 6.4 Re-runnability guarantee

Steps above can be repeated freely before the S21 freeze window. Idempotent upserts + `ON CONFLICT DO NOTHING` + S22's upsert logic on `name_match_review_queue` mean a second run adds no duplicates and never clobbers a manually resolved people-link.

---

## 7. Acceptance criteria

1. Running the contacts phase twice produces the same `contacts` row count. The second batch's results show all rows `updated` (or `skipped` if unchanged), zero `created`.
2. After the contacts phase, every `contacts` row has `external_id` equal to its source CiviCRM Contact ID; `external_id` is unique; and the app-minted `id` is not equal to the integer value of `external_id`.
3. Two source rows with the **same event title but different numeric Event IDs** produce **two distinct** `events` rows. Two rows with the same numeric Event ID produce exactly one row (idempotent upsert).
4. A comma-separated multi-value cell (`"Worship, Ushering"`) mapped to a `multiselect` field is stored as `["Worship","Ushering"]` in `contacts.custom_data` (trimmed, de-duplicated).
5. Option-value normalization collapses `"DEPARO"`, `" Deparo "`, and `"deparo"` to the configured canonical value; the per-row `message` notes the normalization applied.
6. The participants phase inserts ~33,600 rows with no duplicate `(event_id, contact_id)` pairs. The 222 title-collision-duplicate rows in the CiviCRM export produce distinct event rows and exactly one participant row per person per event.
7. A contact row with no email column / null email imports successfully; `contacts.email` is null.
8. A participant row referencing an unknown Contact ID or Event ID produces `outcome='error'` with an actionable message; the rest of the batch continues; batch `status='completed'`.
9. A people-link name resolving to **exactly one** contact: `contacts.custom_data[field_name]` is set to that contact's app-minted id; no `name_match_review_queue` row is created for this link.
10. A people-link name resolving to **more than one** candidate: a `name_match_review_queue` row is created with `status='pending'`; `custom_data[field_name]` remains unset.
11. A people-link name with **zero** matches: a `name_match_review_queue` row is created; `custom_data[field_name]` remains unset.
12. Re-running the `links` phase on a contact that already has `custom_data["invited_by"]` set does **not** overwrite the resolved value.
13. `--dry-run` writes **no** `contacts`/`events`/`participants`/`custom_data` changes but **does** create `import_batch` + `import_row_result` rows inspectable in the UI with `mode='dry_run'` and accurate outcome counts.
14. A column-map targeting an undefined `custom_field_def.name` causes the batch to fail **before any rows are written** (`BatchFatalError`); the `import_batch` row is created with `status='failed'` and a message naming the unknown field.
15. `GET /migration/batches/{id}/report.csv` returns `text/csv`, `Content-Disposition: attachment`, with one header row and one data row per `import_row_result`; the response is never fully buffered in server RAM (confirmed by streaming generator with server-side cursor).
16. All `/migration/*` endpoints return `401` unauthenticated and `403` for a volunteer; admin returns `200`.
17. `npm run build` + `npm run lint` + `npm run test:run` all pass; `ruff check app` clean; `pytest tests/ -q` passes under `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test`.

---

## 8. Test plan

### Backend pytest — new files

**`backend/tests/test_migration_normalize.py`** (pure functions — no DB, fast):
- `test_normalize_option_deparo` — `normalize_option("DEPARO", {"deparo":"Deparo"}) == "Deparo"`; same for `" DEPARO "`, `"Deparo"`, `"deparo"`.
- `test_normalize_option_no_alias_passthrough` — value not in alias map returned stripped and unchanged.
- `test_split_multi_dedup_trim` — `split_multi("Worship, Ushering , Worship") == ["Worship","Ushering"]`.
- `test_split_multi_none_empty` — `split_multi(None) == []`; `split_multi("") == []`.
- `test_coerce_date_m_d_yyyy` — `coerce("3/9/2024", "date") == "2024-03-09"`.
- `test_coerce_date_civicrm_datetime` — `coerce("2024-03-09 00:00:00", "date") == "2024-03-09"`.
- `test_coerce_date_iso` — `coerce("2024-03-09", "date") == "2024-03-09"`.
- `test_coerce_bad_date_raises` — `coerce("not-a-date", "date")` raises `ValueError`.
- `test_coerce_checkbox_truthy_values` — `"yes"/"true"/"1"/"x"/"checked"` → `True`; `"0"/"no"/"false"` → `False`.
- `test_coerce_number_int` — `coerce("42", "number") == 42` (int).
- `test_coerce_number_float` — `coerce("3.14", "number") == 3.14`.
- `test_name_key_accent_strip` — `name_key("José", "Dela Cruz") == "jose dela cruz"`.
- `test_name_key_whitespace_collapse` — `name_key("  Juan  ", "  Santos  ") == "juan santos"`.

**`backend/tests/test_migration_mapper.py`** (mapper logic — may seed minimal DB for field_defs):
- `test_mapper_unknown_custom_field_raises_batch_fatal` — column_map targets `custom_data.nonexistent`; `map_contact_row` raises `BatchFatalError` (or the preflight in `runner.py` does); message names the unknown field.
- `test_mapper_multiselect_split_and_normalize` — Ministry cell `"Worship, Ushering"` with `multi_value_fields=["ministry"]` → `custom_data["ministry"] == ["Worship","Ushering"]`.
- `test_mapper_links_not_in_contacts_phase_custom_data` — `links.invited_by` column is collected in `.links` dict, not in `.core` or `.custom_data`.
- `test_mapper_missing_external_id_returns_mapping_error` — row without Contact ID column mapped → `MappingError`.
- `test_mapper_birth_date_coercion` — `"3/9/1990"` → `core["birth_date"] == "1990-03-09"`.
- `test_mapper_participant_status_mapping` — `"No-show"` → `status="no_show"`; `"Attended"` → `"attended"`.
- `test_mapper_participant_default_status` — no status column → `status = options["participant_default_status"]`.

**`backend/tests/test_migration_etl.py`** (async, hits the test SQLite DB — seed `custom_field_def` via `session.add`):
- `test_contacts_idempotent_upsert` — 3-row fixture run twice; `count(contacts)==3` after both; second batch `created==0`, `updated==3` (or `skipped`), `external_id` preserved, `id != int(external_id)`.
- `test_events_keyed_on_event_id_not_title` — 2 rows same title, different external_id → 2 events; 2 rows same external_id → 1 event.
- `test_participants_title_collision_222` — 2 events with same title but different external_id; one contact; one participant row per event (2 rows); re-run → both rows `skipped`.
- `test_participants_bulk_on_conflict_no_duplicate` — deliberate duplicate `(contact_external_id, event_external_id)` in the input → exactly 1 participant row; second row `skipped`.
- `test_participants_unknown_id_errors_row_not_batch` — one row with unknown Contact ID → `outcome='error'`; others `created`; `batch.status=='completed'`.
- `test_dry_run_writes_no_core_rows_but_records_batch` — `dry_run=True`; `count(contacts)==0` after run; `import_batch` + `import_row_result` exist with `mode='dry_run'` and correct outcomes.
- `test_email_absent_imports_ok` — no email column in fixture → contacts inserted, `email is None`.
- `test_custom_data_merge_preserves_existing_keys` — contact with pre-existing `custom_data={"foo": "bar"}`; migration maps only `"ministry"` → `custom_data["foo"]` remains `"bar"`.
- `test_batch_fatal_on_unknown_field_before_any_writes` — column_map targets undefined field; `run_phase` raises `BatchFatalError`; `count(contacts)==0`; batch `status=='failed'`.
- `test_option_normalization_deparo` — source cell `"DEPARO"` with normalization alias → stored as `"Deparo"`; row `message` contains "normalized".

**`backend/tests/test_migration_links.py`** (async, mock `MatchService`):
- `test_link_resolution_single_match_writes_reference` — mock `MatchService.resolve` returns `(MatchResult.SINGLE, candidate_id)`; `contacts.custom_data["invited_by"] == candidate_id`; no `name_match_review_queue` row created.
- `test_link_resolution_ambiguous_creates_queue_row` — `MatchResult.AMBIGUOUS` → one `name_match_review_queue` row with `status='pending'`; `custom_data["invited_by"]` unset.
- `test_link_resolution_unmatched_creates_queue_row` — `MatchResult.UNMATCHED` → queue row; field unset.
- `test_link_rerun_does_not_overwrite_resolved` — contact with `custom_data["invited_by"] == 5` already set; re-run links phase → field unchanged; no new queue row; outcome `skipped`.
- `test_link_queue_row_has_batch_id_in_raw_payload` — `name_match_review_queue.raw_payload["batch_id"] == batch_id`.

**`backend/tests/test_migration_api.py`** (API / RBAC):
- `test_migration_endpoints_require_admin` — each route: `401` unauthenticated; `403` as volunteer; `200` as admin.
- `test_list_batches_pagination` — create 5 `import_batch` rows; `GET /migration/batches?limit=2&offset=2` returns 2 items, `total==5`.
- `test_list_batches_entity_filter` — batches of mixed entities; `?entity=contacts` returns only contacts batches.
- `test_report_csv_streams` — batch with 3 `import_row_result` rows; `GET /migration/batches/{id}/report.csv` returns `text/csv`, `Content-Disposition` header includes `attachment`, body has header line + 3 data lines.
- `test_batch_detail_pending_review_count` — seed a `name_match_review_queue` row with `raw_payload->>'batch_id'==str(batch_id)` and `status='pending'`; `GET /migration/batches/{id}` returns `pending_review_count==1`.
- `test_migration_summary_returns_latest_per_entity` — 3 contacts batches, 1 events batch; summary `contacts` is the latest completed contacts batch.

### Frontend vitest — new test files

**`frontend/src/pages/__tests__/MigrationPage.test.tsx`**:
- `renders batch list with correct outcome chips` — mock `GET /migration/batches` with 2 batches; assert entity, status badge, count chips render.
- `shows empty state with CLI instructions when no batches` — empty response → `EmptyState` with CLI text rendered.
- `navigates to report page on row click` — click a batch row → navigate to `/settings/migration/{id}`.
- `polls while a batch is running` — one batch with `status='running'`; assert `refetchInterval` is set (≤5000ms).

**`frontend/src/pages/__tests__/MigrationReportPage.test.tsx`**:
- `renders summary header with correct counts and badges` — batch with `error_count=3, review_count=2`; error chip has destructive styling; review chip present.
- `shows review link when pending_review_count > 0` — `pending_review_count=4`; "Review People Links" button present and links to S22 URL.
- `outcome filter tab triggers refetch with outcome param` — click "Errors" tab → next fetch has `?outcome=error`.
- `download button calls report csv endpoint` — click "Download report CSV" → `GET .../report.csv` called with `responseType: 'blob'`.
- `running batch shows pulsing indicator and polls` — `status='running'`; pulsing element (`animate-pulse`) present; `refetchInterval` ≤ 3000.
- `error state renders with retry` — mock network error → `ErrorState` with retry button; click retry → re-fetches.
- `non-admin cannot reach migration routes` — `AdminRoute` with volunteer user → redirects to `/`.

---

## 9. Rollout / rollback / risks

### Rollout

1. Add `openpyxl==3.1.5` to `backend/requirements.txt`; rebuild backend Docker image.
2. CI must pass `alembic upgrade head` cleanly on Postgres (AGENTS.md:138).
3. Register `migration.router` in `backend/app/main.py` (exact insert point: after `analytics.router`, same `dependencies=[Depends(check_setup_complete)]` pattern as all CRM routers).
4. Rehearse the full ETL sequence via dry-runs before the S21 freeze window. Live run executes once during freeze.
5. S22 must be merged before the `links` phase is executed. The `contacts`/`events`/`participants` phases have no S22 dependency.

### Rollback

- **Pre-cutover (dry-run / rehearsal):** `alembic downgrade -1` drops the two staging tables. No core contact/event/participant data is present yet; nothing to recover.
- **Mid-live-run failure:** commits are chunked (500/1000 rows). Fix the XLSX or column-map and re-run the failed phase; already-committed rows hit `ON CONFLICT DO NOTHING` or upsert correctly. The `import_batch` audit trail shows exactly which rows succeeded.
- **Post-cutover catastrophic rollback:** handled by DB snapshot restore (S21 + `scripts/backup.sh`), not ETL logic. Staging tables carry the full audit trail and remain permanently.

### Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Export lacks real Contact ID / Event ID | Medium | Fatal | `mapper.py` requires `external_id` column; raises `BatchFatalError` immediately if absent. Named in `framework.md` §5 pending owner artifact #1. |
| People-link review volume too large for manual work | Medium | Medium | S22's `bulk-resolve` auto-resolves single-candidate ambiguous reviews. Queue is paginated; survives across sessions. `name_alias` table (S22) learns resolved names for re-runs. |
| Custom-field defs absent when migration runs | High (likely oversight) | High | `runner.py` preflight fails before any writes; error message names the missing fields. Documented in runbook step 2. |
| openpyxl memory pressure at 33k rows | Low | High | `read_only=True` uses streaming SAX parser; chunked 1,000-row inserts; the full sheet is never accumulated in RAM. |
| S05 `bulk_upsert_participants` signature drift | Low | Medium | S06 imports from S05's service module rather than copying. If S05 has not merged, implement directly against `INSERT ... ON CONFLICT (event_id, contact_id) DO NOTHING` and refactor to the shared helper on integration. |
| S22 not ready when links phase runs | Medium | Low for overall migration | `contacts`/`events`/`participants` phases are independent; the `links` phase is explicitly last. Block only on S22 for that phase. |
| Date parsing edge cases in old CiviCRM data | Medium | Low | `coerce("date")` handles `M/D/YYYY`, `YYYY-MM-DD`, `YYYY-MM-DD HH:MM:SS`, ISO. Unknown formats record `outcome='error'`; batch continues. |
| Dirty option values beyond known aliases | Medium | Low | Run with `--dry-run` first; inspect `import_row_result.message` for normalization warnings; update `config/*.map.yml` alias maps before live run. |

---

## 10. Open questions & pending owner artifacts

### Pending owner artifacts (do not block spec; block the live run)

1. **Fresh CiviCRM XLSX re-exports** with real **Contact ID** + **Event ID** (and email + all custom fields where available). Hard dependency for the live run. Referenced in `framework.md` §5 item #1 and `decisions.md` pending list.
2. **Authoritative custom-field list**: Barangay values, Ministry values, PEPSOL values, Community values, Followup fields, Church Info fields, and all six people-link field names — with their canonical option values. Needed to author S02 `custom_field_def` seeds and the normalization alias maps.
3. **Participant status column**: do exports carry a per-row status column, or should all historical rows default to `attended`? Spec defaults to `attended`; confirm.
4. **Multi-value delimiter**: spec assumes `,` (comma). Confirm no fields use `;` or CiviCRM's ctrl-A (ASCII 1) delimiter.

### Open questions

- **Q1 (default subtype):** When `contact_type` is absent from the export, spec defaults to `individual`. Is `contact_subtype` set for organizational contacts in the export? Confirm.
- **Q2 (self-reference):** People-link resolving to the same contact — allowed (spec writes + logs warning) or hard error? Confirm with owner.
- **Q3 (partial re-export):** If an XLSX covers only a subset of contacts (e.g. a ministry-specific extract), the contacts phase upserts (merges) the subset into the existing table. Confirm this is desired behavior vs a fresh full export only.
- **Q4 (links source file):** The links phase reads people-link columns from the same contacts XLSX as the contacts phase. Confirm no separate "relationships export" exists in CiviCRM.

### Cross-sprint dependencies and touchpoints for 00-MASTER

- **S22 (hard dependency for the links phase).** S06 calls `MatchService.resolve(raw_name, event_id=None, source="migration")` and reads/writes `name_match_review_queue`. The master must pin S22's `MatchService` public signature. Specifically: the `source` enum must include `"migration"`; `raw_payload` must be a JSONB field accepting `{"batch_id": int, "field_name": str, "source_contact_id": int}`; `MatchResult` must expose `SINGLE`, `AMBIGUOUS`, `UNMATCHED` variants. S06's `pending_review_count` query filters `raw_payload->>'batch_id'` — the JSONB key name must be stable.

- **S05 `bulk_upsert_participants` (hard dependency for the participants phase).** S06's participants loader imports `app.services.participants.bulk_upsert_participants`. The master must require S05 to export this as an importable function signature: `async def bulk_upsert_participants(session: AsyncSession, rows: list[dict]) -> list[int | None]` returning one entry per input row (app-minted id if inserted, `None` if DO NOTHING / already existed).

- **S02 custom-field engine (hard dependency for the contacts phase).** S06 reads `CustomFieldDef` rows (`name`, `data_type`, `options`, `is_multi`, `is_required`) at batch start. The master must pin the `contact_reference` storage shape: single = `int` (app-minted contact id), multi = `list[int]`. S06 writes single-int for the six people-link fields.

- **S01 schema inversion (gates-everything dependency).** S06 depends on `external_id` (nullable UNIQUE) on `contacts` and `events`, on the `Contact`/`Event`/`Participant` renames (models.py as of commit `4438595` still reads `CiviCRMMember`/`CiviCRMEvent`/`Attendance` — see `models.py:66-84, 191-214`), and on app-minted identity PKs. The master must treat S01 as a gates-everything dependency; S06 cannot start until S01 is merged and tested.

- **S15 RBAC.** Every S06 route is `require_admin`. When the viewer role lands (S15), no S06 change needed — migration is admin-only. `User.role` in `models.py:41-43` currently reads `admin | volunteer`; S15 must widen to `admin | volunteer | viewer`; S06 does not touch it.

- **S17 audit_log.** `write_people_link` guards the `audit_log` write behind a `try/except` capability check so S06 ships without a hard S17 dependency. Once S17 merges, remove the guard and add the audit calls unconditionally.

- **Inconsistency to reconcile in 00-MASTER.** `models.py:66-84, 191-214` (commit `4438595`) still defines `CiviCRMMember`, `CiviCRMEvent`, `Attendance` with CiviCRM-id PKs and `push_status`/`push_attempts`/`last_push_error`. S06 assumes S01 has renamed these to `Contact`/`Event`/`Participant`, added `external_id`, and dropped push columns. This is a hard gate — flag in 00-MASTER.

- **`participants.source` value.** The canonical `source` enum (from the data model spec) is `face|manual|zoom|name_list|community_report`. S06 sets `source="name_list"` for migrated historical attendance rows (they came from CiviCRM event reports, which themselves originated from name-list community reports). The master must ensure `name_list` is a valid enum value in `participants.source` — confirm with S04/S05 implementation.

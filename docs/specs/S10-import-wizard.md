# S10 — CSV/XLSX Import Wizard
**Phase:** D — Power features · **Depends on:** S06 (Data Migration ETL core), S02 (Dynamic Custom-Field Engine), S03 (Native Contact CRUD), S04 (Native Event CRUD), S05 (streaming-export generator reuse) · **Effort:** L · **Status:** Not started

> Conventions are binding. Read `CLAUDE.md` and `AGENTS.md` before implementing. All backend I/O is `async`/`await`; all public functions are type-hinted. No `/api` prefix in FastAPI routers (nginx/Vite strip it). JSON columns use `JSON().with_variant(JSONB, "postgresql")` (already aliased as `JSONB` in `backend/app/models.py:22`). Timestamps are naive UTC via `utc_now()` (`models.py:27-28`). One Alembic migration per schema change; never edit an applied migration. Frontend uses Tailwind design tokens (`bg-card`, `text-foreground`, `bg-background`, `border-border`, `bg-primary`/`text-primary-foreground`, `text-destructive`) — never hardcoded hex. Toasts via `sonner`, always surfacing `err.response?.data?.detail`. Access token is in-memory (Zustand `authStore`); refresh via HttpOnly cookie; all calls go through `frontend/src/services/api.ts`.

> **Scope discipline — S10 vs S06.** S06 is the **one-time, operator-run, host-side CLI migration** optimized for the exact CiviCRM export shape (Contact ID + Event ID anchored, run by an admin on the Unraid host, no HTTP upload). S10 is the **general-purpose, browser-driven, interactive import wizard** that any volunteer/admin uses repeatedly to bring in arbitrary CSV/XLSX files (a new-member sign-up sheet, a community roster, an event RSVP list). S10 **reuses S06's pure ETL core** (`normalize.py`, `mapper.py`, `loader.py` writers, `reader.py`) and **adds**: HTTP upload + parse, an interactive column-mapping UI, a dedupe-match **preview** step with explicit per-record **skip / update / fill** disposition, batched-transaction execution (~500 rows/txn), a per-row result report, and **saveable mapping presets**. S10 introduces **no** new core CRM tables (`contacts`/`events`/`participants`/`custom_field_*` are owned by S02–S04). If any field/helper referenced here is missing in the live schema at implementation time, treat it as upstream-sprint drift, not license to invent a column.

---

## 1. Goal & rationale

CiviCRM has a generic "Import Contacts / Import Participants" wizard the church uses constantly — far more often than the one-time migration. Volunteers receive spreadsheets all the time: a newcomer sign-up sheet from a Sunday service, a community-group roster, an event RSVP export, a corrected name list. Today that data has to be hand-keyed into CiviCRM one record at a time, or run through fragile CiviCRM batch UIs. To fully replace CiviCRM (the locked goal in `framework.md` §1), Seraphim needs a **self-service, browser-based import wizard** that a non-technical volunteer can drive end-to-end without touching the host shell.

S10 delivers exactly that wizard for the two entities people actually paste in repeatedly — **contacts** and **participants (attendance)** — built as a guided four-step flow:

1. **Upload** — drag-and-drop a `.csv` / `.xlsx` (UTF-8 / UTF-8-BOM / Latin-1 auto-detected); the server parses the header + a sample of rows and reports detected columns, encoding, delimiter, and row count.
2. **Map columns** — the wizard proposes a header→target mapping (core fields + active custom fields from S02; fuzzy header auto-match), the user adjusts it, declares the dedupe match key, and may **load or save a named mapping preset** so the next identical file maps itself.
3. **Dedupe + preview** — the server resolves each row against existing records using the chosen match key(s), classifies it `new` / `match` / `ambiguous` / `error`, and the user sets a per-import **conflict policy** (`skip` existing / `update` all mapped fields / `fill` only-empty fields) plus optional per-row overrides; counts are shown before any write.
4. **Run + report** — execution runs server-side in **batched transactions of ~500 rows** (so a 5,000-row paste commits in 10 chunks and a mid-file failure keeps prior chunks), producing a per-row result report (created / updated / skipped / errored) downloadable as CSV and inspectable in the UI.

The wizard is **idempotent-friendly** (re-importing the same file with `skip` policy is a no-op), **safe** (preview-before-write, soft conflict default, full audit), and **reuses the tested ETL core from S06** so normalization/coercion/dedup logic lives in exactly one place.

---

## 2. Scope (In / Out)

### In scope
- **Two import entities:** `contacts` and `participants` (attendance). (Events import is **out** — events are few and created via S04 CRUD; see Out.)
- **HTTP file upload** (`multipart/form-data`) of `.csv` and `.xlsx`, ≤ **15 MB** and ≤ **50,000 rows** (configurable cap), stored to a short-lived staging area under `STORAGE_PATH/imports/`.
- **Encoding + dialect auto-detection** for CSV (UTF-8, UTF-8-BOM, Latin-1/cp1252 fallback; comma/semicolon/tab delimiter sniffing) with manual override.
- **XLSX parsing** via the same `openpyxl` reader S06 adds (`read_only=True, data_only=True`), first worksheet by default with a sheet selector.
- **Header + sample preview** (first N rows) returned to the wizard for mapping.
- **Interactive column mapping**: each source header → one of {ignore, a core contact/participant field, a `custom_field_def.name`}; fuzzy auto-suggestion; required-field validation; the dedupe **match key** selection.
- **Saveable mapping presets** (`import_mapping_preset` table): name + entity + column_map + options; list/create/update/delete; load into the wizard.
- **Dedupe-match preview**: per-row classification against existing records using a chosen match key (`external_id`, `email`, or `name_key`); **per-import conflict policy** `skip | update | fill`; preview counts (new / will-update / will-skip / ambiguous / error) **with zero writes**.
- **Batched-transaction execution** (~500 rows per committed chunk) reusing S06's `loader.py` writers (`on_conflict_do_update` for contacts; `on_conflict_do_nothing` for participants) wrapped in the new disposition logic; per-row `import_row_result` rows reusing S06's table.
- **Per-row result report**: counts + drill-down table + **streaming CSV download** (reuse S05/S06 streaming generator).
- **Custom-field mapping + coercion**: multi-value comma-split (multiselect), option normalization, type coercion via S06 `normalize.py`; validation via S02 `custom_fields.py`.
- **Participants import**: resolve `contact_id` (by `external_id` / `email` / `name`, degrading to the **S22 name-match review queue** when name-only and ambiguous — see §10 cross-sprint note) and `event_id` (by `external_id`, or a wizard-selected target event), `source='import'`.
- **RBAC:** volunteer+ may run imports (edit right); preset management volunteer+; admin sees all imports/presets. Backed by S15's `require_volunteer`.
- **Audit:** one summary `audit_log` row per import run (action `import.run`), guarded behind the audit-table capability check (same pattern as S05/S06).
- Tests: backend pytest (parse/sniff, mapping validation, dedupe classification, skip/update/fill semantics, batched-commit resumability, preset CRUD, participants contact-resolution, RBAC, streaming report) + frontend vitest (4-step wizard flow, preset load/save, preview counts, conflict-policy toggle, error/empty states).

### Out of scope
- **Events import** — events are low-volume and created via S04 native CRUD; no spreadsheet import path. (Re-evaluate only if the owner asks.)
- **The host-side CLI migration** of the full CiviCRM dataset (~1,440 contacts / ~33.6k attendance) → **S06**. S10 is for ad-hoc, browser-driven files; the big-bang cutover stays on the CLI.
- **Building** the normalization/coercion/mapper/loader primitives — those are **S06**; S10 imports them. If S06 has not merged when S10 starts, build against S06's documented function contracts and refactor to the shared module on integration (see §9 risk, §10).
- **Find & Merge Duplicates** (record-to-record merge UI + FK reassignment) → **S11**. S10's "match" path **updates/fills/skips** an existing record by key; it never merges two existing records.
- **The name→contact AI matching service** itself (normalize → fuzzy → alias dictionary → Claude → review queue) → **S22**. S10 **consumes** it for participants name-resolution; it does not reimplement matching.
- **Smart-group / saved-search audiences, streaming export of contacts** → S05/S09 (S10 only reuses the streaming-CSV generator helper for the *report*).
- **Face data / `compreface_subjects` / `face_samples` / biometric consent** import — face enrollment is recognition-driven (S07/S08), never spreadsheet-imported.
- **Scheduled / unattended imports** (drop-a-file-and-auto-run) → not now; the wizard is interactive only. (A future automation rule could call the runner.)
- **Undo/rollback of a committed import** as a one-click button — recovery is via re-import with corrected data or S11 merge / contact soft-delete; the per-row report + audit trail make manual reversal auditable.

---

## 3. Data model changes

S10 adds **only** staging/preset tables and **reuses S06's `import_batch` + `import_row_result`** for run tracking and per-row results. It must **not** alter `contacts`, `events`, `participants`, `custom_field_group`, or `custom_field_def`. All JSON columns use the `JSONB = JSON().with_variant(_PG_JSONB, "postgresql")` alias (`models.py:16-22`); all timestamps use `utc_now()` (`models.py:27-28`).

### 3.1 Reused (owned by S06 — do not recreate)
- **`import_batch`** — S10 writes rows here with `entity ∈ {contacts, participants}` and adds two **new values** to the existing `mode` column: `wizard_preview` (the dedupe-preview pass; no core writes) and `wizard_run` (the committed run). The S06 `mode` column is a free `String(10)`; S10 introduces these values without a schema change. S10 also stores the wizard's resolved `column_map` and `options` (conflict policy, match key, encoding, target event) in the existing `column_map`/`options` JSONB columns. **One new column is required** (see 3.3): `import_batch.staging_file` to point at the uploaded file so a preview can be promoted to a run without re-uploading.
- **`import_row_result`** — S10 writes per-row outcomes here. The S06 `outcome` column (`created|updated|skipped|error|review`) already covers S10's needs; S10 adds the value **`filled`** is **not** introduced as a new enum value — a `fill`-policy write that changes columns is reported as `updated` and a `fill` that changes nothing is reported as `skipped`, both with an explanatory `message`. No schema change to `import_row_result`.

### 3.2 New table — `import_mapping_preset`
A reusable, named header→target mapping so a recurring file maps itself.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK | no | identity | app-minted |
| `name` | String(120) | no | — | display name ("Sunday newcomer sheet") |
| `entity` | String(20) | no | — | `contacts` \| `participants` |
| `column_map` | JSONB | no | `{}` | `{ "<source header>": {"target": "<field|custom:name|ignore>"} }` |
| `options` | JSONB | no | `{}` | default conflict_policy, match_key, multi_value_fields, normalization, delimiter |
| `owner_id` | Integer FK→`users.id` `ON DELETE SET NULL` | yes | — | creator; null after user deletion |
| `is_shared` | Boolean | no | `true` | `true` = visible to all volunteers; `false` = owner-only |
| `created_at` | DateTime | no | `utc_now` | naive UTC |
| `updated_at` | DateTime | no | `utc_now` (onupdate `utc_now`) | |

Constraints / indexes:
- `UniqueConstraint(entity, name)` → `uq_import_preset_entity_name` (one preset name per entity; predictable for the dropdown).
- `Index(entity, is_shared)` → `ix_import_preset_entity_shared` (list hot path).

### 3.3 Changed table — `import_batch` (one additive column)
Add `staging_file` so a `wizard_preview` batch can be promoted to a `wizard_run` against the same uploaded file.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `staging_file` | Text | yes | — | path **relative to** `STORAGE_PATH` (e.g. `imports/ab12cd34.xlsx`); null for S06 CLI batches |

Add `expires_at`:

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `expires_at` | DateTime | yes | — | staging-file TTL (`created_at + 24h`); a worker sweep purges the file and nulls `staging_file` |

> Both columns are **nullable additive** — they do not affect existing S06 CLI batches (which leave them null). They live on `import_batch` (not a new table) because they are per-run attributes and keep the report/list endpoints unified across S06 and S10.

### 3.4 Alembic migration plan
- **One** new migration `h5c6d7e8f9a0_add_import_wizard_tables.py`. `down_revision` = the current applied head at implementation time (S06's `g4b5c6d7e8f9_add_migration_etl_tables` if S06 is merged first; **run `alembic heads` and chain to the real head** — never edit an applied migration).
- **Forward ops:**
  1. `op.create_table("import_mapping_preset", ...)` with the unique constraint and index; JSONB columns declared via the dialect branch
     ```python
     bind = op.get_bind()
     json_type = postgresql.JSONB(astext_type=sa.Text()) if bind.dialect.name == "postgresql" else sa.JSON()
     ```
     `column_map`/`options` get `server_default=sa.text("'{}'")`; `is_shared` `server_default=sa.true()`; `created_at` `server_default=sa.func.now()`.
  2. `op.add_column("import_batch", sa.Column("staging_file", sa.Text(), nullable=True))`.
  3. `op.add_column("import_batch", sa.Column("expires_at", sa.DateTime(), nullable=True))`.
- **JSONB reminder:** mirror the model-level alias; use `postgresql.JSONB(astext_type=sa.Text())` on Postgres and `sa.JSON()` on SQLite so the migration is clean+idempotent on Postgres (CI asserts `alembic upgrade head`) and works under the SQLite test suite.
- **No data backfill** — `import_mapping_preset` starts empty; the two new `import_batch` columns are null on existing rows.
- **Downgrade:** `op.drop_column("import_batch", "expires_at")`; `op.drop_column("import_batch", "staging_file")`; `op.drop_table("import_mapping_preset")` (drops its indexes/constraint with it). Downgrade is dev-only; never run past cutover.
- **Dependency note:** if S06 has **not** merged when this migration is authored, this migration must also create `import_batch` / `import_row_result` (copy S06's table DDL verbatim and chain `down_revision` accordingly), and S06's later migration must guard with an inspector check. Preferred ordering is **S06 first**; flag in §10.

---

## 4. Backend

All routes follow the no-`/api`-prefix convention and register on a new router behind `check_setup_complete` + auth. Roles use `backend/app/dependencies.py`: `require_volunteer` (admin+volunteer) for run/preview/preset-write; `require_admin` only for cross-user preset deletion / viewing others' staging files. (`require_viewer` exists from S05 but imports are writes — volunteer is the floor.)

### 4.1 Endpoints

New router `backend/app/routers/imports.py`, `prefix="/imports"`, registered in `main.py` alongside the other CRM routers.

| METHOD | path | role | request | response | notes |
|---|---|---|---|---|---|
| POST | `/imports/upload` | volunteer | `multipart/form-data`: `file`, `entity` (`contacts`\|`participants`) | `ImportUploadResponse` | Stages the file under `STORAGE_PATH/imports/<token>.<ext>`; parses header + first 20 rows; sniffs encoding/delimiter/sheet; opens an `import_batch` (`mode='wizard_preview'`, `status='running'→'completed'`, `staging_file` set, `expires_at=+24h`). Returns `batch_id`, detected `columns[]`, `sample_rows[]`, `total_rows`, `encoding`, `delimiter`, `sheets[]`. Enforces 15 MB / 50k-row caps → 413/400. |
| GET | `/imports/{batch_id}/columns` | volunteer | `?sheet=` | `ImportColumnsResponse` | Re-read header + sample for a chosen sheet (XLSX multi-sheet); also returns `suggested_map` (fuzzy header→target) and the target catalog (core fields + active `custom_field_def`s for the entity). |
| POST | `/imports/{batch_id}/preview` | volunteer | `ImportPreviewRequest` `{column_map, options{conflict_policy, match_key, target_event_id?, multi_value_fields?, normalization?, delimiter?, encoding?, sheet?}}` | `ImportPreviewResponse` | Dedupe-classify **every** row (new/match/ambiguous/error) and compute `would_create/would_update/would_skip/ambiguous/errors` under the chosen policy. **No core writes.** Persists `import_row_result`s on a child `import_batch` (`mode='wizard_preview'`) so the preview is inspectable/paginated; validates the map (required fields present, custom-field targets exist) and **fails fast** (422) on an undefined custom-field target. |
| GET | `/imports/{batch_id}/preview-rows` | volunteer | `?disposition=&limit=&offset=` | `ImportRowResultListResponse` (paginated, clamp `limit`≤100) | Drill into preview classifications; filter by disposition (`new`/`match`/`ambiguous`/`error`). |
| POST | `/imports/{batch_id}/run` | volunteer | `ImportRunRequest` `{column_map, options{...}, row_overrides?: {row_number: "skip"|"create"|"update"|"fill"}}` | `ImportRunResponse` | Promote the staged file to a committed run (`mode='wizard_run'`). Streams rows through reader→mapper→normalize→loader in **~500-row committed chunks**; writes `import_row_result`s; returns the new run `batch_id` + counts. Idempotent: re-running with `conflict_policy='skip'` is a no-op. |
| GET | `/imports/batches` | volunteer | `?entity=&mode=&limit=&offset=` | `ImportBatchListResponse` (reuses S06 schema) | List the caller's import runs (admin sees all); newest first. |
| GET | `/imports/{batch_id}` | volunteer | — | `ImportBatchDetailResponse` (reuses S06 schema) | Run summary header (counts, mode, status). |
| GET | `/imports/{batch_id}/rows` | volunteer | `?outcome=&limit=&offset=` | `ImportRowResultListResponse` | Per-row results of a committed run. |
| GET | `/imports/{batch_id}/report.csv` | volunteer | — | `text/csv` stream | Streaming CSV of `import_row_result` (reuse the S05/S06 streaming generator; server-side cursor). |
| GET | `/imports/presets` | volunteer | `?entity=` | `ImportPresetListResponse` | List shared presets + the caller's private ones for the entity. |
| POST | `/imports/presets` | volunteer | `ImportPresetCreate` `{name, entity, column_map, options, is_shared}` | `ImportPresetResponse` | Create; 409 on `(entity, name)` collision. |
| PUT | `/imports/presets/{preset_id}` | volunteer (owner) / admin | `ImportPresetUpdate` | `ImportPresetResponse` | Update (owner or admin only). |
| DELETE | `/imports/presets/{preset_id}` | volunteer (owner) / admin | — | `204` | Delete (owner or admin only). |

> **Why upload-over-HTTP here but not in S06:** S10's files are small ad-hoc spreadsheets a volunteer holds in a browser; S06's files are the 33k-row migration that lives on the host. The 15 MB / 50k-row cap keeps the upload + parse well within request limits; anything larger is a migration and belongs on the S06 CLI.

### 4.2 Schemas
Add to `backend/app/schemas.py` (Pydantic v2). Reuse S06's `ImportBatchOut`, `ImportBatchListResponse`, `ImportBatchDetailResponse`, `ImportRowResultOut`, `ImportRowResultListResponse` verbatim. New:

- `ImportColumnInfo` `{name: str, index: int, sample_values: list[str]}`
- `ImportTargetField` `{key: str, label: str, kind: Literal["core","custom"], data_type: str | None, is_required: bool, is_multi: bool}`
- `ImportUploadResponse` `{batch_id: int, entity: str, columns: list[ImportColumnInfo], sample_rows: list[dict], total_rows: int, encoding: str, delimiter: str | None, sheets: list[str], expires_at: datetime}`
- `ImportColumnsResponse` `{columns: list[ImportColumnInfo], sample_rows: list[dict], suggested_map: dict[str, str], targets: list[ImportTargetField]}`
- `ImportOptions` `{conflict_policy: Literal["skip","update","fill"] = "skip", match_key: Literal["external_id","email","name"] = "external_id", target_event_id: int | None = None, multi_value_fields: list[str] = [], normalization: dict[str, dict[str,str]] = {}, delimiter: str | None = None, encoding: str | None = None, sheet: str | None = None}`
- `ImportColumnMapEntry` `{target: str}` (target = core field key, `custom:<name>`, or `ignore`)
- `ImportPreviewRequest` `{column_map: dict[str, ImportColumnMapEntry], options: ImportOptions}`
- `ImportPreviewResponse` `{preview_batch_id: int, total_rows: int, would_create: int, would_update: int, would_skip: int, ambiguous: int, errors: int, sample: list[ImportRowResultOut]}`
- `ImportRunRequest` `{column_map: dict[str, ImportColumnMapEntry], options: ImportOptions, row_overrides: dict[int, Literal["skip","create","update","fill"]] = {}}`
- `ImportRunResponse` `{batch_id: int, total_rows: int, created: int, updated: int, skipped: int, errors: int, review: int, elapsed_ms: int}`
- `ImportPresetCreate` / `ImportPresetUpdate` / `ImportPresetResponse` (`{id, name, entity, column_map, options, owner_id, is_shared, created_at, updated_at}`, `model_config = {"from_attributes": True}`)
- `ImportPresetListResponse` `{items: list[ImportPresetResponse], total: int}`

All list responses carry `total`, `limit`, `offset` (clamp `limit`≤100) per the AGENTS.md "always paginate/clamp" rule.

### 4.3 Services / workers / business rules

New package `backend/app/services/imports/` (distinct from S06's `services/migration/` but **importing S06's primitives** — see §10 for the shared-module note). Modules:

- **`staging.py`** — file staging + parse:
  - `stage_upload(file, entity, user) -> ImportUploadResponse`: validate content-type/extension (`.csv`/`.xlsx`), enforce 15 MB / 50k-row caps (count rows during sniff), write to `STORAGE_PATH/imports/<uuid4>.<ext>` (mkdir on first use), open the `import_batch` (`mode='wizard_preview'`, `staging_file`, `expires_at`).
  - `sniff_csv(raw: bytes) -> (encoding, delimiter)`: try `utf-8-sig` → `utf-8` → `cp1252`/`latin-1` (decode round-trip); delimiter via `csv.Sniffer().sniff(sample, delimiters=",;\t")` with a comma fallback. Edge: a single-column file (no delimiter) → default comma, one column.
  - `read_header_and_sample(path, entity, encoding, delimiter, sheet, n=20) -> (columns, sample_rows, total_rows, sheets)`: CSV via stdlib `csv.DictReader`; XLSX via S06 `reader.py` (`openpyxl.load_workbook(read_only=True, data_only=True)`), enumerating sheet names. Trim whitespace; empty string → `None`.
- **`suggest.py`** — `suggest_map(headers, targets) -> dict[str,str]`: fuzzy header→target using `name_key`-style folding (reuse S06 `normalize.name_key`) + a small synonym table (`"first name"|"firstname"|"given name" → first_name`, `"contact id"|"id" → external_id`, `"email address" → email`, etc.); custom-field defs matched by label/name fold. Unmatched headers default to `ignore`.
- **`disposition.py`** — the new logic S10 adds on top of S06's loader (the **testable core**):
  - `classify_contact(row_data, existing, match_key) -> Literal["new","match","ambiguous","error"]`: resolve the match key into 0/1/>1 existing contacts (`external_id` exact; `email` case-folded exact; `name` via `name_key`). 0 → `new`; 1 → `match`; >1 → `ambiguous`; missing required core field (`first_name`) or unparseable key → `error`.
  - `apply_policy(existing, incoming, policy) -> (action, changed_fields)`: `skip` → `("skip", [])`; `update` → overwrite every mapped non-None field, merging `custom_data` key-wise (never clobber unmapped custom keys); `fill` → set only fields that are currently empty/None on the existing record (core + custom), report which changed. A `fill`/`update` that changes nothing → `skip` outcome with message "no changes".
  - `resolve_participant(row_data, options) -> (contact_id|None, event_id|None, problems[])`: contact via `match_key` (external_id/email/name); event via `external_id` column **or** `options.target_event_id` (wizard-selected). Name-only ambiguity → defer to **S22 name-match review queue** (write a `name_match_review_queue` row, outcome `review`) rather than guess — see §10. Unresolvable contact/event with no name to queue → `error`.
- **`runner.py`** — `run_preview(batch_id, req)` and `run_import(batch_id, req)`:
  - **Preview:** iterate all rows (streamed via `reader`), map+normalize each, `classify_*`, persist `import_row_result` with the would-be `outcome` mapped from disposition (`new→created`-intent, `match`+policy→`updated`/`skipped`, `ambiguous→review`, `error→error`); accumulate counts; **never** touch core tables; commit only the audit/row-result rows. Returns `ImportPreviewResponse`.
  - **Run:** open a `wizard_run` `import_batch`; stream rows; accumulate a **chunk buffer**; every **500 rows** (configurable `IMPORT_CHUNK_SIZE`) flush the buffer through S06's `loader` writers under one transaction, then `commit()` (so a failure at row 2,300 keeps rows 1–2,000). Apply `row_overrides[row_number]` to override the per-import policy for individual rows. Contacts use S06 `loader.upsert_contacts` (Postgres `on_conflict_do_update` keyed on `external_id`; SQLite select-then-write branch) **but gated by the disposition** — only rows the policy says to write are included; `skip` rows produce a `skipped` result without an INSERT/UPDATE. Participants use S06 `loader` set-based `insert(...).on_conflict_do_nothing(index_elements=["event_id","contact_id"])` in the same 500-row chunks (reuses the S05 bulk helper per S06 §10). Write per-row `import_row_result`; set `status='completed'`/`finished_at`. **CN-24:** after the run completes successfully (all chunks committed), call `await member_status_service.recompute_all_contacts(db)` so derived member status reflects the freshly imported/updated contacts; then return counts + `elapsed_ms`. (Preview runs do **not** recompute — no core writes occur.)
  - **Match-key when `match_key='external_id'` for a file with no external_id column:** the mapper has nothing to anchor on → every row classifies `new` (create). When `match_key='email'`/`'name'`, dedup uses those. Document that `external_id` is only meaningful for files carrying the original id (e.g. a re-export); newcomer sheets should pick `email` or `name`.
- **`presets.py`** — preset CRUD service (list with `is_shared OR owner_id==caller`, create/update/delete with owner/admin guard).
- **Staging-file purge** — extend the existing worker loop (`backend/app/services/queue_manager.py`, where `_process_face_cleanup` lives — reuse the worker **host**, never the deleted CiviCRM push): a sweep deletes `STORAGE_PATH/imports/*` whose `import_batch.expires_at < utc_now()` and nulls `staging_file`. (If S05's `_process_export_jobs` purge already iterates staging dirs, extend it; otherwise add `_process_import_cleanup`.)

**Edge cases / rules**
- **Encoding mojibake** — if `cp1252` decode still yields replacement chars, surface a clear 400 ("Could not decode file; re-save as UTF-8") rather than importing garbage.
- **Header collisions** — two source columns mapping to the same target → 422 at preview/run with a clear message.
- **Required core field** — contacts require `first_name`; a row missing it → `error` result, batch continues (never aborts the whole run for one bad row).
- **Multi-value + normalization** — comma-split (`normalize.split_multi`) for fields listed in `options.multi_value_fields` / typed `multiselect`; option normalization (`normalize.normalize_option`) per `options.normalization` (DEPARO→Deparo example, same as S06).
- **`custom_data` merge safety** — both `update` and `fill` **merge** custom keys (read existing, set mapped keys, write back); never overwrite custom keys not present in this import, and never clobber values written by S07 face-enrollment.
- **Idempotency** — re-importing the identical file with `conflict_policy='skip'` writes nothing new; with `external_id` match key, `update` re-applies the same values (reports `updated`/"no changes"→`skipped`). Participants rely on `ON CONFLICT (event_id, contact_id) DO NOTHING`.
- **Performance** — 5,000-row contacts import target < 8 s on Postgres (10 chunked txns + set-based upsert); preview of 5,000 rows < 5 s (no writes to core). XLSX read stays bounded via `read_only=True`. Never per-row ORM flush for the write path.
- **Audit** — one `audit_log` row per committed run (`action='import.run'`, `entity`, `after={counts, conflict_policy, match_key}`), guarded behind a capability check until `audit_log` exists (S15/S17), matching S05/S06.

### 4.4 File-by-file change list
- **CREATE** `backend/app/routers/imports.py` — the endpoints in 4.1.
- **MODIFY** `backend/app/main.py` — register `imports.router` (behind `check_setup_complete`, auth-gated; volunteer enforced per-route).
- **CREATE** `backend/app/services/imports/__init__.py`
- **CREATE** `backend/app/services/imports/staging.py`
- **CREATE** `backend/app/services/imports/suggest.py`
- **CREATE** `backend/app/services/imports/disposition.py`
- **CREATE** `backend/app/services/imports/runner.py`
- **CREATE** `backend/app/services/imports/presets.py`
- **MODIFY** `backend/app/models.py` — add `ImportMappingPreset`; add `staging_file` + `expires_at` columns to S06's `ImportBatch` model (reuse the `JSONB` alias `models.py:22` and `utc_now` `models.py:27`).
- **MODIFY** `backend/app/schemas.py` — add the schemas in 4.2.
- **CREATE** `backend/alembic/versions/h5c6d7e8f9a0_add_import_wizard_tables.py`
- **MODIFY** `backend/app/services/queue_manager.py` — add/extend the staging-file purge sweep (reuse worker host).
- **MODIFY** `backend/app/services/imports/runner.py` to **import** S06's `services.migration.reader`, `.normalize`, `.mapper`, `.loader` (do not re-implement). If the shared module is renamed during integration (see §10), update the import path only.
- **MODIFY** `backend/requirements.txt` — no new dep (S06 already adds `openpyxl==3.1.5`; `PyYAML` already present; stdlib `csv`); if S06 not merged, add `openpyxl==3.1.5` here and remove the dup on integration.

---

## 5. Frontend

A guided **4-step wizard** at `/imports/new`, plus a runs list and a report page. Mobile-first (375px) with desktop table affordances; all calls via `frontend/src/services/api.ts`; toasts via `sonner` surfacing `err.response?.data?.detail`; design tokens only; reuse `StateViews.tsx` (`LoadingState`/`EmptyState`/`ErrorState`) and `ConfirmDialog.tsx`.

### 5.1 Pages / routes / components

| Route | Component | Guard | Purpose |
|---|---|---|---|
| `/imports` | `ImportsPage` (new) | `ProtectedRoute` (volunteer+) | list of import runs (entity, mode, status, counts, started_at) + "New import" CTA + presets manager link |
| `/imports/new` | `ImportWizardPage` (new) | `ProtectedRoute` (volunteer+) | the 4-step wizard host (Upload → Map → Preview → Run) |
| `/imports/:batchId` | `ImportReportPage` (new) | `ProtectedRoute` (volunteer+) | run summary header + paginated per-row results (outcome filter) + "Download report CSV" |

- Register routes in `frontend/src/App.tsx` (flat router, under `ProtectedRoute` like the other authenticated pages).
- Add a "Import" entry to the admin/volunteer "More" sheet in `frontend/src/components/layout/BottomNav.tsx` (hidden for `viewer`).

**Wizard step components** (under `frontend/src/components/imports/`):
- `Step1Upload.tsx` — entity radio (Contacts / Participants), drag-drop + file picker, client-side type/size hint; on drop → `POST /imports/upload` (FormData) → store `batchId` + detected columns/encoding/delimiter/sheets in wizard state; sheet selector for XLSX (re-fetch `/imports/{id}/columns?sheet=`). Loading spinner during parse; error toast on 400/413.
- `Step2Map.tsx` — table of source columns with a target `<select>` each (options: Ignore, core fields, custom fields grouped by label); preloads `suggested_map`; **Preset bar**: "Load preset…" dropdown (`GET /imports/presets?entity=`), "Save as preset…" (name + shared toggle → `POST /imports/presets`). Match-key selector (`external_id`/`email`/`name`) and, for participants, a **target event picker** (reuse the event search/select from S04) or "use Event ID column". Inline validation: required-field-missing and duplicate-target warnings before "Next".
- `Step3Preview.tsx` — "Run preview" → `POST /imports/{id}/preview`; renders the **counts strip** (New · Will update · Will skip · Ambiguous · Errors) with token color chips, a **conflict-policy segmented control** (`Skip existing` / `Update all` / `Fill empty only`) that re-runs preview on change, and a paginated `/imports/{id}/preview-rows` table with disposition filter. Per-row override menu (skip/create/update/fill) writes into `row_overrides`. Ambiguous participant rows show a note that they go to the name-match review queue (S22).
- `Step4Run.tsx` — summary recap + `ConfirmDialog` ("Import 412 contacts: 380 new, 32 updates, 0 skipped?") → `POST /imports/{id}/run` → progress; on success route to `/imports/{batchId}` (report) and toast counts ("Created 380 · Updated 32 · Skipped 0 · 1.4 s").
- `ImportReportTable.tsx` — shared by `Step3Preview` and `ImportReportPage` (outcome chips, message, row#, external_id; mobile = stacked cards, `md:` = real table; Prev/Next pagination mirroring `LogsPage.tsx`).
- `PresetManager.tsx` — list/edit/delete presets (owner/admin), used from `ImportsPage`.

### 5.2 State (TanStack Query keys / Zustand)
- Query keys: `['import-batches', {entity, mode, offset}]`, `['import-batch', batchId]`, `['import-rows', batchId, {outcome, offset}]`, `['import-preview-rows', batchId, {disposition, offset}]`, `['import-columns', batchId, sheet]`, `['import-presets', entity]`.
- **Mutations** (`useMutation`): `uploadFile`, `runPreview`, `runImport`, `savePreset`, `updatePreset`, `deletePreset`; invalidate `['import-batches']` / `['import-presets', entity]` on success (mirrors the `useMutation` pattern S06 establishes).
- **Wizard transient state** is local to `ImportWizardPage` (a `useReducer` or small local object: `{step, entity, batchId, columns, columnMap, options, rowOverrides, previewCounts}`) — **no** new Zustand store. Role gating reads `useAuthStore((s) => s.role)` (volunteer+ to see the wizard; viewer hidden).

### 5.3 Role gating
- All `/imports*` routes under `ProtectedRoute`; the wizard and preset writes require **volunteer+** (server enforces `require_volunteer`). `viewer` users do not see the nav entry and are redirected if they deep-link (client guard + server 403). Cross-user preset delete is **admin** (server-enforced).

### 5.4 UX states / tokens / mobile-first
- **Loading:** `LoadingState` during upload/parse, preview, and run.
- **Empty:** `EmptyState` — runs list: "No imports yet. Import a CSV or Excel file."; preview with zero rows: "No data rows found in this file."
- **Error:** `ErrorState` with retry + `sonner` toast surfacing server `detail` (encoding/cap/validation errors).
- **Counts chips:** token colors — `bg-primary/text-primary-foreground` for new/created, an `amber`/`warning` token (or `bg-secondary`) for update/fill, `text-foreground/50` for skip, `text-destructive` for error/ambiguous. No hex; works under `.dark`.
- **CSV download:** reuse the blob-download helper pattern from `DashboardPage.tsx` (`downloadCsv`), hitting `/imports/{id}/report.csv` with the auth header.
- **Mobile-first:** the wizard is a single-column stepper with a sticky step header + Back/Next footer; the mapping table becomes stacked label→select cards on narrow screens, a real two-column table at `md:`. The conflict-policy control is a segmented control mirroring the tab pattern in `AttendancePage.tsx`.

### 5.5 File-by-file change list (frontend)
- **CREATE** `frontend/src/pages/ImportsPage.tsx`
- **CREATE** `frontend/src/pages/ImportWizardPage.tsx`
- **CREATE** `frontend/src/pages/ImportReportPage.tsx`
- **CREATE** `frontend/src/components/imports/Step1Upload.tsx`
- **CREATE** `frontend/src/components/imports/Step2Map.tsx`
- **CREATE** `frontend/src/components/imports/Step3Preview.tsx`
- **CREATE** `frontend/src/components/imports/Step4Run.tsx`
- **CREATE** `frontend/src/components/imports/ImportReportTable.tsx`
- **CREATE** `frontend/src/components/imports/PresetManager.tsx`
- **CREATE** `frontend/src/services/imports.ts` — typed wrappers (`uploadImport`, `getColumns`, `runPreview`, `getPreviewRows`, `runImport`, `listBatches`, `getBatch`, `listRows`, `downloadReport`, `listPresets`, `savePreset`, `updatePreset`, `deletePreset`).
- **CREATE** `frontend/src/types/imports.ts` — `ImportColumnInfo`, `ImportTargetField`, `ImportOptions`, `ImportPreviewResponse`, `ImportRunResponse`, `ImportPreset`, `ImportBatch`, `ImportRowResult` (the last two may re-export S06's `types/migration.ts` shapes).
- **MODIFY** `frontend/src/App.tsx` — three new routes under `ProtectedRoute`.
- **MODIFY** `frontend/src/components/layout/BottomNav.tsx` — "More" → Import link (volunteer+).
- **REUSE (no change):** `components/ui/{StateViews,ConfirmDialog}.tsx`, `services/api.ts`, the event picker from S04, S06's `types/migration.ts`.

---

## 6. Migration / data

S10 introduces **no** data migration. It creates one empty table (`import_mapping_preset`) and two nullable columns on `import_batch`. The committed import runs produce normal CRM data (`contacts`/`participants` rows) via the same write paths as S03/S04/S05/S06 — they are real records, not staged migration data, and are not rolled back by code revert.

**Staging-file lifecycle:** uploaded files live under `STORAGE_PATH/imports/<uuid>.<ext>` for 24h (`import_batch.expires_at`), then the worker sweep deletes them and nulls `staging_file`. Files are only ever served/read through authenticated, ownership-checked endpoints (never the public `/storage` route) — they are inputs, not outputs.

---

## 7. Acceptance criteria

1. `POST /imports/upload` with a UTF-8 CSV returns `batch_id`, the detected `columns[]` (header names + sample values), `total_rows`, `encoding="utf-8"` (or `utf-8-sig`/`cp1252` as detected), the sniffed `delimiter`, and an `import_batch` row with `mode='wizard_preview'`, `staging_file` set, `expires_at ≈ now+24h`.
2. A `.xlsx` upload parses via the S06 `openpyxl` reader, returns the worksheet names in `sheets[]`, and `GET /imports/{id}/columns?sheet=` re-reads the chosen sheet's header + sample.
3. A Latin-1/cp1252 file with accented Filipino names imports without mojibake; an undecodable file returns a clear 400 ("Could not decode file…").
4. Files over 15 MB → 413; files over the 50k-row cap → 400; non-CSV/XLSX content type → 400. None create core rows.
5. `GET /imports/{id}/columns` returns a `suggested_map` that auto-matches obvious headers ("First Name"→`first_name`, "Email Address"→`email`, "Contact ID"→`external_id`) and a `targets[]` catalog containing every core field **and** one entry per active `custom_field_def` for the entity.
6. `POST /imports/{id}/preview` classifies each contact row as `new` (0 key matches), `match` (1), `ambiguous` (>1), or `error` (missing required `first_name` / unparseable), and returns counts; **no `contacts` rows are written** during preview (row count unchanged before/after).
7. Conflict policy `skip` leaves matched existing records untouched and reports them `skipped`; `update` overwrites every mapped non-empty field (merging `custom_data` key-wise, never clobbering unmapped custom keys); `fill` writes only fields that are currently empty on the existing record and reports which changed.
8. A `fill`/`update` that results in **no** field change is reported as `skipped` with message "no changes" (not `updated`).
9. `POST /imports/{id}/run` executes in committed chunks of ~500 rows: a 5,000-row file commits in 10 transactions; if processing fails at row ~2,300, rows 1–2,000 remain committed and re-running with `conflict_policy='skip'` adds no duplicates (idempotent).
10. A multi-value cell (Ministry `"Worship, Ushering"`) mapped to a `multiselect` custom field is stored as `["Worship","Ushering"]` (trimmed, de-duped) in `contacts.custom_data`; option normalization collapses `DEPARO`/`Deparo` to the configured canonical value with an explanatory per-row `message`.
11. Participants import resolves `contact_id` by the chosen match key and `event_id` from an `external_id` column **or** the wizard-selected `target_event_id`; inserts `participants` rows with `source='import'` via `ON CONFLICT (event_id, contact_id) DO NOTHING`; a duplicate `(event_id, contact_id)` pair inserts once.
12. A participants row whose contact is **name-only ambiguous** is routed to the S22 `name_match_review_queue` (outcome `review`) rather than guessed; a row with an unresolvable contact/event and no queueable name is `error` and does not abort the batch.
13. Mapping two source columns to the same target, or omitting a required field, returns a clear 422 at preview/run before any write.
14. Saving a mapping preset (`POST /imports/presets`) and re-uploading the same file lets the user load it and reproduce the exact mapping + options; `(entity, name)` collision returns 409; non-owner non-admin cannot update/delete another user's preset (403).
15. `GET /imports/{id}/report.csv` streams a CSV of all per-row results (server-side cursor / generator — no full in-memory materialization).
16. All `/imports/*` write/run endpoints return **401** unauthenticated and **403** for `viewer`; volunteer and admin succeed; cross-user preset delete requires admin. Frontend `/imports*` routes are volunteer+ (hidden/redirected for viewer).
17. A summary `audit_log` row (`action='import.run'`) is written per committed run when the audit table is present (guarded otherwise).
18. The staging-file purge sweep deletes files past `expires_at` and nulls `staging_file`; the staged file is never reachable via the public `/storage` route.
19. Backend suite passes under `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test pytest tests/ -q`; `ruff check app` clean; `npm run build` + `npm run lint` + `npm run test:run` green; `alembic upgrade head` then `downgrade -1` clean on SQLite and Postgres.

---

## 8. Test plan

### Backend pytest (new `backend/tests/test_import_wizard.py`, `test_import_api.py`)
Use the existing `conftest.py` (file-SQLite, `memory://` Redis, pre-seeded JWT secret). Seed `custom_field_def` rows in fixtures (defs come from S02). Add CSV/XLSX byte fixtures inline or under `backend/tests/fixtures/imports/`.

- `test_sniff_csv_encodings` — UTF-8, UTF-8-BOM, and cp1252 (accented names) all decode; garbage → raises the decode error surfaced as 400.
- `test_sniff_delimiter_comma_semicolon_tab` — each delimiter detected; single-column file defaults to comma.
- `test_upload_caps` — >15 MB → 413; >50k rows → 400; `.txt`/wrong content-type → 400; no core rows written.
- `test_xlsx_multi_sheet` — workbook with two sheets returns both names; `?sheet=` selects the second; header/sample match.
- `test_suggest_map` — "First Name"/"Email Address"/"Contact ID" auto-map to `first_name`/`email`/`external_id`; an unknown header defaults to `ignore`; a custom-field label maps to `custom:<name>`.
- `test_classify_contact_new_match_ambiguous_error` — seed two same-name contacts and one with a unique email; assert `name` key → `ambiguous`, `email` key → `match`, unknown → `new`, missing `first_name` → `error`.
- `test_policy_skip_update_fill` — for a matched contact: `skip` writes nothing; `update` overwrites mapped fields + merges custom_data without clobbering an unmapped custom key; `fill` only sets a previously-empty field; a no-op `fill`/`update` → `skipped` "no changes".
- `test_preview_writes_no_core_rows` — preview over a 50-row file; `count(contacts)` unchanged; preview `import_row_result`s + counts exist.
- `test_run_batched_commit_resumable` — monkeypatch the loader to raise on the 3rd chunk; assert chunks 1–2 (1,000 rows) are committed, batch `status` reflects partial/failed, and a re-run with `skip` adds zero rows.
- `test_run_idempotent_skip` — run the same file twice with `conflict_policy='skip'`; second run created==0.
- `test_multiselect_and_normalization` — Ministry cell → JSON array; `DEPARO`→`Deparo`; message notes normalization.
- `test_participants_resolution_and_on_conflict` — resolve by email + `target_event_id`; duplicate `(event,contact)` inserts once; unknown contact with no name → `error` row, batch completes.
- `test_participants_name_ambiguous_to_review_queue` — name-only ambiguous participant → a `name_match_review_queue` row (S22), outcome `review`, no participant inserted. (Guard/skip if S22 table absent; assert the integration seam.)
- `test_duplicate_target_and_missing_required_422` — two headers → same target → 422; missing `first_name` mapping → 422.
- `test_preset_crud_and_ownership` — create/list/update/delete; `(entity,name)` collision → 409; volunteer cannot delete another's private preset (403); admin can.
- `test_report_csv_streams` — `GET /imports/{id}/report.csv` → `text/csv`, header + one line per result; uses the streaming generator (spy that `.all()` is not called on the full set).
- `test_import_endpoints_rbac` — every `/imports/*` route: 401 unauthenticated, 403 viewer (run/preview/preset-write), 200 volunteer/admin.
- `test_import_pagination_clamped` — `limit` over cap clamped; `offset` honored on rows/preview-rows/batches/presets.
- `test_staging_purge_sweep` — set `expires_at` in the past; run the sweep → file deleted, `staging_file` nulled.
- `test_migration_s10` — `alembic upgrade head` then `downgrade -1` clean on SQLite; `import_mapping_preset` + the two `import_batch` columns created/dropped.

### Frontend vitest (new `frontend/src/pages/__tests__/ImportWizardPage.test.tsx`, `ImportReportPage.test.tsx`)
- `wizard happy path` — mock upload→columns→preview→run; advance through 4 steps; assert each endpoint called with the built `column_map`/`options`; final toast shows counts and routes to the report.
- `load and save preset` — mock `GET /imports/presets`; selecting a preset populates the mapping; "Save as preset" POSTs `{name, entity, column_map, options, is_shared}`.
- `conflict policy toggle re-previews` — switching the segmented control to "Update all" re-calls `POST /imports/{id}/preview` and re-renders counts.
- `duplicate-target validation blocks Next` — mapping two columns to `email` shows an inline error and disables Next (no preview call).
- `report page filters by outcome` — mock batch + rows; switch outcome filter → refetch with `?outcome=error`; "Download report CSV" triggers the blob helper.
- `empty + error states` — `EmptyState` ("No data rows found") and `ErrorState` with retry render from mocked responses; a 413 upload surfaces the server `detail` via `toast.error` (mock `sonner`).
- `viewer cannot reach wizard` — `ProtectedRoute`/role guard hides the nav entry and redirects a `viewer` away from `/imports/new`.

Run gates: `npm run test:run`, `npm run build`, `npm run lint` green.

---

## 9. Rollout / rollback / risks

**Rollout**
- Ship migration `h5c6d7e8f9a0` (additive: one empty table + two nullable columns — zero-downtime). CI must still pass `alembic upgrade head` cleanly + idempotently on Postgres.
- Deploy backend (new router/services/worker sweep) + frontend (wizard) together. Ensure `STORAGE_PATH/imports/` is writable by the API process (mkdir on first upload); the worker already writes under `STORAGE_PATH`.
- No `requirements.txt` change if S06 already added `openpyxl`; verify before building the image.
- Feature is volunteer-gated; no public surface.

**Rollback**
- Frontend: hide the Import nav entry / revert the routes — read+write paths gone; no data risk.
- Backend: revert the router/services; `alembic downgrade -1` drops `import_mapping_preset` + the two columns. Already-imported `contacts`/`participants` are real CRM data and intentionally **not** reverted. Orphaned staging files under `imports/` are purged by TTL or removable manually.

**Risks & mitigations**
1. **S06 ETL core not merged when S10 starts** → S10 hard-depends on `services/migration/{reader,normalize,mapper,loader}`. Mitigation: sequence S06 first (preferred); if not, build against S06's documented contracts and refactor the imports on integration; the migration includes a guard to create `import_batch`/`import_row_result` if absent. (§10.)
2. **S22 name-matching service not ready** → participants name-only resolution can't queue ambiguous rows. Mitigation: until S22, degrade name-only ambiguity to `error` with a clear message ("name matching unavailable — provide Contact ID or email"); wire the review-queue path behind a capability check. (§10.)
3. **Large/slow uploads block the request** → 15 MB / 50k-row cap keeps parse + chunked write within request limits; anything larger is a migration → S06 CLI. (If the owner needs bigger browser imports later, add an async-job mode like S05's export jobs.)
4. **Destructive `update` policy** (overwriting good data with a stale sheet) → preview-before-write is mandatory, default policy is `skip`, `update`/`fill` merge custom_data key-wise, per-row overrides + the per-run audit row + per-row report make mistakes visible and recoverable via re-import.
5. **`rowcount` semantics after `ON CONFLICT`** vary by driver → reuse S06/S05's tested helper + the idempotency test; assert on both asyncpg and aiosqlite.
6. **Encoding edge cases** (Excel "CSV UTF-8" vs legacy cp1252 from Windows) → three-step decode ladder + explicit override + a clear 400 on failure; covered by `test_sniff_csv_encodings`.

---

## 10. Open questions & pending owner artifacts

**Open questions:**
- **Q1 (match-key default):** newcomer sheets rarely carry the original Contact ID. Spec defaults `match_key='external_id'` but the wizard should *recommend* `email` (then `name`) when no `external_id` column is detected. Confirm the desired default ordering.
- **Q2 (events import):** spec excludes spreadsheet import of events (created via S04 CRUD). Confirm the owner never bulk-imports events from a sheet; if they do, add a third entity + an `event_series` resolution path.
- **Q3 (preset sharing):** presets default `is_shared=true` (visible to all volunteers). Confirm whether some presets should be private-by-default or admin-curated only.
- **Q4 (fill semantics for multi-value):** for `fill` policy, is a non-empty `multiselect` "empty" if it's `[]`? Spec treats `[]`/`None` as empty (fillable) and a non-empty array as present (skipped). Confirm.
- **Q5 (staging TTL):** 24h staging-file retention — confirm acceptable, or shorten for privacy (files may contain PII).

**Cross-sprint dependencies & shared-model touchpoints (for the master doc to reconcile):**
- **S06 ETL core (hard dep):** S10's `runner.py` **must import** S06's `services/migration/{reader,normalize,mapper,loader}` rather than re-implement parsing/normalization/coercion/upsert. The master doc should pin these as a stable, importable internal API (function signatures: `reader.iter_rows(path, sheet)`, `normalize.{normalize_option,split_multi,coerce,name_key}`, `mapper.map_row(row, column_map, defs) -> {core, custom_data}`, `loader.upsert_contacts(db, rows)` / `loader.insert_participants(db, rows)`). Recommend hoisting the shared core to `app/services/etl/` so both S06 (migration) and S10 (wizard) import one module; S10 then keeps only `staging/suggest/disposition/runner(thin)/presets`.
- **Shared tables:** S10 **reuses** S06's `import_batch` + `import_row_result` (adds `mode` values `wizard_preview`/`wizard_run`; adds `staging_file`/`expires_at` columns). The master doc must confirm S06 owns those tables and that adding nullable columns + new `mode` string values is acceptable to S06's list/report endpoints (it is — they're string-filtered). The `/imports/*` and `/migration/*` routers both read these tables; keep their list endpoints filter-compatible.
- **S22 name-matching (soft dep):** participants name-resolution defers ambiguous name-only rows to the **S22** `name_match_review_queue` (normalize → fuzzy → alias dict → Claude → human review). S10 consumes this once S22 ships; until then it degrades to `error` (risk #2). Master doc should sequence S22 before or alongside S10's participants path, or accept the degraded mode for v1.
- **S02 custom fields:** S10 reads `custom_field_def` (name/label/data_type/is_multi/options) for the target catalog + coercion, and validates via S02's `custom_fields.py` chokepoint. The `contact_reference` stored shape (single `int` / `list[int]`) must match S02/S06 exactly — the master doc already pins single=`int`, multi=`list[int]`; S10 follows it.
- **S05 streaming + bulk helpers:** the `report.csv` endpoint reuses S05's streaming-CSV generator; the participants writer reuses S05's `INSERT … ON CONFLICT … DO NOTHING` chunked helper (via S06's loader). Master doc should ensure these are importable utilities (`app/services/export.py::stream_csv`, the bulk-upsert helper), not duplicated.
- **S15 RBAC:** S10 routes use `require_volunteer` (writes) and `require_admin` (cross-user preset delete); `viewer` is denied writes. No change needed when S15 lands; the `User.role` comment in `models.py:41-43` must include `viewer` (S15's job).
- **Inconsistency to reconcile:** as of the current tree, `models.py` still defines `CiviCRMMember`/`CiviCRMEvent` (CiviCRM-id PKs) and `Attendance` with push columns (`models.py:66-84, 187-214`). S10 assumes S01 has renamed these to `Contact`/`Event`/`Participant`, added `external_id`, and S02–S06 exist. If those upstream sprints slip, S10 cannot start — the dependency chain (S01→S02→S03→S04→S05→S06→S10) is hard.

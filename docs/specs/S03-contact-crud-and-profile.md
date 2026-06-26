# S03 — Native Contact CRUD & Detail Profile
**Phase:** B — Core CRM · **Depends on:** S01 (schema inversion: `contacts`/`events`/`participants` tables, app-minted ids, `custom_data` JSONB, `external_id`), S02 (custom-field engine: `custom_field_group`/`custom_field_def`, `validate_and_coerce`, `GET /custom-fields/schema`, `<CustomFieldRenderer>`/`<CustomFieldsSection>`) · **Effort:** L · **Status:** Not started

## 1. Goal & rationale

Today there is **no way to create, edit, or delete a contact** in Seraphim. The only contact surfaces are read-only: `GET /members` (search; `members.py:18`), `GET /members/attendees` (card grid with face thumbnails; `members.py:63`), and the frontend `AttendeesPage.tsx` — a read-only card grid keyed on `contact_id` with a free-text search and no row click, no detail route, no edit form (`frontend.md` §D.1). Contacts were *born* in CiviCRM and synced in; S01 deleted that sync. After S01/S02 the `contacts` table is the system of record with app-minted ids and a dynamic `custom_data` JSONB column, but nothing writes to it through the UI.

S03 closes that gap. It delivers the **first native write path for the core CRM entity**: create / edit / list (paginated, searchable, offset-based) / soft-delete contacts with both **core scalar fields** (name, suffix, gender, birth_date, phone, email, street_address, contact_type/subtype) and **dynamic custom fields** (rendered from S02's schema, validated through S02's `validate_and_coerce`). It replaces the read-only `AttendeesPage` with a real **`ContactsPage`** (paginated table) plus a **`ContactDetailPage`** profile — the central hub every later sprint hangs features off of. The detail page ships explicit, well-labelled **slots** that later sprints fill: a **face panel** (S07 enrollment + recognition history), an **attendance history** list (participants joined to events, all 4 sources), **derived-status badges** (tier / active / regular / connected — populated by the S23 nightly recompute job, read-only here), **biometric consent** (S08), and **activities** (S12).

Because this is the first data-dense CRM screen, S03 also establishes the **reusable frontend primitives** the whole CRM needs and which `frontend.md` §D.3/§D.11 flagged as missing: `FormField` (label + control + error — the gap noted at `frontend.md` §B "no shared `<Input>`/`<Select>`/`<FormField>`"), `DataTable` (server-paginated, mobile-card / desktop-row responsive), and `Pagination` (offset Prev/Next + page size, generalizing the only existing paginator `LogsPage.tsx:95-114`). These are consumed here and reused by S04 (events), S05 (participants), S09 (search), S10 (import), S11 (merge), S12 (activities).

Net outcome: an admin or volunteer can add a brand-new contact, fill core + custom fields, see the full church directory in a fast paginated table, open any contact's profile, edit it, and soft-delete it — all native, no CiviCRM, all wired to S02's field engine and ready for the face/attendance/consent/activity slots.

## 2. Scope

### In scope
- **Backend contact CRUD** on the (post-S01) `contacts` table, registered under the existing `/members` router prefix to avoid a breaking rename churn (the prefix is internal; UI calls go through `api`). New endpoints:
  - `POST /members` — create contact (core + `custom_data` validated via S02).
  - `GET /members/{id}` — fetch one contact (full detail incl. resolved custom fields).
  - `PATCH /members/{id}` — partial update (core + custom_data).
  - `DELETE /members/{id}` — **soft delete** (`is_deleted=true`).
  - `POST /members/{id}/restore` — undo soft delete (admin).
  - `GET /members/{id}/attendance` — paginated participant history (joined to events) for the profile's attendance slot.
- **Paginated, searchable list** — upgrade `GET /members` to offset pagination returning `{items, total, page, page_size}` (today it returns a bare `list` with `limit` only and no offset — `members.py:18-45`). Search across first/last/nickname/email; exclude soft-deleted by default; `include_deleted` admin flag.
- **Core scalar fields** create/edit: `first_name`, `last_name`, `nickname`, `suffix`, `gender`, `birth_date`, `phone`, `email`, `street_address`, `contact_type`, `contact_subtype`. (`external_id` is read-only/trace only; `custom_data` via the engine.)
- **Dynamic custom fields** in the create/edit form via S02's `<CustomFieldsSection>` driven by `GET /custom-fields/schema?entity=contact`; written through S02's `validate_and_coerce` (the single chokepoint).
- **Contact detail profile page** (`/contacts/:id`) with: header (name/avatar/type/subtype/derived badges), core-info card, custom-fields card (grouped, read view + resolved `contact_reference` display names), and **labelled slots** (face panel, attendance history, consent, activities) — slots render their own data where it already exists (attendance history is real in S03) or a graceful "coming soon / not enrolled" placeholder with a stable DOM anchor the later sprint replaces.
- **Derived-status badges** read-only from the contact snapshot columns (`tier`, `is_active`, `is_regular`, `is_connected`, `weeks_absent`, `last_attended_at`, `attendance_count`) — **display only**; the columns and the recompute job belong to S23 (see §3 / §10).
- **Reusable frontend primitives**: `FormField`, `DataTable`, `Pagination` (+ `StatusBadge` for derived/contact-type badges) in `components/ui/`.
- **Replace** `AttendeesPage.tsx` → `ContactsPage.tsx`; add `ContactDetailPage.tsx` + `ContactFormPage.tsx`; reroute `/attendees` and add `/contacts` + `/contacts/:id` (+ new/edit); update `BottomNav` tab.
- **Audit logging** of contact create/update/delete/restore via S02's `app/services/audit.py` `record(...)` helper (actions `contact.create` / `contact.update` / `contact.delete` / `contact.restore`).
- Pydantic schemas: `ContactCreate`, `ContactUpdate`, `ContactDetailResponse`, `ContactListItem`, `PaginatedContactResponse`, `ContactAttendanceItem`, `PaginatedAttendanceResponse`, plus the small DTOs `DerivedBadges`, `FaceSummary`, `ContactReferenceChip`.

### Out of scope (explicit — belongs to a named sprint)
- **Face enrollment / bulk photo ingestion / recognition history data** → **S07**. S03 ships the *face panel slot* (component shell + "not enrolled" placeholder + read of an existing `ComprefaceSubject`/thumbnail if present) but **no** enroll/re-enroll/RTBF actions.
- **Biometric consent capture** → **S08**. S03 ships the *consent slot* placeholder only.
- **Activities** (create/assign/list) → **S12**. S03 ships the *activities slot* placeholder only.
- **Computing** derived attributes (tier/active/regular/connected) and the **nightly recompute job** → **S23**. S03 only *reads/displays* the snapshot columns; if they are not yet added, S03 adds them nullable (see §3 / §10 Q1) but never writes them.
- **Advanced multi-criteria search, saved searches, smart groups, custom-field filtering, GIN indexes** → **S09**. S03 search is name/email/nickname free-text only.
- **Bulk add/update of participants, attendance export at scale** → **S05**. (S03's attendance slot is a read-only paginated history.)
- **CSV/XLSX import wizard, dedupe-on-import, real CiviCRM value migration** → **S06/S10**.
- **Find & merge duplicates** (dedupe candidate finder, FK reassignment) → **S11**. (S03 does not hard-block duplicates; it emits a non-blocking warning only.)
- **Custom-field metadata CRUD UI** → already shipped in **S02** (S03 only *consumes* the schema + renderer).
- **`viewer` role** (read-only reports/dashboard) → **S15**. S03 uses `require_volunteer` (admin+volunteer); when S15 lands, viewers must NOT reach contact records (locked Round 4) — noted in §10/§5.3.
- **Profiles / public newcomer form** (templated/quick-add, public intake) → **S13**.
- **Desktop master-detail sidebar shell / app-wide responsive pass** → **S20**. S03 is mobile-first with a sensible desktop table layout, no app-wide layout refactor.
- **Households/organizations relationship modelling** (employer_id, household members) → not planned; `contact_type` is stored/filterable but only `individual` gets the full form (household/organization render name-as-org fallback — see §4 edge cases).

## 3. Data model changes

S03 introduces **no new tables**. The `contacts` table already exists with all core columns after S01, and `custom_data` + `custom_field_*` after S01/S02. Two conditional concerns:

### 3.1 Derived snapshot columns (conditional add; never written by S03)
The canonical model lists nullable snapshot columns on `contacts`: `last_attended_at`, `attendance_count`, `weeks_absent`, `tier`, `is_active`, `is_regular`, `is_connected`. **Ownership must be assigned to exactly one sprint** (S01 added core + `custom_data`; S23 owns the recompute job). S03 *reads* these for the badge slot. To let the detail page compile/render badges without blocking on S23:

- If **S01/S23 already added** these columns → S03 adds nothing (no migration).
- If **not yet added** → S03 ships a **thin additive migration** that adds them **nullable, no backfill**, so the detail page reads them (all `NULL` until S23's job runs → renders an "Unrated" badge). Inspector-guarded so the op is idempotent and safe regardless of who adds them first.

| column | type | null | default | notes |
|---|---|---|---|---|
| `last_attended_at` | DateTime (naive UTC) | yes | NULL | newest `attended` `participants.created_at`; set by S23 |
| `attendance_count` | Integer | yes | NULL | lifetime `attended` count; set by S23 |
| `weeks_absent` | Integer | yes | NULL | `floor((today - last_attended)/7)`; set by S23 |
| `tier` | String(16) | yes | NULL | `tier0`/`tier1`/`tier2`/`tier3`/`inactive`; set by S23 |
| `is_active` | Boolean | yes | NULL | tier0–3; set by S23 |
| `is_regular` | Boolean | yes | NULL | lifetime attendance > 9; set by S23 |
| `is_connected` | Boolean | yes | NULL | has Community/CG; set by S23 |

> **Decision (recommended, flagged §10 Q1):** the master should assign these columns to **S01** (alongside the other core columns it already added) so neither S03 nor S23 races. If the master keeps them on S23, S03's guarded migration is the safety net. Either way S03 **never writes** them.

### 3.2 Indexes for list/search performance
S01 already adds `ix_contacts_last_first` on `(last_name, first_name)` and `ix_contacts_is_deleted` on `(is_deleted)` (S01 §3). S03 relies on these for the default ordered, soft-delete-filtered list. **No new index for the name/email ilike search at 1,440 rows** (sequential scan is sub-millisecond; trigram/GIN is an S09 concern). `external_id` lookups are covered by S01's `UNIQUE(external_id)`.

### 3.3 Alembic plan
- **If snapshot columns are needed here:** one new migration `h5c6d7e8f9a0_add_contact_snapshot_columns.py`, `down_revision` = the S02 head (resolve at implementation time; never edit an applied migration — AGENTS.md:100). Forward: inspector-guarded `op.add_column` for each of the seven columns (nullable, **no** `server_default` → no table rewrite; pre-prod, 1,440 rows). Use the inspector pattern from S02 §3.2:
  ```python
  from sqlalchemy import inspect
  cols = [c["name"] for c in inspect(op.get_bind()).get_columns("contacts")]
  if "tier" not in cols: op.add_column("contacts", sa.Column("tier", sa.String(16), nullable=True))
  # ... repeat for the other six
  ```
  Downgrade: drop the seven columns **only if this migration added them** (guard) — never drop columns another sprint may own. No `.with_variant`/JSONB here (all scalars).
- **If snapshot columns owned elsewhere:** S03 ships **no migration** (pure API + FE).
- CI runs `alembic upgrade head` clean + idempotent on Postgres (AGENTS.md:124) and `create_all` on SQLite — the guarded add keeps both green.

## 4. Backend

### 4.1 Endpoints

All on `routers/members.py` (existing prefix `/members`), included in `main.py` with `dependencies=[Depends(check_setup_complete)]` like every domain router. No `/api` prefix (nginx strips it). Roles via existing `require_admin`/`require_volunteer` (`app/dependencies.py`). Writes are `require_volunteer` (admin+volunteer per locked roles); destructive restore / include-deleted are `require_admin`.

| METHOD | path | role | request | response | notes |
|---|---|---|---|---|---|
| GET | `/members` | volunteer | query: `search`, `page=1`, `page_size=25` (≤100), `contact_type?`, `subtype?`, `tier?`, `is_regular?`, `include_deleted=false` (admin-only flag) | `PaginatedContactResponse` | **CHANGED** from `members.py:18` bare-list+limit → offset pagination `{items,total,page,page_size}`. Search ilike across first/last/nickname/email. Default order `last_name, first_name` (uses `ix_contacts_last_first`). Excludes `is_deleted=true` unless `include_deleted` AND admin. `tier`/`is_regular` filters read snapshot columns (NULL rows excluded when filtered). |
| GET | `/members/{id}` | volunteer | path id | `ContactDetailResponse` | Full detail: core + `external_id` + raw `custom_data` + `custom_fields_resolved` (contact_reference ids → `{id, display_name}` chips, grouped per S02 schema) + `derived` snapshot badges + `face` summary (`{enrolled, subject_id?, sample_count, thumbnail_path?}` from `ComprefaceSubject`/thumbnail; read-only — full panel is S07). 404 if not found; 404 (not 403) for a soft-deleted contact when caller isn't admin (don't leak existence beyond list rules). |
| POST | `/members` | volunteer | `ContactCreate` | `ContactDetailResponse` (201) | Mint id (identity PK). Validate core (Pydantic) + `custom_data` via S02 `validate_and_coerce(db,'contact',raw)`. Non-blocking soft-duplicate **warning** when `email` matches an existing non-deleted contact (returned in `warnings`; create still succeeds — hard merge is S11). Writes `audit_log` `contact.create`. |
| PATCH | `/members/{id}` | volunteer | `ContactUpdate` (all optional) | `ContactDetailResponse` | Partial update; only provided keys change (`exclude_unset`). `custom_data`, if present, is **merged field-by-field then re-validated** through `validate_and_coerce` (partial custom edits don't wipe untouched keys — see business rules). `updated_at` bumped. `external_id` immutable. 404 rules as above (admin may edit soft-deleted). Audited `contact.update` with before/after diff. |
| DELETE | `/members/{id}` | volunteer | path id | `204` | **Soft delete:** `is_deleted=true`, bump `updated_at`. **Idempotent** (deleting an already-deleted contact → 204 no-op). Does **not** cascade-delete participants/detections/subjects (history preserved; contact just leaves default lists). Audited `contact.delete`. (Hard delete + biometric purge is S08 RTBF.) |
| POST | `/members/{id}/restore` | admin | path id | `ContactDetailResponse` | `is_deleted=false`. 404 if not found; 409 if not currently deleted. Audited `contact.restore`. |
| GET | `/members/{id}/attendance` | volunteer | query: `page=1`, `page_size=25` (≤100), `source?`, `event_type?` | `PaginatedAttendanceResponse` | Profile attendance slot. `select(Participant, Event).join(Event, Participant.event_id==Event.id).where(Participant.contact_id==:id)` order by `Event.start_at desc` (fallback `Participant.created_at desc`). Item: `{participant_id, event_id, event_title, event_type, session_time?, occurrence_date?/start_at, status, role, source, detection_id?}`. 404 if contact missing. |

`GET /members/attendees` (`members.py:63`) — **retained** post-S01 (face-thumbnail card data); `ContactsPage` uses the new paginated `GET /members` instead. Master may later deprecate `/attendees` once S07's face panel subsumes it (flag, don't delete here — §10 Q7).

### 4.2 Pydantic schemas (`app/schemas.py`, new "Contacts (CRUD)" section)

> S01 renamed `MemberResponse.contact_id`→`id` (or `ContactResponse`); S03 builds the full CRUD DTOs. Keep `model_config = ConfigDict(from_attributes=True)` on response models.

- `ContactCore` (shared base): `first_name: str` (1–255, stripped, required), `last_name: str` (required), `nickname: Optional[str]`, `suffix: Optional[str]`, `gender: Optional[str]` (permissive now; see §10 Q3), `birth_date: Optional[date]` (ISO `YYYY-MM-DD`; validator rejects future dates), `phone: Optional[str]` (≤64; strip spaces, no hard format check), `email: Optional[EmailStr]`, `street_address: Optional[str]`, `contact_type: Literal['individual','household','organization'] = 'individual'`, `contact_subtype: Optional[str]`. Model-level validator: `last_name` required only when `contact_type == 'individual'`.
- `ContactCreate(ContactCore)`: + `custom_data: dict[str, Any] = {}` (validated server-side via S02, NOT in Pydantic).
- `ContactUpdate`: every `ContactCore` field `Optional` + `custom_data: Optional[dict[str, Any]] = None`; handler uses `model_dump(exclude_unset=True)` to distinguish "absent" (don't touch) from explicit null.
- `ContactReferenceChip`: `{id: int, display_name: str}`.
- `DerivedBadges`: `{tier: Optional[str], is_active: Optional[bool], is_regular: Optional[bool], is_connected: Optional[bool], weeks_absent: Optional[int], last_attended_at: Optional[datetime], attendance_count: Optional[int]}` (all nullable until S23).
- `FaceSummary`: `{enrolled: bool, subject_id: Optional[str], sample_count: int = 0, thumbnail_path: Optional[str]}` (read-only; full panel S07).
- `ContactListItem` (from_attributes): `id, external_id, first_name, last_name, nickname, display_name, email, phone, contact_type, contact_subtype, is_deleted, tier, is_regular, is_connected, face_thumbnail_path` (flattened for the list-row badges).
- `PaginatedContactResponse`: `{items: list[ContactListItem], total: int, page: int, page_size: int}` (mirrors `PaginatedLogResponse`/`PaginatedTaskResponse` in `schemas.py:145,266`).
- `ContactDetailResponse(ContactCore)`: + `id, external_id, is_deleted, created_at, updated_at, display_name, custom_data: dict[str, Any]` (raw), `custom_fields_resolved: dict[str, Any]` (contact_reference ids → chips for display), `derived: DerivedBadges`, `face: FaceSummary`, `warnings: list[str] = []`.
- `ContactAttendanceItem`: `{participant_id, event_id, event_title, event_type, session_time, occurrence_date, start_at, status, role, source, detection_id}`.
- `PaginatedAttendanceResponse`: `{items: list[ContactAttendanceItem], total, page, page_size}`.

### 4.3 Services / business rules / algorithms / edge cases

**New module `app/services/contact_service.py`** (keeps the router thin; async; type-hinted on all public funcs):

- `async def list_contacts(db, *, search, page, page_size, contact_type, subtype, tier, is_regular, include_deleted) -> tuple[list[Contact], int]`
  - Base `select(Contact)`; `where(Contact.is_deleted.is_(False))` unless `include_deleted`.
  - `search.strip()` → `or_(first_name ilike, last_name ilike, nickname ilike, email ilike)` with `%q%` (mirrors `members.py:80-89`).
  - Optional equality filters: `contact_type`, `contact_subtype`, `tier`, `is_regular`.
  - `total` via `select(func.count()).select_from(<filtered subquery>)` (one count query) for the paginator.
  - `order_by(Contact.last_name, Contact.first_name)`, `.offset((page-1)*page_size).limit(page_size)`.
  - **Edge:** clamp `page_size` 1–100, `page` ≥1; `page` past the end → empty `items`, correct `total`.
- `async def get_contact_detail(db, contact_id, *, allow_deleted) -> ContactDetailResponse`
  - Load; 404 if missing or (`is_deleted` and not `allow_deleted`).
  - **Custom-field resolution:** call S02 `get_active_schema(db,'contact')`; collect every `contact_reference` field present in `custom_data`; **batch** resolve referenced ids in one query `select(Contact.id, Contact.first_name, Contact.last_name, Contact.nickname, Contact.is_deleted).where(Contact.id.in_(all_ref_ids))` (no N+1). Build `custom_fields_resolved` (chips; a soft-deleted referent gets a "(deleted)" display suffix — don't 422 on read).
  - **Face summary:** one `select(ComprefaceSubject).where(contact_id==id)`; if present, `enrolled = enrollment_status=='active'`, `sample_count`, `thumbnail_path = members._find_first_thumbnail(subject.compreface_subject_id)`.
  - **Derived badges:** copy snapshot columns straight onto `DerivedBadges` (NULL passthrough).
- `async def create_contact(db, payload, actor_id) -> Contact`
  - `validated = await validate_and_coerce(db,'contact', payload.custom_data)` (S02 chokepoint; bubbles its structured 422).
  - Build `Contact(**core, custom_data=validated, is_deleted=False)`; `db.add`; `flush()` to mint id; `audit.record(db, actor_id, 'contact.create', 'contact', contact.id, before=None, after=<serialized>)`; `commit`.
  - **Soft-dup warning:** if `payload.email`, pre-check `select(Contact.id).where(func.lower(Contact.email)==email.lower(), Contact.is_deleted.is_(False))` → append `f"Possible duplicate of contact {id}"` to `warnings` (non-blocking).
- `async def update_contact(db, contact_id, payload, actor_id) -> Contact`
  - Load `with_for_update()` (avoid lost-update on concurrent volunteer edits — `task_service` `with_for_update()` pattern, AGENTS.md:105). 404 rules as above.
  - Snapshot `before` for audit.
  - Assign provided core fields (`exclude_unset`). If `custom_data` provided: **merge** `{**contact.custom_data, **payload.custom_data}` then `validate_and_coerce(db,'contact', merged)` (partial edits keep untouched keys + re-validate the whole object; empties cleared per S02 §4.2 step 4); assign result.
  - `audit.record(...'contact.update'..., before, after)`; `commit`.
- `async def soft_delete_contact(db, contact_id, actor_id)` / `restore_contact(...)` — set `is_deleted`; idempotent delete; 409 restore-when-not-deleted; audit.
- `async def list_contact_attendance(db, contact_id, *, page, page_size, source, event_type) -> tuple[list[Row], int]` — join `Participant`⋈`Event` on `contact_id`; optional `source`/`event_type` filters; order `Event.start_at desc`; count + offset/limit; map to `ContactAttendanceItem`. 404 if contact missing.
- `_display_name(contact) -> str` — `individual` with nickname → `f'{first} "{nick}" {last}'`; else `f"{first} {last}".strip()`; org/household → `first` (org name lives in `first_name`). Centralized so list/detail/chips and `MemberSearchModal` agree (`MemberSearchModal.tsx:129` expects `display_name`).

**Cross-cutting rules / edge cases**
- **Required custom fields, create vs partial update:** `validate_and_coerce` enforces `is_required` (S02 §4.2). On **create** the full object is validated → required enforced. On **partial update** the *merged* object is validated → an already-present required field stays valid; explicitly clearing it → 422 `required`. (Documented: a required custom field cannot be blanked via PATCH.)
- **Household/Organization `contact_type`:** the S03 form is individual-centric. For `household`/`organization`, `first_name` carries the org/household name and `last_name` may be blank (validator relaxes `last_name` for non-individual). Profile header shows `first_name` when `last_name` empty.
- **Soft-deleted referenced contacts:** a `contact_reference` value pointing at a soft-deleted contact resolves to a chip with a "(deleted)" suffix on read (S02 only blocks soft-deleted on *write*).
- **Performance:** list = 2 queries (count + page); detail = ≤4 queries (contact, S02 schema groups+defs, batched contact_reference resolve, face subject). Trivial at 1,440 contacts; no N+1 (batched `IN`).
- **Naive UTC** timestamps via `utc_now()`; `birth_date` is a `Date`, not datetime.

### 4.4 File-by-file change list (backend)
- **CREATE** `backend/app/services/contact_service.py` — the functions above.
- **MODIFY** `backend/app/routers/members.py` — replace `search_members` bare-list (`members.py:18-45`) with the paginated `list_contacts` handler; add `POST /members`, `GET/PATCH/DELETE /members/{id}`, `POST /members/{id}/restore`, `GET /members/{id}/attendance`. Keep `_find_first_thumbnail` (`members.py:48`) and `GET /members/attendees`. Import `Contact`/`Participant`/`Event`/`ComprefaceSubject` (post-S01 names) + `contact_service` + S02 `validate_and_coerce`/`get_active_schema` + `audit.record`.
- **MODIFY** `backend/app/schemas.py` — add the "Contacts (CRUD)" section (§4.2). Ensure `MemberResponse`/`ContactListItem` carries `display_name` so the picker keeps working.
- **MODIFY** `backend/app/main.py` — members router already included; confirm `check_setup_complete` dependency present.
- **CREATE (conditional)** `backend/alembic/versions/h5c6d7e8f9a0_add_contact_snapshot_columns.py` — only if §3.1 columns not owned by S01/S23.
- **MODIFY (conditional)** `backend/app/models.py` — add the seven snapshot columns to `Contact` **only if** S03 owns them (else they exist from S01; coordinate §10 Q1). All nullable; S03 never writes them.
- **MODIFY** `backend/tests/conftest.py` — extend the S01/S02 `sample_contact` fixture; add `sample_deleted_contact`, `sample_participant_history` (`Participant`+`Event` for the attendance slot), `sample_contact_with_custom_data` (incl. a `contact_reference` value).

## 5. Frontend

### 5.1 Pages / routes / components

**Replace** `pages/AttendeesPage.tsx` → **`pages/ContactsPage.tsx`** (paginated directory). **Add** `pages/ContactDetailPage.tsx` (profile) and `pages/ContactFormPage.tsx` (create/edit as a full-page route for mobile ergonomics: `/contacts/new`, `/contacts/:id/edit`).

**Routes** (`App.tsx`):
- `/contacts` → `<ProtectedRoute><ContactsPage/></ProtectedRoute>` (replaces `/attendees`).
- `/contacts/new` → `<ProtectedRoute><ContactFormPage mode="create"/></ProtectedRoute>`.
- `/contacts/:id` → `<ProtectedRoute><ContactDetailPage/></ProtectedRoute>`.
- `/contacts/:id/edit` → `<ProtectedRoute><ContactFormPage mode="edit"/></ProtectedRoute>`.
- Keep `/attendees` as `<Navigate to="/contacts" replace/>` for one sprint (deep-link/back-compat), remove in S20.

**New reusable primitives** (the §1 gap; `components/ui/`):
- `components/ui/FormField.tsx` — `{label, htmlFor, required?, error?, hint?, children}`: label (with required asterisk), control (children), inline hint/`help_text`, token-styled error (`text-destructive`/`text-red-500`). Exports centralized `inputClass`/`labelClass` constants (currently duplicated per page — `frontend.md` §B).
- `components/ui/DataTable.tsx` — generic `<DataTable<T>>`: `{columns:{key,header,render?,className?}[], rows, getRowKey, onRowClick?, isLoading, emptyState}`. **Responsive:** desktop (`md:`) real `<table>` with sticky header; mobile (<768px) each row a tappable `bg-card rounded-2xl` card. Uses `LoadingState`/`EmptyState` (`StateViews.tsx`).
- `components/ui/Pagination.tsx` — `{page, pageSize, total, onPageChange, onPageSizeChange?}`: Prev/Next (disabled at bounds), "Page X of N" + "showing a–b of total", optional page-size select (10/25/50/100). Generalizes the only existing paginator (`LogsPage.tsx:95-114`). 44px touch targets.
- `components/ui/StatusBadge.tsx` — `{label, tone}` pill mapping tone→token classes (`bg-primary/10 text-primary`, `bg-muted text-foreground/70`, amber/orange/red for tiers). Tier→tone: tier0 active-green, tier1 amber, tier2/3 orange/red, inactive muted, NULL→"Unrated" muted. (Addresses `frontend.md` §B/§D.11 "status colors hardcoded".)

**`ContactsPage.tsx`** — header (title "Contacts" + "New Contact" button), debounced (300ms) search (reuse `MemberSearchModal`'s debounce idiom), filter chips (contact_type/tier/regular → query params), `<DataTable>` of `ContactListItem` (columns: name+nickname, type/subtype badge, tier badge, regular/connected pills, email/phone; row click → `/contacts/:id`), `<Pagination>` footer. Admin sees an "include deleted" toggle. Empty/loading/error via `StateViews`.

**`ContactDetailPage.tsx`** — the hub. Each section a `bg-card rounded-2xl` block with a stable `data-slot` anchor:
- **Header**: avatar (face thumbnail if `face.thumbnail_path`, else initials), `display_name`, type/subtype badge, **derived badges** (tier / Active / Regular / Connected via `StatusBadge`), Edit button (volunteer+), overflow menu (Delete → `ConfirmDialog` destructive; Restore if deleted, admin only).
- **Core info card**: phone, email, birth_date, gender, suffix, street_address, `external_id` (muted "Legacy ID").
- **Custom fields card(s)**: grouped per S02 schema (read view); `contact_reference` values render as chips linking to `/contacts/:refId`.
- **Slot: Face panel** (`data-slot="face-panel"`): if `face.enrolled` → thumbnail + sample count (read-only); else `EmptyState` "No face enrolled" + "Coming in S07" affordance. **S07 replaces this component body.**
- **Slot: Attendance history** (`data-slot="attendance-history"`): `<DataTable>` fed by `GET /members/{id}/attendance` (event title, type, date, status, source badge) + `<Pagination>`. **Real data in S03.**
- **Slot: Consent** (`data-slot="consent"`): placeholder "Biometric consent — S08".
- **Slot: Activities** (`data-slot="activities"`): placeholder "Activities — S12".

**`ContactFormPage.tsx`** (create + edit) — sections: Core `FormField`s (name, nickname, suffix, contact_type select, subtype, gender, birth_date, phone, email, street_address) + **`<CustomFieldsSection>`** (S02) per active group via `useCustomFieldSchema('contact')` (S02 hook), bound to a local `customData` state object. Save → `POST`/`PATCH /members[/{id}]`; on 422 map S02 field errors onto the offending `CustomFieldRenderer` (S02 supports `error?`) and core errors onto `FormField`. Success → `toast.success`, navigate to `/contacts/:id`. Cancel → back. Edit mode prefetches via `GET /members/{id}`.

**Modified shared files**
- `components/layout/BottomNav.tsx` — rename the Attendees/Members tab to **"Contacts"** → `/contacts` (keep `Users` icon).
- `components/tasks/MemberSearchModal.tsx` — read the new paginated shape: `res.data.items ?? res.data` (the picker only needs the array). Keeps relying on `display_name`.
- `types/index.ts` — add `Contact`, `ContactListItem`, `ContactDetail`, `DerivedBadges`, `FaceSummary`, `ContactAttendanceItem`, generic `Paginated<T>`; reconcile `Member`/`Attendee` to post-S01 `id`.
- `services/contacts.ts` (CREATE) — typed API fns: `listContacts`, `getContact`, `createContact`, `updateContact`, `deleteContact`, `restoreContact`, `getContactAttendance`.
- `hooks/useContacts.ts` (CREATE, optional) — `useQuery`/`useMutation` wrappers.

### 5.2 State (TanStack Query keys, Zustand, mutations)
- Keys: `['contacts', {search, page, pageSize, filters, includeDeleted}]` (list), `['contact', id]` (detail), `['contact', id, 'attendance', {page, source, eventType}]`, `['custom-fields','schema','contact']` (shared with S02).
- **Adopt `useMutation`** for create/update/delete/restore (project standard going forward per S02 §5.2; `frontend.md` §B notes none exists yet). On success: `invalidateQueries(['contacts'])` + `setQueryData(['contact', id], data)` / `invalidateQueries(['contact', id])`.
- Zustand: no new store; `authStore.isAdmin` gates include-deleted toggle, delete/restore, and (post-S15) viewer redirect. Writes allowed for admin+volunteer (no inline gate beyond route protection).

### 5.3 Role gating
- All contact routes are `ProtectedRoute` (any authenticated user) in S03. Create/edit/delete buttons render for all (admin+volunteer write per locked roles); `include_deleted`/restore/hard-destructive are admin-only (button hidden via `isAdmin`, enforced by backend `require_admin`).
- **S15 seam (do not build here):** when `viewer` lands, viewers must be redirected away from `/contacts*` (reports/dashboard only — locked Round 4). Leave a `// S15: gate contacts from viewer` comment at the route and in `ContactsPage`.

### 5.4 UX states, tokens, mobile-first + desktop
- **Loading:** `LoadingState`; **Empty:** `EmptyState` ("No contacts found — try a different search" / "No contacts yet — add your first"); **Error:** `ErrorState` with retry (`StateViews`). Mutations → `toast.success`/`toast.error(err.response?.data?.detail || 'fallback')` (sonner). 422 custom-field errors surface inline on the field (not just a toast).
- **Destructive** delete/restore via `ConfirmDialog` (`destructive` variant).
- **Design tokens only** — `bg-background`/`bg-card`/`text-foreground`/`text-foreground/50`/`border-border`/`bg-primary`/`text-primary-foreground`/`focus:ring-primary/30`; status colors centralized in `StatusBadge` (no raw hex in component logic). Works under `.dark`.
- **Mobile-first (375px):** single column; `DataTable` → card stack; full-width stacked form fields; stacked detail sections; sticky header; 44px touch targets; thumb-reachable `Pagination`. **Desktop (`md:`+):** `DataTable` → real table; detail page 2-column grid (core/custom left, slots right) — **no app-wide sidebar** (S20). `rounded-xl`/`rounded-2xl` convention.

### 5.5 File-by-file change list (frontend)
- **DELETE** `frontend/src/pages/AttendeesPage.tsx`.
- **CREATE** `frontend/src/pages/ContactsPage.tsx`, `ContactDetailPage.tsx`, `ContactFormPage.tsx`.
- **CREATE** `frontend/src/components/ui/FormField.tsx`, `DataTable.tsx`, `Pagination.tsx`, `StatusBadge.tsx`.
- **CREATE** `frontend/src/services/contacts.ts`, `frontend/src/hooks/useContacts.ts`.
- **MODIFY** `frontend/src/App.tsx` — swap `/attendees`, add `/contacts*` routes + redirect, import new pages, drop `AttendeesPage` import.
- **MODIFY** `frontend/src/components/layout/BottomNav.tsx` — Contacts tab → `/contacts`.
- **MODIFY** `frontend/src/components/tasks/MemberSearchModal.tsx` — read paginated `items`.
- **MODIFY** `frontend/src/types/index.ts` — new contact types; reconcile `Member`/`Attendee`.

## 6. Migration / data
No production data migration here (CiviCRM values land via **S06** import). S03 is schema-light: at most the conditional snapshot-columns add (§3.3), nullable + no backfill. Custom-field group seeds come from **S02**. Dev/test data is built via conftest fixtures (`sample_contact` with `custom_data`, `sample_participant_history`). Existing S01-migrated contacts (none in pre-prod yet) simply gain editability; their `custom_data` defaults `{}`.

## 7. Acceptance criteria (each testable)
1. `POST /members` with valid core + valid `custom_data` mints a new contact (app-assigned id), persists `custom_data` coerced through S02 `validate_and_coerce`, returns 201 `ContactDetailResponse`, and writes one `audit_log` row `action='contact.create'`.
2. `POST /members` with an invalid custom field (e.g. `select` value not in options) returns 422 with the S02 structured error (`invalid_option`) and creates **no** contact.
3. `POST /members` with an `email` matching an existing non-deleted contact still succeeds (201) but the response `warnings` lists a possible-duplicate hint (no hard block).
4. `GET /members?page=1&page_size=25&search=<q>` returns `{items,total,page,page_size}`, excludes `is_deleted=true`, orders by `last_name,first_name`, and `total` reflects the full filtered count (not the page length); `page` past the end yields empty `items` with correct `total`.
5. `GET /members?include_deleted=true` as **volunteer** ignores the flag (no deleted rows); as **admin** includes soft-deleted rows.
6. `GET /members/{id}` returns full detail with core fields, `external_id`, raw `custom_data`, `custom_fields_resolved` where every `contact_reference` id resolves to `{id, display_name}`, the `derived` badge object (NULL fields when S23 hasn't run), and a `face` summary; a soft-deleted contact returns 404 to a volunteer and 200 to an admin.
7. `PATCH /members/{id}` with a partial `custom_data` (one key) **preserves** untouched custom keys (merge-then-validate), updates only provided core fields, bumps `updated_at`, and audits `contact.update` with a before/after diff.
8. Attempting to blank a `is_required` custom field via PATCH returns 422 `required`.
9. `DELETE /members/{id}` sets `is_deleted=true` (204), is idempotent (second call 204), does **not** delete the contact's `participants` rows, and the contact disappears from the default `GET /members`; `POST /members/{id}/restore` (admin) returns it to lists; restore on a non-deleted contact → 409.
10. `GET /members/{id}/attendance` returns the contact's participant history joined to events, paginated, newest-event first, with `source` (face/manual/zoom/name_list/community_report) present; 404 for an unknown contact.
11. `external_id` cannot be changed via `PATCH` (ignored/rejected); a contact's `id` is never reused after soft-delete.
12. Frontend: `/attendees` redirects to `/contacts`; `ContactsPage` renders the paginated `DataTable` (card stack <768px, table on desktop), Prev/Next paginate, search filters, and a row click navigates to `/contacts/:id`.
13. Frontend: `ContactFormPage` (create) renders core `FormField`s + S02 `<CustomFieldsSection>` from `GET /custom-fields/schema`, submits, and on 422 maps the error onto the offending custom field inline; success toasts and navigates to the new detail page.
14. Frontend: `ContactDetailPage` shows derived `StatusBadge`s (tier/active/regular/connected, "Unrated" when NULL), the real attendance-history table, resolved `contact_reference` chips linking to those contacts, and renders the four labelled slots (`data-slot="face-panel|attendance-history|consent|activities"`) with face/attendance live and consent/activities placeholders.
15. `MemberSearchModal` (contact picker) still works against the new paginated `GET /members` (reads `items`) and displays `display_name`.
16. All new UI uses design tokens only (no hardcoded hex in component logic), works under `.dark`, and meets 44px touch targets on mobile.
17. `pytest tests/ -q` and `ruff check app` pass; `npm run build`, `npm run lint`, `npm run test:run` pass; `alembic upgrade head` is clean + idempotent on Postgres (and the conditional snapshot migration, if shipped, is inspector-guarded).

## 8. Test plan

### Backend pytest (`DATABASE_URL=sqlite+aiosqlite`, `REDIS_URL=memory://`, `ENVIRONMENT=test`)
New file `backend/tests/test_contacts_crud.py`:
- `test_create_contact_ok` — POST valid core+custom → 201, row persisted, id minted (>0), `audit_log` `contact.create` written.
- `test_create_contact_invalid_custom_field` — `select` value not in options → 422 `invalid_option`, no contact created.
- `test_create_contact_duplicate_email_warns_not_blocks` — second contact, same email → 201 with non-empty `warnings`.
- `test_create_requires_first_last_for_individual` — missing `last_name` for `individual` → 422; for `organization` allowed.
- `test_list_paginated_shape` — seed 30 contacts → `GET /members?page=2&page_size=25` → `total=30`, `page=2`, `len(items)==5`, ordered by last/first.
- `test_list_excludes_soft_deleted` — soft-deleted absent for volunteer; present with `include_deleted=true` for admin only.
- `test_list_search_matches_nickname_and_email`.
- `test_get_detail_resolves_contact_reference` — contact with a `contact_reference` value → `custom_fields_resolved` contains a chip `{id, display_name}`.
- `test_get_detail_soft_deleted_404_for_volunteer_200_for_admin`.
- `test_patch_merges_custom_data` — set `{a:1}`, PATCH `{custom_data:{b:2}}` → stored has both `a` and `b`.
- `test_patch_blank_required_field_422`.
- `test_patch_core_partial_and_audit` — change `phone` only → other fields unchanged, `updated_at` bumped, `contact.update` audit with before/after.
- `test_patch_external_id_immutable`.
- `test_soft_delete_idempotent_and_preserves_participants` — delete twice → 204; participant rows still exist.
- `test_restore_contact_admin_only_and_409_when_not_deleted`.
- `test_delete_requires_auth` — 401 unauthenticated.
- `test_attendance_history_paginated_and_joined` — contact with 3 participant rows across events → paginated, newest-first, `source` present.
- `test_attendance_history_unknown_contact_404`.

Update `backend/tests/conftest.py`: add `sample_deleted_contact`, `sample_contact_with_custom_data` (incl. a `contact_reference` value pointing at a second contact), `sample_participant_history` (Participant+Event, `source='face'`/`'name_list'`).

### Frontend vitest + RTL
- `frontend/src/components/ui/DataTable.test.tsx` — table on desktop width / card stack on mobile (mock `matchMedia`), `onRowClick` fires with the row, empty/loading states.
- `frontend/src/components/ui/Pagination.test.tsx` — Prev disabled page 1, Next disabled last page, "showing a–b of total" correct, page-size change fires callback.
- `frontend/src/components/ui/FormField.test.tsx` — renders label + required asterisk + error; associates `htmlFor`.
- `frontend/src/pages/ContactsPage.test.tsx` — mocks paginated `GET /members` → rows render, search updates query, row click navigates (mock router), "New Contact" visible.
- `frontend/src/pages/ContactFormPage.test.tsx` — renders core fields + custom section (mock schema), submit calls `createContact`, 422 maps error onto the custom field, success navigates + toasts.
- `frontend/src/pages/ContactDetailPage.test.tsx` — mocks `GET /members/{id}` + attendance → renders derived badges (incl. "Unrated" for NULL tier), attendance table, resolved reference chips, and the four `data-slot` anchors; delete opens `ConfirmDialog`.

## 9. Rollout / rollback / risks
- **Rollout:** single PR. Order: (1) conditional migration + model snapshot columns (if owned here), (2) `contact_service.py` + `schemas.py` + `members.py` endpoints, (3) backend tests, (4) FE primitives (`FormField`/`DataTable`/`Pagination`/`StatusBadge`), (5) pages + routes + `BottomNav` + `MemberSearchModal` tweak, (6) FE tests. Gate on `pytest`/`ruff`/`alembic upgrade head` + `npm run build`/`lint`/`test:run`. No deploy-time data step (pre-prod).
- **Rollback:** revert the PR; if the snapshot migration shipped here, `alembic downgrade -1` drops only the columns it added (guarded). The `/attendees`→`/contacts` redirect prevents dead links. No production data impact (CiviCRM still source of truth until S21).
- **Risks & mitigations:**
  1. **Snapshot-column ownership race with S01/S23** → inspector-guarded conditional migration + master-doc assignment (§10 Q1); S03 never writes the columns.
  2. **Paginated `GET /members` is a breaking response-shape change** for existing consumers (`MemberSearchModal`, any S01/S02 caller) → update the picker to read `items` (defensive `items ?? data`); `npm run build` typecheck gate; covered by the `MemberSearchModal` test.
  3. **Partial-update `custom_data` clobbering** if implemented as replace → explicit merge-then-validate rule + `test_patch_merges_custom_data`.
  4. **Lost-update under concurrent volunteer edits** → `with_for_update()` on PATCH (AGENTS.md:105).
  5. **Contact-reference N+1 / slow detail** → single batched `IN` resolve; bounded query count documented.
  6. **Slots entangling with later sprints** → stable `data-slot` anchors + isolated placeholder components so S07/S08/S12 replace one component body without touching the page.
  7. **`viewer` reaching contacts before S15** → not an issue in S03 (only admin/volunteer exist); seam comment left for S15.
  8. **`birth_date` future-date / type** → `Date` (not datetime), validator rejects future dates.

## 10. Open questions & pending owner artifacts
1. **(Master) Snapshot-column ownership** — assign `last_attended_at/attendance_count/weeks_absent/tier/is_active/is_regular/is_connected` to exactly one sprint. Recommended: **S01** (with the other core columns). S03 reads-only; S23 writes. The conditional guarded migration here is the safety net otherwise.
2. **`/members` vs `/contacts` route prefix** — S03 keeps backend prefix `/members` (avoids a second rename after S01); UI/domain is "Contacts". Confirm the master is fine with the internal prefix staying `/members` (or schedule a `/contacts` alias). `MemberSearchModal` already calls `/members`.
3. **`gender` value set** — free-form `str` vs fixed `Literal['male','female','other']` / CiviCRM `gender_id` mapping. Owner to confirm the canonical list (the export's `gender_id` informs S06). S03 stores permissive `str`; tighten later.
4. **`contact_subtype` taxonomy** — locked list: New Friend / Regular Attendee / Regular Member / Volunteer / Student / Parent / Staff (+ Org: Team / Sponsor) (framework.md §2). Confirm fixed **select** (seed it) vs free text; S03 stores free-form `str`. **Note overlap:** "Regular Attendee" is also a *derived* attribute — keep the admin-set `contact_subtype` and the S23-computed `is_regular` distinct.
5. **Soft-dup policy** — S03 warns (non-blocking) on email match; confirm owner wants warn vs hard block vs silent (hard merge tooling is S11). Default chosen: warn-only.
6. **Household/Organization UX depth** — S03 stores `contact_type` and renders an individual-centric form (org name in `first_name`). Confirm whether households/orgs need first-class fields/relationships now or can wait.
7. **Deprecating `GET /members/attendees`** — once S07's profile face panel + S03's paginated list cover its use, the `/attendees` endpoint (face-thumbnail card data, `members.py:63`) may be retired; flag for the master, do not delete in S03.
8. No external owner artifact blocks S03 (the CiviCRM exports / n8n JSON / reports screenshot are S06/S14/S18 inputs, not here).

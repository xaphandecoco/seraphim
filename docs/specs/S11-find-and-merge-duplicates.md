# S11 — Find & Merge Duplicates
**Phase:** D — Power features · **Depends on:** S03 (Native Contact CRUD & Detail Profile) · **Effort:** L · **Status:** Not started

> **Authoritative naming.** This spec uses the canonical target data model from the master prompt + `docs/crm-research/framework.md`. The contact table is `contacts` / model `Contact` (renamed from `CiviCRMMember`/`civicrm_members` in **S01**; `backend/app/models.py:66`), attendance is `participants` / `Participant` (renamed from `Attendance`/`attendance`; `models.py:187`), and `events` / `Event` (renamed from `CiviCRMEvent`; `models.py:77`). The `audit_log` table is **created by S01** (used by S03/S08/S11/S12 — see `docs/specs/S08-biometric-consent-and-rtbf.md:76`). FastAPI routers carry **no `/api` prefix** (nginx/Vite strip it — `AGENTS.md` "API prefix convention"). Role guards reuse the real `require_admin`/`require_volunteer`/`get_current_user` in `backend/app/dependencies.py:45,56,10`.
>
> **Charter hard rule (from the prompt + framework.md §3.5):** *never auto-merge name collisions.* The finder only **proposes**; a human always **decides**; same-name merges require an explicit confirmation flag, enforced server-side.
>
> Where this spec touches a table another sprint owns (`audit_log` S01; `face_samples`/`compreface_subjects` S07; `biometric_consent` S08; `groups`/`group_members` S09; `activities` S12; `name_alias`/`name_match_review_queue`/`community_report` S22), it **consumes** the canonical shape and does not redefine it. §10 lists every reconciliation item the master doc must confirm.

---

## 1. Goal & rationale

S06 (CiviCRM XLSX re-export migration), S10 (general import wizard), S13 (public newcomer form), S07 (face auto-enroll), and S22 (name-list attendance intake → "create contact if no match") all create duplicate `contacts` rows for the same human:
- the same person re-exported under two CiviCRM Contact IDs (two `external_id`s);
- a walk-in newcomer submitted through the public form who already exists;
- a name-list / community-report attendee that the matcher (S22) created fresh instead of linking;
- a face auto-enroll (S07) that minted a new contact when an existing one should have been linked.

With ~1,440 contacts and ~33.6k attendance rows growing organically, duplicate contacts **double-count attendance** (`participants` enforces `UNIQUE(event_id, contact_id)` *per contact id*, so two ids = two attended rows for one event), **break leader/zone/ministry rollups** (S14), **split** a person's face-recognition history (S07) and biometric consent (S08) across two records, and **fragment** the people-link graph (Invited By / Consolidated By / Community / Ministry / Network / Lifegroup Leader — all `contact_reference` custom fields per the locked decisions).

The locked decision (decisions.md Round 3) is **one attendance per person per event** and **every attendance row maps to a known contact** — duplicate contacts violate that the moment two ids point at one human. CiviCRM shipped "Find & Merge Duplicate Contacts"; replacing CiviCRM (framework.md §1) means Seraphim must own it.

This sprint delivers:
1. **Admin-configurable dedupe rules** — a list of `{field, length, weight}` plus a match `threshold`, so the church tunes match aggressiveness.
2. A **candidate-pair finder**: cheap SQL **blocking** (key buckets) to keep the O(n²) comparison tractable on 1,440+ rows, then Python **scoring** per blocked pair against the active ruleset.
3. A **side-by-side merge UI** where a human picks the survivor, resolves field-by-field conflicts, previews the FK impact, and confirms.
4. **Transactional FK reassignment** of *every* table referencing `contacts.id` from loser → survivor, soft-deleting the loser (`is_deleted=True`), in one DB transaction.
5. A **full before/after `audit_log` row** per merge (complete loser snapshot + reassignment manifest), making each merge forensically reconstructable and a future "unmerge" sprint possible.

---

## 2. Scope

### In scope
- New table `dedupe_rule_set` (admin-editable rule configs) + one seeded **Default** set.
- Candidate-pair finder service: SQL blocking + Python pairwise scoring; **on-demand** run (admin/volunteer clicks "Find duplicates") returning a ranked, paginated list of candidate pairs. **No** background/scheduled scan (deferred to S16 cron / S17 outbox).
- Merge **preview** endpoint: given `(survivor_id, loser_id)`, returns the per-field conflict matrix + the exact reassignment manifest (counts per FK table) **without writing**.
- Merge **execute** endpoint: transactional FK reassignment loser→survivor across **every** table referencing `contacts.id`, field-level survivor-value resolution, loser soft-delete, single `audit_log` row.
- Merge **history** endpoint: read `audit_log WHERE action='contact_merge'`.
- "Duplicates" page: rules editor (admin only), candidate list, side-by-side merge modal with conflict resolution + impact preview + name-collision gate, history tab.
- Backend pytest + frontend vitest coverage; `ruff check app`, `npm run build`, `npm run lint`, `npm run test:run` clean.

### Out of scope (explicit)
- **Automatic / unattended merging** of any kind. The charter says never auto-merge name collisions; we extend that to **never auto-merge anything** — every merge is reviewed individually.
- **Unmerge / merge-reversal UI.** The audit row is *designed* to make this possible later; the reverse op is a separate sprint.
- **Scheduled/background duplicate scanning** + email/Chat digests of new duplicates (S16 cron + S17 outbox + S18 Google Chat).
- **"Merge all high-confidence pairs in one click"** — explicitly excluded.
- **Deduplicating events** or any entity other than `contacts`. (Event identity was resolved by real Event ID in S06.)
- **Phonetic/nickname libraries** beyond the standard library — no `jellyfish`/`rapidfuzz` dependency added in v1; scoring uses `difflib.SequenceMatcher` + normalized exact/prefix comparisons. Filipino-aware nickname matching is owned by the S22 name-matching service; if the owner later wants it in dedupe scoring, that is an additive iteration (§10). `pip-audit` review required for any new dependency.
- **Cross-`contact_type` merge hard-block** — merging an Individual into an Organization is allowed only with an explicit confirm; we **warn**, we don't hard-block (§4 business rules).

---

## 3. Data model changes

> All JSON columns use the project alias `JSONB = JSON().with_variant(_PG_JSONB, "postgresql")` defined at `backend/app/models.py:16-22` (JSONB in Postgres, JSON on SQLite tests). All timestamps use `utc_now()` (`models.py:27-28`), naive UTC. **Never edit applied migrations** (`AGENTS.md`); CI runs `alembic upgrade head` clean + idempotent on Postgres.

### 3.1 New table: `dedupe_rule_set`

Admin-tunable rule configurations. Multiple sets may exist (e.g. "Strict", "Loose"); exactly one carries `is_default=True` and is used when a run does not name a set.

| column | type | null | default | notes |
|---|---|---|---|---|
| `id` | Integer PK, identity/autoincrement | no | seq | app-minted |
| `name` | String(100) | no | — | UNIQUE; e.g. "Default" |
| `description` | Text | yes | NULL | |
| `rules` | `JSONB` | no | `[]` (`server_default '[]'`) | list of `{ "field": str, "length": int\|null, "weight": int }`; `field` ∈ whitelist (§4); `length` = optional prefix length for blocking/compare; `weight` = positive int points |
| `threshold` | Integer | no | `100` | minimum summed weight for a pair to qualify as a candidate |
| `is_default` | Boolean | no | `false` (server_default) | exactly one row true (enforced in service, not DB) |
| `is_active` | Boolean | no | `true` (server_default) | inactive sets are hidden from run selection |
| `created_at` | DateTime (naive UTC) | no | `utc_now` | |
| `updated_at` | DateTime (naive UTC) | no | `utc_now`, onupdate `utc_now` | |
| `updated_by_id` | Integer FK→`users.id` ON DELETE SET NULL | yes | NULL | |

- `UniqueConstraint("name", name="uq_dedupe_rule_set_name")` (declared in both the model `__table_args__` and the migration so `create_all` tests + Postgres agree — mirror `models.py:212-214`).
- `rules`/`threshold` are validated by **Pydantic** on write (§4), not by DB constraints.

**Seeded Default** (migration data step; mirrors CiviCRM's "Individual Unsupervised" rule of thumb):
```json
{
  "name": "Default",
  "description": "Seeded default — exact email OR (first+last name) wins; phone/birth_date add weight.",
  "rules": [
    {"field": "email",      "length": null, "weight": 100},
    {"field": "first_name", "length": null, "weight": 30},
    {"field": "last_name",  "length": null, "weight": 40},
    {"field": "phone",      "length": null, "weight": 50},
    {"field": "birth_date", "length": null, "weight": 40}
  ],
  "threshold": 70,
  "is_default": true,
  "is_active": true
}
```
Threshold 70 means: email alone (100) qualifies; first+last (30+40=70) qualifies; phone+last (50+40) qualifies — but first OR last alone (30 / 40) does **not**.

### 3.2 `audit_log` — consumed, **not** created here

Canonical shape **created in S01** (`docs/specs/S08-biometric-consent-and-rtbf.md:76`; assigned to S01 in §10):
`audit_log(id PK, actor_id FK→users.id ON DELETE SET NULL nullable, action String(50), entity String(50), entity_id Integer, before JSONB, after JSONB, at DateTime default utc_now)`, with `ix_audit_log_entity (entity, entity_id)` and `ix_audit_log_at (at)`.

S11 writes rows with `action="contact_merge"`, `entity="contacts"`, `entity_id=<survivor_id>`. **S11 adds no migration for `audit_log`** and **does not** declare an `AuditLog` model (S01 owns the class). If — and only if — at implementation time S01's `audit_log` is somehow absent, the implementer escalates per §10 rather than forking a local variant.

### 3.3 No schema change to `contacts`
Merge soft-deletes the loser via the existing `contacts.is_deleted` boolean (set in S01; `models.py` contact block). No new column on `contacts`; the audit row — not a column — records "merged_into". A nullable `contacts.merged_into_id` redirect column is a recommendation for the master doc (§10), **not** built here, to avoid widening `contacts` ownership outside S01/S03.

### 3.4 Alembic migration plan

One new revision, `backend/alembic/versions/<rev>_s11_add_dedupe_rule_set.py`. **`down_revision` = the current `alembic heads` at implementation time** (do not invent — S11 is Phase D, downstream of S01–S10; resolve the real head).

**Forward ops:**
1. `op.create_table("dedupe_rule_set", …)` with all columns in §3.1. JSON column declared `sa.JSON().with_variant(postgresql.JSONB, "postgresql")` (import `from sqlalchemy.dialects import postgresql`); `server_default=sa.text("'[]'")` for `rules`, `sa.text("100")` for `threshold`, `sa.text("false")`/`sa.text("true")` for the booleans, `sa.text("CURRENT_TIMESTAMP")` for the timestamps.
2. `op.create_unique_constraint("uq_dedupe_rule_set_name", "dedupe_rule_set", ["name"])`.
3. **Idempotent seed:** insert the Default set **only if the table is empty** (or rely on the unique `name` to no-op): `op.execute` a parameterized insert with the JSON serialized as a text literal (portable — the `.with_variant` column accepts text on SQLite and JSONB-casts on Postgres). Guard: `if conn.execute(sa.text("SELECT COUNT(*) FROM dedupe_rule_set")).scalar() == 0:`.

**Downgrade:** `op.drop_constraint(...)`; `op.drop_table("dedupe_rule_set")`. **Do not** touch `audit_log` (shared, S01-owned) — a comment in the migration must state this explicitly.

**.with_variant reminder:** the `rules` JSONB column must use `JSON().with_variant(JSONB,"postgresql")` so SQLite tests and Postgres prod agree (`models.py:20-22`).

---

## 4. Backend

### 4.1 Endpoints

All under a new router `backend/app/routers/dedupe.py`, `APIRouter(prefix="/dedupe", tags=["dedupe"])`, registered in `backend/app/main.py` with `dependencies=[Depends(check_setup_complete)]` (mirror the existing router includes; no `/api` prefix).

| METHOD | path | role | request schema | response schema | notes |
|---|---|---|---|---|---|
| GET | `/dedupe/rule-sets` | volunteer+ | — | `DedupeRuleSetList` | list active rule sets (rules visible so volunteers see how matching works) |
| POST | `/dedupe/rule-sets` | **admin** | `DedupeRuleSetCreate` | `DedupeRuleSetResponse` (201) | create; validates rules vs field whitelist + positive weights + `threshold≥1`; `audit_log(action="dedupe_rule.create")` |
| PUT | `/dedupe/rule-sets/{id}` | **admin** | `DedupeRuleSetUpdate` | `DedupeRuleSetResponse` | edit; setting `is_default=true` clears the flag on all others in the **same transaction**; `audit_log(action="dedupe_rule.update")` |
| DELETE | `/dedupe/rule-sets/{id}` | **admin** | — | `204` | **409** if it is the only `is_active` set or has `is_default=true` |
| POST | `/dedupe/candidates` | volunteer+ | `CandidateRunRequest` | `CandidatePairList` | run finder on demand; blocking + scoring; paginated, ranked desc by score |
| POST | `/dedupe/merge/preview` | volunteer+ | `MergePreviewRequest` | `MergePreviewResponse` | dry-run: conflict matrix + reassignment manifest; **no writes** |
| POST | `/dedupe/merge` | volunteer+ | `MergeExecuteRequest` | `MergeResultResponse` | transactional merge; writes `audit_log`; returns survivor + manifest |
| GET | `/dedupe/merge/history` | volunteer+ | query: `page`, `page_size` | `MergeHistoryList` | reads `audit_log WHERE action='contact_merge'` for transparency |

Role enforcement uses `require_admin` / `require_volunteer` (`dependencies.py:45,56`). The **viewer** role (S15) gets none of these — `require_volunteer` already rejects anything not in `("admin","volunteer")` (`dependencies.py:59`), so viewers receive 403 on every dedupe route. `actor_id` comes from `int(current_user["sub"])` (`dependencies.py:37-41`).

### 4.2 Pydantic schemas (new "Dedupe" section in `backend/app/schemas.py`)

```python
WhitelistField = Literal[
    "first_name","last_name","suffix","gender","birth_date","phone","email","street_address"
]

class DedupeFieldRule(BaseModel):
    field: WhitelistField
    length: int | None = Field(default=None, ge=1)
    weight: int = Field(ge=1, le=1000)

class DedupeRuleSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    rules: list[DedupeFieldRule] = Field(min_length=1)
    threshold: int = Field(ge=1)
    is_default: bool = False
    is_active: bool = True

class DedupeRuleSetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    rules: list[DedupeFieldRule] | None = None
    threshold: int | None = Field(default=None, ge=1)
    is_default: bool | None = None
    is_active: bool | None = None

class DedupeRuleSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int; name: str; description: str | None
    rules: list[DedupeFieldRule]; threshold: int
    is_default: bool; is_active: bool
    created_at: datetime; updated_at: datetime

class DedupeRuleSetList(BaseModel):
    items: list[DedupeRuleSetResponse]

class CandidateRunRequest(BaseModel):
    rule_set_id: int | None = None          # None → default set
    contact_type: str | None = None          # restrict to one type
    max_pairs: int = Field(default=200, ge=1, le=1000)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)

class ContactSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int; external_id: int | None
    contact_type: str
    first_name: str | None; last_name: str | None
    email: str | None; phone: str | None; birth_date: date | None
    participant_count: int = 0               # "which to keep" hint
    face_sample_count: int = 0

class CandidatePair(BaseModel):
    contact_a: ContactSummary; contact_b: ContactSummary
    score: int; matched_fields: list[str]
    same_name: bool                          # drives the never-auto-merge warning

class CandidatePairList(BaseModel):
    items: list[CandidatePair]; total: int
    page: int; page_size: int
    rule_set_id: int; threshold: int

class FieldConflict(BaseModel):
    field: str; survivor_value: Any; loser_value: Any; differs: bool

class MergePreviewRequest(BaseModel):
    survivor_id: int; loser_id: int

class ReassignmentCount(BaseModel):
    table: str; column: str; rows: int

class MergePreviewResponse(BaseModel):
    survivor: ContactSummary; loser: ContactSummary
    field_conflicts: list[FieldConflict]
    custom_field_conflicts: list[FieldConflict]
    reassignments: list[ReassignmentCount]
    same_name: bool
    warnings: list[str]

class FieldChoice(BaseModel):
    field: str; source: Literal["survivor","loser"]

class MergeExecuteRequest(BaseModel):
    survivor_id: int; loser_id: int
    field_choices: list[FieldChoice] = []            # default: keep survivor's value
    custom_field_choices: list[FieldChoice] = []
    rule_set_id: int | None = None                   # recorded into audit.after
    confirm_same_name: bool = False                  # MUST be True when names collide

class MergeResultResponse(BaseModel):
    survivor_id: int; loser_id: int; merged: bool
    reassignments: list[ReassignmentCount]
    audit_log_id: int
    warnings: list[str]

class MergeHistoryItem(BaseModel):
    audit_log_id: int; survivor_id: int; loser_id: int
    actor_id: int | None; at: datetime; reassignment_total: int

class MergeHistoryList(BaseModel):
    items: list[MergeHistoryItem]; total: int; page: int; page_size: int
```

**Field whitelist** (the only `field` values a rule may name — `contacts` core columns safe to compare and cheap to block on):
`first_name, last_name, suffix, gender, birth_date, phone, email, street_address`. Custom fields are **not** rule-targetable in v1 (keeps blocking SQL on indexed core columns). `WhitelistField` makes any other value a Pydantic 422.

### 4.3 Services / workers / business rules

New service `backend/app/services/dedupe_service.py`. No worker changes (on-demand only).

#### A. Candidate finder — blocking, then scoring

**Blocking (SQL, cheap):** never compare all C(1440,2) ≈ 1.04M pairs. Build *blocks* from the active rule set's fields; only compare pairs **within** a block.

- Blocking keys come from the rule fields that are good keys: `email`, `last_name`, `phone`, and `first_name` truncated to `length` when given.
- **Normalization** — Python helper `_norm(field, value)` reused by both blocking-key generation and scoring: lowercase, strip, collapse internal whitespace; for names strip punctuation; for `phone` keep digits only; `birth_date` → ISO `YYYY-MM-DD`. This is the same option-normalization concern as S06's "DEPARO/Deparo" — **reuse a shared `services/normalize.py` if S06/S22 ship one**; otherwise define locally and flag for consolidation (§10).
- Exclude `is_deleted=True` contacts from every blocking query.
- Algorithm (set-based, async SQLAlchemy 2.0):
  1. For each blocking field: `SELECT id, <field> FROM contacts WHERE <field> IS NOT NULL AND <field> <> '' AND is_deleted = false` (optionally `AND contact_type = :ct`). Group by `_norm(field, value)` **in Python** (dialect-portable; avoids Postgres-only `array_agg`/`lower()` semantics differing from SQLite). Keep only groups with `count > 1`.
  2. Within each group emit unordered pairs `(min_id, max_id)` into a Python `set` (dedupes pairs found via multiple blocking keys).
  3. Do **not** cap before scoring — cap `max_pairs` **after** scoring/ranking so the highest-scoring pairs survive the cap.

**Scoring (Python, per blocked pair `(a, b)` against active `rules`):**
- For each rule `{field, length, weight}` compute a per-field match in `[0,1]`:
  - `email` → normalized exact → 1.0 else 0.0.
  - `phone` → digits-only exact → 1.0 else 0.0.
  - `birth_date` → exact ISO equality → 1.0 else 0.0.
  - `gender`, `suffix` → normalized exact → 1.0 else 0.0.
  - `first_name`, `last_name`, `street_address` → if `length` given, compare normalized prefixes of that length (exact → 1.0); else `difflib.SequenceMatcher(None, na, nb).ratio()` with `>= 0.92 → 1.0`, `0.80–0.92 → 0.5`, else `0.0` (constants at module top, documented).
  - Missing value on either side → contributes 0 (never a match).
- `score = round(sum(weight_i * match_i))`. Keep the pair iff `score >= threshold`.
- `matched_fields` = fields whose per-field match was `> 0`.
- `same_name = (_norm("first_name", a.first_name) == _norm("first_name", b.first_name)) and (_norm("last_name", a.last_name) == _norm("last_name", b.last_name))` — drives the UI never-auto-merge gate.
- Rank by `score` desc, then `(min_id, max_id)` for stability; apply `max_pairs` cap; then paginate (`page`/`page_size`).

**Performance notes:**
- Blocking keeps comparisons within-block; at 1,440 contacts the realistic candidate count is hundreds, not a million. `difflib` runs only on name fields of already-blocked pairs.
- `participant_count` / `face_sample_count` for `ContactSummary` are fetched in **two grouped queries** over the candidate contact-id set (no N+1): `participants` keyed by `contact_id`, `face_samples` keyed by `contact_id`.
- Endpoint is on-demand and explicitly bounded (`max_pairs ≤ 1000`); no streaming needed at this scale. Document the O(blocks × block_size²) shape for when the dataset grows past ~50k (then move to S16 cron / S17 outbox).

#### B. Merge preview (`merge_preview(db, survivor_id, loser_id) -> MergePreviewResponse`)

- Load both contacts (404 if either missing; 409 if either `is_deleted=True`; 422 if `survivor_id == loser_id`).
- **Field conflict matrix** over core fields (`first_name, last_name, suffix, gender, birth_date, phone, email, street_address`, plus `external_id`): for each, `differs = survivor_value != loser_value`. Special rule for `external_id` — **never silently drop** the loser's: if survivor's is NULL and loser's is set, default the choice to keep the loser's (preserves CiviCRM traceability); if **both** are set and differ, surface a conflict + warning `"both contacts carry a CiviCRM external_id; only one can be kept on the survivor (the other is preserved in the audit before-snapshot)."`
- **Custom-field conflict matrix:** iterate keys present in either contact's `custom_data` JSONB. Multi-value custom fields (`is_multi`/multiselect per S02) default to **union** (no conflict) unless a scalar key differs. A `contact_reference` custom value equal to the **loser's own id** → rewrite to survivor (self-reference fix-up), surfaced as a **warning** not a conflict.
- **Reassignment manifest:** for every FK table (full list in §4.C) `SELECT count(*) … WHERE <col> = :loser_id` → a `ReassignmentCount{table, column, rows}`. Includes the `participants` collision count.
- **`same_name`** echoed from the active ruleset comparison so the UI/preview agree with execute.
- **Warnings list:** different `contact_type` ("merging across contact types"); both have a `biometric_consent` row (S08) ("two consent records — survivor's retained, loser's archived to audit"); both enrolled in CompreFace (S07) ("loser's face samples reassigned to survivor; loser's ComprefaceSubject detached for cleanup"); low-confidence (`same_name` false but emails differ) ("verify these are the same person"); name collision present ("same name — explicit confirmation required").

#### C. Merge execute — transactional FK reassignment (the core)

`merge_contacts(db, survivor_id, loser_id, field_choices, custom_field_choices, rule_set_id, confirm_same_name, actor_id) -> MergeResultResponse`:

**Guards (before any write):**
1. `survivor_id != loser_id` → 422.
2. Load both rows `with_for_update()` (mirrors the dual-approval lock pattern, `AGENTS.md` / `models.py:170-184` note); both must exist and neither `is_deleted` → 404 / 409.
3. If the active ruleset makes `same_name` true and `confirm_same_name is not True` → **409 "Name collision — explicit confirmation required (never auto-merge)."** (Charter hard rule, enforced server-side, not just in the UI.)
4. Re-run preview internally so the persisted manifest reflects the at-commit state (avoids TOCTOU between preview and execute).

**Transaction body (single commit — all-or-nothing; on any exception, nothing persists):**
1. **Snapshot** loser (full row + `custom_data` + counts) and survivor's pre-merge state into Python dicts for the audit `before`.
2. **Apply field choices** to survivor: for each `FieldChoice(source="loser")` copy the loser's value onto survivor; default (no choice) keeps survivor's. Apply the `external_id` preservation rule. Apply `custom_field_choices` + multi-value union into `survivor.custom_data`.
3. **Reassign every FK** from `loser_id` to `survivor_id`. **Exhaustive, enumerated list of every column referencing `contacts.id` in the canonical model** (reconcile the exact constraint names with the owning sprint at implementation; sources cited):

   | table | column | owning sprint | reassign strategy |
   |---|---|---|---|
   | `participants` | `contact_id` | S01 (`models.py:191`) | **collision-aware** — `UNIQUE(event_id, contact_id)` means both ids may attend one event (see below) |
   | `compreface_subjects` | `contact_id` | S01 repoint (`models.py:93`) | if survivor already has a subject row, **detach** loser's (`contact_id=NULL`) and queue CompreFace cleanup **post-commit**; else `UPDATE` loser's to survivor. Never CompreFace network I/O inside the DB txn |
   | `face_samples` | `contact_id` | S07 | plain `UPDATE` — all loser's enrolled images move to survivor |
   | `biometric_consent` | `contact_id` (UNIQUE) | S08 | if survivor has a row, archive loser's into audit `before` then `DELETE` loser's; else `UPDATE` loser's to survivor |
   | `activities` | `target_contact_id` | S12 (`S12:64`) | plain `UPDATE … SET target_contact_id=survivor WHERE target_contact_id=loser` |
   | `group_members` | `contact_id` (composite PK `(group_id, contact_id)`) | S09 (`S09:87`) | **collision-aware** — `DELETE` loser rows whose `group_id` already has survivor, `UPDATE` the rest |
   | `name_alias` | `contact_id` | S22 | plain `UPDATE` — learned/manual aliases of the loser now resolve to survivor |
   | `name_match_review_queue` | `resolved_contact_id` (nullable) | S22 | **nullable — only rewrite if set** (per CN-04): plain `UPDATE … WHERE resolved_contact_id=loser`; already-resolved queue rows repoint to survivor (`candidate_contact_ids` JSONB may also contain `loser_id`; rewrite in Python if present) |
   | `community_report` | `submitted_by_contact_id` (nullable) | S22 | **nullable — only rewrite if set** (per CN-04): plain `UPDATE` — leader-submitter link repoints (`attendee_names` JSONB is name text, not ids → untouched) |
   | `community_report` | `event_leader_contact_id` (nullable) | S22 | **nullable — only rewrite if set** (per CN-04): plain `UPDATE … WHERE event_leader_contact_id=loser` — event-leader link repoints |
   | `contacts.custom_data` (other rows) | `contact_reference` values pointing at `loser_id` | S02/S03 | **scan-and-rewrite** across *other* contacts (see below) — the people-link fields (Invited By / Consolidated By / Community / Ministry / Network / Lifegroup Leader) |
   | `detections` | `matched_name` synthetic `member:{id}` | S01 (no contact FK today, `models.py:103`) | if S07 added a real `Detection.contact_id` FK, `UPDATE` it; else `UPDATE … SET matched_name='member:{survivor}' WHERE matched_name='member:{loser}'`. Confirm which path landed (§10) |
   | `logs` | `matched_name` synthetic `member:{id}` | S01 (no contact FK, `models.py:217`) | same string rewrite as detections |
   | `saved_searches.owner_id` | → **users**, not contacts | S09 | **N/A** (owner is a user) |
   | `tasks` / `task_actions` / `pit_queue` | → `detections`/`users` | existing | **N/A** (no contact FK) |
   | `volunteer_stats` | → `users` | existing | **N/A** |
   | `dedupe_rule_set.updated_by_id` | → `users` | this sprint | **N/A** |

   **`participants` collision handling (most important):** for events where **both** survivor and loser have a `participants` row, a blind `UPDATE` violates `UNIQUE(event_id, contact_id)`. Strategy:
   - Collision events: `SELECT event_id FROM participants WHERE contact_id=loser INTERSECT SELECT event_id FROM participants WHERE contact_id=survivor` (or the dialect-portable Python set-intersection of the two id lists).
   - For collision events: **delete the loser's** participant row (survivor already counts for that event), but first **promote** the survivor's `status` to the precedence-max of the two (`attended` > `registered` > `no_show` > `cancelled`) so an `attended` is never downgraded; record deleted loser rows in the audit `before`. If the survivor's row was `source != 'face'` and the loser's was `face` with a `detection_id`, prefer keeping the face provenance on the survivor (set survivor `source='face'`, `detection_id=loser.detection_id`) — flagged as optional polish, low priority.
   - Non-collision events: `UPDATE participants SET contact_id=survivor WHERE contact_id=loser AND event_id NOT IN (collisions)`.

   **`group_members` collision handling:** delete loser rows where `group_id` already has the survivor; update the rest.

   **`contact_reference` custom-field rewrite (people-link fields):** other contacts may reference the loser via a `contact_reference` custom field in `custom_data` (e.g. "Invited By" = loser_id). Per decisions.md Round 3 these are first-class people-link fields. Rewrite them:
   - Identify `contact_reference` field names from `custom_field_def WHERE data_type='contact_reference'` (S02).
   - For each such field, find contacts whose `custom_data->>field` (scalar) equals the loser id, or whose `custom_data->field` (multi/array) contains the loser id, and rewrite to survivor (dedupe if the array already holds survivor). **Portable baseline:** fetch candidate rows and rewrite in Python (works on SQLite tests); on Postgres a JSONB-path `UPDATE` is an acceptable optimization but the Python path is the baseline asserted by tests.
   - Count these into the manifest as `table="contacts", column="custom_data:<field>"`.

4. **Soft-delete the loser:** `loser.is_deleted = True`, `loser.updated_at = utc_now()`. **Never** hard-delete (preserves the FK target until audit is written + supports future unmerge).
5. **Write the `audit_log` row (same transaction):**
   - `actor_id=actor_id`, `action="contact_merge"`, `entity="contacts"`, `entity_id=survivor_id`.
   - `before` = `{ "loser": <full loser snapshot incl custom_data>, "survivor_pre": <survivor core+custom>, "deleted_participants": [...], "deleted_group_members": [...], "archived_consent": {…|null}, "detached_subject_id": <id|null>, "rewritten_references": [...] }`.
   - `after` = `{ "survivor_post": <survivor core+custom after choices>, "reassignments": [<ReassignmentCount…>], "rule_set_id": <id|null>, "same_name": bool }`.
   - This single row is the **reversibility contract**: enough to (a) prove what happened and (b) seed a future unmerge.
6. **Recompute survivor member status (per CN-23):** call `await member_status_service.recompute_contacts(db, [survivor_id])` at the end of the merge transaction (the survivor's attendance/participant history changed when the loser's rows were reassigned). Signature: `async def recompute_contacts(db: AsyncSession, contact_ids: list[int]) -> dict` (owned by S23). If S23 has not landed at implementation time, escalate per §10 rather than forking.
7. Commit.

**Post-commit (outside the DB transaction, best-effort, never blocks/rolls back the merge):**
- If a `compreface_subjects` row was detached in step 3, enqueue CompreFace cleanup via the existing face-cleanup host (`backend/app/services/face_cleanup.py` / `queue_manager._process_face_cleanup`, `backend.md §E`). CompreFace network I/O must not be in the DB transaction (matches the existing worker-side pattern). If post-commit cleanup fails, log it and add a **non-fatal** warning to `MergeResultResponse.warnings`; DB state is already correct and the orphan is reconciled by the cleanup job.

**Edge cases:**
- Loser has **zero** FK rows anywhere → still soft-deletes loser + writes audit (manifest all zeros).
- Both unenrolled (no faces) → no CompreFace work.
- Concurrent merge of the same loser by two admins → `with_for_update()` row locks; the second transaction sees `is_deleted=True` and 409s.
- A contact referenced as another's `contact_reference` **and** being merged → handled by both the self-reference fix-up (preview) and the external rewrite (execute).
- `external_id` uniqueness: survivor keeps exactly one `external_id`; the other lives only in the audit `before` (the UNIQUE constraint on `contacts.external_id` from S01 would reject two anyway).
- Merge across `contact_type` → allowed with a warning (not blocked).

### 4.4 File-by-file change list

**Create:**
- `backend/app/services/dedupe_service.py` — `find_candidates()`, `merge_preview()`, `merge_contacts()`, `_norm()`, `_score_pair()`, blocking-key helpers, `_status_precedence()`, `_active_rule_set()`, `_reassignment_manifest()`.
- `backend/app/routers/dedupe.py` — the 8 endpoints in §4.1; role deps from `dependencies.py`.
- `backend/alembic/versions/<rev>_s11_add_dedupe_rule_set.py` — §3.4 migration (dedupe_rule_set + seed; **no** audit_log).
- `backend/tests/test_dedupe.py` — pytest suite (§8).

**Modify:**
- `backend/app/models.py` — add `DedupeRuleSet` model (use the `JSONB` alias at `models.py:22`; declare `uq_dedupe_rule_set_name` in `__table_args__`). **Do not** add an `AuditLog` model (S01 owns it).
- `backend/app/schemas.py` — add the "Dedupe" section (§4.2).
- `backend/app/main.py` — `from app.routers import … dedupe` + `app.include_router(dedupe.router, dependencies=[Depends(check_setup_complete)])`.

**DELETE:** none (additive; no CiviCRM coupling in dedupe paths).

---

## 5. Frontend

### 5.1 Pages / routes / components

**New route:** `/duplicates` → `DuplicatesPage`, registered in `frontend/src/App.tsx` wrapped in `ProtectedRoute` (volunteer+ find/merge). The **Rules** tab inside is admin-gated by `useAuthStore().isAdmin` (`frontend/src/store/authStore.ts`) — hidden controls + the API returns 403 for non-admins anyway. Add a nav entry in `frontend/src/components/layout/BottomNav.tsx` (desktop sidebar handled by the S20 polish pass); **hidden for viewers** (viewers must not reach contact records — decisions.md Round 4; the volunteer-gated API also 403s them).

**`frontend/src/pages/DuplicatesPage.tsx`** — mobile-first single column, three segments/tabs:
1. **Candidates** — "Find duplicates" button with a rule-set `<select>` + optional contact-type filter → `POST /dedupe/candidates`; renders a paginated list (reuse S03's `DataTable` + `Pagination`) of `CandidatePair`s. Each row: the two contacts side by side (name, email, phone, `participant_count`, `face_sample_count`), `score` as a badge, `matched_fields` chips, and a destructive-token **"same name — verify"** chip when `same_name`. "Review & merge" opens the merge modal.
2. **Rules** (**admin only**) — list rule sets; add/edit/delete via `DedupeRuleEditor` (field `<select>` from the whitelist, optional length, weight number, repeatable rows; threshold input; default/active toggles). Saves via `POST/PUT/DELETE /dedupe/rule-sets`.
3. **History** — `GET /dedupe/merge/history` table (survivor, loser, who, when, rows reassigned) — read-only transparency.

**`frontend/src/components/dedupe/MergeModal.tsx`**
- On open, `POST /dedupe/merge/preview`. Default survivor = the contact with the higher `(participant_count + face_sample_count)` (more history = better keep); a **"swap survivor/loser"** toggle re-fetches preview.
- Side-by-side columns survivor | loser. Each conflicting core/custom field gets a radio/toggle for the source (`survivor`/`loser`); non-conflicting fields shown muted. Multi-value fields show the union with a note.
- **Impact panel:** renders the `reassignments` manifest ("12 participants, 3 activities, 5 face samples move to survivor; 2 duplicate event attendances removed") and all `warnings` as alert banners (destructive token style).
- **Name-collision gate:** when preview `same_name` is true (or a collision warning is present), **Confirm merge** is disabled until an "I confirm these are the same person" checkbox is ticked, which sets `confirm_same_name=true`. This mirrors the server 409 — the UI never lets a same-name merge through silently.
- Confirm → `POST /dedupe/merge`; success → `sonner` toast ("Merged into <survivor name>"), invalidate queries, close. Error → surface `err.response?.data?.detail` via `sonner` (`AGENTS.md`). Use `frontend/src/components/ui/ConfirmDialog.tsx` for the final destructive confirmation (or fold it into the modal's primary action behind the checkbox guard).

**`frontend/src/components/dedupe/DedupeRuleEditor.tsx`** — admin rules CRUD form.

### 5.2 State (TanStack Query / Zustand)
- Query keys: `['dedupe','rule-sets']`, `['dedupe','candidates', ruleSetId, contactType, page]`, `['dedupe','merge','history', page]`; preview via `useMutation` (or `['dedupe','preview', survivorId, loserId]` re-fetched on swap).
- Mutations: `createRuleSet`, `updateRuleSet`, `deleteRuleSet`, `mergeContacts`. On merge success **invalidate**: `['dedupe','candidates', …]`, `['dedupe','merge','history']`, `['contacts', …]` + `['contact', survivorId]` (S03 keys — loser disappears, survivor refreshes), and S07 face-panel / S14 dashboard keys if present.
- Role from existing `authStore` (in-memory token; `AGENTS.md`). No new store.

### 5.3 UX states / tokens / mobile-first
- Reuse `frontend/src/components/ui/StateViews.tsx` (`LoadingState`/`EmptyState`/`ErrorState`) — confirmed present.
- **Loading:** finder run → spinner "Scanning for duplicates…"; preview → modal skeleton.
- **Empty:** "No likely duplicates found with the **<rule set>** ruleset." (offer to loosen threshold / pick another set).
- **Error:** surface server `detail`; the same-name 409 maps to a clear inline message ("This pair has the same name — tick the confirmation box to proceed.").
- **Design tokens only:** `bg-card`, `text-foreground`, `bg-background`, `border-border`, `text-primary`, destructive token for warnings — **no hex** (`AGENTS.md`). Dark mode works via tokens.
- **Mobile-first:** side-by-side merge becomes **stacked** survivor/loser cards under ~640px with a sticky "swap / confirm" action bar; `≥md` shows the two-column layout. Score badges + chips wrap. Desktop polish coordinates with S20.

### 5.4 File-by-file change list
**Create:**
- `frontend/src/pages/DuplicatesPage.tsx`
- `frontend/src/components/dedupe/MergeModal.tsx`
- `frontend/src/components/dedupe/DedupeRuleEditor.tsx`
- `frontend/src/services/dedupe.ts` — axios wrappers via the shared `frontend/src/services/api.ts` client (401→refresh interceptor reused).
- `frontend/src/types/dedupe.ts` — TS types mirroring the §4.2 schemas.
- `frontend/src/pages/__tests__/DuplicatesPage.test.tsx`, `frontend/src/components/dedupe/__tests__/MergeModal.test.tsx`.

**Modify:**
- `frontend/src/App.tsx` — add the `/duplicates` route (ProtectedRoute).
- `frontend/src/components/layout/BottomNav.tsx` — add "Duplicates" entry (hidden for viewer).

---

## 6. Migration / data
- **Schema:** the one new `dedupe_rule_set` table + idempotent seeded Default set (§3.4). No `audit_log` migration (S01 owns it).
- **No bulk data transform.** S11 is the **cleanup tool for S06's output**: after the big-bang CiviCRM import (S06) and the people-link review-queue resolution (S22), an admin runs the finder to catch the same person re-exported under two Contact IDs. Add a `docs/PRODUCTION_RUNBOOK.md` cutover step: *"After migration, run Find & Merge Duplicates with the Default ruleset; review high-score pairs first."*
- **Idempotency:** the seed inserts only when the table is empty (or no-ops via the unique `name`). Merges are inherently non-idempotent (loser is soft-deleted); the `is_deleted` + `with_for_update()` guards prevent double-merge.

---

## 7. Acceptance criteria (each independently testable)

1. A `dedupe_rule_set` table exists with a seeded **Default** set (`is_default=true`, `threshold=70`, the 5 seeded rules); `alembic upgrade head` → `downgrade -1` → `upgrade head` is clean + idempotent on Postgres, and `Base.metadata.create_all` builds the same table on SQLite.
2. `POST /dedupe/rule-sets` returns 422 for a rule whose `field` is not in the whitelist, a non-positive `weight`, or `threshold < 1`; accepts a valid set; setting `is_default=true` on one set clears it on all others in one transaction.
3. `DELETE /dedupe/rule-sets/{id}` returns 409 when the target is the only active set or is the default; otherwise 204.
4. `POST /dedupe/candidates` with the Default ruleset, on a DB seeded with two contacts sharing an email, returns exactly one `CandidatePair` with `score >= 100` and `email` in `matched_fields`; returns **no** pair for two contacts sharing only a first name (30 < 70).
5. The candidate finder **excludes** `is_deleted=true` contacts, never returns `(x, x)`, and emits each pair once even when matched via multiple blocking keys (unordered + deduped).
6. `same_name=true` on a pair whose normalized first+last match; `false` when they differ.
7. `POST /dedupe/merge/preview` returns a `field_conflicts` entry with `differs=true` where survivor/loser disagree, a `custom_field_conflicts` matrix, the `external_id` preservation behavior, and a `reassignments` manifest whose counts equal direct SQL `count(*)` for each FK column.
8. `POST /dedupe/merge` reassigns **every** enumerated FK loser→survivor in one transaction: after merge, `SELECT count(*) WHERE <col>=loser` is 0 for `participants` (non-collision), `face_samples.contact_id`, `activities.target_contact_id`, `name_alias.contact_id`, `name_match_review_queue.resolved_contact_id` (when set), `community_report.submitted_by_contact_id` (when set), `community_report.event_leader_contact_id` (when set); loser is `is_deleted=true`; survivor exists. (Full FK manifest per CN-04.)
9. For an event where **both** contacts have a `participants` row, the merge deletes the loser's row (no `UNIQUE(event_id, contact_id)` violation), keeps the survivor's, and the survivor's `status` is the precedence-max of the two.
10. A `group_members` collision (both ids in one group) de-dupes (loser membership deleted, survivor kept) with no PK violation.
11. A `contact_reference` custom field on a third contact pointing at the loser is rewritten to the survivor after merge; a multi-value array already containing the survivor adds no duplicate; a loser self-reference is fixed up.
12. `POST /dedupe/merge` on a name-collision pair with `confirm_same_name=false` returns **409** and performs **no** writes (loser still active, no audit row); with `confirm_same_name=true` it succeeds. (Never-auto-merge enforced server-side.)
13. Exactly **one** `audit_log` row per successful merge: `action="contact_merge"`, `entity="contacts"`, `entity_id=survivor_id`, `before` containing the full loser snapshot, `after` containing the reassignment manifest; `GET /dedupe/merge/history` lists it.
14. Merging the same loser twice (second call after the first commits) returns **409** (`is_deleted`/`with_for_update` guard); no partial state.
15. A simulated mid-transaction DB error rolls back **everything**: loser is **not** `is_deleted`, no audit row, survivor unchanged (atomicity).
16. CompreFace network I/O occurs **only post-commit**; a forced CompreFace failure does **not** roll back or fail the merge (response carries a non-fatal `warnings` entry; DB is committed).
17. All dedupe routes return 401 unauthenticated; rule-set write routes return 403 for volunteer and viewer; candidate/merge routes return 403 for viewer.
18. A `test_manifest_covers_all_contact_fks` introspection test: every FK column pointing at `contacts.id` discovered via `Base.metadata` reflection (plus the `custom_data` contact_reference + `member:{id}` string paths) appears in the reassignment plan — fails loudly if a new sprint adds a contact FK that S11 missed.
19. After a successful merge, `member_status_service.recompute_contacts(db, [survivor_id])` is invoked (per CN-23); a test asserts it is called with the survivor id at the end of the merge transaction.
20. Frontend: `DuplicatesPage` renders candidates, shows the same-name chip, disables **Confirm merge** until the confirmation checkbox is ticked for a colliding pair; merge success shows a `sonner` toast and removes the loser from the contacts cache. `npm run build` + `npm run lint` + `npm run test:run` pass; `ruff check app` + full pytest pass.

---

## 8. Test plan

### Backend (pytest — `backend/tests/test_dedupe.py`)
Run config: `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test pytest tests/ -q`. Use existing `conftest.py` fixtures (shared engine + file SQLite + ≥32-char JWT + admin/volunteer/viewer auth). Helper factories create `contacts`, `events`, `participants`, `face_samples`, `activities`, `group_members`, `name_alias`, `name_match_review_queue`, `community_report`, `custom_field_def`, and a 3rd contact with a `contact_reference` in `custom_data`.

- `test_seed_default_rule_set_present` — Default set exists, `is_default=true`, threshold 70, 5 rules.
- `test_create_rule_set_rejects_unknown_field` — `field="nickname"` → 422.
- `test_create_rule_set_rejects_nonpositive_weight` / `test_create_rule_set_rejects_zero_threshold` — 422.
- `test_set_default_clears_other_defaults` — only one `is_default=true` after PUT.
- `test_delete_last_active_or_default_rejected` — 409.
- `test_candidates_email_match_qualifies` — same email → 1 pair, score≥100, `email` in matched_fields.
- `test_candidates_first_name_only_below_threshold` — shared first name only → no pair.
- `test_candidates_excludes_soft_deleted` — `is_deleted=true` never appears.
- `test_candidates_pair_unordered_and_deduped` — appears once via email AND phone blocks; no `(x,x)`.
- `test_candidates_same_name_flag` — collision → `same_name=true`; different last name → false.
- `test_candidates_ranked_and_capped` — sorted by score desc; `max_pairs` honored.
- `test_merge_preview_field_conflict_matrix` — `differs` correct; `external_id` preservation reflected.
- `test_merge_preview_reassignment_counts_match_sql` — manifest counts == direct SQL counts across all FK columns.
- `test_merge_preview_custom_reference_self_fixup` — loser's own contact_reference flagged.
- `test_merge_reassigns_all_fks` — post-merge loser FK counts are 0 across participants(non-collision)/face_samples/activities/name_alias/name_match_review_queue/community_report; loser `is_deleted`; survivor present.
- `test_merge_participant_collision_dedupes` — shared-event collision → loser row deleted, no UNIQUE violation, survivor status = precedence-max.
- `test_merge_group_member_collision_dedupes` — shared group membership de-duped.
- `test_merge_rewrites_external_contact_reference` — 3rd contact's "Invited By"=loser → survivor; multi-value array dedupes.
- `test_merge_blocks_same_name_without_confirm` — 409, zero writes (loser still active + no audit row).
- `test_merge_same_name_with_confirm_succeeds`.
- `test_merge_writes_single_audit_row` — exactly one row; `before` has loser snapshot; `after` has manifest; `entity_id==survivor_id`; `action="contact_merge"`.
- `test_merge_history_lists_merge` — `GET /dedupe/merge/history` returns it.
- `test_merge_idempotent_guard` — second merge of same loser → 409.
- `test_merge_rolls_back_on_error` — monkeypatch a mid-transaction failure → loser not deleted, no audit, survivor unchanged.
- `test_merge_compreface_cleanup_is_post_commit` — mock CompreFace to raise; merge succeeds; warning present; DB committed.
- `test_manifest_covers_all_contact_fks` — reflect `Base.metadata` for FKs to `contacts.id`; assert each is in the reassignment plan (the headline safety net).
- `test_dedupe_routes_authz` — parametrized: 401 unauth; 403 volunteer on rule-set writes; 403 viewer on candidates/merge.
- `test_merge_self_rejected` — `survivor_id==loser_id` → 422.

### Frontend (vitest — `npm run test:run`)
- `DuplicatesPage.test.tsx`: renders candidate list from a mocked `POST /dedupe/candidates` (score badge + matched-field chips); shows the same-name chip when `same_name=true`; Rules tab hidden for volunteer (mock `authStore.role='volunteer'`), shown for admin; empty state renders the `EmptyState` copy on `[]`.
- `MergeModal.test.tsx`: opening fetches preview (side-by-side fields + impact manifest); **Confirm merge** disabled until the same-name checkbox ticked when `same_name=true`, enabled immediately when false; swap survivor/loser re-fetches preview; successful merge fires the success toast + query invalidation; server `detail` surfaced on error.

---

## 9. Rollout / rollback / risks

**Rollout:** purely additive backend (one new table + one router) + one new frontend route. Deploy backend (runs the migration via the existing `alembic upgrade head` gate) then frontend. No data migration, no downtime. Operationally: after S06/S22 cutover, run the finder on the Default ruleset; review highest-score pairs first (runbook step §6).

**Rollback:** remove the `/duplicates` route (API becomes unused); revert the router include; `alembic downgrade -1` drops `dedupe_rule_set` (leaves shared `audit_log` intact). **Already-executed merges are NOT auto-reversed** by a code rollback — a merged loser stays soft-deleted; the `audit_log` row is the manual recovery path (and the basis for a future unmerge sprint). State this explicitly in the runbook.

**Risks:**
- **Incomplete FK enumeration (headline risk, framework.md §3.5):** a missed `contacts.id` FK leaves a table pointing at a soft-deleted loser → orphaned/invisible data. *Mitigation:* `test_manifest_covers_all_contact_fks` introspects `Base.metadata` and fails if any FK column is absent from the plan; the §4.C table is the explicit, sprint-attributed source of truth and must be reconciled against the real constraint names at implementation. Single most important safety net.
- **Mid-transaction CompreFace coupling:** kept entirely post-commit so network I/O never poisons the DB transaction (matches the existing worker-side CompreFace pattern, `backend.md §C/§E`).
- **Aggressive rules → bad merges:** humans review every pair; never-auto-merge is server-enforced; the audit row enables recovery; the Default threshold is conservative (email-or-fullname).
- **Performance at future scale:** blocking bounds comparisons; documented O() shape; `max_pairs` cap; move to background (S16/S17) if contacts exceed ~50k.
- **Concurrent merges:** `with_for_update()` row locks + `is_deleted` guard (mirrors the dual-approval pattern, `models.py:170-184` / `AGENTS.md`).

---

## 10. Open questions & pending owner artifacts / cross-sprint reconciliation

1. **`audit_log` ownership = S01.** This spec consumes it (per `docs/specs/S08-biometric-consent-and-rtbf.md:76`). The master doc must confirm S01 creates the table + model so no sprint declares `AuditLog` twice or migrates it twice. (S03/S08/S11/S12 all consume it.)
2. **Full contact-FK enumeration is cross-sprint.** §4.C lists FKs owned by S01, S07, S08, S09, S12, **and S22** (`name_alias.contact_id`, `name_match_review_queue.resolved_contact_id`, `community_report.submitted_by_contact_id`). **S22 has no spec file yet** — the master doc must ensure those columns exist with these exact names, and that S11's reassignment plan is updated in lockstep when S22 lands. The `test_manifest_covers_all_contact_fks` reflection test is the enforcement mechanism.
3. **Detection→contact linkage.** Does S07 add a real `Detection.contact_id` FK, or keep the `member:{id}` synthetic string in `matched_name` / `Log.matched_name` (`face_pipeline.py:110`, `models.py:103,217`)? The reassignment strategy differs (FK UPDATE vs string rewrite). Reconcile with S07.
4. **Shared normalization helper.** S06 (migration normalization "DEPARO/Deparo"), S10 (import dedupe-match), and **S22** (name matching) all normalize names/options. The master doc should designate one canonical `services/normalize.py`; S11's blocking-key/scoring `_norm()` must reuse it rather than fork.
5. **Dedup-match reuse by S10 / S22.** S10's import wizard and S22's name matcher both need "does this incoming row/name match an existing contact?" — the same scoring core. The master doc should record that S10/S22 import `dedupe_service._score_pair` / `find_candidates` rather than reimplement, to keep one matching definition (while S22 layers Filipino-aware nickname/alias logic on top).
6. **Phonetic/Filipino-aware matching dependency.** v1 uses stdlib `difflib` only (no new dependency, no `pip-audit` delta). If the owner wants nickname/phonetic matching ("Bob"↔"Robert", Tagalog spelling variants) inside *dedupe scoring* (vs. only in the S22 matcher), a future iteration may add `jellyfish`/`rapidfuzz` — owner decision + `pip-audit` review required.
7. **`contacts.merged_into_id` redirect column.** Should the master model add a nullable `contacts.merged_into_id FK→contacts.id` so a stale loser URL redirects to its survivor (and to make unmerge cleaner)? Out of scope here to avoid widening `contacts` ownership outside S01/S03; needs an S01/S03 decision.
8. **Unmerge.** Confirm with the owner that merge reversal is a *later* sprint (the audit row is built to enable it). Not in S11.
9. **Cross-`contact_type` merge policy.** S11 **warns** but allows (with confirm) merging across contact types (e.g. Individual↔Organization). Confirm the owner wants this permitted rather than hard-blocked.
10. **RBAC sequencing.** S11 relies on `require_volunteer` already excluding the `viewer` role (`dependencies.py:59`). The `viewer` enum value + `require_viewer` guard are owned by S15. If S15 hasn't landed, viewers simply don't exist yet and the guards still behave correctly; confirm S15 keeps `require_volunteer` = `{admin, volunteer}` semantics.

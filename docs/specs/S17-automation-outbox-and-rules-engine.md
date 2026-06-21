# S17 — Automation Core: Outbox + Visual Rules Engine
**Phase:** F — Automation · **Depends on:** S12 (outbox stub + activities), S16 (APScheduler host + `job_runs`) · **Effort:** XL · **Status:** Not started

---

## 1. Goal & rationale

The old CiviCRM push pipeline (`Attendance.push_status` + `_process_civicrm_push` in `queue_manager.py`) gave the app at-least-once delivery via a retry/dead-letter state machine on the attendance table. That pipeline is deleted in S01. S17 generalises the **pattern** — not the destination — into a reusable `outbox` table and a declarative **ECA (Event-Condition-Action) rules engine** that drives every future integration: Google Chat notifications, Gmail sends, activity creation, group membership updates, field updates.

Without this sprint nothing in Phase F (S18 integrations, S19 API) can ship. With it:

- Every outbound side-effect is written **in the same DB transaction** as the state change (transactional outbox), surviving process restarts.
- Admins can configure "when X happens, if Y, do Z after N minutes" without code deploys, via a purpose-built UI.
- S12's stub `outbox` rows and S13's `newcomer.created` rows light up automatically.
- S18 (Google Chat / Gmail / Zoom) wires additional action handlers into the same consumer.

The rules engine is deliberately **data, not a DSL**. Conditions are evaluated by a whitelist of Python comparison functions over a typed event payload dict — no expression language, no `eval()`, no sandboxed scripts. This matches the "safe declarative ECA" hard-problem from `framework.md §3.6`.

---

## 2. Scope (In / Out)

### In scope
- **`outbox` table** — created here (owner S17; S12/S13's idempotent-guard stubs become no-ops). Full model + migration + `Outbox` SQLAlchemy model in `models.py`.
- **`automation_rules` table** — declarative ECA rule definitions: trigger name, conditions JSON array, actions JSON array, delay\_minutes, rate\_cap, dry\_run flag.
- **`rule_executions` table** — per-rule per-event execution log (idempotency key, status, dry-run output), enabling loop protection and per-rule rate-cap enforcement.
- **`OutboxWorker`** in `services/queue_manager.py` — a new job slot (`_process_outbox`) drained each worker cycle; at-least-once delivery with configurable max-attempts, exponential jitter backoff, dead-letter terminal state. Registered in `QueueManager.run`.
- **`AutomationEngine` service** (`services/automation_engine.py`) — `emit(event_type, payload, db)` entry point: load matching active rules, evaluate conditions, fan-out to action handlers (inline for immediate, outbox-insert for delayed / external).
- **Whitelist trigger vocabulary** (initial set): `contact.created`, `contact.updated`, `contact.tier_changed`, `participant.added`, `participant.source_changed`, `activity.created`, `activity.due_reminder`, `review_item.unmatched`, `community_report.submitted`, `newcomer.created`.
- **Whitelist action vocabulary** (initial set): `google_chat.send` (S18 provides the HTTP client; S17 writes the outbox row), `gmail.send` (ditto), `activity.create` (inline, writes `activities` row), `contact.add_to_group` (inline, writes `group_members`), `contact.set_field` (inline, updates `contacts.custom_data` JSONB key), `contact.set_tag` (alias for set\_field on a multiselect key), `outbox.enqueue` (meta-action: write another outbox row for S18 to pick up).
- **Condition evaluator** — pure Python, no DSL. Whitelist of operator keys: `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `contains`, `startswith`, `is_null`, `is_not_null`. Operand is a dotted path into the trigger payload dict (e.g. `"contact.tier"`, `"participant.source"`).
- **Loop protection** — per-rule rate cap (`rate_cap_per_entity_minutes`): before firing, check `rule_executions` for a recent run on the same `(rule_id, entity_id)` pair; skip if within the window. Also a global circuit-breaker: if a rule fires > N times in a 1-min window, disable it (`is_active = false`) and emit a `rule.circuit_broken` audit entry.
- **Dry-run mode** — if `automation_rules.dry_run = true`, evaluate conditions and log what *would* happen to `rule_executions.dry_run_log` JSONB but write no side-effects. Admin UI exposes a "Test now" trigger.
- **Idempotency key** on `outbox` (`idempotency_key` UNIQUE, nullable) — callers that know a unique key (e.g. `activity_reminder:{activity_id}`) prevent duplicate enqueues even under worker crash/retry.
- **`run_at` scheduling** — outbox rows with `run_at > now()` are not picked up by the worker until their time arrives. Delayed actions set `run_at = now() + rule.delay_minutes`.
- **Admin UI — Rules Engine page** (`/settings/automation`): list rules (table), create/edit rule (multi-step form: trigger selector → condition builder → action builder → schedule/rate/dry-run options), enable/disable toggle, "Test now" dry-run button, per-rule execution log viewer.
- **Admin UI — Outbox Monitor** (`/settings/automation/outbox`): list pending/failed/dead-letter rows, retry dead-letter, view payload/last\_error.
- **`emit()` call-sites wired in this sprint**: `contact.created` (contacts router, post-create), `participant.added` (participants router, post-bulk-add), `activity.due_reminder` (S12 scanner now calls `AutomationEngine.emit` instead of raw outbox insert). Additional emitters wired in S18.
- Role gating: admin-only for all `/settings/automation*` endpoints and mutation ops; viewer/volunteer get no access.
- Backend pytest: outbox worker, condition evaluator, rate-cap guard, dry-run, idempotency, loop-protection, action inline handlers.
- Frontend vitest: rule form serialisation/validation, condition-builder state, action-builder state.

### Out of scope
- Actual HTTP send to Google Chat or Gmail — that is S18's action handler. S17 writes the outbox row; S18 adds the `handle_google_chat` / `handle_gmail` functions and registers them.
- Zoom attendance pull (S18).
- API-key-scoped webhook triggers (S19).
- Rule versioning / rollback (future).
- Multi-step case workflows (deferred per decisions.md Round 4).
- Rule import/export UI.
- Visual graph/flow-chart representation (the builder is a step-by-step form, not a canvas).
- Action type `sms.send` (not in scope for this church deployment).
- Custom-field `data_type=formula` computed at rule evaluation time (deferred).

---

## 3. Data model changes

### 3.1 `outbox` (owner S17 — replaces stub guards from S12/S13)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK | no | identity | |
| `event_type` | String(100) | no | — | e.g. `activity.due_reminder`, `google_chat.send`, `gmail.send` |
| `payload` | JSONB | no | `{}` | serialised trigger context or action payload |
| `status` | String(20) | no | `'pending'` | `pending \| processing \| sent \| failed \| dead_letter` |
| `attempts` | Integer | no | `0` | incremented on each worker pick-up attempt |
| `last_error` | Text | yes | NULL | last exception or error message |
| `run_at` | DateTime (naive UTC) | no | `utc_now()` | not picked up before this timestamp |
| `idempotency_key` | String(255) | yes | NULL | UNIQUE nullable; callers pass to prevent re-enqueue |
| `rule_id` | Integer FK → `automation_rules.id` ON DELETE SET NULL | yes | NULL | backlink for execution tracking |
| `created_at` | DateTime (naive UTC) | no | `utc_now()` | |

**Indexes:**
- `ix_outbox_status_run_at` on `(status, run_at)` — the worker's primary scan.
- `ix_outbox_rule_id` on `(rule_id)` — rule execution lookups.
- `uq_outbox_idempotency_key` UNIQUE on `(idempotency_key)` WHERE `idempotency_key IS NOT NULL` (partial; Postgres-only; SQLite: full UNIQUE).

**`status` state machine:**
```
pending → processing → sent (terminal success)
                    → failed (attempts < MAX; retried)
                    → dead_letter (terminal failure; attempts >= MAX)
```
`processing` is set at pick-up start to prevent double-processing; rolled back to `failed` on exception.

**Outbox MAX\_ATTEMPTS**: configurable via `admin_settings` key `outbox_max_attempts` (default `5`).

**Backoff**: attempt N uses `min(2^N * base_delay_seconds, 3600)` seconds jitter (`base_delay` default `30s`, also in `admin_settings`). `run_at` is set forward on each failure so delayed rows are not immediately retried.

### 3.2 `automation_rules`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK | no | identity | |
| `name` | String(255) | no | — | human label; UNIQUE |
| `description` | Text | yes | NULL | |
| `trigger` | String(100) | no | — | one of the whitelisted trigger names |
| `conditions` | JSONB | no | `[]` | list of condition objects `{field, operator, value}` |
| `actions` | JSONB | no | `[]` | list of action objects `{action_type, params}` |
| `delay_minutes` | Integer | no | `0` | delay before executing actions (0 = immediate) |
| `rate_cap_per_entity_minutes` | Integer | no | `0` | 0 = no rate cap; else per `(rule_id, entity_id)` |
| `max_entities_per_hour` | Integer | no | `0` | 0 = no global cap; circuit-breaker threshold |
| `is_active` | Boolean | no | `true` | soft-disable without deleting |
| `dry_run` | Boolean | no | `false` | evaluate + log without side effects |
| `created_by_id` | Integer FK → `users.id` ON DELETE SET NULL | yes | NULL | |
| `updated_by_id` | Integer FK → `users.id` ON DELETE SET NULL | yes | NULL | |
| `created_at` | DateTime (naive UTC) | no | `utc_now()` | |
| `updated_at` | DateTime (naive UTC) | no | `utc_now()` (`onupdate=utc_now`) | |

**Indexes:**
- `ix_automation_rules_trigger_active` on `(trigger, is_active)` — `emit()` hot path.
- UNIQUE constraint `uq_automation_rules_name` on `(name)`.

### 3.3 `rule_executions`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK | no | identity | |
| `rule_id` | Integer FK → `automation_rules.id` ON DELETE CASCADE | no | — | |
| `entity_id` | Integer | yes | NULL | the primary entity id from the trigger payload (e.g. contact.id, participant.id) |
| `trigger_payload` | JSONB | no | `{}` | snapshot of event payload at time of fire |
| `status` | String(20) | no | — | `fired \| skipped_rate_cap \| skipped_conditions \| dry_run \| circuit_broken` |
| `dry_run_log` | JSONB | yes | NULL | what would have happened (dry-run only) |
| `actions_taken` | JSONB | yes | NULL | list of action results for `fired` rows |
| `outbox_ids` | JSONB | yes | NULL | list of outbox row ids enqueued (for tracing) |
| `created_at` | DateTime (naive UTC) | no | `utc_now()` | |

**Indexes:**
- `ix_rule_executions_rule_entity` on `(rule_id, entity_id, created_at)` — rate-cap window query.
- `ix_rule_executions_rule_created` on `(rule_id, created_at)` — circuit-breaker count query.

### 3.4 Alembic migration plan

One new migration: `backend/alembic/versions/<rev>_add_automation_outbox.py`.

**`down_revision`**: the current alembic head at implementation time (do not hardcode — run `alembic heads` at PR time and set it).

**`upgrade()` steps (order matters for FKs):**

1. `op.create_table('automation_rules', ...)` — no FKs to tables that don't exist yet.
2. `op.create_table('outbox', ...)` — FK to `automation_rules.id` (ON DELETE SET NULL, so both tables exist first).
3. `op.create_table('rule_executions', ...)` — FK to `automation_rules.id`.
4. Create all indexes listed in §3.1–§3.3.
5. For `outbox.idempotency_key` partial unique (Postgres-only):

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert
from alembic import op
import sqlalchemy as sa

bind = op.get_bind()
if bind.dialect.name == "postgresql":
    op.create_index(
        "uq_outbox_idempotency_key",
        "outbox",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
else:
    op.create_index(
        "uq_outbox_idempotency_key",
        "outbox",
        ["idempotency_key"],
        unique=True,
    )
```

6. **Idempotency check for S12/S13 stubs:** If S12 or S13 already created an `outbox` table with fewer columns (their has\_table guard), this migration must detect the old shape and `op.add_column` the missing `idempotency_key` and `rule_id` columns rather than `create_table`. Guard:

```python
from sqlalchemy import inspect as sa_inspect
insp = sa_inspect(bind)
if not insp.has_table("outbox"):
    op.create_table("outbox", ...)  # full create
else:
    existing_cols = {c["name"] for c in insp.get_columns("outbox")}
    if "idempotency_key" not in existing_cols:
        op.add_column("outbox", sa.Column("idempotency_key", sa.String(255), nullable=True))
        # ... add other new cols
```

**`downgrade()` steps (reverse order):**

1. Drop `rule_executions` indexes, then `op.drop_table('rule_executions')`.
2. Drop `outbox` indexes + partial unique, then `op.drop_table('outbox')`.
3. Drop `automation_rules` indexes, then `op.drop_table('automation_rules')`.

**JSONB convention:** all JSONB columns in models use the project alias `JSONB = JSON().with_variant(_PG_JSONB, "postgresql")` (declared at `models.py:22`). In the migration use `sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')`.

**Backfill:** S12's stub `outbox` rows (event\_type=`activity.due_reminder`) already have the right schema shape and are valid; no backfill needed.

---

## 4. Backend

### 4.1 Endpoints

| METHOD | Path | Role | Request body / params | Response | Notes |
|---|---|---|---|---|---|
| GET | `/automation/rules` | admin | `?page&page_size&trigger&is_active` | `PaginatedRuleResponse` | List all rules; filter by trigger / active. |
| POST | `/automation/rules` | admin | `RuleCreate` | `RuleResponse` | Create rule; validates trigger + conditions + actions against whitelists. 400 on unknown trigger/action. |
| GET | `/automation/rules/{id}` | admin | — | `RuleResponse` | Single rule detail. |
| PUT | `/automation/rules/{id}` | admin | `RuleUpdate` | `RuleResponse` | Full update; `updated_by_id` stamped. |
| PATCH | `/automation/rules/{id}/toggle` | admin | — | `RuleResponse` | Toggle `is_active`. Returns updated rule. |
| DELETE | `/automation/rules/{id}` | admin | — | `{deleted: true}` | Soft-delete is not modelled; hard-delete. Cascades to `rule_executions`. |
| POST | `/automation/rules/{id}/test` | admin | `{entity_id?: int, sample_payload?: dict}` | `DryRunResult` | Evaluates rule against provided or synthetic payload; never writes side-effects regardless of `dry_run` flag. Returns `{would_fire: bool, conditions_result: [{...}], actions_would_take: [...]}`. |
| GET | `/automation/rules/{id}/executions` | admin | `?page&page_size&status` | `PaginatedExecutionResponse` | Execution history for a rule. |
| GET | `/automation/outbox` | admin | `?status&event_type&page&page_size` | `PaginatedOutboxResponse` | List outbox rows with filters. |
| GET | `/automation/outbox/{id}` | admin | — | `OutboxItemResponse` | Single row with full payload + last\_error. |
| POST | `/automation/outbox/{id}/retry` | admin | — | `OutboxItemResponse` | Reset `status=pending`, `attempts=0`, `last_error=null`, `run_at=utc_now()`. 400 if not in `failed` or `dead_letter`. |
| DELETE | `/automation/outbox/{id}` | admin | — | `{deleted: true}` | Purge a dead-letter row. 400 if status is not `dead_letter`. |

**Router file:** `backend/app/routers/automation.py` (new). Prefix `/automation`. Registered in `main.py` with `dependencies=[Depends(check_setup_complete)]`.

**Schema file additions** (to `backend/app/schemas.py` or new `backend/app/schemas/automation.py`):

- `ConditionSchema`: `{field: str, operator: str, value: Any}` — validated against `WHITELISTED_OPERATORS`.
- `ActionSchema`: `{action_type: str, params: dict}` — validated against `WHITELISTED_ACTIONS`.
- `RuleCreate`: `name, description?, trigger, conditions: list[ConditionSchema], actions: list[ActionSchema], delay_minutes=0, rate_cap_per_entity_minutes=0, max_entities_per_hour=0, dry_run=false`.
- `RuleUpdate`: same fields, all optional (partial update).
- `RuleResponse`: all `automation_rules` columns + `execution_count` (recent 7 days, a subquery or annotation).
- `PaginatedRuleResponse`: `{items: list[RuleResponse], total: int, page: int, page_size: int}`.
- `OutboxItemResponse`: all `outbox` columns; `payload` as `dict`.
- `PaginatedOutboxResponse`: paginated list of `OutboxItemResponse`.
- `DryRunResult`: `{would_fire: bool, conditions_result: list[dict], actions_would_take: list[dict], rate_capped: bool}`.
- `ExecutionResponse`: all `rule_executions` columns.
- `PaginatedExecutionResponse`: paginated.

### 4.2 `AutomationEngine` service

**File:** `backend/app/services/automation_engine.py` (new).

```python
# Key public surface — type hints on all public functions (convention)

WHITELISTED_TRIGGERS: set[str] = {
    "contact.created",
    "contact.updated",
    "contact.tier_changed",
    "participant.added",
    "participant.source_changed",
    "activity.created",
    "activity.due_reminder",
    "review_item.unmatched",
    "community_report.submitted",
    "newcomer.created",
}

WHITELISTED_OPERATORS: set[str] = {
    "eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte",
    "contains", "startswith", "is_null", "is_not_null",
}

WHITELISTED_ACTIONS: set[str] = {
    "google_chat.send",
    "gmail.send",
    "activity.create",
    "contact.add_to_group",
    "contact.set_field",
    "contact.set_tag",
    "outbox.enqueue",
}

async def emit(
    event_type: str,
    payload: dict,
    db: AsyncSession,
    entity_id: int | None = None,
) -> list[int]:
    """
    Load all active rules matching event_type. Evaluate conditions. Fire actions.
    Returns list of outbox row ids created (empty for inline-only or dry-run).
    Must be called INSIDE an open DB transaction so outbox inserts are atomic
    with the triggering state change.
    """

def _resolve_field(payload: dict, dotted_path: str) -> Any:
    """Walk dotted path into nested dict. Returns None if path is absent."""

def _evaluate_condition(condition: dict, payload: dict) -> bool:
    """Single condition: {field, operator, value}. Raises ValueError on unknown operator."""

def _evaluate_conditions(conditions: list[dict], payload: dict) -> bool:
    """AND-conjunction over all conditions. Empty list = always True."""

def _check_rate_cap(
    rule: AutomationRule,
    entity_id: int | None,
    db: AsyncSession,
) -> bool:
    """Returns True if rate cap allows firing (or no cap configured)."""

def _check_circuit_breaker(rule: AutomationRule, db: AsyncSession) -> bool:
    """Returns True if firing is allowed. Disables rule if threshold exceeded."""

async def _fire_rule(
    rule: AutomationRule,
    payload: dict,
    entity_id: int | None,
    db: AsyncSession,
    dry_run: bool = False,
) -> ExecutionRecord:
    """
    Execute all actions for one rule. For delayed actions: INSERT outbox row
    with run_at = now() + delay_minutes. For immediate inline actions: execute
    directly and record result. Returns an ExecutionRecord for the caller to
    INSERT.
    """

async def _execute_inline_action(
    action_type: str,
    params: dict,
    payload: dict,
    entity_id: int | None,
    db: AsyncSession,
) -> dict:
    """Execute a non-outbox action inline. Returns result dict for logging."""
```

**Inline action implementations** (in `automation_engine.py` or `services/automation_actions.py` if large):

- `activity.create`: builds `ActivityCreate` from params (supports template vars `{{contact.first_name}}` resolved against payload), calls the shared `ActivityService.create_activity(...)`.
- `contact.add_to_group`: validates `group_id` in params, inserts `GroupMember(group_id, contact_id)` with `ON CONFLICT DO NOTHING`.
- `contact.set_field`: validates field name exists in `custom_field_def` (loads from DB), updates `contacts.custom_data[field_name] = value` using a JSONB merge update (`UPDATE contacts SET custom_data = custom_data || :patch WHERE id = :id`).
- `contact.set_tag`: alias for `contact.set_field` on a `multiselect` type; value is appended to the existing list rather than overwriting.
- `outbox.enqueue`: inserts another outbox row (allows chaining to S18 handlers).
- `google_chat.send`, `gmail.send`: insert outbox rows with `event_type = 'google_chat.send'` / `'gmail.send'`; S18 registers the consumer for these types.

**Template variable resolution** in action params: `{{payload.field.subfield}}` syntax, resolved via `_resolve_field`. Only payload fields — no arbitrary Python. Unknown vars render as `""`.

**Audit:** For each rule fired (not dry-run), call `audit.record(actor_id=None, action="rule.fired", entity="automation_rule", entity_id=rule.id, before=None, after={"rule_execution_id": ..., "entity_id": ...}, db=db)`. Circuit-break events use `action="rule.circuit_broken"`.

### 4.3 `_process_outbox` worker job

**File:** `backend/app/services/queue_manager.py` — add new job method; register in `QueueManager.run` (line 38 area, alongside existing `_process_enrollment` etc.).

```python
MAX_OUTBOX_ATTEMPTS: int  # read from dynamic_settings.get_int("outbox_max_attempts", 5)

async def _process_outbox(self) -> bool:
    """
    Drain pending outbox rows whose run_at <= now().
    At-least-once delivery: set status=processing, call handler, set sent/failed/dead_letter.
    Returns True if any row was processed.
    """
```

**Pick-up query:**
```sql
SELECT * FROM outbox
WHERE status IN ('pending', 'failed')
  AND run_at <= now()
  AND attempts < :max_attempts
ORDER BY run_at ASC
LIMIT 20
FOR UPDATE SKIP LOCKED  -- Postgres only; SQLite: omit SKIP LOCKED
```

`SKIP LOCKED` prevents two worker instances from processing the same row. On SQLite (tests) omit `SKIP LOCKED` (the test suite is single-threaded).

**Per-row logic:**
1. Set `status = 'processing'`; commit (so other workers skip it).
2. Call the registered handler for `row.event_type` (handler registry is a `dict[str, Callable]` in `queue_manager.py`; S18 registers `google_chat.send` / `gmail.send` handlers at startup).
3. On success: `status = 'sent'`.
4. On exception: `attempts += 1`, `last_error = str(exc)`. If `attempts >= MAX_OUTBOX_ATTEMPTS`: `status = 'dead_letter'`. Else: `status = 'failed'`, `run_at = now() + backoff(attempts)`.
5. Commit after every row (not batch-commit) so partial batches survive crashes.

**Handler registry pattern:**
```python
# In queue_manager.py
_outbox_handlers: dict[str, Callable] = {}

def register_outbox_handler(event_type: str, handler: Callable) -> None:
    """S18 calls this at app startup to register google_chat.send etc."""
    _outbox_handlers[event_type] = handler
```

Handler signature: `async def handler(payload: dict, db: AsyncSession) -> None` — raises on failure.

**Backoff formula:**
```python
import random
def backoff(attempts: int, base: int = 30) -> timedelta:
    delay = min(base * (2 ** attempts), 3600)
    jitter = random.uniform(0, delay * 0.1)
    return timedelta(seconds=delay + jitter)
```

### 4.4 `emit()` call-sites wired in S17

**`backend/app/routers/contacts.py`** (post-create, from S03):
```python
# After db.commit() inside POST /contacts (or /members):
await automation_engine.emit("contact.created", {
    "contact": {"id": contact.id, "first_name": contact.first_name,
                 "last_name": contact.last_name, "tier": contact.tier,
                 "contact_subtype": contact.contact_subtype},
}, db=db, entity_id=contact.id)
```

**`backend/app/routers/participants.py`** (post-bulk-add, from S05):
```python
# After bulk insert inside POST /events/{event_id}/participants/bulk:
for p in created_participants:
    await automation_engine.emit("participant.added", {
        "participant": {"contact_id": p.contact_id, "event_id": p.event_id,
                        "status": p.status, "source": p.source},
    }, db=db, entity_id=p.contact_id)
```

**`backend/app/services/activity_service.py`** (`scan_due_reminders`, from S12):
Replace the raw outbox INSERT stub with:
```python
await automation_engine.emit("activity.due_reminder", {
    "activity": {"id": activity.id, "subject": activity.subject,
                 "due_date": activity.due_date.isoformat(),
                 "assignee_user_id": activity.assignee_user_id},
}, db=db, entity_id=activity.id)
```

(The `idempotency_key` for activity reminders: `f"activity_reminder:{activity.id}"` — pass as a keyword arg to `emit()`, which forwards it when inserting outbox rows.)

**`emit()` idempotency key passthrough:**
```python
async def emit(
    event_type: str,
    payload: dict,
    db: AsyncSession,
    entity_id: int | None = None,
    idempotency_key: str | None = None,
) -> list[int]:
    ...
    # When inserting outbox rows for delayed actions, pass idempotency_key through
    # to the INSERT; on UNIQUE conflict, silently skip (ON CONFLICT DO NOTHING).
```

### 4.5 File-by-file plan

**New files:**
- `backend/app/routers/automation.py` — endpoints for rules CRUD, test, outbox monitor.
- `backend/app/services/automation_engine.py` — `AutomationEngine`, `emit()`, condition evaluator, inline action handlers, circuit breaker.
- `backend/alembic/versions/<rev>_add_automation_outbox.py` — migration.

**Modified files:**
- `backend/app/models.py` — add `Outbox`, `AutomationRule`, `RuleExecution` models. Remove any stub `Outbox` model if S12/S13 added one (coordinate; prefer a single definition).
- `backend/app/schemas.py` (or new `schemas/automation.py`) — add all automation schemas listed in §4.1.
- `backend/app/main.py` — import and register `automation.router` with `dependencies=[Depends(check_setup_complete)]`.
- `backend/app/services/queue_manager.py` — add `_process_outbox` method, `MAX_OUTBOX_ATTEMPTS`, `_outbox_handlers` registry, `register_outbox_handler`. Call `_process_outbox()` in `QueueManager.run` loop.
- `backend/app/config.py` — add `DynamicSettings.get_outbox_max_attempts() -> int` and `get_outbox_base_delay_seconds() -> int` helpers (reads from `admin_settings`; defaults 5 and 30).
- `backend/app/routers/contacts.py` (S03 file) — add `emit("contact.created", ...)` call post-create.
- `backend/app/routers/participants.py` (S05 file) — add `emit("participant.added", ...)` call post-bulk-add.
- `backend/app/services/activity_service.py` (S12 file) — replace raw outbox INSERT with `emit("activity.due_reminder", ...)`.

**No deletions** in this sprint (CiviCRM excision was S01).

---

## 5. Frontend

### 5.1 Pages and routes (new)

**`/settings/automation`** — Rules Engine admin page (admin-only, `AdminRoute`).

Sub-views (handled within the page via tab/section state, not separate routes):
- **Rules list** — full-width table of all rules (name, trigger, conditions count, actions count, is\_active badge, last fired, actions: edit/toggle/delete).
- **Create / Edit rule** — slide-over or full-page form (see §5.2 builder).
- **Rule detail + execution log** — inline expansion or side panel showing recent `rule_executions` rows.

**`/settings/automation/outbox`** — Outbox Monitor (admin-only, `AdminRoute`).

### 5.2 Components (new)

**`frontend/src/pages/AutomationPage.tsx`** — the rules list + create/edit trigger. Uses `AdminRoute`. TanStack Query key `['automation', 'rules']` with filter params.

**`frontend/src/pages/AutomationOutboxPage.tsx`** — outbox monitor. TanStack Query key `['automation', 'outbox']`. Polling `refetchInterval: 10_000` (10s) when any row is `pending|processing|failed`. Columns: id, event\_type, status badge, attempts, run\_at, created\_at. Row expand shows payload JSON (formatted, not raw string) and last\_error.

**`frontend/src/components/automation/RuleForm.tsx`** — multi-step rule builder:

Step 1 — **Trigger selector**: dropdown of whitelisted triggers (grouped: Contact / Participant / Activity / System). Shows description of what data the payload will contain.

Step 2 — **Condition builder**: a dynamic list of condition rows, each with:
- Field picker: a dropdown of known payload fields for the selected trigger (defined in a `TRIGGER_PAYLOAD_FIELDS` constant, e.g. `contact.created → [{path: "contact.tier", label: "Tier", type: "string"}, ...]`).
- Operator dropdown: filtered by field type (`eq`/`ne`/`in`/`not_in` for strings; `gt`/`gte`/`lt`/`lte` additionally for numbers; `is_null`/`is_not_null` for all).
- Value input: text for scalar, tag-input (comma-separated chips) for `in`/`not_in`.
- "Add condition" and "Remove" buttons. Empty conditions list is valid (always-fire).
- Conditions are AND-joined (label: "All conditions must match").

Step 3 — **Action builder**: an ordered list of action blocks, each with:
- Action type picker: dropdown of whitelisted action types (grouped).
- Dynamic params form per action type:
  - `google_chat.send`: textarea for `message` (with `{{payload.field}}` hint).
  - `gmail.send`: `to_email` (template var or literal), `subject`, `body` textarea.
  - `activity.create`: `activity_type` select (seeded list), `subject` input (template), `assignee_user_id` picker (calls `GET /auth/users` for the list), `due_date_offset_days` number.
  - `contact.add_to_group`: `group_id` picker (calls `GET /groups`).
  - `contact.set_field`: field name input + value input.
  - `outbox.enqueue`: advanced; `event_type` input + `payload_template` JSON textarea.
- "Add action" and reorder (drag-and-drop optional; up/down arrows are sufficient).

Step 4 — **Schedule and limits**:
- `delay_minutes`: number input (0 = immediate; help text "Actions fire N minutes after the trigger").
- `rate_cap_per_entity_minutes`: number input (0 = no cap).
- `max_entities_per_hour`: number input (0 = no cap).
- `dry_run`: toggle switch.
- "Enabled" toggle (is\_active).

Step 5 — **Review and save**: summary of trigger + conditions + actions + settings. Save button (POST or PUT). "Test now" button (calls `POST /automation/rules/{id}/test`; shows `DryRunResult` in a modal).

**`frontend/src/components/automation/ConditionBuilder.tsx`** — extracts the condition list from `RuleForm`. Props: `conditions: ConditionSchema[], trigger: string, onChange`. Manages local state; calls `onChange` on every edit.

**`frontend/src/components/automation/ActionBuilder.tsx`** — extracts the action list. Props: `actions: ActionSchema[], onChange`.

**`frontend/src/components/automation/RuleExecutionLog.tsx`** — table of `RuleExecution` rows for a given rule. Shows status badge, entity\_id, created\_at, dry\_run\_log/actions\_taken (collapsible JSON viewer).

**`frontend/src/components/automation/OutboxRow.tsx`** — single outbox row with expandable payload/error panel.

**`frontend/src/services/automationApi.ts`** (new):
```typescript
export const automationApi = {
  listRules: (params) => api.get('/automation/rules', { params }),
  createRule: (data) => api.post('/automation/rules', data),
  getRule: (id) => api.get(`/automation/rules/${id}`),
  updateRule: (id, data) => api.put(`/automation/rules/${id}`, data),
  toggleRule: (id) => api.patch(`/automation/rules/${id}/toggle`),
  deleteRule: (id) => api.delete(`/automation/rules/${id}`),
  testRule: (id, payload) => api.post(`/automation/rules/${id}/test`, payload),
  getRuleExecutions: (id, params) => api.get(`/automation/rules/${id}/executions`, { params }),
  listOutbox: (params) => api.get('/automation/outbox', { params }),
  retryOutbox: (id) => api.post(`/automation/outbox/${id}/retry`),
  deleteOutboxItem: (id) => api.delete(`/automation/outbox/${id}`),
};
```

**`frontend/src/types/automation.ts`** (new): TypeScript interfaces mirroring all Pydantic schemas above.

### 5.3 TanStack Query keys (canonical)

```typescript
['automation', 'rules']               // list (paginated, with filter params)
['automation', 'rules', id]           // single rule detail
['automation', 'rules', id, 'executions'] // execution history
['automation', 'outbox']              // outbox list (with filter params)
['automation', 'outbox', id]          // single outbox item
```

### 5.4 Zustand

No new Zustand slices. Automation is fully server-state (TanStack Query). Form state is local `useState`.

### 5.5 Role gating

- All `/settings/automation` and `/settings/automation/outbox` routes are wrapped in `AdminRoute`.
- `BottomNav` admin "More" sheet gains an "Automation" item (icon: zap/bolt) linking to `/settings/automation`.
- Viewers and volunteers receive 403 from the backend and see nothing in the nav.

### 5.6 UX: loading / empty / error states

- List views use `LoadingState` / `EmptyState` / `ErrorState` from `components/ui/StateViews.tsx`.
- Rule list empty state: "No automation rules yet. Click 'New rule' to get started."
- Outbox empty state (no pending/failed rows): "Outbox is clear."
- Deleting a rule: `ConfirmDialog` ("This will permanently delete the rule and its execution history.").
- Toggle disable: `ConfirmDialog` ("Disable this rule? It will stop firing until re-enabled.").
- "Test now" result modal: shows `DryRunResult` with green/red condition chips and the would-take action list.
- Dead-letter outbox rows highlighted with `bg-destructive/10 border-destructive` row styling.
- Payload JSON viewer: `<pre className="text-xs font-mono bg-card p-2 rounded overflow-auto max-h-64">`.

### 5.7 Design tokens and mobile-first

- All colors via tokens: `bg-card`, `text-foreground`, `border-border`, `bg-primary`, `text-primary-foreground`, `bg-destructive`, `text-destructive-foreground`, `bg-muted`.
- Status badges: `StatusBadge` component (S03) with mapping `{pending: 'muted', sent: 'success', failed: 'warning', dead_letter: 'destructive', dry_run: 'secondary'}`.
- Mobile-first: rule list collapses to card-per-rule below `md:`. Builder steps are full-screen at mobile, side-panel at `md:`. Outbox table scrolls horizontally on mobile (`overflow-x-auto`).
- Dark mode: tokens adapt automatically; JSON viewer background uses `bg-card`.

### 5.8 File-by-file (frontend)

**New files:**
- `frontend/src/pages/AutomationPage.tsx`
- `frontend/src/pages/AutomationOutboxPage.tsx`
- `frontend/src/components/automation/RuleForm.tsx`
- `frontend/src/components/automation/ConditionBuilder.tsx`
- `frontend/src/components/automation/ActionBuilder.tsx`
- `frontend/src/components/automation/RuleExecutionLog.tsx`
- `frontend/src/components/automation/OutboxRow.tsx`
- `frontend/src/services/automationApi.ts`
- `frontend/src/types/automation.ts`

**Modified files:**
- `frontend/src/App.tsx` — add routes `/settings/automation` and `/settings/automation/outbox` wrapped in `AdminRoute`.
- `frontend/src/components/layout/BottomNav.tsx` — add "Automation" link to admin More sheet.

---

## 6. Migration / data

No data migration from CiviCRM for this sprint. The only data concern is:

- **S12 stub rows** — if S12 shipped its idempotent outbox create and inserted `activity.due_reminder` rows, this migration's column-add path preserves them (the `if not insp.has_table` guard described in §3.4 handles this).
- **Seed rule**: the migration optionally inserts a single inactive dry-run rule as a demo: `{name: "Demo: new contact notifier", trigger: "contact.created", conditions: [], actions: [{action_type: "google_chat.send", params: {message: "New contact added: {{contact.first_name}} {{contact.last_name}}"}}], is_active: false, dry_run: true}`. This shows the structure in the UI from day one without firing real notifications.

---

## 7. Acceptance criteria

1. A POST to `/automation/rules` with a valid trigger, conditions, and actions creates a row in `automation_rules` and returns 201 with the rule id and all fields.
2. `AutomationEngine.emit("contact.created", payload, db)` with a matching active rule causes the rule to fire: inline actions execute synchronously, delayed actions insert an `outbox` row with `run_at = utc_now() + delay_minutes`.
3. A rule with `dry_run=true` emits no side effects: no outbox rows inserted, no inline actions executed; `rule_executions` shows `status=dry_run` with `dry_run_log` populated.
4. A rule with `rate_cap_per_entity_minutes=60` does not fire twice for the same `entity_id` within 60 minutes: the second call inserts `rule_executions(status=skipped_rate_cap)`.
5. `_process_outbox` picks up rows with `status=pending AND run_at <= now()`, calls the registered handler, and marks them `sent` on success or `failed`/`dead_letter` on repeated failure; rows with `run_at` in the future are not picked up.
6. A `dead_letter` outbox row reset via `POST /automation/outbox/{id}/retry` reverts to `status=pending, attempts=0, last_error=null, run_at=utc_now()` and is picked up on the next worker cycle.
7. Inserting an outbox row with the same `idempotency_key` twice (e.g. `activity_reminder:42`) results in exactly one row: the second insert is silently ignored (`ON CONFLICT DO NOTHING`).
8. `POST /automation/rules/{id}/test` with a sample payload returns `{would_fire: bool, conditions_result: [...], actions_would_take: [...]}` and does not write any `outbox` or `rule_executions` rows.
9. A rule with `max_entities_per_hour=10` that fires 11 times within 60 minutes has `is_active` flipped to false and an `audit_log` row with `action=rule.circuit_broken` written.
10. `GET /automation/rules` returns paginated results with filter by `trigger` and `is_active` working correctly; `page_size` is clamped to ≤ 100.
11. `GET /automation/outbox` with `status=dead_letter` returns only dead-letter rows; `DELETE /automation/outbox/{id}` fails with 400 for non-dead-letter rows.
12. The admin UI can create a rule end-to-end: select trigger → add two conditions → add one `google_chat.send` action → save → rule appears in list as active.
13. The outbox monitor page auto-refreshes every 10 seconds when pending/failed rows are present and shows a dead-letter row highlighted in `bg-destructive/10`.
14. All new backend endpoints require `role=admin`; volunteer and viewer users receive 403.
15. `npm run build`, `npm run lint`, and `npm run test:run` all pass with no errors.
16. `pytest tests/ -q` with `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test` passes all new tests.

---

## 8. Test plan

### Backend pytest (new file: `backend/tests/test_automation.py`)

```
test_emit_matching_rule_fires_and_logs_execution
  - Insert active rule matching trigger "contact.created" with empty conditions.
  - Call emit("contact.created", payload, db).
  - Assert rule_executions row with status="fired" created.
  - Assert no outbox rows (actions all inline with delay_minutes=0).

test_emit_delayed_action_enqueues_outbox
  - Rule with delay_minutes=30, action google_chat.send.
  - Call emit().
  - Assert outbox row inserted with run_at ~ now()+30min, status=pending.

test_emit_no_matching_rule_noop
  - Call emit("contact.created") with no rules in DB.
  - Assert no rule_executions rows, no outbox rows.

test_condition_evaluator_eq
test_condition_evaluator_in
test_condition_evaluator_gt
test_condition_evaluator_is_null
test_condition_evaluator_unknown_operator_raises
  - Unit tests for _evaluate_condition() with various operators.

test_condition_false_skips_rule
  - Conditions evaluates to False → rule_executions row status=skipped_conditions.

test_rate_cap_prevents_second_fire
  - rate_cap_per_entity_minutes=60, fire twice for same entity_id within 60 min.
  - Assert second fires returns status=skipped_rate_cap; no duplicate outbox row.

test_dry_run_no_side_effects
  - Rule with dry_run=True.
  - emit() → assert rule_executions.status=dry_run, no outbox, no inline DB writes.
  - Assert dry_run_log is populated.

test_idempotency_key_prevents_duplicate_outbox
  - emit() twice with same idempotency_key.
  - Assert outbox table has exactly one row.

test_outbox_worker_picks_up_pending_row
  - Insert pending outbox row, run_at=past, event_type=test_event.
  - Register a mock handler that sets a flag.
  - Call _process_outbox().
  - Assert handler called, outbox row status=sent.

test_outbox_worker_fails_and_increments_attempts
  - Register handler that raises.
  - Call _process_outbox().
  - Assert status=failed, attempts=1, last_error contains exception message.

test_outbox_worker_dead_letters_after_max_attempts
  - Insert failed row with attempts=MAX-1.
  - Call _process_outbox().
  - Assert status=dead_letter.

test_outbox_worker_respects_run_at_future
  - Insert pending row with run_at=now()+1hr.
  - Call _process_outbox().
  - Assert row not picked up (still pending).

test_outbox_retry_endpoint_resets_dead_letter
  - Insert dead_letter row.
  - POST /automation/outbox/{id}/retry as admin.
  - Assert status=pending, attempts=0, last_error=None.

test_outbox_retry_endpoint_400_for_sent_row
  - POST /automation/outbox/{id}/retry on a sent row → 400.

test_circuit_breaker_disables_rule
  - Rule with max_entities_per_hour=2.
  - emit() 3 times (different entity_ids).
  - Assert rule.is_active=False after 3rd fire.
  - Assert audit_log row with action=rule.circuit_broken.

test_rule_create_unknown_trigger_400
  - POST /automation/rules with trigger="unknown.xyz" → 400.

test_rule_create_unknown_action_400
  - POST /automation/rules with action_type="sms.send" → 400.

test_test_endpoint_dry_run
  - Create a rule. POST /automation/rules/{id}/test with sample_payload.
  - Assert would_fire=True, no outbox rows inserted.

test_admin_only_403
  - All automation endpoints return 403 for volunteer and viewer roles.

test_emit_activity_due_reminder_with_idempotency
  - S12's scan_due_reminders emitter: emit("activity.due_reminder") with idempotency_key.
  - Run twice. Assert one outbox row.
```

### Frontend vitest (new file: `frontend/src/__tests__/automation.test.tsx`)

```
test_condition_builder_renders_empty_state
  - Mount ConditionBuilder with empty conditions. Assert "Add condition" button present.

test_condition_builder_add_remove
  - Add two conditions. Remove one. Assert one remains.

test_condition_builder_operator_options_filtered_by_type
  - Select a numeric field. Assert "gt"/"gte" options present. Select a string field. Assert "gt" absent.

test_action_builder_google_chat_shows_message_field
  - Select action_type google_chat.send. Assert message textarea renders.

test_action_builder_activity_create_shows_type_and_assignee
  - Select action_type activity.create. Assert activity_type select + subject + assignee picker render.

test_rule_form_serialises_correctly
  - Fill all steps. Submit. Assert POST body has correct trigger/conditions/actions/delay_minutes shape.

test_automation_page_renders_empty_state
  - Mock GET /automation/rules returning {items:[], total:0}. Assert empty state message visible.

test_outbox_page_shows_dead_letter_row_highlighted
  - Mock outbox with one dead_letter row. Assert row has destructive styling class.
```

---

## 9. Rollout / rollback / risks

### Rollout
1. Run `alembic upgrade head` (adds `outbox`, `automation_rules`, `rule_executions`; zero data impact on existing tables).
2. Deploy backend. The `_process_outbox` worker slot is registered but the queue is empty — no action needed.
3. Deploy frontend. `/settings/automation` is admin-only and does not affect other pages.
4. Optionally: run the migration that inserts the demo inactive dry-run rule (see §6).
5. S12's activity reminder stub `outbox` rows (if any) become live — the worker will pick them up and attempt delivery for `event_type=activity.due_reminder`. S18 has not yet registered a handler for this type, so the rows will fail and dead-letter. **Mitigation:** pre-register a no-op handler for `activity.due_reminder` until S18 lands:
   ```python
   # In queue_manager.py startup or lifespan:
   register_outbox_handler("activity.due_reminder", _noop_handler)
   async def _noop_handler(payload: dict, db: AsyncSession) -> None:
       pass  # S18 replaces this
   ```
6. Wire S17 `emit()` calls into contacts router and participants router in the same PR.

### Rollback
- `alembic downgrade -1` drops `rule_executions`, `outbox`, `automation_rules` (all new; no existing table touched).
- Remove `_process_outbox` from `QueueManager.run` and revert `emit()` call-site imports. All other code paths are unaffected.
- Frontend rollback: revert `App.tsx` route additions and `BottomNav` link; zero risk to existing pages.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `_process_outbox` double-processes a row under parallel workers | Low (single Unraid instance; `SKIP LOCKED` guards Postgres) | Medium (duplicate notifications) | `status=processing` intermediate state + `SKIP LOCKED`; SQLite tests are single-threaded so the guard is unnecessary there. |
| Rules loop: Rule A fires Rule B fires Rule A | Medium (user error) | High (outbox flood) | Circuit-breaker (`max_entities_per_hour`); `rule_executions` rate-cap check; `rule.circuit_broken` audit entry + `is_active=False` auto-disable. |
| Condition evaluator used with untrusted input | Low (admin-only UI) | High (path traversal / data leak) | Whitelist operators strictly; `_resolve_field` only walks existing dict keys (no `__dunder__`); operand type coercion is strict (int/float for numeric ops; str for string ops). Reject on first unknown operator. |
| S12/S13 stub outbox rows have wrong schema | Low (stubs use the canonical shape per §3.3 of S12 spec) | Low | §3.4 column-add migration guard handles any schema drift. |
| `idempotency_key` partial index not supported on SQLite 3.x | Low (SQLite does not support `WHERE` clause on CREATE UNIQUE INDEX before 3.9; CI uses recent SQLite) | Low (test only) | Fall back to full unique index on SQLite (no WHERE clause); the partial index is Postgres-only for prod. |
| Worker crash mid-`processing` leaves rows stuck | Low (single instance; restart resumes) | Medium | On startup: reset all `status=processing` rows back to `status=failed, attempts+=1` (add a `_reset_processing_rows()` call in `QueueManager.__init__` or `run` entry). |

---

## 10. Open questions & pending owner artifacts

1. **Google Chat webhook URL** — required by S18 but the `google_chat.send` outbox row format must match S18's handler. S17 locks the payload schema as `{webhook_url?: str, message: str, thread_key?: str}`. If the owner supplies only one central webhook URL (per decisions.md Round 8), it defaults from `admin_settings.get_str("google_chat_webhook_url")` and the per-action `webhook_url` param is optional.

2. **Gmail send creds** — same pattern: payload schema `{to: str, subject: str, body_html: str, from?: str}`. S17 locks this; S18 implements the SMTP/API send.

3. **`activity.create` template vars** — the locked action params use `{{contact.first_name}}` etc. The exact payload field paths for each trigger must be documented in `TRIGGER_PAYLOAD_FIELDS` at implementation time. Pending: confirm with the owner which context fields (contact, event, participant) should be injectable in notification templates.

4. **Rate-cap vs. event-series attendance bursts** — when a Sunday service ends, `participant.added` fires for every attendee (potentially 500+ in a burst). Rules targeting this trigger MUST set a `rate_cap_per_entity_minutes` or `max_entities_per_hour`; add a validation warning (not a hard block) in the rule form: "Warning: this trigger may fire for many entities at once. Consider setting a rate cap." Surface the count from recent executions.

5. **`require_viewer` shim** — S15 adds the viewer role. S17 is admin-only so no viewer shim is needed here. Confirm with the master plan that automation admin is correctly restricted to admin-only (per decisions.md Round 4: "admin = configure").

6. **`TRIGGER_PAYLOAD_FIELDS` constant** — the UI field picker needs a static map `{trigger_name → [{path, label, type}]}`. This is a pure frontend constant; it must mirror what `AutomationEngine.emit()` actually passes. Keeping them in sync requires documentation discipline or a future `/automation/trigger-schema` introspection endpoint. Note as a follow-up for S19 (clean API).

**Cross-sprint dependencies / shared-model touchpoints for the master:**

- **S12 (`outbox` stub):** S17 owns `outbox`; S12's `has_table` guard creates it if absent. When S17 is cut, the guard becomes a no-op. No spec edits needed to S12, but the master should confirm that S12's migration `down_revision` chain still resolves cleanly after S17's migration inserts between them in the alembic history.
- **S16 (`job_runs` + APScheduler):** S17 adds `_process_outbox` as a new worker job. It does not add a new APScheduler cron — the outbox worker is driven by `QueueManager.run`'s poll loop (continuous, not cron). S16 should log `job_runs` entries for `_process_outbox` sweeps at the same granularity as other jobs; confirm S16's `job_runs` schema covers ad-hoc worker jobs (not just cron jobs).
- **S18 (integrations):** S17's `register_outbox_handler` contract (`async def handler(payload: dict, db: AsyncSession) -> None`) must be locked before S18 is written — it is in §4.3 and is the S17→S18 API surface. S18 must register its handlers in the `lifespan` startup or module import, not at request time.
- **S13 (`newcomer.created` outbox producer):** S13 emits `newcomer.created` via a raw outbox insert (its capability-guard stub). With S17 landed, S13 should be patched to call `automation_engine.emit("newcomer.created", {...}, db)` instead — the outbox row insert becomes S17's responsibility. Track as a follow-up patch to S13.
- **S09 (`resolve_group_contacts`):** the `contact.add_to_group` inline action must call `GET /groups/{id}` or load the group directly via ORM, not re-implement group membership logic. Confirm the S09 `groups`/`group_members` table and ORM model exist before S17 lands.
- **Inconsistency to reconcile:** The master canonical `outbox` schema (`00-MASTER.md §2.7`) does not list `idempotency_key` or `rule_id` columns. This spec adds them as implementation-necessary. **Master must be patched** to add these two columns to the `outbox` canonical definition.

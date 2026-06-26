# Configurable Settings Engine — Design
**Date:** 2026-06-22  
**Status:** Approved  
**Sprint owner:** S16 (Settings, System Status & Scheduled Jobs)  
**Consumers:** S08 (biometric retention), S18 (integrations), S22 (Anthropic API key)

---

## 1. Problem

Several values that must be provided by the church owner before the system can go live were scattered as either hardcoded constants, env-var-only config, or informal "pending artifacts":

- Integration credentials: Google Chat webhook, Zoom S2S OAuth, Gmail SMTP
- Policy toggles: biometric retention, consent gate, viewer PII export, newcomer form, attendance default
- Branding: product name
- Scheduler: timezone, event series IDs

These need to be admin-configurable at runtime from the Settings UI without SSH access or server restarts.

---

## 2. Decisions Made

| Decision | Choice | Rationale |
|---|---|---|
| Storage | DB (`admin_settings` table) | Runtime-editable; survives container restarts |
| Encryption | Fernet symmetric (`SETTINGS_ENCRYPTION_KEY` env var) | One secret to protect; standard library |
| Missing integration behavior | Fail loudly to `job_runs`, red flag on system status | Visibility without blocking other system functions |
| Test connections | Yes — all three (Google Chat, Zoom, Gmail) | One-time setup; broken config painful to debug via logs |
| Audit log | All changes; secret values redacted to `[redacted]` | Settings changes are security-relevant |
| UI layout | Tabbed: Integrations / Policies / Branding / Scheduled Jobs | Distinct categories; mobile-first |
| Ownership | S16 owns engine + UI; S08/S18/S22 are consumers | Clean single-owner; no new sprint needed |

---

## 3. Data Model

**`admin_settings` table already exists** (`backend/app/models.py:279`). No new table.

**S16 migration adds:**
- `label VARCHAR` column (short UI label, separate from `description` help text)
- Seeds all canonical keys below

**No column renames.** Existing columns map cleanly:
- `sensitive` (not `is_secret`) — drives both UI masking and Fernet encryption
- `category` (not `group`) — drives tab grouping
- `value JSONB` (not TEXT) — stored as `{"value": <actual>}`; getter methods handle type coercion

### Canonical Key List (seeded by S16 migration)

| key | category | sensitive | requires_restart | default |
|---|---|---|---|---|
| `google_chat_webhook_url` | integrations | yes | no | `""` |
| `zoom_account_id` | integrations | no | no | `""` |
| `zoom_client_id` | integrations | no | no | `""` |
| `zoom_client_secret` | integrations | yes | no | `""` |
| `zoom_host_email` | integrations | no | no | `"lightnorthcaloocan@gmail.com"` |
| `gmail_smtp_host` | integrations | no | no | `"smtp.gmail.com"` |
| `gmail_smtp_port` | integrations | no | no | `587` |
| `gmail_smtp_user` | integrations | yes | no | `""` |
| `gmail_smtp_password` | integrations | yes | no | `""` |
| `gmail_from_address` | integrations | no | no | `""` |
| `gmail_notification_recipients` | integrations | no | no | `""` |
| `anthropic_api_key` | integrations | yes | no | `""` |
| `biometric_retention_years` | biometric | no | no | `7` |
| `enroll_without_consent` | policies | no | no | `false` |
| `viewer_can_export_pii` | policies | no | no | `false` |
| `newcomer_form_enabled` | policies | no | no | `false` |
| `newcomer_form_captcha` | policies | no | no | `false` |
| `historical_attendance_default` | policies | no | no | `"attended"` |
| `product_name` | branding | no | no | `"Seraphim"` |
| `scheduler_timezone` | general | no | yes | `"Asia/Manila"` |
| `sunday_event_series_id` | general | no | no | `null` |
| `powerhouse_event_series_id` | general | no | no | `null` |

---

## 4. Backend

### `services/settings_service.py` (new, owned by S16)

Single entrypoint for all settings reads and writes. No other file imports Fernet directly.

```python
class SettingsService:
    def __init__(self, encryption_key: str): ...  # from SETTINGS_ENCRYPTION_KEY env var

    async def get(self, db, key: str) -> Any:
        # Fetch row; if sensitive, Fernet-decrypt value["value"]; return typed value

    async def set(self, db, key: str, value: Any, actor_id: int) -> None:
        # Encrypt if sensitive; write JSONB; write audit_log (redact before/after if sensitive)

    async def get_all(self, db, category: str | None = None) -> list[SettingRow]:
        # Return all rows; mask sensitive non-empty values as "***"

    async def test_google_chat(self, db) -> TestResult:
        # POST webhook URL with {"text": "Seraphim connection test"} → ok/error

    async def test_zoom(self, db) -> TestResult:
        # Exchange account_id+client_id+client_secret for S2S token → GET /v2/users/me

    async def test_gmail(self, db) -> TestResult:
        # SMTP EHLO + STARTTLS + AUTH with smtp_user+smtp_password → ok/error
```

### API Endpoints (all `require_admin`, no `/api` prefix)

| Method | Path | Response |
|---|---|---|
| `GET` | `/settings` | `{integrations: [...], policies: [...], branding: [...], general: [...]}` |
| `PUT` | `/settings/{key}` | `{key, value, updated_at}` — 404 if key unknown |
| `POST` | `/settings/test/google-chat` | `{ok: bool, error?: str, latency_ms?: int}` |
| `POST` | `/settings/test/zoom` | `{ok: bool, error?: str}` |
| `POST` | `/settings/test/gmail` | `{ok: bool, error?: str}` |

### Audit Log Contract

Every `PUT /settings/{key}` writes:
```python
audit_log(
    actor_id=current_user.id,
    action="settings.update",
    entity="admin_setting",
    entity_id=key,
    before={"value": "[redacted]"} if sensitive else {"value": old_value},
    after={"value": "[redacted]"}  if sensitive else {"value": new_value},
)
```

### Consumer Pattern (S08 / S18 / S22)

Services read settings at call time (not cached at startup):
```python
webhook_url = await settings_service.get(db, "google_chat_webhook_url")
if not webhook_url:
    job_runs.fail("Google Chat webhook not configured")
    return
```

---

## 5. Frontend

**Route:** `/settings` — admin only (`require_admin`).

### Tab: Integrations
- **Google Chat** section: URL field (sensitive — show `●●●●●●` if set, "Change" to reveal) + "Send test message" button → inline pass/fail badge.
- **Zoom** section: Account ID, Client ID (plain), Client Secret (sensitive) + zoom_host_email + "Verify credentials" button.
- **Gmail** section: SMTP host/port (plain), SMTP user (sensitive), App Password (sensitive), From address, Notification recipients + "Send test email" button.
- **Anthropic** section: API key (sensitive) — no test button (cost); shows "configured" status only.

### Tab: Policies
- Biometric retention years — number input (1–99)
- Enroll without consent — toggle (default off)
- Viewer can export PII — toggle (default off)
- Newcomer form enabled — toggle (default off)
- Newcomer form requires CAPTCHA — toggle (default off, disabled if newcomer form off)
- Historical attendance default — dropdown: `attended` / `registered`

### Tab: Branding
- Product name — text input with live preview of app `<title>` and header logo text.

### Tab: Scheduled Jobs
- APScheduler job table: name, cron expression, last run, next run, status badge (green/red).
- Scheduler timezone — dropdown (from `pytz` zone list).
- Sunday event series ID / Powerhouse event series ID — autocomplete from `event_series` table.

### UX Rules
- Save per-field on blur (no bulk save button) via `PUT /settings/{key}`.
- Dirty indicator (blue dot) on unsaved fields.
- Sensitive fields: show `●●●●●●` placeholder if already set; "Change" button reveals an empty input. Submitting an empty input = no-op (keeps existing value). Frontend never stores the actual secret value.
- Toast success/error via sonner: surface `err.response?.data?.detail`.
- Test connection buttons: spinner while in-flight; green checkmark or red ✗ with error message inline.

---

## 6. Missing Integration Behavior

When a job needs an unconfigured integration:
1. Log `job_runs.status = "failed"`, `detail = "{key} not configured"`.
2. `GET /health/jobs` returns the failed entry.
3. System status page (S16 UI) shows a red badge for that job with the detail message.
4. No exception propagates — other jobs continue unaffected.

---

## 7. Environment Variables

Only one new env var added:

| Var | Required | Purpose |
|---|---|---|
| `SETTINGS_ENCRYPTION_KEY` | Yes (production) | 32-byte URL-safe base64 key for Fernet. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

All other integration credentials move FROM env vars INTO the DB. Existing `config.py` keys (`REDIS_URL`, `COMPREFACE_*`, `JWT_SECRET`, `DATABASE_URL`) remain as env vars — they are infrastructure, not admin settings.

---

## 8. Scope Boundaries

**In scope (S16):**
- `settings_service.py` + encryption
- `PUT/GET /settings`, `POST /settings/test/*` endpoints
- Settings tabbed UI
- `admin_settings` migration: add `label` column + seed all 22 keys

**Out of scope:**
- Import/export of settings (future)
- Per-key permission scoping (all settings are admin-only)
- Settings versioning / rollback

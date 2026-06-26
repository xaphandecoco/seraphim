## [Unreleased]

### S23 — Member Status & Engagement Engine (Sprint complete 2026-06-26)

Automatic member engagement tracking via derived snapshot columns (attendance count, last attended, weeks absent, engagement tier, active/regular/connected status). Recomputable on-demand by admins and via scheduled background jobs (guarded by S16 scheduler availability).

**Backend**
- `app/services/member_status_service.py` (new): `recompute_all_contacts()` recomputes all contact snapshots based on attendance history; exported as both function name and alias for S06 runner contract.
- `app/scheduler.py` (new): APScheduler job registration for weekly (Mon 00:00 UTC) and end-of-month (1st 00:00 UTC) recomputation, guarded by `HAS_SCHEDULER=False` flag.
- `app/constants.py` (new): `DEFAULT_CONNECTED_FIELD_NAMES`, tier thresholds.
- `app/utils/db_helpers.py` (new): database utility functions.
- `app/routers/analytics.py`: added `POST /analytics/recompute-member-status` (admin-only, on-demand trigger) and `GET /analytics/member-status-summary` (fast engagement summary).
- `app/main.py`: lifespan calls `register_s23_jobs()` with Ellipsis placeholder (awaits S16 real AsyncIOScheduler).
- `app/models.py`: Contact model adds 7 snapshot columns (`last_attended_at`, `attendance_count`, `weeks_absent`, `tier`, `is_active`, `is_regular`, `is_connected`) + 5 indexes for query perf.
- Migration `a3b4c5d6e7f8_s23_member_status_snapshot_guard.py` (new): adds snapshot columns and indexes; down_revision `s24a1b2c3d4e5`.
- Tests: `test_member_status_service.py`, `test_analytics_member_status.py`, `test_db_helpers.py` cover recompute logic, integration with S06 import pipeline, and edge cases.

**Frontend**
- `types/index.ts`: added tier and snapshot fields to `ContactDetail`.
- `StatusBadge.tsx`: renders tier label; handles null `is_active`.
- `ContactDetailPage.tsx`: displays member status snapshot via StatusBadge; null-guarded.

**Integration fixes (full-suite QA gate)**
- **Function export mismatch:** S06 migration runner calls `recompute_all_contacts()` but service initially exported only `recompute_all()` → 9 t06 test failures on AttributeError. Fixed by adding alias.
- **Test expectation staleness:** `test_migrations.py` expected S06 alembic head; updated to S23 head `a3b4c5d6e7f8` and down_revision `s24a1b2c3d4e5`.
- **Missing transitive dependency:** `anthropic>=0.40.0` (used by S22 name-match) not in test env → 33 pytest collection errors. Installed.

**Key assumptions (documented in BLOCKERS.md)**
- Tier computation uses EOW Mon 00:00 UTC timezone (confirm PHT); EOM recompute cadence `0 0 1 * *`.
- Connected-field names default to `{community_leader, community}`, overridable via `admin_settings.connected_field_names`.
- `job_runs` raw-SQL INSERT schema (columns `job_name, status, detail, ran_at`) reconciliation pending S16; silent failure if schema diverges.
- S16 must supply real AsyncIOScheduler to `register_s23_jobs()` or boot will TypeError.

### S03 — Native Contact CRUD & Profile (Sprint complete 2026-06-25)

Full contact write path: create / edit / soft-delete / restore / paginated list / detail profile / attendance history.
Replaces the read-only `AttendeesPage` with a real CRM directory and profile hub.

**Backend**
- `app/services/contact_service.py` (new): `list_contacts`, `get_contact_detail`, `create_contact`, `update_contact`, `soft_delete_contact`, `restore_contact`, `list_contact_attendance` — async, type-hinted, no N+1 (batched contact_reference IN query), `with_for_update()` on PATCH, audit logging on every write.
- `app/routers/members.py`: replaced bare-list `GET /members` with offset pagination; added `POST /members`, `GET/PATCH/DELETE /members/{id}`, `POST /members/{id}/restore`, `GET /members/{id}/attendance`. Preserved `GET /members/attendees` for S07 compatibility. Correct route declaration order (static `/attendees` before parameterized `/{id}`).
- `app/schemas.py`: new `ContactCreate`, `ContactUpdate`, `ContactDetailResponse`, `ContactListItem`, `PaginatedContactResponse`, `DerivedBadges`, `FaceSummary`, `ContactAttendanceItem`, `PaginatedAttendanceResponse`, `ContactReferenceChip`.
- `backend/tests/test_contacts_crud.py` (new): 18 HTTP integration tests covering all acceptance criteria.
- `backend/tests/conftest.py`: added `sample_deleted_contact`, `sample_contact_with_custom_data` (contact_reference resolution), `sample_participant_history` (2 events + 2 participants, explicit timestamps for desc-order assertion), `sample_participant`, `sample_checkbox_field`.

**Patches applied**
- P01: `contact_type` ORM default `'Individual'` → `'individual'` (migration `server_default` unchanged — S06 data task).
- P02: `AttendeesPage.tsx` and its test deleted; `/attendees` redirects to `/contacts`.
- P03: `FacePanel.tsx` line 320 `thumb_path` → `thumb_url` fallback corrected.
- P04: `MemberSearchModal` renamed to `ContactPickerModal` (file + export + all import sites).
- P05: `GET /members` nickname search folded into `list_contacts` `ilike` on `Contact.nickname`.

**Frontend**
- New pages: `ContactsPage`, `ContactDetailPage`, `ContactFormPage` (create + edit).
- New reusable primitives in `components/ui/`: `FormField`, `DataTable`, `Pagination`, `StatusBadge`.
- New: `components/contacts/FacePanel.tsx` (read-only face panel slot for S07).
- New: `services/contacts.ts`, `hooks/useContacts.ts`.
- `App.tsx`: `/contacts`, `/contacts/new`, `/contacts/:id`, `/contacts/:id/edit`; `/attendees` → `Navigate to="/contacts"`.
- `BottomNav.tsx`: "Attendees" tab → "Contacts" tab (`/contacts`).
- `ContactPickerModal` (was `MemberSearchModal`): reads paginated `items ?? data` for forward compat.
- `types/index.ts`: `ContactListItem`, `ContactDetail`, `DerivedBadges`, `FaceSummary`, `ContactAttendanceItem`, `Paginated<T>`.

**No new migrations** — snapshot columns (`last_attended_at`, `attendance_count`, `weeks_absent`, `tier`, `is_active`, `is_regular`, `is_connected`) were added by a prior sprint; S03 reads them as nullable (S23 owns writes). No deployment-time data steps required.

### Dependency CVE remediation sweep

Audited all production dependencies (frontend `npm audit`, backend OSV) and upgraded the
pinned versions that carried known CVEs. The pins in `requirements.txt` had drifted well
behind the locally-installed (and test-validated) versions; this sprint brings the pins
current. Full backend suite (355 passed, 1 xfailed) re-run green against the upgraded stack;
frontend build + lint + tests green.

- **python-multipart 0.0.19 → 0.0.32** — clears 7 CVEs in FastAPI's multipart/form parser:
  arbitrary file write (GHSA-wp53-j4wj-2cfg), unbounded part-header DoS (GHSA-pp6c-gr5w-3c5g),
  large-prelude DoS (GHSA-mj87-hwqh-73pj), negative Content-Length buffering (GHSA-v9pg-7xvm-68hf),
  semicolon/RFC-2231 parameter smuggling and quadratic querystring parsing (GHSA-6jv3, GHSA-5rvq, GHSA-vffw).
- **cryptography 44.0.0 → 49.0.0** — clears 5 issues: bundled-OpenSSL CVEs (GHSA-537c-gmf6-5ccf,
  GHSA-79v4-65xg-pq4g), incomplete DNS name-constraint enforcement (GHSA-m959-cc7f-wv43),
  subgroup attack (GHSA-r6ph-v2qm-q3c2), PYSEC-2026-35.
- **fastapi 0.115.5 → 0.138.0 + starlette pinned == 1.3.1** — clears 8 starlette CVEs that the
  old transitive `~0.41.3` carried: form-limit bypass (GHSA-82w8-qh3p-5jfq), Host-header
  poisoning (GHSA-86qp-5c8j-p5mr, PYSEC-2026-161), StaticFiles SSRF / UNC NTLM theft
  (GHSA-wqp7-x3pw-xc5r), arbitrary HTTP method dispatch (GHSA-x746-7m8f-x49c), path
  concatenation into auth (GHSA-jp82-jpqv-5vv3), and two DoS vectors (GHSA-2c2j, GHSA-7f5h).
  `starlette` is now pinned explicitly so the CVE-clean release is guaranteed regardless of
  FastAPI's range. pydantic (2.10.3), pydantic-settings (2.6.1), and bcrypt (4.2.1) were
  verified CVE-clean and left unchanged.
- **Frontend: form-data 4.0.5 → 4.0.6** (npm `overrides`) — clears CRLF injection
  GHSA-hmw2-7cc7-3qxx (high), reached transitively via `axios`. `npm audit --omit=dev` now
  reports 0 vulnerabilities. Remaining dev-only audit findings (esbuild/vite, @babel/core,
  js-yaml) do not ship in the production bundle and are deferred (esbuild fix requires a
  breaking vite 5→8 major bump).

### Backlog hardening sweep

This sprint closes out 10 medium/low-priority items from the production-readiness backlog:

- **H1 Test isolation** — backend test suite is now deterministically green across consecutive runs (no stale DB file state between runs; session-scoped `asyncio_default_fixture_loop_scope` in `pytest.ini`).
- **H2 Redis shutdown cleanup** — FastAPI lifespan now calls `aclose_redis()` on shutdown to cleanly release the Redis connection pool.
- **M2 Auth param semantics** — `verify_token(require_type=...)` renamed to `verify_token(reject_type=...)` to match true behavior (rejects, does not require).
- **M3 Backup restore caution** — `backup.sh` restore hint annotated with clear warning that `alembic upgrade head` must only run on current (unrolled-back) images.
- **L4 Remove unused params** — `/auth/refresh` and `/auth/logout` no longer take unused injected `response: Response` parameter.
- **L5 Redis singleton + cleanup** — `token_denylist.py` now uses a module-level singleton Redis client (no new connection per call) with `aclose_redis()` for graceful shutdown.
- **L6 nginx log scrubbing** — parent `/api/` and catch-all `/` location blocks now use `scrubbed` log format (defense-in-depth) alongside existing `/api/tasks/feed` and `/api/storage/` scrubbing.
- **L7 isAdmin re-derivation** — `authStore.setToken()` now inline-decodes JWT payload to re-derive `isAdmin` from `role` claim on silent refresh, keeping client state consistent after 401→refresh cycles.
- **L8 CI uvicorn advisory** — added non-blocking advisory step in `.gitea/workflows/ci.yml` and `.github/workflows/ci.yml` to flag uvicorn pin changes for `test_rate_limit_proxy.py` re-verification.
- **L10 Header bg-card regression** — added lightweight Vitest assertion in `AttendeesPage.test.tsx` that header uses design token `bg-card`, not hardcoded `bg-white`.

Also included: react-router CVE GHSA-2j2x-hqr9-3h42 fixed (open-redirect in 6.7.0–6.30.3, upgraded to 6.30.4).

### Security

- **PyJWT[crypto] bumped to >=2.13.0** — closes CVE-2026-32597 / GHSA-752w-5fwx-jx9f
  (critical-header bypass, CVSS 7.5) and four related Snyk issues. All JWT verification
  calls go through `verify_token()` in `backend/app/utils/auth.py`.

- **Token-type lockdown (S1)** — `verify_token()` now accepts `require_type="refresh"`;
  Bearer and `?_t=` query-param paths in `/tasks/feed`, `/storage/*`, and all
  `require_volunteer`/`require_admin` routes now reject tokens with `type="refresh"`,
  preventing refresh-token replay against the API surface. The HttpOnly cookie path
  remains permissive (browser `<img>` tags cannot set Authorization headers).

- **Refresh token rotation + JTI denylist (S6)** — `/auth/refresh` now rotates the
  refresh token on every call (old token deny-listed in Redis by JTI; new token with a
  fresh JTI issued as the cookie). `/auth/logout` best-effort deny-lists the current JTI
  before clearing the cookie. The denylist is fail-open under `REDIS_URL=memory://`
  (tests/CI): rotation still works; only the revocation guarantee degrades.

- **nginx log scrubbing (S2)** — `?_t=<JWT>` query parameters are omitted from nginx
  access logs for `/api/tasks/feed` and `/api/storage/` via the `scrubbed` log format
  (logs `$uri`, omits `$args`).

- **Storage revocation parity** — the `/storage/*` HttpOnly-cookie auth path now checks
  the refresh-token JTI denylist, so a logged-out / rotated refresh cookie can no longer
  load biometric face images (parity with `/auth/refresh`; fail-open under `memory://`).

- **Rollback hardening** — `scripts/rollback.sh` now pre-flight-checks that the target
  image tags exist before stopping the stack, and includes the cloudflared overlay in the
  down/up cycle so the public HTTPS tunnel is restored after a rollback.

### Added

- **SSE heartbeat (P1)** — `/tasks/feed` now emits a keepalive heartbeat (`: keepalive\n\n`) every 15 seconds (configurable via `sse_heartbeat_seconds` admin setting), so connections survive idle periods behind Cloudflare's 100-second timeout. Uses Queue-bridge pattern: feeder task + asyncio.Queue with per-request timeout, Broadcaster's existing `finally` cleanup on cancellation.

- **CompreFace dual-key model (P1)** — Support separate Detection Service and Recognition Service API keys via `COMPREFACE_DETECT_API_KEY` and `COMPREFACE_API_KEY` settings; backward-compatible single-key fallback when either is blank.

- **Cloudflare tunnel deployment options (P1)** — `docker-compose.cloudflared.yml` overlay is now **optional** for operators who already run a persistent `cloudflared` connector on Unraid. The existing-tunnel path (add a Public Hostname ingress on your existing tunnel → frontend `:3000` only, no new container) is now first-class and documented in PRODUCTION_RUNBOOK.md Option C. See README Deployment Options for both 1a (existing tunnel) and 1b (new named tunnel) paths.

- **Gitea CI workflow (P1)** — `.gitea/workflows/ci.yml` runs backend pytest (Python 3.11) + frontend build on Gitea Actions; uses `actions/checkout@v4`, `setup-python@v5`, `setup-node@v4`; SQLite + `memory://` Redis for tests; matches prod 3.11 baseline.

- **LAN-HTTP access correctness (P2)** — Per-request `_cookie_secure()` logic sets `Secure` only over HTTPS; plain HTTP (`http://<lan-ip>:3000`) now works without triggering login loops or HSTS enforcement.

- **Docs alignment for Cloudflare + LAN dual-access (P2)** — PRODUCTION_RUNBOOK and AGENTS now document both Cloudflare named-Tunnel and plain-HTTP LAN paths; `.env.production.template` documents `CLOUDFLARE_TUNNEL_TOKEN` and clarifies the per-request-Secure behavior.

- **Backlog triage (P3)** — Created `docs/BACKLOG.md` with 10 medium/low-priority items from sprint discovery (integration test for real event_generator, nginx query-string scrubbing, refresh-token rotation, etc.).

### Changed

- **AGENTS.md** — Added Docker section on `docker-compose.cloudflared.yml` usage; noted `.gitea/workflows/ci.yml` as primary CI with GitHub mirror.

- **README.md** — Added dual-access model note in TLS/secure-cookie section; clarified plain-HTTP works on LAN with per-request Secure flag.

- **README.md (security sync)** — Documented the token-hardening sprint in the Security, Token Architecture, and SSE Authentication sections: refresh-token rotation + JTI denylist, token-type lockdown (`reject_type="refresh"`), `/storage` denylist parity, nginx log scrubbing, and the PyJWT (CVE-2026-32597) + react-router-dom 6.30.4 (GHSA-2j2x-hqr9-3h42) CVE fixes. Corrected the now-false "No refresh-token revocation list" known-gap entry.

- **Testing methodology** — pytest always runs inside the Docker backend image with Python 3.11; Gitea CI matches this baseline.

### Fixed

- **SSE survival behind Cloudflare** — 100-second idle timeout is now avoided by heartbeat; no connection churn on quiet periods.

- **CompreFace dual-key inconsistency** — API key now selected per-operation (detect vs recognize); single-key setups continue to work.


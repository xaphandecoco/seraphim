# Project Seraphim — Sprint Backlog

Triage results from the production-readiness scan. Items are ordered by priority and effort.

---

## Dependency CVE Remediation Sweep

**Priority:** High
**Status:** Done

Audited frontend (`npm audit`) and backend (OSV) production dependencies; pinned versions
had drifted behind the test-validated local versions and carried known CVEs. Bumped:
- `python-multipart` 0.0.19 → 0.0.32 (7 CVEs, incl. arbitrary file write)
- `cryptography` 44.0.0 → 49.0.0 (5 CVEs, incl. bundled OpenSSL)
- `fastapi` 0.115.5 → 0.138.0 + explicit `starlette==1.3.1` pin (8 starlette CVEs)
- Frontend `form-data` → 4.0.6 via npm `overrides` (CRLF injection, high)

All seven backend deps verified CVE-clean via OSV; full backend suite (355 passed) + frontend
build/lint/test green against the upgraded stack. **Final gate: CI on Python 3.11** (local
validation ran on Windows/3.14, which cannot rebuild numpy for an isolated `pip-audit -r`).

**Deferred — frontend dev-tooling CVEs (Low):** `npm audit` still reports 7 dev-only findings
(esbuild/vite, @babel/core, js-yaml). None ship in the production bundle. The esbuild fix
requires a breaking `vite` 5→8 major bump — schedule as its own task with full frontend
regression.

---

## Integration Test for Real event_generator

**Priority:** Medium  
**Effort:** 1–2 points  
**Status:** Done (S5 — `test_sse_feed_endpoint.py` added)

Add an integration test in `backend/tests/` that exercises the real `task_feed()` endpoint through the FastAPI test client (not an inline copy of the generator logic). The test should:
- Mock `broadcaster.subscribe()` to emit test events
- Mock `dynamic_settings.get_int("sse_heartbeat_seconds", 15)` 
- Assert the response stream contains a keepalive frame (`: keepalive\n\n`)
- Assert a real event is delivered as `data: {...}\n\n`
- Confirm client disconnect cleanly cancels the feeder task (no leaked pubsub)

This catches regressions in `backend/app/main.py` that the current `test_sse_heartbeat.py` tests (which test an inline copy) would miss.

---

## Nginx Query-String Scrubbing for SSE and Storage Logs

**Priority:** Medium  
**Effort:** 1 point  
**Status:** Done (S2 — `log_format scrubbed` + two location blocks in nginx.conf; runbook note added)

Define a custom nginx log_format in `frontend/nginx.conf` that omits the query string for `/api/tasks/feed` and `/storage/` location blocks (which carry the `?_t=<token>` JWT parameter). Options:
1. Disable `access_log` for those locations entirely.
2. Create a separate log_format that excludes `$args`.

Include a runbook note that if full logging is retained, access logs for these paths must be protected from public disclosure (they may contain query params with sensitive data).

---

## Replace SSE f-string Construction with json.dumps

**Priority:** Low  
**Effort:** 0.5–1 point  
**Status:** Done (S4 — all 5 publish calls in tasks.py now use json.dumps)

Replace five SSE frame builder locations in `backend/app/routers/tasks.py` (lines 138, 175, 212, 249, 285) that use f-string interpolation with `json.dumps` for injection-proof consistency. No behavior change today, but eliminates future risk if user-derived data reaches these fields.

---

## Implement Refresh-Token Rotation and Redis JTI Denylist

**Priority:** Low  
**Effort:** 3–5 points  
**Status:** Done (S6 — token_denylist.py + rotation in /auth/refresh + logout deny-list)

Enhance the token security model:
1. On each `/auth/refresh` call, rotate the refresh token (issue a new one, invalidate the old).
2. Maintain a Redis `jti_denylist:<jti>` key for revoked tokens.
3. Allow `/auth/logout` to add the refresh JTI to the denylist so a copied refresh cookie can be invalidated server-side.

Currently, logout only deletes the cookie client-side; a stolen refresh token remains valid for 7 days.

---

## Restrict Token Type on SSE and Storage Auth Paths

**Priority:** Medium  
**Effort:** 1 point  
**Status:** Done (S1 — require_type="refresh" on all 5 Bearer/query-param call sites)

The `verify_token()` utility accepts any validly-signed JWT regardless of type claim. The `/tasks/feed` and `/storage/*` endpoints should reject tokens with `type='refresh'` to prevent refresh tokens from being used to pull the API.

Options:
1. Add a `require_type` parameter to `verify_token()` in `backend/app/utils/auth.py`.
2. Add an explicit rejection check after calling `verify_token()`.

---

## Replace Hardcoded Container Name in backup.sh

**Priority:** Medium  
**Effort:** 1 point  
**Status:** Done (S3 — docker compose exec -T postgres on dump + restore hint lines)

`backup.sh` uses `docker exec seraphim-postgres` which breaks if the Docker Compose project name prefixes container names differently. Replace with:
```bash
docker compose -f docker-compose.unraid.yml exec postgres ...
```
Or add a startup guard that verifies the container exists and exits with a clear error if not found.

---

## Update AGENTS.md for Cloudflared Compose and Gitea CI

**Priority:** Medium  
**Effort:** 0.5–1 point  
**Status:** Completed

Add notes on `docker-compose.cloudflared.yml` usage and `.gitea/workflows/ci.yml` as the primary CI location (with GitHub workflow as mirror only).

---

## Add Resource Limits to Cloudflared Container

**Priority:** Low  
**Effort:** 0.5 point  
**Status:** Done (S8 — deploy.resources.limits cpus/memory in docker-compose.cloudflared.yml)

Once `docker-compose.cloudflared.yml` is created, add explicit `deploy.resources.limits` (e.g., `cpus: 0.25`, `memory: 128m`) for defensive practice on the shared Unraid host. Cloudflared footprint is tiny but explicit limits make resource accounting visible.

---

## Add actions/cache to Gitea CI

**Priority:** Low  
**Effort:** 2–3 points  
**Status:** Open (depends on LNC Gitea cache server)

The `.gitea/workflows/ci.yml` workflow cannot use `actions/cache` without a Gitea-side cache server. Every CI run is currently a cold pip install (~2–4 min) and npm ci. Once the LNC Gitea instance has a cache server or pre-baked base image, add pip and npm caching to cut CI time.

---

## Document Google OAuth Single-Origin Limitation

**Priority:** Low  
**Effort:** 0.5 point  
**Status:** Done (S7 — runbook section 1.2a documents single redirect-origin limit)

Google OAuth uses a single `FRONTEND_URL` and does not support multiple redirect origins in a single app credential. Document this known limitation in the runbook. If needed in the future:
1. An operator adding a second public hostname (e.g., alongside the Cloudflare Tunnel hostname) would need to manually update the Google Console.
2. Optionally implement a comma-separated `FRONTEND_URL` list for CORS and OAuth, with multiple redirect URIs registered in Google Console.

---

## QA Background Suite Results (Pending)

**Priority:** Medium  
**Status:** Closed

Backend suite: 292 passed, 0 failed (SQLite + in-memory Redis, pytest).
Frontend build: ✓ tsc + vite green.
Frontend lint: ✓ eslint (flat config) green after fixing 2 regex escape warnings.
No regressions detected. All sprint (S1–S8) and gap-closing (I4, D5, CI4, T5) items verified.

---

## Proxy-Aware Rate Limiting (D5 — Decision + Test)

**Priority:** Medium  
**Effort:** 2–3 points  
**Status:** Done

Added `backend/tests/test_rate_limit_proxy.py` with three assertions:
- Distinct XFF headers produce distinct rate-limit keys (independent buckets).
- Identical XFF headers produce the same key (shared bucket).
- The resolved address matches the leftmost XFF value.

Uses `ProxyHeadersMiddleware(trusted_hosts="*")` to mirror production uvicorn config.

**Cloudflare Tunnel nuance (documented, not tested):** With `*`-trust the leftmost XFF is client-spoofable. Accepted tradeoff for LAN/single-tunnel church deployment. If multi-tenant exposure occurs later, escalate per PRODUCTION_RUNBOOK.md §7a: restrict `--forwarded-allow-ips` to cloudflared/nginx peer subnet and reseed XFF from `$http_cf_connecting_ip`.

---

## Backlog Hardening Sprint — Triage Results

| Item | Priority | Status | Notes |
|------|----------|--------|-------|
| H1: Test isolation (deterministic double-run green) | High | Done | Session-scoped DB cleanup + `asyncio_default_fixture_loop_scope=session` |
| H2: Wire `aclose_redis()` into lifespan shutdown | High | Done | FastAPI lifespan now calls `aclose_redis()` for graceful Redis pool release |
| M2: Rename `verify_token` param `require_type` → `reject_type` | Medium | Done | Renamed across 5 call sites; no behavior change |
| M3: Annotate `backup.sh` alembic restore hint with code-rollback warning | Medium | Done | Clear caution that `alembic upgrade head` only runs on current images |
| M4: React-router CVE GHSA-2j2x-hqr9-3h42 fix | Medium | Done | Bumped react-router-dom to 6.30.4 (open-redirect fix) |
| M5: Add integration smoke test for refresh-token rejection on storage/SSE | Medium | Open | Deferred to next sprint (L9/L10 tests cover core flows) |
| M6: Document fail-open Redis denylist + biometric consent in RUNBOOK | Medium | Open | Deferred — linked to L9 (GPG encryption docs); document when encryption lands |
| L4: Remove unused `response: Response` from `/auth/refresh` + `/auth/logout` | Low | Done | Both endpoints now build own Response; no Set-Cookie behavior change |
| L5: Redis singleton + cleanup (guarantee connection cleanup) | Low | Done | Module-level singleton + `aclose_redis()` for graceful shutdown |
| L6: nginx log scrubbing on parent `/api/` and `/` blocks | Low | Done | Added `access_log scrubbed;` for defense-in-depth |
| L7: `isAdmin` re-derived on silent refresh (no stale admin bug) | Low | Done | JWT role decoder in `authStore.setToken()`; comprehensive unit tests |
| L8: CI uvicorn-pin advisory step (non-blocking, both `.gitea` and `.github`) | Low | Done | Added to both CI workflows; flags pin changes for `test_rate_limit_proxy.py` re-verification |
| L9: Backup at-rest GPG encryption (env-gated, opt-in) | Low | Open | Deferred to next sprint (architectural decision deferred) |
| L10: Header bg-card regression (Vitest assertion) | Low | Done | AttendeesPage.test.tsx asserts header uses design token, not hardcoded white |
| D11: XFF spoofing hardening acceptance (no code change) | Done | Documented | Accepted tradeoff + escalation path already in PRODUCTION_RUNBOOK §7a |
| D12: Gitea actions/cache unblock | Open | Blocked | Awaiting LNC Gitea cache server; documented as blocked, not to be attempted |

## Summary

| Category | Count | Status |
|----------|-------|--------|
| Done this sprint (H1–H2, M2–M4, L4–L8, L10) | 10 | Fully implemented, tested, passing CI |
| Open (defer to next sprint) | 3 | M5, M6, L9 — architectural decisions or larger scope |
| Blocked (not in scope) | 2 | D11 (already documented), D12 (external dependency) |


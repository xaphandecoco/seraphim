# Project Seraphim — Sprint Backlog

Triage results from the production-readiness scan. Items are ordered by priority and effort.

---

## Integration Test for Real event_generator

**Priority:** Medium  
**Effort:** 1–2 points  
**Status:** Open

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
**Status:** Open

Define a custom nginx log_format in `frontend/nginx.conf` that omits the query string for `/api/tasks/feed` and `/storage/` location blocks (which carry the `?_t=<token>` JWT parameter). Options:
1. Disable `access_log` for those locations entirely.
2. Create a separate log_format that excludes `$args`.

Include a runbook note that if full logging is retained, access logs for these paths must be protected from public disclosure (they may contain query params with sensitive data).

---

## Replace SSE f-string Construction with json.dumps

**Priority:** Low  
**Effort:** 0.5–1 point  
**Status:** Open

Replace five SSE frame builder locations in `backend/app/routers/tasks.py` (lines 138, 175, 212, 249, 285) that use f-string interpolation with `json.dumps` for injection-proof consistency. No behavior change today, but eliminates future risk if user-derived data reaches these fields.

---

## Implement Refresh-Token Rotation and Redis JTI Denylist

**Priority:** Low  
**Effort:** 3–5 points  
**Status:** Open (future sprint)

Enhance the token security model:
1. On each `/auth/refresh` call, rotate the refresh token (issue a new one, invalidate the old).
2. Maintain a Redis `jti_denylist:<jti>` key for revoked tokens.
3. Allow `/auth/logout` to add the refresh JTI to the denylist so a copied refresh cookie can be invalidated server-side.

Currently, logout only deletes the cookie client-side; a stolen refresh token remains valid for 7 days.

---

## Restrict Token Type on SSE and Storage Auth Paths

**Priority:** Medium  
**Effort:** 1 point  
**Status:** Open

The `verify_token()` utility accepts any validly-signed JWT regardless of type claim. The `/tasks/feed` and `/storage/*` endpoints should reject tokens with `type='refresh'` to prevent refresh tokens from being used to pull the API.

Options:
1. Add a `require_type` parameter to `verify_token()` in `backend/app/utils/auth.py`.
2. Add an explicit rejection check after calling `verify_token()`.

---

## Replace Hardcoded Container Name in backup.sh

**Priority:** Medium  
**Effort:** 1 point  
**Status:** Open

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
**Status:** Open

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
**Status:** Open

Google OAuth uses a single `FRONTEND_URL` and does not support multiple redirect origins in a single app credential. Document this known limitation in the runbook. If needed in the future:
1. An operator adding a second public hostname (e.g., alongside the Cloudflare Tunnel hostname) would need to manually update the Google Console.
2. Optionally implement a comma-separated `FRONTEND_URL` list for CORS and OAuth, with multiple redirect URIs registered in Google Console.

---

## QA Background Suite Results (Pending)

**Priority:** Medium  
**Status:** Waiting

The QA Engineer agent submitted only a status message while waiting for an async background task to complete. Once the background suite completes and real findings are available, run a follow-up triage pass to prioritize any discovered issues.

---

## Summary

| Category | Count | Notes |
|----------|-------|-------|
| High priority (P1) | 0 | All P1 items completed (Cloudflare, SSE, dual-key, Gitea CI) |
| Medium priority (P2) | 6 | Integration test, nginx scrubbing, token restriction, backup hardening, AGENTS update, background suite results |
| Low priority (P3) | 4 | f-string to json.dumps, resource limits, cache setup, Google OAuth docs |


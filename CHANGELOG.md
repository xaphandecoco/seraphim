## [Unreleased]

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

- **Testing methodology** — pytest always runs inside the Docker backend image with Python 3.11; Gitea CI matches this baseline.

### Fixed

- **SSE survival behind Cloudflare** — 100-second idle timeout is now avoided by heartbeat; no connection churn on quiet periods.

- **CompreFace dual-key inconsistency** — API key now selected per-operation (detect vs recognize); single-key setups continue to work.


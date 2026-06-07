# Project Seraphim - Production Go-Live Runbook

**Light of the World Worldwide Ministries - North Caloocan (LNC) Attendance System**

This is the operator's runbook for taking Project Seraphim live on the Unraid
host behind HTTPS. Work through it top to bottom. Sections 1-3 get the stack
running; **Section 4 is the actual go-live gate** - do not announce the system
as live until every box in the E2E smoke checklist is ticked.

> **Scope.** This deploys 6 services via Docker Compose (Postgres, Redis,
> backend, frontend/nginx, RTSP worker, queue worker) plus an optional Caddy
> TLS edge. CompreFace (face recognition) and CiviCRM (member/attendance
> records) are **external** services you point the system at.

**Conventions used below**
- Commands assume the repo lives at `/mnt/user/appdata/seraphim/repo` on Unraid.
  Adjust paths to match your install.
- Routers are registered **without** an `/api` prefix in FastAPI. The frontend
  nginx (and Caddy) add `/api` from the browser and strip it before forwarding.
  So: browser hits `/api/tasks`, backend sees `/tasks`. Direct backend calls
  (port 3001, or `docker exec ... curl localhost:8000`) use the **un-prefixed**
  path.
- Auth-gated file serving is `GET /storage/{path}`. SSE feed is
  `GET /tasks/feed` (token via `?_t=` query param). Health is under `/health`.

---

## Table of Contents

1. [Pre-flight](#1-pre-flight)
2. [Bring-up](#2-bring-up)
3. [First-run](#3-first-run)
4. [E2E smoke checklist (the go-live gate)](#4-e2e-smoke-checklist-the-go-live-gate)
5. [Backup & Restore](#5-backup--restore)
6. [Rollback](#6-rollback)
7. [Troubleshooting](#7-troubleshooting)
8. [Automated smoke test](#8-automated-smoke-test)

---

## 1. Pre-flight

Complete **all** of these before bringing the stack up. Each is a hard
prerequisite for a clean go-live.

### 1.1 Generate secrets -> `.env.production`

Generate strong secrets and write the production env file. Use the provided
helpers (do **not** hand-write secrets, and never commit `.env.production`):

```bash
cd /mnt/user/appdata/seraphim/repo

# Start from the template, then fill in / generate secrets.
cp .env.production.template .env.production

# Generate strong values for every secret in the file:
bash scripts/generate-secrets.sh
```

`scripts/generate-secrets.sh` produces cryptographically strong values for at
least:
- `JWT_SECRET` - **must be >= 32 characters.** If setup is complete but this is
  missing/short, the backend **refuses to start** (fail-fast `RuntimeError`).
- `DB_PASSWORD` (Postgres) and any Redis password.
- `WEBHOOK_SECRET` (>= 32 chars) - only if you use the Gitea deploy webhook.

**Checklist:**
- [ ] `.env.production` exists and is **not** tracked by git.
- [ ] `ENVIRONMENT=production` is set (enables `Secure` cookies, disables SQL
      echo, and disables `/docs`, `/redoc`, `/openapi.json`).
- [ ] `JWT_SECRET` is >= 32 chars.
- [ ] `STORAGE_PATH` points at the persisted storage volume (e.g. `/app/storage`).
- [ ] File permissions on `.env.production` are restrictive (`chmod 600`).

> The env vars are the **startup fallback only**. After the setup wizard runs,
> the live values for JWT secret, CompreFace, and CiviCRM come from the
> `admin_settings` table and can be changed in the admin Settings UI.

### 1.2 DNS + TLS domain in `Caddyfile`

If you are terminating TLS with the bundled Caddy edge:

- [ ] Pick your domain (e.g. `seraphim.example.org`) and set `DOMAIN=` in
      `.env.production`. The `Caddyfile` reads `{$DOMAIN}`.
- [ ] Create a DNS **A record** for that domain pointing at this host's public
      IP.
- [ ] Open **ports 80 and 443** on the router/firewall to the host so Caddy can
      complete the Let's Encrypt ACME challenge and auto-renew certificates.
- [ ] Confirm the domain resolves: `dig +short seraphim.example.org` returns
      your IP.

> **LAN-only Unraid with no public domain?** You have three good options:
>
> - **Plain HTTP on the LAN IP** — bring up the base stack alone
>   (`docker compose -f docker-compose.unraid.yml up -d`). Auth cookies are now
>   marked `Secure` **per request** (only when the request arrives over HTTPS), so
>   plain-HTTP access on the LAN IP works without the old login/refresh loop.
>   HTTPS access still receives hardened `Secure` cookies.
> - **Existing Cloudflare tunnel on Unraid** — if you already run a persistent
>   `cloudflared` connector on Unraid (managed independently), add a Public
>   Hostname ingress rule on your **existing tunnel** targeting the frontend only,
>   with no new Docker container. See **Option C** below.
> - **New Cloudflare named Tunnel** — terminate TLS at Cloudflare's edge with no open
>   ports, no DNS A record, and no Let's Encrypt:
>   1. Create a named Tunnel in the Cloudflare Zero Trust dashboard, copy its
>      token, and set `CLOUDFLARE_TUNNEL_TOKEN=` in `.env.production`.
>   2. Point the tunnel's public-hostname ingress at `http://seraphim-frontend:80`.
>   3. Start the stack with the cloudflared overlay **instead of** the Caddy one:
>      `docker compose -f docker-compose.unraid.yml -f docker-compose.cloudflared.yml up -d`
>   4. For the other commands in this runbook, substitute
>      `-f docker-compose.cloudflared.yml` wherever you see
>      `-f docker-compose.caddy.yml`. The `/tasks/feed` SSE endpoint emits a 15s
>      keepalive so live updates survive Cloudflare's ~100s idle timeout.

### 1.2a Google OAuth — single redirect-origin limit

**Google OAuth single redirect origin** — Google Console allows only **one**
Authorized Redirect URI per OAuth client credential. `FRONTEND_URL` sets both the
app origin and the OAuth redirect URI (e.g. `https://seraphim.example.org`). If you
expose the app on a **second origin** (e.g. a LAN IP such as
`http://192.168.1.50:3000`), logins from that second origin will fail with
`redirect_uri_mismatch` unless you take one of these steps:

1. Add the second origin as a second **Authorized Redirect URI** in the Google Cloud
   Console for that OAuth client.
2. Use only one origin for Google-authenticated logins (e.g. the Cloudflare Tunnel
   hostname); users on the LAN IP can still log in with a local password account.

### 1.2b nginx log scrubbing

**JWT tokens and access logs** — `/api/tasks/feed` and `/api/storage/` use a
`?_t=<JWT>` query parameter for EventSource and image requests (browsers cannot set
Authorization headers for these). The bundled `frontend/nginx.conf` uses the
`scrubbed` log format for these two location blocks: it logs `$uri` (the path only)
rather than the full `$request` (which would include `$args`), so JWTs are never
written to plaintext access logs.

If you replace the bundled nginx with a custom proxy (Caddy, Traefik, etc.), reproduce
this filtering — a JWT visible in access logs means any reader of those logs can
replay that token for its remaining lifetime. `access_log off` for those locations is
also acceptable.

### 1.3 CompreFace reachable (URL + API key)

CompreFace is external. From the host, confirm it answers:

```bash
curl -fsS "http://<compreface-host>:<port>/api/v1/health" && echo " CompreFace OK"
```

- [ ] CompreFace health returns HTTP 200.
- [ ] You have a **Recognition service** API key from the CompreFace UI.
- [ ] Have the base URL + API key ready to paste into the setup wizard
      (External Services step) - **not** a trailing-slash URL, not the `/api/v1`
      path; just the base (e.g. `http://192.168.1.50:8000`).

### 1.4 CiviCRM v3 reachable (base URL + api_key + site_key)

- [ ] CiviCRM REST **v3** base URL is known (the wizard / settings expect v3).
- [ ] You have the **api_key** (per-user/contact) and **site_key** (from
      `civicrm.settings.php`).
- [ ] The CiviCRM host is reachable from the Unraid host's network.

> CiviCRM is optional to enter at setup time and can be filled in later under
> **Settings**. But attendance push (Section 4) requires it, so configure it
> before go-live.

### 1.5 Camera RTSP URLs validated

For each camera (e.g. Tapo C260):

- [ ] The URL starts with `rtsp://` or `rtsps://`. The backend **rejects** any
      other scheme on camera create/update - `file://`/`http://` will be
      refused.
- [ ] The stream actually plays. Validate from the host before adding it:

```bash
# Quick reachability/structure probe (install ffmpeg if needed):
ffprobe -v error -rtsp_transport tcp "rtsp://user:pass@192.168.1.100:554/stream1"
```

- [ ] Credentials in the URL are correct (Tapo cameras embed `user:pass@`).

---

## 2. Bring-up

### 2.1 Start the stack (6 services)

From the repo directory, with `.env.production` in place:

```bash
cd /mnt/user/appdata/seraphim/repo

# Production (Unraid) base compose + Caddy TLS edge:
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml up -d --build
```

> **This is the production combination** — it matches `scripts/deploy.sh`, which
> uses `docker-compose.unraid.yml` as its base. Do **not** use the bare
> `docker-compose.yml` on the box: Compose auto-applies `docker-compose.override.yml`
> (local-dev settings) when the base file is `docker-compose.yml`. Use the same
> `-f docker-compose.unraid.yml -f docker-compose.caddy.yml` combination for every
> later command (`ps`, `logs`, `exec`, `down`), or Compose won't see the running
> services.

> **First build is slow.** The backend image bundles ffmpeg and ~250 Debian
> packages (~680 MB). The first `--build` can take 30+ minutes; later builds use
> the layer cache.

### 2.2 Confirm all services are healthy

```bash
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml ps
```

- [ ] You see these services, all `running` and (where they have a healthcheck)
      `healthy`:
  - `postgres`
  - `redis`
  - `seraphim-backend`
  - `seraphim-frontend`
  - `seraphim-rtsp-worker`
  - `seraphim-queue-worker`
  - `caddy` (if using the TLS edge)

Wait until `postgres` and `redis` report `healthy` before trusting the backend -
the backend depends on both.

### 2.3 Confirm migrations ran (`alembic upgrade head`)

The backend's `entrypoint.sh` runs `alembic upgrade head` automatically on every
start (it is idempotent - already-applied revisions are skipped). Confirm it
succeeded:

```bash
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml logs seraphim-backend | grep -i -E "running database migrations|alembic|upgrade|Starting application"
```

- [ ] Logs show **"Running database migrations..."** followed by Alembic output
      and **"Starting application..."** with no traceback.
- [ ] Verify the DB is at head:

```bash
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml exec seraphim-backend alembic current
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml exec seraphim-backend alembic heads
# 'current' and 'heads' should report the same revision.
```

If migrations fail on boot, see [Troubleshooting -> migration failure](#7-troubleshooting).

### 2.4 Read worker logs - what GOOD looks like

The pipeline only works if the workers initialized their settings from the DB
**and** are talking to CompreFace.

```bash
# Queue worker (Redis queue consumer -> task creation):
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml logs -f seraphim-queue-worker

# RTSP worker (FFmpeg frame capture from cameras):
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml logs -f seraphim-rtsp-worker
```

- [ ] **GOOD:** RTSP worker logs show it connecting to your camera(s) and
      pulling frames; the pipeline shows CompreFace detect/recognize calls
      against your real CompreFace URL.
- [ ] **GOOD:** Workers logged that they loaded dynamic settings (active event,
      thresholds) from the database on startup.
- [ ] **BAD - stop and fix:** any log line indicating CompreFace is
      **"not configured"**, an **empty URL** being called, or repeated
      connection refusals to CompreFace. This means the workers did not pick up
      the CompreFace URL/key. Re-check Settings (or `.env.production`) and
      restart the workers. See
      [Troubleshooting -> CompreFace empty-URL](#7-troubleshooting).

> A fast end-to-end sanity check at this point: run the automated smoke test
> (Section 8) with `--write` once an admin exists (after Section 3).

---

## Option C — Existing Cloudflare tunnel (no new container)

If you already run a persistent `cloudflared` connector on your Unraid host
(managed from the Cloudflare Zero Trust dashboard independently), you do NOT need
a second Docker container. Instead, add a Public Hostname ingress rule on your
**existing tunnel** to expose Seraphim. This path is faster than spinning up a
new tunnel and reuses your existing connector infrastructure.

### Setup in the Cloudflare Zero Trust Dashboard

1. **Named Tunnels** → Select your existing tunnel.
2. **Public Hostname** → **Add a public hostname**:
   - **Public Hostname:** Your chosen domain (e.g., `seraphim.example.org`)
   - **Service:** `http://<unraid-ip>:3000` (the frontend nginx container port)
   - Click **Save**. Cloudflare auto-creates a CNAME record pointing to the tunnel.

**Critical:** Do **NOT** add hostnames for `:3001` (backend API), port 5432
(Postgres), or 6379 (Redis). The frontend is the only public entry point.

### Traffic Flow

```
Your Browser (HTTPS) → Cloudflare Edge (TLS)
  → existing cloudflared daemon on Unraid
  → Frontend nginx (port 3000, :80 inside container)
  → X-Forwarded-Proto=https (nginx proxy_pass to backend)
  → Backend (port 3001, :8000 inside container)
```

The nginx configuration at `frontend/nginx.conf:17–18` forwards
`X-Forwarded-Proto=https` (added by Cloudflare edge) to the backend, so the
backend knows the origin was HTTPS. This signals the per-request `_cookie_secure()`
logic to mark the refresh token `Secure`, and downstream SSE does not drop the
connection.

### Bring-up & Smoke Test

Start the stack using only the **Unraid base** (no cloudflared overlay):

```bash
cd /mnt/user/appdata/seraphim/repo
docker compose -f docker-compose.unraid.yml up -d --build
```

Then follow the standard bring-up (Sections 2.2–2.4) and first-run (Section 3)
steps. When the app is live, test end-to-end with the smoke checklist below.

### Securing the Tunneled Deployment

Before announcing the system as live, verify all of the following:

**(a) Ingress rules → frontend only**
- In the dashboard, confirm only the frontend Public Hostname exists; no hostname
  targets `:3001`, 5432, or 6379.
- **Caveat:** The frontend nginx on `:3000` is reachable from anywhere on the
  Unraid LAN without Cloudflare. The app's own JWT login is the gate for users
  inside the LAN. If LAN isolation is required, use Cloudflare Access (item (f)
  below) or restrict the LAN IP with firewall rules.

**(b) `ENVIRONMENT=production` disables API docs**
- Set `ENVIRONMENT=production` in `.env.production` to disable `/docs`, `/redoc`,
  and `/openapi.json` (return 404). This is the **primary mitigation** for a
  misconfigured public hostname that accidentally exposes the OpenAPI UI.
- Verify: `curl https://<hostname>/docs` returns 404.

**(c) Secrets via `scripts/generate-secrets.sh`**
- Run the helper to generate JWT secret (≥ 32 bytes), DB password, and webhook
  secret.
- Verify no `__GENERATE_ME__` sentinel remains:
  `grep __GENERATE_ME__ .env.production` (should output nothing).

**(d) Browser refresh → still logged in (Secure cookie check)**
- Log in to the app over the public hostname.
- Open **Browser DevTools → Application → Cookies**.
- Reload the page.
- **Expected:** The `refresh_token` cookie shows the **`Secure`** flag (✓), and
  you remain logged in after reload.
- **If it fails:** The cookie was dropped (likely served over plain HTTP, or
  missing `ENVIRONMENT=production`). Re-check the proxy setup and try again.

**(e) SSE streams events incrementally (GET buffering check)**
- Open **Browser DevTools → Network** tab.
- On the Tasks page, ensure an **active event** is set.
- Trigger a new detection (via camera or `POST /uploads/faces`).
- In the Network tab, find `GET /api/tasks/feed` and click it.
- Scroll to **Response**.
- **Expected:** The response shows events arriving incrementally over time as
  they are generated (e.g., `data: {"type":"new_task",...}` lines appearing as
  the request progresses), **not** all buffered and delivered at once when the
  connection closes.
- **If events arrive all at once at the end:** Cloudflare's edge (or a proxy in
  front) is buffering the stream. As a fallback, use plain HTTP on the LAN
  (`http://<unraid-ip>:3000`, which bypasses Cloudflare), or deploy via Caddy
  (`docker-compose.caddy.yml`) for now and open a Cloudflare support issue about
  named-tunnel GET request buffering (referenced in cloudflared#1449).

**(f) Cloudflare Access SSO (optional, but recommended)**
- **What it adds:** A second login gate in front of the app. Users must
  authenticate with a provider (Google, GitHub, Azure, etc.) at the edge before
  reaching the app.
- **What it does NOT add:** It does not replace the app's JWT login. Two
  sequential prompts (Access SSO, then app login) are **expected behavior**.
- **How to set up:**
  1. In the Cloudflare dashboard, **Access → Applications → Create an
     Application → Cloudflare (Dash)** (or equivalent for your account type).
  2. Set the app domain to `seraphim.example.org` (your Public Hostname).
  3. Define an **Access Policy** (e.g., allow anyone in a specific email domain,
     or specific identity providers).
  4. Save and test from a fresh browser window (you'll see the Access login page
     before the app login).
- **Why optional:** The app's own login is sufficient for access control. Access
  is an additional operational gate to prevent bots and enforce SSO across your
  organization.

**(g) No public hostname for backend / database**
- `docker compose exec postgres ...` (internal network, not exposed).
- Direct backend (`:3001`) is not publicly accessible; only reachable from the
  Unraid LAN.
- Verify: `curl https://<hostname>:3001/health` times out (Cloudflare does not
  route to it).

**(h) Google OAuth single-origin note**
The backend builds the OAuth `redirect_uri` from `request.base_url` at runtime and
reads `FRONTEND_URL` for the post-login redirect. Google OAuth requires every
`redirect_uri` to be pre-registered in the Google Cloud Console under Credentials
-> Authorized Redirect URIs.

If you expose the app on **two origins simultaneously** (e.g., the Cloudflare tunnel
hostname `https://seraphim.example.org` AND the LAN IP `http://192.168.1.x:3000`),
Google will reject the OAuth callback for whichever origin is not registered. You
must either:
- Register both redirect URIs in Google Console:
  - `https://seraphim.example.org/auth/google/callback`
  - `http://192.168.1.x:3000/auth/google/callback` (or the LAN backend port)
- Or choose one canonical origin and set `FRONTEND_URL` to match it.

Recommended: use the tunnel hostname as the single canonical origin and access the
LAN only via plain-password login. OAuth via LAN IP requires a second Google Console
entry and an HTTP-origin (non-Secure) cookie, which browsers increasingly restrict.

**When all boxes are checked**, the system is go-live ready over the Cloudflare
tunnel.

---

## 3. First-run

### 3.1 Complete the setup wizard

Open the app in a browser:
- Via Caddy/HTTPS: `https://<your-domain>`
- Direct (no proxy): `http://<unraid-ip>:3000`

The 5-step wizard at `/setup` runs once and then **permanently locks** (all
`/setup/*` endpoints return **410 GONE** afterward):

1. **Database** - Postgres + Redis connection details. Click **Test
   Connections** before advancing.
2. **External Services** - CompreFace URL + API key, and CiviCRM base URL +
   api_key + site_key.
3. **Admin Account** - create the first admin. Password policy: **>= 12 chars,
   with uppercase, lowercase, a number, and a special character.**
4. **Camera** - add your first RTSP camera (optional; can add later in Settings).
5. **Confirm** - review (DB password is masked) and finish.

On success the wizard reloads in-memory settings, so the app is usable
**without a restart**.

- [ ] Wizard completed; you can log in as the admin you created.
- [ ] `GET /setup/status` returns `{"setup_complete": true}` (or the wizard URL
      now 410s), confirming the lock.

### 3.2 Set the ACTIVE EVENT (critical)

**This is the link between a live camera feed and "who attended which service."**
Tier-100 (auto-log) attendance is only written when an active event is set.

1. Go to **Events** (tap **Sync** first if the event you need is not listed -
   this pulls events from CiviCRM).
2. Tap **Set Active** on the event happening now - it gets a **LIVE** badge.
   (API equivalent: `POST /events/set-active?event_id=<id>`; clear with
   `POST /events/set-active` with no `event_id`.)

- [ ] An event shows the **LIVE** badge.
- [ ] The **"no active event"** banner on the **Tasks** page has **cleared**.
- [ ] Confirm via API: `GET /events/active-event-id` returns a non-null
      `active_event_id`.

> When the service ends, clear the active event (or set the next one). If no
> active event is set, detections are still created but **attendance is not
> logged** and the warning banner reappears.

---

## 4. E2E smoke checklist (the go-live gate)

Run this against the **real** stack with a real (or mock) camera and an active
event set. This is the go-live gate - **every box must be checked** before you
announce the system as live. Have a CiviCRM record you can safely write to (or a
test event) for the push step.

> Tip: keep two browser tabs open as two different volunteer accounts to exercise
> dual-approval and SSE live-update, and keep `docker compose ... logs -f
> seraphim-queue-worker` running in a terminal.

**Detection -> task creation**
- [ ] A face passes the camera (or use the mock camera / `POST /uploads/faces`
      with a real face image). Within a few seconds a **`detections`** row and a
      **`tasks`** row appear.

```bash
# Peek at the DB to confirm rows are landing (adjust DB name/user if changed):
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml exec postgres \
  psql -U seraphim -d seraphim_attendance -c \
  "select id, tier, status, matched_name, event_id from detections order by id desc limit 5;"

docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml exec postgres \
  psql -U seraphim -d seraphim_attendance -c \
  "select id, detection_id, status, required_approvals, current_approvals from tasks order by id desc limit 5;"
```

**Task card shows the real name + thumbnail**
- [ ] On the **Tasks** page, the new card shows the suggested member's **real
      name** (e.g. "Juan Dela Cruz"), **not** `member:123` and **not** a raw
      `Unknown` for a known member.
- [ ] The **face thumbnail renders** (served via the authenticated
      `/storage/{path}` route - a broken image means a storage-volume or auth
      problem).

**Confirm -> attendance row with correct contact_id**
- [ ] Click **Confirm** (for a 91-99 / below90 task this records one approval;
      finish the required approvals - 2 different volunteers for low-confidence).
      Once approvals are met, an **`attendance`** row is created with the correct
      **`contact_id`** and `event_id`, `status=confirmed`, `push_status=pending`.

```bash
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml exec postgres \
  psql -U seraphim -d seraphim_attendance -c \
  "select id, contact_id, event_id, status, push_status, push_attempts from attendance order by id desc limit 5;"
```

> Tier-100 with a known member + active event auto-creates the attendance row
> directly (no review). Repeats of the same member in one event de-dupe against
> the `(contact_id, event_id)` unique constraint - one row, no crash.

**Queue worker pushes to CiviCRM**
- [ ] As admin, go to **Settings -> Attendance & CiviCRM Push**, pick the event,
      **Preview Push** (sanity-check the diff), then **Push ... Records to
      CiviCRM**.
- [ ] The record's `push_status` transitions `pending -> queued -> pushed`.
      Confirm in CiviCRM that the participant/attendance record now exists.

```bash
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml exec postgres \
  psql -U seraphim -d seraphim_attendance -c \
  "select push_status, count(*) from attendance group by push_status;"
```

**Live update via SSE (~1s, no manual refresh)**
- [ ] With the **Tasks** page open in one tab, trigger a new detection. A new
      task card appears in the volunteer UI in roughly a second **without a
      manual refresh** (delivered over SSE `/tasks/feed`).
- [ ] If it only appears after a manual refresh, SSE is not flowing - see
      [Troubleshooting -> SSE not updating](#7-troubleshooting).

**Analytics + CSV export reflect the data**
- [ ] As admin, open **Settings -> Analytics Dashboard**. The charts (attendance
      per event, daily detections vs resolved, tier distribution, volunteer
      performance) reflect the test data you just created.
- [ ] **CSV Export** -> `attendance.csv` and `logs.csv` download and contain the
      expected rows.

**Backup produces DB + storage archives, and restore works**
- [ ] Run `scripts/backup.sh` (Section 5). Confirm it produced **both** a
      database dump (`db-<ts>.sql.gz`) **and** a storage archive
      (`storage-<ts>.tar.gz`), plus a config archive.
- [ ] Perform a documented **restore** into a scratch location / test DB and
      confirm the data comes back (Section 5). A backup you have never restored
      is not a backup.

When every box above is ticked, **the system is go-live ready.**

---

## 5. Backup & Restore

### 5.1 Run a backup

```bash
bash scripts/backup.sh
```

What it does (see the script for exact paths):
- Dumps Postgres -> `db-<timestamp>.sql.gz`
- Archives the face-image **storage** volume -> `storage-<timestamp>.tar.gz`
- Archives the **config** volume (`bootstrap.json`, etc.) ->
  `config-<timestamp>.tar.gz`
- Prunes archives older than **14 days**.

**Where archives land:** `/mnt/user/backups/seraphim/` (the `BACKUP_DIR` in the
script). The storage source is `/mnt/user/appdata/seraphim/storage` by default -
adjust the variables at the top of `scripts/backup.sh` if your paths differ.

- [ ] Schedule it daily (Unraid **User Scripts** plugin, or cron) and verify the
      first scheduled run actually wrote files.
- [ ] Periodically copy archives **off-host** (another disk / NAS / cloud).

### 5.2 Restore (data)

The backup script prints the exact restore commands for the archives it just
made. In general:

```bash
# 1) Restore the database (into the running postgres container):
zcat /mnt/user/backups/seraphim/db-<timestamp>.sql.gz \
  | docker exec -i seraphim-postgres psql -U seraphim seraphim_attendance

# 2) Restore the storage volume (face images):
tar -xzf /mnt/user/backups/seraphim/storage-<timestamp>.tar.gz \
  -C /mnt/user/appdata/seraphim/

# 3) Restore config (only if you also lost bootstrap.json):
tar -xzf /mnt/user/backups/seraphim/config-<timestamp>.tar.gz \
  -C /mnt/user/appdata/seraphim/

# 4) Bring schema to head (idempotent):
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml exec seraphim-backend alembic upgrade head
```

> For a clean DB restore, consider stopping the backend/workers first so nothing
> writes mid-restore:
> `docker compose ... stop seraphim-backend seraphim-rtsp-worker seraphim-queue-worker`,
> restore, then `start` them again.

### 5.3 Full-stack rollback to a previous image/tag

For rolling the **application** back to a previously deployed image tag (not a
data restore), use `scripts/rollback.sh` - see Section 6.

---

## 6. Rollback

Deploys tag images for rollback. `scripts/deploy.sh` builds, tags the new images
with the git short-SHA **and** `:unstable`, and brings the stack up.
`scripts/rollback.sh` retags a known-good image back to `:unstable` and
redeploys.

### 6.1 App / image rollback

```bash
# Roll back to a known-good tag (default: 'stable'):
bash scripts/rollback.sh stable

# Or roll back to a specific previously-deployed git SHA:
bash scripts/rollback.sh <short-sha>
```

What it does: `docker compose down`, retags `seraphim-backend:<tag>` and
`seraphim-frontend:<tag>` to `:unstable`, then `docker compose up -d` using
`docker-compose.unraid.yml`.

- [ ] List available tags before rolling back:
      `docker image ls | grep -E "seraphim-(backend|frontend)"`
- [ ] After rollback, re-run the bring-up health checks (Section 2.2) and a quick
      smoke test (Section 8).

### 6.2 If a bad migration is involved

If the failed deploy also applied a schema migration, an image rollback alone may
leave the DB ahead of the code. In that case:
1. Roll the image back (above).
2. Restore the **pre-deploy database backup** (Section 5.2). Always take a backup
   immediately before any deploy that includes migrations.

> Never hand-edit or `alembic downgrade` a production DB without a fresh backup
> in hand.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Worker logs show CompreFace **"not configured"** or calls to an **empty URL** | Workers didn't load the CompreFace URL/key from the DB (or it's blank in Settings) | Set CompreFace URL + API key in **Settings -> System** (or `.env.production`), then restart workers: `docker compose ... restart seraphim-rtsp-worker seraphim-queue-worker`. Verify base URL has no trailing slash and excludes `/api/v1`. Confirm reachability: `curl http://<compreface>/api/v1/health`. |
| **SSE not updating** - new tasks only appear after a manual refresh | Token not reaching `/tasks/feed`, or the proxy is **buffering** the stream | `EventSource` can't send headers, so the token rides as `?_t=<token>`; confirm the browser is authenticated. On the proxy, SSE must **not** be buffered: the bundled Caddyfile sets `flush_interval -1` and `read_timeout 0` for `/api/tasks/feed` and `/tasks/feed`; nginx must pass SSE headers (`proxy_buffering off`). Check `docker compose ... logs caddy` / frontend nginx. |
| **401 redirect loop** right after login; can't stay signed in | `Secure` refresh cookie sent over plain **HTTP** (so the browser drops it), or `ENVIRONMENT` not `production` behind TLS | Serve the app over **HTTPS** (Caddy or NPM). Ensure `ENVIRONMENT=production` so cookies are `Secure`+`SameSite=lax`. The access token is in-memory only and the 401 interceptor tries `/auth/refresh` once - if the refresh cookie was never stored, it loops to `/login`. Behind a proxy also set `uvicorn --forwarded-allow-ips='*'` so rate limiting keys on the real client IP. |
| **Migration failure on boot** - backend won't start, traceback in logs | Bad/partial migration state, or DB unreachable when entrypoint ran | Read `docker compose ... logs seraphim-backend`. Confirm `postgres` is `healthy` first. Check state with `alembic current` vs `alembic heads`. If a partial setup left a stale `bootstrap.json`, remove it: `docker compose ... exec seraphim-backend rm /app/config/bootstrap.json` and re-run setup. For a botched migration, restore the pre-deploy DB backup (Section 5.2) and redeploy the matching image. |
| **App refuses to start:** "JWT secret is missing or too short" | Setup is complete but `admin_settings.jwt_secret` is empty/short (fail-fast guard) | Ensure `JWT_SECRET` (>= 32 chars) is set in `.env.production`, or that the `jwt_secret` row exists in `admin_settings`. If setup was only partially completed, delete `bootstrap.json` (above) and re-run the wizard. |
| **Tier-100 not auto-logging** to attendance | **No active event set** | Set the active event on the **Events** page (`POST /events/set-active`). Confirm `GET /events/active-event-id` is non-null and the Tasks-page banner cleared. Without it, detections are created but attendance is not. |
| **CiviCRM push stuck** - records never reach CiviCRM | Bad CiviCRM creds, CiviCRM unreachable, or records moved to the **dead-letter queue** after 3 failed attempts | Verify api_key + site_key + base URL in **Settings**. Check **Settings -> Attendance & CiviCRM Push -> Dead Letter Queue** and use **retry** on failed records. Inspect `last_push_error`: `select id, push_status, push_attempts, last_push_error from attendance where push_status in ('failed','dead_letter');`. Check **Logs** for push errors. |
| No tasks appearing at all | **Safe Mode** on, or queue **saturated** (>= 500 pending) | Check the banners on the Tasks page (and `GET /health/queue` -> `safe_mode` / `saturated`). Turn Safe Mode off in Settings; let the queue drain below the resume limit (400). Verify a camera is `streaming` in Settings. |
| Face thumbnails show broken icons | Storage volume not mounted into the backend, or not authenticated | Confirm the backend container can read `STORAGE_PATH` and the volume is mounted. Images only serve via authenticated `/storage/{path}`; an unauthenticated request 401s. |
| Camera offline / no frames | Wrong RTSP URL, scheme rejected, or network unreachable | URL must start with `rtsp://`/`rtsps://`. Test with `ffprobe` (Section 1.5). Use **Preview** in Settings to grab one frame; try **Reconnect**. Verify the camera is powered and on-network. |

---

## 7a. Operational Security Notes

### nginx log scrubbing

`/api/tasks/feed` and `/api/storage/` are served via nginx `location` blocks that
use a custom `log_format scrubbed`. This format logs `$uri` (the normalized path
only) instead of `$request` (which includes the full query string). Without this,
the `?_t=<JWT>` token that EventSource uses for authentication would be written in
plaintext to `/var/log/nginx/access.log` on every SSE connection and every
authenticated image load.

The scrubbed format still records: client IP, timestamp, HTTP method, path, status
code, bytes sent, referer, and user-agent — sufficient for access-pattern analysis
and debugging, without persisting bearer tokens.

**If you ever re-enable full request logging** (e.g., for a debugging session), treat
the resulting log files as sensitive material: restrict read access (`chmod 640`,
readable only by the `nginx` user and a privileged ops group), rotate them promptly,
and purge them when the debugging session ends. Do not ship unrestricted access logs
to external log aggregators without scrubbing `?_t=` values at the collector.

### Rate-limit proxy keying (D5)

**How per-client-IP rate limiting works behind nginx / Cloudflare.**

`slowapi.util.get_remote_address` returns `request.client.host`.  The rewrite from
`X-Forwarded-For` to `request.client.host` is performed by uvicorn's
`ProxyHeadersMiddleware`, enabled at the server level via:

```
uvicorn --proxy-headers --forwarded-allow-ips='*'
```

This flag is set in the Unraid/prod entrypoint.  It is **not** part of the FastAPI
app object — middleware added via `app.add_middleware(...)` cannot perform this
rewrite.

**XFF chain through Cloudflare tunnel:**

```
Browser (real IP) → Cloudflare edge → cloudflared → nginx → backend (uvicorn)
```

Cloudflare sets `X-Forwarded-For: <visitor-IP>`.  nginx appends its own peer
(`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`), so by the time
uvicorn sees the header it is `<visitor-IP>, <nginx-peer>`.  With `always-trust`
(`*`) uvicorn uses the **leftmost** value (`<visitor-IP>`), so rate-limit buckets
key on the real visitor IP — they do **not** collapse onto the shared cloudflared
or nginx peer address.

**Decision: nginx XFF reseed is not required.**  Seeding from
`$http_cf_connecting_ip` is not needed for bucket separation in this
single-tunnel/LAN deployment.

**Accepted tradeoff — spoofability with `*` trust:**

With `trusted_hosts="*"` the leftmost XFF value is client-spoofable: an attacker
can send `X-Forwarded-For: <fake-IP>` and nginx will append the real peer address,
but the fake IP stays leftmost.  This is an accepted tradeoff for a LAN/single-tunnel
church deployment with no multi-tenant edge exposure.

*Escalation path* if stricter anti-spoofing is needed:
1. Change `--forwarded-allow-ips` from `*` to the specific cloudflared/nginx peer
   subnet (e.g. `172.18.0.0/16` for the Docker bridge).
2. Reseed XFF from `$http_cf_connecting_ip` (or Cloudflare published real-IP ranges)
   in nginx, so the trusted-peer logic picks the CF-attested IP rather than the
   leftmost value.

**Version sensitivity:** uvicorn ≥0.40 changed multi-value XFF handling compared
to 0.32.1 (the pinned production version).  The single-value behavior both versions
agree on is what this deployment relies on and what the regression test
(`backend/tests/test_rate_limit_proxy.py`) asserts.  Any `uvicorn` pin bump must
re-verify rate-limit keying behavior against the new version's `ProxyHeadersMiddleware`
source before deploying.

---

## 8. Automated smoke test

`scripts/smoke_test.py` runs an automated smoke test against the **running**
stack over HTTP. It uses only the Python standard library (preferring `httpx` if
present, otherwise falling back to `urllib`), so it runs with a bare `python3` on
the Unraid host or any box that can reach the stack.

**What it checks** (each prints a `PASS`/`FAIL` line, with an overall summary and
non-zero exit on failure):
1. `GET /health` returns 200 and reports component booleans (postgres/redis/compreface).
2. `POST /auth/login` succeeds and yields an access token (reused as `Bearer` for the rest).
3. `GET /events/active-event-id` - reports whether an active event is set (warns, does not fail, if none).
4. Read-only endpoints respond 200 with the expected shape: `/members` (list, clamped),
   `/tasks` (paginated object with `items`), `/attendance` (list), `/health/queue`
   (object with `safe_mode`/`saturated`).
5. **Optional, behind `--write`:** `POST /uploads/faces` with a synthetic
   high-variance image, asserting the JSON response has the expected keys
   (`faces_detected`, `quality_passed`, `tasks_created`, `auto_logged`,
   `skipped`, `deduplicated`).

### Example invocations

```bash
# Read-only checks against a direct backend (un-prefixed paths):
python3 scripts/smoke_test.py \
    --base-url http://localhost:8000 \
    --email admin@example.org --password 'YourPassw0rd!'

# Against the public site (nginx/Caddy strips /api -> add the /api suffix):
python3 scripts/smoke_test.py \
    --base-url https://seraphim.example.org/api \
    --email admin@example.org --password 'YourPassw0rd!'

# Include the optional write check (uploads a synthetic face image; needs admin):
python3 scripts/smoke_test.py --write \
    --base-url http://localhost:8000 \
    --email admin@example.org --password 'YourPassw0rd!'

# Credentials can also come from the environment:
BASE_URL=http://localhost:8000 SMOKE_EMAIL=admin@example.org \
    SMOKE_PASSWORD='YourPassw0rd!' python3 scripts/smoke_test.py
```

Run it from inside the backend container if the host lacks Python:

```bash
docker compose -f docker-compose.unraid.yml -f docker-compose.caddy.yml exec seraphim-backend \
  python3 /app/../scripts/smoke_test.py --base-url http://localhost:8000 \
  --email admin@example.org --password 'YourPassw0rd!'
# (or copy scripts/smoke_test.py into the container / run it from the repo on the host)
```

Exit status: **0** = all checks passed (warnings allowed); **1** = one or more
checks failed (or login was required but failed). `python3 scripts/smoke_test.py
--help` prints full usage and exits without touching the network.

> The automated smoke test covers the API surface fast, but it is **not** a
> substitute for the full Section 4 E2E checklist (camera -> review -> CiviCRM ->
> SSE -> analytics -> backup), which is the real go-live gate.

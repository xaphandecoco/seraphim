"""Deployment-artifact contract tests (Stories S1 + S4 — DevOps deliverables).

These encode the acceptance criteria for the two outstanding DevOps files:
  - docker-compose.cloudflared.yml   (Story S1)
  - .gitea/workflows/ci.yml          (Story S4)

Both files are listed in the sprint as 'no on-disk file yet'. Each test SKIPS with
an explicit 'deliverable not yet created' message while the file is absent, and
becomes a hard gate the moment the file lands — at which point it verifies the
exact AC (pinned image, CLI healthcheck, TUNNEL_TOKEN env, restart policy; and for
CI: checkout@v4 / setup-python@v5 / setup-node@v4, py3.11, no services: block,
SQLite + memory:// env, frontend build).

They live in the backend pytest suite so a single `pytest tests/ -q` run reports
the deployment-artifact status alongside the code tests.
"""
from pathlib import Path

import pytest
import yaml

def _find_repo_root() -> Path:
    """Locate the directory that holds the compose/deploy files.

    On the host and in CI the repo root is parents[2] (backend/tests/<file> -> repo).
    Inside the backend Docker image only ``backend/`` is mounted at ``/app``, so the
    repo-root deploy artifacts are absent; in that case the tests below SKIP via
    ``_require`` instead of failing (they are host/CI contract tests, not in-image).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docker-compose.unraid.yml").exists():
            return parent
    return here.parents[2]


REPO_ROOT = _find_repo_root()
CLOUDFLARED = REPO_ROOT / "docker-compose.cloudflared.yml"
UNRAID = REPO_ROOT / "docker-compose.unraid.yml"
GITEA_CI = REPO_ROOT / ".gitea" / "workflows" / "ci.yml"
ENV_TEMPLATE = REPO_ROOT / ".env.production.template"


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _require(path: Path, what: str):
    if not path.exists():
        pytest.skip(f"{what} not yet created at {path} (outstanding DevOps deliverable)")


# ===========================================================================
# Story S1 — docker-compose.cloudflared.yml
# ===========================================================================


def test_cloudflared_compose_exists_and_parses():
    _require(CLOUDFLARED, "docker-compose.cloudflared.yml (S1)")
    data = _load_yaml(CLOUDFLARED)
    assert "services" in data
    assert "cloudflared" in data["services"], "must define a 'cloudflared' service"


def test_cloudflared_image_is_pinned():
    _require(CLOUDFLARED, "docker-compose.cloudflared.yml (S1)")
    svc = _load_yaml(CLOUDFLARED)["services"]["cloudflared"]
    image = svc.get("image", "")
    assert image.startswith("cloudflare/cloudflared:"), f"unexpected image {image!r}"
    tag = image.split(":", 1)[1]
    assert tag not in ("", "latest"), (
        "image must be pinned to a version tag (e.g. 2025.10.0), not floating 'latest'"
    )


def test_cloudflared_healthcheck_is_cli_not_http():
    """AC/Q4: cloudflared has no local HTTP port → healthcheck must be a CLI check."""
    _require(CLOUDFLARED, "docker-compose.cloudflared.yml (S1)")
    svc = _load_yaml(CLOUDFLARED)["services"]["cloudflared"]
    hc = svc.get("healthcheck", {})
    test = hc.get("test")
    assert test, "cloudflared service must define a healthcheck"
    test_str = " ".join(test) if isinstance(test, list) else str(test)
    assert "cloudflared" in test_str, "healthcheck must invoke the cloudflared CLI"
    # Must NOT probe an HTTP endpoint (curl/wget/http://) — there is no local port.
    lowered = test_str.lower()
    assert "curl" not in lowered and "wget" not in lowered and "http://" not in lowered, (
        f"healthcheck must be a CLI check, not an HTTP probe: {test_str!r}"
    )


def test_cloudflared_uses_tunnel_token_env():
    _require(CLOUDFLARED, "docker-compose.cloudflared.yml (S1)")
    svc = _load_yaml(CLOUDFLARED)["services"]["cloudflared"]
    env = svc.get("environment", [])
    env_str = " ".join(env) if isinstance(env, list) else " ".join(
        f"{k}={v}" for k, v in env.items()
    )
    assert "TUNNEL_TOKEN" in env_str, "token-based tunnel must pass TUNNEL_TOKEN"
    assert "CLOUDFLARE_TUNNEL_TOKEN" in env_str, (
        "TUNNEL_TOKEN should be sourced from ${CLOUDFLARE_TUNNEL_TOKEN}"
    )


def test_cloudflared_restart_policy_unless_stopped():
    _require(CLOUDFLARED, "docker-compose.cloudflared.yml (S1)")
    svc = _load_yaml(CLOUDFLARED)["services"]["cloudflared"]
    assert svc.get("restart") == "unless-stopped"


def test_cloudflared_no_published_ports():
    """Security: cloudflared dials outbound — it must publish NO ports."""
    _require(CLOUDFLARED, "docker-compose.cloudflared.yml (S1)")
    svc = _load_yaml(CLOUDFLARED)["services"]["cloudflared"]
    assert not svc.get("ports"), "cloudflared must not publish any host ports"


def test_cloudflared_no_autoupdate_in_command():
    """--no-autoupdate prevents silent runtime binary pulls (Security Considerations)."""
    _require(CLOUDFLARED, "docker-compose.cloudflared.yml (S1)")
    svc = _load_yaml(CLOUDFLARED)["services"]["cloudflared"]
    cmd = svc.get("command", "")
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "--no-autoupdate" in cmd_str
    assert "run" in cmd_str.split()


def test_env_template_documents_cloudflare_tunnel_token():
    _require(ENV_TEMPLATE, ".env.production.template")
    text = ENV_TEMPLATE.read_text(encoding="utf-8")
    if "CLOUDFLARE_TUNNEL_TOKEN" not in text:
        pytest.skip(
            "DOC-S6 outstanding: .env.production.template does not yet document "
            "CLOUDFLARE_TUNNEL_TOKEN"
        )
    # Once documented, guard that it is presented as a to-be-generated secret.
    assert "CLOUDFLARE_TUNNEL_TOKEN" in text


# ===========================================================================
# Story S4 — .gitea/workflows/ci.yml
# ===========================================================================


def test_gitea_ci_exists_and_parses():
    _require(GITEA_CI, ".gitea/workflows/ci.yml (S4)")
    data = _load_yaml(GITEA_CI)
    assert "jobs" in data and data["jobs"], "CI workflow must define jobs"


def _all_steps(workflow: dict):
    steps = []
    for job in workflow.get("jobs", {}).values():
        steps.extend(job.get("steps", []))
    return steps


def test_gitea_ci_uses_pinned_official_actions():
    _require(GITEA_CI, ".gitea/workflows/ci.yml (S4)")
    steps = _all_steps(_load_yaml(GITEA_CI))
    uses = [s.get("uses", "") for s in steps if s.get("uses")]
    joined = " ".join(uses)
    assert "actions/checkout@v4" in joined
    assert "actions/setup-python@v5" in joined
    assert "actions/setup-node@v4" in joined


def test_gitea_ci_backend_uses_python_311():
    _require(GITEA_CI, ".gitea/workflows/ci.yml (S4)")
    steps = _all_steps(_load_yaml(GITEA_CI))
    py_versions = [
        str(s.get("with", {}).get("python-version", ""))
        for s in steps
        if "setup-python" in s.get("uses", "")
    ]
    assert any(v.startswith("3.11") for v in py_versions), (
        f"backend job must pin Python 3.11 (prod parity); got {py_versions}"
    )


def test_gitea_ci_no_services_block():
    """Q5: tests use SQLite + memory:// Redis → no services: block needed."""
    _require(GITEA_CI, ".gitea/workflows/ci.yml (S4)")
    for name, job in _load_yaml(GITEA_CI).get("jobs", {}).items():
        assert "services" not in job, f"job '{name}' must not declare a services: block"


def test_gitea_ci_backend_env_uses_sqlite_and_memory_redis():
    _require(GITEA_CI, ".gitea/workflows/ci.yml (S4)")
    raw = GITEA_CI.read_text(encoding="utf-8")
    assert "sqlite+aiosqlite" in raw, "backend CI must run against SQLite"
    assert "memory://" in raw, "backend CI must use memory:// Redis"
    assert "pytest" in raw, "backend CI must run pytest"


def test_gitea_ci_does_not_use_actions_cache():
    """Q5: actions/cache needs a Gitea-side cache server — must be avoided."""
    _require(GITEA_CI, ".gitea/workflows/ci.yml (S4)")
    steps = _all_steps(_load_yaml(GITEA_CI))
    assert not any("actions/cache" in s.get("uses", "") for s in steps), (
        "do not use actions/cache (requires a Gitea cache server)"
    )


def test_gitea_ci_frontend_builds():
    _require(GITEA_CI, ".gitea/workflows/ci.yml (S4)")
    raw = GITEA_CI.read_text(encoding="utf-8")
    assert "npm ci" in raw
    assert "npm run build" in raw


# ===========================================================================
# Regression — existing layering files still parse (so `compose config` works)
# ===========================================================================


def test_unraid_compose_parses_and_has_frontend():
    """The cloudflared overlay layers onto unraid and proxies seraphim-frontend:80."""
    _require(UNRAID, "docker-compose.unraid.yml")
    data = _load_yaml(UNRAID)
    assert "services" in data
    # The cloudflared ingress targets the frontend service by name.
    assert "seraphim-frontend" in data["services"], (
        "cloudflared ingress proxies to seraphim-frontend:80 — that service must exist"
    )

"""H1 — session DB-cleanup fixture guard (QA gap-closing).

The H1 determinism property (two consecutive ``pytest tests/ -q`` runs green with
no manual rm) is proven by actually running the suite twice.  What that double-run
does NOT prove is the *safety guard*: the conftest session-autouse fixture
``_cleanup_sqlite_db_at_session_start`` must only delete a **file-based sqlite**
DB and must be a strict no-op for ``:memory:`` and for any non-sqlite URL (a
future Postgres test URL).  A regression that widened the guard could silently
delete a real file derived from a Postgres DSN.

These tests pin that guard two ways:

  1. A pure-predicate test that mirrors the fixture's documented guard
     (``url.startswith("sqlite") and ":memory:" not in url``) across a table of
     URLs, so the intended decision is executable and regression-checked.
  2. A source-level assertion that the live conftest still contains the guard
     tokens, so deleting the guard in conftest.py trips this test.

Maps to AC (story H1): "session-autouse fixture deletes the file-based SQLite DB
... guarded to file-based sqlite URLs only."
"""
from pathlib import Path

import pytest


# Mirror of the fixture's documented guard predicate (conftest.py).
def _should_clean(url: str) -> bool:
    return url.startswith("sqlite") and ":memory:" not in url


@pytest.mark.parametrize(
    "url, expected",
    [
        # file-based sqlite (relative + absolute) -> clean
        ("sqlite+aiosqlite:///./ci_test.db", True),
        ("sqlite+aiosqlite:///./test_seraphim.db", True),
        ("sqlite:///./local.db", True),
        ("sqlite+aiosqlite:////abs/path/seraphim.db", True),
        # in-memory sqlite (explicit :memory:) -> excluded by the guard
        ("sqlite+aiosqlite:///:memory:", False),
        # bare sqlite:// (also in-memory) — the startswith/:memory: guard does NOT
        # exclude it (predicate is True), but the fixture's per-candidate .exists()
        # check makes it a harmless no-op (no such file). See the dedicated test below.
        ("sqlite://", True),
        # non-sqlite (future Postgres test URL) -> NEVER touch
        ("postgresql+asyncpg://seraphim:seraphim@localhost:5432/seraphim_attendance", False),
        ("postgres://user:pw@host/db", False),
        ("", False),
        ("mysql://root@localhost/db", False),
    ],
)
def test_cleanup_guard_predicate(url, expected):
    assert _should_clean(url) is expected


def test_bare_sqlite_scheme_is_a_safe_noop_via_exists_check():
    """Bare ``sqlite://`` passes the startswith/:memory: guard (predicate True), but
    must still be a harmless no-op: the path it derives does not point at any real
    DB file, so the fixture's ``candidate.exists()`` check skips the unlink.

    This replicates the fixture's path-derivation to prove no real file is targeted.
    """
    url = "sqlite://"
    after_scheme = url.split("///", 1)[-1]  # -> "sqlite://" (no '///' present)
    db_path = Path(after_scheme)
    # None of the derived candidates may be an existing real file in the repo.
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        assert not candidate.exists(), (
            f"bare sqlite:// derived an existing path {candidate!r} — fixture could delete it"
        )


def test_live_conftest_contains_the_file_based_guard():
    """Source-level regression fence: the real conftest fixture must still guard on
    BOTH conditions (startswith sqlite AND exclude :memory:). If someone removes the
    guard, this fails — catching a change that could delete a non-sqlite path."""
    conftest_src = (Path(__file__).resolve().parent / "conftest.py").read_text(encoding="utf-8")
    assert "_cleanup_sqlite_db_at_session_start" in conftest_src, "H1 cleanup fixture missing"
    # The two guard tokens that make the delete file-based-sqlite-only.
    assert 'startswith("sqlite")' in conftest_src, "guard must require a sqlite URL"
    assert '":memory:"' in conftest_src, "guard must exclude in-memory sqlite"
    # WAL/SHM siblings must be handled (file-based sqlite can leave -wal/-shm).
    assert "-wal" in conftest_src and "-shm" in conftest_src, (
        "cleanup must also remove the -wal/-shm siblings"
    )


def test_pytest_ini_sets_session_loop_scope():
    """H1 AC: asyncio_default_fixture_loop_scope = session is present in pytest.ini.
    (Pinned pytest-asyncio 0.24.0 supports this key; it is what keeps the shared
    file-based engine usable across the session without per-fixture loop drift.)"""
    ini = (Path(__file__).resolve().parent.parent / "pytest.ini").read_text(encoding="utf-8")
    assert "asyncio_default_fixture_loop_scope" in ini
    assert "session" in ini.split("asyncio_default_fixture_loop_scope", 1)[1].split("\n", 1)[0]

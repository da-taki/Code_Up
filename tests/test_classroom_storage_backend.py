"""Backend-selection, SQL-compatibility, and concurrency-hardening tests for
codeup/classroom/_storage.py + db.py.

Real PostgreSQL is not available in this environment (no local server, no
Docker). Everything here either exercises SQLite directly (which shares the
same code paths in db.py as Postgres - only ``_storage.py`` branches by
backend) or exercises the PostgreSQL SQL-translation/lastrowid-emulation
logic against a stub connection with no real network/server involved. None
of this substitutes for the real-Postgres acceptance run described in
docs/CLASSROOM_PRODUCTION_STORAGE.md - see that document and the PR/report
for what remains unverified.
"""

from __future__ import annotations

import threading

import pytest

import app as app_module
from codeup.classroom import _storage, db


# ---- A/B/C: backend selection --------------------------------------------------

def test_backend_is_sqlite_when_database_url_absent(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _storage.backend_name() == "sqlite"


def test_backend_is_postgres_when_database_url_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@example.invalid:5432/classroom")
    assert _storage.backend_name() == "postgres"


def test_blank_database_url_is_treated_as_absent(monkeypatch):
    # Render/most PaaS unset rather than blank env vars, but guard the
    # "DATABASE_URL=" (empty string) case explicitly since os.environ.get
    # would otherwise return a falsy-but-truthy-length string in some
    # deploy configs.
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert _storage.backend_name() == "sqlite"


def test_database_url_set_but_unreachable_raises_and_never_falls_back(monkeypatch):
    # Port 1 on localhost refuses immediately (no black-hole timeout), so
    # this fails fast instead of hanging for the connect timeout. The
    # timeout itself is also shortened so the (still real) retry/backoff
    # window inside psycopg_pool doesn't slow down the test suite.
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@127.0.0.1:1/nonexistent_classroom_db")
    monkeypatch.setenv("CLASSROOM_PG_CONNECT_TIMEOUT", "2")
    _storage._pool = None
    _storage._pool_url = None
    _storage._schema_ready_for = None
    with pytest.raises(_storage.ClassroomStorageError):
        with _storage.connect():
            pass  # pragma: no cover - must not be reached
    # Critically: the failure must not have quietly produced a usable
    # SQLite connection instead.
    assert _storage.backend_name() == "postgres"


def test_storage_status_reports_unhealthy_without_raising(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@127.0.0.1:1/nonexistent_classroom_db")
    monkeypatch.setenv("CLASSROOM_PG_CONNECT_TIMEOUT", "2")
    _storage._pool = None
    _storage._pool_url = None
    _storage._schema_ready_for = None
    status = db.storage_status()
    assert status["backend"] == "postgres"
    assert status["healthy"] is False
    # Never leak the DSN/host/credentials into the diagnostic payload.
    assert "127.0.0.1" not in str(status)
    assert "pass" not in str(status)


def test_storage_status_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    status = db.storage_status()
    assert status == {"backend": "sqlite", "healthy": True, "database_path_configured": True}


# ---- SQL translation / lastrowid emulation (no real Postgres needed) -----------

class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class _FakeConn:
    """Stands in for a psycopg.Connection: records the exact SQL/params it
    was asked to run and returns a canned row, so the translation layer in
    _storage._PGConnection can be tested without a real server."""

    def __init__(self, row=None):
        self.calls = []
        self._row = row

    def execute(self, sql, params):
        self.calls.append((sql, tuple(params)))
        return _FakeCursor(self._row)


def test_pg_connection_translates_placeholders_and_appends_returning_id():
    fake = _FakeConn(row={"id": 42})
    wrapper = _storage._PGConnection(fake)
    cur = wrapper.execute(
        "INSERT INTO instructors (username, password_hash, display_name, created_at) VALUES (?, ?, ?, ?)",
        ("alice", "hash", "Alice", "2026-01-01"),
    )
    sql, params = fake.calls[0]
    assert "%s" in sql and "?" not in sql
    assert sql.rstrip().endswith("RETURNING id")
    assert params == ("alice", "hash", "Alice", "2026-01-01")
    assert cur.lastrowid == 42


def test_pg_connection_skips_returning_id_for_on_conflict_upsert():
    fake = _FakeConn(row=None)
    wrapper = _storage._PGConnection(fake)
    cur = wrapper.execute(
        "INSERT INTO learner_notification_state (learner_id, assignments_seen_at) VALUES (?, ?) "
        "ON CONFLICT(learner_id) DO UPDATE SET assignments_seen_at = excluded.assignments_seen_at",
        (7, "2026-01-01"),
    )
    sql, _ = fake.calls[0]
    assert "RETURNING" not in sql.upper()
    assert cur.lastrowid is None


def test_pg_connection_leaves_selects_and_updates_alone():
    fake = _FakeConn(row={"id": 1, "status": "open"})
    wrapper = _storage._PGConnection(fake)
    wrapper.execute("SELECT * FROM help_requests WHERE id = ?", (1,))
    sql, params = fake.calls[0]
    assert sql == "SELECT * FROM help_requests WHERE id = %s"
    assert params == (1,)

    fake2 = _FakeConn()
    wrapper2 = _storage._PGConnection(fake2)
    wrapper2.execute("UPDATE help_requests SET status = ? WHERE id = ?", ("resolved", 1))
    sql2, _ = fake2.calls[0]
    assert "RETURNING" not in sql2.upper()


# ---- concurrency hardening (real SQLite, real threads) -------------------------

def test_concurrent_learners_joining_same_cohort_all_succeed():
    instructor = db.create_instructor("concur_join", "hashed", "T")
    cohort = db.create_cohort(instructor["id"], "Concurrency Cohort")

    results = []
    errors = []

    def _join(i):
        try:
            results.append(db.join_cohort(cohort["id"], f"Learner {i}"))
        except Exception as exc:  # pragma: no cover - failure path under test
            errors.append(exc)

    threads = [threading.Thread(target=_join, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len({r["id"] for r in results}) == 8
    assert len({r["token"] for r in results}) == 8


def test_concurrent_get_or_create_progress_does_not_duplicate_or_raise():
    instructor = db.create_instructor("concur_progress", "hashed", "T")
    cohort = db.create_cohort(instructor["id"], "Concurrency Cohort 2")
    learner = db.join_cohort(cohort["id"], "Racer")
    assignment = db.create_assignment(
        cohort["id"], "Race Assignment", "instructions", "starter code",
        None, [], "FULL",
    )

    results = []
    errors = []

    def _get_or_create():
        try:
            results.append(db.get_or_create_progress(assignment["id"], learner["id"]))
        except Exception as exc:  # pragma: no cover - failure path under test
            errors.append(exc)

    threads = [threading.Thread(target=_get_or_create) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len({r["id"] for r in results}) == 1  # exactly one row was created
    rows = db.list_progress_for_assignment(assignment["id"])
    assert len(rows) == 1


# ---- diagnostic route ----------------------------------------------------------

def test_storage_status_route_requires_instructor_login():
    client = app_module.app.test_client()
    r = client.get("/classroom/admin/storage-status", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_storage_status_route_shows_sqlite_backend_to_instructor(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = app_module.app.test_client()
    client.post(
        "/classroom/instructor/register",
        data={"username": "storage_admin", "password": "correct-horse-1", "display_name": "Teacher"},
    )
    r = client.get("/classroom/admin/storage-status")
    assert r.status_code == 200
    assert b"SQLite" in r.data
    # Mentioning the DATABASE_URL *setting* (how to configure Postgres) is
    # fine and expected; only its value/host/credentials must never appear.
    assert app_module.DATA_DIR.encode() not in r.data


def test_create_cohort_retries_on_join_code_collision(monkeypatch):
    instructor = db.create_instructor("collide_instructor", "hashed", "T")
    taken = db.create_cohort(instructor["id"], "Existing Cohort")

    codes = iter([taken["join_code"], "FRESH1"])
    monkeypatch.setattr(db, "new_join_code", lambda conn, length=6: next(codes))

    new_cohort = db.create_cohort(instructor["id"], "New Cohort")
    assert new_cohort["join_code"] == "FRESH1"

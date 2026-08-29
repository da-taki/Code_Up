"""Backend selection and connection handling for classroom persistence.

    DATABASE_URL set     -> PostgreSQL (psycopg3, small pooled connections)
    DATABASE_URL absent  -> SQLite (unchanged local-file behavior)

This module is the *only* place that knows which backend is active. Every
function in ``db.py`` calls :func:`connect` and gets back an object that
behaves like a ``sqlite3.Connection`` for the one method db.py actually
uses - ``.execute(sql, params) -> cursor`` where the cursor exposes
``.fetchone()``, ``.fetchall()`` and ``.lastrowid`` - regardless of which
backend is behind it. That is what lets db.py's ~80 query functions stay
byte-for-byte the same across both backends.

If ``DATABASE_URL`` is set but PostgreSQL cannot be reached or
initialized, :class:`ClassroomStorageError` is raised. It is never caught
to fall back to SQLite - a broken DATABASE_URL must fail loudly instead of
silently producing split-brain classroom data (some requests hitting a
Postgres database, others hitting a throwaway local SQLite file).
"""

from __future__ import annotations

import atexit
import os
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import psycopg
import psycopg.rows
from psycopg_pool import ConnectionPool

from codeup.classroom import _postgres_schema

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class ClassroomStorageError(RuntimeError):
    """The configured classroom storage backend is unavailable.

    Deliberately never caught anywhere in this codebase to fall back to a
    different backend - see module docstring.
    """


# Exceptions that mean "a UNIQUE constraint was violated" on whichever
# backend is active. db.py catches this tuple to retry join-code
# generation and to treat concurrent duplicate-row-initialization as
# "someone else already created it" rather than a hard failure.
UNIQUE_VIOLATION_EXCEPTIONS: Tuple[type, ...] = (
    sqlite3.IntegrityError,
    psycopg.errors.UniqueViolation,
)


def _database_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def backend_name() -> str:
    """'postgres' or 'sqlite', resolved fresh from the environment each
    call (mirrors the rest of this module reading DATA_DIR/DATABASE_URL
    fresh rather than caching at import time, so tests that monkeypatch
    the environment per-test get an isolated backend)."""
    return "postgres" if _database_url() else "sqlite"


def _sanitized_target(url: str) -> str:
    """host[:port]/dbname only - never username, password, or the raw
    DATABASE_URL - safe to put in logs and internal exception messages."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or "unknown-host"
        port = f":{parts.port}" if parts.port else ""
        db = (parts.path or "").lstrip("/") or "unknown-db"
        return f"{host}{port}/{db}"
    except Exception:
        return "postgres-database"


@contextmanager
def connect() -> Iterator[Any]:
    """Yield a connection-like object for the active backend. Schema
    creation/migration happens transparently on first use. Callers write
    plain ``conn.execute("... ?", (...))`` SQL with ``?`` placeholders;
    translation to ``%s`` for PostgreSQL happens inside this module."""
    url = _database_url()
    if url is None:
        with _connect_sqlite() as conn:
            yield conn
    else:
        with _connect_postgres(url) as conn:
            yield conn


# ---------------------------------------------------------------------------
# SQLite backend (unchanged behavior from the original single-file db.py)
# ---------------------------------------------------------------------------

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS instructors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cohorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id INTEGER NOT NULL REFERENCES instructors(id),
    name TEXT NOT NULL,
    join_code TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    display_name TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    joined_at TEXT NOT NULL,
    last_active_at TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    title TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    starter_code TEXT NOT NULL DEFAULT '',
    due_date TEXT,
    expected_concepts TEXT NOT NULL DEFAULT '[]',
    ai_policy TEXT NOT NULL DEFAULT 'FULL',
    capability_settings TEXT,
    is_assessment INTEGER NOT NULL DEFAULT 0,
    start_at TEXT,
    end_at TEXT,
    locked INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignment_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    learner_id INTEGER NOT NULL REFERENCES learners(id),
    status TEXT NOT NULL DEFAULT 'not_started',
    code TEXT NOT NULL DEFAULT '',
    submitted_code TEXT,
    run_count INTEGER NOT NULL DEFAULT 0,
    success_run_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_saved_at TEXT,
    submitted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(assignment_id, learner_id)
);

CREATE TABLE IF NOT EXISTS help_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    learner_id INTEGER NOT NULL REFERENCES learners(id),
    assignment_id INTEGER REFERENCES assignments(id),
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS curriculum_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL UNIQUE REFERENCES learners(id),
    course_id TEXT NOT NULL DEFAULT 'python_foundations',
    current_module_id TEXT,
    current_stage TEXT,
    started_at TEXT,
    last_activity_at TEXT
);

CREATE TABLE IF NOT EXISTS module_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learners(id),
    module_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_started',
    completed_stages TEXT NOT NULL DEFAULT '[]',
    attempts INTEGER NOT NULL DEFAULT 0,
    quiz_score INTEGER,
    quiz_total INTEGER,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(learner_id, module_id)
);

CREATE TABLE IF NOT EXISTS custom_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    instructor_id INTEGER NOT NULL REFERENCES instructors(id),
    title TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    starter_code TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    expected_concepts TEXT NOT NULL DEFAULT '[]',
    challenge TEXT NOT NULL DEFAULT '',
    expected_output TEXT,
    quiz_question TEXT,
    quiz_choices TEXT NOT NULL DEFAULT '[]',
    quiz_answer_index INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    instructor_id INTEGER NOT NULL REFERENCES instructors(id),
    title TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    starter_code TEXT NOT NULL DEFAULT '',
    expected_concepts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_project_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES custom_projects(id),
    order_index INTEGER NOT NULL DEFAULT 0,
    label TEXT NOT NULL,
    check_type TEXT NOT NULL,
    check_config TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS onboarding_progress (
    learner_id INTEGER PRIMARY KEY REFERENCES learners(id),
    completed INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS learner_notification_state (
    learner_id INTEGER PRIMARY KEY REFERENCES learners(id),
    assignments_seen_at TEXT,
    ide_orientation_at TEXT
);

CREATE TABLE IF NOT EXISTS lesson_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learners(id),
    lesson_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_started',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_code TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(learner_id, lesson_id)
);

CREATE TABLE IF NOT EXISTS project_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learners(id),
    project_id TEXT NOT NULL,
    checkpoints_completed TEXT NOT NULL DEFAULT '[]',
    code TEXT,
    active_file TEXT,
    files TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(learner_id, project_id)
);

CREATE TABLE IF NOT EXISTS concept_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learners(id),
    concept TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'not_started',
    evidence_count INTEGER NOT NULL DEFAULT 0,
    last_evidence_at TEXT,
    UNIQUE(learner_id, concept)
);

CREATE TABLE IF NOT EXISTS progress_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learners(id),
    cohort_id INTEGER,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learners_cohort ON learners(cohort_id);
CREATE INDEX IF NOT EXISTS idx_assignments_cohort ON assignments(cohort_id);
CREATE INDEX IF NOT EXISTS idx_progress_events_learner ON progress_events(learner_id);
CREATE INDEX IF NOT EXISTS idx_progress_events_cohort ON progress_events(cohort_id, created_at);
CREATE INDEX IF NOT EXISTS idx_help_requests_cohort ON help_requests(cohort_id, status);
CREATE INDEX IF NOT EXISTS idx_module_progress_learner ON module_progress(learner_id);
CREATE INDEX IF NOT EXISTS idx_custom_lessons_cohort ON custom_lessons(cohort_id);
CREATE INDEX IF NOT EXISTS idx_custom_projects_cohort ON custom_projects(cohort_id);
CREATE INDEX IF NOT EXISTS idx_custom_project_checkpoints_project ON custom_project_checkpoints(project_id);
"""

# Columns added after the initial release of a table, applied defensively so
# a classroom.db created by an older version of this module keeps working
# without a separate migration step (SQLite has no "ADD COLUMN IF NOT
# EXISTS", so each is attempted and a "duplicate column" failure is normal
# and ignored).
_SQLITE_MIGRATIONS = (
    "ALTER TABLE assignments ADD COLUMN capability_settings TEXT",
    "ALTER TABLE assignments ADD COLUMN is_assessment INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE assignments ADD COLUMN start_at TEXT",
    "ALTER TABLE assignments ADD COLUMN end_at TEXT",
    "ALTER TABLE assignments ADD COLUMN locked INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE help_requests ADD COLUMN note TEXT",
    "ALTER TABLE assignments ADD COLUMN published_at TEXT",
)


def _sqlite_db_path() -> str:
    data_dir = os.environ.get("DATA_DIR", ".")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        pass
    return os.path.join(data_dir, "classroom.db")


_sqlite_schema_lock = threading.Lock()
_sqlite_schema_ready_for: set = set()


def _ensure_sqlite_schema(conn: sqlite3.Connection, path: str) -> None:
    # Schema creation/migration used to replay on *every* connect() call -
    # a full multi-table executescript() plus 7 ALTER TABLE attempts before
    # each single query. That dominated cohort-dashboard latency (each
    # learner triggered several connect() calls). Now it runs once per
    # database file per process; tests still get correct per-path isolation
    # since DATA_DIR (and so the resolved path) changes per test.
    if path in _sqlite_schema_ready_for:
        return
    with _sqlite_schema_lock:
        if path in _sqlite_schema_ready_for:
            return
        conn.executescript(SQLITE_SCHEMA)
        for statement in _SQLITE_MIGRATIONS:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        _sqlite_schema_ready_for.add(path)


@contextmanager
def _connect_sqlite() -> Iterator[sqlite3.Connection]:
    path = _sqlite_db_path()
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_sqlite_schema(conn, path)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PostgreSQL backend
# ---------------------------------------------------------------------------

_pool_lock = threading.Lock()
_pool: Optional[ConnectionPool] = None
_pool_url: Optional[str] = None

_schema_lock = threading.Lock()
_schema_ready_for: Optional[str] = None

# Arbitrary fixed key for the Postgres session advisory lock guarding
# schema migrations, so two application workers/threads starting at the
# same moment against a fresh database serialize instead of racing on
# "CREATE TABLE IF NOT EXISTS".
_SCHEMA_ADVISORY_LOCK_KEY = 895_617_234

_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO", re.IGNORECASE)


def _get_pool(url: str) -> ConnectionPool:
    global _pool, _pool_url
    with _pool_lock:
        if _pool is not None and _pool_url == url:
            return _pool
        if _pool is not None:
            try:
                _pool.close()
            except Exception:
                pass
            _pool = None
            _pool_url = None
        max_size = int(os.environ.get("CLASSROOM_PG_POOL_MAX", "5"))
        pool = ConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=max_size,
            kwargs={
                "autocommit": False,
                "row_factory": psycopg.rows.dict_row,
                # A pooled connection can sit idle between requests for a
                # while; without these, a network path that silently drops
                # the TCP connection (NAT/load-balancer idle timeout, a
                # brief network blip) leaves the next query blocked in a
                # socket select() with no upper bound - observed in testing
                # as an indefinite hang, not a clean error. Keepalives make
                # a dead connection surface as a normal OperationalError
                # within ~60s instead. statement_timeout is defense in
                # depth against a genuinely stuck query server-side -
                # every classroom query is a small, fast CRUD statement,
                # so 30s is a ceiling that should never trigger normally.
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
                "options": "-c statement_timeout=30000",
            },
            open=False,
        )
        connect_timeout = float(os.environ.get("CLASSROOM_PG_CONNECT_TIMEOUT", "10"))
        try:
            pool.open(wait=True, timeout=connect_timeout)
        except Exception as exc:
            try:
                pool.close()
            except Exception:
                pass
            raise ClassroomStorageError(
                "PostgreSQL classroom storage unavailable "
                f"({_sanitized_target(url)}): {exc.__class__.__name__}"
            ) from None
        _pool = pool
        _pool_url = url
        # Belt-and-suspenders: gunicorn worker shutdown should already be
        # graceful, but registering this means process exit never leaves a
        # background reconnect thread half-joined (psycopg_pool's own
        # __del__ can raise "cannot join thread at interpreter shutdown"
        # on abrupt exit otherwise). ConnectionPool.close() is idempotent,
        # so this is safe even if a later _get_pool() call closes the same
        # pool first.
        atexit.register(pool.close)
        return pool


def _ensure_postgres_schema(pool: ConnectionPool, url: str) -> None:
    global _schema_ready_for
    with _schema_lock:
        if _schema_ready_for == url:
            return
        try:
            with pool.connection() as conn:
                conn.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_ADVISORY_LOCK_KEY,))
                conn.commit()
                try:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS classroom_schema_version ("
                        "id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
                    )
                    conn.commit()
                    row = conn.execute(
                        "SELECT version FROM classroom_schema_version WHERE id = 1"
                    ).fetchone()
                    current_version = row["version"] if row else 0
                    for version, statements in _postgres_schema.MIGRATIONS:
                        if version <= current_version:
                            continue
                        for statement in statements:
                            conn.execute(statement)
                        conn.execute(
                            "INSERT INTO classroom_schema_version (id, version) VALUES (1, %s) "
                            "ON CONFLICT (id) DO UPDATE SET version = excluded.version",
                            (version,),
                        )
                        conn.commit()
                        current_version = version
                finally:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (_SCHEMA_ADVISORY_LOCK_KEY,))
                    conn.commit()
        except psycopg.Error:
            raise ClassroomStorageError(
                f"PostgreSQL classroom schema migration failed ({_sanitized_target(url)})"
            ) from None
        _schema_ready_for = url


class _PGCursor:
    """Duck-types the handful of sqlite3.Cursor members db.py touches."""

    __slots__ = ("_cursor", "lastrowid")

    def __init__(self, cursor: "psycopg.Cursor") -> None:
        self._cursor = cursor
        self.lastrowid: Optional[int] = None

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _PGConnection:
    """Duck-types the one sqlite3.Connection member db.py touches
    (``.execute``), translating ``?`` placeholders to ``%s`` and emulating
    ``cursor.lastrowid`` via ``RETURNING id`` for plain INSERTs."""

    __slots__ = ("_conn",)

    def __init__(self, conn: "psycopg.Connection") -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _PGCursor:
        pg_sql = sql.replace("?", "%s")
        upper = sql.upper()
        wants_id = bool(_INSERT_RE.match(sql)) and "RETURNING" not in upper and "ON CONFLICT" not in upper
        if wants_id:
            pg_sql = f"{pg_sql} RETURNING id"
        cursor = self._conn.execute(pg_sql, params)
        result = _PGCursor(cursor)
        if wants_id:
            row = cursor.fetchone()
            result.lastrowid = row["id"] if row else None
        return result

    def commit(self) -> None:
        self._conn.commit()


@contextmanager
def _connect_postgres(url: str) -> Iterator[_PGConnection]:
    pool = _get_pool(url)
    _ensure_postgres_schema(pool, url)
    conn_ctx = pool.connection()
    # Only *acquiring* a connection from the pool is wrapped as
    # ClassroomStorageError ("Postgres is unreachable"). Errors raised by
    # the caller's own SQL once a connection is in hand (e.g. a UNIQUE
    # violation from a join-code collision) must propagate unchanged - the
    # same way sqlite3.IntegrityError does from the SQLite path - so
    # db.py's own retry/handling logic (UNIQUE_VIOLATION_EXCEPTIONS) still
    # sees the real exception instead of a rewrapped one.
    try:
        conn = conn_ctx.__enter__()
    except Exception as exc:
        raise ClassroomStorageError(
            f"PostgreSQL classroom storage error ({_sanitized_target(url)}): {exc.__class__.__name__}"
        ) from None
    try:
        yield _PGConnection(conn)
    except BaseException:
        conn_ctx.__exit__(*sys.exc_info())
        raise
    else:
        conn_ctx.__exit__(None, None, None)


def ensure_schema(url: str) -> None:
    """Public entry point for callers outside this module (currently only
    scripts/migrate_classroom_sqlite_to_postgres.py) that need the
    destination schema ready before writing to it directly with their own
    connection, without duplicating the pool/advisory-lock/migration
    logic above."""
    pool = _get_pool(url)
    _ensure_postgres_schema(pool, url)


def schema_version() -> Optional[int]:
    """Current classroom_schema_version, or None on SQLite (which has no
    such table - see storage_status() in db.py for the SQLite-side
    diagnostic instead)."""
    if backend_name() != "postgres":
        return None
    with connect() as conn:
        row = conn.execute("SELECT version FROM classroom_schema_version WHERE id = 1").fetchone()
    return row["version"] if row else None

"""SQLite persistence for the classroom (cohort/instructor/assignment) layer.

Design notes:
  - One small file, ``classroom.db``, under ``DATA_DIR`` (same env var the
    rest of the app already uses for per-session JSON files). Resolved fresh
    on every call rather than cached at import time, so tests that
    monkeypatch ``DATA_DIR`` per-test get an isolated database.
  - A new connection is opened per call and closed immediately; at this
    scale (a single classroom, occasional writes) that is simpler and safer
    than sharing one connection across threads.
  - Schema is created with ``CREATE TABLE IF NOT EXISTS`` on every connect,
    which is idempotent and avoids a separate migration step.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import string
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

_JOIN_CODE_ALPHABET = "".join(
    ch for ch in (string.ascii_uppercase + string.digits) if ch not in "O0I1"
)

SCHEMA = """
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
    created_at TEXT NOT NULL,
    resolved_at TEXT
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
CREATE INDEX IF NOT EXISTS idx_help_requests_cohort ON help_requests(cohort_id, status);
"""


def _db_path() -> str:
    data_dir = os.environ.get("DATA_DIR", ".")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        pass
    return os.path.join(data_dir, "classroom.db")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_path(), timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_token() -> str:
    return secrets.token_urlsafe(24)


def new_join_code(conn: sqlite3.Connection, length: int = 6) -> str:
    for _ in range(50):
        code = "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(length))
        existing = conn.execute("SELECT 1 FROM cohorts WHERE join_code = ?", (code,)).fetchone()
        if not existing:
            return code
    raise RuntimeError("Could not generate a unique join code")


def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _rows(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


# ---- instructors ---------------------------------------------------------

def create_instructor(username: str, password_hash: str, display_name: str) -> Dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO instructors (username, password_hash, display_name, created_at) "
            "VALUES (?, ?, ?, ?)",
            (username, password_hash, display_name, now_iso()),
        )
        return _row(conn.execute("SELECT * FROM instructors WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_instructor(instructor_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM instructors WHERE id = ?", (instructor_id,)).fetchone())


def get_instructor_by_username(username: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(
            conn.execute("SELECT * FROM instructors WHERE username = ?", (username,)).fetchone()
        )


# ---- cohorts ---------------------------------------------------------------

def create_cohort(instructor_id: int, name: str) -> Dict[str, Any]:
    with connect() as conn:
        code = new_join_code(conn)
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO cohorts (instructor_id, name, join_code, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (instructor_id, name.strip(), code, ts, ts),
        )
        return _row(conn.execute("SELECT * FROM cohorts WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_cohort(cohort_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM cohorts WHERE id = ?", (cohort_id,)).fetchone())


def get_cohort_by_join_code(join_code: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(
            conn.execute(
                "SELECT * FROM cohorts WHERE join_code = ? AND status = 'active'",
                (join_code.strip().upper(),),
            ).fetchone()
        )


def list_cohorts_for_instructor(instructor_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        return _rows(
            conn.execute(
                "SELECT * FROM cohorts WHERE instructor_id = ? ORDER BY created_at DESC",
                (instructor_id,),
            ).fetchall()
        )


def rename_cohort(cohort_id: int, instructor_id: int, name: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE cohorts SET name = ?, updated_at = ? WHERE id = ? AND instructor_id = ?",
            (name.strip(), now_iso(), cohort_id, instructor_id),
        )
        return _row(conn.execute("SELECT * FROM cohorts WHERE id = ?", (cohort_id,)).fetchone())


def set_cohort_status(cohort_id: int, instructor_id: int, status: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE cohorts SET status = ?, updated_at = ? WHERE id = ? AND instructor_id = ?",
            (status, now_iso(), cohort_id, instructor_id),
        )
        return _row(conn.execute("SELECT * FROM cohorts WHERE id = ?", (cohort_id,)).fetchone())


# ---- learners / membership -------------------------------------------------

def join_cohort(cohort_id: int, display_name: str) -> Dict[str, Any]:
    with connect() as conn:
        token = new_token()
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO learners (cohort_id, display_name, token, joined_at, last_active_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cohort_id, display_name.strip()[:80], token, ts, ts),
        )
        return _row(conn.execute("SELECT * FROM learners WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_learner(learner_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM learners WHERE id = ?", (learner_id,)).fetchone())


def get_learner_by_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM learners WHERE token = ?", (token,)).fetchone())


def touch_learner_active(learner_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE learners SET last_active_at = ? WHERE id = ?", (now_iso(), learner_id)
        )


def list_learners_for_cohort(cohort_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        return _rows(
            conn.execute(
                "SELECT * FROM learners WHERE cohort_id = ? ORDER BY display_name COLLATE NOCASE",
                (cohort_id,),
            ).fetchall()
        )


# ---- assignments ------------------------------------------------------------

def create_assignment(
    cohort_id: int,
    title: str,
    instructions: str,
    starter_code: str,
    due_date: Optional[str],
    expected_concepts: List[str],
    ai_policy: str,
    status: str = "draft",
) -> Dict[str, Any]:
    with connect() as conn:
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO assignments "
            "(cohort_id, title, instructions, starter_code, due_date, expected_concepts, "
            " ai_policy, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cohort_id,
                title.strip(),
                instructions,
                starter_code,
                due_date,
                json.dumps(list(expected_concepts or [])),
                ai_policy,
                status,
                ts,
                ts,
            ),
        )
        row = _row(conn.execute("SELECT * FROM assignments WHERE id = ?", (cur.lastrowid,)).fetchone())
    if row:
        row["expected_concepts"] = list(expected_concepts or [])
    return row


def get_assignment(assignment_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = _row(conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone())
    if row:
        try:
            row["expected_concepts"] = json.loads(row.get("expected_concepts") or "[]")
        except (TypeError, ValueError):
            row["expected_concepts"] = []
    return row


def list_assignments_for_cohort(cohort_id: int, *, published_only: bool = False) -> List[Dict[str, Any]]:
    with connect() as conn:
        if published_only:
            rows = conn.execute(
                "SELECT * FROM assignments WHERE cohort_id = ? AND status = 'published' "
                "ORDER BY created_at DESC",
                (cohort_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM assignments WHERE cohort_id = ? ORDER BY created_at DESC",
                (cohort_id,),
            ).fetchall()
    out = _rows(rows)
    for row in out:
        try:
            row["expected_concepts"] = json.loads(row.get("expected_concepts") or "[]")
        except (TypeError, ValueError):
            row["expected_concepts"] = []
    return out


def publish_assignment(assignment_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE assignments SET status = 'published', updated_at = ? WHERE id = ?",
            (now_iso(), assignment_id),
        )
    return get_assignment(assignment_id)


def update_assignment_policy(assignment_id: int, ai_policy: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE assignments SET ai_policy = ?, updated_at = ? WHERE id = ?",
            (ai_policy, now_iso(), assignment_id),
        )
    return get_assignment(assignment_id)


# ---- assignment progress / submissions --------------------------------------

def get_or_create_progress(assignment_id: int, learner_id: int) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM assignment_progress WHERE assignment_id = ? AND learner_id = ?",
            (assignment_id, learner_id),
        ).fetchone()
        if row:
            return dict(row)
        ts = now_iso()
        assignment = conn.execute(
            "SELECT starter_code FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        starter = assignment["starter_code"] if assignment else ""
        cur = conn.execute(
            "INSERT INTO assignment_progress "
            "(assignment_id, learner_id, status, code, created_at, updated_at) "
            "VALUES (?, ?, 'not_started', ?, ?, ?)",
            (assignment_id, learner_id, starter, ts, ts),
        )
        return dict(
            conn.execute(
                "SELECT * FROM assignment_progress WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        )


def get_progress(assignment_id: int, learner_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(
            conn.execute(
                "SELECT * FROM assignment_progress WHERE assignment_id = ? AND learner_id = ?",
                (assignment_id, learner_id),
            ).fetchone()
        )


def save_progress_code(
    assignment_id: int,
    learner_id: int,
    code: str,
    *,
    ran: bool = False,
    run_ok: Optional[bool] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    get_or_create_progress(assignment_id, learner_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM assignment_progress WHERE assignment_id = ? AND learner_id = ?",
            (assignment_id, learner_id),
        ).fetchone()
        status = row["status"]
        if status == "not_started":
            status = "in_progress"
        run_count = row["run_count"]
        success_run_count = row["success_run_count"]
        last_error = row["last_error"]
        if ran:
            run_count += 1
            if run_ok:
                success_run_count += 1
                last_error = None
            elif error:
                last_error = error
        conn.execute(
            "UPDATE assignment_progress SET code = ?, status = ?, run_count = ?, "
            "success_run_count = ?, last_error = ?, last_saved_at = ?, updated_at = ? "
            "WHERE assignment_id = ? AND learner_id = ?",
            (
                code,
                status,
                run_count,
                success_run_count,
                last_error,
                now_iso(),
                now_iso(),
                assignment_id,
                learner_id,
            ),
        )
        return dict(
            conn.execute(
                "SELECT * FROM assignment_progress WHERE assignment_id = ? AND learner_id = ?",
                (assignment_id, learner_id),
            ).fetchone()
        )


def submit_assignment(assignment_id: int, learner_id: int, code: str) -> Dict[str, Any]:
    get_or_create_progress(assignment_id, learner_id)
    with connect() as conn:
        ts = now_iso()
        conn.execute(
            "UPDATE assignment_progress SET code = ?, submitted_code = ?, status = 'submitted', "
            "submitted_at = ?, last_saved_at = ?, updated_at = ? "
            "WHERE assignment_id = ? AND learner_id = ?",
            (code, code, ts, ts, ts, assignment_id, learner_id),
        )
        return dict(
            conn.execute(
                "SELECT * FROM assignment_progress WHERE assignment_id = ? AND learner_id = ?",
                (assignment_id, learner_id),
            ).fetchone()
        )


def list_progress_for_assignment(assignment_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        return _rows(
            conn.execute(
                "SELECT ap.*, l.display_name FROM assignment_progress ap "
                "JOIN learners l ON l.id = ap.learner_id WHERE ap.assignment_id = ? "
                "ORDER BY l.display_name COLLATE NOCASE",
                (assignment_id,),
            ).fetchall()
        )


def list_progress_for_learner(learner_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        return _rows(
            conn.execute(
                "SELECT * FROM assignment_progress WHERE learner_id = ?", (learner_id,)
            ).fetchall()
        )


# ---- help requests ------------------------------------------------------------

def create_help_request(
    cohort_id: int, learner_id: int, assignment_id: Optional[int], message: str
) -> Dict[str, Any]:
    with connect() as conn:
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO help_requests (cohort_id, learner_id, assignment_id, message, "
            "status, created_at) VALUES (?, ?, ?, ?, 'open', ?)",
            (cohort_id, learner_id, assignment_id, message[:2000], ts),
        )
        return _row(conn.execute("SELECT * FROM help_requests WHERE id = ?", (cur.lastrowid,)).fetchone())


def list_help_requests(cohort_id: int, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT hr.*, l.display_name FROM help_requests hr "
                "JOIN learners l ON l.id = hr.learner_id "
                "WHERE hr.cohort_id = ? AND hr.status = ? ORDER BY hr.created_at DESC",
                (cohort_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT hr.*, l.display_name FROM help_requests hr "
                "JOIN learners l ON l.id = hr.learner_id "
                "WHERE hr.cohort_id = ? ORDER BY hr.created_at DESC",
                (cohort_id,),
            ).fetchall()
        return _rows(rows)


def get_help_request(help_request_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(
            conn.execute("SELECT * FROM help_requests WHERE id = ?", (help_request_id,)).fetchone()
        )


def resolve_help_request(help_request_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE help_requests SET status = 'resolved', resolved_at = ? WHERE id = ?",
            (now_iso(), help_request_id),
        )
        return _row(
            conn.execute("SELECT * FROM help_requests WHERE id = ?", (help_request_id,)).fetchone()
        )


def cancel_help_request(help_request_id: int, learner_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE help_requests SET status = 'cancelled', resolved_at = ? "
            "WHERE id = ? AND learner_id = ?",
            (now_iso(), help_request_id, learner_id),
        )
        return _row(
            conn.execute("SELECT * FROM help_requests WHERE id = ?", (help_request_id,)).fetchone()
        )


# ---- lesson progress ------------------------------------------------------------

def upsert_lesson_progress(
    learner_id: int, lesson_id: str, status: str, *, last_code: Optional[str] = None
) -> Dict[str, Any]:
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM lesson_progress WHERE learner_id = ? AND lesson_id = ?",
            (learner_id, lesson_id),
        ).fetchone()
        ts = now_iso()
        if existing:
            attempts = existing["attempts"] + 1
            conn.execute(
                "UPDATE lesson_progress SET status = ?, attempts = ?, last_code = ?, "
                "updated_at = ? WHERE learner_id = ? AND lesson_id = ?",
                (status, attempts, last_code, ts, learner_id, lesson_id),
            )
        else:
            conn.execute(
                "INSERT INTO lesson_progress (learner_id, lesson_id, status, attempts, "
                "last_code, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (learner_id, lesson_id, status, last_code, ts),
            )
        return dict(
            conn.execute(
                "SELECT * FROM lesson_progress WHERE learner_id = ? AND lesson_id = ?",
                (learner_id, lesson_id),
            ).fetchone()
        )


def list_lesson_progress(learner_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        return _rows(
            conn.execute(
                "SELECT * FROM lesson_progress WHERE learner_id = ?", (learner_id,)
            ).fetchall()
        )


# ---- guided project progress ------------------------------------------------------------

def get_or_create_project_progress(learner_id: int, project_id: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM project_progress WHERE learner_id = ? AND project_id = ?",
            (learner_id, project_id),
        ).fetchone()
        if row:
            data = dict(row)
        else:
            ts = now_iso()
            cur = conn.execute(
                "INSERT INTO project_progress (learner_id, project_id, checkpoints_completed, "
                "updated_at) VALUES (?, ?, '[]', ?)",
                (learner_id, project_id, ts),
            )
            data = dict(
                conn.execute(
                    "SELECT * FROM project_progress WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
            )
    try:
        data["checkpoints_completed"] = json.loads(data.get("checkpoints_completed") or "[]")
    except (TypeError, ValueError):
        data["checkpoints_completed"] = []
    return data


def save_project_progress(
    learner_id: int,
    project_id: str,
    *,
    code: str,
    checkpoints_completed: List[str],
    active_file: Optional[str] = None,
    files: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    get_or_create_project_progress(learner_id, project_id)
    with connect() as conn:
        conn.execute(
            "UPDATE project_progress SET code = ?, checkpoints_completed = ?, active_file = ?, "
            "files = ?, updated_at = ? WHERE learner_id = ? AND project_id = ?",
            (
                code,
                json.dumps(list(checkpoints_completed or [])),
                active_file,
                json.dumps(files) if files else None,
                now_iso(),
                learner_id,
                project_id,
            ),
        )
        row = dict(
            conn.execute(
                "SELECT * FROM project_progress WHERE learner_id = ? AND project_id = ?",
                (learner_id, project_id),
            ).fetchone()
        )
    try:
        row["checkpoints_completed"] = json.loads(row.get("checkpoints_completed") or "[]")
    except (TypeError, ValueError):
        row["checkpoints_completed"] = []
    return row


def list_project_progress(learner_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = _rows(
            conn.execute(
                "SELECT * FROM project_progress WHERE learner_id = ?", (learner_id,)
            ).fetchall()
        )
    for row in rows:
        try:
            row["checkpoints_completed"] = json.loads(row.get("checkpoints_completed") or "[]")
        except (TypeError, ValueError):
            row["checkpoints_completed"] = []
    return rows


# ---- concept progress ------------------------------------------------------------

def get_concept_progress(learner_id: int) -> Dict[str, Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM concept_progress WHERE learner_id = ?", (learner_id,)
        ).fetchall()
    return {row["concept"]: dict(row) for row in rows}


def set_concept_state(
    learner_id: int, concept: str, state: str, *, bump_evidence: bool = True
) -> Dict[str, Any]:
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM concept_progress WHERE learner_id = ? AND concept = ?",
            (learner_id, concept),
        ).fetchone()
        ts = now_iso()
        if existing:
            evidence = existing["evidence_count"] + (1 if bump_evidence else 0)
            conn.execute(
                "UPDATE concept_progress SET state = ?, evidence_count = ?, last_evidence_at = ? "
                "WHERE learner_id = ? AND concept = ?",
                (state, evidence, ts, learner_id, concept),
            )
        else:
            conn.execute(
                "INSERT INTO concept_progress (learner_id, concept, state, evidence_count, "
                "last_evidence_at) VALUES (?, ?, ?, 1, ?)",
                (learner_id, concept, state, ts),
            )
        return dict(
            conn.execute(
                "SELECT * FROM concept_progress WHERE learner_id = ? AND concept = ?",
                (learner_id, concept),
            ).fetchone()
        )


# ---- progress events (generic activity log) ------------------------------------------------------------

def log_event(learner_id: int, cohort_id: Optional[int], kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO progress_events (learner_id, cohort_id, kind, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (learner_id, cohort_id, kind, json.dumps(payload or {}), now_iso()),
        )


def list_events_for_learner(learner_id: int, *, limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = _rows(
            conn.execute(
                "SELECT * FROM progress_events WHERE learner_id = ? ORDER BY created_at DESC LIMIT ?",
                (learner_id, limit),
            ).fetchall()
        )
    for row in rows:
        try:
            row["payload"] = json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            row["payload"] = {}
    return rows


def list_events_for_cohort(cohort_id: int, *, limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = _rows(
            conn.execute(
                "SELECT pe.*, l.display_name FROM progress_events pe "
                "JOIN learners l ON l.id = pe.learner_id "
                "WHERE pe.cohort_id = ? ORDER BY pe.created_at DESC LIMIT ?",
                (cohort_id, limit),
            ).fetchall()
        )
    for row in rows:
        try:
            row["payload"] = json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            row["payload"] = {}
    return rows

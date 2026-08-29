"""Persistence for the classroom (cohort/instructor/assignment) layer.

Backend selection (SQLite locally, PostgreSQL in production when
``DATABASE_URL`` is set) lives entirely in :mod:`codeup.classroom._storage`.
This module only ever calls ``_storage.connect()`` and writes SQL using
``?`` placeholders; every function below is backend-agnostic by
construction - see ``_storage.py`` for how that is made to work.

Design notes:
  - A connection is acquired per call (SQLite: opened and closed; Postgres:
    borrowed from and returned to a small pool) and closed/returned
    immediately - simpler and safer than sharing one connection across
    threads.
  - Callers should not need to know or care which backend is active;
    conditionals on backend type belong in ``_storage.py``, not here.
"""

from __future__ import annotations

import json
import secrets
import string
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from codeup.classroom import _storage
from codeup.classroom._storage import ClassroomStorageError, connect  # re-exported

_JOIN_CODE_ALPHABET = "".join(
    ch for ch in (string.ascii_uppercase + string.digits) if ch not in "O0I1"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_token() -> str:
    return secrets.token_urlsafe(24)


def new_join_code(conn: Any, length: int = 6) -> str:
    for _ in range(50):
        code = "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(length))
        existing = conn.execute("SELECT 1 FROM cohorts WHERE join_code = ?", (code,)).fetchone()
        if not existing:
            return code
    raise RuntimeError("Could not generate a unique join code")


def _row(row: Optional[Any]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _rows(rows: List[Any]) -> List[Dict[str, Any]]:
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
    # new_join_code() only checks-then-inserts, which is a TOCTOU race under
    # concurrent instructors creating cohorts at the same instant. The
    # UNIQUE(join_code) constraint is the real source of truth; if a rare
    # collision slips past the pre-check, retry with a fresh code instead
    # of surfacing a 500. Each attempt uses its own connection/transaction
    # so a failed insert never leaves a half-open one behind.
    last_error: Optional[Exception] = None
    for _ in range(5):
        try:
            with connect() as conn:
                code = new_join_code(conn)
                ts = now_iso()
                cur = conn.execute(
                    "INSERT INTO cohorts (instructor_id, name, join_code, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'active', ?, ?)",
                    (instructor_id, name.strip(), code, ts, ts),
                )
                return _row(
                    conn.execute("SELECT * FROM cohorts WHERE id = ?", (cur.lastrowid,)).fetchone()
                )
        except _storage.UNIQUE_VIOLATION_EXCEPTIONS as exc:
            last_error = exc
            continue
    raise RuntimeError("Could not create cohort with a unique join code") from last_error


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
                "SELECT * FROM learners WHERE cohort_id = ? ORDER BY LOWER(display_name)",
                (cohort_id,),
            ).fetchall()
        )


# ---- assignments ------------------------------------------------------------

def _decode_assignment(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    try:
        row["expected_concepts"] = json.loads(row.get("expected_concepts") or "[]")
    except (TypeError, ValueError):
        row["expected_concepts"] = []
    try:
        row["capability_settings"] = json.loads(row["capability_settings"]) if row.get("capability_settings") else None
    except (TypeError, ValueError):
        row["capability_settings"] = None
    row["is_assessment"] = bool(row.get("is_assessment"))
    row["locked"] = bool(row.get("locked"))
    return row


def create_assignment(
    cohort_id: int,
    title: str,
    instructions: str,
    starter_code: str,
    due_date: Optional[str],
    expected_concepts: List[str],
    ai_policy: str,
    status: str = "draft",
    *,
    capability_settings: Optional[Dict[str, bool]] = None,
    is_assessment: bool = False,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
) -> Dict[str, Any]:
    with connect() as conn:
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO assignments "
            "(cohort_id, title, instructions, starter_code, due_date, expected_concepts, "
            " ai_policy, capability_settings, is_assessment, start_at, end_at, status, "
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cohort_id,
                title.strip(),
                instructions,
                starter_code,
                due_date,
                json.dumps(list(expected_concepts or [])),
                ai_policy,
                json.dumps(capability_settings) if capability_settings else None,
                1 if is_assessment else 0,
                start_at,
                end_at,
                status,
                ts,
                ts,
            ),
        )
        row = _row(conn.execute("SELECT * FROM assignments WHERE id = ?", (cur.lastrowid,)).fetchone())
    return _decode_assignment(row)


def get_assignment(assignment_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = _row(conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone())
    return _decode_assignment(row)


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
    return [_decode_assignment(row) for row in _rows(rows)]


def publish_assignment(assignment_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        ts = now_iso()
        # published_at is set only the first time (COALESCE) so re-saving an
        # already-published assignment never makes it look newly published
        # to a learner who already saw it.
        conn.execute(
            "UPDATE assignments SET status = 'published', updated_at = ?, "
            "published_at = COALESCE(published_at, ?) WHERE id = ?",
            (ts, ts, assignment_id),
        )
    return get_assignment(assignment_id)


def update_assignment_policy(assignment_id: int, ai_policy: str) -> Optional[Dict[str, Any]]:
    """Apply a preset: sets both the label and the enforced settings to that
    preset's defaults (an instructor can fine-tune afterward via
    update_assignment_settings)."""
    from codeup.classroom import ai_policy as ai_policy_mod
    settings = ai_policy_mod.default_settings_for_preset(ai_policy)
    with connect() as conn:
        conn.execute(
            "UPDATE assignments SET ai_policy = ?, capability_settings = ?, updated_at = ? WHERE id = ?",
            (ai_policy_mod.normalize_policy(ai_policy), json.dumps(settings), now_iso(), assignment_id),
        )
    return get_assignment(assignment_id)


def update_assignment_settings(assignment_id: int, settings: Dict[str, bool]) -> Optional[Dict[str, Any]]:
    """Fine-tune individual capability toggles without changing the preset label."""
    with connect() as conn:
        conn.execute(
            "UPDATE assignments SET capability_settings = ?, updated_at = ? WHERE id = ?",
            (json.dumps(settings), now_iso(), assignment_id),
        )
    return get_assignment(assignment_id)


def set_assignment_lock(assignment_id: int, locked: bool) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE assignments SET locked = ?, updated_at = ? WHERE id = ?",
            (1 if locked else 0, now_iso(), assignment_id),
        )
    return get_assignment(assignment_id)


def set_assignment_schedule(
    assignment_id: int, *, start_at: Optional[str], due_date: Optional[str], end_at: Optional[str]
) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE assignments SET start_at = ?, due_date = ?, end_at = ?, updated_at = ? WHERE id = ?",
            (start_at, due_date, end_at, now_iso(), assignment_id),
        )
    return get_assignment(assignment_id)


def duplicate_assignment(assignment_id: int, *, as_assessment: bool = False) -> Optional[Dict[str, Any]]:
    """Copy an assignment (fresh draft, no learner progress carried over).
    If as_assessment, marks it as an assessment and resets capability
    settings to the strict ASSESSMENT preset as a starting point."""
    from codeup.classroom import ai_policy as ai_policy_mod
    source = get_assignment(assignment_id)
    if not source:
        return None
    title = source["title"] + (" (Assessment copy)" if as_assessment else " (copy)")
    settings = ai_policy_mod.default_settings_for_preset("ASSESSMENT") if as_assessment else source.get("capability_settings")
    return create_assignment(
        source["cohort_id"], title, source["instructions"], source["starter_code"],
        None, source["expected_concepts"], "ASSESSMENT" if as_assessment else source["ai_policy"],
        status="draft", capability_settings=settings, is_assessment=as_assessment,
    )


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
        # ON CONFLICT DO NOTHING (not "insert if row was None above") closes
        # the race where two requests for the same learner/assignment both
        # see no row and both try to initialize it - only one insert wins,
        # the loser's INSERT is a no-op, and the unconditional re-SELECT
        # below returns the winner's row either way.
        conn.execute(
            "INSERT INTO assignment_progress "
            "(assignment_id, learner_id, status, code, created_at, updated_at) "
            "VALUES (?, ?, 'not_started', ?, ?, ?) "
            "ON CONFLICT (assignment_id, learner_id) DO NOTHING",
            (assignment_id, learner_id, starter, ts, ts),
        )
        return dict(
            conn.execute(
                "SELECT * FROM assignment_progress WHERE assignment_id = ? AND learner_id = ?",
                (assignment_id, learner_id),
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
                "ORDER BY LOWER(l.display_name)",
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


def list_progress_for_learners(learner_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Batched equivalent of calling :func:`list_progress_for_learner` once per
    id - one round trip for the whole cohort instead of one per learner."""
    grouped: Dict[int, List[Dict[str, Any]]] = {lid: [] for lid in learner_ids}
    if not learner_ids:
        return grouped
    placeholders = ",".join("?" for _ in learner_ids)
    with connect() as conn:
        rows = _rows(
            conn.execute(
                f"SELECT * FROM assignment_progress WHERE learner_id IN ({placeholders})",
                tuple(learner_ids),
            ).fetchall()
        )
    for row in rows:
        grouped.setdefault(row["learner_id"], []).append(row)
    return grouped


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


def resolve_help_request(help_request_id: int, note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE help_requests SET status = 'resolved', resolved_at = ?, note = COALESCE(?, note) "
            "WHERE id = ?",
            (now_iso(), note, help_request_id),
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


def mark_help_request_helping(help_request_id: int, note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        conn.execute(
            "UPDATE help_requests SET status = 'helping', note = COALESCE(?, note) WHERE id = ?",
            (note, help_request_id),
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
            # ON CONFLICT DO NOTHING: see get_or_create_progress() above for
            # why this closes the concurrent-double-initialization race.
            conn.execute(
                "INSERT INTO project_progress (learner_id, project_id, checkpoints_completed, "
                "updated_at) VALUES (?, ?, '[]', ?) "
                "ON CONFLICT (learner_id, project_id) DO NOTHING",
                (learner_id, project_id, ts),
            )
            data = dict(
                conn.execute(
                    "SELECT * FROM project_progress WHERE learner_id = ? AND project_id = ?",
                    (learner_id, project_id),
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


def get_concept_progress_for_learners(learner_ids: List[int]) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """Batched equivalent of calling :func:`get_concept_progress` once per id."""
    grouped: Dict[int, Dict[str, Dict[str, Any]]] = {lid: {} for lid in learner_ids}
    if not learner_ids:
        return grouped
    placeholders = ",".join("?" for _ in learner_ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM concept_progress WHERE learner_id IN ({placeholders})",
            tuple(learner_ids),
        ).fetchall()
    for row in rows:
        row = dict(row)
        grouped.setdefault(row["learner_id"], {})[row["concept"]] = row
    return grouped


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


# ---- curriculum resume/restart state ------------------------------------------------------------

DEFAULT_COURSE_ID = "python_foundations"


def get_curriculum_state(learner_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(
            conn.execute(
                "SELECT * FROM curriculum_progress WHERE learner_id = ?", (learner_id,)
            ).fetchone()
        )


def set_curriculum_position(learner_id: int, module_id: str, stage: str) -> Dict[str, Any]:
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM curriculum_progress WHERE learner_id = ?", (learner_id,)
        ).fetchone()
        ts = now_iso()
        if existing:
            conn.execute(
                "UPDATE curriculum_progress SET current_module_id = ?, current_stage = ?, "
                "last_activity_at = ? WHERE learner_id = ?",
                (module_id, stage, ts, learner_id),
            )
        else:
            conn.execute(
                "INSERT INTO curriculum_progress (learner_id, course_id, current_module_id, "
                "current_stage, started_at, last_activity_at) VALUES (?, ?, ?, ?, ?, ?)",
                (learner_id, DEFAULT_COURSE_ID, module_id, stage, ts, ts),
            )
        return dict(
            conn.execute(
                "SELECT * FROM curriculum_progress WHERE learner_id = ?", (learner_id,)
            ).fetchone()
        )


def clear_curriculum_state(learner_id: int) -> None:
    """Used by "restart entire course" - resets the resume pointer only.
    Per-module progress rows are left alone by default (callers decide
    whether to also reset those)."""
    with connect() as conn:
        conn.execute("DELETE FROM curriculum_progress WHERE learner_id = ?", (learner_id,))


# ---- module progress (built-in and instructor-created lessons) ----------------------------------

def get_module_progress(learner_id: int, module_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = _row(
            conn.execute(
                "SELECT * FROM module_progress WHERE learner_id = ? AND module_id = ?",
                (learner_id, module_id),
            ).fetchone()
        )
    if row:
        try:
            row["completed_stages"] = json.loads(row.get("completed_stages") or "[]")
        except (TypeError, ValueError):
            row["completed_stages"] = []
    return row


def list_module_progress(learner_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = _rows(
            conn.execute(
                "SELECT * FROM module_progress WHERE learner_id = ?", (learner_id,)
            ).fetchall()
        )
    for row in rows:
        try:
            row["completed_stages"] = json.loads(row.get("completed_stages") or "[]")
        except (TypeError, ValueError):
            row["completed_stages"] = []
    return rows


def upsert_module_stage(
    learner_id: int, module_id: str, stage: str, *, status: Optional[str] = None
) -> Dict[str, Any]:
    """Record that a learner reached/completed one stage (example/attempt/
    challenge/quiz) of a module. Conservative: 'completed_stages' only ever
    grows, status only ever moves forward (not_started -> in_progress ->
    completed), matching the "never invent, never silently regress"
    convention used by concept_progress."""
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM module_progress WHERE learner_id = ? AND module_id = ?",
            (learner_id, module_id),
        ).fetchone()
        ts = now_iso()
        if existing:
            try:
                stages = json.loads(existing["completed_stages"] or "[]")
            except (TypeError, ValueError):
                stages = []
            if stage not in stages:
                stages.append(stage)
            new_status = status or existing["status"]
            if existing["status"] == "completed":
                new_status = "completed"  # never regress a completed module
            conn.execute(
                "UPDATE module_progress SET completed_stages = ?, status = ?, attempts = attempts + 1, "
                "completed_at = CASE WHEN ? = 'completed' AND completed_at IS NULL THEN ? ELSE completed_at END, "
                "updated_at = ? WHERE learner_id = ? AND module_id = ?",
                (json.dumps(stages), new_status, new_status, ts, ts, learner_id, module_id),
            )
        else:
            conn.execute(
                "INSERT INTO module_progress (learner_id, module_id, status, completed_stages, "
                "attempts, completed_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (
                    learner_id, module_id, status or "in_progress", json.dumps([stage]),
                    ts if status == "completed" else None, ts,
                ),
            )
        return dict(
            conn.execute(
                "SELECT * FROM module_progress WHERE learner_id = ? AND module_id = ?",
                (learner_id, module_id),
            ).fetchone()
        )


def record_quiz_result(learner_id: int, module_id: str, score: int, total: int) -> Dict[str, Any]:
    get_or_create_module_progress_row(learner_id, module_id)
    with connect() as conn:
        conn.execute(
            "UPDATE module_progress SET quiz_score = ?, quiz_total = ?, updated_at = ? "
            "WHERE learner_id = ? AND module_id = ?",
            (score, total, now_iso(), learner_id, module_id),
        )
        return dict(
            conn.execute(
                "SELECT * FROM module_progress WHERE learner_id = ? AND module_id = ?",
                (learner_id, module_id),
            ).fetchone()
        )


def get_or_create_module_progress_row(learner_id: int, module_id: str) -> Dict[str, Any]:
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM module_progress WHERE learner_id = ? AND module_id = ?",
            (learner_id, module_id),
        ).fetchone()
        if existing:
            row = dict(existing)
        else:
            ts = now_iso()
            # ON CONFLICT DO NOTHING: see get_or_create_progress() above for
            # why this closes the concurrent-double-initialization race.
            conn.execute(
                "INSERT INTO module_progress (learner_id, module_id, status, completed_stages, "
                "attempts, updated_at) VALUES (?, ?, 'not_started', '[]', 0, ?) "
                "ON CONFLICT (learner_id, module_id) DO NOTHING",
                (learner_id, module_id, ts),
            )
            row = dict(
                conn.execute(
                    "SELECT * FROM module_progress WHERE learner_id = ? AND module_id = ?",
                    (learner_id, module_id),
                ).fetchone()
            )
    try:
        row["completed_stages"] = json.loads(row.get("completed_stages") or "[]")
    except (TypeError, ValueError):
        row["completed_stages"] = []
    return row


def reset_module_progress(learner_id: int, module_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM module_progress WHERE learner_id = ? AND module_id = ?",
            (learner_id, module_id),
        )


def reset_all_module_progress(learner_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM module_progress WHERE learner_id = ?", (learner_id,))


# ---- instructor-authored lessons ------------------------------------------------------------

def create_custom_lesson(
    cohort_id: int, instructor_id: int, *, title: str, objective: str, explanation: str,
    starter_code: str, instructions: str, expected_concepts: List[str], challenge: str,
    expected_output: Optional[str] = None, quiz_question: Optional[str] = None,
    quiz_choices: Optional[List[str]] = None, quiz_answer_index: Optional[int] = None,
) -> Dict[str, Any]:
    with connect() as conn:
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO custom_lessons (cohort_id, instructor_id, title, objective, explanation, "
            "starter_code, instructions, expected_concepts, challenge, expected_output, "
            "quiz_question, quiz_choices, quiz_answer_index, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cohort_id, instructor_id, title.strip(), objective, explanation, starter_code,
                instructions, json.dumps(list(expected_concepts or [])), challenge, expected_output,
                quiz_question, json.dumps(list(quiz_choices or [])), quiz_answer_index, ts, ts,
            ),
        )
        row = _row(conn.execute("SELECT * FROM custom_lessons WHERE id = ?", (cur.lastrowid,)).fetchone())
    return _decode_custom_lesson(row)


def _decode_custom_lesson(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    try:
        row["expected_concepts"] = json.loads(row.get("expected_concepts") or "[]")
    except (TypeError, ValueError):
        row["expected_concepts"] = []
    try:
        row["quiz_choices"] = json.loads(row.get("quiz_choices") or "[]")
    except (TypeError, ValueError):
        row["quiz_choices"] = []
    return row


def get_custom_lesson(lesson_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = _row(conn.execute("SELECT * FROM custom_lessons WHERE id = ?", (lesson_id,)).fetchone())
    return _decode_custom_lesson(row)


def list_custom_lessons(cohort_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = _rows(
            conn.execute(
                "SELECT * FROM custom_lessons WHERE cohort_id = ? ORDER BY created_at DESC",
                (cohort_id,),
            ).fetchall()
        )
    return [_decode_custom_lesson(r) for r in rows]


# ---- instructor-authored guided projects -----------------------------------------------------

def create_custom_project(
    cohort_id: int, instructor_id: int, *, title: str, instructions: str, starter_code: str,
    expected_concepts: List[str], checkpoints: List[Dict[str, Any]],
) -> Dict[str, Any]:
    with connect() as conn:
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO custom_projects (cohort_id, instructor_id, title, instructions, "
            "starter_code, expected_concepts, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cohort_id, instructor_id, title.strip(), instructions, starter_code,
                json.dumps(list(expected_concepts or [])), ts, ts,
            ),
        )
        project_id = cur.lastrowid
        for idx, cp in enumerate(checkpoints or []):
            conn.execute(
                "INSERT INTO custom_project_checkpoints (project_id, order_index, label, "
                "check_type, check_config) VALUES (?, ?, ?, ?, ?)",
                (
                    project_id, idx, str(cp.get("label") or f"Checkpoint {idx + 1}"),
                    str(cp.get("check_type") or "contains_print"),
                    json.dumps(cp.get("check_config") or {}),
                ),
            )
        row = _row(conn.execute("SELECT * FROM custom_projects WHERE id = ?", (project_id,)).fetchone())
    return _decode_custom_project(row)


def _decode_custom_project(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    try:
        row["expected_concepts"] = json.loads(row.get("expected_concepts") or "[]")
    except (TypeError, ValueError):
        row["expected_concepts"] = []
    return row


def get_custom_project(project_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = _row(conn.execute("SELECT * FROM custom_projects WHERE id = ?", (project_id,)).fetchone())
        if row is None:
            return None
        checkpoints = _rows(
            conn.execute(
                "SELECT * FROM custom_project_checkpoints WHERE project_id = ? ORDER BY order_index",
                (project_id,),
            ).fetchall()
        )
    project = _decode_custom_project(row)
    for cp in checkpoints:
        try:
            cp["check_config"] = json.loads(cp.get("check_config") or "{}")
        except (TypeError, ValueError):
            cp["check_config"] = {}
    project["checkpoints"] = checkpoints
    return project


def list_custom_projects(cohort_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = _rows(
            conn.execute(
                "SELECT * FROM custom_projects WHERE cohort_id = ? ORDER BY created_at DESC",
                (cohort_id,),
            ).fetchall()
        )
    return [_decode_custom_project(r) for r in rows]


# ---- onboarding ------------------------------------------------------------

def onboarding_completed(learner_id: int) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT completed FROM onboarding_progress WHERE learner_id = ?", (learner_id,)
        ).fetchone()
    return bool(row and row["completed"])


def mark_onboarding_completed(learner_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO onboarding_progress (learner_id, completed, completed_at) VALUES (?, 1, ?) "
            "ON CONFLICT(learner_id) DO UPDATE SET completed = 1, completed_at = excluded.completed_at",
            (learner_id, now_iso()),
        )


# ---- learner notification state (IDE "new" tracking, orientation) ----------
#
# Deliberately a separate tiny table from onboarding_progress: onboarding is
# about the curriculum tutorial, this is about the IDE classroom panel's own
# "have they seen this yet" state (assignment list, first-run orientation).

def _get_notification_row(learner_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row(
            conn.execute(
                "SELECT * FROM learner_notification_state WHERE learner_id = ?", (learner_id,)
            ).fetchone()
        )


def get_assignments_seen_at(learner_id: int) -> Optional[str]:
    row = _get_notification_row(learner_id)
    return row["assignments_seen_at"] if row else None


def mark_assignments_seen(learner_id: int, when: Optional[str] = None) -> None:
    ts = when or now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO learner_notification_state (learner_id, assignments_seen_at) VALUES (?, ?) "
            "ON CONFLICT(learner_id) DO UPDATE SET assignments_seen_at = excluded.assignments_seen_at",
            (learner_id, ts),
        )


def get_ide_orientation_shown_at(learner_id: int) -> Optional[str]:
    row = _get_notification_row(learner_id)
    return row["ide_orientation_at"] if row else None


def mark_ide_orientation_shown(learner_id: int, when: Optional[str] = None) -> None:
    ts = when or now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO learner_notification_state (learner_id, ide_orientation_at) VALUES (?, ?) "
            "ON CONFLICT(learner_id) DO UPDATE SET ide_orientation_at = excluded.ide_orientation_at",
            (learner_id, ts),
        )


# ---- storage diagnostic ------------------------------------------------------------
#
# Safe for an authenticated instructor/admin screen: never returns
# DATABASE_URL, host, username, password, tokens, or learner data - only
# which backend is active and whether it is currently reachable.

def storage_status() -> Dict[str, Any]:
    backend = _storage.backend_name()
    if backend != "postgres":
        return {"backend": "sqlite", "healthy": True, "database_path_configured": True}
    try:
        version = _storage.schema_version()
    except ClassroomStorageError:
        return {
            "backend": "postgres",
            "healthy": False,
            "error": "PostgreSQL classroom storage unavailable - see server logs.",
        }
    return {"backend": "postgres", "healthy": True, "schema_version": version}

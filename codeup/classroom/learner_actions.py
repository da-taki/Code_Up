"""Shared learner write-path actions.

Extracted so the IDE's typed/voice command pipeline (``codeup.classroom.
ide_commands`` -> app.py's ``/voice-command``) and the classroom Flask views
(``codeup.classroom.routes``) perform submissions, help requests and cohort
joins through the exact same logic - never two competing implementations of
"what does submit actually do". Plain functions, no Flask/request objects,
so both callers (and tests) can use them directly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from codeup.classroom import concepts as concepts_mod
from codeup.classroom import db


def join_cohort_by_code(join_code: str, display_name: str) -> Dict[str, Any]:
    """Join a cohort by its join code. Returns a result dict:

    {"success": True, "cohort": {...}, "learner": {...}}
    or
    {"success": False, "error": "missing_fields" | "not_found"}
    """
    code = (join_code or "").strip().upper()
    name = (display_name or "").strip()
    if not code or not name:
        return {"success": False, "error": "missing_fields"}
    cohort = db.get_cohort_by_join_code(code)
    if not cohort:
        return {"success": False, "error": "not_found"}
    learner = db.join_cohort(cohort["id"], name)
    try:
        db.log_event(learner["id"], cohort["id"], "learner_joined", {})
    except Exception:
        pass  # the join itself already succeeded; the activity-log entry is best-effort
    return {"success": True, "cohort": cohort, "learner": learner}


def submit_current_assignment(learner: Dict[str, Any], assignment: Dict[str, Any], code: str) -> Dict[str, Any]:
    """Persist a submission for ``assignment`` and record downstream progress
    (concept credit, activity log). Mirrors ``routes.submit_assignment``
    exactly because that view calls this same function."""
    progress = db.submit_assignment(assignment["id"], learner["id"], code or "")
    try:
        concepts_mod.record_assignment_submitted(
            learner["id"], learner["cohort_id"], code or "", assignment.get("expected_concepts") or [],
        )
        db.log_event(learner["id"], learner["cohort_id"], "assignment_submitted", {"assignment_id": assignment["id"]})
        db.touch_learner_active(learner["id"])
    except Exception:
        pass  # the submission itself already succeeded; progress tracking is best-effort
    return progress


def send_help_request(learner: Dict[str, Any], message: str, assignment_id: Optional[int] = None) -> Dict[str, Any]:
    existing = current_help_request(learner)
    if existing:
        return existing
    hr = db.create_help_request(learner["cohort_id"], learner["id"], assignment_id, message or "")
    db.log_event(learner["id"], learner["cohort_id"], "help_requested", {"help_request_id": hr["id"]})
    return hr


def cancel_help_request_for_learner(learner: Dict[str, Any], help_request_id: int) -> Optional[Dict[str, Any]]:
    return db.cancel_help_request(help_request_id, learner["id"])


def current_help_request(learner: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The learner's own open/being-helped request, if any."""
    for hr in db.list_help_requests(learner["cohort_id"]):
        if hr["learner_id"] == learner["id"] and hr["status"] in ("open", "helping"):
            return hr
    return None

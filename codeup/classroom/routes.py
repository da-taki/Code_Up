"""Flask blueprint for the classroom (cohort/instructor/assignment) layer.

Kept separate from app.py's single-user IDE routes on purpose: this is the
only Flask-aware module in ``codeup.classroom`` (the rest - db, ai_policy,
concepts, guided_projects, reports - are plain, unit-testable logic), the
same separation of concerns the rest of the ``codeup`` package already
follows with app.py.

Most instructor/learner pages are traditional server-rendered forms (not a
JS single-page app): plain HTML tables, headings and forms are the least
risky way to keep every new screen keyboard- and screen-reader-friendly by
default. Only the in-editor assignment/project panel (which must live
inside the existing Monaco-based /ide page) needs client-side JS, and that
is added separately in static/classroom.js.
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from codeup.classroom import (
    ai_policy, concepts as concepts_mod, curriculum, db, guided_projects,
    learner_actions, learner_context, reports,
)
from codeup.providers import groq_pool

classroom_bp = Blueprint("classroom", __name__, url_prefix="/classroom")

INSTRUCTOR_SESSION_KEY = "classroom_instructor_id"
LEARNER_COOKIE = "cu_learner_token"
ASSIGNMENT_COOKIE = ai_policy.ASSIGNMENT_COOKIE
PROJECT_COOKIE = "cu_project_id"
MODULE_COOKIE = "cu_module_id"
LESSON_COOKIE = "cu_lesson_id"
LEARNER_COOKIE_MAX_AGE = 3600 * 24 * 180  # 180 days - a classroom device is reused all term


# ---- auth helpers -----------------------------------------------------------

def current_instructor() -> Optional[Dict[str, Any]]:
    instructor_id = session.get(INSTRUCTOR_SESSION_KEY)
    if not instructor_id:
        return None
    return db.get_instructor(instructor_id)


def require_instructor(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        instructor = current_instructor()
        if not instructor:
            if request.path.startswith("/classroom/api/") or request.is_json:
                return jsonify({"success": False, "error": "not_logged_in"}), 401
            return redirect(url_for("classroom.instructor_login"))
        return view(instructor, *args, **kwargs)

    return wrapped


def current_learner() -> Optional[Dict[str, Any]]:
    token = request.cookies.get(LEARNER_COOKIE)
    if not token:
        return None
    return db.get_learner_by_token(token)


def require_learner(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        learner = current_learner()
        if not learner:
            if request.is_json or request.path.startswith("/classroom/assignments/") or request.path.startswith("/classroom/projects/") or request.path.startswith("/classroom/help-requests"):
                return jsonify({"success": False, "error": "not_joined"}), 401
            return redirect(url_for("classroom.join_page"))
        return view(learner, *args, **kwargs)

    return wrapped


def _own_cohort_or_404(instructor: Dict[str, Any], cohort_id: int) -> Optional[Dict[str, Any]]:
    cohort = db.get_cohort(cohort_id)
    if not cohort or cohort["instructor_id"] != instructor["id"]:
        return None
    return cohort


def _assignment_cohort_or_404(instructor: Dict[str, Any], assignment_id: int):
    assignment = db.get_assignment(assignment_id)
    if not assignment:
        return None, None
    cohort = _own_cohort_or_404(instructor, assignment["cohort_id"])
    if not cohort:
        return None, None
    return assignment, cohort


def _learner_assignment_or_404(learner: Dict[str, Any], assignment_id: int):
    assignment = db.get_assignment(assignment_id)
    if not assignment or assignment["cohort_id"] != learner["cohort_id"]:
        return None
    return assignment


# ---- instructor auth ---------------------------------------------------------

@classroom_bp.route("/instructor/login", methods=["GET"])
def instructor_login():
    if current_instructor():
        return redirect(url_for("classroom.instructor_dashboard"))
    return render_template("classroom/login.html", error=request.args.get("error"))


@classroom_bp.route("/instructor/login", methods=["POST"])
def instructor_login_submit():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    instructor = db.get_instructor_by_username(username)
    if not instructor or not check_password_hash(instructor["password_hash"], password):
        return redirect(url_for("classroom.instructor_login", error="Incorrect username or password."))
    session[INSTRUCTOR_SESSION_KEY] = instructor["id"]
    return redirect(url_for("classroom.instructor_dashboard"))


@classroom_bp.route("/instructor/register", methods=["POST"])
def instructor_register_submit():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    display_name = (request.form.get("display_name") or username).strip()
    if not username or not password or len(password) < 8:
        return redirect(url_for(
            "classroom.instructor_login",
            error="Choose a username and a password of at least 8 characters.",
        ))
    if db.get_instructor_by_username(username):
        return redirect(url_for("classroom.instructor_login", error="That username is already taken."))
    instructor = db.create_instructor(username, generate_password_hash(password), display_name)
    session[INSTRUCTOR_SESSION_KEY] = instructor["id"]
    return redirect(url_for("classroom.instructor_dashboard"))


@classroom_bp.route("/instructor/logout", methods=["POST"])
def instructor_logout():
    session.pop(INSTRUCTOR_SESSION_KEY, None)
    return redirect(url_for("classroom.instructor_login"))


# ---- instructor: cohorts ---------------------------------------------------------

@classroom_bp.route("/instructor", methods=["GET"])
@require_instructor
def instructor_dashboard(instructor):
    cohorts = db.list_cohorts_for_instructor(instructor["id"])
    for cohort in cohorts:
        cohort["learner_count"] = len(db.list_learners_for_cohort(cohort["id"]))
    return render_template(
        "classroom/instructor_dashboard.html", instructor=instructor, cohorts=cohorts,
        groq_status_url=url_for("classroom.groq_status"),
    )


@classroom_bp.route("/admin/groq-status", methods=["GET"])
@require_instructor
def groq_status(instructor):
    """Read-only diagnostic: pool health/load, never a secret. Any signed-in
    instructor can view it (there is no separate super-admin tier in
    CodeUp) - learners cannot reach this, since @require_instructor
    redirects them to the instructor login page."""
    return render_template("classroom/groq_status.html", instructor=instructor, pool=groq_pool.status())


@classroom_bp.route("/cohorts", methods=["POST"])
@require_instructor
def create_cohort(instructor):
    name = (request.form.get("name") or "").strip()
    if name:
        db.create_cohort(instructor["id"], name)
    return redirect(url_for("classroom.instructor_dashboard"))


@classroom_bp.route("/cohorts/<int:cohort_id>/rename", methods=["POST"])
@require_instructor
def rename_cohort(instructor, cohort_id):
    if not _own_cohort_or_404(instructor, cohort_id):
        return redirect(url_for("classroom.instructor_dashboard"))
    name = (request.form.get("name") or "").strip()
    if name:
        db.rename_cohort(cohort_id, instructor["id"], name)
    return redirect(url_for("classroom.cohort_dashboard", cohort_id=cohort_id))


@classroom_bp.route("/cohorts/<int:cohort_id>/archive", methods=["POST"])
@require_instructor
def archive_cohort(instructor, cohort_id):
    if _own_cohort_or_404(instructor, cohort_id):
        db.set_cohort_status(cohort_id, instructor["id"], "archived")
    return redirect(url_for("classroom.instructor_dashboard"))


@classroom_bp.route("/cohorts/<int:cohort_id>/activate", methods=["POST"])
@require_instructor
def activate_cohort(instructor, cohort_id):
    if _own_cohort_or_404(instructor, cohort_id):
        db.set_cohort_status(cohort_id, instructor["id"], "active")
    return redirect(url_for("classroom.instructor_dashboard"))


_LIVE_WORKING_WINDOW_MINUTES = 15


def _derive_live_status(learner: Dict[str, Any], events: list, help_status: Optional[str]) -> str:
    """A lightweight, non-polling "what is this learner doing" label -
    computed once at page render from real stored data (events, help
    requests, last_active_at), never from a background poll."""
    if help_status == "helping":
        return "instructor helping"
    if help_status == "open":
        return "requested help"
    last_active = learner.get("last_active_at")
    if not last_active:
        return "offline"
    minutes = _waiting_minutes(last_active)
    if minutes > _LIVE_WORKING_WINDOW_MINUTES:
        return "offline"
    for event in events:
        kind = event.get("kind")
        if kind == "assignment_submitted":
            return "submitted"
        if kind in ("run_success", "run_failure"):
            return "running code"
    return "working"


def _help_status_by_learner(cohort_id):
    """Shared by cohort_dashboard and cohort_live_summary so their "is this
    learner waiting for help" classification can never quietly drift apart."""
    open_help = db.list_help_requests(cohort_id, status="open")
    helping_help = db.list_help_requests(cohort_id, status="helping")
    status = {hr["learner_id"]: "open" for hr in open_help}
    status.update({hr["learner_id"]: "helping" for hr in helping_help})
    return status, open_help


def _learner_progress(learner, assignments, help_status_by_learner):
    """One learner's row of progress data - the exact same DB reads and
    computation for both the rendered cohort dashboard and its JSON
    live-summary poll target (static/instructor-sync.js), so a "shortcut"
    taken in one can never silently diverge from the other."""
    progress_rows = db.list_progress_for_learner(learner["id"])
    submitted = sum(1 for r in progress_rows if r["status"] == "submitted")
    concept_states = concepts_mod.summary_for_learner(learner["id"])
    demonstrated = sum(1 for s in concept_states.values() if s == "demonstrated")
    events = db.list_events_for_learner(learner["id"], limit=5)
    module_rows = db.list_module_progress(learner["id"])
    modules_completed = sum(1 for m in module_rows if m["status"] == "completed")
    return {
        "assignments_submitted": submitted,
        "assignments_total": len(assignments),
        "concepts_demonstrated": demonstrated,
        "concepts_total": len(concepts_mod.CURRICULUM_CONCEPTS),
        "recent_activity": events,
        "modules_completed": modules_completed,
        "modules_total": len(curriculum.MODULE_ORDER),
        "live_status": _derive_live_status(learner, events, help_status_by_learner.get(learner["id"])),
    }


@classroom_bp.route("/cohorts/<int:cohort_id>", methods=["GET"])
@require_instructor
def cohort_dashboard(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))

    learners = db.list_learners_for_cohort(cohort_id)
    assignments = db.list_assignments_for_cohort(cohort_id)
    help_status_by_learner, open_help = _help_status_by_learner(cohort_id)

    learner_rows = [
        {"learner": learner, **_learner_progress(learner, assignments, help_status_by_learner)}
        for learner in learners
    ]

    impact = reports.build_cohort_impact_summary(cohort_id)

    return render_template(
        "classroom/cohort_dashboard.html",
        instructor=instructor, cohort=cohort, learner_rows=learner_rows,
        assignments=assignments, open_help_count=len(open_help),
        ai_policies=ai_policy.POLICIES, impact=impact,
    )


# Event kinds surfaced (and, client-side, announced) by cohort_live_summary -
# a deliberate allowlist, not every codeup.classroom.db.log_event() kind.
# progress_events also carries much higher-frequency, non-notification-
# worthy kinds (assignment_autosave fires on every debounced keystroke save,
# lesson_progress/quiz_submitted/module_restarted/concept_evidence are
# routine curriculum bookkeeping) that a screen-reader instructor must never
# hear about on a routine poll.
_LIVE_SUMMARY_EVENT_KINDS = {"learner_joined", "help_requested", "assignment_submitted"}
_LIVE_SUMMARY_EVENT_LIMIT = 20


@classroom_bp.route("/cohorts/<int:cohort_id>/live-summary", methods=["GET"])
@require_instructor
def cohort_live_summary(instructor, cohort_id):
    """Lightweight JSON poll target for the instructor dashboard's near-live
    sync (static/instructor-sync.js) - reuses exactly the same DB reads as
    cohort_dashboard above, just serialized for a targeted DOM patch instead
    of a full page render. No new infrastructure: plain polling, same data,
    same authorization.

    Also carries a small, cohort-scoped, allowlisted recent-events feed
    (each with a stable auto-increment id) so the client can announce a
    genuinely new join/help-request/submission exactly once - see
    _LIVE_SUMMARY_EVENT_KINDS above and instructor-sync.js's
    lastSeenEventId dedupe."""
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return jsonify({"success": False, "error": "not_found"}), 404

    learners = db.list_learners_for_cohort(cohort_id)
    assignments = db.list_assignments_for_cohort(cohort_id)
    help_status_by_learner, open_help = _help_status_by_learner(cohort_id)

    learner_rows = []
    for learner in learners:
        progress = _learner_progress(learner, assignments, help_status_by_learner)
        learner_rows.append({
            "id": learner["id"],
            "display_name": learner["display_name"],
            "detail_url": url_for("classroom.learner_detail", cohort_id=cohort_id, learner_id=learner["id"]),
            "live_status": progress["live_status"],
            "last_active_at": learner.get("last_active_at") or "Never",
            "modules_completed": progress["modules_completed"],
            "modules_total": progress["modules_total"],
            "assignments_submitted": progress["assignments_submitted"],
            "assignments_total": progress["assignments_total"],
            "concepts_demonstrated": progress["concepts_demonstrated"],
            "concepts_total": progress["concepts_total"],
        })

    assignment_titles = {a["id"]: a["title"] for a in assignments}
    events = []
    for e in db.list_events_for_cohort(cohort_id, limit=100):
        if e["kind"] not in _LIVE_SUMMARY_EVENT_KINDS:
            continue
        entry = {
            "id": e["id"], "kind": e["kind"],
            "learner_name": e.get("display_name") or "A learner",
            "created_at": e["created_at"],
        }
        if e["kind"] == "assignment_submitted":
            entry["assignment_title"] = assignment_titles.get((e.get("payload") or {}).get("assignment_id"))
        events.append(entry)
        if len(events) >= _LIVE_SUMMARY_EVENT_LIMIT:
            break

    return jsonify({
        "success": True,
        "learner_count": len(learners),
        "learners": learner_rows,
        "open_help_count": len(open_help),
        "events": events,
    })


@classroom_bp.route("/cohorts/<int:cohort_id>/learners/<int:learner_id>", methods=["GET"])
@require_instructor
def learner_detail(instructor, cohort_id, learner_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    learner = db.get_learner(learner_id)
    if not learner or learner["cohort_id"] != cohort_id:
        return redirect(url_for("classroom.cohort_dashboard", cohort_id=cohort_id))

    report = reports.build_learner_report(learner_id)
    progress_rows = db.list_progress_for_learner(learner_id)
    assignments = {a["id"]: a for a in db.list_assignments_for_cohort(cohort_id)}
    for row in progress_rows:
        row["assignment"] = assignments.get(row["assignment_id"])
    concept_states = concepts_mod.summary_for_learner(learner_id)
    events = db.list_events_for_learner(learner_id, limit=30)

    curriculum_state = db.get_curriculum_state(learner_id)
    module_rows = db.list_module_progress(learner_id)
    module_titles = {mid: curriculum.public_module(mid)["title"] for mid in curriculum.MODULE_ORDER}
    for custom_lesson in db.list_custom_lessons(cohort_id):
        module_titles[f"custom:{custom_lesson['id']}"] = custom_lesson["title"]
    for row in module_rows:
        row["title"] = module_titles.get(row["module_id"], row["module_id"])
    modules_completed = sum(1 for m in module_rows if m["status"] == "completed")
    current_module_title = None
    if curriculum_state and curriculum_state.get("current_module_id"):
        current_module_title = module_titles.get(curriculum_state["current_module_id"])

    return render_template(
        "classroom/learner_detail.html",
        instructor=instructor, cohort=cohort, learner=learner, report=report,
        progress_rows=progress_rows, concept_states=concept_states, events=events,
        module_rows=module_rows, modules_completed=modules_completed,
        modules_total=len(curriculum.MODULE_ORDER), curriculum_state=curriculum_state,
        current_module_title=current_module_title, onboarding_completed=db.onboarding_completed(learner_id),
    )


@classroom_bp.route("/cohorts/<int:cohort_id>/learners/<int:learner_id>/reset-module", methods=["POST"])
@require_instructor
def reset_learner_module(instructor, cohort_id, learner_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    learner = db.get_learner(learner_id)
    if cohort and learner and learner["cohort_id"] == cohort_id:
        module_id = request.form.get("module_id") or ""
        if module_id:
            db.reset_module_progress(learner_id, module_id)
    return redirect(url_for("classroom.learner_detail", cohort_id=cohort_id, learner_id=learner_id))


# ---- instructor: assignments ---------------------------------------------------------

@classroom_bp.route("/cohorts/<int:cohort_id>/assignments", methods=["POST"])
@require_instructor
def create_assignment(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))

    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("classroom.cohort_dashboard", cohort_id=cohort_id))
    instructions = request.form.get("instructions") or ""
    starter_code = request.form.get("starter_code") or ""
    due_date = (request.form.get("due_date") or "").strip() or None
    start_at = (request.form.get("start_at") or "").strip() or None
    end_at = (request.form.get("end_at") or "").strip() or None
    expected_concepts = [c.strip() for c in (request.form.get("expected_concepts") or "").split(",") if c.strip()]
    preset = ai_policy.normalize_policy(request.form.get("ai_policy"))
    is_assessment = request.form.get("is_assessment") == "on"

    assignment = db.create_assignment(
        cohort_id, title, instructions, starter_code, due_date, expected_concepts, preset,
        capability_settings=ai_policy.default_settings_for_preset(preset),
        is_assessment=is_assessment, start_at=start_at, end_at=end_at,
    )
    return redirect(url_for("classroom.assignment_detail", assignment_id=assignment["id"]))


@classroom_bp.route("/assignments/<int:assignment_id>", methods=["GET"])
@require_instructor
def assignment_detail(instructor, assignment_id):
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if not assignment:
        return redirect(url_for("classroom.instructor_dashboard"))

    progress_rows = db.list_progress_for_assignment(assignment_id)
    learners = {row["id"]: row for row in db.list_learners_for_cohort(cohort["id"])}
    progressed_ids = {row["learner_id"] for row in progress_rows}
    for learner_id, learner in learners.items():
        if learner_id not in progressed_ids:
            progress_rows.append({
                "learner_id": learner_id, "display_name": learner["display_name"],
                "status": "not_started", "run_count": 0, "success_run_count": 0,
                "submitted_at": None,
            })
    progress_rows.sort(key=lambda r: str(r.get("display_name") or "").lower())
    settings = assignment.get("capability_settings") or ai_policy.default_settings_for_preset(assignment["ai_policy"])

    return render_template(
        "classroom/assignment_detail.html",
        instructor=instructor, cohort=cohort, assignment=assignment,
        progress_rows=progress_rows, ai_policies=ai_policy.POLICIES,
        capability_settings=settings, capability_labels=ai_policy.CAPABILITY_LABELS,
        capabilities=ai_policy.CAPABILITIES,
        policy_summary=ai_policy.summarize_settings(settings, is_assessment=assignment["is_assessment"]),
    )


@classroom_bp.route("/assignments/<int:assignment_id>/publish", methods=["POST"])
@require_instructor
def publish_assignment(instructor, assignment_id):
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if assignment:
        db.publish_assignment(assignment_id)
    return redirect(url_for("classroom.assignment_detail", assignment_id=assignment_id))


@classroom_bp.route("/assignments/<int:assignment_id>/policy", methods=["POST"])
@require_instructor
def set_assignment_policy(instructor, assignment_id):
    """Apply a preset as a starting point (individual toggles can then be
    fine-tuned via /settings). Refuses if the assignment is locked."""
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if assignment and not assignment["locked"]:
        db.update_assignment_policy(assignment_id, ai_policy.normalize_policy(request.form.get("ai_policy")))
    return redirect(url_for("classroom.assignment_detail", assignment_id=assignment_id))


@classroom_bp.route("/assignments/<int:assignment_id>/settings", methods=["POST"])
@require_instructor
def update_assignment_settings(instructor, assignment_id):
    """Fine-tune the nine individual capability toggles directly."""
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if assignment and not assignment["locked"]:
        settings = {cap: (request.form.get(f"cap_{cap}") == "on") for cap in ai_policy.CAPABILITIES}
        db.update_assignment_settings(assignment_id, settings)
    return redirect(url_for("classroom.assignment_detail", assignment_id=assignment_id))


@classroom_bp.route("/assignments/<int:assignment_id>/lock", methods=["POST"])
@require_instructor
def lock_assignment(instructor, assignment_id):
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if assignment:
        db.set_assignment_lock(assignment_id, True)
    return redirect(url_for("classroom.assignment_detail", assignment_id=assignment_id))


@classroom_bp.route("/assignments/<int:assignment_id>/unlock", methods=["POST"])
@require_instructor
def unlock_assignment(instructor, assignment_id):
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if assignment:
        db.set_assignment_lock(assignment_id, False)
    return redirect(url_for("classroom.assignment_detail", assignment_id=assignment_id))


@classroom_bp.route("/assignments/<int:assignment_id>/schedule", methods=["POST"])
@require_instructor
def set_assignment_schedule(instructor, assignment_id):
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if assignment and not assignment["locked"]:
        db.set_assignment_schedule(
            assignment_id,
            start_at=(request.form.get("start_at") or "").strip() or None,
            due_date=(request.form.get("due_date") or "").strip() or None,
            end_at=(request.form.get("end_at") or "").strip() or None,
        )
    return redirect(url_for("classroom.assignment_detail", assignment_id=assignment_id))


@classroom_bp.route("/assignments/<int:assignment_id>/duplicate", methods=["POST"])
@require_instructor
def duplicate_assignment(instructor, assignment_id):
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if not assignment:
        return redirect(url_for("classroom.instructor_dashboard"))
    as_assessment = request.form.get("as_assessment") == "on"
    new_assignment = db.duplicate_assignment(assignment_id, as_assessment=as_assessment)
    return redirect(url_for("classroom.assignment_detail", assignment_id=new_assignment["id"]))


@classroom_bp.route("/assignments/<int:assignment_id>/submissions/<int:learner_id>", methods=["GET"])
@require_instructor
def view_submission(instructor, assignment_id, learner_id):
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if not assignment:
        return redirect(url_for("classroom.instructor_dashboard"))
    learner = db.get_learner(learner_id)
    if not learner or learner["cohort_id"] != cohort["id"]:
        return redirect(url_for("classroom.assignment_detail", assignment_id=assignment_id))
    progress = db.get_progress(assignment_id, learner_id) or {}
    return render_template(
        "classroom/submission_view.html",
        instructor=instructor, cohort=cohort, assignment=assignment, learner=learner, progress=progress,
    )


# ---- instructor: help queue ---------------------------------------------------------

def _waiting_minutes(created_at: str) -> int:
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created
    return max(0, int(delta.total_seconds() // 60))


@classroom_bp.route("/cohorts/<int:cohort_id>/help-requests", methods=["GET"])
@require_instructor
def help_queue(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    open_requests = db.list_help_requests(cohort_id, status="open")
    helping_requests = db.list_help_requests(cohort_id, status="helping")
    resolved_requests = db.list_help_requests(cohort_id, status="resolved")[:20]
    assignments = {a["id"]: a for a in db.list_assignments_for_cohort(cohort_id)}
    for hr in open_requests + helping_requests + resolved_requests:
        hr["assignment"] = assignments.get(hr.get("assignment_id"))
    for hr in open_requests + helping_requests:
        hr["waiting_minutes"] = _waiting_minutes(hr["created_at"])
    open_requests.sort(key=lambda hr: -hr["waiting_minutes"])
    return render_template(
        "classroom/help_queue.html",
        instructor=instructor, cohort=cohort, open_requests=open_requests,
        helping_requests=helping_requests, resolved_requests=resolved_requests,
    )


@classroom_bp.route("/help-requests/<int:help_request_id>/helping", methods=["POST"])
@require_instructor
def start_helping(instructor, help_request_id):
    hr = db.get_help_request(help_request_id)
    if hr and _own_cohort_or_404(instructor, hr["cohort_id"]):
        db.mark_help_request_helping(help_request_id, (request.form.get("note") or "").strip() or None)
        return redirect(url_for("classroom.help_queue", cohort_id=hr["cohort_id"]))
    return redirect(url_for("classroom.instructor_dashboard"))


@classroom_bp.route("/help-requests/<int:help_request_id>/resolve", methods=["POST"])
@require_instructor
def resolve_help_request(instructor, help_request_id):
    hr = db.get_help_request(help_request_id)
    if hr:
        cohort = _own_cohort_or_404(instructor, hr["cohort_id"])
        if cohort:
            db.resolve_help_request(help_request_id, (request.form.get("note") or "").strip() or None)
            return redirect(url_for("classroom.help_queue", cohort_id=hr["cohort_id"]))
    return redirect(url_for("classroom.instructor_dashboard"))


# ---- instructor: reports ---------------------------------------------------------

@classroom_bp.route("/cohorts/<int:cohort_id>/report", methods=["GET"])
@require_instructor
def cohort_report(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    report = reports.build_cohort_report(cohort_id)
    return render_template("classroom/report.html", instructor=instructor, cohort=cohort, report=report)


@classroom_bp.route("/cohorts/<int:cohort_id>/report.csv", methods=["GET"])
@require_instructor
def cohort_report_csv(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    csv_text = reports.cohort_report_csv(cohort_id)
    safe_name = "".join(ch for ch in cohort["name"] if ch.isalnum() or ch in " _-").strip() or "cohort"
    return Response(
        csv_text, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=\"{safe_name}-report.csv\""},
    )


# ---- learner: join ---------------------------------------------------------

@classroom_bp.route("/join", methods=["GET"])
def join_page():
    learner = current_learner()
    if learner:
        return redirect(url_for("classroom.learner_home"))
    return render_template("classroom/join.html", error=request.args.get("error"))


@classroom_bp.route("/join", methods=["POST"])
def join_submit():
    code = (request.form.get("join_code") or "").strip().upper()
    display_name = (request.form.get("display_name") or "").strip()
    result = learner_actions.join_cohort_by_code(code, display_name)
    if not result["success"]:
        error = "Enter a join code and your name." if result["error"] == "missing_fields" else "That join code was not found."
        return redirect(url_for("classroom.join_page", error=error))
    resp = redirect(url_for("classroom.learner_home"))
    resp.set_cookie(
        LEARNER_COOKIE, result["learner"]["token"], max_age=LEARNER_COOKIE_MAX_AGE,
        httponly=True, samesite="Lax",
    )
    return resp


@classroom_bp.route("/join-api", methods=["POST"])
def join_api():
    """JSON join endpoint used by the in-IDE "Join Classroom" panel and by
    typed/spoken join commands (``codeup.classroom.ide_commands``). Uses the
    exact same ``learner_actions.join_cohort_by_code`` as the classic HTML
    join form above - one join implementation, two entry points."""
    existing = current_learner()
    if existing:
        # Already joined: never create a second learner row for the same
        # browser/device. The learner must leave (see /classroom/leave)
        # before joining a different cohort.
        cohort = db.get_cohort(existing["cohort_id"])
        return jsonify({
            "success": False, "error": "already_joined",
            "message": f"You're already in {cohort['name'] if cohort else 'a classroom'}. "
                       "Leave that classroom first if you want to join a different one.",
        }), 409
    body = request.get_json(silent=True) or {}
    code = str(body.get("join_code") or body.get("code") or "")
    display_name = str(body.get("display_name") or body.get("name") or "")
    result = learner_actions.join_cohort_by_code(code, display_name)
    if not result["success"]:
        message = (
            "Enter a join code and your name." if result["error"] == "missing_fields"
            else "I couldn't find a classroom with that code. Check the code and try again."
        )
        return jsonify({"success": False, "error": result["error"], "message": message}), 400
    resp = jsonify({
        "success": True,
        "cohort": {"id": result["cohort"]["id"], "name": result["cohort"]["name"]},
        "learner": {"id": result["learner"]["id"], "display_name": result["learner"]["display_name"]},
        "message": f"You joined {result['cohort']['name']}.",
    })
    resp.set_cookie(
        LEARNER_COOKIE, result["learner"]["token"], max_age=LEARNER_COOKIE_MAX_AGE,
        httponly=True, samesite="Lax",
    )
    return resp


@classroom_bp.route("/leave/confirm", methods=["GET"])
@require_learner
def leave_confirm(learner):
    cohort = db.get_cohort(learner["cohort_id"])
    return render_template(
        "classroom/confirm.html",
        heading="Leave this classroom?",
        body=f"You will stop seeing {cohort['name'] if cohort else 'this'} assignments, projects and course "
             "position until you join again with a code. Nothing is deleted.",
        confirm_action=url_for("classroom.leave_cohort"),
        cancel_url=url_for("ide"),
        confirm_label="Yes, leave this classroom",
    )


@classroom_bp.route("/leave", methods=["POST"])
def leave_cohort():
    is_json = request.is_json
    resp = jsonify({"success": True}) if is_json else redirect(url_for("classroom.join_page"))
    resp.delete_cookie(LEARNER_COOKIE)
    resp.delete_cookie(ASSIGNMENT_COOKIE)
    resp.delete_cookie(PROJECT_COOKIE)
    resp.delete_cookie(MODULE_COOKIE)
    return resp


# ---- learner: home ---------------------------------------------------------

@classroom_bp.route("", methods=["GET"])
@classroom_bp.route("/learner", methods=["GET"])
@require_learner
def learner_home(learner):
    db.touch_learner_active(learner["id"])
    cohort = db.get_cohort(learner["cohort_id"])
    assignments = db.list_assignments_for_cohort(learner["cohort_id"], published_only=True)
    for a in assignments:
        progress = db.get_progress(a["id"], learner["id"])
        a["my_status"] = (progress or {}).get("status", "not_started")
    projects = guided_projects.list_projects()
    for p in projects:
        prog = db.get_or_create_project_progress(learner["id"], p["id"])
        p["completed_checkpoints"] = len(prog.get("checkpoints_completed") or [])
        p["total_checkpoints"] = len(p["checkpoints"])
        p["open_url"] = url_for("classroom.open_project", project_id=p["id"])
    for custom_summary in db.list_custom_projects(learner["cohort_id"]):
        custom = db.get_custom_project(custom_summary["id"])
        pid = f"custom:{custom['id']}"
        prog = db.get_or_create_project_progress(learner["id"], pid)
        projects.append({
            "id": pid, "title": custom["title"],
            "completed_checkpoints": len(prog.get("checkpoints_completed") or []),
            "total_checkpoints": len(custom.get("checkpoints") or []),
            "open_url": url_for("classroom.open_custom_project", project_id=custom["id"]),
        })
    my_help = [
        hr for hr in db.list_help_requests(learner["cohort_id"])
        if hr["learner_id"] == learner["id"] and hr["status"] in ("open", "helping")
    ]
    curriculum_state = db.get_curriculum_state(learner["id"])
    return render_template(
        "classroom/learner_home.html",
        learner=learner, cohort=cohort, assignments=assignments, projects=projects,
        open_help=my_help, curriculum_state=curriculum_state,
        onboarding_completed=db.onboarding_completed(learner["id"]),
    )


# ---- learner: IDE classroom panel (the primary learner workflow) -----------
#
# Everything below powers the always-on classroom panel inside /ide (see
# static/classroom.js) and the typed/spoken classroom commands (see
# codeup.classroom.ide_commands, wired into /voice-command in app.py).
# Deliberately returns {"success": False, "joined": False} instead of
# redirecting on no-cohort, since it is always called via fetch() from a
# page the learner is already on - a redirect would just be swallowed.

def _current_module_lookup(learner: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    state = db.get_curriculum_state(learner["id"])
    if not state or not state.get("current_module_id"):
        return None
    module_id = state["current_module_id"]
    lesson = _lesson_context(module_id)
    if not lesson:
        return None
    index = None
    if module_id in curriculum.MODULE_ORDER:
        index = curriculum.MODULE_ORDER.index(module_id) + 1
    return {
        "module_id": module_id, "title": lesson["title"],
        "index": index, "total": len(curriculum.MODULE_ORDER),
    }


def _learner_projects_with_progress(learner: Dict[str, Any]) -> List[Dict[str, Any]]:
    projects = guided_projects.list_projects()
    for p in projects:
        prog = db.get_or_create_project_progress(learner["id"], p["id"])
        p["completed_checkpoints"] = list(prog.get("checkpoints_completed") or [])
        p["updated_at"] = prog.get("updated_at")
    for custom_summary in db.list_custom_projects(learner["cohort_id"]):
        custom = db.get_custom_project(custom_summary["id"])
        pid = f"custom:{custom['id']}"
        prog = db.get_or_create_project_progress(learner["id"], pid)
        projects.append({
            "id": pid, "title": custom["title"],
            "checkpoints": [{"id": str(c["id"]), "label": c["label"]} for c in custom.get("checkpoints") or []],
            "completed_checkpoints": list(prog.get("checkpoints_completed") or []),
            "updated_at": prog.get("updated_at"),
        })
    return projects


def build_ide_summary(learner: Dict[str, Any]) -> Dict[str, Any]:
    """The full learner classroom snapshot: cohort, assignments (classified),
    current module, and guided-project progress. Shared by the JSON summary
    endpoint below and by ``ide_commands`` (typed/spoken classroom
    commands) so the panel and the voice/text answers never disagree."""
    cohort = db.get_cohort(learner["cohort_id"])
    assignments = db.list_assignments_for_cohort(learner["cohort_id"], published_only=True)
    progress_rows = {r["assignment_id"]: r for r in db.list_progress_for_learner(learner["id"])}
    progress_status = {aid: row["status"] for aid, row in progress_rows.items()}
    seen_at = db.get_assignments_seen_at(learner["id"])
    classified = learner_context.classify_assignments(assignments, progress_status, seen_at)
    for a in classified:
        a["updated_at"] = (progress_rows.get(a["id"]) or {}).get("updated_at")
        a["open_url"] = url_for("classroom.open_assignment", assignment_id=a["id"])
    counts = learner_context.summarize_assignment_states(classified)

    module = _current_module_lookup(learner)
    projects = _learner_projects_with_progress(learner)
    for p in projects:
        p["total_checkpoints"] = len(p["checkpoints"])
        p["done_checkpoints"] = len(p["completed_checkpoints"])
        p["open_url"] = (
            url_for("classroom.open_custom_project", project_id=int(p["id"].split(":", 1)[1]))
            if str(p["id"]).startswith("custom:") else url_for("classroom.open_project", project_id=p["id"])
        )

    help_request = learner_actions.current_help_request(learner)

    module_phrase = (
        learner_context.format_module_progress(module["title"], module["index"], module["total"])
        if module else ""
    )
    active_project = next(
        (p for p in projects if 0 < p["done_checkpoints"] < p["total_checkpoints"]), None,
    )
    project_phrase = (
        learner_context.format_project_progress(
            active_project["title"], active_project["done_checkpoints"], active_project["total_checkpoints"],
        ) if active_project else ""
    )
    cohort_name = cohort["name"] if cohort else ""
    welcome_message = learner_context.welcome_back_summary(
        learner["display_name"], cohort_name, counts, module_phrase, project_phrase, bool(active_project),
    )
    orientation_shown = bool(db.get_ide_orientation_shown_at(learner["id"]))

    return {
        "success": True, "joined": True,
        "learner": {"id": learner["id"], "display_name": learner["display_name"]},
        "cohort": {"id": cohort["id"], "name": cohort_name} if cohort else None,
        "assignments": classified, "assignment_counts": counts,
        "module": module, "projects": projects,
        "help_request": help_request,
        "onboarding_completed": db.onboarding_completed(learner["id"]),
        "ide_orientation_shown": orientation_shown,
        "welcome_message": welcome_message,
        "orientation_message": learner_context.joined_orientation(cohort_name) if cohort_name else "",
    }


@classroom_bp.route("/ide/summary", methods=["GET"])
def ide_summary():
    learner = current_learner()
    if not learner:
        return jsonify({
            "success": True, "joined": False,
            "orientation_message": learner_context.no_cohort_orientation(),
        })
    db.touch_learner_active(learner["id"])
    return jsonify(build_ide_summary(learner))


@classroom_bp.route("/ide/assignments-seen", methods=["POST"])
@require_learner
def ide_mark_assignments_seen(learner):
    db.mark_assignments_seen(learner["id"])
    return jsonify({"success": True})


@classroom_bp.route("/ide/orientation-seen", methods=["POST"])
def ide_mark_orientation_seen():
    learner = current_learner()
    if not learner:
        return jsonify({"success": False, "error": "not_joined"}), 401
    db.mark_ide_orientation_shown(learner["id"])
    return jsonify({"success": True})


# ---- learner: assignments (opened inside the IDE) ---------------------------------

@classroom_bp.route("/assignments/<int:assignment_id>/open", methods=["GET"])
@require_learner
def open_assignment(learner, assignment_id):
    assignment = _learner_assignment_or_404(learner, assignment_id)
    if not assignment:
        return redirect(url_for("classroom.learner_home"))
    db.get_or_create_progress(assignment_id, learner["id"])
    db.touch_learner_active(learner["id"])
    resp = redirect(url_for("ide") + f"?assignment={assignment_id}")
    resp.set_cookie(ASSIGNMENT_COOKIE, str(assignment_id), httponly=False, samesite="Lax")
    resp.delete_cookie(PROJECT_COOKIE)
    resp.delete_cookie(MODULE_COOKIE)
    return resp


@classroom_bp.route("/assignments/<int:assignment_id>/context", methods=["GET"])
@require_learner
def assignment_context(learner, assignment_id):
    assignment = _learner_assignment_or_404(learner, assignment_id)
    if not assignment:
        return jsonify({"success": False, "error": "not_found"}), 404
    progress = db.get_or_create_progress(assignment_id, learner["id"])
    settings = assignment.get("capability_settings") or ai_policy.default_settings_for_preset(assignment["ai_policy"])
    return jsonify({
        "success": True,
        "assignment": {
            "id": assignment["id"], "title": assignment["title"],
            "instructions": assignment["instructions"], "due_date": assignment["due_date"],
            "start_at": assignment.get("start_at"), "end_at": assignment.get("end_at"),
            "ai_policy": assignment["ai_policy"], "expected_concepts": assignment["expected_concepts"],
            "capability_settings": settings, "is_assessment": assignment["is_assessment"],
            "policy_summary": ai_policy.summarize_settings(settings, is_assessment=assignment["is_assessment"]),
        },
        "progress": {
            "status": progress["status"], "code": progress["code"],
            "submitted_at": progress.get("submitted_at"),
        },
    })


@classroom_bp.route("/assignments/<int:assignment_id>/autosave", methods=["POST"])
@require_learner
def autosave_assignment(learner, assignment_id):
    assignment = _learner_assignment_or_404(learner, assignment_id)
    if not assignment:
        return jsonify({"success": False, "error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    try:
        progress = db.save_progress_code(assignment_id, learner["id"], code)
        db.touch_learner_active(learner["id"])
        db.log_event(learner["id"], learner["cohort_id"], "assignment_autosave", {"assignment_id": assignment_id})
        return jsonify({"success": True, "status": progress["status"]})
    except Exception:
        # Never let a persistence hiccup surface as a broken editor - the
        # client keeps its own localStorage draft regardless.
        return jsonify({"success": False, "error": "save_failed"}), 200


@classroom_bp.route("/assignments/<int:assignment_id>/run-result", methods=["POST"])
@require_learner
def record_assignment_run(learner, assignment_id):
    assignment = _learner_assignment_or_404(learner, assignment_id)
    if not assignment:
        return jsonify({"success": False, "error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    ran_ok = bool(body.get("ran_ok"))
    error = body.get("error")
    try:
        db.save_progress_code(assignment_id, learner["id"], code, ran=True, run_ok=ran_ok, error=error)
        concepts_mod.record_run(learner["id"], learner["cohort_id"], code, ran_ok)
        db.log_event(
            learner["id"], learner["cohort_id"], "run_success" if ran_ok else "run_failure",
            {"assignment_id": assignment_id},
        )
        db.touch_learner_active(learner["id"])
        return jsonify({"success": True})
    except Exception:
        return jsonify({"success": False, "error": "record_failed"}), 200


@classroom_bp.route("/assignments/<int:assignment_id>/submit", methods=["POST"])
@require_learner
def submit_assignment(learner, assignment_id):
    assignment = _learner_assignment_or_404(learner, assignment_id)
    if not assignment:
        return jsonify({"success": False, "error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    try:
        progress = learner_actions.submit_current_assignment(learner, assignment, code)
    except Exception:
        return jsonify({"success": False, "error": "submit_failed"}), 200
    return jsonify({"success": True, "status": progress["status"], "submitted_at": progress["submitted_at"]})


# ---- learner: guided projects (opened inside the IDE) ---------------------------------

@classroom_bp.route("/projects/<project_id>/open", methods=["GET"])
@require_learner
def open_project(learner, project_id):
    if not guided_projects.get_project(project_id):
        return redirect(url_for("classroom.learner_home"))
    db.get_or_create_project_progress(learner["id"], project_id)
    db.touch_learner_active(learner["id"])
    resp = redirect(url_for("ide") + f"?project={project_id}")
    resp.set_cookie(PROJECT_COOKIE, project_id, httponly=False, samesite="Lax")
    resp.delete_cookie(ASSIGNMENT_COOKIE)
    resp.delete_cookie(MODULE_COOKIE)
    return resp


def _project_public(project_id: str) -> Optional[Dict[str, Any]]:
    if project_id.startswith("custom:"):
        try:
            custom_id = int(project_id.split(":", 1)[1])
        except ValueError:
            return None
        project = db.get_custom_project(custom_id)
        if not project:
            return None
        return {
            "id": project_id, "title": project["title"], "description": project["instructions"],
            "starter_code": project["starter_code"], "expected_concepts": project["expected_concepts"],
            "checkpoints": [{"id": str(c["id"]), "label": c["label"]} for c in project["checkpoints"]],
        }
    return guided_projects.get_project(project_id)


def _project_newly_completed(project_id: str, code: str, previously_completed: list) -> list:
    if project_id.startswith("custom:"):
        custom_id = int(project_id.split(":", 1)[1])
        project = db.get_custom_project(custom_id)
        if not project:
            return []
        newly = guided_projects.newly_completed_custom(
            project["checkpoints"], code, [int(c) for c in previously_completed if str(c).isdigit()]
        )
        return [str(cid) for cid in newly]
    return guided_projects.newly_completed(project_id, code, previously_completed)


@classroom_bp.route("/projects/<path:project_id>/context", methods=["GET"])
@require_learner
def project_context(learner, project_id):
    project = _project_public(project_id)
    if not project:
        return jsonify({"success": False, "error": "not_found"}), 404
    progress = db.get_or_create_project_progress(learner["id"], project_id)
    completed = progress.get("checkpoints_completed") or []
    intro = (
        learner_context.project_returning_intro(project, completed) if completed
        else learner_context.project_intro(project)
    )
    return jsonify({"success": True, "project": project, "progress": progress, "intro": intro})


@classroom_bp.route("/projects/<path:project_id>/save", methods=["POST"])
@require_learner
def save_project(learner, project_id):
    project = _project_public(project_id)
    if not project:
        return jsonify({"success": False, "error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    try:
        prior = db.get_or_create_project_progress(learner["id"], project_id)
        newly = _project_newly_completed(project_id, code, prior.get("checkpoints_completed") or [])
        completed = list(dict.fromkeys((prior.get("checkpoints_completed") or []) + newly))
        progress = db.save_project_progress(learner["id"], project_id, code=code, checkpoints_completed=completed)
        if newly:
            checkpoint_concepts = project.get("expected_concepts") or []
            concepts_mod.record_checkpoint(learner["id"], learner["cohort_id"], checkpoint_concepts)
            db.log_event(
                learner["id"], learner["cohort_id"], "checkpoint_completed",
                {"project_id": project_id, "checkpoints": newly},
            )
        db.touch_learner_active(learner["id"])
        # Humane feedback (see learner_context.py) is always computed here so
        # it stays available to the caller, but the IDE only speaks it after
        # a Run - see static/classroom.js onRunResult vs. plain onAutosave -
        # so ordinary autosaves-while-typing stay silent per the spec.
        feedback = (
            learner_context.checkpoint_completion_feedback(project, newly) if newly
            else learner_context.checkpoint_incomplete_feedback(project, completed)
        )
        return jsonify({
            "success": True, "newly_completed": newly,
            "checkpoints_completed": progress["checkpoints_completed"],
            "feedback": feedback,
        })
    except Exception:
        return jsonify({"success": False, "error": "save_failed"}), 200


# ---- learner: help requests ---------------------------------------------------------

@classroom_bp.route("/help-requests", methods=["POST"])
@require_learner
def create_help_request(learner):
    body = request.get_json(silent=True)
    is_json = isinstance(body, dict)
    source = body if is_json else request.form
    message = str(source.get("message") or "")
    assignment_id = source.get("assignment_id")
    try:
        assignment_id = int(assignment_id) if assignment_id else None
    except (TypeError, ValueError):
        assignment_id = None
    hr = learner_actions.send_help_request(learner, message, assignment_id)
    if is_json:
        return jsonify({"success": True, "help_request": hr})
    return redirect(url_for("classroom.learner_home"))


@classroom_bp.route("/help-requests/<int:help_request_id>/cancel", methods=["POST"])
@require_learner
def cancel_help_request(learner, help_request_id):
    hr = learner_actions.cancel_help_request_for_learner(learner, help_request_id)
    if request.get_json(silent=True) is not None:
        return jsonify({"success": bool(hr)})
    return redirect(url_for("classroom.learner_home"))


# ============================================================================
# CURRICULUM (built-in course + instructor-authored lessons, one interface)
# ============================================================================

def _lesson_context(module_id: str) -> Optional[Dict[str, Any]]:
    """Normalizes a built-in module id OR a 'custom:<lesson_id>' instructor
    lesson to the same shape, so the learner always sees one lesson
    interface regardless of source."""
    if module_id.startswith("custom:"):
        try:
            lesson_id = int(module_id.split(":", 1)[1])
        except ValueError:
            return None
        lesson = db.get_custom_lesson(lesson_id)
        if not lesson:
            return None
        return {
            "id": module_id, "title": lesson["title"], "concept": lesson["explanation"] or lesson["objective"],
            "example_code": lesson["starter_code"], "instructions": lesson["instructions"],
            "hints": [], "challenge": lesson["challenge"],
            "quiz_question": lesson.get("quiz_question"), "quiz_choices": lesson.get("quiz_choices") or [],
            "is_custom": True, "expected_concepts": lesson["expected_concepts"],
            "quiz_answer_index": lesson.get("quiz_answer_index"),
        }
    module = curriculum.public_module(module_id)
    if not module:
        return None
    module = dict(module)
    module["is_custom"] = False
    module["expected_concepts"] = []
    return module


def _check_lesson_stage(module_id: str, code: str, stage: str) -> Dict[str, Any]:
    if module_id.startswith("custom:"):
        lesson = _lesson_context(module_id)
        if not lesson:
            return {"passed": False, "feedback": "This lesson could not be found."}
        expected = set(lesson.get("expected_concepts") or [])
        present = set(concepts_mod.detect_concepts(code))
        if expected:
            passed = bool(code.strip()) and expected.issubset(present)
        else:
            passed = bool(code.strip()) and "print output" in present
        return {
            "passed": passed,
            "feedback": "Nicely done." if passed else "Not quite there yet - " + (lesson.get("instructions") or ""),
        }
    if stage == "challenge":
        return curriculum.check_challenge(module_id, code)
    return curriculum.check_attempt(module_id, code)


@classroom_bp.route("/curriculum", methods=["GET"])
@require_learner
def curriculum_home(learner):
    db.touch_learner_active(learner["id"])
    state = db.get_curriculum_state(learner["id"])
    module_progress = {row["module_id"]: row for row in db.list_module_progress(learner["id"])}
    custom_lessons = db.list_custom_lessons(learner["cohort_id"])

    modules = []
    for mid in curriculum.MODULE_ORDER:
        m = curriculum.public_module(mid)
        prog = module_progress.get(mid)
        m["status"] = (prog or {}).get("status", "not_started")
        modules.append(m)
    for lesson in custom_lessons:
        cid = f"custom:{lesson['id']}"
        prog = module_progress.get(cid)
        modules.append({
            "id": cid, "title": lesson["title"], "status": (prog or {}).get("status", "not_started"),
            "is_custom": True,
        })

    continue_module = None
    if state and state.get("current_module_id"):
        continue_module = _lesson_context(state["current_module_id"])
        if continue_module:
            done = sum(1 for m in modules if m.get("status") == "completed")
            continue_module = {
                **continue_module,
                "module_id": state["current_module_id"],
                "completed_count": done,
                "total_count": len(modules),
                "last_activity_at": state.get("last_activity_at"),
            }

    onboarding_done = db.onboarding_completed(learner["id"])
    return render_template(
        "classroom/curriculum_home.html",
        learner=learner, modules=modules, continue_module=continue_module,
        onboarding_completed=onboarding_done,
    )


@classroom_bp.route("/curriculum/<path:module_id>/open", methods=["GET"])
@require_learner
def open_module(learner, module_id):
    lesson = _lesson_context(module_id)
    if not lesson:
        return redirect(url_for("classroom.curriculum_home"))
    db.get_or_create_module_progress_row(learner["id"], module_id)
    db.set_curriculum_position(learner["id"], module_id, "concept")
    db.touch_learner_active(learner["id"])
    resp = redirect(url_for("ide") + f"?module={module_id}")
    resp.set_cookie(MODULE_COOKIE, module_id, httponly=False, samesite="Lax")
    resp.delete_cookie(ASSIGNMENT_COOKIE)
    resp.delete_cookie(PROJECT_COOKIE)
    return resp


@classroom_bp.route("/curriculum/<path:module_id>/context", methods=["GET"])
@require_learner
def module_context(learner, module_id):
    lesson = _lesson_context(module_id)
    if not lesson:
        return jsonify({"success": False, "error": "not_found"}), 404
    progress = db.get_or_create_module_progress_row(learner["id"], module_id)
    next_id = None if lesson.get("is_custom") else curriculum.next_module_id(module_id)
    return jsonify({"success": True, "lesson": lesson, "progress": progress, "next_module_id": next_id})


@classroom_bp.route("/curriculum/<path:module_id>/attempt", methods=["POST"])
@require_learner
def submit_module_attempt(learner, module_id):
    lesson = _lesson_context(module_id)
    if not lesson:
        return jsonify({"success": False, "error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    result = _check_lesson_stage(module_id, code, "attempt")
    try:
        db.upsert_module_stage(learner["id"], module_id, "attempt", status="completed" if result["passed"] else None)
        db.set_curriculum_position(learner["id"], module_id, "attempt")
        db.touch_learner_active(learner["id"])
        if result["passed"]:
            concepts_to_credit = lesson.get("expected_concepts") or concepts_mod.detect_concepts(code)
            for concept in concepts_to_credit:
                concepts_mod.record_lesson_passed(learner["id"], learner["cohort_id"], concept)
            db.log_event(learner["id"], learner["cohort_id"], "lesson_progress", {"module": module_id, "passed": True})
    except Exception:
        pass
    return jsonify({"success": True, "passed": result["passed"], "feedback": result["feedback"]})


@classroom_bp.route("/curriculum/<path:module_id>/challenge", methods=["POST"])
@require_learner
def submit_module_challenge(learner, module_id):
    lesson = _lesson_context(module_id)
    if not lesson:
        return jsonify({"success": False, "error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    result = _check_lesson_stage(module_id, code, "challenge")
    try:
        if result["passed"]:
            db.upsert_module_stage(learner["id"], module_id, "challenge")
            db.set_curriculum_position(learner["id"], module_id, "challenge")
            db.touch_learner_active(learner["id"])
            db.log_event(learner["id"], learner["cohort_id"], "challenge_completed", {"module": module_id})
    except Exception:
        pass
    return jsonify({"success": True, "passed": result["passed"], "feedback": result["feedback"]})


@classroom_bp.route("/curriculum/<path:module_id>/quiz", methods=["GET"])
@require_learner
def module_quiz(learner, module_id):
    lesson = _lesson_context(module_id)
    if not lesson or not lesson.get("quiz_question"):
        return redirect(url_for("classroom.curriculum_home"))
    return render_template("classroom/quiz.html", learner=learner, module_id=module_id, lesson=lesson, result=None)


@classroom_bp.route("/curriculum/<path:module_id>/quiz", methods=["POST"])
@require_learner
def module_quiz_submit(learner, module_id):
    lesson = _lesson_context(module_id)
    if not lesson or not lesson.get("quiz_question"):
        return redirect(url_for("classroom.curriculum_home"))
    try:
        choice_index = int(request.form.get("choice"))
    except (TypeError, ValueError):
        choice_index = -1
    if module_id.startswith("custom:"):
        correct = choice_index == lesson.get("quiz_answer_index")
        feedback = "Correct." if correct else "Not quite - review the lesson and try again."
    else:
        correct, feedback = curriculum.check_quiz(module_id, choice_index)
    db.record_quiz_result(learner["id"], module_id, 1 if correct else 0, 1)
    db.touch_learner_active(learner["id"])
    db.log_event(learner["id"], learner["cohort_id"], "quiz_submitted", {"module": module_id, "correct": correct})
    return render_template(
        "classroom/quiz.html", learner=learner, module_id=module_id, lesson=lesson,
        result={"correct": correct, "feedback": feedback},
    )


@classroom_bp.route("/curriculum/restart-module/<path:module_id>/confirm", methods=["GET"])
@require_learner
def restart_module_confirm(learner, module_id):
    lesson = _lesson_context(module_id)
    if not lesson:
        return redirect(url_for("classroom.curriculum_home"))
    return render_template(
        "classroom/confirm.html",
        heading=f"Restart {lesson['title']}?",
        body="This clears your progress on this module only - your assignments and projects are not affected.",
        confirm_action=url_for("classroom.restart_module", module_id=module_id),
        cancel_url=url_for("classroom.curriculum_home"),
        confirm_label="Yes, restart this module",
    )


@classroom_bp.route("/curriculum/restart-module/<path:module_id>", methods=["POST"])
@require_learner
def restart_module(learner, module_id):
    db.reset_module_progress(learner["id"], module_id)
    db.log_event(learner["id"], learner["cohort_id"], "module_restarted", {"module": module_id})
    return redirect(url_for("classroom.curriculum_home"))


@classroom_bp.route("/curriculum/restart-course/confirm", methods=["GET"])
@require_learner
def restart_course_confirm(learner):
    return render_template(
        "classroom/confirm.html",
        heading="Restart the entire course?",
        body="This clears progress on every lesson module and takes you back to the first module. "
             "Your assignments and guided projects are not affected.",
        confirm_action=url_for("classroom.restart_course"),
        cancel_url=url_for("classroom.curriculum_home"),
        confirm_label="Yes, restart the entire course",
    )


@classroom_bp.route("/curriculum/restart-course", methods=["POST"])
@require_learner
def restart_course(learner):
    db.reset_all_module_progress(learner["id"])
    db.clear_curriculum_state(learner["id"])
    db.log_event(learner["id"], learner["cohort_id"], "course_restarted", {})
    return redirect(url_for("classroom.curriculum_home"))


@classroom_bp.route("/onboarding/complete", methods=["POST"])
@require_learner
def complete_onboarding(learner):
    db.mark_onboarding_completed(learner["id"])
    if request.get_json(silent=True) is not None:
        return jsonify({"success": True})
    return redirect(url_for("classroom.learner_home"))


# ============================================================================
# INSTRUCTOR-AUTHORED LESSONS
# ============================================================================

@classroom_bp.route("/cohorts/<int:cohort_id>/lessons", methods=["GET"])
@require_instructor
def custom_lessons_list(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    lessons = db.list_custom_lessons(cohort_id)
    return render_template("classroom/custom_lessons.html", instructor=instructor, cohort=cohort, lessons=lessons)


@classroom_bp.route("/cohorts/<int:cohort_id>/lessons", methods=["POST"])
@require_instructor
def create_custom_lesson(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("classroom.custom_lessons_list", cohort_id=cohort_id))
    choices = [c.strip() for c in (request.form.get("quiz_choices") or "").split("\n") if c.strip()]
    answer_index = None
    try:
        raw_answer = request.form.get("quiz_answer_index")
        if raw_answer not in (None, ""):
            answer_index = int(raw_answer)
    except ValueError:
        answer_index = None
    db.create_custom_lesson(
        cohort_id, instructor["id"],
        title=title,
        objective=request.form.get("objective") or "",
        explanation=request.form.get("explanation") or "",
        starter_code=request.form.get("starter_code") or "",
        instructions=request.form.get("instructions") or "",
        expected_concepts=[c.strip() for c in (request.form.get("expected_concepts") or "").split(",") if c.strip()],
        challenge=request.form.get("challenge") or "",
        expected_output=(request.form.get("expected_output") or "").strip() or None,
        quiz_question=(request.form.get("quiz_question") or "").strip() or None,
        quiz_choices=choices,
        quiz_answer_index=answer_index,
    )
    return redirect(url_for("classroom.custom_lessons_list", cohort_id=cohort_id))


# ============================================================================
# INSTRUCTOR-AUTHORED GUIDED PROJECTS
# ============================================================================

@classroom_bp.route("/cohorts/<int:cohort_id>/projects", methods=["GET"])
@require_instructor
def custom_projects_list(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    projects = db.list_custom_projects(cohort_id)
    return render_template(
        "classroom/custom_projects.html", instructor=instructor, cohort=cohort, projects=projects,
        check_types=guided_projects.CUSTOM_CHECK_TYPES,
    )


@classroom_bp.route("/cohorts/<int:cohort_id>/projects", methods=["POST"])
@require_instructor
def create_custom_project(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("classroom.custom_projects_list", cohort_id=cohort_id))

    checkpoints = []
    labels = request.form.getlist("checkpoint_label")
    types = request.form.getlist("checkpoint_type")
    configs = request.form.getlist("checkpoint_config")
    for label, check_type, config_raw in zip(labels, types, configs):
        if not label.strip():
            continue
        config = {}
        config_raw = (config_raw or "").strip()
        if config_raw:
            if check_type == "contains_assignment_named":
                config = {"names": [n.strip() for n in config_raw.split(",") if n.strip()]}
            elif check_type == "contains_call":
                config = {"name": config_raw}
            elif check_type == "contains_keyword":
                config = {"keyword": config_raw}
            elif check_type == "contains_operator":
                config = {"op": config_raw}
        checkpoints.append({"label": label.strip(), "check_type": check_type, "check_config": config})

    db.create_custom_project(
        cohort_id, instructor["id"], title=title,
        instructions=request.form.get("instructions") or "",
        starter_code=request.form.get("starter_code") or "",
        expected_concepts=[c.strip() for c in (request.form.get("expected_concepts") or "").split(",") if c.strip()],
        checkpoints=checkpoints,
    )
    return redirect(url_for("classroom.custom_projects_list", cohort_id=cohort_id))


@classroom_bp.route("/custom-projects/<int:project_id>/open", methods=["GET"])
@require_learner
def open_custom_project(learner, project_id):
    project = db.get_custom_project(project_id)
    if not project:
        return redirect(url_for("classroom.learner_home"))
    pid = f"custom:{project_id}"
    db.get_or_create_project_progress(learner["id"], pid)
    db.touch_learner_active(learner["id"])
    resp = redirect(url_for("ide") + f"?project={pid}")
    resp.set_cookie(PROJECT_COOKIE, pid, httponly=False, samesite="Lax")
    resp.delete_cookie(ASSIGNMENT_COOKIE)
    resp.delete_cookie(MODULE_COOKIE)
    return resp

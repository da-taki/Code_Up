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
from typing import Any, Dict, Optional

from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from codeup.classroom import ai_policy, concepts as concepts_mod, db, guided_projects, reports

classroom_bp = Blueprint("classroom", __name__, url_prefix="/classroom")

INSTRUCTOR_SESSION_KEY = "classroom_instructor_id"
LEARNER_COOKIE = "cu_learner_token"
ASSIGNMENT_COOKIE = ai_policy.ASSIGNMENT_COOKIE
PROJECT_COOKIE = "cu_project_id"
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
    return render_template("classroom/instructor_dashboard.html", instructor=instructor, cohorts=cohorts)


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


@classroom_bp.route("/cohorts/<int:cohort_id>", methods=["GET"])
@require_instructor
def cohort_dashboard(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))

    learners = db.list_learners_for_cohort(cohort_id)
    assignments = db.list_assignments_for_cohort(cohort_id)
    open_help = db.list_help_requests(cohort_id, status="open")

    learner_rows = []
    for learner in learners:
        progress_rows = db.list_progress_for_learner(learner["id"])
        submitted = sum(1 for r in progress_rows if r["status"] == "submitted")
        concept_states = concepts_mod.summary_for_learner(learner["id"])
        demonstrated = sum(1 for s in concept_states.values() if s == "demonstrated")
        events = db.list_events_for_learner(learner["id"], limit=5)
        learner_rows.append({
            "learner": learner,
            "assignments_submitted": submitted,
            "assignments_total": len(assignments),
            "concepts_demonstrated": demonstrated,
            "concepts_total": len(concepts_mod.CURRICULUM_CONCEPTS),
            "recent_activity": events,
        })

    return render_template(
        "classroom/cohort_dashboard.html",
        instructor=instructor, cohort=cohort, learner_rows=learner_rows,
        assignments=assignments, open_help_count=len(open_help),
        ai_policies=ai_policy.POLICIES,
    )


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

    return render_template(
        "classroom/learner_detail.html",
        instructor=instructor, cohort=cohort, learner=learner, report=report,
        progress_rows=progress_rows, concept_states=concept_states, events=events,
    )


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
    expected_concepts = [c.strip() for c in (request.form.get("expected_concepts") or "").split(",") if c.strip()]
    policy = ai_policy.normalize_policy(request.form.get("ai_policy"))

    assignment = db.create_assignment(
        cohort_id, title, instructions, starter_code, due_date, expected_concepts, policy,
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

    return render_template(
        "classroom/assignment_detail.html",
        instructor=instructor, cohort=cohort, assignment=assignment,
        progress_rows=progress_rows, ai_policies=ai_policy.POLICIES,
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
    assignment, cohort = _assignment_cohort_or_404(instructor, assignment_id)
    if assignment:
        db.update_assignment_policy(assignment_id, ai_policy.normalize_policy(request.form.get("ai_policy")))
    return redirect(url_for("classroom.assignment_detail", assignment_id=assignment_id))


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

@classroom_bp.route("/cohorts/<int:cohort_id>/help-requests", methods=["GET"])
@require_instructor
def help_queue(instructor, cohort_id):
    cohort = _own_cohort_or_404(instructor, cohort_id)
    if not cohort:
        return redirect(url_for("classroom.instructor_dashboard"))
    open_requests = db.list_help_requests(cohort_id, status="open")
    resolved_requests = db.list_help_requests(cohort_id, status="resolved")[:20]
    assignments = {a["id"]: a for a in db.list_assignments_for_cohort(cohort_id)}
    for hr in open_requests + resolved_requests:
        hr["assignment"] = assignments.get(hr.get("assignment_id"))
    return render_template(
        "classroom/help_queue.html",
        instructor=instructor, cohort=cohort, open_requests=open_requests, resolved_requests=resolved_requests,
    )


@classroom_bp.route("/help-requests/<int:help_request_id>/resolve", methods=["POST"])
@require_instructor
def resolve_help_request(instructor, help_request_id):
    hr = db.get_help_request(help_request_id)
    if hr:
        cohort = _own_cohort_or_404(instructor, hr["cohort_id"])
        if cohort:
            db.resolve_help_request(help_request_id)
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
    if not code or not display_name:
        return redirect(url_for("classroom.join_page", error="Enter a join code and your name."))
    cohort = db.get_cohort_by_join_code(code)
    if not cohort:
        return redirect(url_for("classroom.join_page", error="That join code was not found."))
    learner = db.join_cohort(cohort["id"], display_name)
    resp = redirect(url_for("classroom.learner_home"))
    resp.set_cookie(
        LEARNER_COOKIE, learner["token"], max_age=LEARNER_COOKIE_MAX_AGE,
        httponly=True, samesite="Lax",
    )
    return resp


@classroom_bp.route("/leave", methods=["POST"])
def leave_cohort():
    resp = redirect(url_for("classroom.join_page"))
    resp.delete_cookie(LEARNER_COOKIE)
    resp.delete_cookie(ASSIGNMENT_COOKIE)
    resp.delete_cookie(PROJECT_COOKIE)
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
    open_help = [
        hr for hr in db.list_help_requests(learner["cohort_id"], status="open")
        if hr["learner_id"] == learner["id"]
    ]
    return render_template(
        "classroom/learner_home.html",
        learner=learner, cohort=cohort, assignments=assignments, projects=projects,
        open_help=open_help,
    )


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
    return resp


@classroom_bp.route("/assignments/<int:assignment_id>/context", methods=["GET"])
@require_learner
def assignment_context(learner, assignment_id):
    assignment = _learner_assignment_or_404(learner, assignment_id)
    if not assignment:
        return jsonify({"success": False, "error": "not_found"}), 404
    progress = db.get_or_create_progress(assignment_id, learner["id"])
    return jsonify({
        "success": True,
        "assignment": {
            "id": assignment["id"], "title": assignment["title"],
            "instructions": assignment["instructions"], "due_date": assignment["due_date"],
            "ai_policy": assignment["ai_policy"], "expected_concepts": assignment["expected_concepts"],
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
        progress = db.submit_assignment(assignment_id, learner["id"], code)
    except Exception:
        return jsonify({"success": False, "error": "submit_failed"}), 200
    try:
        concepts_mod.record_assignment_submitted(
            learner["id"], learner["cohort_id"], code, assignment.get("expected_concepts") or [],
        )
        db.log_event(learner["id"], learner["cohort_id"], "assignment_submitted", {"assignment_id": assignment_id})
        db.touch_learner_active(learner["id"])
    except Exception:
        pass  # the submission itself already succeeded; progress tracking is best-effort
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
    return resp


@classroom_bp.route("/projects/<project_id>/context", methods=["GET"])
@require_learner
def project_context(learner, project_id):
    project = guided_projects.get_project(project_id)
    if not project:
        return jsonify({"success": False, "error": "not_found"}), 404
    progress = db.get_or_create_project_progress(learner["id"], project_id)
    return jsonify({"success": True, "project": project, "progress": progress})


@classroom_bp.route("/projects/<project_id>/save", methods=["POST"])
@require_learner
def save_project(learner, project_id):
    project = guided_projects.get_project(project_id)
    if not project:
        return jsonify({"success": False, "error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    try:
        prior = db.get_or_create_project_progress(learner["id"], project_id)
        newly = guided_projects.newly_completed(project_id, code, prior.get("checkpoints_completed") or [])
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
        return jsonify({
            "success": True, "newly_completed": newly,
            "checkpoints_completed": progress["checkpoints_completed"],
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
    hr = db.create_help_request(learner["cohort_id"], learner["id"], assignment_id, message)
    db.log_event(learner["id"], learner["cohort_id"], "help_requested", {"help_request_id": hr["id"]})
    if is_json:
        return jsonify({"success": True, "help_request": hr})
    return redirect(url_for("classroom.learner_home"))


@classroom_bp.route("/help-requests/<int:help_request_id>/cancel", methods=["POST"])
@require_learner
def cancel_help_request(learner, help_request_id):
    hr = db.cancel_help_request(help_request_id, learner["id"])
    if request.get_json(silent=True) is not None:
        return jsonify({"success": bool(hr)})
    return redirect(url_for("classroom.learner_home"))

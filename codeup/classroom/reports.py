"""Instructor-facing reports for a cohort or a single learner.

Extends the existing single-session Teacher Report pattern
(``codeup.reports.teacher_report``) to the classroom's durable, multi-learner
data instead of one in-memory session dict. Everything here is a plain
aggregation over real stored rows - no AI, nothing invented, missing data is
described as missing rather than guessed.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List

from codeup.classroom import curriculum, db, concepts as concepts_mod, guided_projects

_CONCEPT_STATE_LABEL = {
    "not_started": "Not started",
    "introduced": "Introduced",
    "practised": "Practised",
    "demonstrated": "Demonstrated",
    "needs_practice": "Needs practice",
}


def _assignment_progress_summary(learner_id: int) -> Dict[str, int]:
    rows = db.list_progress_for_learner(learner_id)
    counts = {"not_started": 0, "in_progress": 0, "submitted": 0}
    for row in rows:
        counts[row.get("status", "not_started")] = counts.get(row.get("status", "not_started"), 0) + 1
    return counts


def build_learner_report(learner_id: int) -> Dict[str, Any]:
    learner = db.get_learner(learner_id)
    if not learner:
        return {"report_md": "Learner not found.", "speech": "Learner not found.", "sections": {}}

    concept_states = concepts_mod.summary_for_learner(learner_id)
    lesson_rows = db.list_lesson_progress(learner_id)
    module_rows = db.list_module_progress(learner_id)
    project_rows = db.list_project_progress(learner_id)
    assignment_counts = _assignment_progress_summary(learner_id)
    events = db.list_events_for_learner(learner_id, limit=15)

    lines = [f"# Learner Report - {learner['display_name']}", ""]

    lines.append("## Assignments")
    lines.append(
        f"Submitted: {assignment_counts['submitted']} | "
        f"In progress: {assignment_counts['in_progress']} | "
        f"Not started: {assignment_counts['not_started']}"
    )
    lines.append("")

    completed_modules = sum(1 for m in module_rows if m["status"] == "completed")
    lines.append("## Course modules (Python Foundations)")
    lines.append(f"Completed: {completed_modules} / {len(curriculum.MODULE_ORDER)}")
    if module_rows:
        for row in module_rows:
            quiz = f", quiz {row['quiz_score']}/{row['quiz_total']}" if row.get("quiz_total") else ""
            lines.append(f"- {row['module_id']}: {row['status']} ({row['attempts']} attempt(s)){quiz}")
    else:
        lines.append("No course modules started yet.")
    lines.append("")

    if lesson_rows:
        lines.append("## Onboarding tutorial")
        for row in lesson_rows:
            lines.append(f"- {row['lesson_id']}: {row['status']} ({row['attempts']} attempt(s))")
        lines.append("")

    lines.append("## Guided projects")
    if project_rows:
        for row in project_rows:
            done = len(row.get("checkpoints_completed") or [])
            lines.append(f"- {row['project_id']}: {done} checkpoint(s) completed")
    else:
        lines.append("No guided projects started yet.")
    lines.append("")

    lines.append("## Concepts practised")
    for concept in concepts_mod.CURRICULUM_CONCEPTS:
        lines.append(f"- {concept}: {_CONCEPT_STATE_LABEL.get(concept_states.get(concept, 'not_started'))}")
    lines.append("")

    lines.append("## Recent activity")
    if events:
        for event in events[:10]:
            lines.append(f"- {event['created_at']}: {event['kind']}")
    else:
        lines.append("No recorded activity yet.")

    report_md = "\n".join(lines).strip()
    speech = (
        f"Report for {learner['display_name']}. "
        f"{assignment_counts['submitted']} assignment(s) submitted, "
        f"{assignment_counts['in_progress']} in progress. "
        "See the full report for concept progress and recent activity."
    )
    return {"report_md": report_md, "speech": speech, "sections": {"assignments": assignment_counts}}


def build_cohort_report(cohort_id: int) -> Dict[str, Any]:
    cohort = db.get_cohort(cohort_id)
    if not cohort:
        return {"report_md": "Cohort not found.", "speech": "Cohort not found.", "rows": []}

    learners = db.list_learners_for_cohort(cohort_id)
    assignments = db.list_assignments_for_cohort(cohort_id, published_only=True)
    open_help = db.list_help_requests(cohort_id, status="open")

    concept_needs_practice_counts: Dict[str, int] = {c: 0 for c in concepts_mod.CURRICULUM_CONCEPTS}
    concept_demonstrated_counts: Dict[str, int] = {c: 0 for c in concepts_mod.CURRICULUM_CONCEPTS}

    rows: List[Dict[str, Any]] = []
    for learner in learners:
        counts = _assignment_progress_summary(learner["id"])
        concept_states = concepts_mod.summary_for_learner(learner["id"])
        for concept, state in concept_states.items():
            if state == "needs_practice":
                concept_needs_practice_counts[concept] += 1
            elif state == "demonstrated":
                concept_demonstrated_counts[concept] += 1
        rows.append(
            {
                "learner_id": learner["id"],
                "display_name": learner["display_name"],
                "last_active_at": learner.get("last_active_at"),
                "assignments_submitted": counts["submitted"],
                "assignments_total": len(assignments),
                "concepts_demonstrated": sum(1 for s in concept_states.values() if s == "demonstrated"),
                "concepts_needs_practice": sum(1 for s in concept_states.values() if s == "needs_practice"),
            }
        )

    lines = [f"# Cohort Report - {cohort['name']}", ""]
    lines.append(f"Learners: {len(learners)} | Published assignments: {len(assignments)} | Open help requests: {len(open_help)}")
    lines.append("")
    lines.append("## Learners")
    if rows:
        for row in rows:
            lines.append(
                f"- {row['display_name']}: {row['assignments_submitted']}/{row['assignments_total']} "
                f"assignments submitted, {row['concepts_demonstrated']} concept(s) demonstrated, "
                f"{row['concepts_needs_practice']} needing practice, last active {row['last_active_at'] or 'never'}"
            )
    else:
        lines.append("No learners have joined yet.")
    lines.append("")

    lines.append("## Common error patterns (concepts most learners are struggling with)")
    struggling = sorted(
        ((c, n) for c, n in concept_needs_practice_counts.items() if n > 0),
        key=lambda kv: -kv[1],
    )
    if struggling:
        for concept, n in struggling:
            lines.append(f"- {concept}: {n} learner(s) currently flagged as needing practice")
    else:
        lines.append("No concept-level error patterns detected yet.")

    report_md = "\n".join(lines).strip()
    speech = (
        f"Report for {cohort['name']}. {len(learners)} learners, "
        f"{len(assignments)} published assignments, {len(open_help)} open help requests."
    )
    return {"report_md": report_md, "speech": speech, "rows": rows}


def cohort_report_csv(cohort_id: int) -> str:
    report = build_cohort_report(cohort_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "learner", "last_active", "assignments_submitted", "assignments_total",
        "concepts_demonstrated", "concepts_needs_practice",
    ])
    for row in report.get("rows", []):
        writer.writerow([
            row["display_name"],
            row.get("last_active_at") or "",
            row["assignments_submitted"],
            row["assignments_total"],
            row["concepts_demonstrated"],
            row["concepts_needs_practice"],
        ])
    return buf.getvalue()


_ACTIVE_WINDOW_DAYS = 7


def _is_recently_active(last_active_at: Any) -> bool:
    if not last_active_at:
        return False
    try:
        last = datetime.fromisoformat(last_active_at)
    except (TypeError, ValueError):
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).days < _ACTIVE_WINDOW_DAYS


def _project_checkpoint_total(project_id: str) -> int:
    if project_id.startswith("custom:"):
        try:
            project = db.get_custom_project(int(project_id.split(":", 1)[1]))
        except ValueError:
            return 0
        return len(project.get("checkpoints") or []) if project else 0
    return len(guided_projects.all_checkpoint_ids(project_id))


def build_cohort_impact_summary(cohort_id: int) -> Dict[str, Any]:
    """Real, stored-data-only headline numbers for a cohort - no charts, no
    invented analytics. Every field here is a plain count over rows that
    already exist for other reasons (progress, events, submissions)."""
    learners = db.list_learners_for_cohort(cohort_id)
    active_learners = sum(1 for row in learners if _is_recently_active(row.get("last_active_at")))

    lessons_completed = 0
    course_completions = 0
    projects_completed = 0
    programs_run = 0
    successful_runs = 0
    total_builtin_modules = len(curriculum.MODULE_ORDER)

    for learner in learners:
        module_rows = db.list_module_progress(learner["id"])
        completed_here = sum(1 for m in module_rows if m["status"] == "completed")
        lessons_completed += completed_here
        builtin_completed = sum(
            1 for m in module_rows if m["status"] == "completed" and not m["module_id"].startswith("custom:")
        )
        if builtin_completed >= total_builtin_modules:
            course_completions += 1

        for project_row in db.list_project_progress(learner["id"]):
            total_cp = _project_checkpoint_total(project_row["project_id"])
            if total_cp and len(project_row.get("checkpoints_completed") or []) >= total_cp:
                projects_completed += 1

        events = db.list_events_for_learner(learner["id"], limit=1000)
        programs_run += sum(1 for e in events if e["kind"] in ("run_success", "run_failure"))
        successful_runs += sum(1 for e in events if e["kind"] == "run_success")

    assignments = db.list_assignments_for_cohort(cohort_id)
    assignments_submitted = 0
    for assignment in assignments:
        for row in db.list_progress_for_assignment(assignment["id"]):
            if row["status"] == "submitted":
                assignments_submitted += 1

    help_requests = db.list_help_requests(cohort_id)

    return {
        "learners_enrolled": len(learners),
        "active_learners": active_learners,
        "lessons_completed": lessons_completed,
        "assignments_submitted": assignments_submitted,
        "projects_completed": projects_completed,
        "programs_run": programs_run,
        "successful_runs": successful_runs,
        "help_requests": len(help_requests),
        "course_completions": course_completions,
    }

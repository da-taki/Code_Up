"""Learner-facing classroom context: human-friendly formatting, assignment
new/pending/overdue classification, guided-project feedback copy, and the
deterministic "what should I do" resolver.

Deliberately plain, framework-agnostic logic (no Flask, no direct db calls)
so it is unit-testable in isolation and so the IDE panel's visual text and
CodeUp's spoken announcements share one semantic source, per the spec's
"avoid raw database language" requirement. Nothing here calls an LLM - every
function is a pure, deterministic transform over already-fetched data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---- time helpers ------------------------------------------------------------

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---- assignment classification (NEW / PENDING / IN_PROGRESS / SUBMITTED / OVERDUE) ----

def classify_assignment(
    assignment: Dict[str, Any],
    progress_status: str,
    seen_at: Optional[str],
    now: Optional[datetime] = None,
) -> str:
    """One learner-facing state for an assignment. Submitted always wins
    (a learner who submitted overdue work isn't nagged about it); otherwise
    overdue beats "new" beats in-progress/pending."""
    now = now or datetime.now(timezone.utc)
    if progress_status == "submitted":
        return "submitted"
    due = _parse_iso(assignment.get("due_date"))
    if due and due < now:
        return "overdue"
    published_at = _parse_iso(assignment.get("published_at"))
    seen = _parse_iso(seen_at)
    if published_at and (seen is None or published_at > seen):
        return "new"
    if progress_status == "in_progress":
        return "in_progress"
    return "pending"


def classify_assignments(
    assignments: List[Dict[str, Any]],
    progress_by_id: Dict[int, str],
    seen_at: Optional[str],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    out = []
    for a in assignments:
        status = progress_by_id.get(a["id"], "not_started")
        out.append({**a, "my_status": status, "state": classify_assignment(a, status, seen_at, now)})
    return out


def summarize_assignment_states(classified: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"new": 0, "pending": 0, "in_progress": 0, "submitted": 0, "overdue": 0}
    for a in classified:
        counts[a["state"]] = counts.get(a["state"], 0) + 1
    counts["remaining"] = counts["new"] + counts["pending"] + counts["in_progress"] + counts["overdue"]
    return counts


# ---- human-friendly formatting (shared by the panel's text and speech) ------

def format_assignment_counts(counts: Dict[str, int]) -> str:
    remaining = counts.get("remaining", 0)
    if remaining == 0:
        return "You have no assignments left."
    sentence = f"You have {remaining} assignment{'s' if remaining != 1 else ''} left"
    extras = []
    if counts.get("new"):
        extras.append(f"{counts['new']} new")
    if counts.get("overdue"):
        extras.append(f"{counts['overdue']} overdue")
    if extras:
        sentence += ", including " + " and ".join(extras)
    return sentence + "."


def format_module_progress(module_title: Optional[str], module_index: Optional[int], module_total: int) -> str:
    if not module_title:
        return ""
    if module_index and module_total:
        return f"You're on {module_title}, module {module_index} of {module_total}."
    return f"You're on {module_title}."


def format_completion_ratio(done: int, total: int) -> str:
    if total <= 0:
        return ""
    return f"{done} of {total}"


def format_project_progress(title: str, done: int, total: int) -> str:
    if total <= 0:
        return ""
    if done >= total:
        return f"You finished {title}. All {total} checkpoints are complete."
    if done == 0:
        return f"{title} is ready to start."
    return f"Your {title} project is {done} of {total} checkpoints complete."


def welcome_back_summary(
    learner_name: str,
    cohort_name: str,
    assignment_counts: Dict[str, int],
    module_phrase: str = "",
    project_phrase: str = "",
    project_in_progress: bool = False,
) -> str:
    remaining = assignment_counts.get("remaining", 0)
    has_attention = remaining > 0 or project_in_progress
    parts = [f"Welcome back, {learner_name}."]
    if has_attention:
        if cohort_name:
            parts.append(f"You're in {cohort_name}.")
        if remaining > 0:
            parts.append(format_assignment_counts(assignment_counts))
    if module_phrase:
        parts.append(module_phrase)
    if project_phrase and project_in_progress:
        parts.append(project_phrase)
    return " ".join(p for p in parts if p)


def no_cohort_orientation() -> str:
    return (
        "Welcome to CodeUp. If your instructor gave you a classroom code, enter it "
        "in the Classroom panel or say 'join a cohort.' You can still use CodeUp "
        "normally without joining a classroom."
    )


def joined_orientation(cohort_name: str) -> str:
    return (
        f"You joined {cohort_name}. Your editor is your main workspace. You can "
        "use the command box or voice commands to open assignments, continue "
        "your course, work on projects or ask your instructor for help. Say "
        "'what should I do?' at any time."
    )


def join_success_message(cohort_name: str) -> str:
    return f"You joined {cohort_name}."


def join_not_found_message() -> str:
    return "I couldn't find a classroom with that code. Check the code and try again."


# ---- guided-project feedback copy -------------------------------------------
#
# Deterministic checkpoint *validation* stays entirely in guided_projects.py
# (AST-based, authoritative). This only turns already-computed checkpoint ids
# into humane sentences instead of "checkpoint_completed: true" - see spec
# section 8/12.

_STUDENT_MARKS_FEEDBACK = {
    "dictionary": {
        "achieved": "Your marks are now stored in a dictionary.",
        "next": "add up the marks to get a total",
        "incomplete_hint": "I can't find a dictionary of marks yet. Keep trying, or ask me for a hint.",
    },
    "total": {
        "achieved": "You've calculated the total of the marks.",
        "next": "calculate the average",
        "incomplete_hint": "You've stored the marks, but I can't find a total yet. Keep trying, or ask me for a hint.",
    },
    "average": {
        "achieved": "Your marks are now stored correctly and the average is calculated.",
        "next": "print the result",
        "incomplete_hint": "You've got a total, but I can't find the average calculation yet. Keep trying, or ask me for a hint.",
    },
    "output": {
        "achieved": "You've printed the result.",
        "next": None,
        "incomplete_hint": "Everything is calculated, but I can't see it printed yet. Keep trying, or ask me for a hint.",
    },
}


def _checkpoint_copy(project_id: str, checkpoint_id: str) -> Dict[str, Optional[str]]:
    if project_id == "student_marks" and checkpoint_id in _STUDENT_MARKS_FEEDBACK:
        return _STUDENT_MARKS_FEEDBACK[checkpoint_id]
    return {}


def project_intro(project: Dict[str, Any]) -> str:
    total = len(project.get("checkpoints") or [])
    first_label = project["checkpoints"][0]["label"] if total else ""
    parts = [project["title"] + " is a short project where you'll " + project.get("description", "").rstrip(".").lower() + "."]
    if total:
        parts.append(f"There are {total} steps.")
    if first_label:
        parts.append(f"Your first step is to {first_label[0].lower()}{first_label[1:]}.")
    return " ".join(parts)


def project_returning_intro(project: Dict[str, Any], completed_ids: List[str]) -> str:
    checkpoints = project.get("checkpoints") or []
    total = len(checkpoints)
    done = len(completed_ids)
    if done >= total and total:
        return f"You finished {project['title']}. All {total} checkpoints are complete."
    next_cp = next((c for c in checkpoints if c["id"] not in completed_ids), None)
    next_phrase = f" Next, {next_cp['label'][0].lower()}{next_cp['label'][1:]}." if next_cp else ""
    return f"You're continuing {project['title']}. You've finished {done} of {total} steps.{next_phrase}"


def checkpoint_completion_feedback(project: Dict[str, Any], newly_completed: List[str]) -> str:
    """Called right after Run when at least one checkpoint newly passed."""
    if not newly_completed:
        return ""
    checkpoints = {c["id"]: c for c in project.get("checkpoints") or []}
    last_id = newly_completed[-1]
    copy = _checkpoint_copy(project["id"], last_id)
    achieved = copy.get("achieved") or f"{checkpoints.get(last_id, {}).get('label', 'Checkpoint')} - done."
    next_step = copy.get("next")
    if next_step:
        return f"{achieved} Next, {next_step}."
    total = len(project.get("checkpoints") or [])
    if len(newly_completed) and last_id == (project.get("checkpoints") or [{}])[-1].get("id"):
        return f"{achieved} You finished {project['title']}. All {total} checkpoints are complete."
    return achieved


def checkpoint_incomplete_feedback(project: Dict[str, Any], completed_ids: List[str]) -> str:
    """Deterministic, useful feedback when Run didn't newly complete anything."""
    checkpoints = project.get("checkpoints") or []
    next_cp = next((c for c in checkpoints if c["id"] not in completed_ids), None)
    if not next_cp:
        return ""
    copy = _checkpoint_copy(project["id"], next_cp["id"])
    return copy.get("incomplete_hint") or f"Keep working toward: {next_cp['label']}."


# ---- "what should I do?" (section 21) ---------------------------------------

def what_should_i_do(
    *,
    joined: bool,
    active_assignment: Optional[Dict[str, Any]] = None,
    active_project: Optional[Dict[str, Any]] = None,
    new_assignment: Optional[Dict[str, Any]] = None,
    overdue_assignment: Optional[Dict[str, Any]] = None,
    pending_assignment: Optional[Dict[str, Any]] = None,
    module_phrase: str = "",
    available_project: Optional[Dict[str, Any]] = None,
) -> str:
    if not joined:
        return "You're not in a classroom yet. Enter the code your instructor gave you."
    if active_project and active_project.get("next_step"):
        return f"You're currently working on {active_project['title']}. Your next step is to {active_project['next_step']}."
    if active_assignment:
        return f"You're currently working on {active_assignment['title']}. Continue where you left off, or say 'read this assignment' to hear the instructions again."
    if new_assignment:
        return f"You have a new assignment: {new_assignment['title']}. Say 'open {new_assignment['title']}' to start."
    if overdue_assignment:
        return f"{overdue_assignment['title']} is overdue. Say 'open {overdue_assignment['title']}' to finish it."
    if pending_assignment:
        return f"You have a pending assignment: {pending_assignment['title']}. Say 'open {pending_assignment['title']}' to start."
    if module_phrase:
        return f"Continue your course. {module_phrase}"
    if available_project:
        return f"Try the guided project {available_project['title']}. Say 'open {available_project['title']}' to start."
    return "You're all caught up. Say 'continue my course' to keep learning, or 'what's new' to check for updates."

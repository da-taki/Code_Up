"""Unit tests for codeup.classroom.learner_context: assignment
classification, human-friendly formatting, guided-project feedback copy, and
the deterministic "what should I do" resolver. Pure logic, no Flask/db."""

from datetime import datetime, timezone

from codeup.classroom import learner_context as lc

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _assignment(**overrides):
    base = {"id": 1, "title": "Student Marks", "due_date": None, "published_at": None}
    base.update(overrides)
    return base


# ---- classification ----------------------------------------------------------

def test_classify_submitted_wins_even_if_overdue():
    a = _assignment(due_date="2026-01-01", published_at="2026-01-01T00:00:00+00:00")
    assert lc.classify_assignment(a, "submitted", None, NOW) == "submitted"


def test_classify_overdue():
    a = _assignment(due_date="2026-01-01", published_at="2026-01-01T00:00:00+00:00")
    assert lc.classify_assignment(a, "not_started", "2026-01-02T00:00:00+00:00", NOW) == "overdue"


def test_classify_new_when_published_after_seen():
    a = _assignment(published_at="2026-05-30T00:00:00+00:00")
    assert lc.classify_assignment(a, "not_started", "2026-05-20T00:00:00+00:00", NOW) == "new"


def test_classify_new_when_never_seen():
    a = _assignment(published_at="2026-05-30T00:00:00+00:00")
    assert lc.classify_assignment(a, "not_started", None, NOW) == "new"


def test_classify_pending_when_already_seen_and_not_overdue():
    a = _assignment(published_at="2026-01-01T00:00:00+00:00")
    assert lc.classify_assignment(a, "not_started", "2026-05-01T00:00:00+00:00", NOW) == "pending"


def test_classify_in_progress():
    a = _assignment(published_at="2026-01-01T00:00:00+00:00")
    assert lc.classify_assignment(a, "in_progress", "2026-05-01T00:00:00+00:00", NOW) == "in_progress"


def test_summarize_assignment_states_counts_remaining():
    classified = [
        {"state": "new"}, {"state": "pending"}, {"state": "overdue"},
        {"state": "submitted"}, {"state": "submitted"},
    ]
    counts = lc.summarize_assignment_states(classified)
    assert counts["new"] == 1 and counts["pending"] == 1 and counts["overdue"] == 1
    assert counts["submitted"] == 2
    assert counts["remaining"] == 3  # new + pending + overdue (submitted excluded)


# ---- human-friendly formatting -------------------------------------------------

def test_format_assignment_counts_zero():
    assert lc.format_assignment_counts({"remaining": 0}) == "You have no assignments left."


def test_format_assignment_counts_with_new_and_overdue():
    text = lc.format_assignment_counts({"remaining": 3, "new": 1, "overdue": 1})
    assert "3 assignments left" in text
    assert "1 new" in text and "1 overdue" in text
    assert "checkpoint_completed" not in text  # never raw db language


def test_format_module_progress():
    assert lc.format_module_progress("Conditions", 5, 12) == "You're on Conditions, module 5 of 12."
    assert lc.format_module_progress("", 5, 12) == ""


def test_format_project_progress_variants():
    assert lc.format_project_progress("Student Marks", 0, 4) == "Student Marks is ready to start."
    assert "2 of 4" in lc.format_project_progress("Student Marks", 2, 4)
    assert "finished" in lc.format_project_progress("Student Marks", 4, 4)


def test_welcome_back_summary_nothing_to_attend_to():
    text = lc.welcome_back_summary("Amir", "Python Beginners", {"remaining": 0})
    assert text == "Welcome back, Amir."


def test_welcome_back_summary_with_attention_items():
    text = lc.welcome_back_summary(
        "Amir", "Python Beginners", {"remaining": 2, "new": 1, "overdue": 0},
        module_phrase="You're on Conditions, module 5 of 12.",
    )
    assert "Welcome back, Amir." in text
    assert "Python Beginners" in text
    assert "2 assignments left" in text
    assert "Conditions" in text


# ---- guided-project feedback -------------------------------------------------

_PROJECT = {
    "id": "student_marks", "title": "Student Marks Program",
    "description": "Build a small program that stores marks and prints the average.",
    "checkpoints": [
        {"id": "dictionary", "label": "Create a dictionary of student marks"},
        {"id": "total", "label": "Compute the total of the marks"},
        {"id": "average", "label": "Compute the average of the marks"},
        {"id": "output", "label": "Print the result"},
    ],
}


def test_project_intro_mentions_first_step():
    text = lc.project_intro(_PROJECT)
    assert "Student Marks Program" in text
    assert "4 steps" in text
    assert "first step" in text.lower()


def test_project_returning_intro_mid_progress():
    text = lc.project_returning_intro(_PROJECT, ["dictionary"])
    assert "1 of 4" in text
    assert "Next" in text


def test_project_returning_intro_complete():
    text = lc.project_returning_intro(_PROJECT, ["dictionary", "total", "average", "output"])
    assert "finished" in text
    assert "All 4 checkpoints are complete" in text


def test_checkpoint_completion_feedback_is_humane_not_raw():
    text = lc.checkpoint_completion_feedback(_PROJECT, ["dictionary"])
    assert "checkpoint_completed" not in text
    assert "dictionary" in text.lower() or "stored" in text.lower()
    assert "next" in text.lower()


def test_checkpoint_completion_feedback_last_checkpoint_announces_finish():
    text = lc.checkpoint_completion_feedback(_PROJECT, ["output"])
    assert "finished" in text.lower() or "printed" in text.lower()


def test_checkpoint_incomplete_feedback_points_to_next_step():
    text = lc.checkpoint_incomplete_feedback(_PROJECT, [])
    assert text  # non-empty, deterministic nudge
    assert "checkpoint" not in text.lower() or "hint" in text.lower()


def test_checkpoint_incomplete_feedback_all_done_is_empty():
    assert lc.checkpoint_incomplete_feedback(_PROJECT, ["dictionary", "total", "average", "output"]) == ""


# ---- what should I do? ---------------------------------------------------------

def test_what_should_i_do_not_joined():
    text = lc.what_should_i_do(joined=False)
    assert "not in a classroom" in text.lower()


def test_what_should_i_do_priority_active_project_over_everything():
    text = lc.what_should_i_do(
        joined=True,
        active_project={"title": "Student Marks Program", "next_step": "calculate the average"},
        new_assignment={"title": "Ignored Assignment"},
    )
    assert "Student Marks Program" in text
    assert "average" in text
    assert "Ignored Assignment" not in text


def test_what_should_i_do_new_assignment_before_pending():
    text = lc.what_should_i_do(
        joined=True,
        new_assignment={"title": "New One"},
        pending_assignment={"title": "Old One"},
    )
    assert "New One" in text
    assert "Old One" not in text


def test_what_should_i_do_falls_back_to_available_project():
    text = lc.what_should_i_do(joined=True, available_project={"title": "Student Marks Program"})
    assert "Student Marks Program" in text


def test_what_should_i_do_all_caught_up():
    text = lc.what_should_i_do(joined=True)
    assert "caught up" in text.lower()

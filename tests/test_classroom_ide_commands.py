"""Unit tests for codeup.classroom.ide_commands: the deterministic phrase
matcher (no Flask, no db) that decides which typed/spoken utterances are
classroom commands, plus handle() logic exercised with a fake context so it
never needs a real learner/db to test priority/branching behavior."""

import pytest

from codeup.classroom import ide_commands as ic


# ---- match(): phrase coverage --------------------------------------------------

NAV_PHRASES = [
    ("go to classroom", "classroom"), ("go to editor", "editor"),
    ("go to the command box", "command box"), ("go to output", "output"),
    ("go to help", "help"), ("go to projects", "projects"),
]

ASSIGNMENT_PHRASES = [
    "open my assignments", "what assignments do I have?", "how many assignments do I have?",
    "how many assignments are left?", "go back to my assignments", "what's new?",
    "what is due", "what is overdue", "read this assignment", "repeat the instructions",
    "what am I supposed to do?", "what can AI help me with?", "submit my assignment", "submit this",
]

CURRICULUM_PHRASES = [
    "continue my course", "continue where I left off", "open my course",
    "start from the beginning", "restart the course", "restart this lesson",
    "next lesson", "previous lesson", "read this lesson", "read the example",
    "take the quiz", "what module am I on?", "show my progress", "how much have I completed?",
]

PROJECT_PHRASES = [
    "open my projects", "what projects do I have?", "how many projects do I have?",
    "continue my project", "what am I working on?", "what's the current step?",
    "repeat the project instructions", "repeat the project introduction",
    "give me a hint", "repeat the hint", "check my progress",
]

HELP_PHRASES = [
    "I need help", "ask my teacher for help", "cancel my help request", "is my teacher helping me?",
]

JOIN_PHRASES = [
    "join a cohort", "join my class", "join classroom", "enter class code",
    "what class am I in?", "leave this class",
]

NON_CLASSROOM_PHRASES = [
    "run the code", "fix this", "what does this error mean", "insert a for loop",
    "explain this function", "save snippet", "hello world", "",
]


@pytest.mark.parametrize("target_phrase,target", NAV_PHRASES)
def test_nav_focus_matches(target_phrase, target):
    intent, slots = ic.match(target_phrase)
    assert intent == "nav_focus"
    assert slots["target"] == target


@pytest.mark.parametrize("phrase", ASSIGNMENT_PHRASES)
def test_assignment_phrases_match_a_classroom_intent(phrase):
    result = ic.match(phrase)
    assert result is not None, f"{phrase!r} should be recognized as a classroom command"


@pytest.mark.parametrize("phrase", CURRICULUM_PHRASES)
def test_curriculum_phrases_match_a_classroom_intent(phrase):
    assert ic.match(phrase) is not None, f"{phrase!r} should be recognized"


@pytest.mark.parametrize("phrase", PROJECT_PHRASES)
def test_project_phrases_match_a_classroom_intent(phrase):
    assert ic.match(phrase) is not None, f"{phrase!r} should be recognized"


@pytest.mark.parametrize("phrase", HELP_PHRASES)
def test_help_phrases_match_a_classroom_intent(phrase):
    assert ic.match(phrase) is not None, f"{phrase!r} should be recognized"


@pytest.mark.parametrize("phrase", JOIN_PHRASES)
def test_join_phrases_match_a_classroom_intent(phrase):
    assert ic.match(phrase) is not None, f"{phrase!r} should be recognized"


@pytest.mark.parametrize("phrase", NON_CLASSROOM_PHRASES)
def test_non_classroom_phrases_fall_through(phrase):
    assert ic.match(phrase) is None, f"{phrase!r} must NOT be captured as a classroom command"


def test_join_code_extraction_from_natural_phrasing():
    assert ic.match("join ABC123") == ("join_with_code", {"code": "ABC123"})
    assert ic.match("my class code is XYZ789") == ("join_with_code", {"code": "XYZ789"})


def test_open_assignment_by_index():
    assert ic.match("open assignment 2") == ("open_assignment_index", {"index": 2})


def test_open_by_name_extracts_free_text():
    intent, slots = ic.match("open Student Marks")
    assert intent == "open_by_name"
    assert slots["name"] == "student marks"


# ---- handle(): branching behavior with a fake ctx ------------------------------

def _ctx(learner=None, summary=None, **overrides):
    base = {
        "learner": learner, "summary": summary, "current_code": "",
        "assignment_cookie_id": None, "project_cookie_id": None, "module_cookie_id": None,
        "join_name": "",
    }
    base.update(overrides)
    return base


def test_handle_requires_learner_for_gated_intents():
    resp = ic.handle("assignments_list", {}, _ctx(learner=None))
    assert resp["success"] is True
    assert "not in a classroom" in resp["message"].lower()


def test_handle_join_with_code_needs_name_first():
    resp = ic.handle("join_with_code", {"code": "ABC123"}, _ctx(learner=None, join_name=""))
    assert "what name should i use" in resp["message"].lower()
    assert resp["join_code_hint"] == "ABC123"
    assert resp["_join_pending"] == {"state": "waiting_for_name", "code": "ABC123"}


def test_handle_already_joined_join_attempt_is_rejected():
    resp = ic.handle("join_with_code", {"code": "ABC123"}, _ctx(learner={"id": 1}, summary={}))
    assert "already in a classroom" in resp["message"].lower()


def test_handle_nav_focus_returns_focus_target_action():
    resp = ic.handle("nav_focus", {"target": "editor"}, _ctx())
    assert resp["action"] == "focus_target"
    assert resp["target"] == "__editor__"


def test_handle_leave_class_navigates_to_confirm_page_not_immediate_leave():
    resp = ic.handle("leave_class", {}, _ctx(learner={"id": 1}, summary={"cohort": {"name": "C"}}))
    assert resp["action"] == "navigate"
    assert "confirm" in resp["url"]


def test_handle_open_assignment_index_out_of_range():
    summary = {"assignments": []}
    resp = ic.handle("open_assignment_index", {"index": 1}, _ctx(learner={"id": 1}, summary=summary))
    assert "only have 0" in resp["message"]


def test_handle_ai_policy_defaults_to_full_when_no_assignment_open():
    resp = ic.handle("ai_policy", {}, _ctx(learner={"id": 1}, summary={"assignments": []}))
    assert "full ai assistance" in resp["message"].lower()

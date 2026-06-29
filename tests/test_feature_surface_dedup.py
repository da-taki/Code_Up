"""Feature-surface deduplication / clarity guards.

These tests lock in that CodeUp's overlapping command surfaces have one clear
canonical owner each, so the product reads as a single coherent tool rather than
many stacked slices. They are fully deterministic: no microphone, screen reader,
Braille, Intel packages, OpenVINO, or external AI is required.
"""

from pathlib import Path

import pytest

from app import app
from intent_parser import parse_intent

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def _vc(client, text, **payload):
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


def _spoken(data):
    return (data.get("speech") or data.get("message") or "").strip()


# --- Canonical routing: distinct features must not collapse into each other ---

def test_teacher_report_vs_lesson_report_are_distinct(client):
    full = _vc(client, "make a teacher report")
    lesson = _vc(client, "teacher lesson report")
    # Both succeed deterministically...
    for d in (full, lesson):
        assert d.get("action") == "deterministic_message"
        assert d.get("success") is not False
    # ...but they are clearly different surfaces, not the same answer twice.
    assert lesson.get("intent") == "programming_literacy"
    assert full.get("intent") != "programming_literacy"
    assert _spoken(full) != _spoken(lesson)


def test_codex_handoff_is_not_a_teacher_report(client):
    handoff = _vc(client, "make codex handoff")
    report = _vc(client, "make a teacher report")
    assert handoff.get("intent") == "handoff"
    assert "codex" in _spoken(handoff).lower()
    # The handoff (for a coding agent) must not be the teacher report (for a
    # trainer); different owners, different speech.
    assert _spoken(handoff) != _spoken(report)


def test_understanding_check_vs_lesson_understanding_are_distinct(client):
    session_q = _vc(client, "check my understanding")
    lesson_q = _vc(client, "check lesson understanding")
    assert session_q.get("intent") == "understanding_question"
    assert lesson_q.get("intent") == "programming_literacy"
    assert _spoken(session_q) != _spoken(lesson_q)


def test_lesson_commands_canonically_belong_to_programming_literacy():
    # Shared lesson phrases resolve to Programming Literacy Mode (registered
    # ahead of the older accessible-learning surface), not the legacy path.
    for phrase in ("list lessons", "show lessons", "next lesson", "previous lesson"):
        parsed = parse_intent(phrase)
        assert parsed["intent"] == "programming_literacy", (phrase, parsed["intent"])
        assert parsed["confidence"] >= 0.9


def test_list_lessons_with_no_mode_active_lists_literacy_lessons(client):
    # Step 4A preferred behavior: with neither lesson mode running, "list lessons"
    # clearly surfaces Programming Literacy Mode lessons rather than dead-ending.
    data = _vc(client, "list lessons")
    assert data.get("action") == "deterministic_message"
    assert data.get("intent") == "programming_literacy"
    assert "literacy mode lessons" in _spoken(data).lower()


# --- Intel aliases all reach the one canonical handler ---

@pytest.mark.parametrize(
    "phrase",
    ["intel toolkit status", "show intel optimization report", "intel status"],
)
def test_intel_aliases_route_to_one_canonical_handler(client, phrase):
    data = _vc(client, phrase)
    assert data.get("intent") == "intel_toolkit_status"
    assert data.get("action") == "deterministic_message"


# --- Audio Blocks: every advertised command is safe inside the mode ---

@pytest.mark.parametrize(
    "phrase",
    ["read block map", "read block order", "list blocks", "read current block"],
)
def test_audio_blocks_advertised_commands_are_safe_in_mode(client, phrase):
    _vc(client, "open audio blocks")
    data = _vc(client, phrase, active_mode="audio_blocks")
    assert data.get("success") is not False
    assert data.get("action") not in (None, "unknown", "needs_clarification")
    spoken = _spoken(data).lower()
    # No advertised Audio Blocks command may dead-end with "not available yet".
    assert "not available yet" not in spoken
    assert spoken  # something useful is always said


def test_switch_to_python_code_mode_exits_audio_blocks(client):
    _vc(client, "open audio blocks")
    data = _vc(client, "switch to python code mode", active_mode="audio_blocks")
    assert "code mode is on" in _spoken(data).lower()


# --- No canonical command falls into AI clarification / unknown ---

CANONICAL_COMMANDS = [
    # Programming Literacy Mode
    "start literacy mode", "list lessons", "what am I learning", "complete lesson",
    "graduation report",
    # Tutor Mode
    "start tutor mode", "give me a hint", "show fix",
    # Understanding Checks
    "check my understanding", "give me a similar exercise",
    # Codex Handoff
    "make codex handoff",
    # Teacher Reports
    "make a teacher report", "teacher lesson report",
    # Cockpit
    "project map", "explain error", "what changed", "show program state", "where am I",
    # Audio Blocks
    "open audio blocks",
    # Intel
    "intel toolkit status",
]


@pytest.mark.parametrize("phrase", CANONICAL_COMMANDS)
def test_canonical_commands_do_not_fall_into_ai_clarification(client, phrase):
    data = _vc(client, phrase, code="print('hi')\n")
    assert data.get("success") is not False, (phrase, data)
    assert data.get("action") not in (None, "unknown", "needs_clarification"), (phrase, data)


# --- README stays consolidated: each shared claim appears once ---

def _readme():
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_does_not_repeat_intel_not_required_claim():
    readme = _readme()
    assert readme.count("does not require all Intel packages") == 1


def test_readme_does_not_repeat_not_replacing_coding_agents_claim():
    readme = _readme()
    assert readme.count("replace professional coding agents") == 1


def test_readme_has_single_authoritative_command_group_map():
    readme = _readme()
    assert "## Commands" in readme
    # The map names each canonical group once.
    for marker in ("Programming Literacy Mode", "Tutor Mode", "Understanding Checks",
                   "Codex Handoff Pack", "Teacher Reports", "Audio Blocks"):
        assert marker in readme

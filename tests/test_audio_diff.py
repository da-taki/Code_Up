"""Audio Diff Review + Safe Apply/Reject: review code changes non-visually.

Covers the deterministic audio_diff module (line diff, risk labels, narration,
multi-file) and the /voice-command review + proposal workflow (what changed,
before/after, explain, risk, next/prev, accept, undo, fix-with-explanation
proposal, apply, reject), plus that applied edits and Audio Blocks transfer
record a reviewable diff.
"""

import pytest

from codeup.accessibility import audio_diff
from app import app
from codeup.commands.intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def vc(client, text, **payload):
    payload.setdefault("source", "typed")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


def run(client, code, **extra):
    return client.post("/run", json={"code": code, **extra}).get_json()


# ---- module: diff + risk + narration -----------------------------------

def test_added_line():
    changes = audio_diff.diff_lines("print(1)\n", "print(1)\nprint(2)\n")
    assert any(c["kind"] == "added" and "print(2)" in c["after"] for c in changes)


def test_deleted_line():
    changes = audio_diff.diff_lines("a=1\nb=2\n", "a=1\n")
    assert any(c["kind"] == "removed" and "b=2" in c["before"] for c in changes)


def test_changed_line():
    changes = audio_diff.diff_lines("x = 1\n", "x = 2\n")
    assert changes and changes[0]["kind"] == "changed"


def test_multi_line_change():
    change = audio_diff.summarize_change("a=1\nb=2\nc=3\n", "a=1\nb=20\nc=30\nd=4\n")
    # difflib groups the trailing new line into the replace block; the point is
    # that a multi-line edit produces several reviewable changes.
    assert len(change["changes"]) >= 3


def test_risk_low_for_added_print():
    change = audio_diff.summarize_change("x = 1\n", "x = 1\nprint(x)\n")
    assert change["risk"] == "low"


def test_risk_medium_for_condition_change():
    change = audio_diff.summarize_change("if a > 1:\n    pass\n", "if a >= 2:\n    pass\n")
    assert change["risk"] == "medium"


def test_risk_high_for_file_io():
    change = audio_diff.summarize_change("print(1)\n", "open('f.txt').read()\n")
    assert change["risk"] == "high"


def test_risk_high_for_many_deletions():
    before = "\n".join(f"x{i} = {i}" for i in range(8)) + "\n"
    change = audio_diff.summarize_change(before, "x0 = 0\n")
    assert change["risk"] == "high"


def test_before_after_narration():
    change = audio_diff.summarize_change("a = 1\n", "a = 2\n")
    narration = audio_diff.narrate_before_after(change)
    assert "Before:" in narration and "After:" in narration
    assert "a = 1" in narration and "a = 2" in narration


def test_narrate_mentions_risk_and_actions():
    change = audio_diff.summarize_change("x = 1\n", "x = 1\nprint(x)\n", file_name="main.py")
    speech = audio_diff.narrate(change)
    assert "main.py" in speech
    assert "Risk:" in speech
    assert "accept this change" in speech


def test_project_diff_multi_file_summary():
    pd = audio_diff.project_diff(
        {"main.py": "print(1)\n"},
        {"main.py": "print(1)\nprint(2)\n", "score.py": "def s():\n    return 0\n"})
    assert pd["files_changed"] == 2
    speech = audio_diff.narrate_project(pd)
    assert "2 files" in speech
    assert "score.py" in speech


# ---- routing: review + proposal workflow -------------------------------

def test_intents_registered():
    assert parse_intent("what changed")["intent"] == "diff_review"
    assert parse_intent("read before and after")["intent"] == "diff_before_after"
    assert parse_intent("explain this change")["intent"] == "diff_explain"
    assert parse_intent("is this risky")["intent"] == "diff_risk"
    assert parse_intent("next change")["intent"] == "diff_next"
    assert parse_intent("undo last change")["intent"] == "undo_last_change"
    assert parse_intent("accept this change")["intent"] == "accept_change"


def test_no_change_available_response(client):
    data = vc(client, "what changed", code="print('hi')\n")
    assert data["action"] == "deterministic_message"
    assert "no code changes to review" in data["speech"].lower()


def test_edit_records_diff_and_what_changed_reads_it(client):
    vc(client, "clear editor", code="print('old')\n")
    edit = vc(client, "insert a for loop that prints the first 3 whole numbers", code="")
    assert edit["action"] == "conversational_edit"
    data = vc(client, "what changed", code="for i in range(3):\n    print(i)\n")
    assert data["action"] == "deterministic_message"
    assert "Change 1" in data["speech"]
    assert data["audio_diff"]["total_changes"] >= 1


def test_read_before_and_after(client):
    vc(client, "clear editor", code="print('old')\n")
    vc(client, "insert a for loop that prints the first 3 whole numbers", code="")
    data = vc(client, "read before and after")
    assert "Before:" in data["speech"] and "After:" in data["speech"]


def test_explain_this_change(client):
    vc(client, "clear editor", code="print('old')\n")
    vc(client, "insert a for loop that prints the first 3 whole numbers", code="")
    data = vc(client, "explain this change")
    assert data["action"] == "deterministic_message"
    assert "Line" in data["speech"]


def test_is_this_risky(client):
    vc(client, "clear editor", code="print('old')\n")
    vc(client, "insert a for loop that prints the first 3 whole numbers", code="")
    data = vc(client, "is this risky")
    assert data["action"] == "deterministic_message"
    assert "Risk:" in data["speech"]


def test_undo_last_change_restores_previous(client):
    vc(client, "clear editor", code="print('keep')\n")
    vc(client, "insert a for loop that prints the first 3 whole numbers", code="")
    undo = vc(client, "undo last change", code="for i in range(3):\n    print(i)\n")
    # Undo returns an edit (or clear) that restores the pre-insert state.
    assert undo["action"] in ("conversational_edit", "clear_editor")
    assert "ai_action" not in undo or "for i in range" not in undo["ai_action"].get("code", "")


def test_fix_with_explanation_creates_proposal_without_applying(client):
    broken = "for i in range(3):\nprint(i)\n"
    run(client, broken)
    proposal = vc(client, "fix with explanation", code=broken)
    assert proposal["action"] == "deterministic_message"
    assert "Proposed change:" in proposal["speech"]
    assert "Say apply, reject, or explain" in proposal["speech"]
    assert "ai_action" not in proposal  # confirmation required: nothing applied yet


def test_pending_fix_proposal_can_read_before_and_after_before_apply(client):
    broken = "for i in range(3):\nprint(i)\n"
    run(client, broken)
    vc(client, "fix with explanation", code=broken)
    review = vc(client, "read before and after", code=broken)
    assert review["action"] == "deterministic_message"
    assert review["intent"] == "diff_before_after"
    assert "before" in review["speech"].lower()
    assert "after" in review["speech"].lower()
    assert "print(i)" in review["speech"]
    assert "no code changes" not in review["speech"].lower()
    assert "ai_action" not in review


def test_apply_proposal_applies_and_records_diff(client):
    broken = "for i in range(3):\nprint(i)\n"
    run(client, broken)
    vc(client, "fix with explanation", code=broken)
    applied = vc(client, "apply", code=broken)
    assert applied["action"] == "conversational_edit"
    assert "    print(i)" in applied["ai_action"]["code"]
    # The applied fix is itself a reviewable change.
    review = vc(client, "what changed", code="for i in range(3):\n    print(i)\n")
    assert "indented" in review["speech"].lower()


def test_reject_proposal_does_not_modify_code(client):
    broken = "for i in range(3):\nprint(i)\n"
    run(client, broken)
    vc(client, "fix with explanation", code=broken)
    rejected = vc(client, "reject", code=broken)
    assert rejected["action"] == "deterministic_message"
    assert "not modified" in rejected["speech"].lower()
    assert "ai_action" not in rejected


def test_explain_proposal(client):
    broken = "for i in range(3):\nprint(i)\n"
    run(client, broken)
    vc(client, "fix with explanation", code=broken)
    data = vc(client, "explain", code=broken)
    assert data["action"] == "deterministic_message"
    assert "indent" in data["speech"].lower()


def test_audio_blocks_transfer_records_diff(client):
    vc(client, "open audio blocks")
    vc(client, "add print block")
    vc(client, "set message to Hello CodeUp")
    vc(client, "compile blocks")
    transfer = vc(client, "transfer blocks to Python mode", code="")
    assert transfer["action"] == "conversational_edit"
    data = vc(client, "what changed", code="print('Hello CodeUp')\n")
    assert data["action"] == "deterministic_message"
    assert "Hello CodeUp" in data["speech"]


def test_audio_diff_does_not_call_ai(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider called for deterministic audio diff")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    vc(client, "clear editor", code="print('old')\n")
    vc(client, "insert a for loop that prints the first 3 whole numbers", code="")
    assert vc(client, "what changed")["success"] is not False

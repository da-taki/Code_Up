"""Teacher Report + Session Recap upgrade (slice 6).

Covers the deterministic teacher_report aggregator (project map, error trace,
audio diff change history, state watch, run/session memory) and the /voice-command
routing, plus that the existing project-report behavior is preserved.
"""

import pytest

from codeup.reports import teacher_report
from app import app


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def vc(client, text, **payload):
    payload.setdefault("source", "typed")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


LOOP = "for i in range(3):\n    print(i)\n"

RICH_MEM = {
    "command_count": 6,
    "command_type_counts": {"project_map": 1, "run": 2, "explain_error": 1, "fix": 1, "what_changed": 1},
    "run_count": 2,
    "last_run_output": "0\n1\n2",
    "last_run_error": "Line 2: IndentationError: expected an indented block",
    "error_type_counts": {"IndentationError": 1},
    "fixes_applied": 1, "fixes_rejected": 0,
    "change_history": [{"before": "for i in range(3):\nprint(i)\n",
                        "after": "for i in range(3):\n    print(i)\n", "file": "", "reason": "fix"}],
    "watched_variables": ["score"],
    "last_state_trace": {"code": "score=0", "error": "",
                          "vars": {"score": {"value": "1", "line": 2, "from": "0"}},
                          "loop": "Loop state: the loop on line 1 ran 1 time.",
                          "conditions": [{"result": True}], "output_lines": 1},
    "last_run_ok": True,
}


# ---- module: build_report ----------------------------------------------

def test_empty_session_is_honest_not_crashing():
    r = teacher_report.build_report({}, None, "")
    assert r["has_content"] is False
    assert "not much" in r["speech"].lower()
    assert "Teacher Report" in r["report_md"]


def test_single_file_report_has_sections():
    r = teacher_report.build_report({"command_count": 1}, {"is_project": False, "code": LOOP}, LOOP)
    md = r["report_md"]
    assert "# CodeUp Teacher Report" in md
    for heading in ("Project Summary", "Files and Structure", "Commands Used",
                    "Concepts Practiced", "Errors and Debugging", "Code Changes Reviewed",
                    "State and Variable Understanding", "Accessibility Workflow",
                    "Final Output / Final State", "Suggested Next Practice"):
        assert heading in md, heading


def test_multi_file_report_uses_project_map():
    project = {"is_project": True, "files": {
        "main.py": "import score\nif __name__ == \"__main__\":\n    print(score.calc())\n",
        "score.py": "def calc():\n    return 1\n"}}
    md = teacher_report.build_report({"command_count": 1}, project, "")["report_md"]
    assert "2 files" in md
    assert "main.py imports score" in md
    assert "score.py" in md


def test_report_includes_error_narration():
    md = teacher_report.build_report(RICH_MEM, {"is_project": False, "code": LOOP}, LOOP)["report_md"]
    assert "IndentationError" in md
    assert "line 2" in md
    assert "Fixes applied: 1" in md


def test_report_includes_audio_diff_changes():
    md = teacher_report.build_report(RICH_MEM, {"is_project": False, "code": LOOP}, LOOP)["report_md"]
    assert "1 change reviewed" in md
    assert "low risk" in md


def test_report_includes_watched_variables_and_state():
    md = teacher_report.build_report(RICH_MEM, {"is_project": False, "code": LOOP}, LOOP)["report_md"]
    assert "Watched variables: score" in md
    assert "score ended as 1" in md
    assert "ran 1 time" in md


def test_report_redacts_secrets():
    mem = {"command_count": 1, "last_state_trace": {
        "code": "x", "error": "", "vars": {"API_KEY": {"value": "'sk-secret'", "line": 1, "from": None}},
        "loop": "", "conditions": [], "output_lines": 0}}
    md = teacher_report.build_report(mem, {"is_project": False, "code": "API_KEY = 'sk-secret'"},
                                    "API_KEY = 'sk-secret'")["report_md"]
    assert "sk-secret" not in md
    assert "sensitive value was hidden" in md


def test_speech_is_concise_relative_to_full_report():
    r = teacher_report.build_report(RICH_MEM, {"is_project": False, "code": LOOP}, LOOP)
    assert len(r["speech"]) < len(r["report_md"])


def test_next_practice_suggestion():
    nxt = teacher_report.next_practice({"last_run_ok": False}, LOOP)
    assert "fix" in nxt.lower() or "error" in nxt.lower()
    nxt2 = teacher_report.next_practice({}, LOOP)
    assert nxt2  # always returns something


def test_learner_recap_is_short():
    r = teacher_report.learner_recap(RICH_MEM, LOOP)
    assert r["recap"]
    assert len(r["recap"]) < 600  # recap stays short vs the full report


def test_what_changed_with_and_without_history():
    with_history = teacher_report.what_changed(RICH_MEM)
    assert "change" in with_history.lower()
    without = teacher_report.what_changed({}, {"is_project": False, "code": LOOP}, LOOP)
    assert "no tracked code changes" in without.lower()


def test_what_errors_fixed_with_and_without():
    assert "IndentationError" in teacher_report.what_errors_fixed(RICH_MEM, LOOP)
    assert "no errors" in teacher_report.what_errors_fixed({}, "").lower()


def test_missing_data_does_not_crash():
    # Each accessor tolerates a bare/empty memory.
    for fn in (teacher_report.build_report, teacher_report.learner_recap,
               teacher_report.what_changed, teacher_report.what_errors_fixed,
               teacher_report.next_practice):
        fn({})  # must not raise


# ---- routing -----------------------------------------------------------

def test_make_teacher_report_command(client):
    client.post("/run", json={"code": LOOP})
    data = vc(client, "make a teacher report", code=LOOP)
    assert data["action"] == "deterministic_message"
    assert "# CodeUp Teacher Report" in data["message"]
    assert data.get("teacher_report") is True
    # concise speech, full report in the message
    assert len(data["speech"]) < len(data["message"])


def test_make_project_report_still_routes_to_project_report(client):
    assert vc(client, "make a project report")["action"] == "project_report"


def test_what_changed_in_this_project_command(client):
    client.post("/run", json={"code": "for i in range(3):\nprint(i)\n"})
    vc(client, "fix with explanation", code="for i in range(3):\nprint(i)\n")
    vc(client, "apply", code="for i in range(3):\nprint(i)\n")
    data = vc(client, "what changed in this project")
    assert data["action"] == "deterministic_message"
    assert "change" in data["speech"].lower()


def test_what_errors_did_i_fix_command(client):
    client.post("/run", json={"code": "print(undefined_name)\n"})
    data = vc(client, "what errors did i fix")
    assert data["action"] == "deterministic_message"
    assert "error" in data["speech"].lower()


def test_what_should_i_practice_next_command(client):
    data = vc(client, "what should I practice next", code=LOOP)
    assert data["action"] == "deterministic_message"
    assert data["speech"]


def test_summarize_my_session_and_what_did_i_learn(client):
    client.post("/run", json={"code": LOOP})
    assert vc(client, "summarize my session")["action"] == "deterministic_message"
    learn = vc(client, "what did I learn today", code=LOOP)
    assert learn["action"] == "deterministic_message"


def test_teacher_report_does_not_call_ai(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider called for deterministic teacher report")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    client.post("/run", json={"code": LOOP})
    assert vc(client, "make a teacher report", code=LOOP)["success"] is not False

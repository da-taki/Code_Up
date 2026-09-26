import pytest

import app as app_module
from codeup.accessibility.speech_output import python_code_to_speech, sanitize_speech_text
from codeup.learning import learner_model, tutorial_engine
from codeup.runtime import diagnostics


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def test_static_diagnostics_collect_multiple_known_problems():
    items = diagnostics.diagnostics_for_code("print(score)\nprint(total)\n")
    assert len(items) >= 2
    assert {item.category for item in items} == {"static"}
    assert any("score" in item.message for item in items)
    assert any("total" in item.message for item in items)


def test_check_syntax_returns_normalized_diagnostics_and_legacy_errors(client):
    data = client.post("/check-syntax", json={"code": "print(score)\nprint(total)\n"}).get_json()
    assert data["has_errors"] is True
    assert data["error_count"] >= 2
    assert len(data["diagnostics"]) == data["error_count"]
    assert len(data["errors"]) == data["error_count"]
    assert "CodeUp found" in data["summary"]


def test_diagnostic_voice_navigation_preserves_selected_state(client):
    client.post("/check-syntax", json={"code": "print(score)\nprint(total)\n"})
    first = _vc(client, "repeat error")
    second = _vc(client, "next error")
    assert first["diagnostic_count"] >= 2
    assert second["selected_index"] == 1
    assert second["line"] == 2
    count = _vc(client, "how many errors")
    assert "problem" in count["speech"].lower()


def test_run_success_clears_stale_diagnostics(client):
    bad = client.post("/run", json={"code": "print(score)"}).get_json()
    assert bad["success"] is False
    assert bad["diagnostics"]
    good = client.post("/run", json={"code": "score = 1\nprint(score)"}).get_json()
    assert good["success"] is True
    repeat = _vc(client, "repeat error")
    assert "no selected error" in repeat["speech"].lower() or "no problems" in repeat["speech"].lower()


def test_tutorial_curriculum_expanded_to_required_beginner_topics():
    pack = tutorial_engine.expanded_module_pack()
    assert pack["count"] >= 31
    for mid in ["input", "type_conversion", "elif", "dictionaries", "return_values", "beginner_projects"]:
        assert mid in pack["modules"]
        assert tutorial_engine.validate_attempt(mid, pack["modules"][mid]["example_code"], ran_ok=True)["passed"] is True


def test_learner_model_adapts_from_evidence_without_sensitive_inference():
    mem = {}
    for _ in range(3):
        learner_model.record_evidence(mem, "loops", "error")
        learner_model.record_evidence(mem, "loops", "hint_used")
    learner_model.record_evidence(mem, "variables", "success")
    learner_model.record_evidence(mem, "variables", "success")
    assert learner_model.concept_state(mem, "loops") == "needs_reinforcement"
    assert learner_model.concept_state(mem, "variables") == "demonstrated"
    assert "shorter" in learner_model.adaptation_note(mem, "loops").lower()
    assert "skip" in learner_model.adaptation_note(mem, "variables").lower()


def test_learner_progress_voice_commands(client):
    _vc(client, "show my learning progress")
    reset = _vc(client, "reset my learning history")
    assert "reset" in reset["speech"].lower()
    practice = _vc(client, "what should i practice")
    assert "start" in practice["speech"].lower() or "practice" in practice["speech"].lower()


def test_python_aware_speech_distinguishes_code_from_prose():
    assert sanitize_speech_text("Call the print function.") == "Call the print function."
    assert python_code_to_speech("print") == "print"
    spoken_call = python_code_to_speech("print()")
    assert "open parenthesis" in spoken_call
    assert python_code_to_speech("x = 5") != python_code_to_speech("x == 5")
    assert "equals equals" in python_code_to_speech("x == 5")
    assert "open bracket" in python_code_to_speech("items[0]")


@pytest.mark.parametrize("text,action", [
    ("code chala do", "run"),
    ("ye code samjhao", "analyze"),
    ("variables batao", "list_variables_voice"),
    ("error fix kar do", "fix"),
    ("output suna do", "speak"),
])
def test_hinglish_semantic_direct_commands(client, text, action):
    data = _vc(client, text)
    assert data["action"] == action
    assert data["source"] == "deterministic_semantic_mapper"



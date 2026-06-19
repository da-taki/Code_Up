
import importlib

import pytest

import app as app_module
import openvino_intent_demo
from openvino_intent_demo import classify_local_intent


RESULT_KEYS = {"available", "source", "intent", "confidence", "note"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c



class TestImportSafety:

    def test_app_module_imported(self):
        assert app_module is not None

    def test_demo_module_imports_without_openvino(self):
        importlib.reload(openvino_intent_demo)
        assert hasattr(openvino_intent_demo, "ov")

    def test_classify_works_when_openvino_absent(self, monkeypatch):
        monkeypatch.setattr(openvino_intent_demo, "ov", None)
        result = openvino_intent_demo.classify_local_intent("insert print hello")
        assert result["available"] is False
        assert result["source"] == "local_rules"
        assert "not installed" in result["note"].lower()
        assert result["intent"] == "insert_code"

    def test_metadata_when_openvino_present(self, monkeypatch):
        monkeypatch.setattr(openvino_intent_demo, "ov", object())
        result = openvino_intent_demo.classify_local_intent("insert print hello")
        assert result["available"] is True
        assert result["source"] == "openvino_ready"
        assert "detected" in result["note"].lower()
        assert result["intent"] == "insert_code"



class TestClassification:

    @pytest.mark.parametrize("text,expected", [
        ("insert print hello", "insert_code"),
        ("write a program for even numbers", "generate_code"),
        ("stop speaking", "stop_speaking"),
        ("run code", "run_code"),
        ("what does range three mean", "concept_question"),
    ])
    def test_known_commands(self, text, expected):
        assert classify_local_intent(text)["intent"] == expected

    @pytest.mark.parametrize("text", [
        "asdf qwerty zxcv",
        "blorp flibber wuzzle",
        "",
        "   ",
    ])
    def test_unknown_nonsense(self, text):
        assert classify_local_intent(text)["intent"] == "unknown"

    def test_result_shape_and_types(self):
        result = classify_local_intent("insert print hello")
        assert set(result.keys()) == RESULT_KEYS
        assert isinstance(result["available"], bool)
        assert isinstance(result["source"], str)
        assert isinstance(result["intent"], str)
        assert isinstance(result["confidence"], float)
        assert isinstance(result["note"], str)
        assert result["intent"] in openvino_intent_demo.INTENT_LABELS

    def test_confidence_in_unit_range(self):
        for text in ["insert print hello", "run code", "stop speaking",
                     "what is a loop", "nonsense blah", ""]:
            conf = classify_local_intent(text)["confidence"]
            assert 0.0 <= conf <= 1.0

    def test_unknown_confidence_is_low(self):
        assert classify_local_intent("zzz qqq")["confidence"] < 0.5



class TestRoute:

    def test_returns_json_classification(self, client):
        resp = client.post("/openvino-intent-demo", json={"text": "insert print hello"})
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert set(data.keys()) == RESULT_KEYS
        assert data["intent"] == "insert_code"

    @pytest.mark.parametrize("text,expected", [
        ("write a program for even numbers", "generate_code"),
        ("stop speaking", "stop_speaking"),
        ("run code", "run_code"),
        ("what does range three mean", "concept_question"),
    ])
    def test_route_matches_classifier(self, client, text, expected):
        data = client.post("/openvino-intent-demo", json={"text": text}).get_json()
        assert data["intent"] == expected

    def test_empty_body_is_unknown_not_error(self, client):
        resp = client.post("/openvino-intent-demo", json={})
        assert resp.status_code == 200
        assert resp.get_json()["intent"] == "unknown"

    def test_non_string_text_does_not_crash(self, client):
        resp = client.post("/openvino-intent-demo", json={"text": 12345})
        assert resp.status_code == 200
        assert resp.is_json



class TestIsolation:

    def test_route_does_not_call_key2(self, client, monkeypatch):
        calls = []

        def _boom(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("Key 2 orchestrator must not be called by the demo route")

        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", _boom)
        resp = client.post("/openvino-intent-demo", json={"text": "run code"})
        assert resp.status_code == 200
        assert calls == []

    def test_route_does_not_call_key1_gemini(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(app_module, "call_gemini",
                            lambda *a, **k: calls.append((a, k)) or "")
        resp = client.post("/openvino-intent-demo", json={"text": "what is a loop"})
        assert resp.status_code == 200
        assert calls == []

    def test_route_response_has_no_editor_or_action_payload(self, client):
        data = client.post("/openvino-intent-demo", json={"text": "insert print hello"}).get_json()
        assert set(data.keys()) == RESULT_KEYS
        for leaked in ("code", "newCode", "action", "output", "ran", "reply", "speak"):
            assert leaked not in data

    def test_route_does_not_create_session_trace_state(self, client):
        before = dict(app_module._session_traces)
        client.post("/openvino-intent-demo", json={"text": "run code"})
        client.post("/openvino-intent-demo", json={"text": "stop speaking"})
        assert app_module._session_traces == before

    def test_route_writes_no_files(self, client, tmp_path, monkeypatch):
        data_dir = app_module.DATA_DIR
        import os
        before = set(os.listdir(data_dir)) if os.path.isdir(data_dir) else set()
        client.post("/openvino-intent-demo", json={"text": "insert print hello"})
        after = set(os.listdir(data_dir)) if os.path.isdir(data_dir) else set()
        assert after == before

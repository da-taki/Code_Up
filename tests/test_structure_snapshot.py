"""
Audio structure snapshot (Sprint 2, Feature 1).

Deterministic AST overview of a program. Covers structure_tools.build_structure_
snapshot plus the /voice-command routing ("summarize structure", "what is in
this program"). No cloud AI is involved.
"""
import pytest

import app as app_module
import structure_tools


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


GREET = 'def greet(name):\n    print("Hello", name)\n\nfor i in range(3):\n    greet(i)\n'


class TestSnapshotBuilder:

    def test_empty_code(self):
        snap = structure_tools.build_structure_snapshot("")
        assert snap["has_code"] is False
        assert snap["summary"] == "There is no code to summarize yet."

    def test_function_loop_print(self):
        snap = structure_tools.build_structure_snapshot(GREET)
        s = snap["summary"].lower()
        assert "function" in s and "greet" in s
        assert "loop" in s
        assert "print" in s
        assert snap["counts"]["functions"] == 1
        assert snap["counts"]["loops"] == 1
        assert snap["counts"]["prints"] == 1

    def test_conditions_and_nesting(self):
        code = "for i in range(5):\n    if i % 2 == 0:\n        print(i)\n"
        snap = structure_tools.build_structure_snapshot(code)
        assert snap["counts"]["conditions"] == 1
        assert snap["counts"]["loops"] == 1
        assert snap["nesting_depth"] >= 2
        assert "conditions" in snap["concepts"]

    def test_imports_and_input(self):
        code = "import math\nname = input('Your name: ')\nprint(math.pi)\n"
        snap = structure_tools.build_structure_snapshot(code)
        assert snap["counts"]["imports"] == 1
        assert snap["counts"]["inputs"] == 1
        assert "imports" in snap["concepts"]

    def test_blocks_have_line_ranges(self):
        snap = structure_tools.build_structure_snapshot(GREET)
        types = {(b["type"], b["line"]) for b in snap["blocks"]}
        assert ("function", 1) in types
        assert ("for loop", 4) in types

    def test_syntax_error_is_honest(self):
        snap = structure_tools.build_structure_snapshot("def f(:\n  pass")
        assert snap["has_code"] is True
        assert "syntax error" in snap["summary"].lower()


class TestSnapshotRoute:

    def test_voice_summarize_structure(self, client):
        d = client.post("/voice-command", json={"text": "summarize structure", "code": GREET}).get_json()
        assert d["action"] == "deterministic_message"
        assert "function" in d["message"].lower()
        assert "structure" in d

    def test_voice_what_is_in_this_program(self, client):
        d = client.post("/voice-command", json={"text": "what is in this program", "code": GREET}).get_json()
        assert d["action"] == "deterministic_message"
        # Must be the structure summary, never a generic concept answer.
        assert d.get("concept") is None

    def test_empty_route(self, client):
        d = client.post("/voice-command", json={"text": "summarize structure", "code": ""}).get_json()
        assert "no code" in d["message"].lower()

    def test_does_not_call_cloud_ai(self, client, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("structure snapshot must not call cloud AI")
        monkeypatch.setattr(app_module, "call_gemini", boom)
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", boom)
        d = client.post("/voice-command", json={"text": "give me a structure snapshot", "code": GREET}).get_json()
        assert d["action"] == "deterministic_message"


# =====================================================================
# Sprint-2 regression guards (existing demo flows unchanged)
# =====================================================================

class TestRegression:

    def test_insert_print_hello(self, client):
        d = client.post("/voice-command", json={"text": "insert print hello"}).get_json()
        assert d["action"] == "conversational_edit"

    def test_generation_still_works(self, client):
        d = client.post("/voice-command", json={"text": "write a program for first five even numbers"}).get_json()
        assert d["action"] == "generate_code"

    def test_onboarding_still_works(self, client):
        d = client.post("/voice-command", json={"text": "what can I do here"}).get_json()
        assert d["action"] == "deterministic_message" and d.get("onboarding") is True

    def test_stop_speaking_still_works(self, client):
        d = client.post("/voice-command", json={"text": "stop speaking"}).get_json()
        assert d["action"] == "stop_speaking"

    def test_openvino_route_unaffected(self, client):
        d = client.post("/openvino-intent-demo", json={"text": "insert print hello"}).get_json()
        assert d["intent"] == "insert_code"

    def test_existing_mistake_replay_unchanged(self, client):
        d = client.post("/voice-command", json={"text": "replay my mistake"}).get_json()
        assert d["action"] == "replay_mistake"

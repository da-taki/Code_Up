"""
"What did I learn today?" session recap.

Covers the deterministic recap builder (learning_recap.py) over session_memory,
plus the /learning-recap route and voice routing. It never invents activity: an
empty session says there is not enough history; otherwise it summarises real
recorded actions and gives one next step. No cloud AI is called.
"""
import pytest

import app as app_module
import learning_recap
import session_memory as sm


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# =====================================================================
# Builder
# =====================================================================

class TestRecapBuilder:

    def test_empty_session(self):
        r = learning_recap.build_recap(sm.new_memory())
        assert r["has_history"] is False
        assert "not have much" in r["recap"].lower() or "not enough" in r["recap"].lower()

    def test_generation_run_explain(self):
        mem = sm.new_memory()
        sm.record_generation(mem, "the first five even numbers")
        sm.record_run(mem, output="0\n2\n4", ran_ok=True)
        sm.record_activity(mem, "walk_through")
        r = learning_recap.build_recap(mem)
        assert r["has_history"] is True
        low = r["recap"].lower()
        assert "generated code" in low
        assert "ran it" in low
        assert "explanations" in low
        assert r["next_step"]

    def test_error_and_fix_mentions_debugging(self):
        mem = sm.new_memory()
        sm.record_run(mem, error="IndentationError", ran_ok=False)
        sm.record_activity(mem, "fix")
        r = learning_recap.build_recap(mem)
        assert "debugged errors" in r["recap"].lower() or "error" in r["recap"].lower()

    def test_structure_tools_mentioned(self):
        mem = sm.new_memory()
        sm.record_activity(mem, "code_map")
        sm.record_activity(mem, "sonify_block")
        r = learning_recap.build_recap(mem)
        low = r["recap"].lower()
        assert "code mapping" in low
        assert "sonification" in low

    def test_gives_one_next_step(self):
        mem = sm.new_memory()
        sm.record_generation(mem, "a loop")
        r = learning_recap.build_recap(mem)
        assert r["next_step"].lower().startswith("a good next step")
        assert r["recap"].count("A good next step") == 1


# =====================================================================
# Route + voice routing
# =====================================================================

class TestRecapRoute:

    def test_route_empty(self, client):
        d = client.post("/learning-recap", json={}).get_json()
        assert d["success"] is True
        assert d["has_history"] is False

    def test_route_after_activity(self, client):
        # Generate then ask for the recap — both via the real voice route so the
        # session memory is populated the same way the app populates it.
        client.post("/voice-command", json={"text": "write a program for the first five even numbers"})
        d = client.post("/voice-command", json={"text": "what did i learn today"}).get_json()
        assert d["action"] == "deterministic_message"
        assert d.get("recap") is True
        assert "generated code" in d["message"].lower()

    def test_voice_routes_recap_not_concept(self, client):
        d = client.post("/voice-command", json={"text": "what did i learn today"}).get_json()
        # Must be the recap path, never a concept answer or generation.
        assert d["action"] == "deterministic_message"
        assert d["action"] not in ("generate_code", "mentor_chat")

    def test_recap_does_not_call_cloud_ai(self, client, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("recap must not call cloud AI")
        monkeypatch.setattr(app_module, "call_gemini", boom)
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", boom)
        client.post("/voice-command", json={"text": "run"})
        d = client.post("/voice-command", json={"text": "recap my session"}).get_json()
        assert d["action"] == "deterministic_message"

import pytest

import app as app_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def _is_clarify(d):
    return d.get("needs_clarification") is True and d.get("action") in ("clarify", "deterministic_message")



class TestVagueAsksClarification:

    def test_generate_code_asks_what_to_do(self, client):
        d = _vc(client, "generate code")
        assert _is_clarify(d)
        assert "what should the program do" in d["message"].lower()

    @pytest.mark.parametrize("text", ["make a program", "write something", "build an app"])
    def test_generic_vague_prompts_clarify(self, client, text):
        assert _is_clarify(_vc(client, text))

    def test_make_a_marks_thing_asks_marks_question(self, client):
        d = _vc(client, "make a marks thing")
        assert _is_clarify(d)
        msg = d["message"].lower()
        assert "average" in msg and ("grade" in msg or "pass" in msg)

    def test_create_a_project_asks_project_question(self, client):
        d = _vc(client, "create a project")
        assert _is_clarify(d)
        assert "project" in d["message"].lower()



class TestAnswerCompletesGeneration:

    def test_answer_completes_generation(self, client):
        assert _is_clarify(_vc(client, "generate code"))
        d = _vc(client, "print the first five even numbers")
        assert d["action"] == "generate_code"
        assert d.get("source") == "clarified_generation"
        assert "even" in d["prompt"].lower()

    def test_marks_answer_completes_generation(self, client):
        assert _is_clarify(_vc(client, "make a marks thing"))
        d = _vc(client, "calculate the average")
        assert d["action"] == "generate_code"
        assert "average" in d["prompt"].lower()
        assert "marks" in d["prompt"].lower()

    def test_pivot_command_during_pending_is_not_swallowed(self, client):
        assert _is_clarify(_vc(client, "generate code"))
        d = _vc(client, "run")
        assert d["action"] == "run"



class TestGoodPromptsGenerate:

    @pytest.mark.parametrize("text", [
        "write a program for the first five even numbers",
        "make me a python program that prints hello",
        "generate code to store marks for three students in a dictionary, "
        "calculate average, and print above average",
    ])
    def test_detailed_prompts_generate(self, client, text):
        assert _vc(client, text)["action"] == "generate_code"

    @pytest.mark.parametrize("text", [
        "make a student marks analysis project using pandas",
        "create a quiz game split into multiple files",
    ])
    def test_project_prompts_generate(self, client, text):
        assert _vc(client, text)["action"] == "generate_code"



class TestKey2StillUsedWhenAvailable:

    def test_rough_prompt_uses_orchestrator_when_key2_present(self, client, monkeypatch):
        plan = (
            '{"confidence":0.9,"normalized_text":"marks",'
            '"needs_clarification":false,"clarification_question":"",'
            '"actions":[{"type":"generate_code","source":"ai_orchestrated",'
            '"prompt":"store three students marks in a dictionary and print the average"}],'
            '"spoken_summary":"Generate a marks program."}'
        )
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", lambda s, u: plan)
        d = _vc(client, "make a marks thing")
        assert d["action"] == "generate_code"
        assert d.get("source") == "ai_orchestrated"

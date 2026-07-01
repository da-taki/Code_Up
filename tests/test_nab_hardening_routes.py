import pytest

import app as app_module
from codeup.runtime import session_memory


LOOP = "for i in range(3):\n    print(i)\n"
MARKS = (
    "marks = [70, 80, 90]\n"
    "total = sum(marks)\n"
    "print(total)\n"
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


class TestSparseRepair:
    @pytest.mark.parametrize("text,expected", [
        ("fix", "fix"),
        ("debug", "deterministic_message"),
        ("explain", "walk_through"),
        ("analyze", "analyze"),
        ("report", "project_report"),
        ("export", "export_project"),
        ("notes", "deterministic_message"),
        ("requirements", "deterministic_message"),
        ("requriements", "deterministic_message"),
        ("makeup project report", "project_report"),
        ("teach me this scored", "analyze"),
        ("what line control the loop", "navigate_code"),
    ])
    def test_sparse_or_noisy_commands_route_to_safe_actions(self, client, text, expected):
        data = _vc(client, text, code=LOOP)
        assert data["action"] == expected
        assert data["action"] not in {"unknown", "choose_suggestion", "append_line"}
        if data["action"] in {"deterministic_message", "project_report", "export_project", "navigate_code"}:
            assert data.get("speech") or data.get("message")

    def test_loop_empty_editor_inserts_safe_beginner_loop(self, client):
        data = _vc(client, "loop", code="")
        assert data["action"] == "conversational_edit"
        assert data["ai_action"]["code"] == "for i in range(3):\n    print(i)"
        assert "0, 1, and 2" in data["ai_action"]["spoken_confirmation"]

    @pytest.mark.parametrize("text", [
        "make loop friends zero to two",
        "of for loop the Trends the first 3 whole numbers",
    ])
    def test_noisy_loop_asr_inserts_real_loop_not_raw_text(self, client, text):
        data = _vc(client, text, code="")
        assert data["action"] == "conversational_edit"
        assert data["ai_action"]["code"] == "for i in range(3):\n    print(i)"
        assert text.lower() not in data["ai_action"]["code"].lower()

    def test_print_sparse_command_explains_existing_print(self, client):
        data = _vc(client, "print", code=LOOP)
        assert data["action"] == "deterministic_message"
        assert "print statement" in data["speech"].lower()


class TestEditMemoryFollowups:
    def test_add_scoring_modifies_same_program(self, client):
        client.post("/generate-code", json={"prompt": "make a quiz game"})
        data = _vc(client, "add scoring", code='print("quiz")\n')
        assert data["action"] == "generate_code"
        assert data["source"] == "memory_followup"
        assert "quiz game" in data["prompt"]
        assert "scoring" in data["prompt"]
        assert data["prompt"].count("Earlier you generated") == 1

    def test_add_comments_modifies_same_program(self, client):
        client.post("/generate-code", json={"prompt": "make a quiz game"})
        data = _vc(client, "add comments", code='print("quiz")\n')
        assert data["action"] == "generate_code"
        assert "quiz game" in data["prompt"]
        assert "comments" in data["prompt"]

    def test_add_average_modifies_marks_program(self, client):
        client.post("/generate-code", json={"prompt": "make a student marks analysis program"})
        data = _vc(client, "add average", code=MARKS)
        assert data["action"] == "generate_code"
        assert "student marks analysis" in data["prompt"]
        assert "average" in data["prompt"]

    def test_change_to_while_loop_modifies_loop_program(self, client):
        client.post("/generate-code", json={"prompt": "make a loop program"})
        data = _vc(client, "arey make it with while loop", code=LOOP)
        assert data["action"] == "generate_code"
        assert "loop program" in data["prompt"]
        assert "while loop" in data["prompt"]

    def test_replace_this_without_context_asks_clarification(self, client):
        data = _vc(client, "replace this", code="")
        assert data["action"] == "clarify"
        assert data.get("needs_clarification") is True
        assert "what should i change" in data["speech"].lower()

    def test_memory_is_session_scoped(self, monkeypatch):
        monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
        monkeypatch.setenv("GEMINI_ENABLED", "0")
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as first:
            first.post("/generate-code", json={"prompt": "make a quiz game"})
            assert _vc(first, "add scoring", code='print("quiz")\n')["action"] == "generate_code"
        with app_module.app.test_client() as second:
            data = _vc(second, "add scoring", code="")
            assert data["action"] == "deterministic_message"
            assert data.get("needs_clarification") is True

    def test_clear_editor_clears_edit_memory(self, client):
        client.post("/generate-code", json={"prompt": "make a quiz game"})
        assert _vc(client, "clear editor", code='print("quiz")\n')["action"] == "clear_editor"
        data = _vc(client, "add scoring", code="")
        assert data["action"] == "deterministic_message"
        assert data.get("needs_clarification") is True


def test_session_memory_records_bounded_generated_code_and_project_manifest():
    mem = session_memory.new_memory()
    session_memory.record_generation(mem, "make a quiz", "print('x')\n" * 1000)
    assert len(mem["last_generated_code"]) <= 5000
    session_memory.record_project_manifest(mem, {
        "name": "Quiz",
        "entry": "main.py",
        "active_file": "main.py",
        "requirements": ["pandas"],
        "files": ["main.py", "helpers.py"],
    })
    assert mem["last_project_manifest"]["entry"] == "main.py"
    assert mem["project_files"] == ["main.py", "helpers.py"]

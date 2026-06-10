"""Tests for per-session working memory and contextual follow-up commands.

Unit tests exercise session_memory directly; route tests drive the real
/voice-command, /run, /generate-code flow (one test client = one session, so
cookies carry the memory across requests). AI is disabled, so these assert the
deterministic memory path that runs whenever Key 2 is missing/busy.
"""
import pytest

import app as app_module
import session_memory as sm


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


NAME_AGE = 'name = input("Enter name: ")\nage = int(input("Enter age: "))\nprint(name, age)\n'
BROKEN = "for i in range(3):\nprint(i)\n"


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


# ---------------------------------------------------------------------------
# Unit: classification
# ---------------------------------------------------------------------------
class TestClassify:
    @pytest.mark.parametrize("text,expected", [
        ("explain it again", "explain_again"),
        ("explain that again", "explain_again"),
        ("what just happened", "what_happened"),
        ("what did that do", "what_happened"),
        ("why did it fail", "why_failed"),
        ("what went wrong", "why_failed"),
        ("fix that", "fix_error"),
        ("fix the error", "fix_error"),
        ("run it again", "run_again"),
        ("use the same inputs", "run_same_inputs"),
        ("run with the same values", "run_same_inputs"),
        ("do the same with 10", "modify"),
        ("make it use a while loop", "modify"),
        ("add comments", "modify"),
        ("change the name to Aman", "modify"),
        ("open that file again", "open_file_again"),
        ("run that file again", "run_file_again"),
        ("explain this project again", "explain_project"),
        ("summarize what I did", "summarize_session"),
    ])
    def test_recognises_followups(self, text, expected):
        assert sm.classify_followup(text) == expected

    @pytest.mark.parametrize("text", [
        "run", "run main", "open main", "clear editor", "fix",
        "insert print hello world", "make a 5 by 5 star pattern",
        "ask mentor why did this fail", "generate code to print hello",
        "walk me through this program", "",
    ])
    def test_ignores_non_followups(self, text):
        assert sm.classify_followup(text) is None


# ---------------------------------------------------------------------------
# Unit: resolution + bounded memory
# ---------------------------------------------------------------------------
class TestResolve:
    def test_why_failed_without_error_clarifies(self):
        d = sm.resolve_followup("why_failed", "why did it fail", sm.new_memory())
        assert d["handled"] is False
        assert "no recent error" in d["clarification"].lower()

    def test_why_failed_uses_remembered_error(self):
        mem = sm.new_memory()
        sm.record_run(mem, error="ZeroDivisionError: division by zero", ran_ok=False)
        d = sm.resolve_followup("why_failed", "why did it fail", mem)
        assert d["handled"] is True
        assert d["resolved_action"] == "explain_error"
        assert "ZeroDivision" in d["params"]["error"]

    def test_fix_error_without_error_clarifies(self):
        d = sm.resolve_followup("fix_error", "fix that", sm.new_memory())
        assert d["handled"] is False
        assert d["referent"] == "no_error"

    def test_modify_grounds_in_last_prompt(self):
        mem = sm.new_memory()
        sm.record_generation(mem, "print the first five even numbers")
        d = sm.resolve_followup("modify", "do the same with 10", mem, code="x = 1")
        assert d["handled"] is True
        assert "first five even numbers" in d["params"]["prompt"]
        assert "10" in d["params"]["prompt"]

    def test_modify_without_any_context_clarifies(self):
        d = sm.resolve_followup("modify", "make it shorter", sm.new_memory(), code="")
        assert d["handled"] is False

    def test_run_same_inputs_reuses_values(self):
        mem = sm.new_memory()
        sm.record_input_values(mem, ["Taknoor", "16"])
        d = sm.resolve_followup("run_same_inputs", "use the same inputs", mem)
        assert d["handled"] is True
        assert d["params"]["inputs"] == ["Taknoor", "16"]

    def test_run_same_inputs_without_values_clarifies(self):
        assert sm.resolve_followup("run_same_inputs", "use the same inputs", sm.new_memory())["handled"] is False

    def test_open_file_again_needs_a_remembered_file(self):
        mem = sm.new_memory()
        assert sm.resolve_followup("open_file_again", "open that file again", mem)["handled"] is False
        sm.record_file_open(mem, "main")
        d = sm.resolve_followup("open_file_again", "open that file again", mem)
        assert d["handled"] is True and d["params"]["path"] == "main"

    def test_explain_again_without_context_clarifies(self):
        assert sm.resolve_followup("explain_again", "explain it again", sm.new_memory(), code="")["handled"] is False

    def test_memory_is_bounded(self):
        mem = sm.new_memory()
        sm.record_run(mem, output="x" * 5000, error="e" * 5000, ran_ok=False)
        assert len(mem["last_run_output"]) <= 800
        assert len(mem["last_run_error"]) <= 600
        sm.record_generation(mem, "p" * 5000)
        assert len(mem["last_gen_prompt"]) <= 600
        sm.record_project_files(mem, [f"file{i}.py" for i in range(200)])
        assert len(mem["project_files"]) <= 50

    def test_generation_stores_summary_not_full_body(self):
        mem = sm.new_memory()
        big = "\n".join(f"line_{i} = {i}" for i in range(500))
        sm.record_generation(mem, "make something", code=big)
        assert big not in mem["last_gen_summary"]
        assert "lines" in mem["last_gen_summary"]


# ---------------------------------------------------------------------------
# Route integration — the required follow-up behaviors
# ---------------------------------------------------------------------------
class TestRouteFollowups:
    def test_explain_it_again_after_generation_explains_code(self, client):
        client.post("/generate-code", json={"prompt": "print the first five even numbers"})
        d = _vc(client, "explain it again", code="for i in range(5):\n    print(i * 2)")
        assert d["action"] == "walk_through"
        assert d.get("memory") is True

    def test_why_did_it_fail_uses_actual_recent_error(self, client):
        client.post("/run", json={"code": BROKEN})
        assert _vc(client, "why did it fail")["action"] == "explain_simply"

    def test_fix_that_after_error_routes_to_fix(self, client):
        client.post("/run", json={"code": BROKEN})
        assert _vc(client, "fix that")["action"] == "fix"

    def test_fix_that_without_recent_error_clarifies(self, client):
        d = _vc(client, "fix that")
        assert d["action"] == "deterministic_message"
        assert "no recent error" in d["message"].lower()

    def test_use_the_same_inputs_reuses_concierge_values(self, client):
        _vc(client, "run with name Taknoor and age 16", code=NAME_AGE)
        d = _vc(client, "use the same inputs", code=NAME_AGE)
        assert d["action"] == "action_sequence"
        values = [a.get("values") for a in d["actions"] if a["action"] == "set_inputs"][0]
        assert values == ["Taknoor", "16"]

    def test_run_it_again_reuses_saved_inputs(self, client):
        _vc(client, "run with name Taknoor and age 16", code=NAME_AGE)
        d = _vc(client, "run it again", code=NAME_AGE)
        assert d["action"] == "action_sequence"
        assert [a for a in d["actions"] if a["action"] == "set_inputs"]

    def test_open_that_file_again_resolves_last_opened(self, client):
        assert _vc(client, "open main")["action"] == "open_project_file"
        d = _vc(client, "open that file again")
        assert d["action"] == "open_project_file"
        assert d["path"] == "main"

    def test_do_the_same_with_uses_previous_generation(self, client):
        client.post("/generate-code", json={"prompt": "print the first five even numbers"})
        d = _vc(client, "do the same with 10", code="for i in range(5):\n    print(i * 2)")
        assert d["action"] == "generate_code"
        assert "first five even numbers" in d["prompt"]
        assert "10" in d["prompt"]

    def test_low_confidence_referent_asks_one_clarification(self, client):
        d = _vc(client, "explain it again")  # fresh session, no code, no memory
        assert d["action"] == "deterministic_message"
        assert d.get("needs_clarification") is True

    def test_exact_symbol_not_hijacked_by_memory(self, client):
        d = _vc(client, "make a 5 by 5 star pattern", source="voice")
        assert d["action"] == "generate_code"
        assert d["source"] == "deterministic_exact"

    def test_normal_commands_not_swallowed(self, client):
        assert _vc(client, "run", code="print(1)")["action"] == "run"
        assert _vc(client, "clear editor", code="x = 1")["action"] == "clear_editor"
        assert _vc(client, "open main")["action"] == "open_project_file"
        # Spoken print inserts now build valid Python via conversational_edit.
        assert _vc(client, "insert print hello world")["action"] == "conversational_edit"

    def test_tutorial_coach_still_works(self, client):
        # Prompt 3 behavior preserved: the coach route is unaffected by memory.
        d = client.post("/tutorial/coach", json={"module": "print", "text": "why do we use quotes"}).get_json()
        assert d["handled"] is True
        assert "text" in d["text"].lower()

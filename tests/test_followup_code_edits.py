import pytest

import app as app_module
from codeup.runtime import session_memory


EVEN = "for i in range(5):\n    if i % 2 == 0:\n        print(i)\n"


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


def _generate_first(client):
    d = _vc(client, "write a program for the first five even numbers")
    assert d["action"] == "generate_code"
    return d


def _generate_age_plus_one_code(client):
    data = _vc(client, "make a program that asks for age and prints age plus one")
    assert data["action"] == "conversational_edit"
    return data["ai_action"]["code"]


def _is_clarify(d):
    return d.get("needs_clarification") is True



class TestDetectCorrection:

    @pytest.mark.parametrize("text,expected_in", [
        ("oh I meant make it first ten even numbers", "make it first ten even numbers"),
        ("actually use a while loop instead", "while loop"),
        ("oh I meant print odd numbers instead", "odd numbers"),
        ("actually make it a function", "function"),
    ])
    def test_detects_corrections(self, text, expected_in):
        out = session_memory.detect_correction(text)
        assert out is not None and expected_in in out.lower()

    @pytest.mark.parametrize("text", ["actually run it", "run the code", "stop speaking"])
    def test_plain_commands_are_not_corrections(self, text):
        assert session_memory.detect_correction(text) is None



class TestFollowupEdits:
    def test_generate_beginner_input_program_lands_in_editor_and_memory(self, client):
        d = _vc(client, "make a program that asks for age and prints age plus one")
        assert d["action"] == "conversational_edit"
        assert d["ai_action"]["action"] == "replace_code"
        assert 'age = int(input("Enter age: "))' in d["ai_action"]["code"]
        mem = session_memory.get_memory(app_module.get_trace_storage())
        assert mem["last_generated_code_hash"]
        assert mem["last_code_generation_command"]

    def test_followup_adds_name_as_safe_apply_proposal(self, client):
        generated = _generate_age_plus_one_code(client)
        d = _vc(client, "now make it ask for name too", code=generated)
        assert d["action"] == "deterministic_message"
        assert d["source"] == "memory_followup"
        assert d["safe_apply_reject"] is True
        applied = _vc(client, "apply", code=generated)
        assert applied["action"] == "conversational_edit"
        assert 'name = input("Enter name: ")' in applied["ai_action"]["code"]

    def test_pronoun_followup_changes_generated_program_to_function(self, client):
        generated = _generate_age_plus_one_code(client)
        d = _vc(client, "change it to use a function", code=generated)
        assert d["action"] == "deterministic_message"
        assert d["proposed_edit"] is True
        applied = _vc(client, "apply", code=generated)
        assert "def next_age(age):" in applied["ai_action"]["code"]

    def test_chained_safe_edit_proposals_compose_before_apply(self, client):
        generated = _generate_age_plus_one_code(client)
        name_proposal = _vc(client, "now make it ask for name too", code=generated)
        assert name_proposal["safe_apply_reject"] is True
        function_proposal = _vc(client, "change it to use a function", code=generated)
        assert function_proposal["source"] == "memory_followup"
        applied = _vc(client, "apply", code=generated)
        code = applied["ai_action"]["code"]
        assert 'name = input("Enter name: ")' in code
        assert 'age = int(input("Enter age: "))' in code
        assert "def next_age(age):" in code
        assert 'print(name, "will be", result)' in code

    def test_chained_safe_edit_reject_discards_latest_composed_proposal(self, client):
        generated = _generate_age_plus_one_code(client)
        _vc(client, "now make it ask for name too", code=generated)
        _vc(client, "change it to use a function", code=generated)
        rejected = _vc(client, "reject", code=generated)
        assert rejected["action"] == "deterministic_message"
        assert "rejected" in rejected["speech"].lower()
        apply_after_reject = _vc(client, "apply", code=generated)
        assert apply_after_reject["action"] != "conversational_edit"

    def test_apply_after_one_safe_edit_still_works(self, client):
        generated = _generate_age_plus_one_code(client)
        _vc(client, "now make it ask for name too", code=generated)
        applied = _vc(client, "apply", code=generated)
        code = applied["ai_action"]["code"]
        assert 'name = input("Enter name: ")' in code
        assert "def next_age" not in code

    def test_apply_after_two_chained_safe_edits_still_works(self, client):
        generated = _generate_age_plus_one_code(client)
        _vc(client, "now make it ask for name too", code=generated)
        _vc(client, "change it to use a function", code=generated)
        applied = _vc(client, "apply", code=generated)
        code = applied["ai_action"]["code"]
        assert 'name = input("Enter name: ")' in code
        assert "def next_age(age):" in code

    def test_editor_code_hash_updates_when_editor_code_changes(self, client):
        _vc(client, "where am i", code="print('one')\n", cursor_line=1)
        mem = session_memory.get_memory(app_module.get_trace_storage())
        first_hash = mem["current_editor_code_hash"]
        _vc(client, "where am i", code="print('two')\n", cursor_line=1)
        assert mem["current_editor_code_hash"] != first_hash

    def test_obvious_generation_and_edit_do_not_clarify(self, client):
        generated = _generate_age_plus_one_code(client)
        assert "input(" in generated
        d = _vc(client, "make it print the result at the end", code=generated)
        assert d.get("needs_clarification") is not True

    def test_oh_i_meant_edits_existing_code(self, client):
        _generate_first(client)
        d = _vc(client, "oh I meant make it first ten even numbers", code=EVEN)
        assert d["action"] == "generate_code"
        assert d.get("source") == "memory_followup"
        assert d.get("followup_edit") is True
        assert "ten" in d["prompt"].lower()

    def test_actually_use_odd_instead_edits_existing_code(self, client):
        _generate_first(client)
        d = _vc(client, "actually use odd numbers instead", code=EVEN)
        assert d["action"] == "generate_code"
        assert d.get("source") == "memory_followup"
        assert "odd" in d["prompt"].lower()

    def test_change_variable_name_edits_current_code(self, client):
        _generate_first(client)
        d = _vc(client, "change the variable name to total", code=EVEN)
        assert d["action"] == "generate_code"
        assert d.get("source") == "memory_followup"
        assert "total" in d["prompt"].lower()

    def test_correction_grounds_in_prior_prompt(self, client):
        _generate_first(client)
        d = _vc(client, "oh I meant make it first ten even numbers", code=EVEN)
        assert "even" in d["prompt"].lower()



class TestFollowupClarifies:

    def test_ambiguous_correction_asks_clarification(self, client):
        _generate_first(client)
        d = _vc(client, "actually, change that", code=EVEN)
        assert _is_clarify(d)
        assert "change" in d["message"].lower()

    def test_no_context_asks_clarification(self, client):
        d = _vc(client, "oh I meant first ten even numbers", code="")
        assert _is_clarify(d)



class TestGenerationValidatesParse:

    def test_empty_prompt_is_rejected(self, client):
        resp = client.post("/generate-code", json={"prompt": ""})
        assert resp.status_code == 400

    def test_generated_code_parses(self, client):
        data = client.post("/generate-code", json={"prompt": "print numbers zero to two"}).get_json()
        assert data["success"] is True and data.get("code")
        compile(data["code"], "<gen>", "exec")



class TestNotAFollowup:

    def test_new_generation_still_generates(self, client):
        d = _vc(client, "write a program for the first five even numbers")
        assert d["action"] == "generate_code"
        assert d.get("source") != "memory_followup"

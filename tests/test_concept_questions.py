import pytest

import app as app_module
from codeup.commands.intent_parser import parse_intent


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


LOOP = "for i in range(3):\n    print(i)\n"
BROKEN = "for i in range(3):\nprint(i)\n"

CONCEPT_PHRASES = [
    "What is this print function all about?",
    "Hey, what is this print function all about?",
    "What does range three mean?",
    "Why is print indented?",
    "What is a loop?",
    "Why is there a colon after the for line?",
    "What does i mean here?",
    "Can you explain line two?",
    "What happens if I remove these spaces?",
    "Show me a simple example of a print statement.",
]

_MUTATING = {
    "conversational_edit", "run", "fix", "generate_code", "save_snippet_named",
    "save_snippet_auto", "clear_editor", "insert_line", "replace_line",
    "delete_line", "append_line", "indent_line", "dedent_line",
}


def _ask(client, q, code, language="en"):
    return client.post(
        "/mentor/chat",
        json={"message": q, "code": code, "mode": "concept", "language": language},
    ).get_json()



class TestConceptRouting:

    @pytest.mark.parametrize("phrase", CONCEPT_PHRASES)
    def test_routes_to_concept_mentor(self, client, phrase):
        d = client.post("/voice-command", json={"text": phrase, "code": LOOP}).get_json()
        if d["action"] == "deterministic_message":
            assert d.get("concept")
        else:
            assert d["action"] == "mentor_chat", f"{phrase!r} -> {d['action']}"
            assert d["mode"] == "concept"

    @pytest.mark.parametrize("phrase", CONCEPT_PHRASES)
    def test_concept_never_triggers_a_mutating_action(self, client, phrase):
        d = client.post("/voice-command", json={"text": phrase, "code": LOOP}).get_json()
        assert d["action"] not in _MUTATING

    def test_hey_filler_does_not_break_routing(self, client):
        d = client.post("/voice-command", json={"text": "Hey, what is a loop?", "code": LOOP}).get_json()
        assert d["action"] == "mentor_chat" and d["mode"] == "concept"

    def test_hey_filler_stripped_from_message(self):
        parsed = parse_intent("Hey, what is a loop?")
        assert parsed["intent"] == "concept_question"
        assert parsed["slots"]["message"].lower().startswith("what is a loop")

    def test_concept_question_high_confidence(self):
        assert parse_intent("why is print indented")["confidence"] >= 0.75



class TestActionSeparation:

    def test_read_code_is_literal_narrate(self, client):
        d = client.post("/voice-command", json={"text": "read code", "code": LOOP}).get_json()
        assert d["action"] == "narrate_file"

    def test_walkthrough_separate(self, client):
        d = client.post("/voice-command", json={"text": "walk me through this program", "code": LOOP}).get_json()
        assert d["action"] == "walk_through"

    def test_step_narration_separate(self, client):
        d = client.post("/voice-command", json={"text": "run with step narration", "code": LOOP}).get_json()
        assert d["action"] == "step_narration"

    @pytest.mark.parametrize("phrase", [
        "Add a print statement inside the loop",
        "Fix the indentation issue",
    ])
    def test_edit_commands_do_not_become_concept(self, client, phrase):
        d = client.post("/voice-command", json={"text": phrase, "code": LOOP}).get_json()
        assert not (d["action"] == "mentor_chat" and d.get("mode") == "concept")

    def test_explicit_put_in_editor_is_not_concept(self, client):
        d = client.post("/voice-command", json={"text": "Put a print example in the editor", "code": LOOP}).get_json()
        assert not (d["action"] == "mentor_chat" and d.get("mode") == "concept")



class TestDeterministicAnswers:

    def test_print_is_context_aware(self, client):
        r = _ask(client, "What is this print function all about?", LOOP)["reply"].lower()
        assert "output" in r
        assert "print(i)" in r
        assert "zero, one, and two" in r

    def test_range_three(self, client):
        r = _ask(client, "What does range three mean?", LOOP)["reply"].lower()
        assert "zero, one, and two" in r
        assert "three" in r

    def test_indentation_valid_code(self, client):
        r = _ask(client, "Why is print indented?", LOOP)["reply"].lower()
        assert "inside the loop" in r
        assert "error" not in r

    def test_loop(self, client):
        r = _ask(client, "What is a loop?", LOOP)["reply"].lower()
        assert "repeat" in r

    def test_colon(self, client):
        r = _ask(client, "Why is there a colon after the for line?", LOOP)["reply"].lower()
        assert "indented block" in r

    def test_loop_variable(self, client):
        r = _ask(client, "What does i mean here?", LOOP)["reply"].lower()
        assert "loop variable" in r
        assert "zero, then one, then two" in r

    def test_specific_line(self, client):
        r = _ask(client, "Can you explain line two?", LOOP)["reply"].lower()
        assert "line two" in r
        assert "print" in r

    def test_show_example_does_not_edit(self, client):
        r = _ask(client, "Show me a simple example of a print statement.", LOOP)["reply"]
        assert 'print("Hello")' in r
        assert "have not changed your code" in r.lower()

    def test_answers_are_short_enough_for_speech(self, client):
        for phrase in CONCEPT_PHRASES:
            reply = _ask(client, phrase, LOOP)["reply"]
            assert reply.count(".") <= 6
            assert "```" not in reply



class TestNoCodeState:

    def test_print_general_explanation(self, client):
        r = _ask(client, "What does print do?", "")["reply"].lower()
        assert "output" in r
        assert 'print("hello")' in r

    def test_contextual_question_clarifies(self, client):
        r = _ask(client, "Why is this line indented?", "")["reply"].lower()
        assert "no code" in r


class TestBrokenCodeState:

    def test_indentation_explained_honestly(self, client):
        r = _ask(client, "Why is print indented?", BROKEN)["reply"].lower()
        assert "not indented" in r
        assert "indentation error" in r

    def test_what_is_wrong_with_spaces(self, client):
        r = _ask(client, "What is wrong with these spaces?", BROKEN)["reply"].lower()
        assert "indent" in r
        assert "displays zero, one, and two" not in r



class TestHinglish:

    @pytest.mark.parametrize("phrase", [
        "print function kya karta hai?",
        "range three ka matlab kya hai?",
        "loop kya hota hai?",
    ])
    def test_hinglish_routes_to_concept(self, phrase):
        assert parse_intent(phrase)["intent"] == "concept_question"

    def test_hinglish_fallback_uses_aur(self, client):
        r = _ask(client, "range three ka matlab kya hai?", LOOP, language="hi")["reply"].lower()
        assert "zero, one, aur two" in r

import pytest

import app as app_module
from codeup.learning import concept_qa

LOOP = "for i in range(3):\n    print(i)\n"

_MUTATING = {
    "run", "fix", "generate_code", "conversational_edit", "clear_editor",
    "insert_line", "replace_line", "delete_line", "append_line", "indent_line",
    "dedent_line", "insert_function", "insert_variable", "save_snippet_named",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, code=LOOP, **extra):
    payload = {"text": text, "code": code}
    payload.update(extra)
    return client.post("/voice-command", json=payload).get_json()



class TestAdvancedConceptAnswers:

    CASES = [
        ("what is recursion", "recursion", "calls itself"),
        ("explain recursion", "recursion", "base case"),
        ("what is inheritance", "inheritance", "class"),
        ("explain inheritance", "inheritance", "parent class"),
        ("what is a tuple", "tuple", "parentheses"),
        ("what is a set", "set", "unique"),
        ("what is a decorator", "decorator", "wraps"),
        ("what is big O", "big_o", "grows"),
        ("what is time complexity", "big_o", "input"),
        ("what is object oriented programming", "oop", "object"),
        ("what is a class", "class", "blueprint"),
        ("what is a method", "method", "class"),
        ("what is a module", "module", "import"),
        ("what is an import", "import", "module"),
        ("what is exception handling", "exception", "except"),
        ("what is try except", "exception", "except"),
        ("what is a parameter", "parameter", "argument"),
        ("what is a return value", "return", "return"),
    ]

    @pytest.mark.parametrize("text,kind,keyword", CASES)
    def test_concept_answered_and_grounded(self, client, text, kind, keyword):
        d = _vc(client, text)
        assert d["action"] == "deterministic_message", (text, d["action"])
        assert d.get("concept") == kind, (text, d.get("concept"))
        assert keyword.lower() in d["message"].lower(), (text, d["message"])

    @pytest.mark.parametrize("text,kind,keyword", CASES)
    def test_concept_never_runs_or_mutates(self, client, text, kind, keyword):
        assert _vc(client, text)["action"] not in _MUTATING

    @pytest.mark.parametrize("text,kind,keyword", CASES)
    def test_concept_has_meaningful_speech(self, client, text, kind, keyword):
        d = _vc(client, text)
        spoken = (d.get("speech") or d.get("message") or "").strip()
        assert len(spoken) >= 60, (text, spoken)
        assert "```" not in spoken and "|" not in spoken



class TestBeginnerConceptsStillWork:

    @pytest.mark.parametrize("text", [
        "what is a variable", "what is a loop", "what is a function",
        "what does range three mean", "what is a list", "what is a dictionary",
    ])
    def test_still_answers_without_mutating(self, client, text):
        d = _vc(client, text)
        assert d["action"] in ("deterministic_message", "mentor_chat"), (text, d["action"])
        assert d["action"] not in _MUTATING


class TestBeginnerTheoryDemoConcepts:

    @pytest.mark.parametrize("text,kind,expected", [
        ("what is a print function", "print", "print is how Python shows"),
        ("what is the print function", "print", "print is how Python shows"),
        ("what is print", "print", "print is how Python shows"),
        ("what is print function", "print", "print is how Python shows"),
        ("what is a print functions", "print", "print is how Python shows"),
        ("explain print", "print", "print is how Python shows"),
        ("teach me print", "print", "print is how Python shows"),
        ("what is an input function", "input", "input is how Python asks"),
        ("what is input", "input", "input is how Python asks"),
        ("teach me input", "input", "input is how Python asks"),
        ("what is a range function", "range", "range is how Python makes"),
        ("what is a for loop", "for_loop", "A for loop repeats"),
        ("what is a variable", "variable", "A variable is a name"),
        ("what is a function", "function", "A function is a named set"),
        ("what is an if statement", "if_statement", "An if statement lets"),
        ("what is a condition", "if_statement", "An if statement lets"),
        ("what is a list", "list", "A list stores"),
    ])
    def test_beginner_concepts_are_deterministic_short_explanations(self, client, text, kind, expected, monkeypatch):
        def fail_ai(*args, **kwargs):
            raise AssertionError("AI should not be called for beginner concept explanations")

        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail_ai)
        d = _vc(client, text, code="")
        assert d["action"] == "deterministic_message", (text, d)
        assert d.get("concept") == kind
        msg = d["message"]
        assert expected in msg
        assert "Example:\n" in msg
        assert "Beginner note:" in msg
        assert len(msg.split()) <= 85
        assert d["action"] not in _MUTATING

    def test_unknown_beginner_concept_gets_graceful_fallback(self, client):
        d = _vc(client, "what is flarbology", code="")
        assert d["action"] == "deterministic_message"
        assert d.get("concept") == concept_qa.UNKNOWN_CONCEPT
        assert "I do not have a prepared explanation" in d["message"]

    def test_existing_analyze_walkthrough_and_project_map_routes_still_win(self, client):
        assert _vc(client, "analyze", code=LOOP)["action"] == "analyze"
        assert _vc(client, "walk through code", code=LOOP)["action"] == "walk_through"
        project = _vc(client, "project map", code=LOOP)
        assert project["action"] == "deterministic_message"
        assert "Project map:" in project["speech"]

    def test_beginner_concept_wins_even_with_recent_error_context(self, client, monkeypatch):
        def fail_mapper(*args, **kwargs):
            raise AssertionError("Natural command mapper should not handle beginner concept questions")

        monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
        monkeypatch.setattr(app_module, "_call_ai_natural_command_mapper", fail_mapper)
        d = _vc(
            client,
            "what is input",
            code='marks = float(input("Enter marks: "))\nprint(marks)\n',
            error="ValueError: could not convert string to float: '80 as input'",
            source="typed",
        )
        assert d["action"] == "deterministic_message"
        assert d.get("concept") == "input"
        assert "input is how Python asks" in d["message"]



class TestNeverRuns:

    @pytest.mark.parametrize("text", [
        "what is recursion", "what is inheritance", "what is a tuple",
        "what is big O", "what is a decorator", "what is object oriented programming",
        "who are you", "what time is it",
    ])
    def test_never_returns_run(self, client, text):
        assert _vc(client, text)["action"] != "run"

    def test_unknown_concept_gives_helpful_fallback(self, client):
        d = _vc(client, "what is a flux capacitor", code="")
        assert d["action"] == "deterministic_message"
        assert "explain" in d["message"].lower()
        assert "recursion" in d["message"].lower() or "loop" in d["message"].lower()



class TestDoesNotStealAskMyCode:

    def test_print_count_is_code_specific(self, client):
        d = _vc(client, "why does this print three times")
        assert d.get("concept") is None
        assert d["action"] in ("navigate_code", "deterministic_message")

    def test_loop_control_is_code_specific(self, client):
        d = _vc(client, "what line controls the loop")
        assert d.get("concept") is None
        assert d["action"] in ("navigate_code", "deterministic_message")

    def test_symbol_location_is_navigation(self, client):
        d = _vc(client, "where is total changed", code="total = 0\ntotal = total + 1\n")
        assert d.get("concept") is None
        assert d["action"] in ("navigate_code", "deterministic_message")

    def test_function_behavior_is_code_specific(self, client):
        code = "def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n"
        d = _vc(client, "what does this function do", code=code)
        assert d.get("concept") is None
        assert d["action"] in ("navigate_code", "deterministic_message")



class TestDoesNotStealGeneration:

    @pytest.mark.parametrize("text", [
        "write a program that explains recursion",
        "generate code to demonstrate recursion",
    ])
    def test_explicit_generation_still_generates(self, client, text):
        d = _vc(client, text, code="")
        assert d["action"] == "generate_code"

    def test_lesson_request_is_not_concept_qa(self, client):
        d = _vc(client, "make a beginner lesson on recursion", code="")
        assert d.get("concept") is None
        assert d["action"] == "deterministic_message"



class TestClassifierUnit:

    @pytest.mark.parametrize("text,kind", [
        ("what is recursion", "recursion"),
        ("what is a recursive function", "recursion"),
        ("what is big-o notation", "big_o"),
        ("what is space complexity", "big_o"),
        ("what are tuples", "tuple"),
        ("what are sets", "set"),
        ("what are decorators", "decorator"),
        ("what is oop", "oop"),
        ("what is a subclass", "inheritance"),
        ("what is an argument", "parameter"),
        ("what is a return statement", "return"),
        ("explain try and except", "exception"),
    ])
    def test_aliases_resolve(self, text, kind):
        assert concept_qa.classify_concept_question(text) == kind

    @pytest.mark.parametrize("text,kind", [
        ("what is a loop", "for_loop"),
        ("what is a list", "list"),
        ("what is a function", "function"),
    ])
    def test_beginner_basics_resolve_locally(self, text, kind):
        assert concept_qa.classify_concept_question(text) == kind

    @pytest.mark.parametrize("text", ["what is a string"])
    def test_mentor_handled_concepts_defer(self, text):
        assert concept_qa.classify_concept_question(text) is None

    @pytest.mark.parametrize("text", [
        "explain this code", "what does this function do", "write a program",
        "run the code", "what is total",
    ])
    def test_does_not_classify_code_or_command_text(self, text):
        result = concept_qa.classify_concept_question(text)
        assert result in (None, concept_qa.UNKNOWN_CONCEPT) or result not in concept_qa._CONCEPTS



class TestNonCodeSafeResponses:

    IDENTITY = ["who are you", "what is your name", "what are you", "are you a robot",
                "introduce yourself"]
    SCOPED = ["what time is it", "what day is it", "what is the date", "are you working",
              "how are you", "is this working"]

    @pytest.mark.parametrize("text", IDENTITY)
    def test_identity_is_deterministic_and_scoped(self, client, text):
        d = _vc(text=text, client=client)
        assert d["action"] == "deterministic_message"
        assert "codeup" in d["message"].lower()
        assert len((d.get("speech") or d.get("message") or "")) >= 40

    @pytest.mark.parametrize("text", SCOPED)
    def test_scoped_fallback_is_deterministic(self, client, text):
        d = _vc(text=text, client=client)
        assert d["action"] == "deterministic_message"
        assert "python" in d["message"].lower()

    @pytest.mark.parametrize("text", IDENTITY + SCOPED)
    def test_non_code_never_runs_or_mutates(self, client, text):
        assert _vc(text=text, client=client)["action"] not in _MUTATING

    @pytest.mark.parametrize("text", IDENTITY + SCOPED)
    def test_non_code_never_fuzzy_confirms(self, client, text):
        d = _vc(text=text, client=client)
        assert d["action"] != "confirm"
        assert "options" not in d



class TestUnsupportedConcepts:

    @pytest.mark.parametrize("text", [
        "what is flarbology", "explain flarbology", "teach me flarbology",
        "tell me about flarbology",
    ])
    def test_unknown_concept_gives_unsupported_fallback(self, client, text):
        d = _vc(text=text, client=client)
        assert d["action"] == "deterministic_message"
        msg = d["message"].lower()
        assert "i can explain" in msg
        assert "recursion" in msg
        spoken = (d.get("speech") or d.get("message") or "")
        assert len(spoken) >= 60

    @pytest.mark.parametrize("text", [
        "what is flarbology", "explain flarbology", "teach me flarbology",
        "who are you", "what time is it",
    ])
    def test_no_fuzzy_junk_in_message(self, client, text):
        msg = _vc(text=text, client=client)["message"].lower()
        for junk in ("did you mean", "locate error", "read line enhanced"):
            assert junk not in msg, (text, junk)



class TestKnownConceptCommandForms:

    @pytest.mark.parametrize("text,kind", [
        ("explain recursion", "recursion"),
        ("teach me inheritance", "inheritance"),
        ("tell me about big O", "big_o"),
        ("how does try except work", "exception"),
        ("why use a tuple", "tuple"),
    ])
    def test_command_form_answers_known_concept(self, client, text, kind):
        d = _vc(text=text, client=client)
        assert d["action"] == "deterministic_message"
        assert d.get("concept") == kind



class TestDoesNotStealCommands:

    def test_explain_structure_is_outline(self, client):
        assert _vc(text="explain structure", client=client)["action"] == "read_outline"

    def test_explain_it_again_is_not_concept(self, client):
        d = _vc(text="explain it again", client=client)
        assert d.get("concept") is None
        assert d["action"] != "run"

    def test_explain_this_program_is_walkthrough(self, client):
        assert _vc(text="explain this program", client=client)["action"] == "walk_through"

    def test_teach_me_this_code_is_demo_analyze_alias(self, client):
        d = _vc(text="teach me this code", client=client)
        assert d["action"] == "analyze"
        assert d.get("concept_lesson") is not True
        assert d.get("concept") is None

    def test_print_count_is_ask_my_code(self, client):
        assert _vc(text="why does this print three times", client=client)["action"] == "navigate_code"

    def test_loop_control_is_navigation(self, client):
        assert _vc(text="what line controls the loop", client=client)["action"] == "navigate_code"

    @pytest.mark.parametrize("text", [
        "generate code to explain recursion",
        "write a program that explains recursion",
    ])
    def test_generation_still_generates(self, client, text):
        assert _vc(text=text, client=client, code="")["action"] == "generate_code"

    def test_lesson_request_is_not_concept_qa(self, client):
        d = _vc(text="make a beginner lesson on recursion", client=client, code="")
        assert d.get("concept") is None
        assert d["action"] == "deterministic_message"



class TestSentinelsDoNotLeak:

    def test_identity_and_unknown_not_recorded_as_concepts(self, client):
        for t in ["who are you", "what time is it", "what is flarbology",
                  "explain flarbology", "what is recursion"]:
            _vc(text=t, client=client)
        trainer = _vc(text="make trainer notes", client=client)["message"]
        recap = _vc(text="what did I learn today", client=client)["message"]
        assert "__" not in trainer, trainer
        assert "__" not in recap, recap
        assert "recursion" in (trainer + recap).lower()

    def test_snake_case_concept_kinds_are_spoken_friendly(self, client):
        for t in ["what is big O", "what is object oriented programming"]:
            _vc(text=t, client=client)
        trainer = _vc(text="make trainer notes", client=client)["message"]
        recap = _vc(text="what did I learn today", client=client)["message"]
        blob = (trainer + " " + recap).lower()
        assert "big_o" not in blob
        assert "time complexity" in blob

    def test_concept_label_maps_kinds(self):
        assert concept_qa.concept_label("big_o") == "time complexity"
        assert concept_qa.concept_label("oop") == "object-oriented programming"
        assert concept_qa.concept_label("recursion") == "recursion"
        assert concept_qa.concept_label("__identity__") == ""
        assert concept_qa.concept_label("") == ""

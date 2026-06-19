import pytest

import tutorial_engine as te


class TestModulePack:
    def test_order_is_print_first_while_last(self):
        assert te.MODULE_ORDER == ["print", "variables", "if", "for", "while"]
        assert te.first_module_id() == "print"

    def test_pack_has_all_modules_with_spoken_content(self):
        pack = te.module_pack()
        assert pack["count"] == 5
        assert pack["order"][0] == "print" and pack["order"][-1] == "while"
        for mid in te.MODULE_ORDER:
            m = pack["modules"][mid]
            for key in ("title", "concept", "example_code", "example_spoken", "task", "hints", "recap", "success"):
                assert m.get(key), f"{mid} missing {key}"
            assert isinstance(m["hints"], list) and len(m["hints"]) >= 1

    def test_next_module_chain(self):
        assert te.next_module_id("print") == "variables"
        assert te.next_module_id("variables") == "if"
        assert te.next_module_id("if") == "for"
        assert te.next_module_id("for") == "while"
        assert te.next_module_id("while") is None
        assert te.next_module_id("nope") is None

    def test_example_code_passes_its_own_validator(self):
        for mid in te.MODULE_ORDER:
            code = te.MODULES[mid]["example_code"]
            res = te.validate_attempt(mid, code, ran_ok=True)
            assert res["passed"], f"{mid} example failed validation: {res}"


class TestValidatePrint:
    @pytest.mark.parametrize("code", [
        'print("Hello world")',
        "print('hi')",
        'print("a", "b")',
        'message = "hi"\nprint(message)',
        'print(42)',
    ])
    def test_accepts(self, code):
        assert te.validate_print(code) is True

    @pytest.mark.parametrize("code", ['print()', 'x = 5', '', '# print("x")', 'pront("hi")'])
    def test_rejects(self, code):
        assert te.validate_print(code) is False


class TestValidateVariables:
    @pytest.mark.parametrize("code", [
        'name = "Aman"\nprint(name)',
        'score = 42\nprint(score)',
        'x = 7\nprint(x)',
        'city = "Patiala"\nprint(city)',
        'total = 3.14\nprint("total is", total)',
        'n = 5\nprint(f"n is {n}")',
    ])
    def test_accepts_varied_names_and_values(self, code):
        assert te.validate_variables(code) is True

    @pytest.mark.parametrize("code", [
        'x = 5',                      # assigned but never printed
        'print("hello")',            # printed but no variable
        'print(undefined_name)',
        '',
    ])
    def test_rejects(self, code):
        assert te.validate_variables(code) is False


class TestValidateIf:
    @pytest.mark.parametrize("code", [
        'x = 10\nif x > 5:\n    print("big")',
        'name = "a"\nif name == "a":\n    print("hi")\nelse:\n    print("bye")',
        'n = 3\nif n > 10:\n    print("high")\nelif n > 1:\n    print("mid")',
    ])
    def test_accepts(self, code):
        assert te.validate_if(code) is True

    @pytest.mark.parametrize("code", ['if x > 5:\n    x = 1', 'print("no if here")', 'x = 5'])
    def test_rejects(self, code):
        assert te.validate_if(code) is False


class TestValidateFor:
    @pytest.mark.parametrize("code", [
        'for i in range(3):\n    print(i)',
        'for fruit in ["a", "b"]:\n    print(fruit)',
        'for n in range(5):\n    print("hello", n)',
    ])
    def test_accepts(self, code):
        assert te.validate_for(code) is True

    @pytest.mark.parametrize("code", ['for i in range(3):\n    x = i', 'print("no loop")'])
    def test_rejects(self, code):
        assert te.validate_for(code) is False


class TestValidateWhile:
    @pytest.mark.parametrize("code", [
        'c = 1\nwhile c <= 3:\n    print(c)\n    c = c + 1',
        'n = 0\nwhile n < 5:\n    print(n)\n    n += 1',
    ])
    def test_accepts(self, code):
        assert te.validate_while(code) is True

    @pytest.mark.parametrize("code", ['c = 1\nwhile c <= 3:\n    c = c + 1', 'print("no loop")'])
    def test_rejects_without_print(self, code):
        assert te.validate_while(code) is False


class TestWhileSafety:
    def test_safe_counter_loop(self):
        safe, _ = te.check_while_safety('c = 1\nwhile c <= 3:\n    print(c)\n    c = c + 1')
        assert safe is True

    def test_safe_augassign_counter(self):
        safe, _ = te.check_while_safety('n = 0\nwhile n < 5:\n    print(n)\n    n += 1')
        assert safe is True

    def test_while_true_without_break_is_unsafe(self):
        safe, reason = te.check_while_safety('while True:\n    print("forever")')
        assert safe is False and reason

    def test_while_true_with_break_is_safe(self):
        safe, _ = te.check_while_safety('while True:\n    print("x")\n    break')
        assert safe is True

    def test_condition_var_never_changes_is_unsafe(self):
        safe, reason = te.check_while_safety('c = 1\nwhile c <= 3:\n    print(c)')
        assert safe is False and reason

    def test_for_loop_is_not_flagged(self):
        safe, _ = te.check_while_safety('for i in range(3):\n    print(i)')
        assert safe is True

    def test_syntax_error_defers_to_run(self):
        safe, _ = te.check_while_safety('while True\n    print(1)')
        assert safe is True


class TestValidateAttempt:
    def test_print_pass(self):
        res = te.validate_attempt("print", 'print("hi")', ran_ok=True)
        assert res["passed"] is True and res["safe"] is True
        assert res["feedback"]

    def test_print_runtime_failure_not_passed(self):
        res = te.validate_attempt("print", 'print("hi")', ran_ok=False)
        assert res["passed"] is False
        assert res["hint"]

    def test_print_missing_construct_gives_hint(self):
        res = te.validate_attempt("print", 'x = 5', ran_ok=True)
        assert res["passed"] is False
        assert res["hint"]

    def test_syntax_error_is_kind_and_not_passed(self):
        res = te.validate_attempt("print", 'print("hi"', ran_ok=False)
        assert res["passed"] is False
        assert res["safe"] is True
        assert res["hint"]

    @pytest.mark.parametrize("code", [
        'name = "Aman"\nprint(name)',
        'score = 99\nprint(score)',
    ])
    def test_variables_pass_with_different_names(self, code):
        assert te.validate_attempt("variables", code, ran_ok=True)["passed"] is True

    def test_if_pass(self):
        assert te.validate_attempt("if", 'x=10\nif x>5:\n    print("big")', ran_ok=True)["passed"] is True

    def test_for_pass(self):
        assert te.validate_attempt("for", 'for i in range(3):\n    print(i)', ran_ok=True)["passed"] is True

    def test_while_safe_pass(self):
        code = 'c = 1\nwhile c <= 3:\n    print(c)\n    c = c + 1'
        assert te.validate_attempt("while", code, ran_ok=True)["passed"] is True

    def test_while_unsafe_is_blocked_and_flagged(self):
        res = te.validate_attempt("while", 'while True:\n    print("x")', ran_ok=True)
        assert res["passed"] is False
        assert res["safe"] is False
        assert res["feedback"]

    def test_while_nonterminating_counter_blocked(self):
        res = te.validate_attempt("while", 'c = 1\nwhile c <= 3:\n    print(c)', ran_ok=True)
        assert res["passed"] is False
        assert res["safe"] is False

    def test_unknown_module(self):
        res = te.validate_attempt("functions", 'print("x")', ran_ok=True)
        assert res["passed"] is False


class TestCoachClassify:
    @pytest.mark.parametrize("text,expected", [
        ("explain simpler", "explain_simpler"),
        ("say that again simpler", "explain_simpler"),
        ("I don't understand", "dont_understand"),
        ("i am confused", "dont_understand"),
        ("why do we use quotes", "why_quotes"),
        ("why indentation", "why_indentation"),
        ("give me another hint", "another_hint"),
        ("encourage me", "encourage"),
        ("what am I learning", "what_learning"),
    ])
    def test_recognises_coach_phrases(self, text, expected):
        assert te.classify_coach_request(text) == expected

    @pytest.mark.parametrize("text", [
        "run code", "run", "insert print hello world", "continue", "hint",
        "recap", "repeat", "exit tutorial", "read my code", "say that again", "",
    ])
    def test_ignores_control_and_coding_commands(self, text):
        assert te.classify_coach_request(text) is None


class TestCoachResponse:
    def test_explain_simpler_is_short_and_about_print(self):
        r = te.coach_response("print", "explain_simpler")
        assert "print" in r["text"].lower()
        assert len(r["text"].split()) <= 30

    def test_dont_understand_reassures_then_explains(self):
        r = te.coach_response("print", "dont_understand")
        assert r["text"].lower().startswith("no problem")
        assert "print" in r["text"].lower()

    def test_why_quotes_explains_text_not_variable(self):
        r = te.coach_response("print", "why_quotes")
        low = r["text"].lower()
        assert "text" in low and "quote" in low

    def test_why_indentation_mentions_block(self):
        r = te.coach_response("if", "why_indentation")
        assert "indent" in r["text"].lower()

    def test_what_learning_names_topic(self):
        assert "for" in te.coach_response("for", "what_learning")["text"].lower()

    def test_another_hint_returns_a_real_hint(self):
        r = te.coach_response("print", "another_hint", attempts=0)
        assert r["text"]
        assert "hint" in r["text"].lower() or "insert" in r["text"].lower()

    def test_encourage_after_repeated_failure_is_supportive_with_hint(self):
        r = te.coach_response("print", "encourage", attempts=2)
        low = r["text"].lower()
        assert "wrong" not in low            # supportive, not blunt "wrong"
        assert "insert" in low or "try" in low or "step" in low

    def test_unknown_request_invents_nothing(self):
        r = te.coach_response("print", "delete_everything")
        assert r["text"] == ""
        assert r["kind"] == "none"

    def test_responses_stay_short(self):
        for request in ("explain_simpler", "why_quotes", "why_indentation", "what_learning", "encourage"):
            r = te.coach_response("while", request, attempts=1)
            assert len(r["text"].split()) <= 45, request

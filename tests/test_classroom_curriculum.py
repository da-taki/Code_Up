from codeup.classroom import curriculum


def test_module_order_has_ten_core_topics_plus_two_optional():
    core = ["printing", "variables", "input", "data_types", "conditions",
            "for_loops", "while_loops", "lists", "dictionaries", "functions"]
    assert curriculum.MODULE_ORDER[:10] == core
    assert curriculum.MODULE_ORDER[10:] == ["debugging", "mini_project"]


def test_every_module_has_a_complete_shape():
    for module_id in curriculum.MODULE_ORDER:
        m = curriculum.MODULES[module_id]
        for field in ("concept", "example_code", "instructions", "hints", "success", "challenge"):
            assert m.get(field), f"{module_id} missing {field}"
        assert callable(m["attempt_check"])
        assert callable(m["challenge_check"])
        assert m.get("quiz_question")
        assert len(m.get("quiz_choices") or []) >= 2
        assert m.get("quiz_answer_index") is not None


def test_public_module_hides_callables():
    m = curriculum.public_module("printing")
    assert "attempt_check" not in m
    assert "challenge_check" not in m
    assert m["title"] == "Printing and output"


def test_next_module_id_chain_reaches_the_end():
    seen = []
    current = curriculum.first_module_id()
    while current:
        seen.append(current)
        current = curriculum.next_module_id(current)
    assert seen == curriculum.MODULE_ORDER
    assert curriculum.next_module_id("mini_project") is None
    assert curriculum.next_module_id("not-a-module") is None


def test_check_attempt_rejects_syntax_errors_gracefully():
    result = curriculum.check_attempt("printing", "print(")
    assert result["passed"] is False
    assert "typo" in result["feedback"].lower()


def test_check_attempt_unknown_module():
    result = curriculum.check_attempt("not-a-module", "print(1)")
    assert result["passed"] is False


def test_while_loops_rejects_infinite_loop_even_if_print_present():
    result = curriculum.check_attempt("while_loops", "while True:\n    print(1)")
    assert result["passed"] is False
    assert "forever" in result["feedback"].lower()


def test_check_quiz_correct_and_incorrect():
    for module_id in curriculum.MODULE_ORDER:
        answer = curriculum.MODULES[module_id]["quiz_answer_index"]
        correct, _ = curriculum.check_quiz(module_id, answer)
        assert correct is True
        wrong_index = (answer + 1) % len(curriculum.MODULES[module_id]["quiz_choices"])
        correct2, _ = curriculum.check_quiz(module_id, wrong_index)
        assert correct2 is False

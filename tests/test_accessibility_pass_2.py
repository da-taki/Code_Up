"""Accessibility pass 2 (research-backed, evidence-gated): three on-demand
commands reusing existing engines, no new modes/panels/subsystems.

  - structure_tools.semantic_find   ("find <word>" disambiguated by role)
  - precision_reader.compare_exact  (reused, now callable on two names via
                                      the "compare_identifiers" intent)
  - app._output_line_info_speech    ("how many lines of output" / first/last)

No AI provider is required anywhere in this file -- all three are pure
deterministic composition of already-existing engines (AST walk +
collect_comments, the existing character-diff function, mem["last_run_output"]).
"""

import pytest

from codeup.projects import structure_tools
from codeup.accessibility import audio_blocks
from app import app
from codeup.commands.intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def vc(client, text, **payload):
    payload.setdefault("source", "typed")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


FLAGSHIP_CODE = (
    "def analyze(numbers):\n"
    "    total = 0\n"
    "    for number in numbers:\n"
    "        total += number\n"
    "        if total > 20:\n"
    "            print(total)\n"
    "    return total\n"
)


# ---- module: structure_tools.semantic_find ------------------------------

def test_semantic_find_all_roles_flagship_program():
    result = structure_tools.semantic_find(FLAGSHIP_CODE, "total")
    assert result["found"] is True
    assert result["count"] == 5
    roles = {o["line"]: o["role"] for o in result["occurrences"]}
    assert roles[2] == "assignment"
    assert roles[4] == "updated"
    assert roles[5] == "condition"
    assert roles[6] == "used in a call"
    assert roles[7] == "returned"


def test_semantic_find_function_name():
    result = structure_tools.semantic_find(FLAGSHIP_CODE, "analyze")
    assert result["found"] is True
    assert result["occurrences"] == [{"line": 1, "role": "function definition"}]


def test_semantic_find_parameter_and_use():
    result = structure_tools.semantic_find(FLAGSHIP_CODE, "numbers")
    roles = [o["role"] for o in result["occurrences"]]
    assert "parameter" in roles
    assert "used" in roles


def test_semantic_find_loop_variable():
    result = structure_tools.semantic_find(FLAGSHIP_CODE, "number")
    roles = {o["line"]: o["role"] for o in result["occurrences"]}
    assert roles[3] == "loop variable"
    assert roles[4] == "used"


def test_semantic_find_string_literal():
    code = 'name = "total sales"\nprint(name)\n'
    result = structure_tools.semantic_find(code, "total")
    assert result["found"] is True
    assert result["occurrences"] == [{"line": 1, "role": "text inside a string"}]


def test_semantic_find_comment():
    code = "x = 1\n# total will be added later\nprint(x)\n"
    result = structure_tools.semantic_find(code, "total")
    assert result["found"] is True
    assert result["occurrences"] == [{"line": 2, "role": "in a comment"}]


def test_semantic_find_no_occurrence():
    result = structure_tools.semantic_find(FLAGSHIP_CODE, "nonexistent_word")
    assert result["found"] is False
    assert "could not find" in result["message"].lower()


def test_semantic_find_whole_word_not_substring():
    # 'total' must not match inside 'totals' -- confusable-identifier safety.
    result = structure_tools.semantic_find("totals = [1, 2]\nprint(totals)\n", "total")
    assert result["found"] is False


def test_semantic_find_never_hallucinates_role_on_multiple_matches():
    # Every occurrence must be independently, correctly labeled -- no role
    # ever copy-pasted from a different occurrence of the same word.
    code = "score1 = 1\nscore2 = score1 + 1\nprint(score1)\n"
    result = structure_tools.semantic_find(code, "score1")
    roles = {o["line"]: o["role"] for o in result["occurrences"]}
    assert roles[1] == "assignment"
    assert roles[2] == "used"
    assert roles[3] == "used in a call"


# ---- correctness pass: broken-Python lexical fallback ---------------------

BROKEN_CODE = "total = 0\nif total >:\n    print(total)\n"


def test_semantic_find_broken_python_does_not_report_missing():
    # The exact bug report: ast.parse fails on "if total >:", and the old
    # code fell back to comment-only scanning, wrongly claiming 'total' was
    # not found even though it plainly appears three times.
    result = structure_tools.semantic_find(BROKEN_CODE, "total")
    assert result["found"] is True
    assert result["count"] == 3
    assert [o["line"] for o in result["occurrences"]] == [1, 2, 3]


def test_semantic_find_broken_python_never_invents_roles():
    # Without a parse, no role can be verified as "assignment"/"condition"/
    # etc -- must use the honest, conservative "found in code" label.
    result = structure_tools.semantic_find(BROKEN_CODE, "total")
    roles = {o["role"] for o in result["occurrences"]}
    assert roles == {"found in code"}
    invented = {"assignment", "condition", "updated", "used in a call",
                "returned", "loop variable", "parameter"}
    assert not (roles & invented)


def test_semantic_find_broken_python_still_finds_comments():
    code = "total = 0\nif total >:\n    # total needs fixing\n    print(total)\n"
    result = structure_tools.semantic_find(code, "total")
    roles = {o["line"]: o["role"] for o in result["occurrences"]}
    assert roles[3] == "in a comment"
    assert roles[1] == "found in code"


def test_semantic_find_broken_python_whole_word_safety():
    # 'total' must still not match inside 'totals' when the code is broken.
    result = structure_tools.semantic_find("totals = 0\nif totals >:\n    pass\n", "total")
    assert result["found"] is False


def test_semantic_find_broken_strings_do_not_crash():
    result = structure_tools.semantic_find('x = "total\nprint(total)\n', "total")
    assert result["found"] is True
    assert result["count"] >= 1


def test_semantic_find_broken_indentation_does_not_crash():
    result = structure_tools.semantic_find("if total:\nprint(total)\n", "total")
    assert result["found"] is True
    assert result["count"] == 2


def test_semantic_find_empty_code_still_graceful():
    result = structure_tools.semantic_find("", "total")
    assert result["found"] is False
    assert "no code" in result["message"].lower()


# ---- correctness pass: truthful multi-occurrence-per-line counting -------

def test_semantic_find_counts_twice_in_one_string():
    result = structure_tools.semantic_find('print("total total")\n', "total")
    assert result["found"] is True
    assert result["count"] == 2
    assert all(o["line"] == 1 and o["role"] == "text inside a string" for o in result["occurrences"])


def test_semantic_find_counts_three_times_in_one_comment():
    result = structure_tools.semantic_find("x = 1\n# total total total\n", "total")
    assert result["found"] is True
    assert result["count"] == 3
    assert all(o["line"] == 2 and o["role"] == "in a comment" for o in result["occurrences"])


def test_semantic_find_counts_twice_as_identifiers_on_one_line():
    result = structure_tools.semantic_find("total = 1\nprint(total, total)\n", "total")
    assert result["count"] == 3  # line 1 assignment + two uses on line 2
    line2_roles = [o["role"] for o in result["occurrences"] if o["line"] == 2]
    assert line2_roles == ["used in a call", "used in a call"]


def test_semantic_find_counts_identifier_plus_string_same_line():
    result = structure_tools.semantic_find('total = 1\nprint(total, "total")\n', "total")
    line2_roles = sorted(o["role"] for o in result["occurrences"] if o["line"] == 2)
    assert line2_roles == ["text inside a string", "used in a call"]


def test_semantic_find_counts_identifier_plus_comment_same_line():
    result = structure_tools.semantic_find("total = 1\nprint(total)  # total\n", "total")
    line2_roles = sorted(o["role"] for o in result["occurrences"] if o["line"] == 2)
    assert line2_roles == ["in a comment", "used in a call"]


def test_semantic_find_substring_safety_string_and_comment():
    result = structure_tools.semantic_find('print("totals")\n# totals here\n', "total")
    assert result["found"] is False


# ---- intents registered ---------------------------------------------------

def test_new_intents_registered():
    assert parse_intent("find total")["intent"] == "semantic_find"
    assert parse_intent("search for total")["intent"] == "semantic_find"
    assert parse_intent("locate total")["intent"] == "semantic_find"
    assert parse_intent("compare score1 and score2")["intent"] == "compare_identifiers"
    assert parse_intent("what's the difference between userID and userId")["intent"] == "compare_identifiers"
    assert parse_intent("how many lines of output")["intent"] == "output_line_info"
    assert parse_intent("first line of output")["intent"] == "output_line_info"
    assert parse_intent("last line of output")["intent"] == "output_line_info"


def test_semantic_find_does_not_collide_with_existing_find_commands():
    # These must keep routing to their existing, more specific intents.
    assert parse_intent("find function analyze")["intent"] == "find_function"
    assert parse_intent("find class Foo")["intent"] == "find_class"
    assert parse_intent("find definition of total")["intent"] == "goto_definition"
    assert parse_intent("find uses of total")["intent"] == "find_references"
    assert parse_intent("find references to total")["intent"] == "find_references"
    assert parse_intent("find long lines")["intent"] == "check_long_lines"
    assert parse_intent("find todo comments")["intent"] == "show_todos"
    assert parse_intent("find missing files")["intent"] == "missing_project_files"
    assert parse_intent("find risky code")["intent"] == "sandbox_check"


def test_compare_identifiers_does_not_collide_with_compare_exact():
    assert parse_intent("compare exact")["intent"] == "compare_exact"
    assert parse_intent("what changed character by character")["intent"] == "compare_exact"


def test_compare_before_after_does_not_collide_with_identifier_comparison():
    assert parse_intent("compare before and after")["intent"] == "compare_before_after"
    assert parse_intent("compare score1 and score2")["intent"] == "compare_identifiers"


# ---- routing: semantic_find -----------------------------------------------

def test_semantic_find_routes_and_navigates(client):
    data = vc(client, "find total", code=FLAGSHIP_CODE)
    assert data["action"] == "navigate_code"
    assert data["line"] == 2
    assert "assignment" in data["speech"].lower()
    assert "5 times" in data["speech"] or "five times" in data["speech"].lower()


def test_semantic_find_more_gives_full_list(client):
    vc(client, "find total", code=FLAGSHIP_CODE)
    data = vc(client, "more", code=FLAGSHIP_CODE)
    assert data["action"] == "deterministic_message"
    for line_no in (2, 4, 5, 6, 7):
        assert f"line {line_no}" in data["speech"].lower()


def test_semantic_find_more_refuses_stale_after_code_change(client):
    vc(client, "find total", code=FLAGSHIP_CODE)
    data = vc(client, "more", code=FLAGSHIP_CODE + "\nx = 1\n")
    assert "changed" in data["speech"].lower()


def test_semantic_find_no_match_is_graceful(client):
    data = vc(client, "find nonexistentword", code=FLAGSHIP_CODE)
    assert data["action"] == "deterministic_message"
    assert "could not find" in data["speech"].lower()


def test_semantic_find_missing_code_does_not_crash(client):
    data = vc(client, "find total")
    assert data["success"] is not False


# ---- routing: compare_identifiers -----------------------------------------

def test_compare_identifiers_case_difference(client):
    data = vc(client, "compare userID and userId")
    assert data["action"] == "deterministic_message"
    assert "userID" in data["speech"] and "userId" in data["speech"]
    assert "position" in data["speech"].lower()


def test_compare_identifiers_digit_difference(client):
    data = vc(client, "compare score1 and score2")
    assert "position" in data["speech"].lower()
    assert "1" in data["speech"] and "2" in data["speech"]


def test_compare_identifiers_identical_names(client):
    data = vc(client, "compare total and total")
    assert "spelled exactly the same" in data["speech"].lower()


def test_compare_identifiers_suffix_difference(client):
    data = vc(client, "what's the difference between studentScore and studentScores")
    assert "studentScore" in data["speech"]


# ---- routing: output_line_info --------------------------------------------

def test_output_line_count(client):
    client.post("/run", json={"code": "print('a')\nprint('b')\nprint('c')\n"})
    data = vc(client, "how many lines of output")
    assert data["action"] == "deterministic_message"
    assert "3 lines" in data["speech"]


def test_output_first_and_last_line(client):
    client.post("/run", json={"code": "print('first')\nprint('middle')\nprint('last')\n"})
    first = vc(client, "first line of output")
    assert "first" in first["speech"].lower()
    last = vc(client, "last line of output")
    assert "last" in last["speech"].lower()


def test_output_line_info_no_previous_output(client):
    data = vc(client, "how many lines of output")
    assert "no previous output" in data["speech"].lower()


# ---- correctness pass: blank output is still output ------------------------
#
# session_memory._clip_output() strips whitespace before storing
# mem["last_run_output"] (shared with repeat_last_output/narration), so a
# program that printed ONLY blank lines (print("") -> "\n") used to be
# indistinguishable from "never ran" -- both stored "". These tests pin the
# fixed three-way distinction: never ran / ran with blank line(s) / ran with
# real content, using run_count + the existing _last_outputs raw-output cache.

def test_output_count_one_blank_line(client):
    client.post("/run", json={"code": "print('')\n"})
    data = vc(client, "how many lines of output")
    assert "1 line" in data["speech"] and "lines" not in data["speech"]


def test_output_count_two_blank_lines(client):
    client.post("/run", json={"code": "print('')\nprint('')\n"})
    data = vc(client, "how many lines of output")
    assert "2 lines" in data["speech"]


def test_output_count_normal_one_line(client):
    client.post("/run", json={"code": "print('hello')\n"})
    data = vc(client, "how many lines of output")
    assert "1 line" in data["speech"] and "lines" not in data["speech"]


def test_output_count_normal_multiline_ending_in_newline(client):
    client.post("/run", json={"code": "print('a')\nprint('b')\n"})
    data = vc(client, "how many lines of output")
    assert "2 lines" in data["speech"]


def test_output_first_line_blank(client):
    client.post("/run", json={"code": "print('')\nprint('x')\n"})
    data = vc(client, "first line of output")
    assert "first line of output is blank" in data["speech"].lower()


def test_output_last_line_blank(client):
    client.post("/run", json={"code": "print('x')\nprint('')\n"})
    data = vc(client, "last line of output")
    assert "last line of output is blank" in data["speech"].lower()


def test_output_line_info_program_ran_with_truly_no_output(client):
    # Distinct third case: a successful run that printed nothing at all
    # (not even a blank line) is neither "never ran" nor "1 blank line".
    client.post("/run", json={"code": "x = 1\n"})
    data = vc(client, "how many lines of output")
    assert "no previous output" not in data["speech"].lower()
    assert "no output" in data["speech"].lower()


def test_output_line_info_after_error_does_not_leak_stale_output(client):
    # A prior successful run's raw output must not leak through _last_outputs
    # after a SUBSEQUENT run errors -- last_run_ok gates the fallback.
    client.post("/run", json={"code": "print('')\n"})
    client.post("/run", json={"code": "print(undefined_name)\n"})
    data = vc(client, "how many lines of output")
    assert "no previous output" in data["speech"].lower()


# ---- Audio Blocks Mode safety ----------------------------------------------

@pytest.mark.parametrize("cmd", ["find total", "compare score1 and score2"])
def test_python_specific_commands_redirect_in_audio_blocks(client, cmd):
    data = vc(client, cmd, code=FLAGSHIP_CODE, active_mode="audio_blocks")
    assert data["action"] == "deterministic_message"
    assert "python code mode" in data["speech"].lower() or "switch to python mode" in data["speech"].lower()


@pytest.mark.parametrize("cmd", ["how many lines of output", "first line of output", "last line of output"])
def test_output_commands_reach_handler_in_audio_blocks(client, cmd):
    client.post("/run", json={"code": "print('x')\n"})
    data = vc(client, cmd, active_mode="audio_blocks")
    # Must reach the real handler (GLOBAL_PASS_THROUGH), not the generic
    # "That block command is not available yet." dead-end.
    assert "not available yet" not in data["speech"].lower()


def test_compare_blocks_and_code_still_owned_by_audio_blocks_mode():
    # Regression: "compare blocks and code" is Audio Blocks Mode's own
    # pre-existing block-vs-editor diff command -- the new "compare X and Y"
    # identifier-comparison pattern must not swallow it.
    assert not audio_blocks.PYTHON_STRUCTURE_RE.match("compare blocks and code")
    assert parse_intent("compare blocks and code")["intent"] != "compare_identifiers"


def test_audio_blocks_regex_matches_new_phrases_directly():
    assert audio_blocks.PYTHON_STRUCTURE_RE.match("find total")
    assert audio_blocks.PYTHON_STRUCTURE_RE.match("compare score1 and score2")
    assert "how many lines of output" in audio_blocks.GLOBAL_PASS_THROUGH
    assert "first line of output" in audio_blocks.GLOBAL_PASS_THROUGH
    assert "last line of output" in audio_blocks.GLOBAL_PASS_THROUGH


# ---- robustness -------------------------------------------------------

def test_new_commands_do_not_call_ai(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider called for deterministic accessibility-pass-2 command")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    assert vc(client, "find total", code=FLAGSHIP_CODE)["success"] is not False
    assert vc(client, "compare score1 and score2")["success"] is not False
    assert vc(client, "how many lines of output")["success"] is not False


def test_compare_exact_still_reuses_last_change_not_broken(client):
    # Regression: the original "compare exact" (before/after last edit)
    # must still work unmodified.
    client.post("/run", json={"code": "for i in range(3):\nprint(i)\n"})
    vc(client, "fix with explanation", code="for i in range(3):\nprint(i)\n")
    vc(client, "apply", code="for i in range(3):\nprint(i)\n")
    data = vc(client, "compare exact")
    assert data["action"] == "deterministic_message"

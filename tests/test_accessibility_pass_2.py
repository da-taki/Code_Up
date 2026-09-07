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

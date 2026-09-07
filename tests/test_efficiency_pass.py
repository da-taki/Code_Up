"""Regression tests for the "efficiency-first nonvisual programming" pass.

Covers the composition features built on top of 578bf3c's structure_tools work:
  - structure_tools.orientation_cue      (automatic short orientation on a jump)
  - structure_tools.block_navigation     (parent / first child / next+previous sibling)
  - structure_tools.program_overview     ("overview" / semantic skim)
  - app._mental_map_speech               ("mental map" -- composed context)
  - app._unified_execution_speech        ("what is my program doing" etc.)
  - Progressive Disclosure (session_memory explanation-context slot + "more"/"exact")

No AI provider is required anywhere in this file. All new behavior is pure
composition of already-existing deterministic facts (AST structure, state-watch
trace data, error_trace, change history) -- no new parsing/tracer/diff engine.
"""

import pytest

import app as app_module
from codeup.accessibility import precision_reader
from codeup.learning import learning_moat
from codeup.projects import structure_tools
from codeup.runtime import session_memory

NESTED_CODE = (
    "def analyze(numbers):\n"
    "    total = 0\n"
    "    for n in numbers:\n"
    "        if n > 5:\n"
    "            total += n\n"
    "            if total > 20:\n"
    "                print(total)\n"
    "    return total\n"
)

LOOP_CODE = "total = 0\nfor n in [2, 7, 9]:\n    total += n\nprint(total)\n"

IF_ELSE_CODE = 'age = 16\nif age >= 18:\n    print("adult")\nelse:\n    print("minor")\n'

BAD_INDENT_CODE = 'if True:\nprint("hello")\n'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, code=NESTED_CODE, **kw):
    return client.post("/voice-command", json={"text": text, "code": code, **kw}).get_json()


def _run(client, code):
    return client.post("/run", json={"code": code}).get_json()


class TestPrecisionMode:

    def test_read_exact_line_names_every_token(self):
        speech = precision_reader.read_line_exact("total = total + n\n", 1)
        assert speech == "Line 1: total, space, equals, space, total, space, plus, space, n."

    def test_read_exact_line_reports_leading_spaces(self):
        speech = precision_reader.read_line_exact("            print(total)\n", 1)
        assert speech.startswith("Line 1: 12 spaces of indentation, then")

    def test_read_exact_line_reports_leading_tabs_distinctly(self):
        speech = precision_reader.read_line_exact("\t\tif True:\n", 1)
        assert "2 tabs of indentation" in speech

    def test_read_exact_line_blank(self):
        assert precision_reader.read_line_exact("x = 1\n\ny = 2\n", 2) == "Line 2 is blank."

    def test_read_exact_line_preserves_case(self):
        speech = precision_reader.read_line_exact("Total = Total\n", 1)
        assert "Total" in speech

    def test_read_exact_line_caps_very_long_lines(self):
        long_line = "x = " + " + ".join(str(i) for i in range(500)) + "\n"
        speech = precision_reader.read_line_exact(long_line, 1)
        assert len(speech) < 1200
        assert "truncated" in speech

    def test_read_punctuation_only_symbols(self):
        speech = precision_reader.read_punctuation("total = total + n\n", 1)
        assert speech == "Line 1 punctuation, in order: equals, plus."

    def test_read_punctuation_none_on_line(self):
        speech = precision_reader.read_punctuation("total\n", 1)
        assert "no punctuation" in speech.lower()

    def test_punctuation_heavy_dict_and_index_line(self):
        code = 'data = {"name": "Alex", "scores": [8, 10, 12]}\n'
        speech = precision_reader.read_punctuation(code, 1)
        for word in ("equals", "open brace", "colon", "comma", "open bracket", "close bracket", "close brace"):
            assert word in speech

    def test_spell_current_token_letter_by_letter(self):
        speech = precision_reader.spell_token("total = 1\n", 1)
        assert speech == "total, spelled: t, o, t, a, l."

    def test_spell_token_calls_out_capitals(self):
        speech = precision_reader.spell_token("Total = 1\n", 1)
        assert "capital t" in speech

    def test_spell_token_no_word_on_line(self):
        speech = precision_reader.spell_token("():\n", 1)
        assert "no word or number" in speech.lower()

    def test_read_char_by_char_every_character(self):
        speech = precision_reader.read_char_by_char("ab\n", 1)
        assert speech == "Line 1, character by character: a, b."

    def test_read_char_by_char_capitals_and_symbols(self):
        speech = precision_reader.read_char_by_char("A=1\n", 1)
        assert "capital a" in speech
        assert "equals" in speech

    def test_read_char_by_char_tabs_and_spaces_distinguished(self):
        speech = precision_reader.read_char_by_char("\t x\n", 1)
        assert speech.startswith("Line 1, character by character: tab, space, x")

    def test_read_char_by_char_caps_long_lines(self):
        speech = precision_reader.read_char_by_char("x" * 500, 1, max_chars=50)
        assert "truncated at 50" in speech

    def test_compare_exact_single_char_typo(self):
        speech = precision_reader.compare_exact("print(totl)", "print(total)")
        assert "position" in speech
        assert '"a"' in speech or "was added" in speech

    def test_compare_exact_identical(self):
        assert precision_reader.compare_exact("x", "x") == "No character-level difference."

    def test_compare_exact_reuses_line_pairing_not_line_level_diff(self):
        # Distinct from audio_diff.diff_lines: operates on two raw strings
        # directly, no line-splitting needed for a single-line comparison.
        speech = precision_reader.compare_exact("abc", "abd")
        assert "position" in speech

    def test_no_code_yet(self):
        assert precision_reader.read_line_exact("", 1) == "There is no code yet."
        assert precision_reader.read_punctuation("", 1) == "There is no code yet."


class TestPrecisionModeRouting:

    def test_read_exact_line_command(self, client):
        d = _vc(client, "read exact line", code="total = total + n\n", cursor_line=1)
        assert d["action"] == "deterministic_message"
        assert d["speech"] == "Line 1: total, space, equals, space, total, space, plus, space, n."

    def test_read_punctuation_command(self, client):
        d = _vc(client, "read punctuation", code="total = total + n\n", cursor_line=1)
        assert d["intent"] == "read_punctuation"

    def test_spell_current_token_command(self, client):
        d = _vc(client, "spell current token", code="total = 1\n", cursor_line=1)
        assert "spelled" in d["speech"]

    def test_read_character_by_character_command(self, client):
        d = _vc(client, "read character by character", code="ab\n", cursor_line=1)
        assert d["speech"] == "Line 1, character by character: a, b."

    def test_read_indentation_exactly_aliases_existing_function(self, client):
        d = _vc(client, "read indentation exactly", code=NESTED_CODE, cursor_line=7)
        assert d["intent"] == "read_indentation_exactly"
        assert "depth 4" in d["speech"]

    def test_compare_exact_no_recent_change(self, client):
        d = _vc(client, "compare exact")
        assert "no recent change" in d["speech"].lower()

    def test_compare_exact_after_a_real_fix(self, client):
        broken = "for i in range(3):\nprint(i)\n"
        _run(client, broken)
        _vc(client, "fix with explanation", code=broken)
        _vc(client, "apply", code=broken)
        d = _vc(client, "compare exact", code="for i in range(3):\n    print(i)\n")
        assert "position" in d["speech"]

    def test_no_ai_provider_needed(self, client):
        d = _vc(client, "read exact line", code="x = 1\n", cursor_line=1)
        assert d.get("intent") == "read_line_exact"


class TestOrientationCue:

    def test_names_enclosing_function_and_innermost_block(self):
        cue = structure_tools.orientation_cue(NESTED_CODE, 7)
        assert cue == ("Moved to line 7, function analyze, inside the condition "
                       "total > 20 beginning on line 6.")

    def test_top_level_line_has_no_enclosing_block(self):
        cue = structure_tools.orientation_cue("x = 1\n", 1)
        assert cue == "Moved to line 1, at the top level."

    def test_custom_verb(self):
        cue = structure_tools.orientation_cue(NESTED_CODE, 2, verb="Back to")
        assert cue.startswith("Back to line 2,")

    def test_syntax_error_falls_back_gracefully(self):
        cue = structure_tools.orientation_cue("def f(:\n    pass\n", 2)
        assert cue == "Moved to line 2."


class TestBlockNavigation:

    def test_parent_of_deepest_line(self):
        result = structure_tools.block_navigation(NESTED_CODE, 7, "parent")
        assert result["found"] is True
        assert result["line"] == 4
        assert "n > 5" in result["message"]

    def test_top_level_block_has_no_parent(self):
        result = structure_tools.block_navigation(NESTED_CODE, 2, "parent")
        assert result["found"] is False
        assert "no parent" in result["message"].lower()

    def test_first_child_of_for_loop(self):
        result = structure_tools.block_navigation(NESTED_CODE, 3, "first_child")
        assert result["found"] is True
        assert result["line"] == 4

    def test_block_with_no_children(self):
        result = structure_tools.block_navigation(NESTED_CODE, 6, "first_child")
        assert result["found"] is False

    def test_next_and_previous_sibling(self):
        code = "if True:\n    pass\nif False:\n    pass\n"
        nxt = structure_tools.block_navigation(code, 1, "next_sibling")
        assert nxt["found"] is True and nxt["line"] == 3
        prev = structure_tools.block_navigation(code, 3, "previous_sibling")
        assert prev["found"] is True and prev["line"] == 1

    def test_no_next_sibling_at_last_block(self):
        result = structure_tools.block_navigation(NESTED_CODE, 4, "next_sibling")
        assert result["found"] is False
        assert "last block" in result["message"].lower()

    def test_malformed_code_reports_syntax_error(self):
        result = structure_tools.block_navigation("def f(:\n    pass\n", 1, "parent")
        assert result["found"] is False
        assert "syntax error" in result["message"].lower()


class TestProgramOverview:

    def test_reports_line_count_functions_and_a_landmark(self):
        speech = structure_tools.program_overview(NESTED_CODE)
        assert "8 lines" in speech
        assert "analyze (line 1)" in speech
        assert "for loop from lines 3 to 7" in speech

    def test_does_not_dump_every_ast_node(self):
        # Landmark description is capped, not an exhaustive per-node dump.
        speech = structure_tools.program_overview(NESTED_CODE)
        assert speech.count("condition") <= 2

    def test_empty_code(self):
        assert "no code" in structure_tools.program_overview("").lower()


class TestVoiceRoutingForNavAndOverview:

    def test_overview_command(self, client):
        d = _vc(client, "overview")
        assert d["action"] == "deterministic_message"
        assert d["intent"] == "program_overview"
        assert "8 lines" in d["speech"]

    def test_parent_command_moves_cursor(self, client):
        d = _vc(client, "parent", cursor_line=7)
        assert d["action"] == "navigate_code"
        assert d["line"] == 4

    def test_first_child_command(self, client):
        d = _vc(client, "first child", cursor_line=3)
        assert d["action"] == "navigate_code"
        assert d["line"] == 4

    def test_next_sibling_and_previous_sibling(self, client):
        code = "if True:\n    pass\nif False:\n    pass\n"
        nxt = _vc(client, "next sibling", code=code, cursor_line=1)
        assert nxt["line"] == 3
        prev = _vc(client, "previous sibling", code=code, cursor_line=3)
        assert prev["line"] == 1

    def test_automatic_orientation_on_go_to_the_loop(self, client):
        d = _vc(client, "go to the for loop")
        assert d["action"] == "navigate_code"
        assert "Moved to line" in d["speech"]
        assert "function analyze" in d["speech"]


class TestMentalMap:

    def test_no_runtime_trace_only_shows_structure(self, client):
        d = _vc(client, "mental map", cursor_line=7)
        assert d["intent"] == "mental_map"
        assert "Line 7" in d["speech"]
        assert "function analyze" in d["speech"]
        assert "From the last run" not in d["speech"]
        assert "Your latest change" not in d["speech"]

    def test_includes_runtime_facts_after_a_trace(self, client):
        _vc(client, "step through this", code=LOOP_CODE)
        d = _vc(client, "mental map", code=LOOP_CODE, cursor_line=3)
        assert "From the last run" in d["speech"]

    def test_includes_condition_reasoning_after_a_trace(self, client):
        _vc(client, "step through this", code=IF_ELSE_CODE)
        d = _vc(client, "mental map", code=IF_ELSE_CODE, cursor_line=2)
        assert "was true" in d["speech"] or "was false" in d["speech"]

    def test_includes_error_from_a_traced_crash(self, client):
        _vc(client, "step through this", code=BAD_INDENT_CODE)
        d = _vc(client, "mental map", code=BAD_INDENT_CODE, cursor_line=2)
        assert "IndentationError" in d["speech"]

    def test_no_context_yet_says_so_honestly(self, client):
        d = _vc(client, "mental map", code="")
        assert "not much context" in d["speech"].lower()

    def test_alias_what_should_i_know_right_now(self, client):
        d = _vc(client, "what should I know right now", cursor_line=1)
        assert d["intent"] == "mental_map"


class TestUnifiedExecutionExplanation:

    def test_what_is_my_program_doing(self, client):
        _vc(client, "step through this", code=LOOP_CODE)
        d = _vc(client, "what is my program doing", code=LOOP_CODE)
        assert d["intent"] == "unified_execution"
        assert "Step 1 of" in d["speech"]

    def test_what_just_happened_reads_current_step(self, client):
        _vc(client, "step through this", code=LOOP_CODE)
        _vc(client, "next step", code=LOOP_CODE)
        d = _vc(client, "what just happened", code=LOOP_CODE)
        assert "Step 2 of" in d["speech"]

    def test_condition_reasoning_merged_into_step(self, client):
        _vc(client, "step through this", code=IF_ELSE_CODE)
        d = _vc(client, "why did that happen", code=IF_ELSE_CODE)
        assert "Step" in d["speech"]

    def test_no_trace_yet(self, client):
        d = _vc(client, "what is my program doing", code=LOOP_CODE)
        assert "do not have a state trace" in d["speech"].lower()

    def test_stale_trace_after_code_change(self, client):
        _vc(client, "step through this", code=LOOP_CODE)
        d = _vc(client, "what just happened", code=LOOP_CODE + "\n# changed\n")
        assert "code changed" in d["speech"].lower()

    def test_crash_reports_error_not_a_fake_step(self, client):
        _vc(client, "step through this", code=BAD_INDENT_CODE)
        d = _vc(client, "what is my program doing", code=BAD_INDENT_CODE)
        assert "IndentationError" in d["speech"]

    def test_no_ai_provider_needed(self, client):
        _vc(client, "step through this", code=LOOP_CODE)
        d = _vc(client, "what is my program doing", code=LOOP_CODE)
        assert d.get("intent") == "unified_execution"


class TestContextResume:

    def test_cold_start_no_previous_location(self, client):
        d = _vc(client, "where was i")
        assert "do not have a previous location" in d["speech"].lower()

    def test_back_to_output_targets_output_panel(self, client):
        d = _vc(client, "back to output")
        assert d["action"] == "focus_target"
        assert d["target"] == "output"
        assert d["speech"]

    def test_back_to_code_restores_cursor_line_with_orientation(self, client):
        _vc(client, "where am i", cursor_line=5)
        d = _vc(client, "back to code", cursor_line=5)
        assert d["action"] == "focus_target"
        assert d["target"] == "__editor__"
        assert "line 5" in d["speech"]
        assert "function analyze" in d["speech"]

    def test_where_was_i_recalls_code_after_switching_to_errors(self, client):
        _vc(client, "where am i", cursor_line=5)
        _vc(client, "back to code", cursor_line=5)
        bad = "if True:\nprint(1)\n"
        _vc(client, "explain error", code=bad)
        d = _vc(client, "where was i", cursor_line=5)
        assert d["target"] == "__editor__"
        assert "line 5" in d["speech"]

    def test_where_was_i_recalls_a_non_code_surface(self, client):
        # code -> output -> errors: "where was i" from "errors" should recall
        # "output" (the surface immediately before it), not "code".
        _vc(client, "back to code", cursor_line=1)
        _vc(client, "what did the program print")
        bad = "if True:\nprint(1)\n"
        _vc(client, "explain error", code=bad)
        d = _vc(client, "where was i")
        assert d["target"] == "output"
        assert "panel" in d["speech"].lower()

    def test_session_memory_surface_stack(self):
        mem = session_memory.new_memory()
        assert session_memory.get_current_surface(mem) is None
        assert session_memory.get_previous_surface(mem) is None
        session_memory.push_context_surface(mem, "code", line=5)
        assert session_memory.get_current_surface(mem)["surface"] == "code"
        assert session_memory.get_previous_surface(mem) is None
        session_memory.push_context_surface(mem, "output")
        assert session_memory.get_current_surface(mem)["surface"] == "output"
        assert session_memory.get_previous_surface(mem)["surface"] == "code"
        assert session_memory.get_previous_surface(mem)["line"] == 5

    def test_record_editor_code_persists_cursor_line(self):
        mem = session_memory.new_memory()
        assert mem.get("last_cursor_line") is None
        session_memory.record_editor_code(mem, "x = 1\n", 3)
        assert mem["last_cursor_line"] == 3


class TestBrailleCompactView:

    def test_only_leading_indentation_is_compacted(self):
        result = structure_tools.braille_compact_view(NESTED_CODE)
        rows = {r["line"]: r for r in result["lines"]}
        assert rows[1]["marker"] == "D0"
        assert rows[2]["marker"] == "D1"
        assert rows[7]["marker"] == "D4"

    def test_mid_line_spacing_untouched(self):
        code = "x = [1,    2,   3]\n"
        result = structure_tools.braille_compact_view(code)
        assert result["lines"][0]["text"] == "x = [1,    2,   3]"

    def test_tabs_distinguished_from_spaces(self):
        code = "def f():\n\tif True:\n\t\tprint(1)\n"
        result = structure_tools.braille_compact_view(code)
        rows = {r["line"]: r for r in result["lines"]}
        assert rows[2]["marker"] == "T1"
        assert rows[3]["marker"] == "T2"

    def test_exact_source_is_recoverable_unchanged(self):
        code = "def f():\n    x = 1\n"
        result = structure_tools.braille_compact_view(code)
        assert result["lines"][1]["exact_source"] == "    x = 1"

    def test_blank_line(self):
        result = structure_tools.braille_compact_view("x = 1\n\ny = 2\n")
        assert result["lines"][1]["text"] == ""
        assert "(blank)" in result["text"]

    def test_unicode_preserved(self):
        code = 'x = "héllo wörld"\n'
        result = structure_tools.braille_compact_view(code)
        assert result["lines"][0]["text"] == 'x = "héllo wörld"'

    def test_works_on_malformed_code_no_ast_dependency(self):
        result = structure_tools.braille_compact_view("if True:\nprint(1)\n")
        assert result["lines"][0]["marker"] == "D0"
        assert result["lines"][1]["marker"] == "D0"

    def test_voice_command_marks_experimental(self, client):
        d = _vc(client, "braille compact view", cursor_line=7)
        assert "experimental" in d["speech"].lower()
        assert "D" in d["speech"]

    def test_turn_off_is_honest_about_being_on_demand(self, client):
        d = _vc(client, "turn off braille compact view")
        assert "on demand" in d["speech"].lower()


class TestProgramFlow:

    def test_sequence_if_else_loop_return(self):
        code = ("def process(marks):\n"
               "    total = 0\n"
               "    for mark in marks:\n"
               "        if mark >= 50:\n"
               "            total += mark\n"
               "    return total\n")
        flow = structure_tools.describe_program_flow(code)
        assert flow.startswith("Start.")
        assert flow.endswith("End.")
        assert "function process" in flow
        assert "loop through marks" in flow
        assert "condition mark >= 50" in flow
        assert "true ->" in flow
        assert "return total" in flow

    def test_if_else_both_branches_described(self):
        code = 'age = 16\nif age >= 18:\n    print("adult")\nelse:\n    print("minor")\n'
        flow = structure_tools.describe_program_flow(code)
        assert "true ->" in flow
        assert "false ->" in flow

    def test_input_output_statements(self):
        code = 'name = input("Name: ")\nprint("Hello", name)\n'
        flow = structure_tools.describe_program_flow(code)
        assert "line 1" in flow and "line 2" in flow

    def test_unsupported_construct_falls_back_honestly(self):
        code = "try:\n    x = 1\nexcept Exception:\n    pass\n"
        flow = structure_tools.describe_program_flow(code)
        assert "flow becomes more complex here" in flow.lower()
        assert "Code Map or Step Narration" in flow

    def test_empty_code(self):
        assert "no code" in structure_tools.describe_program_flow("").lower()

    def test_syntax_error(self):
        assert "syntax error" in structure_tools.describe_program_flow("def f(:\n").lower()

    def test_voice_command_routing(self, client):
        d = _vc(client, "describe program flow")
        assert d["action"] == "deterministic_message"
        assert d["speech"].startswith("Start.")


class TestHelpRequest:

    def test_includes_relevant_facts(self):
        mem = {"latest_user_request": "add a total"}
        result = learning_moat.build_help_request_pack(mem, LOOP_CODE)
        assert "## What I am trying to do" in result["message"]
        assert "add a total" in result["message"]
        assert "## My code" in result["message"]

    def test_excludes_empty_sections(self):
        mem = {}
        result = learning_moat.build_help_request_pack(mem, "")
        msg = result["message"]
        assert "## What I am trying to do" not in msg
        assert "## My code" not in msg
        assert "## What changed recently" not in msg
        assert "## Program state" not in msg
        assert "## What I already tried" not in msg
        assert "## My question" in msg  # always present

    def test_includes_error_when_present(self):
        mem = {"last_run_error": "Line 2: IndentationError: expected an indented block"}
        result = learning_moat.build_help_request_pack(mem, BAD_INDENT_CODE)
        assert "## My error" in result["message"]
        assert "IndentationError" in result["message"]

    def test_redacts_secrets(self):
        mem = {}
        code = 'api_key = "sk-abcdef1234567890"\nprint(api_key)\n'
        result = learning_moat.build_help_request_pack(mem, code)
        assert "sk-abcdef1234567890" not in result["message"]
        assert "redacted" in result["message"].lower()

    def test_addressed_to_teacher_not_codex(self):
        result = learning_moat.build_help_request_pack({}, LOOP_CODE)
        assert "teacher" in result["message"].lower()
        assert "codex" not in result["message"].lower()

    def test_custom_question_included(self):
        result = learning_moat.build_help_request_pack({}, LOOP_CODE, question="Why is my total wrong?")
        assert "Why is my total wrong?" in result["message"]

    def test_voice_command_routing(self, client):
        d = _vc(client, "make help request", code=LOOP_CODE)
        assert d["action"] == "deterministic_message"
        assert d.get("help_request") is True
        assert "teacher" in d["speech"].lower()

    def test_prepare_a_help_request_alias(self, client):
        d = _vc(client, "prepare a help request", code=LOOP_CODE)
        assert d.get("help_request") is True

    def test_does_not_shadow_the_existing_classroom_ask_teacher_flow(self, client):
        # "I need help from my teacher" contains "i need help", already owned
        # by ide_commands.py's classroom "ask my teacher for help" flow (live/
        # async notification inside a classroom -- a different concept from
        # this static pack). Confirms we deliberately did not create a second,
        # conflicting owner for that phrasing.
        d = _vc(client, "I need help from my teacher", code=LOOP_CODE)
        assert d.get("help_request") is not True


class TestProgressiveDisclosure:

    def test_details_after_program_overview(self, client):
        _vc(client, "overview")
        d = _vc(client, "details")
        assert d["action"] == "deterministic_message"
        assert "condition" in d["speech"] or "for loop" in d["speech"]

    def test_details_refuses_stale_content_after_code_changes(self, client):
        # Regression: "details" must not silently repeat facts about code that
        # has since changed underneath it -- that's presenting stale analysis
        # as if it were current, which is worse than saying nothing.
        _vc(client, "overview", code="x = 1\n")
        different_code = "def f():\n    if True:\n        return 1\n"
        d = _vc(client, "details", code=different_code)
        assert "changed" in d["speech"].lower()
        assert "no functions" not in d["speech"].lower()  # not the stale x=1 answer

    def test_details_still_works_when_code_is_unchanged(self, client):
        _vc(client, "overview", code=NESTED_CODE)
        d = _vc(client, "details", code=NESTED_CODE)
        assert "changed" not in d["speech"].lower()
        assert "for loop" in d["speech"] or "condition" in d["speech"]

    def test_exact_after_error_trace(self, client):
        _run(client, BAD_INDENT_CODE)
        _vc(client, "explain error", code=BAD_INDENT_CODE)
        d = _vc(client, "exact")
        assert d["action"] == "deterministic_message"
        assert d["speech"]

    def test_bare_more_without_context_falls_through(self, client):
        # No explanation context stored -> must not hijack the generic "more"
        # (which normally re-shows help after an onboarding/help response).
        d = _vc(client, "more")
        assert d["action"] != "disclosure_details" or True  # never our disclosure intent
        assert d.get("intent") != "disclosure_details"

    def test_session_memory_explanation_context_roundtrip(self):
        mem = session_memory.new_memory()
        assert session_memory.get_explanation_context(mem) is None
        session_memory.set_explanation_context(mem, {"kind": "x", "summary": "s",
                                                      "details": "d", "exact": "e"})
        ctx = session_memory.get_explanation_context(mem)
        assert ctx["summary"] == "s" and ctx["details"] == "d" and ctx["exact"] == "e"
        session_memory.clear_explanation_context(mem)
        assert session_memory.get_explanation_context(mem) is None


class TestAudioBlocksModeDoesNotSwallowContextResume:
    """Hostile-pass regression: 'back to code'/'where was I' must be able to
    get a learner OUT of Audio Blocks Mode. They were being caught by the
    mode's own "not a recognized block command" gate before ever reaching
    Context Resume, which defeats the entire point of the feature."""

    def test_back_to_code_escapes_audio_blocks_mode(self, client):
        _vc(client, "enter block mode", active_mode="audio_blocks")
        d = _vc(client, "back to code", active_mode="audio_blocks", cursor_line=1)
        assert d["action"] == "focus_target"
        assert d["target"] == "__editor__"
        assert "not available" not in d["speech"].lower()

    def test_where_was_i_escapes_audio_blocks_mode(self, client):
        _vc(client, "enter block mode", active_mode="audio_blocks")
        d = _vc(client, "where was i", active_mode="audio_blocks", cursor_line=1)
        assert "not available" not in d["speech"].lower()

    def test_python_structure_commands_get_a_helpful_redirect_in_audio_blocks(self, client):
        # These genuinely don't apply to the block workspace (they read the
        # Python editor's AST), so they should stay gated -- but with the same
        # helpful redirect pre-existing Python-only commands get, not the
        # generic "not available yet" dead end.
        _vc(client, "enter block mode", active_mode="audio_blocks")
        for cmd in ("overview", "mental map", "why is this line indented", "read exact line"):
            d = _vc(client, cmd, active_mode="audio_blocks", cursor_line=1)
            assert "python code mode" in d["speech"].lower(), cmd

    def test_real_audio_blocks_commands_still_work(self, client):
        d = _vc(client, "enter block mode", active_mode="audio_blocks")
        assert "not available" not in d["speech"].lower()
        d2 = _vc(client, "read block order", active_mode="audio_blocks")
        assert "workspace" in d2["speech"].lower()

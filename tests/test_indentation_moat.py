"""Regression tests for the product-differentiation indentation/"where am I" pass.

These lock in deterministic, AST-based behavior for:
  - structure_tools.cursor_context   ("where am I" -- ancestor chain + exact depth)
  - structure_tools.why_indented     ("why is this line indented")
  - structure_tools.indentation_level ("how deep am I")
  - structure_tools.what_contains    ("what block contains this line")
  - structure_tools.describe_contents ("what is inside this loop/condition/function")
  - structure_tools.explain_indentation_error (malformed-indentation fallback)
  - the /breadcrumbs route and the new voice/typed command routing

No AI provider is required or used anywhere in this file -- every assertion is against
a deterministic, exact-string or exact-value output so results cannot drift with model
behavior.
"""

import pytest

import app as app_module
from codeup.accessibility import screen_reader_bridge
from codeup.projects import structure_tools

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


class TestCursorContext:
    """The flagship 'where am I' example from the audit spec, line by line."""

    def test_deepest_line_full_ancestor_chain(self):
        ctx = structure_tools.cursor_context(NESTED_CODE, 7)
        assert ctx["found"] is True
        assert ctx["line"] == 7
        assert ctx["depth"] == 4
        kinds = [a["kind"] for a in ctx["ancestors"]]
        assert kinds == ["function", "for loop", "if", "if"]
        lines = [a["line"] for a in ctx["ancestors"]]
        assert lines == [1, 3, 4, 6]
        assert ctx["ancestors"][0]["name"] == "analyze"
        assert "n > 5" in ctx["ancestors"][2]["label"]
        assert "total > 20" in ctx["ancestors"][3]["label"]

    def test_deepest_line_speech_matches_expected_quality(self):
        speech = structure_tools.cursor_context(NESTED_CODE, 7)["speech"]
        assert speech.startswith("Line 7.")
        assert "Indentation depth 4" in speech
        assert "function analyze" in speech
        assert "the for loop" in speech and "line 3" in speech
        assert "the condition n > 5" in speech and "line 4" in speech
        assert "the condition total > 20" in speech and "line 6" in speech

    def test_top_level_line_has_zero_depth(self):
        code = "x = 1\ndef f():\n    pass\n"
        ctx = structure_tools.cursor_context(code, 1)
        assert ctx["depth"] == 0
        assert ctx["ancestors"] == []
        assert "top level" in ctx["speech"]

    def test_return_line_is_inside_function_only(self):
        ctx = structure_tools.cursor_context(NESTED_CODE, 8)
        assert ctx["depth"] == 1
        assert [a["kind"] for a in ctx["ancestors"]] == ["function"]

    def test_depth_increases_monotonically_down_the_nesting(self):
        # line 2: total=0 (in function only); line 3: "for n in numbers:" header itself
        # counts as inside the loop it opens; line 4: "if n > 5:" header counts as
        # inside that if; line 5: total += n (still inside the outer if only); line 6:
        # "if total > 20:" header counts as inside itself; line 7: print(total).
        depths = [structure_tools.cursor_context(NESTED_CODE, n)["depth"] for n in (2, 3, 4, 5, 6, 7)]
        assert depths == [1, 2, 3, 3, 4, 4]

    def test_blank_line_still_resolves_ancestor_chain(self):
        code = "def f():\n    x = 1\n\n    y = 2\n"
        ctx = structure_tools.cursor_context(code, 3)
        assert ctx["depth"] == 1
        assert ctx["ancestors"][0]["name"] == "f"

    def test_tabs_are_counted_as_indentation(self):
        code = "def f():\n\tx = 1\n"
        ctx = structure_tools.cursor_context(code, 2)
        assert ctx["indent_spaces"] == 1
        assert ctx["depth"] == 1

    def test_no_ai_hallucination_syntax_error_is_explicit(self):
        ctx = structure_tools.cursor_context("def f(:\n    pass\n", 2)
        assert ctx["found"] is False
        assert "syntax error" in ctx["speech"].lower()


class TestElifElseDisambiguation:

    CODE = (
        "def check(n):\n"
        "    if n > 5:\n"
        "        print('big')\n"
        "    elif n > 0:\n"
        "        print('small')\n"
        "    else:\n"
        "        print('non-positive')\n"
    )

    def test_if_branch(self):
        ctx = structure_tools.cursor_context(self.CODE, 3)
        assert ctx["ancestors"][-1]["kind"] == "if"
        assert ctx["ancestors"][-1]["line"] == 2

    def test_elif_branch_is_distinguished_from_if(self):
        ctx = structure_tools.cursor_context(self.CODE, 5)
        assert ctx["ancestors"][-1]["kind"] == "elif"
        assert ctx["ancestors"][-1]["line"] == 4
        assert "n > 0" in ctx["ancestors"][-1]["label"]

    def test_else_branch_locates_the_else_keyword_line_not_the_body(self):
        ctx = structure_tools.cursor_context(self.CODE, 7)
        assert ctx["ancestors"][-1]["kind"] == "else"
        assert ctx["ancestors"][-1]["line"] == 6


class TestWhyIndented:

    def test_deepest_line_names_immediate_and_parent_block(self):
        speech = structure_tools.why_indented(NESTED_CODE, 7)
        assert "16 spaces" in speech
        assert "total > 20" in speech and "line 6" in speech
        assert "n > 5" in speech and "line 4" in speech

    def test_top_level_line_is_not_indented(self):
        code = "x = 1\ndef f():\n    pass\n"
        speech = structure_tools.why_indented(code, 1)
        assert "not indented" in speech
        assert "top level" in speech

    def test_single_ancestor_has_no_parent_clause(self):
        speech = structure_tools.why_indented(NESTED_CODE, 2)
        assert "belongs to function analyze on line 1" in speech
        assert "itself is inside" not in speech


class TestIndentationLevel:

    def test_matches_ancestor_chain_depth(self):
        for line, expected_depth in ((2, 1), (4, 3), (7, 4)):
            speech = structure_tools.indentation_level(NESTED_CODE, line)
            assert f"indentation depth {expected_depth}" in speech

    def test_is_cursor_aware_not_whole_file_maximum(self):
        # Line 2 is only 1 level deep even though the file's deepest nesting is 4.
        speech = structure_tools.indentation_level(NESTED_CODE, 2)
        assert "depth 1" in speech


class TestWhatContains:

    def test_names_innermost_block_with_start_line(self):
        speech = structure_tools.what_contains(NESTED_CODE, 5)
        assert "n > 5" in speech
        assert "line 4" in speech

    def test_top_level_line_has_nothing_containing_it(self):
        code = "x = 1\ndef f():\n    pass\n"
        speech = structure_tools.what_contains(code, 1)
        assert "top level" in speech


class TestDescribeContents:

    def test_lists_statements_inside_the_cursor_block(self):
        speech = structure_tools.describe_contents(NESTED_CODE, 4)
        assert "line 5" in speech
        assert "line 6" in speech

    def test_empty_block_reported_honestly(self):
        code = "if True:\n    pass\n"
        speech = structure_tools.describe_contents(code, 1)
        assert "pass" in speech or "line 2" in speech


class TestMalformedIndentation:

    def test_names_offending_line_and_block_header(self):
        code = "def f():\nprint(1)\n"
        msg = structure_tools.explain_indentation_error(code)
        assert msg == "Line 2 should be indented because line 1 starts a function."

    def test_if_block_header_named_correctly(self):
        code = "if True:\nprint(1)\n"
        msg = structure_tools.explain_indentation_error(code)
        assert msg == "Line 2 should be indented because line 1 starts an if block."

    def test_for_loop_header_named_correctly(self):
        code = "for i in range(3):\nprint(i)\n"
        msg = structure_tools.explain_indentation_error(code)
        assert msg == "Line 2 should be indented because line 1 starts a for loop."

    def test_well_formed_code_returns_none(self):
        assert structure_tools.explain_indentation_error(NESTED_CODE) is None

    def test_unrelated_syntax_error_returns_none(self):
        assert structure_tools.explain_indentation_error("def f(:\n    pass\n") is None


class TestCodeMapHierarchy:
    """Part 5: the code map must show nested parent/child block relationships,
    not just a flat 'N loops, M functions' count."""

    def test_hierarchy_matches_nesting_structure(self):
        result = structure_tools.code_map_hierarchy(NESTED_CODE)
        top = result["hierarchy"]
        assert len(top) == 1
        assert top[0]["label"] == "function analyze"
        assert top[0]["line"] == 1 and top[0]["end_line"] == 8
        loop = top[0]["children"][0]
        assert loop["label"] == "for loop"
        cond1 = loop["children"][0]
        assert cond1["line"] == 4
        cond2 = cond1["children"][0]
        assert cond2["line"] == 6
        assert cond2["children"] == []

    def test_speech_reflects_depth_and_line_ranges(self):
        speech = structure_tools.code_map_hierarchy(NESTED_CODE)["speech"]
        assert "function analyze, lines 1 to 8" in speech
        assert "depth 1" in speech and "for loop, lines 3 to 7" in speech
        assert "depth 2" in speech
        assert "depth 3" in speech

    def test_flat_program_has_no_hierarchy(self):
        result = structure_tools.code_map_hierarchy("x = 1\nprint(x)\n")
        assert result["hierarchy"] == []
        assert "no functions" in result["speech"]

    def test_voice_command_routes_to_hierarchy(self, client):
        d = _vc(client, "read code hierarchy")
        assert d["action"] == "deterministic_message"
        assert d["intent"] == "code_hierarchy"
        assert "function analyze" in d["speech"]
        assert "depth 1" in d["speech"]


class TestGraduationMapping:
    """Part 10: 'prepare me for VS Code' must map CodeUp concepts to the matching
    VS Code feature, and frame VS Code as the next professional tool, never as a
    competitor or as inaccessible."""

    def test_mapping_covers_the_required_concepts(self):
        speech = screen_reader_bridge.graduation_mapping()
        assert "State Watch" in speech and "Variables and Watch" in speech
        assert "step narration" in speech and "debugger stepping" in speech
        assert "conditional" in speech and "conditional breakpoints" in speech
        assert "Audio Diff" in speech and "Accessible Diff Viewer" in speech
        assert "Code Map" in speech and "Outline" in speech
        assert "error explanation" in speech and "diagnostics" in speech

    def test_frames_vs_code_as_next_tool_not_competitor(self):
        speech = screen_reader_bridge.graduation_mapping().lower()
        assert "not a competitor" in speech
        assert "inaccessible" not in speech

    def test_voice_command_routes_to_graduation_mapping(self, client):
        for phrase in ("prepare me for vs code", "graduate to vs code",
                       "how does this map to vs code"):
            d = _vc(client, phrase)
            assert d["action"] == "deterministic_message", phrase
            assert d["intent"] == "graduation_mapping", phrase
            assert "State Watch" in d["speech"]


class TestBreadcrumbsRoute:

    def test_deepest_line_full_quality_response(self, client):
        d = client.post("/breadcrumbs", json={"code": NESTED_CODE, "line": 7}).get_json()
        assert d["success"] is True
        assert d["depth"] == 4
        assert len(d["trail"]) == 4
        assert [t["line"] for t in d["trail"]] == [1, 3, 4, 6]
        assert "Indentation depth 4" in d["breadcrumb"]

    def test_malformed_indentation_names_block_header(self, client):
        d = client.post("/breadcrumbs", json={"code": "def f():\nprint(1)\n", "line": 2}).get_json()
        assert d["success"] is False
        assert "line 1 starts a function" in d["message"]


class TestVoiceCommandRouting:

    def test_why_indented_command(self, client):
        d = _vc(client, "why is this line indented", cursor_line=7)
        assert d["action"] == "deterministic_message"
        assert "16 spaces" in d["speech"]

    def test_what_contains_this_line_command(self, client):
        d = _vc(client, "what contains this line", cursor_line=5)
        assert d["action"] == "deterministic_message"
        assert "n > 5" in d["speech"]

    def test_how_deep_am_i_command(self, client):
        d = _vc(client, "how deep am i", cursor_line=7)
        assert "depth 4" in d["speech"]

    def test_what_is_inside_this_condition_command(self, client):
        d = _vc(client, "what is inside this condition", cursor_line=4)
        assert d["action"] == "deterministic_message"
        assert "line 5" in d["speech"]

    def test_no_ai_provider_needed(self, client, monkeypatch):
        # Belt-and-suspenders: even if AI env vars were somehow set, these commands
        # must never reach an AI call path. Assert routing stays on the deterministic
        # branch by checking the intent tag on the response.
        d = _vc(client, "why is this line indented", cursor_line=7)
        assert d.get("intent") == "why_indented"

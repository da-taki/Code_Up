"""
Regression tests for the accessible code walkthrough and narrated structural
cue audit.

These lock in learner-visible behavior, not implementation details:

  * "walk through" / "explain this code" phrases route to the holistic
    /walkthrough explanation, while "read code" stays a literal narrate.
  * /walkthrough explains what a program does (loop, values, output) and never
    claims broken code ran.
  * /step-narration cues each learner-visible executed line at the indentation
    depth of the *source line a learner sees run* — output at the print line,
    a nested assignment at its own depth — not the line where the tracer
    happened to notice a variable change.

All tests force the deterministic (AI-unavailable) path so they are hermetic.
"""
import pytest

import app as app_module
from intent_parser import parse_intent


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


# =====================================================================
# WALKTHROUGH ROUTING — walk-through vs literal read-code
# =====================================================================

class TestWalkthroughRouting:

    @pytest.mark.parametrize("phrase", [
        "walk through code",
        "walk me through this code",
        "walk me through this program",
        "explain what this code does",
        "explain this program step by step",
    ])
    def test_walkthrough_phrases_route_to_walk_through(self, client, phrase):
        data = client.post("/voice-command", json={"text": phrase}).get_json()
        assert data["action"] == "walk_through", f"{phrase!r} -> {data['action']}"

    @pytest.mark.parametrize("phrase", ["read code", "read the code"])
    def test_read_code_stays_a_literal_narrate(self, client, phrase):
        data = client.post("/voice-command", json={"text": phrase}).get_json()
        assert data["action"] == "narrate_file"
        assert data["action"] != "walk_through"

    @pytest.mark.parametrize("phrase", [
        "read my code", "read my program", "read my whole code",
        "read all my code", "read back my code", "read my code out loud",
    ])
    def test_read_my_code_is_a_deterministic_read_back(self, client, phrase):
        # "read my code" must do a local, non-AI line-by-line read-back — never
        # the AI fix/edit it previously misrouted to.
        data = client.post("/voice-command", json={"text": phrase}).get_json()
        assert data["action"] == "read_code", f"{phrase!r} -> {data['action']}"

    def test_read_my_code_does_not_steal_plain_read_code(self, client):
        # Regression guard: broadening must not change "read code"/"read the code".
        assert client.post("/voice-command", json={"text": "read code"}).get_json()["action"] == "narrate_file"
        assert client.post("/voice-command", json={"text": "read the code"}).get_json()["action"] == "narrate_file"

    @pytest.mark.parametrize("phrase", [
        "walk through code",
        "walk me through this program",
        "explain what this code does",
    ])
    def test_walk_through_intent_is_deterministic(self, phrase):
        parsed = parse_intent(phrase)
        assert parsed["intent"] == "walk_through"
        assert parsed["confidence"] >= 0.75

    def test_slow_walkthrough_still_routes_to_mentor(self, client):
        # The narrower mentor phrase must NOT be captured by walk_through.
        data = client.post("/voice-command", json={"text": "walk me through this slowly"}).get_json()
        assert data["action"] == "mentor_chat"
        assert data.get("mode") == "slow_walkthrough"


# =====================================================================
# WALKTHROUGH ROUTE — meaningful explanation, never fake success
# =====================================================================

class TestWalkthroughRoute:

    def test_simple_loop_explains_behavior_not_raw_code(self, client):
        data = client.post("/walkthrough", json={"code": LOOP}).get_json()
        assert data["success"] is True
        expl = data["explanation"]
        low = expl.lower()
        assert "loop" in low
        # Conveys the values the loop variable takes and what is printed.
        assert "0" in expl and "1" in expl and "2" in expl
        # Not a bare recitation of the source.
        assert expl.strip() != LOOP.strip()
        assert "```" not in expl  # no code blocks in spoken text
        assert data.get("auto_speak") is True

    def test_simple_variables(self, client):
        code = 'name = "Arun"\nprint("Hello", name)\n'
        data = client.post("/walkthrough", json={"code": code}).get_json()
        assert data["success"] is True
        low = data["explanation"].lower()
        assert "name" in low
        assert "arun" in low

    def test_empty_editor_gives_clear_no_code_message(self, client):
        data = client.post("/walkthrough", json={"code": ""}).get_json()
        assert data["success"] is True
        assert "no code" in data["explanation"].lower()

    def test_broken_indentation_does_not_claim_success(self, client):
        broken = "for i in range(3):\nprint(i)\n"
        data = client.post("/walkthrough", json={"code": broken}).get_json()
        # The endpoint succeeds, but the explanation flags the defect and does
        # not pretend the program produced output.
        low = data["explanation"].lower()
        assert ("problem" in low) or ("error" in low) or ("indent" in low)
        assert "0, then 1, then 2" not in data["explanation"]


class TestDeterministicWalkthrough:

    def test_fallback_explains_loop_meaningfully(self):
        expl = app_module._deterministic_walkthrough(LOOP)
        low = expl.lower()
        assert "loop" in low
        assert "0" in expl and "1" in expl and "2" in expl
        assert "print" in low
        # Not raw-code recitation.
        assert expl.strip() != LOOP.strip()

    def test_canonical_helper_reports_values_and_output(self):
        expl = app_module._canonical_loop_walkthrough("for n in range(1, 4):\n    print(n)\n")
        assert expl is not None
        assert "1, 2, 3" in expl
        assert "1, then 2, then 3" in expl

    def test_canonical_helper_rejects_non_literal_range(self):
        # Cannot prove the values without running it -> fall back to generic.
        assert app_module._canonical_loop_walkthrough(
            "k = 3\nfor i in range(k):\n    print(i)\n"
        ) is None

    def test_canonical_helper_rejects_non_canonical_body(self):
        assert app_module._canonical_loop_walkthrough(
            "for i in range(3):\n    x = i\n    print(x)\n"
        ) is None

    def test_broken_code_reports_problem(self):
        expl = app_module._deterministic_walkthrough("for i in range(3):\nprint(i)\n")
        assert "problem" in expl.lower()


# =====================================================================
# STEP NARRATION — learner-visible cue semantics
# =====================================================================

def _output_steps(data):
    """(text, depth) for narrated steps that report program output."""
    return [
        (n, d)
        for n, d in zip(data["narration"], data["indent_depths"])
        if "prints" in n.lower()
    ]


class TestStepNarrationCues:

    def test_schema_lengths_aligned(self, client):
        # The frontend cue loop only fires when depths align 1:1 with narration.
        for code in [
            LOOP,
            "x = 1\nprint(x)\n",
            "for i in range(2):\n    if i > 0:\n        print(i)\n",
            "x = 0\nfor i in range(2):\n    x = x + i\nprint(x)\n",
        ]:
            data = client.post("/step-narration", json={"code": code}).get_json()
            assert data["success"] is True
            assert len(data["narration"]) == len(data["indent_depths"])
            assert all(isinstance(d, int) for d in data["indent_depths"])

    def test_simple_loop_output_events_map_to_depth_1(self, client):
        data = client.post("/step-narration", json={"code": LOOP}).get_json()
        assert data["success"] is True
        outs = _output_steps(data)
        assert [t for t, _ in outs] == [
            "The program prints 0.",
            "The program prints 1.",
            "The program prints 2.",
        ]
        assert all(d == 1 for _, d in outs)
        # The loop-induction variable is not narrated as a separate noisy step.
        assert not any("i becomes" in n.lower() for n in data["narration"])

    def test_flat_print_maps_to_depth_0(self, client):
        data = client.post("/step-narration", json={"code": "x = 1\nprint(x)\n"}).get_json()
        outs = _output_steps(data)
        assert len(outs) == 1
        assert outs[0] == ("The program prints 1.", 0)

    def test_nested_print_maps_to_depth_2(self, client):
        code = "for i in range(2):\n    if i > 0:\n        print(i)\n"
        data = client.post("/step-narration", json={"code": code}).get_json()
        outs = _output_steps(data)
        assert len(outs) == 1
        assert outs[0][0] == "The program prints 1."
        # Depth of the print source line (8 spaces -> 2), NOT the if line (1).
        assert outs[0][1] == 2

    def test_nested_assignment_depth_1_final_print_depth_0(self, client):
        code = "x = 0\nfor i in range(2):\n    x = x + i\nprint(x)\n"
        data = client.post("/step-narration", json={"code": code}).get_json()
        pairs = list(zip(data["narration"], data["indent_depths"]))
        nested = [(n, d) for n, d in pairs if "x changes" in n.lower()]
        assert nested and all(d == 1 for _, d in nested)
        outs = _output_steps(data)
        assert outs and outs[-1][1] == 0

    def test_error_program_no_misleading_success(self, client):
        broken = "for i in range(3):\nprint(i)\n"
        data = client.post("/step-narration", json={"code": broken}).get_json()
        assert data["success"] is False
        assert not any("prints" in n.lower() for n in data.get("narration", []))
        assert not any("execution complete" in n.lower() for n in data.get("narration", []))

    def test_runtime_error_not_narrated_as_success(self, client):
        data = client.post("/step-narration", json={"code": "x = 1 / 0\n"}).get_json()
        assert data["success"] is False
        assert not any("prints" in n.lower() for n in data.get("narration", []))

    def test_multiline_print_falls_back_to_collapsed_output(self, client):
        # Output lines won't match print executions -> safe collapsed output,
        # no per-line cue guesses.
        code = 'print("a\\nb")\n'
        data = client.post("/step-narration", json={"code": code}).get_json()
        assert data["success"] is True
        assert any(n.startswith("Output:") for n in data["narration"])

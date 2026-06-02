"""
Tests for the three audio-native features:
  1. Audio Code Map
  2. Variable Watch / Step Narration
  3. Mistake Replay / Before-vs-After

Tests cover deterministic analysis, intent parsing, API routes, edge cases,
and command routing — all without requiring AI/Groq.
"""
import pytest

import app as app_module
from intent_parser import IntentParser


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def parser():
    return IntentParser()


# =====================================================================
# AUDIO CODE MAP — Deterministic analysis
# =====================================================================

class TestEnhancedCodeMap:

    def test_simple_assignments_and_print(self):
        code = "x = 1\ny = 2\nprint(x + y)\n"
        result = app_module._enhanced_code_map(code)
        assert result["line_count"] == 3
        assert result["error"] is None
        assert len(result["assignments"]) >= 2
        assert len(result["prints"]) == 1
        assert "3 lines" in result["summary"]

    def test_one_loop(self):
        code = "total = 0\nfor i in range(3):\n    total += i\nprint(total)\n"
        result = app_module._enhanced_code_map(code)
        assert len(result["loops"]) == 1
        assert result["loops"][0]["kind"] == "for"
        assert "loop" in result["summary"].lower()

    def test_nested_loop_condition(self):
        code = (
            "for i in range(5):\n"
            "    if i > 2:\n"
            "        print(i)\n"
        )
        result = app_module._enhanced_code_map(code)
        assert len(result["loops"]) >= 1
        assert result["nesting_depth"] >= 2

    def test_condition_inside_loop_mentions_nested_assignment(self):
        code = (
            "total = 0\n"
            "for i in range(3):\n"
            "    if i > 0:\n"
            "        total = total + i\n"
            "print(total)\n"
        )
        result = app_module._enhanced_code_map(code)
        summary = result["summary"].lower()
        inside = app_module._inside_loop_summary(code).lower()
        assert "condition" in summary
        assert "assignment to total" in summary
        assert "condition" in inside
        assert "assignment to total" in inside

    def test_function_definition(self):
        code = "def greet(name):\n    print('Hello', name)\n\ngreet('Alice')\n"
        result = app_module._enhanced_code_map(code)
        assert len(result["functions"]) == 1
        assert result["functions"][0]["name"] == "greet"
        assert "greet" in result["summary"]

    def test_deepest_nesting_calculation(self):
        code = (
            "for i in range(3):\n"
            "    if i > 0:\n"
            "        for j in range(2):\n"
            "            print(i, j)\n"
        )
        depth = app_module._deepest_nesting(code)
        assert depth >= 3

    def test_inside_loop_query(self):
        code = "for i in range(3):\n    total = total + i\n    print(total)\n"
        result = app_module._inside_loop_summary(code)
        assert "inside" in result.lower() or "loop" in result.lower()
        assert "assignment" in result.lower() or "total" in result.lower()

    def test_after_loop_query(self):
        code = "for i in range(3):\n    x = i\nprint(x)\n"
        result = app_module._after_loop_summary(code)
        assert "after" in result.lower() or "print" in result.lower()

    def test_malformed_syntax_error_code(self):
        code = "for i in range(3)\n    print(i)\n"
        result = app_module._enhanced_code_map(code)
        assert result["error"] is not None
        assert "syntax" in result["summary"].lower()

    def test_empty_code(self):
        result = app_module._enhanced_code_map("")
        assert "empty" in result["summary"].lower()

    def test_list_functions_summary(self):
        code = "def foo():\n    pass\ndef bar(x):\n    return x\n"
        result = app_module._list_functions_summary(code)
        assert "foo" in result
        assert "bar" in result
        assert "2" in result

    def test_no_loop_inside_loop(self):
        code = "x = 1\nprint(x)\n"
        result = app_module._inside_loop_summary(code)
        assert "no" in result.lower() or "not find" in result.lower()

    def test_no_loop_after_loop(self):
        code = "x = 1\nprint(x)\n"
        result = app_module._after_loop_summary(code)
        assert "no loop" in result.lower() or "there is no" in result.lower()


class TestCodeMapRoute:

    def test_code_map_route_basic(self, client):
        code = "x = 1\nfor i in range(3):\n    x += i\nprint(x)\n"
        resp = client.post("/audio-code-map", json={"code": code})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "reply" in data
        assert data["auto_speak"] is True

    def test_code_map_inside_loop_query(self, client):
        code = "for i in range(3):\n    print(i)\n"
        resp = client.post("/audio-code-map", json={"code": code, "query": "inside loop"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_code_map_nesting_query(self, client):
        code = "for i in range(3):\n    if i:\n        pass\n"
        resp = client.post("/audio-code-map", json={"code": code, "query": "nesting depth"})
        data = resp.get_json()
        assert data["success"] is True
        assert "nesting" in data["reply"].lower() or "depth" in data["reply"].lower()

    def test_code_map_empty_code(self, client):
        resp = client.post("/audio-code-map", json={"code": ""})
        data = resp.get_json()
        assert data["success"] is True
        assert "empty" in data["reply"].lower()

    def test_code_map_functions_query(self, client):
        code = "def foo():\n    pass\n"
        resp = client.post("/audio-code-map", json={"code": code, "query": "list functions"})
        data = resp.get_json()
        assert "foo" in data["reply"]


# =====================================================================
# VARIABLE WATCH / STEP NARRATION
# =====================================================================

class TestWatchVariable:

    def test_add_watch(self, client):
        resp = client.post("/watch-variable", json={"variable": "total", "action": "add"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "total" in data["watched"]
        assert "watching" in data["speech"].lower()

    def test_remove_watch(self, client):
        client.post("/watch-variable", json={"variable": "total", "action": "add"})
        resp = client.post("/watch-variable", json={"variable": "total", "action": "remove"})
        data = resp.get_json()
        assert data["success"] is True
        assert "total" not in data["watched"]

    def test_clear_watch(self, client):
        client.post("/watch-variable", json={"variable": "x", "action": "add"})
        client.post("/watch-variable", json={"variable": "y", "action": "add"})
        resp = client.post("/watch-variable", json={"action": "clear"})
        data = resp.get_json()
        assert data["success"] is True
        assert data["watched"] == []

    def test_invalid_variable_name(self, client):
        resp = client.post("/watch-variable", json={"variable": "123bad", "action": "add"})
        assert resp.status_code == 400

    def test_empty_variable_name(self, client):
        resp = client.post("/watch-variable", json={"variable": "", "action": "add"})
        assert resp.status_code == 400


class TestStepNarration:

    def test_basic_narration(self, client):
        code = "total = 0\nfor i in range(3):\n    total = total + i\nprint(total)\n"
        resp = client.post("/step-narration", json={"code": code})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "narration" in data
        assert len(data["narration"]) > 0
        assert any("total" in n.lower() for n in data["narration"])

    def test_narration_with_watched_var(self, client):
        client.post("/watch-variable", json={"variable": "total", "action": "add"})
        code = "total = 0\nfor i in range(3):\n    total = total + i\nprint(total)\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is True
        # Should only show total-related changes
        narr = " ".join(data["narration"])
        assert "total" in narr.lower()

    def test_narration_runtime_error(self, client):
        code = "x = 1 / 0\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is False
        assert "error" in data.get("error", "").lower() or "error" in " ".join(data.get("narration", [])).lower()
        assert "sandbox_runner.py" not in data.get("error", "")
        assert "<path>" not in data.get("error", "")

    def test_narration_sandbox_error_is_sanitized(self, client):
        code = "import os\nprint(os.listdir('.'))\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is False
        assert "not allowed" in data.get("error", "").lower()
        assert "sandbox_runner.py" not in data.get("error", "")
        assert "<path>" not in data.get("error", "")

    def test_large_output_is_bounded(self, client):
        code = "for i in range(1000):\n    print('x' * 100)\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["output"]) <= app_module.MAX_NARRATION_OUTPUT_SIZE + 100
        assert "Output truncated" in data["output"]

    def test_narration_empty_code(self, client):
        resp = client.post("/step-narration", json={"code": ""})
        assert resp.status_code == 400

    def test_narration_syntax_error(self, client):
        code = "for i in range(3)\n    print(i)\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is False

    def test_narration_function_call(self, client):
        code = "def double(v):\n    return v * 2\n\nresult = double(4)\nprint(result)\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is True
        narr = " ".join(data["narration"])
        assert "result" in narr.lower() or "double" in narr.lower()

    def test_narration_safe_value_representation(self, client):
        code = "x = list(range(100))\nprint(len(x))\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is True


class TestConditionalAudioBreakpoints:

    def test_add_pause_why_and_continue_from_trace(self, client):
        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-main")
        resp = client.post("/audio-breakpoints", json={
            "action": "add",
            "condition": "total becomes greater than 10",
        })
        data = resp.get_json()
        assert data["success"] is True
        assert data["breakpoint"]["variable"] == "total"
        assert data["breakpoint"]["operator"] == ">"
        assert data["breakpoint"]["threshold"] == 10

        code = "total = 0\nfor i in range(10):\n    total = total + i\nprint(total)\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is True
        assert data["paused"] is True
        assert data["output"] == ""
        assert data["pause"]["change"]["previous_display"] == "10"
        assert data["pause"]["change"]["current_display"] == "15"
        speech = data["speech"].lower()
        assert "i is 5" in speech
        assert "total changed from 10 to 15" in speech

        why = client.post("/audio-breakpoints", json={"action": "why"}).get_json()
        assert why["success"] is True
        assert "total changed from 10 to 15" in why["speech"].lower()

        continued = client.post("/audio-breakpoints", json={"action": "continue"}).get_json()
        assert continued["success"] is True
        assert continued["continued"] is True
        assert continued["output"] == "45"
        assert "Output: 45" in continued["narration_text"]
        assert "Execution complete" in continued["narration_text"]

    def test_no_match_uses_normal_step_narration(self, client):
        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-no-match")
        client.post("/audio-breakpoints", json={
            "action": "add",
            "condition": "total greater than 100",
        })
        code = "total = 0\nfor i in range(3):\n    total = total + i\nprint(total)\n"
        data = client.post("/step-narration", json={"code": code}).get_json()
        assert data["success"] is True
        assert data.get("paused") is not True
        assert data["output"] == "3"
        assert any("Execution complete" in step for step in data["narration"])

    def test_new_run_clears_stale_pause_state(self, client):
        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-stale")
        client.post("/audio-breakpoints", json={
            "action": "add",
            "condition": "total greater than 10",
        })
        hit_code = "total = 0\nfor i in range(10):\n    total = total + i\nprint(total)\n"
        paused = client.post("/step-narration", json={"code": hit_code}).get_json()
        assert paused["paused"] is True

        miss_code = "total = 0\nfor i in range(3):\n    total = total + i\nprint(total)\n"
        missed = client.post("/step-narration", json={"code": miss_code}).get_json()
        assert missed.get("paused") is not True
        why = client.post("/audio-breakpoints", json={"action": "why"}).get_json()
        assert why["success"] is False
        assert why["active"] is False

    def test_equals_breakpoint_pauses_on_actual_value(self, client):
        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-equals")
        client.post("/audio-breakpoints", json={
            "action": "add",
            "condition": "score equals 6",
        })
        code = (
            "score = 0\n"
            "for number in range(5):\n"
            "    if number % 2 == 0:\n"
            "        score = score + number\n"
            "print(score)\n"
        )
        data = client.post("/step-narration", json={"code": code}).get_json()
        assert data["success"] is True
        assert data["paused"] is True
        assert data["pause"]["change"]["variable"] == "score"
        assert data["pause"]["change"]["current_display"] == "6"
        assert "score changed" in data["speech"].lower()

    def test_list_clear_and_session_isolation(self, client):
        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-one")
        client.post("/audio-breakpoints", json={
            "action": "add",
            "condition": "total > 10",
        })
        listed = client.post("/audio-breakpoints", json={"action": "list"}).get_json()
        assert listed["success"] is True
        assert len(listed["breakpoints"]) == 1

        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-two")
        other = client.post("/audio-breakpoints", json={"action": "list"}).get_json()
        assert other["breakpoints"] == []

        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-one")
        cleared = client.post("/audio-breakpoints", json={"action": "clear"}).get_json()
        assert cleared["success"] is True
        listed = client.post("/audio-breakpoints", json={"action": "list"}).get_json()
        assert listed["breakpoints"] == []

    def test_rejects_unsafe_or_unsupported_conditions(self, client):
        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-invalid")
        unsafe = client.post("/audio-breakpoints", json={
            "action": "add",
            "condition": "total > __import__('os')",
        })
        assert unsafe.status_code == 400
        dotted = client.post("/audio-breakpoints", json={
            "action": "add",
            "variable": "total.value",
            "operator": ">",
            "threshold": 10,
        })
        assert dotted.status_code == 400

    def test_breakpoint_limit_is_bounded(self, client):
        client.set_cookie(app_module.SESSION_COOKIE_NAME, "audio-bp-limit")
        for idx in range(app_module.MAX_AUDIO_BREAKPOINTS):
            resp = client.post("/audio-breakpoints", json={
                "action": "add",
                "variable": f"v{idx}",
                "operator": ">",
                "threshold": idx,
            })
            assert resp.status_code == 200
        overflow = client.post("/audio-breakpoints", json={
            "action": "add",
            "variable": "too_many",
            "operator": ">",
            "threshold": 99,
        })
        assert overflow.status_code == 400


# =====================================================================
# MISTAKE REPLAY / BEFORE-VS-AFTER
# =====================================================================

class TestMistakeReplay:

    def test_no_previous_state(self, client):
        resp = client.post("/mistake-replay", json={"code": "print(1)"})
        data = resp.get_json()
        assert data["success"] is False
        assert "do not have" in data["reply"].lower() or "no" in data["reply"].lower()

    def test_indentation_correction(self, client):
        broken = "total = 0\nfor i in range(3):\ntotal = total + i\nprint(total)\n"
        fixed = "total = 0\nfor i in range(3):\n    total = total + i\nprint(total)\n"

        session_id = "mistake-test-indent"
        client.set_cookie(app_module.SESSION_COOKIE_NAME, session_id)

        # Simulate error then success
        client.post("/run", json={"code": broken})
        client.post("/run", json={"code": fixed})

        resp = client.post("/mistake-replay", json={"code": fixed, "query": "compare"})
        data = resp.get_json()
        assert data["success"] is True
        assert "reply" in data
        assert len(data["reply"]) > 0
        assert "indent" in data["reply"].lower()
        assert "block" in data["reply"].lower() or "scope" in data["reply"].lower()
        assert data["diff"]["structural_changes"]

    def test_changed_operator(self, client):
        broken = "x = 5\ny = x / 0\nprint(y)\n"
        fixed = "x = 5\ny = x + 3\nprint(y)\n"

        session_id = "mistake-test-operator"
        client.set_cookie(app_module.SESSION_COOKIE_NAME, session_id)

        client.post("/run", json={"code": broken})
        client.post("/run", json={"code": fixed})

        resp = client.post("/mistake-replay", json={"code": fixed, "query": "changed lines"})
        data = resp.get_json()
        assert data["success"] is True
        assert "diff" in data

    def test_unparsable_prior_code(self, client):
        broken = "for i in range(3)\n    print(i)\n"
        fixed = "for i in range(3):\n    print(i)\n"

        session_id = "mistake-test-unparsable"
        client.set_cookie(app_module.SESSION_COOKIE_NAME, session_id)

        client.post("/run", json={"code": broken})
        client.post("/run", json={"code": fixed})

        resp = client.post("/mistake-replay", json={"code": fixed})
        data = resp.get_json()
        assert data["success"] is True

    def test_compute_code_diff(self):
        before = "total = 0\nfor i in range(3):\ntotal = total + i\nprint(total)\n"
        after = "total = 0\nfor i in range(3):\n    total = total + i\nprint(total)\n"
        diff = app_module._compute_code_diff(before, after)
        assert diff["total_changes"] >= 1
        assert len(diff["changes"]) >= 1
        assert diff["changes"][0]["before_indent"] == 0
        assert diff["changes"][0]["after_indent"] == 4
        assert diff["structural_changes"]

    def test_deterministic_explanation(self):
        before = "x = 1\n"
        after = "x = 2\n"
        diff = app_module._compute_code_diff(before, after)
        explanation = app_module._deterministic_mistake_explanation(before, after, diff)
        assert "changed" in explanation.lower()
        assert len(explanation) > 0

    def test_no_diff(self):
        code = "x = 1\n"
        diff = app_module._compute_code_diff(code, code)
        assert diff["total_changes"] == 0
        explanation = app_module._deterministic_mistake_explanation(code, code, diff)
        assert "no differences" in explanation.lower()


# =====================================================================
# INTENT PARSER — New commands
# =====================================================================

class TestCodeMapIntents:

    @pytest.mark.parametrize("text,expected_intent", [
        ("code map", "code_map"),
        ("give me a code map", "code_map"),
        ("read the structure", "code_map"),
        ("what is inside the loop", "inside_loop"),
        ("what comes after the loop", "after_loop"),
        ("how deeply nested am i", "nesting_depth"),
        ("list my functions", "list_functions"),
        ("where am i in the program", "where_in_program"),
    ])
    def test_code_map_intents(self, parser, text, expected_intent):
        result = parser.parse(text)
        assert result["intent"] == expected_intent, f"'{text}' -> {result['intent']} (expected {expected_intent})"


class TestWatchIntents:

    @pytest.mark.parametrize("text,expected_intent,expected_var", [
        ("track total", "watch_var", "total"),
        ("track variable total", "watch_var", "total"),
        ("watch variable x", "watch_variable", "x"),
        ("stop watching total", "stop_watching", "total"),
        ("unwatch x", "stop_watching", "x"),
    ])
    def test_watch_intents(self, parser, text, expected_intent, expected_var):
        result = parser.parse(text)
        assert result["intent"] == expected_intent, f"'{text}' -> {result['intent']} (expected {expected_intent})"
        assert result["slots"].get("variable") == expected_var

    @pytest.mark.parametrize("text,expected_intent", [
        ("clear watched variables", "clear_watched"),
        ("run with step narration", "step_narration"),
        ("step narration", "step_narration"),
        ("read variable values", "read_var_values"),
        ("what changed in this step", "what_changed_step"),
        ("only announce changes", "only_announce_changes"),
    ])
    def test_narration_intents(self, parser, text, expected_intent):
        result = parser.parse(text)
        assert result["intent"] == expected_intent, f"'{text}' -> {result['intent']} (expected {expected_intent})"


class TestConditionalBreakpointIntents:

    @pytest.mark.parametrize("text,expected_condition", [
        ("pause when total becomes greater than 10", "total becomes greater than 10"),
        ("stop when score equals 6", "score equals 6"),
        ("break when i reaches 3", "i reaches 3"),
        ("set a conditional breakpoint when total <= 20", "total <= 20"),
    ])
    def test_set_audio_breakpoint_intents(self, parser, text, expected_condition):
        result = parser.parse(text)
        assert result["intent"] == "set_audio_breakpoint"
        assert result["slots"]["condition"] == expected_condition

    @pytest.mark.parametrize("text,expected_intent", [
        ("list breakpoints", "list_audio_breakpoints"),
        ("show conditional audio breakpoints", "list_audio_breakpoints"),
        ("why did it pause", "why_audio_breakpoint"),
        ("explain the pause", "why_audio_breakpoint"),
    ])
    def test_audio_breakpoint_control_intents(self, parser, text, expected_intent):
        result = parser.parse(text)
        assert result["intent"] == expected_intent


class TestMistakeReplayIntents:

    @pytest.mark.parametrize("text,expected_intent", [
        ("compare before and after", "compare_before_after"),
        ("replay my mistake", "replay_mistake"),
        ("why does the fixed version work", "why_fixed_works"),
        ("show changed lines", "show_changed_lines"),
    ])
    def test_mistake_intents(self, parser, text, expected_intent):
        result = parser.parse(text)
        assert result["intent"] == expected_intent, f"'{text}' -> {result['intent']} (expected {expected_intent})"


# =====================================================================
# INTENT COLLISION — New intents must not break existing ones
# =====================================================================

class TestIntentNonCollision:

    @pytest.mark.parametrize("text,expected_intent", [
        ("run", "run"),
        ("analyze code", "analyze"),
        ("fix code", "fix"),
        ("tell the story", "story_mode"),
        ("read outline", "read_outline"),
        ("help", "help"),
        ("execution story", "story_mode"),
        ("watch variable counter", "watch_variable"),
    ])
    def test_existing_intents_preserved(self, parser, text, expected_intent):
        result = parser.parse(text)
        assert result["intent"] == expected_intent, f"'{text}' -> {result['intent']} (expected {expected_intent})"


# =====================================================================
# VOICE COMMAND ROUTING — New intents route correctly
# =====================================================================

class TestVoiceCommandRouting:

    def test_code_map_voice(self, client):
        resp = client.post("/voice-command", json={"text": "code map"})
        data = resp.get_json()
        assert data["action"] == "code_map"

    def test_inside_loop_voice(self, client):
        resp = client.post("/voice-command", json={"text": "what is inside the loop"})
        data = resp.get_json()
        assert data["action"] == "code_map"
        assert data.get("query") == "inside loop"

    def test_step_narration_voice(self, client):
        resp = client.post("/voice-command", json={"text": "run with step narration"})
        data = resp.get_json()
        assert data["action"] == "step_narration"

    def test_track_variable_voice(self, client):
        resp = client.post("/voice-command", json={"text": "track total"})
        data = resp.get_json()
        assert data["action"] == "watch_var"
        assert data.get("variable") == "total"

    def test_clear_watched_voice(self, client):
        resp = client.post("/voice-command", json={"text": "clear watched variables"})
        data = resp.get_json()
        assert data["action"] == "clear_watched"

    def test_compare_before_after_voice(self, client):
        resp = client.post("/voice-command", json={"text": "compare before and after"})
        data = resp.get_json()
        assert data["action"] == "compare_before_after"

    def test_replay_mistake_voice(self, client):
        resp = client.post("/voice-command", json={"text": "replay my mistake"})
        data = resp.get_json()
        assert data["action"] == "replay_mistake"

    def test_why_fixed_works_voice(self, client):
        resp = client.post("/voice-command", json={"text": "why does the fixed version work"})
        data = resp.get_json()
        assert data["action"] == "why_fixed_works"

    def test_set_audio_breakpoint_voice(self, client):
        resp = client.post("/voice-command", json={"text": "pause when total becomes greater than 10"})
        data = resp.get_json()
        assert data["action"] == "set_audio_breakpoint"
        assert data["condition"] == "total becomes greater than 10"

    def test_list_audio_breakpoints_voice(self, client):
        resp = client.post("/voice-command", json={"text": "list breakpoints"})
        data = resp.get_json()
        assert data["action"] == "list_audio_breakpoints"

    def test_why_audio_breakpoint_voice(self, client):
        resp = client.post("/voice-command", json={"text": "why did it pause"})
        data = resp.get_json()
        assert data["action"] == "why_audio_breakpoint"


# =====================================================================
# SANDBOX SAFETY — existing protections still intact
# =====================================================================

class TestSandboxSafety:

    def test_step_narration_sandbox_blocks_imports(self, client):
        code = "import os\nprint(os.listdir('.'))\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is False or "not allowed" in data.get("error", "").lower() or "not allowed" in " ".join(data.get("narration", [])).lower()

    def test_step_narration_timeout(self, client):
        code = "while True:\n    pass\n"
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is False or "timeout" in data.get("error", "").lower() or "timed out" in " ".join(data.get("narration", [])).lower()

    def test_step_narration_rejects_non_python(self, client):
        resp = client.post("/step-narration", json={"code": "<html><body>hi</body></html>"})
        assert resp.status_code == 400


# =====================================================================
# DEMO PROGRAMS — Feature correctness on spec examples
# =====================================================================

class TestDemoPrograms:

    def test_demo_indentation_scope(self, client):
        """Demo case 1: indentation/scope correction."""
        broken = "total = 0\nfor i in range(3):\ntotal = total + i\nprint(total)\n"
        corrected = "total = 0\nfor i in range(3):\n    total = total + i\nprint(total)\n"

        # Code map on broken code
        resp = client.post("/audio-code-map", json={"code": broken})
        data = resp.get_json()
        assert data["success"] is True

        # Code map on corrected code
        resp = client.post("/audio-code-map", json={"code": corrected})
        data = resp.get_json()
        assert data["success"] is True
        assert "loop" in data["reply"].lower()

        # Step narration on corrected code
        resp = client.post("/step-narration", json={"code": corrected})
        data = resp.get_json()
        assert data["success"] is True
        narr = " ".join(data["narration"]).lower()
        assert "total" in narr

    def test_demo_conditional_inside_loop(self, client):
        """Demo case 2: conditional inside loop."""
        code = (
            "score = 0\n"
            "for number in range(5):\n"
            "    if number % 2 == 0:\n"
            "        score = score + number\n"
            "print(score)\n"
        )
        # Code map
        resp = client.post("/audio-code-map", json={"code": code})
        data = resp.get_json()
        assert data["success"] is True
        reply = data["reply"].lower()
        assert "loop" in reply

        # Nesting depth
        resp = client.post("/audio-code-map", json={"code": code, "query": "nesting depth"})
        data = resp.get_json()
        assert "2" in data["reply"] or "two" in data["reply"].lower()

    def test_demo_function_call(self, client):
        """Demo case 3: function call."""
        code = (
            "def double(value):\n"
            "    return value * 2\n"
            "\n"
            "result = double(4)\n"
            "print(result)\n"
        )
        # Code map identifies function
        resp = client.post("/audio-code-map", json={"code": code})
        data = resp.get_json()
        assert data["success"] is True
        assert "double" in data["reply"].lower()

        # Step narration
        resp = client.post("/step-narration", json={"code": code})
        data = resp.get_json()
        assert data["success"] is True
        narr = " ".join(data["narration"]).lower()
        assert "result" in narr or "8" in narr

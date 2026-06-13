"""Blind Debugger Mode (NAB value sprint, Feature 1).

A guided, teacher-style debugging response built from deterministic facts +
the staged hint engine. Never edits code. Also guards that the new routing
does not shadow Sprint 1 / Sprint 2 commands. No cloud AI is involved.
"""
import pytest

import app as app_module
import debug_teacher

LOOP_OK = "for i in range(3):\n    print(i)\n"
LOOP_BAD_INDENT = "for i in range(3):\nprint(i)\n"
NAME_ERR_CODE = "print(total)\n"
MISSING_COLON = "for i in range(3)\n    print(i)\n"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestBuilder:

    def test_empty_code_returns_no_code_message(self):
        r = debug_teacher.build_blind_debugger_response("", {}, None)
        assert r["problem_type"] == "empty"
        assert "no code" in r["message"].lower()
        assert r["next_commands"] == []

    def test_success_gives_debugging_habit(self):
        r = debug_teacher.build_blind_debugger_response(LOOP_OK, {}, {"ok": True})
        assert r["problem_type"] == "none"
        assert "runs successfully" in r["message"].lower()
        assert "predict" in r["message"].lower() or "habit" in r["message"].lower()

    def test_indentation_error_explains_indentation_and_line(self):
        r = debug_teacher.build_blind_debugger_response(
            LOOP_BAD_INDENT, {}, {"error": "IndentationError: expected an indented block on line 2"})
        assert r["problem_type"] == "indentation"
        assert r["line"] == 2
        assert "indentation" in r["message"].lower()
        assert "loop" in r["concepts"]

    def test_name_error_explains_missing_variable(self):
        r = debug_teacher.build_blind_debugger_response(
            NAME_ERR_CODE, {"last_run_error": "NameError: name 'total' is not defined"}, None)
        assert r["problem_type"] == "name_error"
        assert "total" in r["message"]
        assert "variable" in r["concepts"]

    def test_syntax_error_explains_syntax(self):
        r = debug_teacher.build_blind_debugger_response(
            MISSING_COLON, {}, {"error": "SyntaxError: expected ':'"})
        assert r["problem_type"] == "syntax"
        assert "syntax" in r["message"].lower()

    def test_includes_next_commands(self):
        r = debug_teacher.build_blind_debugger_response(
            LOOP_BAD_INDENT, {}, {"error": "IndentationError: x on line 2"})
        assert r["next_commands"] == ["give me a bigger hint", "show me the answer", "replay the mistake"]

    def test_does_not_edit_code(self):
        r = debug_teacher.build_blind_debugger_response(
            LOOP_BAD_INDENT, {}, {"error": "IndentationError on line 2"})
        # No code-editing fields are ever returned.
        assert "ai_action" not in r
        assert "code" not in r

    def test_verbosity_affects_length(self):
        concise = debug_teacher.build_blind_debugger_response(
            LOOP_BAD_INDENT, {}, {"error": "IndentationError on line 2"}, "concise")["message"]
        normal = debug_teacher.build_blind_debugger_response(
            LOOP_BAD_INDENT, {}, {"error": "IndentationError on line 2"}, "normal")["message"]
        assert len(normal) >= len(concise)


class TestTeacherFirst:
    """Problem 4 — debug like a teacher BEFORE offering staged hints."""

    def test_indentation_gives_explanation_and_likely_fix(self):
        r = debug_teacher.build_blind_debugger_response(
            LOOP_BAD_INDENT, {}, {"error": "IndentationError: expected an indented block on line 2"})
        low = r["message"].lower()
        assert "indentation error" in low
        assert "likely fix" in low and "indent" in low
        assert "four spaces" in low  # concrete fix
        # The why-it-works explanation is present.
        assert "loop" in low

    def test_nameerror_explains_and_gives_likely_fix(self):
        r = debug_teacher.build_blind_debugger_response(
            NAME_ERR_CODE, {"last_run_error": "NameError: name 'total' is not defined"}, None)
        low = r["message"].lower()
        assert "name error" in low and "total" in r["message"]
        assert "likely fix" in low and "total = 0" in r["message"]

    def test_syntaxerror_explains_and_gives_likely_fix(self):
        r = debug_teacher.build_blind_debugger_response(
            MISSING_COLON, {}, {"error": "SyntaxError: expected ':'"})
        low = r["message"].lower()
        assert "syntax error" in low
        assert "likely fix" in low and "colon" in low

    def test_hint_commands_come_after_the_explanation(self):
        # The optional "bigger hint" offer must appear AFTER the likely fix, never
        # before the learner has been taught the problem.
        r = debug_teacher.build_blind_debugger_response(
            LOOP_BAD_INDENT, {}, {"error": "IndentationError: expected an indented block on line 2"})
        low = r["message"].lower()
        assert "bigger hint" in low
        assert low.index("likely fix") < low.index("bigger hint")
        assert low.index("indentation error") < low.index("bigger hint")

    def test_normal_response_is_not_just_a_hint_wrap(self):
        # Regression: the old debugger led with "Small hint: ..." — the teacher
        # response must instead carry a real fix + reasoning.
        r = debug_teacher.build_blind_debugger_response(
            LOOP_BAD_INDENT, {}, {"error": "IndentationError on line 2"})
        assert "small hint:" not in r["message"].lower()
        assert "likely fix" in r["message"].lower()

    def test_success_explains_behavior_and_suggests_a_test(self):
        r = debug_teacher.build_blind_debugger_response(LOOP_OK, {}, {"ok": True})
        low = r["message"].lower()
        assert "runs successfully" in low
        assert "predict" in low or "test" in low or "habit" in low


class TestRoute:

    def test_debug_like_a_teacher_routes(self, client):
        d = client.post("/voice-command", json={
            "text": "debug this like a teacher", "code": LOOP_BAD_INDENT,
            "error": "IndentationError: expected an indented block on line 2"}).get_json()
        assert d["action"] == "deterministic_message"
        assert d.get("blind_debugger") is True
        assert "indentation" in d["message"].lower()
        assert "give me a bigger hint" in d["next_commands"]

    def test_debug_does_not_edit_or_run(self, client):
        d = client.post("/voice-command", json={
            "text": "debug my code", "code": LOOP_BAD_INDENT,
            "error": "IndentationError on line 2"}).get_json()
        assert d["action"] == "deterministic_message"
        assert "ai_action" not in d

    def test_does_not_call_cloud_ai(self, client, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("blind debugger must not call cloud AI")
        monkeypatch.setattr(app_module, "call_gemini", boom)
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", boom)
        d = client.post("/voice-command", json={
            "text": "why is my code failing", "code": LOOP_BAD_INDENT,
            "error": "IndentationError on line 2"}).get_json()
        assert d.get("blind_debugger") is True


# =====================================================================
# Regression guards — NAB routing must not shadow Sprint 1 / Sprint 2.
# =====================================================================

class TestNabRegression:

    def test_onboarding_still_works(self, client):
        d = client.post("/voice-command", json={"text": "what can I do here"}).get_json()
        assert d.get("onboarding") is True

    def test_insert_print_still_works(self, client):
        d = client.post("/voice-command", json={"text": "insert print hello"}).get_json()
        assert d["action"] == "conversational_edit"

    def test_generation_still_works(self, client):
        d = client.post("/voice-command", json={"text": "write a program for first five even numbers"}).get_json()
        assert d["action"] == "generate_code"

    def test_project_report_still_sprint1(self, client):
        d = client.post("/voice-command", json={"text": "make a project report"}).get_json()
        assert d["action"] == "project_report"

    def test_recap_still_sprint1(self, client):
        d = client.post("/voice-command", json={"text": "what did I learn today"}).get_json()
        assert d.get("recap") is True

    def test_structure_still_sprint2(self, client):
        d = client.post("/voice-command", json={"text": "summarize structure", "code": LOOP_OK}).get_json()
        assert d["action"] == "deterministic_message"
        assert "structure" in d

    def test_navigation_still_sprint2(self, client):
        d = client.post("/voice-command", json={"text": "go to the loop", "code": LOOP_OK}).get_json()
        assert d["action"] == "navigate_code"

    def test_small_hint_still_sprint2(self, client):
        d = client.post("/voice-command", json={
            "text": "give me a small hint", "code": LOOP_BAD_INDENT,
            "error": "IndentationError on line 2"}).get_json()
        assert d["action"] == "deterministic_message"
        assert "hint_level" in d

    def test_replay_still_sprint2(self, client):
        d = client.post("/voice-command", json={"text": "replay the mistake"}).get_json()
        assert d["action"] == "deterministic_message"
        assert d.get("error_replay") is True

    def test_openvino_route_unaffected(self, client):
        d = client.post("/openvino-intent-demo", json={"text": "insert print hello"}).get_json()
        assert d["intent"] == "insert_code"

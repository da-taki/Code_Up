import ast
from pathlib import Path

import pytest
from codeup.learning import accessible_learning as learning
from app import app
from codeup.commands.intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def voice(client, text, **payload):
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


def test_lesson_catalog_and_every_success_check():
    assert [lesson["title"] for lesson in learning.LESSONS] == [
        "Hello world and spoken output",
        "Variables and values",
        "Strings and numbers",
        "Conditions",
        "Loops",
        "Lists",
        "Functions",
        "Debugging errors",
        "Step narration and tracing",
        "Multi-file project basics",
        "Screen reader workflow",
        "Moving toward VS Code",
    ]
    project = {
        "files": {
            "main.py": "from helpers import greet",
            "helpers.py": "def greet(): pass",
        }
    }
    for lesson in learning.LESSONS:
        assert learning._lesson_passes(
            lesson,
            lesson["code"]
            if lesson["check"] != "valid"
            else 'message="Hello"\nprint(message)\n',
            project,
        ), lesson["title"]


def test_learning_path_navigation_and_check(client):
    started = voice(client, "start learning path")
    assert started["action"] == "conversational_edit"
    assert started["lesson"] == 1
    assert (
        voice(client, "check lesson", code='print("Hello")\n')["lesson_passed"] is True
    )
    assert voice(client, "next lesson")["lesson"] == 2
    assert voice(client, "previous lesson")["lesson"] == 1
    assert "Hello world" in voice(client, "repeat lesson")["speech"]
    assert "12 lessons" in voice(client, "list lessons")["speech"]
    assert voice(client, "give lesson hint")["action"] == "deterministic_message"


def test_all_block_exercises_load_and_operations_are_safe(client):
    for index, (title, expected) in enumerate(learning.BLOCK_EXERCISES, 1):
        loaded = voice(client, f"start block practice {index}")
        assert title in loaded["speech"]
        order = voice(client, "read block order")["speech"]
        assert "indentation" in order
        assert voice(client, "read block 999")["action"] == "deterministic_message"
        if len(expected) > 2:
            assert voice(client, "check block order")["block_passed"] is False
            voice(client, f"move block {expected[2][0]} down")
        assert voice(client, "check block order")["block_passed"] is True
        converted = voice(client, "convert blocks to code", code="keep = True")
        assert converted["action"] == "conversational_edit"
        ast.parse(converted["ai_action"]["code"])
    voice(client, "start block practice 3")
    voice(client, "indent block 1")
    assert "indentation 1" in voice(client, "read block 1")["speech"]
    voice(client, "outdent block 1")
    voice(client, "outdent block 1")
    assert "indentation 0" in voice(client, "read block 1")["speech"]


def test_hint_ladder_is_request_only_and_integrates(client):
    voice(client, "start learning path")
    small = voice(client, "give me a small hint")["speech"]
    bigger = voice(client, "give me a bigger hint")["speech"]
    assert len(bigger) > len(small)
    assert voice(client, "repeat hint")["speech"] == bigger
    assert "Step 1" in voice(client, "show solution steps")["speech"]
    assert "cleared" in voice(client, "stop hints")["speech"]
    assert (
        "hint" not in voice(client, "where am I in the learning path")["speech"].lower()
    )


def test_navigation_mode_and_structural_navigation(client):
    code = "# TODO first\ndef one():\n    pass\n\nfor i in range(2):\n    print(i)\n"
    assert "on" in voice(client, "navigation mode on")["speech"]
    assert voice(client, "next symbol", code=code, cursor_line=1)["line"] == 2
    assert voice(client, "next loop", code=code, cursor_line=1)["line"] == 5
    assert (
        voice(client, "next todo", code=code, cursor_line=1)["action"]
        == "deterministic_message"
    )
    assert (
        "one" in voice(client, "read current scope", code=code, cursor_line=3)["speech"]
    )
    assert (
        "no next error"
        in voice(client, "next error", code=code, cursor_line=1)["speech"].lower()
    )
    assert "off" in voice(client, "navigation mode off")["speech"]
    assert "Alt Shift R" in voice(client, "show keyboard shortcuts")["speech"]


def csv_project():
    return {
        "files": {"scores.csv": "name,score,age\nAsha,80,12\nBen,90,13\nCara,,12\n"},
        "active_file": "scores.csv",
    }


def test_accessible_csv_stats_chart_and_sonification(client):
    project = csv_project()
    assert "3 rows" in voice(client, "summarize csv", project=project)["speech"]
    assert "score" in voice(client, "list csv columns", project=project)["speech"]
    assert "Asha" in voice(client, "read csv row 1", project=project)["speech"]
    assert "90" in voice(client, "find highest in score", project=project)["speech"]
    assert "85" in voice(client, "average score", project=project)["speech"]
    assert (
        "does not infer causation"
        in voice(client, "compare columns score and age", project=project)["speech"]
    )
    assert "line chart" in voice(client, "describe chart", project=project)["speech"]
    sonify = voice(client, "sonify column score", project=project)
    assert sonify["action"] == "accessible_data_sonify" and sonify["values"] == [
        80.0,
        90.0,
    ]
    assert voice(client, "stop sonification")["action"] == "stop_data_sonification"
    assert "No CSV" in voice(client, "summarize csv", project={"files": {}})["speech"]
    assert "not found" in voice(client, "average unknown", project=project)["speech"]


def test_teacher_report_privacy_counts_and_export(client):
    assert "on" in voice(client, "teacher mode on")["speech"]
    voice(client, "start learning path")
    voice(client, "check lesson", code="x =")
    report = voice(client, "generate student report", code="secret_value = 42")
    assert "# CodeUp Teacher Report" in report["report"]
    assert "secret_value" not in report["report"]
    voice(client, "include code in teacher report")
    included = voice(client, "generate lesson report", code="visible_value = 42")[
        "report"
    ]
    assert "visible_value" in included
    redacted = voice(
        client, "generate lesson report", code="API_KEY = 'do-not-report'"
    )["report"]
    assert "do-not-report" not in redacted
    assert "Redacted possible secret" in redacted
    voice(client, "exclude code from teacher report")
    exported = voice(client, "export teacher report", code="hidden = True")
    assert exported["action"] == "export_teacher_report"
    assert exported["filename"] == "CodeUp_Teacher_Report.md"
    assert "hidden = True" not in exported["report"]


def test_beginner_style_max_three_and_show_more(client):
    code = (
        "a = 1\nlist = []\nstr = 'x'\nflag = True\nprint(flag == True)\ndef unused():\n    pass\n"
        + "x = '"
        + "z" * 100
        + "'\n"
    )
    result = voice(client, "check beginner style", code=code)
    assert len(result["style_issues"]) == 3
    assert "more" in result["speech"]
    assert len(voice(client, "show more style issues", code=code)["style_issues"]) > 3
    assert (
        "No beginner style issues"
        in voice(
            client, "check beginner style", code="for i in range(3):\n    print(i)\n"
        )["speech"]
    )


def test_all_error_challenges_fail_then_pass_and_hint_solution(client):
    commands = [
        "start error practice",
        "practice indentation errors",
        "practice name errors",
        "practice type errors",
        "practice syntax errors",
    ]
    for command in commands:
        loaded = voice(client, command)
        assert loaded["action"] == "conversational_edit"
        broken = loaded["ai_action"]["code"]
        assert (
            voice(client, "check error fix", code=broken)["error_fix_passed"] is False
        )
        assert voice(client, "give error hint")["speech"]
        solution = voice(client, "show error solution")
        fixed = solution["ai_action"]["code"]
        assert voice(client, "check error fix", code=fixed)["error_fix_passed"] is True
    voice(client, "start error practice")
    seen = []
    for _ in range(len(learning.ERROR_CHALLENGES)):
        nxt = voice(client, "next error challenge")
        seen.append(nxt["speech"])
    assert len(set(seen)) == len(learning.ERROR_CHALLENGES)


def test_accessible_tools_page_and_commands(client):
    response = client.get("/accessible-coding-tools")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for phrase in (
        "CodeUp's role",
        "Quorum",
        "Screen readers",
        "Professional editor pathway",
        "Suggested pathway",
        "NVDA",
        "JAWS",
        "VoiceOver",
        "Orca",
    ):
        assert phrase in html
    assert "partnership" not in html.lower() and "certified" not in html.lower()
    assert (
        voice(client, "open accessible coding tools")["action"]
        == "open_accessible_tools"
    )
    assert "does not replace" in voice(client, "explain Quorum")["speech"]
    assert (
        "separate tools"
        in voice(client, "how is CodeUp different from Quorum")["speech"]
    )


def test_frontend_accessible_actions_and_shortcuts_are_wired():
    js = Path("static/app.js").read_text(encoding="utf-8")
    for token in (
        "export_teacher_report",
        "accessible_data_sonify",
        "stop_data_sonification",
        "open_accessible_tools",
        "e.altKey && e.shiftKey",
        "R: 'run'",
    ):
        assert token in js


def test_commands_do_not_reach_ai_provider(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI called")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    for command in (
        "start learning path",
        "start block practice",
        "show keyboard shortcuts",
        "give me a small hint",
        "teacher mode on",
        "check beginner style",
        "start error practice",
        "explain Quorum",
    ):
        assert voice(client, command, code="print('ok')")["success"] is True


@pytest.mark.parametrize(
    "command",
    [
        "start learning path",
        "start block practice",
        "show keyboard shortcuts",
        "give me a small hint",
        "summarize csv",
        "teacher mode on",
        "check beginner style",
        "start error practice",
        "explain Quorum",
    ],
)
def test_feature_commands_are_registered_with_existing_parser(command):
    assert parse_intent(command)["intent"] == "accessible_learning"

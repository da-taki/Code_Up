"""Focused regressions for exact claims in CodeUp_How_To_Use_Guide.pdf."""

import re
from pathlib import Path

import pytest

import app as app_module
from codeup.classroom import concepts, db as classroom_db, ide_commands
from codeup.accessibility import audio_blocks
from codeup.runtime import state_watch


@pytest.mark.parametrize(
    ("phrase", "intent"),
    [
        ("is my instructor helping", "help_status"),
        ("cancel help", "help_cancel"),
        ("leave class", "leave_class"),
    ],
)
def test_exact_guide_classroom_phrases_are_deterministic(phrase, intent):
    assert ide_commands.match(phrase) == (intent, {})


def test_guide_variable_summary_finds_bindings_throughout_program():
    code = (
        "def calculate(values):\n"
        "    total = 0\n"
        "    for value in values:\n"
        "        total += value\n"
        "    return total\n\n"
        "numbers = [1, 2, 3]\n"
        "print(calculate(numbers))\n"
    )

    variables = {item["name"]: item["type"] for item in state_watch.list_variables(code)}

    assert variables == {
        "values": "function input",
        "total": "number",
        "value": "loop value",
        "numbers": "list",
    }
    narration = state_watch.narrate_variables(code)
    assert all(name in narration for name in variables)


def test_pending_fix_supports_the_guides_complete_pre_apply_review():
    client = app_module.app.test_client()
    code = "if True:\nprint('fixed')"
    error = client.post("/run", json={"code": code}).get_json()["error"]

    def command(text, current_code=code):
        return client.post(
            "/voice-command",
            json={"text": text, "code": current_code, "error": error, "source": "typed"},
        ).get_json()

    proposed = command("fix with explanation")
    assert "say apply" in proposed["speech"].lower()

    for phrase in (
        "what changed",
        "read before and after",
        "explain this change",
        "is this risky",
    ):
        reviewed = command(phrase)
        assert reviewed["success"] is True
        assert "no code changes" not in reviewed["speech"].lower()
        assert "nothing to review" not in reviewed["speech"].lower()

    applied = command("apply this change")
    fixed = applied["ai_action"]["code"]
    assert fixed == "if True:\n    print('fixed')"

    undone = command("undo last change", fixed)
    assert undone["ai_action"]["code"] == code

    comparison = command("compare before and after")
    assert comparison["action"] == "compare_before_after"


@pytest.fixture
def assessment_client(monkeypatch):
    provider_calls = []

    def fail_provider(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError("assessment command reached an AI provider")

    monkeypatch.setattr(
        app_module.classroom_db,
        "get_assignment",
        lambda assignment_id: {"ai_policy": "OFF", "capability_settings": None},
    )
    monkeypatch.setattr(app_module, "call_gemini", fail_provider)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail_provider)
    return app_module.app.test_client(), provider_calls


@pytest.mark.parametrize(
    ("phrase", "capability", "code", "error"),
    [
        ("make a calculator", "generate", "", ""),
        ("insert a for loop", "generate", "print('start')", ""),
        ("give me a hint", "hint", "for item in range(2):\n    print(item)", ""),
        ("explain error", "error_help", "print(missing)", "NameError: name 'missing' is not defined"),
        ("where did it crash", "error_help", "print(missing)", "NameError: name 'missing' is not defined"),
        ("what is a dictionary", "concept_qa", "", ""),
        ("give me a code map", "audio_code_map", "def greet():\n    print('hi')", ""),
        ("step through this", "step_narration", "count = 1\nprint(count)", ""),
        ("next step", "step_narration", "count = 1\nprint(count)", ""),
        ("show program state", "watch_variable", "count = 1\nprint(count)", ""),
        ("what variables exist", "watch_variable", "count = 1\nprint(count)", ""),
    ],
)
def test_assessment_policy_blocks_controlled_typed_commands(
    assessment_client, phrase, capability, code, error
):
    client, provider_calls = assessment_client
    response = client.post(
        "/voice-command",
        json={
            "text": phrase,
            "code": code,
            "error": error,
            "assignment_id": 7,
            "source": "typed",
        },
        headers={"Origin": "http://localhost"},
    ).get_json()

    assert response["policy_blocked"] is True
    assert response["capability"] == capability
    assert "instructor" in response["message"].lower()
    assert provider_calls == []


def test_assessment_policy_blocks_direct_deterministic_assistance_endpoints(assessment_client):
    client, provider_calls = assessment_client

    generated = client.post(
        "/generate-code",
        json={"prompt": "write a calculator", "assignment_id": 7},
        headers={"Origin": "http://localhost"},
    ).get_json()
    tracked = client.post(
        "/track-variables",
        json={"code": "count = 1\nprint(count)", "line": 2, "assignment_id": 7},
        headers={"Origin": "http://localhost"},
    ).get_json()

    assert generated["success"] is False
    assert tracked["success"] is False
    assert "instructor" in generated["error"].lower()
    assert "instructor" in tracked["error"].lower()
    assert provider_calls == []


@pytest.mark.parametrize(
    "phrase",
    ["run", "read output", "go to output", "why is this line indented"],
)
def test_assessment_policy_preserves_core_and_structural_commands(assessment_client, phrase):
    client, provider_calls = assessment_client
    response = client.post(
        "/voice-command",
        json={
            "text": phrase,
            "code": "for item in range(2):\n    print(item)",
            "cursor_line": 2,
            "assignment_id": 7,
            "source": "typed",
        },
        headers={"Origin": "http://localhost"},
    ).get_json()

    assert response.get("policy_blocked") is not True
    assert response["success"] is True
    assert provider_calls == []


def test_first_project_file_preserves_existing_single_file_as_main():
    source = Path("static/app.js").read_text(encoding="utf-8")
    start = source.index("async function createProjectFile(path)")
    end = source.index("async function renameProjectFile", start)
    create_file = source[start:end]

    preserve = "await saveProjectFile('main.py', getCode(), false)"
    create_new = "await saveProjectFile(clean, starter, true)"
    assert preserve in create_file
    assert create_file.index(preserve) < create_file.index(create_new)


def test_builtin_completions_treat_dollar_zero_as_a_cursor_placeholder():
    source = Path("static/app.js").read_text(encoding="utf-8")
    start = source.index("PYTHON_BUILTINS.forEach(fn => suggestions.push")
    end = source.index("Object.entries(PYTHON_SNIPPETS)", start)
    builtins = source[start:end]

    assert "insertText: fn + '($0)'" in builtins
    assert "CompletionItemInsertTextRule.InsertAsSnippet" in builtins


def test_multifile_project_restores_and_persists_active_edits_across_reload():
    source = Path("static/app.js").read_text(encoding="utf-8")

    assert "const PROJECT_DRAFT_KEY = 'codeup_project_draft'" in source
    assert "editor.onDidChangeModelContent(() =>" in source
    assert "scheduleActiveProjectSave();" in source
    assert "persistProjectDraftLocal();" in source
    assert "const response = await fetch('/project');" in source
    assert "recoverProjectWorkspace().catch" in source
    assert "project: (typeof ProjectState !== 'undefined' && ProjectState.active) ? currentProjectPayload() : null" in source
    assert "runFile || ProjectState.entry || ProjectState.activeFile || 'main.py'" in source


def test_audio_code_map_reports_classes_without_promoting_methods():
    code = (
        "def helper():\n"
        "    return 1\n\n"
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.count = 0\n\n"
        "for item in range(2):\n"
        "    print(item)\n"
    )

    result = app_module._enhanced_code_map(code)

    assert [item["name"] for item in result["functions"]] == ["helper"]
    assert result["classes"] == [{
        "name": "Counter", "start": 4, "end": 6, "methods": ["__init__"]
    }]
    assert "class: Counter with method __init__" in result["summary"]
    assert "for loop from line 8 to 9" in result["summary"]


def test_all_commands_help_advertises_supported_snippet_name_lookup():
    source = Path("static/app.js").read_text(encoding="utf-8")

    assert '"load snippet [name]"' in source
    assert '"load snippet [number]"' not in source


def test_audio_blocks_plain_numeric_assignment_stays_numeric():
    memory = {}
    audio_blocks.route_command("open audio blocks", "", memory)
    response = audio_blocks.route_command("set variable count to 5", "", memory)

    block = response["audio_blocks"]["blocks"][-1]
    assert block["type"] == "set_number"
    assert block["generated"] == "count = 5"


def test_step_narration_names_classes_and_hides_runtime_addresses():
    code = (
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.count = 0\n\n"
        "counter = Counter()\n"
    )
    result = app_module._run_with_trace_for_narration(code, set(), "guide_class_trace")
    narration = " ".join(step["text"] for step in state_watch.build_steps(result["raw_trace"], code))

    assert "Enter class definition Counter" in narration
    assert "Enter function Counter" not in narration
    assert "a Counter object" in narration
    assert "0x" not in narration
    assert "the class Counter" in narration


# ============================================================================
# Guide page 2: editor traversal is trap-free by default.
# Real trusted-keypress coverage lives in tests/test_monaco_tab_focus.py
# (default Tab/Shift+Tab exit + explicit opt-out indentation mode).
# ============================================================================

def test_editor_tab_exit_is_default_and_indent_remains_keyboard_reachable():
    source = Path("static/app.js").read_text(encoding="utf-8")
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert "e.key === 'Tab' && !e.altKey && !e.ctrlKey && !e.metaKey && tabMovesFocusEnabled()" in source
    assert "return stored == null ? true : stored === 'true'" in source
    # Leaving the editor never depends on Tab: Escape and Ctrl+M stay bound.
    assert "monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyM, () => { leaveEditor(); }" in source
    assert 'id="tabFocusToggle"' in html
    help_start = html.index('id="editorHelp"')
    assert "Tab and Shift+Tab move forward and backward out of the editor" in html[help_start:help_start + 500]
    assert "Control right bracket and Control left bracket to indent and outdent" in html[help_start:help_start + 500]


GUIDE_SOURCE = Path("docs/guide/quick-how-to-guide.html")
GUIDE_PDF = Path("docs/guide/CodeUp_How_To_Use_Guide.pdf")


def test_guide_source_documents_default_tab_navigation_and_bracket_indent():
    guide = GUIDE_SOURCE.read_text(encoding="utf-8")
    assert "Tab inside the editor indents code" not in guide
    start = guide.index('id="tab-behavior"')
    bullet = guide[start:guide.index("</li>", start)]
    assert "By default, Tab moves to the next control and Shift+Tab moves to the previous control" in bullet
    assert "Use Ctrl+] to indent a line and Ctrl+[ to outdent it" in bullet
    assert 'turn off "Tab Leaves Editor"' in bullet
    # The toggle the guide names must be the real control, and it must really
    # default to on (Tab leaves the editor) for new users.
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert re.search(r'id="tabFocusToggle"[^>]*aria-pressed="true"[^>]*>\s*Tab Leaves Editor', html)


def test_guide_shortcut_table_matches_the_ide_shortcut_dialog():
    guide = GUIDE_SOURCE.read_text(encoding="utf-8")
    table = guide[guide.index('id="essential-shortcuts"'):]
    table = table[:table.index("</table>")]
    shortcuts = re.findall(r"<tr><td>([^<]+)</td>", table)
    html = Path("templates/index.html").read_text(encoding="utf-8")
    dialog = html[html.index('id="shortcutHelpModal"'):]
    dialog = dialog[:dialog.index('id="shortcutHelpCloseBtn"')]
    for shortcut in shortcuts:
        for key in (part.strip() for part in shortcut.split("/")):
            assert f'<span class="cu-hotkey">{key}</span>' in dialog, key


def test_generated_guide_pdf_is_in_sync_with_its_source():
    from pypdf import PdfReader

    text = " ".join(page.extract_text() for page in PdfReader(str(GUIDE_PDF)).pages)
    text = re.sub(r"\s+", " ", text)
    assert "Tab inside the editor indents code" not in text
    assert "By default, Tab moves to the next control and Shift+Tab moves to the previous control" in text
    assert chr(0x2014) not in text  # no em dashes in learner-facing text


# ============================================================================
# Full September 2026 guide (docs/guide/full-how-to-guide.html). Converted once
# from the 67-page PDF; only the Tab/indent statements changed.
# ============================================================================

FULL_SOURCE = Path("docs/guide/full-how-to-guide.html")
FULL_PDF = Path("docs/guide/CodeUp_How_To_Guide_September_2026.pdf")

# Any phrasing that says Tab inside the editor indents (the pre-XRCVC default).
_STALE_TAB = re.compile(
    r"Tab (?:inside|in) the editor(?:\s|</td>|<td>)*(?:indents|Indent Python code)", re.IGNORECASE
)


def _pdf_text(path):
    from pypdf import PdfReader

    return re.sub(r"\s+", " ", " ".join(page.extract_text() for page in PdfReader(str(path)).pages))


@pytest.mark.parametrize("source", [GUIDE_SOURCE, FULL_SOURCE], ids=["quick", "full"])
def test_no_guide_source_says_tab_indents_by_default(source):
    text = source.read_text(encoding="utf-8")
    assert not _STALE_TAB.search(text)
    assert chr(0x2014) not in text


def test_full_guide_documents_default_tab_navigation_and_bracket_indent():
    guide = FULL_SOURCE.read_text(encoding="utf-8")
    keyboard = guide[guide.index("<h2>6. Keyboard basics</h2>"):]
    keyboard = keyboard[:keyboard.index("</table>")]
    assert "<td>Tab or Shift+Tab inside the editor</td><td>Leave the code editor forward or backward (the default)" in keyboard
    assert "turn off Tab Leaves Editor" in keyboard
    assert "<td>Ctrl+] / Ctrl+[</td><td>Indent or outdent the current line of Python code." in keyboard
    settings = guide[guide.index("5.5 Visual accessibility controls"):]
    settings = settings[:settings.index("</table>")]
    assert "<td>Tab Leaves Editor</td><td>On by default: Tab and Shift+Tab move focus out of the code editor." in settings


def test_full_guide_keeps_every_part_and_numbered_section():
    guide = FULL_SOURCE.read_text(encoding="utf-8")
    parts = re.findall(r'<h1 class="part">([^<]+)</h1>', guide)
    assert parts == [
        "Start here: choose your path",
        "Part I. Learner Guide",
        "Part II. Instructor Guide",
        "Part III. Running a Class or Program",
        "Part IV. Complete Operational Reference",
        "Part V. Repository Feature Coverage Appendix",
    ]
    numbers = [int(n) for n in re.findall(r"<h2>(\d+)\. ", guide)]
    assert numbers == list(range(1, 104)), "a numbered section was dropped or reordered"
    # Conversion artifacts that once broke the layout: rows split across a page
    # break, a code block misread as a table, and a code block that restarted
    # the six-step quickstart numbering at step 3.
    assert "<tr><td></td>" not in guide and '<th scope="col"></th>' not in guide
    assert "<strong></strong>" not in guide
    quickstart = guide[guide.index("<h2>First 10 minutes: learner quickstart</h2>"):]
    quickstart = quickstart[:quickstart.index("</ol>")]
    assert quickstart.count("<li>") == 6


@pytest.mark.parametrize("pdf", [GUIDE_PDF, FULL_PDF], ids=["quick", "full"])
def test_generated_guide_pdfs_have_no_stale_tab_wording(pdf):
    text = _pdf_text(pdf)
    assert not _STALE_TAB.search(text)
    assert chr(0x2014) not in text


def test_generated_full_guide_pdf_is_in_sync_with_its_source():
    from pypdf import PdfReader

    assert len(PdfReader(str(FULL_PDF)).pages) >= 67, "the full guide must stay a full guide"
    text = _pdf_text(FULL_PDF)
    assert "Leave the code editor forward or backward (the default)" in text
    assert "103. " in text and "Part V. Repository Feature Coverage Appendix" in text


def test_guide_build_script_regenerates_both_guides():
    source = Path("scripts/build_guide_pdf.py").read_text(encoding="utf-8")
    for path in (GUIDE_SOURCE, GUIDE_PDF, FULL_SOURCE, FULL_PDF):
        assert path.exists(), path
        assert f'"{path.name}"' in source, path.name


# ============================================================================
# Guide page 5: Programming Literacy / Tutor Mode - "request progressive hints,
# and check understanding."
# ============================================================================

INDENT_CODE = "for i in range(3):\nprint(i)"
LOOP_CODE = "for i in range(3):\n    print(i)"


@pytest.fixture
def tutor(monkeypatch):
    def fail_provider(*args, **kwargs):
        raise AssertionError("tutor flow reached an AI provider")

    monkeypatch.setattr(app_module, "call_gemini", fail_provider)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail_provider)
    client = app_module.app.test_client()

    def say(text, code="", error=""):
        return client.post(
            "/voice-command",
            json={"text": text, "code": code, "error": error, "source": "typed"},
            headers={"Origin": "http://localhost"},
        ).get_json()

    say.client = client
    return say


def _indent_error(say):
    return say.client.post("/run", json={"code": INDENT_CODE}).get_json()["error"]


def test_tutor_hints_escalate_from_small_to_answer_without_changing_code(tutor):
    error = _indent_error(tutor)
    tutor("start tutor mode", INDENT_CODE)

    first = tutor("give me a hint", INDENT_CODE, error)
    second = tutor("give me another hint", INDENT_CODE, error)
    third = tutor("give me another hint", INDENT_CODE, error)

    assert [first["hint_level"], second["hint_level"], third["hint_level"]] == ["small", "bigger", "answer"]
    assert first["speech"].startswith("Hint 1 of 3.")
    assert "four spaces" not in first["speech"], "the first hint must not give away the fix"
    assert "print(i)" in second["speech"]
    assert "four spaces before print(i)" in third["speech"]
    assert all("ai_action" not in r for r in (first, second, third))


@pytest.mark.parametrize("phrase", ["give me a bigger hint", "bigger hint", "give me the next hint"])
def test_tutor_bigger_hint_phrases_are_not_treated_as_edit_requests(tutor, phrase):
    error = _indent_error(tutor)
    tutor("give me a hint", INDENT_CODE, error)
    response = tutor(phrase, INDENT_CODE, error)
    assert response["action"] == "deterministic_message"
    assert response["hint_level"] == "bigger"


def test_tutor_small_hint_restarts_ladder_and_repeat_hint_escalates(tutor):
    error = _indent_error(tutor)
    tutor("give me a hint", INDENT_CODE, error)
    assert tutor("give me a hint", INDENT_CODE, error)["hint_level"] == "bigger"
    assert tutor("give me a small hint", INDENT_CODE, error)["hint_level"] == "small"


def test_learning_path_keeps_its_own_hint_ladder(tutor):
    tutor("start learning path")
    small = tutor("give me a small hint")["speech"]
    bigger = tutor("give me a bigger hint")["speech"]
    assert len(bigger) > len(small)
    assert not bigger.startswith("Hint 2 of 3")


@pytest.mark.parametrize("answer", ["2", "two", "i is 2 on the last run", "my answer is 2"])
def test_check_understanding_grades_the_learners_answer(tutor, answer):
    question = tutor("check my understanding", LOOP_CODE)
    assert "last loop run" in question["speech"]

    graded = tutor(answer, LOOP_CODE)
    assert graded["intent"] == "understanding_answer"
    assert graded["understanding_result"] == "correct"
    assert graded["understanding_correct"] is True
    assert "Correct." in graded["speech"]
    assert graded["action"] != "run"


def test_check_understanding_wrong_answer_hint_retry_and_grade_replay(tutor):
    tutor("check my understanding", LOOP_CODE)
    wrong = tutor("3", LOOP_CODE)
    assert wrong["understanding_result"] == "incorrect"
    assert "Not quite" in wrong["speech"]

    hint = tutor("give me a hint", LOOP_CODE)
    assert hint["understanding_result"] == "hint"
    assert "range(3) stops before 3" in hint["speech"]

    right = tutor("2", LOOP_CODE)
    assert right["understanding_result"] == "correct"

    replay = tutor("grade my attempt", LOOP_CODE)
    assert replay["intent"] == "understanding_grade"
    assert "Your answer was: 2." in replay["speech"] and "Correct." in replay["speech"]


def test_check_understanding_reveal_and_attempt_limit(tutor):
    tutor("check my understanding", LOOP_CODE)
    revealed = tutor("i don't know", LOOP_CODE)
    assert revealed["understanding_result"] == "revealed"
    assert "i is 2 on the last run" in revealed["speech"]

    tutor("check my understanding", LOOP_CODE)
    results = [tutor(answer, LOOP_CODE)["understanding_result"] for answer in ("0", "1", "3")]
    assert results == ["incorrect", "incorrect", "revealed"]
    # Question closed: the next bare number is no longer claimed as an answer.
    assert tutor("5", LOOP_CODE).get("intent") != "understanding_answer"


def test_open_question_does_not_swallow_commands_or_answers_after_code_changes(tutor):
    tutor("check my understanding", LOOP_CODE)
    assert tutor("run", LOOP_CODE)["action"] == "run"
    assert tutor("read output", LOOP_CODE).get("intent") != "understanding_answer"

    changed = LOOP_CODE + "\nprint('done')"
    assert tutor("2", changed).get("intent") != "understanding_answer"
    # An explicit answer is still accepted after an edit.
    assert tutor("my answer is 2", changed)["understanding_result"] == "correct"


def test_indentation_error_question_is_about_the_real_line_and_is_graded(tutor):
    error = _indent_error(tutor)
    question = tutor("quiz me on this code", INDENT_CODE, error)
    assert "Why does Python need spaces before print(i)" in question["speech"]
    graded = tutor("because it belongs inside the loop block", INDENT_CODE, error)
    assert graded["understanding_result"] == "correct"


@pytest.mark.parametrize(
    ("lesson", "code", "wrong", "right"),
    [
        ("6", 'def greet(name):\n    print("Hello", name)\n\ngreet("CodeUp")\n', "line 1", "line 4"),
        ("5", 'items = ["apple", "banana", "cherry"]\nprint(items[0])\n', "banana", "apple"),
        ("2", 'name = "CodeUp"\nprint(name)\n', "Learner", "CodeUp"),
        ("4", LOOP_CODE, "3", "2"),
    ],
)
def test_literacy_lesson_check_is_a_single_gradable_question(tutor, lesson, code, wrong, right):
    tutor(f"start lesson {lesson}", code)
    check = tutor("check lesson understanding", code)
    assert check["speech"].startswith("Lesson check:")
    assert check["speech"].count("?") == 1, "lesson question must not be asked twice"

    assert tutor(wrong, code)["understanding_result"] == "incorrect"
    assert tutor(right, code)["understanding_result"] == "correct"


def test_understanding_answers_respect_audio_blocks_mode(tutor):
    tutor("check my understanding", LOOP_CODE)
    response = tutor.client.post(
        "/voice-command",
        json={"text": "2", "code": LOOP_CODE, "source": "typed", "active_mode": "audio_blocks"},
        headers={"Origin": "http://localhost"},
    ).get_json()
    assert response.get("intent") != "understanding_answer"


# ============================================================================
# Guide page 5: Python Foundations and guided projects as optional learner
# tools - usable without joining a class (guest practice, no DB writes).
# ============================================================================

@pytest.fixture
def guest(monkeypatch):
    def no_db_write(*args, **kwargs):
        raise AssertionError("guest practice must not write classroom progress")

    for name in ("upsert_module_stage", "set_curriculum_position", "get_or_create_module_progress_row",
                 "record_quiz_result", "save_project_progress", "get_or_create_project_progress", "log_event"):
        monkeypatch.setattr(classroom_db, name, no_db_write)
    return app_module.app.test_client()


def test_guest_can_browse_and_open_python_foundations(guest):
    home = guest.get("/classroom/curriculum")
    assert home.status_code == 200
    assert b"Practicing without a class" in home.data
    assert home.data.count(b"/classroom/curriculum/") >= 12
    assert b"restart-course" not in home.data

    opened = guest.get("/classroom/curriculum/printing/open")
    assert opened.status_code == 302
    assert opened.headers["Location"].endswith("/ide?module=printing")

    context = guest.get("/classroom/curriculum/printing/context").get_json()
    assert context["success"] is True and context["guest"] is True
    assert context["lesson"]["title"] == "Printing and output"
    assert context["next_module_id"] == "variables"


def test_guest_module_progress_is_checked_and_kept_in_the_browser_session(guest):
    failed = guest.post("/classroom/curriculum/printing/attempt", json={"code": "x = 1"}).get_json()
    assert failed["passed"] is False

    passed = guest.post("/classroom/curriculum/printing/attempt", json={"code": "print('hi')"}).get_json()
    assert passed["passed"] is True
    progress = guest.get("/classroom/curriculum/printing/context").get_json()["progress"]
    assert progress["completed_stages"] == ["attempt"]
    assert progress["status"] == "completed"

    fresh_browser = app_module.app.test_client()
    assert fresh_browser.get("/classroom/curriculum/printing/context").get_json()["progress"]["completed_stages"] == []


def test_guest_quiz_is_graded_without_saving(guest):
    assert guest.get("/classroom/curriculum/printing/quiz").status_code == 200
    from codeup.classroom import curriculum
    answer = curriculum.MODULES["printing"]["quiz_answer_index"]
    result = guest.post("/classroom/curriculum/printing/quiz", data={"choice": str(answer)})
    assert result.status_code == 200
    assert b"cu-notice--error" not in result.data


def test_guest_can_open_and_complete_builtin_guided_project_checkpoints(guest):
    from codeup.classroom import guided_projects
    project = guided_projects.list_projects()[0]
    home = guest.get("/classroom/curriculum")
    assert f"/classroom/projects/{project['id']}/open".encode() in home.data

    opened = guest.get(f"/classroom/projects/{project['id']}/open")
    assert opened.headers["Location"].endswith(f"/ide?project={project['id']}")
    context = guest.get(f"/classroom/projects/{project['id']}/context").get_json()
    assert context["success"] is True and context["progress"]["checkpoints_completed"] == []

    saved = guest.post(f"/classroom/projects/{project['id']}/save", json={"code": "marks = {'a': 1}"}).get_json()
    assert saved["success"] is True and saved["newly_completed"]
    again = guest.get(f"/classroom/projects/{project['id']}/context").get_json()
    assert again["progress"]["checkpoints_completed"] == saved["checkpoints_completed"]


def test_guest_cannot_reach_class_only_content_or_restart_routes(guest):
    assert guest.get("/classroom/curriculum/custom:1/context").status_code == 401
    assert guest.post("/classroom/curriculum/custom:1/attempt", json={"code": "print(1)"}).status_code == 401
    assert guest.post("/classroom/projects/custom:1/save", json={"code": ""}).status_code == 401
    assert guest.get("/classroom/curriculum/custom:1/open").headers["Location"].endswith("/classroom/join")
    assert guest.get("/classroom/curriculum/not-a-module/context").status_code == 401
    assert guest.get("/classroom/curriculum/restart-course/confirm").headers["Location"].endswith("/classroom/join")


def test_ide_join_panel_offers_practice_without_a_class():
    source = Path("static/classroom.js").read_text(encoding="utf-8")
    start = source.index("function renderJoinPanel(panel, data)")
    join_panel = source[start:source.index("function renderDashboardPanel", start)]
    assert "href: '/classroom/curriculum'" in join_panel
    assert "Practice without a class" in join_panel
    assert "if (data.guest) appendGuestPracticeNote(panel);" in source


# ============================================================================
# Guide page 7: instructors enter expected concepts for custom lessons.
# ============================================================================

def _make_instructor_cohort(client, username):
    client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": "Teacher"},
    )
    page = client.post("/classroom/cohorts", data={"name": "Cohort"}, follow_redirects=True)
    join_code = re.search(rb'cu-join-code">([A-Z0-9]+)<', page.data).group(1).decode()
    cohort_id = int(re.search(rb'cohorts/(\d+)"', page.data).group(1))
    return join_code, cohort_id


def _learner_lesson_id(learner, join_code):
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Learner"}, follow_redirects=True)
    return "custom:" + re.search(rb"custom:(\d+)", learner.get("/classroom/curriculum").data).group(1).decode()


@pytest.mark.parametrize(
    ("typed", "code"),
    [
        ("variables, loops", "total = 0\nfor i in range(3):\n    total += i\nprint(total)"),
        ("for loops, if statements", "for i in range(3):\n    if i > 1:\n        print(i)"),
        ("Variables, Loops", "total = 0\nfor i in range(3):\n    total += i\nprint(total)"),
        ("conditionals (if/else)", "x = 1\nif x:\n    print(x)"),
        ("conditionals", "x = 1\nif x:\n    print(x)"),
        ("dictionary, f-string", "d = {'a': 1}\nprint(f\"{d['a']}\")"),
        ("user input, data types", "n = int(input())\nprint(n)"),
        ("print output, functions", "def greet():\n    print('hi')\n\ngreet()"),
    ],
)
def test_custom_lesson_expected_concepts_accept_supported_labels_and_common_wording(typed, code):
    instructor, learner = app_module.app.test_client(), app_module.app.test_client()
    join_code, cohort_id = _make_instructor_cohort(instructor, "concepts" + str(abs(hash(typed)) % 10**8))
    instructor.post(f"/classroom/cohorts/{cohort_id}/lessons",
                    data={"title": "Lesson", "instructions": "Do it", "expected_concepts": typed})
    lesson_id = _learner_lesson_id(learner, join_code)

    assert learner.post(f"/classroom/curriculum/{lesson_id}/attempt", json={"code": code}).get_json()["passed"] is True
    missing = learner.post(f"/classroom/curriculum/{lesson_id}/attempt", json={"code": "pass"}).get_json()
    assert missing["passed"] is False


def test_custom_lesson_rejects_labels_that_cannot_be_checked_and_keeps_the_form():
    instructor = app_module.app.test_client()
    _join_code, cohort_id = _make_instructor_cohort(instructor, "concepts_unknown")
    page = instructor.post(
        f"/classroom/cohorts/{cohort_id}/lessons",
        data={"title": "Recursion", "instructions": "Recurse", "expected_concepts": "functions, recursion"},
    )
    assert page.status_code == 200
    assert b"CodeUp cannot automatically check: recursion" in page.data
    assert b'aria-invalid="true"' in page.data
    assert b'value="Recursion"' in page.data
    assert b"conditionals (if/else)" in page.data  # supported labels are listed
    assert classroom_db.list_custom_lessons(cohort_id) == []


def test_custom_lesson_saves_canonical_labels():
    instructor = app_module.app.test_client()
    _join_code, cohort_id = _make_instructor_cohort(instructor, "concepts_canonical")
    instructor.post(f"/classroom/cohorts/{cohort_id}/lessons",
                    data={"title": "Loops", "instructions": "Loop", "expected_concepts": "For Loops, if statements, loops"})
    [lesson] = classroom_db.list_custom_lessons(cohort_id)
    assert lesson["expected_concepts"] == ["loops", "conditionals (if/else)"]


def test_legacy_custom_lesson_with_alias_labels_is_still_checkable():
    instructor, learner = app_module.app.test_client(), app_module.app.test_client()
    join_code, cohort_id = _make_instructor_cohort(instructor, "concepts_legacy")
    cohort = classroom_db.get_cohort(cohort_id)
    classroom_db.create_custom_lesson(
        cohort_id, cohort["instructor_id"], title="Legacy", objective="", explanation="", starter_code="",
        instructions="loop", expected_concepts=["for loops", "if statements"], challenge="",
    )
    lesson_id = _learner_lesson_id(learner, join_code)
    code = "for i in range(3):\n    if i:\n        print(i)"
    assert learner.post(f"/classroom/curriculum/{lesson_id}/attempt", json={"code": code}).get_json()["passed"] is True


def test_expected_concept_normalizer_reports_unknown_labels():
    canonical, unknown = concepts.normalize_expected_concepts("Loops, while loop, dicts, recursion, , Recursion")
    assert canonical == ["loops", "dictionaries"]
    assert unknown == ["recursion"]
    assert set(concepts.CURRICULUM_CONCEPTS) <= set(concepts.SUPPORTED_CONCEPTS)

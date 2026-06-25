import pytest

import app as app_module
from command_normalization import normalize_command_transcript
from speech_output import sanitize_speech_text


LOOP = "for i in range(3):\n    print(i)\n"
BAD = "for i in range(3):\nprint(i)\n"
REQS = "flask\nnumpy\npandas\n"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def _spoken(d):
    return (d.get("speech") or d.get("message") or (d.get("ai_action") or {}).get("spoken_confirmation") or "").strip()


def test_normalizes_common_asr_mistakes():
    cases = {
        "bookmark this slope as main loop": "bookmark this loop as main loop",
        "least bookmark": "list bookmarks",
        "makeup project report": "make a project report",
        "explain requriements": "explain requirements",
        "remove error": "fix this code",
    }
    for raw, expected in cases.items():
        assert normalize_command_transcript(raw)["normalized_text"] == expected


def test_what_can_i_do_here_speaks_useful_help(client):
    data = _vc(client, "What can I do here?")
    assert data["action"] == "deterministic_message"
    speech = _spoken(data).lower()
    assert "generate code" in speech
    assert "run code" in speech
    assert "say more" in speech or "more examples" in speech


def test_teach_me_this_code_routes_to_analyze_for_demo(client):
    data = _vc(client, "teach me this code", code=LOOP)
    assert data["action"] == "analyze"
    assert data.get("concept_lesson") is not True


def test_teach_me_this_scored_routes_to_analyze_for_demo(client):
    data = _vc(client, "teach me this scored", code=LOOP)
    assert data["action"] == "analyze"
    assert data.get("concept_lesson") is not True


@pytest.mark.parametrize("text", ["fix this code", "remove error"])
def test_fix_commands_repair_indentation_and_record_replay(client, text):
    fixed = _vc(client, text, code=BAD)
    assert fixed["action"] == "conversational_edit"
    ai = fixed["ai_action"]
    assert ai["action"] == "indent_line"
    assert ai["target"]["line_number"] == 2
    assert "indent" in ai["spoken_confirmation"].lower()

    replay = _vc(client, "Replay mistake", code=LOOP)
    assert replay["action"] == "deterministic_message"
    assert "indent" in _spoken(replay).lower()


def test_misheard_bookmark_commands_work(client):
    created = _vc(client, "bookmark this slope as main loop", code=LOOP)
    assert created["action"] == "bookmark_created"
    listed = _vc(client, "least bookmark", code=LOOP)
    assert listed["action"] == "bookmark_list"
    assert "main loop" in _spoken(listed).lower()


def test_prepare_for_nvda_is_accessibility_summary_not_generation(client):
    data = _vc(client, "prepare this for NVDA", code=LOOP)
    assert data["action"] == "deterministic_message"
    speech = _spoken(data).lower()
    assert speech.startswith("screen reader handoff notes ready")
    assert "easier to read with nvda or another screen reader" in speech
    assert "files:" in speech
    assert "keyboard steps" in speech
    assert data["action"] != "generate_code"


def test_make_screen_reader_handoff_notes_is_alias(client):
    data = _vc(client, "make screen reader handoff notes", code=LOOP)
    assert data["action"] == "deterministic_message"
    assert data.get("screen_reader_bridge") is True
    assert _spoken(data).lower().startswith("screen reader handoff notes ready")


@pytest.mark.parametrize("text", ["make a project report", "makeup project report"])
def test_project_report_command_speaks_real_preview(client, text):
    data = _vc(client, text, code=LOOP)
    assert data["action"] == "project_report"
    speech = _spoken(data).lower()
    assert "project report" in speech
    assert "for loop" in speech or "range(3)" in speech
    if "makeup" in text:
        assert data["normalized_text"] == "make a project report"


def test_requirements_commands_explain_dependencies(client):
    project = {"files": {"requirements.txt": REQS, "main.py": "import flask\n"}}
    for text in ("explain requriements", "explain requirements"):
        data = _vc(client, text, project=project)
        speech = _spoken(data).lower()
        assert data["action"] == "deterministic_message"
        assert "requirements.txt" in speech
        assert "flask" in speech and "numpy" in speech and "pandas" in speech


def test_speech_sanitizer_removes_markdown_controls():
    speech = sanitize_speech_text("Use `print(i)` and **run**:\n- `range(3)`")
    assert "`" not in speech
    assert "backquote" not in speech.lower()
    assert "print(i)" in speech
    assert "run" in speech


def test_frontend_has_tts_sanitizer_and_key_2_binding():
    with open("static/app.js", encoding="utf-8") as fh:
        src = fh.read()
    assert "function sanitizeSpeechText" in src
    assert "VoiceEngine.speak(spokenText" in src
    assert "e.key === '2'" in src
    assert "Stopped listening and speech. Editor unchanged." in src
    assert "Key 2 repeats the current context" not in src


# --- Deterministic accessibility commands (explain line, read errors, where am I,
#     read around, list variables). All run with cloud AI disabled by the fixture. ---

ACCESS_CODE = "total = 0\nfor i in range(3):\n    total = total + i\nprint(total)\n"


@pytest.mark.parametrize("phrase", ["explain this line", "explain current line", "what does this line do"])
def test_explain_current_line_speaks_deterministic_explanation(client, phrase):
    data = _vc(client, phrase, code=ACCESS_CODE, cursor_line=2)
    assert data["action"] == "deterministic_message"
    assert _spoken(data) == "This starts a for loop. The indented lines after it run three times."


def test_explain_current_line_follows_the_cursor(client):
    data = _vc(client, "explain this line", code=ACCESS_CODE, cursor_line=4)
    assert _spoken(data) == "This prints the value of total."


@pytest.mark.parametrize("phrase,error,expected", [
    ("read errors only", "NameError: name 'total' is not defined",
     "Latest error: The program ran into an error. The error is NameError. "
     "The name total is used before it has a value."),
    ("just tell me the error", "IndentationError: expected an indented block on line 3",
     "Latest error: The program crashed at line 3. The error is IndentationError. "
     "Python expected an indented block after a loop, if, or function header."),
    ("summarize the error", '  File "<user>", line 2\n    x =\n       ^\nSyntaxError: invalid syntax',
     "Latest error: The program crashed at line 2. The error is SyntaxError. "
     "Python could not understand the code structure near line 2."),
])
def test_read_errors_only_speaks_narrated_summary(client, phrase, error, expected):
    # read errors only now routes through the deterministic error_trace narrator
    # (slice 2), giving a "Latest error:" summary instead of raw stderr.
    data = _vc(client, phrase, code="x = 1\n", error=error)
    assert data["action"] == "deterministic_message"
    assert _spoken(data) == expected
    assert "Traceback" not in _spoken(data)


def test_read_errors_only_without_error_is_graceful(client):
    data = _vc(client, "read errors only", code="x = 1\n")
    assert data["action"] == "deterministic_message"
    assert "no error" in _spoken(data).lower()


@pytest.mark.parametrize("phrase", ["where am i", "what block am i in", "where is my cursor"])
def test_where_am_i_aliases_reuse_existing_position_command(client, phrase):
    data = _vc(client, phrase, code=ACCESS_CODE, cursor_line=3)
    assert data["action"] == "where_am_i"


@pytest.mark.parametrize("phrase", ["read around me", "read nearby lines", "give me context"])
def test_read_around_cursor_reads_neighbours(client, phrase):
    data = _vc(client, phrase, code=ACCESS_CODE, cursor_line=2)
    assert data["action"] == "deterministic_message"
    speech = _spoken(data)
    # cursor on line 2 -> two lines above (only line 1 exists) and two below (lines 3, 4)
    assert speech == ("Line 1: total = 0. Line 2: for i in range 3, colon. "
                      "Line 3: indented total = total + i. Line 4: print total.")


@pytest.mark.parametrize("phrase", ["list variables", "what variables do i have", "show my variables"])
def test_list_variables_speaks_assigned_names(client, phrase):
    data = _vc(client, phrase, code=ACCESS_CODE)
    assert data["action"] == "deterministic_message"
    assert _spoken(data) == "You have 2 variables: total and i."


def test_list_variables_when_none(client):
    data = _vc(client, "list variables", code="print('hi')\n")
    assert _spoken(data) == "I do not see any variables yet."


def test_accessibility_commands_do_not_call_cloud_ai(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("deterministic accessibility command must not call cloud AI")
    monkeypatch.setattr(app_module, "call_gemini", boom)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", boom)
    for phrase in ("explain this line", "read errors only", "read around me", "list variables"):
        data = _vc(client, phrase, code=ACCESS_CODE, cursor_line=2,
                   error="NameError: name 'total' is not defined")
        assert data["action"] == "deterministic_message"


def test_beginner_error_summary_helper_is_deterministic():
    assert app_module._beginner_error_summary("") == ""
    assert app_module._beginner_error_summary("ZeroDivisionError: division by zero") == (
        "Your code tries to divide by zero."
    )


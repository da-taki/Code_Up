import os

import pytest

import app as app_module

LOOP = "for i in range(3):\n    print(i)\n"
BAD = "for i in range(3):\nprint(i)\n"
INDENT_ERR = "IndentationError: expected an indented block on line 2"

_STATIC = os.path.join(os.path.dirname(__file__), "..", "static")

GENERIC = {
    "done.", "done", "here you go.", "here you go", "output ready.", "output ready",
    "report generated.", "lesson created.", "you can say another command.",
    "okay.", "ok.", "okay", "ok", "i applied that edit.", "i applied that edit",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(scope="module")
def app_js():
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        return fh.read()


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def _spoken(d):
    ai = d.get("ai_action") or {}
    return (d.get("speech") or d.get("message") or ai.get("spoken_confirmation") or "").strip()


def _assert_meaningful(spoken, keyword=None):
    assert spoken, "no spoken payload"
    assert spoken.lower() not in GENERIC, f"generic confirmation only: {spoken!r}"
    assert len(spoken) >= 15, f"too short to be meaningful: {spoken!r}"
    if keyword:
        assert keyword.lower() in spoken.lower(), (keyword, spoken)



class TestDirectContentActions:

    CASES = [
        ("what can I do here", {}, "python"),
        ("what is recursion", {}, "recursion"),
        ("who are you", {}, "codeup"),
        ("debug this like a teacher", {"code": BAD, "error": INDENT_ERR}, "indent"),
        ("fix the indentation issue", {"code": BAD}, "indent"),
        ("summarize structure", {"code": LOOP}, "loop"),
        ("go to the loop", {"code": LOOP}, "loop"),
        ("make a beginner lesson on loops", {}, "loop"),
        ("prepare this for NVDA", {"code": LOOP}, "screen reader"),
    ]

    @pytest.mark.parametrize("text,kw,keyword", CASES)
    def test_action_speaks_core_information(self, client, text, kw, keyword):
        _assert_meaningful(_spoken(_vc(client, text, **kw)), keyword)

    def test_bookmark_create_and_list_speak(self, client):
        created = _vc(client, "bookmark this loop as main loop", code=LOOP)
        _assert_meaningful(_spoken(created), "main loop")
        listed = _vc(client, "list bookmarks", code=LOOP)
        _assert_meaningful(_spoken(listed), "main loop")

    @pytest.mark.parametrize("text,keyword", [
        ("insert print hello", "hello"),
        ("set age to 16", "age"),
    ])
    def test_insert_confirmation_names_what_changed(self, client, text, keyword):
        d = _vc(client, text, code="")
        ai = d.get("ai_action") or {}
        spoken = (ai.get("spoken_confirmation") or "").strip()
        _assert_meaningful(spoken, keyword)
        assert "added" in spoken.lower()



class TestStatefulActionsSpeak:

    def _seed(self, client):
        _vc(client, "run", code=BAD, error=INDENT_ERR)
        client.post("/run", json={"code": BAD})
        client.post("/run", json={"code": LOOP})
        _vc(client, "teach me this code", code=LOOP)

    def test_replay_explains_broken_vs_fixed(self, client):
        self._seed(client)
        _assert_meaningful(_spoken(_vc(client, "replay the mistake", code=LOOP)), "indent")

    def test_trainer_notes_are_grounded(self, client):
        self._seed(client)
        spoken = _spoken(_vc(client, "make trainer notes", code=LOOP))
        _assert_meaningful(spoken)
        assert "__" not in spoken  # no internal sentinels leak
        assert any(w in spoken.lower() for w in ("learner", "concept", "loop", "ran"))

    def test_recap_mentions_real_events(self, client):
        self._seed(client)
        spoken = _spoken(_vc(client, "what did I learn today", code=LOOP))
        _assert_meaningful(spoken)
        assert "__" not in spoken



class TestMultiStepContentEndpoints:

    def test_project_report_endpoint_speaks_behavior(self, client):
        d = client.post("/project-report", json={"code": LOOP}).get_json()
        spoken = (d.get("speech") or "").strip()
        _assert_meaningful(spoken, "for loop")
        assert "range(3)" in spoken or "0, 1, and 2" in spoken

    def test_voice_report_routes_and_frontend_speaks_followup(self, client, app_js):
        assert _vc(client, "make a project report", code=LOOP)["action"] == "project_report"
        start = app_js.index("async function requestProjectReport(")
        assert "speak(" in app_js[start:start + 1300]

    def test_export_endpoint_speaks_success(self, client):
        d = client.post("/export-project", json={"code": LOOP}).get_json()
        assert d.get("download_url")
        _assert_meaningful((d.get("speech") or ""), "download")

    def test_voice_export_routes_and_frontend_speaks(self, client, app_js):
        assert _vc(client, "export this project")["action"] == "export_project"
        start = app_js.index("async function exportProject(")
        assert "speak(" in app_js[start:start + 1500]



class TestRunAndReadbackSpeechWiring:

    def test_run_output_is_spoken_via_formatter(self, app_js):
        assert "speak(formatRunOutputSpeech(data.output), { forceFull: true, speechKind: 'program-output', sr: false })" in app_js
        assert "speak('Program output:');" not in app_js

    def test_run_error_speaks_kind_and_line_not_just_error(self, app_js):
        assert "speak(`Error${lineHint}: ${lastLine}`, { sr: false, priority: 'assertive' })" in app_js
        assert "match(/line (\\d+)/)" in app_js     # extracts the line number
        assert "data.explanation" in app_js          # plus the friendly explanation

    def test_read_output_reads_stored_output(self, app_js):
        start = app_js.index("function speakOutput(")
        block = app_js[start:start + 600]
        assert "formatFullOutputSpeech" in block
        assert "window.lastRunOutput" in block
        assert "speechKind: 'program-output-replay'" in block

    def test_read_my_code_is_wired_to_speak(self, app_js):
        assert "action === 'read_code'" in app_js
        assert "readMyCodeAloud()" in app_js

    def test_analyze_alias_is_wired_to_speak(self, client, app_js):
        assert _vc(client, "teach me this scored", code=LOOP)["action"] == "analyze"
        assert "action === 'analyze'" in app_js
        assert "analyzeCode()" in app_js

    def test_deterministic_message_dispatch_speaks(self, app_js):
        idx = app_js.index("action === 'deterministic_message'")
        assert "speak(" in app_js[idx:idx + 400]

    def test_empty_output_message_states_success_no_output(self, app_js):
        assert "Program ran successfully with no printed output." in app_js

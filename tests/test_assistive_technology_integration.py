from pathlib import Path

import pytest

import app as app_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.setenv("GROQ_ENABLED", "0")
    monkeypatch.setenv("OLLAMA_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def voice(client, text):
    return client.post("/voice-command", json={"text": text}).get_json()


def test_accessibility_page_is_honest_and_complete(client):
    response = client.get("/accessibility")
    text = response.get_data(as_text=True).lower()
    assert response.status_code == 200
    for heading in (
        "what codeup is", "supported assistive technology workflow", "keyboard-only use",
        "screen reader notes", "speech support", "known browser limits", "non-ai tools",
        "what was tested", "what still needs real user testing",
    ):
        assert heading in text
    assert "does not replace nvda" in text
    assert "fully certified" not in text
    assert "guaranteed compatible" not in text
    assert "works perfectly" not in text


@pytest.mark.parametrize(
    "command,enabled",
    [
        ("enable screen reader mode", True),
        ("screen reader mode on", True),
        ("enable assistive technology mode", True),
        ("AT mode on", True),
        ("disable screen reader mode", False),
        ("screen reader mode off", False),
    ],
)
def test_screen_reader_mode_aliases(command, enabled, client):
    data = voice(client, command)
    assert data["action"] == "accessibility_setting"
    assert data["screen_reader_mode"] is enabled


def test_screen_reader_mode_state_can_be_queried_and_disabled(client):
    assert voice(client, "enable screen reader mode")["screen_reader_mode"] is True
    status = voice(client, "what screen reader mode am I using")
    assert status["screen_reader_mode"] is True
    assert "mode is on" in status["speech"].lower()
    assert voice(client, "disable screen reader mode")["screen_reader_mode"] is False


@pytest.mark.parametrize(
    "command,profile,label",
    [
        ("set screen reader to NVDA", "nvda", "NVDA"),
        ("set screen reader to JAWS", "jaws", "JAWS"),
        ("set screen reader to Narrator", "narrator", "Windows Narrator"),
        ("set screen reader to VoiceOver", "voiceover", "VoiceOver"),
        ("set screen reader to Orca", "orca", "Orca"),
        ("set profile to VS Code", "vs code", "VS Code handoff"),
    ],
)
def test_profile_commands(command, profile, label, client):
    data = voice(client, command)
    assert data["screen_reader_profile"] == profile
    assert label in data["speech"]
    active = voice(client, "which screen reader profile is active")
    assert label in active["speech"]
    assert "detected automatically" in active["speech"]


@pytest.mark.parametrize(
    "command,expected",
    [
        ("open accessibility page", "/accessibility"),
        ("show screen reader tips", "polite status area"),
        ("list screen reader commands", "enable screen reader mode"),
        ("explain screen reader support", "does not detect or control"),
    ],
)
def test_accessibility_help_commands(command, expected, client):
    assert expected in voice(client, command)["speech"]


@pytest.mark.parametrize("command", ["export for VS Code", "download VS Code project", "prepare VS Code handoff"])
def test_vscode_export_aliases(command, client):
    data = voice(client, command)
    assert data["action"] == "export_project"
    assert data["vscode_handoff"] is True


def test_onboarding_help_mentions_accessibility_commands(client):
    speech = voice(client, "what can I do here")["speech"].lower()
    assert "enable screen reader mode" in speech
    assert "set screen reader to nvda" in speech
    assert "open accessibility page" in speech


def test_ide_has_live_regions_and_labeled_controls(client):
    html = client.get("/ide").get_data(as_text=True)
    assert 'id="srAnnouncer" role="status" aria-live="polite" aria-atomic="true"' in html
    assert 'id="srAlert" role="alert" aria-live="assertive" aria-atomic="true"' in html
    assert '<label for="assistiveTechnologyProfile">Assistive technology profile</label>' in html
    # The separate "Toggle screen reader mode" / "Toggle browser speech"
    # buttons were replaced by a single speechModeSelect control - two
    # independently-clickable toggles could drift into a contradictory
    # state (e.g. Screen Reader Mode on AND Browser Speech on at once);
    # one dropdown with two mutually exclusive options cannot.
    assert '<select id="speechModeSelect"' in html
    assert 'id="speechModeDescription"' in html


def test_frontend_routes_visual_output_to_live_regions():
    source = Path("static/app.js").read_text(encoding="utf-8")
    assert "srAnnounce(text, isError ? 'assertive' : 'polite')" in source
    assert "found \\d+ errors?" in source
    assert "function srAlert(msg)" in source
    assert "function clearSrAlert()" in source
    run_code = source[source.index("async function runCode("):]
    success_start = run_code.index("if (data.success) {")
    run_success = run_code[success_start:run_code.index("} else {", success_start)]
    assert run_success.index("clearSrAlert();") < run_success.index("out(data.output);")
    assert "replace(/<module>/g, 'top-level code')" in source


def test_accessibility_features_do_not_call_ai(client, monkeypatch):
    def fail_ai(*args, **kwargs):
        raise AssertionError("assistive technology support called an AI provider")

    monkeypatch.setattr(app_module, "call_gemini", fail_ai)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail_ai)
    monkeypatch.setattr(app_module, "_call_ollama", fail_ai)

    assert voice(client, "enable screen reader mode")["screen_reader_mode"] is True
    assert voice(client, "set screen reader to NVDA")["screen_reader_profile"] == "nvda"
    assert client.get("/accessibility").status_code == 200
    assert client.post("/export-project", json={"code": "print('safe')"}).get_json()["success"] is True

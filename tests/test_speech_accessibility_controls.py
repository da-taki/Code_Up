"""
Speech rate + verbosity accessibility controls (Sprint 1, Feature 4).

Backend: deterministic voice routing of rate/verbosity commands (and that they
are never mistaken for code edits). Frontend (structural assertions over the
shipped static/app.js, as this repo tests frontend wiring): rate + verbosity are
persisted to localStorage, the SpeechManager fallback honours the stored rate,
and the existing stop/voice-stability wiring is intact. Plus Sprint-1 regression
guards for the core demo commands.
"""
import os

import pytest

import app as app_module

_STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


@pytest.fixture(scope="module")
def app_js():
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        return fh.read()


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


# =====================================================================
# Speech rate routing
# =====================================================================

class TestSpeechRateRouting:

    @pytest.mark.parametrize("text,rate", [
        ("speak slower", 0.75), ("slow down", 0.75),
        ("speak faster", 1.25), ("speed up", 1.25),
        ("normal speed", 1.0), ("reset speech speed", 1.0),
    ])
    def test_rate_commands(self, client, text, rate):
        d = _vc(client, text)
        assert d["action"] == "set_speech_rate"
        assert d["rate"] == rate

    def test_rate_confirmation_is_short(self, client):
        d = _vc(client, "speak slower")
        assert d["speech"] and len(d["speech"]) < 60


# =====================================================================
# Verbosity routing
# =====================================================================

class TestVerbosityRouting:

    @pytest.mark.parametrize("text,mode", [
        ("be more concise", "concise"),
        ("less detail", "concise"),
        ("explain in detail", "detailed"),
        ("more detail", "detailed"),
        ("beginner mode", "beginner"),
        ("expert mode", "expert"),
        ("normal mode", "normal"),
    ])
    def test_verbosity_commands(self, client, text, mode):
        d = _vc(client, text)
        assert d["action"] == "set_verbosity"
        assert d["verbosity"] == mode

    def test_verbosity_does_not_edit_code(self, client):
        d = _vc(client, "be more concise", code="print('x')\n")
        assert d["action"] == "set_verbosity"
        assert d["action"] not in ("generate_code", "conversational_edit", "fix")

    def test_verbosity_directive_is_empty_for_normal(self):
        assert app_module._verbosity_directive("normal") == ""
        assert app_module._verbosity_directive("") == ""
        assert app_module._verbosity_directive("concise")  # non-empty


# =====================================================================
# Frontend persistence + SpeechManager rate (structural)
# =====================================================================

class TestFrontendWiring:

    def test_speech_rate_persisted(self, app_js):
        assert "localStorage.setItem('codeupSpeechRate'" in app_js
        assert "function applySpeechRate(" in app_js

    def test_verbosity_persisted(self, app_js):
        assert "localStorage.setItem('codeupVerbosity'" in app_js
        assert "function setVerbosity(" in app_js

    def test_speech_manager_uses_stored_rate(self, app_js):
        assert "item.rate  || _speechRate" in app_js or "item.rate || _speechRate" in app_js

    def test_verbosity_sent_in_payload(self, app_js):
        start = app_js.index("function buildVoiceCommandPayload(")
        block = app_js[start:start + 600]
        assert "verbosity: getVerbosity()" in block

    def test_preferences_restored_on_load(self, app_js):
        assert "restoreAccessibilityPreferences()" in app_js

    def test_dispatch_handles_new_actions(self, app_js):
        for token in ("action === 'set_speech_rate'", "action === 'set_verbosity'",
                      "action === 'export_project'", "action === 'project_report'"):
            assert token in app_js, token


# =====================================================================
# Stop / voice stability still works
# =====================================================================

class TestStopStillWorks:

    def test_stop_speaking(self, client):
        assert _vc(client, "stop speaking")["action"] == "stop_speaking"

    def test_stop_everything(self, client):
        assert _vc(client, "stop everything")["action"] == "stop_everything"

    def test_cancel_all_still_bumps_epoch(self, app_js):
        start = app_js.index("function cancelAll()")
        assert "bumpSpeechEpoch()" in app_js[start:start + 400]


# =====================================================================
# Sprint-1 regression guards
# =====================================================================

class TestRegression:

    def test_insert_print_hello(self, client):
        assert _vc(client, "insert print hello")["action"] == "conversational_edit"

    def test_generation_still_works(self, client):
        assert _vc(client, "write a program for first five even numbers")["action"] == "generate_code"

    def test_onboarding_still_works(self, client):
        d = _vc(client, "what can I do here")
        assert d["action"] == "deterministic_message"
        assert d.get("onboarding") is True

    def test_openvino_route_unaffected(self, client):
        d = client.post("/openvino-intent-demo", json={"text": "insert print hello"}).get_json()
        assert d["intent"] == "insert_code"

    def test_project_generation_still_works(self, client):
        d = _vc(client, "make a student marks analysis project using pandas")
        assert d["action"] == "generate_code"

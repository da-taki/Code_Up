"""
Voice-recognition lifecycle stability (Part 1).

Browser SpeechRecognition cannot be exercised headlessly, so — as this repo
already does for frontend wiring — these are structural assertions over the
shipped static/app.js, proving the lifecycle has a clear state model:

  * an UNEXPECTED end auto-restarts only while voice is still WANTED;
  * an intentional stop ("stop listening" / voice off) prevents auto-restart;
  * restarts are debounced (single timer), backed off, and capped so there is no
    restart storm, and a guard prevents two recognizers running at once;
  * hidden tabs defer restart and resume on visibility;
  * the typed-command path is independent of recognition and still works.
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


# =====================================================================
# State model exists
# =====================================================================

class TestStateModel:

    def test_lifecycle_state_vars_exist(self, app_js):
        for token in ("_voiceEnabledByUser", "_recognitionStarting",
                      "_recognitionRestartCount", "_MAX_RAPID_RESTARTS",
                      "_restartTimer"):
            assert token in app_js, token

    def test_scheduler_and_safe_start_exist(self, app_js):
        assert "function _scheduleRecognitionRestart()" in app_js
        assert "function _safeStartRecognition()" in app_js


# =====================================================================
# 1. Unexpected onend schedules a restart only when voice is wanted
# =====================================================================

class TestUnexpectedEndRestarts:

    def test_onend_restarts_when_wanted(self, app_js):
        # The real handler (not the teardown `recognition.onend = null`).
        start = app_js.index("recognition.onend = () => {")
        block = app_js[start:start + 800]
        assert "_scheduleRecognitionRestart()" in block
        # Stays off when the user turned voice off.
        assert "if (!_voiceEnabledByUser) return;" in block


# =====================================================================
# 2. Intentional stop prevents auto-restart
# =====================================================================

class TestIntentionalStopNoRestart:

    def test_scheduler_bails_when_not_wanted(self, app_js):
        start = app_js.index("function _scheduleRecognitionRestart()")
        block = app_js[start:start + 700]
        assert "if (!_voiceEnabledByUser) return;" in block

    def test_voice_off_clears_wanted_flag(self, app_js):
        start = app_js.index("function markVoiceListeningOff()")
        block = app_js[start:start + 200]
        assert "_voiceEnabledByUser = false" in block

    def test_stop_listening_routes_to_mic_pause(self, client):
        assert _vc(client, "stop listening")["action"] == "pause_voice"


# =====================================================================
# 3. Debounced, backed off, no storm, no double instances
# =====================================================================

class TestNoStormNoDoubleInstance:

    def test_single_debounced_timer(self, app_js):
        start = app_js.index("function _scheduleRecognitionRestart()")
        block = app_js[start:start + 1400]
        # One timer, cleared before being re-armed.
        assert "clearTimeout(_restartTimer)" in block
        assert "_restartTimer = setTimeout(" in block

    def test_backoff_and_storm_cap(self, app_js):
        start = app_js.index("function _scheduleRecognitionRestart()")
        block = app_js[start:start + 1400]
        assert "_recognitionRestartCount" in block
        assert "Math.pow(2, _recognitionRestartCount)" in block
        assert "_MAX_RAPID_RESTARTS" in block

    def test_safe_start_prevents_double_instance(self, app_js):
        start = app_js.index("function _safeStartRecognition()")
        block = app_js[start:start + 400]
        assert "_recognitionStarting || isListening" in block

    def test_start_listening_tears_down_old_recognizer(self, app_js):
        start = app_js.index("function startListening()")
        block = app_js[start:start + 900]
        assert "recognition.abort()" in block
        assert "recognition.onend = null" in block


# =====================================================================
# Hidden-tab handling
# =====================================================================

class TestHiddenTab:

    def test_restart_skips_hidden_tab(self, app_js):
        start = app_js.index("function _scheduleRecognitionRestart()")
        block = app_js[start:start + 900]
        assert "document.hidden" in block

    def test_visibilitychange_resumes(self, app_js):
        assert "addEventListener('visibilitychange'" in app_js


# =====================================================================
# 4. Typed command path is unaffected by recognition
# =====================================================================

class TestTypedPathUnaffected:

    def test_typed_run_still_works(self, client):
        d = client.post("/voice-command", json={"text": "run", "source": "typed"}).get_json()
        assert d["action"] == "run"

    def test_typed_insert_still_works(self, client):
        d = client.post("/voice-command", json={"text": "insert print hello", "source": "typed"}).get_json()
        assert d["action"] == "conversational_edit"


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()

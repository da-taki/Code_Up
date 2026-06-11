"""
Speech-cancellation semantics.

Backend: "stop speaking" cancels narration only; "stop everything" cancels
speech + tones + queued narration but does NOT pause the mic. (Distinctness is
also covered in test_voice_control_and_variable_insert.py.)

Frontend (structural assertions over the shipped static/app.js, the way this repo
already tests frontend wiring): cancellation clears the queue, bumps a speech
epoch so an async callback that resolves after "stop" refuses to speak, the
"stop everything" handler preserves the output box (status only), and the long
narrations check the epoch so they stop too.
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


def _block_after(haystack, marker, length=600):
    idx = haystack.index(marker)
    return haystack[idx:idx + length]


# =====================================================================
# Backend routing semantics
# =====================================================================

class TestStopRouting:

    @pytest.mark.parametrize("text", ["stop speaking", "be quiet", "pause speech"])
    def test_stop_speaking_is_speech_only(self, client, text):
        d = _vc(client, text)
        assert d["action"] == "stop_speaking"
        assert d["action"] != "pause_voice"
        assert d["action"] != "stop_everything"

    @pytest.mark.parametrize("text", ["stop everything", "cancel everything", "stop all"])
    def test_stop_everything_is_cancel_all_not_mic_pause(self, client, text):
        d = _vc(client, text)
        assert d["action"] == "stop_everything"
        assert d["action"] != "pause_voice"


# =====================================================================
# Frontend: cancellation clears queue + bumps epoch
# =====================================================================

class TestCancellationWiring:

    def test_stop_speaking_handler_cancels_speech(self, app_js):
        block = _block_after(app_js, "action === 'stop_speaking'")
        assert "SpeechManager.cancelAll()" in block

    def test_cancel_all_clears_queue_and_bumps_epoch(self, app_js):
        start = app_js.index("function cancelAll()")
        block = app_js[start:start + 400]
        assert "queue.length = 0" in block
        assert "bumpSpeechEpoch()" in block

    def test_speak_has_epoch_guard(self, app_js):
        start = app_js.index("function speak(text, opts")
        block = app_js[start:start + 300]
        assert "opts.epoch" in block and "_speechEpoch" in block

    def test_epoch_helpers_exist(self, app_js):
        assert "function currentSpeechEpoch()" in app_js
        assert "function bumpSpeechEpoch()" in app_js


# =====================================================================
# Frontend: async narrations stop after a cancel (epoch checks)
# =====================================================================

class TestAsyncNarrationsRespectCancel:

    def test_walkthrough_captures_and_checks_epoch(self, app_js):
        start = app_js.index("async function walkThroughCode()")
        block = app_js[start:start + 900]
        assert "currentSpeechEpoch()" in block
        assert "epoch: _walkEpoch" in block

    def test_step_narration_loop_bails_on_epoch_change(self, app_js):
        start = app_js.index("async function requestStepNarration()")
        block = app_js[start:start + 1400]
        assert "currentSpeechEpoch()" in block
        assert "_narrEpoch !== currentSpeechEpoch()" in block


# =====================================================================
# Frontend: stop everything preserves the output box (Part 3)
# =====================================================================

class TestOutputPreserved:

    def test_stop_everything_does_not_overwrite_output(self, app_js):
        block = _block_after(app_js, "action === 'stop_everything'")
        # The visible program output must be preserved: announce via the status/
        # ARIA region, never rewrite the output box with "Stopped".
        assert "out('Stopped" not in block and 'out("Stopped' not in block
        assert "srAnnounce('Stopped" in block or 'srAnnounce("Stopped' in block

    def test_stop_everything_still_cancels_speech_tones_and_job(self, app_js):
        # Regression guard mirroring test_tts_voice_consistency.
        block = _block_after(app_js, "action === 'stop_everything'")
        assert "SpeechManager.cancelAll()" in block
        assert "SonificationManager.clearAll()" in block
        assert "_stepNarrationJob.cancelled = true" in block

"""
Guards for the narrated-execution speech + voice-consistency fix.

The physical defect was: Step Narration spoke through a second speech manager
(`SpeechManager`) that never set a voice (browser default, often male) and was
invisible to VoiceEngine's listen/echo handling, so it went silent and switched
voices. The fix routes every spoken line through the single VoiceEngine path
(one resolved preferred English voice) and hardens the voice resolver.

These are structural assertions over the shipped JS (the repo already tests
frontend behavior this way) plus backend cue-semantics regressions. They avoid
asserting any machine-specific voice name or subjective quality.
"""
import os

import pytest

import app as app_module

_STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


def _read(name):
    with open(os.path.join(_STATIC, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def app_js():
    return _read("app.js")


@pytest.fixture(scope="module")
def voice_js():
    return _read("voice-engine.js")


# =====================================================================
# One canonical speech path (no per-feature voice divergence)
# =====================================================================

class TestSingleSpeechPath:

    def test_speech_manager_enqueue_delegates_to_voice_engine(self, app_js):
        # enqueue must hand off to VoiceEngine.speak when present, so every
        # caller (including Step Narration) shares one resolved voice.
        start = app_js.index("function enqueue(")
        block = app_js[start:start + 700]
        assert "VoiceEngine.speak(text, opts)" in block
        assert "typeof VoiceEngine !== 'undefined'" in block

    def test_speech_manager_cancel_delegates_to_voice_engine(self, app_js):
        start = app_js.index("function cancelAll(")
        block = app_js[start:start + 700]
        assert "VoiceEngine.cancelSpeech()" in block

    def test_speak_wrapper_prefers_voice_engine(self, app_js):
        start = app_js.index("function speak(text, opts")
        block = app_js[start:start + 400]
        assert "VoiceEngine.speak(text, opts)" in block

    def test_step_narration_uses_shared_path_not_raw_synthesis(self, app_js):
        start = app_js.index("async function requestStepNarration()")
        end = app_js.index("// ---------- MISTAKE REPLAY ----------")
        block = app_js[start:end]
        # Speaks via the shared manager...
        assert "SpeechManager.enqueue(steps[i])" in block
        # ...and never creates its own utterance or picks its own voice.
        assert "new SpeechSynthesisUtterance" not in block
        assert "speechSynthesis.speak(" not in block
        assert ".voice =" not in block


# =====================================================================
# Voice resolver: one stable, female-preferred English voice
# =====================================================================

class TestVoiceResolution:

    def test_resolver_sets_utterance_voice(self, voice_js):
        assert "_currentUtterance.voice = voice" in voice_js
        assert "_getVoiceForLang(item.lang)" in voice_js

    def test_voiceschanged_is_handled(self, voice_js):
        assert "addEventListener('voiceschanged'" in voice_js
        # Resolver is invoked at init and on the event (not only lazily).
        assert voice_js.count("_loadVoices") >= 3

    def test_female_preference_and_male_avoidance(self, voice_js):
        start = voice_js.index("function _loadVoices(")
        block = voice_js[start:start + 1600]
        assert "FEMALE" in block and "MALE" in block
        # The deterministic fallback chain avoids a known-male default before
        # blindly taking the first voice.
        assert "!MALE.test(v.name)" in block
        # "Google US English" (the existing preferred female voice) preserved.
        assert "Google" in block

    def test_resolver_is_deterministic_single_cached_voice(self, voice_js):
        # One cached English voice reused for every utterance (not reselected
        # per step).
        assert "_englishVoice =" in voice_js
        assert "let _englishVoice" in voice_js


# =====================================================================
# Audio job coordination / cancellation
# =====================================================================

class TestAudioCoordination:

    def test_retrigger_cancels_prior_narration_and_tones(self, app_js):
        start = app_js.index("async function requestStepNarration()")
        block = app_js[start:start + 600]
        assert "_stepNarrationJob.cancelled = true" in block
        assert "SpeechManager.cancelAll()" in block
        assert "SonificationManager.clearAll()" in block

    def test_stop_everything_cancels_speech_tones_and_job(self, app_js):
        idx = app_js.index("action === 'stop_everything'")
        block = app_js[idx:idx + 400]
        assert "SpeechManager.cancelAll()" in block
        assert "SonificationManager.clearAll()" in block
        assert "_stepNarrationJob.cancelled = true" in block

    def test_escape_cancels_narration(self, app_js):
        assert "if (_stepNarrationJob) _stepNarrationJob.cancelled = true;" in app_js

    def test_sonify_block_replaces_active_narration(self, app_js):
        start = app_js.index("async function sonifyCurrentBlock()")
        end = app_js.index("// ---------- NAVIGATION HISTORY ----------")
        block = app_js[start:end]
        assert "_stepNarrationJob.cancelled = true" in block
        assert "SonificationManager.clearAll()" in block


# =====================================================================
# Backend cue-semantics regressions (must remain truthful after the fix)
# =====================================================================

class TestCueSemanticsPreserved:

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
        monkeypatch.setenv("GEMINI_ENABLED", "false")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            yield c

    def test_main_loop_speakable_steps_depth_1(self, client):
        code = "for i in range(3):\n    print(i)\n"
        data = client.post("/step-narration", json={"code": code}).get_json()
        assert data["success"] is True
        narr, depths = data["narration"], data["indent_depths"]
        assert len(narr) == len(depths)
        # Every narrated step carries speakable text (not silent/empty).
        assert all(isinstance(s, str) and s.strip() for s in narr)
        outs = [(n, d) for n, d in zip(narr, depths) if "prints" in n.lower()]
        assert [t for t, _ in outs] == [
            "The program prints 0.", "The program prints 1.", "The program prints 2."]
        assert all(d == 1 for _, d in outs)

    def test_nested_output_depth_2(self, client):
        code = "for i in range(2):\n    if i > 0:\n        print(i)\n"
        data = client.post("/step-narration", json={"code": code}).get_json()
        outs = [(n, d) for n, d in zip(data["narration"], data["indent_depths"]) if "prints" in n.lower()]
        assert outs and outs[0][1] == 2

    def test_flat_program_depth_0(self, client):
        data = client.post("/step-narration", json={"code": "x = 1\nprint(x)\n"}).get_json()
        outs = [(n, d) for n, d in zip(data["narration"], data["indent_depths"]) if "prints" in n.lower()]
        assert outs and outs[0][1] == 0

    def test_broken_program_no_normal_narration(self, client):
        data = client.post("/step-narration", json={"code": "for i in range(3):\nprint(i)\n"}).get_json()
        assert data["success"] is False
        assert not any("prints" in n.lower() for n in data.get("narration", []))

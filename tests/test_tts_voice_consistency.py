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



class TestSingleSpeechPath:

    def test_speech_manager_enqueue_delegates_to_voice_engine(self, app_js):
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
        assert "SpeechManager.enqueue(steps[i])" in block
        assert "new SpeechSynthesisUtterance" not in block
        assert "speechSynthesis.speak(" not in block
        assert ".voice =" not in block



class TestVoiceResolution:

    def test_resolver_sets_utterance_voice(self, voice_js):
        assert "_currentUtterance.voice = voice" in voice_js
        assert "_getVoiceForLang(item.lang)" in voice_js

    def test_voiceschanged_is_handled(self, voice_js):
        assert "addEventListener('voiceschanged'" in voice_js
        assert voice_js.count("_loadVoices") >= 3

    def test_female_preference_and_male_avoidance(self, voice_js):
        start = voice_js.index("function _loadVoices(")
        block = voice_js[start:start + 1600]
        assert "FEMALE" in block and "MALE" in block
        assert "!MALE.test(v.name)" in block
        assert "Google" in block

    def test_resolver_is_deterministic_single_cached_voice(self, voice_js):
        assert "_englishVoice =" in voice_js
        assert "let _englishVoice" in voice_js



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

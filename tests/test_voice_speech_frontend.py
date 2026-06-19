import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestBrowserSpeechStartsAtBeginning:
    def test_voice_engine_speech_chunking_in_node(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node not available in this environment")
        script = os.path.join(ROOT, "tests", "voice_speech_chunking.test.js")
        result = subprocess.run(
            [node, script], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, (
            "node voice-engine speech test failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert "groups passed" in result.stdout


class TestVoiceEngineCancelGuard:
    @pytest.fixture(scope="class")
    def src(self):
        return _read("static/voice-engine.js")

    def test_cancel_uses_generation_guard(self, src):
        assert "_cancelGeneration" in src
        assert "const myGeneration = ++_cancelGeneration;" in src
        assert "if (_cancelGeneration !== myGeneration) return;" in src

    def test_speak_bumps_generation(self, src):
        idx = src.index("function speak(text, opts")
        block = src[idx:idx + 900]
        assert "_cancelGeneration++" in block

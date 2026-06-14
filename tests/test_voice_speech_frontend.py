"""Frontend guarantees for browser speech output (the proven audible path).

Two kinds of checks, both runnable under pytest:

  1. A behavioural test executed by Node against the real static/voice-engine.js
     with a mocked speechSynthesis + controllable clock. It reproduces the
     command handler's cancelAll()+speak() sequence and asserts on the ACTUAL
     text handed to speechSynthesis.speak(): the first utterance starts at the
     BEGINNING of the response, and no started chunk is cancelled by a stale
     deferred-cancel timer. Skipped if node is unavailable.

  2. Source-structural assertions proving the cancel-generation guard exists, so
     a future refactor cannot silently reintroduce the "only speaks the end"
     accessibility regression.
"""
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
        # Deferred cancels must be neutralised by a later speak() so they cannot
        # cut off the first chunk of the next response.
        assert "_cancelGeneration" in src
        assert "const myGeneration = ++_cancelGeneration;" in src
        assert "if (_cancelGeneration !== myGeneration) return;" in src

    def test_speak_bumps_generation(self, src):
        # speak() must invalidate any pending cancels before enqueueing.
        idx = src.index("function speak(text, opts")
        block = src[idx:idx + 900]
        assert "_cancelGeneration++" in block

"""Frontend guarantees for the guided tutorial.

Two kinds of checks, both runnable under pytest:

  1. Pure state-machine behaviour, executed by Node against the real
     static/tutorial.js (TutorialModel). Skipped if node is unavailable.

  2. Source-structural assertions (the repo's established frontend-test style)
     proving the tutorial speaks through the PROVEN audible path, never builds
     its own synthesizer, preserves narration when loading examples, never
     auto-advances on success, and is wired into both command handlers with an
     accessible, keyboard-reachable panel.
"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Pure state machine via Node
# ---------------------------------------------------------------------------
class TestTutorialModelNode:
    def test_model_transitions_pass_in_node(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node not available in this environment")
        script = os.path.join(ROOT, "tests", "tutorial_model.test.js")
        result = subprocess.run(
            [node, script], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, (
            "node TutorialModel test failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert "groups passed" in result.stdout


# ---------------------------------------------------------------------------
# 2a. tutorial.js speaks through the real path
# ---------------------------------------------------------------------------
class TestTutorialJsSpeechPath:
    @pytest.fixture(scope="class")
    def src(self):
        return _read("static/tutorial.js")

    def test_speaks_via_global_speak(self, src):
        # Narration is routed through the app's proven speak() -> VoiceEngine,
        # not merely written to the DOM.
        assert "function _speak(" in src
        assert "speak(text, opts" in src
        assert "_speak(" in src

    def test_never_creates_its_own_utterance(self, src):
        # It must reuse the shared pipeline, never a private synthesizer that
        # could drift from the one working voice path.
        assert "new SpeechSynthesisUtterance" not in src

    def test_setcode_preserves_speech(self, src):
        # Loading example code must not silently cancel queued narration
        # (the classic "shown but not spoken" failure).
        assert "preserveSpeech: true" in src

    def test_no_auto_advance_on_success(self, src):
        # markSuccess moves to the DECISION stage; it must never jump to the
        # next module on its own.
        assert "this.stage = 'decision'" in src
        start = src.index("_celebrateAndDecide: function")
        end = src.index("_continue: function")
        block = src[start:end]
        assert "markSuccess()" in block
        assert "continueNext()" not in block

    def test_exposes_controller_and_run_hooks(self, src):
        assert "window.TutorialController = Controller" in src
        assert "window._tutorialOnRunSuccess" in src
        assert "window._tutorialOnRunError" in src

    def test_validates_against_backend(self, src):
        assert "/tutorial/validate" in src
        assert "/tutorial/modules" in src


# ---------------------------------------------------------------------------
# 2b. app.js integration
# ---------------------------------------------------------------------------
class TestAppJsIntegration:
    @pytest.fixture(scope="class")
    def src(self):
        return _read("static/app.js")

    def test_run_success_and_error_hooks_present(self, src):
        assert "window._tutorialOnRunSuccess" in src
        assert "window._tutorialOnRunError" in src

    def test_voice_handler_intercepts_tutorial(self, src):
        start = src.index("async function handleVoiceCommand(")
        block = src[start:start + 3000]
        assert "TutorialController.handleUtterance" in block

    def test_typed_handler_intercepts_tutorial(self, src):
        start = src.index("async function handleCommandText(")
        block = src[start:start + 1500]
        assert "TutorialController.handleUtterance" in block

    def test_practice_action_handled(self, src):
        assert "action === 'tutorial_practice'" in src


# ---------------------------------------------------------------------------
# 2c. index.html accessible panel
# ---------------------------------------------------------------------------
class TestIndexHtmlPanel:
    @pytest.fixture(scope="class")
    def src(self):
        return _read("templates/index.html")

    def test_accessible_panel_with_keyboard_buttons(self, src):
        for el in [
            "tutorialOverlay", "tutorialTopic", "tutorialProgress",
            "tutorialStatus", "tutorialText",
            "tutorialContinueBtn", "tutorialAgainBtn", "tutorialRecapBtn",
            "tutorialStopBtn", "tutorialHintBtn", "tutorialExampleBtn",
            "tutorialRepeatBtn", "tutorialExitBtn", "tutorialRunBtn",
        ]:
            assert el in src, f"missing element: {el}"

    def test_panel_is_semantic_and_live(self, src):
        assert 'role="complementary"' in src
        assert 'aria-live="polite"' in src

    def test_loads_tutorial_js(self, src):
        assert "/static/tutorial.js" in src

    def test_old_static_slides_removed(self, src):
        # The previous static-slide tutorial must be gone.
        assert "TUTORIAL_STEPS" not in src
        assert "tutorialNextBtn" not in src

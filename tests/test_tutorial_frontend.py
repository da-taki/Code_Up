import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


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

    def test_spoken_code_normalizers_pass_in_node(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node not available in this environment")
        script = os.path.join(ROOT, "tests", "spoken_code.test.js")
        result = subprocess.run(
            [node, script], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, (
            "node spoken-code normalizer test failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert "groups passed" in result.stdout


class TestTutorialJsSpeechPath:
    @pytest.fixture(scope="class")
    def src(self):
        return _read("static/tutorial.js")

    def test_speaks_via_global_speak(self, src):
        assert "function _speak(" in src
        assert "speak(text, opts" in src
        assert "_speak(" in src

    def test_never_creates_its_own_utterance(self, src):
        assert "new SpeechSynthesisUtterance" not in src

    def test_setcode_preserves_speech(self, src):
        assert "preserveSpeech: true" in src

    def test_no_auto_advance_on_success(self, src):
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

    def test_is_voice_first_not_typing_first(self, src):
        assert "build Python programs by speaking" in src
        assert "Type your code, then press Control and Enter" not in src

    def test_has_staged_voice_build_steps(self, src):
        assert "TUTORIAL_STEPS" in src
        assert "onInsert:" in src           # observes real insertions
        assert "_readMyCode" in src         # read-back of the learner's program
        assert "insert a variable named name and give it the value Taknoor" in src


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

    def test_new_insert_actions_routed(self, src):
        assert "action === 'insert_variable'" in src
        assert "action === 'insert_while'" in src
        assert "insertVariableVoice(" in src
        assert "insertWhileVoice(" in src

    def test_tutorial_observes_inserts_without_intercepting(self, src):
        assert "_TUTORIAL_EDIT_ACTIONS" in src
        assert "TutorialController.onInsert" in src

    def test_clear_editor_exits_project_mode(self, src):
        start = src.index("function clearEditor()")
        block = src[start:start + 800]
        assert "ProjectState.active = false" in block
        assert "ProjectState.files = {}" in block
        assert "renderProjectFiles()" in block


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
        """Updated by the accessibility semantic-placement audit: the panel
        is a top-level <aside> (not nested inside article/main/nav/section),
        which already computes to the complementary landmark natively - an
        explicit role="complementary" on top of that was redundant ARIA and
        has been removed, not the landmark itself."""
        overlay = re.search(r'<aside id="tutorialOverlay"[^>]*>', src)
        assert overlay, "tutorialOverlay must be a native <aside>"
        assert 'role="complementary"' not in overlay.group(0)
        assert 'aria-live="polite"' in src

    def test_loads_tutorial_js(self, src):
        assert "/static/tutorial.js" in src

    def test_old_static_slides_removed(self, src):
        assert "TUTORIAL_STEPS" not in src
        assert "tutorialNextBtn" not in src

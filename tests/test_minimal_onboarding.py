"""Vision-Aid build: "Start tutorial" launches a minimal, 5-step IDE
quick-start (static/onboarding.js) instead of the longer Python-concepts
walkthrough (static/tutorial.js, backed by the test-locked
codeup.learning.tutorial_engine). No JS test runner in this repo (see
test_classroom_live_sync.py's established convention), so this is a
source-level contract check, the same pattern already used throughout this
codebase for static/*.js.
"""

from pathlib import Path

ONBOARDING_JS = Path("static/onboarding.js").read_text(encoding="utf-8")
STATIC_APP = Path("static/app.js").read_text(encoding="utf-8")
TUTORIAL_JS = Path("static/tutorial.js").read_text(encoding="utf-8")
INDEX_HTML = Path("templates/index.html").read_text(encoding="utf-8")
TUTORIAL_ENGINE = Path("codeup/learning/tutorial_engine.py").read_text(encoding="utf-8")


def test_onboarding_js_exists_and_exports_the_controller():
    assert "window.MinimalOnboarding = Controller;" in ONBOARDING_JS


def test_onboarding_is_exactly_five_linear_steps():
    ids = [
        "find_editor", "type_and_run", "output", "ask_codeup", "finish",
    ]
    for step_id in ids:
        assert f"id: '{step_id}'" in ONBOARDING_JS
    assert ONBOARDING_JS.count("id: '") == 5


def test_onboarding_does_not_teach_the_python_curriculum():
    """The whole point: this teaches the IDE, not Python. None of the
    concept-teaching vocabulary from the real curriculum belongs in the
    step content itself (the file's own header comment mentions the old
    tutorial's topics by name for context, which is fine)."""
    steps_start = ONBOARDING_JS.index("var STEPS = [")
    steps_end = ONBOARDING_JS.index("\n  ];", steps_start)
    low = ONBOARDING_JS[steps_start:steps_end].lower()
    for banned in ("variable", "condition", "if statement", "for loop", "while loop", "function definition"):
        assert banned not in low, f"onboarding.js step content should not teach {banned!r}"


def test_onboarding_covers_the_six_required_beats():
    low = ONBOARDING_JS.lower()
    assert "editor" in low
    assert 'print("hello")' in ONBOARDING_JS.lower() or "print, open parenthesis" in low
    assert "control and enter" in low or "ctrl+enter" in low
    assert "program output" in low
    assert "ask codeup" in low
    assert "isFinal: true" in ONBOARDING_JS


def test_onboarding_waits_for_a_real_run_not_just_continue():
    """Step 2 (type_and_run) only advances via _onRun(), proving a run
    actually happened - "continue" alone must not skip past it."""
    start = ONBOARDING_JS.index("id: 'type_and_run',")
    block = ONBOARDING_JS[start:start + 400]
    assert "waitsForRun: true" in block


def test_onboarding_hooks_the_same_run_result_globals_as_the_old_tutorial():
    assert "window._tutorialOnRunSuccess = this._boundRunSuccess;" in ONBOARDING_JS
    assert "window._tutorialOnRunError = this._boundRunError;" in ONBOARDING_JS


def test_onboarding_handles_continue_exit_and_fill_in_example_by_voice_or_typed():
    body = ONBOARDING_JS[ONBOARDING_JS.index("handleUtterance: function"):]
    assert "this.close(false); return true;" in body
    assert "this.fillExample(); return true;" in body
    assert "this.next(); return true;" in body


# ---- "Start tutorial" now launches the new flow, old one stays reachable --

def test_start_tutorial_action_gives_first_run_guidance_without_overlay():
    idx = STATIC_APP.index("else if (action === 'start_tutorial')")
    block = STATIC_APP[idx:idx + 700]
    assert "Write Python in the editor and press Control Enter or Run" in block
    assert ".open();" not in block.split("else if (action === 'skip_tutorial')", 1)[0]


def test_tutorial_button_opens_minimal_onboarding():
    idx = TUTORIAL_JS.index("on('tutorialBtn'")
    block = TUTORIAL_JS[idx:idx + 200]
    assert "window.MinimalOnboarding" in block
    assert "window.MinimalOnboarding.open();" in block


def test_banner_has_no_tutorial_entry_point():
    assert 'id="cuStartTutorialLink"' not in INDEX_HTML
    assert "Write Python below and press <strong>Ctrl+Enter</strong> or Run" in INDEX_HTML


def test_onboarding_overlay_markup_present_and_hidden_by_default():
    assert 'id="onboardingOverlay" hidden' in INDEX_HTML
    assert 'id="onboardingNextBtn"' in INDEX_HTML
    assert 'id="onboardingExampleBtn"' in INDEX_HTML
    assert 'id="onboardingExitBtn"' in INDEX_HTML


def test_tutorial_scripts_are_not_loaded_by_the_learner_ide():
    assert '<script src="/static/onboarding.js">' not in INDEX_HTML
    assert '<script src="/static/tutorial.js">' not in INDEX_HTML


# ---- old tutorial engine and TutorialController are untouched, dormant ---

def test_old_tutorial_overlay_markup_still_present_but_not_the_default():
    """Not deleted - just no longer what "Start tutorial" opens."""
    assert 'id="tutorialOverlay" hidden' in INDEX_HTML
    assert 'id="tutorialTopic"' in INDEX_HTML


def test_tutorial_engine_module_order_untouched():
    assert 'MODULE_ORDER: List[str] = ["print", "variables", "if", "for", "while"]' in TUTORIAL_ENGINE


def test_tutorial_controller_open_still_directly_callable():
    """window.TutorialController.open() must still work if something
    internal calls it directly - dormant, not broken."""
    assert "window.TutorialController = Controller;" in TUTORIAL_JS
    assert "open: function () {" in TUTORIAL_JS

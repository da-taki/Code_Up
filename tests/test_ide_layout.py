"""The /ide page should load calm and decluttered: only essential controls are
visible, advanced controls live inside accessible collapsed <details>, and the
command help stays available. Voice/typed/keyboard access must be preserved."""

import re
from pathlib import Path

import pytest

from app import app


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def _ide_html(client):
    return client.get("/ide").get_data(as_text=True)


def _details_blocks(html):
    # None of the IDE disclosures are nested, so a non-greedy match is exact.
    return re.findall(r"<details\b[\s\S]*?</details>", html)


def _visible_html(html):
    # Everything left after removing collapsed <details> is what shows on load.
    return re.sub(r"<details\b[\s\S]*?</details>", "", html)


def test_ide_has_no_redundant_mode_badge(client):
    html = _ide_html(client)
    assert 'id="cuModeStatus"' not in html


@pytest.mark.parametrize("control_id", ["runBtn", "voiceButton", "voiceText", "sendCommandBtn"])
def test_essential_controls_stay_visible_on_load(client, control_id):
    visible = _visible_html(_ide_html(client))
    assert f'id="{control_id}"' in visible, f"{control_id} must stay visible, not collapsed"


@pytest.mark.parametrize(
    "region_id",
    ["srAnnouncer", "srAlert", "output", "voiceStateIndicator"],
)
def test_live_regions_are_not_hidden_inside_collapsed_details(client, region_id):
    # Collapsed <details> render display:none, which would silence aria-live.
    visible = _visible_html(_ide_html(client))
    assert f'id="{region_id}"' in visible, f"{region_id} live region must remain announced"


@pytest.mark.parametrize(
    "control_id",
    [
        "colorVisionMode", "speechModeSelect", "textSizeMode", "motionToggle", "speechRateControl",
    ],
)
def test_advanced_controls_are_collapsed_into_details(client, control_id):
    collapsed = "\n".join(_details_blocks(_ide_html(client)))
    assert f'id="{control_id}"' in collapsed, f"{control_id} should be inside a collapsed <details>"


@pytest.mark.parametrize(
    "control_id",
    [
        "analyzeBtn", "fixBtn", "codeMapBtn", "stepNarrationBtn", "mistakeReplayBtn",
        "saveBtn", "languageSelector", "snippetSaveBtn", "clearInputsBtn", "toggleInputModeBtn",
    ],
)
def test_removed_advanced_controls_have_no_learner_facing_button(client, control_id):
    # Vision-Aid build: these are not merely tucked into a details/summary
    # anymore - the buttons are gone entirely (the underlying commands stay
    # reachable through Ask CodeUp; see app.js's action dispatch).
    assert f'id="{control_id}"' not in _ide_html(client)


def test_collapsed_sections_are_accessible_disclosures(client):
    blocks = _details_blocks(_ide_html(client))
    # Header (accessibility/display/speech settings) and "Show commands & help".
    assert len(blocks) >= 2
    for block in blocks:
        assert "<summary" in block, "every collapsed section needs a keyboard/SR summary label"


def test_ask_codeup_help_panel_has_simple_examples(client):
    html = _ide_html(client)
    assert 'id="cuHelpPanel"' in html
    for example in ("Why did this break?", "What did it print?", "Where am I?", "Why is this line indented?", "Give me a hint."):
        assert f"<li>{example}</li>" in html, f"help panel missing example: {example}"
    # Vision-Aid build: no technical command-parser categories.
    for group in ("Audio Blocks", "Export", "Debug", "Navigate", "Resume my context"):
        assert f">{group}</h3>" not in html, f"help panel should not list the {group} group in this build"


def test_audio_blocks_has_no_learner_facing_controls(client):
    # Vision-Aid build: the whole Audio Blocks panel (primary actions and
    # per-block editing controls alike) is removed from the learner UI.
    html = _ide_html(client)
    for token in ('aria-label="Compile blocks to Python"', 'aria-label="Compile and run blocks"'):
        assert token not in html
    collapsed = "\n".join(_details_blocks(html))
    for control_id in ("audioBlockMoveUpBtn", "audioBlockMoveDownBtn", "audioBlockOutdentBtn"):
        assert f'id="{control_id}"' not in collapsed
        assert f'id="{control_id}"' not in html


def test_no_inline_ai_model_pill_clutter(client):
    # The decorative "LLAMA 3.3" AI-model pill was removed from the header.
    assert "LLAMA 3.3" not in _ide_html(client)


def test_app_js_has_no_removed_mode_badge_dependency():
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "cuModeStatus" not in js


def test_app_js_applies_block_category_and_state_classes():
    js = Path("static/app.js").read_text(encoding="utf-8")
    for token in ("audio-block--", "audio-block--current", "audio-block--nested"):
        assert token in js, f"renderAudioBlocks should apply {token} for styling"


def test_code_mode_editor_shell_is_visible_by_default(client):
    html = _ide_html(client)
    visible = _visible_html(html)
    # The Monaco mount point and its region are part of the visible first screen.
    assert 'id="editor"' in visible
    assert 'id="codeModeRegion"' in visible
    region = re.search(r'<div[^>]*id="codeModeRegion"[^>]*>', html).group(0)
    assert "hidden" not in region, "Code Mode editor region must be visible on load"
    # Vision-Aid build: there is no Audio Blocks panel at all, hidden or not.
    assert 'id="audioBlocksPanel"' not in html


def test_editor_is_not_trapped_inside_a_collapsed_details(client):
    collapsed = "\n".join(_details_blocks(_ide_html(client)))
    assert 'id="editor"' not in collapsed, "the Monaco editor must not be inside a collapsed <details>"
    assert 'id="codeModeRegion"' not in collapsed


def test_app_js_forces_monaco_layout_after_load_and_mode_switch():
    # jsdom cannot measure Monaco, so assert the layout-hardening hooks exist:
    # a layout pass after first paint and when returning to Code Mode.
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "editor.layout()" in js, "an explicit Monaco layout call must exist"
    assert "requestAnimationFrame" in js, "layout should be forced on the next frame"

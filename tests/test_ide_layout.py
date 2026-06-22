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


def test_ide_shows_current_mode_badge_defaulting_to_code_mode(client):
    html = _ide_html(client)
    assert 'id="cuModeStatus"' in html
    visible = _visible_html(html)
    assert "Python Code Mode" in visible


@pytest.mark.parametrize("control_id", ["runBtn", "voiceButton", "voiceText", "sendCommandBtn"])
def test_essential_controls_stay_visible_on_load(client, control_id):
    visible = _visible_html(_ide_html(client))
    assert f'id="{control_id}"' in visible, f"{control_id} must stay visible, not collapsed"


@pytest.mark.parametrize(
    "region_id",
    ["srAnnouncer", "srAlert", "output", "commandUnderstanding", "voiceStateIndicator"],
)
def test_live_regions_are_not_hidden_inside_collapsed_details(client, region_id):
    # Collapsed <details> render display:none, which would silence aria-live.
    visible = _visible_html(_ide_html(client))
    assert f'id="{region_id}"' in visible, f"{region_id} live region must remain announced"


@pytest.mark.parametrize(
    "control_id",
    [
        "analyzeBtn", "fixBtn", "codeMapBtn", "stepNarrationBtn", "mistakeReplayBtn",
        "saveBtn", "languageSelector", "colorVisionMode", "dyslexiaToggle",
        "screenReaderModeToggle", "browserSpeechToggle", "snippetSaveBtn",
        "clearInputsBtn", "toggleInputModeBtn",
        "audioBlockMoveUpBtn", "audioBlockDeleteBtn", "audioBlockIndentBtn",
    ],
)
def test_advanced_controls_are_collapsed_into_details(client, control_id):
    collapsed = "\n".join(_details_blocks(_ide_html(client)))
    assert f'id="{control_id}"' in collapsed, f"{control_id} should be inside a collapsed <details>"


def test_collapsed_sections_are_accessible_disclosures(client):
    blocks = _details_blocks(_ide_html(client))
    # Header settings, snippets, project files, inputs, more tools, block actions, help...
    assert len(blocks) >= 6
    for block in blocks:
        assert "<summary" in block, "every collapsed section needs a keyboard/SR summary label"


def test_command_help_panel_groups_every_category(client):
    html = _ide_html(client)
    assert 'id="cuHelpPanel"' in html
    for group in (
        "Start", "Run", "Debug", "Navigate", "Edit",
        "Learn", "Audio Blocks", "Accessibility", "Project", "Export",
    ):
        assert f">{group}</h4>" in html, f"help panel missing the {group} group"


def test_audio_blocks_primary_actions_stay_but_editing_actions_collapse(client):
    html = _ide_html(client)
    # Primary block actions remain directly reachable.
    for token in ('aria-label="Compile blocks to Python"', 'aria-label="Compile and run blocks"'):
        assert token in html
    # Per-block editing spam is tucked into a "Block actions" disclosure.
    collapsed = "\n".join(_details_blocks(html))
    for control_id in ("audioBlockMoveUpBtn", "audioBlockMoveDownBtn", "audioBlockOutdentBtn"):
        assert f'id="{control_id}"' in collapsed


def test_no_inline_ai_model_pill_clutter(client):
    # The decorative "LLAMA 3.3" AI-model pill was removed from the header.
    assert "LLAMA 3.3" not in _ide_html(client)


def test_app_js_updates_mode_badge():
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "cuModeStatus" in js, "renderAudioBlocks should keep the mode badge in sync"


def test_app_js_applies_block_category_and_state_classes():
    js = Path("static/app.js").read_text(encoding="utf-8")
    for token in ("audio-block--", "audio-block--current", "audio-block--nested"):
        assert token in js, f"renderAudioBlocks should apply {token} for styling"

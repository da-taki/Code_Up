"""Vision-Aid first-ten-hours build: Audio Blocks must be unreachable by
default. The backend implementation (codeup/accessibility/audio_blocks.py)
is intentionally left intact rather than deleted; these tests lock in that
it stays *dormant* unless CODEUP_AUDIO_BLOCKS_ENABLED is explicitly set, so
a future accidental removal of that env check is caught immediately.

test_audio_blocks_mode.py and friends test the same backend with the flag
explicitly re-enabled (see their client fixtures) - this file is the
opposite: it proves the disabled default holds.
"""

import pytest

from app import app, AUDIO_BLOCKS_UNAVAILABLE_MESSAGE


@pytest.fixture
def client(monkeypatch):
    # Belt and suspenders: explicitly unset so a leftover value from the
    # environment (or an earlier test's monkeypatch) can never leak in.
    monkeypatch.delenv("CODEUP_AUDIO_BLOCKS_ENABLED", raising=False)
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def vc(client, text, **payload):
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


ENTRY_PHRASES = [
    "open audio blocks",
    "enter block mode",
    "switch to block mode",
    "enter audio blocks",
    "switch to audio blocks",
    "start audio blocks mode",
]


@pytest.mark.parametrize("phrase", ENTRY_PHRASES)
def test_entry_phrases_cannot_enter_audio_blocks_mode(client, phrase):
    data = vc(client, phrase)
    assert data.get("success") is not False
    assert data.get("action") == "deterministic_message"
    assert data.get("speech") == AUDIO_BLOCKS_UNAVAILABLE_MESSAGE
    assert data.get("message") == AUDIO_BLOCKS_UNAVAILABLE_MESSAGE
    # Never leaks the real Audio Blocks payload shape.
    assert "audio_blocks" not in data


def test_in_mode_commands_also_refused(client):
    # These are unambiguous even cold (without first being inside the
    # mode); a phrase like "list block categories" only resolves to an
    # Audio Blocks response once a workspace is already in that mode -
    # which, with entry refused, a learner can never reach - so it is not
    # tested here (it falls through to normal ambiguous-command handling).
    for phrase in ("add print block", "compile blocks", "run blocks"):
        data = vc(client, phrase)
        assert data.get("speech") == AUDIO_BLOCKS_UNAVAILABLE_MESSAGE, phrase


def test_active_mode_body_field_cannot_force_audio_blocks(client):
    # A raw API caller passing active_mode directly (not just a typed/voice
    # phrase) must not be able to force the mode either.
    data = vc(client, "run", code="print('hi')\n", active_mode="audio_blocks")
    assert data.get("audio_blocks") is None or data.get("audio_blocks", {}).get("activeMode") != "audio_blocks"


def test_disabled_message_does_not_shadow_unrelated_block_practice_feature(client):
    # "read block order" is shared vocabulary with the unrelated
    # accessible-learning block-practice drills; disabling Audio Blocks
    # Mode must not swallow that unrelated feature. Regression guard for
    # the naive "any phrase audio_blocks.handles() recognizes is refused"
    # approach, which was too broad.
    loaded = vc(client, "start block practice 1")
    assert loaded.get("speech") != AUDIO_BLOCKS_UNAVAILABLE_MESSAGE
    order = vc(client, "read block order")
    assert order.get("speech") != AUDIO_BLOCKS_UNAVAILABLE_MESSAGE


def test_no_workspace_state_persists_from_a_refused_attempt(client):
    vc(client, "open audio blocks")
    vc(client, "add print block")
    # A second, unrelated command must not observe any Audio Blocks state
    # a refused attempt might otherwise have leaked into session memory.
    data = vc(client, "run", code="print('hi')\n")
    assert data.get("audio_blocks") is None


def test_export_audio_blocks_route_is_unavailable_when_disabled(client):
    resp = client.post("/export-audio-blocks", json={})
    assert resp.status_code == 404
    data = resp.get_json()
    assert data["success"] is False
    assert data["message"] == AUDIO_BLOCKS_UNAVAILABLE_MESSAGE


def test_ide_page_has_no_audio_blocks_learner_ui(client):
    html = client.get("/ide").get_data(as_text=True)
    assert 'id="audioBlocksModeBtn"' not in html
    assert 'id="audioBlocksPanel"' not in html
    assert "<h3>Audio Blocks</h3>" not in html
    assert "In Audio Blocks:" not in html


@pytest.mark.parametrize("phrase", ENTRY_PHRASES)
def test_flag_re_enables_the_dormant_backend(monkeypatch, phrase):
    # Proves the backend genuinely still works end to end - it is disabled
    # by policy, not broken/deleted.
    monkeypatch.setenv("CODEUP_AUDIO_BLOCKS_ENABLED", "1")
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        data = vc(test_client, phrase)
    assert data.get("audio_blocks", {}).get("mode") == "audio_blocks"
    assert "Audio Blocks Mode opened" in data.get("speech", "")

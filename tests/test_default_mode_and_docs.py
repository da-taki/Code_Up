"""CodeUp must always start in Python Code Mode, Audio Blocks Mode is reachable
by both button and voice command, and the repository keeps a single consolidated
README instead of the recent extra markdown docs."""

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


def _tag(html, element, element_id):
    match = re.search(rf'<{element}[^>]*id="{element_id}"[^>]*>', html)
    assert match, f'could not find <{element} id="{element_id}">'
    return match.group(0)


def test_ide_starts_in_python_code_mode_by_default(client):
    html = _ide_html(client)
    # The Python editor region is visible and the Audio Blocks panel is hidden.
    assert "hidden" not in _tag(html, "div", "codeModeRegion")
    assert "hidden" in _tag(html, "section", "audioBlocksPanel")
    # The Code Mode button is the pressed/active mode on first load.
    code_button = _tag(html, "button", "codeModeBtn")
    assert 'aria-pressed="true"' in code_button
    blocks_button = _tag(html, "button", "audioBlocksModeBtn")
    assert 'aria-pressed="false"' in blocks_button


def test_ide_never_auto_opens_audio_blocks_from_stored_state():
    # The frontend no longer reads or writes a persisted programming mode, so a
    # fresh /ide load can never restore Audio Blocks Mode from localStorage.
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "codeupProgrammingMode" not in js


def test_audio_blocks_button_switches_directly():
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "audioBlocksCommand('open audio blocks')" in js
    assert "To enter Audio Blocks Mode, use voice and say open audio blocks." not in js


def test_voice_payload_includes_visible_programming_mode():
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "active_mode: window.activeMode || window._activeMode || 'python'" in js


@pytest.mark.parametrize(
    "text,payload",
    [
        ("run", {"code": "print('hi')\n"}),
        ("what can I do here", {}),
        ("where am i", {"code": "print('hi')\n", "cursor_line": 1}),
        (
            "read errors only",
            {"error": "NameError: name 'x' is not defined", "code": "print(x)\n"},
        ),
        ("preflight check", {"code": "print('hi')\n"}),
        ("run with step narration", {"code": "print('hi')\n"}),
    ],
)
def test_code_mode_commands_still_route(client, text, payload):
    data = client.post("/voice-command", json={"text": text, **payload}).get_json()
    assert data.get("success") is not False
    assert data.get("action") not in (None, "unknown")


DELETED_DOCS = [
    "docs/AUDIO_BLOCKS_MODE.md",
    "docs/ACCESSIBLE_CODING_PATHWAY.md",
    "docs/QA_MATRIX.md",
    "docs/SCREEN_READER_TEST_PLAN.md",
    "docs/TEACHER_REPORTS.md",
    "SECURITY.md",
]


@pytest.mark.parametrize("path", DELETED_DOCS)
def test_recent_feature_docs_are_removed(path):
    assert not Path(path).exists(), f"{path} should have been deleted"


def test_readme_has_key_sections_and_no_dead_doc_links():
    readme = Path("README.md").read_text(encoding="utf-8")
    for heading in (
        "# CodeUp",
        "## Python Code Mode",
        "## Audio Blocks Mode",
        "## Non-AI tools",
        "## Screen reader and assistive technology support",
        "## Safety model",
        "## Testing",
        "## License",
    ):
        assert heading in readme, f"README is missing section: {heading}"
    assert "starts in Python Code Mode" in readme
    assert "open audio blocks" in readme
    # No dead links to the deleted docs and no leftover "see docs/..." pointers.
    assert "docs/" not in readme
    for stem in ("AUDIO_BLOCKS_MODE", "ACCESSIBLE_CODING_PATHWAY", "QA_MATRIX",
                 "SCREEN_READER_TEST_PLAN", "TEACHER_REPORTS"):
        assert stem not in readme


def test_no_shipped_source_references_deleted_docs():
    # Scan shipped sources (not this test file) for references to deleted docs.
    targets = [Path("README.md"), Path("static/app.js")]
    targets += sorted(Path(".").glob("*.py"))
    targets += sorted(Path("templates").glob("*.html"))
    stems = (
        "AUDIO_BLOCKS_MODE",
        "ACCESSIBLE_CODING_PATHWAY",
        "QA_MATRIX",
        "SCREEN_READER_TEST_PLAN",
        "TEACHER_REPORTS",
    )
    for path in targets:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for stem in stems:
            assert stem not in text, f"{path} still references deleted doc {stem}"

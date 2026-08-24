import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import app


STATIC_APP = Path("static/app.js").read_text(encoding="utf-8")
VOICE_ENGINE = Path("static/voice-engine.js").read_text(encoding="utf-8")


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def ide_html(client):
    return client.get("/ide").get_data(as_text=True)


class HeadingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headings = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r"h[1-6]", tag):
            self._current = [int(tag[1]), ""]

    def handle_data(self, data):
        if self._current:
            self._current[1] += data

    def handle_endtag(self, tag):
        if self._current and tag == f"h{self._current[0]}":
            self.headings.append((self._current[0], " ".join(self._current[1].split())))
            self._current = None


class LandmarkParser(HTMLParser):
    LANDMARK_TAGS = {"main", "nav", "aside"}
    LANDMARK_ROLES = {"banner", "navigation", "main", "complementary", "contentinfo", "search", "region"}

    def __init__(self):
        super().__init__()
        self.landmarks = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        explicit_role = attr.get("role", "")
        label = attr.get("aria-label") or attr.get("aria-labelledby") or ""
        implicit_region = tag == "section" and bool(label)
        if tag in self.LANDMARK_TAGS or explicit_role in self.LANDMARK_ROLES or implicit_region:
            role = "region" if implicit_region and not explicit_role else explicit_role
            self.landmarks.append((tag, role, label))


def parse_attrs(html, element_id):
    match = re.search(rf"<[^>]+id=\"{re.escape(element_id)}\"[^>]*>", html)
    assert match, f"missing #{element_id}"
    return match.group(0)


def test_screen_reader_mode_defaults_browser_speech_off_without_manual_override():
    assert "BROWSER_SPEECH_OVERRIDE_KEY" in STATIC_APP
    assert "_browserSpeechUserOverride" in STATIC_APP
    assert "_browserSpeechEnabled = !_screenReaderModeEnabled" in STATIC_APP
    assert "Screen Reader Mode is on, so Browser Speech is off by default" in STATIC_APP


def test_manual_browser_speech_override_is_persisted():
    assert "localStorage.setItem(BROWSER_SPEECH_OVERRIDE_KEY, 'true')" in STATIC_APP
    assert "localStorage.setItem(BROWSER_SPEECH_KEY, String(_browserSpeechEnabled))" in STATIC_APP
    assert "Browser speech is on by manual choice while Screen Reader Mode is also on" in STATIC_APP


def test_live_regions_are_deduplicated_and_errors_use_one_assertive_path():
    assert "<div id=\"srAnnouncer\" role=\"status\" aria-live=\"polite\"" in Path("templates/index.html").read_text(encoding="utf-8")
    assert "<div id=\"srAlert\" role=\"alert\" aria-live=\"assertive\"" in Path("templates/index.html").read_text(encoding="utf-8")
    assert "< 1200" in STATIC_APP
    assert "out('ERROR:\\n' + (data.error || ''), { assertive: true, sr: false })" in STATIC_APP
    assert "speak(`Error${lineHint}: ${lastLine}`, { sr: false, priority: 'assertive' })" in STATIC_APP


def test_program_input_has_dedicated_focusable_field_and_cancel(client):
    html = ide_html(client)
    for token in ("programInputValue", "programInputSubmitBtn", "programInputCancelBtn", "programInputStatus"):
        assert f'id="{token}"' in html
    assert "showProgramInputControl" in STATIC_APP
    assert "input.focus()" in STATIC_APP
    assert "focusProgramInput" in STATIC_APP
    assert "setTimeout(focusProgramInput, 80)" in STATIC_APP
    assert "Type your answer in Program inputs" in STATIC_APP
    assert "await sendStreamingInput(txt)" in STATIC_APP
    assert "programInput.addEventListener('keydown'" in STATIC_APP
    assert "programSubmit.addEventListener('click', submitProgramInputValue)" in STATIC_APP
    assert "submitProgramInputValue" in STATIC_APP
    assert "cancelProgramInputRequest" in STATIC_APP
    assert "_programInputRequest.code || getCode()" in STATIC_APP
    assert "shouldFocusOutputAfterInput" in STATIC_APP


def test_heading_hierarchy_does_not_skip_levels(client):
    parser = HeadingParser()
    parser.feed(ide_html(client))
    headings = parser.headings
    assert headings.count((1, "CodeUp")) == 1
    names = [name for _, name in headings]
    for expected in ["Learning tools", "Code editor", "Program output", "Program inputs", "Commands"]:
        assert expected in names
    previous = 0
    for level, name in headings:
        assert level <= previous + 1 or previous == 0, f"heading skip before {name}: h{previous} to h{level}"
        previous = level


def test_landmark_labels_are_unique_and_regions_are_limited(client):
    """Updated by the accessibility semantic-placement audit
    (feature/accessibility-semantic-placement-audit): a prior pass here
    asserted ZERO regions at all. That was too blunt - Code editor, Program
    output, and Commands are three of the IDE's primary interaction areas
    in a voice-first tool, and jumping straight to them via NVDA/JAWS
    region navigation (the 'd' key) is a genuine, common need, not landmark
    spam. The bounded, deliberate set asserted below matches this audit's
    reasoned target list (see its final report) - it is NOT "add regions
    freely": every individual toolbar, card, checkpoint, and help-group
    category (Start/Run/Debug/...) explicitly stays un-landmarked, still
    enforced immediately below by
    test_command_help_groups_keep_headings_without_implicit_regions."""
    parser = LandmarkParser()
    parser.feed(ide_html(client))
    labels = [label for _, _, label in parser.landmarks if label]
    assert len(labels) == len(set(labels)), labels
    assert sum(1 for tag, _, _ in parser.landmarks if tag == "main") == 1
    region_labels = [label for _, role, label in parser.landmarks if role == "region"]
    assert set(region_labels) == {"codeEditorHeading", "programOutputHeading", "command-input-label"}, region_labels
    assert len(region_labels) == 3, "exactly one region each - no duplicates, no extras"



def test_named_sections_count_as_implicit_region_landmarks():
    parser = LandmarkParser()
    parser.feed('<section aria-label="Start commands"><h3>Start</h3></section>')
    assert ("section", "region", "Start commands") in parser.landmarks


def test_command_help_groups_keep_headings_without_implicit_regions(client):
    html = ide_html(client)
    forbidden_named_sections = [
        "Start commands", "Run commands", "Debug commands", "Navigate commands", "Edit commands",
        "Learn commands", "Audio Blocks commands", "Accessibility commands", "Project commands", "Export commands",
    ]
    for label in forbidden_named_sections:
        assert f'<section class="cu-help-group" aria-label="{label}">' not in html
        assert f'aria-label="{label}"' not in html
    assert '<div class="cu-help-group">' in html

    parser = LandmarkParser()
    parser.feed(html)
    region_labels = [label for _, role, label in parser.landmarks if role == "region"]
    for label in forbidden_named_sections + ["Commands"]:
        assert label not in region_labels

    heading_parser = HeadingParser()
    heading_parser.feed(html)
    heading_names = [name for _, name in heading_parser.headings]
    for heading in ["Commands", "Start", "Run", "Debug", "Navigate", "Edit", "Learn", "Audio Blocks", "Accessibility", "Project", "Export"]:
        assert heading in heading_names
def test_editor_escape_path_and_skip_links_exist(client):
    html = ide_html(client)
    assert 'href="#editor">Jump to editor' in html
    assert 'href="#output">Jump to output' in html
    assert 'id="leaveEditorBtn"' in html
    assert 'Press Escape when speech is quiet, or press Control+M' in html
    assert "editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyM" in STATIC_APP
    assert "leaveEditor();" in STATIC_APP


def test_monaco_error_markers_are_created_labelled_and_cleared(client):
    html = ide_html(client)
    assert 'id="errorSummary"' in html
    assert "monaco.editor.setModelMarkers(model, 'codeup-runtime'" in STATIC_APP
    assert "CodeUp error marker:" in STATIC_APP
    assert "glyphMarginClassName: 'cu-monaco-error-glyph'" in STATIC_APP
    assert "clearEditorErrorMarkers();" in STATIC_APP
    assert "monaco.editor.setModelMarkers(model, 'codeup-runtime', [])" in STATIC_APP


def test_speech_speed_and_voice_preferences_persist(client):
    html = ide_html(client)
    for token in ("speechRateControl", "speechVoiceSelect", "testVoiceBtn", "speechRateValue", "speechVoiceValue"):
        assert f'id="{token}"' in html
    assert "localStorage.setItem('codeupSpeechRate'" in STATIC_APP
    assert "localStorage.setItem('codeupSpeechVoice'" in STATIC_APP
    assert "speechSynthesis.getVoices" in STATIC_APP
    assert "voiceName" in VOICE_ENGINE


def test_output_speech_limit_is_documented_in_code_and_replay_available(client):
    html = ide_html(client)
    assert 'id="readOutputAgainBtn"' in html
    assert 'id="stopSpeechBtn"' in html
    assert "const CODEUP_SPOKEN_OUTPUT_LIMIT = 4000" in STATIC_APP
    assert "shortened for speech after" in STATIC_APP
    assert "function speakOutput()" in STATIC_APP
    assert "readAgain.addEventListener('click', speakOutput)" in STATIC_APP
    assert "stopSpeech.addEventListener('click'" in STATIC_APP
    assert "SpeechManager.cancelAll()" in STATIC_APP

"""Polish pass for NAB Delhi external testing: English-only UI, Chrome-first
compatibility messaging, a short calm page-load welcome, and removal of the
stray spoken "cycle…" phrase.

These tests pin the *product-readiness* contract a first-time blind learner (or
their trainer) meets before they touch any AI:

  * English is the default and only visible language; Hindi is hidden from the
    /ide gate, the command bar, the landing page, and TTS voice selection.
    (Backend Hindi parsing is deliberately NOT removed — see
    tests/test_voice_engine.py::TestVoiceCommands::test_voice_command_hindi.)
  * Both the landing page and the /ide start gate say CodeUp works best on
    Google Chrome and warn that Brave / privacy-heavy browsers may block the
    mic or speech, with a typed-command fallback.
  * The page-load welcome speech is one short line (Chrome + speak/type + start
    tutorial) and never the old multi-paragraph feature dump.
  * No spoken string contains "cycle" (the reported startup bug).

AI is fully disabled (incl. GROQ_API_KEY_2, the conversation brain) so routing
is deterministic — see the project memory note on Key 2.
"""
import os
import re

import pytest

import app as app_module

ROOT = os.path.dirname(os.path.dirname(__file__))

# Advanced features that must stay behind "help" / "say more" / "more examples"
# and never be spoken on page load or in the first onboarding reply.
ADVANCED_DUMP = (
    "replay mistake", "summarize structure", "make project report",
    "project report", "code map", "step narration", "mistake replay",
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.setenv("AI_ENABLED", "0")
    monkeypatch.setenv("GROQ_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def no_cloud(monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("onboarding routing must not call cloud AI")

    monkeypatch.setattr(app_module, "call_gemini", _fail)


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def _spoken(d):
    return (d.get("speech") or d.get("message")
            or (d.get("ai_action") or {}).get("spoken_confirmation") or "").strip()


DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _index_html():
    return _read("templates/index.html")


def _welcome_speech():
    """The single page-load welcome line spoken by startWithLang()."""
    html = _index_html()
    m = re.search(r"speak\('(Welcome to CodeUp[^']*)'\)", html)
    assert m, "page-load welcome speak('Welcome to CodeUp …') not found in index.html"
    return m.group(1)


def _spoken_literals(html):
    """Every simple string literal passed as the first arg to speak('…')/speak(\"…\").

    speak() is the only audible path (out()/srAnnounce() are silent), so this is
    the set of strings the learner can actually hear from the page shell.
    """
    return re.findall(r"speak\(\s*'((?:[^'\\]|\\.)*)'", html) + \
           re.findall(r'speak\(\s*"((?:[^"\\]|\\.)*)"', html)


# --------------------------------------------------------------------------- #
# 1. English-only / Hindi hidden
# --------------------------------------------------------------------------- #
class TestEnglishOnly:
    def test_ide_language_selector_is_english_only_and_disabled(self):
        html = _index_html()
        m = re.search(r'<select id="languageSelector".*?</select>', html, re.S)
        assert m, "languageSelector not found"
        block = m.group(0)
        assert 'value="en"' in block
        assert 'value="hi"' not in block
        assert "हिंदी" not in block
        assert "disabled" in block  # English shown selected + disabled

    def test_ide_has_no_hindi_start_button_or_voice_language_selector(self):
        html = _index_html()
        assert 'id="startHindi"' not in html
        assert 'id="voiceLangSelector"' not in html

    def test_ide_page_has_no_devanagari_text(self):
        # The /ide shell must not render any Hindi script for this release.
        assert not DEVANAGARI.search(_index_html())

    def test_default_response_language_is_english(self):
        # getLanguage() falls back to 'en' and reads the English-only selector.
        app_js = _read("static/app.js")
        # getLanguage() is a one-liner; the body contains a `|| {}` brace, so
        # match the whole line rather than up to the first '}'.
        m = re.search(r"function getLanguage\(\)[^\n]*", app_js)
        assert m, "getLanguage() not found"
        assert "|| 'en'" in m.group(0)

    def test_tts_never_resolves_a_hindi_voice(self):
        # _getVoiceForLang must return the resolved English voice (Hindi TTS is
        # inconsistent across browsers), never _hindiVoice, while Hindi is hidden.
        voice_js = _read("static/voice-engine.js")
        start = voice_js.index("function _getVoiceForLang(")
        block = voice_js[start:start + 600]
        assert "return _englishVoice" in block
        assert "return _hindiVoice" not in block

    def test_landing_bundle_does_not_advertise_hindi(self):
        bundle = _read("static/landing/dist/bundle.js")
        assert "Hindi" not in bundle
        assert "hi-IN" not in bundle
        assert "bilingual" not in bundle
        assert not DEVANAGARI.search(bundle)


# --------------------------------------------------------------------------- #
# 2. Chrome-first compatibility messaging
# --------------------------------------------------------------------------- #
class TestChromeMessaging:
    def test_ide_start_gate_recommends_chrome_and_warns_about_brave(self):
        low = _index_html().lower()
        assert "works best on google chrome" in low
        assert "brave" in low
        assert "privacy-heavy" in low

    def test_ide_start_gate_offers_typed_command_fallback(self):
        low = _index_html().lower()
        assert "command box" in low
        assert "type the command in the box" in low or "type a command" in low

    def test_landing_recommends_chrome_and_warns_about_brave(self):
        bundle = _read("static/landing/dist/bundle.js")
        assert "works best on Google Chrome" in bundle
        assert "Brave" in bundle
        assert "privacy-heavy" in bundle
        # Typed fallback stays first-class because voice support varies.
        assert "command box" in bundle


# --------------------------------------------------------------------------- #
# 3. Short, calm page-load welcome speech
# --------------------------------------------------------------------------- #
class TestStartupSpeech:
    def test_welcome_is_short(self):
        speech = _welcome_speech()
        assert len(speech) < 320, len(speech)  # one line, not a manual

    def test_welcome_recommends_chrome(self):
        assert "chrome" in _welcome_speech().lower()

    def test_welcome_offers_speak_or_type(self):
        low = _welcome_speech().lower()
        assert "speak or type" in low
        assert "command box" in low  # typed fallback named explicitly

    def test_welcome_suggests_start_tutorial(self):
        assert "start tutorial" in _welcome_speech().lower()

    def test_welcome_does_not_dump_a_huge_command_list(self):
        low = _welcome_speech().lower()
        for adv in ADVANCED_DUMP:
            assert adv not in low, adv

    def test_welcome_has_no_cycle_phrase(self):
        assert "cycle" not in _welcome_speech().lower()


# --------------------------------------------------------------------------- #
# 4. The reported "cycle through…" spoken bug is gone
# --------------------------------------------------------------------------- #
class TestCycleBugRemoved:
    def test_old_accessibility_announcement_is_not_spoken(self):
        html = _index_html()
        assert "Accessibility features available" not in html
        assert "to cycle color modes" not in html

    def test_no_spoken_string_in_the_page_shell_says_cycle(self):
        for literal in _spoken_literals(_index_html()):
            assert "cycle" not in literal.lower(), literal

    def test_startup_focus_lands_on_a_button_not_a_dropdown(self):
        html = _index_html()
        # The gate's primary action and the post-start focus are buttons, so a
        # screen reader never announces combobox/list navigation hints on load.
        assert re.search(r'<button id="startEnglish"[^>]*autofocus', html)
        assert "getElementById('tutorialBtn')" in html
        assert not re.search(r"<select[^>]*autofocus", html)


# --------------------------------------------------------------------------- #
# 5. "what can I do here" stays useful but short (now names a loop + "say more")
# --------------------------------------------------------------------------- #
class TestWhatCanIDoHere:
    def test_message_constant_covers_the_beginner_contract(self):
        msg = app_module._ONBOARDING_MESSAGE.lower()
        for kw in ("learn python", "speaking or typing", "start tutorial",
                   "insert a loop", "run", "explain", "fix this code",
                   "say more"):
            assert kw in msg, kw
        # Typed-command fallback is explicit (voice support varies by browser).
        assert "type the command" in msg
        # Still short: the advanced dump stays behind help / say more.
        for adv in ADVANCED_DUMP:
            assert adv not in msg, adv

    def test_route_returns_short_onboarding_without_cloud_ai(self, client, no_cloud):
        data = _vc(client, "what can I do here")
        assert data["action"] == "deterministic_message"
        assert data["onboarding"] is True
        low = _spoken(data).lower()
        assert "insert a loop" in low
        assert "say more" in low

    def test_say_more_and_more_examples_expose_the_longer_help(self, client, no_cloud):
        assert _vc(client, "say more")["action"] == "say_more"
        assert _vc(client, "more examples")["action"] == "more_help"


# --------------------------------------------------------------------------- #
# 6. Regression: the core NAB demo commands still route after the polish
# --------------------------------------------------------------------------- #
class TestDemoCommandsStillRoute:
    @pytest.mark.parametrize("text,expected", [
        ("what can I do here", "deterministic_message"),
        ("say more", "say_more"),
        ("help", "help"),
        ("start tutorial", "start_tutorial"),
        ("teach me Python", "start_tutorial"),
        ("run", "run"),
        ("stop everything", "stop_everything"),
        ("export this project", "export_project"),
    ])
    def test_command_routes(self, client, no_cloud, text, expected):
        assert _vc(client, text)["action"] == expected, text

    def test_insert_for_loop_still_inserts_a_real_loop(self, client, no_cloud):
        d = _vc(client, "insert a for loop that prints the first 3 whole numbers")
        assert d["action"] == "conversational_edit"
        assert d["ai_action"]["code"] == "for i in range(3):\n    print(i)"

    def test_noisy_asr_loop_still_repairs(self, client, no_cloud):
        d = _vc(client, "of for loop the Trends the first 3 whole numbers")
        assert d["action"] == "conversational_edit"
        assert d["ai_action"]["code"] == "for i in range(3):\n    print(i)"

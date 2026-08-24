import os
import re

import pytest

import app as app_module

ROOT = os.path.dirname(os.path.dirname(__file__))

ADVANCED_DUMP = (
    "replay mistake", "summarize structure", "make project report",
    "project report", "code map", "step narration", "mistake replay",
)


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


def _spoken_literals(html):
    return re.findall(r"speak\(\s*'((?:[^'\\]|\\.)*)'", html) + \
           re.findall(r'speak\(\s*"((?:[^"\\]|\\.)*)"', html)


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
        assert not DEVANAGARI.search(_index_html())

    def test_default_response_language_is_english(self):
        app_js = _read("static/app.js")
        m = re.search(r"function getLanguage\(\)[^\n]*", app_js)
        assert m, "getLanguage() not found"
        assert "|| 'en'" in m.group(0)

    def test_tts_never_resolves_a_hindi_voice(self):
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


class TestChromeMessaging:
    def test_ide_banner_recommends_chrome_and_warns_about_brave(self):
        low = _index_html().lower()
        assert "works best on google chrome" in low
        assert "brave" in low
        assert "privacy-heavy" in low

    def test_ide_banner_offers_typed_command_fallback(self):
        low = _index_html().lower()
        assert "command box" in low
        assert "type the command in the box" in low or "type a command" in low

    def test_landing_recommends_chrome_and_warns_about_brave(self):
        bundle = _read("static/landing/dist/bundle.js")
        assert "works best on Google Chrome" in bundle
        assert "Brave" in bundle
        assert "privacy-heavy" in bundle
        assert "command box" in bundle


class TestDirectToIdeNoGate:
    def test_no_blocking_start_gate_in_dom(self):
        html = _index_html()
        assert 'id="startGate"' not in html
        assert "Start in English" not in html
        assert 'id="startEnglish"' not in html
        assert 'id="startReturning"' not in html

    def test_command_box_is_present_immediately(self):
        html = _index_html()
        assert 'id="voiceText"' in html
        assert 'id="sendCommandBtn"' in html

    def test_non_blocking_banner_and_first_use_hint_exist(self):
        """#cuStartBanner is a one-time, dismissible getting-started tip, not
        a landmark: there is exactly one of it, it is encountered naturally
        as the first thing in <main>'s normal reading order, and it never
        changes after load - giving it role="region" would only add a
        low-value entry to NVDA's landmark list (see the accessibility
        semantic-placement audit). It is correctly plain content with a
        properly labelled Dismiss button, and is not a dialog."""
        html = _index_html()
        assert 'id="cuStartBanner"' in html
        assert 'id="cuFirstUseHint"' in html
        m = re.search(r'<div id="cuStartBanner"[^>]*>', html)
        assert m
        assert 'role="region"' not in m.group(0)
        assert 'aria-modal' not in m.group(0)
        dismiss = re.search(r'<button id="cuStartBannerDismiss"[^>]*>', html)
        assert dismiss and 'aria-label="Dismiss getting-started banner"' in dismiss.group(0)


class TestNoStartupSpeech:
    def test_no_auto_welcome_speech_on_load(self):
        lits = _spoken_literals(_index_html())
        for lit in lits:
            assert "welcome to codeup" not in lit.lower(), lit

    def test_no_auto_firefox_speech_on_load(self):
        for lit in _spoken_literals(_index_html()):
            assert "not supported in firefox" not in lit.lower(), lit

    def test_no_spoken_shell_string_dumps_a_command_list(self):
        for lit in _spoken_literals(_index_html()):
            low = lit.lower()
            for adv in ADVANCED_DUMP:
                assert adv not in low, (adv, lit)

    def test_saved_color_mode_restore_is_silent_on_load(self):
        html = _index_html()
        assert re.search(r"applyColorVisionMode\(colorMode,\s*true\)", html)
        assert re.search(r"function applyColorVisionMode\(mode,\s*silent\)", html)


class TestCycleBugRemoved:
    def test_old_accessibility_announcement_is_not_spoken(self):
        html = _index_html()
        assert "Accessibility features available" not in html
        assert "to cycle color modes" not in html

    def test_no_spoken_string_in_the_page_shell_says_cycle(self):
        for literal in _spoken_literals(_index_html()):
            assert "cycle" not in literal.lower(), literal

    def test_no_autofocus_traps_a_control_on_load(self):
        html = _index_html()
        assert "autofocus" not in html
        assert not re.search(r"<select[^>]*autofocus", html)
        assert not re.search(r"<select[^>]*autofocus", html)


class TestWhatCanIDoHere:
    def test_message_constant_covers_the_beginner_contract(self):
        msg = app_module._ONBOARDING_MESSAGE.lower()
        for kw in ("learn python", "speaking or typing", "start tutorial",
                   "insert a loop", "run", "explain", "fix this code",
                   "say more"):
            assert kw in msg, kw
        assert "type the command" in msg
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

"""End-to-end "stop" semantics in a real browser.

Real Flask server, real /ide page, real VoiceEngine/VoiceInput and app.js
dispatch. Only the microphone is replaced: an instrumented SpeechRecognition
is installed before any page script runs, and utterances are delivered
through it, so every command travels the real path
    recognizer -> VoiceInput (echo guard) -> handleVoiceCommand
    -> POST /voice-command -> handleConfirmedAction.

  "stop" / "be quiet"             -> CodeUp stops talking; voice stays on
  "stop listening" / "turn voice off" -> recognition off, remembered off
  "stop everything"               -> both

Integration module (needs Playwright Chromium); see tests/conftest.py.
"""

from __future__ import annotations

import socket
import threading

import pytest

import app as app_module

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from werkzeug.serving import make_server


def _chromium_available() -> bool:
    if sync_playwright is None:
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not available")

FAKE_MIC = r"""
(() => {
  window.__sr = { instances: [], fetches: [] };
  class FakeSR {
    constructor() { this.running = false; window.__sr.instances.push(this); }
    start() {
      if (this.running) throw new Error('InvalidStateError');
      this.running = true;
      setTimeout(() => { if (this.onstart) this.onstart(); }, 10);
    }
    stop() { if (!this.running) return; this.running = false; setTimeout(() => { if (this.onend) this.onend(); }, 10); }
    abort() { if (!this.running) return; this.running = false;
      setTimeout(() => { if (this.onerror) this.onerror({ error: 'aborted' }); if (this.onend) this.onend(); }, 10); }
    fail(error) { this.running = false; if (this.onerror) this.onerror({ error }); if (this.onend) this.onend(); }
    say(text) {
      if (this.onresult) this.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: true })] });
    }
  }
  window.SpeechRecognition = FakeSR;
  window.webkitSpeechRecognition = FakeSR;
  window.__live = () => window.__sr.instances.filter(r => r.running);
  window.__say = (text) => { const r = window.__live()[0]; if (!r) throw new Error('no live recognizer'); r.say(text); };
  window.__hidden = false;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => window.__hidden });
  const origFetch = window.fetch;
  window.fetch = function (url, opts) {
    if (String(url).includes('/voice-command')) {
      try { window.__sr.fetches.push(JSON.parse(opts.body).text); } catch (e) {}
    }
    return origFetch.apply(this, arguments);
  };
})();
"""


@pytest.fixture(scope="module")
def live_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    app_module.app.config.update(TESTING=False)
    server = make_server("127.0.0.1", port, app_module.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, live_server):
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.add_init_script(FAKE_MIC)
    pg.goto(f"{live_server}/ide")
    pg.wait_for_function("typeof editor !== 'undefined' && !!editor && typeof toggleVoice === 'function'")
    # Count every speech cancellation, whichever layer performs it.
    pg.evaluate("""() => {
      window.__cancels = 0;
      const orig = SpeechManager.cancelAll;
      SpeechManager.cancelAll = function () { window.__cancels++; return orig.apply(this, arguments); };
      const origVE = VoiceEngine.cancelSpeech;
      VoiceEngine.cancelSpeech = function () { window.__cancels++; return origVE.apply(this, arguments); };
    }""")
    yield pg
    ctx.close()


def _voice_on(page):
    page.evaluate("() => toggleVoice()")
    page.wait_for_function("window.__live().length === 1 && VoiceEngine.VoiceInput.isActive()")


def _state(page):
    return page.evaluate("""() => ({
      live: window.__live().length,
      instances: window.__sr.instances.length,
      enabled: VoiceEngine.VoiceInput.isEnabledByUser(),
      active: VoiceEngine.VoiceInput.isActive(),
      wanted: sessionStorage.getItem('codeupVoiceWanted'),
      button: document.getElementById('voiceButton').getAttribute('aria-pressed'),
      cancels: window.__cancels,
      fetches: window.__sr.fetches.slice(),
      output: document.getElementById('output').textContent,
    })""")


def _say_and_wait_for_server(page, text):
    before = len(_state(page)["fetches"])
    page.evaluate("(t) => window.__say(t)", text)
    page.wait_for_function("(n) => window.__sr.fetches.length > n", arg=before)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)


def _type_and_wait_for_server(page, text):
    before = len(_state(page)["fetches"])
    page.evaluate("(t) => handleCommandText(t)", text)
    page.wait_for_function("(n) => window.__sr.fetches.length > n", arg=before)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)


def _long_speech(page):
    page.evaluate("() => { _speechMode = 'codeup-voice'; speak('Here is a long explanation of your loop. "
                  "It repeats five times and adds each number to the total before printing it.'); }")


def test_a_bare_stop_while_speaking_cancels_speech_but_keeps_listening(page):
    _voice_on(page)
    _long_speech(page)
    cancels_before = _state(page)["cancels"]
    _say_and_wait_for_server(page, "stop")
    s = _state(page)
    assert s["cancels"] > cancels_before, "speech was cancelled"
    assert s["live"] == 1 and s["active"] and s["enabled"], "recognition stays on"
    assert s["wanted"] == "1", "the saved voice-on choice is untouched"
    assert s["instances"] == 1, "no new recognizer was created"
    assert s["button"] == "true"
    assert "Listening stopped" not in s["output"]


@pytest.mark.parametrize("phrase", ["be quiet", "stop talking"])
def test_other_speech_stop_phrases_keep_listening(page, phrase):
    _voice_on(page)
    _long_speech(page)
    _say_and_wait_for_server(page, phrase)
    s = _state(page)
    assert s["live"] == 1 and s["enabled"] and s["wanted"] == "1"


def test_b_bare_stop_when_not_speaking_is_harmless(page):
    _voice_on(page)
    _say_and_wait_for_server(page, "stop")
    s = _state(page)
    assert s["live"] == 1 and s["active"] and s["enabled"] and s["wanted"] == "1"
    assert s["instances"] == 1


@pytest.mark.parametrize("phrase", ["stop listening", "turn voice off"])
def test_c_d_explicit_voice_off_stays_off(page, phrase):
    _voice_on(page)
    _say_and_wait_for_server(page, phrase)
    page.wait_for_function("window.__live().length === 0")
    page.wait_for_timeout(3000)  # longer than any automatic restart backoff
    s = _state(page)
    assert s["live"] == 0 and not s["enabled"]
    assert s["wanted"] is None, "voice-off is remembered"
    assert s["button"] == "false"


def test_e_stop_everything_stops_speech_and_listening(page):
    _voice_on(page)
    _long_speech(page)
    cancels_before = _state(page)["cancels"]
    _say_and_wait_for_server(page, "stop everything")
    page.wait_for_function("window.__live().length === 0")
    page.wait_for_timeout(2000)
    s = _state(page)
    assert s["cancels"] > cancels_before
    assert s["live"] == 0 and not s["enabled"] and s["wanted"] is None


def test_f_voice_can_be_turned_back_on_with_exactly_one_recognizer(page):
    _voice_on(page)
    _say_and_wait_for_server(page, "stop listening")
    page.wait_for_function("window.__live().length === 0")
    _voice_on(page)
    page.wait_for_timeout(1500)
    s = _state(page)
    assert s["live"] == 1 and s["enabled"] and s["wanted"] == "1"


def test_g_hidden_visible_recovery_still_works_after_bare_stop(page):
    _voice_on(page)
    _say_and_wait_for_server(page, "stop")
    page.evaluate("() => { window.__hidden = true; document.dispatchEvent(new Event('visibilitychange'));"
                  " window.__live()[0].stop(); }")
    page.wait_for_timeout(2000)
    hidden = _state(page)
    assert hidden["live"] == 0 and hidden["enabled"], "no restart in a hidden tab, still wanted"
    page.evaluate("() => { window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); }")
    page.wait_for_function("window.__live().length === 1", timeout=5000)
    assert _state(page)["wanted"] == "1"


def test_h_aborted_recovery_still_works_after_bare_stop(page):
    _voice_on(page)
    _say_and_wait_for_server(page, "stop")
    page.evaluate("() => window.__live()[0].fail('aborted')")
    # isActive() flips on the recognizer's onstart, a timer tick after start();
    # wait for both, like _voice_on(), so a loaded machine cannot race it.
    page.wait_for_function("window.__live().length === 1 && VoiceEngine.VoiceInput.isActive()", timeout=5000)
    s = _state(page)
    assert s["enabled"] and s["active"] and s["wanted"] == "1"


def test_i_codeup_saying_stop_cannot_trigger_stop_through_echo(page):
    _voice_on(page)
    page.evaluate("() => speak('Say stop to interrupt me, or say stop listening to turn voice off.')")
    fetches_before = len(_state(page)["fetches"])
    cancels_before = _state(page)["cancels"]
    page.evaluate("() => { window.__say('stop listening'); window.__say('stop'); }")
    page.wait_for_timeout(1500)
    s = _state(page)
    assert len(s["fetches"]) == fetches_before, "echoed control words never reach the command pipeline"
    assert s["cancels"] == cancels_before
    assert s["live"] == 1 and s["enabled"] and s["wanted"] == "1"


@pytest.mark.parametrize("phrase", ["stop listening", "stop everything"])
def test_explicit_off_clears_saved_intent_during_hidden_restart_gap(page, phrase):
    _voice_on(page)
    page.evaluate("""() => {
      window.__hidden = true;
      document.dispatchEvent(new Event('visibilitychange'));
      window.__live()[0].stop();
    }""")
    page.wait_for_function("window.__live().length === 0")
    assert _state(page)["enabled"] and _state(page)["wanted"] == "1"

    _type_and_wait_for_server(page, phrase)
    page.wait_for_function("sessionStorage.getItem('codeupVoiceWanted') === null")
    page.evaluate("() => { window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); }")
    page.wait_for_timeout(2000)
    s = _state(page)
    assert s["live"] == 0 and not s["enabled"] and s["wanted"] is None


@pytest.mark.parametrize("phrase,voice_stays_on", [
    ("stop", True),
    ("stop listening", False),
    ("stop everything", False),
])
def test_stop_controls_keep_their_semantics_during_step_narration(page, phrase, voice_stays_on):
    _voice_on(page)
    page.evaluate("() => { _stepNarrationJob = { cancelled: false }; }")
    _say_and_wait_for_server(page, phrase)
    page.wait_for_timeout(500)
    s = _state(page)
    assert page.evaluate("() => _stepNarrationJob.cancelled") is True
    assert s["enabled"] is voice_stays_on
    assert (s["wanted"] == "1") is voice_stays_on
    assert (s["live"] == 1) is voice_stays_on

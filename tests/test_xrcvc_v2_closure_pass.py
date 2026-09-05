"""Regression tests for the XRCVC "CodeUp IDE - Testing Report Version 2"
closure pass (testing period 11 Aug 2026 - 2 Sep 2026).

A prior pass (see tests/test_xrcvc_v2_remediation.py) already addressed an
earlier rendition of this same report under a different issue numbering.
This file is anchored to the fuller Version 2 report text and only covers
gaps found during this closure pass: real bugs that survived the earlier
pass, verified here via the actual static/app.js and static/voice-engine.js
source (this codebase's established convention - see
test_xrcvc_v2_remediation.py - since there is no JS DOM runner wired into
pytest) or a live browser session, where noted.
"""
from pathlib import Path

STATIC_APP = Path("static/app.js").read_text(encoding="utf-8")
VOICE_ENGINE = Path("static/voice-engine.js").read_text(encoding="utf-8")
INDEX_HTML = Path("templates/index.html").read_text(encoding="utf-8")
UI_CSS = Path("static/style/ui-improvements.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Issue 1: exiting the code editor - the visible editor help text claimed
# "Tab indents inside the editor", directly contradicting Monaco's real,
# intentional behavior (accessibilitySupport:'on' makes Tab always move
# focus out of the editor, to avoid the keyboard trap XRCVC reported;
# indentation is Control+]/Control+[ instead - confirmed live: Tab, even
# over a multi-line selection, moves focus to the "Leave editor" button
# rather than indenting). A sighted low-vision user reading that help text,
# or a screen reader announcing it via aria-describedby, would be told the
# opposite of what actually happens.
# ---------------------------------------------------------------------------

def test_editor_help_text_does_not_claim_tab_indents():
    assert "Tab indents inside the editor" not in INDEX_HTML
    help_start = INDEX_HTML.index('id="editorHelp"')
    help_text = INDEX_HTML[help_start:help_start + 260]
    assert "Tab moves focus out of the editor" in help_text
    assert "Control right bracket" in help_text or "Control+]" in help_text.replace('&#93;', ']')


# ---------------------------------------------------------------------------
# Issue 2: "speech stops mid-output" (reproduced with a prime-number list
# read aloud stopping around 29). Two independent, compounding root causes
# in the real production speech path (static/voice-engine.js's VoiceEngine,
# not the SpeechManager fallback in app.js - see enqueue()'s own comment
# that VoiceEngine.speak is "the single speech path"):
#
#   1. Chrome's speechSynthesis silently stops producing audio - `speaking`
#      stays true, neither onend nor onerror fires - once a single
#      utterance has played for roughly 15 seconds, with no periodic
#      pause()+resume() "keep-alive" in VoiceEngine to reset that timer.
#   2. VoiceEngine's own "stuck utterance" safety timeout was a fixed
#      `text.length * 100`ms estimate that ignored the utterance's actual
#      speech rate (user-configurable 0.5x-2.0x via applySpeechRate/
#      speechRateControl) - at a slower rate CodeUp's own safety net could
#      cancel speech that was still legitimately playing.
#
# Behavioral coverage (the keep-alive actually firing/stopping, and the
# safety timeout not cutting off a slow-rate utterance early) lives in
# tests/voice_speech_chunking.test.js, run via node; this just guards the
# wiring so neither mechanism can be silently deleted.
# ---------------------------------------------------------------------------

def test_synth_keep_alive_watchdog_exists_and_is_wired_into_the_speak_path():
    assert "function _startSynthKeepAlive()" in VOICE_ENGINE
    assert "function _stopSynthKeepAlive()" in VOICE_ENGINE
    dequeue_start = VOICE_ENGINE.index("function _dequeueNarration()")
    dequeue_block = VOICE_ENGINE[dequeue_start:dequeue_start + 3000]
    assert "_startSynthKeepAlive();" in dequeue_block
    assert "_stopSynthKeepAlive();" in dequeue_block

    cancel_start = VOICE_ENGINE.index("function cancelSpeech()")
    cancel_block = VOICE_ENGINE[cancel_start:cancel_start + 400]
    assert "_stopSynthKeepAlive();" in cancel_block, (
        "cancelSpeech() must stop the keep-alive too, or it keeps kicking "
        "speechSynthesis after the user has already cancelled"
    )


def test_stuck_utterance_safety_timeout_scales_with_the_utterance_rate():
    dequeue_start = VOICE_ENGINE.index("function _dequeueNarration()")
    dequeue_block = VOICE_ENGINE[dequeue_start:dequeue_start + 2200]
    assert "item.text.length * 100" not in dequeue_block, (
        "the safety timeout regressed to a fixed per-character estimate that "
        "ignores speech rate - a slow rate will make it cancel speech early"
    )
    assert "_currentUtterance.rate" in dequeue_block
    assert "/ rate" in dequeue_block


# ---------------------------------------------------------------------------
# Issue 10 (CodeUp speech clashes with NVDA / "Voice mode can become stuck
# on 'command understanding'"): both places that send a typed or spoken
# command to /voice-command left the "Interpreting command."/"Interpreting
# voice command." status readout (#understoodCommand / #nextCommandAction,
# announced via updateCommandUnderstanding) stuck forever if the request
# never settled - no client-side timeout, and the existing catch block
# never reset the readout on failure either.
# ---------------------------------------------------------------------------

def test_typed_command_path_bounds_the_wait_and_resets_status_on_failure():
    fn_start = STATIC_APP.index("async function handleCommandText(")
    fn_end = STATIC_APP.index("\nasync function submitCommand(")
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "command_understanding_timeout" in fn_block
    assert "Promise.race" in fn_block
    catch_start = fn_block.index("} catch (err) {")
    catch_block = fn_block[catch_start:catch_start + 400]
    assert "updateCommandUnderstanding(" in catch_block


def test_voice_command_path_bounds_the_wait_and_resets_status_on_failure():
    fn_start = STATIC_APP.index("async function handleVoiceCommand(")
    fn_end = STATIC_APP.index("\nwindow.addEventListener('DOMContentLoaded'")
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "command_understanding_timeout" in fn_block
    assert "Promise.race" in fn_block
    catch_start = fn_block.rindex("} catch (e) {")
    catch_block = fn_block[catch_start:catch_start + 400]
    assert "updateCommandUnderstanding(" in catch_block


# ---------------------------------------------------------------------------
# Issue 18/24 (night mode / main page text size and contrast): the command
# palette's "ESC to close" hint was a plain <small> (an ~80% shrink) stacked
# on top of the header's own 11px base, landing at ~9px, plus a hardcoded
# `color: #666` inline style that ignored night/high-contrast theming
# entirely. Measured live in-browser: this rendered at 6.99:1 contrast
# against --bg-soft after the fix (comfortably above the 4.5:1 floor) vs.
# effectively unreadable before in some themes. Un-shrinking it and reusing
# the header's own --text-dim token fixes both at once and keeps it in sync
# with any future theme contrast tuning instead of drifting independently.
# ---------------------------------------------------------------------------

def test_command_palette_esc_hint_is_not_tiny_and_is_theme_aware():
    assert 'style="float:right; color: #666;"' not in INDEX_HTML
    assert 'class="cu-command-palette-esc-hint"' in INDEX_HTML
    assert ".cu-command-palette-esc-hint {" in UI_CSS
    rule_start = UI_CSS.index(".cu-command-palette-esc-hint {")
    rule_block = UI_CSS[rule_start:rule_start + 200]
    assert "font-size: 1em;" in rule_block
    assert "color: var(--text-dim);" in rule_block

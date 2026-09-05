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
    catch_block = fn_block[catch_start:catch_start + 900]
    assert "updateCommandUnderstanding(" in catch_block


def test_voice_command_path_bounds_the_wait_and_resets_status_on_failure():
    fn_start = STATIC_APP.index("async function handleVoiceCommand(")
    fn_end = STATIC_APP.index("\nwindow.addEventListener('DOMContentLoaded'")
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "command_understanding_timeout" in fn_block
    assert "Promise.race" in fn_block
    catch_start = fn_block.rindex("} catch (e) {")
    catch_block = fn_block[catch_start:catch_start + 700]
    assert "updateCommandUnderstanding(" in catch_block


# ---------------------------------------------------------------------------
# Product-polish pass (2026-09-05): a command/AI request failure (network
# error, timeout - not a normal "unknown command") previously only changed
# the small "Next action" status line with no visible cue, reading as dead
# air to a sighted user. An earlier version of this fix wrote the failure
# message into #output - but #output is the learner's actual PROGRAM output
# (confirmed live: running `print("Hello")`, then simulating a network
# failure on "explain it", left #output showing the failure message instead
# of "Hello"). That both destroyed a real result the learner still needed
# and could double-announce: #output carries its own aria-live="polite"
# that reacts to the DOM mutation on its own, regardless of the `sr:false`
# passed to out() (out()'s sr:false only skips out()'s own manual
# srAnnounce() call - see the near-identical warning in updateSpeechModeUI's
# comment). The corrected fix never touches #output for this: it makes the
# existing, already-announced status line (#nextCommandAction) visually
# distinct instead, via updateCommandUnderstanding's own isError flag.
# ---------------------------------------------------------------------------

def test_command_failure_never_writes_to_program_output():
    for fn_name, end_marker in [
        ("async function handleCommandText(", "\nasync function submitCommand("),
        ("async function handleVoiceCommand(", "\nwindow.addEventListener('DOMContentLoaded'"),
    ]:
        fn_start = STATIC_APP.index(fn_name)
        fn_end = STATIC_APP.index(end_marker, fn_start)
        catch_start = STATIC_APP.rindex("} catch (", fn_start, fn_end)
        catch_block = STATIC_APP[catch_start:fn_end]
        assert "out(" not in catch_block, (
            f"{fn_name} writes to #output on failure again - this overwrites "
            "the learner's actual program output. Live-verified regression: "
            "run print(\"Hello\"), then simulate a network failure on "
            "\"explain it\" - #output must still read \"Hello\\n\" afterward, "
            "not the failure message."
        )


def test_typed_command_failure_is_visually_distinct_via_the_status_line_only():
    fn_start = STATIC_APP.index("async function handleCommandText(")
    fn_end = STATIC_APP.index("\nasync function submitCommand(")
    catch_start = STATIC_APP.index("} catch (err) {", fn_start, fn_end)
    catch_block = STATIC_APP[catch_start:catch_start + 900]
    assert "isError: true," in catch_block


def test_voice_command_failure_is_visually_distinct_via_the_status_line_only():
    fn_start = STATIC_APP.index("async function handleVoiceCommand(")
    fn_end = STATIC_APP.index("\nwindow.addEventListener('DOMContentLoaded'")
    catch_start = STATIC_APP.rindex("} catch (e) {", fn_start, fn_end)
    catch_block = STATIC_APP[catch_start:catch_start + 900]
    assert "isError: true," in catch_block


def test_update_command_understanding_toggles_visible_error_styling():
    fn_start = STATIC_APP.index("function updateCommandUnderstanding(")
    fn_end = STATIC_APP.index("window.updateTranscriptStatus = updateCommandUnderstanding;", fn_start)
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "update.isError ? 'var(--danger)' : ''" in fn_block


# ---------------------------------------------------------------------------
# Accessibility gate (2026-09-05): live DOM/mutation tracing in Browser
# Speech OFF + Screen Reader Safe mode (the "NVDA profile" scenario) showed
# that updateCommandUnderstanding()'s isError update (native aria-live on
# #commandUnderstanding, always on, never toggled) and the plain speak(...)
# call right after it produced TWO separate live-region announcements for
# one failure: "Command failed. Try again." from the status line, then
# "Voice command failed." from speak()'s own srAnnounce() fallback (see
# speak()'s `!_browserSpeechEnabled` branch - with no `sr:false`, it calls
# srAnnounce() itself whenever browser speech is off). Confirmed this is
# NOT unique to these two catch blocks - the identical two-live-region
# shape (out()'s #output native aria-live + speak()'s own srAnnounce, or
# updateCommandUnderstanding's #commandUnderstanding native aria-live +
# speak()'s own srAnnounce) appears throughout the app whenever out()/
# updateCommandUnderstanding() and speak() are both called for the same
# event with browser speech off - confirmed live in a successful "what can
# I do here", a "generate code" response, and even plain Run/output
# ("Running..." + "Running code.", then the program output itself). That
# broader, pre-existing, systemic pattern predates this session's work
# entirely and is NOT touched here - fixing it everywhere would be a much
# larger, deliberate architectural change, out of scope for this gate.
# This guards only the two catch blocks actually touched by this pass.
# ---------------------------------------------------------------------------

def test_typed_command_failure_speak_does_not_duplicate_the_status_announcement():
    fn_start = STATIC_APP.index("async function handleCommandText(")
    fn_end = STATIC_APP.index("\nasync function submitCommand(")
    catch_start = STATIC_APP.index("} catch (err) {", fn_start, fn_end)
    catch_block = STATIC_APP[catch_start:catch_start + 1900]
    assert "speak(timedOut ? 'That took too long. Please try the command again.' : 'Voice command failed.', { sr: false });" in catch_block


def test_voice_command_failure_speak_does_not_duplicate_the_status_announcement():
    fn_start = STATIC_APP.index("async function handleVoiceCommand(")
    fn_end = STATIC_APP.index("\nwindow.addEventListener('DOMContentLoaded'")
    catch_start = STATIC_APP.rindex("} catch (e) {", fn_start, fn_end)
    catch_block = STATIC_APP[catch_start:catch_start + 1300]
    assert "speak(timedOut ? 'That took too long. Please try the command again.' : 'Voice command failed.', { sr: false });" in catch_block


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

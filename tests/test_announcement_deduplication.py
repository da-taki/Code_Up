"""Regression tests for the XRCVC announcement-deduplication pass.

The underlying mechanism (out()'s own-announcement suppression,
updateCommandUnderstanding()/showAI()'s announce:false) is covered
behaviorally in tests/announcement_ownership.test.js against a mock DOM.
This file instead anchors to the *specific* call sites for each of the
12 mandated events, so a future edit that quietly drops {sr:false} (or
reintroduces a redundant _srAnnounce() in tutorial.js) at one of those
call sites fails loudly here, even though the mechanism itself still
works everywhere else.
"""
from pathlib import Path

STATIC_APP = Path("static/app.js").read_text(encoding="utf-8")
TUTORIAL_JS = Path("static/tutorial.js").read_text(encoding="utf-8")


def _slice(src, start_marker, end_marker, start_from=0):
    start = src.index(start_marker, start_from)
    end = src.index(end_marker, start)
    return src[start:end], start, end


# ---------------------------------------------------------------------------
# Event 1/2/3: Run starts / Run completes / Program output
# ---------------------------------------------------------------------------

def test_run_start_message_has_one_owner():
    block, _, _ = _slice(STATIC_APP, "if (!usesInput) out(_runMsgOut", "try {\n    const payload")
    assert "speak(_runMsgSpoken, usesInput ? {} : { sr: false });" in block, (
        "Run-start speak() must stay silent on the live-region side only when "
        "out() actually wrote #output just above - if usesInput is true, out() "
        "never runs and this must remain the only announcement"
    )


def test_run_complete_output_has_one_owner():
    block, _, _ = _slice(STATIC_APP, "out(data.output, { sr: false });", "if (data.clear_inputs_after_run)")
    assert (
        "speak(formatRunOutputSpeech(data.output), "
        "{ forceFull: true, speechKind: 'program-output', sr: false });"
    ) in block, "the formatted run-output announcement must not duplicate #output's own native aria-live"


# ---------------------------------------------------------------------------
# Event 4/6: successful typed/voice command (routed through
# applyCommandUnderstanding -> handleConfirmedAction's deterministic_message
# branch for "what can I do here" and similar)
# ---------------------------------------------------------------------------

def test_apply_command_understanding_does_not_self_announce():
    fn_start = STATIC_APP.index("function applyCommandUnderstanding(")
    fn_end = STATIC_APP.index("\n}", fn_start)
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "announce: false" in fn_block, (
        "every successfully-dispatched command's status-line update must defer "
        "to that command's own richer out()/speak() announcement"
    )


def test_update_command_understanding_supports_announce_false():
    fn_start = STATIC_APP.index("function updateCommandUnderstanding(")
    fn_end = STATIC_APP.index("window.updateTranscriptStatus", fn_start)
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "update.announce === false" in fn_block
    assert "setTimeout(() => container.setAttribute('aria-live', 'polite'), 50);" in fn_block
    assert "requestAnimationFrame(" not in fn_block, (
        "requestAnimationFrame(...) does not fire in a backgrounded tab and "
        "would leave the region stuck silenced - this was a real bug found live"
    )


def test_deterministic_message_branch_has_one_owner():
    block, _, _ = _slice(
        STATIC_APP,
        "action === 'deterministic_message'",
        "else if (action === 'set_speech_rate')",
    )
    assert "speak((payload && payload.speech) || message, { sr: false });" in block


# ---------------------------------------------------------------------------
# Event 5/7: failed typed/voice command (accessibility gate pass)
# ---------------------------------------------------------------------------

def test_failure_paths_still_use_the_status_line_as_sole_owner():
    for fn_name, end_marker in [
        ("async function handleCommandText(", "\nasync function submitCommand("),
        ("async function handleVoiceCommand(", "\nwindow.addEventListener('DOMContentLoaded'"),
    ]:
        fn_start = STATIC_APP.index(fn_name)
        fn_end = STATIC_APP.index(end_marker, fn_start)
        catch_start = STATIC_APP.rindex("} catch (", fn_start, fn_end)
        catch_block = STATIC_APP[catch_start:fn_end]
        assert "isError: true" in catch_block
        assert "{ sr: false }" in catch_block
        assert "out(" not in catch_block, "must not overwrite the learner's program output on failure"


# ---------------------------------------------------------------------------
# Event 8: generate-code response
# ---------------------------------------------------------------------------

def test_generate_code_has_one_owner_at_every_branch():
    fn_start = STATIC_APP.index("async function generateCode(")
    fn_end = STATIC_APP.index("\n}", STATIC_APP.index("hideAI();", fn_start))
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "showAI('Generating code for: ' + prompt, { announce: false });" in fn_block
    assert "speak('Generating code for ' + prompt + '. One moment please.', { sr: false });" in fn_block
    # Every terminal branch's speak() must be silenced against its own out().
    assert fn_block.count("out(") == fn_block.count("{ sr: false }") - 1 or fn_block.count("speak(") >= 5, (
        "expected every out()/speak() pair after the initial one to carry sr:false"
    )
    for expected in (
        "speak(data.speech || message, { sr: false });",
        "speak('Code generation did not work. ' + reason, { sr: false });",
        "speak('Code generation failed.', { sr: false });",
    ):
        assert expected in fn_block, expected


# ---------------------------------------------------------------------------
# Event 9: Explain/Fix response
# ---------------------------------------------------------------------------

def test_analyze_code_has_one_owner():
    fn_start = STATIC_APP.index("async function analyzeCode(")
    fn_end = STATIC_APP.index("\nasync function analyzeDeep(")
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "showAI('Analyzing code with AI...', { announce: false });" in fn_block
    assert "speak('Analyzing code.', { sr: false });" in fn_block
    assert "speak(spoken, { sr: false });" in fn_block
    assert "speak('Analyze failed.', { sr: false });" in fn_block


def test_fix_code_has_one_owner():
    fn_start = STATIC_APP.index("async function fixCode(")
    fn_end = STATIC_APP.index("\nasync function describeLine(")
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "showAI('Fixing code with AI...', { announce: false });" in fn_block
    assert "speak('Fixing code.', { sr: false });" in fn_block
    assert "out(fixedSpeech, { sr: false }); speak(fixedSpeech, { sr: false });" in fn_block
    assert fn_block.count("speak('Fix failed.', { sr: false });") == 2


def test_describe_line_has_one_owner():
    fn_start = STATIC_APP.index("async function describeLine(")
    fn_end = STATIC_APP.index("\nasync function generateCode(")
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "showAI('Describing line ' + line, { announce: false });" in fn_block
    assert "out(data.description, { sr: false }); speak(data.description, { sr: false });" in fn_block


# ---------------------------------------------------------------------------
# Event 10: input-required state (was already correct; guard against
# regressing back to a double announcement)
# ---------------------------------------------------------------------------

def test_input_required_state_was_already_correct():
    fn_start = STATIC_APP.index("function handleProgramInputRequest(")
    fn_end = STATIC_APP.index("\nasync function submitProgramInputValue(")
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "out(`${inputRequestMessage(_programInputRequest)}\\nType or say the value now.`, { sr: false });" in fn_block
    assert "speak(message, { sr: false });" in fn_block


# ---------------------------------------------------------------------------
# Event 11: output replay - must remain announcement-free (no #output
# mutation to react to), relying solely on explicit/audible VoiceEngine
# speech, exactly as it already did before this pass.
# ---------------------------------------------------------------------------

def test_output_replay_does_not_touch_output_or_announce():
    fn_start = STATIC_APP.index("function speakOutput()")
    fn_end = STATIC_APP.index("\n}", fn_start)
    fn_block = STATIC_APP[fn_start:fn_end]
    assert "out(" not in fn_block
    assert "srAnnounce(" not in fn_block
    assert "explicit: true" in fn_block


# ---------------------------------------------------------------------------
# Event 12: tutorial status messages
# ---------------------------------------------------------------------------

def test_tutorial_no_longer_double_announces_start_and_exit():
    assert "_srAnnounce('Guided tutorial started." not in TUTORIAL_JS
    assert "_srAnnounce('Tutorial closed.')" not in TUTORIAL_JS
    assert "_srAnnounce('Practising " not in TUTORIAL_JS


def test_tutorial_srannounce_no_longer_shadows_speak_anywhere():
    # Every remaining _srAnnounce( occurrence should be the helper's own
    # definition or a comment mentioning it, not a live call site - a
    # future PR re-adding one right after a _speak() call would
    # reintroduce the exact bug this pass fixed.
    import re
    live_calls = [
        line for line in TUTORIAL_JS.splitlines()
        if re.search(r"_srAnnounce\(", line) and not line.strip().startswith("//")
        and "function _srAnnounce(msg)" not in line
    ]
    assert live_calls == [], (
        f"found live _srAnnounce(...) call site(s) outside the helper's own "
        f"definition: {live_calls!r} - does it duplicate a nearby _speak()?"
    )

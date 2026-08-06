# NVDA Review Fixes Verification

Date: 2026-08-06

Implementation commit deployed to production: `aab77c9e0ad6e638222b58c6d27b8605d7b87fd4` (`fix NVDA accessibility review issues`).

Production URL tested: https://code-up-fmqr.onrender.com/ide

Local URL tested: http://127.0.0.1:5000/ide

This report covers the NVDA accessibility review fixes for CodeUp. Automated checks, Playwright, and axe-core can verify structure, browser behaviour, and regressions, but they do not prove full NVDA compatibility. Real NVDA testing is still required.

## What Changed

- Screen Reader Mode now turns Browser Speech off by default unless the user manually overrides it. ARIA status and alert regions remain active.
- Browser Speech has clearer status text, persisted on/off override, persisted speech rate, persisted selected browser voice, and a Test Voice control.
- Reasonable program output is spoken completely, including the prime-number output through `47`; very long output is shortened with an explicit limit notice.
- Program output has Stop speech and Read output again controls.
- Runtime `input()` prompts now use a dedicated Program inputs field. Focus moves there, Enter submits, Escape/Cancel cancels, and successful completion moves focus to Program output when appropriate.
- Streaming input requests now use the same Program inputs path instead of sending users back to the command box.
- Syntax and runtime errors now create Monaco markers, a gutter indicator, a visible error summary, and one assertive announcement path. Markers clear on edit and after successful runs.
- `/ide` now has one H1, non-skipping headings, one main landmark, fewer regions, unique landmark labels, skip links, an editor escape path, and clearer settings headings.
- A data favicon link was added to prevent browser console 404 noise.

## Files Covered

Implementation commit files:

- `templates/index.html`
- `static/app.js`
- `static/voice-engine.js`
- `static/style/core.css`
- `tests/spoken_code.test.js`
- `tests/test_accessibility_speech_contract.py`
- `tests/test_ide_layout.py`
- `tests/test_nvda_review_fixes.py`
- `tests/test_run_output_speech.py`
- `tests/test_spoken_output_contract.py`

Verification report file:

- `docs/nvda-review-fixes.md`

Unrelated file intentionally untouched and uncommitted:

- `tutorial_engine.py`

## Local Automated Tests

All local checks passed after the final fixes:

- `py -m pytest tests -m "not slow and not integration and not exhaustive"` -> `1064 passed, 2035 deselected`.
- `py -m pytest tests/test_nvda_review_fixes.py tests/test_ide_layout.py tests/test_python_input_flow.py tests/test_run_output_speech.py tests/test_voice_accessibility_regressions.py tests/test_screen_reader_bridge.py tests/test_security_voice.py tests/test_error_trace.py tests/test_error_replay.py tests/test_demo_voice_commands.py tests/test_voice_engine.py` -> `210 passed, 379 deselected`.
- `node tests/spoken_code.test.js` -> `23 groups passed`.
- `node tests/voice_speech_chunking.test.js` -> `4 groups passed`.
- `node tests/live_assistant.test.js` -> `13 live assistant tests passed`.
- `node --check static/app.js` -> passed.
- `node --check static/voice-engine.js` -> passed.
- `git diff --check` -> passed.

## Local Browser Review

Tooling: Playwright with mocked browser speech plus axe-core against `http://127.0.0.1:5000/ide`.

Result: Passed.

Local workflows verified:

- Exactly one H1.
- Heading hierarchy has no skipped levels.
- Code editor, Program output, Program inputs, Commands, Learning tools, and accessibility settings are heading-reachable.
- Exactly one main landmark.
- Labelled landmarks are unique.
- No duplicate IDs.
- No unnecessary `role="region"` landmarks.
- Form controls and buttons have accessible names.
- Skip links appear first in tab order and move to editor/output targets.
- Screen Reader Mode defaults Browser Speech off.
- Browser Speech manual override persists after reload.
- Speech rate and browser voice preferences persist.
- Test Voice uses the selected voice/rate.
- Prime output through 50 visibly and verbally includes `47`.
- Stop speech and Read output again work.
- Runtime input focus moves to Program inputs, keeps Monaco source unchanged, handles two prompts, supports cancel, and moves focus to output after success.
- Syntax errors and runtime errors create visible/accessibility summaries and Monaco markers.
- Stale error markers clear after a successful run.
- `map my code`, `what can I do here`, and `start tutorial` still produce expected UI/output.
- Control+M and the Leave editor button move focus out of Monaco.
- No browser console errors.
- No page errors.

## Production Deployment Confirmation

Render deployment evidence available from this session:

- GitHub push to `main` succeeded for implementation commit `aab77c9e0ad6e638222b58c6d27b8605d7b87fd4`.
- Production polling initially timed out/not-yet-updated, then `https://code-up-fmqr.onrender.com/ide` exposed the new HTML and static JS markers on the third poll.
- Production `/ide` returned successfully after deployment.
- Production page source contained the new settings label, Program inputs control, output speech controls, and favicon fix.
- Production `/static/app.js` contained the new focus and Browser Speech markers, showing stale static assets were no longer being served.

No Render dashboard/API status was available in this environment, so deployment was confirmed through production responses and live browser behaviour.

## Production Browser Review

Tooling: Playwright with a fresh browser context, mocked browser speech, and axe-core against `https://code-up-fmqr.onrender.com/ide`.

Result: Passed.

Production workflows verified:

- Page loads successfully.
- New settings labels appear.
- Skip links work.
- Screen Reader Mode defaults Browser Speech off.
- Manual Browser Speech override persists after reload.
- Voice selection and speech-rate settings persist.
- Prime output reaches `47` visibly and in browser-speech text.
- Stop speech and Read output again work.
- Runtime input receives focus and does not edit Monaco source.
- Syntax errors create an accessible visible summary and Monaco marker.
- Runtime division-by-zero errors create a useful summary and marker.
- Headings and landmarks match the intended structure.
- Control+M and Leave editor work.
- No browser console errors.
- No page errors.
- No severe axe violations.

Production command smoke tests also passed with non-empty expected output and no console/page errors:

- `run`
- `generate code to print hello world`
- `walk me through this program`
- `what is inside the loop?`
- `explain the error`

## Axe Results

Local axe-core result: no violations reported in the final full browser state.

Production axe-core result: no violations reported in the final full browser state.

The audit explicitly checked document language/title coverage, heading order, landmark uniqueness, form labels, button names, duplicate IDs, ARIA validity, and serious/critical violations. These results do not by themselves establish WCAG compliance or full NVDA compatibility.

## Viewports Reviewed

Local and production browser checks covered:

- `1440 x 900`
- `1024 x 768`
- `768 x 1024`
- `390 x 844`

All four viewport checks reported zero horizontal overflow. No screenshots were created because the automated viewport metrics and interaction checks were sufficient for this verification pass.

## Bugs Found During Final Testing

- Live streaming input still directed users to the command input. Fixed by routing streaming prompts through the dedicated Program inputs control and preserving voice-based answers.
- Program input controls existed but were not all wired on `DOMContentLoaded`. Fixed Enter, Escape, Submit, Cancel, Read output again, Stop speech, and Leave editor bindings.
- After runtime input success, focus could be lost when the input field was disabled. Fixed by capturing whether Program inputs was active before hiding it and then focusing Program output.
- Axe initially flagged the getting-started banner outside landmarks. Fixed by moving it into `<main>` and restarting the local app to avoid stale template cache during verification.
- Browser audits reported favicon 404 console errors. Fixed by adding `<link rel="icon" href="data:,">`.
- Program input focus was flaky in Chromium after an async run prompt. Fixed by focusing immediately, on the next animation frame, and with a short retry.

## Remaining Limitations

- CodeUp does not detect whether NVDA itself is running; Screen Reader Mode is user controlled.
- Browser voice availability depends on the browser and operating system.
- Runtime line extraction for errors depends on reliable line numbers from Python error text.
- Automated Playwright and axe results cannot validate NVDA browse/focus mode quality.
- The tutorial overlay remains a complementary landmark only when active.

## Real NVDA Retest Required

Tejas and the XRCVC team should test with real NVDA on Windows:

1. Browser Speech stays off by default when Screen Reader Mode is enabled, while useful ARIA announcements remain.
2. Manually re-enabled Browser Speech persists and does not cause confusing overlap.
3. Prime output through `47` is understandable when Browser Speech is on, and NVDA users can still rely on the live region when Browser Speech is off.
4. Runtime input focus movement, prompt wording, Enter submit, Escape/Cancel, second prompt, and final output focus feel natural in NVDA.
5. Error summaries and Monaco markers are discoverable, announced once, and cleared after correction.
6. Heading navigation order is useful for beginners.
7. Landmark navigation is not noisy.
8. Escape, Control+M, Leave editor, Jump to editor, and Jump to output do not conflict with NVDA focus/browse workflows.
9. Settings wording is concise enough when read by NVDA.
10. Voice/rate controls are clear but do not imply NVDA users need Browser Speech.
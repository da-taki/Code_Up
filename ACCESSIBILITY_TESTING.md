# Accessibility Testing Checklist

Run this checklist before demos, releases, and school sessions. Record the browser, OS, assistive technology, result, issue found, and fix made.

| Area | Test | Pass condition | Result |
|---|---|---|---|
| NVDA | Navigate landing page, open IDE, run code, hear output | All core controls are announced with useful names |  |
| Keyboard only | Complete landing page to IDE to run flow without mouse | Focus is visible and no workflow traps focus |  |
| High contrast | Toggle high contrast and rerun code | Text, buttons, editor, dialogs, and trace remain readable |  |
| Hindi voice | Switch language and use Hindi run/help/line commands | Core Hindi commands are recognized or give clear fallback |  |
| Reduced motion | Enable OS reduced motion and app reduced motion | No required information depends on animation |  |
| No mouse | Save snippet, load snippet, run, debug, sonify, trace | Every task is reachable by keyboard or voice |  |
| Screen-reader modal behavior | Open go-to-line dialog, cancel, reopen, submit | Focus moves into dialog, label is announced, Escape closes |  |
| Speech interruption | Start long narration, press Escape | Speech stops immediately |  |
| Browser support | Test Chrome, Edge, Firefox | Chrome/Edge support speech recognition; Firefox has typed commands fallback |  |
| Error recovery | Trigger SyntaxError and IndentationError | Spoken explanation names the likely fix |  |

## Required Notes

- NVDA version:
- Browser and version:
- Keyboard layout:
- Microphone used:
- Tester role: student, teacher, reviewer, or developer
- Sighted assistance needed: none, setup only, or during task

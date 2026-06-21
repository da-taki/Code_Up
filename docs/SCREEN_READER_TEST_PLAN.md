# Screen reader test plan

This is a manual test plan, not a compatibility or certification claim.

## Platforms

Test the current `/ide` build with:

- NVDA and Chrome or Edge on Windows
- JAWS and Chrome or Edge on Windows
- Windows Narrator and Edge
- VoiceOver and Safari on macOS
- Orca and Firefox on Linux
- A Braille display with the user's normal screen reader, when available

Record the operating system, browser, screen reader, versions, speech settings, and whether browser text-to-speech is enabled.

## Core journey

1. Load `/ide` and reach the Python editor and typed command box by keyboard.
2. Run `print("Hello")` with Alt+Shift+R and confirm output is announced once.
3. Type `start learning path`; confirm the lesson and editor update are announced.
4. Type `start block practice`; read and move blocks without a pointer.
5. Trigger a syntax error, run `read errors only`, and navigate to the error.
6. Toggle navigation mode and use next symbol, next loop, next TODO, and current scope.
7. Toggle screen-reader mode with Alt+Shift+A and confirm the state change.
8. Export a project and open it in VS Code screen reader mode.

## Data and audio

Use a small CSV project to test `summarize csv`, `describe chart`, and `sonify column score`. Confirm all facts remain available as text even if Web Audio is blocked. Confirm `stop sonification` stops active tones.

## Pass criteria

- Keyboard focus is visible and predictable.
- Important results reach a live region and the output pane.
- Assertive errors do not repeatedly interrupt unrelated navigation.
- No task requires drag and drop or a visual-only chart.
- Speech can be stopped without changing editor content.
- Browser text-to-speech can be disabled to avoid duplicate speech.

## Current limitations

Automated tests validate ARIA and command contracts, not real auditory quality or daily assistive-technology usability. Microphone recognition, Braille output, browser audio policies, and exact screen-reader behavior need testing on each platform with real users.

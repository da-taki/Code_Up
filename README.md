# CodeUp

**A blind-first Python learning environment for visually impaired beginners.**

CodeUp is a browser-based IDE for learning Python without depending on visual scanning. It combines a real Python editor, Audio Blocks Mode, spoken feedback, code maps, debugging support, and teacher handoff tools.

The project focuses on Python structure: indentation, nesting, errors, output, and control flow. CodeUp is a working prototype, stable enough for demos and structured testing while broader assistive-technology validation continues.

## Python Code Mode

CodeUp starts in Python Code Mode. Learners write and run real Python with spoken support around output, errors, structure, and program flow.

Python Code Mode is the default workspace for editing, execution, debugging, and understanding code.

## Audio Blocks Mode

Audio Blocks Mode is a separate workspace for building beginner Python programs with accessible numbered blocks. It opens through the Audio Blocks button or the `open audio blocks` command.

Blocks can be compiled, run, explained, exported, or transferred into Python Code Mode when the learner is ready to work with the generated Python.

## Non-AI tools

Deterministic, non-AI features include:

* spoken output
* code maps
* spoken project map (single-file and multi-file)
* structure summaries
* error reading
* mistake replay
* Audio Blocks compile/run
* block-to-Python transfer
* project export
* teacher/trainer reports

Optional AI support can add explanations, but the core learning and routing tools do not depend on AI.

## Screen reader and assistive technology support

CodeUp includes screen-reader-aware workflows, live regions, keyboard-accessible controls, and spoken feedback for editor and command-box interaction.

Broader validation with NVDA, JAWS, VoiceOver, Orca, Braille displays, and more visually impaired learners and trainers is still ongoing.

## Safety model

Python Code Mode and Audio Blocks Mode are separated. Commands affect the active workspace only, and blocks move into the Python editor only through explicit transfer.

Python execution is controlled and sandboxed according to the app's existing design, but CodeUp should still be treated as a learning prototype rather than a general-purpose online judge.

## Testing

CodeUp has automated regression coverage for Python flow, Audio Blocks routing, block compile/run/transfer, exports, and accessibility-related command behavior.

Quick tests: `py -m pytest -q`

Full tests: `py -m pytest -q --run-full`

Docs tests: `py -m pytest -q -m docs`

Current status: working prototype, stable enough for demos and structured testing.

## Demo

[https://code-up-fmqr.onrender.com/ide](https://code-up-fmqr.onrender.com/ide)

## Tech stack

* Python
* Flask
* JavaScript
* HTML/CSS
* Browser speech APIs
* Optional AI support

## License

See LICENSE.

## Author

Built by Taknoor Singh.

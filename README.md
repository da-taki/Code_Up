# CodeUp

**A blind-first Python learning IDE for visually impaired beginners.**

CodeUp is a blind-first Python learning IDE that turns programming into a non-visual cockpit, helping visually impaired beginners understand project structure, debug errors, review code changes, inspect program state, navigate code, and generate teacher reports through speech, typed commands, and screen-reader-aware workflows.

Live demo: [https://code-up-fmqr.onrender.com/ide](https://code-up-fmqr.onrender.com/ide)

The project focuses on Python structure: indentation, nesting, errors, output, and control flow. CodeUp is a strong working prototype, stable enough for demos and structured testing while broader assistive-technology and learner validation continues.

## What CodeUp does now

CodeUp is no longer just a voice-controlled editor. It is a non-visual programming cockpit for blind and visually impaired beginners learning Python.

It helps learners:

* understand project structure with Project Map
* hear stack traces through Error Trace Narration
* review code changes through Audio Diff Review
* apply, reject, or undo fixes through Safe Apply/Reject
* inspect variables and execution through State and Variable Watch
* navigate code non-visually with line, function, error, change, and file-role commands
* use Live Assistant Mode for controlled spoken interaction
* generate teacher reports and learner recaps for pilots, trainers, and classrooms
* move gradually toward professional tools like VS Code with screen readers

## Command groups

Every capability is reachable by one canonical command; listed aliases also work. This is the single authoritative map of what to say.

1. **Learn Python (Programming Literacy Mode):** `start literacy mode`, `list lessons`, `start lesson`, `what am I learning`, `complete lesson`, `graduation report`.
2. **Get hints (Tutor Mode):** `start tutor mode`, `give me a hint`, `show fix`, `let me try again`, `fix with teaching`.
3. **Understanding Checks:** `check my understanding`, `quiz me on this code`, `give me a similar exercise`.
4. **Codex Handoff Pack:** `make codex handoff`.
5. **Teacher Reports:** `make a teacher report` (full session/cockpit summary), `teacher lesson report` (current literacy lesson).
6. **Cockpit (orient, debug, review, inspect, navigate):** `project map`, `explain error`, `what changed`, `show program state`, `where am I`.
7. **Audio Blocks:** `open audio blocks`, `read block map`, `read block order`, `switch to python code mode`.
8. **Intel showcase (optional):** `intel toolkit status`.

## Core cockpit features

### Project Map

Explains files, imports, functions, classes, entry points, and project structure without requiring the learner to visually scan a file tree.

### Error Trace Narration

Turns Python errors into beginner-friendly explanations with the crash line, error type, likely cause, and next step.

### Audio Diff Review

Explains what changed in the code, reads before/after snippets, labels risk, and supports undo.

### Safe Apply / Reject

Proposes fixes before applying them, so learners can ask for an explanation, accept the fix, reject it, or undo later.

### State and Variable Watch

Lets learners ask what variables exist, what a variable is now, what the program printed, and why a loop or condition behaved a certain way.

### Live Assistant Mode

Adds a controlled assistant layer with start/stop, pause/resume, stop speaking, repeat, and typed fallback when browser speech recognition is unavailable.

### Non-visual Navigation

Supports commands such as where am I, read imports, read functions, go to main function, next error, jump to changed line, and open the file that handles a role.

### Teacher Report and Session Recap

Generates a clear report for teachers, trainers, NGO reviewers, and pilots using project structure, errors, fixes, code changes, watched variables, and session activity.

## Learning bridge features

CodeUp focuses on the stage before professional coding agents: helping blind beginners understand code, errors, changes, and program state.

- **Tutor Mode** gives hints and explanations before fixes, so students can try again instead of immediately accepting generated code.
- **Codex Handoff Pack** creates a copyable summary of the learner's code, error, recent changes, program state, and questions to ask when moving to a professional coding agent.
- **Understanding Checks** create short questions and practice prompts from the current code/session.

## Programming Literacy Mode

CodeUp includes a beginner learning path for blind and visually impaired students who are learning Python from zero.

Programming Literacy Mode guides learners through short missions on printing, variables, conditions, loops, lists, and functions. Each mission includes starter code, commands to try, mistake practice, hints, understanding checks, and teacher-facing reports.

The goal is not to replace professional coding agents. The goal is to help beginners understand code non-visually before they move into tools like VS Code, GitHub, and Codex.

## Python Code Mode

CodeUp starts in Python Code Mode. Learners write and run real Python with spoken support around output, errors, structure, and program flow.

Python Code Mode is the default workspace for editing, execution, debugging, and understanding code.

CodeUp supports beginner `input()` programs. Learners can pre-set input values with commands like `use 16 as input`, or CodeUp can ask for missing input while running and continue automatically after the learner replies.

## Audio Blocks Mode

Audio Blocks Mode is a separate workspace for building beginner Python programs with accessible numbered blocks. It opens through the Audio Blocks button or the `open audio blocks` command.

Blocks can be compiled, run, explained, exported, or transferred into Python Code Mode when the learner is ready to work with the generated Python.

## Non-AI tools

Deterministic, non-AI features include spoken output, project maps, non-visual navigation, structure summaries, error trace narration, audio diff review, safe apply/reject with undo, state and variable watch, Live Assistant Mode, mistake replay, Audio Blocks compile/run, block-to-Python transfer, project export, and teacher/trainer reports.

Optional AI support can add explanations, but the core learning and routing tools do not depend on AI.

## Intel toolkit integrations

CodeUp includes optional Intel-focused demo tooling for accessibility and AI optimization experiments.

- **OpenVINO**: used for the local intent-classification demo path.
- **Intel Neural Compressor**: optional demo tooling for model-compression and quantization experiments around local intent models.
- **Intel Extension for Scikit-learn, powered by oneDAL**: optional benchmark path for accelerated classical ML experiments around command-intent classification.

These integrations are optional. The deployed CodeUp app does not require all Intel packages to run.

Optional install:

```bash
pip install -r requirements-intel.txt
```

Environment check:

```bash
python tools/intel/neural_compressor_demo.py --check-env
python tools/intel/sklearnex_benchmark.py --check-env
```

### Intel showcase commands

CodeUp can report its Intel integration status from inside the app:

```text
intel toolkit status
show intel optimization report
```

These commands show which Intel paths are available in the current environment and whether any local optimization or benchmark reports have been generated. No speedup is claimed unless measured locally.

## Screen reader and assistive technology support

CodeUp includes screen-reader-aware workflows, live regions, keyboard-accessible controls, and spoken feedback for editor and command-box interaction.

Broader assistive-technology validation is still ongoing; see Current validation status below for what has and has not been verified.

## Safety model

Python Code Mode and Audio Blocks Mode are separated. Commands affect the active workspace only, and blocks move into the Python editor only through explicit transfer.

Python execution is controlled and sandboxed according to the app's existing design, but CodeUp should still be treated as a learning prototype rather than a general-purpose online judge.

## Testing

CodeUp has 2,800+ automated tests covering command routing, sandboxed execution, accessibility flows, Audio Blocks, multi-file projects, project export, error narration, audio diff review, safe apply/reject, state watch, Live Assistant Mode, teacher reports, and non-visual navigation.

For a quick local check:

```bash
py -m pytest -q
```

For the full suite:

```bash
py -m pytest -q --run-full
```

Docs tests:

```bash
py -m pytest -q -m docs
```

## Current validation status

The deployed Render version has been sanity-checked through the browser for the main cockpit flow: error narration, fix proposal, apply, run, audio diff, state watch, navigation, teacher report, Live Assistant start/stop, and Audio Blocks opening.

Real microphone use, NVDA, JAWS, VoiceOver, Orca, Braille display workflows, and post-cockpit learner testing still need separate validation.

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

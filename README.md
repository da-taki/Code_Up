# CodeUp

A blind-first Python learning IDE for beginner programmers.

CodeUp is a Flask and browser app for visually impaired learners who are learning Python structure, input, errors, code changes, and program state through voice, typed commands, keyboard workflows, and screen-reader-aware output.

Live demo: https://code-up-fmqr.onrender.com/ide

## Features

- Voice and typed commands through the IDE command box, with Live Assistant controls for microphone workflows.
- Python Code Mode for writing, generating, editing, running, and explaining beginner Python programs.
- Deterministic beginner code generation for common prompts such as calculators, loops, input programs, marks averages, and password checkers.
- Voice generation/edit memory for follow-up changes like asking for a name, using a function, or explaining the latest edit.
- Safe Apply/Reject, Audio Diff Review, Project Map, Error Trace Narration, State and Variable Watch, and Non-visual Navigation.
- Python input flow with saved input values and runtime input continuation.
- Programming Literacy Mode with Tutor Mode, Understanding Checks, Codex Handoff Pack, learner recaps, and Teacher Reports.
- Multi-file projects with project summaries, file navigation, and project ZIP export.
- Groq model selection through `GROQ_MODEL`; the default model is `openai/gpt-oss-120b`.
- Optional Intel toolkit demos for local intent and optimization experiments.

## Modes

**Python Code Mode** is the default workspace. It handles generated code, follow-up edits, guided `input()` values, debugging help, state watch, audio diff review, project workflows, and safe edit application.

**Audio Blocks Mode** is a structure-first workspace for building beginner Python programs as ordered blocks before moving into full syntax. Some command paths refer to the same workflow as Code Blocks.

**Programming Literacy Mode** provides short Python learning missions, tutor hints, checks for understanding, learner recaps, and teacher-facing reports.

## Commands

- Generate: `make a calculator`, `make a marks average program`, `make a password checker`, `make a loop program`, `make an input program`.
- Edit: `now make it ask for name too`, `change it to use a function`, `make this code better but explain it like I am new`, `what changed`.
- Run and input: `run`, `use Taknoor as input`, `use 16 as input`, `read input values`, `clear input values`.
- Understand: `project map`, `explain error`, `read before and after`, `show program state`, `where am I`.
- Learn and report: `start literacy mode`, `start tutor mode`, `list lessons`, `check my understanding`, `make codex handoff`, `make a teacher report`.
- Audio Blocks: `open audio blocks`, `ask for age as number`, `ask for marks as decimal`, `read block map`, `read block order`, `run blocks`, `switch to python code mode`.
- Projects and demos: `read project files`, `open main dot py`, `make project zip`, `intel toolkit status`.

## Audio Blocks

Audio Blocks, also surfaced as Code Blocks in some command paths, is a structure-first workspace for blind beginners to build Python through numbered blocks before moving to full syntax.

- Blocks represent real Python structures: output, variables, math, conditions, loops, lists, functions, exceptions, imports, comments, and input.
- Numbered accessible blocks can be read, selected, moved, nested, edited, duplicated, deleted, undone, and redone.
- Input blocks compile to real Python input code, including number input with `int(input(...))` and decimal input with `float(input(...))`.
- Compile and run use the same Python runner as Python Code Mode.
- Block map and block order commands explain the current program structure without requiring sight.
- Source mapping connects each generated Python line back to the block that created it.
- Transfer commands move generated Python into Python Code Mode when the learner is ready to work with syntax.

## Architecture

CodeUp uses a Flask/Python backend with a browser frontend. The backend handles command routing, sandboxed Python execution, session memory, project files, reports, deterministic learning tools, Audio Blocks compilation, optional Groq AI, and optional Intel demos.

The frontend provides the editor, typed command box, voice controls, Live Assistant controls, Audio Blocks workspace, generated-code preview, output panes, and accessible status regions.

Cloud AI is optional. Core command routing, beginner templates, Audio Blocks, input handling, project maps, reports, and many learning tools work without a Groq key.

## Intel toolkit integrations

CodeUp includes optional Intel-focused demo tooling for accessibility and AI optimization experiments.

**OpenVINO**: used for the local intent-classification demo path.

**Intel Neural Compressor**: optional demo tooling for model-compression and quantization experiments around local intent models.

**Intel Extension for Scikit-learn, powered by oneDAL**: optional benchmark path for accelerated classical ML experiments around command-intent classification.

These integrations are optional. The deployed CodeUp app does not require all Intel packages to run.

### Intel showcase commands

CodeUp can report optional Intel integration status from inside the app with `intel toolkit status` and `show intel optimization report`.
No speedup is claimed unless measured locally.

Local optional checks:

```bash
pip install -r requirements-intel.txt
python tools/intel/neural_compressor_demo.py --check-env
python tools/intel/sklearnex_benchmark.py --check-env
```

## Validation

The repository includes automated coverage for command routing, README claims, sandboxed execution, Python input handling, follow-up code edits, Audio Blocks, Programming Literacy Mode, project export, audio diff review, safe apply/reject, state watch, teacher reports, and non-visual navigation.

The deployed app has also had sanity checks across the main cockpit flow, Audio Blocks flow, Live Assistant controls, input continuation, and reviewer-style beginner prompts. Validation notes are kept honest: optional AI and Intel paths depend on configuration.

## Limitations

CodeUp is a learning prototype, not a general-purpose online judge, and it is not intended to replace professional coding agents. Browser speech recognition depends on the user's browser and device. Optional AI and Intel paths depend on local or server configuration.

## Security

See [SECURITY.md](SECURITY.md) for reporting and demo-safety expectations.

## License

See [LICENSE](LICENSE).

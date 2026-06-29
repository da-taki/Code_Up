# CodeUp

A blind-first Python learning IDE for beginner programmers.

CodeUp is a Flask/browser application for visually impaired learners who are learning Python structure, input, errors, code changes, and program state through voice, typed commands, keyboard workflows, and screen-reader-aware output.

Live demo: https://code-up-fmqr.onrender.com/ide

## Features

- Python Code Mode for writing, generating, editing, running, and explaining beginner Python programs.
- Voice and typed commands for code generation, follow-up code editing, Python input flow, output review, and navigation.
- Voice generation/edit memory keeps follow-up commands attached to the current generated program instead of starting unrelated code.
- Error Trace Narration, Project Map, Audio Diff Review, Safe Apply/Reject, State and Variable Watch, and Non-visual Navigation.
- Programming Literacy Mode with Tutor Mode, Understanding Checks, Codex Handoff Pack, and Teacher Reports.
- Audio Blocks Mode for building beginner programs as ordered structure blocks before transferring them into Python Code Mode.
- Multi-file project support, project structure summaries, and project ZIP export.
- Optional Intel toolkit demos for local intent and optimization experiments.

## Commands

- Generate code: `make a program that asks for age and prints age plus one`, `make a calculator`, `make a marks average program`, `make a password checker`, `make a loop program`.
- Edit generated code: `now make it ask for name too`, `change it to use a function`, `make it print the result at the end`, `add comments`, `change it to a while loop`.
- Run and input: `run`, `use 16 as input`, `insert Taknoor as value`, `read input values`, `clear input values`.
- Understand code: `project map`, `explain error`, `what changed`, `read before and after`, `show program state`, `where am I`.
- Learn: `start literacy mode`, `start tutor mode`, `give me a hint`, `check my understanding`, `make codex handoff`, `make a teacher report`.
- Audio Blocks: `open audio blocks`, `ask for age as number`, `ask for marks as decimal`, `read block map`, `run blocks`, `transfer blocks to Python mode`.
- Projects: `read project files`, `open main dot py`, `export this project`, `make project zip`.
- Intel status: `intel toolkit status`.

## Modes

**Python Code Mode** is the default workspace for editing and running Python. It supports generated code, follow-up edits with session memory, guided `input()` values, debugging, state watch, audio diff review, and project workflows.

**Audio Blocks Mode** is a structure-first workspace for building beginner Python programs before writing full syntax. Blocks compile into Python, run through the normal execution flow, expose block order/map information, and can transfer into Python Code Mode.

**Programming Literacy Mode** provides short Python learning missions, tutor hints, understanding checks, learner recaps, and teacher-facing reports.

## Architecture

CodeUp uses a Flask/Python backend with a browser frontend. The backend handles command routing, sandboxed Python execution, session memory, project files, reports, deterministic learning tools, Audio Blocks compilation, and optional AI/Intel paths. The frontend provides the editor, command box, speech controls, Audio Blocks workspace, and accessible output surfaces.

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

The repository includes automated tests for command routing, README claims, sandboxed execution, Python input handling, Audio Blocks, project export, audio diff review, safe apply/reject, state watch, teacher reports, and non-visual navigation.

The deployed app has been sanity-checked for the main cockpit flow, including error narration, fix proposal, apply/reject, run, input handling, audio diff, state watch, navigation, teacher reports, Live Assistant start/stop, and Audio Blocks opening. Mic-based voice command testing has covered the main command flow.

## Limitations

CodeUp is a learning prototype, not a general-purpose online judge, and it is not intended to replace professional coding agents. Browser speech recognition availability depends on the user's browser and device. Optional AI and Intel paths depend on local configuration and are not required for the deployed app.

## Security

See [SECURITY.md](SECURITY.md) for supported reporting and safe demo-use expectations.

## License

See [LICENSE](LICENSE).

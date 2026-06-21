# CodeUp

CodeUp is a Python-first learning IDE for visually impaired beginners.

It has two learning modes.

1. **Python Code Mode** — the normal mode. Students write real Python, run it, hear output, debug errors, trace execution, navigate code, and export projects.
2. **Audio Blocks Mode** — a separate voice-opened mode for first-time beginners. Students build programs using numbered accessible blocks. CodeUp then compiles those blocks into real Python so the learner can move into Code Mode.

CodeUp is not trying to replace VS Code, NVDA, JAWS, VoiceOver, Narrator, Orca, Braille displays, Quorum, or other accessibility tools. It is a bridge for beginners who need help understanding code structure, indentation, errors, loops, functions, and debugging before moving into full professional editors.

Demo:

https://code-up-fmqr.onrender.com/ide

## Why this exists

Programming can be hard to start when the interface assumes visual scanning.

A beginner often has to track:

* where the cursor is
* which block they are inside
* whether indentation is correct
* what changed after running the code
* where an error happened
* how variables changed step by step
* how to move from learning examples into real projects

CodeUp tries to make those steps audible, structured, and beginner-friendly.

## Start here

When the IDE opens, it starts in Python Code Mode.

Try these first:

```text
insert print hello world
run
what can I do here
preflight check
read errors only
run with step narration
```

To enter Audio Blocks Mode, use voice and say:

```text
open audio blocks
```

Audio Blocks Mode is voice-opened on purpose. The normal editor stays as the default so CodeUp does not surprise users or replace the Python workspace.

## Python Code Mode

Python Code Mode includes:

* Python editor
* spoken output
* beginner error messages
* code map
* indentation checks
* run history
* last output and last error recall
* step narration
* variable watch
* mistake replay
* safe editing commands
* project checks
* export support

Useful commands:

```text
run
read last output
read last error
where is my cursor
read around me
give me a code map
check indentation
show code stats
go to definition of total
find references to total
rename total to score
comment this line
duplicate this line
```

## Audio Blocks Mode

Audio Blocks Mode is for learners who are not ready to type full Python yet.

It uses numbered blocks instead of drag-and-drop-only blocks. This matters because drag-and-drop is often not enough for blind users.

In Audio Blocks Mode, students can:

* add blocks by voice or typed command after entering the mode
* hear the current block order
* move blocks up or down
* nest blocks inside loops, conditions, and functions
* edit block values
* undo and redo block changes
* preview generated Python
* run blocks through the same CodeUp sandbox
* export blocks and generated Python

Example flow:

```text
open audio blocks
add variable total equals 0
add repeat 3 times block
add change total by 1
put block 3 inside block 2
add print variable total
preview generated code
run blocks
```

Generated Python:

```python
total = 0
for i in range(3):
    total += 1
print(total)
```

Audio Blocks Mode has block categories for:

* output
* variables
* math
* conditions
* loops
* lists
* functions
* input
* comments

It also has built-in block lessons for hello world, variables, loops, lists, functions, and converting blocks into Python.

## Non-AI tools

Most of CodeUp works without AI.

Deterministic tools include:

```text
preflight check
check indentation
read errors only
show safe imports
explain blocked import
show code stats
show nesting depth
read current block
outline this file
go to definition of total
find references to total
check names
check beginner style
compare output to 3
show run history
reset run state
```

These tools use Python parsing, AST analysis, tokenizer checks, sandbox state, trace data, and project metadata.

They do not need Gemini, Groq, Ollama, OpenAI, or any cloud AI key.

## Learning tools

CodeUp includes:

* 12-lesson Python learning path
* accessible Parsons practice
* Audio Blocks lessons
* error practice challenges
* request-only hint ladder
* beginner style checks
* teacher reports

Learning commands:

```text
start learning path
next lesson
check lesson
give lesson hint
start block practice
check block order
start error practice
check error fix
generate teacher report
```

Hints are request-only. CodeUp should not interrupt beginners with automatic suggestions.

## Screen reader and assistive technology support

CodeUp is designed to work alongside existing assistive technology.

Supported profiles:

* NVDA
* JAWS
* Windows Narrator
* VoiceOver
* Orca
* VS Code handoff

CodeUp includes:

* screen reader mode
* polite live region for normal status
* assertive live region for errors
* optional browser speech toggle
* keyboard-first controls
* accessible command output
* VS Code export notes

Commands:

```text
enable screen reader mode
set screen reader to NVDA
set screen reader to JAWS
set screen reader to VoiceOver
show screen reader tips
show keyboard shortcuts
```

Important limitation: actual NVDA, JAWS, Narrator, VoiceOver, Orca, Braille-display, microphone, and audible-TTS testing still needs real users and real platform hardware.

## VS Code handoff

CodeUp is meant to help students move toward real Python workflows.

Exports can include:

* generated Python files
* Audio Blocks workspace JSON
* accessibility notes
* safe VS Code settings
* teacher reports when requested

The goal is:

```text
Audio Blocks Mode -> Python Code Mode -> VS Code with screen reader support
```

## Safety model

CodeUp runs learner Python through a restricted sandbox.

The sandbox is meant for beginner learning, not for hosting an unrestricted public online judge.

CodeUp blocks or limits risky behavior such as:

* unsafe imports
* dangerous builtins
* direct file/system access
* long-running loops
* oversized traces
* unsafe generated code from blocks

Security notes:

* The sandbox reduces risk but does not replace a container, VM, or production judge.
* AI is not used for security decisions.
* Browser speech and microphone permissions depend on the browser.
* Windows may not support the same process limits as Linux.

To report a security issue, open a GitHub issue with the route or feature involved, minimal steps to reproduce, and your browser and OS.

## Related tools

CodeUp is not the only accessibility project in programming.

Quorum is an accessible programming language and learning ecosystem. CodeUp has a different focus: mainstream Python and transition into tools like VS Code.

Screen readers like NVDA, JAWS, Narrator, VoiceOver, and Orca are still important. CodeUp is designed to work beside them, not replace them.

## Testing

Recent validation has included:

* full Python test suite
* focused accessibility regression tests
* frontend Node tests
* Ruff
* Python compile checks
* JavaScript syntax checks
* no-AI guard tests
* local `/ide` smoke tests
* `/accessibility` route checks
* `/accessible-coding-tools` route checks
* Audio Blocks Mode smoke tests

Do not overread this. Automated tests do not prove full accessibility. Real testing with daily screen reader users is still needed.

## Local setup

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the app:

```bash
python app.py
```

Run tests:

```bash
python -m pytest -q
ruff check .
python -m compileall .
```

If frontend tests exist in your checkout, run the existing Node test command used by the repo.

## Project status

CodeUp is still a student-built accessibility project.

It has been tested through automated tests and early demonstrations, but it still needs more feedback from visually impaired learners, trainers, and screen reader users.

The current focus is simple:

```text
make beginner Python easier to hear, understand, debug, and eventually outgrow
```

## License

MIT

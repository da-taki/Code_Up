# CodeUp

Blind-first Python IDE for beginners.

CodeUp is a Flask app that lets visually impaired students write, run, edit, and understand Python with voice commands, typed commands, keyboard workflows, and screen-reader-aware output.

Live demo: https://code-up-fmqr.onrender.com/ide

## Features

* Write and run beginner Python programs
* Generate programs from commands
* Edit code with follow-up commands
* Hear what changed after an edit
* Get spoken error help
* Track variables and program state
* Save and continue `input()` values
* Build programs with Audio Blocks
* Read project structure without needing sight
* Export multi-file projects
* Create learner recaps and teacher reports

## Modes

### Python Code Mode

The main workspace.

This is where most commands run. It handles code generation, edits, program runs, project files, error help, state watch, code maps, audio diffs, and safe apply/reject flows.

### Audio Blocks Mode

A block-based workspace for learning Python structure.

Students can create numbered blocks for output, variables, input, math, conditions, loops, lists, functions, comments, and basic program flow.

CodeUp can read the block order, explain the structure, run the program, and move the generated Python into the main editor.

### Programming Literacy Mode

Small Python lessons with tutor hints, checks for understanding, learner recaps, and teacher reports.

## Commands

### Generate code

```text
make a calculator
make a marks average program
make a loop program
make a password checker
make a program that asks for [value]
```

### Edit code

```text
make it use a function
add a loop
rename [old name] to [new name]
insert print [text]
comment this line
make this code better
```

### Understand code

```text
project map
give me a code map
where does the program start
where am I
read around me
what variables exist
show program state
step through this
```

### Debug

```text
explain error
where did it crash
what caused this
what value caused this
fix with explanation
read last error
```

### Review changes

```text
what changed
read before and after
explain this change
is this risky
apply
reject
undo last change
```

### Audio Blocks

```text
open audio blocks
list block categories
add print block
add variable block
ask for [value] as number
read block order
compile blocks to Python
run blocks
transfer blocks to Python mode
switch to Python Code Mode
```

### Learn and report

```text
start literacy mode
list lessons
start [lesson name] lesson
check lesson understanding
complete lesson
start tutor mode
give me a hint
show fix
check my understanding
make a teacher report
```

### Projects, voice, and accessibility

```text
read project files
open [file name]
create file [file name]
run [file name]
export this project
export for VS Code
start live assistant
pause listening
stop speaking
enable screen reader mode
set screen reader to [screen reader]
intel toolkit status
```

### Input

```text
read input prompt
use [text] as input
use [number] as input
read input values
clear input values
```

## Tech stack

* Python
* Flask
* HTML
* CSS
* JavaScript
* Browser speech APIs
* Optional Groq AI
* Optional Intel demo tooling

Most core learning flows work without a Groq key. AI features need configuration.

## AI use declaration

AI was used in this project.

The frontend was heavily AI-assisted. Vibe coded, basically. I used AI to get the browser UI moving, then tested it, changed it, broke it, fixed it, and shaped it around the demo.

Some backend base code was also AI-generated. I built on top of it, reviewed the logic, connected the flows, fixed bugs, added tests, and made the final decisions.

Project direction, feature choices, testing, demos, and submission are mine.

Built by Da-Taki.

## Intel tool-kits

CodeUp includes optional Intel demo paths for local intent classification and optimization experiments.

The deployed app does not need the Intel packages to run.

## Limits

CodeUp is a prototype. Browser speech depends on the browser and device. AI features need keys.

## Security

See `SECURITY.md`.

## License

See `LICENSE`.

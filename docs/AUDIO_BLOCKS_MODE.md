# Audio Blocks Mode

Audio Blocks Mode is CodeUp's structured beginner mode. It lets a learner build real Python from accessible numbered blocks before moving into Code Mode and VS Code.

It is not Scratch, does not copy Scratch branding or assets, and does not provide Scratch integration or an official partnership.

## Start and discover

```text
enter block mode
list block categories
list loop blocks
help with blocks
```

The categories are output, variables, math, conditions, loops, lists, functions, input, and comments. Blocks have stable IDs, validated slot values, a type, an accessible label, a parent relationship, a branch, a nesting level, and generated Python.

## Build without a mouse

Examples:

```text
add print text hello world
add variable total equals 0
add repeat 3 times block
add change total by 1
put block 3 inside block 2
add print variable total
read block order
```

Use `next block`, `previous block`, `read block 3`, move, indent, outdent, nesting, delete, undo, and redo commands. The visible panel provides equivalent labeled buttons and keyboard controls; drag and drop is not required.

## Compile and run

`preview generated code` describes and displays the Python without changing the editor. `compile blocks to Python` explicitly replaces the Code Mode editor through CodeUp's safe editor-update path. `run blocks` compiles, updates the editor, and calls the existing CodeUp run/sandbox path. Audio Blocks Mode does not have a second execution engine.

Incomplete containers, cleared values, invalid names, unsafe expressions, and unsupported nesting refuse compilation with a block-specific explanation.

## Import Python

`import code into blocks` supports simple output, assignments, arithmetic expressions, if/else, range and list loops, simple while loops, lists, zero-argument functions, calls, returns, append operations, and standalone comments. Unsupported Python is refused without replacing the existing block workspace. The Code Mode editor is never changed by import.

## Lessons

`start block lesson` begins eight deterministic lessons:

1. Print hello world
2. Store and print a variable
3. Count with a loop
4. Add numbers in a loop
5. Use if else
6. Build and print a list
7. Define and call a function
8. Convert blocks to Python

Each lesson has a goal, checker, hint, solution workspace, and generated Python comparison.

## Export and handoff

`export block project` downloads a safe ZIP containing:

- `main.py`
- `audio_blocks_workspace.json`
- `AUDIO_BLOCKS_NOTES.md`
- CodeUp's existing accessibility handoff notes
- VS Code accessibility settings

The JSON records the numbered block order and nesting. Continue with the generated Python in Code Mode or VS Code.

## Limitations

Audio Blocks Mode intentionally supports a constrained beginner subset of Python. It does not import advanced calls, comprehensions, classes, decorators, async code, lambdas, arbitrary attributes, imports, file access, system calls, `eval`, or `exec`.

Automated tests cover command, model, compiler, UI contract, sandbox, and export behavior. Real usability testing is still needed with NVDA, JAWS, Windows Narrator, VoiceOver, Orca, Braille displays, microphone input, and audible browser speech.

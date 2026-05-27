# Teacher Guide

Use this for a 30-minute beginner class with blind or low-vision students.

## Setup Before Class

- Use Chrome or Edge for speech recognition.
- Open `http://127.0.0.1:5000` and confirm microphone permission.
- Keep headphones available for sonification.
- Have the typed command box ready as the fallback if the room is noisy.
- Start with AI disabled unless the class specifically needs explanations.

## 30-Minute Class Plan

| Time | Activity | Teacher prompt | Student action |
|---:|---|---|---|
| 0-5 min | Orientation | "This IDE can be used without a mouse." | Tab through landing page and open IDE |
| 5-10 min | First output | "Say run after typing print." | Type `print("hello")`, say `run` |
| 10-15 min | First error | "Now remove a quote and run again." | Hear the error and ask for simple explanation |
| 15-20 min | Indentation by sound | "A loop has shape. Listen for indentation." | Run `sonify block` on a loop |
| 20-25 min | Trace | "Step through what changed." | Run loop and use `next step` |
| 25-30 min | Reflection | "What was still confusing?" | Rate confidence and name one blocker |

## Beginner Exercises

- Print a name and favorite subject.
- Store a value in a variable and print it.
- Add two numbers.
- Run a `for` loop from 0 to 4.
- Trigger one indentation error and fix it using spoken debug.

## Common Commands

| Command | Use |
|---|---|
| `run` | Execute the current code |
| `help` | List commands |
| `go to line three` | Move to a line |
| `read current line` | Hear the current line |
| `check for errors` | Syntax check |
| `explain simply` | Beginner error explanation |
| `sonify block` | Hear indentation and structure |
| `next step` | Move through trace |

## Troubleshooting

| Problem | Try |
|---|---|
| Speech recognition does not start | Use Chrome/Edge, allow microphone, reload page |
| Room is noisy | Use typed command box |
| AI response unavailable | Continue with run, trace, sonification, and syntax checks |
| Student gets lost in editor | Use `where am i` or `read current line` |
| Firefox speech input fails | Switch to Chrome/Edge or use typed commands |

# CodeUp

A voice first Python IDE for blind and visually impaired beginners.

Demo

https://code-up-fmqr.onrender.com/ide

CodeUp helps learners create, run, debug, understand, navigate, package, and explain Python projects through typed or spoken natural language commands.

It is not trying to replace VS Code, NVDA, JAWS, or Braille workflows. It is a beginner bridge for students who are still learning how code structure, indentation, errors, and runtime changes work.

## Try these commands first

```text
insert print hello world
run
give me a code map
watch total
run with step narration
```

## Core features

Voice first code creation.

Spoken output and beginner friendly error recovery.

Audio Code Maps for indentation, scope, loops, functions, and structure.

Step Narration and Variable Watch for real runtime changes.

Mistake Replay for comparing a broken attempt with a fixed one.

Conditional Audio Breakpoints for pausing when a watched value reaches a condition.

Indentation sonification for hearing nested code blocks.

Guided tutorial modules for beginner Python.

Multi file project mode with project export.

Teacher handoff reports and session learning recaps.

Speech rate and verbosity controls.

NVDA and JAWS aware interaction patterns.

Optional AI coaching grounded in deterministic program facts.

## Nonvisual code commands

CodeUp includes deterministic commands that help learners understand code without relying on the screen.

```text
explain this line
read errors only
where is my cursor
read around me
list variables
```

These commands do not use AI. They use the current code, cursor position, parser, sandbox, trace, and stored run result.

## Guided learning and practice

`start learning path` begins a 12-lesson Python pathway from spoken output through multi-file projects, screen-reader habits, and VS Code handoff. Each lesson has deterministic starter code, a task, a hint, and a success check.

`start block practice` opens keyboard- and voice-operated Parsons-style exercises. Learners can read, move, indent, outdent, check, and explicitly convert numbered blocks to Python without drag and drop. `start error practice` provides six deterministic debugging challenges. Hints appear only after a learner asks for them.

## Accessible data tools

Project CSV files can be summarized with commands such as `summarize csv`, `list csv columns`, `average score`, and `describe chart`. Text descriptions are always available. `sonify column score` uses browser Web Audio when available and degrades to the spoken/text command result when audio is unavailable.

## Teacher reports

`export teacher report` downloads `CodeUp_Teacher_Report.md`. Reports remain in the browser session, exclude full code by default, and contain lesson progress, activity counters, recent output/error summaries, tracked error types, and accessibility settings. See [docs/TEACHER_REPORTS.md](docs/TEACHER_REPORTS.md).

## Screen readers and professional handoff

CodeUp results use the existing output, speech, and ARIA live-region paths. Keyboard shortcuts use Alt+Shift combinations and are listed by `show keyboard shortcuts`. Screen-reader and browser behavior varies by platform; see [docs/SCREEN_READER_TEST_PLAN.md](docs/SCREEN_READER_TEST_PLAN.md) for the manual test matrix.

CodeUp is a Python-focused beginner bridge, not a replacement for NVDA, JAWS, Narrator, VoiceOver, Orca, Braille workflows, Quorum, or VS Code. The [accessible coding pathway](docs/ACCESSIBLE_CODING_PATHWAY.md) and `/accessible-coding-tools` page explain the handoff honestly.

## AI use

CodeUp does not need AI for the main learning flow.

Execution, sandboxing, syntax checks, AST structure, Audio Code Maps, Step Narration, Variable Watch, Mistake Replay, Conditional Audio Breakpoints, sonification, tutorial validation, multi file projects, exports, typed commands, and nonvisual code commands work without a cloud AI key.

When AI is enabled, it is used to make explanations easier to understand. It does not invent program state. The source of truth is still the parser, trace, sandbox, and diff.

To test without cloud AI, set this locally.

```text
GEMINI_ENABLED=0
```

## Local config

Use the deployed link for review.

If the deployed link is down, the project is not ready to submit.

Local runs need the Python requirements, a Flask secret key, and `python app.py`.

```text
FLASK_SECRET_KEY=change-this
```

Optional AI config.

```text
GROQ_API_KEY=your-key
GROQ_API_KEY_2=your-second-key
OLLAMA_ENABLED=1
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
```

The IDE runs at `/ide`.

## Testing

The full suite passed with 2,457 tests and 1 skipped test on June 22, 2026. Coverage includes command routing, execution, sandboxing, accessibility flows, speech controls, tutorial behavior, multi file projects, project export, error recovery, nonvisual code understanding, and safety checks.

Main test commands.

```bash
python -m pytest -q
node tests/tutorial_model.test.js
node tests/spoken_code.test.js
node tests/voice_speech_chunking.test.js
```

The deterministic accessibility learning packs have focused automated coverage for lesson checks, block ordering, navigation, request-only hints, CSV summaries, report privacy, style checks, error practice, command parsing, and the related-tools page. Real screen-reader, Braille-display, microphone, and audible-TTS testing still requires users and the relevant hardware/software platforms.

## Pilot and review

CodeUp has been piloted with 10 visually impaired users.

7 users rated it 10 out of 10.

3 users rated it between 7.5 and 8.5 out of 10.

Earlier versions were tested with students at the School for the Blind and Deaf, Patiala. That feedback shaped the focus on voice first coding, spoken debugging, indentation support, and beginner friendly explanations.

CodeUp has also been shown to teams connected with Vision Aid, TTI of PBMA, NAB Delhi Academy, XRCVC Mumbai, NAB India, Blind People's Association India, and NIEPVD Dehradun as part of its external review path.

## Scope

CodeUp is built for beginner learning, demos, and supervised testing.

The sandbox is not a public online judge.

The frontend was vibe coded. The backend execution flow, sandboxing, tracing, AST analysis, command parsing, tutorial validation, and tests were built and checked separately.

## License

MIT

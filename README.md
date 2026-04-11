# CodeUp — Blind-first, Voice-driven Python IDE

CodeUp is a blind-first, voice-driven Python IDE designed to make programming accessible without relying on visual interfaces.

Unlike traditional IDEs that retrofit accessibility on top of visual workflows, CodeUp treats non-visual interaction as the default. All core interactions — navigation, execution, debugging, and understanding program structure — are designed to work through audio, keyboard, and natural language commands.

---

## What is CodeUp?

CodeUp enables visually impaired users to write, understand, and debug Python code using:

- Voice commands in **English and Hindi**
- Audio feedback and code sonification
- Step-by-step execution tracing with spoken playback
- Static and semantic analysis
- A guided onboarding tutorial covering 4 progressive Python topics

AI assistance is optional, strictly separated from deterministic tooling, and never required to use the system.

---

## Core Features

### 🔹 Onboarding & Guided Tutorial

First-time users are greeted with a language selection modal (English or Hindi). Press **1** for English or **2** for Hindi — no mouse needed.

The tutorial covers four progressive topics, each chaining automatically after a successful run:

| Topic | Concept | What you learn |
|---|---|---|
| 1 | `print` | Output text to the panel |
| 2 | Variables | Named values, assignment, reuse |
| 3 | Loops | `for` loops, `range()`, iterating lists |
| 4 | Conditionals | `if`, `elif`, `else` — branching logic |

Returning users skip onboarding automatically. Say **"restart tutorial"** or **"start over"** at any time to go back to the beginning.

### 🔹 Code Execution with Trace

- Executes Python code inside a restricted subprocess sandbox
- Tracks line execution, variable initialization and mutation, function calls and returns
- Detects semantic risk patterns such as infinite loops using heuristics
- `input()` is blocked with a clear spoken explanation and a suggestion to use hardcoded values instead

### 🔹 Voice-Driven Navigation

Users can navigate code using natural language:

- Jump to specific lines — spoken numbers supported ("go to line twenty five")
- Read current, next, or previous lines with full context
- Navigate history (back and forward)
- Jump directly to errors
- Save snippets by name ("save snippet named hello world")
- Restart the tutorial ("restart tutorial")

Ambiguous commands always require spoken confirmation. There is no silent automation.

### 🔹 Audio Code Structure (Sonification)

Code structure is conveyed using sound:

- Indentation depth maps to pitch
- Functions, classes, loops, and conditionals each have a distinct tone
- Block sonification plays the entire current block as an audio sequence

This enables understanding program structure without visual inspection.

### 🔹 Variable Intelligence (AST-based)

Uses Python's AST to:

- List variables in the current scope with phonetic pronunciation
- Track first definition, usage count, read vs. assignment
- Find all usages of a variable reliably

### 🔹 Error Beacon System

- Detects syntax and heuristic runtime errors
- Automatically jumps to the error line and activates a repeating audio beacon
- Beacon severity adapts based on error type
- AI explains the error in plain language immediately after announcing "Analyzing the error, please wait"

### 🔹 Command Palette

Press **Ctrl+Shift+P** to open a fully keyboard-navigable command palette. Arrow keys move between commands, Enter executes, Escape closes and returns focus to the editor. Screen readers are notified on open and close via `aria-live`.

### 🔹 Accessible Snippet Management

- Name and save code snippets using an inline text field — no inaccessible `window.prompt()`
- Load snippets by clicking or pressing Enter/Space
- Save by voice: "save snippet named my first program"

### 🔹 AI Assistance (Optional)

When a Gemini API key is configured:

- Explains errors in simple language (English or Hindi)
- Describes what a specific line does
- Summarises files
- Suggests improvements
- Generates starter code from natural language descriptions

AI output is explicitly separated from deterministic features and can be fully disabled by setting `GEMINI_ENABLED=0`. Every core feature works without an API key.

---

## Architecture Overview

### Backend (Flask — `app.py`)

- Handles execution, analysis, and voice intent parsing
- Uses `ast` for static analysis, `sys.settrace` for execution tracing, restricted builtins for sandboxing
- Thread-safe session storage for per-user trace state
- Per-call `genai.Client` instances — no shared global API key state between threads
- Uses `google-genai` SDK (current) — fully migrated from deprecated `google-generativeai`

### Frontend (Monaco Editor + JavaScript)

- Accessible editor interface with full keyboard shortcut coverage
- Speech synthesis and recognition (`SpeechSynthesisUtterance`, `SpeechRecognition`)
- Voice recognition language switches automatically between `en-US` and `hi-IN` based on selected language
- Audio feedback (Web Audio API) for navigation and code structure
- `aria-live` region for NVDA/JAWS screen reader announcements
- Command palette (Ctrl+Shift+P) with full keyboard navigation

### Supporting Modules

| File | Purpose |
|---|---|
| `intent_parser.py` | Natural language → structured intent + slots |
| `structure_parser.py` | AST-based code structure extraction |
| `sandboxed_fs.py` | Restricted workspace file system |

---

## Quickstart

### 1. Clone and set up a virtual environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```
FLASK_SECRET_KEY=<a random secret>
GEMINI_API_KEY=<your key from https://ai.google.dev/>
```

AI features work without a key set — they return a clear spoken message rather than crashing. To disable them entirely set `GEMINI_ENABLED=0`.

### 4. Run the application

```bash
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

### 5. Run tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests are fully isolated — snippet storage is redirected to a temp directory and no real AI calls are made.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FLASK_SECRET_KEY` | Yes | `dev-secret-key-change-in-production` | Signs session cookies |
| `GEMINI_API_KEY` | No | — | Enables AI features |
| `SESSION_COOKIE_SECURE` | No | `false` | Set `true` behind HTTPS |
| `GEMINI_ENABLED` | No | `1` | Set `0` to disable all AI calls |
| `SNIPPETS_FILE` | No | `snippets.json` | Snippet storage filename |
| `DATA_DIR` | No | `.` | Directory for snippet storage |

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Run code |
| `Alt+S` | Sonify current block |
| `Alt+L` | Read current line with context |
| `Alt+V` | List variables in scope |
| `Alt+E` | Check for syntax errors |
| `Alt+H` | Show help / command list |
| `Alt+N` | Next execution trace step |
| `Alt+←` / `Alt+→` | Navigate history |
| `Alt+Home` / `Alt+End` | Jump to top / bottom |
| `Ctrl+Shift+P` | Open command palette |
| `Ctrl+Shift+M` | Toggle voice control |

---

## Voice Commands

| Say | Action |
|---|---|
| "run" / "execute code" | Run code |
| "go to line twenty five" | Navigate to line 25 |
| "read line three" | Read line 3 aloud |
| "what changed here" | Describe variable changes at current trace step |
| "next step" / "previous step" | Step through execution trace |
| "find variable x" | Jump to all usages of variable x |
| "summarize this file" | AI file summary |
| "generate code for a fibonacci sequence" | AI code generation |
| "check for errors" | Syntax check + audio beacon |
| "sonify block" | Hear current block as audio tones |
| "save snippet named my program" | Save current code with a specific name |
| "restart tutorial" / "start over" | Reset and restart the onboarding tutorial |
| "help" | List all available commands |

---

## Language Support

CodeUp supports **English** and **Hindi (हिंदी)** throughout:

- Onboarding modal and tutorial narration in both languages
- All AI responses — analysis, fixes, explanations — in the selected language
- Voice recognition uses `en-US` or `hi-IN` automatically
- Switching language mid-session restarts voice recognition with the correct locale

---

## Accessibility Features

- **Screen reader support** — `aria-live` announcer region for NVDA and JAWS
- **Color vision modes** — Protanopia, Deuteranopia, Tritanopia, High Contrast
- **Dyslexia-friendly mode** — switches to Atkinson Hyperlegible font, increased line spacing
- **Reduced motion** — respects both the in-app toggle and the OS-level `prefers-reduced-motion` setting
- **Keyboard-only navigation** — every feature reachable without a mouse
- **Focus indicators** — visible in all themes including Windows High Contrast / Forced Colors Mode
- **No `window.prompt()`** — all dialogs use accessible inline modals with proper focus management

---

## Design Principles

- **Accessibility first** — non-visual interaction is the default, not an afterthought
- **Voice ambiguity must be confirmed** — no silent automation on ambiguous commands
- **Fail loudly and explain clearly** — errors produce audio feedback and plain-language AI explanations
- **Heuristic does not mean guaranteed correctness** — semantic warnings are guidance, not formal verification
- **AI is optional** — every core feature works without a network connection or API key
- **No inaccessible browser APIs** — `window.prompt()` and similar are replaced with accessible alternatives

---

## Intended Audience

- Blind or visually impaired Python learners
- Educators teaching Python accessibly
- Accessibility researchers
- Anyone exploring non-visual programming interfaces

---

## Disclaimer

Some analyses — semantic warnings, infinite loop detection — are heuristic-based and intended for guidance, not formal verification.

---

## Status

**v0.6.0** — Core features complete and locally tested.

- ✅ 4-topic guided tutorial (print, variables, loops, conditionals) in English and Hindi
- ✅ Fully accessible voice-driven IDE with sonification
- ✅ Screen reader support (NVDA/JAWS via aria-live)
- ✅ Subprocess sandbox with restricted builtins
- ✅ AI error explanation, code generation, analysis, fix, summarize
- ✅ Command palette with full keyboard navigation
- ✅ Accessible snippet management (no window.prompt)
- ✅ Hindi voice recognition (hi-IN) with auto-restart on language change

**Next:**
- Deployment and HTTPS
- User testing with blind users
- Research-level write-up
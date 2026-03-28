# CodeUp — Blind-first, Voice-driven Python IDE

CodeUp is a blind-first, voice-driven Python IDE designed to make programming accessible without relying on visual interfaces.

Unlike traditional IDEs that retrofit accessibility on top of visual workflows, CodeUp treats non-visual interaction as the default. All core interactions — navigation, execution, debugging, and understanding program structure — are designed to work through audio, keyboard, and natural language commands.

---

## What is CodeUp?

CodeUp enables visually impaired users to write, understand, and debug Python code using:

- Voice commands
- Audio feedback
- Execution tracing
- Static and semantic analysis

AI assistance is optional, strictly separated from deterministic tooling, and never required to use the system.

---

## Core Features

### 🔹 Code Execution with Trace

- Executes Python code inside a restricted subprocess sandbox
- Tracks line execution, variable initialization and mutation, function calls and returns
- Detects semantic risk patterns such as infinite loops using heuristics

### 🔹 Voice-Driven Navigation

Users can navigate code using natural language:

- Jump to specific lines (spoken numbers supported: "go to line twenty five")
- Read current, next, or previous lines with full context
- Navigate history (back and forward)
- Jump directly to errors

Ambiguous commands always require confirmation. There is no silent automation.

### 🔹 Audio Code Structure (Sonification)

Code structure is conveyed using sound:

- Indentation depth
- Functions and classes
- Loops and conditionals

This enables understanding program structure without visual inspection.

### 🔹 Variable Intelligence (AST-based)

Uses Python's AST to:

- List variables in the current scope
- Track first definition, usage count, read vs. assignment
- Find all usages of a variable reliably

### 🔹 Error Beacon System

- Detects syntax and heuristic runtime errors
- Automatically jumps to the error line and activates an audio beacon
- Beacon severity adapts based on error type

### 🔹 AI Assistance (Optional)

When a Gemini API key is configured:

- Explains errors in simple language
- Describes what a line does
- Summarises files
- Suggests improvements
- Generates starter code from natural language

AI output is explicitly separated from deterministic features and can be fully disabled by setting `GEMINI_ENABLED=0`.

---

## Architecture Overview

### Backend (Flask — `app.py`)

- Handles execution, analysis, and voice intent parsing
- Uses `ast` for static analysis, `sys.settrace` for execution tracing, restricted builtins for sandboxing
- Thread-safe session storage for per-user trace state
- Per-call `genai.Client` instances — no shared global API key state between threads

### Frontend (Monaco Editor + JavaScript)

- Accessible editor interface with full keyboard shortcut coverage
- Speech synthesis and recognition (`SpeechSynthesisUtterance`, `SpeechRecognition`)
- Audio feedback (Web Audio API) for navigation and code structure
- Command palette (Ctrl+Shift+P) for discoverability

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

AI features work without a key set — they return a clear message rather than crashing. To disable them entirely set `GEMINI_ENABLED=0`.

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

All configuration is via environment variables. See `.env.example` for the full list with descriptions.

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

## Voice Commands (Selected)

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
| "help" | List all available commands |

---

## Language Support

AI interactions support **English** and **Hindi (हिंदी)**. Select your preferred language from the dropdown in the UI. All AI responses — analysis, fixes, explanations — are returned in the selected language. Text-to-speech includes both English and Hindi pronunciations.

---

## Accessibility Features

- **Color vision modes** — Protanopia, Deuteranopia, Tritanopia, High Contrast
- **Dyslexia-friendly mode** — switches to Atkinson Hyperlegible font, increased line spacing
- **Reduced motion** — respects both the in-app toggle and the OS-level `prefers-reduced-motion` setting
- **Keyboard-only navigation** — every feature reachable without a mouse
- **Focus indicators** — visible in all themes including Windows High Contrast / Forced Colors Mode

---

## Design Principles

- **Accessibility first** — non-visual interaction is the default, not an afterthought
- **Voice ambiguity must be confirmed** — no silent automation on ambiguous commands
- **Fail loudly and explain clearly** — errors produce audio feedback and plain-language explanations
- **Heuristic does not mean guaranteed correctness** — semantic warnings are guidance, not formal verification
- **AI is optional** — every core feature works without a network connection or API key

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

**Prototype v0.5.0.** Core features implemented and locally tested.

Future work:

- Deployment and containerisation
- Robustness improvements and error recovery
- User testing and validation with blind users
- Research-level polish and academic write-up
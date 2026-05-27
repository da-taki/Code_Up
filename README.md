# CodeUp: A Blind-First Python IDE

[![Test](https://github.com/da-taki/Code_Up/actions/workflows/test.yml/badge.svg)](https://github.com/da-taki/Code_Up/actions/workflows/test.yml)

CodeUp is a Python IDE designed for blind and visually impaired learners. Unlike traditional IDEs that retrofit accessibility on top of visual workflows, CodeUp treats non-visual interaction as the default. Every core feature (navigation, execution, debugging, code understanding) works through audio, keyboard, and natural language commands.

The project is independently developed and intended for use in schools and accessibility programs.

> **Sister project:** CodeUp Web extends the same accessibility model to HTML and CSS. See [Code_Up_Web](https://github.com/da-taki/CodeUp-web) (in active development).

---

## Status and adoption

**v0.8.0**, classroom-pilot ready. Locally tested with the current automated suite in `tests/test_security_voice.py` and validated through a structured pilot with real blind users.

### Pilot results

CodeUp has been piloted with 10 users to date:

- 7 users rated it 10/10
- 3 users rated it between 7.5/10 and 8.5/10
- Full anonymized pilot table: [docs/pilot-results.md](docs/pilot-results.md)

The project is in active use at the **School for the Blind and Deaf, Patiala**, where it was formally adopted as a teaching tool. Coding sessions are conducted there twice monthly.

### Testing approach

CodeUp is tested across two complementary surfaces.

**Automated tests** cover sandbox security (escape attempts, restricted imports, time and memory caps), voice intent parsing (English and Hindi, including compound number words), trace playback, snippet CRUD, request size limits, per-session isolation, rate limiting, and the sandboxed filesystem. The current repository keeps these checks in one large pytest file plus shared fixtures. Tests are fully isolated: snippet storage redirects to a temp directory and no real AI calls are made. Run with `python -m pytest -q`.

**User testing** is conducted in person with blind students at the School for the Blind and Deaf, Patiala. Test plans focus on whether each feature is reachable, understandable, and useful without sighted assistance. Iterations from this loop include the move from `window.prompt()` to inline accessible modals, the auto-save behaviour, the audio heartbeat during long runs, and the beginner-mode error explainer.

---

## Screenshots

| Landing page | IDE with spoken debug | Sonification and trace |
|---|---|---|
| ![CodeUp landing page](docs/assets/landing-page.png) | ![CodeUp IDE with spoken debug](docs/assets/ide-spoken-debug.png) | ![CodeUp sonification and trace panel](docs/assets/sonification-trace.png) |

---

## Before vs After CodeUp

| Traditional IDE | CodeUp |
|---|---|
| `SyntaxError: line 3` | `Line 3 is inside the loop. The indentation dropped. Try adding four spaces before print.` |
| Error output is mostly visual | Error is spoken, simplified, and tied to code structure |
| Trace requires reading debugger panes | Trace steps can be heard with `next step` and `previous step` |
| Indentation is only visual | Indentation can be heard through sonification |

---

## What it does

- **Voice commands** in English and Hindi for navigation, execution, and editing
- **Audio code structure** through sonification: pitch maps to indentation, distinct tones for functions, classes, loops, and conditionals
- **Step-by-step execution traces** with spoken playback and a "story mode" narrative
- **Sandboxed Python execution** with subprocess isolation, restricted imports, time and memory caps, AST audit, and same-origin enforcement on state-changing requests
- **Optional AI assistance** (Groq Llama 3.3 70B) for error explanation, code generation, summarization, and a mentor mode with quizzes and bug challenges
- **Audio breakpoint debugger** with variable watching
- **Six-step interactive tutorial** in both English and Hindi covering print, variables, loops, and conditionals
- **Conversational CodeUp Mentor** for short follow-up questions, hints, progress checks, and audio code maps

AI assistance is strictly optional. Every core feature works without an API key or network connection.

---

## Demo and teaching materials

- One-command demo flow: [DEMO_FLOW.md](DEMO_FLOW.md)
- Accessibility test checklist: [ACCESSIBILITY_TESTING.md](ACCESSIBILITY_TESTING.md)
- Teacher guide: [docs/teacher-guide.md](docs/teacher-guide.md)
- Beginner lessons: [lesson 1](lessons/lesson_1_print.md), [lesson 2](lessons/lesson_2_variables.md), [lesson 3](lessons/lesson_3_loops.md)
- Security model: [SECURITY.md](SECURITY.md)

---

## Accessibility

- Screen reader support via `aria-live` announcer (NVDA, JAWS, VoiceOver)
- Color vision modes (Protanopia, Deuteranopia, Tritanopia)
- High Contrast mode
- Night Mode for low-light environments and users who prefer dark themes
- Dyslexia-friendly mode with Atkinson Hyperlegible font and increased line spacing
- Reduced motion support, both via in-app toggle and the OS-level `prefers-reduced-motion` setting
- Full keyboard navigation: every feature reachable without a mouse
- Press `Escape` at any time to stop speech mid-sentence
- No `window.prompt()`. All dialogs use accessible inline modals with focus management

---

## Quickstart

Requirements: Python 3.8 or newer. The IDE itself runs offline once installed. Monaco, JetBrains Mono, and Atkinson Hyperlegible are all vendored. The landing page additionally requires a one-time Node build to vendor React and bundle the JSX components (see "Building the landing page" below).

Clone and set up a virtualenv:

    git clone https://github.com/da-taki/Code_Up.git
    cd Code_Up
    python -m venv .venv

Activate the virtualenv (Windows PowerShell):

    .\.venv\Scripts\Activate.ps1

Activate the virtualenv (macOS / Linux):

    source .venv/bin/activate

Install dependencies:

    pip install -r requirements.txt

Configure environment:

    cp .env.example .env

Edit `.env` and set at minimum:

    FLASK_SECRET_KEY=<a random secret>
    GROQ_API_KEY=<your free key from https://console.groq.com>

If you don't set a Groq key, AI features return a clear spoken message rather than crashing. To disable AI entirely, set `GEMINI_ENABLED=0`.

### Optional: Offline AI via Ollama

CodeUp can fall back to a locally-running Ollama instance when Groq is unavailable (no internet, no API key, rate-limited). To enable:

1. Install Ollama from <https://ollama.com>
2. Pull a small model: `ollama pull llama3.2:3b`
3. Set environment variables in `.env`:

       OLLAMA_ENABLED=1
       OLLAMA_URL=http://localhost:11434
       OLLAMA_MODEL=llama3.2:3b

When enabled, Groq is tried first. On any failure (network, auth, rate-limit), CodeUp transparently routes to Ollama and prefixes responses with `[offline mode]` so the user knows.

Run the application:

    python app.py

Open `http://127.0.0.1:5000` in Chrome or Edge. (Firefox does not support the Web Speech API for voice input. Keyboard and the typed command box still work in any browser.)

Run tests:

    pip install -r requirements-dev.txt
    python -m pytest -q

Tests are fully isolated. Snippet storage redirects to a temp directory and no real AI calls are made.

---

## Evaluation metrics

The pilot and future classroom tests track:

- Task completion rate
- Time to fix first error
- Commands recognized correctly
- Number of sighted-assistance interventions
- User confidence rating before and after the session

See [docs/pilot-results.md](docs/pilot-results.md) for the current anonymized table.

---

## Landing page

The marketing landing page at `/` is a separate React experience that walks visitors through the core ideas before they reach the IDE itself. It is built around four sections:

1. **Sonification demo** with a 9-tone Web Audio playback of a sample function, showing how pitch maps to indentation and timbre maps to construct
2. **Voice typewriter** cycling through bilingual command examples (English and Hindi) with live transcription
3. **IDE preview** showing a working trace stepper, breakpoint glyph, and snippet sidebar
4. **Features grid + manifesto** covering the sandbox, bilingual voice, accessibility commitments, and zero-CDN dependency posture

The landing page is fully keyboard navigable, respects `prefers-reduced-motion`, ships an aria-labeled skip link past the decorative hero, and uses the same paper-themed design system as the IDE.

The IDE at `/ide` works without any Node tooling. Only the landing page requires a Node build to bundle JSX.

### Building the landing page

First-time setup:

    npm install
    npm run build

This produces:

- `static/vendor/react/react.production.min.js`
- `static/vendor/react/react-dom.production.min.js`
- `static/landing/dist/bundle.js`

After editing any `static/landing/*.jsx` file, rerun `npm run build`. Node is only required for the landing page. Running, deploying, or hacking on the IDE never needs it.

If you're deploying to a school environment without Node, the three built files above can be committed to the repo so end users skip the build step entirely.

---

## Architecture

### Backend (`app.py`)

Flask application handling code execution, AST-based analysis, voice intent parsing, and AI proxying. Each `/run` request spawns a fresh subprocess confined to a per-session workspace directory, with restricted built-ins and (on POSIX systems) `RLIMIT_AS` and `RLIMIT_CPU` enforced via `preexec_fn`. Per-session state (execution traces, snippets, sandboxes) is keyed by signed session cookies.

### Frontend

Monaco Editor (vendored locally, no CDN dependency), JavaScript using the Web Speech API for voice recognition and `SpeechSynthesisUtterance` for output, Web Audio API for sonification. Speech recognition language switches automatically between `en-US` and `hi-IN` based on the selected interface language.

### Supporting modules

| File | Purpose |
|---|---|
| `intent_parser.py` | Natural language to structured intent and slots, with Hindi number support |
| `structure_parser.py` | AST-based code structure extraction (functions, classes, loops, async detection, parent class tracking) |
| `sandboxed_fs.py` | Per-session restricted workspace file system |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FLASK_SECRET_KEY` | Yes | `dev-secret-key-change-in-production` | Signs session cookies |
| `GROQ_API_KEY` | No |  | Enables AI features (get free at console.groq.com) |
| `GEMINI_ENABLED` | No | `1` | Set `0` to disable all AI calls (env var name kept for backward compat) |
| `SESSION_COOKIE_SECURE` | No | `false` | Set `true` behind HTTPS |
| `DATA_DIR` | No | `.` | Directory for per-session snippet files |
| `OLLAMA_ENABLED` | No | `0` | Set `1` to enable local Ollama fallback when Groq fails |
| `OLLAMA_URL` | No | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | No | `llama3.2:3b` | Ollama model name |

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Run code |
| `Escape` | Stop speech immediately |
| `Alt+S` | Sonify current block |
| `Alt+L` | Read current line with context |
| `Alt+V` | List variables in scope |
| `Alt+E` | Check for syntax errors |
| `Alt+H` | Show help / command list |
| `Alt+N` | Next execution trace step |
| `Alt+Left` / `Alt+Right` | Navigate history |
| `Alt+Home` / `Alt+End` | Jump to top / bottom |
| `Ctrl+Shift+P` | Open command palette |
| `Ctrl+Shift+M` | Toggle voice control |
| `Alt+A` | Cycle color vision modes |
| `Alt+D` | Toggle dyslexia mode |
| `Alt+M` | Toggle reduced motion |

---

## Voice Commands

A partial list. Many natural variations work because the intent parser is grammar-based, not exact-match.

| Say | Action |
|---|---|
| "run" / "execute code" | Run code |
| "go to line twenty five" | Navigate to line 25 |
| "read line three" | Read line 3 aloud |
| "what changed here" | Describe variable changes at current trace step |
| "next step" / "previous step" | Step through execution trace |
| "find variable x" | Jump to all usages of variable x |
| "summarize this file" | AI file summary |
| "generate code for fibonacci sequence" | AI code generation |
| "check for errors" | Syntax check plus audio beacon |
| "sonify block" | Hear current block as audio tones |
| "save snippet named hello world" | Save current code |
| "tell the story" | Narrate what your code did |
| "set breakpoint at line 10" | Audio debugger |
| "watch variable x" | Report x at each breakpoint |
| "continue" | Run to next breakpoint |
| "learning mode" | Start mentor / quiz mode |
| "quiz me on loops" | Get a quiz question |
| "explain variables" | Concept explanation |
| "bug challenge" | Find and fix a bug |
| "insert function called greet" | Voice code editing |
| "suggest next line" then "choose 2" | AI autocomplete |
| "help" | List all commands |

Hindi equivalents work for around 15 core commands including `चलाओ` (run), `कोड समझाओ` (analyze), `कोड ठीक करो` (fix), `लाइन बीस पर जाओ` (go to line 20), `मदद` (help). Hindi number words 0 to 50 are recognized in line-navigation commands.

---

## Sandbox

User code runs in a separate Python subprocess with:

- Restricted imports: only `math`, `random`, `string`, `datetime` allowed
- Restricted built-ins: `eval`, `exec`, `compile`, `open`, `__import__`, and direct module attribute access blocked
- 3-second wall-clock timeout
- 5,000-event trace cap to prevent runaway memory growth
- POSIX-only: 128 MB address space cap and 3-second CPU time cap via `setrlimit`
- Working directory confined to a per-session temp workspace
- `input()` blocked with a clear explanation suggesting hardcoded values

The wall-clock timeout stops slow or sleeping programs. The POSIX CPU cap stops tight loops that burn processor time before wall-clock timeout would otherwise fire.

Per-session rate limit: 10 runs per 60 seconds.

---

## Known limitations

- Chrome and Edge are best for speech recognition. Firefox has limited Web Speech API support, so keyboard and typed commands are the fallback there.
- Live `input()` mode is POSIX-only. The pre-flight input panel works across platforms.
- POSIX CPU and memory caps are stronger than the Windows fallback.
- AI help is optional and depends on Groq or Ollama when enabled. Core run, trace, sonification, and navigation features do not require AI.
- The sandbox is intended for classroom and demo use, not as a public multi-tenant judge service.

---

## What's new in 0.8.0

- **`input()` support, two ways**:
  - *Pre-flight* (default, reproducible): declare values ahead with the inputs panel, voice (`set inputs to alice and seventeen`), or a magic comment (`# inputs: alice, 17`)
  - *Live* (POSIX only): say `live input mode`, then your code pauses at each `input()` and asks you for the value via voice or typing
- **Output diff narration**: re-run code and hear only what changed, not the whole output again. Voice: `what's different`
- **Audio heartbeat**: soft tone every 500ms while code runs so you know it's alive
- **Voice macros**: `remember this as quick sort` saves the editor as a named macro. `use macro quick sort` loads it
- **Output bookmarks**: `bookmark this` mid-output, then `read from bookmark <name>` later
- **Breadcrumbs**: Alt+B (or `where am i`) reads "function calculate, inside for loop, line 15"
- **Beginner errors**: after an error, say `explain simply` for a jargon-free, real-life-analogy version
- **Auto-save**: every 30 seconds, silently. A draft from the previous session is restored automatically on next visit
- **Coffee theme**: replaces the previous teal palette with cream, caramel, and espresso

Next:

- Continued user testing with blind students at the School for the Blind and Deaf, Patiala
- Deployment behind HTTPS for non-localhost use
- Research write-up
- CodeUp Web (HTML / CSS sister project) reaching feature parity on the accessibility surface

---

## License

MIT. See `LICENSE`.

---

## Author

Independent project by Taknoor Singh ([@da-taki](https://github.com/da-taki)).

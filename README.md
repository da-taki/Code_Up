# CodeUp: An Audio-Native Python Learning Environment

[![Test](https://github.com/da-taki/Code_Up/actions/workflows/test.yml/badge.svg)](https://github.com/da-taki/Code_Up/actions/workflows/test.yml)

CodeUp is a blind-first, audio-native Python learning and debugging environment that translates visual programming structure, runtime state and debugging history into spoken, navigable explanations. Students understand indentation and scope through audio code mapping, hear verified variable changes during execution, compare a broken attempt with a corrected program, and receive AI-assisted coaching grounded in deterministic program facts — all through voice commands, typed commands, or keyboard-driven interaction. Every core feature works offline without an API key.

The project is independently developed and in active use at the **School for the Blind and Deaf, Patiala**, where coding sessions are conducted twice monthly.

> **Sister project:** CodeUp Web extends the same accessibility model to HTML and CSS. See [Code_Up_Web](https://github.com/da-taki/CodeUp-web) (in active development).

---

## Flagship Capabilities

### Audio Code Map

Derives program structure from deterministic Python AST analysis. Students hear loops, conditions, functions, statements after blocks and nesting depth without reading every line. Sub-queries like "what is inside the loop" and "what comes after the loop" return precise, line-numbered answers.

### Variable Watch + Step Narration

Runs code within the existing sandboxed execution environment and narrates actual traced variable updates and output. Values are derived from execution traces, not guessed by AI. Students say `watch total` to focus narration on specific variables, then `run with step narration` to hear each change as it happens.

### Mistake Replay

Compares a recent failed attempt with a corrected successful run. Explains structural differences — such as moving an assignment inside a loop — and why behaviour changes. The comparison is built from deterministic diff and AST analysis, with optional AI rephrasing for beginner-friendliness.

### AI-Assisted Coaching

Groq (Llama 3.3 70B) enhances explanations for clarity and beginner-friendliness. Deterministic AST, trace and diff facts remain the source of truth — AI rephrases verified facts, never invents them. When cloud AI is unavailable, every feature falls back to its deterministic output. A local Ollama fallback is also supported.

### Accessibility-First Interaction

Typed commands, keyboard-accessible controls, spoken output and narration, and voice-command routing in English and Hindi. All buttons have ARIA labels and are keyboard-reachable. Output appears in `aria-live` regions. Press `Escape` at any time to stop speech.

---

## Flagship Demo Flow

Start with this broken program:

```python
total = 0
for i in range(3):
total = total + i
print(total)
```

| Step | Command | What the student hears |
|------|---------|----------------------|
| 1 | **Run** | Beginner-friendly explanation: the line after the loop must be indented with four spaces |
| 2 | `give me a code map` | "Your code has a syntax error near line 3. Here is what I can tell from indentation alone." |
| 3 | Fix indentation → `    total = total + i` | — |
| 4 | `watch total` | "Now watching total." |
| 5 | `run with step narration` | "total becomes 0 … total changes to 1 … total changes to 3. Output: 3" |
| 6 | `compare before and after` | "Line 3 was indented from 0 to 4 spaces, changing what block it belongs to." + explanation of why the fix works |

The student identifies the scope problem, traces `total` as it changes through loop execution, and understands the fix relative to their earlier mistake.

---

## Supported Commands

Many natural variations work because the intent parser is grammar-based, not exact-match.

| Purpose | Example commands |
|---------|-----------------|
| Understand structure | `code map`, `give me a code map`, `what is inside the loop`, `what comes after the loop`, `how deeply nested am I`, `list my functions` |
| Trace execution | `watch total`, `track score`, `clear watched variables`, `run with step narration`, `what changed in this step` |
| Learn from mistakes | `compare before and after`, `replay my mistake`, `why does the fixed version work`, `show changed lines` |
| Run and debug | `run`, `execute code`, `set breakpoint at line 10`, `watch variable x`, `continue`, `next step`, `previous step` |
| Navigate code | `go to line twenty five`, `read line three`, `find variable x`, `where am i` |
| Audio features | `sonify block`, `tell the story`, `what's different` |
| AI assistance | `fix`, `analyze`, `explain simply`, `generate code for fibonacci`, `learning mode`, `quiz me on loops` |
| Hindi | `चलाओ` (run), `कोड समझाओ` (analyze), `कोड ठीक करो` (fix), `लाइन बीस पर जाओ` (go to line 20), `मदद` (help) |

Hindi number words 0–100 are recognized in line-navigation commands.

---

## How It Works

| Layer | Mechanism |
|-------|-----------|
| **Structural analysis** | Python `ast` module parses code into loops, conditions, functions, assignments and nesting depth. Syntax errors fall back to indentation-based heuristics. |
| **Runtime tracing** | Code runs in a sandboxed subprocess with a `sys.settrace` callback that records variable initializations, changes and function calls. Values come from actual execution, never AI. |
| **Mistake Replay** | Session-scoped snapshots store the most recent failed and successful code. `difflib.SequenceMatcher` computes line-level changes; AST comparison identifies structural shifts like indentation scope changes. |
| **AI coaching** | Groq rephrases deterministic facts into student-friendly spoken summaries. System prompts instruct the model not to invent structural facts or variable values. |
| **Deterministic fallback** | When AI is unavailable (no key, network error, disabled), every feature returns its raw deterministic output. No feature depends on AI for correctness. |

---

## Screenshots

| Landing page | IDE with spoken debug | Sonification and trace |
|---|---|---|
| ![CodeUp landing page](docs/assets/landing-page.png) | ![CodeUp IDE with spoken debug](docs/assets/ide-spoken-debug.png) | ![CodeUp sonification and trace panel](docs/assets/sonification-trace.png) |

---

## Quickstart

Requirements: Python 3.8 or newer. The IDE runs offline once installed. Monaco, JetBrains Mono, and Atkinson Hyperlegible are all vendored. The landing page additionally requires a one-time Node build (see "Building the landing page" below).

Clone and set up a virtualenv:

    git clone https://github.com/da-taki/Code_Up.git
    cd Code_Up
    python -m venv .venv

On Windows PowerShell, use the Python launcher if `python` is not on PATH:

    py -m venv .venv

Activate the virtualenv (Windows PowerShell):

    .\.venv\Scripts\Activate.ps1

Activate the virtualenv (macOS / Linux):

    source .venv/bin/activate

Install dependencies:

    python -m pip install -r requirements.txt

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

## Validation Status

| Check | Result |
|-------|--------|
| Automated tests | 423 passed, 1 skipped |
| Python compile check | Clean |
| Ruff lint | Clean |
| JavaScript syntax checks | Clean |
| Frontend build | Clean |
| Audio Code Map (browser) | Verified — deterministic AST facts and AI-enhanced output |
| Step Narration (browser) | Verified — correct traced variable values |
| Mistake Replay (browser) | Verified — correct diff and indentation/scope explanation |
| Groq-backed enhancement | Verified — AI rephrases without inventing facts |
| Offline/deterministic fallback | Verified — all three features return correct deterministic output |
| Secret/API-key scan | No leakage found in responses, console, or tracked files |

> **Limitations not yet independently validated:** physical microphone capture for all new commands, NVDA/JAWS/VoiceOver screen reader testing of the new controls, and physical mobile-device accessibility.

---

## Status and Adoption

**v0.8.0**, classroom-pilot ready.

### Pilot results

CodeUp has been piloted with 10 users to date:

- 7 users rated it 10/10
- 3 users rated it between 7.5/10 and 8.5/10
- Full anonymized pilot table: [docs/pilot-results.md](docs/pilot-results.md)

The project is in active use at the **School for the Blind and Deaf, Patiala**, where it was formally adopted as a teaching tool.

### Evaluation metrics

The pilot and future classroom tests track:

- Task completion rate
- Time to fix first error
- Commands recognized correctly
- Number of sighted-assistance interventions
- User confidence rating before and after the session

See [docs/pilot-results.md](docs/pilot-results.md) for the current anonymized table.

---

## Architecture

### Backend (`app.py`)

Flask application handling code execution, AST-based analysis, voice intent parsing, and AI proxying. Each `/run` request spawns a fresh subprocess confined to a per-session workspace directory, with restricted built-ins and (on POSIX systems) `RLIMIT_AS` and `RLIMIT_CPU` enforced via `preexec_fn`. Per-session state (execution traces, snippets, watched variables, mistake snapshots, sandboxes) is keyed by signed session cookies.

### Frontend

Monaco Editor (vendored locally, no CDN dependency), JavaScript using the Web Speech API for voice recognition and `SpeechSynthesisUtterance` for output, Web Audio API for sonification. Speech recognition language switches automatically between `en-US` and `hi-IN` based on the selected interface language.

### Supporting modules

| File | Purpose |
|---|---|
| `intent_parser.py` | Natural language to structured intent and slots, with Hindi number support |
| `structure_parser.py` | AST-based code structure extraction (functions, classes, loops, async detection, parent class tracking) |
| `sandboxed_fs.py` | Per-session restricted workspace file system |

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

Per-session rate limit: 30 runs per 60 seconds.

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

## Accessibility

- Screen reader support via `aria-live` announcer (NVDA, JAWS, VoiceOver)
- Color vision modes (Protanopia, Deuteranopia, Tritanopia)
- High Contrast mode
- Night Mode for low-light environments
- Dyslexia-friendly mode with Atkinson Hyperlegible font and increased line spacing
- Reduced motion support, both via in-app toggle and the OS-level `prefers-reduced-motion` setting
- Full keyboard navigation: every feature reachable without a mouse
- No `window.prompt()`. All dialogs use accessible inline modals with focus management

---

## Demo and Teaching Materials

- One-command demo flow: [DEMO_FLOW.md](DEMO_FLOW.md)
- Accessibility test checklist: [ACCESSIBILITY_TESTING.md](ACCESSIBILITY_TESTING.md)
- Teacher guide: [docs/teacher-guide.md](docs/teacher-guide.md)
- Beginner lessons: [lesson 1](lessons/lesson_1_print.md), [lesson 2](lessons/lesson_2_variables.md), [lesson 3](lessons/lesson_3_loops.md)
- Security model: [SECURITY.md](SECURITY.md)

---

## Known Limitations

- Chrome and Edge are best for speech recognition. Firefox has limited Web Speech API support, so keyboard and typed commands are the fallback there.
- Live `input()` mode is POSIX-only. The pre-flight input panel works across platforms.
- POSIX CPU and memory caps are stronger than the Windows fallback.
- AI help is optional and depends on Groq or Ollama when enabled. Core run, trace, sonification, and navigation features do not require AI.
- The sandbox is intended for classroom and demo use, not as a public multi-tenant judge service.

---

## Landing Page

The marketing landing page at `/` is a separate React experience that walks visitors through the core ideas before they reach the IDE. It includes a sonification demo, voice typewriter, IDE preview, and features grid. The landing page is fully keyboard navigable and respects `prefers-reduced-motion`.

The IDE at `/ide` works without any Node tooling. The repository includes built landing-page assets, so a clean clone can start Flask immediately. Rebuild only if you edit `static/landing/*.jsx`.

### Building the landing page

    npm install
    npm run build

This produces `static/vendor/react/*.min.js` and `static/landing/dist/bundle.js`. Node is only required for the landing page.

---

## What's New in 0.8.0

- **Audio Code Map**: hear program structure from deterministic AST analysis
- **Variable Watch + Step Narration**: trace verified runtime variable changes during execution
- **Mistake Replay**: compare broken and fixed code with structural diff explanation
- **`input()` support**: pre-flight inputs panel and live input mode (POSIX only)
- **Output diff narration**: re-run code and hear only what changed
- **Audio heartbeat**: soft tone every 500ms while code runs
- **Voice macros**: `remember this as quick sort` / `use macro quick sort`
- **Output bookmarks**: `bookmark this` / `read from bookmark <name>`
- **Breadcrumbs**: Alt+B reads "function calculate, inside for loop, line 15"
- **Beginner errors**: `explain simply` for jargon-free error explanations
- **Auto-save**: every 30 seconds, with draft restoration on next visit

---

## License

MIT. See `LICENSE`.

---

## Author

Independent project by Taknoor Singh ([@da-taki](https://github.com/da-taki)).

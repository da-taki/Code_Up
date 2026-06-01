# CodeUp Demo Readiness

Use this checklist for a 5-10 minute external trainer evaluation on Windows with Chrome or Edge.

## Startup

```powershell
git clone https://github.com/da-taki/Code_Up.git
cd Code_Up
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set:

```text
FLASK_SECRET_KEY=<random local demo secret>
GEMINI_ENABLED=0
```

Then start:

```powershell
python app.py
```

Open `http://127.0.0.1:5000` in Chrome or Edge. Use Chrome or Edge for microphone voice commands; Firefox can still run the keyboard and typed-command workflow.

## Demo Script

1. Open `/` and tab through the first screen. Confirm the skip link and "Open the IDE" link receive visible focus.
2. Open `/ide`.
3. Start with keyboard only: Tab to Tutorial, open it, then close or advance it.
4. Paste the broken loop:

   ```python
   for i in range(3):
   print(i)
   ```

5. Run with `Ctrl+Enter` or the Run button. Expected result: an indentation message for line 2 that says the line after the loop must be indented. No local file path or internal traceback should appear.
6. Replace it with:

   ```python
   total = 0
   for i in range(3):
       total = total + i
       print(total)
   ```

7. Run again. Expected output is:

   ```text
   0
   1
   3
   ```

8. Open or read the structure panel. Expected result: CodeUp identifies a `for` loop on line 2.
9. Use trace/story/debugging: say or type `next step`, `tell the story`, and optionally `set breakpoint at line 3`.
10. Use audio controls: press `Alt+S` or say `sonify block`; press `Escape` while speech is playing and confirm speech stops immediately.
11. Try voice: turn on Voice in Chrome/Edge and say `run`, `help`, and `go to line two`.
12. Try Hindi command fallback: switch to Hindi and try `मदद` or `चलाओ`. If speech recognition is unavailable, type the same command in the command box.

## Fallbacks

- If AI is unavailable or disabled, core run, trace, structure, tutorial, sonification, snippets, and typed/keyboard commands must still work.
- If microphone access fails, use the typed command box and keyboard shortcuts.
- If live input mode is unavailable on Windows, use the inputs panel or a `# inputs:` comment.
- If speech synthesis voice support is limited, keep the visual output panel and screen reader live regions visible while using keyboard commands.

## Pre-Demo Checklist

- `python -m pytest -q` passes.
- `python -m py_compile app.py sandbox_runner.py sandboxed_fs.py structure_parser.py intent_parser.py` passes.
- `node --check static/app.js` and `node --check static/voice-engine.js` pass.
- `npm run build` passes if landing JSX changed.
- Browser smoke passes for `/`, `/ide`, broken-loop error, corrected-loop output, structure panel, tutorial/help, Escape speech stop, and AI-disabled mode.
- `.env`, API keys, temp files, `__pycache__`, and local workspace paths are not in the commit.

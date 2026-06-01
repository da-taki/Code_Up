# One-Command Demo Flow

Use this script when showing CodeUp to a teacher, competition judge, or admissions reviewer. It demonstrates the accessibility problem first, then the CodeUp workflow.

## Start

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` in Chrome or Edge.

## Flow

1. Landing page: let the page load, then tab through the first screen to show keyboard access and the skip link.
2. Open IDE: activate "Open IDE" or go to `http://127.0.0.1:5000/ide`.
3. Voice command: say `insert print hello world`, then say `run`.
4. Run code: confirm the output is spoken and appears in the output panel.
5. Error: replace the code with:

   ```python
   for i in range(3):
   print(i)
   ```

   Say `run`.
6. Spoken debug: after the `IndentationError`, say `explain simply` or `debug this error`. CodeUp should explain that the line inside the loop needs indentation.
7. Sonification: say `sonify block` or press `Alt+S`. Explain that indentation is mapped to pitch so code shape can be heard.
8. Trace: fix the code to:

   ```python
   total = 0
   for i in range(3):
       total = total + i
       print(total)
   ```

   Say `run`, then use `next step` / `previous step` to hear execution state changes.

## Closing Line

Traditional IDEs usually say only "SyntaxError line 3." CodeUp says where the learner is, what changed, and what action to try next, through speech, keyboard, and sound.

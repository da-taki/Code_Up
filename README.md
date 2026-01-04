CodeUp | Blind-first, Voice-driven Python IDE

CodeUp is a blind-first, voice-driven Python IDE designed to make programming accessible without relying on visual interfaces.

Unlike traditional IDEs that retrofit accessibility on top of visual workflows, CodeUp treats non-visual interaction as the default. All core interactions such as navigation, execution, debugging, and understanding program structure are designed to work through audio, keyboard, and natural language commands.

What is CodeUp?

CodeUp enables visually impaired users to write, understand, and debug Python code using:
Voice commands
Audio feedback
Execution tracing
Static and semantic analysis
AI assistance is optional, strictly separated from deterministic tooling, and never required to use the system.

Core Features

🔹 Code Execution with Trace

Executes Python code inside a restricted sandbox
Tracks:
Line execution
Variable initialization and mutation
Function calls and returns
Detects semantic risk patterns such as infinite loops using heuristics

🔹 Voice-Driven Navigation

Users can navigate code using natural language:
Jump to specific lines
Read current, next, or previous lines
Navigate history (back and forward)
Jump directly to errors
Ambiguous commands always require confirmation. There is no silent automation.

🔹 Audio Code Structure (Sonification)

Code structure is conveyed using sound:
Indentation depth
Functions
Loops
Conditionals
This enables understanding program structure without visual inspection.

🔹 Variable Intelligence (AST-based)

Uses Python’s AST to:
List variables in the current scope

Track:
First definition
Usage count
Read versus assignment
Find all usages of a variable reliably

🔹 Error Beacon System

Detects syntax and heuristic runtime errors

Automatically:
Jumps to the error line
Activates an audio beacon
Beacon severity adapts based on error type

🔹 AI Assistance (Optional)

When enabled via a local LLM backend:
Explains errors in simple language
Describes what a line does
Summarizes files
Suggests improvements
Generates starter code from natural language
AI output is explicitly separated from deterministic features and can be fully disabled.
Architecture Overview
Backend (Flask)
Handles execution, analysis, and voice intent parsing

Uses:

ast for static analysis
sys.settrace for execution tracing
Restricted builtins for sandboxing
Frontend (Monaco Editor and JavaScript)
Accessible editor interface
Speech synthesis and recognition
Keyboard shortcuts for all major features
Audio feedback for navigation and structure

Quickstart
1. Create and activate a virtual environment

Windows (PowerShell):
python -m venv .venv
.\.venv\Scripts\Activate.ps1

macOS or Linux:
python -m venv .venv
source .venv/bin/activate


Install dependencies:

pip install -r requirements.txt

2. Run the application
python app.py

3. Run tests
python -m pytest -q

Notes

Tests use tests/conftest.py to launch the dev server in a subprocess

External LLM calls are disabled during tests
To enable real LLM usage, set:
OLLAMA_ENABLED=1
and configure OLLAMA_URL and OLLAMA_MODEL

Design Principles
Accessibility first
Voice ambiguity must be confirmed
Fail loudly and explain clearly
Heuristic does not mean guaranteed correctness
No silent automation
Intended Audience
Blind or visually impaired beginners
Educators teaching Python accessibly
Accessibility researchers
Anyone exploring non-visual programming interfaces

Disclaimer
Some analyses such as semantic warnings and infinite loop detection are heuristic-based and intended for guidance, not formal verification.

Status
Prototype v1. Core features implemented and locally tested.

Future work focuses on:
Deployment
Robustness improvements
User testing and validation
Research-level polish

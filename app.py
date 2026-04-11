from flask import Flask, render_template, request, jsonify, g
import json, os, traceback, io, contextlib, re, ast, sys, time, threading, subprocess, tempfile, uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Optional, Any
from rapidfuzz import fuzz
from google import genai
from google.genai import types as genai_types
from structure_parser import CodeAnalyzer
from intent_parser import parse_intent
from sandboxed_fs import get_sandbox

# Thread-local storage helpers
_trace_context = threading.local()  # used by run_with_trace
_api_context = threading.local()   # stores per-request Gemini API key

# Session-based trace storage (prevents concurrent user interference)
# Keys: session_id (UUID), Values: {last_trace, current_trace_index, trace_timestamp, trace_duration_ms, last_voice_action}
_session_traces = {}  # dict[str, dict]
_session_traces_lock = threading.Lock()
_session_ttl = 3600  # 1 hour session TTL

# Background cleanup thread for old sessions
def _session_cleanup_worker():
    """Background thread that periodically cleans up expired sessions."""
    while True:
        time.sleep(300)  # Run cleanup every 5 minutes
        try:
            cleanup_old_sessions()
        except Exception as e:
            print(f"Session cleanup error: {e}", file=sys.stderr)

_cleanup_thread = threading.Thread(target=_session_cleanup_worker, daemon=True)
_cleanup_thread.start()

# Bounded ThreadPoolExecutor for Gemini API calls (prevents resource exhaustion)
# Max 3 concurrent requests with queue size limit
_gemini_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="gemini")

# FIX H-1: Track active+queued requests with a thread-safe counter instead of
# accessing private ThreadPoolExecutor internals (_threads, _work_queue).
_gemini_active_requests = 0
_gemini_active_lock = threading.Lock()
_gemini_queued_requests = 0  # separate counter for submitted-but-not-started tasks

# lock used to serialize tracer installation to avoid cross-thread interference
_tracer_lock = threading.Lock()
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Session configuration
SESSION_COOKIE_NAME = 'codeup_session'
SESSION_COOKIE_MAX_AGE = 3600 * 24 * 7  # 7 days
# FIX H-4: Drive SESSION_COOKIE_SECURE from environment so TLS deployments are
# protected without requiring a manual code change.
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# FIX C-1: Register ONE module-level after_request handler instead of
# registering a new permanent handler on every new-session request.
# Previously get_session_id() contained @app.after_request inside its body,
# causing an unbounded accumulation of handlers and duplicate Set-Cookie headers.
@app.after_request
def set_session_cookie(response):
    """Set session cookie if not already present."""
    if not request.cookies.get(SESSION_COOKIE_NAME):
        session_id = str(uuid.uuid4())
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            max_age=SESSION_COOKIE_MAX_AGE,
            secure=SESSION_COOKIE_SECURE,
            httponly=SESSION_COOKIE_HTTPONLY,
            samesite=SESSION_COOKIE_SAMESITE
        )
    return response


def get_session_id():
    """Get or create a persistent session ID using cookies."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        session_id = str(uuid.uuid4())
    return session_id


def get_trace_storage():
    """Get the trace storage dict for current session."""
    session_id = get_session_id()
    with _session_traces_lock:
        if session_id not in _session_traces:
            _session_traces[session_id] = {
                'last_trace': [],
                'current_trace_index': -1,
                'trace_timestamp': time.time(),
                'trace_duration_ms': 0,
                'last_voice_action': None,
                'created_at': time.time(),
                # FIX H-2: Track last_accessed so expiry is activity-based, not
                # creation-based. Previously cleanup used created_at, which would
                # expire an actively-used 61-minute-old session mid-use.
                'last_accessed': time.time()
            }
        else:
            _session_traces[session_id]['last_accessed'] = time.time()
        return _session_traces[session_id]


def cleanup_old_sessions():
    """Clean up sessions that have been idle longer than _session_ttl."""
    now = time.time()
    with _session_traces_lock:
        # FIX H-2 (continued): expire based on last_accessed, not created_at.
        expired = [sid for sid, data in _session_traces.items()
                   if now - data.get('last_accessed', 0) > _session_ttl]
        for sid in expired:
            del _session_traces[sid]


# ==========================
# REQUEST SIZE VALIDATION
# ==========================
# Hard limits to prevent resource exhaustion
MAX_REQUEST_SIZE = 1_000_000  # 1 MB max request body
MAX_CODE_SIZE = 100_000       # 100 KB max code
MAX_GEMINI_TIMEOUT = 30       # 30 second timeout for LLM calls

# FIX L-4: Named constant with explanatory comment replaces magic number 40.
# Threshold of 40/100 chosen to allow reasonable fuzzy flexibility while
# rejecting clearly unrelated input; values below ~35 produce too many false positives.
VOICE_FUZZY_THRESHOLD = 40

@app.before_request
def validate_request_size():
    """Reject oversized requests before processing."""
    if request.content_length and request.content_length > MAX_REQUEST_SIZE:
        return jsonify({"success": False, "error": "Request too large (max 1MB)"}), 413

SNIPPETS_FILE = os.environ.get("SNIPPETS_FILE", "snippets.json")
DATA_DIR = os.environ.get("DATA_DIR", ".")


def _snippets_path() -> str:
    """Return the absolute path to the snippets file, ensuring the data directory exists."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass
    return os.path.join(DATA_DIR, SNIPPETS_FILE)

# ==========================
# BASIC HELPERS
# ==========================

def safejson() -> dict:
    """Safely parse JSON from the request, returning a dict."""
    d = request.get_json(silent=True)
    if isinstance(d, dict):
        return d
    return {}

def safe(v: Any, d: Any = "") -> Any:
    """Return `v` when not None, otherwise return default `d`."""
    return v if v is not None else d

def load_snippets() -> dict:
    """Load snippets from disk and return a dict with key `snippets`."""
    path = _snippets_path()
    if not os.path.exists(path):
        return {"snippets": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "snippets" in data:
                return data
    except Exception:
        pass
    return {"snippets": []}

_snippets_lock = threading.Lock()

def save_snippets(d: dict) -> None:
    """Save snippets atomically using temp-then-move to prevent corruption."""
    path = _snippets_path()
    dirpath = os.path.dirname(path) or "."

    with _snippets_lock:
        temp_path = None
        try:
            # FIX L-1: Removed redundant `import tempfile` that was previously
            # inside this function body; tempfile is already imported at module level.
            fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="snippets_", dir=dirpath)
            try:
                with os.fdopen(fd, 'w', encoding="utf-8") as f:
                    json.dump(d, f, indent=4)
            except:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            os.replace(temp_path, path)
        except Exception:
            try:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            raise

# ==========================
# GEMINI API CONFIG
# ==========================

# FIX L-2: `from typing import Any` moved to top-of-file import (already present above).
# The original file had it buried at line 140 after helper function definitions.

# default global fallback key (will only be used if session key missing)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "Insert_API_Key_Here")

# FIX L-3: Updated deprecated "gemini-pro" model name to a current model identifier.
GEMINI_MODEL = "gemini-2.0-flash"

# helper to retrieve current API key (session overrides global)
def _current_api_key():
    return getattr(_api_context, 'gemini_key', GEMINI_API_KEY)

def call_gemini(system_prompt, user_prompt, temperature=0.2, language="en"):
    """Call Gemini API with hard timeout to prevent hanging.

    Uses a per-session key if set via /api-config; otherwise falls back to a global key.
    """
    if os.environ.get("GEMINI_ENABLED", "1") != "1":
        return "AI service disabled"

    key = _current_api_key()
    if not key or key == "Insert_API_Key_Here":
        return "AI service not configured. Please set GEMINI_API_KEY environment variable or configure via /api-config."

    def _do_call():
        # FIX H-1 (active counter): increment before work, decrement in finally.
        global _gemini_active_requests, _gemini_queued_requests
        with _gemini_active_lock:
            _gemini_queued_requests = max(0, _gemini_queued_requests - 1)
            _gemini_active_requests += 1
        try:
            sp = system_prompt
            if language == "hi":
                sp = f"आप एक सहायक हैं जो हिंदी में सहायता प्रदान करते हैं। {system_prompt}"

            # FIX C-2: Construct a per-call genai.Client instance instead of
            # calling genai.configure() (a process-wide global mutation).
            # Previously, concurrent threads would overwrite each other's key:
            # Thread A configures key K1, Thread B overwrites with K2 before
            # Thread A's worker reads it — Thread A silently uses K2.
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=sp,
                    temperature=temperature,
                    max_output_tokens=1024,
                ),
            )
            return response.text.strip() if response.text else "No response generated"
        except Exception as e:
            return f"AI service error: {str(e)}"
        finally:
            with _gemini_active_lock:
                _gemini_active_requests -= 1

    # FIX H-1: Use the thread-safe counters instead of accessing private
    # executor internals (_threads, _work_queue) which are CPython implementation
    # details that can break on Python/library upgrades.
    with _gemini_active_lock:
        current_active = _gemini_active_requests
        current_queued = _gemini_queued_requests

    if current_active + current_queued >= 8:
        return "AI service is busy. Please try again later."

    with _gemini_active_lock:
        _gemini_queued_requests += 1

    try:
        future = _gemini_executor.submit(_do_call)
        result = future.result(timeout=MAX_GEMINI_TIMEOUT + 1)
        return result
    except Exception as e:
        with _gemini_active_lock:
            _gemini_queued_requests = max(0, _gemini_queued_requests - 1)
        return f"AI service is currently unavailable: {str(e)}"

def extract_code(text: str):
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    raw = m.group(1).strip() if m else text.strip()
    lines = []
    for line in raw.splitlines():
        low = line.strip().lower()
        if low.startswith("here is") or low.startswith("fixed code") or low.startswith("the corrected"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()

# ==========================
# MAIN PAGE
# ==========================

@app.route("/")
def index():
    return render_template("index.html")

# ==========================
# API KEY CONFIGURATION
# ==========================

def set_gemini_api_key(key):
    """Configure the Gemini API key for the current request/session.

    Stores the key in thread-local storage so concurrent sessions can each
    use a different key without interfering with one another.
    Note: genai.configure() call removed here to avoid the global-mutation
    race described in C-2; per-call clients are used in call_gemini() instead.
    """
    _api_context.gemini_key = key

@app.route("/api-config", methods=["POST"])
def api_config():
    """Set the Gemini API key for the session."""
    body = safejson()
    api_key = safe(body.get("api_key"), "")

    if not api_key or api_key == "Insert_API_Key_Here":
        return jsonify({"success": False, "error": "Invalid API key"}), 400

    try:
        set_gemini_api_key(api_key)
        test_response = call_gemini("Say 'OK'", "Test", language="en")
        if test_response.startswith("AI service error:") or \
           test_response.startswith("AI service is currently unavailable:") or \
           test_response == "AI service not configured. Please set GEMINI_API_KEY environment variable or configure via /api-config." or \
           test_response == "AI service disabled":
            return jsonify({"success": False, "error": "API key is invalid or Gemini API is unavailable"}), 401
        return jsonify({"success": True, "message": "API key configured successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ==========================
# ERROR EXPLAINER
# ==========================

def sanitize_traceback(traceback_str: str) -> str:
    """Remove sensitive information from traceback before sending to LLM."""
    lines = traceback_str.split('\n')
    sanitized = []
    for line in lines:
        line = re.sub(r'[A-Za-z]:[/\\][^:\n]*', '<path>', line)   # Windows paths
        line = re.sub(r'/home/[^:]*', '<path>', line)              # Linux /home
        line = re.sub(r'/Users/[^:]*', '<path>', line)             # macOS /Users
        line = re.sub(r'/var/[^:]*', '<path>', line)               # Linux /var
        # FIX M-1: Also sanitize /tmp/ paths so subprocess temp-script paths
        # (e.g. /tmp/tmpXXXX.py) are not leaked to the Gemini LLM or the client.
        line = re.sub(r'/tmp/[^:]*', '<path>', line)               # Linux /tmp
        sanitized.append(line)
    return '\n'.join(sanitized)

def explain_error(code: str, err_text: str, language="en") -> str:
    if language == "hi":
        system = (
            "आप एक पायथन ट्यूटर हैं जो एक अंधे प्रथम IDE में काम करते हैं।\n"
            "उपयोगकर्ता के कोड और त्रुटि को देखते हुए, समझाएं:\n"
            "- क्या त्रुटि हुई\n"
            "- किस पंक्ति पर (यदि दृश्यमान हो)\n"
            "- इसका सरल शब्दों में क्या अर्थ है\n"
            "- इसे कैसे ठीक करें\n"
            "अधिकतम 6 छोटी पंक्तियां। सीधे रहें।"
        )
    else:
        system = (
            "You are a Python tutor in a blind-first IDE.\n"
            "Given the user's code and the error, explain:\n"
            "- What error happened\n"
            "- On which line (if visible)\n"
            "- What it means in simple terms\n"
            "- How to fix it\n"
            "If the error mentions input() is not supported, explain that the sandbox "
            "does not support keyboard input, and show how to replace input() with a "
            "hardcoded value for testing.\n"
            "Max 6 short lines. Be direct."
        )
    safe_error = sanitize_traceback(err_text)
    user = f"Code:\n```python\n{code}\n```\n\nError:\n```\n{safe_error}\n```"
    return call_gemini(system, user, language=language)

# ==========================
# RUN CODE (WITH AI ERROR EXPLANATION)
# ==========================

# FIX H-5: Removed all dead in-process execution code (run_with_trace invocation,
# compile(), AST depth/node checks, SAFE_GLOBALS, SafeFunction, safe_print,
# safe_open — roughly lines 570–715 in the original). The actual execution path
# has been the subprocess sandbox since the refactor; keeping the dead code
# wasted CPU on every /run request and obscured the real execution path.
# run_with_trace itself is retained because it is referenced by the tracer inside
# the subprocess script template below.

def run_with_trace(compiled_code, globals_dict, locals_dict, time_limit=1.0):
    """Enhanced tracer with memory and concurrency safeguards.

    Executes code with hard limits to prevent unbounded execution.
    - Max recursion depth: 50 calls
    - Max line executions: 10,000 total
    - Max event buffer: 5,000 events
    - Max timeout: 1.0 sec
    """
    events = []
    last_locals = {}
    start_time = time.time()

    MAX_CALL_DEPTH = 50
    MAX_LINE_EXECS = 10000
    MAX_EVENTS = 5000

    def safe_repr(obj):
        try:
            r = repr(obj)
        except Exception:
            try:
                r = str(obj)
            except Exception:
                r = f"<{type(obj).__name__}?>"
        if len(r) > 200:
            return r[:197] + "..."
        return r

    call_depth = [0]
    line_count = [0]

    def append_event(ev):
        if len(events) < MAX_EVENTS:
            events.append(ev)
        elif len(events) == MAX_EVENTS:
            events.append({"type": "overflow", "note": "event limit reached; additional events dropped"})

    def tracer(frame, event, arg):
        nonlocal last_locals

        if frame.f_code.co_filename != '<user>':
            return tracer

        if event == "call":
            call_depth[0] += 1
            if call_depth[0] > MAX_CALL_DEPTH:
                raise RuntimeError("Recursion depth exceeded (%d)." % MAX_CALL_DEPTH)
        elif event == "return":
            call_depth[0] = max(0, call_depth[0] - 1)

        if time.time() - start_time > time_limit:
            raise TimeoutError(f"Execution timed out after {time_limit}s")

        if event == "line":
            line_count[0] += 1
            if line_count[0] > MAX_LINE_EXECS:
                raise RuntimeError("Execution exceeded line limit; possible infinite loop.")
            lineno = frame.f_lineno
            append_event({"type": "line_exec", "line": lineno})

            current = frame.f_locals.copy()
            changes = []
            for k, v in current.items():
                if k not in last_locals:
                    changes.append(f"{k} initialized to {safe_repr(v)}")
                elif last_locals[k] != v:
                    changes.append(f"{k} changed from {safe_repr(last_locals[k])} to {safe_repr(v)}")
            for k in last_locals:
                if k not in current:
                    changes.append(f"{k} went out of scope")
            if changes:
                append_event({"type": "state_change", "line": lineno, "changes": changes})
            last_locals = current

        elif event == "call":
            append_event({"type": "call", "function": frame.f_code.co_name, "line": frame.f_lineno})
        elif event == "return":
            append_event({"type": "return", "value": safe_repr(arg)})

        return tracer

    old_tracer = sys.gettrace()
    if old_tracer is not None and old_tracer is not tracer:
        raise RuntimeError("Another tracer active on this thread; aborting.")

    with _tracer_lock:
        sys.settrace(tracer)
        try:
            exec(compiled_code, globals_dict, locals_dict)
        finally:
            sys.settrace(old_tracer)

    return events

def classify_semantic_errors(trace):
    """
    ⚠️ HEURISTIC DETECTION (Assistance Signal, Not Guaranteed)

    These are heuristic patterns, not rigorous analysis.
    The real safety limits are in run_with_trace (hard caps on execution).
    """
    issues = []
    execution_count = {}

    for event in trace:
        if event["type"] in ("state_change", "line_exec"):
            line = event["line"]
            execution_count[line] = execution_count.get(line, 0) + 1

    for line, count in execution_count.items():
        if count > 50:
            issues.append({
                "category": "Possible Infinite Loop (Heuristic)",
                "line": line,
                "message": "This line executed 50+ times. You may have an infinite loop, but this is a heuristic hint, not a guarantee."
            })

    return issues


def save_execution_trace(trace, duration_ms=0):
    """Store trace in session storage for replay features.

    FIX H-3: All mutations to the session dict now happen inside the lock so
    that two concurrent requests for the same session cannot race on
    storage['last_trace'] etc. Previously get_trace_storage() released the
    lock before returning, leaving mutations unprotected.
    """
    session_id = get_session_id()
    with _session_traces_lock:
        if session_id not in _session_traces:
            _session_traces[session_id] = {
                'last_trace': [],
                'current_trace_index': -1,
                'trace_timestamp': time.time(),
                'trace_duration_ms': 0,
                'last_voice_action': None,
                'created_at': time.time(),
                'last_accessed': time.time()
            }
        storage = _session_traces[session_id]
        storage['last_trace'] = trace or []
        storage['current_trace_index'] = -1
        storage['trace_timestamp'] = time.time()
        storage['trace_duration_ms'] = duration_ms
        storage['last_accessed'] = time.time()
    cleanup_old_sessions()


class SafeModule:
    """Wrapper that blocks dangerous attribute access on imported modules."""
    def __init__(self, module):
        object.__setattr__(self, '_module', module)

    def __getattr__(self, name):
        BLOCKED = {'__dict__', '__class__', '__bases__', '__mro__', '__subclasses__',
                   '__loader__', '__spec__', '__builtins__', '__globals__', '__code__',
                   '__func__', '__self__'}
        if name in BLOCKED:
            raise AttributeError(f"Access to '{name}' is blocked for security reasons")
        return getattr(object.__getattribute__(self, '_module'), name)

    def __setattr__(self, name, value):
        if name == '_module':
            object.__setattr__(self, name, value)
        else:
            raise AttributeError(f"Cannot modify module attributes")

    def __dir__(self):
        return [x for x in dir(object.__getattribute__(self, '_module'))
                if not x.startswith('__')]

def restricted_import(name, *args, **kwargs):
    """Restrict imports to safe stdlib modules only."""
    ALLOWED = {"math", "random", "string", "datetime", "date"}
    if name not in ALLOWED:
        raise ImportError(f"Module '{name}' is not allowed. Allowed: {', '.join(ALLOWED)}")
    module = __import__(name, *args, **kwargs)
    return SafeModule(module)


@app.route("/run", methods=["POST"])
def run_code():
    body = safejson()
    code = safe(body.get("code"), "")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    # FIX H-5 (continued): The old in-process compile/AST/SAFE_GLOBALS block
    # that ran before the subprocess call has been removed. Only the subprocess
    # path remains, matching the actual execution reality.
    try:
        execution_start = time.time()
        sandbox = get_sandbox()
        workspace_dir = sandbox.workspace_dir
        trace_file = os.path.join(workspace_dir, "last_trace.json")

        script_content = f'''
import sys, time, json, traceback, os

ALLOWED_MODULES = {{'math','random','string','datetime','date'}}

class SafeFunction:
    def __init__(self, func):
        self._func = func
    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)
    def __getattr__(self, name):
        raise AttributeError(f'Access to {{name}} is blocked')

def restricted_import(name, *args, **kwargs):
    if name not in ALLOWED_MODULES:
        raise ImportError(f"Module '{{name}}' is not allowed.")
    return __import__(name, *args, **kwargs)

def _blocked_input(prompt=''):
    if prompt:
        print(prompt)
    raise RuntimeError(
        "input() is not supported in CodeUp's sandbox. "
        "To use a value, assign it directly instead: "
        "for example, replace  name = input('Your name?')  with  name = 'Alice'  "
        "and change 'Alice' to whatever you want to test with."
    )

SAFE_GLOBALS = {{
    'print': SafeFunction(print),
    'range': SafeFunction(range),
    'len': SafeFunction(len),
    'int': SafeFunction(int),
    'float': SafeFunction(float),
    'str': SafeFunction(str),
    'bool': SafeFunction(bool),
    'list': SafeFunction(list),
    'dict': SafeFunction(dict),
    'tuple': SafeFunction(tuple),
    'set': SafeFunction(set),
    'sum': SafeFunction(sum),
    'min': SafeFunction(min),
    'max': SafeFunction(max),
    'abs': SafeFunction(abs),
    'round': SafeFunction(round),
    'sorted': SafeFunction(sorted),
    'enumerate': SafeFunction(enumerate),
    'zip': SafeFunction(zip),
    'map': SafeFunction(map),
    'filter': SafeFunction(filter),
    'pow': SafeFunction(pow),
    'repr': SafeFunction(repr),
    '__builtins__': {{'None': None, 'False': False, 'True': True}},
    '__import__': restricted_import,
    'input': _blocked_input,
}}

code = os.environ.get('CODEUP_EXEC_CODE', '')
trace = []
last_locals = {{}}
start = time.time()

def safe_repr(v):
    try:
        r = repr(v)
    except Exception:
        try:
            r = str(v)
        except Exception:
            r = "<" + type(v).__name__ + ">"
    if len(r) > 200:
        return r[:197] + '...'
    return r

def tracer(frame, event, arg):
    global last_locals
    if frame.f_code.co_filename != '<user>':
        return tracer

    if event == 'line':
        line = frame.f_lineno
        trace.append({{'type':'line_exec','line':line}})
        current = frame.f_locals.copy()
        changes = []
        for k,v in current.items():
            if k not in last_locals:
                changes.append(k + " initialized to " + safe_repr(v))
            elif last_locals[k] != v:
                changes.append(k + " changed from " + safe_repr(last_locals[k]) + " to " + safe_repr(v))
        for k in last_locals:
            if k not in current:
                changes.append(k + " went out of scope")
        if changes:
            trace.append({{'type':'state_change','line':line,'changes':changes}})
        last_locals = current
    elif event == 'call':
        trace.append({{'type':'call','function':frame.f_code.co_name,'line':frame.f_lineno}})
    elif event == 'return':
        trace.append({{'type':'return','value':safe_repr(arg)}})
    return tracer

try:
    compiled = compile(code, '<user>', 'exec')
    sys.settrace(tracer)
    exec(compiled, SAFE_GLOBALS, {{}})
except Exception:
    traceback.print_exc(file=sys.stderr)
finally:
    sys.settrace(None)
    trace_file = os.environ.get('CODEUP_TRACE_FILE', '')
    if trace_file:
        with open(trace_file, 'w', encoding='utf-8') as f:
            json.dump({{'trace':trace,'duration_ms':int((time.time()-start)*1000)}}, f)
'''

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as script_file:
            script_file.write(script_content)
            script_file_path = script_file.name

        time_limit = 5  # seconds for subprocess
        try:
            env = os.environ.copy()
            env['CODEUP_EXEC_CODE'] = code
            env['CODEUP_TRACE_FILE'] = trace_file

            proc = subprocess.run(
                [sys.executable, script_file_path],
                capture_output=True,
                text=True,
                timeout=max(1, int(time_limit)),
                env=env
            )
            stdout_buf.write(proc.stdout)
            stderr_buf.write(proc.stderr)
        except subprocess.TimeoutExpired:
            stderr_buf.write(f"Execution timed out after {time_limit}s (subprocess)")
        finally:
            try:
                os.unlink(script_file_path)
            except OSError:
                pass

        trace = []
        trace_error = None
        try:
            if os.path.exists(trace_file):
                with open(trace_file, 'r', encoding='utf-8') as tfh:
                    data = json.load(tfh)
                    trace = data.get('trace', [])
            else:
                trace_error = 'Trace file not found in workspace'
        except json.JSONDecodeError:
            trace_error = 'Trace data was corrupted or incomplete'
        except Exception as e:
            trace_error = f'Error reading trace: {str(e)}'

        if not trace:
            msg = trace_error or 'No detailed trace available from subprocess (likely timed out or crashed)'
            trace = [{'type': 'subprocess_exec', 'note': msg}]

        semantic_issues = classify_semantic_errors(trace) if trace else []

        duration_ms = int((time.time() - execution_start) * 1000)
        save_execution_trace(trace, duration_ms)

        output = stdout_buf.getvalue()
        error = stderr_buf.getvalue()

        if error.strip():
            explanation = explain_error(code, error, language=safe(body.get("language"), "en"))
            return jsonify({
                "success": False,
                "error": error,
                "explanation": explanation
            })

        return jsonify({
            "success": True,
            "output": output or "Program finished with no output.",
            "trace": trace,
            "semantic_issues": semantic_issues
        })

    except Exception:
        tb = traceback.format_exc()
        explanation = explain_error(code, tb, language=safe(body.get("language"), "en"))
        return jsonify({
            "success": False,
            "error": tb,
            "explanation": explanation
        })

# ==========================
# ANALYZE
# ==========================

@app.route("/analyze", methods=["POST"])
def analyze():
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400

    if language == "hi":
        system = (
            "आप एक अंधे-पहले IDE में एक विशेषज्ञ पायथन ट्यूटर हैं।\n"
            "दिए गए पायथन कोड का विश्लेषण करें। रिपोर्ट करें:\n"
            "- यह क्या करता है\n"
            "- कोई बग या edge cases\n"
            "- कोई बुरी प्रथाएं\n"
            "कोई फ्लफ नहीं। अधिकतम 10 पंक्तियां।"
        )
    else:
        system = (
            "You are an expert Python tutor inside a blind-first IDE.\n"
            "Analyze the given Python code. Report:\n"
            "- What it does\n"
            "- Any bugs or edge cases\n"
            "- Any bad practices\n"
            "No fluff. Max 10 lines."
        )

    user = f"Python code:\n```python\n{code}\n```"
    analysis = call_gemini(system, user, language=language)

    analyzer = CodeAnalyzer()
    structure = analyzer.analyze(code)
    param_info = []

    if structure.get("functions"):
        for func in structure["functions"]:
            if func.get("params"):
                typed_params = []
                for param in func["params"]:
                    if isinstance(param, dict) and param.get("type"):
                        typed_params.append(f"{param['name']} ({param['type']})")
                    elif isinstance(param, dict):
                        typed_params.append(param.get("name", "unknown"))
                    else:
                        typed_params.append(str(param))
                if typed_params:
                    param_info.append(f"Function {func['name']} takes: {', '.join(typed_params)}")

    speech_text = analysis
    if param_info:
        speech_text = analysis + "\n" + "Parameters: " + " ".join(param_info)

    return jsonify({"analysis": analysis, "param_info": param_info, "speech": speech_text, "auto_speak": True})

# ==========================
# ADVISE (FEATURE / IMPROVEMENT SUGGESTIONS)
# ==========================

@app.route("/advise", methods=["POST"])
def advise():
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400

    if language == "hi":
        system = (
            "आप एक शुरुआती को मेंटर करने वाले एक वरिष्ठ पायथन इंजीनियर हैं।\n"
            "उनके कोड को देखते हुए, 3-7 ठोस सुधार सुझाएं:\n"
            "- नई सुविधाएं जो वे जोड़ सकते हैं\n"
            "- इसे साफ करने के लिए रीफैक्टर\n"
            "- संभालने के लिए edge cases\n"
            "स्पष्ट बुलेट पॉइंट्स लौटाएं। संक्षिप्त, सीधा।"
        )
    else:
        system = (
            "You are a senior Python engineer mentoring a beginner.\n"
            "Given their code, suggest 3–7 concrete improvements:\n"
            "- New features they could add\n"
            "- Refactors to make it cleaner\n"
            "- Edge cases to handle\n"
            "Return clear bullet points. Short, direct."
        )

    user = f"Code:\n```python\n{code}\n```"
    advice = call_gemini(system, user, language=language)
    return jsonify({"advice": advice})

# ==========================
# INLINE DEBUG SUGGESTIONS
# ==========================

@app.route("/debug-suggestions", methods=["POST"])
def debug_suggestions():
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        return jsonify({"success": True, "suggestions": []})

    if language == "hi":
        system = (
            "आप एक पायथन डीबगर सहायक हैं।\n"
            "कोड को देखें और सुधार के लिए विशिष्ट सुझाव दें।\n"
            "प्रत्येक सुझाव अलग लाइन पर हो:\n"
            "⚠️ [समस्या description]\n"
            "💡 [सुझाव]\n"
            "केवल वास्तविक समस्याओं को सूचीबद्ध करें।"
        )
    else:
        system = (
            "You are a Python debugging assistant.\n"
            "Review the code and suggest specific improvements.\n"
            "Each suggestion on a separate line:\n"
            "⚠️ [issue description]\n"
            "💡 [suggestion]\n"
            "Only list actual problems."
        )

    user = f"Code:\n```python\n{code}\n```"
    suggestions = call_gemini(system, user, language=language)

    lines = suggestions.split('\n')
    parsed_suggestions = []

    for line in lines:
        line = line.strip()
        if line.startswith('⚠️'):
            parsed_suggestions.append({"type": "warning", "icon": "⚠️", "text": line[2:].strip()})
        elif line.startswith('💡'):
            parsed_suggestions.append({"type": "suggestion", "icon": "💡", "text": line[2:].strip()})

    return jsonify({"success": True, "suggestions": parsed_suggestions})

# ==========================
# DESCRIBE LINE
# ==========================

@app.route("/describe", methods=["POST"])
def describe():
    body = safejson()
    code = safe(body.get("code"), "")

    # FIX M-2: Added MAX_CODE_SIZE check (was missing from this endpoint).
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    # FIX C-4: Wrap int() cast in try/except so non-numeric "line" values
    # return a 400 instead of an unhandled 500 ValueError/TypeError.
    try:
        line = int(body.get("line", 1))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid line number"}), 400

    language = safe(body.get("language"), "en")

    lines = code.splitlines()
    if not lines or line < 1 or line > len(lines):
        return jsonify({"success": False, "message": "Invalid line number."})

    start = max(1, line - 3)
    end = min(len(lines), line + 3)
    context = "\n".join(f"{i+1}: {lines[i]}" for i in range(start - 1, end))

    if language == "hi":
        system = (
            "आप एक अंधे डेवलपर के लिए केवल TARGET Python पंक्ति की व्याख्या करते हैं।\n"
            "सरल भाषा का प्रयोग करें। अधिकतम 3 छोटी पंक्तियां।"
        )
    else:
        system = (
            "You explain ONLY the target Python line for a blind developer.\n"
            "Use simple language. Max 3 short lines."
        )

    user = f"Code context:\n{context}\nTarget line: {line}"
    desc = call_gemini(system, user, language=language)
    return jsonify({"success": True, "description": desc})

# ==========================
# STRUCTURE PANEL (CODE NAVIGATION)
# ==========================

@app.route("/structure", methods=["POST"])
def structure():
    body = safejson()
    code = safe(body.get("code"), "")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        return jsonify({"success": True, "structure": {
            "imports": [], "functions": [], "classes": [], "loops": []
        }})

    analyzer = CodeAnalyzer()
    structure_data = analyzer.analyze(code)

    if "error" in structure_data:
        return jsonify({"success": False, "error": structure_data["error"]})

    return jsonify({"success": True, "structure": structure_data})

# ==========================
# READ LINE WITH CONTEXT
# ==========================

@app.route("/read-line-context", methods=["POST"])
def read_line_context():
    body = safejson()
    code = safe(body.get("code"), "")

    # FIX M-2: Added MAX_CODE_SIZE check (was missing from this endpoint).
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    # FIX C-4: Wrap int() cast to return 400 on bad input.
    try:
        line = int(body.get("line", 1))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid line number"}), 400

    lines = code.splitlines()
    if not lines or line < 1 or line > len(lines):
        return jsonify({"success": False, "message": "Invalid line number."})

    current_line = lines[line - 1]
    expanded_line = current_line.expandtabs(tabsize=4)
    indent_level = (len(expanded_line) - len(expanded_line.lstrip())) // 4

    context = "top level"
    if line > 1:
        for i in range(line - 2, -1, -1):
            prev = lines[i].strip()
            func_match = re.match(r'^def\s+(\w+)\s*\(', prev)
            if func_match:
                context = f"inside function {func_match.group(1)}"
                break
            class_match = re.match(r'^class\s+(\w+)\s*[\(:]', prev)
            if class_match:
                context = f"inside class {class_match.group(1)}"
                break
            elif prev.startswith("if ") or prev.startswith("elif "):
                context = "inside if block"
                break
            elif prev.startswith("for "):
                context = "inside for loop"
                break
            elif prev.startswith("while "):
                context = "inside while loop"
                break

    if not current_line.strip():
        response = f"Line {line}: blank line, {context}, indentation level {indent_level}"
    else:
        line_type = "code"
        if current_line.strip().startswith("#"):
            line_type = "comment"
        elif current_line.strip().startswith("def "):
            line_type = "function definition"
        elif current_line.strip().startswith("class "):
            line_type = "class definition"
        elif current_line.strip().startswith("return"):
            line_type = "return statement"
        elif current_line.strip().startswith("import ") or current_line.strip().startswith("from "):
            line_type = "import statement"

        response = (
            f"Line {line}, {context}, indentation level {indent_level}, "
            f"{line_type}: {current_line.strip()}"
        )

    return jsonify({
        "success": True,
        "response": response,
        "line": line,
        "indent_level": indent_level,
        "context": context,
        "content": current_line.strip()
    })

# ==========================
# VARIABLE TRACKING
# ==========================

@app.route("/track-variables", methods=["POST"])
def track_variables():
    body = safejson()
    code = safe(body.get("code"), "")

    # FIX M-2: Added MAX_CODE_SIZE check (was missing from this endpoint).
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    # FIX C-4: Wrap int() cast to return 400 on bad input.
    try:
        line = int(body.get("line", 1))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid line number"}), 400

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return jsonify({"success": False, "message": f"Syntax error: {e}"})

    class VariableTracker(ast.NodeVisitor):
        def __init__(self):
            self.variables = {}
            self.scope_stack: List[Tuple[str, Optional[str], int]] = [("global", None, 1)]
            self.scopes: List[dict] = []

        def current_scope(self):
            for kind, name, start in reversed(self.scope_stack):
                if kind != 'global' and name:
                    return name
            return 'global'

        def _record_var(self, name, lineno, is_store=True):
            scope = self.current_scope()
            if name not in self.variables:
                self.variables[name] = {
                    'name': name,
                    'phonetic': pronounce_variable(name),
                    'scope': scope,
                    'first_line': lineno,
                    'usage_count': 0
                }
            if is_store:
                self.variables[name]['usage_count'] += 1

        def _handle_target(self, target, lineno):
            if isinstance(target, ast.Name):
                self._record_var(target.id, lineno, is_store=True)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for elt in target.elts:
                    self._handle_target(elt, lineno)

        def visit_FunctionDef(self, node):
            name = node.name
            start = node.lineno
            end = self._max_lineno(node)
            self.scopes.append({'name': name, 'kind': 'function', 'start': start, 'end': end})
            self.scope_stack.append(('function', name, start))
            for arg in node.args.args:
                self._record_var(arg.arg, node.lineno, is_store=True)
            self.generic_visit(node)
            self.scope_stack.pop()

        def visit_Global(self, node):
            for n in node.names:
                if n in self.variables:
                    self.variables[n]['scope'] = 'global'

        def visit_Nonlocal(self, node):
            for n in node.names:
                if n in self.variables:
                    self.variables[n]['scope'] = 'nonlocal'

        def visit_ListComp(self, node):
            for gen in node.generators:
                self._handle_target(gen.target, getattr(gen, 'lineno', node.lineno))
            self.generic_visit(node)

        def visit_DictComp(self, node):
            for gen in node.generators:
                self._handle_target(gen.target, getattr(gen, 'lineno', node.lineno))
            self.generic_visit(node)

        def visit_SetComp(self, node):
            for gen in node.generators:
                self._handle_target(gen.target, getattr(gen, 'lineno', node.lineno))
            self.generic_visit(node)

        def visit_GeneratorExp(self, node):
            for gen in node.generators:
                self._handle_target(gen.target, getattr(gen, 'lineno', node.lineno))
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            name = node.name
            start = node.lineno
            end = self._max_lineno(node)
            self.scopes.append({'name': name, 'kind': 'class', 'start': start, 'end': end})
            self.scope_stack.append(('class', name, start))
            self.generic_visit(node)
            self.scope_stack.pop()

        def visit_Assign(self, node):
            for target in node.targets:
                self._handle_target(target, node.lineno)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if isinstance(node.target, ast.Name):
                self._record_var(node.target.id, node.lineno, is_store=True)
            else:
                self._handle_target(node.target, node.lineno)
            self.generic_visit(node)

        def visit_For(self, node):
            self._handle_target(node.target, node.lineno)
            self.generic_visit(node)

        def visit_With(self, node):
            for item in node.items:
                if item.optional_vars is not None:
                    self._handle_target(item.optional_vars, node.lineno)
            self.generic_visit(node)

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                if node.id in self.variables:
                    self.variables[node.id]['usage_count'] += 1
            elif isinstance(node.ctx, ast.Store):
                if node.id not in self.variables:
                    self._record_var(node.id, node.lineno, is_store=True)

        def _max_lineno(self, node):
            maxn = getattr(node, 'lineno', -1)
            for n in ast.walk(node):
                if hasattr(n, 'lineno'):
                    maxn = max(maxn, getattr(n, 'lineno', -1))
            return maxn

    tracker = VariableTracker()
    tracker.visit(tree)

    current_scope = 'global'
    best_start = -1
    for s in tracker.scopes:
        if s['start'] <= line <= s['end'] and s['start'] > best_start:
            current_scope = s['name']
            best_start = s['start']

    scope_vars = [v for v in tracker.variables.values()
                  if v['scope'] == current_scope or v['scope'] == 'global']

    return jsonify({
        'success': True,
        'current_scope': current_scope,
        'variables': scope_vars,
        'total_count': len(scope_vars)
    })


def pronounce_variable(var_name: str) -> str:
    """Convert variable name to phonetic pronunciation for screen readers."""
    if not var_name or not isinstance(var_name, str):
        return "unknown variable"

    var_name = var_name.strip()
    if not var_name:
        return "unknown variable"

    if "_" in var_name:
        segments = var_name.split("_")
        parts = [seg for seg in segments if seg]
        if not parts:
            return "underscore"
        return " underscore ".join(parts)

    camel_parts = []
    current = ""
    for char in var_name:
        if char.isupper() and current:
            camel_parts.append(current)
            current = char.lower()
        else:
            current += char
    if current:
        camel_parts.append(current)

    if len(camel_parts) > 1:
        return " camel case ".join(camel_parts)

    return var_name


@app.route("/find-variable-usage", methods=["POST"])
def find_variable_usage():
    body = safejson()
    code = safe(body.get("code"), "")

    # FIX M-2: Added MAX_CODE_SIZE check (was missing from this endpoint).
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    var_name = safe(body.get("variable"), "")
    if not var_name:
        return jsonify({"success": False, "message": "No variable specified."})

    lines = code.splitlines()
    usages = []

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return jsonify({
            "success": False,
            "message": "Code has syntax errors; cannot parse variable usage."
        })

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == var_name:
            line_no = node.lineno
            line_content = lines[line_no - 1] if line_no <= len(lines) else ""

            if isinstance(node.ctx, ast.Store):
                usage_type = "assignment"
            elif isinstance(node.ctx, ast.Load):
                usage_type = "read"
            else:
                usage_type = "usage"

            usages.append({"line": line_no, "content": line_content.strip(), "type": usage_type})

    if not usages:
        return jsonify({"success": False, "message": f"Variable '{var_name}' not found in code."})

    unique_usages = {}
    for u in usages:
        key = (u["line"], u["type"])
        if key not in unique_usages:
            unique_usages[key] = u

    sorted_usages = sorted(unique_usages.values(), key=lambda x: x["line"])

    return jsonify({
        "success": True,
        "variable": var_name,
        "phonetic": pronounce_variable(var_name),
        "usages": sorted_usages,
        "count": len(sorted_usages)
    })


# ==========================
# ERROR LOCATION BEACON
# ==========================

@app.route("/check-syntax", methods=["POST"])
def check_syntax():
    body = safejson()
    code = safe(body.get("code"), "")

    if not code.strip():
        return jsonify({"success": True, "has_errors": False, "message": "Code is empty."})

    errors = []

    try:
        compile(code, "<syntax_check>", "exec")
        return jsonify({"success": True, "has_errors": False, "message": "No syntax errors detected."})
    except IndentationError as e:
        errors.append({
            "line": e.lineno or 1,
            "type": "IndentationError",
            "message": str(e.msg or "Indentation error"),
            "severity": "high"
        })
    except SyntaxError as e:
        error_type = "SyntaxError"
        if "unexpected EOF" in str(e).lower():
            error_type = "MissingClosing"
        elif "invalid syntax" in str(e).lower():
            error_type = "InvalidSyntax"

        errors.append({
            "line": e.lineno or 1,
            "type": error_type,
            "message": str(e.msg or "Syntax error"),
            "severity": "high"
        })
    except Exception as e:
        errors.append({"line": 1, "type": "UnknownError", "message": str(e), "severity": "medium"})

    # FIX M-3: Only run the heuristic undefined-variable ast.parse() block when
    # errors is empty (i.e. compile() succeeded). Previously the code fell through
    # to ast.parse() even after a SyntaxError from compile(), causing a redundant
    # and wasteful re-parse of known-invalid code.
    if not errors:
        try:
            tree = ast.parse(code)
            defined = set()
            used_module_level = set()
            params = set()

            class UseCollector(ast.NodeVisitor):
                def __init__(self):
                    self.current_function = None

                def visit_FunctionDef(self, node):
                    for a in node.args.args:
                        params.add(a.arg)
                    prev = self.current_function
                    self.current_function = node
                    self.generic_visit(node)
                    self.current_function = prev

                def visit_For(self, node):
                    if isinstance(node.target, ast.Name):
                        params.add(node.target.id)
                    self.generic_visit(node)

                def visit_Assign(self, node):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            defined.add(target.id)
                    self.generic_visit(node)

                def visit_Name(self, node):
                    if isinstance(node.ctx, ast.Load):
                        if self.current_function is None:
                            used_module_level.add(node.id)

            UseCollector().visit(tree)

            builtins = {
                "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
                "chr", "classmethod", "compile", "complex", "delattr", "dict", "dir",
                "divmod", "enumerate", "eval", "exec", "filter", "float", "format",
                "frozenset", "getattr", "globals", "hasattr", "hash", "help", "hex",
                "id", "input", "int", "isinstance", "issubclass", "iter", "len",
                "list", "locals", "map", "max", "memoryview", "min", "next", "object",
                "oct", "open", "ord", "pow", "print", "property", "range", "repr",
                "reversed", "round", "set", "setattr", "slice", "sorted",
                "staticmethod", "str", "sum", "super", "tuple", "type", "vars", "zip",
                "BaseException", "Exception", "ArithmeticError", "AssertionError",
                "AttributeError", "EOFError", "ImportError", "IndexError", "KeyError",
                "KeyboardInterrupt", "MemoryError", "NameError", "NotImplementedError",
                "OSError", "RuntimeError", "SyntaxError", "SystemError", "SystemExit",
                "TypeError", "ValueError", "ZeroDivisionError",
                "True", "False", "None", "NotImplemented", "Ellipsis", "__debug__"
            }
            undefined = used_module_level - defined - builtins - params

            for var in sorted(undefined):
                errors.append({
                    "line": 0,
                    "type": "Potential undefined variable (heuristic)",
                    "message": f"Variable '{var}' may be used before assignment at module level",
                    "severity": "low"
                })
        except Exception:
            pass

    return jsonify({
        "success": True,
        "has_errors": len(errors) > 0,
        "errors": errors,
        "error_count": len(errors)
    })

# ==========================
# FIX
# ==========================

@app.route("/fix", methods=["POST"])
def fix():
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400

    if language == "hi":
        system = (
            "आप एक Python ऑटो-फिक्सर हैं।\n"
            "सिंटैक्स और स्पष्ट runtime त्रुटियों को ठीक करें।\n"
            "एक ही मकसद रखें। स्पष्टता को थोड़ा सुधारें।\n"
            "केवल वैध पायथन कोड लौटाएं। कोई टिप्पणी नहीं। कोई MARKDOWN नहीं।"
        )
    else:
        system = (
            "You are a Python auto-fixer.\n"
            "Fix syntax and obvious runtime errors.\n"
            "Keep the same intent. Improve clarity slightly.\n"
            "RETURN ONLY VALID PYTHON CODE. NO COMMENTS. NO MARKDOWN."
        )

    user = f"Fix this code:\n```python\n{code}\n```"
    raw = call_gemini(system, user, temperature=0.1, language=language)
    fixed = extract_code(raw)
    return jsonify({"success": True, "code": fixed})

# ==========================
# GENERATE CODE (NL → CODE)
# ==========================

@app.route("/generate-code", methods=["POST"])
def generate_code():
    body = safejson()
    prompt = safe(body.get("prompt"), "")
    language = safe(body.get("language"), "en")

    if len(prompt) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Prompt too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not prompt.strip():
        return jsonify({"success": False, "error": "Prompt cannot be empty"}), 400

    if language == "hi":
        system = (
            "आप एक शुरुआत-अनुकूल, अंधे-प्रथम IDE के लिए एक पायथन कोड जेनरेटर हैं।\n"
            "उपयोगकर्ता एक कार्य को प्राकृतिक भाषा में वर्णित करेगा।\n"
            "आपको केवल वैध पायथन कोड लौटाना चाहिए जो कार्य को हल करता है।\n"
            "टिप्पणी, markdown, या व्याख्या न जोड़ें।\n"
            "बस कोड।"
        )
    else:
        system = (
            "You are a Python code generator for a beginner-friendly, blind-first IDE.\n"
            "The user will describe a task in natural language.\n"
            "You must return ONLY valid Python code that solves the task.\n"
            "Do not add comments, markdown, or explanations.\n"
            "Just the code."
        )

    user = f"Task description:\n{prompt}"
    raw = call_gemini(system, user, temperature=0.2, language=language)
    code = extract_code(raw)
    return jsonify({"success": True, "code": code})

# ==========================
# SNIPPETS
# ==========================

@app.route("/snippets", methods=["GET", "POST"])
def snippets():
    if request.method == "GET":
        data = load_snippets()
        snippets_list = data.get("snippets", [])

        speech_text = None
        if snippets_list:
            names = [s.get("name", f"Snippet {i+1}") for i, s in enumerate(snippets_list)]
            if len(names) == 1:
                speech_text = f"You have 1 snippet: {names[0]}."
            else:
                speech_text = f"You have {len(names)} snippets: {', '.join(names[:-1])}, and {names[-1]}."
        else:
            speech_text = "You have no saved snippets."

        data["speech"] = speech_text
        return jsonify(data)

    data = load_snippets()
    body = safejson()

    name = str(safe(body.get("name"), "Untitled"))
    code = str(safe(body.get("code"), ""))

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if len(name) > 256:
        return jsonify({"success": False, "error": "Name too long (max 256 chars)"}), 400

    new_id = str(uuid.uuid4())
    data["snippets"].append({"id": new_id, "name": name, "code": code})
    save_snippets(data)
    return jsonify({"success": True, "id": new_id, "speech": f"Saved snippet: {name}"})

@app.route("/snippets/<sid>", methods=["PUT", "DELETE"])
def snippet_detail(sid):
    data = load_snippets()

    if request.method == "DELETE":
        deleted_name = None
        for s in data["snippets"]:
            if str(s["id"]) == str(sid):
                deleted_name = s.get("name", f"Snippet {sid}")
                break

        data["snippets"] = [s for s in data["snippets"] if str(s["id"]) != str(sid)]
        save_snippets(data)
        speech = f"Deleted snippet: {deleted_name}." if deleted_name else "Snippet deleted."
        return jsonify({"success": True, "speech": speech})

    body = safejson()
    found = False
    snippet_name = None
    for s in data["snippets"]:
        if str(s["id"]) == str(sid):
            found = True
            snippet_name = s.get("name", "Snippet")
            if "name" in body:
                new_name = str(body["name"])
                if len(new_name) > 256:
                    return jsonify({"success": False, "error": "Name too long (max 256 chars)"}), 400
                s["name"] = new_name
                snippet_name = new_name
            if "code" in body:
                new_code = str(body["code"])
                if len(new_code) > MAX_CODE_SIZE:
                    return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
                s["code"] = new_code
    if not found:
        return jsonify({"success": False, "error": "Snippet not found"}), 404
    save_snippets(data)
    speech = f"Updated snippet: {snippet_name}." if snippet_name else "Snippet updated."
    return jsonify({"success": True, "speech": speech})

# ==========================
# VOICE COMMANDS
# ==========================

COMMANDS = {
    "run": ["run", "execute", "run code", "execute code", "start code", "start program"],
    "analyze": ["analyze", "analyse", "analyze code", "analyse code", "explain code", "check code", "review code"],
    "speak": ["speak output", "read output", "read the output", "say the output"],
    "fix": ["fix", "fix code", "auto fix", "repair code", "correct code"],
    "repeat_last_action": ["repeat", "do that again", "again", "repeat last"],
    "repeat_last_speech": ["repeat that", "say that again", "repeat message", "repeat output"],
    "advise": ["advise on code", "advice on code", "improve code", "how to improve code"],
    "generate_code": ["generate code", "write code", "create code", "make code"],
    "clear_editor": ["clear editor", "clear code", "clear file", "reset code"],
    "read_line_enhanced": ["read line with context", "enhanced read line", "describe line position", "where am i", "line context"],
    "sonify_block": ["sonify block", "audio structure", "hear structure", "play code structure", "sound out code"],
    "list_variables": ["what variables", "list variables", "show variables", "what variables are available", "variables in scope"],
    "check_errors": ["check for errors", "check syntax", "find errors", "are there errors", "syntax check"],
    "locate_error": ["where is the error", "where is error", "find error", "jump to error", "go to error"],
    "stop_beacon": ["stop error beacon", "stop beacon", "turn off beacon", "disable beacon"],
    "go_back": ["go back", "navigate back", "back", "previous position"],
    "go_forward": ["go forward", "navigate forward", "forward", "next position"],
    "show_history": ["show history", "navigation history", "where have i been"],
    "help": ["help", "show help", "what can you do", "list commands"],
    "file_stats": ["file stats", "how many lines", "file statistics", "code stats"],
    "go_to_top": ["go to top", "jump to top", "top of file"],
    "go_to_bottom": ["go to bottom", "jump to bottom", "bottom of file", "end of file"],
    "copy_code": ["copy code", "copy to clipboard", "copy this"],
    "paste_code": ["paste code", "paste from clipboard", "paste"],
    "restart_tutorial": ["restart tutorial", "reset tutorial", "start over", "redo tutorial", "tutorial again"],
    "story_mode":       ["tell the story", "narrate execution", "execution story", "what happened", "story mode", "कहानी बताओ"],
    "mentor_mode":      ["learning mode", "mentor mode", "tutor mode", "teach me", "मुझे सिखाओ"],
    "quiz_me":          ["quiz me", "test me", "challenge me", "quiz करो", "test करो"],
    "bug_challenge":    ["bug challenge", "debug challenge", "give me a bug", "bug ढूंढो"],
    "clear_breakpoints":["clear breakpoints", "remove breakpoints", "delete breakpoints"]
}


def best_two_commands(text: str):
    text = text.lower().strip()
    best_name = None
    best_score = 0
    second_name = None
    second_score = 0

    for name, phrases in COMMANDS.items():
        for p in phrases:
            s = fuzz.ratio(text, p)
            if s > best_score:
                second_name, second_score = best_name, best_score
                best_name, best_score = name, s
            elif s > second_score:
                second_name, second_score = name, s
    return best_name, best_score, second_name, second_score

# ==========================
# FILE SUMMARY
# ==========================

@app.route("/summarize", methods=["POST"])
def summarize():
    body = safejson()
    code = safe(body.get("code"), "")

    # FIX M-2: Added MAX_CODE_SIZE check (was missing from this endpoint).
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    language = safe(body.get("language"), "en")

    if language == "hi":
        system = (
            "आप एक अंधे शुरुआत के लिए पायथन कोड को सारांशित करते हैं।\n"
            "4 से 7 छोटे बुलेट पॉइंट्स में समझाएं:\n"
            "- उद्देश्य\n"
            "- इनपुट\n"
            "- आउटपुट\n"
            "- मुख्य तर्क\n"
            "- कोई जोखिम\n"
            "कोई मार्कडाउन नहीं, कोई इमोजी नहीं।"
        )
    else:
        system = (
            "You summarize Python code for a blind beginner.\n"
            "Explain in 4 to 7 short bullet points:\n"
            "- Purpose\n"
            "- Inputs\n"
            "- Outputs\n"
            "- Main logic\n"
            "- Any risks\n"
            "No markdown, no emojis."
        )

    user = f"Code:\n```python\n{code}\n```"
    summary = call_gemini(system, user, language=language)
    return jsonify({"summary": summary})

# ==========================
# DIFF EXPLAIN
# ==========================

@app.route("/diff-explain", methods=["POST"])
def diff_explain():
    body = safejson()
    before = safe(body.get("before"), "")
    after = safe(body.get("after"), "")

    # FIX M-2: Added MAX_CODE_SIZE check (was missing from this endpoint).
    if len(before) > MAX_CODE_SIZE or len(after) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    language = safe(body.get("language"), "en")

    if language == "hi":
        system = (
            "आप Python कोड के दो संस्करणों के बीच क्या बदला है यह समझाते हैं।\n"
            "एक अंधे छात्र के लिए सरल शब्दों में समझाएं।\n"
            "उल्लेख करें कि क्या ठीक किया गया और क्यों।\n"
            "अधिकतम 6 छोटी पंक्तियां।"
        )
    else:
        system = (
            "You explain what changed between two versions of Python code.\n"
            "Explain in simple terms for a blind student.\n"
            "Mention what was fixed and why.\n"
            "Max 6 short lines."
        )

    user = f"BEFORE:\n```python\n{before}\n```\n\nAFTER:\n```python\n{after}\n```"
    explanation = call_gemini(system, user, language=language)
    return jsonify({"explanation": explanation})

# ==========================
# SUGGEST NEXT LINE
# ==========================

@app.route("/suggest-next", methods=["POST"])
def suggest_next():
    body = safejson()
    code = safe(body.get("code"), "")
    line = body.get("line", 1)
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    if not code.strip():
        return jsonify({"success": False, "suggestions": [], "error": "Code is empty"})

    lines = code.splitlines()
    current_line = lines[min(int(line) - 1, len(lines) - 1)] if lines else ""
    context_start = max(0, int(line) - 5)
    context = "\n".join(f"{i+1}: {l}" for i, l in enumerate(lines[context_start:int(line)]))

    if language == "hi":
        system = (
            "आप एक Python code completion assistant हैं।\n"
            "दिए गए code context को देखकर अगली 3 possible lines suggest करें।\n"
            "JSON format में respond करें:\n"
            "{\"suggestions\": [\"line1\", \"line2\", \"line3\"]}\n"
            "केवल valid Python lines। कोई explanation नहीं। केवल JSON।"
        )
    else:
        system = (
            "You are a Python code completion assistant.\n"
            "Given the code context, suggest the 3 most likely next lines.\n"
            "Respond ONLY with JSON in this exact format:\n"
            "{\"suggestions\": [\"line1\", \"line2\", \"line3\"]}\n"
            "Only valid Python lines. No explanation. JSON only."
        )

    user = f"Code context (cursor after line {line}):\n{context}\nCurrent line: {current_line}"

    try:
        raw = call_gemini(system, user, temperature=0.2, language=language)
        import json as _json
        # Strip markdown fences if present
        clean = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
        parsed = _json.loads(clean)
        suggestions = parsed.get("suggestions", [])[:3]
        return jsonify({"success": True, "suggestions": suggestions})
    except Exception as e:
        return jsonify({"success": False, "suggestions": [], "error": str(e)})

def _trace_playback(direction):
    storage = get_trace_storage()
    trace = storage.get('last_trace', []) or []
    if not trace:
        return "No execution trace available."

    idx = storage.get('current_trace_index', -1)
    if direction == 'next':
        idx = min(len(trace) - 1, idx + 1)
    elif direction == 'prev':
        idx = max(0, idx - 1)
    elif direction == 'current_change':
        if idx < 0:
            return "No current trace step selected. Say 'next step' to begin."
    else:
        return "Unknown trace navigation command."

    storage['current_trace_index'] = idx
    event = _get_trace_event(idx)
    return _event_to_speech(event, idx, len(trace))


@app.route("/voice-command", methods=["POST"])
def voice():
    text = safe(safejson().get("text"), "")
    parsed = parse_intent(text)
    intent = parsed.get("intent")
    slots = parsed.get("slots", {})
    confidence = parsed.get("confidence", 0.0)

    storage = get_trace_storage()

    # Handle repeat command
    if intent == "repeat":
        last_action = storage.get('last_voice_action', None)
        if not last_action:
            return jsonify({"success": True, "action": "unknown", "heard": "repeat", "message": "No previous command to repeat"})
        # FIX M-4: Store last_voice_action as a (dict, status_code) tuple so that
        # repeating preserves the original HTTP status code. Previously it was stored
        # as response.get_json() (a plain dict) and returned directly, causing Flask
        # to auto-serialize it as 200 OK regardless of the original status.
        return jsonify(last_action[0]), last_action[1]

    def _store_and_return(response_dict, status_code=200):
        """Helper: save action for repeat, then return the response."""
        storage['last_voice_action'] = (response_dict, status_code)
        return jsonify(response_dict), status_code

    if intent and confidence >= 0.75:
        if intent == "goto_line":
            return _store_and_return({"success": True, "action": "goto_line", "line": slots.get("line_number", 1), "confidence": confidence})
        if intent == "read_line":
            return _store_and_return({"success": True, "action": "read_line", "line": slots.get("line_number", 1), "confidence": confidence})
        if intent == "describe_line":
            return _store_and_return({"success": True, "action": "describe_line", "line": slots.get("line_number", 1), "confidence": confidence})
        if intent == "delete_line":
            return _store_and_return({"success": True, "action": "delete_line", "line": slots.get("line_number", 1), "confidence": confidence})
        if intent == "read_function":
            return _store_and_return({"success": True, "action": "read_function", "function_name": slots.get("function_name"), "confidence": confidence})
        if intent == "find_function":
            return _store_and_return({"success": True, "action": "find_function", "function_name": slots.get("function_name"), "confidence": confidence})
        if intent == "find_class":
            return _store_and_return({"success": True, "action": "find_class", "class_name": slots.get("class_name"), "confidence": confidence})
        if intent == "sonify_function":
            return _store_and_return({"success": True, "action": "sonify_function", "function_name": slots.get("function_name"), "confidence": confidence})
        if intent == "sonify_class":
            return _store_and_return({"success": True, "action": "sonify_class", "class_name": slots.get("class_name"), "confidence": confidence})
        if intent == "show_structure":
            return _store_and_return({"success": True, "action": "show_structure", "confidence": confidence})
        if intent == "run":
            return _store_and_return({"success": True, "action": "run", "confidence": confidence})
        if intent == "analyze":
            return _store_and_return({"success": True, "action": "analyze", "confidence": confidence})
        if intent == "fix":
            return _store_and_return({"success": True, "action": "fix", "confidence": confidence})
        if intent == "advise":
            return _store_and_return({"success": True, "action": "advise", "confidence": confidence})
        if intent == "summarize":
            return _store_and_return({"success": True, "action": "summarize", "confidence": confidence})
        if intent == "generate_code":
            return _store_and_return({"success": True, "action": "generate_code", "prompt": slots.get("prompt", ""), "confidence": confidence})
        if intent == "rename_snippet":
            return _store_and_return({"success": True, "action": "rename_snippet", "id": slots.get("id"), "new_name": slots.get("new_name"), "confidence": confidence})
        if intent == "save_snippet_named":
            return _store_and_return({"success": True, "action": "save_snippet_named", "name": slots.get("name", "Untitled"), "confidence": confidence})
        if intent == "insert_function":
            return _store_and_return({"success": True, "action": "insert_function", "function_name": slots.get("function_name", "my_function"), "confidence": confidence})
        if intent == "insert_class":
            return _store_and_return({"success": True, "action": "insert_class", "class_name": slots.get("class_name", "MyClass"), "confidence": confidence})
        if intent == "insert_loop":
            return _store_and_return({"success": True, "action": "insert_loop", "loop_var": slots.get("loop_var", "i"), "iterable": slots.get("iterable", "range(10)"), "confidence": confidence})
        if intent == "insert_if":
            return _store_and_return({"success": True, "action": "insert_if", "condition": slots.get("condition", "True"), "confidence": confidence})
        if intent == "append_line":
            return _store_and_return({"success": True, "action": "append_line", "text": slots.get("text", ""), "confidence": confidence})
        if intent == "replace_line":
            return _store_and_return({"success": True, "action": "replace_line", "line_number": slots.get("line_number", 1), "text": slots.get("text", ""), "confidence": confidence})
        if intent == "insert_line":
            return _store_and_return({"success": True, "action": "insert_line", "line_number": slots.get("line_number", 1), "text": slots.get("text", ""), "confidence": confidence})
        if intent == "add_parameter":
            return _store_and_return({"success": True, "action": "add_parameter", "param_name": slots.get("param_name", "param"), "function_name": slots.get("function_name"), "confidence": confidence})
        if intent == "suggest_next":
            return _store_and_return({"success": True, "action": "suggest_next", "confidence": confidence})
        if intent == "choose_suggestion":
            return _store_and_return({"success": True, "action": "choose_suggestion", "choice": slots.get("choice", 1), "confidence": confidence})
        if intent == "story_mode":
            return _store_and_return({"success": True, "action": "story_mode", "confidence": confidence})
        if intent == "set_breakpoint":
            return _store_and_return({"success": True, "action": "set_breakpoint", "line_number": slots.get("line_number", 1), "confidence": confidence})
        if intent == "clear_breakpoints":
            return _store_and_return({"success": True, "action": "clear_breakpoints", "confidence": confidence})
        if intent == "watch_variable":
            return _store_and_return({"success": True, "action": "watch_variable", "variable": slots.get("variable", ""), "confidence": confidence})
        if intent == "debug_continue":
            return _store_and_return({"success": True, "action": "debug_continue", "confidence": confidence})
        if intent == "debug_step_in":
            return _store_and_return({"success": True, "action": "debug_step_in", "confidence": confidence})
        if intent == "debug_step_out":
            return _store_and_return({"success": True, "action": "debug_step_out", "confidence": confidence})
        if intent == "mentor_mode":
            return _store_and_return({"success": True, "action": "mentor_mode", "confidence": confidence})
        if intent == "quiz_me":
            return _store_and_return({"success": True, "action": "quiz_me", "topic": slots.get("topic", "Python basics"), "confidence": confidence})
        if intent == "explain_concept":
            return _store_and_return({"success": True, "action": "explain_concept", "concept": slots.get("concept", "variables"), "confidence": confidence})
        if intent == "bug_challenge":
            return _store_and_return({"success": True, "action": "bug_challenge", "confidence": confidence})
        if intent == "clear_editor":
            return _store_and_return({"success": True, "action": "clear_editor", "confidence": confidence})
        if intent == "read_output":
            return _store_and_return({"success": True, "action": "read_output", "confidence": confidence})
        if intent == "next_step":
            return _store_and_return({"success": True, "action": "next_step", "speech": _trace_playback("next"), "confidence": confidence})
        if intent == "previous_step":
            return _store_and_return({"success": True, "action": "previous_step", "speech": _trace_playback("prev"), "confidence": confidence})
        if intent == "what_changed":
            return _store_and_return({"success": True, "action": "what_changed", "speech": _trace_playback("current_change"), "confidence": confidence})

    # Fallback: fuzzy matching on COMMANDS
    lower_text = text.lower().strip()
    best, bscore, second, sscore = best_two_commands(lower_text)

    if best and bscore >= VOICE_FUZZY_THRESHOLD:
        if bscore < 75 or (second and (bscore - sscore) < 15):
            options = [best, second] if second else [best]
            return jsonify({
                "success": True,
                "action": "confirm",
                "options": options,
                "heard": text,
                "confidence": bscore / 100.0
            })
        return jsonify({"success": True, "action": best, "confidence": bscore / 100.0})

    return jsonify({"success": True, "action": "unknown", "heard": text, "confidence": 0.0})

# ==========================
# SANDBOXED FILE SYSTEM
# ==========================

@app.route("/fs/write", methods=["POST"])
def fs_write():
    body = safejson()
    filepath = safe(body.get("path"), "")
    content = safe(body.get("content"), "")
    if not filepath:
        return jsonify({"success": False, "error": "Path is required"}), 400
    sandbox = get_sandbox()
    result = sandbox.write(filepath, content)
    return jsonify(result)

@app.route("/fs/read", methods=["POST"])
def fs_read():
    body = safejson()
    filepath = safe(body.get("path"), "")
    if not filepath:
        return jsonify({"success": False, "error": "Path is required"}), 400
    sandbox = get_sandbox()
    result = sandbox.read(filepath)
    return jsonify(result)

@app.route("/fs/delete", methods=["POST"])
def fs_delete():
    body = safejson()
    filepath = safe(body.get("path"), "")
    if not filepath:
        return jsonify({"success": False, "error": "Path is required"}), 400
    sandbox = get_sandbox()
    result = sandbox.delete(filepath)
    return jsonify(result)

@app.route("/fs/list", methods=["POST"])
def fs_list():
    body = safejson()
    dirpath = safe(body.get("path"), ".")
    sandbox = get_sandbox()
    result = sandbox.list_files(dirpath)
    return jsonify(result)

@app.route("/fs/info", methods=["GET"])
def fs_info():
    sandbox = get_sandbox()
    result = sandbox.get_workspace_info()
    return jsonify(result)

# ==========================
# EXECUTION TRACE (for playback)
# ==========================

@app.route("/execution-trace", methods=["GET"])
def get_execution_trace():
    """Get the last execution trace for playback.

    FIX C-3: Rewritten to read from session storage (get_trace_storage()) instead
    of _trace_context (a threading.local() that was never written). Previously
    this endpoint always returned an empty trace because _trace_context.last_trace
    was never assigned anywhere; save_execution_trace() writes to _session_traces,
    not to _trace_context.
    """
    storage = get_trace_storage()
    trace = storage.get('last_trace', []) or []
    idx = storage.get('current_trace_index', -1)
    duration = storage.get('trace_duration_ms', 0)

    return jsonify({
        "trace": trace,
        "current_index": idx,
        "total_lines_executed": sum(1 for e in trace if e.get('type') == 'line_exec'),
        "duration_ms": duration,
        "message": "Trace playback data"
    })


def _get_trace_event(index):
    """Retrieve a single trace event by index from session storage.

    FIX C-3 (continued): Previously read from _trace_context (never written),
    so always returned None, making all voice trace-playback responses say
    'No trace event at this position.' Now correctly reads from get_trace_storage().
    """
    storage = get_trace_storage()
    trace = storage.get('last_trace', []) or []
    if index < 0 or index >= len(trace):
        return None
    return trace[index]


def _event_to_speech(event, idx=None, total=None):
    if not event:
        return "No trace event at this position."

    step_prefix = f"Step {idx + 1} of {total}: " if idx is not None and total is not None else ""

    t = event.get('type')
    if t == 'line_exec':
        return f"{step_prefix}Executed line {event.get('line')}"
    if t == 'state_change':
        changes = '; '.join(event.get('changes', []))
        return f"{step_prefix}State changed on line {event.get('line')}: {changes}"
    if t == 'call':
        return f"{step_prefix}Called function {event.get('function')} at line {event.get('line')}"
    if t == 'return':
        return f"{step_prefix}Returned value {event.get('value')}"
    return f"{step_prefix}{t}: {event}"


# ==========================
# EXECUTION STORY MODE
# ==========================

@app.route("/execution-story", methods=["POST"])
def execution_story():
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    storage = get_trace_storage()
    trace = storage.get('last_trace', []) or []

    if not trace:
        msg = "No execution trace available. Please run your code first."
        if language == "hi":
            msg = "कोई execution trace उपलब्ध नहीं है। पहले अपना code चलाएं।"
        return jsonify({"success": False, "story": msg})

    # Build a compact trace summary for the LLM
    events_summary = []
    for e in trace[:50]:  # cap at 50 events to stay within token limit
        t = e.get('type')
        if t == 'line_exec':
            events_summary.append(f"line {e.get('line')} executed")
        elif t == 'state_change':
            changes = ', '.join(e.get('changes', []))
            events_summary.append(f"line {e.get('line')}: {changes}")
        elif t == 'call':
            events_summary.append(f"function '{e.get('function')}' called at line {e.get('line')}")
        elif t == 'return':
            events_summary.append(f"returned {e.get('value')}")

    trace_text = "\n".join(events_summary)

    if language == "hi":
        system = (
            "आप एक Python execution narrator हैं जो blind learners के लिए काम करते हैं।\n"
            "नीचे दिए गए code और execution trace को देखकर एक simple, conversational story बनाएं।\n"
            "Dry technical output की जगह narrative language use करें। जैसे:\n"
            "'Program शुरू हुआ। पहले x को 5 की value मिली। फिर loop शुरू हुआ...'\n"
            "अधिकतम 8 छोटे वाक्य। Simple Hindi में।"
        )
    else:
        system = (
            "You are a Python execution narrator for blind learners.\n"
            "Given the code and execution trace, narrate what happened as a clear story.\n"
            "Use conversational language instead of dry technical output. For example:\n"
            "'The program began. First, x was given the value 5. Then the loop started...'\n"
            "Maximum 8 short sentences. Simple English."
        )

    user = f"Code:\n```python\n{code}\n```\n\nExecution trace:\n{trace_text}"
    story = call_gemini(system, user, temperature=0.3, language=language)
    return jsonify({"success": True, "story": story})


# ==========================
# MENTOR / LEARNING MODE
# ==========================

@app.route("/mentor/quiz", methods=["POST"])
def mentor_quiz():
    body = safejson()
    topic = safe(body.get("topic"), "Python basics")
    language = safe(body.get("language"), "en")

    if language == "hi":
        system = (
            "आप एक blind Python learner के लिए quiz questions बनाते हैं।\n"
            "दिए गए topic पर एक quiz question बनाएं जिसमें:\n"
            "- एक clear question\n"
            "- 3 options (A, B, C)\n"
            "- सही answer\n"
            "- एक line explanation\n"
            "JSON format में respond करें:\n"
            "{\"question\": \"...\", \"options\": [\"A: ...\", \"B: ...\", \"C: ...\"], \"answer\": \"A\", \"explanation\": \"...\"}\n"
            "केवल JSON। कोई extra text नहीं।"
        )
    else:
        system = (
            "You create quiz questions for a blind Python learner.\n"
            "Create one quiz question on the given topic with:\n"
            "- A clear question\n"
            "- 3 options (A, B, C)\n"
            "- The correct answer letter\n"
            "- A one-line explanation\n"
            "Respond ONLY with JSON:\n"
            "{\"question\": \"...\", \"options\": [\"A: ...\", \"B: ...\", \"C: ...\"], \"answer\": \"A\", \"explanation\": \"...\"}\n"
            "JSON only. No extra text."
        )

    user = f"Topic: {topic}"
    try:
        raw = call_gemini(system, user, temperature=0.4, language=language)
        clean = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
        import json as _json
        parsed = _json.loads(clean)
        return jsonify({"success": True, "quiz": parsed})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/mentor/explain", methods=["POST"])
def mentor_explain():
    body = safejson()
    concept = safe(body.get("concept"), "variables")
    language = safe(body.get("language"), "en")

    if language == "hi":
        system = (
            "आप एक blind beginner के लिए Python concepts explain करते हैं।\n"
            "Concept को बिल्कुल simple तरीके से समझाएं जैसे पहली बार सुन रहे हों।\n"
            "एक real-life analogy दें। फिर एक short code example।\n"
            "अधिकतम 6 छोटी lines।"
        )
    else:
        system = (
            "You explain Python concepts to a blind beginner.\n"
            "Explain the concept as simply as possible, as if hearing it for the first time.\n"
            "Give a real-life analogy. Then a short code example.\n"
            "Maximum 6 short lines."
        )

    user = f"Concept: {concept}"
    explanation = call_gemini(system, user, temperature=0.2, language=language)
    return jsonify({"success": True, "explanation": explanation})


@app.route("/mentor/bug-challenge", methods=["POST"])
def mentor_bug_challenge():
    body = safejson()
    language = safe(body.get("language"), "en")

    if language == "hi":
        system = (
            "आप एक Python debugging challenge बनाते हैं।\n"
            "एक short buggy Python program बनाएं (5-10 lines)।\n"
            "JSON format में respond करें:\n"
            "{\"code\": \"buggy code here\", \"hint\": \"एक line hint\", \"bug\": \"actual bug description\", \"fixed\": \"fixed code\"}\n"
            "Bugs simple होने चाहिए: syntax error, wrong variable name, off-by-one, missing colon आदि।\n"
            "केवल JSON।"
        )
    else:
        system = (
            "You create Python debugging challenges.\n"
            "Create a short buggy Python program (5-10 lines).\n"
            "Respond ONLY with JSON:\n"
            "{\"code\": \"buggy code here\", \"hint\": \"one line hint\", \"bug\": \"actual bug description\", \"fixed\": \"fixed code\"}\n"
            "Bugs should be simple: syntax error, wrong variable name, off-by-one, missing colon, etc.\n"
            "JSON only."
        )

    user = "Generate a bug challenge"
    try:
        raw = call_gemini(system, user, temperature=0.5, language=language)
        clean = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
        import json as _json
        parsed = _json.loads(clean)
        return jsonify({"success": True, "challenge": parsed})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ==========================
# VOICE INTENTS — story, breakpoint, mentor
# ==========================

# These are handled in the /voice-command route.
# New intents added to the bottom of the intent dispatch block:
# story_mode, set_breakpoint, clear_breakpoints, watch_variable,
# debug_continue, debug_step_in, debug_step_out,
# mentor_mode, quiz_me, explain_concept, bug_challenge


# ==========================

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
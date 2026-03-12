from flask import Flask, render_template, request, jsonify
import json, os, traceback, io, contextlib, re, requests, ast, sys, time, threading, uuid, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Optional
from rapidfuzz import fuzz
import google.generativeai as genai

# Thread-local storage for request-scoped sys.settrace
_trace_context = threading.local()
# Executor for LLM calls
_gemini_executor = ThreadPoolExecutor(max_workers=2)
app = Flask(__name__)


# ==========================
# REQUEST SIZE VALIDATION
# ==========================
# Hard limits to prevent resource exhaustion
MAX_REQUEST_SIZE = 1_000_000  # 1 MB max request body
MAX_CODE_SIZE = 100_000       # 100 KB max code
MAX_GEMINI_TIMEOUT = 30       # 30 second timeout for LLM calls

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
        # If we cannot create the data dir, fallback to current dir
        pass
    return os.path.join(DATA_DIR, SNIPPETS_FILE)

# ==========================
# BASIC HELPERS
# ==========================

def safejson() -> dict:
    """Safely parse JSON from the request, returning a dict.

    Uses Flask's `request.get_json(silent=True)` and guarantees a dictionary
    to callers. Logs and returns empty dict on failure.
    """
    d = request.get_json(silent=True)
    if isinstance(d, dict):
        return d
    return {}

from typing import Any


def safe(v: Any, d: Any = "") -> Any:
    """Return `v` when not None, otherwise return default `d`.

    This helper ensures callers don't need to repeatedly check for None.
    """
    return v if v is not None else d

def load_snippets() -> dict:
    """Load snippets from disk and return a dict with key `snippets`.

    If file is missing or malformed, returns an empty structure.
    """
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
    """Save snippets atomically using temp-then-move to prevent corruption.

    The filename is resolved via `_snippets_path()` so it honors `DATA_DIR`.
    """
    import tempfile

    path = _snippets_path()
    dirpath = os.path.dirname(path) or "."

    with _snippets_lock:
        temp_fd = None
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="snippets_", dir=dirpath)
            with os.fdopen(fd, 'w', encoding="utf-8") as f:
                json.dump(d, f, indent=4)
            # Atomic replace
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "Insert_API_Key_Here")
GEMINI_MODEL = "gemini-pro"

# Configure Gemini API
if GEMINI_API_KEY != "Insert_API_Key_Here":
    genai.configure(api_key=GEMINI_API_KEY)

def call_gemini(system_prompt, user_prompt, temperature=0.2, language="en"):
    """Call Gemini API with hard timeout to prevent hanging."""
    # Allow tests and offline usage to disable Gemini via env var
    if os.environ.get("GEMINI_ENABLED", "1") != "1":
        return "AI service disabled"
    
    # Check if API key is configured
    if GEMINI_API_KEY == "Insert_API_Key_Here":
        return "AI service not configured. Please set GEMINI_API_KEY environment variable."

    def _do_call():
        try:
            # Adapt system prompt based on language
            if language == "hi":
                system_prompt = f"आप एक सहायक हैं जो हिंदी में सहायता प्रदान करते हैं। {system_prompt}"
            
            model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system_prompt)
            response = model.generate_content(
                user_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=1024
                )
            )
            return response.text.strip() if response.text else "No response generated"
        except Exception as e:
            return f"AI service error: {str(e)}"

    try:
        future = _gemini_executor.submit(_do_call)
        result = future.result(timeout=MAX_GEMINI_TIMEOUT + 1)
        return result
    except Exception as e:
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

@app.route("/api-config", methods=["POST"])
def api_config():
    """Set the Gemini API key for the session."""
    body = safejson()
    api_key = safe(body.get("api_key"), "")
    
    if not api_key or api_key == "Insert_API_Key_Here":
        return jsonify({"success": False, "error": "Invalid API key"}), 400
    
    try:
        # Try to set and configure the API key
        set_gemini_api_key(api_key)
        # Test the API key by making a simple call
        test_response = call_gemini("Say 'OK'", "Test", language="en")
        if "error" in test_response.lower() or "invalid" in test_response.lower():
            return jsonify({"success": False, "error": "API key is invalid or Gemini API is unavailable"}), 401
        return jsonify({"success": True, "message": "API key configured successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ==========================
# ERROR EXPLAINER
# ==========================

def sanitize_traceback(traceback_str: str) -> str:
    """
    Remove sensitive information from traceback before sending to LLM.
    - Strip absolute file paths
    - Remove /home, /Users, C: paths
    - Keep only filename and line number
    """
    lines = traceback_str.split('\n')
    sanitized = []
    for line in lines:
        # Replace absolute paths with just filename
        line = re.sub(r'[A-Za-z]:[/\\][^:\n]*', '<path>', line)  # Windows paths
        line = re.sub(r'/home/[^:]*', '<path>', line)  # Linux /home
        line = re.sub(r'/Users/[^:]*', '<path>', line)  # macOS /Users
        line = re.sub(r'/var/[^:]*', '<path>', line)  # Linux /var
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
            "Max 6 short lines. Be direct."
        )
    # Sanitize traceback to remove paths before sending to LLM
    safe_error = sanitize_traceback(err_text)
    user = f"Code:\n```python\n{code}\n```\n\nError:\n```\n{safe_error}\n```"
    return call_gemini(system, user, language=language)

# ==========================
# RUN CODE (WITH AI ERROR EXPLANATION)
# ==========================

def run_with_trace(compiled_code, globals_dict, locals_dict, time_limit=1.0):
    """
    Execute code with hard limits to prevent unbounded execution.
    Uses THREAD-LOCAL storage to isolate concurrent requests (no cross-request trace pollution).
    
    HARD LIMITS (not best-effort):
    - Max recursion depth: 50 calls (prevents deep recursion exploits)
    - Max line executions: 10,000 total (prevents infinite loops)
    - Max timeout: 1.0 sec (timeout as backup, not primary guard)
    
    If any limit is exceeded, execution aborts with clear error.
    """
    # Store trace state in thread-local storage to prevent concurrent request interference
    events = []
    last_locals = {}
    start_time = time.time()
    
    # Hard execution limits
    MAX_CALL_DEPTH = 50
    MAX_LINE_EXECS = 10000
    call_depth = [0]  # mutable to track in tracer
    line_count = [0]   # mutable to track in tracer

    def tracer(frame, event, arg):
        nonlocal last_locals

        # HARD LIMIT 1: Call depth
        if event == "call":
            call_depth[0] += 1
            if call_depth[0] > MAX_CALL_DEPTH:
                raise RuntimeError(f"Recursion depth exceeded ({MAX_CALL_DEPTH} calls). Code may have infinite recursion.")
        elif event == "return":
            call_depth[0] = max(0, call_depth[0] - 1)

        # HARD LIMIT 2: Timeout (backup, not primary)
        if time.time() - start_time > time_limit:
            raise TimeoutError(f"Execution timed out after {time_limit}s")

        if event == "line":
            # HARD LIMIT 3: Total line executions
            line_count[0] += 1
            if line_count[0] > MAX_LINE_EXECS:
                raise RuntimeError(f"Execution exceeded line limit ({MAX_LINE_EXECS} lines). Code may be in an infinite loop.")
            
            lineno = frame.f_lineno

            events.append({
                "type": "line_exec",
                "line": lineno
            })

            current = frame.f_locals.copy()
            changes = []

            for k, v in current.items():
                if k not in last_locals:
                    changes.append(f"{k} initialized to {v}")
                elif last_locals[k] != v:
                    changes.append(f"{k} changed from {last_locals[k]} to {v}")

            for k in last_locals:
                if k not in current:
                    changes.append(f"{k} went out of scope")

            if changes:
                events.append({
                    "type": "state_change",
                    "line": lineno,
                    "changes": changes
                })

            last_locals = current

        elif event == "call":
            events.append({
                "type": "call",
                "function": frame.f_code.co_name,
                "line": frame.f_lineno
            })

        elif event == "return":
            events.append({
                "type": "return",
                "value": str(arg)
            })

        return tracer

    # Save the previous tracer (if any, from parent context)
    old_tracer = sys.gettrace()
    # If another tracer is active (not ours), refuse to run to avoid cross-request contamination
    if old_tracer is not None and old_tracer is not tracer:
        raise RuntimeError("Another tracing function is active in this process; cannot safely trace execution. Consider configuring USE_SUBPROCESS_SANDBOX=1 to run code in an isolated process.")

    # Set our tracer for this request
    sys.settrace(tracer)
    try:
        exec(compiled_code, globals_dict, locals_dict)
    finally:
        # Restore the previous tracer (critical for thread safety)
        sys.settrace(old_tracer)
    
    return events

def classify_semantic_errors(trace):
    """
    ⚠️ HEURISTIC DETECTION (Assistance Signal, Not Guaranteed)
    
    These are heuristic patterns, not rigorous analysis:
    - Infinite loop detection: counts repeated lines (may have false positives/negatives)
    - This is an assistance signal to the user, NOT a safety guarantee
    
    The real safety limits are in run_with_trace (hard caps on execution).
    """
    issues = []
    execution_count = {}

    for event in trace:
        if event["type"] in ("state_change", "line_exec"):
            line = event["line"]
            execution_count[line] = execution_count.get(line, 0) + 1

    # HEURISTIC: flag lines that executed many times without visible state change
    # (This is a hint to the user, not a guarantee of a bug)
    for line, count in execution_count.items():
        if count > 50:
            issues.append({
                "category": "Possible Infinite Loop (Heuristic)",
                "line": line,
                "message": "This line executed 50+ times. You may have an infinite loop, but this is a heuristic hint, not a guarantee."
            })

    return issues


class SafeModule:
    """Wrapper that blocks dangerous attribute access on imported modules"""
    def __init__(self, module):
        object.__setattr__(self, '_module', module)
    
    def __getattr__(self, name):
        # Block access to dangerous attributes that enable sandbox escape
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
    """
    Restrict imports to safe stdlib modules only.
    Block file/OS/network access.
    Wrap result to prevent __mro__, __dict__, __class__ exploits.
    """
    ALLOWED = {"math", "random", "string", "datetime", "date"}
    if name not in ALLOWED:
        raise ImportError(f"Module '{name}' is not allowed. Allowed: {', '.join(ALLOWED)}")
    module = __import__(name, *args, **kwargs)
    return SafeModule(module)


@app.route("/run", methods=["POST"])
def run_code():
    body = safejson()
    code = safe(body.get("code"), "")
    
    # Validate code size
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    
    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    # SAFE_GLOBALS: Only explicitly whitelisted functions, no __builtins__ access
    # This prevents object.__subclasses__() and __mro__ escapes
    # Create a safe print that doesn't expose globals
    def safe_print(*args, **kwargs):
        print(*args, **kwargs, flush=True)
    # Block attribute access on safe functions
    class SafeFunction:
        def __init__(self, func):
            self._func = func
        def __call__(self, *args, **kwargs):
            return self._func(*args, **kwargs)
        def __getattr__(self, name):
            raise AttributeError(f"Access to '{name}' is blocked")

    SAFE_GLOBALS = {
        "print": SafeFunction(safe_print),
        "range": SafeFunction(range),
        "len": SafeFunction(len),
        "int": SafeFunction(int),
        "float": SafeFunction(float),
        "str": SafeFunction(str),
        "bool": SafeFunction(bool),
        "list": SafeFunction(list),
        "dict": SafeFunction(dict),
        "tuple": SafeFunction(tuple),
        "set": SafeFunction(set),
        "sum": SafeFunction(sum),
        "min": SafeFunction(min),
        "max": SafeFunction(max),
        "abs": SafeFunction(abs),
        "round": SafeFunction(round),
        "sorted": SafeFunction(sorted),
        "reversed": SafeFunction(reversed),
        "enumerate": SafeFunction(enumerate),
        "zip": SafeFunction(zip),
        "map": SafeFunction(map),
        "filter": SafeFunction(filter),
        "isinstance": SafeFunction(isinstance),
        "type": SafeFunction(type),
        "hasattr": SafeFunction(hasattr),
        "getattr": SafeFunction(getattr),
        "callable": SafeFunction(callable),
        "id": SafeFunction(id),
        "hash": SafeFunction(hash),
        "ord": SafeFunction(ord),
        "chr": SafeFunction(chr),
        "bin": SafeFunction(bin),
        "hex": SafeFunction(hex),
        "oct": SafeFunction(oct),
        "divmod": SafeFunction(divmod),
        "pow": SafeFunction(pow),
        "slice": SafeFunction(slice),
        "repr": SafeFunction(repr),
        "any": SafeFunction(any),
        "all": SafeFunction(all),
        "__builtins__": {},
        "input": SafeFunction(lambda *a: ""),
        "__import__": restricted_import,
    }

    try:
        compiled = compile(code, "<user>", "exec")
        # Pre-compute short-circuit call targets from AST so we can detect skipped calls
        short_circuit_map = {}
        try:
            parsed = ast.parse(code)
            for node in ast.walk(parsed):
                # boolean short-circuit (and/or)
                if isinstance(node, ast.BoolOp):
                    for val in node.values:
                        if isinstance(val, ast.Call):
                            # get a simple function name representation
                            fn = None
                            if isinstance(val.func, ast.Name):
                                fn = val.func.id
                            elif isinstance(val.func, ast.Attribute):
                                parts = []
                                a = val.func
                                while isinstance(a, ast.Attribute):
                                    parts.append(a.attr)
                                    a = a.value
                                if isinstance(a, ast.Name):
                                    parts.append(a.id)
                                fn = ".".join(reversed(parts))
                            if fn:
                                short_circuit_map.setdefault(node.lineno, set()).add(fn)
                # ternary expressions (a if cond else b) may skip evaluation
                if isinstance(node, ast.IfExp):
                    for part in (node.body, node.orelse):
                        if isinstance(part, ast.Call):
                            fn = None
                            if isinstance(part.func, ast.Name):
                                fn = part.func.id
                            elif isinstance(part.func, ast.Attribute):
                                parts = []
                                a = part.func
                                while isinstance(a, ast.Attribute):
                                    parts.append(a.attr)
                                    a = a.value
                                if isinstance(a, ast.Name):
                                    parts.append(a.id)
                                fn = ".".join(reversed(parts))
                            if fn:
                                short_circuit_map.setdefault(node.lineno, set()).add(fn)
        except Exception:
            short_circuit_map = {}

        # Execution time limit (seconds) - configurable via env
        time_limit = float(os.environ.get("EXECUTION_TIME_LIMIT", "1.0"))
        # If the runtime is configured to use a subprocess sandbox, run there for safety.
        USE_SUBPROCESS = os.environ.get("USE_SUBPROCESS_SANDBOX", "0") == "1"
        if USE_SUBPROCESS:
            # Write code to a temporary file and execute with a subprocess and a hard timeout.
            tf = None
            try:
                fd, tf = tempfile.mkstemp(suffix=".py", prefix="usercode_")
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(code)
                # Execute in separate process with timeout; capture stdout/stderr
                proc = subprocess.run([sys.executable, tf], capture_output=True, text=True, timeout=max(1, int(time_limit)))
                stdout_buf.write(proc.stdout)
                stderr_buf.write(proc.stderr)
                trace = [{ 'type': 'subprocess_exec', 'note': 'Executed in subprocess; detailed trace unavailable.' }]
            except subprocess.TimeoutExpired:
                stderr_buf.write(f"Execution timed out after {time_limit}s (subprocess)")
                trace = []
            finally:
                try:
                    if tf and os.path.exists(tf):
                        os.remove(tf)
                except Exception:
                    pass
        else:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                # Use both globals and locals to prevent locals from polluting globals
                exec_locals = {}
                trace = run_with_trace(compiled, SAFE_GLOBALS, exec_locals, time_limit=time_limit)
            # Post-process trace to add synthetic events for short-circuited calls
            if short_circuit_map:
                for lineno, fnset in short_circuit_map.items():
                    # check if any call with that line and function name occurred
                    called = False
                    for ev in trace:
                        if ev.get('type') == 'call' and ev.get('line') == lineno:
                            if ev.get('function') in fnset:
                                called = True
                                break
                    # if not called but line executed, mark short-circuit
                    if not called:
                        # find insertion point after first line_exec for that line
                        idx = next((i for i,e in enumerate(trace) if e.get('type')=='line_exec' and e.get('line')==lineno), None)
                        sc_event = { 'type': 'short_circuit', 'line': lineno, 'skipped_calls': list(fnset) }
                        if idx is None:
                            trace.append(sc_event)
                        else:
                            trace.insert(idx+1, sc_event)

            semantic_issues = classify_semantic_errors(trace)

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
    language = safe(body.get("language"), "en")  # Default to English
    
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

    return jsonify({"analysis": analysis})

# ==========================
# ADVISE (FEATURE / IMPROVEMENT SUGGESTIONS)
# ==========================

@app.route("/advise", methods=["POST"])
def advise():
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")  # Default to English
    
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
# DESCRIBE LINE
# ==========================

@app.route("/describe", methods=["POST"])
def describe():
    body = safejson()
    code = safe(body.get("code"), "")
    line = int(body.get("line", 1))
    language = safe(body.get("language"), "en")  # Default to English

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

# =========================
# READ LINE WITH CONTEXT
# ==========================

@app.route("/read-line-context", methods=["POST"])
def read_line_context():
    """
    Provides rich context for blind users navigating code line-by-line.
    Reports: line number, indentation level, code structure context, and the line itself.
    """
    body = safejson()
    code = safe(body.get("code"), "")
    line = int(body.get("line", 1))

    lines = code.splitlines()
    if not lines or line < 1 or line > len(lines):
        return jsonify({"success": False, "message": "Invalid line number."})

    current_line = lines[line - 1]
    
    # Calculate indentation
    indent_level = (len(current_line) - len(current_line.lstrip())) // 4
    
    # Determine code structure context
    context = "top level"
    if line > 1:
        for i in range(line - 2, -1, -1):
            prev = lines[i].strip()
            if prev.startswith("def "):
                context = f"inside function {prev[4:].split('(')[0]}"
                break
            elif prev.startswith("class "):
                context = f"inside class {prev[6:].split('(')[0].split(':')[0]}"
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
    
    # Check if line is blank
    if not current_line.strip():
        response = f"Line {line}: blank line, {context}, indentation level {indent_level}"
    else:
        # Identify line type
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
    """
    Analyzes code and returns all variables with their scope, type, and usage.
    """
    body = safejson()
    code = safe(body.get("code"), "")
    line = int(body.get("line", 1))
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return jsonify({"success": False, "message": f"Syntax error: {e}"})

    class VariableTracker(ast.NodeVisitor):
        def __init__(self):
            self.variables = {}  # name -> info
            # (kind, name, start_lineno)
            self.scope_stack: List[Tuple[str, Optional[str], int]] = [("global", None, 1)]
            # list of scopes with kind included
            self.scopes: List[dict] = []

        def current_scope(self):
            # return the most specific scope name or 'global'
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
            # Count store and load as usages (store indicates definition)
            if is_store:
                self.variables[name]['usage_count'] += 1

        def _handle_target(self, target, lineno):
            if isinstance(target, ast.Name):
                self._record_var(target.id, lineno, is_store=True)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for elt in target.elts:
                    self._handle_target(elt, lineno)

        def visit_FunctionDef(self, node):
            # store the raw function name and keep kind separate
            name = node.name
            start = node.lineno
            end = self._max_lineno(node)
            self.scopes.append({'name': name, 'kind': 'function', 'start': start, 'end': end})
            self.scope_stack.append(('function', name, start))
            # parameters are stores
            for arg in node.args.args:
                self._record_var(arg.arg, node.lineno, is_store=True)
            self.generic_visit(node)
            self.scope_stack.pop()

        def visit_Global(self, node):
            # Mark global declarations so scope resolution can account for them
            for n in node.names:
                if n in self.variables:
                    self.variables[n]['scope'] = 'global'

        def visit_Nonlocal(self, node):
            # Mark nonlocal declarations
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
            # store the raw class name and keep kind separate
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
            # annotated assign: target can be a Name
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
            # Count reads as usages; stores were already counted in targets
            if isinstance(node.ctx, ast.Load):
                if node.id in self.variables:
                    self.variables[node.id]['usage_count'] += 1
            elif isinstance(node.ctx, ast.Store):
                # sometimes stores appear outside Assign (e.g., comprehension); treat as definition
                if node.id not in self.variables:
                    self._record_var(node.id, node.lineno, is_store=True)
            # do not call generic_visit for Name

        def _max_lineno(self, node):
            maxn = getattr(node, 'lineno', -1)
            for n in ast.walk(node):
                if hasattr(n, 'lineno'):
                    maxn = max(maxn, getattr(n, 'lineno', -1))
            return maxn

    tracker = VariableTracker()
    tracker.visit(tree)

    # Determine current scope by finding the most specific scope that contains the requested line
    current_scope = 'global'
    best_start = -1
    for s in tracker.scopes:
        if s['start'] <= line <= s['end'] and s['start'] > best_start:
            current_scope = s['name']
            best_start = s['start']

    # Collect visible variables: those in current scope or globals
    scope_vars = [v for v in tracker.variables.values() if v['scope'] == current_scope or v['scope'] == 'global']

    return jsonify({
        'success': True,
        'current_scope': current_scope,
        'variables': scope_vars,
        'total_count': len(scope_vars)
    })


def pronounce_variable(var_name: str) -> str:
    """
    Convert variable name to phonetic pronunciation for screen readers.
    """
    parts = []
    
    # Handle underscores
    if "_" in var_name:
        segments = var_name.split("_")
        parts = [seg for seg in segments if seg]
        return " underscore ".join(parts)
    
    # Handle camelCase
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
    """
    Finds all locations where a variable is used (using AST).
    Ignores comments, strings, and shadowed variables.
    """
    body = safejson()
    code = safe(body.get("code"), "")
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
    
    # Collect all Name nodes for the target variable, with scope context
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == var_name:
            line_no = node.lineno
            line_content = lines[line_no - 1] if line_no <= len(lines) else ""
            
            # Distinguish between assignment (Store) and read (Load)
            if isinstance(node.ctx, ast.Store):
                usage_type = "assignment"
            elif isinstance(node.ctx, ast.Load):
                usage_type = "read"
            else:
                usage_type = "usage"
            
            usages.append({
                "line": line_no,
                "content": line_content.strip(),
                "type": usage_type
            })
    
    if not usages:
        return jsonify({
            "success": False,
            "message": f"Variable '{var_name}' not found in code."
        })
    
    # Remove duplicates and sort by line
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
    """
    Real-time syntax checking without executing code.
    Returns error locations and types.
    """
    body = safejson()
    code = safe(body.get("code"), "")
    
    if not code.strip():
        return jsonify({
            "success": True,
            "has_errors": False,
            "message": "Code is empty."
        })
    
    errors = []
    
    try:
        compile(code, "<syntax_check>", "exec")
        return jsonify({
            "success": True,
            "has_errors": False,
            "message": "No syntax errors detected."
        })
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
        errors.append({
            "line": 1,
            "type": "UnknownError",
            "message": str(e),
            "severity": "medium"
        })
    
    # Basic undefined variable check (simple heuristic)
    try:
        tree = ast.parse(code)
        defined = set()
        used_module_level = set()
        used_in_functions = set()
        params = set()

        class UseCollector(ast.NodeVisitor):
            def __init__(self):
                self.current_function = None

            def visit_FunctionDef(self, node):
                # collect function parameters
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
                    else:
                        used_in_functions.add(node.id)

        UseCollector().visit(tree)

        # Check for undefined variables used at module level (heuristic)
        builtins = {"print", "range", "len", "int", "float", "str", "list", "dict"}
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
    language = safe(body.get("language"), "en")  # Default to English
    
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
    language = safe(body.get("language"), "en")  # Default to English
    
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
        return jsonify(load_snippets())

    data = load_snippets()
    body = safejson()

    name = str(safe(body.get("name"), "Untitled"))
    code = str(safe(body.get("code"), ""))
    
    # Validate size
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if len(name) > 256:
        return jsonify({"success": False, "error": "Name too long (max 256 chars)"}), 400

    # Use UUIDs for snippet ids to avoid collisions across restarts/concurrent writes
    new_id = str(uuid.uuid4())
    data["snippets"].append({"id": new_id, "name": name, "code": code})
    save_snippets(data)

    return jsonify({"success": True, "id": new_id})

@app.route("/snippets/<sid>", methods=["PUT", "DELETE"])
def snippet_detail(sid):
    data = load_snippets()

    if request.method == "DELETE":
        data["snippets"] = [s for s in data["snippets"] if str(s["id"]) != str(sid)]
        save_snippets(data)
        return jsonify({"success": True})

    body = safejson()
    found = False
    for s in data["snippets"]:
        if str(s["id"]) == str(sid):
            found = True
            if "name" in body:
                new_name = str(body["name"])
                if len(new_name) > 256:
                    return jsonify({"success": False, "error": "Name too long (max 256 chars)"}), 400
                s["name"] = new_name
            if "code" in body:
                new_code = str(body["code"])
                if len(new_code) > MAX_CODE_SIZE:
                    return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
                s["code"] = new_code
    if not found:
        return jsonify({"success": False, "error": "Snippet not found"}), 404
    save_snippets(data)
    return jsonify({"success": True})

# ==========================
# VOICE COMMANDS (WITH CONFIRMATION + NAV/EDIT/ADVISE)
# ==========================

COMMANDS = {
    "run": [
        "run", "execute", "run code", "execute code", "start code", "start program"
    ],
    "analyze": [
        "analyze", "analyse", "analyze code", "analyse code",
        "explain code", "check code", "review code"
    ],
    "speak": [
        "speak output", "read output", "read the output", "say the output"
    ],
    "fix": [
        "fix", "fix code", "auto fix", "repair code", "correct code"
    ],
    "repeat_last_action": [
        "repeat", "do that again", "again", "repeat last"
    ],
    "repeat_last_speech": [
        "repeat that", "say that again", "repeat message", "repeat output"
    ],
    "advise": [
        "advise on code", "advice on code", "improve code", "how to improve code"
    ],
    "generate_code": [
        "generate code", "write code", "create code", "make code"
    ],
    "clear_editor": [
        "clear editor", "clear code", "clear file", "reset code"
    ],
    "read_line_enhanced": [
        "read line with context", "enhanced read line", "describe line position",
        "where am i", "line context"
    ],
    "sonify_block": [
        "sonify block", "audio structure", "hear structure", 
        "play code structure", "sound out code"
    ],
    "list_variables": [
        "what variables", "list variables", "show variables",
        "what variables are available", "variables in scope"
    ],
    "check_errors": [
        "check for errors", "check syntax", "find errors",
        "are there errors", "syntax check"
    ],
    "locate_error": [
        "where is the error", "where is error", "find error",
        "jump to error", "go to error"
    ],
    "stop_beacon": [
        "stop error beacon", "stop beacon", "turn off beacon", "disable beacon"
    ],
    "go_back": [
        "go back", "navigate back", "back", "previous position"
    ],
    "go_forward": [
        "go forward", "navigate forward", "forward", "next position"
    ],
    "show_history": [
        "show history", "navigation history", "where have i been"
    ],
    "help": [
        "help", "show help", "what can you do", "list commands"
    ],
    "file_stats": [
        "file stats", "how many lines", "file statistics", "code stats"
    ],
    "go_to_top": [
        "go to top", "jump to top", "top of file"
    ],
    "go_to_bottom": [
        "go to bottom", "jump to bottom", "bottom of file", "end of file"
    ],
    "copy_code": [
        "copy code", "copy to clipboard", "copy this"
    ],
    "paste_code": [
        "paste code", "paste from clipboard", "paste"
    ]
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
    language = safe(body.get("language"), "en")  # Default to English

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
    language = safe(body.get("language"), "en")  # Default to English

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


@app.route("/voice-command", methods=["POST"])
def voice():
    """
    Voice command dispatcher with explicit confidence levels.
    
    Response Schema:
    - success: bool (endpoint worked)
    - action: str (parsed action)
    - confidence: float 0–1 (how sure the system is)
      - 1.0: exact regex match (e.g., "go to line 5")
      - 0.75–0.99: pattern match (e.g., "read line 2")
      - 0.5–0.74: fuzzy match (may need confirmation)
      - < 0.5: unknown/ambiguous
    - heard: str (what was heard, for user feedback)
    - options: list (if ambiguous, ask frontend to confirm)
    """
    text = safe(safejson().get("text"), "")
    lower = text.lower().strip()

    # EXACT MATCHES (confidence 1.0): Regex with captured groups
    # describe line X
    m = re.search(r"describe line (\d+)", lower)
    if m:
        line = int(m.group(1))
        return jsonify({"success": True, "action": "describe_line", "line": line, "confidence": 1.0})

    # navigation: go to line X
    m = re.search(r"go to line (\d+)", lower)
    if m:
        line = int(m.group(1))
        return jsonify({"success": True, "action": "goto_line", "line": line, "confidence": 1.0})

    # navigation: read line X
    m = re.search(r"read line (\d+)", lower)
    if m:
        line = int(m.group(1))
        return jsonify({"success": True, "action": "read_line", "line": line, "confidence": 1.0})

    # STRONG PATTERN MATCHES (confidence 0.9): Exact substring match
    if "read current line" in lower or "current line" in lower:
        return jsonify({"success": True, "action": "read_current_line", "confidence": 0.9})

    if "next line" in lower:
        return jsonify({"success": True, "action": "next_line", "confidence": 0.9})

    if "previous line" in lower or "prev line" in lower:
        return jsonify({"success": True, "action": "prev_line", "confidence": 0.9})

    # basic editing: delete line X
    m = re.search(r"delete line (\d+)", lower)
    if m:
        line = int(m.group(1))
        return jsonify({"success": True, "action": "delete_line", "line": line, "confidence": 1.0})

    # basic editing: clear editor
    if "clear editor" in lower or "clear code" in lower or "clear file" in lower:
        return jsonify({"success": True, "action": "clear_editor", "confidence": 0.9})

    # snippets: save snippet named X
    m = re.search(r"save snippet named (.+)", lower)
    if m:
        name = m.group(1).strip()
        return jsonify({"success": True, "action": "save_snippet_named", "name": name, "confidence": 1.0})

    # load snippet N
    m = re.search(r"(load|open|get) snippet ([a-z0-9\-]+)", lower)
    if m:
        sid = m.group(2)
        return jsonify({"success": True, "action": "load_snippet", "id": sid, "confidence": 1.0})

    # delete snippet N
    m = re.search(r"delete snippet ([a-z0-9\-]+)", lower)
    if m:
        sid = m.group(1)
        return jsonify({"success": True, "action": "delete_snippet", "id": sid, "confidence": 1.0})

    # rename snippet ID to NAME
    m = re.search(r"rename snippet ([a-z0-9\-]+) to (.+)", lower)
    if m:
        sid = m.group(1)
        new_name = m.group(2).strip()
        return jsonify({
            "success": True,
            "action": "rename_snippet",
            "id": sid,
            "new_name": new_name,
            "confidence": 1.0
        })
    # Enhanced line reading
    m = re.search(r"(read line|enhanced read|line context) (\d+)", lower)
    if m:
        line = int(m.group(2))
        return jsonify({"success": True, "action": "read_line_enhanced", "line": line, "confidence": 1.0})
    
    # Sonify block
    if "sonify" in lower or "audio structure" in lower or "hear structure" in lower:
        return jsonify({"success": True, "action": "sonify_block", "confidence": 0.85})
    
    # ---- summarize file ----
    if "summarize" in lower or "summary of this file" in lower:
        return jsonify({"success": True, "action": "summarize", "confidence": 0.9})
    # Find variable usage
    m = re.search(r"find variable (.+)", lower)
    if m:
        var_name = m.group(1).strip()
        return jsonify({"success": True, "action": "find_variable", "variable": var_name, "confidence": 1.0})

    # List variables
    if "what variables" in lower or "list variables" in lower or "variables in scope" in lower:
        return jsonify({"success": True, "action": "list_variables", "confidence": 0.9})

    # Error checking
    if "check for errors" in lower or "check syntax" in lower or "find errors" in lower:
        return jsonify({"success": True, "action": "check_errors", "confidence": 0.95})

    if "where is the error" in lower or "locate error" in lower or "jump to error" in lower:
        return jsonify({"success": True, "action": "locate_error", "confidence": 0.9})

    if "stop beacon" in lower or "stop error beacon" in lower:
        return jsonify({"success": True, "action": "stop_beacon", "confidence": 0.9})

    # Navigation history
    if "go back" in lower or "navigate back" in lower:
        return jsonify({"success": True, "action": "go_back", "confidence": 0.95})

    if "go forward" in lower or "navigate forward" in lower:
        return jsonify({"success": True, "action": "go_forward", "confidence": 0.95})

    if "show history" in lower or "navigation history" in lower or "where have i been" in lower:
        return jsonify({"success": True, "action": "show_history", "confidence": 0.9})

    # Quick wins
    if "help" in lower or "what can you do" in lower or "list commands" in lower:
        return jsonify({"success": True, "action": "help", "confidence": 0.95})

    if "file stats" in lower or "how many lines" in lower:
        return jsonify({"success": True, "action": "file_stats", "confidence": 0.9})
    
    # explain last action
    if "what just happened" in lower or "what did you do" in lower or "explain last action" in lower:
        return jsonify({
            "success": True,
            "action": "explain_last_action",
            "confidence": 0.9
        })


    if "go to top" in lower or "top of file" in lower:
        return jsonify({"success": True, "action": "go_to_top", "confidence": 0.95})

    if "go to bottom" in lower or "bottom of file" in lower or "end of file" in lower:
        return jsonify({"success": True, "action": "go_to_bottom", "confidence": 0.95})

    if "copy code" in lower or "copy to clipboard" in lower:
        return jsonify({"success": True, "action": "copy_code", "confidence": 0.9})

    if "paste code" in lower or "paste from clipboard" in lower:
        return jsonify({"success": True, "action": "paste_code", "confidence": 0.9})

    # natural language code generation
    m = re.search(r"(generate|write|get|i want)\s+(?:python )?(?:code )?(?:for|to)\s+(.+)", lower)
    if m:
        prompt = m.group(2).strip()
        return jsonify({"success": True, "action": "generate_code", "prompt": prompt, "confidence": 0.8})

    # advise on code
    if "advise on this code" in lower or "advice on this code" in lower \
       or "how can i improve this code" in lower or "what features can i add" in lower:
        return jsonify({"success": True, "action": "advise", "confidence": 0.85})

    # explicit read output
    if "read output" in lower or "speak output" in lower or "say the output" in lower:
        return jsonify({"success": True, "action": "read_output", "confidence": 0.95})

    # ✅ repeat last action
    if "do that again" in lower or "repeat last action" in lower:
        return jsonify({"success": True, "action": "repeat_last_action", "confidence": 0.9})

    # ✅ repeat last spoken response
    if "repeat that" in lower or "say that again" in lower or "repeat last message" in lower:
        return jsonify({"success": True, "action": "repeat_last_speech", "confidence": 0.9})

    # explicit fix
    if "fix" in lower and "code" in lower:
        return jsonify({"success": True, "action": "fix", "confidence": 0.9})

    # fuzzy for core commands + confirmation
    best, bscore, second, sscore = best_two_commands(lower)
    if best is None or bscore < 40:
        return jsonify({
            "success": True,
            "action": "unknown",
            "heard": text,
            "confidence": 0.0
        })
    # if ambiguous, ask frontend to confirm
    if second and (bscore < 75 or (bscore - sscore) < 15):
        options = [best, second]
        return jsonify({
            "success": True,
            "action": "confirm",
            "options": options,
            "heard": text,
            "confidence": bscore / 100.0
        })

    # confident
    return jsonify({"success": True, "action": best, "confidence": bscore / 100.0})

# ==========================

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)


import ast
import atexit
import errno
import io
import json
import os
import queue as _queue_mod
import random
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, Response, g, has_request_context, jsonify, render_template, request, stream_with_context
from rapidfuzz import fuzz

from intent_parser import parse_intent
from sandboxed_fs import cleanup_sandbox, cleanup_stale_sandboxes, get_sandbox
from structure_parser import CodeAnalyzer

load_dotenv()

__version__ = "0.8.0"


def _debug_log(message: str):
    print(f"[CodeUp] {message}", file=sys.stderr)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _is_testing_mode():
    return _truthy_env("FLASK_TESTING")


def _is_dev_or_testing_mode():
    codeup_env = os.environ.get("CODEUP_ENV", "").lower()
    flask_env = os.environ.get("FLASK_ENV", "").lower()
    return (
        _is_testing_mode()
        or __name__ == "__main__"
        or codeup_env in {"dev", "development", "test", "testing"}
        or flask_env in {"dev", "development"}
        or _truthy_env("FLASK_DEBUG")
    )


def _configure_secret_key(flask_app: Flask):
    secret = os.environ.get("FLASK_SECRET_KEY")
    if secret:
        flask_app.secret_key = secret
        return
    if _is_dev_or_testing_mode():
        flask_app.secret_key = secrets.token_urlsafe(32)
        return
    raise RuntimeError(
        "FLASK_SECRET_KEY must be set outside testing/development modes."
    )

# ---------------------------------------------------------------------------
# Interactive run (Mechanism B) state
# ---------------------------------------------------------------------------
# Each live run gets a UUID. The state dict holds the subprocess handle,
# FIFO path, output buffer, completion flag, and a queue.Queue that the SSE
# generator reads chunks from. Cleanup happens on subprocess exit OR when
# the SSE client disconnects (whichever comes first).
_active_runs = {}  # run_id -> dict
_active_runs_lock = threading.Lock()

# Voice macros: per-session named code snippets the student can recall by name.
# Stored on disk alongside snippets but in a separate file so they don't clutter
# the snippet list. Keyed by sanitized session id.
_voice_macros_lock = threading.RLock()
_shared_macros_lock = threading.Lock()
_shared_macro_lookup_lock = threading.Lock()
_shared_macro_lookup_attempts = {}
_SHARED_MACRO_LOOKUP_LIMIT = 30
_SHARED_MACRO_LOOKUP_WINDOW = 60
_SHARED_MACRO_LOOKUP_MAX_KEYS = 1000

# Output bookmarks: per-session list of {label, position, timestamp, output_id}.
# In-memory only (cheap, ephemeral, scoped to session).
_output_bookmarks = {}  # session_id -> list[dict]
_output_bookmarks_lock = threading.Lock()

_voice_telemetry_lock = threading.Lock()
_voice_telemetry: List[Dict[str, Any]] = []
_VOICE_TELEMETRY_CAP = 1000

# Thread-local storage for per-request Gemini API key.
# (_trace_context was removed along with run_with_trace — session storage handles traces.)
_api_context = threading.local()

_session_ai_keys: Dict[str, Dict[str, Any]] = {}
_session_ai_keys_lock = threading.Lock()

# Session-based trace storage (prevents concurrent user interference)
# Keys: session_id (UUID), Values: {last_trace, current_trace_index, trace_timestamp, trace_duration_ms, last_voice_action}
_session_traces = {}  # dict[str, dict]
_session_traces_lock = threading.Lock()

# Tunable limits — kept together at module top so deployment can tweak via env
SESSION_TTL_SECONDS = int(os.environ.get("CODEUP_SESSION_TTL", "3600"))
_session_ttl = SESSION_TTL_SECONDS  # back-compat alias

# Background cleanup thread for old sessions
def _session_cleanup_worker():
    """Background thread that periodically cleans up expired sessions and
    bounded structures that would otherwise leak in long-running deployments."""
    while not _cleanup_stop_event.wait(300):  # Run cleanup every 5 minutes
        try:
            cleanup_old_sessions()
        except Exception as e:
            _debug_log(f"Session cleanup error: {e}")
        try:
            cleanup_stale_runs()
        except Exception as e:
            _debug_log(f"Stale runs cleanup error: {e}")
        try:
            cleanup_orphan_bookmarks_and_telemetry()
        except Exception as e:
            _debug_log(f"Bookmark/telemetry cleanup error: {e}")


def cleanup_stale_runs():
    """Reap _active_runs whose subprocess has exited or whose started_at
    is older than 5 minutes. Live runs are capped at 60s server-side, so
    anything older is definitely abandoned."""
    now = time.time()
    stale_ids = []
    with _active_runs_lock:
        for run_id, state in list(_active_runs.items()):
            proc = state.get("proc")
            started = state.get("started_at", now)
            if (proc is not None and proc.poll() is not None) or (now - started > 300):
                stale_ids.append(run_id)
    for run_id in stale_ids:
        try:
            _cleanup_run(run_id)
        except (OSError, RuntimeError) as e:
            _debug_log(f"Could not clean up stale run {run_id}: {e}")


def cleanup_orphan_bookmarks_and_telemetry():
    """Drop bookmarks for sessions no longer in _session_traces, and trim
    voice telemetry to its cap. Bounded by session count so this never
    grows past O(active_sessions)."""
    with _session_traces_lock:
        live_sessions = set(_session_traces.keys())
    with _output_bookmarks_lock:
        for sid in list(_output_bookmarks.keys()):
            if sid not in live_sessions:
                del _output_bookmarks[sid]
    # Telemetry already self-trims on insert, but guarantee the cap here too
    with _voice_telemetry_lock:
        if len(_voice_telemetry) > _VOICE_TELEMETRY_CAP:
            del _voice_telemetry[:len(_voice_telemetry) - _VOICE_TELEMETRY_CAP]
_cleanup_thread = None
_cleanup_thread_lock = threading.Lock()
_cleanup_stop_event = threading.Event()


def start_background_services():
    """Start process-local background maintenance once the app is running."""
    global _cleanup_thread
    with _cleanup_thread_lock:
        if _cleanup_thread and _cleanup_thread.is_alive():
            return _cleanup_thread
        _cleanup_stop_event.clear()
        _cleanup_thread = threading.Thread(
            target=_session_cleanup_worker,
            name="codeup-session-cleanup",
            daemon=True,
        )
        _cleanup_thread.start()
        return _cleanup_thread

# Bounded ThreadPoolExecutor for Gemini API calls (prevents resource exhaustion)
# Max 3 concurrent requests with queue size limit
_gemini_executor = None
_gemini_executor_lock = threading.Lock()


def _get_gemini_executor():
    global _gemini_executor
    with _gemini_executor_lock:
        if _gemini_executor is None:
            _gemini_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="gemini")
        return _gemini_executor


def shutdown_background_services():
    """Release lazy process-local resources during interpreter shutdown."""
    global _cleanup_thread, _gemini_executor
    with _cleanup_thread_lock:
        _cleanup_stop_event.set()
        cleanup_thread = _cleanup_thread
        if cleanup_thread and cleanup_thread.is_alive() and cleanup_thread is not threading.current_thread():
            cleanup_thread.join(timeout=2)
        if not cleanup_thread or not cleanup_thread.is_alive():
            _cleanup_thread = None

    with _gemini_executor_lock:
        executor = _gemini_executor
        _gemini_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


atexit.register(shutdown_background_services)

# FIX H-1: Track active+queued requests with a thread-safe counter instead of
# accessing private ThreadPoolExecutor internals (_threads, _work_queue).
_gemini_active_requests = 0
_gemini_active_lock = threading.Lock()
_gemini_queued_requests = 0  # separate counter for submitted-but-not-started tasks

# lock used to serialize tracer installation to avoid cross-thread interference
_tracer_lock = threading.Lock()

# Subprocess resource limits — POSIX only. Defined at module scope so each
# subprocess.Popen call doesn't pay the cost of redefining + importing.
SUBPROCESS_MEMORY_LIMIT_MB = int(os.environ.get("CODEUP_SUBPROCESS_MEMORY_MB", "128"))
SUBPROCESS_CPU_LIMIT_SECONDS = int(os.environ.get("CODEUP_SUBPROCESS_CPU_SECONDS", "3"))
SUBPROCESS_WALL_TIMEOUT_SECONDS = int(os.environ.get("CODEUP_SUBPROCESS_WALL_SECONDS", "3"))

if sys.platform != "win32":
    import resource as _resource

    def _set_subprocess_limits():
        try:
            memory_bytes = SUBPROCESS_MEMORY_LIMIT_MB * 1024 * 1024
            _resource.setrlimit(_resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            _resource.setrlimit(_resource.RLIMIT_CPU, (SUBPROCESS_CPU_LIMIT_SECONDS, SUBPROCESS_CPU_LIMIT_SECONDS))
            try:
                _resource.setrlimit(_resource.RLIMIT_NPROC, (64, 64))
            except (ValueError, OSError):
                pass
        except (ValueError, OSError) as e:
            _debug_log(f"Could not apply subprocess resource limits: {e}")
else:
    def _set_subprocess_limits():
        pass

app = Flask(__name__)
_configure_secret_key(app)

# Session configuration
SESSION_COOKIE_NAME = 'codeup_session'
SESSION_COOKIE_MAX_AGE = 3600 * 24 * 7  # 7 days
if "SESSION_COOKIE_SECURE" in os.environ:
    SESSION_COOKIE_SECURE = _truthy_env("SESSION_COOKIE_SECURE")
else:
    SESSION_COOKIE_SECURE = not _is_dev_or_testing_mode()
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = None if os.environ.get("FLASK_TESTING", "false").lower() == "true" else 'Lax'


@app.before_request
def ensure_background_services_started():
    if not _is_testing_mode():
        start_background_services()

# FIX C-1: Register ONE module-level after_request handler instead of
# registering a new permanent handler on every new-session request.
# Previously get_session_id() contained @app.after_request inside its body,
# causing an unbounded accumulation of handlers and duplicate Set-Cookie headers.
@app.after_request
def set_session_cookie(response):
    """Set session cookie if not already present.

    Uses the same session_id that get_session_id() generated during this
    request (stashed on flask.g) so storage and cookie always agree.
    """
    if not request.cookies.get(SESSION_COOKIE_NAME):
        session_id = getattr(g, 'session_id', None) or str(uuid.uuid4())
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            max_age=SESSION_COOKIE_MAX_AGE,
            secure=SESSION_COOKIE_SECURE,
            httponly=SESSION_COOKIE_HTTPONLY,
            samesite=SESSION_COOKIE_SAMESITE,
        )
    return response


def get_session_id():
    """Get or create a persistent session ID using cookies.

    Caches the generated ID on flask.g so set_session_cookie can reuse the
    same value. Otherwise storage gets keyed under one UUID and the cookie
    sent to the client is a different UUID.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        return session_id
    cached = getattr(g, 'session_id', None)
    if cached:
        return cached
    session_id = str(uuid.uuid4())
    g.session_id = session_id
    return session_id


def _make_session_storage():
    """Single source of truth for the shape of per-session trace storage."""
    now = time.time()
    return {
        'last_trace': [],
        'current_trace_index': -1,
        'trace_timestamp': now,
        'trace_duration_ms': 0,
        'last_voice_action': None,
        'created_at': now,
        'last_accessed': now,
    }


def get_trace_storage():
    """Get the trace storage dict for current session."""
    session_id = get_session_id()
    with _session_traces_lock:
        if session_id not in _session_traces:
            _session_traces[session_id] = _make_session_storage()
        else:
            _session_traces[session_id]['last_accessed'] = time.time()
        return _session_traces[session_id]


def cleanup_old_sessions():
    """Clean up sessions that have been idle longer than _session_ttl."""
    now = time.time()
    expired = []
    with _session_traces_lock:
        # FIX H-2 (continued): expire based on last_accessed, not created_at.
        expired = [sid for sid, data in _session_traces.items()
                   if now - data.get('last_accessed', 0) > _session_ttl]
        for sid in expired:
            del _session_traces[sid]
    for sid in expired:
        cleanup_sandbox(sid)
    cleanup_stale_sandboxes()
    with _session_ai_keys_lock:
        expired_keys = [
            sid for sid, data in _session_ai_keys.items()
            if now - data.get('last_accessed', now) > _session_ttl
        ]
        for sid in expired_keys:
            del _session_ai_keys[sid]


# ==========================
# REQUEST SIZE VALIDATION
# ==========================
# Hard limits to prevent resource exhaustion
MAX_REQUEST_SIZE = 1_000_000  # 1 MB max request body
MAX_CODE_SIZE = 100_000       # 100 KB max code
MAX_GEMINI_TIMEOUT = 30       # 30 second timeout for LLM calls
MAX_API_KEY_SIZE = 8_000
MAX_VOICE_TEXT_SIZE = 2_000
MAX_LEARNING_TOPIC_SIZE = 500
try:
    DEFAULT_AI_MAX_TOKENS = int(os.environ.get("GROQ_MAX_TOKENS", "2048"))
except (TypeError, ValueError):
    DEFAULT_AI_MAX_TOKENS = 2048
DEFAULT_AI_MAX_TOKENS = max(256, min(DEFAULT_AI_MAX_TOKENS, 8192))
MAX_MENTOR_MESSAGE_SIZE = 2_000
MAX_MENTOR_CONTEXT_SIZE = 4_000

# Per-session rate limiting for /run
# Allows at most RUN_RATE_LIMIT executions per RUN_RATE_WINDOW seconds per session.
RUN_RATE_LIMIT  = 10   # max runs
RUN_RATE_WINDOW = 60   # per this many seconds
_run_timestamps: dict = {}   # session_id -> list[float]
_run_rate_lock = threading.Lock()

def _check_run_rate_limit(session_id: str) -> bool:
    """Return True if the session is within the rate limit, False if throttled.

    Also opportunistically sweeps stale entries so _run_timestamps doesn't
    grow without bound on long-running deployments. We sweep on roughly 1 in
    every 50 calls to amortize the cost.
    """
    now = time.time()
    with _run_rate_lock:
        # Opportunistic cleanup: drop session entries that have no live timestamps
        if len(_run_timestamps) > 100 and random.random() < 0.02:
            stale = [sid for sid, ts in _run_timestamps.items()
                     if not any(now - t < RUN_RATE_WINDOW for t in ts)]
            for sid in stale:
                del _run_timestamps[sid]

        timestamps = _run_timestamps.get(session_id, [])
        # Drop entries outside the window
        timestamps = [t for t in timestamps if now - t < RUN_RATE_WINDOW]
        if len(timestamps) >= RUN_RATE_LIMIT:
            _run_timestamps[session_id] = timestamps
            return False
        timestamps.append(now)
        _run_timestamps[session_id] = timestamps
        return True
    
    
# Threshold for fuzzy voice command matching. Raised from 40 to 55 because
# 40 was permissive enough that ambient speech ("no thanks", "what was that")
# would fuzzy-match command keywords and trigger spurious confirm prompts.
# 55 still allows for typos and partial commands but rejects genuinely
# unrelated phrases. Below this threshold the input falls through to "unknown".
VOICE_FUZZY_THRESHOLD = 55

@app.before_request
def validate_request_size():
    """Reject oversized requests before processing."""
    if request.content_length and request.content_length > MAX_REQUEST_SIZE:
        return jsonify({"success": False, "error": "Request too large (max 1MB)"}), 413


# Parse allowlist once at import time
_ALLOWED_ORIGINS = {
    o.strip().lower()
    for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
}


@app.before_request
def enforce_same_origin():
    """Block cross-origin POST/PUT/DELETE requests.

    Policy:
      - Same-origin (Origin host == Host) is allowed.
      - Origin in ALLOWED_ORIGINS env var (comma-separated) is allowed.
      - In FLASK_TESTING=true mode, headerless requests (test client, curl) are allowed.
      - Outside testing mode, a request with neither Origin nor Referer is REJECTED.
    """
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return None

    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    host = request.headers.get("Host", "").lower()

    # No Origin and no Referer
    if not origin and not referer:
        if _is_testing_mode():
            return None
        return jsonify({"success": False, "error": "Missing Origin/Referer header"}), 403

    # Check Origin first (more reliable)
    if origin:
        origin_lower = origin.lower()
        origin_host = origin_lower.split("://", 1)[-1]
        if origin_host == host:
            return None
        if origin_lower in _ALLOWED_ORIGINS:
            return None
        return jsonify({"success": False, "error": "Cross-origin request blocked"}), 403

    # Fall back to Referer
    if referer:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(referer)
            referer_host = parsed.netloc.lower()
            referer_origin = f"{parsed.scheme}://{parsed.netloc}".lower()
            if referer_host == host:
                return None
            if referer_origin in _ALLOWED_ORIGINS:
                return None
        except ValueError as e:
            _debug_log(f"Could not parse Referer header: {e}")
        return jsonify({"success": False, "error": "Cross-origin request blocked"}), 403

    return None

SNIPPETS_FILE = os.environ.get("SNIPPETS_FILE", "snippets.json")
DATA_DIR = os.environ.get("DATA_DIR", ".")

def _snippets_path(session_id: str = None) -> str:
    """Return the absolute path to the snippets file for a given session.

    Each session gets its own snippets file so students sharing a lab
    machine don't see each other's saved code. The session_id is
    sanitized to a UUID-safe filename.
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError as e:
        _debug_log(f"Could not create data directory {DATA_DIR!r}: {e}")

    if session_id is None:
        session_id = get_session_id()

    # Sanitize: only allow hex chars and dashes (UUID format)
    safe_id = re.sub(r'[^a-fA-F0-9\-]', '', session_id)[:64]
    if not safe_id:
        safe_id = "default"

    filename = f"snippets_{safe_id}.json"
    return os.path.join(DATA_DIR, filename)

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

def _safe_text(value: Any, default: str = "", limit: Optional[int] = None) -> str:
    text = str(default if value is None else value)
    if limit is not None:
        return text[:limit]
    return text

def _looks_like_non_python_code(code: str) -> bool:
    """Reject whole-document HTML/CSS/JS accidentally pasted into the Python IDE."""
    text = str(code or "").lstrip("\ufeff \t\r\n")
    if not text:
        return False

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = lines[0].lower() if lines else ""
    head = "\n".join(lines[:30]).lower()

    html_starts = (
        "<!doctype html", "<html", "<head", "<body", "<script", "<style",
        "</html", "</head", "</body", "<div", "<section", "<main",
    )
    if first.startswith(html_starts):
        return True

    python_parse_failed = False
    try:
        ast.parse(str(code or ""))
    except SyntaxError:
        python_parse_failed = True

    tag_count = len(re.findall(r"</?[a-z][a-z0-9-]*(?:\s|>|/>)", head))
    if python_parse_failed and tag_count >= 4 and ("<html" in head or "<body" in head or "</" in head):
        return True

    css_block = re.search(r"(?m)^\s*(body|html|header|main|section|div|p|h[1-6]|[.#][\w-]+)\s*\{", head)
    if python_parse_failed and css_block and "}" in head and ":" in head:
        return True

    js_patterns = (
        r"(?m)^\s*(const|let|var)\s+\w+\s*=",
        r"(?m)^\s*function\s+\w+\s*\(",
        r"=>\s*\{",
        r"\bdocument\.(getElementById|querySelector|addEventListener)\b",
    )
    return python_parse_failed and any(re.search(pattern, head) for pattern in js_patterns)

def _python_only_error() -> str:
    return (
        "CodeUp is Python-only. Remove HTML, CSS, or JavaScript and enter valid "
        "Python code, for example: print('Hello CodeUp!')."
    )

def _reject_non_python_response(code: str):
    if _looks_like_non_python_code(code):
        return jsonify({"success": False, "error": _python_only_error()}), 400
    return None

def load_snippets() -> dict:
    """Load snippets from disk and return a dict with key `snippets`."""
    path = _snippets_path()
    if not os.path.exists(path):
        return {"snippets": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                normalized = []
                for item in data.get("snippets", []):
                    if not isinstance(item, dict):
                        continue
                    sid = _safe_text(item.get("id") or uuid.uuid4())
                    name = _safe_text(item.get("name") or "Untitled", limit=256).strip() or "Untitled"
                    code = _safe_text(item.get("code") or "")
                    normalized.append({"id": sid, "name": name, "code": code})
                return {"snippets": normalized}
    except (OSError, json.JSONDecodeError) as e:
        _debug_log(f"Could not load snippets from {path!r}: {e}")
    return {"snippets": []}

_snippets_lock = threading.RLock()

def save_snippets(d: dict) -> None:
    """Save snippets atomically using temp-then-move to prevent corruption.
    On POSIX, also fsyncs the directory so the rename is durable across power loss."""
    path = _snippets_path()
    dirpath = os.path.dirname(path) or "."

    with _snippets_lock:
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="snippets_", dir=dirpath)
            try:
                with os.fdopen(fd, 'w', encoding="utf-8") as f:
                    json.dump(d, f, indent=4)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
            except (OSError, TypeError, ValueError):
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            os.replace(temp_path, path)
            # Fsync the directory so the rename itself is durable
            if sys.platform != "win32":
                try:
                    dir_fd = os.open(dirpath, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
        except (OSError, TypeError, ValueError):
            try:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
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
GEMINI_MODEL = "llama-3.3-70b-versatile"

# helper to retrieve current API key (browser session overrides process env)
def _current_api_key():
    if has_request_context():
        session_id = get_session_id()
        with _session_ai_keys_lock:
            record = _session_ai_keys.get(session_id)
            if record:
                record['last_accessed'] = time.time()
                return record.get('key', '')
    return getattr(_api_context, 'gemini_key', GEMINI_API_KEY)

def _configured_cloud_api_key():
    """Return a usable cloud AI key from session config or environment."""
    for key in (
        _current_api_key(),
        os.environ.get("GROQ_API_KEY", ""),
        os.environ.get("GEMINI_API_KEY", ""),
    ):
        key = str(key or "").strip()
        if key and key != "Insert_API_Key_Here":
            return key
    return ""

def _env_flag_disabled(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"0", "false", "no", "off", "disabled"}

def _cloud_ai_disabled_for_request(key: str) -> bool:
    """Return True only when AI is explicitly disabled for this request.

    GEMINI_ENABLED is kept as a legacy offline/test switch. If a real Groq key
    is configured, do not let that stale flag kill every AI feature.
    Use CODEUP_AI_ENABLED=0 or GROQ_ENABLED=0 for a deliberate hard disable.
    """
    if _env_flag_disabled("CODEUP_AI_ENABLED") or _env_flag_disabled("AI_ENABLED"):
        return True
    if _env_flag_disabled("GROQ_ENABLED"):
        return True
    if _env_flag_disabled("GEMINI_ENABLED") and not key:
        return True
    return False

def _normalize_ai_max_tokens(max_tokens: Optional[int]) -> int:
    if max_tokens is None:
        return DEFAULT_AI_MAX_TOKENS
    try:
        value = int(max_tokens)
    except (TypeError, ValueError):
        return DEFAULT_AI_MAX_TOKENS
    return max(256, min(value, 8192))

def _is_ai_service_message(text: str) -> bool:
    lower = (text or "").strip().lower()
    return (
        lower.startswith("ai service")
        or lower.startswith("cloud ai")
        or lower.startswith("the cloud ai")
        or lower.startswith("no internet connection")
        or "offline ai is not available" in lower
        or "not configured" in lower
        or "authentication failed" in lower
    )

def _call_ollama(system_prompt, user_prompt, temperature=0.2, max_tokens=None):
    """Call local Ollama instance. Returns response string or None on failure."""
    if os.environ.get("OLLAMA_ENABLED", "0") != "1":
        return None
    try:
        import requests
        url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        model = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
        resp = requests.post(
            f"{url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "options": {"temperature": temperature, "num_predict": _normalize_ai_max_tokens(max_tokens)},
                "stream": False,
            },
            timeout=MAX_GEMINI_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"Ollama returned HTTP {resp.status_code}", file=sys.stderr)
            return None
        data = resp.json()
        content = (data.get("message") or {}).get("content", "").strip()
        return content if content else None
    except Exception as e:
        print(f"Ollama call failed: {e}", file=sys.stderr)
        return None


def call_gemini(system_prompt, user_prompt, temperature=0.2, language="en", max_tokens=None):
    """Call Groq API with hard timeout, falling back to local Ollama if Groq fails.

    Function name kept as call_gemini for backward compat with all callers.
    Order: Groq cloud → Ollama local → friendly error message.
    """
    max_tokens = _normalize_ai_max_tokens(max_tokens)
    key = _configured_cloud_api_key()

    def _try_ollama_fallback():
        """Try the local Ollama fallback. Returns response or None."""
        sp = system_prompt
        if language == "hi":
            sp = f"आप एक सहायक हैं जो हिंदी में सहायता प्रदान करते हैं। {system_prompt}"
        return _call_ollama(sp, user_prompt, temperature, max_tokens=max_tokens)

    if _cloud_ai_disabled_for_request(key):
        local = _try_ollama_fallback()
        if local:
            return local
        return "AI service disabled by server configuration."

    global _gemini_queued_requests

    if not key:
        local = _try_ollama_fallback()
        if local:
            return local
        return "AI service not configured. Add a Groq API key in settings, or install Ollama locally for offline AI."

    def _do_call():
        global _gemini_active_requests, _gemini_queued_requests
        with _gemini_active_lock:
            _gemini_queued_requests = max(0, _gemini_queued_requests - 1)
            _gemini_active_requests += 1
        try:
            sp = system_prompt
            if language == "hi":
                sp = f"आप एक सहायक हैं जो हिंदी में सहायता प्रदान करते हैं। {system_prompt}"

            from groq import Groq
            client = Groq(api_key=key)
            response = client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[
                    {"role": "system", "content": sp},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            for choice in getattr(response, "choices", []) or []:
                message = getattr(choice, "message", None)
                content = str(getattr(message, "content", "") or "").strip()
                if content:
                    return content
            return "AI service returned an empty response. Please try again."
        except Exception as e:
            err_str = str(e).lower()
            # Try Ollama before returning a user-facing error
            local = _try_ollama_fallback()
            if local:
                return f"[offline mode] {local}"
            if "rate" in err_str or "quota" in err_str or "429" in err_str:
                return "The cloud AI is busy and offline AI is not available. Please wait a moment and try again."
            if "auth" in err_str or "invalid" in err_str or "401" in err_str:
                return "Cloud AI authentication failed and offline AI is not available. Please ask your teacher to check the API key."
            if "timeout" in err_str or "timed out" in err_str:
                return "The cloud AI took too long to respond and offline AI is not available. Try a shorter request."
            if "connection" in err_str or "network" in err_str or "dns" in err_str:
                return "No internet connection and offline AI is not available. Ask your teacher to install Ollama for offline mode."
            return f"AI service had a problem: {str(e)[:100]}. Offline AI is also not available."
        finally:
            with _gemini_active_lock:
                _gemini_active_requests -= 1

    with _gemini_active_lock:
        current_active = _gemini_active_requests
        current_queued = _gemini_queued_requests

    if current_active + current_queued >= 8:
        local = _try_ollama_fallback()
        if local:
            return f"[offline mode] {local}"
        return "AI service is busy and offline AI is not available. Please try again later."

    with _gemini_active_lock:
        _gemini_queued_requests += 1

    def _cancel_future_if_pending(future):
        global _gemini_queued_requests
        try:
            cancelled = future.cancel()
        except RuntimeError:
            cancelled = False
        if cancelled:
            with _gemini_active_lock:
                _gemini_queued_requests = max(0, _gemini_queued_requests - 1)
        return cancelled

    def _log_timed_out_future(future):
        if getattr(future, "cancelled", lambda: False)():
            return
        try:
            future.result()
        except FutureTimeoutError:
            _debug_log("Timed-out AI task remained incomplete after caller returned.")
        except Exception as e:
            _debug_log(f"Timed-out AI task finished with an error: {e}")

    def _track_timed_out_future(future):
        try:
            future.add_done_callback(_log_timed_out_future)
        except AttributeError:
            _debug_log("Timed-out AI future could not be tracked for late completion.")

    try:
        future = _get_gemini_executor().submit(_do_call)
    except Exception as e:
        with _gemini_active_lock:
            _gemini_queued_requests = max(0, _gemini_queued_requests - 1)
        local = _try_ollama_fallback()
        if local:
            return f"[offline mode] {local}"
        return f"AI service is currently unavailable: {str(e)}"

    try:
        return future.result(timeout=MAX_GEMINI_TIMEOUT + 1)
    except FutureTimeoutError:
        if not _cancel_future_if_pending(future):
            _track_timed_out_future(future)
        local = _try_ollama_fallback()
        if local:
            return f"[offline mode] {local}"
        return "AI service took too long to respond and offline AI is not available. Try a shorter request."
    except Exception as e:
        _cancel_future_if_pending(future)
        local = _try_ollama_fallback()
        if local:
            return f"[offline mode] {local}"
        return f"AI service is currently unavailable: {str(e)}"    

def extract_code(text: str):
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    raw = m.group(1).strip()
    lines = []
    for line in raw.splitlines():
        low = line.strip().lower()
        if low.startswith("here is") or low.startswith("fixed code") or low.startswith("the corrected"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()

def _local_code_generation_fallback(prompt: str) -> str:
    """Small deterministic fallback for common beginner prompts when AI is blank."""
    lower = (prompt or "").lower()
    if "circle" in lower and "area" in lower:
        return (
            "# Sample radius. Change this value to test another circle.\n"
            "radius = 5\n\n"
            "# Pi is used in the area formula for a circle.\n"
            "pi = 3.14159\n\n"
            "# Area of a circle is pi times radius squared.\n"
            "area = pi * radius * radius\n\n"
            "# Show the answer.\n"
            "print(\"Area of the circle:\", area)\n"
        )
    if "rectangle" in lower and "area" in lower:
        return (
            "# Sample length and width. Change these values to test another rectangle.\n"
            "length = 10\n"
            "width = 5\n\n"
            "# Area of a rectangle is length times width.\n"
            "area = length * width\n\n"
            "# Show the answer.\n"
            "print(\"Area of the rectangle:\", area)\n"
        )
    if "triangle" in lower and "area" in lower:
        return (
            "# Sample base and height. Change these values to test another triangle.\n"
            "base = 8\n"
            "height = 6\n\n"
            "# Area of a triangle is one half times base times height.\n"
            "area = 0.5 * base * height\n\n"
            "# Show the answer.\n"
            "print(\"Area of the triangle:\", area)\n"
        )
    if "fibonacci" in lower:
        return DEMO_PRESETS["fibonacci"]["code"]
    if "prime" in lower:
        return DEMO_PRESETS["primes"]["code"]
    return ""

def _should_use_local_generation_fallback(raw: str) -> bool:
    lower = (raw or "").strip().lower()
    return not lower or "empty response" in lower

# ==========================
# MAIN PAGE
# ==========================

@app.route("/")
def landing():
    return render_template('landing.html')

@app.route("/ide")
def ide():
    return render_template('index.html')


@app.route("/healthz", methods=["GET"])
def healthz():
    """Simple liveness probe for deployment monitoring."""
    return jsonify({"status": "ok", "version": __version__}), 200

# ==========================
# DEMO PRESETS
# ==========================

DEMO_PRESETS = {
    "hello": {
        "title": "Hello World",
        "description": "Your very first Python program. Print text to the screen.",
        "code": 'print("Hello, CodeUp!")\nprint("Welcome to Python.")\n',
    },
    "fibonacci": {
        "title": "Fibonacci Sequence",
        "description": "Generate the first 10 numbers in the Fibonacci sequence using a loop.",
        "code": (
            "# First 10 Fibonacci numbers\n"
            "a, b = 0, 1\n"
            "for i in range(10):\n"
            "    print(a)\n"
            "    a, b = b, a + b\n"
        ),
    },
    "primes": {
        "title": "Prime Numbers",
        "description": "Find all prime numbers between 2 and 30.",
        "code": (
            "# Primes between 2 and 30\n"
            "for n in range(2, 31):\n"
            "    is_prime = True\n"
            "    for d in range(2, n):\n"
            "        if n % d == 0:\n"
            "            is_prime = False\n"
            "            break\n"
            "    if is_prime:\n"
            "        print(n)\n"
        ),
    },
    "calculator": {
        "title": "Simple Calculator",
        "description": "Perform basic arithmetic with hardcoded values.",
        "code": (
            "# Change these values to try different calculations\n"
            "a = 15\n"
            "b = 4\n"
            "\n"
            'print("Sum:", a + b)\n'
            'print("Difference:", a - b)\n'
            'print("Product:", a * b)\n'
            'print("Quotient:", a / b)\n'
            'print("Remainder:", a % b)\n'
        ),
    },
    "wordcount": {
        "title": "Word Counter",
        "description": "Count words in a sentence using string methods.",
        "code": (
            'sentence = "The quick brown fox jumps over the lazy dog"\n'
            "words = sentence.split()\n"
            'print("Sentence:", sentence)\n'
            'print("Word count:", len(words))\n'
            'print("First word:", words[0])\n'
            'print("Last word:", words[-1])\n'
        ),
    },
    "guess": {
        "title": "Number Guessing",
        "description": "A simple guessing game with hardcoded guess (no input() needed).",
        "code": (
            "import random\n"
            "\n"
            "secret = random.randint(1, 10)\n"
            "guess = 5  # change this to test different guesses\n"
            "\n"
            'print("The secret number was:", secret)\n'
            'print("Your guess was:", guess)\n'
            "\n"
            "if guess == secret:\n"
            '    print("Correct! You win!")\n'
            "elif guess < secret:\n"
            '    print("Too low. Try a higher number.")\n'
            "else:\n"
            '    print("Too high. Try a lower number.")\n'
        ),
    },
}


@app.route("/demo-presets", methods=["GET"])
def list_demo_presets():
    """Return list of available demo presets (id, title, description)."""
    return jsonify({
        "success": True,
        "presets": [
            {"id": k, "title": v["title"], "description": v["description"]}
            for k, v in DEMO_PRESETS.items()
        ]
    })


@app.route("/demo-presets/<preset_id>", methods=["GET"])
def get_demo_preset(preset_id):
    """Return a single preset's code by id."""
    preset = DEMO_PRESETS.get(preset_id)
    if not preset:
        return jsonify({"success": False, "error": "Preset not found"}), 404
    return jsonify({
        "success": True,
        "id": preset_id,
        "title": preset["title"],
        "description": preset["description"],
        "code": preset["code"],
    })

# ==========================
# API KEY CONFIGURATION
# ==========================

def set_gemini_api_key(key):
    """Configure the cloud AI key for the current browser session.

    Stores the key server-side by CodeUp session id so concurrent sessions can
    each use a different key without exposing the secret in a client cookie.
    Note: genai.configure() call removed here to avoid the global-mutation
    race described in C-2; per-call clients are used in call_gemini() instead.
    """
    cleaned = str(key or "").strip()
    _api_context.gemini_key = cleaned
    if has_request_context():
        session_id = get_session_id()
        with _session_ai_keys_lock:
            if cleaned and cleaned != "Insert_API_Key_Here":
                _session_ai_keys[session_id] = {"key": cleaned, "last_accessed": time.time()}
            else:
                _session_ai_keys.pop(session_id, None)

@app.route("/api-config", methods=["POST"])
def api_config():
    """Set the Groq API key for the session."""
    body = safejson()
    api_key = _safe_text(body.get("api_key"), limit=MAX_API_KEY_SIZE).strip()

    if not api_key or api_key == "Insert_API_Key_Here":
        return jsonify({"success": False, "error": "Invalid API key"}), 400
    if len(api_key) >= MAX_API_KEY_SIZE:
        return jsonify({"success": False, "error": "API key is too long"}), 413

    try:
        set_gemini_api_key(api_key)
        test_response = call_gemini("Say 'OK'", "Test", language="en")
        if _is_ai_service_message(test_response) or test_response == "AI service disabled":
            return jsonify({"success": False, "error": "API key is invalid or cloud AI is unavailable"}), 401
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

def explain_error(code: str, err_text: str, language="en", beginner=False) -> str:
    if beginner:
        if language == "hi":
            system = (
                "आप एक धैर्यवान शिक्षक हैं जो बिल्कुल नए पायथन सीखने वाले को समझा रहे हैं।\n"
                "मान लें कि उन्होंने पहले कभी programming नहीं की है।\n"
                "Technical jargon use न करें — 'NameError' या 'TypeError' न कहें।\n"
                "Real-life analogy दें। फिर बताएं कि कौन सी line में problem है और exact क्या type करना है।\n"
                "अधिकतम 5 बहुत simple वाक्य।"
            )
        else:
            system = (
                "You are a patient teacher explaining an error to someone brand new to "
                "Python — assume they have never coded before.\n"
                "Avoid jargon entirely. Do not say 'NameError' or 'TypeError'. Say what "
                "happened in plain English, like you are talking to a friend.\n"
                "Use a real-life analogy if it helps. Then tell them which line has the "
                "problem and exactly what to type to fix it.\n"
                "Maximum 5 very simple sentences. Be warm and encouraging.\n"
                "End with one question: Do you want a tiny hint, the exact fix, or a line-by-line explanation?"
            )
    elif language == "hi":
        system = (
            "आप एक पायथन ट्यूटर हैं जो एक दृष्टिबाधित-केंद्रित IDE में काम करते हैं।\n"
            "उपयोगकर्ता के कोड और त्रुटि को देखते हुए, समझाएं:\n"
            "- क्या त्रुटि हुई\n"
            "- किस पंक्ति पर (यदि दृश्यमान हो)\n"
            "- इसका सरल शब्दों में क्या अर्थ है\n"
            "- इसे कैसे ठीक करें\n"
            "अगर error में input() का mention है, समझाएं कि CodeUp में inputs को पहले "
            "declare करना होता है — magic comment '# inputs: मान1, मान2' से या inputs panel से।\n"
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
            "If the error mentions input() needing pre-flight values, explain that "
            "CodeUp uses a pre-flight input queue: declare values with a magic comment "
            "like '# inputs: Alice, 17' at the top, or fill the inputs panel, or "
            "switch to live input mode.\n"
            "Max 6 short lines. Be direct.\n"
            "End with one question: Do you want a tiny hint, the exact fix, or a line-by-line explanation?"
        )
    safe_error = sanitize_traceback(err_text)
    user = f"Code:\n```python\n{code}\n```\n\nError:\n```\n{safe_error}\n```"
    return call_gemini(system, user, language=language)


@app.route("/explain-error-beginner", methods=["POST"])
def explain_error_beginner():
    """Beginner-friendly error explanation. Same input as /run's error path
    but uses the gentle-tutor system prompt."""
    body = safejson()
    code = safe(body.get("code"), "")
    error_text = safe(body.get("error"), "")
    language = safe(body.get("language"), "en")
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked
    if not error_text.strip():
        return jsonify({"success": False, "error": "No error provided"}), 400
    explanation = explain_error(code, error_text, language=language, beginner=True)
    return jsonify({"success": True, "explanation": explanation})


# ==========================
# CONVERSATIONAL MENTOR
# ==========================

MENTOR_MODES = {
    "general", "tiny_hint", "bigger_hint", "exact_fix", "slow_walkthrough",
    "concept", "shorter", "repeat", "simpler",
}


def _ai_unavailable(text: str) -> bool:
    if _is_ai_service_message(text):
        return True
    lower = (text or "").lower()
    return (
        lower.startswith("ai service")
        or "not configured" in lower
        or "unavailable" in lower
        or "offline ai is not available" in lower
        or "ai service disabled" in lower
    )


def _mentor_clean_text(value: Any, limit: int = 4000) -> str:
    text = str(safe(value, ""))
    text = text.replace("\x00", "")
    return text[:limit]


def _mentor_history_text(history: Any, limit: int = 6) -> str:
    if not isinstance(history, list):
        return "No recent mentor history."
    lines = []
    for item in history[-limit:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "student")).strip().lower()
        label = "Mentor" if role == "mentor" else "Student"
        text = _mentor_clean_text(item.get("text", ""), 500).strip()
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines) if lines else "No recent mentor history."


def _mentor_preferences_text(preferences: Any) -> str:
    if not isinstance(preferences, dict):
        preferences = {}
    level = preferences.get("level")
    answer_style = preferences.get("answerStyle")
    language_style = preferences.get("languageStyle")
    if level not in {"beginner", "intermediate"}:
        level = "beginner"
    if answer_style not in {"hints_first", "direct"}:
        answer_style = "hints_first"
    if language_style not in {"simple", "hinglish", "hindi", "english"}:
        language_style = "simple"
    return (
        f"level={level}; answerStyle={answer_style}; "
        f"languageStyle={language_style}"
    )


def _line_range_for_node(node: ast.AST) -> Tuple[int, int]:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    return start, end


def build_code_audio_map(code: str) -> str:
    """Return a short audio-friendly code map without executing user code."""
    lines = code.splitlines()
    non_empty = [(idx + 1, line) for idx, line in enumerate(lines) if line.strip()]
    if not non_empty:
        return "Your code is empty."

    parts = [f"Your code has {len(lines)} lines, with {len(non_empty)} non-empty lines."]
    try:
        tree = ast.parse(code)
        nodes = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start, end = _line_range_for_node(node)
                nodes.append((start, f"Lines {start} to {end} define function {node.name}."))
            elif isinstance(node, ast.ClassDef):
                start, end = _line_range_for_node(node)
                nodes.append((start, f"Lines {start} to {end} define class {node.name}."))
            elif isinstance(node, (ast.For, ast.While)):
                start, end = _line_range_for_node(node)
                kind = "for loop" if isinstance(node, ast.For) else "while loop"
                nodes.append((start, f"Lines {start} to {end} are a {kind}."))
            elif isinstance(node, ast.If):
                start, end = _line_range_for_node(node)
                nodes.append((start, f"Lines {start} to {end} are an if decision."))
            elif isinstance(node, ast.Try):
                start, end = _line_range_for_node(node)
                nodes.append((start, f"Lines {start} to {end} are a try and except block."))
            elif isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Call):
                call = node.value
                if isinstance(call.func, ast.Name) and call.func.id == "print":
                    start, _ = _line_range_for_node(node)
                    nodes.append((start, f"Line {start} prints output."))
        for _, summary in sorted(nodes, key=lambda item: item[0])[:12]:
            parts.append(summary)
    except SyntaxError:
        parts.append("I could not parse the full structure because the code has a syntax error, so here is a simple indentation map.")

    for line_no, line in non_empty[:20]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if indent > 0:
            parts.append(f"Line {line_no} is indented by {indent} spaces, so it is inside a block.")
        if stripped.startswith(("def ", "async def ")):
            parts.append(f"Line {line_no} starts a function.")
        elif stripped.startswith(("for ", "while ")):
            parts.append(f"Line {line_no} starts a loop.")
        elif stripped.startswith(("if ", "elif ", "else:")):
            parts.append(f"Line {line_no} is a decision line.")
        elif stripped.startswith(("try:", "except ", "finally:")):
            parts.append(f"Line {line_no} is exception-handling structure.")
        elif stripped.startswith("print"):
            parts.append(f"Line {line_no} prints something.")

    return " ".join(dict.fromkeys(parts))


@app.route("/mentor/code-map", methods=["POST"])
def mentor_code_map():
    body = safejson()
    code = safe(body.get("code"), "")
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked
    reply = build_code_audio_map(code)
    return jsonify({"success": True, "reply": reply, "speech": reply, "auto_speak": True})


@app.route("/mentor/chat", methods=["POST"])
def mentor_chat():
    body = safejson()
    raw_code = str(safe(body.get("code", ""), ""))
    raw_message = str(safe(body.get("message", ""), ""))
    raw_output = str(safe(body.get("output", ""), ""))
    raw_error = str(safe(body.get("error", ""), ""))
    if len(raw_code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    blocked = _reject_non_python_response(raw_code)
    if blocked:
        return blocked
    if len(raw_message) > MAX_MENTOR_MESSAGE_SIZE:
        return jsonify({"success": False, "error": f"Message too large (max {MAX_MENTOR_MESSAGE_SIZE} bytes)"}), 413
    if len(raw_output) > MAX_MENTOR_CONTEXT_SIZE or len(raw_error) > MAX_MENTOR_CONTEXT_SIZE:
        return jsonify({"success": False, "error": f"Mentor context too large (max {MAX_MENTOR_CONTEXT_SIZE} bytes per field)"}), 413
    code = _mentor_clean_text(raw_code, MAX_CODE_SIZE)
    message = _mentor_clean_text(raw_message, MAX_MENTOR_MESSAGE_SIZE).strip()
    output = _mentor_clean_text(raw_output, MAX_MENTOR_CONTEXT_SIZE)
    error = sanitize_traceback(_mentor_clean_text(raw_error, MAX_MENTOR_CONTEXT_SIZE))
    language = _mentor_clean_text(body.get("language", "en"), 20) or "en"
    mode = _mentor_clean_text(body.get("mode", "general"), 40)
    mode = mode if mode in MENTOR_MODES else "general"

    if not message and mode not in {"repeat", "shorter", "simpler", "slow_walkthrough"}:
        return jsonify({"success": False, "error": "Message is required", "reply": "Please ask the mentor a question.", "speech": "Please ask the mentor a question.", "auto_speak": True}), 400

    system = (
        "You are CodeUp Mentor, a conversational Python tutor inside a blind-first IDE.\n"
        "Keep replies warm, short, and screen-reader friendly.\n"
        "Do not dump large code unless mode is exact_fix or the student explicitly asks.\n"
        "Use line numbers when useful. Explain indentation, nesting, blocks, tracebacks, and visual code shape in audio-friendly language.\n"
        "Respect a hints-first style unless preferences ask for direct answers.\n"
        "End with exactly one useful next step or one follow-up choice, not a menu.\n"
        "No markdown tables. Prefer 2 to 5 short sentences."
    )
    if mode == "tiny_hint":
        system += "\nMode: give only one tiny hint. Do not reveal the full answer."
    elif mode == "bigger_hint":
        system += "\nMode: give a bigger hint, but still avoid full corrected code."
    elif mode == "exact_fix":
        system += "\nMode: give the exact fix. Keep code minimal and only include changed lines if possible."
    elif mode == "slow_walkthrough":
        system += "\nMode: walk through slowly, line by line, with short spoken sentences."
    elif mode == "concept":
        system += "\nMode: explain the concept in the current code."
    elif mode == "shorter":
        system += "\nMode: rewrite the previous mentor answer shorter."
    elif mode == "simpler":
        system += "\nMode: rewrite the previous mentor answer in simpler words."
    elif mode == "repeat":
        system += "\nMode: repeat the previous mentor answer clearly."

    user = (
        f"Student message: {message or '(follow-up transform requested)'}\n"
        f"Mode: {mode}\n"
        f"Language: {language}\n"
        f"Preferences: {_mentor_preferences_text(body.get('preferences'))}\n\n"
        f"Recent mentor history:\n{_mentor_history_text(body.get('history'))}\n\n"
        f"Current code:\n```python\n{code[:MAX_CODE_SIZE]}\n```\n\n"
        f"Latest output:\n{output or '(none)'}\n\n"
        f"Latest error:\n{error or '(none)'}"
    )
    reply = call_gemini(system, user, temperature=0.25, language=language)
    if _ai_unavailable(reply):
        fallback = (
            "The AI mentor is unavailable right now. You can still run the code, ask for a code map, or use explain simply after an error."
        )
        return jsonify({"success": False, "error": reply, "reply": fallback, "speech": fallback, "auto_speak": True})
    return jsonify({"success": True, "reply": reply, "speech": reply, "auto_speak": True})


@app.route("/mentor/chat-stream", methods=["POST"])
def mentor_chat_stream():
    """Streaming version of /mentor/chat. Sends SSE events with incremental chunks."""
    body = safejson()
    raw_code = str(safe(body.get("code", ""), ""))
    raw_message = str(safe(body.get("message", ""), ""))
    raw_output = str(safe(body.get("output", ""), ""))
    raw_error = str(safe(body.get("error", ""), ""))
    if len(raw_code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    blocked = _reject_non_python_response(raw_code)
    if blocked:
        return blocked
    if len(raw_message) > MAX_MENTOR_MESSAGE_SIZE:
        return jsonify({"success": False, "error": f"Message too large (max {MAX_MENTOR_MESSAGE_SIZE} bytes)"}), 413
    if len(raw_output) > MAX_MENTOR_CONTEXT_SIZE or len(raw_error) > MAX_MENTOR_CONTEXT_SIZE:
        return jsonify({"success": False, "error": f"Mentor context too large (max {MAX_MENTOR_CONTEXT_SIZE} bytes per field)"}), 413
    code = _mentor_clean_text(raw_code, MAX_CODE_SIZE)
    message = _mentor_clean_text(raw_message, MAX_MENTOR_MESSAGE_SIZE).strip()
    output = _mentor_clean_text(raw_output, MAX_MENTOR_CONTEXT_SIZE)
    error = sanitize_traceback(_mentor_clean_text(raw_error, MAX_MENTOR_CONTEXT_SIZE))
    language = _mentor_clean_text(body.get("language", "en"), 20) or "en"
    mode = _mentor_clean_text(body.get("mode", "general"), 40)
    mode = mode if mode in MENTOR_MODES else "general"

    if not message and mode not in {"repeat", "shorter", "simpler", "slow_walkthrough"}:
        return jsonify({"success": False, "error": "Message is required"}), 400

    system = (
        "You are CodeUp Mentor, a conversational Python tutor inside a blind-first IDE.\n"
        "Keep replies warm, short, and screen-reader friendly.\n"
        "Do not dump large code unless mode is exact_fix or the student explicitly asks.\n"
        "Use line numbers when useful. Explain indentation, nesting, blocks, tracebacks, and visual code shape in audio-friendly language.\n"
        "Respect a hints-first style unless preferences ask for direct answers.\n"
        "End with exactly one useful next step or one follow-up choice, not a menu.\n"
        "No markdown tables. Prefer 2 to 5 short sentences."
    )
    if mode == "tiny_hint":
        system += "\nMode: give only one tiny hint. Do not reveal the full answer."
    elif mode == "bigger_hint":
        system += "\nMode: give a bigger hint, but still avoid full corrected code."
    elif mode == "exact_fix":
        system += "\nMode: give the exact fix. Keep code minimal and only include changed lines if possible."
    elif mode == "slow_walkthrough":
        system += "\nMode: walk through slowly, line by line, with short spoken sentences."

    user = (
        f"Student message: {message or '(follow-up transform requested)'}\n"
        f"Mode: {mode}\n"
        f"Language: {language}\n"
        f"Preferences: {_mentor_preferences_text(body.get('preferences'))}\n\n"
        f"Recent mentor history:\n{_mentor_history_text(body.get('history'))}\n\n"
        f"Current code:\n```python\n{code[:MAX_CODE_SIZE]}\n```\n\n"
        f"Latest output:\n{output or '(none)'}\n\n"
        f"Latest error:\n{error or '(none)'}"
    )

    key = _configured_cloud_api_key()

    def generate():
        try:
            if _cloud_ai_disabled_for_request(key) or not key:
                # Fallback: non-streaming response as single chunk
                reply = call_gemini(system, user, temperature=0.25, language=language)
                yield f"data: {json.dumps({'chunk': reply})}\n\n"
                yield "data: [DONE]\n\n"
                return

            sp = system
            if language == "hi":
                sp = f"आप एक सहायक हैं जो हिंदी में सहायता प्रदान करते हैं। {system}"

            from groq import Groq
            client = Groq(api_key=key)
            stream = client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[
                    {"role": "system", "content": sp},
                    {"role": "user", "content": user},
                ],
                temperature=0.25,
                max_tokens=_normalize_ai_max_tokens(None),
                stream=True,
            )

            for chunk in stream:
                for choice in getattr(chunk, "choices", []) or []:
                    delta = getattr(choice, "delta", None)
                    content = getattr(delta, "content", None)
                    if content:
                        yield f"data: {json.dumps({'chunk': content})}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as e:
            # On error, send full non-streaming fallback
            reply = call_gemini(system, user, temperature=0.25, language=language)
            yield f"data: {json.dumps({'chunk': reply})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/mentor/check-progress", methods=["POST"])
def mentor_check_progress():
    body = safejson()
    raw_previous_code = str(safe(body.get("previousCode", ""), ""))
    raw_current_code = str(safe(body.get("currentCode", ""), ""))
    raw_previous_error = str(safe(body.get("previousError", ""), ""))
    raw_current_output = str(safe(body.get("currentOutput", ""), ""))
    raw_current_error = str(safe(body.get("currentError", ""), ""))
    if len(raw_previous_code) > MAX_CODE_SIZE or len(raw_current_code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    blocked = _reject_non_python_response(raw_previous_code) or _reject_non_python_response(raw_current_code)
    if blocked:
        return blocked
    if any(len(value) > MAX_MENTOR_CONTEXT_SIZE for value in (raw_previous_error, raw_current_output, raw_current_error)):
        return jsonify({"success": False, "error": f"Mentor context too large (max {MAX_MENTOR_CONTEXT_SIZE} bytes per field)"}), 413
    previous_code = _mentor_clean_text(raw_previous_code, MAX_CODE_SIZE)
    current_code = _mentor_clean_text(raw_current_code, MAX_CODE_SIZE)
    previous_error = sanitize_traceback(_mentor_clean_text(raw_previous_error, MAX_MENTOR_CONTEXT_SIZE))
    current_output = _mentor_clean_text(raw_current_output, MAX_MENTOR_CONTEXT_SIZE)
    current_error = sanitize_traceback(_mentor_clean_text(raw_current_error, MAX_MENTOR_CONTEXT_SIZE))
    language = _mentor_clean_text(body.get("language", "en"), 20) or "en"

    system = (
        "You are CodeUp Mentor checking a blind beginner's progress.\n"
        "Reply in 3 short parts: what improved, whether the original issue seems fixed, and one next step.\n"
        "Do not overclaim. If current code was not run or still has an error, say that clearly.\n"
        "Keep it short and spoken-friendly."
    )
    user = (
        f"Preferences: {_mentor_preferences_text(body.get('preferences'))}\n"
        f"Recent mentor history:\n{_mentor_history_text(body.get('history'))}\n\n"
        f"Previous code:\n```python\n{previous_code}\n```\n\n"
        f"Current code:\n```python\n{current_code}\n```\n\n"
        f"Previous error:\n{previous_error or '(none)'}\n"
        f"Current output:\n{current_output or '(none)'}\n"
        f"Current error:\n{current_error or '(none)'}"
    )
    reply = call_gemini(system, user, temperature=0.2, language=language)
    if _ai_unavailable(reply):
        fallback = "I cannot check with AI right now. If the latest run has no error and shows the expected output, your first issue may be fixed. Run once more and ask for a code map."
        return jsonify({"success": False, "error": reply, "reply": fallback, "speech": fallback, "auto_speak": True})
    return jsonify({"success": True, "reply": reply, "speech": reply, "auto_speak": True})

# ==========================
# RUN CODE (WITH AI ERROR EXPLANATION)
# ==========================

# Note: in-process execution (run_with_trace, SAFE_GLOBALS, SafeModule, etc.)
# was removed. All user code runs in the subprocess sandbox below.

def classify_semantic_errors(trace):
    """
    ⚠️ HEURISTIC DETECTION (Assistance Signal, Not Guaranteed)

    These are heuristic patterns, not rigorous analysis. The real safety
    limits are the subprocess timeout, the MAX_TRACE_EVENTS cap, and the
    POSIX RLIMIT_AS / RLIMIT_CPU caps applied via preexec_fn.
    """
    issues = []
    execution_count = {}
    truncated = False

    for event in trace:
        if event.get("type") == "overflow":
            truncated = True
            continue
        if event.get("type") in ("state_change", "line_exec"):
            line = event["line"]
            execution_count[line] = execution_count.get(line, 0) + 1

    # 2000 is the sweet spot:
    #   - Catches genuinely runaway code: tight loops with no termination,
    #     recursion that's clearly wrong, accidental infinite-ish iteration.
    #   - Stays above legitimate classroom exercises: a range(100) loop,
    #     a nested 30x30, a prime sieve to 100 — all comfortably below.
    #   - Sits below the 5000 trace cap so the warning can actually fire
    #     before truncation makes the count meaningless.
    # Previous 4500 was dead code (above truncation); 800 fired on legit
    # nested-loop demos. 2000 was tuned against the bundled demo presets.
    HIGH_ITERATION_THRESHOLD = 2000
    for line, count in execution_count.items():
        if count > HIGH_ITERATION_THRESHOLD:
            note = (f"Line {line} executed {count} times. The program completed "
                    f"successfully, but if this looks higher than you expected, "
                    f"check whether your loop terminates correctly.")
            if truncated:
                note += " (Trace was truncated — actual count may be higher.)"
            issues.append({
                "category": "High iteration count",
                "line": line,
                "message": note,
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
            _session_traces[session_id] = _make_session_storage()
        storage = _session_traces[session_id]
        storage['last_trace'] = trace or []
        storage['current_trace_index'] = -1
        storage['trace_timestamp'] = time.time()
        storage['trace_duration_ms'] = duration_ms
        storage['last_accessed'] = time.time()
    cleanup_old_sessions()



# Magic comment parser for `# inputs: foo, bar, baz` declared at top of code
_INPUT_MAGIC_RE = re.compile(r'^\s*#\s*inputs?\s*:\s*(.+)$', re.IGNORECASE | re.MULTILINE)

def _parse_magic_inputs(code: str):
    """Look for `# inputs: a, b, c` near the top of the file (first 5 lines).

    Returns a list of input strings, or [] if no magic comment found.
    Comma-separated; whitespace stripped per item. If several magic input
    comments are present near the top, the last one wins so a student can
    revise the declaration by adding a newer line.
    """
    head = '\n'.join(code.splitlines()[:5])
    matches = _INPUT_MAGIC_RE.findall(head)
    if not matches:
        return []
    raw = matches[-1].strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(',') if item.strip()]


def _detect_input_prompts(code: str) -> List[str]:
    """Return labels for input() calls whose prompt is a string literal."""
    prompts: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return prompts
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "input":
            continue
        label = ""
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            label = node.args[0].value.strip()
        prompts.append(label or f"Input {len(prompts) + 1}")
    return prompts[:50]


# Track last output per session so we can narrate diffs on next run
_last_outputs = {}  # session_id -> {"output": str, "timestamp": float}
_last_outputs_lock = threading.Lock()


def _compute_output_diff(prev: str, curr: str) -> dict:
    """Return a structured diff between two outputs suitable for narration.

    Returns dict with: identical (bool), summary (str), changed_lines (list of
    {line_no, before, after, kind}). Kind is 'added', 'removed', or 'changed'.
    """
    if prev == curr:
        return {"identical": True, "summary": "Output is identical to the previous run.", "changed_lines": []}
    if not prev:
        return {"identical": False, "summary": "First run — no previous output to compare.", "changed_lines": []}

    import difflib
    prev_lines = prev.splitlines()
    curr_lines = curr.splitlines()
    if len(curr_lines) > len(prev_lines) and curr_lines[:len(prev_lines)] == prev_lines:
        added_count = len(curr_lines) - len(prev_lines)
        start_line = len(prev_lines) + 1
        changes = [
            {
                "line_no": start_line + idx,
                "before": "",
                "after": line,
                "kind": "added",
            }
            for idx, line in enumerate(curr_lines[len(prev_lines):len(prev_lines) + 5])
        ]
        return {
            "identical": False,
            "summary": f"You got {added_count} new line{'s' if added_count != 1 else ''} at the end.",
            "changed_lines": changes,
            "total_changes": added_count,
            "mode": "appended",
            "prev_line_count": len(prev_lines),
            "curr_line_count": len(curr_lines),
            "change_start_line": start_line,
        }
    matcher = difflib.SequenceMatcher(None, prev_lines, curr_lines)
    changes = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        if tag == 'replace':
            # Split unequal-length replace into a paired "changed" region
            # plus a trailing add or delete. Pairing past min() drifts the
            # line numbers because we're indexing into curr while the extra
            # rows belong to prev (or vice versa).
            paired = min(i2 - i1, j2 - j1)
            for k in range(paired):
                changes.append({
                    "line_no": j1 + k + 1,
                    "before": prev_lines[i1 + k],
                    "after": curr_lines[j1 + k],
                    "kind": "changed",
                })
            # Extra rows in prev → removed
            for k in range(paired, i2 - i1):
                changes.append({
                    "line_no": j1 + paired + 1,
                    "before": prev_lines[i1 + k],
                    "after": "",
                    "kind": "removed",
                })
            # Extra rows in curr → added
            for k in range(paired, j2 - j1):
                changes.append({
                    "line_no": j1 + k + 1,
                    "before": "",
                    "after": curr_lines[j1 + k],
                    "kind": "added",
                })
        elif tag == 'delete':
            for k in range(i1, i2):
                changes.append({
                    "line_no": k + 1,
                    "before": prev_lines[k],
                    "after": "",
                    "kind": "removed",
                })
        elif tag == 'insert':
            for k in range(j1, j2):
                changes.append({
                    "line_no": k + 1,
                    "before": "",
                    "after": curr_lines[k],
                    "kind": "added",
                })

    if len(changes) > 20:
        first_change = changes[0]["line_no"] if changes else 1
        return {
            "identical": False,
            "summary": (
                "Output is mostly different. "
                f"Last run produced {len(prev_lines)} lines, this one produced {len(curr_lines)}. "
                f"The change starts at line {first_change}."
            ),
            "changed_lines": changes[:5],
            "total_changes": len(changes),
            "mode": "summary",
            "prev_line_count": len(prev_lines),
            "curr_line_count": len(curr_lines),
            "change_start_line": first_change,
        }

    # Cap at first 5 changes to avoid speech overload
    capped = changes[:5]
    if len(changes) <= 1:
        summary = f"{len(changes)} line changed since last run."
    else:
        more = f" (and {len(changes) - 5} more)" if len(changes) > 5 else ""
        summary = f"{len(changes)} lines changed since last run{more}."
    return {
        "identical": False,
        "summary": summary,
        "changed_lines": capped,
        "total_changes": len(changes),
    }


@app.route("/run", methods=["POST"])
def run_code():
    body = safejson()
    code = safe(body.get("code"), "")
    # Mechanism A: pre-flight inputs. List of strings. Body wins; magic
    # comment is the fallback so students can ship reproducible examples.
    inputs_from_body = body.get("inputs")
    if not isinstance(inputs_from_body, list):
        inputs_from_body = None
    inputs = inputs_from_body if inputs_from_body is not None else _parse_magic_inputs(code)
    # Sanitize: stringify, cap length per item and total count
    inputs = [str(x)[:1000] for x in inputs[:50]]

    if not _check_run_rate_limit(get_session_id()):
        return jsonify({
            "success": False,
            "error": f"Rate limit exceeded. Max {RUN_RATE_LIMIT} runs per {RUN_RATE_WINDOW} seconds."
        }), 429

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    # Heuristic: detect input() use without provided inputs and surface a
    # friendly hint up front. The subprocess will still raise the canonical
    # error if it actually hits input() with an empty queue, but this helps
    # catch the common case before the user waits for execution.
    input_prompts = _detect_input_prompts(code)
    uses_input = bool(input_prompts) or bool(re.search(r'\binput\s*\(', code))
    inputs_hint = None
    if uses_input and not inputs:
        inputs_hint = (
            "Your code uses input(), but you did not provide any pre-flight "
            "inputs. The first input() call will fail with a clear error "
            "telling you what to do. To fix: open the inputs panel and add "
            "values, or add a magic comment like '# inputs: Alice, 17' at "
            "the top of your code, or switch to live input mode."
        )

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    try:
        execution_start = time.time()
        sandbox = get_sandbox(get_session_id())
        workspace_dir = sandbox.workspace_dir
        trace_file = os.path.join(workspace_dir, f"trace_{uuid.uuid4().hex}.json")

        _runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox_runner.py')

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                          encoding='utf-8', dir=workspace_dir) as code_file:
            code_file.write(code)
            code_file_path = code_file.name

        # Write inputs queue to its own file. Empty file if no inputs — the
        # subprocess handles that gracefully.
        inputs_file_path = None
        if inputs:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                              encoding='utf-8', dir=workspace_dir) as if_handle:
                for item in inputs:
                    # Strip newlines from each input — they're line-delimited
                    if_handle.write(item.replace('\n', ' ').replace('\r', ' ') + '\n')
                inputs_file_path = if_handle.name

        time_limit = SUBPROCESS_WALL_TIMEOUT_SECONDS
        try:
            env = os.environ.copy()
            env['CODEUP_CODE_FILE'] = code_file_path
            env['CODEUP_TRACE_FILE'] = trace_file
            if inputs_file_path:
                env['CODEUP_INPUTS_FILE'] = inputs_file_path
            # Make sure interactive mode is OFF for the standard /run path
            env.pop('CODEUP_INTERACTIVE', None)
            env.pop('CODEUP_INPUT_FIFO', None)
            env.pop('CODEUP_EXEC_CODE', None)

            preexec = None
            if sys.platform != "win32":
                preexec = _set_subprocess_limits

            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=workspace_dir,
                text=True,
            )
            if sys.platform != "win32":
                popen_kwargs["preexec_fn"] = preexec
                popen_kwargs["start_new_session"] = True
            proc_handle = subprocess.Popen([sys.executable, _runner_path], **popen_kwargs)
            try:
                proc_stdout, proc_stderr = proc_handle.communicate(timeout=max(1, int(time_limit)))
                stdout_buf.write(proc_stdout or "")
                stderr_buf.write(proc_stderr or "")
            except subprocess.TimeoutExpired:
                try:
                    if sys.platform != "win32":
                        import signal as _signal
                        os.killpg(os.getpgid(proc_handle.pid), _signal.SIGKILL)
                    else:
                        proc_handle.kill()
                except (OSError, ProcessLookupError) as e:
                    _debug_log(f"Could not terminate timed-out run: {e}")
                try:
                    proc_handle.communicate(timeout=2)
                except subprocess.TimeoutExpired as e:
                    _debug_log(f"Timed-out run did not exit after kill: {e}")
                stderr_buf.write(f"Execution timed out after {time_limit}s (subprocess)")
        finally:
            for _tmp in (code_file_path, inputs_file_path):
                if _tmp:
                    try:
                        os.unlink(_tmp)
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
        except OSError as e:
            trace_error = f'Error reading trace: {str(e)}'
        finally:
            try:
                if os.path.exists(trace_file):
                    os.unlink(trace_file)
            except OSError:
                pass

        if not trace:
            msg = trace_error or 'No detailed trace available from subprocess (likely timed out or crashed)'
            trace = [{'type': 'subprocess_exec', 'note': msg}]

        semantic_issues = classify_semantic_errors(trace) if trace else []
        duration_ms = int((time.time() - execution_start) * 1000)
        save_execution_trace(trace, duration_ms)

        output = stdout_buf.getvalue()
        error = stderr_buf.getvalue()

        # Compute output diff vs last run (only on success — error states aren't
        # comparable in a useful way)
        diff_info = None
        if not error.strip():
            session_id = get_session_id()
            with _last_outputs_lock:
                prev_record = _last_outputs.get(session_id)
                prev_output = prev_record["output"] if prev_record else ""
                _last_outputs[session_id] = {"output": output, "timestamp": time.time()}
            diff_info = _compute_output_diff(prev_output, output)

        if error.strip():
            explanation = explain_error(code, error, language=safe(body.get("language"), "en"))
            return jsonify({
                "success": False,
                "error": error,
                "explanation": explanation,
                "inputs_hint": inputs_hint,
                "input_prompts": input_prompts,
            })

        return jsonify({
            "success": True,
            "output": output or "Program finished with no output.",
            "trace": trace,
            "semantic_issues": semantic_issues,
            "diff": diff_info,
            "inputs_consumed": len(inputs),
            "inputs_provided": len(inputs),
            "inputs_hint": inputs_hint,
            "input_prompts": input_prompts,
        })

    except Exception:
        tb = traceback.format_exc()
        explanation = explain_error(code, tb, language=safe(body.get("language"), "en"))
        return jsonify({
            "success": False,
            "error": tb,
            "explanation": explanation,
            "inputs_hint": inputs_hint,
            "input_prompts": input_prompts,
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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    if language == "hi":
        system = (
            "आप blind student के लिए conversational Python tutor हैं।\n"
            "Code का BRIEF analysis दें — 4 से 5 छोटी lines में:\n"
            "1. यह code overall क्या करता है\n"
            "2. कौन सी 2-3 main techniques use हुई हैं\n"
            "3. कोई obvious bug या edge case (अगर हो)\n\n"
            "End में पूछें: 'क्या आप line by line deeper explanation सुनना चाहते हैं? बोलें: analyze deeper'\n"
            "Markdown headers न use करें। Spoken Hindi/English में लिखें।"
        )
    else:
        system = (
            "You are a conversational Python tutor for a blind student.\n"
            "Give a BRIEF analysis in 4 to 5 short lines:\n"
            "1. What this code does overall (one line)\n"
            "2. The 2 or 3 main techniques used (e.g., 'uses a for loop with range', 'recursive function')\n"
            "3. Any obvious bug or edge case (if any)\n\n"
            "End with: 'Want a deeper line by line walkthrough? Just say: analyze deeper.'\n"
            "No markdown headers. No bullet points. Spoken English only."
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

@app.route("/analyze-deep", methods=["POST"])
def analyze_deep():
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    if language == "hi":
        system = (
            "आप blind student के लिए expert Python tutor हैं। Code का DETAILED line-by-line analysis दें:\n"
            "हर logical block (loop, function, condition) को explain करें।\n"
            "हर built-in function (print, range, len, int, str, etc.) का नाम लें और बताएं वो क्या करता है।\n"
            "Variables का role बताएं।\n"
            "Symbols को words में लिखें: == 'equals equals', != 'not equals'।\n"
            "Format: 'Lines X to Y: explanation'। Plain text paragraphs। कोई markdown नहीं।"
        )
    else:
        system = (
            "You are an expert Python tutor for a blind student. Give a DETAILED line-by-line walkthrough:\n"
            "Walk through each logical block (loop, function, conditional, etc.).\n"
            "Name every built-in function used (print, range, len, int, str, etc.) and explain what each does.\n"
            "Name variables and explain their role.\n"
            "For symbols use words: == as 'equals equals', != as 'not equals', <= as 'less than or equal'.\n"
            "Format: 'Lines X to Y: explanation'. Plain text paragraphs. No markdown."
        )

    user = f"Python code:\n```python\n{code}\n```"
    analysis = call_gemini(system, user, language=language, max_tokens=4096)
    return jsonify({"analysis": analysis, "speech": analysis, "auto_speak": True})

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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    if language == "hi":
        system = (
            "आप एक शुरुआती को मार्गदर्शन देने वाले वरिष्ठ पायथन इंजीनियर हैं।\n"
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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

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
            "आप एक दृष्टिबाधित डेवलपर के लिए केवल TARGET Python पंक्ति की व्याख्या करते हैं।\n"
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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    analyzer = CodeAnalyzer()
    structure_data = analyzer.analyze(code)

    if "error" in structure_data:
        return jsonify({"success": False, "error": structure_data["error"]})

    return jsonify({"success": True, "structure": structure_data})


def _structure_outline_text(structure_data: dict) -> str:
    imports = structure_data.get("imports", []) or []
    functions = structure_data.get("functions", []) or []
    classes = structure_data.get("classes", []) or []
    loops = structure_data.get("loops", []) or []

    parts = [
        (
            f"This file has {len(imports)} import{'s' if len(imports) != 1 else ''}, "
            f"{len(functions)} function{'s' if len(functions) != 1 else ''}, "
            f"{len(classes)} class{'es' if len(classes) != 1 else ''}, and "
            f"{len(loops)} loop{'s' if len(loops) != 1 else ''}."
        )
    ]
    if classes:
        class_bits = []
        for cls in classes[:5]:
            methods = [
                fn.get("name")
                for fn in functions
                if fn.get("parent_class") == cls.get("name") and fn.get("name")
            ]
            method_text = f" with methods {', '.join(methods[:5])}" if methods else ""
            class_bits.append(f"{cls.get('name', 'unnamed')} at line {cls.get('line', '?')}{method_text}")
        parts.append("Classes: " + "; ".join(class_bits) + ".")
    top_functions = [fn for fn in functions if not fn.get("parent_class")]
    if top_functions:
        func_bits = [
            f"{fn.get('name', 'unnamed')} at line {fn.get('line', '?')}"
            for fn in top_functions[:8]
        ]
        parts.append("Functions: " + ", ".join(func_bits) + ".")
    if loops:
        loop_bits = [
            f"{loop.get('type', 'loop')} at line {loop.get('line', '?')}"
            for loop in loops[:8]
        ]
        parts.append("Loops: " + ", ".join(loop_bits) + ".")
    return " ".join(parts)


@app.route("/structure-outline", methods=["POST"])
def structure_outline():
    body = safejson()
    code = safe(body.get("code"), "")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        return jsonify({"success": True, "outline": "This file is empty."})
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    analyzer = CodeAnalyzer()
    structure_data = analyzer.analyze(code)
    if "error" in structure_data:
        return jsonify({"success": False, "error": structure_data["error"]})
    return jsonify({"success": True, "outline": _structure_outline_text(structure_data), "structure": structure_data})

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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

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


# Single-letter variable names get NATO-phonetic expansion so screen readers
# don't mangle them ("x" → "ks" or "ex" depending on the engine).
_SINGLE_LETTER_PRONUNCIATION = {
    'a': 'a', 'b': 'bee', 'c': 'see', 'd': 'dee', 'e': 'ee',
    'f': 'eff', 'g': 'gee', 'h': 'aitch', 'i': 'eye', 'j': 'jay',
    'k': 'kay', 'l': 'ell', 'm': 'em', 'n': 'en', 'o': 'oh',
    'p': 'pee', 'q': 'cue', 'r': 'arr', 's': 'ess', 't': 'tee',
    'u': 'you', 'v': 'vee', 'w': 'double-you', 'x': 'ex', 'y': 'why', 'z': 'zee',
}


def pronounce_variable(var_name: str) -> str:
    """Convert variable name to phonetic pronunciation for screen readers."""
    if not var_name or not isinstance(var_name, str):
        return "unknown variable"

    var_name = var_name.strip()
    if not var_name:
        return "unknown variable"

    # Single character — use phonetic spelling to avoid TTS ambiguity
    if len(var_name) == 1:
        return _SINGLE_LETTER_PRONUNCIATION.get(var_name.lower(), var_name)

    if "_" in var_name:
        # Special case for dunders: __init__, __name__, __main__ etc.
        # Pronounce as "dunder X" rather than "underscore underscore X underscore underscore"
        if var_name.startswith("__") and var_name.endswith("__") and len(var_name) > 4:
            inner = var_name[2:-2]
            return f"dunder {inner}"
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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

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
            "आप एक expert Python debugger हैं जो blind-first IDE में काम करते हैं।\n"
            "नीचे दिए गए कोड में हर तरह की समस्या ढूंढें और ठीक करें:\n"
            "1. Syntax errors (missing colons, unmatched parentheses, indentation)\n"
            "2. Runtime errors (NameError, TypeError, IndexError, ZeroDivisionError, KeyError)\n"
            "3. Logic bugs (off-by-one, wrong comparison operator, wrong variable used, missing return)\n"
            "4. CodeUp-specific: input() function BLOCKED है — इसे hardcoded values से replace करें (e.g. name = 'Alice')\n"
            "5. Edge cases (empty list, division by zero, negative numbers जहां positive expected)\n"
            "6. Bad practices (mutable default arguments, comparing with == None instead of is None)\n\n"
            "CRITICAL: केवल actual bugs ठीक करें। नए features या type checks add NA करें।\n"
            "अगर code पहले से ठीक है तो उसे वैसा ही return करें।\n"
            "isinstance() या type() जैसी checks केवल तब add करें जब आप 100% sure हैं कि second argument एक valid type है।\n"
            "हर महत्वपूर्ण fix के ऊपर एक comment add करें।\n"
            "Original intent maintain करें — completely re-write न करें।\n"
            "केवल valid Python code लौटाएं। कोई markdown fences नहीं।"
        )
    else:
        system = (
            "You are an expert Python debugger for a blind-first IDE.\n"
            "Find and fix EVERY type of problem in the code below:\n"
            "1. Syntax errors (missing colons, unmatched parens, bad indentation)\n"
            "2. Runtime errors (NameError, TypeError, IndexError, ZeroDivisionError, KeyError)\n"
            "3. Logic bugs (off-by-one, wrong comparison operator, wrong variable, missing return)\n"
            "4. CodeUp-specific: the input() function is BLOCKED in this sandbox. Replace any input() call with a hardcoded sample value (e.g. name = 'Alice'  instead of  name = input('Your name? '))\n"
            "5. Edge cases (empty list, division by zero, negative numbers where positive expected)\n"
            "6. Bad practices (mutable default args, comparing with == None instead of is None, bare except clauses)\n\n"
            "Add a brief comment above each meaningful fix explaining WHAT changed and WHY. A blind student will hear these comments via screen reader.\n"
            "Preserve original intent — do not rewrite from scratch.\n"
            "Return only valid Python code. NO markdown fences. NO prose outside code comments."
        )

    user = f"Fix this code:\n```python\n{code}\n```"
    raw = call_gemini(system, user, temperature=0.1, language=language)
    fixed = extract_code(raw)
    if not fixed and raw and not _is_ai_service_message(raw):
        fixed = raw.strip()

    # Reject suspicious "fixes" that bear no resemblance to the original. The LLM
    # sometimes ignores "do not rewrite from scratch" and ships an unrelated
    # solution, which is dangerous because the student loses their work.
    if fixed and code:
        import difflib
        ratio = difflib.SequenceMatcher(None, code, fixed).ratio()
        if ratio < 0.3:
            retry_system = system + "\n\nCRITICAL: Your previous answer was rejected because it was too different from the user's original code. Make MINIMAL changes — preserve variable names, structure, and overall approach. Only fix the specific bugs."
            raw_retry = call_gemini(retry_system, user, temperature=0.05, language=language)
            retry_fixed = extract_code(raw_retry) or (raw_retry.strip() if raw_retry and not _is_ai_service_message(raw_retry) else "")
            if retry_fixed:
                retry_ratio = difflib.SequenceMatcher(None, code, retry_fixed).ratio()
                if retry_ratio >= 0.3:
                    fixed = retry_fixed
    if not fixed and _is_ai_service_message(raw):
        return jsonify({"success": False, "error": raw.strip(), "code": ""})
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
            "आप एक beginner-friendly, blind-first Python IDE (CodeUp) के लिए code generator हैं।\n"
            "User प्राकृतिक भाषा में task बताएगा। आप उसे हल करने वाला Python code generate करें।\n\n"
            "महत्वपूर्ण constraints:\n"
            "1. input() function का use NEVER करें। यह CodeUp sandbox में blocked है।\n"
            "   Instead: variables को hardcoded sample values दें (e.g. length = 10, width = 5, name = 'Alice')\n"
            "   जब user input की जरूरत हो तो उन values को directly assign करें।\n"
            "2. केवल इन modules का use कर सकते हैं: math, random, string, datetime। बाकी कुछ blocked है।\n"
            "3. open(), eval(), exec(), __import__() का NEVER use करें — सब blocked हैं।\n"
            "4. हर महत्वपूर्ण line के ऊपर एक छोटी comment add करें जो शुरुआती समझ सकें।\n"
            "5. Code के end में print() statements add करें ताकि user output देख सके।\n\n"
            "केवल Python code लौटाएं। Markdown fences न डालें। कोई prose explanation नहीं।"
        )
    else:
        system = (
            "You are a Python code generator for CodeUp, a beginner-friendly, blind-first IDE.\n"
            "The user will describe a task in natural language. Generate Python code that solves it.\n\n"
            "CRITICAL CONSTRAINTS:\n"
            "1. NEVER use input() — it is BLOCKED in the CodeUp sandbox.\n"
            "   Instead, hardcode sample values directly into variables.\n"
            "   Example: instead of `length = float(input('length?'))` write `length = 10  # sample value, change this to test`\n"
            "   Choose realistic sample values that make the program produce visible output.\n"
            "2. Only these modules are available: math, random, string, datetime. Do NOT import anything else.\n"
            "3. NEVER use open(), eval(), exec(), or __import__() — all blocked.\n"
            "4. Add a brief comment above each non-trivial line explaining what it does, written for a beginner. A blind student will hear these via screen reader.\n"
            "5. Always include print() statements at the end so the user can see the result when they run it.\n"
            "6. For interactive-feeling tasks (menus, calculators, games), use hardcoded sample inputs and explain in a comment how to change them.\n\n"
            "Return ONLY Python code. No markdown fences. No prose outside code comments."
        )

    user = f"Task description:\n{prompt}"
    raw = call_gemini(system, user, temperature=0.2, language=language)
    code = extract_code(raw)
    # Llama sometimes returns code without ``` fences. If extract_code came back empty,
    # try using the raw response directly (assuming it's already plain code).
    if not code and raw and not _is_ai_service_message(raw):
        code = raw.strip()
    if not code:
        fallback = _local_code_generation_fallback(prompt)
        if fallback and _should_use_local_generation_fallback(raw):
            return jsonify({"success": True, "code": fallback, "source": "local_fallback"})
        error = raw.strip() if raw else "AI returned empty response. Try rephrasing."
        return jsonify({"success": False, "error": error, "code": ""})

    # Verify the result actually parses as Python before shipping it to the editor.
    # If the LLM returned an explanation paragraph by mistake, this catches it.
    try:
        compile(code, "<generated>", "exec")
    except SyntaxError:
        # One retry with a stricter system message
        retry_system = system + "\n\nIMPORTANT: Return ONLY syntactically valid Python. No prose. No markdown."
        raw_retry = call_gemini(retry_system, user, temperature=0.1, language=language)
        retry_code = extract_code(raw_retry) or (raw_retry.strip() if raw_retry and not _is_ai_service_message(raw_retry) else "")
        try:
            compile(retry_code, "<generated>", "exec")
            code = retry_code
        except SyntaxError:
            fallback = _local_code_generation_fallback(prompt)
            if fallback and _should_use_local_generation_fallback(raw_retry):
                return jsonify({"success": True, "code": fallback, "source": "local_fallback"})
            if _is_ai_service_message(raw_retry):
                return jsonify({"success": False, "error": raw_retry.strip(), "code": ""})
            return jsonify({"success": False, "error": "AI returned invalid Python code. Please try rephrasing your request.", "code": ""})
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

    body = safejson()

    name = _safe_text(body.get("name"), "Untitled", limit=257).strip()
    code = _safe_text(body.get("code"), "")

    if not name:
        return jsonify({"success": False, "error": "Name is required"}), 400
    if not code.strip():
        return jsonify({"success": False, "error": "Code is required"}), 400
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked
    if len(name) > 256:
        return jsonify({"success": False, "error": "Name too long (max 256 chars)"}), 400

    new_id = str(uuid.uuid4())
    with _snippets_lock:
        data = load_snippets()
        data["snippets"].append({"id": new_id, "name": name, "code": code})
        save_snippets(data)
    return jsonify({"success": True, "id": new_id, "speech": f"Saved snippet: {name}"})

@app.route("/snippets/<sid>", methods=["PUT", "DELETE"])
def snippet_detail(sid):
    if request.method == "DELETE":
        with _snippets_lock:
            data = load_snippets()
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
    with _snippets_lock:
        data = load_snippets()
        found = False
        snippet_name = None
        for s in data["snippets"]:
            if str(s["id"]) == str(sid):
                found = True
                snippet_name = s.get("name", "Snippet")
                if "name" in body:
                    new_name = _safe_text(body["name"], limit=257).strip()
                    if not new_name:
                        return jsonify({"success": False, "error": "Name is required"}), 400
                    if len(new_name) > 256:
                        return jsonify({"success": False, "error": "Name too long (max 256 chars)"}), 400
                    s["name"] = new_name
                    snippet_name = new_name
                if "code" in body:
                    new_code = _safe_text(body["code"])
                    if not new_code.strip():
                        return jsonify({"success": False, "error": "Code is required"}), 400
                    if len(new_code) > MAX_CODE_SIZE:
                        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
                    blocked = _reject_non_python_response(new_code)
                    if blocked:
                        return blocked
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
    "run": [
        "run", "execute", "run code", "execute code", "start code", "start program",
        "चलाओ", "कोड चलाओ", "रन करो", "रन",
    ],
    "analyze": [
        "analyze", "analyse", "analyze code", "analyse code", "explain code", "check code", "review code",
        "विश्लेषण करो", "कोड का विश्लेषण", "कोड समझाओ", "कोड जांचो",
    ],
    "speak": [
        "speak output", "read output", "read the output", "say the output",
        "आउटपुट पढ़ो", "आउटपुट बोलो", "output बताओ",
    ],
    "fix": [
        "fix", "fix code", "auto fix", "repair code", "correct code",
        "ठीक करो", "कोड ठीक करो", "गलती ठीक करो", "सही करो",
    ],
    "repeat_last_action": [
        "repeat", "do that again", "again", "repeat last",
        "दोहराओ", "फिर से करो", "वापस",
    ],
    "repeat_last_speech": [
        "repeat that", "say that again", "repeat message", "repeat output",
        "फिर से बोलो", "वही दोहराओ",
    ],
    "advise": [
        "advise on code", "advice on code", "improve code", "how to improve code",
        "सुझाव दो", "कोड सुधारो", "improve करो",
    ],
    "generate_code": [
        "generate code", "write code", "create code", "make code",
        "कोड बनाओ", "कोड लिखो", "code बनाओ",
    ],
    "clear_editor": [
        "clear editor", "clear code", "clear file", "reset code",
        "एडिटर साफ करो", "कोड हटाओ", "कोड मिटाओ",
    ],
    "read_line_enhanced": ["read line with context", "enhanced read line", "describe line position", "where am i", "line context", "read line", "read current line", "read this line", "what is this line", "describe this line"],
    "sonify_block": ["sonify block", "sonify", "audio structure", "hear structure", "play code structure", "sound out code", "play this", "play code"],
    "sonify_file": ["sonify whole file", "sonify file", "sonify code", "sonify indent profile"],
    "read_outline": ["read outline", "read structure", "speak outline", "code outline"],
    "explain_diff": ["why is the output different", "why did this run differently", "explain output diff"],
    "list_variables": ["what variables", "list variables", "show variables", "what variables are available", "variables in scope"],
    "check_errors": ["check for errors", "check syntax", "find errors", "are there errors", "syntax check"],
    "locate_error": ["where is the error", "where is error", "find error", "jump to error", "go to error"],
    "stop_beacon": ["stop error beacon", "stop beacon", "turn off beacon", "disable beacon"],
    "go_back": ["go back", "navigate back", "back", "previous position"],
    "go_forward": ["go forward", "navigate forward", "forward", "next position"],
    "show_history": ["show history", "navigation history", "where have i been"],
    "help": ["help", "show help", "what can you do", "list commands"],
    "walk_through": ["walk through", "walk to", "walk through code", "walk me through", "walk through the code", "explain line by line", "narrate each line", "go through code", "explain the code line by line"],
    "file_stats": ["file stats", "how many lines", "file statistics", "code stats"],
    "go_to_top": ["go to top", "jump to top", "top of file"],
    "go_to_bottom": ["go to bottom", "jump to bottom", "bottom of file", "end of file"],
    "copy_code": ["copy code", "copy to clipboard", "copy this"],
    "paste_code": ["paste code", "paste from clipboard", "paste"],
    "start_tutorial":     ["start tutorial", "open tutorial", "begin tutorial", "tutorial"],
    "restart_tutorial":  ["restart tutorial", "tutorial restart", "start tutorial again"],
    "skip_tutorial":    ["skip tutorial", "close tutorial", "exit tutorial", "stop tutorial"],
    "toggle_dyslexia":  ["dyslexia mode", "toggle dyslexia", "turn on dyslexia", "turn off dyslexia"],
    "toggle_motion":    ["reduce motion", "reduced motion", "toggle motion", "motion mode"],
    "toggle_night":     ["night mode", "dark mode", "toggle night", "toggle dark mode"],
    "cycle_color_mode": ["color mode", "colour mode", "color blind mode", "colour blind mode", "cycle color mode", "next color mode"],
    "list_variables_voice": ["what are my variables", "show me variables", "list my variables", "what variables do i have", "what variables are declared"],
    "story_mode":       ["tell the story", "narrate execution", "execution story", "what happened", "story mode", "कहानी बताओ"],
    "mentor_mode":      ["learning mode", "mentor mode", "tutor mode", "teach me", "मुझे सिखाओ"],
    "quiz_me":          ["quiz me", "test me", "challenge me", "quiz करो", "test करो"],
    "bug_challenge":    ["bug challenge", "debug challenge", "give me a bug", "bug ढूंढो"],
    "clear_breakpoints":["clear breakpoints", "remove breakpoints", "delete breakpoints"],
    "stop_everything": ["stop", "stop it", "shut up", "be quiet", "silence", "stop talking", "cancel", "रुको", "बंद करो", "चुप", "रुक"],
}


def best_two_commands(text: str):
    """Return the top two DISTINCT command names by fuzzy score.

    Bug fix: previously this scored every phrase independently, so a command
    with multiple similar phrases could occupy both the best AND second slot
    (e.g. "walk to" → ["walk through", "walk through"]). Now we collapse to
    the best score per command name first, then pick the top two distinct
    commands.
    """
    text = text.lower().strip()

    # Best score per command name
    per_command_best = {}
    for name, phrases in COMMANDS.items():
        top = 0
        for p in phrases:
            s = fuzz.ratio(text, p)
            if s > top:
                top = s
        per_command_best[name] = top

    # Sort commands by best score, take top two distinct names
    ranked = sorted(per_command_best.items(), key=lambda kv: kv[1], reverse=True)

    best_name, best_score = (None, 0)
    second_name, second_score = (None, 0)
    if len(ranked) >= 1:
        best_name, best_score = ranked[0]
    if len(ranked) >= 2:
        second_name, second_score = ranked[1]

    return best_name, best_score, second_name, second_score

# ==========================
# FULL FILE NARRATION
# ==========================

@app.route("/narrate-file", methods=["POST"])
def narrate_file():
    """Narrate the entire file line-by-line in conversational, screen-reader-friendly form.

    Unlike /analyze (brief summary) or /analyze-deep (line-by-line technical),
    this produces a continuous spoken narrative meant to be played from start to finish.
    """
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        return jsonify({"success": False, "error": "Code is empty"}), 400
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    # Cap at 5KB OR 50 lines, whichever comes first, to keep LLM input in budget.
    NARRATE_CHAR_CAP = 5000
    NARRATE_LINE_CAP = 50
    code_lines_full = code.splitlines()
    if len(code) > NARRATE_CHAR_CAP or len(code_lines_full) > NARRATE_LINE_CAP:
        truncated_code = "\n".join(code_lines_full[:NARRATE_LINE_CAP])
        if len(truncated_code) > NARRATE_CHAR_CAP:
            truncated_code = truncated_code[:NARRATE_CHAR_CAP]
        code_to_narrate = truncated_code
        was_truncated = True
    else:
        code_to_narrate = code
        was_truncated = False

    if language == "hi":
        system = (
            "आप एक blind learner के लिए Python code का पूरा narration बनाते हैं।\n"
            "Code को शुरू से अंत तक पढ़ें और हर line को conversational tone में explain करें।\n"
            "Format: 'Line 1: print statement है जो hello print करता है।'\n"
            "Symbols को words में: == 'equals equals', != 'not equals'।\n"
            "हर line का अलग वाक्य। कोई markdown, bullet points, या headers नहीं।\n"
            "अधिकतम 50 lines। अगर file बड़ी है तो पहली 50 lines narrate करें।"
        )
    else:
        system = (
            "You are narrating Python code from start to finish for a blind learner.\n"
            "Walk through every line in order and explain it conversationally.\n"
            "Format: 'Line 1: a print statement that prints hello.'\n"
            "Spell out symbols as words: == as 'equals equals', != as 'not equals'.\n"
            "One sentence per line. No markdown, bullets, or headers.\n"
            "Maximum 50 lines. If the file is longer, narrate the first 50 and note the truncation at the end.\n"
            "Keep each sentence short — this will be played aloud."
        )

    user = f"Narrate this code:\n```python\n{code_to_narrate}\n```"
    narration = call_gemini(system, user, temperature=0.2, language=language, max_tokens=4096)

    line_count = len(code_lines_full)

    return jsonify({
        "success": True,
        "narration": narration,
        "line_count": line_count,
        "truncated": was_truncated,
    })

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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    language = safe(body.get("language"), "en")

    if language == "hi":
        system = (
            "आप एक दृष्टिबाधित शुरुआती के लिए पायथन कोड को सारांशित करते हैं।\n"
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
    blocked = _reject_non_python_response(before) or _reject_non_python_response(after)
    if blocked:
        return blocked

    language = safe(body.get("language"), "en")

    if language == "hi":
        system = (
            "आप Python कोड के दो संस्करणों के बीच क्या बदला है यह समझाते हैं।\n"
            "एक दृष्टिबाधित छात्र के लिए सरल शब्दों में समझाएं।\n"
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
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    lines = code.splitlines()
    current_line = lines[min(int(line) - 1, len(lines) - 1)] if lines else ""
    context_start = max(0, int(line) - 5)
    context = "\n".join(f"{i+1}: {line_text}" for i, line_text in enumerate(lines[context_start:int(line)]))

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
        if _is_ai_service_message(raw):
            return jsonify({"success": False, "suggestions": [], "error": raw})
        clean = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
        parsed = json.loads(clean)
        suggestions = parsed.get("suggestions", [])[:3]
        return jsonify({"success": True, "suggestions": suggestions})
    except Exception as e:
        return jsonify({"success": False, "suggestions": [], "error": str(e)})

def _trace_playback(direction):
    """Advance, rewind, or report the current trace step. All reads and the
    follow-up write happen under the same lock so a concurrent session
    cleanup cannot delete the dict between fetch and mutation."""
    session_id = get_session_id()
    with _session_traces_lock:
        storage = _session_traces.get(session_id)
        if storage is None:
            # Cleanup happened or session never ran code
            return "No execution trace available."
        storage['last_accessed'] = time.time()
        trace = list(storage.get('last_trace', []) or [])
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
        event = trace[idx] if 0 <= idx < len(trace) else None

    return _event_to_speech(event, idx, len(trace))


# Telemetry: log unrecognized voice commands so we can grow the vocabulary
# from real usage instead of guessing. Stored per-session, capped, opt-out via env.

def _log_unrecognized_command(text: str, session_id: str):
    """Record a command that the parser couldn't match. Used to identify
    phrasings real users employ that the patterns don't yet cover.
    Default-OFF — set VOICE_TELEMETRY=1 to enable."""
    if os.environ.get("VOICE_TELEMETRY", "0") != "1":
        return
    if not text or len(text) > 500:
        return
    with _voice_telemetry_lock:
        _voice_telemetry.append({
            "text": text,
            "session": session_id[:8],  # truncated for privacy
            "timestamp": time.time(),
        })
        # Keep only the most recent N entries
        if len(_voice_telemetry) > _VOICE_TELEMETRY_CAP:
            del _voice_telemetry[:len(_voice_telemetry) - _VOICE_TELEMETRY_CAP]


@app.route("/voice-telemetry", methods=["GET"])
def get_voice_telemetry():
    """Return logged unrecognized commands for analysis. Auth-gated via
    VOICE_TELEMETRY_TOKEN env var. Default-OFF: telemetry must be explicitly
    enabled with VOICE_TELEMETRY=1 AND a token must be set and supplied."""
    if os.environ.get("VOICE_TELEMETRY", "0") != "1":
        return jsonify({"success": False, "error": "Telemetry disabled"}), 404
    expected_token = os.environ.get("VOICE_TELEMETRY_TOKEN", "")
    if not expected_token:
        return jsonify({"success": False, "error": "Telemetry not configured"}), 404
    supplied = request.headers.get("X-Telemetry-Token", "") or request.args.get("token", "")
    if supplied != expected_token:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    with _voice_telemetry_lock:
        return jsonify({
            "success": True,
            "count": len(_voice_telemetry),
            "entries": list(_voice_telemetry),
        })


@app.route("/voice-command", methods=["POST"])
def voice():
    text = _safe_text(safejson().get("text"), limit=MAX_VOICE_TEXT_SIZE + 1).strip()
    if len(text) > MAX_VOICE_TEXT_SIZE:
        return jsonify({"success": False, "action": "unknown", "error": "Voice command is too long"}), 413
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
        with _session_traces_lock:
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
        if intent == "mentor_stop":
            return _store_and_return({"success": True, "action": "mentor_stop", "confidence": confidence})
        if intent == "mentor_code_map":
            return _store_and_return({"success": True, "action": "mentor_code_map", "confidence": confidence})
        if intent == "mentor_progress":
            return _store_and_return({"success": True, "action": "mentor_progress", "confidence": confidence})
        if intent == "mentor_hint":
            return _store_and_return({"success": True, "action": "mentor_chat", "message": slots.get("message", ""), "mode": slots.get("mode", "tiny_hint"), "confidence": confidence})
        if intent == "mentor_walkthrough":
            return _store_and_return({"success": True, "action": "mentor_chat", "message": slots.get("message", ""), "mode": "slow_walkthrough", "confidence": confidence})
        if intent == "mentor_transform":
            return _store_and_return({"success": True, "action": "mentor_chat", "message": slots.get("message", ""), "mode": slots.get("mode", "shorter"), "confidence": confidence})
        if intent == "mentor_preference":
            return _store_and_return({"success": True, "action": "mentor_preference", "key": slots.get("key"), "value": slots.get("value"), "confidence": confidence})
        if intent == "mentor_chat":
            return _store_and_return({"success": True, "action": "mentor_chat", "message": slots.get("message", text), "mode": slots.get("mode", "general"), "confidence": confidence})
        if intent == "analyze_deep":
            return _store_and_return({"success": True, "action": "analyze_deep", "confidence": confidence})
        if intent == "analyze":
            return _store_and_return({"success": True, "action": "analyze", "confidence": confidence})
        if intent == "fix":
            return _store_and_return({"success": True, "action": "fix", "confidence": confidence})
        if intent == "advise":
            return _store_and_return({"success": True, "action": "advise", "confidence": confidence})
        if intent == "summarize":
            return _store_and_return({"success": True, "action": "summarize", "confidence": confidence})
        if intent == "narrate_file":
            return _store_and_return({"success": True, "action": "narrate_file", "confidence": confidence})
        if intent == "demo_list":
            return _store_and_return({"success": True, "action": "demo_list", "confidence": confidence})
        if intent == "demo_run":
            return _store_and_return({"success": True, "action": "demo_run", "preset": slots.get("preset", ""), "confidence": confidence})
        if intent == "pause_voice":
            return _store_and_return({"success": True, "action": "pause_voice", "confidence": confidence})
        if intent == "resume_voice":
            return _store_and_return({"success": True, "action": "resume_voice", "confidence": confidence})
        if intent == "generate_code":
            return _store_and_return({"success": True, "action": "generate_code", "prompt": slots.get("prompt", ""), "confidence": confidence})
        if intent == "rename_snippet":
            return _store_and_return({"success": True, "action": "rename_snippet", "id": slots.get("id"), "new_name": slots.get("new_name"), "confidence": confidence})
        if intent == "save_snippet_named":
            return _store_and_return({"success": True, "action": "save_snippet_named", "name": slots.get("name", "Untitled"), "confidence": confidence})
        if intent == "preview_snippet":
            return _store_and_return({"success": True, "action": "preview_snippet", "snippet_id": slots.get("snippet_id"), "confidence": confidence})
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
        if intent == "more_help":
            return _store_and_return({"success": True, "action": "more_help", "confidence": confidence})
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
        if intent == "read_outline":
            return _store_and_return({"success": True, "action": "read_outline", "confidence": confidence})
        if intent == "sonify_file":
            return _store_and_return({"success": True, "action": "sonify_file", "confidence": confidence})
        if intent == "explain_diff":
            return _store_and_return({"success": True, "action": "explain_diff", "confidence": confidence})
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
        if intent == "set_inputs":
            return _store_and_return({"success": True, "action": "set_inputs", "values": slots.get("values", []), "confidence": confidence})
        if intent == "clear_inputs":
            return _store_and_return({"success": True, "action": "clear_inputs", "confidence": confidence})
        if intent == "list_inputs":
            return _store_and_return({"success": True, "action": "list_inputs", "confidence": confidence})
        if intent == "live_input_mode":
            return _store_and_return({"success": True, "action": "live_input_mode", "confidence": confidence})
        if intent == "preflight_input_mode":
            return _store_and_return({"success": True, "action": "preflight_input_mode", "confidence": confidence})
        if intent == "save_macro":
            return _store_and_return({"success": True, "action": "save_macro", "name": slots.get("name", ""), "confidence": confidence})
        if intent == "use_macro":
            return _store_and_return({"success": True, "action": "use_macro", "name": slots.get("name", ""), "confidence": confidence})
        if intent == "list_macros":
            return _store_and_return({"success": True, "action": "list_macros", "confidence": confidence})
        if intent == "share_macro":
            return _store_and_return({"success": True, "action": "share_macro", "name": slots.get("name", ""), "confidence": confidence})
        if intent == "use_shared_macro":
            return _store_and_return({"success": True, "action": "use_shared_macro", "share_code": slots.get("share_code", ""), "confidence": confidence})
        if intent == "bookmark_output":
            return _store_and_return({"success": True, "action": "bookmark_output", "label": slots.get("label", ""), "confidence": confidence})
        if intent == "read_bookmark":
            return _store_and_return({"success": True, "action": "read_bookmark", "label": slots.get("label", ""), "confidence": confidence})
        if intent == "list_bookmarks":
            return _store_and_return({"success": True, "action": "list_bookmarks", "confidence": confidence})
        if intent == "where_am_i":
            return _store_and_return({"success": True, "action": "where_am_i", "confidence": confidence})
        if intent == "explain_simply":
            return _store_and_return({"success": True, "action": "explain_simply", "confidence": confidence})
        if intent == "narrate_diff":
            return _store_and_return({"success": True, "action": "narrate_diff", "confidence": confidence})
        if intent == "restart_tutorial":
            return _store_and_return({"success": True, "action": "restart_tutorial", "confidence": confidence})
        if intent == "set_color_mode":
            return _store_and_return({"success": True, "action": "set_color_mode", "mode": slots.get("mode", "default"), "confidence": confidence})

    # Fallback: fuzzy matching on COMMANDS
    lower_text = text.lower().strip()
    best, bscore, second, sscore = best_two_commands(lower_text)

    # High-frequency commands skip the confirm prompt even at moderate confidence,
    # because in a noisy classroom getting "did you mean run or fix?" on every
    # command is more frustrating than occasionally running the wrong simple action.
    HIGH_FREQUENCY_COMMANDS = {"run", "analyze", "fix", "help", "speak", "read_output", "walk_through", "sonify_block", "stop_everything", "pause_voice", "resume_voice"}

    if best and bscore >= VOICE_FUZZY_THRESHOLD:
        is_clear_winner = bscore >= 75 and (not second or (bscore - sscore) >= 15)
        is_high_freq = best in HIGH_FREQUENCY_COMMANDS and bscore >= 65

        if is_clear_winner or is_high_freq:
            return _store_and_return({"success": True, "action": best, "confidence": bscore / 100.0})

        options = [best, second] if second else [best]
        return jsonify({
            "success": True,
            "action": "confirm",
            "options": options,
            "heard": text,
            "confidence": bscore / 100.0
        })

    # Log unrecognized commands so we can iterate the vocabulary from real data
    _log_unrecognized_command(text, get_session_id())
    return jsonify({"success": True, "action": "unknown", "heard": text, "confidence": 0.0})

# ==========================
# CODE STRUCTURE BREADCRUMBS
# ==========================

@app.route("/breadcrumbs", methods=["POST"])
def breadcrumbs():
    """Return the nested-structure breadcrumb at a given line.

    Walks the AST from the file root down, tracking which functions/classes/
    loops/conditionals contain the target line. Returns a human-readable
    string like 'function calculate, inside for loop, line 15'.
    """
    body = safejson()
    code = safe(body.get("code"), "")
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked
    try:
        line = int(body.get("line", 1))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid line"}), 400

    if not code.strip():
        return jsonify({"success": True, "breadcrumb": "empty file", "trail": []})

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return jsonify({"success": False, "message": f"Cannot parse: {e}"})

    trail = []  # list of (kind, name, start_line, end_line)

    def end_line(node):
        explicit_end = getattr(node, 'end_lineno', None)
        if explicit_end is not None:
            return explicit_end
        line_numbers = [n.lineno for n in ast.walk(node) if hasattr(n, 'lineno')]
        return max(line_numbers) if line_numbers else None

    def walk(node, depth=0):
        for child in ast.iter_child_nodes(node):
            start = getattr(child, 'lineno', None)
            if start is None:
                continue
            stop = end_line(child)
            if stop is None:
                continue
            if not (start <= line <= stop):
                continue
            kind = None
            name = None
            if isinstance(child, ast.FunctionDef):
                kind, name = "function", child.name
            elif isinstance(child, ast.AsyncFunctionDef):
                kind, name = "async function", child.name
            elif isinstance(child, ast.ClassDef):
                kind, name = "class", child.name
            elif isinstance(child, ast.For):
                kind, name = "for loop", None
            elif isinstance(child, ast.While):
                kind, name = "while loop", None
            elif isinstance(child, ast.If):
                kind, name = "if block", None
            elif isinstance(child, ast.With):
                kind, name = "with block", None
            elif isinstance(child, ast.Try):
                kind, name = "try block", None
            if kind:
                trail.append({"kind": kind, "name": name, "line": start})
            walk(child, depth + 1)

    walk(tree)

    if not trail:
        breadcrumb = f"line {line}, top level of file"
    else:
        parts = []
        for item in trail:
            if item["name"]:
                parts.append(f"{item['kind']} {item['name']}")
            else:
                parts.append(item["kind"])
        breadcrumb = ", inside ".join(parts) + f", line {line}"

    return jsonify({"success": True, "breadcrumb": breadcrumb, "trail": trail})


# ==========================
# OUTPUT BOOKMARKS
# ==========================

@app.route("/bookmarks", methods=["GET", "POST", "DELETE"])
def bookmarks():
    """Per-session output bookmarks.

    POST {label, position} → save bookmark at that output offset
    GET → list all bookmarks for this session
    DELETE → clear all bookmarks for this session
    """
    session_id = get_session_id()
    if request.method == "GET":
        with _output_bookmarks_lock:
            marks = list(_output_bookmarks.get(session_id, []))
        return jsonify({"success": True, "bookmarks": marks})

    if request.method == "DELETE":
        with _output_bookmarks_lock:
            _output_bookmarks.pop(session_id, None)
        return jsonify({"success": True, "speech": "All bookmarks cleared."})

    body = safejson()
    label = str(safe(body.get("label"), "")).strip()[:80]
    try:
        position = int(body.get("position", 0))
    except (ValueError, TypeError):
        position = 0
    position = max(0, position)
    if not label:
        # Auto-name based on count
        with _output_bookmarks_lock:
            count = len(_output_bookmarks.get(session_id, []))
        label = f"bookmark {count + 1}"

    with _output_bookmarks_lock:
        marks = _output_bookmarks.setdefault(session_id, [])
        # Cap at 20 per session
        if len(marks) >= 20:
            marks.pop(0)
        marks.append({
            "label": label,
            "position": position,
            "timestamp": time.time(),
        })
    return jsonify({"success": True, "label": label, "speech": f"Bookmarked as {label}."})


@app.route("/bookmarks/read", methods=["POST"])
def read_from_bookmark():
    """Return the slice of output starting from a named bookmark."""
    body = safejson()
    label = str(safe(body.get("label"), "")).strip().lower()
    full_output = _safe_text(body.get("output"), "", limit=MAX_REQUEST_SIZE + 1)
    if len(full_output) > MAX_REQUEST_SIZE:
        return jsonify({"success": False, "error": "Output too large"}), 413
    session_id = get_session_id()

    with _output_bookmarks_lock:
        marks = list(_output_bookmarks.get(session_id, []))
    match = next((m for m in marks if m["label"].lower() == label), None)
    if not match and marks:
        # Fuzzy: take the most recent if no exact match
        match = marks[-1]
    if not match:
        return jsonify({"success": False, "error": "No bookmark found"})

    pos = match["position"]
    if pos < 0 or pos > len(full_output):
        return jsonify({"success": False, "error": "Bookmark position out of range"})
    return jsonify({
        "success": True,
        "label": match["label"],
        "slice": full_output[pos:],
    })


# ==========================
# VOICE MACROS
# ==========================

def _macros_path(session_id=None):
    if session_id is None:
        session_id = get_session_id()
    safe_id = re.sub(r'[^a-fA-F0-9\-]', '', session_id)[:64] or "default"
    return os.path.join(DATA_DIR, f"macros_{safe_id}.json")


def _shared_macros_dir():
    path = os.path.join(DATA_DIR, "shared_macros")
    os.makedirs(path, exist_ok=True)
    return path


def _normalize_share_code(code: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]', '', str(code or ''))[:64]


def _shared_macro_path(code: str):
    safe_code = _normalize_share_code(code)
    return os.path.join(_shared_macros_dir(), f"{safe_code}.json")


def _new_share_code():
    for _ in range(20):
        code = secrets.token_urlsafe(16)
        if not os.path.exists(_shared_macro_path(code)):
            return code
    return secrets.token_hex(16)


def _shared_macro_lookup_key():
    return f"{request.remote_addr or 'unknown'}:{get_session_id()}"


def _check_shared_macro_lookup_limit():
    now = time.time()
    key = _shared_macro_lookup_key()
    with _shared_macro_lookup_lock:
        for lookup_key in list(_shared_macro_lookup_attempts.keys()):
            attempts = [
                ts for ts in _shared_macro_lookup_attempts.get(lookup_key, [])
                if now - ts < _SHARED_MACRO_LOOKUP_WINDOW
            ]
            if attempts:
                _shared_macro_lookup_attempts[lookup_key] = attempts
            else:
                del _shared_macro_lookup_attempts[lookup_key]

        max_keys = max(1, int(_SHARED_MACRO_LOOKUP_MAX_KEYS))
        while key not in _shared_macro_lookup_attempts and len(_shared_macro_lookup_attempts) >= max_keys:
            oldest_key = min(
                _shared_macro_lookup_attempts,
                key=lambda lookup_key: (
                    _shared_macro_lookup_attempts[lookup_key][-1],
                    lookup_key,
                ),
            )
            del _shared_macro_lookup_attempts[oldest_key]

        attempts = [
            ts for ts in _shared_macro_lookup_attempts.get(key, [])
            if now - ts < _SHARED_MACRO_LOOKUP_WINDOW
        ]
        if len(attempts) >= _SHARED_MACRO_LOOKUP_LIMIT:
            _shared_macro_lookup_attempts[key] = attempts
            return False
        attempts.append(now)
        _shared_macro_lookup_attempts[key] = attempts
        return True


def _load_macros():
    path = _macros_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                cleaned = {}
                for name, value in data.items():
                    safe_name = _safe_text(name, limit=64).strip().lower()
                    if not safe_name or not re.match(r'^[a-z0-9 _\u0900-\u097f-]+$', safe_name):
                        continue
                    if isinstance(value, dict):
                        code = _safe_text(value.get("code"), "")
                        saved_at = value.get("saved_at", 0)
                    else:
                        code = _safe_text(value, "")
                        saved_at = 0
                    if code.strip():
                        cleaned[safe_name] = {"code": code, "saved_at": saved_at}
                return cleaned
    except (OSError, json.JSONDecodeError, TypeError) as e:
        _debug_log(f"Could not load macros from {path}: {e}")
    return {}


def _save_macros(macros):
    path = _macros_path()
    dirpath = os.path.dirname(path) or "."
    with _voice_macros_lock:
        tmp = None
        try:
            os.makedirs(dirpath, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".json", prefix="macros_", dir=dirpath)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(macros, f, indent=2)
            except (OSError, TypeError, ValueError):
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise


@app.route("/macros", methods=["GET"])
def list_macros():
    macros = _load_macros()
    names = sorted(macros.keys())
    if not names:
        speech = "You have no voice macros saved."
    elif len(names) == 1:
        speech = f"You have 1 macro: {names[0]}."
    else:
        speech = f"You have {len(names)} macros: {', '.join(names)}."
    return jsonify({"success": True, "macros": macros, "names": names, "speech": speech})


@app.route("/macros", methods=["POST"])
def save_macro():
    body = safejson()
    name = str(safe(body.get("name"), "")).strip().lower()[:64]
    code = str(safe(body.get("code"), ""))
    if not name:
        return jsonify({"success": False, "error": "Macro name required"}), 400
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked
    if not re.match(r'^[a-z0-9 _\u0900-\u097f-]+$', name):
        return jsonify({"success": False, "error": "Macro name must be letters, numbers, spaces, dash or underscore"}), 400
    with _voice_macros_lock:
        macros = _load_macros()
        if len(macros) >= 50 and name not in macros:
            return jsonify({"success": False, "error": "Macro limit reached (50)"}), 400
        macros[name] = {"code": code, "saved_at": time.time()}
        _save_macros(macros)
    return jsonify({"success": True, "speech": f"Macro {name} saved."})


@app.route("/macros/<name>", methods=["DELETE"])
def delete_macro(name):
    name = name.strip().lower()
    with _voice_macros_lock:
        macros = _load_macros()
        if name not in macros:
            return jsonify({"success": False, "error": "Macro not found"}), 404
        del macros[name]
        _save_macros(macros)
    return jsonify({"success": True, "speech": f"Macro {name} deleted."})


@app.route("/macros/get/<name>", methods=["GET"])
def get_macro(name):
    name = name.strip().lower()
    macros = _load_macros()
    if name not in macros:
        return jsonify({"success": False, "error": "Macro not found"}), 404
    return jsonify({"success": True, "code": macros[name].get("code", ""), "name": name})


@app.route("/macros/share", methods=["POST"])
def share_macro():
    body = safejson()
    name = str(safe(body.get("name"), "")).strip().lower()[:64]
    code = str(safe(body.get("code"), ""))
    if not code and name:
        with _voice_macros_lock:
            macros = _load_macros()
            code = str((macros.get(name) or {}).get("code", ""))
    if not code.strip():
        return jsonify({"success": False, "error": "Macro code required"}), 400
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked
    payload = {
        "name": name or "shared macro",
        "code": code,
        "created_at": time.time(),
        "expires_at": time.time() + 24 * 60 * 60,
    }
    with _shared_macros_lock:
        for _ in range(20):
            share_code = _new_share_code()
            try:
                with open(_shared_macro_path(share_code), "x", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                break
            except FileExistsError:
                continue
        else:
            return jsonify({"success": False, "error": "Could not create share code"}), 500
    return jsonify({"success": True, "share_code": share_code, "speech": f"Shared macro code {share_code}."})


@app.route("/macros/shared/<code>", methods=["GET"])
def get_shared_macro(code):
    safe_code = _normalize_share_code(code)
    if len(safe_code) < 16:
        return jsonify({"success": False, "error": "Invalid share code"}), 400
    if not _check_shared_macro_lookup_limit():
        return jsonify({"success": False, "error": "Too many shared macro attempts"}), 429
    path = _shared_macro_path(safe_code)
    if not os.path.exists(path):
        return jsonify({"success": False, "error": "Shared macro not found"}), 404
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return jsonify({"success": False, "error": "Shared macro not found"}), 404
    except PermissionError as e:
        _debug_log(f"Shared macro {safe_code} is not readable: {e}")
        return jsonify({"success": False, "error": "Shared macro not readable"}), 403
    except json.JSONDecodeError as e:
        _debug_log(f"Shared macro {safe_code} is corrupted: {e}")
        return jsonify({"success": False, "error": "Shared macro data is corrupted"}), 500
    except OSError as e:
        _debug_log(f"Shared macro {safe_code} read failed: {e}")
        return jsonify({"success": False, "error": "Shared macro read failed"}), 500
    if not isinstance(payload, dict):
        _debug_log(f"Shared macro {safe_code} has invalid payload type.")
        return jsonify({"success": False, "error": "Shared macro data is corrupted"}), 500
    try:
        expires_at = float(payload.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at < time.time():
        try:
            os.remove(path)
        except OSError:
            pass
        return jsonify({"success": False, "error": "Shared macro expired"}), 404
    code_value = _safe_text(payload.get("code"), "")
    blocked = _reject_non_python_response(code_value)
    if blocked:
        try:
            os.remove(path)
        except OSError:
            pass
        return blocked
    return jsonify({
        "success": True,
        "share_code": safe_code,
        "name": payload.get("name", "shared macro"),
        "code": code_value,
    })


# ==========================
# INTERACTIVE RUN (Mechanism B) — SSE streaming with input pipe
# ==========================

def _terminate_process_group(proc):
    if not proc or proc.poll() is not None:
        return
    if sys.platform != "win32":
        import signal as _signal
        try:
            os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    else:
        proc.kill()


def _wait_for_process_exit(proc, timeout=2):
    if not proc:
        return None
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(proc)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return proc.poll()


def _cleanup_run(run_id):
    """Tear down a run's resources. Idempotent."""
    with _active_runs_lock:
        state = _active_runs.pop(run_id, None)
    if not state:
        return
    proc = state.get("proc")
    if proc:
        try:
            _terminate_process_group(proc)
            _wait_for_process_exit(proc)
        except (OSError, RuntimeError) as e:
            _debug_log(f"Could not terminate run {run_id}: {e}")
    fifo = state.get("fifo")
    if fifo and os.path.exists(fifo):
        try:
            os.unlink(fifo)
        except OSError:
            pass
    for tmp in (state.get("code_file"), state.get("trace_file"), state.get("script_file")):
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


@app.route("/run-stream/start", methods=["POST"])
def run_stream_start():
    """Start an interactive run. Returns a run_id. Client opens an SSE
    connection at /run-stream/<run_id> to receive output events and posts
    answers to /run-stream/<run_id>/input when prompted.

    POSIX-only feature. On Windows, returns 501 with a friendly message
    suggesting Mechanism A.
    """
    if sys.platform == "win32":
        return jsonify({
            "success": False,
            "error": "Live input mode requires POSIX (Linux or macOS). On Windows, use the inputs panel instead.",
        }), 501

    body = safejson()
    code = safe(body.get("code"), "")
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked
    if not _check_run_rate_limit(get_session_id()):
        return jsonify({"success": False, "error": "Rate limit exceeded"}), 429

    run_id = uuid.uuid4().hex
    sandbox = get_sandbox(get_session_id())
    workspace_dir = sandbox.workspace_dir

    # Create FIFO. Only one consumer (subprocess) and one producer (us).
    fifo_path = os.path.join(workspace_dir, f"input_{run_id}.fifo")
    try:
        os.mkfifo(fifo_path)
    except OSError as e:
        return jsonify({"success": False, "error": f"Could not create input pipe: {e}"}), 500

    # Write code to temp file in workspace
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                      encoding='utf-8', dir=workspace_dir) as cf:
        cf.write(code)
        code_file_path = cf.name

    trace_file = os.path.join(workspace_dir, f"trace_{run_id}.json")
    runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox_runner.py')

    env = os.environ.copy()
    env['CODEUP_CODE_FILE'] = code_file_path
    env['CODEUP_TRACE_FILE'] = trace_file
    env['CODEUP_INTERACTIVE'] = '1'
    env['CODEUP_INPUT_FIFO'] = fifo_path
    env.pop('CODEUP_INPUTS_FILE', None)

    output_queue = _queue_mod.Queue()

    popen_kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=workspace_dir,
        text=True,
        bufsize=1,  # line-buffered so we can stream
    )
    if sys.platform != "win32":
        popen_kwargs["preexec_fn"] = _set_subprocess_limits
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen([sys.executable, runner_path], **popen_kwargs)
    except (OSError, ValueError) as e:
        try:
            os.unlink(fifo_path)
        except OSError:
            pass
        try:
            os.unlink(code_file_path)
        except OSError:
            pass
        return jsonify({"success": False, "error": f"Could not start run: {e}"}), 500

    state = {
        "proc": proc,
        "fifo": fifo_path,
        "code_file": code_file_path,
        "trace_file": trace_file,
        "queue": output_queue,
        "started_at": time.time(),
        "session_id": get_session_id(),
        "awaiting_input": False,
        "awaiting_input_lock": threading.Lock(),
    }
    with _active_runs_lock:
        _active_runs[run_id] = state

    # Reader threads — push every line into the output queue. Watch for input
    # sentinel and emit a structured event when seen.
    SENTINEL = "CODEUP::INPUT_REQUEST::"

    def _stdout_reader():
        try:
            for line in iter(proc.stdout.readline, ''):
                if not line:
                    break
                if line.startswith(SENTINEL):
                    prompt = line[len(SENTINEL):].rstrip('\n')
                    with state["awaiting_input_lock"]:
                        state["awaiting_input"] = True
                    output_queue.put({"type": "input_request", "prompt": prompt})
                else:
                    output_queue.put({"type": "stdout", "text": line})
        except (OSError, ValueError) as e:
            output_queue.put({"type": "error", "text": f"stdout reader error: {e}"})
        finally:
            try:
                proc.stdout.close()
            except (AttributeError, OSError, ValueError):
                pass

    def _stderr_reader():
        try:
            for line in iter(proc.stderr.readline, ''):
                if not line:
                    break
                output_queue.put({"type": "stderr", "text": line})
        except (OSError, ValueError) as e:
            output_queue.put({"type": "error", "text": f"stderr reader error: {e}"})
        finally:
            try:
                proc.stderr.close()
            except (AttributeError, OSError, ValueError):
                pass

    def _waiter():
        # Hard 60-second cap on interactive runs (longer than batch /run because
        # users need time to think and answer prompts)
        deadline = time.time() + 60
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            try:
                _terminate_process_group(proc)
            except (OSError, RuntimeError) as e:
                _debug_log(f"Could not terminate timed-out interactive run {run_id}: {e}")
            _wait_for_process_exit(proc)
            output_queue.put({"type": "stderr", "text": "\nExecution timed out after 60 seconds.\n"})
        else:
            _wait_for_process_exit(proc, timeout=0)
        output_queue.put({"type": "done", "exit_code": proc.returncode})

    threading.Thread(target=_stdout_reader, daemon=True).start()
    threading.Thread(target=_stderr_reader, daemon=True).start()
    threading.Thread(target=_waiter, daemon=True).start()

    return jsonify({"success": True, "run_id": run_id})


@app.route("/run-stream/<run_id>/stream", methods=["GET"])
def run_stream(run_id):
    """SSE endpoint. Yields events from the output queue until 'done'."""
    with _active_runs_lock:
        state = _active_runs.get(run_id)
    if not state:
        return jsonify({"success": False, "error": "Run not found or expired"}), 404
    # Authorize: only the originating session can read its run
    if state.get("session_id") != get_session_id():
        return jsonify({"success": False, "error": "Forbidden"}), 403

    output_queue = state["queue"]

    def generate():
        try:
            while True:
                try:
                    event = output_queue.get(timeout=30)
                except _queue_mod.Empty:
                    # Heartbeat to keep connection alive through proxies
                    yield ": heartbeat\n\n"
                    # Check if run is dead but no 'done' was emitted
                    proc = state.get("proc")
                    if proc and proc.poll() is not None:
                        _wait_for_process_exit(proc, timeout=0)
                        yield f"data: {json.dumps({'type': 'done', 'exit_code': proc.returncode})}\n\n"
                        break
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    break
        finally:
            _cleanup_run(run_id)

    return app.response_class(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


@app.route("/run-stream/<run_id>/input", methods=["POST"])
def run_stream_input(run_id):
    """Receive a line of input from the client and write it to the FIFO."""
    with _active_runs_lock:
        state = _active_runs.get(run_id)
    if not state:
        return jsonify({"success": False, "error": "Run not found"}), 404
    if state.get("session_id") != get_session_id():
        return jsonify({"success": False, "error": "Forbidden"}), 403
    body = safejson()
    value = str(safe(body.get("value"), ""))[:1000]
    with state["awaiting_input_lock"]:
        if not state.get("awaiting_input", False):
            return jsonify({"success": False, "error": "Run is not awaiting input"}), 409
    proc = state.get("proc")
    if not proc or proc.poll() is not None:
        return jsonify({"success": False, "error": "Run is not active"}), 410
    fifo = state.get("fifo")
    if not fifo or not os.path.exists(fifo):
        return jsonify({"success": False, "error": "Input pipe not available"}), 410
    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(value + '\n')
        with state["awaiting_input_lock"]:
            state["awaiting_input"] = False
        return jsonify({"success": True})
    except OSError as e:
        if e.errno in (errno.ENXIO, errno.EAGAIN, errno.EWOULDBLOCK):
            return jsonify({"success": False, "error": "Input reader is not ready"}), 409
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/run-stream/<run_id>/cancel", methods=["POST"])
def run_stream_cancel(run_id):
    with _active_runs_lock:
        state = _active_runs.get(run_id)
    if state and state.get("session_id") != get_session_id():
        return jsonify({"success": False, "error": "Forbidden"}), 403
    _cleanup_run(run_id)
    return jsonify({"success": True})


# ==========================
# CURRENT EXECUTION POSITION (mid-run polling for live mode)
# ==========================

@app.route("/run-stream/<run_id>/position", methods=["GET"])
def run_stream_position(run_id):
    """Return whether the run is awaiting input. Used by the heartbeat UI."""
    with _active_runs_lock:
        state = _active_runs.get(run_id)
    if not state:
        return jsonify({"success": False, "alive": False})
    if state.get("session_id") != get_session_id():
        return jsonify({"success": False, "error": "Forbidden"}), 403
    proc = state.get("proc")
    alive = proc is not None and proc.poll() is None
    with state["awaiting_input_lock"]:
        awaiting = state.get("awaiting_input", False)
    return jsonify({
        "success": True,
        "alive": alive,
        "awaiting_input": awaiting,
        "elapsed_ms": int((time.time() - state.get("started_at", time.time())) * 1000),
    })


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
    sandbox = get_sandbox(get_session_id())
    result = sandbox.write(filepath, content)
    return jsonify(result)

@app.route("/fs/read", methods=["POST"])
def fs_read():
    body = safejson()
    filepath = safe(body.get("path"), "")
    if not filepath:
        return jsonify({"success": False, "error": "Path is required"}), 400
    sandbox = get_sandbox(get_session_id())
    result = sandbox.read(filepath)
    return jsonify(result)

@app.route("/fs/delete", methods=["POST"])
def fs_delete():
    body = safejson()
    filepath = safe(body.get("path"), "")
    if not filepath:
        return jsonify({"success": False, "error": "Path is required"}), 400
    sandbox = get_sandbox(get_session_id())
    result = sandbox.delete(filepath)
    return jsonify(result)

@app.route("/fs/list", methods=["POST"])
def fs_list():
    body = safejson()
    dirpath = safe(body.get("path"), ".")
    sandbox = get_sandbox(get_session_id())
    result = sandbox.list_files(dirpath)
    return jsonify(result)

@app.route("/fs/info", methods=["GET"])
def fs_info():
    sandbox = get_sandbox(get_session_id())
    result = sandbox.get_workspace_info()
    return jsonify(result)

# ==========================
# EXECUTION TRACE (for playback)
# ==========================

@app.route("/execution-trace", methods=["GET"])
def get_execution_trace():
    """Get the last execution trace for playback.

    Reads under the session lock and returns a snapshot copy so a concurrent
    /run that's mid-write can't return a partially-mutated trace.
    """
    session_id = get_session_id()
    with _session_traces_lock:
        if session_id not in _session_traces:
            _session_traces[session_id] = _make_session_storage()
        storage = _session_traces[session_id]
        storage['last_accessed'] = time.time()
        # Snapshot under the lock — list copy is shallow but trace events are
        # immutable dicts so this is safe.
        trace = list(storage.get('last_trace', []) or [])
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
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

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

def _validate_quiz_response(parsed: dict) -> Optional[str]:
    """Validate a quiz dict from the LLM. Returns None if valid, error string if not.
    Defends against the LLM returning malformed or partial JSON that would crash
    the frontend or confuse the learner. Restricts to exactly 3 options (A/B/C)
    so the frontend regex can match cleanly."""
    if not isinstance(parsed, dict):
        return "Quiz response was not a dictionary"
    if not parsed.get("question") or not isinstance(parsed["question"], str):
        return "Quiz missing valid question"
    options = parsed.get("options", [])
    if not isinstance(options, list) or len(options) != 3:
        return "Quiz must have exactly 3 options"
    answer = parsed.get("answer", "")
    if not isinstance(answer, str) or answer.upper() not in ("A", "B", "C"):
        return "Quiz answer must be A, B, or C"
    if not parsed.get("explanation") or not isinstance(parsed.get("explanation"), str):
        return "Quiz missing explanation"
    parsed["question"] = parsed["question"].strip()
    parsed["explanation"] = parsed["explanation"].strip()
    normalized = []
    for idx, option in enumerate(options):
        if not isinstance(option, str) or not option.strip():
            return "Quiz option was empty"
        clean = re.sub(r'^\s*[\(\[]?[A-Da-d][\)\].:]?\s*', '', option).strip()
        normalized.append(f"{chr(ord('A') + idx)}: {clean}")
    parsed["options"] = normalized
    return None


def _strip_code_fences(text: str) -> str:
    return re.sub(r'^\s*```(?:python)?\s*|\s*```\s*$', '', text.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()


@app.route("/mentor/quiz", methods=["POST"])
def mentor_quiz():
    body = safejson()
    topic = _safe_text(body.get("topic"), "Python basics", limit=MAX_LEARNING_TOPIC_SIZE + 1).strip() or "Python basics"
    if len(topic) > MAX_LEARNING_TOPIC_SIZE:
        return jsonify({"success": False, "error": "Quiz topic is too long"}), 413
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
    raw = call_gemini(system, user, temperature=0.4, language=language)

    # Detect AI-disabled / error responses before attempting JSON parse
    if _is_ai_service_message(raw):
        return jsonify({
            "success": False,
            "error": "Quiz unavailable: AI not configured. Try the bug challenge or tutorial instead.",
        })

    try:
        clean = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
        parsed = json.loads(clean)
    except Exception:
        return jsonify({
            "success": False,
            "error": "AI returned a malformed quiz. Try saying 'quiz me' again, or try a different topic.",
        })

    validation_error = _validate_quiz_response(parsed)
    if validation_error:
        return jsonify({
            "success": False,
            "error": f"AI returned an incomplete quiz ({validation_error}). Try again.",
        })

    # Normalize answer to uppercase for the frontend matcher
    parsed["answer"] = parsed["answer"].upper()
    return jsonify({"success": True, "quiz": parsed})


@app.route("/mentor/explain", methods=["POST"])
def mentor_explain():
    body = safejson()
    concept = _safe_text(body.get("concept"), "variables", limit=MAX_LEARNING_TOPIC_SIZE + 1).strip() or "variables"
    if len(concept) > MAX_LEARNING_TOPIC_SIZE:
        return jsonify({"success": False, "error": "Concept is too long"}), 413
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

    # Reject empty or near-empty responses so the frontend doesn't speak silence.
    if not explanation or not explanation.strip() or len(explanation.strip()) < 20:
        return jsonify({
            "success": False,
            "error": "AI returned an incomplete explanation. Try asking again, or try a different concept name."
        })
    if _is_ai_service_message(explanation):
        return jsonify({
            "success": False,
            "error": explanation,
        })
    return jsonify({"success": True, "explanation": explanation})


@app.route("/explain-diff", methods=["POST"])
def explain_diff():
    body = safejson()
    code = safe(body.get("code"), "")
    previous_output = str(safe(body.get("previous_output"), ""))
    current_output = str(safe(body.get("current_output"), ""))
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE or len(previous_output) > MAX_CODE_SIZE or len(current_output) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Request too large"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked
    if not previous_output and not current_output:
        return jsonify({"success": False, "error": "No outputs to compare"}), 400

    diff = _compute_output_diff(previous_output, current_output)
    if diff.get("identical"):
        return jsonify({"success": True, "explanation": "The output did not change.", "diff": diff})

    system = (
        "You explain Python output differences to a blind beginner. "
        "Use at most two short sentences. Common causes include random values, "
        "time-dependent code, dict or set ordering, input values, or file state."
    )
    user = (
        f"Code:\n```python\n{code[:MAX_CODE_SIZE]}\n```\n\n"
        f"Previous output:\n{previous_output[:4000]}\n\n"
        f"Current output:\n{current_output[:4000]}\n\n"
        f"Diff summary: {diff.get('summary', '')}"
    )
    explanation = call_gemini(system, user, temperature=0.2, language=language)
    if _is_ai_service_message(explanation):
        explanation = (
            f"{diff.get('summary', 'The output changed.')} "
            "Check for changed input values, random numbers, time-based code, or data structures whose order can vary."
        )
    return jsonify({"success": True, "explanation": explanation, "diff": diff})


def _validate_bug_challenge(parsed: dict) -> Optional[str]:
    """Validate a bug challenge dict. Critical: the buggy code must be valid
    enough to load into the editor (even if it doesn't run), and the fixed
    code must actually compile, otherwise the student gets a 'fix' that's
    worse than the original."""
    if not isinstance(parsed, dict):
        return "Challenge was not a dictionary"
    for key in ("code", "hint", "bug", "fixed"):
        if not parsed.get(key) or not isinstance(parsed[key], str):
            return f"Challenge missing or invalid field: {key}"
        if len(parsed[key]) > MAX_CODE_SIZE:
            return f"Challenge field too large: {key}"
    parsed["code"] = _strip_code_fences(parsed["code"])
    parsed["fixed"] = _strip_code_fences(parsed["fixed"])
    if _looks_like_non_python_code(parsed["code"]) or _looks_like_non_python_code(parsed["fixed"]):
        return "Challenge included non-Python code"
    # The fixed code must at least parse — otherwise the LLM gave us garbage
    try:
        compile(parsed["fixed"], "<challenge>", "exec")
    except SyntaxError:
        return "AI's 'fixed' code has syntax errors"
    return None


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
    raw = call_gemini(system, user, temperature=0.5, language=language)

    if _is_ai_service_message(raw):
        return jsonify({
            "success": False,
            "error": "Bug challenge unavailable: AI not configured. Try the tutorial or write your own code instead.",
        })

    try:
        clean = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
        parsed = json.loads(clean)
    except Exception:
        return jsonify({
            "success": False,
            "error": "AI returned a malformed challenge. Try again.",
        })

    validation_error = _validate_bug_challenge(parsed)
    if validation_error:
        return jsonify({
            "success": False,
            "error": f"AI generated an invalid challenge ({validation_error}). Try saying 'bug challenge' again.",
        })

    return jsonify({"success": True, "challenge": parsed})


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
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "127.0.0.1")
    start_background_services()
    app.run(debug=False, host=host, port=port)

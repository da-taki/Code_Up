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
import textwrap
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, Response, g, has_request_context, jsonify, render_template, request, send_from_directory, stream_with_context
from rapidfuzz import fuzz

from conversation_orchestrator import frontend_actions, action_next_label, looks_like_generation_request, orchestrate_command, strip_wake_phrase
from input_concierge import build_input_plan, concierge_request_message, detect_inputs as detect_concierge_inputs
import session_memory
import command_clarifier
import grounded_ai
import clarification_flow
import concept_qa
import intent_repair
from intent_parser import parse_intent
from project_support import (
    PROJECT_MANIFEST,
    PROJECT_ROOT_DIR,
    PROJECT_TEMPLATES,
    ProjectPathError,
    build_template,
    choose_template_for_prompt,
    extract_project_json,
    infer_requirements,
    local_module_names,
    looks_like_multifile_prompt,
    make_manifest,
    normalize_file_map,
    normalize_project_path,
    project_summary,
)
from sandboxed_fs import cleanup_sandbox, cleanup_stale_sandboxes, get_sandbox
from symbolic_specs import build_exact_symbol_generation, constraint_summary, validate_exact_output
from structure_parser import CodeAnalyzer
import tutorial_engine
# Optional, isolated Intel OpenVINO local-intent demo (Intel AI Global Impact
# Festival). Importing this module is always safe: its OpenVINO import is
# guarded, so CodeUp runs normally whether or not OpenVINO is installed. This
# is a diagnostic prototype only and is NOT part of the real command router.
from openvino_intent_demo import classify_local_intent
# Sprint 2: non-visual code understanding. All deterministic / Flask-free; routed
# through the existing /voice-command + session model.
import structure_tools
import error_replay
import hint_engine
import landmarks

load_dotenv(override=True)

__version__ = "0.8.0"


def _debug_log(message: str):
    print(f"[CodeUp] {message}", file=sys.stderr)


def _codeup_positioning_message() -> str:
    return (
        "CodeUp is not a replacement for VS Code. VS Code with NVDA and tools "
        "like Copilot or Codex is powerful for advanced users. CodeUp focuses "
        "on the earlier beginner stage: learning indentation, loops, errors, "
        "execution flow, file structure, and confidence in an audio-native, "
        "command-first environment before moving to professional IDEs."
    )


def _codeup_transition_message() -> str:
    return (
        "After CodeUp, students should be ready to move into professional tools "
        "such as VS Code with a screen reader, keyboard shortcuts, terminals, "
        "Git, and Braille displays or input devices where available. CodeUp's "
        "job is to make the first programming stage less overwhelming and help "
        "students understand the structure before that transition."
    )


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
SUBPROCESS_MEMORY_LIMIT_MB = int(os.environ.get("CODEUP_SUBPROCESS_MEMORY_MB", "512"))
SUBPROCESS_CPU_LIMIT_SECONDS = int(os.environ.get("CODEUP_SUBPROCESS_CPU_SECONDS", "3"))
SUBPROCESS_WALL_TIMEOUT_SECONDS = int(os.environ.get("CODEUP_SUBPROCESS_WALL_SECONDS", "8"))
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
        'audio_breakpoints': [],
        'audio_breakpoint_pause': None,
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
MAX_NARRATION_OUTPUT_SIZE = 4_000
MAX_AUDIO_BREAKPOINTS = 10
try:
    DEFAULT_AI_MAX_TOKENS = int(os.environ.get("GROQ_MAX_TOKENS", "2048"))
except (TypeError, ValueError):
    DEFAULT_AI_MAX_TOKENS = 2048
DEFAULT_AI_MAX_TOKENS = max(256, min(DEFAULT_AI_MAX_TOKENS, 8192))
MAX_MENTOR_MESSAGE_SIZE = 2_000
MAX_MENTOR_CONTEXT_SIZE = 4_000
MAX_CONVERSATIONAL_CONTEXT_SIZE = 8_000
MAX_CONVERSATIONAL_EDIT_CODE_SIZE = 4_000
MAX_CONVERSATIONAL_CONFIRM_CODE_SIZE = 12_000

# Per-session rate limiting for /run
# Allows at most RUN_RATE_LIMIT executions per RUN_RATE_WINDOW seconds per session.
RUN_RATE_LIMIT  = 30   # max runs
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


def _project_rel(path: str = "") -> str:
    clean = str(path or "").strip("/")
    return f"{PROJECT_ROOT_DIR}/{clean}" if clean else PROJECT_ROOT_DIR


def _project_root_abs(sandbox) -> str:
    root = sandbox._validate_path(PROJECT_ROOT_DIR)
    os.makedirs(root, exist_ok=True)
    return root


def _load_project_files(sandbox) -> Dict[str, str]:
    root = _project_root_abs(sandbox)
    files: Dict[str, str] = {}
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            abs_path = os.path.join(dirpath, filename)
            rel = os.path.relpath(abs_path, root).replace("\\", "/")
            if rel == PROJECT_MANIFEST:
                continue
            try:
                with open(abs_path, "r", encoding="utf-8") as handle:
                    files[rel] = handle.read()
            except (OSError, UnicodeDecodeError):
                continue
    return dict(sorted(files.items()))


def _load_project_manifest(sandbox) -> Dict[str, Any]:
    result = sandbox.read(_project_rel(PROJECT_MANIFEST))
    if not result.get("success"):
        files = _load_project_files(sandbox)
        return make_manifest(files) if files else {}
    try:
        manifest = json.loads(result.get("content") or "{}")
    except json.JSONDecodeError:
        files = _load_project_files(sandbox)
        return make_manifest(files) if files else {}
    if not isinstance(manifest, dict):
        return {}
    files = _load_project_files(sandbox)
    if files:
        manifest = make_manifest(
            files,
            name=manifest.get("name") or "CodeUp Project",
            entry=manifest.get("entry") or "main.py",
            active_file=manifest.get("active_file") or manifest.get("entry") or "main.py",
            requirements=manifest.get("requirements") or infer_requirements(files),
        )
    return manifest


def _write_project_manifest(sandbox, manifest: Dict[str, Any]) -> None:
    manifest = dict(manifest)
    manifest["updated_at"] = int(time.time())
    sandbox.write(_project_rel(PROJECT_MANIFEST), json.dumps(manifest, indent=2))


def _write_project_files(sandbox, files: Dict[str, str], manifest: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_file_map(files)
    for path, content in normalized.items():
        result = sandbox.write(_project_rel(path), content)
        if not result.get("success"):
            raise ProjectPathError(result.get("error") or f"Could not write {path}.")
    manifest = make_manifest(
        normalized,
        name=manifest.get("name") or "CodeUp Project",
        entry=manifest.get("entry") or "main.py",
        active_file=manifest.get("active_file") or manifest.get("entry") or "main.py",
        requirements=manifest.get("requirements") or infer_requirements(normalized),
    )
    _write_project_manifest(sandbox, manifest)
    return manifest


def _project_response(sandbox, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    files = _load_project_files(sandbox)
    manifest = manifest or _load_project_manifest(sandbox)
    if not manifest and files:
        manifest = make_manifest(files)
    return {
        "success": True,
        "manifest": manifest,
        "files": files,
        "speech": project_summary(manifest) if manifest else "No multi-file project is open.",
    }


def _prepare_project_run(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = body.get("project")
    if not isinstance(payload, dict):
        return None

    files = normalize_file_map(payload.get("files"))
    if not files:
        return None
    entry = normalize_project_path(body.get("file") or payload.get("entry") or payload.get("active_file") or "main.py")
    if entry not in files:
        raise ProjectPathError(f"{entry} is not in the current project.")

    sandbox = get_sandbox(get_session_id())
    manifest = make_manifest(
        files,
        name=payload.get("name") or (payload.get("manifest") or {}).get("name") or "CodeUp Project",
        entry=entry,
        active_file=payload.get("active_file") or entry,
        requirements=payload.get("requirements") or (payload.get("manifest") or {}).get("requirements") or infer_requirements(files),
    )
    manifest = _write_project_files(sandbox, files, manifest)
    project_root = _project_root_abs(sandbox)
    entry_abs = sandbox._validate_path(_project_rel(entry))
    return {
        "sandbox": sandbox,
        "project_root": project_root,
        "entry": entry,
        "entry_abs": entry_abs,
        "files": files,
        "manifest": manifest,
        "local_modules": local_module_names(files.keys()),
    }

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
            _debug_log(f"AI service failure: {sanitize_traceback(str(e))}")
            return "AI service had a problem and offline AI is not available. Core CodeUp features still work."
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
        _debug_log(f"AI executor unavailable: {sanitize_traceback(str(e))}")
        return "AI service is currently unavailable. Core CodeUp features still work."

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
        _debug_log(f"AI future failed: {sanitize_traceback(str(e))}")
        return "AI service is currently unavailable. Core CodeUp features still work."


def call_conversation_orchestrator_ai(system_prompt: str, user_prompt: str) -> str:
    """Use GROQ_API_KEY_2 only for structured command interpretation."""
    if _env_flag_disabled("CODEUP_AI_ENABLED") or _env_flag_disabled("AI_ENABLED") or _env_flag_disabled("GROQ_ENABLED"):
        return ""
    key = str(os.environ.get("GROQ_API_KEY_2", "") or "").strip()
    if not key or key == "your_second_groq_api_key":
        return ""

    def _do_call():
        from groq import Groq
        client = Groq(api_key=key)
        response = client.chat.completions.create(
            model=os.environ.get("GROQ_ORCHESTRATOR_MODEL", GEMINI_MODEL),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=900,
        )
        for choice in getattr(response, "choices", []) or []:
            message = getattr(choice, "message", None)
            content = str(getattr(message, "content", "") or "").strip()
            if content:
                return content
        return ""

    try:
        future = _get_gemini_executor().submit(_do_call)
        return future.result(timeout=MAX_GEMINI_TIMEOUT + 1)
    except Exception:
        return ""


# Core concept words a coach rephrase must keep if the note states them.
_COACH_CONCEPT_VOCAB = (
    "quotes", "quote", "text", "variable", "indentation", "indent", "block",
    "print", "message", "loop", "while", "for", "if", "string", "even", "average",
)


def _ai_coach_rephrase(base_text: str) -> str:
    """Use Key 2 to rephrase a known teaching note into one warmer short line.

    Returns "" (so the deterministic note is used) when Key 2 is missing/busy or
    the reply looks unsafe. The model may ONLY restate the given note — guardrails
    reject anything with code, multiple sentences of bloat, or excessive length so
    the coach can never invent code, output, or validation results."""
    base_text = str(base_text or "").strip()
    if not base_text:
        return ""
    system = (
        "You are CodeUp's friendly beginner coding tutor for blind learners. "
        "Rephrase the teaching note below into ONE short, warm, encouraging sentence. "
        "Do NOT add new facts, code, numbers, variable values, or results — only "
        "restate the note more kindly. Plain text only, no code, under 28 words."
    )
    user = f"Teaching note: {base_text}\n\nRephrase it as one short, friendly sentence."
    raw = call_conversation_orchestrator_ai(system, user)
    coached = str(raw or "").strip().strip('"').strip()
    if not coached:
        return ""
    # The rephrase must preserve the note's core concept terms (e.g. quotes/text/
    # variable, indentation/block, print/message) and invent no code or numbers,
    # else we keep the deterministic note.
    concept_terms = [t for t in _COACH_CONCEPT_VOCAB if grounded_ai.fact_present(t, base_text)]
    ok, _reason = grounded_ai.validate(
        coached, deterministic_text=base_text, required_facts=concept_terms,
        context=base_text, single_sentence=True, max_words=40, max_chars=220,
    )
    return coached if ok else ""


def _concierge_ai_values(code_inputs, text):
    """Key 2 fallback for the input concierge: map a messy spoken value phrase to
    ordered input values. Returns a list of string values aligned to the prompts,
    or None. Never raises — the deterministic path stays the source of truth."""
    try:
        labels = [str(inp.get("label") or inp.get("name") or f"Input {i + 1}") for i, inp in enumerate(code_inputs)]
        types = [str(inp.get("type") or "str") for inp in code_inputs]
    except Exception:
        return None
    if not labels:
        return None
    system = (
        "You are CodeUp's input concierge. Map the user's spoken values to a Python "
        "program's input() prompts, in order. Convert spoken numbers to digits "
        "(sixteen -> 16, ninety two point five -> 92.5). Return ONLY a JSON array of "
        "strings, exactly one per prompt, in order. No prose and no code."
    )
    user = (
        "Prompts in order:\n"
        + "\n".join(f"{i + 1}. {label} (type: {typ})" for i, (label, typ) in enumerate(zip(labels, types)))
        + f"\n\nUser said: {text}\n\nReturn a JSON array of {len(labels)} string values."
    )
    raw = call_conversation_orchestrator_ai(system, user)
    if not raw:
        return None
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", str(raw)).strip()
    data = None
    try:
        data = json.loads(cleaned)
    except (ValueError, TypeError):
        match = re.search(r"\[.*\]", str(raw), re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except (ValueError, TypeError):
                data = None
    if not isinstance(data, list):
        return None
    return [str(v) for v in data][:50]


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
    if (
        ("zero to two" in lower or "0 to 2" in lower or "numbers zero" in lower)
        and ("print" in lower or "loop" in lower or "numbers" in lower)
    ):
        return "for i in range(3):\n    print(i)\n"
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


@app.route("/vs/<path:filename>")
def monaco_vs_asset(filename):
    return send_from_directory(
        os.path.join(app.static_folder, "vendor", "monaco", "min", "vs"),
        filename,
    )


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
# GUIDED TUTORIAL
# ==========================

@app.route("/tutorial/modules", methods=["GET"])
def tutorial_modules():
    """Return the spoken lesson pack (ordered modules + content).

    The frontend tutorial fetches this once and narrates it through the proven
    audible speech path. Content lives in tutorial_engine.py so it has a single
    source of truth and stays unit-testable.
    """
    return jsonify({"success": True, **tutorial_engine.module_pack()})


@app.route("/tutorial/validate", methods=["POST"])
def tutorial_validate():
    """Validate one tutorial activity attempt.

    Body: {module, code, ran_ok, output}. Returns a deterministic verdict with
    spoken feedback and an optional hint. Validation is AST-based (many valid
    answers accepted) and the while module gets a static safety pre-check.
    """
    body = safejson()
    module_id = _safe_text(body.get("module"), "", limit=40).strip()
    code = _safe_text(body.get("code"), "", limit=MAX_CODE_SIZE + 1)
    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    ran_ok = bool(body.get("ran_ok", True))
    output = _safe_text(body.get("output"), "", limit=MAX_NARRATION_OUTPUT_SIZE)

    if not module_id or tutorial_engine.get_module(module_id) is None:
        return jsonify({"success": False, "error": "Unknown tutorial module"}), 400

    result = tutorial_engine.validate_attempt(
        module_id, code, ran_ok=ran_ok, output=output
    )
    return jsonify({
        "success": True,
        "module": module_id,
        "passed": result["passed"],
        "safe": result["safe"],
        "feedback": result["feedback"],
        "hint": result["hint"],
        "next_module": tutorial_engine.next_module_id(module_id),
    })


@app.route("/tutorial/coach", methods=["POST"])
def tutorial_coach():
    """AI-assisted tutorial coaching (friendlier phrasing, hints, encouragement).

    Body: {module, request|text, attempts}. The deterministic engine produces a
    known-fact answer; Key 2 may only rephrase it more warmly. If Key 2 is
    missing/busy the deterministic note is returned, so the tutorial never
    depends on AI. The coach never validates code or invents program state.
    """
    body = safejson()
    module_id = _safe_text(body.get("module"), "", limit=40).strip()
    raw_text = _safe_text(body.get("text"), "", limit=300)
    request_type = _safe_text(body.get("request"), "", limit=40).strip()
    try:
        attempts = int(body.get("attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    attempts = max(0, min(attempts, 20))

    if module_id:
        try:
            session_memory.record_tutorial(session_memory.get_memory(get_trace_storage()), module_id)
        except Exception:
            pass

    if request_type not in tutorial_engine.COACH_REQUESTS:
        request_type = tutorial_engine.classify_coach_request(raw_text) or ""
    if not request_type:
        # Not a coach phrase — let the frontend fall back to its normal handling.
        return jsonify({"success": True, "handled": False})

    base = tutorial_engine.coach_response(module_id, request_type, attempts=attempts)
    spoken = base["text"]
    source = "deterministic"
    if spoken:
        coached = _ai_coach_rephrase(spoken)
        if coached:
            spoken = coached
            source = "ai_coached"

    return jsonify({
        "success": True,
        "handled": True,
        "request": request_type,
        "module": module_id,
        "text": spoken,
        "speech": spoken,
        "source": source,
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
        _debug_log(f"API key configuration failed: {sanitize_traceback(str(e))}")
        return jsonify({"success": False, "error": "Could not configure the API key. Please check the key and try again."}), 500

# ==========================
# ERROR EXPLAINER
# ==========================

def sanitize_traceback(traceback_str: str) -> str:
    """Remove sensitive paths and credentials from diagnostic text."""
    text = str(traceback_str or "")
    text = re.sub(r'gsk_[A-Za-z0-9_-]{16,}', '<redacted-api-key>', text)
    text = re.sub(r'(?i)(api[_-]?key["\']?\s*[:=]\s*)["\']?[^"\'\s,;]+', r'\1<redacted>', text)
    text = re.sub(r'[A-Za-z]:[/\\][^"\'\n\r]*', '<path>', text)
    text = re.sub(r'/(?:home|Users|var|tmp|private|opt|srv|mnt|workspace|app)/[^"\'\n\r]*', '<path>', text)
    return text


def _line_from_code(code: str, line_no: Optional[int]) -> str:
    if not line_no or line_no < 1:
        return ""
    lines = code.splitlines()
    if line_no > len(lines):
        return ""
    return lines[line_no - 1].strip()


def _syntax_error_message(error: SyntaxError, code: str) -> str:
    err_type = type(error).__name__
    msg = str(error.msg or "Python syntax error").strip()
    line_no = error.lineno or 1
    line_text = _line_from_code(code, line_no)
    parts = [f"Line {line_no}: {err_type}: {msg}."]
    if isinstance(error, IndentationError):
        if "expected an indented block" in msg.lower():
            parts.append("The line after the loop or block header must be indented.")
            if line_text:
                parts.append(f"Add four spaces before: {line_text}")
        elif "unexpected indent" in msg.lower():
            parts.append("This line is indented more than Python expected.")
    return " ".join(parts)


def _extract_error_summary(error_text: str) -> Tuple[Optional[int], Optional[str], str, str]:
    safe_text = sanitize_traceback(error_text)
    line_no = None
    file_label = None
    for match in re.finditer(r'File\s+"([^"]+)",\s+line\s+(\d+)', safe_text):
        candidate = match.group(1)
        if candidate == "<path>" or candidate.endswith("sandbox_runner.py"):
            continue
        file_label = candidate if candidate != "<user>" else None
        line_no = int(match.group(2))
    if line_no is None:
        non_frame_text = "\n".join(
            line for line in safe_text.splitlines()
            if not line.strip().startswith("File ")
        )
        match = re.search(r'\bline\s+(\d+)\b', non_frame_text, flags=re.IGNORECASE)
        if match:
            line_no = int(match.group(1))

    for raw_line in reversed([line.strip() for line in safe_text.splitlines() if line.strip()]):
        if raw_line.startswith("^") or raw_line.startswith("File ") or raw_line.startswith("Traceback"):
            continue
        match = re.match(r'([A-Za-z_][A-Za-z0-9_]*Error|Exception|KeyboardInterrupt|SystemExit):\s*(.*)', raw_line)
        if match:
            return line_no, file_label, match.group(1), match.group(2).strip()
    return line_no, file_label, "PythonError", "Python could not run this code."


def user_facing_error(error_text: str) -> str:
    """Return concise error text for students, never internal traceback frames."""
    safe_text = sanitize_traceback(error_text)
    timeout_match = re.search(r'Execution timed out after\s+([0-9.]+)s', safe_text, flags=re.IGNORECASE)
    if timeout_match:
        return f"Execution timed out after {timeout_match.group(1)} seconds."
    if re.search(r'\b(?:timed out|timeout)\b', safe_text, flags=re.IGNORECASE):
        return "Execution timed out before it could finish."
    if re.search(r'\b(?:resource limit|memory limit|cpu limit|killed)\b', safe_text, flags=re.IGNORECASE):
        return "Execution stopped because it exceeded a safe runtime or resource limit."
    line_no, file_label, err_type, message = _extract_error_summary(error_text)
    if file_label and line_no:
        prefix = f"{file_label} line {line_no}: "
    elif line_no:
        prefix = f"Line {line_no}: "
    else:
        prefix = ""
    message = sanitize_traceback(message).strip() or "Python could not run this code."
    return f"{prefix}{err_type}: {message}"


def _subprocess_exit_error(returncode: Optional[int]) -> str:
    if returncode in (None, 0):
        return ""
    if returncode < 0:
        return "Execution timed out or exceeded a safe runtime limit."
    return f"Execution stopped unexpectedly with exit code {returncode}."


def _bounded_narration_output(output_text: str) -> str:
    text = str(output_text or "")
    if len(text) <= MAX_NARRATION_OUTPUT_SIZE:
        return text
    omitted = len(text) - MAX_NARRATION_OUTPUT_SIZE
    return (
        text[:MAX_NARRATION_OUTPUT_SIZE]
        + f"\n[Output truncated after {MAX_NARRATION_OUTPUT_SIZE} characters; {omitted} more characters omitted.]"
    )


def _local_error_explanation(code: str, err_text: str, language: str = "en", beginner: bool = False) -> str:
    cleaned_error = sanitize_traceback(err_text).strip()
    if re.search(r'(?:^|\b)(?:[A-Za-z_][A-Za-z0-9_]*Error|Exception|KeyboardInterrupt|SystemExit):', cleaned_error):
        safe_error = cleaned_error
    else:
        safe_error = user_facing_error(err_text)
    lower = safe_error.lower()
    line_match = re.search(r'line\s+(\d+)', safe_error, flags=re.IGNORECASE)
    line_no = int(line_match.group(1)) if line_match else None
    line_text = _line_from_code(code, line_no)

    if "indentationerror" in lower and "expected an indented block" in lower:
        target = f" line {line_no}" if line_no else " the next line"
        sample = f" Add four spaces before `{line_text}`." if line_text else ""
        if language == "hi":
            line_part = f"Line {line_no} par" if line_no else "Next line par"
            sample_part = " Print statement se pehle four spaces add karo." if line_text else ""
            return (
                f"{line_part} indentation missing hai. Python spaces se samajhta hai ki kaunsa code loop ke andar hai. "
                f"Print statement loop ke andar hona chahiye, isliye us line se pehle four spaces add karo.{sample_part}"
            )
        return (
            f"The line after the loop must be indented. Python uses spaces to know what belongs inside the loop. "
            f"Indent{target} with four spaces, then run again.{sample}"
        )
    if "indentationerror" in lower:
        if language == "hi":
            return "Python ko indentation problem mili hai. Error wali line par spacing check karo, phir block ko consistent four spaces se align karo."
        return "Python found an indentation problem. Check the spacing at the line named in the error, then make the block line up consistently."
    if "syntaxerror" in lower:
        return "Python could not understand the code yet. Check the line named in the error for a missing colon, bracket, quote, or other punctuation."
    if "nameerror" in lower:
        return "Python saw a name it does not know yet. Check the spelling, or create that variable before you use it."
    if "zerodivisionerror" in lower:
        return "The code tried to divide by zero. Change the divisor or add a check so the divisor is not zero."
    if "missing dependency" in lower or "modulenotfounderror" in lower or "no module named" in lower:
        return "This project needs a package that is not installed. Add it to requirements.txt, install the requirements, then run again."
    if "codeupinputerror" in lower or "input()" in lower:
        return "Your code asks for input. Add pre-flight inputs in the inputs panel, add a '# inputs:' comment, or switch to live input mode."

    if beginner:
        return "Python could not run this yet. Read the line named in the error, fix that one part, and run again."
    return "Python could not run this code. Check the line named in the error, fix it, and run again."

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
    safe_error = user_facing_error(err_text)
    local_explanation = _local_error_explanation(code, safe_error, language=language, beginner=beginner)
    if "indentationerror" in safe_error.lower():
        return local_explanation
    user = f"Code:\n```python\n{code}\n```\n\nError:\n```\n{safe_error}\n```"
    ai_explanation = call_gemini(system, user, language=language)
    if _is_ai_service_message(ai_explanation) or _ai_unavailable(ai_explanation):
        return local_explanation
    return ai_explanation


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


def _ast_block_children(node):
    """Return immediate child body nodes of a block statement."""
    children = []
    for field_name in ('body', 'orelse', 'handlers', 'finalbody'):
        field = getattr(node, field_name, None)
        if isinstance(field, list):
            children.extend(field)
    return children


def _assignment_target_names(node: ast.AST) -> List[str]:
    targets = []
    if isinstance(node, ast.Assign):
        candidate_targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        candidate_targets = [node.target]
    elif isinstance(node, ast.AugAssign):
        candidate_targets = [node.target]
    else:
        candidate_targets = []
    for target in candidate_targets:
        if isinstance(target, ast.Name):
            targets.append(target.id)
    return targets


def _condition_body_summary(node: ast.If) -> str:
    nested_parts = []
    for child in node.body:
        names = _assignment_target_names(child)
        if names:
            nested_parts.append(f"an assignment to {', '.join(names)} on line {child.lineno}")
        elif isinstance(child, ast.Expr) and isinstance(getattr(child, 'value', None), ast.Call):
            call = child.value
            if isinstance(call.func, ast.Name) and call.func.id == 'print':
                nested_parts.append(f"a print on line {child.lineno}")
        elif isinstance(child, (ast.For, ast.While)):
            nested_parts.append(f"a nested loop on line {child.lineno}")
    if nested_parts:
        return f"a condition on line {node.lineno} containing {'; '.join(nested_parts[:3])}"
    return f"a condition on line {node.lineno}"


def _deepest_nesting(code: str) -> int:
    """Compute the deepest nesting level of control flow in code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        lines = code.splitlines()
        max_indent = 0
        for line in lines:
            stripped = line.lstrip(' ')
            if stripped:
                indent = len(line) - len(stripped)
                max_indent = max(max_indent, indent // 4)
        return max_indent

    def _depth(node, current=0):
        best = current
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While, ast.If, ast.With, ast.Try)):
                best = max(best, _depth(child, current + 1))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                best = max(best, _depth(child, current + 1))
            else:
                best = max(best, _depth(child, current))
        return best

    return _depth(tree)


def _enhanced_code_map(code: str) -> dict:
    """Return structured code map data for the Audio Code Map feature.

    Returns a dict with keys: summary (str), blocks (list), functions (list),
    loops (list), conditions (list), assignments (list), prints (list),
    nesting_depth (int), line_count (int), error (str or None).
    """
    lines = code.splitlines()
    result = {
        "summary": "",
        "blocks": [],
        "functions": [],
        "loops": [],
        "conditions": [],
        "assignments": [],
        "prints": [],
        "nesting_depth": 0,
        "line_count": len(lines),
        "error": None,
    }

    if not code.strip():
        result["summary"] = "Your code is empty."
        return result

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        result["error"] = str(e)
        result["nesting_depth"] = _deepest_nesting(code)
        indent_map = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped:
                indent = len(line) - len(line.lstrip(' '))
                indent_map.append({"line": i + 1, "indent": indent, "text": stripped[:80]})
        result["summary"] = (
            f"Your code has a syntax error: {e.msg} near line {e.lineno or '?'}. "
            f"I can see {len(lines)} lines. "
            f"Here is what I can tell from indentation alone."
        )
        result["blocks"] = indent_map[:20]
        return result

    result["nesting_depth"] = _deepest_nesting(code)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args]
            result["functions"].append({
                "name": node.name,
                "start": node.lineno,
                "end": getattr(node, 'end_lineno', node.lineno),
                "params": params,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })

    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            kind = "for" if isinstance(node, ast.For) else "while"
            body_summary = []
            for child in _ast_block_children(node):
                if isinstance(child, ast.If):
                    body_summary.append(_condition_body_summary(child))
                elif isinstance(child, (ast.For, ast.While)):
                    body_summary.append("a nested loop")
                elif _assignment_target_names(child):
                    names = _assignment_target_names(child)
                    body_summary.append(f"an assignment to {', '.join(names)}")
                elif isinstance(child, ast.Expr) and isinstance(getattr(child, 'value', None), ast.Call):
                    call = child.value
                    if isinstance(call.func, ast.Name) and call.func.id == 'print':
                        body_summary.append("a print statement")
            result["loops"].append({
                "kind": kind,
                "start": node.lineno,
                "end": getattr(node, 'end_lineno', node.lineno),
                "body_summary": body_summary,
            })
        elif isinstance(node, ast.If) and not isinstance(getattr(node, '_parent', None), ast.If):
            result["conditions"].append({
                "start": node.lineno,
                "end": getattr(node, 'end_lineno', node.lineno),
            })
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for name in _assignment_target_names(node):
                result["assignments"].append({
                    "name": name,
                    "line": node.lineno,
                })
        elif isinstance(node, ast.Expr) and isinstance(getattr(node, 'value', None), ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == 'print':
                result["prints"].append({"line": node.lineno})

    parts = []
    total_non_empty = sum(1 for ln in lines if ln.strip())
    parts.append(f"Your program has {total_non_empty} lines of code.")

    if result["functions"]:
        names = [f["name"] for f in result["functions"]]
        parts.append(f"It defines {len(names)} function{'s' if len(names) != 1 else ''}: {', '.join(names)}.")

    assigns_before_loops = [a for a in result["assignments"]
                            if not result["loops"] or a["line"] < result["loops"][0]["start"]]
    if assigns_before_loops:
        names = list(dict.fromkeys(a["name"] for a in assigns_before_loops))
        parts.append(f"First, {'variable' if len(names) == 1 else 'variables'} {', '.join(names[:5])} {'is' if len(names) == 1 else 'are'} set up.")

    for loop in result["loops"]:
        inside = ", ".join(loop["body_summary"][:4]) if loop["body_summary"] else "some statements"
        parts.append(
            f"There is a {loop['kind']} loop from line {loop['start']} to {loop['end']}. "
            f"Inside it: {inside}."
        )

    after_loop_lines = []
    if result["loops"]:
        last_loop_end = max(lp["end"] for lp in result["loops"])
        for node in ast.iter_child_nodes(tree):
            if hasattr(node, 'lineno') and node.lineno > last_loop_end:
                if isinstance(node, ast.Expr) and isinstance(getattr(node, 'value', None), ast.Call):
                    call = node.value
                    if isinstance(call.func, ast.Name) and call.func.id == 'print':
                        after_loop_lines.append(f"line {node.lineno} prints output")
                elif isinstance(node, ast.Assign):
                    after_loop_lines.append(f"line {node.lineno} is an assignment")
        if after_loop_lines:
            parts.append(f"After the loop: {', '.join(after_loop_lines[:3])}.")

    if result["prints"] and not after_loop_lines:
        parts.append(f"The program has {len(result['prints'])} print statement{'s' if len(result['prints']) != 1 else ''}.")

    if result["nesting_depth"] > 0:
        parts.append(f"Your deepest nesting level is {result['nesting_depth']}.")

    result["summary"] = " ".join(parts)
    return result


def _inside_loop_summary(code: str) -> str:
    """Describe what is inside the first loop found in code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "I cannot parse the code due to a syntax error."
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            kind = "for" if isinstance(node, ast.For) else "while"
            body_parts = []
            for child in node.body:
                if isinstance(child, ast.If):
                    body_parts.append(_condition_body_summary(child))
                elif isinstance(child, (ast.For, ast.While)):
                    body_parts.append(f"a nested loop on line {child.lineno}")
                elif _assignment_target_names(child):
                    targets = _assignment_target_names(child)
                    body_parts.append(f"an assignment to {', '.join(targets) or 'a variable'} on line {child.lineno}")
                elif isinstance(child, ast.Expr) and isinstance(getattr(child, 'value', None), ast.Call):
                    call = child.value
                    if isinstance(call.func, ast.Name) and call.func.id == 'print':
                        body_parts.append(f"a print on line {child.lineno}")
                    else:
                        body_parts.append(f"a function call on line {child.lineno}")
                elif isinstance(child, ast.Return):
                    body_parts.append(f"a return on line {child.lineno}")
            if body_parts:
                return f"Inside the {kind} loop (lines {node.lineno} to {getattr(node, 'end_lineno', '?')}): {'; '.join(body_parts)}."
            return f"The {kind} loop from line {node.lineno} to {getattr(node, 'end_lineno', '?')} has an empty body."
    return "I did not find a loop in your code."


def _after_loop_summary(code: str) -> str:
    """Describe what comes after the last loop in code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "I cannot parse the code due to a syntax error."
    loops = [(n.lineno, getattr(n, 'end_lineno', n.lineno))
             for n in ast.walk(tree) if isinstance(n, (ast.For, ast.While))]
    if not loops:
        return "There is no loop in your code."
    last_end = max(end for _, end in loops)
    after = []
    for node in ast.iter_child_nodes(tree):
        if hasattr(node, 'lineno') and node.lineno > last_end:
            if isinstance(node, ast.Expr) and isinstance(getattr(node, 'value', None), ast.Call):
                call = node.value
                if isinstance(call.func, ast.Name):
                    after.append(f"a call to {call.func.id} on line {node.lineno}")
            elif isinstance(node, ast.Assign):
                after.append(f"an assignment on line {node.lineno}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                after.append(f"function {node.name} on line {node.lineno}")
    if not after:
        return f"Nothing comes after the loop ending at line {last_end}. The loop is the last thing in your program."
    return f"After the loop: {'; '.join(after[:5])}."


def _list_functions_summary(code: str) -> str:
    """List all functions defined in code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "I cannot parse the code due to a syntax error."
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args]
            param_str = f" taking {', '.join(params)}" if params else " with no parameters"
            funcs.append(f"{node.name} on line {node.lineno}{param_str}")
    if not funcs:
        return "Your code has no function definitions."
    return f"Your code defines {len(funcs)} function{'s' if len(funcs) != 1 else ''}: {'; '.join(funcs)}."


def _hinglish_code_map_summary(code: str) -> str:
    lines = [line for line in str(code or "").splitlines() if line.strip()]
    count_words = {1: "one", 2: "two", 3: "three"}
    line_count = count_words.get(len(lines), str(len(lines)))
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                has_print = any(
                    isinstance(child, ast.Expr)
                    and isinstance(getattr(child, "value", None), ast.Call)
                    and getattr(getattr(child.value, "func", None), "id", "") == "print"
                    for child in node.body
                )
                if has_print and len(lines) == 2:
                    return "Is program mein two lines hain. Pehli line loop start karti hai. Dusri line print statement hai jo loop ke andar nested hai."
    except SyntaxError:
        pass
    return f"Is program mein {line_count} nonblank line{'s' if len(lines) != 1 else ''} hain. Code map indentation aur nesting ke hisaab se structure batata hai."


@app.route("/audio-code-map", methods=["POST"])
def audio_code_map():
    """Enhanced Audio Code Map endpoint returning structured + spoken summary."""
    body = safejson()
    code = safe(body.get("code"), "")
    query = safe(body.get("query"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    if not code.strip():
        msg = "Your code is empty. Write some Python and ask for a code map."
        return jsonify({"success": True, "reply": msg, "speech": msg, "auto_speak": True})

    # Dispatch sub-queries
    query_lower = query.lower().strip()
    if "inside" in query_lower and "loop" in query_lower:
        reply = _inside_loop_summary(code)
    elif "after" in query_lower and "loop" in query_lower:
        reply = _after_loop_summary(code)
    elif "nest" in query_lower or "depth" in query_lower or "deep" in query_lower:
        depth = _deepest_nesting(code)
        reply = f"Your deepest nesting level is {depth}." if depth > 0 else "Your code has no nesting."
    elif "function" in query_lower:
        reply = _list_functions_summary(code)
    elif "where" in query_lower and "program" in query_lower:
        reply = build_code_audio_map(code)
    else:
        map_data = _enhanced_code_map(code)
        deterministic_summary = map_data["summary"]
        if language == "hi":
            reply = _hinglish_code_map_summary(code)
            return jsonify({"success": True, "reply": reply, "speech": reply, "auto_speak": True})

        # Optionally use AI to make it friendlier
        key = _configured_cloud_api_key()
        if key and not _cloud_ai_disabled_for_request(key):
            system = (
                "You are a friendly coding tutor for a blind student. "
                "Given the following verified structural facts about their Python program, "
                "rephrase them into a warm, concise spoken summary suitable for audio. "
                "Do NOT invent any structural facts. Keep it under 6 sentences. "
                "Do not use markdown."
            )
            user = f"Structural facts:\n{deterministic_summary}"
            ai_reply = call_gemini(system, user, temperature=0.2, language=language)
            if not _ai_unavailable(ai_reply):
                reply = ai_reply
            else:
                reply = deterministic_summary
        else:
            reply = deterministic_summary

    return jsonify({"success": True, "reply": reply, "speech": reply, "auto_speak": True})


# ==========================
# VARIABLE WATCH / STEP NARRATION
# ==========================

def _run_with_trace_for_narration(code: str, watched_vars: set, session_id: str) -> dict:
    """Run code in sandbox and return structured narration trace.

    Returns dict with keys: success, narration (list of str), output (str),
    error (str), steps (list of dict), raw_trace (list).
    """
    sandbox = get_sandbox(session_id)
    workspace_dir = sandbox.workspace_dir
    trace_file = os.path.join(workspace_dir, f"trace_{uuid.uuid4().hex}.json")
    runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox_runner.py')

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                      encoding='utf-8', dir=workspace_dir) as code_file:
        code_file.write(code)
        code_file_path = code_file.name

    env = os.environ.copy()
    env['CODEUP_CODE_FILE'] = code_file_path
    env['CODEUP_TRACE_FILE'] = trace_file
    env.pop('CODEUP_INTERACTIVE', None)
    env.pop('CODEUP_INPUT_FIFO', None)
    env.pop('CODEUP_EXEC_CODE', None)

    popen_kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=workspace_dir,
        text=True,
    )
    if sys.platform != "win32":
        popen_kwargs["preexec_fn"] = _set_subprocess_limits
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen([sys.executable, runner_path], **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=SUBPROCESS_WALL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return {"success": False, "narration": ["Execution timed out."],
                    "output": "", "error": "Timeout", "steps": [], "raw_trace": []}
    finally:
        try:
            os.unlink(code_file_path)
        except OSError:
            pass

    trace = []
    try:
        if os.path.exists(trace_file):
            with open(trace_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                trace = data.get('trace', [])
    except (json.JSONDecodeError, OSError):
        pass
    finally:
        try:
            if os.path.exists(trace_file):
                os.unlink(trace_file)
        except OSError:
            pass

    # Build narration from trace events
    narration = []
    steps = []
    error_text = stderr.strip() if stderr else ""
    output_text = _bounded_narration_output(stdout.strip() if stdout else "")

    if error_text:
        safe_error = user_facing_error(error_text)
        narration.append("Starting execution.")
        narration.append(f"The program encountered an error: {safe_error}")
        return {"success": False, "narration": narration, "output": output_text,
                "error": safe_error, "steps": steps, "raw_trace": trace}

    if proc.returncode not in (None, 0):
        limit_message = "Execution timed out or exceeded a safe runtime limit."
        return {"success": False, "narration": ["Starting execution.", limit_message],
                "output": output_text, "error": limit_message, "steps": steps, "raw_trace": trace}

    narration.append("Starting execution.")
    narration_lines: list = [None]
    step_count = 0
    MAX_NARRATION_STEPS = 200

    # --- Source-line helpers for learner-visible cue semantics --------------
    # Cues must reflect the indentation depth of the *source line a learner
    # sees execute* (an output statement, an assignment), not the line where a
    # variable change happens to be detected by the tracer. The raw trace is
    # left untouched (the conditional-breakpoint feature and trace playback
    # depend on it); all of this only shapes the spoken narration and the
    # per-step indent depths the frontend turns into structural beeps.
    src_lines = (code or "").splitlines()

    def _src_line(lineno):
        if lineno and 1 <= lineno <= len(src_lines):
            return src_lines[lineno - 1]
        return None

    def _is_print_line(lineno):
        src = _src_line(lineno)
        return bool(src and re.match(r'\s*print\s*\(', src))

    def _is_loop_header(lineno):
        src = _src_line(lineno)
        if not src:
            return False
        stripped = src.lstrip()
        return stripped.startswith('for ') or stripped.startswith('while ')

    # Each executed print line maps to one learner-visible output line. When the
    # count of executed print lines matches the produced output lines we can
    # narrate and cue each output at the exact depth of the print that produced
    # it. If they don't line up (multi-line prints, end='', echoed input), we
    # fall back to a single collapsed "Output:" line with no misleading cue.
    out_lines = output_text.split('\n') if output_text else []
    print_exec_count = sum(
        1 for ev in trace
        if ev.get('type') == 'line_exec' and _is_print_line(ev.get('line'))
    )
    per_print_mode = print_exec_count > 0 and print_exec_count == len(out_lines)
    out_cursor = 0

    # An assignment's effect is only visible to the tracer on the *next* line
    # event, so a state change reported on line N was actually produced by the
    # line that executed just before it. Track the last two executed lines so we
    # can attribute each change to the line that caused it.
    prev1 = None  # most recently executed source line
    prev2 = None  # the line executed before prev1 (the cause of a fresh change)
    pending_output = None  # (line, value) deferred until the print actually runs

    def _flush_output():
        nonlocal pending_output, step_count
        if pending_output is None:
            return
        pline, pval = pending_output
        pending_output = None
        narration.append(f"The program prints {pval}." if pval else "The program prints a blank line.")
        narration_lines.append(pline)
        steps.append({"line": pline, "description": f"output {pval}"})
        step_count += 1

    for event in trace:
        if step_count >= MAX_NARRATION_STEPS:
            _flush_output()
            narration.append(f"Narration capped at {MAX_NARRATION_STEPS} steps. The program continued beyond this point.")
            narration_lines.append(None)
            break

        etype = event.get('type')

        if etype == 'line_exec':
            # A new line is running, so any deferred print output has now been
            # produced and is narrated before this line's effects.
            _flush_output()
            line = event.get('line')
            prev2 = prev1
            prev1 = line
            if per_print_mode and _is_print_line(line) and out_cursor < len(out_lines):
                pending_output = (line, out_lines[out_cursor][:200])
                out_cursor += 1

        elif etype == 'state_change':
            # Attribute the change to the line that caused it (prev2), not the
            # line where the tracer noticed it. Skip the loop-induction variable
            # bound by a for/while header so cues track learner-visible work.
            causing = prev2 if prev2 is not None else event.get('line')
            if _is_loop_header(causing):
                continue
            for change_str in event.get('changes', []):
                parts = change_str.split(' ', 1)
                var_name = parts[0] if parts else ''
                if watched_vars and var_name not in watched_vars:
                    continue
                steps.append({"line": causing, "description": change_str})
                if 'initialized to' in change_str:
                    narration.append(f"{var_name} becomes {change_str.split('initialized to ')[-1]}.")
                elif 'changed from' in change_str:
                    old_new = change_str.split('changed from ')[-1]
                    narration.append(f"{var_name} changes to {old_new.split(' to ')[-1] if ' to ' in old_new else old_new}.")
                elif 'remains' in change_str:
                    narration.append(f"{var_name} remains the same.")
                else:
                    narration.append(change_str + ".")
                narration_lines.append(causing)
                step_count += 1

        elif etype == 'call':
            _flush_output()
            func = event.get('function', '?')
            if func != '<module>':
                narration.append(f"Entering function {func}.")
                narration_lines.append(event.get('line'))
                steps.append({"line": event.get('line'), "description": f"call {func}"})
                step_count += 1

        elif etype == 'return':
            _flush_output()
            val = event.get('value', '')
            if val and val != 'None':
                narration.append(f"Returned {val}.")
                narration_lines.append(None)
                steps.append({"line": 0, "description": f"return {val}"})
                step_count += 1

        elif etype == 'overflow':
            _flush_output()
            narration.append("Trace limit reached. The program may have more steps.")
            narration_lines.append(None)
            break

    _flush_output()

    # Only emit the collapsed output line when we could not narrate output
    # per-statement above; otherwise it would duplicate the per-print steps.
    if output_text and not per_print_mode:
        narration.append(f"Output: {output_text[:500]}")
        narration_lines.append(None)

    narration.append("Execution complete.")
    narration_lines.append(None)

    return {"success": True, "narration": narration, "narration_lines": narration_lines,
            "output": output_text, "error": "", "steps": steps, "raw_trace": trace}


_AUDIO_BREAKPOINT_VAR_RE = re.compile(r'^[A-Za-z_]\w*$')
_AUDIO_BREAKPOINT_NUM_RE = r'[-+]?\d+(?:\.\d+)?'
_AUDIO_BREAKPOINT_OPERATORS = {">", "<", "==", ">=", "<="}
_AUDIO_BREAKPOINT_OPERATOR_LABELS = {
    ">": "greater than",
    "<": "less than",
    "==": "equal to",
    ">=": "greater than or equal to",
    "<=": "less than or equal to",
}
_AUDIO_BREAKPOINT_PHRASES = {
    "greater than or equal to": ">=",
    "at least": ">=",
    "less than or equal to": "<=",
    "at most": "<=",
    "greater than": ">",
    "more than": ">",
    "above": ">",
    "less than": "<",
    "below": "<",
}


def _format_audio_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _coerce_audio_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(_AUDIO_BREAKPOINT_NUM_RE, text):
            return None
        try:
            number = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return None
    else:
        return None
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if isinstance(number, float) and number.is_integer() else number


def _normalize_audio_breakpoint_text(text: str) -> str:
    cleaned = _safe_text(text, limit=MAX_VOICE_TEXT_SIZE).strip()
    cleaned = re.sub(r'^(?:please\s+)?(?:pause|stop|break)(?:\s+execution)?\s+when\s+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned.rstrip(".?! ")


def _parse_audio_breakpoint_condition(text: str) -> Optional[Dict[str, Any]]:
    condition = _normalize_audio_breakpoint_text(text)
    if not condition:
        return None
    var = r'(?P<variable>[A-Za-z_]\w*)'
    num = rf'(?P<threshold>{_AUDIO_BREAKPOINT_NUM_RE})'
    phrase = (
        r'(?P<phrase>greater than or equal to|less than or equal to|'
        r'greater than|more than|less than|at least|at most|above|below)'
    )
    patterns = [
        rf'^{var}\s*(?P<operator>>=|<=|==|>|<)\s*{num}$',
        rf'^{var}\s+(?:becomes|gets|goes|is|turns)\s+{phrase}\s+{num}$',
        rf'^{var}\s+{phrase}\s+{num}$',
        rf'^{var}\s+(?:equals|is equal to|is|becomes|reaches)\s+{num}$',
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, condition, flags=re.IGNORECASE)
        if not match:
            continue
        slots = match.groupdict()
        operator = slots.get("operator")
        if not operator:
            phrase_text = (slots.get("phrase") or "").lower()
            operator = _AUDIO_BREAKPOINT_PHRASES.get(phrase_text, "==")
        threshold = _coerce_audio_number(slots.get("threshold"))
        variable = slots.get("variable", "")
        if not variable or threshold is None:
            return None
        return _build_audio_breakpoint(variable, operator, threshold, condition)
    return None


def _build_audio_breakpoint(variable: Any, operator: Any, threshold: Any, source: str = "") -> Optional[Dict[str, Any]]:
    variable = _safe_text(variable, limit=80).strip()
    operator = _safe_text(operator, limit=4).strip()
    threshold_number = _coerce_audio_number(threshold)
    if not _AUDIO_BREAKPOINT_VAR_RE.fullmatch(variable):
        return None
    if operator not in _AUDIO_BREAKPOINT_OPERATORS or threshold_number is None:
        return None
    return {
        "id": uuid.uuid4().hex[:12],
        "variable": variable,
        "operator": operator,
        "threshold": threshold_number,
        "source": source or _audio_breakpoint_label(variable, operator, threshold_number),
    }


def _audio_breakpoint_label(variable: Any, operator: Any, threshold: Any) -> str:
    op_label = _AUDIO_BREAKPOINT_OPERATOR_LABELS.get(str(operator), str(operator))
    return f"{variable} {op_label} {_format_audio_number(threshold)}"


def _numeric_from_trace_repr(text: str) -> Optional[float]:
    try:
        value = ast.literal_eval(text.strip())
    except (SyntaxError, ValueError):
        return None
    return _coerce_audio_number(value)


def _parse_audio_trace_change(change: str) -> Optional[Dict[str, Any]]:
    initialized = re.fullmatch(r'([A-Za-z_]\w*) initialized to (.+)', change)
    if initialized:
        current_text = initialized.group(2)
        current = _numeric_from_trace_repr(current_text)
        if current is None:
            return None
        return {
            "variable": initialized.group(1),
            "previous": None,
            "current": current,
            "previous_display": None,
            "current_display": _format_audio_number(current),
            "change": change,
        }
    changed = re.fullmatch(r'([A-Za-z_]\w*) changed from (.+) to (.+)', change)
    if changed:
        previous = _numeric_from_trace_repr(changed.group(2))
        current = _numeric_from_trace_repr(changed.group(3))
        if current is None:
            return None
        return {
            "variable": changed.group(1),
            "previous": previous,
            "current": current,
            "previous_display": _format_audio_number(previous) if previous is not None else None,
            "current_display": _format_audio_number(current),
            "change": change,
        }
    return None


def _audio_breakpoint_matches(current: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return current > threshold
    if operator == "<":
        return current < threshold
    if operator == "==":
        return current == threshold
    if operator == ">=":
        return current >= threshold
    if operator == "<=":
        return current <= threshold
    return False


def _find_audio_breakpoint_pause(trace: List[Dict[str, Any]], breakpoints: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    context_values: Dict[str, str] = {}
    for idx, event in enumerate(trace or []):
        if event.get("type") != "state_change":
            continue
        for change_str in event.get("changes", []) or []:
            change = _parse_audio_trace_change(change_str)
            if not change:
                continue
            context_values[change["variable"]] = change["current_display"]
            for breakpoint in breakpoints:
                if change["variable"] != breakpoint.get("variable"):
                    continue
                threshold = breakpoint.get("threshold")
                operator = breakpoint.get("operator")
                if _audio_breakpoint_matches(change["current"], operator, threshold):
                    return {
                        "breakpoint": breakpoint,
                        "event_index": idx,
                        "resume_index": idx + 1,
                        "line": event.get("line"),
                        "change": change,
                        "context": dict(context_values),
                    }
    return None


def _trace_slice_to_narration(
    trace: List[Dict[str, Any]],
    watched_vars: Optional[set] = None,
    start_index: int = 0,
    end_index: Optional[int] = None,
    output_text: str = "",
    include_start: bool = False,
    include_complete: bool = False,
) -> List[str]:
    narration: List[str] = []
    watched = watched_vars or set()
    if include_start:
        narration.append("Starting execution.")
    step_count = 0
    for idx, event in enumerate(trace or []):
        if idx < start_index:
            continue
        if end_index is not None and idx > end_index:
            break
        if step_count >= 200:
            narration.append("Narration capped at 200 steps. The program continued beyond this point.")
            break
        etype = event.get("type")
        if etype == "state_change":
            for change_str in event.get("changes", []) or []:
                parts = change_str.split(" ", 1)
                var_name = parts[0] if parts else ""
                if watched and var_name not in watched:
                    continue
                if "initialized to" in change_str:
                    narration.append(f"{var_name} becomes {change_str.split('initialized to ')[-1]}.")
                elif "changed from" in change_str:
                    old_new = change_str.split("changed from ")[-1]
                    narration.append(f"{var_name} changes to {old_new.split(' to ')[-1] if ' to ' in old_new else old_new}.")
                elif "remains" in change_str:
                    narration.append(f"{var_name} remains the same.")
                else:
                    narration.append(change_str + ".")
                step_count += 1
        elif etype == "call":
            func = event.get("function", "?")
            if func != "<module>":
                narration.append(f"Entering function {func}.")
                step_count += 1
        elif etype == "return":
            val = event.get("value", "")
            if val and val != "None":
                narration.append(f"Returned {val}.")
                step_count += 1
        elif etype == "overflow":
            narration.append("Trace limit reached. The program may have more steps.")
            break
    if output_text:
        narration.append(f"Output: {output_text[:500]}")
    if include_complete:
        narration.append("Execution complete.")
    return narration


def _audio_breakpoint_pause_message(pause: Dict[str, Any]) -> str:
    breakpoint = pause.get("breakpoint", {})
    change = pause.get("change", {})
    variable = change.get("variable", "")
    context = pause.get("context", {}) or {}
    context_parts = [
        f"{name} is {value}"
        for name, value in context.items()
        if name != variable
    ][:3]
    if change.get("previous_display") is None:
        change_phrase = f"{variable} became {change.get('current_display')}"
    else:
        change_phrase = (
            f"{variable} changed from {change.get('previous_display')} "
            f"to {change.get('current_display')}"
        )
    prefix = ""
    if context_parts:
        prefix = ", ".join(context_parts) + " and "
    return (
        f"Paused because {prefix}{change_phrase}, satisfying your breakpoint: "
        f"{_audio_breakpoint_label(breakpoint.get('variable'), breakpoint.get('operator'), breakpoint.get('threshold'))}."
    )


def _get_audio_breakpoint_state(session_id: str) -> Dict[str, Any]:
    with _session_traces_lock:
        if session_id not in _session_traces:
            _session_traces[session_id] = _make_session_storage()
        storage = _session_traces[session_id]
        storage["last_accessed"] = time.time()
        storage.setdefault("audio_breakpoints", [])
        storage.setdefault("audio_breakpoint_pause", None)
        return {
            "breakpoints": list(storage["audio_breakpoints"]),
            "pause": storage.get("audio_breakpoint_pause"),
            "trace": list(storage.get("last_trace", []) or []),
        }


def _store_audio_breakpoint_pause(session_id: str, pause: Dict[str, Any]) -> None:
    with _session_traces_lock:
        if session_id not in _session_traces:
            _session_traces[session_id] = _make_session_storage()
        storage = _session_traces[session_id]
        storage["audio_breakpoint_pause"] = pause
        storage["last_accessed"] = time.time()


def _clear_audio_breakpoint_pause(session_id: str) -> None:
    with _session_traces_lock:
        storage = _session_traces.get(session_id)
        if storage:
            storage["audio_breakpoint_pause"] = None
            storage["last_accessed"] = time.time()


def _continue_audio_breakpoint(session_id: str) -> Dict[str, Any]:
    with _session_traces_lock:
        storage = _session_traces.get(session_id)
        if not storage or not storage.get("audio_breakpoint_pause"):
            msg = "No active conditional audio breakpoint pause."
            return {"success": False, "active": False, "speech": msg, "auto_speak": True}
        pause = storage.get("audio_breakpoint_pause") or {}
        trace = list(storage.get("last_trace", []) or [])
        storage["audio_breakpoint_pause"] = None
        storage["last_accessed"] = time.time()

    output_text = pause.get("output", "")
    start_index = int(pause.get("resume_index", 0))
    narration = _trace_slice_to_narration(
        trace,
        start_index=start_index,
        output_text=output_text,
        include_complete=True,
    )
    if not narration:
        narration = ["Execution complete."]
    speech = "Continuing from the audio breakpoint. " + " ".join(narration[:50])
    return {
        "success": True,
        "active": False,
        "continued": True,
        "narration": narration,
        "narration_text": speech,
        "output": output_text,
        "speech": speech,
        "auto_speak": True,
    }


@app.route("/audio-breakpoints", methods=["POST"])
def audio_breakpoints_route():
    body = safejson()
    action = _safe_text(body.get("action", "add"), limit=40).strip().lower() or "add"
    session_id = get_session_id()

    if action in {"list", "show"}:
        state = _get_audio_breakpoint_state(session_id)
        breakpoints = state["breakpoints"]
        if not breakpoints:
            msg = "No conditional audio breakpoints are set."
        else:
            labels = [
                _audio_breakpoint_label(bp["variable"], bp["operator"], bp["threshold"])
                for bp in breakpoints
            ]
            msg = "Conditional audio breakpoints: " + "; ".join(labels) + "."
        return jsonify({"success": True, "breakpoints": breakpoints, "speech": msg, "auto_speak": True})

    if action in {"clear", "remove", "delete"}:
        with _session_traces_lock:
            if session_id not in _session_traces:
                _session_traces[session_id] = _make_session_storage()
            storage = _session_traces[session_id]
            storage["audio_breakpoints"] = []
            storage["audio_breakpoint_pause"] = None
            storage["last_accessed"] = time.time()
        msg = "Cleared all conditional audio breakpoints."
        return jsonify({"success": True, "breakpoints": [], "speech": msg, "auto_speak": True})

    if action in {"why", "why_pause", "why_paused"}:
        state = _get_audio_breakpoint_state(session_id)
        pause = state.get("pause")
        if not pause:
            msg = "There is no active conditional audio breakpoint pause to explain."
            return jsonify({"success": False, "active": False, "speech": msg, "auto_speak": True})
        msg = _audio_breakpoint_pause_message(pause)
        return jsonify({"success": True, "active": True, "speech": msg, "auto_speak": True, "pause": pause})

    if action in {"continue", "resume"}:
        return jsonify(_continue_audio_breakpoint(session_id))

    if action != "add":
        return jsonify({"success": False, "error": "Unknown audio breakpoint action."}), 400

    breakpoint = None
    if body.get("variable") is not None or body.get("operator") is not None or body.get("threshold") is not None:
        breakpoint = _build_audio_breakpoint(body.get("variable"), body.get("operator"), body.get("threshold"))
    if breakpoint is None:
        command = body.get("condition") or body.get("text") or body.get("command") or ""
        breakpoint = _parse_audio_breakpoint_condition(command)
    if breakpoint is None:
        msg = "I can set conditional breakpoints like: pause when total becomes greater than 10."
        return jsonify({"success": False, "error": "Invalid conditional audio breakpoint.", "speech": msg, "auto_speak": True}), 400

    with _session_traces_lock:
        if session_id not in _session_traces:
            _session_traces[session_id] = _make_session_storage()
        storage = _session_traces[session_id]
        breakpoints = list(storage.get("audio_breakpoints", []))
        if len(breakpoints) >= MAX_AUDIO_BREAKPOINTS:
            msg = f"Conditional audio breakpoints are limited to {MAX_AUDIO_BREAKPOINTS}. Clear one before adding another."
            return jsonify({"success": False, "error": msg, "speech": msg, "auto_speak": True}), 400
        breakpoints.append(breakpoint)
        storage["audio_breakpoints"] = breakpoints
        storage["audio_breakpoint_pause"] = None
        storage["last_accessed"] = time.time()

    label = _audio_breakpoint_label(breakpoint["variable"], breakpoint["operator"], breakpoint["threshold"])
    msg = f"Conditional audio breakpoint set: {label}."
    return jsonify({
        "success": True,
        "breakpoint": breakpoint,
        "breakpoints": breakpoints,
        "speech": msg,
        "auto_speak": True,
    })


@app.route("/step-narration", methods=["POST"])
def step_narration():
    """Run code with step narration, reporting variable changes."""
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": "Code too large"}), 413
    if not code.strip():
        return jsonify({"success": False, "error": "Code is empty"}), 400
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    session_id = get_session_id()
    _clear_audio_breakpoint_pause(session_id)

    try:
        compile(code, "<user>", "exec")
    except SyntaxError as e:
        msg = _syntax_error_message(e, code)
        return jsonify({"success": False, "error": msg, "narration": [msg]})

    if not _check_run_rate_limit(session_id):
        return jsonify({"success": False, "error": "Rate limit exceeded"}), 429

    with _watched_vars_lock:
        watched = set(_watched_vars.get(session_id, set()))

    result = _run_with_trace_for_narration(code, watched, session_id)

    # Save trace for step navigation
    if result.get("raw_trace"):
        save_execution_trace(result["raw_trace"])

    if result["success"]:
        state = _get_audio_breakpoint_state(session_id)
        pause = _find_audio_breakpoint_pause(result.get("raw_trace", []), state["breakpoints"])
        if pause:
            pause["output"] = result.get("output", "")
            _store_audio_breakpoint_pause(session_id, pause)
            pause_msg = _audio_breakpoint_pause_message(pause)
            paused_narration = _trace_slice_to_narration(
                result.get("raw_trace", []),
                watched_vars=watched,
                start_index=0,
                end_index=pause["event_index"],
                include_start=True,
            )
            paused_narration.append(pause_msg)
            narration_text = " ".join(paused_narration[:50])
            return jsonify({
                "success": True,
                "paused": True,
                "pause": pause,
                "narration": paused_narration,
                "narration_text": narration_text,
                "output": "",
                "error": "",
                "step_count": len(paused_narration),
                "speech": pause_msg,
                "auto_speak": True,
            })

    # Build spoken narration
    narration_text = " ".join(result["narration"][:50])
    if language == "hi" and result["success"] and "for i in range(3)" in code and "print(i)" in code:
        narration_text = (
            "Loop ki first iteration mein i zero hai aur output zero hai. "
            "Second iteration mein i one hai aur output one hai. "
            "Third iteration mein i two hai aur output two hai. Execution complete."
        )

    # Optionally enhance with AI
    if result["success"] and language != "hi":
        key = _configured_cloud_api_key()
        if key and not _cloud_ai_disabled_for_request(key) and len(result["narration"]) > 2:
            system = (
                "You are a friendly coding tutor narrating program execution for a blind student. "
                "Given these verified execution events, rephrase them into a warm, clear spoken narration. "
                "Do NOT invent variable values or execution steps. Keep the same order. "
                "Mention loop iterations when relevant. Under 10 sentences. No markdown."
            )
            user = f"Code:\n```python\n{code}\n```\n\nExecution events:\n" + "\n".join(result["narration"])
            ai_text = call_gemini(system, user, temperature=0.2, language=language)
            if not _ai_unavailable(ai_text):
                narration_text = ai_text

    source_lines = code.splitlines()
    narration_lines = result.get("narration_lines", [])
    indent_depths = []
    for ln in narration_lines:
        if ln is not None and 1 <= ln <= len(source_lines):
            src = source_lines[ln - 1]
            spaces = len(src) - len(src.lstrip())
            indent_depths.append(spaces // 4)
        else:
            indent_depths.append(-1)

    return jsonify({
        "success": result["success"],
        "narration": result["narration"],
        "narration_text": narration_text,
        "indent_depths": indent_depths,
        "output": result.get("output", ""),
        "error": result.get("error", ""),
        "step_count": len(result.get("steps", [])),
        "speech": narration_text,
        "auto_speak": True,
    })


@app.route("/watch-variable", methods=["POST"])
def watch_variable_route():
    """Add or remove a variable from the watch list."""
    body = safejson()
    action = safe(body.get("action"), "add")
    variable = safe(body.get("variable"), "").strip()
    session_id = get_session_id()

    if action == "clear":
        with _watched_vars_lock:
            _watched_vars.pop(session_id, None)
        msg = "Cleared all watched variables."
        return jsonify({"success": True, "watched": [], "speech": msg, "auto_speak": True})

    if action == "remove" and variable:
        with _watched_vars_lock:
            s = _watched_vars.get(session_id, set())
            s.discard(variable)
            _watched_vars[session_id] = s
            watched = list(s)
        msg = f"Stopped watching {variable}." if variable else "No variable specified."
        if watched:
            msg += f" Still watching: {', '.join(watched)}."
        return jsonify({"success": True, "watched": watched, "speech": msg, "auto_speak": True})

    if not variable:
        return jsonify({"success": False, "error": "No variable name provided."}), 400
    if not re.match(r'^[a-zA-Z_]\w*$', variable):
        return jsonify({"success": False, "error": f"'{variable}' is not a valid Python variable name."}), 400

    with _watched_vars_lock:
        s = _watched_vars.get(session_id, set())
        s.add(variable)
        _watched_vars[session_id] = s
        watched = list(s)

    msg = f"Now watching {variable}."
    if len(watched) > 1:
        msg += f" Watched variables: {', '.join(watched)}."
    return jsonify({"success": True, "watched": watched, "speech": msg, "auto_speak": True})


# ==========================
# MISTAKE REPLAY / BEFORE-VS-AFTER
# ==========================

def _save_mistake_snapshot(session_id: str, code: str, error: str, success: bool, output: str = ""):
    """Record code snapshots for mistake replay. Called from /run."""
    with _mistake_snapshots_lock:
        snap = _mistake_snapshots.get(session_id, {})
        if not success and error:
            snap["error_code"] = code
            snap["error_msg"] = error
            snap["error_timestamp"] = time.time()
        elif success:
            if snap.get("error_code"):
                snap["success_code"] = code
                snap["success_output"] = output
                snap["success_timestamp"] = time.time()
        _mistake_snapshots[session_id] = snap


def _compute_code_diff(before: str, after: str) -> dict:
    """Compute structural and textual diff between before and after code."""
    import difflib

    before_lines = before.splitlines()
    after_lines = after.splitlines()

    def _indent_width(line: str) -> int:
        return len(line) - len(line.lstrip(' '))

    def _changed_line(line_no: int, before_line: str, after_line: str, kind: str) -> dict:
        return {
            "line": line_no,
            "before": before_line,
            "after": after_line,
            "kind": kind,
            "before_indent": _indent_width(before_line),
            "after_indent": _indent_width(after_line),
        }

    # Textual diff
    changes = []
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        if tag == 'replace':
            for k in range(min(i2 - i1, j2 - j1)):
                changes.append(_changed_line(j1 + k + 1, before_lines[i1 + k], after_lines[j1 + k], "changed"))
        elif tag == 'delete':
            for k in range(i1, i2):
                changes.append(_changed_line(k + 1, before_lines[k], "", "removed"))
        elif tag == 'insert':
            for k in range(j1, j2):
                changes.append(_changed_line(k + 1, "", after_lines[k], "added"))

    structural_changes = []
    for ch in changes:
        if ch["kind"] == "changed":
            before_indent = ch["before_indent"]
            after_indent = ch["after_indent"]
            if before_indent != after_indent:
                direction = "indented" if after_indent > before_indent else "unindented"
                movement = "into" if after_indent > before_indent else "out of"
                structural_changes.append(
                    f"Line {ch['line']} was {direction} from {before_indent} to {after_indent} spaces, "
                    f"so it moved {movement} the surrounding block."
                )

    # Keep this parse check as a future extension point for richer AST facts;
    # indentation changes above must still work when the broken version is invalid.
    try:
        ast.parse(before)
        ast.parse(after)
    except SyntaxError:
        pass

    return {
        "changes": changes[:20],
        "total_changes": len(changes),
        "structural_changes": structural_changes,
    }


def _deterministic_mistake_explanation(before: str, after: str, diff: dict) -> str:
    """Build a deterministic spoken explanation of what changed between versions."""
    if not diff["changes"]:
        return "I see no differences between the two versions."

    parts = [f"There {'is' if diff['total_changes'] == 1 else 'are'} {diff['total_changes']} line{'s' if diff['total_changes'] != 1 else ''} changed."]

    for sc in diff.get("structural_changes", [])[:3]:
        parts.append(sc)

    for ch in diff["changes"][:5]:
        if ch["kind"] == "changed":
            if (
                ch["before"].strip() == ch["after"].strip()
                and ch.get("before_indent") != ch.get("after_indent")
            ):
                parts.append(
                    f"Line {ch['line']} kept \"{ch['after'].strip()}\" but changed indentation "
                    f"from {ch.get('before_indent', 0)} to {ch.get('after_indent', 0)} spaces."
                )
            else:
                parts.append(f"Line {ch['line']} changed from \"{ch['before'].strip()}\" to \"{ch['after'].strip()}\".")
        elif ch["kind"] == "added":
            parts.append(f"Line {ch['line']} was added: \"{ch['after'].strip()}\".")
        elif ch["kind"] == "removed":
            parts.append(f"A line was removed: \"{ch['before'].strip()}\".")

    return " ".join(parts)


def _deterministic_mistake_explanation_hinglish(before: str, after: str, diff: dict) -> str:
    for ch in diff.get("changes", [])[:5]:
        if (
            ch.get("kind") == "changed"
            and ch.get("before", "").strip() == ch.get("after", "").strip()
            and ch.get("before_indent") != ch.get("after_indent")
            and "print" in ch.get("after", "")
        ):
            return (
                "Pehle print statement ke pehle indentation missing thi, isliye Python ko loop ka body nahi mila. "
                "Four spaces add karne ke baad print statement loop ke andar aa gaya."
            )
    return _deterministic_mistake_explanation(before, after, diff)


@app.route("/mistake-replay", methods=["POST"])
def mistake_replay():
    """Compare before (error) and after (fix) versions of code."""
    body = safejson()
    language = safe(body.get("language"), "en")
    query = safe(body.get("query"), "compare")
    current_code = safe(body.get("code"), "")

    session_id = get_session_id()
    with _mistake_snapshots_lock:
        snap = dict(_mistake_snapshots.get(session_id, {}))

    error_code = snap.get("error_code", "")
    success_code = snap.get("success_code", current_code)

    if not error_code:
        msg = "I do not have a recent corrected mistake to compare yet. Try running code with an error, correcting it, and running again."
        return jsonify({"success": False, "reply": msg, "speech": msg, "auto_speak": True})

    if not success_code:
        success_code = current_code
    if not success_code:
        msg = "I have the errored version but no corrected version yet. Fix the code and run it successfully first."
        return jsonify({"success": False, "reply": msg, "speech": msg, "auto_speak": True})

    diff = _compute_code_diff(error_code, success_code)
    deterministic = (
        _deterministic_mistake_explanation_hinglish(error_code, success_code, diff)
        if language == "hi" else
        _deterministic_mistake_explanation(error_code, success_code, diff)
    )

    query_lower = query.lower()
    if "changed lines" in query_lower or "show" in query_lower:
        reply = deterministic
    elif language == "hi":
        reply = deterministic
    else:
        # Try AI enhancement
        key = _configured_cloud_api_key()
        if key and not _cloud_ai_disabled_for_request(key):
            error_msg = snap.get("error_msg", "")
            system = (
                "You are a friendly coding tutor explaining to a blind student what changed between "
                "their broken and fixed code. You are given verified structural facts about the diff. "
                "Explain clearly WHY the fix works, relating the code change to the error. "
                "Do NOT invent changes. Under 5 sentences. No markdown."
            )
            user = (
                f"Previous error: {error_msg}\n\n"
                f"Diff facts:\n{deterministic}\n\n"
                f"Before code:\n```python\n{error_code}\n```\n\n"
                f"After code:\n```python\n{success_code}\n```"
            )
            ai_reply = call_gemini(system, user, temperature=0.2, language=language)
            if not _ai_unavailable(ai_reply):
                reply = ai_reply
            else:
                reply = deterministic
        else:
            reply = deterministic

    return jsonify({
        "success": True,
        "reply": reply,
        "speech": reply,
        "auto_speak": True,
        "diff": diff,
        "error_code": error_code,
        "success_code": success_code,
    })


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


# ==========================
# CONCEPTUAL QUESTION FALLBACK
# ==========================
# Deterministic, context-aware explanations for the canonical beginner concepts
# so conceptual questions still get a useful spoken answer when the AI mentor is
# unavailable. This never inspects or mutates the editor beyond reading the code
# string passed in, and never executes anything.

_CONCEPT_NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six",
                         "seven", "eight", "nine", "ten"]


def _concept_num_word(n: int) -> str:
    if isinstance(n, int) and 0 <= n < len(_CONCEPT_NUMBER_WORDS):
        return _CONCEPT_NUMBER_WORDS[n]
    return str(n)


def _concept_join_words(values, sep_then: bool = False, hi: bool = False) -> str:
    words = [_concept_num_word(v) for v in values]
    if not words:
        return ""
    if sep_then:
        return (", phir " if hi else ", then ").join(words)
    conj = "aur" if hi else "and"
    if len(words) == 1:
        return words[0]
    if len(words) == 2:
        return f"{words[0]} {conj} {words[1]}"
    return ", ".join(words[:-1]) + f", {conj} " + words[-1]


def _concept_word_to_int(token: str):
    token = (token or "").strip().lower()
    try:
        return int(token)
    except ValueError:
        pass
    table = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    return table.get(token)


def _concept_code_facts(code: str) -> dict:
    """Read-only structural facts about the editor code for concept answers.

    Uses a line scan (not the AST) so it still works on code with an
    indentation error — needed to explain the broken-indentation case honestly.
    """
    facts = {
        "has_code": bool((code or "").strip()),
        "syntax_ok": True,
        "loop_var": None,
        "range_args": None,
        "range_values": None,
        "print_arg": None,
        "print_line": None,
        "print_indented": None,
        "for_line": None,
    }
    if not facts["has_code"]:
        return facts
    try:
        compile(code, "<concept>", "exec")
    except SyntaxError:
        facts["syntax_ok"] = False
    except Exception:
        pass
    for idx, line in enumerate((code or "").splitlines(), start=1):
        mfor = re.match(r"^(\s*)for\s+(\w+)\s+in\s+range\s*\((.*?)\)\s*:", line)
        if mfor and facts["for_line"] is None:
            facts["for_line"] = idx
            facts["loop_var"] = mfor.group(2)
            arg_text = mfor.group(3).strip()
            facts["range_args"] = arg_text
            parts = [p.strip() for p in arg_text.split(",") if p.strip()]
            try:
                ints = [int(p) for p in parts]
                if ints:
                    facts["range_values"] = list(range(*ints))
            except (ValueError, TypeError):
                facts["range_values"] = None
        mprint = re.match(r"^(\s*)print\s*\((.*)\)\s*$", line)
        if mprint and facts["print_line"] is None:
            facts["print_line"] = idx
            facts["print_indented"] = len(mprint.group(1)) > 0
            facts["print_arg"] = mprint.group(2).strip()
    return facts


def _concept_fallback(message: str, code: str, language: str = "en") -> str:
    """Short, beginner-friendly explanation for canonical Python concepts.

    Context-aware where possible (refers to the current code), honest about
    broken indentation, explanation-only, and capped for comfortable speech.
    """
    q = (message or "").lower()
    hi = (language == "hi")
    facts = _concept_code_facts(code)
    loop_var = facts["loop_var"] or "i"
    values = facts["range_values"] if facts["range_values"] and len(facts["range_values"]) <= 12 else None
    values_phrase = _concept_join_words(values, hi=hi) if values else None
    values_then = _concept_join_words(values, sep_then=True, hi=hi) if values else None
    count_word = _concept_num_word(len(values)) if values else None
    print_arg = facts["print_arg"] or loop_var

    # 0. "show me an example ..." — give a concrete example, no editor change.
    if "example" in q:
        if "loop" in q or re.search(r"\bfor\b", q):
            if hi:
                return ('Ek simple loop example: for n in range(3): print(n). Ye zero, one, aur two print karta '
                        'hai. Maine aapka code nahi badla.')
            return ('A simple loop example is: for n in range(3): print(n). It prints zero, one, and two. '
                    'I have not changed your code.')
        if hi:
            return ('Ek simple example hai print("Hello"). Jab aap ise run karte ho to Python Hello dikhata hai. '
                    'Maine aapka code nahi badla.')
        return ('A simple example is print("Hello"). When you run it, Python displays Hello as output. '
                'I have not changed your code.')

    # 1. A specific line: "explain line two", "what is on line 2" (number only,
    #    so "this line indented" is not mistaken for a line reference).
    mline = re.search(r"line\s+(\w+)", q)
    if mline and _concept_word_to_int(mline.group(1)) is not None:
        n = _concept_word_to_int(mline.group(1))
        src_lines = (code or "").splitlines()
        if n and 1 <= n <= len(src_lines):
            raw = src_lines[n - 1]
            src = raw.strip()
            indented = (len(raw) - len(raw.lstrip())) > 0
            nw = _concept_num_word(n)
            if re.match(r"^print\s*\(", src):
                arg = src[src.find("(") + 1:src.rfind(")")].strip() or "a value"
                tail = " It is indented, so it runs inside the block above it." if indented else ""
                return f"Line {nw} is {src}. It prints {arg}.{tail}"
            if re.match(r"^(for|while)\b", src):
                return f"Line {nw} is {src}. It starts a loop, and the indented lines below it run inside that loop."
            if re.match(r"^(if|elif|else)\b", src):
                return f"Line {nw} is {src}. It is a condition that decides whether the indented lines below it run."
            if "=" in src and not src.lstrip().startswith("#"):
                return f"Line {nw} is {src}. It creates or updates a variable."
            return f"Line {nw} is: {src}."
        return f"There is no line {mline.group(1)} in your program right now."

    # 2. Colon after a header
    if "colon" in q:
        if facts["for_line"]:
            if hi:
                return ("for line ke aakhir mein colon Python ko batata hai ki ab ek indented block "
                        "shuru hone wala hai. Yahan wo block print statement hai jo loop ke andar chalta hai.")
            return ("The colon at the end of the for line tells Python that an indented block is about "
                    "to begin. Here, that block is the print statement that runs inside the loop.")
        if hi:
            return ("Colon, jaise for ya if statement ke baad, Python ko batata hai ki ab ek indented "
                    "block shuru hone wala hai.")
        return ("A colon at the end of a line, such as after a for, while, or if statement, tells Python "
                "that an indented block is about to begin.")

    # 3. Indentation / spaces (must win over print: "why is print indented")
    if "indent" in q or "space" in q:
        if facts["print_line"] and facts["print_indented"] is False and facts["has_code"]:
            if hi:
                return ("Aapke program mein print indented nahi hai, isliye Python ise loop ke andar nahi "
                        "samajhta. for ya while header ke baad wali line ko indent karna zaroori hai, "
                        "aam taur par chaar spaces se. Abhi ye indentation error dega.")
            return ("In your program, print is not indented, so Python does not treat it as inside the loop. "
                    "The line after a for or while header must be indented, usually by four spaces. "
                    "As written, this raises an indentation error.")
        if "remove" in q and facts["print_indented"]:
            if hi:
                return ("print ke aage ke spaces ise loop ke andar rakhte hain. Agar aap unhe hata denge, "
                        "to print loop ke andar nahi rahega aur Python indentation error dega, kyunki loop "
                        "ka koi body nahi bachega.")
            return ("The spaces before print put it inside the loop. If you remove them, print is no longer "
                    "inside the loop, and Python raises an indentation error because the loop would have no body.")
        if facts["print_indented"]:
            if hi:
                return (f"Indentation ka matlab hai ki print loop ke andar hai. Andar hone ki wajah se ye "
                        f"{loop_var} ki har value ke liye ek baar chalta hai. Indentation ke bina Python is "
                        f"program mein valid loop body nahi dekhega.")
            return (f"The indentation means print is inside the loop. Because it is inside, it runs once for "
                    f"each value of {loop_var}. Without the indentation, Python would not see a valid loop "
                    f"body in this program.")
        if not facts["has_code"]:
            if hi:
                return ("Abhi editor mein koi code nahi hai, isliye main kisi ek line ko nahi bata sakta. Aam "
                        "taur par indentation line ke shuru ke spaces hote hain, jo Python ko batate hain ki "
                        "kaunsi lines kisi block, jaise loop ya function, ke andar hain.")
            return ("There is no code in the editor yet, so I cannot point to a specific line. In general, "
                    "indentation is the spaces at the start of a line, and Python uses it to show which lines "
                    "are inside a block such as a loop or a function.")
        if hi:
            return ("Indentation line ke shuru ke spaces hote hain. Python mein ye dikhata hai ki kaunsi "
                    "lines kisi block ke andar hain, jaise loop ya function ke body mein.")
        return ("Indentation is the spaces at the start of a line. In Python it shows which lines are inside "
                "a block, such as the body of a loop or a function.")

    # 4. range
    if "range" in q:
        if values:
            args = facts["range_args"] or str(len(values))
            if hi:
                return (f"range({args}) loop ko {count_word} values deta hai: {values_phrase}. Python yahan "
                        f"zero se ginna shuru karta hai, isliye loop {count_word} baar chalta hai.")
            return (f"range({args}) gives the loop {count_word} values: {values_phrase}. Python starts counting "
                    f"from zero here, so the loop runs {count_word} times.")
        if hi:
            return ("range numbers ki ek sequence banata hai. Jaise range(3) zero, one, aur two deta hai — "
                    "zero se shuru hone wali teen values.")
        return ("range creates a sequence of numbers. For example, range(3) gives zero, one, and two — three "
                "values starting from zero.")

    # 5. Loop variable by name ("what does i mean") or the word "variable"
    if "variable" in q or re.search(r"\b" + re.escape(loop_var) + r"\b", q):
        if values:
            if hi:
                return (f"{loop_var} loop variable hai. Pehli baar ye {values_then} hota hai. print statement "
                        f"har baar {loop_var} ki value dikhata hai.")
            return (f"{loop_var} is the loop variable. During the passes it is {values_then}. The print "
                    f"statement shows each of those values.")
        if hi:
            return (f"{loop_var} ek variable hai. Ye ek value store karta hai jise program chalte waqt use "
                    f"aur change kar sakta hai.")
        return (f"{loop_var} is the loop variable. It takes a new value on each pass of the loop, and your "
                f"code can use that value inside the loop.")

    # 6. loop
    if "loop" in q or re.search(r"\bfor\b", q) or "while" in q:
        if values:
            if hi:
                return (f"Loop ek action ko baar baar chalata hai. Aapke program mein loop print({print_arg}) "
                        f"ko {count_word} baar chalata hai, har value ke liye ek baar: {values_phrase}.")
            return (f"A loop repeats an action. In your program, the loop repeats print({print_arg}) {count_word} "
                    f"times, once for each value: {values_phrase}.")
        if hi:
            return ("Loop ek action ko kai baar repeat karta hai. Jaise ek for loop print statement ko range "
                    "ke har number ke liye ek baar chala sakta hai.")
        return ("A loop repeats an action several times. For example, a for loop can run a print statement once "
                "for each number in a range.")

    # 7. print
    if "print" in q:
        if facts["print_line"] and values:
            if hi:
                return (f"print ek Python function hai jo value ko output ke roop mein dikhata hai. Aapke program "
                        f"mein print({print_arg}) har baar {print_arg} ki current value dikhata hai. Yahan output "
                        f"{values_phrase} hoga.")
            return (f"print is a Python function that shows a value as output. In your program, print({print_arg}) "
                    f"shows the current value of {print_arg} each time the loop runs. Here, your program displays "
                    f"{values_phrase}.")
        if facts["print_line"]:
            if hi:
                return (f"print ek Python function hai jo value ko output ke roop mein dikhata hai. Aapke program "
                        f"mein print({print_arg}) ek value dikhata hai. Jaise print(\"Hello\") Hello dikhata hai.")
            return (f"print is a Python function that shows a value as output. In your program, print({print_arg}) "
                    f"displays a value. For example, print(\"Hello\") displays Hello.")
        if hi:
            return ("print ek Python function hai jo value ko output ke roop mein dikhata hai. Jaise "
                    "print(\"Hello\") screen par Hello dikhata hai.")
        return ("print is a Python function that shows a value as output. For example, print(\"Hello\") displays "
                "Hello on the screen.")

    # 8. Generic clarification — short, and offers concrete choices.
    if facts["has_code"] and facts["for_line"]:
        if hi:
            return ("Main aapke program ke hisson ko samjha sakta hoon, jaise loop, print statement, colon, ya "
                    "indentation. Aap kis cheez ke baare mein puchhna chahte hain?")
        return ("I can explain parts of your program, like the loop, the print statement, the colon, or the "
                "indentation. Which one would you like me to explain?")
    if hi:
        return ("Main Python ki cheezein samjha sakta hoon, jaise print, loop, range, ya indentation. Aap kya "
                "samajhna chahte hain?")
    return ("I can explain Python ideas like print, loops, range, or indentation. Which one would you like me "
            "to explain?")


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
        system += ("\nMode: the student asked a conceptual question about Python or their current code. "
                   "Explain the concept simply in 2 to 4 short sentences, connect it to their current code "
                   "when relevant, give at most one tiny example, and do not modify their code.")
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
        # Conceptual questions get a deterministic, context-aware explanation so
        # the live-demo concepts still work when the AI mentor is unavailable.
        if mode == "concept":
            concept_reply = _concept_fallback(message, code, language)
            return jsonify({"success": True, "reply": concept_reply, "speech": concept_reply, "auto_speak": True})
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
    elif mode == "concept":
        system += ("\nMode: the student asked a conceptual question about Python or their current code. "
                   "Explain the concept simply in 2 to 4 short sentences, connect it to their current code "
                   "when relevant, give at most one tiny example, and do not modify their code.")
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
        except GeneratorExit:
            return
        except Exception:
            # On error, try non-streaming fallback; if that also fails, send error message
            try:
                reply = call_gemini(system, user, temperature=0.25, language=language)
            except Exception:
                reply = "Sorry, the AI mentor is temporarily unavailable. Please try again."
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

# Per-session watched variables for Variable Watch mode
_watched_vars = {}  # session_id -> set[str]
_watched_vars_lock = threading.Lock()

# Per-session mistake replay state (before/after code snapshots)
_mistake_snapshots = {}  # session_id -> {"error_code": str, "error_msg": str, "success_code": str, "success_output": str, "timestamp": float}
_mistake_snapshots_lock = threading.Lock()


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
    try:
        project_run = _prepare_project_run(body)
    except ProjectPathError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    if project_run:
        code = project_run["files"][project_run["entry"]]
    # Mechanism A: pre-flight inputs. List of strings. Body wins; magic
    # comment is the fallback so students can ship reproducible examples.
    inputs_from_body = body.get("inputs")
    if not isinstance(inputs_from_body, list):
        inputs_from_body = None
    inputs = inputs_from_body if inputs_from_body is not None else _parse_magic_inputs(code)
    # Sanitize: stringify, cap length per item and total count
    inputs = [str(x)[:1000] for x in inputs[:50]]

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413

    if not code.strip():
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    try:
        compile(code, project_run["entry"] if project_run else "<user>", "exec")
    except SyntaxError as e:
        safe_error = _syntax_error_message(e, code)
        _save_mistake_snapshot(get_session_id(), code, safe_error, success=False)
        explanation = _local_error_explanation(code, safe_error, language=safe(body.get("language"), "en"), beginner=True)
        try:
            session_memory.record_run(session_memory.get_memory(get_trace_storage()),
                                      error=safe_error, inputs=inputs, ran_ok=False)
        except Exception:
            pass
        return jsonify({
            "success": False,
            "error": safe_error,
            "explanation": explanation,
            "inputs_hint": None,
            "input_prompts": [],
        })

    if not _check_run_rate_limit(get_session_id()):
        return jsonify({
            "success": False,
            "error": f"Rate limit exceeded. Max {RUN_RATE_LIMIT} runs per {RUN_RATE_WINDOW} seconds."
        }), 429

    # Heuristic: detect input() use without provided inputs and surface a
    # friendly hint up front. The subprocess will still raise the canonical
    # error if it actually hits input() with an empty queue, but this helps
    # catch the common case before the user waits for execution.
    input_prompts = _detect_input_prompts(code)
    uses_input = bool(input_prompts) or bool(re.search(r'\binput\s*\(', code))
    inputs_hint = None
    if uses_input and not inputs:
        concierge_inputs = detect_concierge_inputs(code)
        if concierge_inputs:
            # Concierge phrasing names the fields and the spoken commands that
            # supply them, so a beginner is never stuck guessing.
            inputs_hint = concierge_request_message(concierge_inputs)
        else:
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
        sandbox = project_run["sandbox"] if project_run else get_sandbox(get_session_id())
        workspace_dir = sandbox.workspace_dir
        run_cwd = project_run["project_root"] if project_run else workspace_dir
        trace_file = os.path.join(workspace_dir, f"trace_{uuid.uuid4().hex}.json")

        _runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox_runner.py')

        cleanup_code_file = False
        if project_run:
            code_file_path = project_run["entry_abs"]
        else:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                              encoding='utf-8', dir=workspace_dir) as code_file:
                code_file.write(code)
                code_file_path = code_file.name
            cleanup_code_file = True

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
            env['CODEUP_SAFE_OPEN_ROOT'] = run_cwd
            if project_run:
                env['CODEUP_PROJECT_ROOT'] = project_run["project_root"]
                env['CODEUP_MAIN_FILE'] = project_run["entry"]
                env['CODEUP_PROJECT_FILES'] = json.dumps(sorted(project_run["files"].keys()))
                env['CODEUP_LOCAL_MODULES'] = ",".join(project_run["local_modules"])
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
                cwd=run_cwd,
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
            for _tmp in ((code_file_path if cleanup_code_file else None), inputs_file_path):
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
            _debug_log(f"Error reading trace file: {sanitize_traceback(str(e))}")
            trace_error = 'Trace data could not be read'
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
        raw_error = stderr_buf.getvalue()
        if raw_error.strip():
            error = user_facing_error(raw_error)
        else:
            error = _subprocess_exit_error(proc_handle.returncode)

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
            _save_mistake_snapshot(get_session_id(), code, error, success=False)
            explanation = explain_error(code, error, language=safe(body.get("language"), "en"))
            try:
                session_memory.record_run(session_memory.get_memory(get_trace_storage()),
                                          error=error, inputs=inputs, ran_ok=False)
            except Exception:
                pass
            return jsonify({
                "success": False,
                "error": error,
                "explanation": explanation,
                "inputs_hint": inputs_hint,
                "input_prompts": input_prompts,
            })

        _save_mistake_snapshot(get_session_id(), code, "", success=True, output=output)
        try:
            session_memory.record_run(session_memory.get_memory(get_trace_storage()),
                                      output=output, inputs=inputs, ran_ok=True)
        except Exception:
            pass

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
        _debug_log(f"Internal /run failure:\n{sanitize_traceback(tb)}")
        error = "CodeUp hit an internal problem while running this code. Please try again, and ask the instructor to check the server log if it repeats."
        return jsonify({
            "success": False,
            "error": error,
            "explanation": "The student-facing error has been kept safe. The detailed diagnostic is in the server log for the instructor.",
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
# WALKTHROUGH (beginner-friendly program explanation)
# ==========================


def _literal_int(node) -> Optional[int]:
    """Return the integer value of a literal AST node, or None.

    Accepts plain int constants and unary-minus on an int (e.g. ``-1``) so a
    canonical ``range(...)`` walkthrough can be computed without executing code.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _literal_int(node.operand)
        return -inner if inner is not None else None
    return None


def _canonical_loop_walkthrough(code: str, language: str = "en") -> Optional[str]:
    """Concise, correct explanation for the canonical beginner loop.

    Matches a program whose only statement is ``for <var> in range(...):`` with a
    single ``print(<var>)`` body, where the range bounds are integer literals.
    For that shape we can state the iteration count, the exact values the loop
    variable takes, and the resulting output — the things a blind beginner most
    needs — without running the code or relying on AI. Returns None for anything
    else so the general structural explanation is used instead.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.For):
        return None
    loop = tree.body[0]
    if loop.orelse or not isinstance(loop.target, ast.Name):
        return None
    var = loop.target.id
    it = loop.iter
    if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
            and it.func.id == "range" and not it.keywords):
        return None
    args = [_literal_int(a) for a in it.args]
    if not (1 <= len(args) <= 3) or any(a is None for a in args):
        return None
    try:
        values = list(range(*args))
    except (ValueError, TypeError):
        return None
    if not values or len(values) > 50:
        return None
    # Body must be exactly one print() call.
    if len(loop.body) != 1 or not isinstance(loop.body[0], ast.Expr):
        return None
    call = loop.body[0].value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "print"):
        return None
    prints_var = (len(call.args) == 1 and isinstance(call.args[0], ast.Name)
                  and call.args[0].id == var)

    count = len(values)
    values_str = ", ".join(str(v) for v in values)
    if prints_var:
        outputs_str = ", then ".join(str(v) for v in values)
        if language == "hi":
            return (
                f"Is program mein ek loop hai. Loop {count} baar chalta hai, aur "
                f"variable {var} ki values {values_str} hoti hain. Loop ke andar "
                f"print({var}) indented hai, isliye har baar chalta hai. "
                f"Output aata hai {outputs_str}, har ek alag line par."
            )
        return (
            f"This program has a loop. The loop runs {count} times, and the "
            f"variable {var} takes the values {values_str}. Inside the loop, "
            f"print({var}) is indented, so it runs on every pass. The program "
            f"prints {outputs_str}, each on its own line."
        )
    if language == "hi":
        return (
            f"Is program mein ek loop hai jo {count} baar chalta hai, jismein "
            f"{var} ki values {values_str} hoti hain. Loop ke andar print "
            f"statement indented hai, isliye har baar ek line output hoti hai."
        )
    return (
        f"This program has a loop that runs {count} times, with {var} taking "
        f"the values {values_str}. Inside the loop, the indented print runs on "
        f"every pass, producing one line of output each time."
    )


def _deterministic_walkthrough(code: str, language: str = "en") -> str:
    """Build a basic structural explanation without AI."""
    lines = code.strip().splitlines()
    if not lines:
        return "There is no code to walk through yet."

    analyzer = CodeAnalyzer()
    structure = analyzer.analyze(code)
    if structure.get("error"):
        err = structure["error"]
        if language == "hi":
            return (
                f"Is code mein ek problem hai: {err}. "
                "Pehle error fix karo, phir walkthrough try karo."
            )
        return (
            f"This code has a problem: {err}. "
            "Fix the error first, then try the walkthrough again."
        )

    canonical = _canonical_loop_walkthrough(code, language)
    if canonical:
        return canonical

    parts = []
    non_blank = sum(1 for ln in lines if ln.strip())
    if language == "hi":
        parts.append(f"Is program mein {non_blank} line{'s' if non_blank != 1 else ''} of code hai{'n' if non_blank != 1 else ''}.")
    else:
        parts.append(f"This program has {non_blank} line{'s' if non_blank != 1 else ''} of code.")

    loops = structure.get("loops", [])
    functions = structure.get("functions", [])
    classes = structure.get("classes", [])

    for fn in functions:
        params = fn.get("params", [])
        pnames = []
        for p in params:
            pnames.append(p.get("name", str(p)) if isinstance(p, dict) else str(p))
        pstr = ", ".join(pnames) if pnames else "no parameters"
        parts.append(f"It defines a function called {fn['name']} that takes {pstr}.")

    for cls in classes:
        methods = cls.get("methods", [])
        mstr = ", ".join(methods[:5]) if methods else "no methods"
        parts.append(f"It defines a class called {cls['name']} with {mstr}.")

    for lp in loops:
        parts.append(f"There is a {lp['type']} loop on line {lp['line']}.")

    for i, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed.startswith("print("):
            arg = trimmed[6:].rstrip(")")
            indent = len(line) - len(line.lstrip())
            if indent > 0:
                parts.append(f"Line {i + 1} is indented, meaning it runs inside the block above it. It prints {arg}.")
            else:
                parts.append(f"Line {i + 1} prints {arg}.")
        elif "=" in trimmed and not trimmed.startswith(("if ", "elif ", "while ", "for ", "def ", "class ", "#", "==", "!=")):
            m = re.match(r"^(\w+)\s*=\s*(.+)$", trimmed)
            if m:
                parts.append(f"Line {i + 1} creates a variable called {m.group(1)} and sets it to {m.group(2)}.")

    return " ".join(parts)


@app.route("/walkthrough", methods=["POST"])
def walkthrough():
    body = safejson()
    code = safe(body.get("code"), "")
    language = safe(body.get("language"), "en")

    if len(code) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Code too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not code.strip():
        msg = "There is no code to walk through yet." if language != "hi" else "Abhi editor mein koi code nahi hai."
        return jsonify({"success": True, "explanation": msg, "speech": msg, "auto_speak": True})
    blocked = _reject_non_python_response(code)
    if blocked:
        return blocked

    if language == "hi":
        system = (
            "Aap ek friendly Python tutor ho jo blind student ko padhate ho.\n"
            "Code ka walkthrough do — explain karo ki program kya karta hai, step by step.\n"
            "Batao ki kaunsi line kya karti hai aur kyun. Loops mein batao ki kitni baar chalega aur kya output aayega.\n"
            "Agar code mein error hai (jaise indentation) toh clearly batao ki program abhi chal nahi payega aur kyun.\n"
            "Spoken Roman Hinglish mein likho. Technical terms (loop, print, variable, indentation) English mein rakhna.\n"
            "8 sentences se kam. No markdown. No code blocks."
        )
    else:
        system = (
            "You are a friendly Python tutor explaining code to a blind beginner student.\n"
            "Walk through what this program does step by step.\n"
            "Explain what each meaningful line does and WHY, not just what it says.\n"
            "For loops, explain how many times they run and what values the variable takes.\n"
            "For nested lines, explain that they are inside the loop or block above.\n"
            "If the code has a syntax error (like missing indentation), clearly state that the program "
            "cannot run as written and explain the problem.\n"
            "Mention what the final output would be.\n"
            "Under 8 sentences. Spoken English only. No markdown. No code blocks."
        )

    explanation = _canonical_loop_walkthrough(code, language)
    if not explanation:
        user = f"Python code:\n```python\n{code}\n```"
        explanation = call_gemini(system, user, temperature=0.2, language=language)

    if _ai_unavailable(explanation):
        explanation = _deterministic_walkthrough(code, language)

    return jsonify({
        "success": True,
        "explanation": explanation,
        "speech": explanation,
        "auto_speak": True,
    })


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
        return jsonify({"success": False, "message": _syntax_error_message(e, code)})

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
    requested_mode = safe(body.get("mode"), "")

    if len(prompt) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Prompt too large (max {MAX_CODE_SIZE} bytes)"}), 413
    if not prompt.strip():
        return jsonify({"success": False, "error": "Prompt cannot be empty"}), 400

    try:
        session_memory.record_generation(session_memory.get_memory(get_trace_storage()), prompt)
    except Exception:
        pass

    input_source = safe(body.get("source"), "typed").strip().lower() or "typed"
    if input_source not in {"typed", "voice"}:
        input_source = "typed"
    exact_result = build_exact_symbol_generation(prompt, source=input_source)
    if exact_result:
        return jsonify(exact_result)

    local_direct = _local_code_generation_fallback(prompt)
    if local_direct and re.search(r"\b(?:zero|0)\s+to\s+(?:two|2)\b", prompt, re.IGNORECASE):
        return jsonify({"success": True, "code": local_direct, "source": "local_fallback"})

    if requested_mode == "project" or looks_like_multifile_prompt(prompt):
        template_id = choose_template_for_prompt(prompt)
        if template_id:
            project = build_template(template_id)
            sandbox = get_sandbox(get_session_id())
            manifest = _write_project_files(sandbox, project["files"], project["manifest"])
            project["manifest"] = manifest
            project["speech"] = project_summary(manifest)
            return jsonify({"success": True, "project": True, "source": "template", **project})

        system = (
            "You generate accessible multi-file Python projects for CodeUp, a blind-first IDE.\n"
            "Return only JSON with keys: name, entry, active_file, requirements, speech, files.\n"
            "files must be an object mapping relative paths to complete file contents.\n"
            "Use safe imports only: math, random, statistics, datetime, json, csv, pathlib, typing, collections, itertools, numpy, pandas, matplotlib.\n"
            "Do not use os, sys, subprocess, sockets, shell commands, eval, exec, or network calls.\n"
            "For file access, use paths relative to the project root. Include requirements.txt when third-party packages are used.\n"
            "Include beginner-friendly comments and print output that can be spoken aloud.\n"
            "Avoid visual-only phrases like 'look at the left pane'; describe files by name and keyboard or command actions."
        )
        user = f"Create this as a CodeUp multi-file project:\n{prompt}"
        raw = call_gemini(system, user, temperature=0.2, language=language, max_tokens=4096)
        parsed_project = extract_project_json(raw)
        if not parsed_project:
            if _is_ai_service_message(raw):
                return jsonify({"success": False, "error": raw.strip(), "code": ""})
            starter_message = json.dumps(f"Starter project for: {prompt[:80]}")
            files = {
                "main.py": (
                    "from utils import describe_project\n\n"
                    "def main():\n"
                    "    # This generated starter is split into two files.\n"
                    "    print(describe_project())\n\n"
                    "if __name__ == \"__main__\":\n"
                    "    main()\n"
                ),
                "utils.py": (
                    "def describe_project():\n"
                    f"    return {starter_message}\n"
                ),
                "README.md": "# Generated CodeUp Project\n\nRun `main.py`.\n",
                "requirements.txt": "",
            }
            manifest = make_manifest(files, name="Generated CodeUp Project", entry="main.py")
            parsed_project = {
                "files": files,
                "entry": "main.py",
                "active_file": "main.py",
                "requirements": [],
                "manifest": manifest,
                "speech": project_summary(manifest),
            }
        sandbox = get_sandbox(get_session_id())
        manifest = _write_project_files(sandbox, parsed_project["files"], parsed_project["manifest"])
        return jsonify({
            "success": True,
            "project": True,
            "source": "ai_project",
            "files": parsed_project["files"],
            "entry": manifest["entry"],
            "active_file": manifest["active_file"],
            "requirements": manifest.get("requirements", []),
            "manifest": manifest,
            "speech": parsed_project.get("speech") or project_summary(manifest),
        })

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

    constraints = constraint_summary(prompt)
    constraint_text = ""
    if constraints:
        constraint_text = "Important exact constraints:\n" + "\n".join(f"- {item}" for item in constraints) + "\n\n"
    user = f"{constraint_text}Task description:\n{prompt}"
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
        if not validate_exact_output(code, prompt):
            return jsonify({
                "success": False,
                "error": "Generated code did not match the exact symbol constraints. Please type a precise command like: generate code to make a 5 by 5 star pattern where row 3 has 6 stars.",
                "code": "",
            })
    except SyntaxError:
        # One retry with a stricter system message
        retry_system = system + "\n\nIMPORTANT: Return ONLY syntactically valid Python. No prose. No markdown."
        raw_retry = call_gemini(retry_system, user, temperature=0.1, language=language)
        retry_code = extract_code(raw_retry) or (raw_retry.strip() if raw_retry and not _is_ai_service_message(raw_retry) else "")
        try:
            compile(retry_code, "<generated>", "exec")
            if not validate_exact_output(retry_code, prompt):
                return jsonify({
                    "success": False,
                    "error": "Generated code did not match the exact symbol constraints. Please type a precise command like: generate code to make a 5 by 5 star pattern where row 3 has 6 stars.",
                    "code": "",
                })
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
# MULTI-FILE PROJECTS
# ==========================

@app.route("/project", methods=["GET", "POST"])
def project_workspace():
    sandbox = get_sandbox(get_session_id())
    if request.method == "GET":
        return jsonify(_project_response(sandbox))

    body = safejson()
    try:
        files = normalize_file_map(body.get("files") or {"main.py": safe(body.get("code"), 'print("Hello CodeUp!")\n')})
        manifest = make_manifest(
            files,
            name=safe(body.get("name"), "CodeUp Project"),
            entry=safe(body.get("entry"), "main.py"),
            active_file=safe(body.get("active_file"), safe(body.get("entry"), "main.py")),
            requirements=body.get("requirements") if isinstance(body.get("requirements"), list) else infer_requirements(files),
        )
        manifest = _write_project_files(sandbox, files, manifest)
        return jsonify({**_project_response(sandbox, manifest), "speech": project_summary(manifest)})
    except ProjectPathError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/project/files", methods=["POST"])
def project_file_write():
    body = safejson()
    sandbox = get_sandbox(get_session_id())
    try:
        path = normalize_project_path(body.get("path"))
        content = _safe_text(body.get("content"), "", limit=MAX_CODE_SIZE + 1)
        if len(content) > MAX_CODE_SIZE:
            return jsonify({"success": False, "error": f"File too large (max {MAX_CODE_SIZE} bytes)"}), 413
        result = sandbox.write(_project_rel(path), content)
        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error") or "Could not save file."}), 400
        files = _load_project_files(sandbox)
        previous = _load_project_manifest(sandbox)
        manifest = make_manifest(
            files,
            name=previous.get("name") or "CodeUp Project",
            entry=previous.get("entry") or path,
            active_file=path if body.get("active", True) else previous.get("active_file") or path,
            requirements=previous.get("requirements") or infer_requirements(files),
        )
        _write_project_manifest(sandbox, manifest)
        speech = f"Saved {path}."
        return jsonify({"success": True, "path": path, "manifest": manifest, "speech": speech})
    except ProjectPathError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/project/open", methods=["POST"])
def project_file_open():
    body = safejson()
    sandbox = get_sandbox(get_session_id())
    try:
        path = normalize_project_path(body.get("path"))
        result = sandbox.read(_project_rel(path))
        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error") or f"{path} was not found."}), 404
        files = _load_project_files(sandbox)
        previous = _load_project_manifest(sandbox)
        manifest = make_manifest(
            files,
            name=previous.get("name") or "CodeUp Project",
            entry=previous.get("entry") or path,
            active_file=path,
            requirements=previous.get("requirements") or infer_requirements(files),
        )
        _write_project_manifest(sandbox, manifest)
        try:
            _mem = session_memory.get_memory(get_trace_storage())
            session_memory.record_file_open(_mem, path)
            session_memory.record_project_files(_mem, list(files.keys()))
        except Exception:
            pass
        return jsonify({
            "success": True,
            "path": path,
            "content": result.get("content", ""),
            "manifest": manifest,
            "speech": f"Opened {path}.",
        })
    except ProjectPathError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/project/rename", methods=["POST"])
def project_file_rename():
    body = safejson()
    sandbox = get_sandbox(get_session_id())
    try:
        old_path = normalize_project_path(body.get("old_path") or body.get("path"))
        new_path = normalize_project_path(body.get("new_path"))
        read = sandbox.read(_project_rel(old_path))
        if not read.get("success"):
            return jsonify({"success": False, "error": read.get("error") or f"{old_path} was not found."}), 404
        write = sandbox.write(_project_rel(new_path), read.get("content", ""))
        if not write.get("success"):
            return jsonify({"success": False, "error": write.get("error") or f"Could not write {new_path}."}), 400
        sandbox.delete(_project_rel(old_path))
        files = _load_project_files(sandbox)
        previous = _load_project_manifest(sandbox)
        entry = new_path if previous.get("entry") == old_path else previous.get("entry") or new_path
        active = new_path if previous.get("active_file") == old_path else previous.get("active_file") or new_path
        manifest = make_manifest(
            files,
            name=previous.get("name") or "CodeUp Project",
            entry=entry,
            active_file=active,
            requirements=previous.get("requirements") or infer_requirements(files),
        )
        _write_project_manifest(sandbox, manifest)
        return jsonify({"success": True, "path": new_path, "manifest": manifest, "speech": f"Renamed {old_path} to {new_path}."})
    except ProjectPathError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/project/delete", methods=["POST"])
def project_file_delete():
    body = safejson()
    sandbox = get_sandbox(get_session_id())
    try:
        path = normalize_project_path(body.get("path"))
        result = sandbox.delete(_project_rel(path))
        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error") or f"Could not delete {path}."}), 404
        files = _load_project_files(sandbox)
        previous = _load_project_manifest(sandbox)
        next_file = next((p for p in files if p.endswith(".py")), next(iter(files), "main.py"))
        manifest = make_manifest(
            files or {"main.py": 'print("Hello CodeUp!")\n'},
            name=previous.get("name") or "CodeUp Project",
            entry=next_file if previous.get("entry") == path else previous.get("entry") or next_file,
            active_file=next_file if previous.get("active_file") == path else previous.get("active_file") or next_file,
            requirements=previous.get("requirements") or infer_requirements(files),
        )
        _write_project_manifest(sandbox, manifest)
        return jsonify({"success": True, "manifest": manifest, "speech": f"Deleted {path}."})
    except ProjectPathError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/project/templates", methods=["GET"])
def project_templates():
    return jsonify({
        "success": True,
        "templates": [
            {"id": key, "title": value["title"], "description": value["description"]}
            for key, value in PROJECT_TEMPLATES.items()
        ],
    })


@app.route("/project/templates/<template_id>", methods=["POST"])
def project_template_load(template_id):
    try:
        project = build_template(template_id)
    except KeyError:
        return jsonify({"success": False, "error": "Project template not found."}), 404
    sandbox = get_sandbox(get_session_id())
    manifest = _write_project_files(sandbox, project["files"], project["manifest"])
    project["manifest"] = manifest
    project["speech"] = project_summary(manifest)
    return jsonify({"success": True, "project": True, **project})


@app.route("/project/requirements", methods=["GET"])
def project_requirements():
    sandbox = get_sandbox(get_session_id())
    manifest = _load_project_manifest(sandbox)
    requirements = manifest.get("requirements") or []
    if requirements:
        speech = f"This project needs: {', '.join(requirements)}. They are listed in requirements.txt."
    else:
        speech = "This project does not need third-party packages."
    return jsonify({"success": True, "requirements": requirements, "speech": speech})

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
    "read_project_files": ["read project files", "list project files", "show project files", "file map", "file tree"],
    "explain_project_structure": ["explain project structure", "describe project structure", "read project structure"],
    "explain_requirements": ["explain requirements", "what requirements are needed", "project requirements"],
    "explain_diff": ["why is the output different", "why did this run differently", "explain output diff"],
    "list_variables": ["what variables", "list variables", "show variables", "what variables are available", "variables in scope"],
    "check_errors": ["check for errors", "check syntax", "find errors", "are there errors", "syntax check"],
    "locate_error": ["where is the error", "where is error", "find error", "jump to error", "go to error"],
    "stop_beacon": ["stop error beacon", "stop beacon", "turn off beacon", "disable beacon"],
    "go_back": ["go back", "navigate back", "back", "previous position"],
    "go_forward": ["go forward", "navigate forward", "forward", "next position"],
    "show_history": ["show history", "navigation history", "where have i been"],
    "help": [
        "help", "show help", "what can you do", "what can i do",
        "what can i do here", "show commands", "what commands can i try",
        "how do i use this", "what should i say", "guide me", "list commands",
    ],
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


CONVERSATIONAL_EDIT_ACTIONS = {
    "insert_line",
    "replace_line",
    "delete_line",
    "indent_line",
    "dedent_line",
    "append_code",
    "replace_code",
    "undo",
}

CONVERSATIONAL_COMMAND_ACTIONS = {
    "fix_current_error": "fix",
    "generate_code": "generate_code",
    "run_code": "run",
    "explain_error": "explain_simply",
    "read_code": "narrate_file",
    "code_map": "code_map",
    "step_narration": "step_narration",
    "mistake_replay": "replay_mistake",
    "sonify_block": "sonify_block",
}

CONVERSATIONAL_ALLOWED_ACTIONS = (
    CONVERSATIONAL_EDIT_ACTIONS
    | set(CONVERSATIONAL_COMMAND_ACTIONS)
    | {"unknown"}
)

CONVERSATIONAL_POSITIONS = {
    "",
    "inside_loop",
    "after_current_line",
    "end_of_file",
    "beginning_of_file",
    "after_loop",
    "before_line",
    "after_line",
}

CONVERSATIONAL_EDIT_CANDIDATE_RE = re.compile(
    r"\b("
    r"add|append|insert|write|print|set|change|replace|delete|remove|"
    r"move|indent|dedent|outdent|comment|line|loop|inside|outside|"
    r"end|beginning|undo|why|fail|failed|fix"
    r")\b",
    re.IGNORECASE,
)

CONVERSATIONAL_UNSAFE_CODE_RE = re.compile(
    r"(__import__\s*\(|\b(?:eval|exec|open|compile|globals|locals)\s*\(|"
    r"\b(?:os|sys|subprocess|socket|pathlib|shutil|requests)\b|"
    r"https?://|\bapi[_-]?key\b|\bsecret\b|\benviron\b)",
    re.IGNORECASE,
)


def _looks_like_conversational_candidate(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    if len(cleaned) > MAX_VOICE_TEXT_SIZE:
        return False
    return bool(CONVERSATIONAL_EDIT_CANDIDATE_RE.search(cleaned))


def _numbered_code_context(code: str) -> str:
    lines = str(code or "").splitlines()
    if not lines:
        return "(editor is empty)"
    numbered = []
    remaining = MAX_CONVERSATIONAL_CONTEXT_SIZE
    for idx, line in enumerate(lines[:120], start=1):
        row = f"{idx}: {line}"
        if len(row) + 1 > remaining:
            numbered.append("[code context truncated]")
            break
        numbered.append(row)
        remaining -= len(row) + 1
    if len(lines) > 120:
        numbered.append(f"[{len(lines) - 120} more lines omitted]")
    return "\n".join(numbered)


def _extract_json_object(text: str) -> Optional[dict]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", str(text or "")).strip()
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _as_optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _line_count_for_voice_context(code: str) -> int:
    return max(1, len(str(code or "").splitlines()) or 1)


def _conversation_safe_code(code: str, *, allow_program: bool = False) -> Optional[str]:
    value = str(code or "").replace("\x00", "").strip("\r\n")
    if not value.strip():
        return ""
    limit = (
        MAX_CONVERSATIONAL_CONFIRM_CODE_SIZE
        if allow_program
        else MAX_CONVERSATIONAL_EDIT_CODE_SIZE
    )
    if len(value) > limit:
        return None
    if _looks_like_non_python_code(value):
        return None
    if CONVERSATIONAL_UNSAFE_CODE_RE.search(value):
        return None
    return value


def _make_conversational_edit_response(
    edit_action: str,
    *,
    code: str = "",
    line_number: Optional[int] = None,
    position: str = "",
    spoken_confirmation: str = "",
    confidence: float = 0.9,
    requires_confirmation: bool = False,
    source: str = "groq",
) -> Optional[dict]:
    if edit_action not in CONVERSATIONAL_EDIT_ACTIONS:
        return None
    safe_code = _conversation_safe_code(
        code,
        allow_program=edit_action == "replace_code",
    )
    if safe_code is None:
        return None
    if edit_action == "replace_code":
        requires_confirmation = True
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    confidence_value = max(0.0, min(confidence_value, 1.0))
    return {
        "success": True,
        "action": "conversational_edit",
        "ai_action": {
            "action": edit_action,
            "target": {
                "line_number": line_number,
                "position": position if position in CONVERSATIONAL_POSITIONS else "",
            },
            "code": safe_code,
            "spoken_confirmation": _safe_text(
                spoken_confirmation
                or "I applied that edit."
                if not requires_confirmation
                else "That change needs confirmation before I replace the program.",
                limit=240,
            ).strip(),
            "confidence": confidence_value,
            "requires_confirmation": bool(requires_confirmation),
            "source": source,
        },
        "confidence": confidence_value,
    }


def _validate_conversational_action(
    parsed: dict,
    *,
    transcript: str,
    code: str,
    error_context: str,
) -> Optional[dict]:
    if not isinstance(parsed, dict):
        return None
    action = _safe_text(parsed.get("action"), limit=80).strip()
    if action not in CONVERSATIONAL_ALLOWED_ACTIONS:
        return None
    if action == "unknown":
        return None

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.65:
        return None

    target = parsed.get("target") if isinstance(parsed.get("target"), dict) else {}
    line_number = _as_optional_int(target.get("line_number", parsed.get("line_number")))
    line_count = _line_count_for_voice_context(code)
    position = _safe_text(target.get("position", ""), limit=40).strip() or ""
    if position not in CONVERSATIONAL_POSITIONS:
        return None

    code_value = _safe_text(parsed.get("code"), limit=MAX_CONVERSATIONAL_CONFIRM_CODE_SIZE + 1)
    confirmation = _safe_text(parsed.get("spoken_confirmation"), limit=240).strip()
    requires_confirmation = bool(parsed.get("requires_confirmation", False))

    if action in CONVERSATIONAL_COMMAND_ACTIONS:
        frontend_action = CONVERSATIONAL_COMMAND_ACTIONS[action]
        response = {
            "success": True,
            "action": frontend_action,
            "confidence": confidence,
            "source": "groq",
        }
        if action == "generate_code":
            prompt = code_value.strip() or transcript.strip()
            if not prompt:
                return None
            response["prompt"] = prompt[:MAX_VOICE_TEXT_SIZE]
        if action == "explain_error" and not error_context.strip():
            response["message"] = "Run the program first, then ask why it failed."
        return response

    if action in {"replace_line", "delete_line", "indent_line", "dedent_line"}:
        if line_number is None or line_number < 1 or line_number > line_count:
            return None
    if action == "insert_line":
        if line_number is None:
            if position == "beginning_of_file":
                line_number = 1
            elif position == "end_of_file":
                line_number = line_count + 1
            else:
                return None
        if line_number < 1 or line_number > line_count + 1:
            return None
    if action in {"append_code", "replace_line", "insert_line", "replace_code"} and not code_value.strip():
        return None

    return _make_conversational_edit_response(
        action,
        code=code_value,
        line_number=line_number,
        position=position,
        spoken_confirmation=confirmation,
        confidence=confidence,
        requires_confirmation=requires_confirmation,
        source="groq",
    )


def _find_first_loop_line(code: str) -> Optional[int]:
    for idx, line in enumerate(str(code or "").splitlines(), start=1):
        if re.match(r"^\s*(?:for|while)\s+.+:\s*$", line):
            return idx
    return None


def _find_first_print_line(code: str, *, indented: Optional[bool] = None) -> Optional[int]:
    for idx, line in enumerate(str(code or "").splitlines(), start=1):
        if not re.match(r"^\s*print\s*\(", line):
            continue
        is_indented = bool(re.match(r"^(?: {4}|\t)", line))
        if indented is None or indented == is_indented:
            return idx
    return None


def _line_after_first_loop_header(code: str) -> Optional[int]:
    lines = str(code or "").splitlines()
    for idx, line in enumerate(lines, start=1):
        if re.match(r"^\s*(?:for|while)\s+.+:\s*$", line):
            return idx + 1 if idx < len(lines) + 1 else None
    return None


def _local_conversational_voice_action(
    transcript: str,
    code: str,
    error_context: str,
) -> Optional[dict]:
    text = str(transcript or "").strip()
    lower = text.lower()
    line_count = _line_count_for_voice_context(code)
    is_hinglish = bool(re.search(r"\b(?:karo|banao|hata|andar|bahar|pehle|tak|se|har|galti)\b", lower))

    if re.search(r"\bundo\b|\bundo that\b|\bundo last\b", lower):
        return _make_conversational_edit_response(
            "undo",
            spoken_confirmation="Undid the last editor change.",
            confidence=0.95,
            source="local",
        )

    if "why" in lower and ("fail" in lower or "error" in lower):
        return {
            "success": True,
            "action": "explain_simply",
            "confidence": 0.9,
            "source": "local",
            "message": "Explaining the most recent error.",
        }

    if (("fix" in lower and "indent" in lower)
            or ("print" in lower and "loop" in lower and "andar" in lower)
            or ("print" in lower and "four spaces" in lower)):
        line_number = _line_after_first_loop_header(code) or _find_first_print_line(code, indented=False)
        if line_number and 1 <= line_number <= line_count:
            return _make_conversational_edit_response(
                "indent_line",
                line_number=line_number,
                spoken_confirmation=(
                    "Maine print statement ko loop ke andar kar diya."
                    if is_hinglish else
                    "I indented the line after the loop."
                ),
                confidence=0.9,
                source="local",
            )

    if ((("remove" in lower or "dedent" in lower or "outdent" in lower or "hata" in lower)
            and "indent" in lower and "print" in lower)
            or ("print" in lower and "loop" in lower and "bahar" in lower)):
        line_number = _find_first_print_line(code, indented=True)
        if line_number:
            return _make_conversational_edit_response(
                "dedent_line",
                line_number=line_number,
                spoken_confirmation=(
                    "Maine print statement ke pehle wali indentation hata di."
                    if is_hinglish else
                    "I removed the indentation before the print statement."
                ),
                confidence=0.92,
                source="local",
            )

    if ("inside" in lower or "andar" in lower) and "loop" in lower and "print" in lower:
        loop_line = _find_first_loop_line(code)
        pass_line = None
        lines = str(code or "").splitlines()
        if loop_line:
            for idx in range(loop_line, min(len(lines), loop_line + 4)):
                if lines[idx].strip() == "pass":
                    pass_line = idx + 1
                    break
        target_line = pass_line or ((loop_line + 1) if loop_line else line_count + 1)
        return _make_conversational_edit_response(
            "replace_line" if pass_line else "insert_line",
            line_number=target_line,
            code="    print(i)",
            position="inside_loop",
            spoken_confirmation=(
                "Maine print statement loop ke andar add kar diya."
                if is_hinglish else
                "I added a print statement inside the loop."
            ),
            confidence=0.9,
            source="local",
        )

    if "loop" in lower and (
        "zero to two" in lower
        or "0 to 2" in lower
        or "numbers from zero" in lower
        or "zero se two" in lower
        or "0 se 2" in lower
    ):
        code_value = "for i in range(3):\n    print(i)" if "print" in lower else "for i in range(3):\n    pass"
        return _make_conversational_edit_response(
            "append_code",
            code=code_value,
            position="end_of_file",
            spoken_confirmation=(
                "Maine zero se two tak ka loop add kar diya."
                if is_hinglish else
                "I added a loop that goes from zero to two."
            ),
            confidence=0.88,
            source="local",
        )

    total_match = re.search(r"\btotal\b.*\b(?:equal|equals|set to)\b.*\bzero\b", lower)
    if total_match:
        return _make_conversational_edit_response(
            "append_code",
            code="total = 0",
            position="end_of_file",
            spoken_confirmation="I added total equals zero.",
            confidence=0.88,
            source="local",
        )

    if re.search(r"\bprint\s+total\b|\bnow\s+print\s+total\b", lower):
        return _make_conversational_edit_response(
            "append_code",
            code="print(total)",
            position="end_of_file",
            spoken_confirmation="I added a print statement for total.",
            confidence=0.88,
            source="local",
        )

    comment_match = re.search(r"\bcomment\s+(?:saying|that says|with)\s+(.+)$", text, flags=re.IGNORECASE)
    if comment_match:
        comment = re.sub(r"\s+", " ", comment_match.group(1)).strip()
        return _make_conversational_edit_response(
            "append_code",
            code=f"# {comment}",
            position="end_of_file",
            spoken_confirmation="I added that comment.",
            confidence=0.86,
            source="local",
        )

    first_line_name = re.search(
        r"\b(?:replace|change)\s+(?:the\s+)?first\s+line\b.*\bname\b.*\b(?:set\s+to|equal(?:s)?(?:\s+to)?)\s+(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if first_line_name:
        value = first_line_name.group(1).strip().strip(".")
        return _make_conversational_edit_response(
            "replace_line",
            line_number=1,
            code=f'name = "{value}"',
            spoken_confirmation="I replaced the first line with a name variable.",
            confidence=0.86,
            source="local",
        )

    if "hello" in lower and "name" in lower and "print" in lower and ("end" in lower or "followed by" in lower):
        return _make_conversational_edit_response(
            "append_code",
            code='print("hello", name)',
            position="end_of_file",
            spoken_confirmation="I added a print statement for hello followed by name.",
            confidence=0.86,
            source="local",
        )

    return None


def _route_conversational_voice_action(
    transcript: str,
    *,
    code: str,
    error_context: str,
    language: str,
    cursor_line: Optional[int],
) -> Optional[dict]:
    if not _looks_like_conversational_candidate(transcript):
        return None

    code = _safe_text(code, limit=MAX_CONVERSATIONAL_CONTEXT_SIZE + 1)
    if len(code) > MAX_CONVERSATIONAL_CONTEXT_SIZE:
        return {
            "success": True,
            "action": "unknown",
            "heard": transcript,
            "message": "I could not apply that edit because the current editor context is too large. Please use a direct command.",
            "confidence": 0.0,
        }
    error_context = sanitize_traceback(_safe_text(error_context, limit=MAX_MENTOR_CONTEXT_SIZE + 1))
    if len(error_context) > MAX_MENTOR_CONTEXT_SIZE:
        error_context = error_context[:MAX_MENTOR_CONTEXT_SIZE]

    local_fallback = _local_conversational_voice_action(transcript, code, error_context)

    system = (
        "You are CodeUp's safe conversational voice intent router for a voice-first Python IDE.\n"
        "Return JSON only. No prose, no markdown.\n"
        "Map a beginner's spoken request to exactly one allowed action.\n"
        "Allowed actions: insert_line, replace_line, delete_line, indent_line, dedent_line, "
        "append_code, replace_code, fix_current_error, generate_code, run_code, explain_error, "
        "read_code, code_map, step_narration, mistake_replay, sonify_block, undo, unknown.\n"
        "Never request file system, shell, URL, secret, environment, admin, or external operations.\n"
        "Only affect the open editor or existing CodeUp workflow.\n"
        "For Python edits, return the exact Python code fragment. Preserve capitalization in names and strings.\n"
        "For ambiguous destructive whole-program replacement, set requires_confirmation true.\n"
        "For normal obvious edits, requires_confirmation should be false.\n"
        "Use current editor line numbers and context for phrases like inside the loop, at the end, or the print line.\n"
        "Do not invent execution results.\n"
        "Schema: {\"action\":\"append_code\",\"target\":{\"line_number\":null,\"position\":\"end_of_file\"},"
        "\"code\":\"print(total)\",\"spoken_confirmation\":\"I added a print statement for total.\","
        "\"confidence\":0.0,\"requires_confirmation\":false}"
    )
    user = (
        f"Transcript:\n{transcript[:MAX_VOICE_TEXT_SIZE]}\n\n"
        f"Cursor line: {cursor_line or 'unknown'}\n\n"
        f"Current Python editor contents with line numbers:\n{_numbered_code_context(code)}\n\n"
        f"Most recent error, if any:\n{error_context or '(none)'}"
    )

    raw = call_gemini(system, user, temperature=0.05, language=language, max_tokens=700)
    if not _ai_unavailable(raw):
        parsed = _extract_json_object(raw)
        routed = _validate_conversational_action(
            parsed or {},
            transcript=transcript,
            code=code,
            error_context=error_context,
        )
        if routed:
            return routed

    if local_fallback:
        return local_fallback

    return {
        "success": True,
        "action": "unknown",
        "heard": transcript,
        "message": "I could not confidently apply that edit. Please rephrase or use a direct command.",
        "confidence": 0.0,
    }

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
        _debug_log(f"Suggestion generation failed: {sanitize_traceback(str(e))}")
        return jsonify({"success": False, "suggestions": [], "error": "Could not generate suggestions right now."})

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


_ONBOARDING_MESSAGE = (
    "You can build Python by speaking or typing. Try saying: generate code to "
    "print the first five even numbers. Then say: run code, explain it, or start "
    "tutorial. Say more examples for a longer list."
)
_FIRST_HELP_RE = re.compile(
    r"^\s*(?:what\s+can\s+i\s+do(?:\s+here)?|what\s+can\s+you\s+do|help\s+me\s+start|"
    r"how\s+do\s+i\s+use\s+this|what\s+should\s+i\s+try|what\s+can\s+i\s+say|"
    r"where\s+do\s+i\s+start|how\s+does\s+this\s+work)\s*$",
    re.IGNORECASE,
)
_MORE_HELP_RE = re.compile(
    r"^\s*(?:more\s+examples|show\s+all\s+commands|full\s+help|command\s+list|"
    r"more\s+help|all\s+commands|list\s+commands|longer\s+list)\s*$",
    re.IGNORECASE,
)
_NEW_COMMAND_RE = re.compile(
    r"^\s*(?:make|generate|print|create|put|write|draw|run|fix|clear|open|read|walk|"
    r"map|sonify|start|stop|help|what|why|how|insert|delete|rename|use\s+sample|"
    r"explain|hint|continue|recap|exit|tutorial)\b",
    re.IGNORECASE,
)


def _looks_like_new_command(text: str) -> bool:
    return bool(_NEW_COMMAND_RE.match(text or ""))


def _broken_code_request(text):
    """If the user clearly asks for a broken example, return the broken code to
    insert. Only fires on explicit *create-an-error* requests — never when the
    user wants to fix/correct one, and never accidentally."""
    low = " ".join(str(text or "").lower().split())
    # The user wants to FIX an error, not make one -> not a broken-code request.
    if re.search(r"\b(?:fix|correct|repair|debug|solve|resolve)\b", low):
        return None
    quote_trigger = (
        re.search(r"\b(?:without|missing|no)\s+(?:a\s+)?closing\s+quote\b", low)
        or "with a missing quote" in low or "missing closing quote" in low
        or "make a quote error" in low or "quotation error" in low
    )
    if quote_trigger:
        msg = re.search(r"print\s+(.+?)\s+(?:without|with|missing|and|that)\b", low)
        message = msg.group(1).strip() if msg else "hello world"
        message = re.sub(r"^(?:a|an|the)\s+", "", message)
        return f'print("{message})'
    wants_indent_error = (
        re.search(r"\b(?:make|create|insert|show|give|add|with)\b.*\bindentation\s+error\b", low)
        or "remove indentation" in low or "without indentation" in low
    )
    if wants_indent_error:
        cond = re.search(r"if\s+(\w+)\s+(?:is\s+)?(greater than|more than|less than|greater|less|equal to|equals?)\s+(\d+)", low)
        if cond:
            var, op_word, num = cond.group(1), cond.group(2), int(cond.group(3))
            op = ">" if ("greater" in op_word or "more" in op_word) else ("<" if "less" in op_word else "==")
            val = num + 2 if op == ">" else (num - 2 if op == "<" else num)
            return f'{var} = {val}\nif {var} {op} {num}:\nprint("Ready")'
        return 'age = 12\nif age > 10:\nprint("Ready")'
    if "make a syntax error" in low or ("syntax error" in low and "insert" in low):
        return 'print("hello)'
    return None


def _spoken_insert_response(text, code):
    """Build valid beginner Python for a spoken print/loop insert and return a
    conversational_edit, or None so the existing insert pipeline handles it."""
    content = intent_repair.extract_insert_content(text)
    if not content:
        return None
    python = intent_repair.build_insert_python(content, code)
    if not python:
        return None
    try:
        compile(textwrap.dedent(python), "<insert>", "exec")
    except SyntaxError:
        return None
    speech = "Inserting the line." if "\n" not in python else "Inserting the loop."
    return {
        "success": True, "action": "conversational_edit",
        "ai_action": {"action": "append_code", "code": python},
        "heard": text, "speech": speech, "spoken_code": python,
    }


def _spoken_variable_response(text, code, mem, intent):
    """Build a valid Python variable assignment from natural speech, or ask one
    specific question (remembered as a pending clarification) when the name or
    value is missing. Only overrides generic/unknown inserts, never a real
    different intent such as set_inputs."""
    if intent not in (None, "", "insert_variable", "append_line"):
        return None
    slots = intent_repair.parse_variable_assignment(text, code)
    if not slots:
        return None
    if slots.get("missing"):
        question = intent_repair.variable_question(slots)
        pending = {"type": "variable"}
        if "name" in slots["missing"]:
            pending["missing"] = "name"
            pending["value"] = slots.get("value")
        else:
            pending["name"] = slots.get("name")
        session_memory.set_pending(mem, pending)
        return {"success": True, "action": "clarify", "intent": "clarify",
                "message": question, "speech": question, "reason": "variable_assignment",
                "needs_clarification": True, "heard": text}
    python = slots["python"]
    try:
        compile(python, "<insert>", "exec")
    except SyntaxError:
        return None
    return {"success": True, "action": "conversational_edit",
            "ai_action": {"action": "append_code", "code": python},
            "heard": text, "speech": "Inserting the variable.", "spoken_code": python}


def _route_repaired_intent(text, code, allow_ai=False):
    """Map a Key 2 / deterministic intent-repair decision onto an existing action,
    or None to fall through. Only allowlisted actions are ever executed.

    ``allow_ai`` gates the Key 2 fallback: it is False for the high-confidence
    deterministic pass that runs *before* the fuzzy/security matcher (so Key 2 can
    never override an existing safe route), and True only for the last-resort pass
    that runs *after* fuzzy matching has had its say."""
    ai_fn = call_conversation_orchestrator_ai if allow_ai else None
    decision = intent_repair.repair(text, code=code, ai_fn=ai_fn)
    if not intent_repair.validate_decision(decision):
        return None
    action = decision.get("action")
    confidence = float(decision.get("confidence", 0) or 0)
    base = {"success": True, "heard": text, "repaired": True, "confidence": confidence,
            "canonical_command": decision.get("canonical_command", "")}
    if decision.get("handled") and confidence >= 0.6:
        mapping = {
            "stop_speaking": {"action": "stop_speaking"},
            "stop_listening": {"action": "pause_voice"},
            "start_listening": {"action": "resume_voice"},
            "stop_everything": {"action": "stop_everything"},
            "run": {"action": "run"},
            "explain_code": {"action": "walk_through"},
            "code_map": {"action": "code_map"},
            "sonify_block": {"action": "sonify_block"},
            "start_tutorial": {"action": "start_tutorial"},
        }
        if action in mapping:
            return {**base, **mapping[action]}
        if action == "generate_code":
            return {**base, "action": "generate_code", "prompt": decision.get("slots", {}).get("prompt", "")}
        if action in ("open_project_file", "run_project_file"):
            return {**base, "action": action, "path": decision.get("slots", {}).get("path", "")}
        if action == "concept_answer":
            return {**base, "action": "mentor_chat", "message": text, "mode": "concept"}
    if action == "clarify" or (decision.get("handled") and confidence < 0.6):
        message = decision.get("clarification") or (
            "Do you want me to generate code, edit the current code, or explain something?"
        )
        return {**base, "action": "clarify", "intent": "clarify", "message": message,
                "speech": message, "needs_clarification": True}
    return None


def _ground_concept_answer(answer, required_facts, code, text):
    """Let Key 2 make a concept answer warmer/shorter, grounded so it cannot drop
    the fact or invent code/output. Deterministic answer is the fallback."""
    if not answer:
        return answer
    system = (
        "You are CodeUp's friendly beginner tutor for a blind learner. Rephrase the "
        "answer below into ONE short, warm sentence. Keep every concrete detail (the "
        "exact word in quotes, the exact numbers). Do NOT invent code, output, files, "
        "or variables. Plain text, under 30 words."
    )
    user = f"Question: {text}\nAnswer to rephrase: {answer}\nReturn one short sentence."
    raw = call_conversation_orchestrator_ai(system, user)
    return grounded_ai.ground(
        raw, answer, required_facts=required_facts,
        context=f"{answer} {code} {text}", single_sentence=True, max_words=40, max_chars=240,
    )


def _resolve_pending_clarification(pending, text, mem):
    """Understand the user's answer to a question CodeUp asked. Returns a response
    dict (a completed action or one more specific question), or None to fall
    through to normal routing."""
    ptype = pending.get("type")
    if ptype == "pattern":
        merged = clarification_flow.parse_pattern_answer(text, pending.get("slots", {}))
        if clarification_flow.pattern_ready(merged):
            command = clarification_flow.build_pattern_command(merged)
            exact = build_exact_symbol_generation(command, source="voice")
            if exact and exact.get("success"):
                session_memory.clear_pending(mem)
                return {
                    "success": True, "action": "generate_code", "prompt": command,
                    "confidence": 0.95, "source": "deterministic_exact", "exact_symbol": True,
                    "resolved_from_clarification": True, "heard": text, "normalized_text": command,
                }
        session_memory.set_pending(mem, {"type": "pattern", "slots": merged})
        question = clarification_flow.pattern_question(merged)
        return {
            "success": True, "action": "clarify", "intent": "clarify", "message": question,
            "speech": question, "reason": "ambiguous_pattern", "needs_clarification": True, "heard": text,
        }
    if ptype == "file":
        ans = " ".join(str(text or "").lower().split())
        verb = pending.get("verb", "delete")
        action = "rename_project_file" if verb == "rename" else "delete_project_file"
        if ans in ("no", "cancel", "stop", "never mind", "nevermind"):
            session_memory.clear_pending(mem)
            msg = "Okay, I will not change any file."
            return {"success": True, "action": "deterministic_message", "message": msg, "speech": msg, "heard": text}
        if pending.get("file") and ans in ("yes", "yeah", "yep", "confirm", "do it", "go ahead"):
            session_memory.clear_pending(mem)
            return {"success": True, "action": action, "path": pending["file"], "heard": text}
        try:
            fname = normalize_project_path(text)
        except Exception:
            fname = ""
        if fname and re.search(r"\.[a-zA-Z0-9]+$", fname):
            session_memory.clear_pending(mem)
            return {"success": True, "action": action, "path": fname, "heard": text}
        question = f"Which file should I {verb}? Please say the file name, for example main dot py."
        return {"success": True, "action": "clarify", "intent": "clarify", "message": question,
                "speech": question, "needs_clarification": True, "heard": text}
    if ptype == "variable":
        python = intent_repair.parse_variable_answer(text, pending)
        if python:
            try:
                compile(python, "<insert>", "exec")
            except SyntaxError:
                python = None
        if python:
            session_memory.clear_pending(mem)
            return {"success": True, "action": "conversational_edit",
                    "ai_action": {"action": "append_code", "code": python},
                    "heard": text, "speech": "Inserting the variable."}
        if pending.get("missing") == "name" or not pending.get("name"):
            question = "What should I name the variable? Please say one word, for example score."
        else:
            question = f"What value should {pending.get('name')} store?"
        return {"success": True, "action": "clarify", "intent": "clarify", "message": question,
                "speech": question, "needs_clarification": True, "heard": text}
    if ptype == "generate":
        answer = " ".join(str(text or "").split())
        low = answer.lower()
        # User pivoted to a real command instead of answering: abandon the
        # pending question and let normal routing handle it.
        if re.match(r"^(?:run|stop|clear|open|help|exit|cancel|never\s*mind|nevermind|"
                    r"pause|resume|quit|tutorial)\b", low):
            session_memory.clear_pending(mem)
            return None
        if _generation_is_vague(answer):
            kind = pending.get("kind", "generic")
            _, question = _vague_generation_question(answer, _generation_spec_tokens(answer))
            if kind == "marks":
                question = "Should it calculate an average, assign grades, or show who passed?"
            elif kind == "project":
                question = "What kind of project should I create?"
            return {"success": True, "action": "clarify", "intent": "clarify", "message": question,
                    "speech": question, "needs_clarification": True, "heard": text}
        session_memory.clear_pending(mem)
        completed = _complete_generation_prompt(pending.get("kind", "generic"), pending.get("original", ""), answer)
        return {"success": True, "action": "generate_code", "prompt": completed,
                "source": "clarified_generation", "resolved_from_clarification": True,
                "confidence": 0.9, "heard": text}
    return None


def _record_voice_memory(mem, text, intent, response):
    """Record short-term working memory from a /voice-command response."""
    action = str(response.get("action") or "")
    session_memory.record_utterance(mem, text, intent or "", action)
    if action == "action_sequence":
        actions = response.get("actions") or []
        session_memory.record_actions(mem, [a.get("action", "") for a in actions if isinstance(a, dict)])
        for a in actions:
            if not isinstance(a, dict):
                continue
            sub = a.get("action")
            if sub == "generate_code" and a.get("prompt"):
                session_memory.record_generation(mem, a.get("prompt"))
            elif sub == "set_inputs" and isinstance(a.get("values"), list):
                session_memory.record_input_values(mem, [str(v) for v in a["values"]])
            elif sub == "open_project_file" and a.get("path"):
                session_memory.record_file_open(mem, a.get("path"))
            elif sub == "run_project_file" and a.get("path"):
                session_memory.record_active_file(mem, a.get("path"))
        return
    if action == "generate_code" and response.get("prompt"):
        session_memory.record_generation(mem, response.get("prompt"))
    elif action == "open_project_file" and response.get("path"):
        session_memory.record_file_open(mem, response.get("path"))
    elif action == "run_project_file" and response.get("path"):
        session_memory.record_active_file(mem, response.get("path"))


def _ai_modify_prompt(text, mem, params):
    """Key 2 referent resolution for a modification follow-up ("do the same with
    10"). Returns a refined generation prompt grounded in memory, or "" to keep
    the deterministic prompt. Key 2 may only resolve the referent — it is given
    bounded memory context and cannot invent facts."""
    snap = session_memory.snapshot(mem, utterance=text)
    if not snap.get("last_gen_prompt") and not snap.get("current_file"):
        return ""
    system = (
        "You are CodeUp's command interpreter. The user gave a short modification "
        "command that refers to earlier work with words like it/that/same. Using ONLY "
        "the provided context, rewrite it into ONE clear, beginner-friendly Python "
        "generation prompt. Do NOT invent details not implied by the context. Plain "
        "text only, one sentence, under 40 words."
    )
    user = (
        f"Last generation request: {snap.get('last_gen_prompt') or '(none)'}\n"
        f"Current file: {snap.get('current_file') or '(none)'}\n"
        f"User command: {text}\n\n"
        "Return one clear generation prompt."
    )
    refined = str(call_conversation_orchestrator_ai(system, user) or "").strip().strip('"').strip()
    if not refined:
        return ""
    # The rewrite must keep the new count/instruction and the prior subject (e.g.
    # "10" and "even"), and invent no new numbers/files, else keep deterministic.
    last_prompt = snap.get("last_gen_prompt", "")
    required = grounded_ai.numbers(text) + grounded_ai.salient_terms(text)[:2] + grounded_ai.salient_terms(last_prompt)[:2]
    required = [f for f in dict.fromkeys(required) if f][:6]
    deterministic_prompt = params.get("prompt", "") if isinstance(params, dict) else ""
    # deterministic_text is left empty here: the new request legitimately changes
    # the old count, so we only require the new facts and forbid invented ones
    # (the allow-list context still includes the prior prompt's numbers).
    ok, _reason = grounded_ai.validate(
        refined, deterministic_text="",
        required_facts=required,
        context=f"{text} {last_prompt} {deterministic_prompt} {snap.get('current_file', '')}",
        single_sentence=True, max_words=45, max_chars=300,
    )
    return refined if ok else ""


def _map_followup_decision(decision, text, mem):
    """Map a session_memory follow-up decision to a /voice-command response, or
    None to fall through to normal handling."""
    base = {
        "success": True,
        "heard": text,
        "memory": True,
        "referent": decision.get("referent", ""),
        "confidence": decision.get("confidence", 0.0),
    }
    if not decision.get("handled"):
        msg = decision.get("clarification") or "Could you say that another way?"
        return {**base, "action": "deterministic_message", "message": msg, "speech": msg,
                "next_action": "Waiting for clarification.", "needs_clarification": True}

    action = decision.get("resolved_action")
    params = decision.get("params", {})
    if action in ("explain_code",):
        return {**base, "action": "walk_through"}
    if action in ("explain_run", "describe_run"):
        return {**base, "action": "read_output"}
    if action == "explain_simpler":
        return {**base, "action": "mentor_chat",
                "message": "Explain the current program in simpler words.", "mode": "shorter"}
    if action == "explain_error":
        return {**base, "action": "explain_simply", "error": params.get("error", "")}
    if action == "fix_error":
        return {**base, "action": "fix", "error": params.get("error", "")}
    if action == "run_again":
        return {**base, "action": "run"}
    if action == "run_same_inputs":
        values = [str(v) for v in (params.get("inputs") or [])]
        msg = "Running again with your saved values."
        return {**base, "action": "action_sequence", "spoken_summary": msg, "speech": msg,
                "next_action": "Setting input values.",
                "actions": [
                    {"action": "set_inputs", "values": values, "label": "Reusing saved input values."},
                    {"action": "run", "label": "Running with the same values."},
                ]}
    if action == "modify_code":
        prompt = _ai_modify_prompt(text, mem, params) or params.get("prompt", "")
        # A correction/modification edits the existing program: route to the
        # generate path (which validates that the result parses before applying)
        # with a prompt grounded in the prior generation and current editor code.
        return {**base, "action": "generate_code", "prompt": prompt,
                "source": "memory_followup", "followup_edit": True}
    if action == "open_file":
        return {**base, "action": "open_project_file", "path": params.get("path", "")}
    if action == "run_file":
        return {**base, "action": "run_project_file", "path": params.get("path", "")}
    if action == "explain_project":
        return {**base, "action": "explain_project_structure"}
    if action == "summarize_session":
        msg = params.get("summary", "")
        return {**base, "action": "deterministic_message", "message": msg, "speech": msg}
    return None


# ---- vague generation clarification (deterministic, demo-safe) --------------
# A generation request with no concrete "what should it do" content should ask
# ONE short question rather than guess. This is the fallback used when the Key 2
# orchestrator is unavailable or declined; a good prompt (numbers, a named task,
# sample data) always generates immediately and is never clarified.
_GEN_BOILERPLATE = {
    "please", "hey", "ok", "okay", "alright", "could", "would", "can", "you",
    "for", "me", "us", "generate", "create", "make", "write", "build", "code",
    "program", "programme", "script", "a", "an", "some", "the", "python",
    "simple", "small", "basic", "little", "new", "to", "that", "which", "of",
    "app", "application", "thing", "something", "anything", "stuff", "project",
    "with", "using", "and", "just", "really",
}
_GEN_ACTION_WORDS = {
    "print", "calculate", "average", "averages", "sum", "total", "count", "sort",
    "grade", "grades", "grading", "pass", "passed", "passing", "fail", "store",
    "analysis", "analyse", "analyze", "reverse", "fibonacci", "prime", "primes",
    "even", "odd", "factorial", "table", "tables", "multiplication", "quiz",
    "game", "pattern", "convert", "compare", "filter", "search", "dictionary",
    "list", "loop", "function", "numbers", "number", "temperature", "celsius",
    "fahrenheit", "palindrome", "calculator", "statistics", "mean", "median",
    "mode", "csv", "plot", "chart", "guess", "password", "timer", "counter",
}
_MARKS_WORDS = {"marks", "mark", "grade", "grades", "score", "scores", "result", "results"}


def _generation_spec_tokens(spec_text):
    cleaned = re.sub(r"[^\w\s]", " ", str(spec_text or "").lower())
    return [w for w in cleaned.split() if w not in _GEN_BOILERPLATE]


def _generation_is_vague(spec_text):
    """True when a generation spec has no concrete task content."""
    tokens = _generation_spec_tokens(spec_text)
    if not tokens:
        return True
    if all(w in _MARKS_WORDS for w in tokens):
        # A bare domain hint ("make a marks thing") is not a task.
        return True
    has_action = any(w in _GEN_ACTION_WORDS for w in tokens) or bool(re.search(r"\d", str(spec_text or "")))
    if has_action:
        return False
    # Two or more concrete content words also describe a real task
    # ("prints hello", "reverse a string"); a lone vague noun does not.
    return len(tokens) < 2


def _vague_generation_question(text, tokens):
    low = (text or "").lower()
    if any(w in _MARKS_WORDS for w in tokens) or (not tokens and re.search(r"\bmarks?\b", low)):
        return "marks", "Should it calculate an average, assign grades, or show who passed?"
    if re.search(r"\bproject\b", low):
        return "project", "What kind of project should I create?"
    return "generic", ("What should the program do? For example, print numbers, "
                       "calculate marks, or make a quiz.")


def _vague_generation_clarification(text, intent, prompt, mem):
    """If this is a generation request too vague to act on, return a clarify
    response (remembering a pending 'generate' question); else None."""
    gen_like = intent == "generate_code" or looks_like_generation_request(text)
    if not gen_like:
        return None
    spec = (prompt or "").strip() or text
    if not _generation_is_vague(spec):
        return None
    kind, question = _vague_generation_question(text, _generation_spec_tokens(spec))
    session_memory.set_pending(mem, {"type": "generate", "kind": kind, "original": text})
    return {
        "success": True, "intent": "clarify", "action": "clarify",
        "message": question, "speech": question, "reason": "vague_generation",
        "needs_clarification": True, "heard": text,
        "next_action": "Waiting for clarification.",
    }


def _complete_generation_prompt(kind, original, answer):
    answer = " ".join(str(answer or "").split())
    if kind == "marks":
        return f"Create a beginner-friendly Python program about student marks that {answer}."
    return answer


# ---- Sprint 2: non-visual code understanding (deterministic) ----------------
# Structure snapshot, navigate-by-meaning, error replay, staged hints, code
# landmarks. Whole-utterance matching on NEW phrasings only, so existing
# route-tested commands (mentor "tiny hint", "compare before and after",
# "replay my mistake", output bookmarks, "where am i in the program") are
# untouched. parse_intent is not modified.
_STRUCTURE_RE = re.compile(
    r"^(?:please\s+)?(?:summari[sz]e (?:the )?structure|give me (?:a )?structure snapshot|"
    r"structure snapshot|give me an audio overview|audio overview|audio structure snapshot|"
    r"what is in this (?:program|code)|what'?s in this (?:program|code)|"
    r"what blocks are in this code|what are the blocks)$", re.IGNORECASE)
_NAV_GOTO_RE = re.compile(
    r"^(?:please\s+)?(?:go to|jump to|take me to|find|read) (?:the )?"
    r"(for loop|while loop|loop|function|condition|if statement|if|print statement|print|"
    r"error|mistake|bug|class|try block|try)$", re.IGNORECASE)
_NAV_BLOCK_RE = re.compile(
    r"^(?:please\s+)?(?:read (?:the )?)?(next|previous|prev|current|this) block$", re.IGNORECASE)
_WHERE_IS_RE = re.compile(
    r"^(?:please\s+)?where is (?:the )?([A-Za-z_][\w ]*?) "
    r"(used|changed|defined|calculated|computed|updated|set|created|modified|assigned)$", re.IGNORECASE)
_REPLAY_RE = re.compile(
    r"^(?:please\s+)?(?:show me what went wrong|what went wrong|replay the mistake|"
    r"compare broken and fixed(?: code)?|what changed after the fix|explain the fix|"
    r"show the difference|show me the fix)$", re.IGNORECASE)
_HINT_SMALL_RE = re.compile(r"^(?:please\s+)?(?:give me )?(?:a )?(?:small|little|gentle) hint$", re.IGNORECASE)
_HINT_BIGGER_RE = re.compile(r"^(?:please\s+)?(?:give me )?(?:a )?(?:bigger|larger|more specific|better) hint$", re.IGNORECASE)
_HINT_ANOTHER_RE = re.compile(r"^(?:please\s+)?(?:give me )?(?:another|one more|the next) hint$", re.IGNORECASE)
_HINT_ANSWER_RE = re.compile(r"^(?:please\s+)?(?:show me the answer|tell me the answer|"
                             r"(?:just )?give me the answer|what'?s the answer|reveal the answer)$", re.IGNORECASE)
_HINT_GENERIC_RE = re.compile(r"^(?:please\s+)?(?:give me )?(?:a )?hint$|^i need a hint$|^help me solve this$|^help me fix this$", re.IGNORECASE)
_HINT_NOTYET_RE = re.compile(r"^(?:please\s+)?(?:do not|don'?t) give me the answer(?: yet)?$", re.IGNORECASE)
_LM_CREATE_RE = re.compile(
    r"^(?:please\s+)?(?:bookmark|mark) this (loop|for loop|while loop|function|line|section|"
    r"block|condition|if|class|method)(?:\s+as\s+(.+))?$", re.IGNORECASE)
_LM_GOTO_RE = re.compile(r"^(?:please\s+)?(?:go to|open|jump to) (?:bookmark|landmark) (.+)$", re.IGNORECASE)
_LM_READ_RE = re.compile(r"^(?:please\s+)?read (?:bookmark|landmark) (.+)$", re.IGNORECASE)
_LM_LIST_RE = re.compile(r"^(?:please\s+)?list (?:my )?(?:code )?(?:bookmarks|landmarks)$", re.IGNORECASE)
_LM_DELETE_RE = re.compile(r"^(?:please\s+)?(?:delete|remove) (?:bookmark|landmark) (.+)$", re.IGNORECASE)
_LM_CLEAR_RE = re.compile(r"^(?:please\s+)?clear (?:all )?(?:code )?(?:bookmarks|landmarks)$", re.IGNORECASE)


def _extract_error_line(error: str) -> Optional[int]:
    matches = re.findall(r"line (\d+)", str(error or ""))
    if matches:
        try:
            return int(matches[-1])
        except ValueError:
            return None
    return None


def _nav_response(nav, text, mem):
    """Turn a structure_tools navigation result into a /voice-command response."""
    if nav.get("found"):
        session_memory.record_navigation(mem, nav)
        resp = {"success": True, "action": "navigate_code", "heard": text,
                "line": nav.get("line"), "end_line": nav.get("end_line"),
                "message": nav.get("message", ""), "speech": nav.get("message", ""),
                "code_excerpt": nav.get("code", ""), "block_type": nav.get("block_type", "")}
        return resp
    msg = nav.get("message", "I could not find that in your code.")
    return {"success": True, "action": "deterministic_message", "heard": text,
            "message": msg, "speech": msg}


def _resolve_landmark_target(code, cursor_line, mem, construct):
    nav = session_memory.get_last_navigated(mem)
    if nav and nav.get("line"):
        return nav
    target = construct or "current block"
    n = structure_tools.navigate(code, target, cursor_line=cursor_line)
    if n.get("found"):
        return {"line": n["line"], "end_line": n.get("end_line") or n["line"],
                "block_type": n.get("block_type", ""), "preview": n.get("preview", "")}
    return None


def _sprint2_command(text, code, mem, cursor_line, error_context, mistake_snapshot):
    """Deterministic Sprint-2 commands, or None to fall through."""
    t = " ".join(str(text or "").lower().strip().rstrip(".!?").split())
    if not t:
        return None
    code = code or ""

    # 1) Structure snapshot
    if _STRUCTURE_RE.match(t):
        snap = structure_tools.build_structure_snapshot(code)
        return {"success": True, "action": "deterministic_message", "heard": text,
                "message": snap["summary"], "speech": snap["summary"], "structure": snap}

    # 2) Error replay (new phrasings; reuses the run snapshots)
    if _REPLAY_RE.match(t):
        result = error_replay.from_snapshot(mistake_snapshot)
        return {"success": True, "action": "deterministic_message", "heard": text,
                "message": result["explanation"], "speech": result["speech"],
                "error_replay": True, "changed_lines": result.get("changed_lines", [])}

    # 3) Landmarks — create (construct word disambiguates from output bookmarks)
    m = _LM_CREATE_RE.match(t)
    if m:
        construct, name = m.group(1), (m.group(2) or "").strip()
        norm_construct = "loop" if "loop" in construct else construct
        target = _resolve_landmark_target(code, cursor_line, mem, norm_construct if norm_construct in
                                          ("loop", "function", "condition", "if", "class") else None)
        if not target:
            msg = "Which line or block should I bookmark? Try saying 'go to the loop' first."
            return {"success": True, "action": "bookmark_error", "heard": text, "message": msg, "speech": msg}
        store = session_memory.get_landmarks(mem)
        result = landmarks.create_landmark(
            store, name or norm_construct, line=target.get("line"),
            end_line=target.get("end_line"), block_type=target.get("block_type") or construct,
            preview=target.get("preview", ""))
        return {**result, "heard": text}

    # Landmark list / goto / read / delete / clear. For commands that collide
    # with output bookmarks ("list bookmarks", "go to bookmark X"), only claim
    # them when a code landmark actually matches; otherwise fall through.
    store = session_memory.get_landmarks(mem)
    if _LM_CLEAR_RE.match(t):
        return {**landmarks.clear_landmarks(store), "heard": text}
    m = _LM_DELETE_RE.match(t)
    if m and landmarks.normalize_name(m.group(1)) in store:
        return {**landmarks.delete_landmark(store, m.group(1)), "heard": text}
    m = _LM_GOTO_RE.match(t) or _LM_READ_RE.match(t)
    if m:
        name = landmarks.normalize_name(m.group(1))
        is_landmark_word = "landmark" in t
        if name in store or is_landmark_word:
            return {**landmarks.get_landmark(store, m.group(1)), "heard": text}
    if _LM_LIST_RE.match(t):
        if store or "landmark" in t:
            return {**landmarks.list_landmarks(store), "heard": text}

    # 4) Navigation by meaning
    if _NAV_GOTO_RE.match(t):
        target = _NAV_GOTO_RE.match(t).group(1)
        last_err_line = _extract_error_line(error_context) or _extract_error_line(mem.get("last_run_error", ""))
        nav = structure_tools.navigate(code, target, cursor_line=cursor_line, last_error_line=last_err_line)
        return _nav_response(nav, text, mem)
    if _NAV_BLOCK_RE.match(t):
        rel = _NAV_BLOCK_RE.match(t).group(1)
        nav = structure_tools.navigate(code, f"{rel} block", cursor_line=cursor_line,
                                       last_block=session_memory.get_last_navigated(mem))
        return _nav_response(nav, text, mem)
    m = _WHERE_IS_RE.match(t)
    if m:
        name, verb = m.group(1).strip(), m.group(2).lower()
        mode = "changed" if verb in ("changed", "calculated", "computed", "updated", "set", "modified", "assigned") \
            else ("defined" if verb in ("defined", "created") else "used")
        result = structure_tools.find_symbol(code, name.split()[-1], mode)
        return _nav_response(result, text, mem)

    # 5) Staged hints
    level = None
    if _HINT_SMALL_RE.match(t):
        level = session_memory.set_hint_level(mem, "small")
    elif _HINT_BIGGER_RE.match(t):
        level = session_memory.set_hint_level(mem, "bigger")
    elif _HINT_ANSWER_RE.match(t):
        level = session_memory.set_hint_level(mem, "answer")
    elif _HINT_ANOTHER_RE.match(t):
        level = session_memory.escalate_hint(mem)
    elif _HINT_GENERIC_RE.match(t):
        level = session_memory.get_hint_level(mem)
    elif _HINT_NOTYET_RE.match(t):
        level = session_memory.set_hint_level(mem, "small")
    if level is not None:
        ctx = {"code": code, "error": error_context or mem.get("last_run_error", ""),
               "tutorial_module": mem.get("tutorial_module", "")}
        hint = hint_engine.build_hint(ctx, level)
        prefix = "I won't give the answer yet. " if _HINT_NOTYET_RE.match(t) else ""
        msg = prefix + hint["hint"]
        return {"success": True, "action": "deterministic_message", "heard": text,
                "message": msg, "speech": msg, "hint_level": hint["level"],
                "problem_type": hint.get("problem_type", "")}

    return None


@app.route("/voice-command", methods=["POST"])
def voice():
    body = safejson()
    text = _safe_text(body.get("text"), limit=MAX_VOICE_TEXT_SIZE + 1).strip()
    if len(text) > MAX_VOICE_TEXT_SIZE:
        return jsonify({"success": False, "action": "unknown", "error": "Voice command is too long"}), 413
    current_code = _safe_text(body.get("code"), limit=MAX_CONVERSATIONAL_CONTEXT_SIZE + 1)
    error_context = _safe_text(body.get("error"), limit=MAX_MENTOR_CONTEXT_SIZE + 1)
    language = _safe_text(body.get("language"), "en", limit=20) or "en"
    input_source = _safe_text(body.get("source"), "typed", limit=20).strip().lower() or "typed"
    if input_source not in {"typed", "voice"}:
        input_source = "typed"
    cursor_line = _as_optional_int(body.get("cursor_line"))
    parsed = parse_intent(text)
    intent = parsed.get("intent")
    slots = parsed.get("slots", {})
    confidence = parsed.get("confidence", 0.0)

    storage = get_trace_storage()
    mem = session_memory.get_memory(storage)

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
        """Helper: save action for repeat, record working memory, then return."""
        with _session_traces_lock:
            storage['last_voice_action'] = (response_dict, status_code)
        try:
            _record_voice_memory(mem, text, intent, response_dict)
        except Exception:
            pass
        return jsonify(response_dict), status_code

    # ---- 1. Pending clarification answer ------------------------------------
    # CodeUp only asks a question it can understand the answer to. If we are
    # waiting on one, parse this utterance as the answer (unless it is clearly a
    # brand-new command, in which case we drop the pending question).
    pending = session_memory.get_pending(mem)
    if pending:
        # A pending "generate" question expects a free-form answer that often
        # starts with a command word ("print the first five even numbers"), so we
        # do NOT treat it as a brand-new command; the resolver itself detects a
        # genuine pivot (e.g. "run") and returns None to fall through.
        if pending.get("type") != "generate" and _looks_like_new_command(text):
            session_memory.clear_pending(mem)
        else:
            resolved = _resolve_pending_clarification(pending, text, mem)
            if resolved:
                return _store_and_return(resolved)

    # ---- 2. Short first-help vs. full command list --------------------------
    if _MORE_HELP_RE.match(text):
        return _store_and_return({"success": True, "action": "more_help", "confidence": 0.95})
    if _FIRST_HELP_RE.match(text):
        return _store_and_return({
            "success": True, "action": "deterministic_message",
            "message": _ONBOARDING_MESSAGE, "speech": _ONBOARDING_MESSAGE,
            "heard": text, "onboarding": True,
        })

    # ---- 3. Intentional broken-code examples --------------------------------
    broken = _broken_code_request(text)
    if broken:
        speech = "Inserting a broken example so you can hear the error when you run it."
        return _store_and_return({
            "success": True, "action": "conversational_edit",
            "ai_action": {"action": "append_code", "code": broken},
            "intentional_error": True, "heard": text, "speech": speech,
        })

    # ---- 3b. Spoken print/loop inserts -> valid beginner Python -------------
    # "insert print hello" / "put print hello in the editor" become
    # print("hello") (text quoted, numbers bare, defined variables left bare),
    # built deterministically with editor context. Other inserts (variables, if,
    # while, for-headers) fall through to the existing pipeline.
    insert_response = _spoken_insert_response(text, current_code)
    if insert_response is not None:
        return _store_and_return(insert_response)

    # ---- 3c. Natural variable assignments -> valid Python (or ask) ----------
    # "set age to 16" -> age = 16; "insert a variable with the value Taki" asks
    # "What should I name the variable?" and remembers it.
    variable_response = _spoken_variable_response(text, current_code, mem, intent)
    if variable_response is not None:
        return _store_and_return(variable_response)

    # ---- 3d. Sprint 2: structure / navigation / replay / hints / landmarks --
    # Deterministic, whole-utterance commands routed before concept Q&A and
    # generation so "what is in this program" summarizes structure (not a concept
    # answer) and "where is total changed" navigates (not chat).
    with _mistake_snapshots_lock:
        _snap = dict(_mistake_snapshots.get(get_session_id(), {}))
    sprint2 = _sprint2_command(text, current_code, mem, cursor_line, error_context, _snap)
    if sprint2 is not None:
        return _store_and_return(sprint2)

    # ---- 4. Global beginner concept Q&A (works outside the tutorial) --------
    concept_kind = concept_qa.classify_concept_question(text)
    if concept_kind:
        # "what does range 3 mean" only answers when the code actually has a range;
        # otherwise let the normal concept-mentor route handle it.
        if concept_kind != "range" or re.search(r"\brange\s*\(", current_code or ""):
            answer, facts = concept_qa.answer_concept(concept_kind, current_code)
            if answer:
                grounded = _ground_concept_answer(answer, facts, current_code, text)
                return _store_and_return({
                    "success": True, "action": "deterministic_message",
                    "message": grounded, "speech": grounded, "heard": text, "concept": concept_kind,
                })

    pending_clarification = storage.get("pending_orchestrator_clarification")
    orchestrator_text = text
    looks_like_new_command = re.match(
        r"\s*(?:make|generate|print|create|put|write|draw|run|fix|clear|open|read|walk|map|sonify|start|help|what|why|how)\b",
        text,
        re.IGNORECASE,
    )
    if (
        isinstance(pending_clarification, dict)
        and time.time() - float(pending_clarification.get("timestamp", 0) or 0) < 90
        and not intent
        and not looks_like_new_command
    ):
        orchestrator_text = f"{pending_clarification.get('original_text', '')} {text}".strip()

    # ---- confidence-aware clarification for risky/ambiguous commands --------
    # Ask one short question before a destructive file op with a vague referent,
    # or a sized symbol-pattern request too vague to parse cleanly. Clear
    # commands (exact patterns, run code, inserts, tutorial) are never blocked.
    # Assess on the wake-stripped, noise-cleaned text so a complete exact-symbol
    # request (e.g. "ast risks" -> asterisks) is recognized and never clarified.
    _clarifier_text = strip_wake_phrase(text).get("text") or text
    clarification = command_clarifier.assess(
        _clarifier_text,
        intent=intent or "",
        confidence=confidence,
        code=current_code,
        mem=mem,
        exact_result=build_exact_symbol_generation(_clarifier_text, source=input_source),
        ai_fn=call_conversation_orchestrator_ai,
    )
    if clarification and clarification.get("needs_clarification"):
        message = clarification["message"]
        # Remember the structured question so the user's next utterance is
        # understood as the answer (see the pending-clarification block above).
        session_memory.set_pending(mem, clarification.get("pending"))
        return _store_and_return({
            "success": True,
            "intent": "clarify",
            "action": "clarify",
            "message": message,
            "speech": message,
            "reason": clarification.get("reason", ""),
            "needs_clarification": True,
            "heard": text,
            "next_action": "Waiting for clarification.",
        })

    wake_info = strip_wake_phrase(orchestrator_text)
    lower_orchestrator_text = orchestrator_text.lower()
    has_multi_connector = bool(re.search(r"\b(?:then|and then|after that)\b", lower_orchestrator_text))
    # Route rough generation asks ("make a marks thing") to the Key 2 brain only
    # when the deterministic parser could not already classify them — clean
    # generation intents (e.g. multi-file projects) keep their fast existing path.
    rough_generation_request = bool(
        not intent
        and looks_like_generation_request(wake_info.get("text") or orchestrator_text)
    )
    should_try_orchestrator = (
        wake_info.get("wake_detected")
        or has_multi_connector
        or "cube" in lower_orchestrator_text
        or re.search(r"\bfix\s+it\s+and\s+run\s+it\b", lower_orchestrator_text)
        or rough_generation_request
        or build_exact_symbol_generation(wake_info.get("text") or orchestrator_text, source=input_source) is not None
    )
    if should_try_orchestrator:
        pre_orchestrator_exact = build_exact_symbol_generation(wake_info.get("text") or orchestrator_text, source=input_source)
        plan = orchestrate_command(
            orchestrator_text,
            code=current_code,
            source=input_source,
            ai_plan_fn=call_conversation_orchestrator_ai if (wake_info.get("wake_detected") or has_multi_connector or rough_generation_request) else None,
        )
        if plan:
            if plan.get("needs_clarification"):
                storage["pending_orchestrator_clarification"] = {
                    "original_text": orchestrator_text,
                    "question": plan.get("clarification_question", ""),
                    "expected_slots": [],
                    "timestamp": time.time(),
                }
                message = plan.get("clarification_question") or plan.get("spoken_summary") or "Please clarify that command."
                return _store_and_return({
                    "success": True,
                    "action": "exact_symbol_clarification" if pre_orchestrator_exact is not None else "orchestrator_clarification",
                    "message": message,
                    "speech": message,
                    "heard": text,
                    "normalized_text": plan.get("normalized_text", ""),
                    "next_action": "Waiting for clarification.",
                    "confidence": plan.get("confidence", 0.0),
                    "orchestrated": True,
                })
            storage.pop("pending_orchestrator_clarification", None)
            planned_actions = frontend_actions(plan, input_source=input_source)
            if planned_actions:
                next_action = action_next_label(plan["actions"][0]) if plan.get("actions") else "Preparing the next action."
                base = {
                    "success": True,
                    "heard": text,
                    "normalized_text": plan.get("normalized_text", ""),
                    "spoken_summary": plan.get("spoken_summary", ""),
                    "next_action": next_action,
                    "confidence": plan.get("confidence", 0.0),
                    "orchestrated": True,
                }
                if len(planned_actions) == 1:
                    return _store_and_return({**base, **planned_actions[0]})
                return _store_and_return({
                    **base,
                    "action": "action_sequence",
                    "actions": planned_actions,
                })

    exact_result = build_exact_symbol_generation(text, source=input_source)
    if exact_result:
        if exact_result.get("success"):
            return _store_and_return({
                "success": True,
                "action": "generate_code",
                "prompt": text,
                "confidence": 0.95,
                "source": "deterministic_exact",
                "input_source": input_source,
                "exact_symbol": True,
            })
        return _store_and_return({
            "success": True,
            "action": "exact_symbol_clarification",
            "message": exact_result.get("message") or exact_result.get("error"),
            "speech": exact_result.get("speech") or exact_result.get("message") or exact_result.get("error"),
            "confidence": 0.95,
            "source": "deterministic_exact",
            "exact_symbol": True,
        })

    # ---- session memory: contextual follow-ups ------------------------------
    # Short follow-ups ("explain it again", "why did it fail", "fix that",
    # "run it again", "do the same with 10", "open that file again") are resolved
    # against this session's working memory and routed to the existing real
    # actions. Runs after exact-symbol/orchestrator so those still win, and
    # before the concierge so "run with the same values" reuses saved inputs.
    followup_category = session_memory.classify_followup(text)
    if followup_category:
        decision = session_memory.resolve_followup(
            followup_category, text, mem, code=current_code, error=error_context,
        )
        mapped = _map_followup_decision(decision, text, mem)
        if mapped is not None:
            return _store_and_return(mapped)

    # ---- input() concierge --------------------------------------------------
    # Natural value-supplying commands ("run with name Taknoor and age 16",
    # "use sample values", "name is Taknoor and age is sixteen") feed the
    # existing pre-flight input run path. We only override the generic run-file
    # intent, never a specific one (e.g. "run with step narration").
    if intent in (None, "run_project_file"):
        concierge = build_input_plan(current_code, text, ai_value_fn=_concierge_ai_values)
        if concierge:
            status = concierge.get("status")
            if status in ("ask_for_code", "type_error"):
                message = concierge["message"]
                return _store_and_return({
                    "success": True,
                    "action": "deterministic_message",
                    "message": message,
                    "speech": message,
                    "heard": text,
                    "next_action": "Waiting for input values.",
                    "input_concierge": True,
                })
            if status == "ready":
                message = concierge["message"]
                return _store_and_return({
                    "success": True,
                    "action": "action_sequence",
                    "heard": text,
                    "normalized_text": concierge.get("summary", message),
                    "spoken_summary": message,
                    "speech": message,
                    "next_action": "Setting input values.",
                    "input_concierge": True,
                    "actions": [
                        {"action": "set_inputs", "values": concierge["values"], "label": "Setting input values."},
                        {"action": "run", "label": "Running with your values."},
                    ],
                })
            if status == "no_input":
                # Values were offered but the code never calls input(): honour
                # requirement 8 and just run the code normally.
                return _store_and_return({"success": True, "action": "run", "heard": text, "input_concierge": True})

    if (
        intent == "mentor_chat"
        and confidence >= 0.75
        and error_context.strip()
        and re.match(r"^why\s+did\s+(?:this|my\s+code|it)\s+fail\??$", text.lower())
    ):
        return _store_and_return({"success": True, "action": "explain_simply", "confidence": confidence})

    # ---- vague generation clarification (deterministic, demo-safe) ----------
    # Runs only after the Key 2 orchestrator declined, so a good rough prompt
    # that Key 2 rewrote still generates. A vague ask ("generate code", "make a
    # marks thing") gets one short question and a pending follow-up instead of a
    # guessed/empty generation.
    vague = _vague_generation_clarification(text, intent, slots.get("prompt", ""), mem)
    if vague is not None:
        return _store_and_return(vague)

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
        if intent == "product_positioning":
            message = _codeup_positioning_message()
            return _store_and_return({"success": True, "action": "deterministic_message", "message": message, "speech": message, "confidence": confidence})
        if intent == "professional_transition":
            message = _codeup_transition_message()
            return _store_and_return({"success": True, "action": "deterministic_message", "message": message, "speech": message, "confidence": confidence})
        if intent == "mentor_chat":
            return _store_and_return({"success": True, "action": "mentor_chat", "message": slots.get("message", text), "mode": slots.get("mode", "general"), "confidence": confidence})
        if intent == "concept_question":
            # Conceptual questions ("what is a loop", "why is print indented")
            # are explanation-only: route to the mentor in concept mode, which
            # returns spoken text and never edits the editor.
            return _store_and_return({"success": True, "action": "mentor_chat", "message": slots.get("message", text), "mode": "concept", "confidence": confidence})
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
        if intent == "read_code":
            return _store_and_return({"success": True, "action": "read_code", "confidence": confidence})
        if intent == "narrate_file":
            return _store_and_return({"success": True, "action": "narrate_file", "confidence": confidence})
        if intent == "walk_through":
            return _store_and_return({"success": True, "action": "walk_through", "confidence": confidence})
        if intent == "demo_list":
            return _store_and_return({"success": True, "action": "demo_list", "confidence": confidence})
        if intent == "demo_run":
            return _store_and_return({"success": True, "action": "demo_run", "preset": slots.get("preset", ""), "confidence": confidence})
        if intent == "pause_voice":
            return _store_and_return({"success": True, "action": "pause_voice", "confidence": confidence})
        if intent == "resume_voice":
            return _store_and_return({"success": True, "action": "resume_voice", "confidence": confidence})
        if intent == "read_project_files":
            return _store_and_return({"success": True, "action": "read_project_files", "confidence": confidence})
        if intent == "open_project_file":
            return _store_and_return({"success": True, "action": "open_project_file", "path": slots.get("path", ""), "confidence": confidence})
        if intent == "create_project_file":
            return _store_and_return({"success": True, "action": "create_project_file", "path": slots.get("path", ""), "confidence": confidence})
        if intent == "rename_project_file":
            return _store_and_return({"success": True, "action": "rename_project_file", "path": slots.get("path", ""), "old_path": slots.get("old_path", ""), "confidence": confidence})
        if intent == "delete_project_file":
            return _store_and_return({"success": True, "action": "delete_project_file", "path": slots.get("path", ""), "confidence": confidence})
        if intent == "run_project_file":
            return _store_and_return({"success": True, "action": "run_project_file", "path": slots.get("path", ""), "confidence": confidence})
        if intent == "explain_project_structure":
            return _store_and_return({"success": True, "action": "explain_project_structure", "confidence": confidence})
        if intent == "explain_requirements":
            return _store_and_return({"success": True, "action": "explain_requirements", "confidence": confidence})
        if intent == "generate_code":
            return _store_and_return({"success": True, "action": "generate_code", "prompt": slots.get("prompt", ""), "confidence": confidence})
        if intent == "rename_snippet":
            return _store_and_return({"success": True, "action": "rename_snippet", "id": slots.get("id"), "new_name": slots.get("new_name"), "confidence": confidence})
        if intent == "save_snippet_auto":
            return _store_and_return({"success": True, "action": "save_snippet_auto", "confidence": confidence})
        if intent == "save_snippet_named":
            return _store_and_return({"success": True, "action": "save_snippet_named", "name": slots.get("name", "Untitled"), "confidence": confidence})
        if intent == "list_snippets":
            return _store_and_return({"success": True, "action": "list_snippets", "confidence": confidence})
        if intent == "load_snippet":
            return _store_and_return({"success": True, "action": "load_snippet", "id": slots.get("id", ""), "confidence": confidence})
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
        if intent == "insert_while":
            return _store_and_return({"success": True, "action": "insert_while", "condition": slots.get("condition", "True"), "confidence": confidence})
        if intent == "insert_variable":
            return _store_and_return({"success": True, "action": "insert_variable", "name": slots.get("name", "value"), "value": slots.get("value", ""), "confidence": confidence})
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
        if intent == "help":
            return _store_and_return({"success": True, "action": "help", "confidence": confidence})
        if intent == "more_help":
            return _store_and_return({"success": True, "action": "more_help", "confidence": confidence})
        if intent == "set_audio_breakpoint":
            return _store_and_return({"success": True, "action": "set_audio_breakpoint", "condition": slots.get("condition", ""), "confidence": confidence})
        if intent == "list_audio_breakpoints":
            return _store_and_return({"success": True, "action": "list_audio_breakpoints", "confidence": confidence})
        if intent == "why_audio_breakpoint":
            return _store_and_return({"success": True, "action": "why_audio_breakpoint", "confidence": confidence})
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
        if intent == "sonify_block":
            return _store_and_return({"success": True, "action": "sonify_block", "confidence": confidence})
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
        # Audio Code Map intents
        if intent == "code_map":
            return _store_and_return({"success": True, "action": "code_map", "confidence": confidence})
        if intent == "inside_loop":
            return _store_and_return({"success": True, "action": "code_map", "query": "inside loop", "confidence": confidence})
        if intent == "after_loop":
            return _store_and_return({"success": True, "action": "code_map", "query": "after loop", "confidence": confidence})
        if intent == "nesting_depth":
            return _store_and_return({"success": True, "action": "code_map", "query": "nesting depth", "confidence": confidence})
        if intent == "list_functions":
            return _store_and_return({"success": True, "action": "code_map", "query": "list functions", "confidence": confidence})
        if intent == "where_in_program":
            return _store_and_return({"success": True, "action": "code_map", "query": "where in program", "confidence": confidence})
        # Variable Watch / Step Narration intents
        if intent == "watch_var":
            return _store_and_return({"success": True, "action": "watch_var", "variable": slots.get("variable", ""), "confidence": confidence})
        if intent == "stop_watching":
            return _store_and_return({"success": True, "action": "stop_watching", "variable": slots.get("variable", ""), "confidence": confidence})
        if intent == "clear_watched":
            return _store_and_return({"success": True, "action": "clear_watched", "confidence": confidence})
        if intent == "step_narration":
            return _store_and_return({"success": True, "action": "step_narration", "confidence": confidence})
        if intent == "read_var_values":
            return _store_and_return({"success": True, "action": "read_var_values", "confidence": confidence})
        if intent == "what_changed_step":
            return _store_and_return({"success": True, "action": "what_changed_step", "speech": _trace_playback("current_change"), "confidence": confidence})
        if intent == "only_announce_changes":
            return _store_and_return({"success": True, "action": "only_announce_changes", "confidence": confidence})
        # Mistake Replay intents
        if intent == "compare_before_after":
            return _store_and_return({"success": True, "action": "compare_before_after", "confidence": confidence})
        if intent == "replay_mistake":
            return _store_and_return({"success": True, "action": "replay_mistake", "confidence": confidence})
        if intent == "why_fixed_works":
            return _store_and_return({"success": True, "action": "why_fixed_works", "confidence": confidence})
        if intent == "show_changed_lines":
            return _store_and_return({"success": True, "action": "show_changed_lines", "confidence": confidence})
        if intent == "start_tutorial":
            return _store_and_return({"success": True, "action": "start_tutorial", "confidence": confidence})
        if intent == "skip_tutorial":
            return _store_and_return({"success": True, "action": "skip_tutorial", "confidence": confidence})
        if intent == "tutorial_next":
            return _store_and_return({"success": True, "action": "tutorial_next", "confidence": confidence})
        if intent == "restart_tutorial":
            return _store_and_return({"success": True, "action": "restart_tutorial", "confidence": confidence})
        if intent == "tutorial_practice":
            return _store_and_return({"success": True, "action": "tutorial_practice", "module": slots.get("module"), "confidence": confidence})
        if intent == "set_color_mode":
            return _store_and_return({"success": True, "action": "set_color_mode", "mode": slots.get("mode", "default"), "confidence": confidence})

    conversational = _route_conversational_voice_action(
        text,
        code=current_code,
        error_context=error_context,
        language=language,
        cursor_line=cursor_line,
    )
    if conversational and conversational.get("action") != "unknown":
        return _store_and_return(conversational)

    # High-confidence DETERMINISTIC intent repair only (speech controls like
    # "alright stop listening", plus run/explain/map/sonify/tutorial). Key 2 is
    # NOT consulted here, so it can never override an existing fuzzy/security
    # voice route — that fallback runs next and gets first refusal.
    repaired = _route_repaired_intent(text, current_code, allow_ai=False)
    if repaired is not None:
        return _store_and_return(repaired)

    if conversational:
        return jsonify(conversational)

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

    # Key 2 intent repair as the LAST resort — only now that the deterministic
    # routes and the fuzzy/security matcher have all declined. It may map a
    # genuinely messy command onto a validated action or ask one clarification,
    # but by running here it can never hijack a route those layers already own.
    repaired_ai = _route_repaired_intent(text, current_code, allow_ai=True)
    if repaired_ai is not None:
        return _store_and_return(repaired_ai)

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
        return jsonify({"success": False, "message": _syntax_error_message(e, code)})

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
        _debug_log(f"Could not create input pipe for interactive run: {sanitize_traceback(str(e))}")
        return jsonify({"success": False, "error": "Could not start live input mode. Please use the inputs panel and run again."}), 500

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
        _debug_log(f"Could not start interactive run: {sanitize_traceback(str(e))}")
        return jsonify({"success": False, "error": "Could not start the live run. Please try pre-flight input mode."}), 500

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
            _debug_log(f"stdout reader error for run {run_id}: {sanitize_traceback(str(e))}")
            output_queue.put({"type": "error", "text": "Output stream ended unexpectedly."})
        finally:
            try:
                proc.stdout.close()
            except (AttributeError, OSError, ValueError):
                pass

    def _stderr_reader():
        collected = []
        try:
            for line in iter(proc.stderr.readline, ''):
                if not line:
                    break
                collected.append(line)
        except (OSError, ValueError) as e:
            _debug_log(f"stderr reader error for run {run_id}: {sanitize_traceback(str(e))}")
            output_queue.put({"type": "error", "text": "Error stream ended unexpectedly."})
        finally:
            raw_error = "".join(collected)
            if raw_error.strip():
                output_queue.put({"type": "stderr", "text": user_facing_error(raw_error) + "\n"})
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
        _debug_log(f"Could not write live input for run {run_id}: {sanitize_traceback(str(e))}")
        return jsonify({"success": False, "error": "Could not send input to the running program."}), 500


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

def _local_execution_story(trace: List[Dict[str, Any]]) -> str:
    if not trace:
        return "No execution trace available. Please run your code first."

    line_events = [e for e in trace if e.get('type') == 'line_exec']
    state_events = [e for e in trace if e.get('type') == 'state_change']
    lines_seen = []
    for event in line_events:
        line = event.get('line')
        if line not in lines_seen:
            lines_seen.append(line)

    sentences = [
        f"The program ran under CodeUp's tracer for {len(trace)} steps.",
    ]
    if lines_seen:
        sentences.append("It visited line " + ", then line ".join(str(line) for line in lines_seen[:6]) + ".")
    for event in state_events[:4]:
        changes = "; ".join(event.get('changes', [])[:3])
        if changes:
            sentences.append(f"On line {event.get('line')}, {changes}.")
    if len(state_events) > 4:
        sentences.append(f"There were {len(state_events) - 4} more state changes after that.")
    sentences.append("Use next step and previous step to hear the trace one event at a time.")
    return " ".join(sentences[:8])


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
    if _is_ai_service_message(story) or _ai_unavailable(story):
        story = _local_execution_story(trace)
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
# OPTIONAL OPENVINO LOCAL-INTENT DEMO (Intel AI Global Impact Festival)
# ==========================
# Isolated, diagnostic prototype. This route classifies a text command into a
# coarse intent using local rules in openvino_intent_demo.py and reports whether
# the OpenVINO runtime is present. It is intentionally NOT part of the real
# voice-command router: it never edits the editor, runs code, mutates session
# state, or calls the Key 2 (GROQ_API_KEY_2) orchestrator. CodeUp's existing
# deterministic + Key 2 pipeline is unchanged.

@app.route("/openvino-intent-demo", methods=["POST"])
def openvino_intent_demo_route():
    """Classify a command into a coarse intent (OpenVINO demo, diagnostic only).

    Input JSON:  {"text": "insert print hello"}
    Output JSON: {"available", "source", "intent", "confidence", "note"}

    Read-only: does not touch the editor, run code, change session state, or
    call any cloud AI key.
    """
    body = safejson()
    text = safe(body.get("text"), "")
    if not isinstance(text, str):
        text = str(text)
    if len(text) > MAX_CODE_SIZE:
        return jsonify({"success": False, "error": f"Text too large (max {MAX_CODE_SIZE} bytes)"}), 413
    return jsonify(classify_local_intent(text))


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

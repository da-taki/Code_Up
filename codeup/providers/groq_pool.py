"""Groq API key pool: health-checked, fairly load-balanced, bounded-queue,
streaming-aware capacity manager for every outbound Groq request in CodeUp.

Sits directly below ``codeup.integrations.groq_key_manager`` (whose failure
classification and message wording are reused here rather than duplicated)
and above the raw ``groq.Groq`` client. Every Groq-calling code path in
app.py goes through this one module, via :func:`acquire` (streaming) or
:func:`call_with_pool` (non-streaming, with one bounded retry on a
different key), so concurrency, fairness, health, cooldown and failover are
handled in exactly one place instead of being reimplemented per endpoint.

Design notes:
  - All state is in-memory and process-local. That is only safe because the
    current production Procfile runs a single gunicorn worker process (see
    Procfile / the Render deployment notes) - if that ever changes to
    multiple worker PROCESSES, this pool would need a shared backing store
    (e.g. Redis) to stay correct; it does not attempt to guess at that here.
  - A single lock (via a Condition) guards all mutable pool state. The
    actual network call to Groq always happens OUTSIDE that lock - the lock
    only ever protects bookkeeping (selection, counters, the wait queue).
  - Secrets: key VALUES are only ever stored in this process's memory and
    only ever handed to the caller inside a lease (``lease.key``) so it can
    build a ``groq.Groq(api_key=...)`` client. Nothing in this module logs,
    serializes, or returns a key value anywhere - see ``status()``.
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from codeup.integrations.groq_key_manager import (
    _friendly_message,  # reuse existing, already-tested wording - not duplicated here
    classify_groq_failure,
    load_groq_api_keys,
)

# Safe by construction: every call site below logs only internal_id / counts /
# failure_type - never a key value, header, or learner code.
_log = logging.getLogger("codeup.groq_pool")

# ---- configuration ----------------------------------------------------------

CSV_KEYS_ENV_NAME = "GROQ_API_KEYS"
_HEALTH_CHECK_MODEL_DEFAULT = "openai/gpt-oss-120b"

DEFAULT_MAX_CONCURRENCY_PER_KEY = 1
DEFAULT_QUEUE_MAX_SIZE = 100
DEFAULT_QUEUE_WAIT_TIMEOUT = 45

DEFAULT_KEY_COOLDOWN_SECONDS = 60           # timeout / 5xx / connection errors
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 30    # 429 with no Retry-After header
MAX_KEY_COOLDOWN_SECONDS = 900

# Shared-organization/project rate-limit heuristic: if several DISTINCT keys
# all receive 429s within a short rolling window, that is strong evidence
# the limit is not per-key, and we should stop cycling through the rest of
# the pool (which would just burn through every remaining key for nothing)
# and instead cool the whole pool down briefly.
SHARED_LIMIT_WINDOW_SECONDS = 20
SHARED_LIMIT_MIN_DISTINCT_KEYS = 3
SHARED_LIMIT_COOLDOWN_SECONDS = 20

RETRYABLE_FAILURE_TYPES = {
    "rate_limit", "quota_or_rate_limit", "token_limit", "timeout",
    "server_error", "connection_or_temporary",
}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def max_concurrency_per_key() -> int:
    return max(1, _int_env("GROQ_MAX_CONCURRENCY_PER_KEY", DEFAULT_MAX_CONCURRENCY_PER_KEY))


def queue_max_size() -> int:
    return max(0, _int_env("GROQ_QUEUE_MAX_SIZE", DEFAULT_QUEUE_MAX_SIZE))


def queue_wait_timeout() -> float:
    return max(1, _int_env("GROQ_QUEUE_WAIT_TIMEOUT", DEFAULT_QUEUE_WAIT_TIMEOUT))


def health_check_model() -> str:
    return os.environ.get("GROQ_MODEL") or _HEALTH_CHECK_MODEL_DEFAULT


# ---- key loading --------------------------------------------------------------

def _split_csv_keys(raw: str) -> List[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_pool_key_values(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Every configured Groq key value, deduplicated, in a stable order.

    Merges the new ``GROQ_API_KEYS`` comma-separated form with the existing
    ``GROQ_API_KEY`` / ``GROQ_API_KEY_2``..``_15`` numbered scheme (reusing
    ``groq_key_manager.load_groq_api_keys`` so both loaders can never
    disagree about what counts as a usable key) - both forms work at once
    and never silently shadow each other.
    """
    source = env if env is not None else os.environ
    seen: set = set()
    values: List[str] = []
    for key in _split_csv_keys(str(source.get(CSV_KEYS_ENV_NAME, "") or "")):
        if key not in seen:
            seen.add(key)
            values.append(key)
    for record in load_groq_api_keys(source):
        if record.key not in seen:
            seen.add(record.key)
            values.append(record.key)
    return values


# ---- errors -------------------------------------------------------------------

class GroqPoolError(Exception):
    """Base for all pool-level (not per-key) failures - always safe to show
    a learner: never contains a key value."""

    def __init__(self, user_message: str, *, reason: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.reason = reason


class GroqPoolExhausted(GroqPoolError):
    """No keys configured at all."""


class GroqPoolQueueFull(GroqPoolError):
    """The bounded FIFO queue was already at capacity."""


class GroqPoolTimeout(GroqPoolError):
    """Waited in the queue past GROQ_QUEUE_WAIT_TIMEOUT with no capacity freed."""


class GroqAllKeysFailedError(GroqPoolError):
    """Every attempted key failed (auth failure, or retries exhausted)."""


# ---- per-key state --------------------------------------------------------------

@dataclass
class KeyState:
    internal_id: str
    key: str  # secret - MUST NOT appear in status()/logs/__repr__
    active: int = 0
    total_requests: int = 0
    successful_requests: int = 0
    transient_failures: int = 0
    consecutive_failures: int = 0
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    last_failure_type: str = ""
    last_health_check_at: Optional[float] = None
    last_health_check_result: str = ""
    cooldown_until: float = 0.0
    disabled: bool = False
    disabled_reason: str = ""
    rate_limit: Dict[str, Any] = field(default_factory=dict)
    seq: int = 0  # fairness tie-break; bumped on each selection (round-robin)

    def __repr__(self) -> str:  # never let an accidental print/log leak the key
        return f"KeyState(id={self.internal_id!r}, active={self.active}, disabled={self.disabled})"


@dataclass
class _Waiter:
    id: str
    values: List[str]
    queued_at: float
    assigned: Optional[KeyState] = None
    cancelled: bool = False


class _Lease:
    """Returned by ``acquire()``. Use ``lease.key`` to build the Groq client
    and ``lease.internal_id`` for logging - never log ``lease.key``."""

    __slots__ = ("key_state",)

    def __init__(self, key_state: KeyState):
        self.key_state = key_state

    @property
    def internal_id(self) -> str:
        return self.key_state.internal_id

    @property
    def key(self) -> str:
        return self.key_state.key


def _extract_rate_limit_info(exc: Optional[BaseException]) -> Dict[str, Any]:
    """Best-effort extraction of Groq's rate-limit response headers. Never
    raises - any SDK shape mismatch just yields an empty dict."""
    if exc is None:
        return {}
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}

    def _get(name: str) -> Optional[str]:
        try:
            return headers.get(name)
        except Exception:
            return None

    info: Dict[str, Any] = {}
    retry_after = _get("retry-after")
    if retry_after is not None:
        try:
            info["retry_after"] = float(retry_after)
        except (TypeError, ValueError):
            pass
    for header_name in (
        "x-ratelimit-remaining-requests", "x-ratelimit-limit-requests", "x-ratelimit-reset-requests",
        "x-ratelimit-remaining-tokens", "x-ratelimit-limit-tokens", "x-ratelimit-reset-tokens",
    ):
        value = _get(header_name)
        if value is not None:
            info[header_name.replace("x-ratelimit-", "").replace("-", "_")] = value
    return info


# ---- the pool -------------------------------------------------------------------

class GroqKeyPool:
    def __init__(self, *, time_fn: Callable[[], float] = time.time):
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._keys: Dict[str, KeyState] = {}          # key value -> state (stable identity)
        self._waiters: "collections.deque[_Waiter]" = collections.deque()
        self._selection_seq = 0
        self._pool_cooldown_until = 0.0
        self._pool_cooldown_reason = ""
        self._recent_rate_limit_events: "collections.deque[tuple]" = collections.deque()
        self._shared_limit_suspected = False
        self._shared_limit_detected_at: Optional[float] = None

    # -- internal helpers (all require self._lock held) ---------------------------

    def _resolve_values(self, *, env=None, extra_keys=None) -> List[str]:
        seen: set = set()
        values: List[str] = []
        for raw in (extra_keys or ()):
            value = str(raw or "").strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
        for value in load_pool_key_values(env):
            if value not in seen:
                seen.add(value)
                values.append(value)
        return values

    def _sync_and_get_locked(self, values: Sequence[str]) -> List[KeyState]:
        existing_ids = {ks.internal_id for ks in self._keys.values()}
        for value in values:
            if value in self._keys:
                continue
            idx = len(self._keys) + 1
            internal_id = f"groq-key-{idx:02d}"
            while internal_id in existing_ids:
                idx += 1
                internal_id = f"groq-key-{idx:02d}"
            existing_ids.add(internal_id)
            self._keys[value] = KeyState(internal_id=internal_id, key=value)
        return [self._keys[v] for v in values]

    def _eligible_locked(self, states: Sequence[KeyState], now: float, max_concurrency: int) -> List[KeyState]:
        if self._pool_cooldown_until > now:
            return []
        return [k for k in states if not k.disabled and k.cooldown_until <= now and k.active < max_concurrency]

    def _select_locked(self, states: Sequence[KeyState], now: float, max_concurrency: int) -> Optional[KeyState]:
        eligible = self._eligible_locked(states, now, max_concurrency)
        if not eligible:
            return None
        eligible.sort(key=lambda k: (k.active, k.seq))
        chosen = eligible[0]
        self._selection_seq += 1
        chosen.seq = self._selection_seq  # sent to the back of the round-robin order
        return chosen

    def _states_for_waiter_locked(self, waiter: _Waiter) -> List[KeyState]:
        return [self._keys[v] for v in waiter.values if v in self._keys]

    def _dispatch_locked(self) -> None:
        """FIFO: serve the oldest waiter first; stop at the first one that
        still can't be served rather than skipping ahead (no starvation)."""
        now = self._time_fn()
        max_concurrency = max_concurrency_per_key()
        while self._waiters:
            waiter = self._waiters[0]
            if waiter.cancelled:
                self._waiters.popleft()
                continue
            states = self._states_for_waiter_locked(waiter)
            key = self._select_locked(states, now, max_concurrency)
            if key is None:
                break
            key.active += 1
            waiter.assigned = key
            self._waiters.popleft()

    def _note_rate_limit_event_locked(self, internal_id: str, now: float) -> None:
        self._recent_rate_limit_events.append((now, internal_id))
        cutoff = now - SHARED_LIMIT_WINDOW_SECONDS
        while self._recent_rate_limit_events and self._recent_rate_limit_events[0][0] < cutoff:
            self._recent_rate_limit_events.popleft()
        distinct = {kid for _, kid in self._recent_rate_limit_events}
        if len(distinct) >= SHARED_LIMIT_MIN_DISTINCT_KEYS:
            self._pool_cooldown_until = max(self._pool_cooldown_until, now + SHARED_LIMIT_COOLDOWN_SECONDS)
            self._pool_cooldown_reason = "shared_rate_limit_suspected"
            self._shared_limit_suspected = True
            if self._shared_limit_detected_at is None:
                self._shared_limit_detected_at = now

    def _cooldown_seconds_for(self, failure_type: str, rate_limit_info: Dict[str, Any]) -> float:
        retry_after = rate_limit_info.get("retry_after") if rate_limit_info else None
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            return min(MAX_KEY_COOLDOWN_SECONDS, max(1.0, float(retry_after)))
        if failure_type in ("rate_limit", "quota_or_rate_limit", "token_limit"):
            return DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
        return DEFAULT_KEY_COOLDOWN_SECONDS

    def _state_label(self, key: KeyState, now: float, max_concurrency: int) -> str:
        if key.disabled:
            return "DISABLED"
        if key.cooldown_until > now:
            if key.last_failure_type in ("rate_limit", "quota_or_rate_limit", "token_limit"):
                return "COOLDOWN"
            return "UNHEALTHY"
        if key.active >= max_concurrency:
            return "BUSY"
        return "HEALTHY"

    # -- public: acquire / release --------------------------------------------------

    @contextmanager
    def acquire(self, *, timeout: Optional[float] = None, env=None, extra_keys=None) -> Iterator[_Lease]:
        """Block (up to ``timeout`` seconds, queueing FIFO if needed) for one
        unit of Groq capacity, then yield a lease. Releases automatically on
        exit - success if the ``with`` block completes normally, failure
        (classified, possibly cooling the key down) if it raises, including
        on generator ``.close()`` for a streaming caller."""
        if timeout is None:
            timeout = queue_wait_timeout()
        values = self._resolve_values(env=env, extra_keys=extra_keys)
        if not values:
            raise GroqPoolExhausted(
                "AI service not configured. Add a Groq API key in settings, or install Ollama locally for offline AI.",
                reason="not_configured",
            )

        deadline = self._time_fn() + timeout
        key: Optional[KeyState] = None
        with self._cv:
            states = self._sync_and_get_locked(values)
            max_concurrency = max_concurrency_per_key()
            now = self._time_fn()
            if not self._waiters:
                key = self._select_locked(states, now, max_concurrency)
            if key is not None:
                key.active += 1
            else:
                limit = queue_max_size()
                if len(self._waiters) >= limit:  # GROQ_QUEUE_MAX_SIZE=0 means "never queue"
                    _log.info("Groq pool queue full (depth=%d) - rejecting new request", len(self._waiters))
                    raise GroqPoolQueueFull(
                        "AI assistance is busy right now. Please try again shortly. Your code is safe.",
                        reason="queue_full",
                    )
                waiter = _Waiter(id=str(uuid.uuid4()), values=values, queued_at=now)
                self._waiters.append(waiter)
                _log.debug("Groq queue depth: %d", len(self._waiters))
                try:
                    while waiter.assigned is None and not waiter.cancelled:
                        remaining = deadline - self._time_fn()
                        if remaining <= 0:
                            raise GroqPoolTimeout(
                                "AI assistance is busy right now. Please try again shortly. Your code is safe.",
                                reason="queue_timeout",
                            )
                        # Bounded wait chunk: lets a queued waiter notice a
                        # key's cooldown expiring on its own, not only on the
                        # next explicit release() notify.
                        self._cv.wait(min(remaining, 1.0))
                        if waiter.assigned is None:
                            self._dispatch_locked()
                    if waiter.assigned is None:  # cancelled without assignment
                        raise GroqPoolTimeout(
                            "AI assistance is busy right now. Please try again shortly. Your code is safe.",
                            reason="queue_timeout",
                        )
                    key = waiter.assigned
                except BaseException:
                    waiter.cancelled = True
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
                    if waiter.assigned is not None:
                        # Freed right as we gave up - hand it back immediately.
                        waiter.assigned.active = max(0, waiter.assigned.active - 1)
                        self._dispatch_locked()
                    raise

        _log.debug("%s acquired", key.internal_id)
        lease = _Lease(key)
        try:
            yield lease
        except GeneratorExit:
            # A streaming caller's generator was closed early (client
            # disconnect, or simply not fully consumed) - that is not the
            # key's fault, so release the slot WITHOUT classifying it as a
            # provider failure (no cooldown, no penalty).
            self._release(lease, success=None, exc=None)
            raise
        except BaseException as exc:
            self._release(lease, success=False, exc=exc)
            raise
        else:
            self._release(lease, success=True, exc=None)

    def _release(self, lease: _Lease, *, success: Optional[bool], exc: Optional[BaseException]) -> None:
        with self._cv:
            key = lease.key_state
            key.active = max(0, key.active - 1)
            key.total_requests += 1
            now = self._time_fn()
            if success is True:
                key.successful_requests += 1
                key.consecutive_failures = 0
                key.last_success_at = now
                _log.debug("%s request completed", key.internal_id)
            elif success is False:
                failure_type, _retryable = classify_groq_failure(exc) if exc is not None else ("unknown", True)
                key.last_failure_at = now
                key.last_failure_type = failure_type
                key.consecutive_failures += 1
                rate_limit_info = _extract_rate_limit_info(exc)
                if rate_limit_info:
                    key.rate_limit = rate_limit_info
                if failure_type == "authentication":
                    key.disabled = True
                    key.disabled_reason = "authentication_failed"
                    _log.warning("%s disabled after an authentication failure", key.internal_id)
                else:
                    key.transient_failures += 1
                    cooldown = self._cooldown_seconds_for(failure_type, rate_limit_info)
                    key.cooldown_until = max(key.cooldown_until, now + cooldown)
                    _log.warning(
                        "%s entered cooldown for %.0fs after a %s failure",
                        key.internal_id, cooldown, failure_type,
                    )
                    if failure_type in ("rate_limit", "quota_or_rate_limit"):
                        self._note_rate_limit_event_locked(key.internal_id, now)
            else:
                _log.debug("%s released (generator closed early, no penalty)", key.internal_id)
            self._dispatch_locked()
            self._cv.notify_all()

    # -- public: high-level non-streaming call with one bounded retry ------------------

    def call_with_pool(
        self,
        request_fn: Callable[[str, str], Any],
        *,
        timeout: Optional[float] = None,
        env=None,
        extra_keys=None,
        max_attempts: int = 2,
    ) -> Any:
        """request_fn(api_key, internal_id) -> result. Acquires capacity,
        calls request_fn, releases. On a retryable failure, tries once more
        with a (likely different) key - never more than ``max_attempts``
        total key attempts, and never retries after a non-retryable
        (authentication) failure or after the queue itself times out/is
        full (those are pool-level conditions, not per-key ones)."""
        max_attempts = max(1, int(max_attempts))
        last_failure_type = "unknown"
        for _attempt in range(max_attempts):
            try:
                with self.acquire(timeout=timeout, env=env, extra_keys=extra_keys) as lease:
                    return request_fn(lease.key, lease.internal_id)
            except (GroqPoolExhausted, GroqPoolQueueFull, GroqPoolTimeout):
                raise  # pool-level condition (not a per-key one) - never retried here
            except Exception as exc:
                # acquire()'s own __exit__ already classified this against the
                # key (cooldown/disable) before it reached us; classify again
                # only to decide whether THIS call should retry with a new key.
                failure_type, retryable = classify_groq_failure(exc)
                last_failure_type = failure_type
                if not retryable:
                    raise GroqAllKeysFailedError(_friendly_message(failure_type), reason=failure_type) from None
                continue
        raise GroqAllKeysFailedError(_friendly_message(last_failure_type), reason=last_failure_type)

    # -- public: health checks ------------------------------------------------------

    def health_check_one(self, key_value: str) -> str:
        """Classifies via ONE tiny request. Returns a short result string;
        never raises. Updates that key's tracked state (creating it if not
        already known - lets the manual script check keys even before any
        real traffic has flowed through the pool)."""
        result = "healthy"
        exc_seen: Optional[BaseException] = None
        try:
            from groq import Groq
            client = Groq(api_key=key_value)
            client.chat.completions.create(
                model=health_check_model(),
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
        except Exception as exc:  # noqa: BLE001 - classify below, never re-raise from a health check
            exc_seen = exc
            failure_type, _retryable = classify_groq_failure(exc)
            if failure_type == "authentication":
                result = "invalid"
            elif failure_type in ("rate_limit", "quota_or_rate_limit", "token_limit"):
                result = "rate_limited"
            else:
                result = "unhealthy"

        with self._cv:
            states = self._sync_and_get_locked([key_value])
            key = states[0]
            now = self._time_fn()
            key.last_health_check_at = now
            key.last_health_check_result = result
            if result == "healthy":
                key.disabled = False
                key.disabled_reason = ""
                key.cooldown_until = 0.0
                key.consecutive_failures = 0
                key.last_failure_type = ""
            elif result == "invalid":
                key.disabled = True
                key.disabled_reason = "authentication_failed"
            else:
                rate_limit_info = _extract_rate_limit_info(exc_seen)
                if rate_limit_info:
                    key.rate_limit = rate_limit_info
                failure_type, _r = classify_groq_failure(exc_seen) if exc_seen else ("unknown", True)
                key.last_failure_type = failure_type
                key.cooldown_until = now + self._cooldown_seconds_for(failure_type, rate_limit_info)
                if result == "rate_limited":
                    self._note_rate_limit_event_locked(key.internal_id, now)
            self._cv.notify_all()
        return result

    def health_check_all(self, *, env=None, extra_keys=None, max_workers: int = 5) -> "collections.OrderedDict[str, str]":
        """Runs health_check_one for every configured key, with modest
        parallelism (still exactly one request per key). Returns
        {internal_id: result}, in configured order."""
        values = self._resolve_values(env=env, extra_keys=extra_keys)
        with self._cv:
            states = self._sync_and_get_locked(values)
        id_by_value = {v: s.internal_id for v, s in zip(values, states)}

        results: Dict[str, str] = {}
        if not values:
            return collections.OrderedDict()

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(values)))) as pool:
            futures = {pool.submit(self.health_check_one, v): v for v in values}
            for future, value in futures.items():
                try:
                    results[value] = future.result()
                except Exception:
                    results[value] = "unhealthy"

        return collections.OrderedDict((id_by_value[v], results[v]) for v in values)

    # -- public: diagnostics (never includes a secret) -------------------------------

    def status(self, *, env=None, extra_keys=None) -> Dict[str, Any]:
        values = self._resolve_values(env=env, extra_keys=extra_keys)
        with self._cv:
            states = self._sync_and_get_locked(values) if values else []
            now = self._time_fn()
            max_concurrency = max_concurrency_per_key()
            counts = {"healthy": 0, "busy": 0, "cooldown": 0, "unhealthy": 0, "disabled": 0}
            rows = []
            for key in states:
                label = self._state_label(key, now, max_concurrency)
                counts[label.lower()] = counts.get(label.lower(), 0) + 1
                rows.append({
                    "id": key.internal_id,
                    "state": label,
                    "active": key.active,
                    "total_requests": key.total_requests,
                    "successful_requests": key.successful_requests,
                    "transient_failures": key.transient_failures,
                    "consecutive_failures": key.consecutive_failures,
                    "last_success_at": key.last_success_at,
                    "last_failure_at": key.last_failure_at,
                    "last_failure_type": key.last_failure_type,
                    "last_health_check_at": key.last_health_check_at,
                    "last_health_check_result": key.last_health_check_result,
                    "cooldown_remaining_seconds": round(max(0.0, key.cooldown_until - now), 1),
                    "rate_limit": dict(key.rate_limit),
                })
            return {
                "configured": len(states),
                "counts": counts,
                "active_requests": sum(k.active for k in states),
                "queued_requests": len(self._waiters),
                "max_concurrency_per_key": max_concurrency,
                "queue_max_size": queue_max_size(),
                "queue_wait_timeout": queue_wait_timeout(),
                "pool_cooldown_remaining_seconds": round(max(0.0, self._pool_cooldown_until - now), 1),
                "pool_cooldown_reason": self._pool_cooldown_reason,
                "shared_limit_suspected": self._shared_limit_suspected,
                "key_rows": rows,  # not "keys" - that would shadow dict.keys() for template/JS consumers
            }

    def reset(self) -> None:
        """Test/diagnostic use only - clears all in-memory pool state."""
        with self._cv:
            self._keys.clear()
            self._waiters.clear()
            self._selection_seq = 0
            self._pool_cooldown_until = 0.0
            self._pool_cooldown_reason = ""
            self._recent_rate_limit_events.clear()
            self._shared_limit_suspected = False
            self._shared_limit_detected_at = None


_pool = GroqKeyPool()


def get_pool() -> GroqKeyPool:
    return _pool


def acquire(*, timeout: Optional[float] = None, env=None, extra_keys=None):
    return _pool.acquire(timeout=timeout, env=env, extra_keys=extra_keys)


def call_with_pool(request_fn, *, timeout=None, env=None, extra_keys=None, max_attempts: int = 2):
    return _pool.call_with_pool(request_fn, timeout=timeout, env=env, extra_keys=extra_keys, max_attempts=max_attempts)


def status(*, env=None, extra_keys=None) -> Dict[str, Any]:
    return _pool.status(env=env, extra_keys=extra_keys)


def health_check_all(*, env=None, extra_keys=None):
    return _pool.health_check_all(env=env, extra_keys=extra_keys)


def has_configured_keys(*, env=None, extra_keys=None) -> bool:
    return bool(_pool._resolve_values(env=env, extra_keys=extra_keys))

"""Central server-side Groq API key failover.

Loads ``GROQ_API_KEY`` followed by ``GROQ_API_KEY_2`` through
``GROQ_API_KEY_15``. Keys are never exposed through status or exception text;
callers receive only key indexes and friendly failure messages.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


GROQ_KEY_ENV_NAMES = ["GROQ_API_KEY"] + [f"GROQ_API_KEY_{idx}" for idx in range(2, 16)]
DEFAULT_COOLDOWN_SECONDS = 600

_PLACEHOLDER_KEYS = {
    "",
    "insert_api_key_here",
    "your_groq_api_key",
    "your_groq_api_key_here",
    "your_second_groq_api_key",
}


@dataclass(frozen=True)
class GroqKeyRecord:
    index: int
    env_name: str
    key: str


@dataclass
class GroqKeyStatus:
    index: int
    env_name: str
    last_failure_type: str = ""
    cooldown_until: float = 0.0
    success_count: int = 0
    failure_count: int = 0


class GroqFailoverError(Exception):
    """Sanitized user-facing failure for all-key Groq failures."""

    def __init__(self, user_message: str, *, failure_type: str = "", attempts: Optional[List[Dict[str, Any]]] = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.failure_type = failure_type
        self.attempts = attempts or []


def _clean_key(value: Any) -> str:
    return str(value or "").strip()


def _is_usable_key(value: Any) -> bool:
    key = _clean_key(value)
    if not key:
        return False
    lowered = key.lower()
    return lowered not in _PLACEHOLDER_KEYS and not lowered.startswith("your_")


def load_groq_api_keys(env: Optional[Mapping[str, str]] = None) -> List[GroqKeyRecord]:
    """Load configured Groq keys from environment in stable failover order."""

    source = env if env is not None else os.environ
    records: List[GroqKeyRecord] = []
    seen: set[str] = set()
    for position, env_name in enumerate(GROQ_KEY_ENV_NAMES, start=1):
        key = _clean_key(source.get(env_name, ""))
        if not _is_usable_key(key) or key in seen:
            continue
        seen.add(key)
        records.append(GroqKeyRecord(position, env_name, key))
    return records


def _extra_key_records(extra_keys: Optional[Sequence[str]], seen: set[str]) -> List[GroqKeyRecord]:
    records: List[GroqKeyRecord] = []
    for offset, value in enumerate(extra_keys or (), start=1):
        key = _clean_key(value)
        if not _is_usable_key(key) or key in seen:
            continue
        seen.add(key)
        records.append(GroqKeyRecord(-offset, "session", key))
    return records


def _status_code(exc: BaseException) -> Optional[int]:
    for name in ("status_code", "status", "code"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def classify_groq_failure(exc: BaseException) -> Tuple[str, bool]:
    """Return ``(failure_type, retryable)`` for a Groq/API exception."""

    status = _status_code(exc)
    text = f"{type(exc).__name__}: {exc}".lower()
    if status == 429 or "429" in text or "too many requests" in text:
        return "rate_limit", True
    if "quota" in text or "rate limit" in text or "tokens per minute" in text or re.search(r"\btpm\b", text):
        return "quota_or_rate_limit", True
    if "token limit" in text or "token_limit" in text:
        return "token_limit", True
    if isinstance(exc, TimeoutError) or status in {408, 504} or "timeout" in text or "timed out" in text:
        return "timeout", True
    if status is not None and 500 <= status <= 599:
        return "server_error", True
    if any(marker in text for marker in (
        "connection", "network", "dns", "connectionreseterror", "connectionerror",
        "readerror", "remoteprotocolerror", "service unavailable", "bad gateway",
        "gateway timeout", "temporarily unavailable", "overloaded",
    )):
        return "connection_or_temporary", True
    if status in {401, 403} or "unauthorized" in text or "authentication" in text or "invalid api key" in text:
        return "authentication", False
    return "unknown", False


def _friendly_message(failure_type: str, *, all_on_cooldown: bool = False) -> str:
    if all_on_cooldown:
        return "The cloud AI is cooling down after temporary failures. Please wait a moment and try again."
    if failure_type == "authentication":
        return "Cloud AI authentication failed. Please ask your teacher to check the server API key."
    if failure_type in {"rate_limit", "quota_or_rate_limit", "token_limit"}:
        return "The cloud AI is busy or out of quota. Please wait a moment and try again."
    if failure_type == "timeout":
        return "The cloud AI took too long to respond. Try a shorter request."
    if failure_type in {"server_error", "connection_or_temporary"}:
        return "The cloud AI is temporarily unavailable. Core CodeUp features still work."
    return "AI service had a problem. Core CodeUp features still work."


class GroqKeyManager:
    """Stable first-healthy-key failover with bounded one-attempt-per-key retry."""

    def __init__(
        self,
        *,
        cooldown_seconds: Optional[int] = None,
        time_fn: Callable[[], float] = time.time,
    ):
        if cooldown_seconds is None:
            cooldown_seconds = int(os.environ.get("GROQ_KEY_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS))
            cooldown_seconds = max(300, min(int(cooldown_seconds), 900))
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self._time_fn = time_fn
        self._lock = threading.RLock()
        self._statuses: Dict[int, GroqKeyStatus] = {}

    def configured_records(
        self,
        *,
        env: Optional[Mapping[str, str]] = None,
        extra_keys: Optional[Sequence[str]] = None,
    ) -> List[GroqKeyRecord]:
        seen: set[str] = set()
        records = _extra_key_records(extra_keys, seen)
        records.extend(load_groq_api_keys(env))
        unique: List[GroqKeyRecord] = []
        seen_again: set[str] = set()
        for record in records:
            if record.key in seen_again:
                continue
            seen_again.add(record.key)
            unique.append(record)
        return unique

    def has_keys(self, *, env: Optional[Mapping[str, str]] = None, extra_keys: Optional[Sequence[str]] = None) -> bool:
        return bool(self.configured_records(env=env, extra_keys=extra_keys))

    def status(
        self,
        *,
        env: Optional[Mapping[str, str]] = None,
        extra_keys: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        records = self.configured_records(env=env, extra_keys=extra_keys)
        with self._lock:
            out = []
            for record in records:
                status = self._statuses.get(record.index) or GroqKeyStatus(record.index, record.env_name)
                out.append({
                    "key_index": status.index,
                    "env_name": status.env_name,
                    "last_failure_type": status.last_failure_type,
                    "cooldown_until": status.cooldown_until,
                    "success_count": status.success_count,
                    "failure_count": status.failure_count,
                })
            return out

    def mark_unhealthy(self, index: int, failure_type: str, *, now: Optional[float] = None) -> None:
        current = self._time_fn() if now is None else now
        with self._lock:
            status = self._statuses.setdefault(index, GroqKeyStatus(index, "session" if index < 0 else GROQ_KEY_ENV_NAMES[index - 1]))
            status.last_failure_type = failure_type
            status.failure_count += 1
            status.cooldown_until = current + self.cooldown_seconds

    def reset(self) -> None:
        with self._lock:
            self._statuses.clear()

    def call_with_failover(
        self,
        request_fn: Callable[[str, int], Any],
        *,
        env: Optional[Mapping[str, str]] = None,
        extra_keys: Optional[Sequence[str]] = None,
    ) -> Any:
        records = self.configured_records(env=env, extra_keys=extra_keys)
        if not records:
            raise GroqFailoverError(
                "AI service not configured. Add a Groq API key in settings, or install Ollama locally for offline AI.",
                failure_type="not_configured",
            )

        now = self._time_fn()
        attempts: List[Dict[str, Any]] = []
        skipped_for_cooldown = 0
        last_failure_type = ""

        for record in records:
            with self._lock:
                status = self._statuses.setdefault(record.index, GroqKeyStatus(record.index, record.env_name))
                status.env_name = record.env_name
                if status.cooldown_until and status.cooldown_until > now:
                    skipped_for_cooldown += 1
                    continue

            try:
                result = request_fn(record.key, record.index)
            except Exception as exc:  # intentionally broad: API clients raise varied exception types
                failure_type, retryable = classify_groq_failure(exc)
                last_failure_type = failure_type
                attempts.append({"key_index": record.index, "failure_type": failure_type})
                with self._lock:
                    status = self._statuses.setdefault(record.index, GroqKeyStatus(record.index, record.env_name))
                    status.last_failure_type = failure_type
                    status.failure_count += 1
                    if retryable:
                        status.cooldown_until = now + self.cooldown_seconds
                if retryable:
                    continue
                raise GroqFailoverError(
                    _friendly_message(failure_type),
                    failure_type=failure_type,
                    attempts=attempts,
                ) from None

            with self._lock:
                status = self._statuses.setdefault(record.index, GroqKeyStatus(record.index, record.env_name))
                status.success_count += 1
                status.last_failure_type = ""
                status.cooldown_until = 0.0
            return result

        if attempts:
            raise GroqFailoverError(
                _friendly_message(last_failure_type),
                failure_type=last_failure_type,
                attempts=attempts,
            )
        if skipped_for_cooldown:
            raise GroqFailoverError(
                _friendly_message("", all_on_cooldown=True),
                failure_type="cooldown",
                attempts=[],
            )
        raise GroqFailoverError(_friendly_message("unknown"), failure_type="unknown", attempts=attempts)

    def redact_known_keys(
        self,
        text: Any,
        *,
        env: Optional[Mapping[str, str]] = None,
        extra_keys: Optional[Sequence[str]] = None,
    ) -> str:
        value = str(text or "")
        for record in self.configured_records(env=env, extra_keys=extra_keys):
            if record.key:
                value = value.replace(record.key, "<redacted-api-key>")
        return value


_manager = GroqKeyManager()


def get_groq_key_manager() -> GroqKeyManager:
    return _manager


def redact_known_keys(text: Any, *, extra_keys: Optional[Sequence[str]] = None) -> str:
    return _manager.redact_known_keys(text, extra_keys=extra_keys)

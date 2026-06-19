
from __future__ import annotations

import io
import time
import zipfile
from typing import Dict, List, Tuple

EXCLUDE_DIR_NAMES = {
    "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".git",
    ".claude", ".vscode", ".idea", ".mypy_cache", ".ruff_cache", ".eggs",
    "build", "dist", ".tox",
}
EXCLUDE_FILE_NAMES = {".env", ".gitignore", ".dockerignore"}
EXCLUDE_FILE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".pyc", ".pyo", ".log")

SECRET_CONTENT_MARKERS = (
    "API_KEY=", "SECRET=", "TOKEN=", "PASSWORD=",
    "GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
    "-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)

MAX_EXPORT_FILES = 200
MAX_EXPORT_TOTAL_BYTES = 5_000_000


def _parts(relpath: str) -> List[str]:
    return [p for p in str(relpath or "").replace("\\", "/").split("/") if p]


def is_excluded_path(relpath: str) -> bool:
    parts = _parts(relpath)
    if not parts:
        return True
    for segment in parts:
        if segment in EXCLUDE_DIR_NAMES:
            return True
    name = parts[-1]
    low = name.lower()
    if name in EXCLUDE_FILE_NAMES or low == ".env" or low.startswith(".env."):
        return True
    if any(low.endswith(suffix) for suffix in EXCLUDE_FILE_SUFFIXES):
        return True
    return False


def content_has_secret(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in SECRET_CONTENT_MARKERS)


def safe_file_map(files: Dict[str, str]) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    kept: Dict[str, str] = {}
    excluded: List[Dict[str, str]] = []
    total = 0
    for path in sorted((files or {}).keys()):
        content = files[path] if isinstance(files.get(path), str) else str(files.get(path) or "")
        if is_excluded_path(path):
            excluded.append({"path": path, "reason": "excluded_path"})
            continue
        if content_has_secret(content):
            excluded.append({"path": path, "reason": "secret_content"})
            continue
        size = len(content.encode("utf-8", "replace"))
        if total + size > MAX_EXPORT_TOTAL_BYTES:
            excluded.append({"path": path, "reason": "too_large"})
            continue
        total += size
        kept[path] = content
        if len(kept) >= MAX_EXPORT_FILES:
            break
    return kept, excluded


def build_zip_bytes(files: Dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files.keys()):
            zf.writestr(path, files[path] if isinstance(files[path], str) else str(files[path] or ""))
    return buffer.getvalue()


def export_filename(prefix: str = "codeup_project") -> str:
    safe_prefix = "".join(ch for ch in str(prefix or "codeup_project") if ch.isalnum() or ch in "_-") or "codeup_project"
    return f"{safe_prefix}_{time.strftime('%Y%m%d_%H%M%S')}.zip"


def prepare_export(files: Dict[str, str], *, prefix: str = "codeup_project") -> Dict[str, object]:
    kept, excluded = safe_file_map(files or {})
    if not kept:
        return {"success": False, "error": "Nothing safe to export.", "excluded": excluded}
    data = build_zip_bytes(kept)
    return {
        "success": True,
        "filename": export_filename(prefix),
        "bytes": data,
        "included": sorted(kept.keys()),
        "excluded": excluded,
        "file_count": len(kept),
        "total_bytes": len(data),
    }


from __future__ import annotations

import io
import re
import time
import zipfile
from typing import Dict, List, Tuple

ACCESSIBILITY_NOTES = """# Accessibility handoff

Open this folder in Visual Studio Code, then confirm that the editor accessibility
setting is on. The included `.vscode/settings.json` sets
`editor.accessibilitySupport` to `on` and enables word wrap.

CodeUp can help beginners with spoken debugging and deterministic guidance. When
you are ready for a professional editor, use VS Code with NVDA, JAWS, Windows
Narrator, VoiceOver, or Orca as appropriate for your operating system.

Common command equivalents:

- go to definition
- find references
- next error
- run
- next step, previous step, and repeat step for trace navigation
- safe rename

CodeUp is designed to work alongside assistive technology. It does not replace a
screen reader, Braille display, or VS Code.
"""

VSCODE_ACCESSIBILITY_SETTINGS = """{
  "editor.accessibilitySupport": "on",
  "editor.wordWrap": "on"
}
"""

EXCLUDE_DIR_NAMES = {
    "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".git",
    ".claude", ".vscode", ".idea", ".mypy_cache", ".ruff_cache", ".eggs",
    "build", "dist", ".tox",
}
EXCLUDE_FILE_NAMES = {".env", ".gitignore", ".dockerignore"}
EXCLUDE_FILE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".pyc", ".pyo", ".log")

SECRET_CONTENT_MARKERS = (
    "GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
    "-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)\b(?:[A-Z][A-Z0-9_]*_)?(?:API_KEY|SECRET|TOKEN|PASSWORD)"
    r"(?:_[A-Z0-9_]+)?"
    r"\s*(?:=|:)\s*(?:(['\"])[^\r\n]*?\1|[^\s#,'\"}\])(;]+(?=\s*(?:#.*)?$))"
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
    upper_text = text.upper()
    return any(marker in upper_text for marker in SECRET_CONTENT_MARKERS) or bool(
        SECRET_ASSIGNMENT_RE.search(text)
    )


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
    kept["ACCESSIBILITY_NOTES.md"] = ACCESSIBILITY_NOTES
    kept[".vscode/settings.json"] = VSCODE_ACCESSIBILITY_SETTINGS
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

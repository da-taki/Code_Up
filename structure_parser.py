"""
Sandboxed Virtual File System for CodeUp

Provides safe file operations within a confined workspace directory.
All file operations are restricted to the workspace folder created at init time.
"""

import os
import json
import shutil          
import tempfile
import threading
from pathlib import Path
from typing import Optional, Dict, List

_WORKSPACE_INFO_MAX_FILES = 5000


class SandboxedFileSystem:
    """Manages file operations within a sandboxed workspace."""

    # Maximum file content size enforced on write.
    MAX_FILE_SIZE = 5_000_000  # bytes

    def __init__(self, workspace_dir: Optional[str] = None) -> None:
 
        if workspace_dir is None:
            self.workspace_dir = tempfile.mkdtemp(prefix="codeup_workspace_")
        else:
            self.workspace_dir = workspace_dir
            os.makedirs(self.workspace_dir, exist_ok=True)

        # Ensure the stored path is absolute and canonical
        self.workspace_dir = os.path.abspath(self.workspace_dir)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _validate_path(self, filepath: str) -> str:

        abs_path = os.path.abspath(os.path.join(self.workspace_dir, filepath))
        try:
            Path(abs_path).relative_to(Path(self.workspace_dir))
        except ValueError:
            raise ValueError(f"Path '{filepath}' is outside workspace")
        return abs_path

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def write(self, filepath: str, content: str, encoding: str = "utf-8") -> Dict:

        try:
            raw: bytes = content.encode(encoding)
            if len(raw) > self.MAX_FILE_SIZE:
                raise ValueError(
                    f"File exceeds maximum size of {self.MAX_FILE_SIZE} bytes"
                )

            abs_path = self._validate_path(filepath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)

            # Write pre-encoded bytes directly — no second encode pass
            with open(abs_path, "wb") as f:
                f.write(raw)

            return {
                "success": True,
                "path": filepath,
                "size": len(raw),
            }
        except Exception as e:
            return {
                "success": False,
                "path": filepath,
                "error": str(e),
            }

    def read(self, filepath: str, encoding: str = "utf-8") -> Dict:

        try:
            abs_path = self._validate_path(filepath)

            if not os.path.exists(abs_path):
                return {"success": False, "path": filepath, "error": "File not found"}

            if not os.path.isfile(abs_path):
                return {"success": False, "path": filepath, "error": "Path is not a file"}

            with open(abs_path, "r", encoding=encoding) as f:
                content = f.read()

            return {
                "success": True,
                "path": filepath,
                "content": content,
                "size": len(content),
            }
        except Exception as e:
            return {"success": False, "path": filepath, "error": str(e)}

    def delete(self, filepath: str) -> Dict:

        try:
            abs_path = self._validate_path(filepath)

            if not os.path.exists(abs_path):
                return {"success": False, "path": filepath, "error": "File not found"}

            if not os.path.isfile(abs_path):
                return {"success": False, "path": filepath, "error": "Path is not a file"}

            os.remove(abs_path)
            return {"success": True, "path": filepath}
        except Exception as e:
            return {"success": False, "path": filepath, "error": str(e)}

    def list_files(self, dirpath: str = ".") -> Dict:
        try:
            abs_path = self._validate_path(dirpath)

            if not os.path.exists(abs_path):
                return {"success": False, "error": "Directory not found", "files": [], "dirs": []}

            if not os.path.isdir(abs_path):
                return {"success": False, "error": "Path is not a directory", "files": [], "dirs": []}

            files: List[Dict] = []
            dirs: List[str] = []

            for entry in os.listdir(abs_path):
                entry_path = os.path.join(abs_path, entry)
                if os.path.isfile(entry_path):
                    files.append({"name": entry, "size": os.path.getsize(entry_path)})
                elif os.path.isdir(entry_path):
                    dirs.append(entry)

            return {
                "success": True,
                "files": sorted(files, key=lambda x: x["name"]),
                "dirs": sorted(dirs),
            }
        except Exception as e:
            return {"success": False, "error": str(e), "files": [], "dirs": []}

    def get_workspace_info(self) -> Dict:

        try:
            total_files = 0
            total_size = 0
            truncated = False

            for root, dirs, files in os.walk(self.workspace_dir):
                for file in files:
                    total_files += 1
                    if total_files > _WORKSPACE_INFO_MAX_FILES:
                        truncated = True
                        break
                    total_size += os.path.getsize(os.path.join(root, file))
                if truncated:
                    break

            result: Dict = {
                "success": True,
                "workspace": self.workspace_dir,
                "total_files": total_files,
                "total_size": total_size,
                "available": True,
            }
            if truncated:
                result["truncated"] = True
                result["truncated_at"] = _WORKSPACE_INFO_MAX_FILES
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def cleanup(self) -> Dict:
        try:
            if os.path.exists(self.workspace_dir):
                shutil.rmtree(self.workspace_dir)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Thread-safe lazy singleton
# ---------------------------------------------------------------------------

_sandbox: Optional[SandboxedFileSystem] = None
_sandbox_lock = threading.Lock()


import atexit

@atexit.register
def _cleanup_sandbox_on_exit() -> None:
    """Best-effort cleanup of the global sandbox on interpreter shutdown."""
    global _sandbox
    if _sandbox is not None:
        try:
            _sandbox.cleanup()
        except Exception:
            pass


def get_sandbox() -> SandboxedFileSystem:
    """Get or create the global SandboxedFileSystem instance (thread-safe)."""
    global _sandbox
    # Fast path — already initialised
    if _sandbox is None:
        with _sandbox_lock:
            # Re-check inside the lock: another thread may have created it
            # between the outer None check and acquiring the lock.
            if _sandbox is None:
                _sandbox = SandboxedFileSystem()
    return _sandbox
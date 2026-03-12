"""
Sandboxed Virtual File System for CodeUp

Provides safe file operations within a confined workspace directory.
All file operations are restricted to /workspace folder.
"""

import os
import json
import tempfile
from pathlib import Path
from typing import Optional, Dict, List


class SandboxedFileSystem:
    """Manages file operations within a sandboxed workspace."""
    
    def __init__(self, workspace_dir: Optional[str] = None):
        """
        Initialize sandboxed filesystem.
        
        Args:
            workspace_dir: Path to workspace root. If None, uses temp directory.
        """
        if workspace_dir is None:
            # Create a temp directory for the workspace
            self.workspace_dir = tempfile.mkdtemp(prefix="codeup_workspace_")
        else:
            self.workspace_dir = workspace_dir
            os.makedirs(self.workspace_dir, exist_ok=True)
        
        # Ensure workspace path is absolute and normalized
        self.workspace_dir = os.path.abspath(self.workspace_dir)
    
    def _validate_path(self, filepath: str) -> str:
        """
        Validate and normalize path to ensure it's within workspace.
        
        Args:
            filepath: Path to validate
            
        Returns:
            Absolute normalized path within workspace
            
        Raises:
            ValueError: If path attempts to escape workspace
        """
        # Normalize and make absolute
        abs_path = os.path.abspath(os.path.join(self.workspace_dir, filepath))
        
        # Ensure it's within workspace (prevent directory traversal)
        try:
            abs_path.relative_to(self.workspace_dir)
        except ValueError:
            raise ValueError(f"Path '{filepath}' is outside workspace")
        
        return abs_path
    
    def write(self, filepath: str, content: str, encoding: str = "utf-8") -> Dict:
        """
        Write file to workspace.
        
        Args:
            filepath: Path relative to workspace
            content: File content
            encoding: Text encoding (default: utf-8)
            
        Returns:
            {"success": bool, "path": str, "size": int, "error": str}
        """
        try:
            abs_path = self._validate_path(filepath)
            
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            
            # Write file
            with open(abs_path, "w", encoding=encoding) as f:
                f.write(content)
            
            size = len(content.encode(encoding))
            return {
                "success": True,
                "path": filepath,
                "size": size,
            }
        except Exception as e:
            return {
                "success": False,
                "path": filepath,
                "error": str(e)
            }
    
    def read(self, filepath: str, encoding: str = "utf-8") -> Dict:
        """
        Read file from workspace.
        
        Args:
            filepath: Path relative to workspace
            encoding: Text encoding (default: utf-8)
            
        Returns:
            {"success": bool, "content": str, "size": int, "error": str}
        """
        try:
            abs_path = self._validate_path(filepath)
            
            if not os.path.exists(abs_path):
                return {
                    "success": False,
                    "path": filepath,
                    "error": "File not found"
                }
            
            if not os.path.isfile(abs_path):
                return {
                    "success": False,
                    "path": filepath,
                    "error": "Path is not a file"
                }
            
            with open(abs_path, "r", encoding=encoding) as f:
                content = f.read()
            
            return {
                "success": True,
                "path": filepath,
                "content": content,
                "size": len(content)
            }
        except Exception as e:
            return {
                "success": False,
                "path": filepath,
                "error": str(e)
            }
    
    def delete(self, filepath: str) -> Dict:
        """
        Delete file from workspace.
        
        Args:
            filepath: Path relative to workspace
            
        Returns:
            {"success": bool, "path": str, "error": str}
        """
        try:
            abs_path = self._validate_path(filepath)
            
            if not os.path.exists(abs_path):
                return {
                    "success": False,
                    "path": filepath,
                    "error": "File not found"
                }
            
            if os.path.isfile(abs_path):
                os.remove(abs_path)
            else:
                return {
                    "success": False,
                    "path": filepath,
                    "error": "Path is not a file"
                }
            
            return {
                "success": True,
                "path": filepath
            }
        except Exception as e:
            return {
                "success": False,
                "path": filepath,
                "error": str(e)
            }
    
    def list_files(self, dirpath: str = ".") -> Dict:
        """
        List files in workspace directory.
        
        Args:
            dirpath: Directory path relative to workspace
            
        Returns:
            {"success": bool, "files": list, "dirs": list, "error": str}
        """
        try:
            abs_path = self._validate_path(dirpath)
            
            if not os.path.exists(abs_path):
                return {
                    "success": False,
                    "error": "Directory not found",
                    "files": [],
                    "dirs": []
                }
            
            if not os.path.isdir(abs_path):
                return {
                    "success": False,
                    "error": "Path is not a directory",
                    "files": [],
                    "dirs": []
                }
            
            files = []
            dirs = []
            
            for entry in os.listdir(abs_path):
                entry_path = os.path.join(abs_path, entry)
                if os.path.isfile(entry_path):
                    files.append({
                        "name": entry,
                        "size": os.path.getsize(entry_path)
                    })
                elif os.path.isdir(entry_path):
                    dirs.append(entry)
            
            return {
                "success": True,
                "files": sorted(files, key=lambda x: x["name"]),
                "dirs": sorted(dirs)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "files": [],
                "dirs": []
            }
    
    def get_workspace_info(self) -> Dict:
        """Get information about the workspace."""
        try:
            total_files = 0
            total_size = 0
            
            for root, dirs, files in os.walk(self.workspace_dir):
                total_files += len(files)
                for file in files:
                    total_size += os.path.getsize(os.path.join(root, file))
            
            return {
                "success": True,
                "workspace": self.workspace_dir,
                "total_files": total_files,
                "total_size": total_size,
                "available": True
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# Module-level instance
_sandbox = None

def get_sandbox() -> SandboxedFileSystem:
    """Get or create the global sandbox instance."""
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxedFileSystem()
    return _sandbox

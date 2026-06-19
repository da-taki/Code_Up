
from __future__ import annotations

from typing import Any, Dict, List, Optional

MAX_LANDMARKS = 40


def normalize_name(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())[:60]


def _created(landmark: Dict[str, Any]) -> Dict[str, Any]:
    line = landmark["line"]
    end = landmark.get("end_line") or line
    where = f"line {line}" if end <= line else f"lines {line} to {end}"
    msg = f"Bookmarked {where} as {landmark['name']}."
    return {"action": "bookmark_created", "success": True, "message": msg, "speech": msg,
            "landmark": landmark}


def create_landmark(landmarks: Dict[str, Any], name: str, *, line: int,
                    end_line: Optional[int] = None, block_type: str = "",
                    preview: str = "") -> Dict[str, Any]:
    name = normalize_name(name)
    if not name:
        msg = "What should I call this bookmark?"
        return {"action": "bookmark_error", "success": False, "message": msg, "speech": msg}
    try:
        line = int(line)
    except (TypeError, ValueError):
        msg = "I need a line to bookmark. Try saying 'go to the loop' first."
        return {"action": "bookmark_error", "success": False, "message": msg, "speech": msg}
    landmark = {
        "name": name,
        "line": line,
        "end_line": int(end_line) if end_line else line,
        "block_type": str(block_type or "")[:40],
        "preview": str(preview or "")[:80],
    }
    if name not in landmarks and len(landmarks) >= MAX_LANDMARKS:
        msg = "You have a lot of bookmarks already. Clear some before adding more."
        return {"action": "bookmark_error", "success": False, "message": msg, "speech": msg}
    landmarks[name] = landmark
    return _created(landmark)


def get_landmark(landmarks: Dict[str, Any], name: str) -> Dict[str, Any]:
    name = normalize_name(name)
    landmark = landmarks.get(name)
    if not landmark:
        msg = f"I do not have a bookmark called {name or 'that'}." if name else "Which bookmark should I open?"
        return {"action": "bookmark_error", "success": False, "message": msg, "speech": msg}
    preview = landmark.get("preview") or ""
    tail = f" {preview}" if preview else ""
    msg = f"{landmark['name'].capitalize()} starts at line {landmark['line']}.{tail}"
    return {"action": "bookmark_read", "success": True, "message": msg, "speech": msg,
            "line": landmark["line"], "end_line": landmark.get("end_line") or landmark["line"],
            "landmark": landmark}


def list_landmarks(landmarks: Dict[str, Any]) -> Dict[str, Any]:
    if not landmarks:
        msg = "You have no code bookmarks yet. Navigate to a block and say, bookmark this loop as main loop."
        return {"action": "bookmark_list", "success": True, "message": msg, "speech": msg, "items": []}
    items: List[Dict[str, Any]] = sorted(landmarks.values(), key=lambda b: b["line"])
    spoken = "; ".join(f"{b['name']} at line {b['line']}" for b in items)
    msg = f"You have {len(items)} bookmark{'s' if len(items) != 1 else ''}: {spoken}."
    return {"action": "bookmark_list", "success": True, "message": msg, "speech": msg, "items": items}


def delete_landmark(landmarks: Dict[str, Any], name: str) -> Dict[str, Any]:
    name = normalize_name(name)
    if name in landmarks:
        del landmarks[name]
        msg = f"Deleted the bookmark {name}."
        return {"action": "bookmark_deleted", "success": True, "message": msg, "speech": msg, "name": name}
    msg = f"I do not have a bookmark called {name or 'that'}."
    return {"action": "bookmark_error", "success": False, "message": msg, "speech": msg}


def clear_landmarks(landmarks: Dict[str, Any]) -> Dict[str, Any]:
    count = len(landmarks)
    landmarks.clear()
    msg = "Cleared all code bookmarks." if count else "There were no code bookmarks to clear."
    return {"action": "bookmark_deleted", "success": True, "message": msg, "speech": msg, "cleared": count}

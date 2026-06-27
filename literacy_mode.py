"""Programming Literacy Mode for CodeUp.

This deterministic layer gives blind beginners a short Python learning path
before they move to professional tools. It orchestrates existing CodeUp
features instead of replacing them.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import learning_moat

__all__ = ["LESSONS", "command_kind", "handle_command", "is_python_lesson_command", "lesson_by_id"]


def _lesson(
    lesson_id: str,
    title: str,
    goal: str,
    starter_code: str,
    commands: List[str],
    mistake: str,
    hint: str,
    check_question: str,
    concept: str,
    next_suggestion: str,
) -> Dict[str, Any]:
    return {
        "id": lesson_id,
        "title": title,
        "goal": goal,
        "starter_code": starter_code.strip() + "\n",
        "commands": commands,
        "mistake": mistake,
        "hint": hint,
        "check_question": check_question,
        "concept": concept,
        "next_suggestion": next_suggestion,
    }


LESSONS: List[Dict[str, Any]] = [
    _lesson(
        "print",
        "Print and output",
        "Learn how Python shows text when the program runs.",
        'print("Hello, CodeUp")',
        ["run", "read output", "where am I", "check lesson understanding"],
        "Remove the closing quote, then run the program and ask for a hint.",
        "A print statement sends a value to the output.",
        "What does print do in this program?",
        "print output",
        "Next mission: Variables and values.",
    ),
    _lesson(
        "variables",
        "Variables and values",
        "Learn how a name stores a value.",
        'name = "CodeUp"\nprint(name)',
        ["run", "show program state", "what am I learning", "check lesson understanding"],
        "Change print(name) to print(nam), then run the program and listen for the name error.",
        "A variable is a name that points to a value so you can reuse it later.",
        "What value does the variable name store?",
        "variables",
        "Next mission: If statements.",
    ),
    _lesson(
        "if",
        "If statements",
        "Learn how Python chooses between paths.",
        'score = 7\nif score >= 5:\n    print("pass")\nelse:\n    print("try again")',
        ["run", "show program state", "where am I", "check lesson understanding"],
        "Remove the colon after the if line, then run the program and ask for a hint.",
        "An if statement checks a condition and runs the indented branch when it is true.",
        "Why does this program print pass?",
        "if statements",
        "Next mission: For loops.",
    ),
    _lesson(
        "loops",
        "For loops",
        "Learn how Python repeats code.",
        "for i in range(3):\n    print(i)",
        ["run", "show program state", "where am I", "check my understanding"],
        "Remove the spaces before print(i), then run the program. Ask \"give me a hint\" before asking for the fix.",
        "A for loop repeats the indented lines once for each value.",
        "What value does i have on the last loop run?",
        "for loops",
        "Next mission: Lists.",
    ),
    _lesson(
        "lists",
        "Lists",
        "Learn how Python keeps several values in order.",
        'items = ["apple", "banana", "cherry"]\nprint(items[0])',
        ["run", "show program state", "read output", "check lesson understanding"],
        "Change items[0] to items[5], then run the program and listen for the index error.",
        "A list stores multiple values. Positions start at zero.",
        "Which item is at position 0?",
        "lists",
        "Next mission: Functions.",
    ),
    _lesson(
        "functions",
        "Functions",
        "Learn how Python gives a set of steps a name.",
        'def greet(name):\n    print("Hello", name)\n\ngreet("CodeUp")',
        ["run", "project map", "where am I", "check lesson understanding"],
        "Change greet(\"CodeUp\") to grete(\"CodeUp\"), then run the program and ask what the mistake was.",
        "A function is a named set of steps. Calling it runs those steps.",
        "What line calls the function?",
        "functions",
        "Next step: ask for a graduation report or make a Codex handoff pack.",
    ),
]

_ALIASES = {
    "1": "print", "first": "print", "print": "print", "printing": "print", "output": "print",
    "2": "variables", "second": "variables", "variable": "variables", "variables": "variables", "values": "variables",
    "3": "if", "third": "if", "if": "if", "condition": "if", "conditions": "if", "conditional": "if",
    "4": "loops", "fourth": "loops", "loop": "loops", "loops": "loops", "for": "loops", "for loops": "loops",
    "5": "lists", "fifth": "lists", "list": "lists", "lists": "lists",
    "6": "functions", "sixth": "functions", "function": "functions", "functions": "functions",
}


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def lesson_by_id(lesson_id: str) -> Optional[Dict[str, Any]]:
    wanted = _ALIASES.get(_norm(lesson_id), _norm(lesson_id))
    return next((lesson for lesson in LESSONS if lesson["id"] == wanted), None)


def _current_lesson(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return lesson_by_id(str(mem.get("current_lesson_id") or ""))


def _lesson_number(lesson: Dict[str, Any]) -> int:
    return LESSONS.index(lesson) + 1


def _set_current(mem: Dict[str, Any], lesson: Dict[str, Any]) -> None:
    mem["literacy_mode_on"] = True
    mem["current_lesson_id"] = lesson["id"]
    attempts = mem.setdefault("lesson_attempts", {})
    attempts[lesson["id"]] = int(attempts.get(lesson["id"]) or 0) + 1


def _next_lesson(lesson: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not lesson:
        return LESSONS[0]
    idx = LESSONS.index(lesson)
    return LESSONS[idx + 1] if idx + 1 < len(LESSONS) else None


def _previous_lesson(lesson: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not lesson:
        return None
    idx = LESSONS.index(lesson)
    return LESSONS[idx - 1] if idx > 0 else None


def _list_lessons() -> str:
    lines = ["Programming Literacy Mode lessons:"]
    lines.extend(f"{idx}. {lesson['title']}" for idx, lesson in enumerate(LESSONS, 1))
    lines.append('Say "start lesson 1" or "start loops lesson".')
    return "\n".join(lines)


def _lesson_intro(lesson: Dict[str, Any]) -> str:
    commands = ", ".join(lesson["commands"])
    return (
        f"Lesson {_lesson_number(lesson)}: {lesson['title']}.\n\n"
        f"Goal: {lesson['goal']}\n\n"
        "Starter code:\n"
        f"```python\n{lesson['starter_code']}```\n"
        f"Try: {commands}.\n\n"
        "I did not overwrite your editor. Paste the starter code when you are ready."
    )


def command_kind(text: str) -> Optional[Dict[str, str]]:
    t = _norm(text)
    if t in {"start programming literacy mode", "start literacy mode"}:
        return {"kind": "start_mode"}
    if t in {"list lessons", "show lessons"}:
        return {"kind": "list_lessons"}
    if t in {"start lesson", "start first lesson"}:
        return {"kind": "start_lesson", "lesson": "1"}
    m = re.match(r"^start lesson ([a-z0-9 ]+)$", t)
    if m:
        return {"kind": "start_lesson", "lesson": m.group(1)}
    m = re.match(r"^start ([a-z ]+) lesson$", t)
    if m:
        lesson_name = m.group(1).strip()
        if lesson_name in {"block", "blocks", "audio block", "audio blocks", "parsons", "error"}:
            return None
        return {"kind": "start_lesson", "lesson": lesson_name}
    exact = {
        "next lesson": "next_lesson",
        "previous lesson": "previous_lesson",
        "lesson status": "lesson_status",
        "what am i learning": "what_learning",
        "what should i do next": "next_step",
        "give me lesson starter code": "starter_code",
        "practice the mistake": "practice_mistake",
        "check lesson understanding": "check_understanding",
        "complete lesson": "complete_lesson",
        "teacher lesson report": "teacher_report",
        "graduation report": "graduation_report",
        "am i ready for codex": "ready_codex",
        "am i ready for vs code": "ready_vscode",
        "am i ready for vscode": "ready_vscode",
    }
    return {"kind": exact[t]} if t in exact else None


def is_python_lesson_command(kind: str) -> bool:
    return kind in {
        "start_lesson", "next_lesson", "previous_lesson", "what_learning", "next_step",
        "starter_code", "practice_mistake", "check_understanding", "complete_lesson",
    }


def _need_lesson() -> Dict[str, str]:
    msg = 'No literacy lesson is active yet. Say "list lessons" or "start lesson 1".'
    return {"message": msg, "speech": msg}


def _completed(mem: Dict[str, Any]) -> List[str]:
    completed = mem.get("completed_lessons")
    return list(completed) if isinstance(completed, list) else []


def _teacher_report(mem: Dict[str, Any]) -> str:
    lesson = _current_lesson(mem)
    checks = mem.get("lesson_checks_requested") if isinstance(mem.get("lesson_checks_requested"), dict) else {}
    mistakes = mem.get("lesson_mistakes_practiced") if isinstance(mem.get("lesson_mistakes_practiced"), dict) else {}
    attempts = mem.get("lesson_attempts") if isinstance(mem.get("lesson_attempts"), dict) else {}
    nxt = _next_lesson(lesson)
    return "\n".join([
        "# CodeUp Teacher Lesson Report",
        "",
        f"- Lesson attempted: {lesson['title'] if lesson else 'No active lesson'}",
        f"- Commands used: {int(mem.get('command_count') or 0)}",
        f"- Runs: {int(mem.get('run_count') or 0)}",
        f"- Recent error: {mem.get('last_run_error') or 'none recorded'}",
        f"- Hints requested: {int(mem.get('tutor_hints_requested') or 0)}",
        f"- Fixes applied: {int(mem.get('fixes_applied') or 0)}; rejected: {int(mem.get('fixes_rejected') or 0)}",
        f"- Understanding checks: {sum(int(v) for v in checks.values()) if checks else 0}",
        f"- Mistake practice requests: {sum(int(v) for v in mistakes.values()) if mistakes else 0}",
        f"- Lesson attempts: {sum(int(v) for v in attempts.values()) if attempts else 0}",
        f"- Suggested next lesson: {nxt['title'] if nxt else 'graduate to small supported projects'}",
    ])


def _graduation_report(mem: Dict[str, Any]) -> str:
    done = set(_completed(mem))
    names = [lesson["title"] for lesson in LESSONS if lesson["id"] in done]
    practiced = ", ".join(names) if names else "no completed lessons yet"
    return (
        "# CodeUp Graduation Readiness\n\n"
        f"Practiced: {practiced}.\n\n"
        "A learner is ready for small professional-tool handoff when they can run code, explain output, describe one "
        "variable or loop, read one error, and review a proposed change. This does not claim full independence."
    )


def _readiness(mem: Dict[str, Any], target: str) -> str:
    completed = len(set(_completed(mem)))
    ran = int(mem.get("run_count") or 0) > 0
    saw_error = bool(mem.get("last_run_error") or mem.get("error_type_counts"))
    reviewed = bool(mem.get("change_history") or mem.get("fixes_applied") or mem.get("fixes_rejected"))
    if target == "codex":
        if completed >= 3 and ran:
            return (
                "You are ready to try Codex for small explanations if you can run code, explain one error, review a "
                "change, and describe what your loop does. You are not expected to build large projects yet."
            )
        return (
            "Not fully yet. Try Codex after you complete a few lessons, run code yourself, and explain one mistake "
            "in your own words. You can still use the Codex handoff pack with support."
        )
    if completed >= 3 and ran and (saw_error or reviewed):
        return (
            "You may be ready to try VS Code for tiny Python files with a screen reader, especially if you can run, "
            "navigate, and explain errors. Stay with small programs first."
        )
    return (
        "Not fully yet. Before VS Code, practice running code, reading output, using project map, understanding one "
        "error, and reviewing a small change in CodeUp."
    )


def handle_command(command: Dict[str, str], mem: Dict[str, Any], code: str = "", error_text: str = "") -> Dict[str, Any]:
    kind = command["kind"]
    mem = mem or {}
    if kind == "start_mode":
        mem["literacy_mode_on"] = True
        msg = (
            'Programming Literacy Mode is on. CodeUp will guide you through beginner Python lessons using speech, '
            'hints, state narration, error practice, and teacher reports. Say "list lessons" to begin.'
        )
        return {"message": msg, "speech": msg}
    if kind == "list_lessons":
        msg = _list_lessons()
        return {"message": msg, "speech": msg}
    if kind == "start_lesson":
        lesson = lesson_by_id(command.get("lesson", "1"))
        if not lesson:
            msg = 'I could not find that lesson. Say "list lessons" to hear the six missions.'
            return {"message": msg, "speech": msg}
        _set_current(mem, lesson)
        return {
            "message": _lesson_intro(lesson),
            "speech": f"Started lesson {_lesson_number(lesson)}: {lesson['title']}. I did not overwrite your editor.",
            "starter_code": lesson["starter_code"],
            "lesson_id": lesson["id"],
        }
    lesson = _current_lesson(mem)
    if kind == "next_lesson":
        target = _next_lesson(lesson)
        if not target:
            msg = "You are at the last lesson. Ask for a graduation report when ready."
            return {"message": msg, "speech": msg}
        _set_current(mem, target)
        return {"message": _lesson_intro(target), "speech": f"Next lesson: {target['title']}.", "starter_code": target["starter_code"]}
    if kind == "previous_lesson":
        target = _previous_lesson(lesson)
        if not target:
            msg = "There is no previous lesson. You are at the beginning of the literacy path."
            return {"message": msg, "speech": msg}
        _set_current(mem, target)
        return {"message": _lesson_intro(target), "speech": f"Previous lesson: {target['title']}.", "starter_code": target["starter_code"]}
    if kind == "lesson_status":
        title = lesson["title"] if lesson else "none"
        msg = f"Current lesson: {title}. Completed {len(set(_completed(mem)))} of {len(LESSONS)} lessons."
        return {"message": msg, "speech": msg}
    if kind in {"what_learning", "next_step", "starter_code", "practice_mistake", "check_understanding", "complete_lesson"} and not lesson:
        return _need_lesson()
    if kind == "what_learning":
        msg = f"You are learning {lesson['concept']}. {lesson['hint']}"
        return {"message": msg, "speech": msg}
    if kind == "next_step":
        if not mem.get("run_count"):
            msg = 'Next, run the program. After that, ask "show program state" to hear what changed.'
        elif not mem.get("last_state_trace"):
            msg = 'Next, ask "show program state" or "where am I" to connect the code to what happened.'
        else:
            msg = 'Next, ask "check lesson understanding" or practice the mistake for this lesson.'
        return {"message": msg, "speech": msg}
    if kind == "starter_code":
        msg = f"Starter code for {lesson['title']}:\n\n```python\n{lesson['starter_code']}```"
        return {"message": msg, "speech": "Here is the lesson starter code. I did not overwrite your editor.", "starter_code": lesson["starter_code"]}
    if kind == "practice_mistake":
        mistakes = mem.setdefault("lesson_mistakes_practiced", {})
        mistakes[lesson["id"]] = int(mistakes.get(lesson["id"]) or 0) + 1
        msg = f"Mistake practice: {lesson['mistake']}"
        return {"message": msg, "speech": msg}
    if kind == "check_understanding":
        checks = mem.setdefault("lesson_checks_requested", {})
        checks[lesson["id"]] = int(checks.get(lesson["id"]) or 0) + 1
        base = learning_moat.build_understanding_check("understanding_question", mem, code, error_text)
        msg = f"Lesson check: {lesson['check_question']} {base['message']}"
        return {"message": msg, "speech": msg}
    if kind == "complete_lesson":
        completed = mem.setdefault("completed_lessons", [])
        if lesson["id"] not in completed:
            completed.append(lesson["id"])
        stamps = mem.setdefault("lesson_completed_at", {})
        stamps[lesson["id"]] = time.time()
        nxt = _next_lesson(lesson)
        msg = f"Lesson complete: {lesson['title']}. " + (f"Next lesson: {nxt['title']}." if nxt else "You finished the six beginner missions.")
        return {"message": msg, "speech": msg}
    if kind == "teacher_report":
        return {"message": _teacher_report(mem), "speech": "Teacher lesson report ready."}
    if kind == "graduation_report":
        return {"message": _graduation_report(mem), "speech": "Graduation readiness report ready."}
    if kind == "ready_codex":
        msg = _readiness(mem, "codex")
        return {"message": msg, "speech": msg}
    if kind == "ready_vscode":
        msg = _readiness(mem, "vscode")
        return {"message": msg, "speech": msg}
    msg = 'Programming Literacy Mode is ready. Say "list lessons" to begin.'
    return {"message": msg, "speech": msg}

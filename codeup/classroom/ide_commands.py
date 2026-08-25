"""Deterministic classroom commands for the IDE's existing command pipeline.

This module is the ONLY place that recognizes classroom phrases ("open my
assignments", "join ABC123", "what should I do?", ...). It is wired into
app.py's single ``/voice-command`` endpoint (both typed and spoken input
already flow through there - see ``handleCommandText`` in static/app.js), so
there is exactly one command system, not two. Matching is plain regex/
substring logic - nothing here calls Groq or any LLM, satisfying "do not
send simple navigation commands to Groq" and "blocked capabilities must
continue to consume ZERO Groq capacity" (join codes and navigation never
even reach a capability check).

``match(text)`` is pure and independently testable. ``handle(...)`` performs
the actual reads/writes, reusing ``learner_actions`` for anything that
mutates state (submit, help requests, joining) and ``learner_context`` for
all learner-facing phrasing, so this file stays a thin router.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from codeup.classroom import ai_policy, curriculum, db, learner_actions, learner_context

NAV_TARGETS = {
    "classroom": "classroomPanelHeading",
    "editor": "__editor__",
    "code editor": "__editor__",
    "command box": "voiceText",
    "command input": "voiceText",
    "output": "output",
    "assignment": "classroomAssignmentsHeading",
    "assignments": "classroomAssignmentsHeading",
    "lesson": "classroomCourseHeading",
    "lessons": "classroomCourseHeading",
    "course": "classroomCourseHeading",
    "projects": "classroomProjectsHeading",
    "project": "classroomProjectsHeading",
    "help": "classroomHelpHeading",
    "join classroom": "classroomJoinHeading",
}

_JOIN_STOPWORDS = {"JOIN", "MY", "CLASS", "CODE", "IS", "A", "CLASSROOM", "COHORT", "ENTER", "THE", "WITH"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()
    # punctuation-insensitive so "what's new?" and "whats new" match the same intent


def _any(text: str, *phrases: str) -> bool:
    n = _norm(text)
    return any(n == _norm(p) or n.startswith(_norm(p) + " ") or _norm(p) in n for p in phrases)


def extract_join_code(text: str) -> Optional[str]:
    """A join-code candidate is a 4-8 character token that isn't ordinary
    join-language (join/my/class/code/...), and is either alphanumeric-mixed
    (e.g. ABC123, which is never an English word) or directly follows the
    word "join"/"code" (e.g. "join ABCXYZ", "class code ABCXYZ")."""
    for token in re.findall(r"[A-Za-z0-9]{4,8}", text or ""):
        upper = token.upper()
        if upper in _JOIN_STOPWORDS:
            continue
        has_digit = any(c.isdigit() for c in upper)
        follows_join_word = bool(re.search(r"\b(?:join|code)\b\s+" + re.escape(token) + r"\b", text, re.IGNORECASE))
        if has_digit or follows_join_word:
            return upper
    return None


# ---- pending-join conversational state machine -------------------------------
#
# IDLE -> "join a cohort" -----------------> WAITING_FOR_CODE
# IDLE -> "join ABC123", no known name -----> WAITING_FOR_NAME(code=ABC123)
# IDLE -> "join ABC123", known name --------> attempt join immediately
# WAITING_FOR_CODE -> code-shaped utterance -> known name? attempt : WAITING_FOR_NAME
# WAITING_FOR_CODE -> anything else --------> abandoned (None, None): the caller
#                                              re-processes the utterance normally
# WAITING_FOR_NAME -> plausible free-text --> attempt join
# WAITING_FOR_NAME -> a recognized command,
#   a code-shaped utterance, or empty text --> abandoned, same as above
# any state -> "cancel"/"never mind"/etc. --> cleared, "cancelled" message
#
# "JOINED" is never a value of this state machine - it's simply the learner
# cookie/context, exactly as everywhere else in this file. Pending state is
# kept entirely in the caller's per-session memory dict (never the classroom
# database - see app.py's _classroom_command_response), so it can never
# outlive the session and never needs a migration.

_JOIN_CANCEL_PHRASES = {"cancel", "cancel joining", "never mind", "nevermind", "stop joining"}


def is_join_cancel_phrase(text: str) -> bool:
    return _norm(text) in {_norm(p) for p in _JOIN_CANCEL_PHRASES}


def looks_like_join_code(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9]{4,8}", (text or "").strip()))


def _attempt_join(code: str, name: str) -> Dict[str, Any]:
    """One authoritative join attempt + outcome classification. Any
    exception (DB/IO hiccup, not just an invalid code) is caught here and
    reported as a temporary failure - never a raw traceback, and never
    mistaken for "that code doesn't exist"."""
    try:
        result = learner_actions.join_cohort_by_code(code, name)
    except Exception:
        return {"outcome": "failed"}
    if result["success"]:
        return {"outcome": "success", "cohort_name": result["cohort"]["name"], "token": result["learner"]["token"]}
    if result["error"] == "not_found":
        return {"outcome": "not_found"}
    return {"outcome": "failed"}


def handle_pending_join(raw_text: str, pending: Optional[Dict[str, Any]], ctx: Dict[str, Any]):
    """Consumes one utterance against in-progress pending-join state.

    Returns (response_or_None, new_pending):
      - response is a full command response dict (same shape as handle()'s
        return value) when this utterance was consumed as part of the join
        conversation - the caller returns it directly.
      - response is None when the utterance was NOT part of the join
        conversation (not code-shaped, not a plausible name, or itself a
        recognized command) - the caller must fall through and process
        raw_text through the normal match()/handle() pipeline. new_pending
        is what the session should remember either way (None clears it).
    """
    if not pending:
        return None, pending

    if is_join_cancel_phrase(raw_text):
        return _msg("Classroom joining cancelled."), None

    state = pending.get("state")
    remembered_name = str(pending.get("name") or "").strip()

    if state == "waiting_for_code":
        # Accept either an explicit "join <code>"/"code is <code>" phrase
        # (extract_join_code) or a bare code-shaped token, since we already
        # asked specifically for a code - unlike the name prompt below,
        # there's no ambiguity to worry about here.
        code = extract_join_code(raw_text) or (
            raw_text.strip().upper() if looks_like_join_code(raw_text) else None
        )
        if not code:
            return None, None  # doesn't look like a code - abandon, let it fall through
        name = str(ctx.get("join_name") or "").strip() or remembered_name
        if not name:
            return (_msg("What name should I use?", focus_hint="classroomJoinName", join_code_hint=code),
                    {"state": "waiting_for_name", "code": code})
        return _resolve_join_attempt(code, name)

    if state == "waiting_for_name":
        code = pending.get("code")
        text = raw_text.strip()
        # Deliberately NOT disqualified by looks_like_join_code: a name like
        # "Amir" is indistinguishable from a bare code by shape alone, and
        # we just explicitly asked for a name, so a bare word is trusted as
        # the name. Only an utterance that is ITSELF a recognized classroom
        # command (a fresh "join <code>", "cancel", "go to editor", ...) is
        # treated as abandonment instead.
        if not text or match(text) is not None:
            return None, None  # not a plausible name - abandon, let it fall through
        return _resolve_join_attempt(code, text[:80], remembered_name=text[:80])

    return None, None


def _resolve_join_attempt(code: str, name: str, remembered_name: Optional[str] = None):
    outcome = _attempt_join(code, name)
    if outcome["outcome"] == "success":
        return (_msg(learner_context.join_success_message(outcome["cohort_name"]),
                      _classroom_token=outcome["token"]), None)
    if outcome["outcome"] == "not_found":
        return (_msg(learner_context.join_not_found_message(), focus_hint="classroomJoinCode"),
                {"state": "waiting_for_code", "name": remembered_name or name})
    return (_msg("Could not join right now. Try again."),
            {"state": "waiting_for_code", "name": remembered_name or name})


def match(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Returns (intent, slots) for a recognized classroom phrase, or None so
    the caller falls through to the existing command pipeline unchanged."""
    n = _norm(text)
    if not n:
        return None

    # ---- navigation focus -----------------------------------------------
    m = re.match(r"^go to (?:the )?(classroom|editor|code editor|command box|command input|"
                 r"output|assignments?|lessons?|course|projects?|help|join classroom)$", n)
    if m:
        return "nav_focus", {"target": m.group(1)}

    # ---- back to the plain IDE (e.g. right after submitting an assignment) --
    if _any(n, "back to ide", "return to ide", "go back to ide", "back to codeup", "return to codeup"):
        return "back_to_ide", {}

    # ---- join / leave classroom ------------------------------------------
    code = extract_join_code(text)
    if code and ("join" in n or "class code" in n or "cohort" in n):
        return "join_with_code", {"code": code}
    if _any(n, "join a cohort", "join my class", "join classroom", "enter class code", "join a classroom"):
        return "join_prompt", {}
    if _any(n, "what class am i in", "what classroom am i in"):
        return "current_class", {}
    if _any(n, "leave this class", "leave classroom", "leave my class", "leave this classroom"):
        return "leave_class", {}

    # ---- assignments -------------------------------------------------------
    if _any(n, "open my assignments", "what assignments do i have", "how many assignments do i have",
            "how many assignments are left", "go back to my assignments", "read my assignments"):
        return "assignments_list", {}
    if _any(n, "whats new", "what is new", "do i have anything new"):
        return "assignments_new", {}
    if _any(n, "what is due", "whats due"):
        return "assignments_due", {}
    if _any(n, "what is overdue", "whats overdue"):
        return "assignments_overdue", {}
    m = re.match(r"^open assignment (\d+)$", n)
    if m:
        return "open_assignment_index", {"index": int(m.group(1))}
    if _any(n, "continue my assignment", "read this assignment", "repeat the instructions",
            "what am i supposed to do", "what do i need to do", "repeat the assignment"):
        return "assignment_instructions", {}
    if _any(n, "what can ai help me with", "what can ai do", "what is allowed"):
        return "ai_policy", {}
    if _any(n, "submit my assignment", "submit this", "submit", "turn this in", "turn in assignment"):
        return "submit_assignment", {}

    # ---- curriculum ---------------------------------------------------------
    if _any(n, "continue my course", "continue where i left off", "open my course"):
        return "curriculum_continue", {}
    if _any(n, "start from the beginning", "restart the course"):
        return "curriculum_restart_course", {}
    if _any(n, "restart this lesson"):
        return "curriculum_restart_lesson", {}
    if _any(n, "next lesson"):
        return "curriculum_next_lesson", {}
    if _any(n, "previous lesson"):
        return "curriculum_previous_lesson", {}
    if _any(n, "read this lesson", "read the example"):
        return "curriculum_read_lesson", {}
    if _any(n, "take the quiz"):
        return "curriculum_quiz", {}
    if _any(n, "what module am i on", "show my progress", "how much have i completed"):
        return "curriculum_progress", {}

    # ---- guided projects -----------------------------------------------------
    if _any(n, "open my projects", "what projects do i have", "how many projects do i have"):
        return "projects_list", {}
    if _any(n, "continue my project", "what am i working on", "whats the current step",
            "what is the current step"):
        return "project_current", {}
    if _any(n, "repeat the project instructions", "repeat the project introduction"):
        return "project_intro_repeat", {}
    if _any(n, "what have i finished", "check my progress"):
        return "overall_progress", {}
    if _any(n, "what comes next"):
        return "what_comes_next", {}
    if _any(n, "give me a hint", "repeat the hint"):
        return "project_hint", {}
    m = re.match(r"^open ([a-z0-9 ]+)$", n)
    if m:
        return "open_by_name", {"name": m.group(1).strip()}

    # ---- help -----------------------------------------------------------------
    if _any(n, "i need help", "ask my teacher for help", "ask my instructor for help"):
        return "help_request", {}
    if _any(n, "cancel my help request"):
        return "help_cancel", {}
    if _any(n, "is my teacher helping me", "is my instructor helping me"):
        return "help_status", {}

    # ---- general -------------------------------------------------------------
    # Exact phrases only (not substring) - "what should I do NEXT" etc. belong
    # to an existing, unrelated tutor feature (see test_programming_literacy_
    # mode.py); only the bare question is classroom-owned.
    if n in ("what should i do", "what should i do now"):
        return "what_should_i_do", {}
    if _any(n, "repeat orientation", "what can i say", "how does classroom mode work"):
        return "orientation_repeat", {}

    return None


# ---- handling ----------------------------------------------------------------

def _msg(message: str, speech: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    return {"success": True, "action": "deterministic_message", "message": message,
            "speech": speech or message, **extra}


def _find_by_name(items: List[Dict[str, Any]], name: str, key: str = "title") -> Optional[Dict[str, Any]]:
    n = _norm(name)
    if not n:
        return None
    best, best_score = None, 0
    for item in items:
        title_n = _norm(item.get(key, ""))
        if not title_n:
            continue
        if n == title_n:
            return item
        score = 0
        if n in title_n or title_n in n:
            score = min(len(n), len(title_n))
        if score > best_score:
            best, best_score = item, score
    return best


def _current_assignment(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    aid = ctx.get("assignment_cookie_id")
    if not aid:
        return None
    for a in (ctx.get("summary") or {}).get("assignments", []):
        if a["id"] == aid:
            return a
    return None


def _current_project(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pid = ctx.get("project_cookie_id")
    if not pid:
        return None
    for p in (ctx.get("summary") or {}).get("projects", []):
        if str(p["id"]) == str(pid):
            return p
    return None


def _assignments_by_state(summary: Dict[str, Any], state: str) -> List[Dict[str, Any]]:
    return [a for a in summary.get("assignments", []) if a["state"] == state]


def _speak_assignment_list(assignments: List[Dict[str, Any]]) -> str:
    if not assignments:
        return "You have no assignments yet."
    parts = []
    for a in assignments:
        label = a["state"].replace("_", " ")
        parts.append(f"{a['title']} ({label})")
    return "Your assignments: " + "; ".join(parts) + "."


def handle(intent: str, slots: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """ctx keys: learner (dict|None), summary (dict|None, from
    routes.build_ide_summary), current_code (str), assignment_cookie_id,
    project_cookie_id, module_cookie_id.

    Returns None for a small set of ambiguous intents (open_by_name,
    project_hint) when there is no real classroom context to back them up -
    callers must treat None as "not actually a classroom command" and fall
    through to whatever else recognizes the phrase, rather than showing a
    classroom-only message for someone else's feature."""
    learner = ctx.get("learner")
    summary = ctx.get("summary")

    # ---- classroom-membership commands work with or without a learner -------
    if intent == "nav_focus":
        target = slots["target"]
        dom_id = NAV_TARGETS.get(target, "mainContent")
        return {"success": True, "action": "focus_target", "target": dom_id,
                "message": f"Moved focus to {target}.", "speech": ""}

    if intent == "join_prompt":
        if learner:
            return _msg(f"You're already in {(summary or {}).get('cohort', {}).get('name', 'a classroom')}. "
                        "Say leave this class if you want to switch.")
        return _msg("Tell me your class code. You can say, join, followed by the code - "
                     "for example, join A B C 1 2 3.", _join_pending={"state": "waiting_for_code"},
                     focus_hint="classroomJoinCode")

    if intent == "join_with_code":
        if learner:
            return _msg("You're already in a classroom. Say leave this class if you want to switch.")
        name = str(ctx.get("join_name") or "").strip()
        if not name:
            return _msg("What name should I use?", focus_hint="classroomJoinName",
                        join_code_hint=slots["code"],
                        _join_pending={"state": "waiting_for_name", "code": slots["code"]})
        response, pending = _resolve_join_attempt(slots["code"], name)
        if pending is not None:
            response = {**response, "_join_pending": pending}
        return response

    if intent == "current_class":
        if not learner:
            return _msg("You're not in a classroom yet. Say join a cohort to get started.")
        cohort_name = (summary or {}).get("cohort", {}).get("name", "a classroom")
        return _msg(f"You're in {cohort_name}.")

    if intent == "leave_class":
        if not learner:
            return _msg("You're not in a classroom, so there's nothing to leave.")
        return {"success": True, "action": "navigate", "url": "/classroom/leave/confirm",
                "message": "Opening the leave-classroom confirmation.",
                "speech": "Say the leave button to confirm, or cancel to stay."}

    if intent == "back_to_ide":
        # Plain navigation to the plain /ide entry point - never clears the
        # classroom cookie/membership, never logs anyone out. Works with or
        # without a learner (harmless if already there / not in a classroom).
        return {"success": True, "action": "navigate", "url": "/ide",
                "message": "Back to CodeUp.", "speech": "Back to CodeUp."}

    # ---- ambiguous phrases: only ours when real classroom context backs them up.
    # "give me a hint" and "open <name>" are also meaningful to unrelated,
    # pre-existing features (the standalone tutor hint system; file/audio-
    # blocks/project "open X" commands) - returning None here means "not a
    # classroom command after all", so the caller falls through to those
    # instead of shadowing them with a classroom-only message.
    if intent == "open_by_name":
        if not learner:
            return None
        assignment = _find_by_name(summary.get("assignments", []), slots["name"])
        if assignment:
            return {"success": True, "action": "navigate", "url": assignment["open_url"],
                    "message": f"Opening {assignment['title']}.", "speech": f"Opening {assignment['title']}."}
        project = _find_by_name(summary.get("projects", []), slots["name"])
        if project:
            return {"success": True, "action": "navigate", "url": project["open_url"],
                    "message": f"Opening {project['title']}.", "speech": f"Opening {project['title']}."}
        return None

    if intent == "project_hint":
        if not learner or not _current_project(ctx):
            return None

    # Generic "lesson" phrasing (next/previous/restart/read this lesson, take
    # the quiz) is also owned by an unrelated, pre-existing standalone
    # tutor "learning path" feature - only claim it when a classroom module
    # is actually open in this browser (the module cookie), otherwise pass
    # through to that feature instead of shadowing it.
    _lesson_intents = {
        "curriculum_restart_lesson", "curriculum_next_lesson", "curriculum_previous_lesson",
        "curriculum_read_lesson", "curriculum_quiz",
    }
    if intent in _lesson_intents and (not learner or not ctx.get("module_cookie_id")):
        return None

    # ---- everything past this point requires a joined learner ---------------
    if not learner:
        return _msg("You're not in a classroom yet. Enter the code your instructor gave you.")

    if intent == "what_should_i_do":
        # A currently-open assignment always takes priority over the general
        # classroom-wide priority logic below - a learner mid-assignment
        # asking "what should I do?" means "for this", not "across
        # everything" (see also the "assignment_instructions" intent, which
        # already behaves this way for its own phrasings).
        active_assignment = _current_assignment(ctx)
        if active_assignment:
            instructions = active_assignment.get("instructions") or "No instructions were given for this assignment."
            return _msg(f"{active_assignment['title']}. {instructions}")
        return _msg(_resolve_what_should_i_do(ctx))

    if intent == "assignments_list":
        db.mark_assignments_seen(learner["id"])
        return _msg(_speak_assignment_list(summary.get("assignments", [])))

    if intent == "assignments_new":
        new_items = _assignments_by_state(summary, "new")
        db.mark_assignments_seen(learner["id"])
        if not new_items:
            return _msg("Nothing new. " + learner_context.format_assignment_counts(summary["assignment_counts"]))
        return _msg("New: " + "; ".join(a["title"] for a in new_items) + ".")

    if intent == "assignments_due":
        due_items = _assignments_by_state(summary, "new") + _assignments_by_state(summary, "pending") + \
            _assignments_by_state(summary, "in_progress")
        if not due_items:
            return _msg("Nothing is due right now.")
        return _msg("Still to do: " + "; ".join(a["title"] for a in due_items) + ".")

    if intent == "assignments_overdue":
        overdue = _assignments_by_state(summary, "overdue")
        if not overdue:
            return _msg("Nothing is overdue.")
        return _msg("Overdue: " + "; ".join(a["title"] for a in overdue) + ".")

    if intent == "open_assignment_index":
        items = summary.get("assignments", [])
        idx = slots["index"] - 1
        if idx < 0 or idx >= len(items):
            return _msg(f"You only have {len(items)} assignments.")
        return {"success": True, "action": "navigate", "url": items[idx]["open_url"],
                "message": f"Opening {items[idx]['title']}.", "speech": f"Opening {items[idx]['title']}."}

    if intent == "assignment_instructions":
        assignment = _current_assignment(ctx)
        if not assignment:
            return _msg("You don't have an assignment open. Say open my assignments to pick one.")
        instructions = assignment.get("instructions") or "(no instructions given)"
        return _msg(f"{assignment['title']}. {instructions}")

    if intent == "ai_policy":
        assignment = _current_assignment(ctx)
        if not assignment:
            return _msg("Full AI assistance is available - you don't have a policy-restricted assignment open.")
        settings = assignment.get("capability_settings") or ai_policy.default_settings_for_preset(assignment.get("ai_policy"))
        summary_text = ai_policy.summarize_settings(settings, is_assessment=bool(assignment.get("is_assessment")))
        return _msg(f"AI help for {assignment['title']}: {summary_text}")

    if intent == "submit_assignment":
        assignment = _current_assignment(ctx)
        if not assignment:
            return _msg("You don't have an assignment open to submit. Say open my assignments to pick one.")
        progress = learner_actions.submit_current_assignment(learner, assignment, ctx.get("current_code") or "")
        # Generic, action-independent flag app.js checks after any response
        # (like focus_hint/classroom_refresh) - reveals the "Back to CodeUp"
        # control even when submission happened via a typed/spoken command
        # rather than clicking the Submit button directly.
        return _msg(f"{assignment['title']} submitted successfully.", status=progress["status"],
                    assignment_submitted=True)

    if intent == "curriculum_continue":
        module = summary.get("module")
        if not module:
            return _msg("You haven't started the course yet. Say open my course to begin.")
        return {"success": True, "action": "navigate",
                "url": f"/classroom/curriculum/{module['module_id']}/open",
                "message": f"Continuing {module['title']}.", "speech": f"Continuing {module['title']}."}

    if intent == "curriculum_restart_course":
        return {"success": True, "action": "navigate", "url": "/classroom/curriculum/restart-course/confirm",
                "message": "Opening the restart-course confirmation.",
                "speech": "Say the restart button to confirm, or cancel to keep your progress."}

    if intent == "curriculum_restart_lesson":
        module = summary.get("module")
        if not module:
            return _msg("You don't have a lesson open right now.")
        return {"success": True, "action": "navigate",
                "url": f"/classroom/curriculum/restart-module/{module['module_id']}/confirm",
                "message": "Opening the restart-lesson confirmation.",
                "speech": "Say the restart button to confirm, or cancel to keep your progress."}

    if intent in ("curriculum_next_lesson", "curriculum_previous_lesson"):
        module = summary.get("module")
        if not module or module["module_id"] not in curriculum.MODULE_ORDER:
            return _msg("There isn't a next or previous lesson from here. Say open my course to see the full list.")
        idx = curriculum.MODULE_ORDER.index(module["module_id"])
        step = 1 if intent == "curriculum_next_lesson" else -1
        new_idx = idx + step
        if new_idx < 0 or new_idx >= len(curriculum.MODULE_ORDER):
            return _msg("You're at the end of the course." if step == 1 else "You're at the start of the course.")
        next_id = curriculum.MODULE_ORDER[new_idx]
        title = curriculum.public_module(next_id)["title"]
        return {"success": True, "action": "navigate", "url": f"/classroom/curriculum/{next_id}/open",
                "message": f"Opening {title}.", "speech": f"Opening {title}."}

    if intent == "curriculum_read_lesson":
        module = summary.get("module")
        if not module:
            return _msg("You don't have a lesson open right now.")
        lesson = curriculum.public_module(module["module_id"]) if module["module_id"] in curriculum.MODULE_ORDER else None
        concept = (lesson or {}).get("concept") or ""
        return _msg(f"{module['title']}. {concept}")

    if intent == "curriculum_quiz":
        module = summary.get("module")
        if not module:
            return _msg("You don't have a lesson open right now.")
        return {"success": True, "action": "navigate", "url": f"/classroom/curriculum/{module['module_id']}/quiz",
                "message": "Opening the quiz.", "speech": "Opening the quiz."}

    if intent == "curriculum_progress" or intent == "what_comes_next":
        module = summary.get("module")
        if module:
            phrase = learner_context.format_module_progress(module["title"], module["index"], module["total"])
            return _msg(phrase or f"You're on {module['title']}.")
        return _msg("You haven't started the course yet. Say open my course to begin.")

    if intent == "projects_list":
        items = summary.get("projects", [])
        if not items:
            return _msg("There are no guided projects available yet.")
        parts = [learner_context.format_project_progress(p["title"], p["done_checkpoints"], p["total_checkpoints"])
                 for p in items]
        return _msg(" ".join(parts))

    if intent == "project_current":
        project = _current_project(ctx)
        if not project:
            return _msg("You don't have a project open. Say open my projects to pick one.")
        return _msg(learner_context.project_returning_intro(project, project["completed_checkpoints"]))

    if intent == "project_intro_repeat":
        project = _current_project(ctx)
        if not project:
            return _msg("You don't have a project open.")
        return _msg(learner_context.project_intro(project))

    if intent == "project_hint":
        project = _current_project(ctx)
        if not project:
            return _msg("You don't have a project open to get a hint for.")
        return _msg(learner_context.checkpoint_incomplete_feedback(project, project["completed_checkpoints"])
                    or "You're on the last step - keep going.")

    if intent == "overall_progress":
        module = summary.get("module")
        counts = summary["assignment_counts"]
        parts = [learner_context.format_assignment_counts(counts)]
        if module:
            parts.append(learner_context.format_module_progress(module["title"], module["index"], module["total"]))
        for p in summary.get("projects", []):
            if p["done_checkpoints"]:
                parts.append(learner_context.format_project_progress(p["title"], p["done_checkpoints"], p["total_checkpoints"]))
        return _msg(" ".join(parts))

    if intent == "help_request":
        assignment = _current_assignment(ctx)
        learner_actions.send_help_request(learner, "", assignment["id"] if assignment else None)
        return _msg("Your help request was sent to your instructor.")

    if intent == "help_cancel":
        current = learner_actions.current_help_request(learner)
        if not current:
            return _msg("You don't have an open help request.")
        learner_actions.cancel_help_request_for_learner(learner, current["id"])
        return _msg("Your help request was cancelled.")

    if intent == "help_status":
        current = learner_actions.current_help_request(learner)
        if not current:
            return _msg("You don't have an open help request.")
        if current["status"] == "helping":
            return _msg("Your instructor is helping you now.")
        return _msg("Your help request is waiting for your instructor.")

    if intent == "orientation_repeat":
        cohort_name = (summary or {}).get("cohort", {}).get("name", "your classroom")
        return _msg(learner_context.joined_orientation(cohort_name))

    return _msg("I heard that, but I'm not sure what to do with it yet.")


def _resolve_what_should_i_do(ctx: Dict[str, Any]) -> str:
    summary = ctx["summary"]
    active_assignment = _current_assignment(ctx)
    active_project = None
    project = _current_project(ctx)
    if project and project["done_checkpoints"] < project["total_checkpoints"]:
        checkpoints = project.get("checkpoints") or []
        next_cp = next((c for c in checkpoints if c["id"] not in project["completed_checkpoints"]), None)
        if next_cp:
            active_project = {"title": project["title"], "next_step": next_cp["label"][0].lower() + next_cp["label"][1:]}
    new_items = _assignments_by_state(summary, "new")
    overdue_items = _assignments_by_state(summary, "overdue")
    pending_items = _assignments_by_state(summary, "pending")
    module = summary.get("module")
    module_phrase = learner_context.format_module_progress(
        module["title"], module["index"], module["total"]) if module else ""
    available_project = next(
        (p for p in summary.get("projects", []) if p["done_checkpoints"] == 0), None,
    )
    return learner_context.what_should_i_do(
        joined=True,
        active_assignment=active_assignment if (active_assignment and active_assignment["state"] == "in_progress") else None,
        active_project=active_project,
        new_assignment=new_items[0] if new_items else None,
        overdue_assignment=overdue_items[0] if overdue_items else None,
        pending_assignment=pending_items[0] if pending_items else None,
        module_phrase=module_phrase,
        available_project=available_project,
    )

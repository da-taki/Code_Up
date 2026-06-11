"""
"What did I learn today?" session recap for CodeUp.

Deterministic and Flask-free. Builds a short, honest recap of the current
session purely from the working memory in ``session_memory`` (generation, run
results, errors/fixes, features used, concepts practiced, tutorial/project
activity) plus one suggested next step. It never invents activity: with little
or no history it says so. The Flask layer may pass the result through Key 2 only
for grounded wording polish.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

NOT_ENOUGH = ("I do not have much session history yet. "
              "Try creating, running, or explaining some code first.")


def _has_history(mem: Dict[str, Any]) -> bool:
    return bool(
        mem.get("last_gen_prompt")
        or mem.get("last_run_ok") is not None
        or mem.get("features_used")
        or mem.get("concepts_practiced")
        or mem.get("project_files")
        or mem.get("tutorial_module")
        or mem.get("last_action")
    )


def _activity_sentences(mem: Dict[str, Any]) -> List[str]:
    bits: List[str] = []
    if mem.get("last_gen_prompt"):
        bits.append(f"generated code for {mem['last_gen_prompt']}")
    ran_ok = mem.get("last_run_ok")
    debugged = "debugged errors" in (mem.get("features_used") or [])
    if ran_ok is True:
        bits.append("ran it successfully")
    elif ran_ok is False:
        bits.append("ran it and worked through an error" if debugged else "ran it and hit an error")
    if mem.get("project_files"):
        bits.append(f"worked in a project with {len(mem['project_files'])} files")
    if mem.get("tutorial_module"):
        bits.append(f"practised the {mem['tutorial_module']} tutorial")
    return bits


def _features_clause(mem: Dict[str, Any]) -> str:
    feats = [f for f in (mem.get("features_used") or []) if f]
    # Drop ones already implied by the activity sentences to avoid repetition.
    feats = [f for f in feats if f not in {"generated code", "ran code"}]
    if not feats:
        return ""
    return "You used " + ", ".join(feats) + "."


def _concepts_clause(mem: Dict[str, Any]) -> str:
    concepts = [c for c in (mem.get("concepts_practiced") or []) if c]
    if not concepts:
        return ""
    return "You touched on " + ", ".join(concepts) + "."


def suggest_next_step(mem: Dict[str, Any]) -> str:
    """One concrete, encouraging next step grounded in what happened."""
    if mem.get("tutorial_module"):
        return "A good next step is to continue the tutorial, or try the next module."
    if mem.get("last_run_ok") is False:
        return "A good next step is to fix the error and run your code again."
    if mem.get("last_gen_prompt") and mem.get("last_run_ok") is None:
        return "A good next step is to run your generated code and listen to the output."
    if mem.get("last_gen_prompt"):
        return "A good next step is to write a small loop or function yourself, without generation."
    if mem.get("project_files"):
        return "A good next step is to run a project file and hear its output."
    return "A good next step is to ask for a walkthrough or a code map of your program."


def build_recap(mem: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a recap dict: {has_history, recap, next_step, speech}."""
    mem = mem or {}
    if not _has_history(mem):
        return {"has_history": False, "recap": NOT_ENOUGH, "next_step": "", "speech": NOT_ENOUGH}

    bits = _activity_sentences(mem)
    sentences: List[str] = []
    if bits:
        sentences.append("Today you " + ", then ".join(bits) + ".")
    for clause in (_features_clause(mem), _concepts_clause(mem)):
        if clause:
            sentences.append(clause)
    next_step = suggest_next_step(mem)
    sentences.append(next_step)

    recap = " ".join(s for s in sentences if s).strip()
    if not recap:
        recap = NOT_ENOUGH
    return {"has_history": True, "recap": recap, "next_step": next_step, "speech": recap}

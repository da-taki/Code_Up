"""Deterministic, non-visual State / Variable Watch narration.

Sighted programmers glance at variables, output and the current line. A visually
impaired learner cannot. This module turns CodeUp's existing sandbox execution
trace (the same trace produced for step narration) plus static AST analysis into
short spoken state: which variables exist, their current values and types, loop
position, condition results, and step-by-step movement.

SAFETY: this module never executes user code. Execution happens only in CodeUp's
existing sandbox (app._run_with_trace_for_narration), which already enforces the
step limit, output limit and wall-clock timeout. Here we only parse the resulting
trace and the code's AST. No AI.
"""

import ast
import re
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "list_variables", "parse_state", "narrate_state", "variable_value",
    "build_steps", "narrate_step", "loop_state", "condition_outcomes",
    "summarize_value",
]

_MAX_VALUE_CHARS = 60


# ---- static variable listing (no execution) ----------------------------

def _literal_type(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "boolean"
        if isinstance(node.value, (int, float)):
            return "number"
        if isinstance(node.value, str):
            return "text"
        if node.value is None:
            return "nothing yet"
    if isinstance(node, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(node, ast.Dict):
        return "dictionary"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in ("input",):
                return "text from input"
            if func.id in ("int", "len"):
                return "number"
            if func.id in ("float",):
                return "number"
            if func.id in ("str",):
                return "text"
            if func.id in ("list", "sorted"):
                return "list"
            if func.id in ("dict",):
                return "dictionary"
    return "value"


def list_variables(code: str) -> List[Dict[str, str]]:
    """Top-level (module) assigned variables with an inferred type. Static only."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []
    seen: Dict[str, str] = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [(t, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [(node.target, node.value)]
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            targets = [(node.target, None)]
        for target, value in targets:
            kind = _literal_type(value) if value is not None else "loop value"
            # Don't downgrade a known concrete type (e.g. score=0 then score=score+1).
            if target.id in seen and kind == "value" and seen[target.id] != "value":
                continue
            seen[target.id] = kind
    return [{"name": n, "type": t} for n, t in seen.items()]


def narrate_variables(code: str) -> str:
    variables = list_variables(code)
    if not variables:
        return "I do not see any variables in this program yet."
    parts = [f"{v['name']} ({v['type']})" for v in variables[:12]]
    extra = len(variables) - len(parts)
    summary = ", ".join(parts)
    if extra > 0:
        summary += f", and {extra} more"
    n = len(variables)
    return f"There {'is' if n == 1 else 'are'} {n} variable{'s' if n != 1 else ''}: {summary}."


# ---- value summarising --------------------------------------------------

def _classify(value_repr: str) -> Tuple[str, str]:
    v = (value_repr or "").strip()
    if v in ("True", "False"):
        return "boolean", v
    if re.fullmatch(r"-?\d+", v) or re.fullmatch(r"-?\d+\.\d+", v):
        return "number", v
    if v[:1] in ("'", '"'):
        return "text", v
    if v.startswith("["):
        return "list", v
    if v.startswith("{") and ":" in v:
        return "dictionary", v
    if v.startswith("{"):
        return "set", v
    if v.startswith("("):
        return "tuple", v
    return "value", v


def _count_items(container_repr: str) -> int:
    inner = container_repr.strip()[1:-1].strip()
    if not inner:
        return 0
    depth = 0
    count = 1
    in_str = ""
    for ch in inner:
        if in_str:
            if ch == in_str:
                in_str = ""
            continue
        if ch in "'\"":
            in_str = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return count


def summarize_value(value_repr: str, *, full: bool = False) -> str:
    """Beginner summary of a value. Hides huge values behind a description."""
    kind, v = _classify(value_repr)
    if kind == "list":
        n = _count_items(v)
        items = v.strip()[1:-1]
        all_numbers = bool(items) and all(
            re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*", part) for part in items.split(","))
        noun = "numbers" if all_numbers and n else "items"
        if full and len(v) <= _MAX_VALUE_CHARS:
            return f"a list {v}"
        return f"a list with {n} {noun}"
    if kind in ("dictionary", "set", "tuple"):
        n = _count_items(v)
        label = {"dictionary": "a dictionary with", "set": "a set with", "tuple": "a tuple with"}[kind]
        unit = "keys" if kind == "dictionary" else "items"
        if full and len(v) <= _MAX_VALUE_CHARS:
            return f"{kind} {v}"
        return f"{label} {n} {unit}"
    if kind == "text":
        if not full and len(v) > _MAX_VALUE_CHARS:
            return "a long piece of text"
        return v if full else "text"
    if len(v) > _MAX_VALUE_CHARS:
        return "a large value"
    return v


# ---- trace parsing ------------------------------------------------------

_INIT_RE = re.compile(r"^(\w+) initialized to (.+)$")
_CHANGE_RE = re.compile(r"^(\w+) changed from (.+) to (.+)$")
_SCOPE_RE = re.compile(r"^(\w+) went out of scope$")


def parse_state(trace: List[Dict[str, Any]], *, watched: Optional[set] = None) -> Dict[str, Dict[str, Any]]:
    """Final value of each variable, parsed from the trace's change strings."""
    state: Dict[str, Dict[str, Any]] = {}
    for event in trace or []:
        if event.get("type") != "state_change":
            continue
        line = event.get("line")
        file = event.get("file")
        for change in event.get("changes", []):
            m = _INIT_RE.match(change)
            if m:
                name, value = m.group(1), m.group(2)
                if watched and name not in watched:
                    continue
                state[name] = {"value": value, "line": line, "from": None, "file": file}
                continue
            m = _CHANGE_RE.match(change)
            if m:
                name, old, new = m.group(1), m.group(2), m.group(3)
                if watched and name not in watched:
                    continue
                state[name] = {"value": new, "line": line, "from": old, "file": file}
                continue
    return state


def narrate_state(state: Dict[str, Dict[str, Any]], *, output_lines: int = 0,
                  watched: Optional[set] = None) -> str:
    if not state:
        base = "I did not capture any variable values from the last run."
    else:
        lines = ["Current state:"]
        for name, info in list(state.items())[:12]:
            kind, _ = _classify(info["value"])
            if kind == "number":
                lines.append(f"{name} is {info['value']}.")
            elif kind == "boolean":
                lines.append(f"{name} is {info['value']}.")
            elif kind == "text":
                lines.append(f"{name} is {summarize_value(info['value'], full=True)}.")
            else:
                lines.append(f"{name} is {summarize_value(info['value'])}.")
        base = "\n".join(lines)
    if output_lines:
        base += f"\nThe program printed {output_lines} line{'s' if output_lines != 1 else ''}."
    return base


def variable_value(state: Dict[str, Dict[str, Any]], name: str) -> str:
    info = state.get(name)
    if not info:
        return (f"I do not have a value for {name} from the last run. "
                "Say 'show program state' after running, or check the name.")
    value = summarize_value(info["value"], full=True)
    sentence = f"{name} is currently {value}."
    if info.get("from") is not None and info.get("line"):
        sentence += f" It changed from {summarize_value(info['from'], full=True)} to {value} on line {info['line']}."
    elif info.get("line"):
        sentence += f" It was set on line {info['line']}."
    return sentence


# ---- step trace ---------------------------------------------------------

def _src_line(code: str, lineno: Optional[int]) -> str:
    if not lineno:
        return ""
    rows = (code or "").splitlines()
    if 1 <= lineno <= len(rows):
        return rows[lineno - 1].strip()
    return ""


def build_steps(trace: List[Dict[str, Any]], code: str) -> List[Dict[str, Any]]:
    """Compact, navigable steps from the trace (assignments, calls, returns).

    State changes are detected on the *next* executed line, so we attribute each
    one to the line two steps back (prev2), matching the existing narrator.
    """
    steps: List[Dict[str, Any]] = []
    prev1 = prev2 = None
    for event in trace or []:
        etype = event.get("type")
        file = event.get("file")
        if etype == "line_exec":
            prev2, prev1 = prev1, event.get("line")
        elif etype == "state_change":
            causing = prev2 if prev2 is not None else event.get("line")
            for change in event.get("changes", []):
                m = _INIT_RE.match(change)
                if m:
                    desc = f"{m.group(1)} becomes {summarize_value(m.group(2), full=True)}"
                else:
                    m = _CHANGE_RE.match(change)
                    if not m:
                        continue
                    desc = f"{m.group(1)} changes to {summarize_value(m.group(3), full=True)}"
                steps.append({"line": causing, "file": file, "kind": "assignment", "text": desc})
        elif etype == "call":
            func = event.get("function", "?")
            if func and func != "<module>":
                steps.append({"line": event.get("line"), "file": file, "kind": "call",
                              "text": f"Enter function {func}"})
        elif etype == "return":
            val = event.get("value", "")
            if val and val != "None":
                steps.append({"line": event.get("line"), "file": file, "kind": "return",
                              "text": f"Return {summarize_value(val, full=True)}"})
    return steps


def narrate_step(steps: List[Dict[str, Any]], index: int) -> str:
    if not steps:
        return "There are no steps to read. Say 'step through this' after writing code."
    index = max(0, min(index, len(steps) - 1))
    step = steps[index]
    where = f" on line {step['line']}" if step.get("line") else ""
    return f"Step {index + 1} of {len(steps)}: {step['text']}{where}."


# ---- loops & conditions -------------------------------------------------

def _spoken_condition(src: str) -> str:
    """Render comparison operators as words. Symbols like > are stripped by the
    voice-response sanitizer, and words are clearer for a screen-reader user."""
    replacements = [
        (">=", " is greater than or equal to "), ("<=", " is less than or equal to "),
        ("==", " equals "), ("!=", " is not equal to "),
        (">", " is greater than "), ("<", " is less than "),
    ]
    for symbol, words in replacements:
        src = src.replace(symbol, words)
    return re.sub(r"\s+", " ", src).strip()


def _loop_header_lines(code: str) -> set:
    lines = set()
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            lines.add(node.lineno)
    return lines


def loop_state(trace: List[Dict[str, Any]], code: str) -> str:
    """Narrate the nearest loop: iterations run and the current loop value."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        tree = None
    for_targets: Dict[int, str] = {}
    loop_lines: set = set()
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                loop_lines.add(node.lineno)
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                for_targets[node.lineno] = node.target.id

    last_loop_line = None
    for event in trace or []:
        if event.get("type") == "line_exec" and event.get("line") in loop_lines:
            last_loop_line = event.get("line")
    if last_loop_line is None:
        return "I did not see a loop run in the last trace. Say 'step through this' after writing a loop."

    var = for_targets.get(last_loop_line)
    if var:
        # Iterations = times the loop variable was (re)assigned. Avoids the
        # off-by-one from the loop header's final StopIteration check.
        iteration = sum(
            1 for e in trace if e.get("type") == "state_change"
            for ch in e.get("changes", [])
            if ch.startswith(f"{var} initialized") or ch.startswith(f"{var} changed"))
    else:
        iteration = max(1, sum(1 for e in trace if e.get("type") == "line_exec"
                               and e.get("line") == last_loop_line) - 1)

    state = parse_state(trace)
    parts = [f"Loop state: the loop on line {last_loop_line} ran {iteration} "
             f"time{'s' if iteration != 1 else ''}."]
    if var and var in state:
        parts.append(f"The current value of {var} is {summarize_value(state[var]['value'], full=True)}.")
    return " ".join(parts)


def _ticks(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group each line_exec with the state changes detected just after it, so the
    snapshot for a line includes the value set by that line (the trace records a
    change on the following executed line)."""
    ticks: List[Dict[str, Any]] = []
    current: Dict[str, str] = {}
    cur_line = None

    def _flush():
        if cur_line is not None:
            ticks.append({"line": cur_line, "state": dict(current)})

    for event in trace or []:
        etype = event.get("type")
        if etype == "line_exec":
            _flush()
            cur_line = event.get("line")
        elif etype == "state_change":
            for change in event.get("changes", []):
                m = _INIT_RE.match(change)
                if m:
                    current[m.group(1)] = m.group(2)
                    continue
                m = _CHANGE_RE.match(change)
                if m:
                    current[m.group(1)] = m.group(3)
    _flush()
    return ticks


def condition_outcomes(trace: List[Dict[str, Any]], code: str) -> List[Dict[str, Any]]:
    """Infer if/elif results by which branch ran next. Deterministic, best-effort."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []
    conds: Dict[int, Dict[str, Any]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            body_lines = {c.lineno for c in node.body}
            try:
                test_src = _spoken_condition(ast.unparse(node.test))
            except Exception:
                test_src = _spoken_condition(_src_line(code, node.lineno).lstrip("if ").rstrip(":"))
            names = [n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)]
            conds[node.lineno] = {"test": test_src, "body": body_lines, "names": names}

    ticks = _ticks(trace)
    outcomes: List[Dict[str, Any]] = []
    for i, tick in enumerate(ticks):
        line = tick["line"]
        if line not in conds:
            continue
        nxt = next((t["line"] for t in ticks[i + 1:] if t["line"] != line), None)
        if nxt is None:
            continue
        cond = conds[line]
        result = nxt in cond["body"]  # the if-body ran iff the condition was true
        state_here = tick["state"]
        reason_bits = [f"{nm} was {summarize_value(state_here[nm], full=True)}"
                       for nm in cond["names"] if nm in state_here]
        reason = (" because " + ", ".join(reason_bits)) if reason_bits else ""
        outcomes.append({"line": line, "test": cond["test"], "result": result,
                         "reason": reason})
    return outcomes


def narrate_condition(outcome: Optional[Dict[str, Any]]) -> str:
    if not outcome:
        return "I did not see a condition result in the last trace."
    word = "true" if outcome["result"] else "false"
    return f"Condition result: {outcome['test']} was {word}{outcome['reason']}."

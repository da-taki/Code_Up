"""Structural placement for spoken code edits.

"in loop print hello each time" names WHAT to add (print("Hello")) and
WHERE it belongs (the body of the loop). The rest of the edit pipeline only
understood the first half, so the line was appended to the end of the file.
This module grounds the location in the program's own AST:

    split_request()  -> the location phrase + the statement request
    build_statement()-> Python for the statement (code-aware print resolver)
    candidates()     -> the blocks of the named kind (loops, ifs, functions...)
    choose_target()  -> one block: the only one, the one at the cursor, or the
                        one CodeUp just created; otherwise ask which
    insert()         -> the new program with the statement at that place
    verify()         -> re-parse and check the new statement really is in the
                        requested body (valid syntax alone is not enough)

Everything is deterministic; an AI edit is used only for statements this
module cannot build, and its result must pass the same verify().
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from codeup.commands import intent_repair, variable_creation


@dataclass
class Location:
    kind: str          # loop | for | while | if | else | elif | function | return | input | print | assign | line
    relation: str      # inside | start | before | after
    anchor: str = ""   # e.g. "i" for "after print i"
    phrase: str = ""


@dataclass
class Target:
    node: ast.AST
    field: str         # body | orelse | parent (for before/after)
    label: str
    header_line: int


@dataclass
class Result:
    status: str                      # ok | clarify | not_structural | unbuildable
    code: str = ""
    message: str = ""
    target: Optional[Target] = None
    options: List[Dict[str, Any]] = field(default_factory=list)
    statement: str = ""
    location: Optional[Location] = None


# ---------------------------------------------------------------------------
# 1. Location phrases (English, broken English, Hinglish)
# ---------------------------------------------------------------------------

_BLOCK = (r"(?P<kind>for\s+loop|while\s+loop|loop|function|def|if(?:\s+block|\s+statement|\s+part)?|"
          r"else(?:\s+block|\s+part)?|elif(?:\s+block|\s+part)?)")
_DET = r"(?:(?:the|this|that|my|our|same)\s+)?"

_LOCATION_PATTERNS: Sequence[Tuple[re.Pattern, str, str]] = (
    # (pattern, relation, fixed kind or "" to read the kind group)
    (re.compile(r"\b(?:at\s+the\s+)?(?:start|beginning|top)\s+of\s+" + _DET + _BLOCK + r"\b"), "start", ""),
    (re.compile(r"\b(?:at\s+the\s+)?(?:end|bottom)\s+of\s+" + _DET + _BLOCK + r"(?:\s+body)?\b"), "inside", ""),
    (re.compile(r"\b(?P<rel>before|after)\s+(?:the\s+)?return(?:\s+statement|\s+line)?\b"), "", "return"),
    (re.compile(r"\b(?P<rel>before|after)\s+(?:the\s+)?input(?:\s+line|\s+statement)?\b"), "", "input"),
    (re.compile(r"\b(?P<rel>before|after)\s+(?:the\s+)?assignment\b"), "", "assign"),
    (re.compile(r"\b(?P<rel>before|after)\s+(?:this|the\s+current|current)\s+line\b"), "", "line"),
    (re.compile(r"\b(?P<rel>before|after)\s+(?:the\s+)?(?:line\s+)?print\s+(?P<anchor>[a-z_]\w*)\b"
                r"(?:\s+(?:in|inside)\s+" + _DET + r"loop)?"), "", "print"),
    (re.compile(r"\b(?P<rel>before|after)\s+(?:the\s+)?print(?:\s+line|\s+statement)?\b"), "", "print"),
    (re.compile(r"\b(?P<rel>before|after)\s+" + _DET + _BLOCK + r"\b"), "", ""),
    (re.compile(r"\b(?:inside|in|within|into)\s+" + _DET + _BLOCK + r"(?:\s+body)?\b"), "inside", ""),
    (re.compile(r"\b(?:under|inside)\s+(?:the\s+|this\s+)?condition\b|"
                r"\bwhen\s+(?:the\s+|this\s+)?condition\s+is\s+true\b|"
                r"\bif\s+the\s+condition\s+is\s+true\b"), "inside", "if"),
    (re.compile(r"\b(?:each|every)\s+(?:time|iteration|pass|round)(?:\s+(?:through|of|in)\s+" + _DET +
                r"loop)?\b|\bon\s+each\s+(?:iteration|pass)\b"), "inside", "loop"),
    # Hinglish: "loop ke andar", "loop mein", "har baar", "loop ke baad", "return se pehle"
    (re.compile(r"\b(?P<kind>loop|function|if|else)\s+(?:ke\s+)?(?:andar|mein|me|main)\b"), "inside", ""),
    (re.compile(r"\bhar\s+(?:baar|bar|iteration|round)(?:\s+(?:mein|me))?\b"), "inside", "loop"),
    (re.compile(r"\b(?P<kind>loop|function)\s+(?:ke\s+)?(?:baad|bad)\b"), "after", ""),
    (re.compile(r"\b(?P<kind>loop|function)\s+(?:se\s+|ke\s+)?pehle\b"), "before", ""),
    (re.compile(r"\breturn\s+(?:se\s+|ke\s+)?pehle\b"), "before", "return"),
)

_KIND_ALIASES = {
    "for loop": "for", "while loop": "while", "loop": "loop", "function": "function", "def": "function",
    "if": "if", "if block": "if", "if statement": "if", "if part": "if",
    "else": "else", "else block": "else", "else part": "else",
    "elif": "elif", "elif block": "elif", "elif part": "elif",
}

# The rest of the utterance must ask for code, not ask a question about it.
_QUESTION_START_RE = re.compile(
    r"^(?:what|why|how|where|which|who|explain|describe|read|tell|show\s+me\s+what|go\s+to|jump|move|"
    r"is|are|does|do\s+you|can\s+you\s+explain|kya|kyun|kaise|kahan|batao)\b")
_ACTION_RE = re.compile(
    r"\b(?:print|prints|display|say|show|add|insert|put|write|set|increase|increment|decrease|"
    r"equals?|call|return|ask|input|append|create|make|likho|dikhao|karo|kar\s+do|kardo)\b")
_FILLER_RE = re.compile(
    r"^(?:(?:acha|achha|accha|ok|okay|so|bhai|yaar|haan|please|now|um+|uh+|hey|and|then|also|just|"
    r"can\s+you|could\s+you|would\s+you)[\s,]+)+")


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().replace(",", " ").split())


def split_request(text: str) -> Optional[Tuple[Location, str]]:
    """Returns (location, remaining statement request) or None."""
    t = _FILLER_RE.sub("", _norm(text)).strip().rstrip(".!?")
    if not t or _QUESTION_START_RE.match(t):
        return None
    for pattern, relation, fixed_kind in _LOCATION_PATTERNS:
        m = pattern.search(t)
        if not m:
            continue
        groups = m.groupdict()
        kind = fixed_kind or _KIND_ALIASES.get(" ".join((groups.get("kind") or "").split()), "")
        rel = relation or groups.get("rel") or "inside"
        if not kind:
            continue
        rest = (t[:m.start()] + " " + t[m.end():]).strip()
        # A second location phrase ("in loop ... each time") only reinforces the first.
        for extra, _r, extra_kind in _LOCATION_PATTERNS:
            em = extra.search(rest)
            if em and (extra_kind in {"loop", ""} or extra_kind == kind):
                rest = (rest[:em.start()] + " " + rest[em.end():]).strip()
        rest = " ".join(rest.split())
        if not rest or not _ACTION_RE.search(rest):
            return None
        return Location(kind=kind, relation=rel, anchor=groups.get("anchor") or "", phrase=m.group(0)), rest
    return None


# ---------------------------------------------------------------------------
# 2. The statement to add
# ---------------------------------------------------------------------------

_LEAD_RE = re.compile(r"^(?:(?:add|insert|put|write|also|then|and|please|a|an|new|line|statement|that|which|"
                      r"to|so\s+it|so\s+that\s+it)\s+)+")
_TRAIL_RE = re.compile(r"\s+(?:please|too|as\s+well|also|now|there|here|karo|kar\s+do|kardo|kare|karna)$")


def build_statement(request: str, code: str = "") -> Optional[str]:
    """Python for the requested statement (may be several lines), unindented."""
    raw = " ".join(str(request or "").split())
    # "make variable total with value 0", "make list marks 90 80": the
    # variable-creation parser owns names, values and containers.
    created = variable_creation.statement_for(raw, code)
    if created:
        return created
    hinglish = re.match(r"^(?P<c>.+?)\s+print\s+(?:karo|kar\s+do|kardo|kare|karna|kar)$", raw)
    if hinglish:
        return f"print({intent_repair.print_argument_python(hinglish.group('c'), code)})"
    hinglish2 = re.match(r"^print\s+(?:karo|kar\s+do|kardo)\s+(?P<c>.+)$", raw)
    if hinglish2:
        return f"print({intent_repair.print_argument_python(hinglish2.group('c'), code)})"
    body = _LEAD_RE.sub("", raw)
    previous = None
    while previous != body:
        previous = body
        body = _TRAIL_RE.sub("", body).strip()
    saying = re.match(r"^(?:print|display)(?:\s+line|\s+statement)?\s+(?:saying|that\s+says)\s+(?P<c>.+)$", body)
    if saying:
        return f"print({intent_repair._quote(intent_repair.display_text(saying.group('c')))})"
    printed = re.match(r"^(?:print|prints|display|displays|say|says|show|shows)\s+(?P<c>.+)$", body)
    if printed and not re.search(r"\b(?:loop|times|numbers?\s+from)\b", printed.group("c")):
        return f"print({intent_repair.print_argument_python(printed.group('c'), code)})"
    increment = (re.match(r"^(?:increase|increment)\s+(?P<v>[a-z_]\w*)\s+by\s+(?P<n>\w+)$", body)
                 or re.match(r"^add\s+(?P<n>\w+)\s+to\s+(?P<v>[a-z_]\w*)$", body))
    if increment:
        number = intent_repair._to_number(increment.group("n"))
        if number is not None:
            return f"{increment.group('v')} += {number}"
    decrement = re.match(r"^(?:decrease|decrement)\s+(?P<v>[a-z_]\w*)\s+by\s+(?P<n>\w+)$", body)
    if decrement and intent_repair._to_number(decrement.group("n")) is not None:
        return f"{decrement.group('v')} -= {intent_repair._to_number(decrement.group('n'))}"
    assign = (re.match(r"^(?:set\s+)?(?P<v>[a-z_]\w*)\s+(?:equals?|equal\s+to|=|to)\s+(?P<e>.+)$", body)
              if re.match(r"^(?:set\s+)?[a-z_]\w*\s+(?:equals?|equal\s+to|=)\s+", body)
              or body.startswith("set ") else None)
    if assign:
        value = intent_repair.print_argument_python(assign.group("e"), code)
        return f"{assign.group('v')} = {value}"
    try:
        built = intent_repair.build_insert_python(body, code)
    except Exception:
        built = None
    if built:
        return built
    counting = intent_repair.build_counting_loop_insert("insert " + body)
    return counting[0] if counting else None


# ---------------------------------------------------------------------------
# 3. Finding the block
# ---------------------------------------------------------------------------

def _src_line(code_lines: List[str], lineno: int) -> str:
    return code_lines[lineno - 1].strip() if 0 < lineno <= len(code_lines) else ""


def _segment(code: str, node: Optional[ast.AST]) -> str:
    try:
        return " ".join((ast.get_source_segment(code, node) or "").split()) if node is not None else ""
    except Exception:
        return ""


def _label(code: str, node: ast.AST, kind: str) -> str:
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return f"the loop over {_segment(code, node.iter)} (line {node.lineno})"
    if isinstance(node, ast.While):
        return f"the while loop checking {_segment(code, node.test)} (line {node.lineno})"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return f"the function {node.name} (line {node.lineno})"
    if isinstance(node, ast.If):
        prefix = {"else": "the else of the if", "elif": "the elif"}.get(kind, "the if")
        test = node.orelse[0].test if kind == "elif" else node.test
        return f"{prefix} checking {_segment(code, test)} (line {node.lineno})"
    return f"line {getattr(node, 'lineno', '?')}"


def _elif_children(tree: ast.AST) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            found.add(id(node.orelse[0]))
    return found


def _calls(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
               for n in ast.walk(node))


def candidates(tree: ast.AST, code: str, location: Location, cursor_line: Optional[int] = None) -> List[Target]:
    kind, found = location.kind, []
    elifs = _elif_children(tree)
    lines = code.splitlines()
    for node in ast.walk(tree):
        if kind in {"loop", "for"} and isinstance(node, (ast.For, ast.AsyncFor)) or \
                kind in {"loop", "while"} and isinstance(node, ast.While):
            found.append(Target(node, "body", _label(code, node, kind), node.lineno))
        elif kind == "function" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(Target(node, "body", _label(code, node, kind), node.lineno))
        elif kind == "if" and isinstance(node, ast.If) and id(node) not in elifs:
            found.append(Target(node, "body", _label(code, node, kind), node.lineno))
        elif kind == "else" and isinstance(node, ast.If) and node.orelse and \
                not (len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)):
            found.append(Target(node, "orelse", _label(code, node, kind), node.lineno))
        elif kind == "elif" and isinstance(node, ast.If) and len(node.orelse) == 1 and \
                isinstance(node.orelse[0], ast.If):
            found.append(Target(node.orelse[0], "body", _label(code, node, kind), node.orelse[0].lineno))
        elif kind == "return" and isinstance(node, ast.Return):
            found.append(Target(node, "parent", f"the return on line {node.lineno}", node.lineno))
        elif kind in {"input", "print", "assign"} and isinstance(node, ast.stmt) and \
                not isinstance(node, (ast.For, ast.While, ast.If, ast.FunctionDef, ast.With, ast.Try)):
            if kind == "input" and _calls(node, "input") or kind == "assign" and isinstance(
                    node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                found.append(Target(node, "parent", f"line {node.lineno}: {_src_line(lines, node.lineno)}", node.lineno))
            elif kind == "print" and isinstance(node, ast.Expr) and _calls(node, "print"):
                if location.anchor:
                    args = node.value.args if isinstance(node.value, ast.Call) else []
                    if not any(_segment(code, a) == location.anchor for a in args):
                        continue
                found.append(Target(node, "parent", f"line {node.lineno}: {_src_line(lines, node.lineno)}", node.lineno))
    if kind == "line" and cursor_line:
        for node in ast.walk(tree):
            if isinstance(node, ast.stmt) and node.lineno == cursor_line:
                found.append(Target(node, "parent", f"line {cursor_line}", cursor_line))
                break
    if location.relation in {"before", "after"}:
        # "after the loop" places the statement beside the whole block, not in it.
        for t in found:
            if t.field in {"body", "orelse"}:
                t.field = "parent"
    found.sort(key=lambda t: t.header_line)
    return found


def _span(node: ast.AST) -> Tuple[int, int]:
    return node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno


def header_lines(code: str) -> List[str]:
    """Stripped header lines of compound statements (for recency matching)."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []
    lines = (code or "").splitlines()
    return [_src_line(lines, n.lineno) for n in ast.walk(tree)
            if isinstance(n, (ast.For, ast.AsyncFor, ast.While, ast.If, ast.FunctionDef, ast.AsyncFunctionDef))]


def choose_target(found: List[Target], code: str, *, cursor_line: Optional[int] = None,
                  recent_headers: Sequence[str] = ()) -> Tuple[Optional[Target], str]:
    """(target, reason). target None means genuinely ambiguous."""
    if len(found) == 1:
        return found[0], "only"
    if cursor_line:
        containing = [t for t in found if _span(t.node)[0] <= cursor_line <= _span(t.node)[1]]
        if containing:
            return max(containing, key=lambda t: t.header_line), "cursor"
    if recent_headers:
        lines = code.splitlines()
        recent = {h.strip() for h in recent_headers if h.strip()}
        matching = [t for t in found if _src_line(lines, t.header_line) in recent]
        if len(matching) == 1:
            return matching[0], "recent"
    return None, "ambiguous"


# ---------------------------------------------------------------------------
# 4. Insertion and verification
# ---------------------------------------------------------------------------

def _indent_of(code_lines: List[str], lineno: int) -> str:
    line = code_lines[lineno - 1] if 0 < lineno <= len(code_lines) else ""
    return line[: len(line) - len(line.lstrip())]


def insert(code: str, target: Target, location: Location, statement: str) -> Tuple[str, int, int]:
    """(new_code, first inserted line, number of inserted lines incl. spacing)."""
    lines = code.splitlines()
    node = target.node
    if target.field in {"body", "orelse"}:
        block = getattr(node, target.field)
        indent = _indent_of(lines, block[0].lineno)
        if location.relation == "start":
            at = block[0].lineno - 1
        else:
            at = max(_span(stmt)[1] for stmt in block)
    else:  # before/after a statement or a whole block
        indent = _indent_of(lines, node.lineno)
        at = node.lineno - 1 if location.relation == "before" else _span(node)[1]
    new_lines = [indent + ln if ln.strip() else ln for ln in statement.splitlines()]
    spacing = 0
    if target.field == "parent" and location.relation == "after" and not indent and \
            isinstance(node, (ast.For, ast.While, ast.If, ast.FunctionDef, ast.With, ast.Try)):
        new_lines = [""] + new_lines   # a blank line after a finished top-level block
        spacing = 1
    result = lines[:at] + new_lines + lines[at:]
    return "\n".join(result) + ("\n" if code.endswith("\n") else ""), at + 1 + spacing, len(new_lines) - spacing


def _parents(tree: ast.AST) -> Dict[int, Tuple[ast.AST, str]]:
    parents: Dict[int, Tuple[ast.AST, str]] = {}
    for node in ast.walk(tree):
        for name in ("body", "orelse", "finalbody"):
            for child in getattr(node, name, []) or []:
                if isinstance(child, ast.AST):
                    parents[id(child)] = (node, name)
    return parents


def verify(new_code: str, target: Target, location: Location, first_line: int, count: int) -> bool:
    """The inserted statements parse and sit in the requested place: in the
    target's body (or else-body), or beside the target for before/after."""
    try:
        tree = ast.parse(new_code)
    except SyntaxError:
        return False
    parents = _parents(tree)
    shift = count if first_line <= target.node.lineno else 0
    new_target = next((n for n in ast.walk(tree) if isinstance(n, ast.stmt)
                       and n.lineno == target.node.lineno + shift and type(n) is type(target.node)), None)
    if new_target is None:
        return False
    last = first_line + count
    inserted = [n for n in ast.walk(tree) if isinstance(n, ast.stmt) and first_line <= n.lineno < last]
    top = [n for n in inserted
           if not (isinstance(parents.get(id(n), (None, ""))[0], ast.stmt)
                   and first_line <= parents[id(n)][0].lineno < last)]
    if not top:
        return False
    expected = (new_target, target.field) if target.field in {"body", "orelse"} else parents.get(id(new_target))
    if expected is None:
        return False
    return all(parents.get(id(stmt)) is not None and parents[id(stmt)][0] is expected[0]
               and parents[id(stmt)][1] == expected[1] for stmt in top)


def plan(text: str, code: str, *, cursor_line: Optional[int] = None,
         recent_headers: Sequence[str] = (), forced_target_line: Optional[int] = None,
         statement: Optional[str] = None) -> Result:
    """Deterministic structural edit for one utterance against the current code."""
    split = split_request(text)
    if split is None or not str(code or "").strip():
        return Result("not_structural")
    location, request = split
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return Result("not_structural")
    found = candidates(tree, code, location, cursor_line)
    noun = {"loop": "loop", "for": "for loop", "while": "while loop", "if": "if statement",
            "else": "else block", "elif": "elif block", "function": "function", "return": "return statement",
            "input": "input line", "print": "print line", "assign": "assignment", "line": "current line"}[location.kind]
    if not found:
        return Result("clarify", message=f"I could not find a {noun} in your program to put that in.",
                      location=location)
    if forced_target_line is not None:
        chosen = [t for t in found if t.header_line == forced_target_line]
        target = chosen[0] if chosen else None
    else:
        target, _reason = choose_target(found, code, cursor_line=cursor_line, recent_headers=recent_headers)
    if target is None:
        names = [t.label for t in found[:4]]
        question = f"Which {noun} do you mean: " + (", ".join(names[:-1]) + ", or " + names[-1] if len(names) > 2
                                                   else " or ".join(names)) + "?"
        return Result("clarify", message=question, location=location,
                      options=[{"line": t.header_line, "label": t.label} for t in found[:4]])
    built = statement or build_statement(request, code)
    if not built:
        return Result("unbuildable", target=target, location=location, statement=request)
    new_code, first, count = insert(code, target, location, built)
    if not verify(new_code, target, location, first, count):
        return Result("unbuildable", target=target, location=location, statement=request)
    return Result("ok", code=new_code, target=target, location=location, statement=built)


_LOOSE_KIND_WORDS = (
    ("loop", r"\b(?:loop|loops|iteration|round|pass|har|each|every|while|for)\b"),
    ("function", r"\b(?:function|def)\b"),
    ("else", r"\belse\b"),
    ("if", r"\b(?:if|condition)\b"),
)
_LOOSE_LOCATION_RE = re.compile(
    r"\b(?:loop|function|if|else|elif|while|inside|within|andar|usme|usmein|isme|ismein|iske|uske|har|each|"
    r"every|before|after|pehle|baad|body|block|round|iteration|condition)\b")


def has_loose_location(text: str) -> bool:
    """Free-form wording that may place code in a block ("usme", "har round")."""
    return bool(_LOOSE_LOCATION_RE.search(_norm(text)))


def loose_target(text: str, code: str) -> Optional[Target]:
    """The single block of the kind the words point at, when there is exactly one."""
    t = _norm(text)
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    for kind, pattern in _LOOSE_KIND_WORDS:
        if re.search(pattern, t):
            found = candidates(tree, code, Location(kind=kind, relation="inside"))
            return found[0] if len(found) == 1 else None
    return None


def added_statements_within(old_code: str, new_code: str, target: Target) -> bool:
    """For an AI-produced edit: every line it added lies inside the target block
    (or next to it, for before/after), and nothing else was removed."""
    import difflib
    old_lines, new_lines = old_code.splitlines(), new_code.splitlines()
    try:
        tree = ast.parse(new_code)
    except SyntaxError:
        return False
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    added: List[int] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"delete", "replace"} and any(old_lines[k].strip() for k in range(i1, i2)):
            return False
        if tag in {"insert", "replace"}:
            added.extend(j + 1 for j in range(j1, j2) if new_lines[j].strip())
    if not added:
        return False
    header = _src_line(old_lines, target.header_line)
    owners = [n for n in ast.walk(tree) if isinstance(n, ast.stmt) and _src_line(new_lines, n.lineno) == header]
    for owner in owners:
        start, end = _span(owner)
        if target.field in {"body", "orelse"} and all(start < line <= end for line in added):
            return True
    return False

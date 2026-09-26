"""Checkpoint-based guided learning: CodeUp walks the learner through writing code.

The loop, per checkpoint:
  1. give ONE small goal;
  2. the learner writes code and says "check my work";
  3. the learner's actual code is inspected (AST - deterministic, never AI-judged);
  4. specific feedback explains what is right and what is missing;
  5. hints climb a ladder on request: small -> stronger -> explicit example;
  6. some checkpoints ask a quick understanding question;
  7. then the next checkpoint.

State lives in the per-session memory dict (``mem["guided"]``), so learners
never share progress. The learner model (``codeup.learning.learner_model``)
changes the scaffolding: concepts that needed reinforcement get smaller
steps, a concept reminder and proactive hint offers; concepts the learner has
repeatedly demonstrated skip trivial steps and unprompted hints.
"""

from __future__ import annotations

import ast
import contextvars
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from codeup.accessibility.speech_output import python_code_to_speech
from codeup.learning import learner_model

# Classroom assistance for the current request: the caller's resolved
# capability settings from codeup/classroom/ai_policy (None = no classroom
# restriction). Set by handle()/answer_question() for one call only, so it is
# never stored in session memory and cannot leak between requests.
_ASSIST: contextvars.ContextVar = contextvars.ContextVar("guided_assist", default=None)


def _allows(capability: str) -> bool:
    settings = _ASSIST.get()
    return not isinstance(settings, dict) or settings.get(capability, True) is not False


# ---------------------------------------------------------------------------
# Code inspection helpers
# ---------------------------------------------------------------------------

class Code:
    """Parsed view of the learner's code with small, readable queries."""

    def __init__(self, source: str):
        self.src = str(source or "")
        self.error: Optional[SyntaxError] = None
        try:
            self.tree: Optional[ast.AST] = ast.parse(self.src)
        except SyntaxError as exc:
            self.tree = None
            self.error = exc
        self.nodes = list(ast.walk(self.tree)) if self.tree is not None else []

    @property
    def empty(self) -> bool:
        return not self.src.strip()

    # -- assignments -------------------------------------------------------
    def assignments(self, name: str) -> List[ast.AST]:
        found = []
        for node in self.nodes:
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                    found.append(node)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                if isinstance(node.target, ast.Name) and node.target.id == name:
                    found.append(node)
        return found

    def assigned_value(self, name: str) -> Optional[ast.AST]:
        for node in self.assignments(name):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                return node.value
        return None

    def assigned_names(self) -> List[str]:
        names = []
        for node in self.nodes:
            if isinstance(node, ast.Assign):
                names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        return names

    def first_line(self, name: str) -> Optional[int]:
        lines = [getattr(n, "lineno", None) for n in self.assignments(name)]
        lines = [line for line in lines if line]
        return min(lines) if lines else None

    # -- calls ----------------------------------------------------------------
    def calls(self, func: str) -> List[ast.Call]:
        return [n for n in self.nodes if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == func]

    def method_calls(self, method: str) -> List[ast.Call]:
        return [n for n in self.nodes
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == method]

    def prints(self) -> List[ast.Call]:
        return self.calls("print")

    def print_uses_name(self, name: str) -> bool:
        return any(_uses_name(arg, name) for call in self.prints() for arg in call.args)

    def print_has_string(self, text: Optional[str] = None) -> bool:
        for call in self.prints():
            for arg in call.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        if text is None or text.lower() in sub.value.lower():
                            return True
        return False

    def bare_expression(self, name: str) -> bool:
        return any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Name) and n.value.id == name
                   for n in self.nodes)

    def of_type(self, *types) -> List[ast.AST]:
        return [n for n in self.nodes if isinstance(n, types)]

    def functions(self) -> Dict[str, ast.FunctionDef]:
        return {n.name: n for n in self.nodes if isinstance(n, ast.FunctionDef)}

    def module_calls(self, func: str) -> List[ast.Call]:
        """Calls of ``func`` outside that function's own body."""
        if self.tree is None:
            return []
        own = self.functions().get(func)
        inside = set(id(n) for n in ast.walk(own)) if own is not None else set()
        return [c for c in self.calls(func) if id(c) not in inside]


def _uses_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(sub, ast.Name) and sub.id == name for sub in ast.walk(node))


def _is_number(node: Optional[ast.AST]) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _is_number(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_number(node.left) and _is_number(node.right)
    return False


def _is_string(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_input_call(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "input"


def _is_converted_input(node: Optional[ast.AST]) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"int", "float"}
            and node.args and _is_input_call(node.args[0]))


def _loop_body_nodes(loop: ast.AST) -> List[ast.AST]:
    return [sub for stmt in getattr(loop, "body", []) for sub in ast.walk(stmt)]


def _increments(nodes: List[ast.AST], name: str) -> bool:
    for n in nodes:
        if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and n.target.id == name:
            return True
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            if isinstance(n.value, ast.BinOp) and _uses_name(n.value, name):
                return True
    return False


def _resets_inside(loop: ast.AST, name: str) -> bool:
    for stmt in getattr(loop, "body", []):
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
            if _is_number(stmt.value) or (isinstance(stmt.value, ast.Constant) and stmt.value.value == 0):
                return True
    return False


def syntax_feedback(code: Code) -> Optional[str]:
    if code.error is None:
        return None
    err = code.error
    msg = str(err.msg or "a syntax problem")
    line = err.lineno or 1
    low = msg.lower()
    if "expected ':'" in low:
        return f"Python cannot read line {line} yet: the line needs a colon at the end."
    if "expected an indented block" in low:
        return f"Line {line} needs to be indented with four spaces, because it belongs inside the block above it."
    if "unexpected indent" in low:
        return f"Line {line} is indented but does not belong inside a block. Remove the spaces at its start."
    if "unterminated string" in low or "eol while scanning" in low:
        return f"The text on line {line} starts with a quote but never closes it. Add the closing quote."
    if "was never closed" in low or "unexpected eof" in low:
        return f"A bracket on line {line} is opened but never closed. Add the closing parenthesis."
    if "maybe you meant '=='" in low or "cannot assign to" in low:
        return f"Line {line} uses one equals sign where a comparison is needed. Use two equals signs, or >=."
    return f"Python cannot read your code yet: {msg} on line {line}. Fix that first."


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

CheckFn = Callable[[Code], Optional[str]]


@dataclass(frozen=True)
class Question:
    ask: Callable[[Code], Optional[Tuple[str, Tuple[str, ...]]]]
    explain: str


@dataclass(frozen=True)
class Checkpoint:
    id: str
    goal: str
    concept: str
    check: CheckFn
    hints: Tuple[str, ...]
    example: str
    explain: str
    note: str = ""
    trivial: bool = False
    small_steps: Tuple[Tuple[str, CheckFn], ...] = ()
    starter: str = ""
    question: Optional[Question] = None


@dataclass(frozen=True)
class Path:
    id: str
    title: str
    aliases: Tuple[str, ...]
    checkpoints: Tuple[Checkpoint, ...] = field(default_factory=tuple)


def _need(code: Code) -> Optional[str]:
    if code.empty:
        return "The editor is empty. Write your line of code first, then say check my work."
    return syntax_feedback(code)


# ---- first programs ----------------------------------------------------------

def _chk_print_hello(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    prints = c.prints()
    if not prints:
        return "Your code does not print anything yet."
    if all(not call.args for call in prints):
        return "Your print has empty parentheses, so it only prints a blank line. Put text in quotes inside them."
    if c.print_has_string():
        return None
    names = [a.id for call in prints for a in call.args if isinstance(a, ast.Name)]
    if names:
        return f"{names[0]} without quotes is read as a variable name. Put quotes around the text, like \"Hello\"."
    return "Your print does not show any text yet. Put some text in quotes inside the parentheses."


def _chk_marks_variable(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    value = c.assigned_value("marks")
    if value is None:
        similar = [n for n in c.assigned_names() if n.lower().startswith("mark") or n.lower() == "score"]
        if similar:
            return f"I found a variable called {similar[0]}, but this step needs the exact name marks, all lowercase."
        if any(isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == "marks" for n in c.nodes):
            return "You used two equals signs, which compares values. Use one equals sign to store a value in marks."
        return "I do not see a variable called marks yet."
    if _is_string(value):
        return "marks holds text because the number is in quotes. Remove the quotes so marks holds a number."
    if not _is_number(value):
        return "marks exists, but it should hold a plain number, like 80."
    return None


def _chk_print_marks(c: Code) -> Optional[str]:
    if (msg := _chk_marks_variable(c)):
        if c.assigned_value("marks") is None and c.error is None and not c.empty:
            return "Your code no longer creates marks. Keep the line marks = a number above your print."
        return msg
    if c.print_uses_name("marks"):
        call_line = min(call.lineno for call in c.prints() if any(_uses_name(a, "marks") for a in call.args))
        if call_line < (c.first_line("marks") or 0):
            return "print(marks) comes before marks is created. Move the print line below the marks line."
        return None
    if c.print_has_string("marks"):
        return ("You printed the word marks in quotes, which is just text. Remove the quotes to print the value "
                "stored in marks.")
    if c.bare_expression("marks"):
        return "You created marks correctly, but your program does not print it yet."
    if c.prints():
        return "You are printing, but not the marks variable. Put marks inside the print parentheses."
    return "You created marks correctly, but your program does not print it yet."


def _q_marks_value(c: Code) -> Optional[Tuple[str, Tuple[str, ...]]]:
    value = c.assigned_value("marks")
    if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
        shown = str(value.value)
        return ("Quick check: when this program runs, what will it show?", (shown,))
    return None


def _chk_input_name(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    value = c.assigned_value("name")
    if _is_input_call(value):
        return None
    if value is not None:
        return "name is set to a fixed value. Use input so the person running the program can type their name."
    if c.calls("input"):
        return "You call input, but the answer is not stored anywhere. Put name = in front of input."
    return "I do not see input yet. input asks a question and waits for an answer."


def _chk_greeting(c: Code) -> Optional[str]:
    if (msg := _chk_input_name(c)):
        return msg
    if c.print_uses_name("name"):
        return None
    if c.print_has_string("name"):
        return "You printed the word name in quotes. Remove the quotes around name so the typed name is shown."
    if c.prints():
        return "Your greeting does not include name yet. Add name inside the print, after a comma."
    return "Now print a greeting that includes name."


# ---- conditions --------------------------------------------------------------

def _compare_on(c: Code, name: str) -> List[ast.Compare]:
    return [n for n in c.of_type(ast.Compare) if _uses_name(n, name)]


def _chk_compare(c: Code) -> Optional[str]:
    if (msg := _chk_marks_variable(c)):
        return msg
    compares = _compare_on(c, "marks")
    if not compares:
        return "I do not see a comparison yet. A comparison uses a symbol like greater than or equal to, written >=."
    if not any(isinstance(op, (ast.GtE, ast.Gt)) for cmp in compares for op in cmp.ops):
        return "Your comparison does not check at least 40 yet. Use >= 40."
    if not c.print_uses_name("marks") and not any(_uses_name(a, "marks") for call in c.prints() for a in call.args):
        return "You wrote a comparison but did not print its result. Put it inside print."
    return None


def _ifs_on(c: Code, name: str) -> List[ast.If]:
    return [n for n in c.of_type(ast.If) if _uses_name(n.test, name)]


def _body_prints(stmts) -> bool:
    return any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "print"
               for stmt in stmts for sub in ast.walk(stmt))


def _chk_if(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    if c.assigned_value("marks") is None:
        return "Create marks with a number first, above the if line."
    ifs = _ifs_on(c, "marks")
    if not ifs:
        return "I do not see an if statement that checks marks yet."
    if not any(_body_prints(i.body) for i in ifs):
        return "Your if statement has no print inside it yet. Indent a print line under the if."
    return None


def _chk_else(c: Code) -> Optional[str]:
    if (msg := _chk_if(c)):
        return msg
    ifs = _ifs_on(c, "marks")
    if not any(i.orelse and not (len(i.orelse) == 1 and isinstance(i.orelse[0], ast.If)) for i in ifs):
        return "Your if works. Now add else, lined up with the if, with a print for fail indented under it."
    if not any(i.orelse and _body_prints(i.orelse) for i in ifs):
        return "Your else has no print inside it yet."
    return None


def _thresholds(if_node: ast.If) -> List[float]:
    values = []
    node: Optional[ast.If] = if_node
    while isinstance(node, ast.If):
        nums = [s.value for s in ast.walk(node.test) if isinstance(s, ast.Constant)
                and isinstance(s.value, (int, float)) and not isinstance(s.value, bool)]
        values.append(nums[0] if nums else None)
        node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
    return values


def _chk_elif(c: Code) -> Optional[str]:
    if (msg := _chk_if(c)):
        return msg
    chains = [i for i in _ifs_on(c, "marks") if len(i.orelse) == 1 and isinstance(i.orelse[0], ast.If)]
    if not chains:
        return "You have an if, but no elif yet. elif adds a second condition between if and else."
    top = chains[0]
    tail = top.orelse[0]
    while len(tail.orelse) == 1 and isinstance(tail.orelse[0], ast.If):
        tail = tail.orelse[0]
    if not tail.orelse:
        return "Add a final else for every mark that is below all your conditions."
    values = [v for v in _thresholds(top) if v is not None]
    if len(values) >= 2 and values[0] < values[1]:
        return (f"Your first condition checks {values[0]}, which also catches marks meant for the elif. "
                "Check the biggest number first.")
    return None


def _chk_input_marks(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    value = c.assigned_value("marks")
    if _is_converted_input(value):
        if not _ifs_on(c, "marks"):
            return "marks now comes from input. Keep your grade if statements below it."
        return None
    if _is_input_call(value):
        return "input gives text, and text cannot be compared with numbers. Wrap it in int, like int(input(...))."
    return "marks should come from input now, so the user can type it."


# ---- loops --------------------------------------------------------------------

def _for_loops(c: Code) -> List[ast.For]:
    return [n for n in c.of_type(ast.For)]


def _chk_for_range(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    loops = [f for f in _for_loops(c) if isinstance(f.iter, ast.Call) and isinstance(f.iter.func, ast.Name)
             and f.iter.func.id == "range"]
    if not loops:
        return "I do not see a for loop with range yet."
    loop = loops[0]
    var = loop.target.id if isinstance(loop.target, ast.Name) else ""
    if not any(_uses_name(a, var) for sub in _loop_body_nodes(loop) if isinstance(sub, ast.Call)
               and isinstance(sub.func, ast.Name) and sub.func.id == "print" for a in sub.args):
        return "Your loop does not print the loop variable. Print it inside the loop so each number is shown."
    args = loop.iter.args
    if len(args) == 1 and isinstance(args[0], ast.Constant) and args[0].value == 4:
        return "range(4) stops before 4, so it shows 0 to 3. Use range(5) to include 4."
    return None


def _chk_while(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    loops = [w for w in c.of_type(ast.While) if _uses_name(w.test, "count")]
    if not loops:
        return "I do not see a while loop that checks count yet. Start with count = 1, then while count <= 3:."
    if c.assigned_value("count") is None:
        return "count is used by the loop but never created. Add count = 1 above the loop."
    loop = loops[0]
    if not _increments(_loop_body_nodes(loop), "count"):
        return ("count never changes inside the loop, so the loop would run forever. "
                "Add count = count + 1 inside the loop.")
    if not _body_prints(loop.body):
        return "The loop changes count but does not print it. Add print(count) inside the loop."
    return None


def _chk_counter(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    loops = c.of_type(ast.For, ast.While)
    if not loops:
        return "Counting needs a loop. Loop over the numbers 1 to 10."
    if any(_resets_inside(loop, "count") for loop in loops):
        return "count = 0 is inside the loop, so it resets every time. Move it above the loop."
    if c.assigned_value("count") is None:
        return "Create count = 0 above the loop so there is something to add to."
    if not any(_increments(_loop_body_nodes(loop), "count") for loop in loops):
        return "count never goes up. Inside the loop, add count = count + 1 when a number is even."
    if not any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod) for n in c.nodes):
        return "You count every number. Use number % 2 == 0 in an if, so only even numbers are counted."
    return None


def _chk_accumulator(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    loops = _for_loops(c)
    if not loops:
        return "Adding the marks one by one needs a for loop over them."
    if any(_resets_inside(loop, "total") for loop in loops):
        return "total = 0 is inside the loop, so the total starts again every time. Move it above the loop."
    if c.assigned_value("total") is None:
        return "Create total = 0 above the loop."
    loop = loops[0]
    body = _loop_body_nodes(loop)
    var = loop.target.id if isinstance(loop.target, ast.Name) else ""
    overwrites = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "total" for t in n.targets)
                     and isinstance(n.value, ast.Name) and n.value.id == var for n in body)
    if overwrites:
        return f"total = {var} replaces the total each time instead of adding. Use total = total + {var}."
    if not _increments(body, "total"):
        return f"Inside the loop, add each mark to total, like total = total + {var or 'mark'}."
    return None


def _chk_average(c: Code) -> Optional[str]:
    if (msg := _chk_accumulator(c)):
        return msg
    divisions = [n for n in c.nodes if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Div, ast.FloorDiv))
                 and _uses_name(n.left, "total")]
    if not divisions:
        return "Your total works. Now divide total by the number of marks to get the average."
    loop = _for_loops(c)[0]
    if any(d in _loop_body_nodes(loop) for d in divisions):
        return "You divide inside the loop. Calculate the average once, after the loop ends."
    if all(isinstance(d.op, ast.FloorDiv) for d in divisions):
        return "Two slashes drop the decimal part. Use a single slash for the exact average."
    if not c.prints():
        return "Print the average so you can hear it."
    return None


# ---- collections -------------------------------------------------------------

def _chk_list(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    value = c.assigned_value("marks")
    if isinstance(value, ast.List):
        if len(value.elts) < 3:
            return f"Your list has {len(value.elts)} item{'s' if len(value.elts) != 1 else ''}. Put three numbers in it, separated by commas."
        return None
    if isinstance(value, ast.Tuple):
        return "Those round brackets make a tuple. Use square brackets for a list."
    if value is not None:
        return "marks exists, but it is not a list yet. Use square brackets around the numbers."
    return "I do not see a list called marks yet."


def _chk_index(c: Code) -> Optional[str]:
    if (msg := _chk_list(c)):
        return msg
    subs = [n for call in c.prints() for a in call.args for n in ast.walk(a)
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == "marks"]
    if not subs:
        return "Print one item using square brackets after the list name, like marks[0]."
    index = subs[0].slice
    if isinstance(index, ast.Constant) and index.value == 1:
        return "Index 1 is the second item. Python counts from 0, so the first item is marks[0]."
    return None


def _chk_iterate(c: Code) -> Optional[str]:
    if (msg := _chk_list(c)):
        return msg
    loops = [f for f in _for_loops(c) if isinstance(f.iter, ast.Name) and f.iter.id == "marks"]
    if not loops:
        return "Loop over the list itself: for mark in marks:."
    var = loops[0].target.id if isinstance(loops[0].target, ast.Name) else ""
    if not any(_uses_name(a, var) for sub in _loop_body_nodes(loops[0]) if isinstance(sub, ast.Call)
               and isinstance(sub.func, ast.Name) and sub.func.id == "print" for a in sub.args):
        return f"Inside the loop, print {var or 'the loop variable'} so each mark is shown."
    return None


def _chk_dict(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    value = c.assigned_value("student")
    if not isinstance(value, ast.Dict):
        if value is not None:
            return "student exists but is not a dictionary. Use curly braces with key: value pairs."
        return "I do not see a dictionary called student yet."
    keys = {k.value for k in value.keys if isinstance(k, ast.Constant)}
    missing = [k for k in ("name", "marks") if k not in keys]
    if missing:
        return f"Your dictionary is missing the key {missing[0]}, in quotes."
    return None


def _chk_dict_read(c: Code) -> Optional[str]:
    if (msg := _chk_dict(c)):
        return msg
    reads = [n for call in c.prints() for a in call.args for n in ast.walk(a)
             if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == "student"]
    gets = [n for n in c.method_calls("get") if isinstance(n.func.value, ast.Name) and n.func.value.id == "student"]
    if reads or gets:
        return None
    if c.print_has_string("name"):
        return "You printed the word name. Read the value from the dictionary: student[\"name\"]."
    return "Print the name by reading it from the dictionary with square brackets."


# ---- functions -----------------------------------------------------------------

def _chk_define_greet(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    fn = c.functions().get("greet")
    if fn is None:
        return "I do not see a function called greet yet. Start with def greet():."
    if not _body_prints(fn.body):
        return "greet exists but does not print anything inside it. Indent a print line under the def."
    return None


def _chk_call_greet(c: Code) -> Optional[str]:
    if (msg := _chk_define_greet(c)):
        return msg
    if c.module_calls("greet"):
        return None
    if c.bare_expression("greet"):
        return "greet without parentheses only names the function. Add parentheses: greet()."
    return "Defining greet does not run it. Call it on a new, unindented line with greet()."


def _chk_param(c: Code) -> Optional[str]:
    if (msg := _chk_call_greet(c)):
        return msg
    fn = c.functions()["greet"]
    params = [a.arg for a in fn.args.args]
    if "name" not in params:
        return "greet does not take a parameter yet. Put name inside the parentheses of the def line."
    if not any(_uses_name(a, "name") for sub in ast.walk(fn) if isinstance(sub, ast.Call)
               and isinstance(sub.func, ast.Name) and sub.func.id == "print" for a in sub.args):
        return "greet takes name but does not print it. Use name inside the print."
    if not any(call.args for call in c.module_calls("greet")):
        return "greet now needs a name, but your call passes nothing. Put a name in quotes inside the call's parentheses."
    return None


def _chk_return(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    fn = c.functions().get("add")
    if fn is None:
        return "I do not see a function called add yet."
    if len(fn.args.args) < 2:
        return "add needs two parameters, a and b, inside the parentheses."
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
    if not returns:
        if _body_prints(fn.body):
            return "add prints the answer instead of returning it. Use return so the caller gets the value."
        return "add does not return anything yet. Use return a + b."
    if not c.module_calls("add"):
        return "Now call add and print the result, like print(add(2, 3))."
    if not any(any(isinstance(s, ast.Call) and isinstance(s.func, ast.Name) and s.func.id == "add"
                   for a in call.args for s in ast.walk(a)) for call in c.prints()):
        return "You call add, but the result is not printed. Put the call inside print."
    return None


def _chk_decompose(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    fns = c.functions()
    if "get_average" not in fns or "show_result" not in fns:
        missing = "get_average" if "get_average" not in fns else "show_result"
        return f"I do not see a function called {missing} yet."
    if not any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(fns["get_average"])):
        return "get_average should return the average, not print it."
    if not _body_prints(fns["show_result"].body):
        return "show_result should print the result."
    if not c.module_calls("show_result") and not c.module_calls("get_average"):
        return "Both functions exist. Call them at the bottom so the program runs."
    return None


# ---- debugging -------------------------------------------------------------------

def _chk_fixed_syntax(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    return None if c.prints() else "The print line disappeared. Keep print and just fix the brackets."


def _chk_fixed_name(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    if c.print_uses_name("score") and c.assigned_value("score") is not None:
        return None
    if c.print_uses_name("scores"):
        return "print still uses scores, but the variable is called score. The names must match exactly."
    return "Make the name in print match the variable that stores the value."


def _chk_fixed_type(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    for n in c.nodes:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            parts = (n.left, n.right)
            if any(_is_string(p) for p in parts) and any(isinstance(p, ast.Name) and p.id == "age" for p in parts):
                return "The code still adds text and the number age with a plus. Use a comma in print, or str(age)."
    if c.print_uses_name("age"):
        return None
    return "Keep printing age, just without adding text and a number together."


def _chk_fixed_condition(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    for cmp in _compare_on(c, "marks"):
        for op, right in zip(cmp.ops, cmp.comparators):
            value = right.value if isinstance(right, ast.Constant) else None
            if isinstance(op, ast.GtE) and value == 40:
                return None
            if isinstance(op, ast.Gt) and value == 39:
                return None
            if isinstance(op, ast.Gt) and value == 40:
                return "The condition still uses greater than 40, so exactly 40 fails. Use greater than or equal to."
    return "Students with exactly 40 should pass. Check the condition's symbol and number."


def _chk_fixed_loop(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    loops = [w for w in c.of_type(ast.While) if _uses_name(w.test, "count")]
    if not loops:
        return "Keep the while loop; just make sure it can stop."
    if not _increments(_loop_body_nodes(loops[0]), "count"):
        return "count still never changes inside the loop. Add count = count + 1, indented inside it."
    return None


def _chk_fixed_variable(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    loops = _for_loops(c)
    if any(_resets_inside(loop, "total") for loop in loops):
        return "total = 0 is still inside the loop, so it resets each time. Keep only the one above the loop."
    if not loops or not _increments(_loop_body_nodes(loops[0]), "total"):
        return "Keep adding each mark to total inside the loop."
    return None


def _chk_fixed_logic(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    for n in c.nodes:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            right = n.right
            if isinstance(right, ast.Call) and isinstance(right.func, ast.Name) and right.func.id == "len":
                return None
            if isinstance(right, ast.Constant) and right.value == 3:
                return None
            if isinstance(right, ast.Constant) and right.value == 2:
                return "It still divides by 2, but there are three marks. Divide by len(marks)."
    return "The average should divide the total by how many marks there are."


# ---- projects -------------------------------------------------------------------------

def _numbers_list(c: Code, name: str, minimum: int = 3) -> Optional[str]:
    value = c.assigned_value(name)
    if not isinstance(value, ast.List) or len(value.elts) < minimum:
        return f"Start with a list called {name} holding at least {minimum} numbers."
    return None


def _chk_proj_marks_list(c: Code) -> Optional[str]:
    return _need(c) or _numbers_list(c, "marks")


def _chk_proj_total(c: Code) -> Optional[str]:
    if (msg := _chk_proj_marks_list(c)):
        return msg
    if c.assigned_value("total") is None:
        return "Create total, the sum of all marks. sum(marks) does it in one step."
    return None


def _chk_proj_average(c: Code) -> Optional[str]:
    if (msg := _chk_proj_total(c)):
        return msg
    value = c.assigned_value("average")
    if value is None:
        return "Create average: total divided by how many marks there are."
    if not (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Div)):
        return "average should divide total by the number of marks, using a single slash."
    return None


def _chk_proj_grade(c: Code) -> Optional[str]:
    if (msg := _chk_proj_average(c)):
        return msg
    chains = [i for i in _ifs_on(c, "average") if i.orelse]
    if not chains:
        return "Decide a grade with if, elif and else based on average."
    values = [v for v in _thresholds(chains[0]) if v is not None]
    if len(values) >= 2 and values[0] < values[1]:
        return "Your first grade condition uses a smaller number than the next one. Check the highest grade first."
    if c.assigned_value("grade") is None:
        return "Store the grade in a variable called grade inside each branch."
    return None


def _chk_proj_report(c: Code) -> Optional[str]:
    if (msg := _chk_proj_grade(c)):
        return msg
    if c.print_uses_name("average") and c.print_uses_name("grade"):
        return None
    return "Finish with print lines that show the average and the grade."


def _chk_calc_numbers(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    converted = [n for n in ("first", "second") if _is_converted_input(c.assigned_value(n))]
    if len(converted) == 2:
        return None
    for n in ("first", "second"):
        if _is_input_call(c.assigned_value(n)):
            return f"{n} comes from input as text. Wrap it in float so you can do maths with it."
    return "Ask for two numbers called first and second, using float(input(...))."


def _chk_calc_operator(c: Code) -> Optional[str]:
    if (msg := _chk_calc_numbers(c)):
        return msg
    if _is_input_call(c.assigned_value("operator")):
        return None
    return "Ask which operation to do and store it in operator, using input. It stays text, which is fine here."


def _chk_calc_branches(c: Code) -> Optional[str]:
    if (msg := _chk_calc_operator(c)):
        return msg
    ifs = _ifs_on(c, "operator")
    ops_seen = {s.value for i in ifs for s in ast.walk(i.test) if isinstance(s, ast.Constant) and isinstance(s.value, str)}
    if len(ops_seen) < 4:
        return f"You handle {len(ops_seen)} operation{'s' if len(ops_seen) != 1 else ''}. Add if and elif branches for plus, minus, times and divide."
    return None


def _chk_calc_zero(c: Code) -> Optional[str]:
    if (msg := _chk_calc_branches(c)):
        return msg
    guards = [cmp for cmp in _compare_on(c, "second") if any(isinstance(r, ast.Constant) and r.value == 0
                                                              for r in cmp.comparators)]
    if guards or c.of_type(ast.Try):
        return None
    return "Dividing by zero crashes the program. Before dividing, check whether second is 0."


def _chk_calc_print(c: Code) -> Optional[str]:
    if (msg := _chk_calc_zero(c)):
        return msg
    return None if c.print_uses_name("result") else "Store each answer in result and print result."


def _chk_quiz_answer(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    if _is_input_call(c.assigned_value("answer")):
        return None
    return "Ask one question with input and store the reply in answer."


def _chk_quiz_compare(c: Code) -> Optional[str]:
    if (msg := _chk_quiz_answer(c)):
        return msg
    ifs = [i for i in _ifs_on(c, "answer") if any(isinstance(op, ast.Eq) for s in ast.walk(i.test)
                                                  if isinstance(s, ast.Compare) for op in s.ops)]
    if not ifs:
        return "Compare answer with the correct answer using two equals signs in an if."
    if not ifs[0].orelse:
        return "Add else so a wrong answer gets its own message."
    return None


def _chk_quiz_score(c: Code) -> Optional[str]:
    if (msg := _chk_quiz_compare(c)):
        return msg
    if c.assigned_value("score") is None:
        return "Create score = 0 at the top."
    if not _increments(c.nodes, "score"):
        return "When the answer is right, add one to score."
    return None


def _chk_quiz_finish(c: Code) -> Optional[str]:
    if (msg := _chk_quiz_score(c)):
        return msg
    if len(c.calls("input")) < 2:
        return "Add a second question with its own input and check."
    return None if c.print_uses_name("score") else "Finish by printing the score."


def _chk_pw_input(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    return None if _is_input_call(c.assigned_value("password")) else "Ask for a password with input and store it in password."


def _chk_pw_length(c: Code) -> Optional[str]:
    if (msg := _chk_pw_input(c)):
        return msg
    if any(isinstance(s, ast.Call) and isinstance(s.func, ast.Name) and s.func.id == "len" and s.args
           and _uses_name(s.args[0], "password") for cmp in c.of_type(ast.Compare) for s in ast.walk(cmp)):
        return None
    return "Check the length with len(password) >= 8."


def _chk_pw_digit(c: Code) -> Optional[str]:
    if (msg := _chk_pw_length(c)):
        return msg
    if c.method_calls("isdigit"):
        return None
    return "Check whether the password has a digit. A loop with character.isdigit() works."


def _chk_pw_verdict(c: Code) -> Optional[str]:
    if (msg := _chk_pw_digit(c)):
        return msg
    if any(i.orelse for i in c.of_type(ast.If)) and c.print_has_string():
        return None
    return "Finish with if and else that print strong or weak."


def _chk_records_list(c: Code) -> Optional[str]:
    if (msg := _need(c)):
        return msg
    value = c.assigned_value("students")
    if isinstance(value, ast.List) and len(value.elts) >= 2 and all(isinstance(e, ast.Dict) for e in value.elts):
        return None
    return "Create students: a list of at least two dictionaries, each with a name and marks."


def _chk_records_loop(c: Code) -> Optional[str]:
    if (msg := _chk_records_list(c)):
        return msg
    loops = [f for f in _for_loops(c) if isinstance(f.iter, ast.Name) and f.iter.id == "students"]
    if not loops:
        return "Loop through students with for student in students:."
    if not _body_prints(loops[0].body):
        return "Inside the loop, print each student's name."
    return None


def _chk_records_function(c: Code) -> Optional[str]:
    if (msg := _chk_records_loop(c)):
        return msg
    fns = [f for f in c.functions().values() if any(isinstance(n, ast.Return) and n.value is not None
                                                     for n in ast.walk(f))]
    if not fns:
        return "Write a function that takes a student and returns whether they passed, using return."
    return None


def _chk_records_report(c: Code) -> Optional[str]:
    if (msg := _chk_records_function(c)):
        return msg
    names = set(c.functions())
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names
           for loop in _for_loops(c) for n in _loop_body_nodes(loop)):
        return None
    return "Call your function inside the loop and print each student's result."


# ---------------------------------------------------------------------------
# Path definitions
# ---------------------------------------------------------------------------

def _cp(id, goal, concept, check, hints, example, explain, **kw) -> Checkpoint:
    return Checkpoint(id=id, goal=goal, concept=concept, check=check, hints=tuple(hints), example=example,
                      explain=explain, **kw)


PATHS: Tuple[Path, ...] = (
    Path("first_programs", "First programs", ("first programs", "first program", "basics", "beginning", "output",
                                              "print", "variables", "input", "shuruaat"), (
        _cp("fp_print", "Make your program say Hello. Write one print line that shows the text Hello.",
            "print output", _chk_print_hello,
            ["Python's function for showing something is print.",
             "Write print, then parentheses, and put your text in quotes inside them."],
            'print("Hello")', "print shows whatever is inside its parentheses. Text goes inside quotes.",
            note="print makes the program speak or show text.", trivial=True),
        _cp("fp_variable", "Create a variable called marks and give it a number.", "variables", _chk_marks_variable,
            ["A variable is made from a name, one equals sign, and a value.",
             "Write marks, then one equals sign, then a number like 80."],
            "marks = 80", "A variable is a labelled box. marks = 80 puts the number 80 in a box called marks.",
            note="A variable stores a value under a name.", trivial=True),
        _cp("fp_print_var", "Now print marks.", "print output", _chk_print_marks,
            ["Think about the Python function used to display something.",
             "Use print with marks inside its parentheses."],
            "print(marks)", "To show what is inside the marks box, give the name marks to print, without quotes.",
            small_steps=(("First, just write the word print followed by empty parentheses on a new line.",
                          lambda c: None if c.prints() else "I do not see print yet."),),
            question=Question(_q_marks_value, "print(marks) shows the value stored in marks, not the word marks.")),
        _cp("fp_input", "Ask the user for their name with input, and store the answer in a variable called name.",
            "input", _chk_input_name,
            ["input shows a question and waits for the user to type.",
             'Write name = input("What is your name? ")'],
            'name = input("What is your name? ")',
            "input pauses the program, lets the person type, and gives back what they typed.",
            note="input lets the person running the program type a value."),
        _cp("fp_greeting", "Now print a greeting that uses name, like Hello followed by the name.", "print output",
            _chk_greeting,
            ["print can show more than one thing if you separate them with commas.",
             'Write print("Hello", name)'],
            'print("Hello", name)', "The comma lets print show the text Hello and then the value in name."),
    )),
    Path("conditions", "Conditions", ("conditions", "condition", "if", "if else", "elif", "grades", "decisions",
                                      "comparison", "comparisons"), (
        _cp("cond_compare", "Create marks with a number, then print whether marks is at least 40 using a comparison.",
            "conditionals (if/else)", _chk_compare,
            ["A comparison asks a true or false question about two values.",
             "Write print(marks >= 40) below your marks line."],
            "marks = 55\nprint(marks >= 40)", "marks >= 40 is a question. Python answers True or False.",
            note="Comparisons give True or False."),
        _cp("cond_if", "Write an if statement: if marks is at least 40, print pass.", "conditionals (if/else)", _chk_if,
            ["An if line ends with a colon, and the lines inside it are indented.",
             'Write if marks >= 40: and on the next line, indented four spaces, print("pass").'],
            'if marks >= 40:\n    print("pass")',
            "if runs the indented lines only when its question is True.",
            small_steps=(("First write only the if line: if marks >= 40, ending with a colon.",
                          lambda c: None if _ifs_on(c, "marks") or (c.error and "expected an indented block" in str(c.error.msg))
                          else (syntax_feedback(c) or "I do not see an if line that checks marks yet.")),),),
        _cp("cond_else", "Add an else that prints fail when marks is below 40.", "conditionals (if/else)", _chk_else,
            ["else lines up with if, and ends with a colon.",
             'Under your if block, write else: then an indented print("fail").'],
            'if marks >= 40:\n    print("pass")\nelse:\n    print("fail")',
            "else is the other path: it runs when the if question is False."),
        _cp("cond_elif", "Use if, elif and else to print A for 80 or more, B for 60 or more, and C otherwise.",
            "conditionals (if/else)", _chk_elif,
            ["elif adds another question that is only asked when the one above was False.",
             "Check 80 first with if, then 60 with elif, then use else for C."],
            'if marks >= 80:\n    print("A")\nelif marks >= 60:\n    print("B")\nelse:\n    print("C")',
            "Python asks the questions from top to bottom and stops at the first True one."),
        _cp("cond_input", "Now let the user type the marks: marks = int(input(...)). Keep your grade checks.",
            "input", _chk_input_marks,
            ["input gives text. Numbers need converting before you compare them.",
             'Write marks = int(input("Marks: "))'],
            'marks = int(input("Marks: "))', "int turns the typed text into a whole number."),
    )),
    Path("loops", "Loops", ("loops", "loop", "for loop", "for loops", "while", "while loop", "repetition",
                            "counter", "accumulator", "average"), (
        _cp("loop_for", "Write a for loop that prints the numbers 0 to 4 using range.", "loops", _chk_for_range,
            ["range(5) gives the numbers 0, 1, 2, 3 and 4.",
             "Write for number in range(5): and then an indented print(number)."],
            "for number in range(5):\n    print(number)",
            "A for loop repeats its indented lines once for each number that range gives.",
            note="A loop repeats the indented lines under it.",
            small_steps=(("First write just the loop line: for number in range(5), ending with a colon.",
                          lambda c: None if _for_loops(c) or (c.error and "expected an indented block" in str(c.error.msg))
                          else (syntax_feedback(c) or "I do not see a for line yet.")),)),
        _cp("loop_while", "Write a while loop that prints count from 1 to 3. Start with count = 1.", "loops", _chk_while,
            ["A while loop needs something inside it that changes, or it never stops.",
             "Inside the loop, print(count) and then count = count + 1."],
            "count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1",
            "while repeats as long as its question stays True, so the loop must change count to finish."),
        _cp("loop_counter", "Count how many numbers from 1 to 10 are even. Use a variable called count that starts at 0.",
            "loops", _chk_counter,
            ["A counter starts at zero before the loop and goes up by one inside it.",
             "Use for number in range(1, 11):, then if number % 2 == 0: count = count + 1."],
            "count = 0\nfor number in range(1, 11):\n    if number % 2 == 0:\n        count = count + 1\nprint(count)",
            "The counter remembers how many times something happened while the loop runs."),
        _cp("loop_total", "Add up the marks 70, 80 and 90 with a for loop, into a variable called total that starts at 0.",
            "loops", _chk_accumulator,
            ["An accumulator starts at zero and grows a little each time round the loop.",
             "Write total = 0, then for mark in [70, 80, 90]: with total = total + mark inside."],
            "total = 0\nfor mark in [70, 80, 90]:\n    total = total + mark\nprint(total)",
            "Each time round, the loop adds one mark on top of what total already holds."),
        _cp("loop_average", "Now calculate and print the average: total divided by how many marks there are.", "loops",
            _chk_average,
            ["The average is found once, after all the adding is finished.",
             "After the loop, not inside it, write average = total / 3 and print(average)."],
            "average = total / 3\nprint(average)", "Divide the finished total by the count of marks."),
    )),
    Path("collections", "Lists and dictionaries", ("collections", "lists", "list", "dictionaries", "dictionary",
                                                   "dict", "records", "student record"), (
        _cp("col_list", "Create a list called marks with three numbers.", "lists", _chk_list,
            ["A list keeps several values in order, inside square brackets.",
             "Write marks = [70, 80, 90]"],
            "marks = [70, 80, 90]", "A list is one name holding many values in order.", trivial=True),
        _cp("col_index", "Print the first item of marks.", "lists", _chk_index,
            ["Python counts list positions from zero.", "Write print(marks[0])"],
            "print(marks[0])", "marks[0] means: go to position zero, the first item."),
        _cp("col_loop", "Use a for loop to print each mark in marks.", "lists", _chk_iterate,
            ["You can loop over a list directly, no range needed.",
             "Write for mark in marks: then an indented print(mark)."],
            "for mark in marks:\n    print(mark)", "The loop hands you each item of the list in turn."),
        _cp("col_dict", "Create a dictionary called student with the keys name and marks.", "dictionaries", _chk_dict,
            ["A dictionary stores values under labels called keys, inside curly braces.",
             'Write student = {"name": "Asha", "marks": 90}'],
            'student = {"name": "Asha", "marks": 90}',
            "A dictionary is like a form: each label, the key, has a value next to it."),
        _cp("col_dict_read", "Print the student's name by reading it from the dictionary.", "dictionaries",
            _chk_dict_read,
            ["You read a value by putting its key in square brackets after the dictionary name.",
             'Write print(student["name"])'],
            'print(student["name"])', 'student["name"] looks up the value stored under the key name.'),
    )),
    Path("functions", "Functions", ("functions", "function", "def", "parameters", "return", "decomposition"), (
        _cp("fn_define", "Define a function called greet that prints Hello.", "functions", _chk_define_greet,
            ["A function starts with def, its name, parentheses and a colon.",
             'Write def greet(): and then an indented print("Hello").'],
            'def greet():\n    print("Hello")', "def creates a named set of steps. It does not run them yet.",
            note="A function is a named set of steps you can run later."),
        _cp("fn_call", "Now call greet so it actually runs.", "functions", _chk_call_greet,
            ["Defining a function only stores it. Something has to call it.",
             "On a new line with no indentation, write greet()."],
            "greet()", "Calling a function means writing its name with parentheses; then its steps run."),
        _cp("fn_param", "Change greet to take a parameter called name and print Hello with the name. Call it with a name.",
            "functions", _chk_param,
            ["A parameter is a name inside the def's parentheses that receives a value.",
             'Write def greet(name): print("Hello", name), then call greet("Asha").'],
            'def greet(name):\n    print("Hello", name)\n\ngreet("Asha")',
            "The value you put in the call's parentheses goes into the parameter name."),
        _cp("fn_return", "Write a function called add that returns a plus b, then print add(2, 3).", "functions",
            _chk_return,
            ["return sends a value back to whoever called the function.",
             "Write def add(a, b): with return a + b inside, then print(add(2, 3))."],
            "def add(a, b):\n    return a + b\n\nprint(add(2, 3))",
            "print shows a value; return hands it back so other code can use it."),
        _cp("fn_decompose", "Split a marks program into get_average, which returns the average of a list, and show_result, which prints it.",
            "functions", _chk_decompose,
            ["Give each function one job: one calculates, one speaks.",
             "get_average(marks) returns sum(marks) / len(marks); show_result(average) prints it."],
            "def get_average(marks):\n    return sum(marks) / len(marks)\n\n"
            "def show_result(average):\n    print(\"Average:\", average)\n\nshow_result(get_average([70, 80, 90]))",
            "Small functions with one job each are easier to test and to fix."),
    )),
    Path("debugging", "Debugging", ("debugging", "debug", "errors", "fixing errors", "mistakes", "bugs"), (
        _cp("dbg_syntax", "This program has a SyntaxError. Find it and fix it.", "debugging", _chk_fixed_syntax,
            ["Read the error line. Count the opening and closing brackets.",
             "The print line opens a parenthesis but never closes it."],
            'print("Hello")', "A SyntaxError means Python cannot read the shape of the code.",
            starter='print("Hello"\n'),
        _cp("dbg_name", "This program crashes with a NameError. Fix the name.", "debugging", _chk_fixed_name,
            ["NameError means a name is used that was never created.",
             "Compare the name you stored with the name you print, letter by letter."],
            "score = 70\nprint(score)", "Python only knows names exactly as they were created.",
            starter="score = 70\nprint(scores)\n"),
        _cp("dbg_type", "This program crashes with a TypeError. Fix it.", "debugging", _chk_fixed_type,
            ["TypeError here means text and a number are being joined with plus.",
             'Use a comma instead: print("Age:", age).'],
            'age = 12\nprint("Age:", age)', "Plus cannot join text and a number; a comma in print can show both.",
            starter='age = 12\nprint("Age: " + age)\n'),
        _cp("dbg_condition", "Students pass with 40 or more, but this program says 40 is a fail. Fix the condition.",
            "debugging", _chk_fixed_condition,
            ["Think about what happens when marks is exactly 40.",
             "Greater than leaves out 40 itself. Greater than or equal to includes it."],
            'marks = 40\nif marks >= 40:\n    print("pass")\nelse:\n    print("fail")',
            "A bad condition runs, but asks the wrong question.",
            starter='marks = 40\nif marks > 40:\n    print("pass")\nelse:\n    print("fail")\n'),
        _cp("dbg_loop", "This loop never stops. Do not run it yet. Fix it so it prints 1, 2 and 3.", "debugging",
            _chk_fixed_loop,
            ["A while loop stops only when its question becomes False.",
             "Nothing changes count inside the loop. Add count = count + 1 inside it."],
            "count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1",
            "An endless loop happens when the loop never changes what it checks.",
            starter="count = 1\nwhile count <= 3:\n    print(count)\n"),
        _cp("dbg_variable", "This should print 240 but prints 90. Fix the variable mistake.", "debugging",
            _chk_fixed_variable,
            ["Look for a line inside the loop that throws the total away.",
             "total = 0 inside the loop resets it every time. Remove that line from the loop."],
            "total = 0\nfor mark in [70, 80, 90]:\n    total = total + mark\nprint(total)",
            "A variable reset inside a loop forgets everything from earlier rounds.",
            starter="total = 0\nfor mark in [70, 80, 90]:\n    total = 0\n    total = total + mark\nprint(total)\n"),
        _cp("dbg_logic", "The average printed here is wrong. Fix the logic mistake.", "debugging", _chk_fixed_logic,
            ["How many marks are in the list? What does the code divide by?",
             "Divide by len(marks), which is 3, instead of 2."],
            "marks = [70, 80, 90]\naverage = sum(marks) / len(marks)\nprint(average)",
            "A logic mistake gives a wrong answer without any error message.",
            starter="marks = [70, 80, 90]\naverage = sum(marks) / 2\nprint(average)\n"),
    )),
    Path("project_marks", "Project: school marks program", ("school marks", "marks program", "marks project",
                                                            "school marks program", "report card"), (
        _cp("pm_list", "Start the school marks program: create a list called marks with at least three marks.", "lists",
            _chk_proj_marks_list, ["Use square brackets and commas.", "marks = [72, 85, 90]"],
            "marks = [72, 85, 90]", "The list holds every mark the program will use.", trivial=True),
        _cp("pm_total", "Create total, the sum of all the marks.", "loops", _chk_proj_total,
            ["Python has a built-in function that adds up a list.", "total = sum(marks)"],
            "total = sum(marks)", "sum adds every number in the list."),
        _cp("pm_average", "Create average: total divided by the number of marks.", "loops", _chk_proj_average,
            ["len(marks) tells you how many marks there are.", "average = total / len(marks)"],
            "average = total / len(marks)", "The average spreads the total evenly across the marks."),
        _cp("pm_grade", "Store a grade in a variable called grade: A for 80 or more, B for 60 or more, otherwise C.",
            "conditionals (if/else)", _chk_proj_grade,
            ["Check the highest grade first.",
             'if average >= 80: grade = "A", elif average >= 60: grade = "B", else: grade = "C".'],
            'if average >= 80:\n    grade = "A"\nelif average >= 60:\n    grade = "B"\nelse:\n    grade = "C"',
            "Each branch stores a different grade; only one branch runs."),
        _cp("pm_report", "Finish the report: print the average and the grade.", "print output", _chk_proj_report,
            ["print can show text and a variable separated by a comma.",
             'print("Average:", average) and print("Grade:", grade)'],
            'print("Average:", average)\nprint("Grade:", grade)', "The report speaks the results."),
    )),
    Path("project_calculator", "Project: calculator", ("calculator", "calculator project", "calc"), (
        _cp("pc_numbers", "Ask for two numbers called first and second, converted with float.", "input",
            _chk_calc_numbers, ["float turns typed text into a number with decimals.",
                                'first = float(input("First number: ")) and the same for second.'],
            'first = float(input("First number: "))\nsecond = float(input("Second number: "))',
            "Numbers typed by the user arrive as text, so convert them first."),
        _cp("pc_operator", "Ask which operation to do, and store it in operator.", "input", _chk_calc_operator,
            ["The operation can stay as text, like a plus sign.", 'operator = input("Choose + - * or /: ")'],
            'operator = input("Choose + - * or /: ")', "operator holds the symbol the user typed."),
        _cp("pc_branches", "Use if and elif to calculate result for plus, minus, times and divide.",
            "conditionals (if/else)", _chk_calc_branches,
            ["Compare operator with each symbol in quotes.",
             'if operator == "+": result = first + second, then elif for the other three.'],
            'if operator == "+":\n    result = first + second\nelif operator == "-":\n    result = first - second\n'
            'elif operator == "*":\n    result = first * second\nelif operator == "/":\n    result = first / second',
            "Each branch handles one symbol."),
        _cp("pc_zero", "Make division safe: do not divide when second is 0.", "debugging", _chk_calc_zero,
            ["Dividing by zero crashes Python.", "Inside the divide branch, check if second == 0 first."],
            'elif operator == "/":\n    if second == 0:\n        result = "Cannot divide by zero"\n    else:\n        result = first / second',
            "Checking before dividing stops the crash."),
        _cp("pc_print", "Print the result.", "print output", _chk_calc_print,
            ["The answer is stored in result.", 'print("Result:", result)'],
            'print("Result:", result)', "The last step speaks the answer."),
    )),
    Path("project_quiz", "Project: quiz", ("quiz", "quiz program", "quiz project", "quiz game"), (
        _cp("pq_answer", "Ask one quiz question with input and store the reply in answer.", "input", _chk_quiz_answer,
            ["input can show the question itself.", 'answer = input("What is 2 + 2? ")'],
            'answer = input("What is 2 + 2? ")', "The learner's reply is saved in answer."),
        _cp("pq_check", "Use if and else to say correct when answer equals the right answer, otherwise wrong.",
            "conditionals (if/else)", _chk_quiz_compare,
            ["input gives text, so compare with text in quotes.", 'if answer == "4": print("Correct") else: print("Wrong")'],
            'if answer == "4":\n    print("Correct")\nelse:\n    print("Wrong")', "Two equals signs compare the answer."),
        _cp("pq_score", "Keep score: start score at 0 and add one for a correct answer.", "loops", _chk_quiz_score,
            ["score = 0 goes at the very top.", "Inside the correct branch, write score = score + 1."],
            "score = 0\n...\nif answer == \"4\":\n    score = score + 1", "The score counts correct answers."),
        _cp("pq_finish", "Add a second question, then print the final score.", "print output", _chk_quiz_finish,
            ["Repeat the input and if for a new question.", 'print("Your score:", score)'],
            'print("Your score:", score)', "The end of the quiz speaks the score."),
    )),
    Path("project_password", "Project: password checker", ("password", "password checker", "password project"), (
        _cp("pp_input", "Ask for a password with input and store it in password.", "input", _chk_pw_input,
            ["input asks and waits.", 'password = input("Password: ")'],
            'password = input("Password: ")', "password now holds what was typed."),
        _cp("pp_length", "Check whether the password has at least 8 characters.", "conditionals (if/else)",
            _chk_pw_length, ["len gives how many characters text has.", "if len(password) >= 8:"],
            "if len(password) >= 8:\n    print(\"Long enough\")", "len counts the characters."),
        _cp("pp_digit", "Check whether the password contains a digit.", "loops", _chk_pw_digit,
            ["Look at each character in a loop.",
             "has_digit = False, then for character in password: if character.isdigit(): has_digit = True"],
            "has_digit = False\nfor character in password:\n    if character.isdigit():\n        has_digit = True",
            "isdigit asks whether one character is a number."),
        _cp("pp_verdict", "Print strong when both checks pass, otherwise weak.", "conditionals (if/else)",
            _chk_pw_verdict, ["Combine the two checks with and.",
                              'if len(password) >= 8 and has_digit: print("strong") else: print("weak")'],
            'if len(password) >= 8 and has_digit:\n    print("strong")\nelse:\n    print("weak")',
            "and needs both questions to be True."),
    )),
    Path("project_records", "Project: student record system", ("student records", "student record system",
                                                               "records project", "record system"), (
        _cp("pr_list", "Create students: a list of at least two dictionaries, each with a name and marks.",
            "dictionaries", _chk_records_list,
            ["Each student is one dictionary; the list holds them all.",
             'students = [{"name": "Asha", "marks": 90}, {"name": "Ravi", "marks": 35}]'],
            'students = [{"name": "Asha", "marks": 90}, {"name": "Ravi", "marks": 35}]',
            "A list of dictionaries is a small table: one dictionary per row."),
        _cp("pr_loop", "Loop through students and print each name.", "lists", _chk_records_loop,
            ["for student in students: gives one dictionary at a time.", 'print(student["name"]) inside the loop'],
            'for student in students:\n    print(student["name"])', "The loop visits every record."),
        _cp("pr_function", "Write a function called passed that takes a student and returns True when marks are 40 or more.",
            "functions", _chk_records_function,
            ["The function receives one dictionary.", 'def passed(student): return student["marks"] >= 40'],
            'def passed(student):\n    return student["marks"] >= 40', "The function answers one question about one student."),
        _cp("pr_report", "Inside the loop, call passed and print pass or fail for each student.", "functions",
            _chk_records_report,
            ["Call your function inside the loop.",
             'if passed(student): print(student["name"], "pass") else: print(student["name"], "fail")'],
            'for student in students:\n    if passed(student):\n        print(student["name"], "pass")\n'
            '    else:\n        print(student["name"], "fail")', "Now every record gets a result."),
    )),
)

PATHS_BY_ID: Dict[str, Path] = {p.id: p for p in PATHS}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

_START_RE = re.compile(
    r"^(?:please\s+)?(?:start|begin|open|resume|continue|do)\s+(?:the\s+)?guided\s+(?:learning|practice|lessons?|mode|path)"
    r"(?:\s+(?:for|on|with|about))?(?:\s+(?P<topic>[a-z ]+?))?$|"
    r"^(?:please\s+)?guided\s+(?P<topic2>(?!learning$|practice$)[a-z ]+?)$|"
    r"^(?:please\s+)?(?:guide\s+me(?:\s+through)?|teach\s+me)\s+(?P<topic4>[a-z ]+?)\s+step\s+by\s+step$|"
    r"^(?:please\s+)?(?:start|begin)\s+(?:the\s+)?guided\s+project\s+(?P<topic3>[a-z ]+)$|"
    r"^(?:please\s+)?(?:start|begin|open|resume)\s+(?:the\s+)?guided\s+(?P<topic5>(?!learning\b|practice\b|lessons?\b|mode\b|path\b|project\b)[a-z ]+?)$|"
    r"^(?:please\s+)?(?:teach|guide)\s+me\s+step\s+by\s+step$|^guided\s+(?:learning|practice)$",
    re.IGNORECASE,
)
_LIST_RE = re.compile(r"^(?:list|show|what are(?: the)?)\s+(?:the\s+)?guided\s+(?:paths|learning(?: paths)?|topics|lessons)$",
                      re.IGNORECASE)

_ACTIVE_COMMANDS = [
    ("stop", r"(?:stop|exit|quit|end|leave|close|pause)\s+(?:the\s+)?guided(?:\s+(?:learning|practice|mode|lesson))?"),
    ("check", r"(?:check(?:\s+(?:my|the|this))?(?:\s+(?:work|code|answer|attempt))?|is\s+(?:this|it|my\s+code)\s+(?:right|correct|ok(?:ay)?)|"
              r"(?:i(?:'m|\s+am)\s+)?done|i\s+(?:have\s+)?finished|mera\s+(?:code\s+)?check\s+karo|check\s+karo|dekho)"),
    ("more_hint", r"(?:another|next|more|stronger|bigger|one\s+more|ek\s+aur)\s+(?:hint|clue)|(?:give\s+me\s+)?(?:a\s+)?(?:stronger|bigger)\s+hint|hint\s+again|aur\s+hint"),
    ("hint", r"(?:(?:give|get)\s+(?:me\s+)?)?(?:a\s+)?(?:small\s+)?(?:hint|clue|nudge)(?:\s+please)?|help\s+me|i(?:'m|\s+am)\s+stuck|hint\s+do|koi\s+hint"),
    ("example", r"(?:show|give)\s+(?:me\s+)?(?:an?\s+|the\s+)?(?:example|answer|solution)|example\s+(?:please|do|dikhao)|(?:just\s+)?tell\s+me\s+the\s+answer"),
    ("why", r"why\s+is\s+(?:this|it|my\s+code)\s+wrong|what(?:'s|\s+is)\s+wrong(?:\s+with\s+(?:this|it|my\s+code))?|why\s+(?:did\s+it\s+)?(?:fail|wrong)|galat\s+kyun(?:\s+hai)?|kya\s+galat\s+hai"),
    ("explain", r"i\s+(?:do\s+not|don'?t)\s+(?:understand|get\s+it)|(?:samajh|samjh)\s+nahi(?:\s+aaya|\s+aya)?|explain\s+(?:the\s+)?(?:task|again|this\s+step)|what\s+does\s+(?:that|this)\s+mean|confused"),
    ("repeat", r"(?:repeat|say)\s+(?:the\s+)?(?:task|goal|step|instructions?)(?:\s+again)?|what\s+(?:was|is)\s+(?:the|my)\s+(?:task|goal)|repeat\s+that|task\s+(?:phir|fir)\s+se\s+batao"),
    ("next", r"what\s+should\s+i\s+do\s+(?:next|now)|(?:what(?:'s|\s+is)\s+)?next(?:\s+(?:task|goal|one|challenge))?|continue|go\s+on|move\s+on|aage\s+(?:badho|chalo)|agla(?:\s+(?:task|kaam))?"),
    ("skip", r"skip(?:\s+(?:this|it|this\s+step|the\s+step|task))?|chhodo\s+ye|next\s+without\s+checking"),
    ("progress", r"what\s+have\s+i\s+(?:completed|finished|done)|(?:show\s+)?my\s+(?:guided\s+)?progress|how\s+far\s+(?:am\s+i|have\s+i\s+got)"),
    ("load", r"load\s+(?:the\s+)?(?:code|starter(?:\s+code)?|broken\s+(?:code|example)|example\s+code)"),
]
_ACTIVE_RES = [(kind, re.compile(r"^(?:please\s+|ok(?:ay)?\s+|so\s+|now\s+|bhai\s+)?(?:" + pattern + r")(?:\s+please)?$",
                                 re.IGNORECASE)) for kind, pattern in _ACTIVE_COMMANDS]


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().replace("’", "'").strip().rstrip(".!?").split())


def find_path(topic: str) -> Optional[Path]:
    topic = _norm(topic)
    if not topic:
        return None
    topic = re.sub(r"^(?:the|a|an)\s+", "", topic)
    topic = re.sub(r"\s+(?:project|program|path|please|step by step)$", "", topic).strip()
    for path in PATHS:
        if topic == path.id.replace("_", " ") or topic == path.title.lower():
            return path
    for path in PATHS:
        if topic in path.aliases:
            return path
    for path in PATHS:
        if any(alias in topic for alias in path.aliases if len(alias) > 3):
            return path
    return None


def command_kind(text: str, *, active: bool) -> Optional[Tuple[str, str]]:
    """Returns (kind, topic) for a guided-learning command, else None."""
    t = _norm(text)
    if not t:
        return None
    if _LIST_RE.match(t):
        return "list", ""
    m = _START_RE.match(t)
    if m:
        topic = (m.group("topic") or m.group("topic2") or m.group("topic3") or m.group("topic4")
                 or m.group("topic5") or "").strip()
        if m.group("topic5") and not find_path(topic):
            # "start guided loops" names a path; "open guided tour" is not ours.
            return None
        if topic and not find_path(topic) and not t.startswith(("start", "begin", "open", "resume", "continue", "do")):
            # "teach me recursion" is a concept question, not a guided path.
            return None
        return "start", topic
    if not active:
        return None
    for kind, pattern in _ACTIVE_RES:
        if pattern.match(t):
            return kind, ""
    return None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _state(mem: Dict[str, Any]) -> Dict[str, Any]:
    state = mem.get("guided")
    if not isinstance(state, dict):
        state = {"active": False, "path": "", "index": 0, "hint_level": 0, "attempts": 0,
                 "completed": {}, "sub_step": -1, "question": None, "hints_used": 0}
        mem["guided"] = state
    state.setdefault("completed", {})
    return state


def is_active(mem: Dict[str, Any]) -> bool:
    state = mem.get("guided")
    return isinstance(state, dict) and bool(state.get("active")) and state.get("path") in PATHS_BY_ID


def _current(state: Dict[str, Any]) -> Optional[Checkpoint]:
    path = PATHS_BY_ID.get(state.get("path") or "")
    if path is None:
        return None
    idx = int(state.get("index") or 0)
    return path.checkpoints[idx] if 0 <= idx < len(path.checkpoints) else None


def _example_speech(lead: str, code: str, tail: str) -> str:
    # "speech" is what CodeUp Voice says aloud: symbols as words, so print
    # and print() never sound the same. "message" keeps the raw code, which is
    # what screen-reader users receive (their reader handles punctuation).
    return f"{lead} {python_code_to_speech(code)}. {tail}"


def _msg(text: str, state: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    path = PATHS_BY_ID.get(state.get("path") or "")
    info = {"active": bool(state.get("active")), "path": state.get("path"),
            "checkpoint": (_current(state).id if _current(state) else None),
            "index": int(state.get("index") or 0),
            "total": len(path.checkpoints) if path else 0,
            "hint_level": int(state.get("hint_level") or 0)}
    payload = {"success": True, "action": "deterministic_message", "message": text, "speech": text,
               "intent": "guided_learning", "guided": info}
    payload.update(extra)
    return payload


def _goal_text(mem: Dict[str, Any], cp: Checkpoint, state: Dict[str, Any], *, prefix: str = "") -> str:
    level = learner_model.scaffolding(mem, cp.concept)
    status = learner_model.concept_state(mem, cp.concept)
    sub = int(state.get("sub_step", -1))
    if sub >= 0 and sub < len(cp.small_steps) and _allows("hint"):
        goal = f"Let's do this in smaller steps. {cp.small_steps[sub][0]}"
    else:
        goal = cp.goal
    parts = [prefix] if prefix else []
    if level == "more" and cp.note:
        parts.append(f"Reminder: {cp.note}")
    elif status in {"not_introduced", "introduced"} and cp.note and level != "less":
        parts.append(cp.note)
    parts.append(goal)
    if level == "more" and _allows("hint"):
        parts.append("Take your time. Say give me a hint whenever you want a small clue.")
    elif level != "less":
        parts.append("When you are ready, say check my work.")
    return " ".join(p for p in parts if p)


def _enter_checkpoint(mem: Dict[str, Any], state: Dict[str, Any], code: str, *, prefix: str = "",
                      mid_path: bool = False) -> Dict[str, Any]:
    path = PATHS_BY_ID[state["path"]]
    notes: List[str] = [prefix] if prefix else []
    while True:
        cp = _current(state)
        if cp is None:
            state["active"] = False
            done = f"You finished {path.title}. Say what have I completed to hear your progress, or start guided learning to pick another path."
            learner_model.record_evidence(mem, path.checkpoints[-1].concept, "practiced")
            return _msg(" ".join(notes + [done]), state, guided_complete=True)
        state["hint_level"] = 0
        state["attempts"] = 0
        state["question"] = None
        state["sub_step"] = -1
        # Demonstrated learners skip trivial steps their existing code already satisfies.
        if cp.trivial and learner_model.scaffolding(mem, cp.concept) == "less" and cp.check(Code(code)) is None:
            _mark_done(state, cp)
            notes.append(f"You have already shown you can do this, so I skipped the basic step: {cp.goal.rstrip('.')}.")
            state["index"] = int(state["index"]) + 1
            continue
        if cp.small_steps and learner_model.scaffolding(mem, cp.concept) == "more" and _allows("hint"):
            state["sub_step"] = 0
        break
    text = _goal_text(mem, cp, state, prefix=" ".join(n for n in notes if n))
    if cp.starter:
        # Mid-path, the previous exercise is finished, so the next broken
        # program replaces it. At the start of a path the learner's own code
        # is never replaced without asking.
        if not str(code or "").strip() or mid_path:
            return _load_starter(cp, state, text)
        text += " Say load the code to put the broken example in the editor. That replaces what is there now."
    return _msg(text, state)


def _load_starter(cp: Checkpoint, state: Dict[str, Any], text: str) -> Dict[str, Any]:
    payload = _msg(f"{text} I put the program in the editor.", state)
    payload.update({
        "action": "conversational_edit",
        "ai_action": {"action": "replace_code", "target": {"line_number": None, "position": ""},
                      "code": cp.starter, "spoken_confirmation": payload["speech"], "confidence": 1.0,
                      "requires_confirmation": False, "source": "guided_learning"},
    })
    return payload


def _mark_done(state: Dict[str, Any], cp: Checkpoint) -> None:
    done = state["completed"].setdefault(state["path"], [])
    if cp.id not in done:
        done.append(cp.id)


def _advance(mem: Dict[str, Any], state: Dict[str, Any], code: str, prefix: str) -> Dict[str, Any]:
    state["index"] = int(state.get("index") or 0) + 1
    return _enter_checkpoint(mem, state, code, prefix=prefix, mid_path=True)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def list_paths_text(mem: Dict[str, Any]) -> str:
    state = _state(mem)
    parts = []
    for path in PATHS:
        done = len(state["completed"].get(path.id, []))
        parts.append(f"{path.title}, {done} of {len(path.checkpoints)} done")
    return ("Guided learning paths: " + "; ".join(parts) +
            ". Say start guided learning and a path name, for example start guided learning loops.")


def recommended_path(mem: Dict[str, Any]) -> Path:
    state = _state(mem)
    for path in PATHS:
        concepts = {cp.concept for cp in path.checkpoints}
        if any(learner_model.concept_state(mem, c) == "needs_reinforcement" for c in concepts) and \
                len(state["completed"].get(path.id, [])) < len(path.checkpoints):
            return path
    for path in PATHS:
        if len(state["completed"].get(path.id, [])) < len(path.checkpoints):
            return path
    return PATHS[0]


def start(mem: Dict[str, Any], code: str, topic: str = "") -> Dict[str, Any]:
    state = _state(mem)
    path = find_path(topic) if topic else None
    if topic and path is None:
        return _msg(f"I do not have a guided path for {topic} yet. " + list_paths_text(mem), state)
    if path is None:
        if state.get("path") in PATHS_BY_ID and _current(state) is not None:
            path = PATHS_BY_ID[state["path"]]
        else:
            path = recommended_path(mem)
    resume = state.get("path") == path.id and _current(state) is not None
    state.update({"active": True, "path": path.id})
    if not resume:
        done = state["completed"].get(path.id, [])
        state["index"] = next((i for i, cp in enumerate(path.checkpoints) if cp.id not in done), 0)
    intro = (f"Resuming {path.title}, step {int(state['index']) + 1} of {len(path.checkpoints)}."
             if resume else f"Guided learning: {path.title}. {len(path.checkpoints)} small steps.")
    if str(code or "").strip() and not resume and not path.checkpoints[int(state["index"])].starter:
        intro += " I will not change your current code; clear the editor first if you want a fresh start."
    return _enter_checkpoint(mem, state, code, prefix=intro)


def check(mem: Dict[str, Any], code: str) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None:
        return _msg("There is no guided step open. Say start guided learning.", state)
    parsed = Code(code)
    sub = int(state.get("sub_step", -1))
    if 0 <= sub < len(cp.small_steps) and _allows("hint"):
        problem = cp.small_steps[sub][1](parsed)
        if problem is None:
            state["sub_step"] = sub + 1 if sub + 1 < len(cp.small_steps) else -1
            learner_model.record_evidence(mem, cp.concept, "practiced")
            return _msg("Good, that small step is right. " + _goal_text(mem, cp, state), state)
        return _msg(problem + " Say give me a hint if you want a clue.", state, guided_passed=False)

    problem = cp.check(parsed)
    if problem is None:
        independent = int(state.get("hint_level") or 0) == 0
        learner_model.record_evidence(mem, cp.concept, "independent_success" if independent else "success_with_hint")
        _mark_done(state, cp)
        praise = "Correct." if independent else "Correct, well done working through the hints."
        if cp.question is not None:
            asked = cp.question.ask(parsed)
            if asked:
                question, answers = asked
                state["question"] = {"answers": list(answers), "explain": cp.question.explain}
                return _msg(f"{praise} {question}", state, guided_passed=True, understanding_check=True)
        return {**_advance(mem, state, code, praise), "guided_passed": True}

    state["attempts"] = int(state.get("attempts") or 0) + 1
    learner_model.record_evidence(mem, cp.concept, "checkpoint_failed")
    if not _allows("hint"):
        # Hints are off for this assignment: the verdict only, never the
        # targeted problem (that is exactly what a hint would reveal).
        return _msg("Not yet. This step is not complete. Compare your code with the task and try again.",
                    state, guided_passed=False)
    level = learner_model.scaffolding(mem, cp.concept)
    text = problem
    if level == "more":
        text += " Say give me a hint if you want a small clue."
    elif level == "normal" and state["attempts"] >= 2:
        text += " You can say give me a hint."
    return _msg(text, state, guided_passed=False)


def hint(mem: Dict[str, Any], code: str, *, stronger: bool = False) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None:
        return _msg("There is no guided step open. Say start guided learning.", state)
    level = int(state.get("hint_level") or 0)
    if stronger and level == 0:
        level = 1 if state.get("hints_given_for") == cp.id else 0
    ladder = list(cp.hints)
    learner_model.record_evidence(mem, cp.concept, "hint_used")
    state["hints_used"] = int(state.get("hints_used") or 0) + 1
    state["hints_given_for"] = cp.id
    if level < len(ladder):
        state["hint_level"] = level + 1
        tag = "Hint" if level == 0 else "Stronger hint"
        more = " Say another hint for more help." if level + 1 < len(ladder) else " Say show me an example if you want to see it."
        return _msg(f"{tag}: {ladder[level]}{more}", state, hint_level=level + 1)
    if not _allows("generate"):
        return _msg("That was the last hint. Full examples are turned off for this assignment. "
                    "Say check my work when you have tried again.", state, hint_level=level)
    state["hint_level"] = len(ladder) + 1
    tail = "Type it in your own words, then say check my work."
    return _msg(f"Here is an example you can adapt:\n{cp.example}\n{tail}", state, hint_level=len(ladder) + 1,
                example_code=cp.example,
                speech=_example_speech("Here is an example you can adapt:", cp.example, tail))


def example(mem: Dict[str, Any]) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None:
        return _msg("There is no guided step open. Say start guided learning.", state)
    learner_model.record_evidence(mem, cp.concept, "hint_used", strength=2)
    state["hint_level"] = len(cp.hints) + 1
    tail = "Try writing it yourself, then say check my work."
    return _msg(f"Here is an example:\n{cp.example}\n{tail}", state, example_code=cp.example,
                speech=_example_speech("Here is an example:", cp.example, tail))


def why(mem: Dict[str, Any], code: str) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None:
        return _msg("There is no guided step open. Say start guided learning.", state)
    problem = cp.check(Code(code))
    if problem is None:
        return _msg("Nothing is wrong: this step is complete. Say check my work to move on.", state)
    return _msg(problem, state)


def explain(mem: Dict[str, Any]) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None:
        return _msg("There is no guided step open. Say start guided learning.", state)
    learner_model.record_evidence(mem, cp.concept, "simpler_requested")
    return _msg(f"{cp.explain} Your step: {cp.goal}", state)


def repeat(mem: Dict[str, Any]) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None:
        return _msg("There is no guided step open. Say start guided learning.", state)
    path = PATHS_BY_ID[state["path"]]
    return _msg(f"Step {int(state['index']) + 1} of {len(path.checkpoints)}. " + _goal_text(mem, cp, state), state)


def next_step(mem: Dict[str, Any], code: str) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None:
        return start(mem, code)
    if state.get("question"):
        state["question"] = None
        return _advance(mem, state, code, "Okay, moving on.")
    if cp.id in state["completed"].get(state["path"], []):
        return _advance(mem, state, code, "")
    return _msg("You are still on this step. " + _goal_text(mem, cp, state), state)


def skip(mem: Dict[str, Any], code: str) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None:
        return _msg("There is no guided step open. Say start guided learning.", state)
    learner_model.record_evidence(mem, cp.concept, "encountered")
    return _advance(mem, state, code, "Skipped. You can come back to it later.")


def progress(mem: Dict[str, Any]) -> Dict[str, Any]:
    state = _state(mem)
    parts = []
    for path in PATHS:
        done = len(state["completed"].get(path.id, []))
        if done:
            parts.append(f"{path.title}: {done} of {len(path.checkpoints)}")
    current = ""
    cp = _current(state)
    if state.get("active") and cp is not None:
        current = f" You are on {PATHS_BY_ID[state['path']].title}, step {int(state['index']) + 1}: {cp.goal}"
    if not parts:
        return _msg("You have not completed any guided steps yet." + current, state)
    return _msg("Completed guided steps. " + "; ".join(parts) + "." + current, state)


def stop(mem: Dict[str, Any]) -> Dict[str, Any]:
    state = _state(mem)
    state["active"] = False
    return _msg("Guided learning paused. Your progress is saved for this session. Say start guided learning to continue.",
                state)


def load(mem: Dict[str, Any]) -> Dict[str, Any]:
    state = _state(mem)
    cp = _current(state)
    if cp is None or not cp.starter:
        return _msg("This step has no example code to load. Write your own code for it.", state)
    return _load_starter(cp, state, "Loaded.")


def answer_question(mem: Dict[str, Any], code: str, reply: str,
                    assistance: Optional[Dict[str, bool]] = None) -> Optional[Dict[str, Any]]:
    """Grade a reply to a pending understanding question; None if not an answer."""
    token = _ASSIST.set(assistance)
    try:
        return _answer_question(mem, code, reply)
    finally:
        _ASSIST.reset(token)


def _answer_question(mem: Dict[str, Any], code: str, reply: str) -> Optional[Dict[str, Any]]:
    state = _state(mem)
    pending = state.get("question")
    if not is_active(mem) or not isinstance(pending, dict):
        return None
    cp = _current(state)
    t = _norm(reply)
    if not t or cp is None:
        return None
    answers = [str(a).lower() for a in pending.get("answers") or []]
    state["question"] = None
    if any(re.search(r"(?<![\w.])" + re.escape(a) + r"(?![\w.])", t) for a in answers):
        learner_model.record_evidence(mem, cp.concept, "quiz_correct")
        return _advance(mem, state, code, "Yes, exactly.")
    explanation = pending.get("explain", "") if _allows("concept_qa") else ""
    return _advance(mem, state, code, f"Not quite. {explanation}".strip())


def handle(kind: str, mem: Dict[str, Any], code: str, topic: str = "",
           assistance: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    """assistance: the caller's classroom capability settings (None = unrestricted)."""
    token = _ASSIST.set(assistance)
    try:
        return _handle(kind, mem, code, topic)
    finally:
        _ASSIST.reset(token)


def _handle(kind: str, mem: Dict[str, Any], code: str, topic: str) -> Dict[str, Any]:
    if kind == "list":
        return _msg(list_paths_text(mem), _state(mem))
    if kind == "start":
        return start(mem, code, topic)
    handlers = {
        "check": lambda: check(mem, code),
        "hint": lambda: hint(mem, code),
        "more_hint": lambda: hint(mem, code, stronger=True),
        "example": lambda: example(mem),
        "why": lambda: why(mem, code),
        "explain": lambda: explain(mem),
        "repeat": lambda: repeat(mem),
        "next": lambda: next_step(mem, code),
        "skip": lambda: skip(mem, code),
        "progress": lambda: progress(mem),
        "stop": lambda: stop(mem),
        "load": lambda: load(mem),
    }
    return handlers[kind]()

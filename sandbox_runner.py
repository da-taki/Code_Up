"""
CodeUp sandbox runner. Executed as a subprocess by app.py /run handler.

Reads the user's code from $CODEUP_CODE_FILE and writes a JSON trace to
$CODEUP_TRACE_FILE. Two input mechanisms supported:

Mechanism A (default, pre-flight queue):
  $CODEUP_INPUTS_FILE points to a file with one input value per line.
  input() pops sequentially. Empty queue raises a friendly error.

Mechanism B (interactive streaming):
  $CODEUP_INTERACTIVE=1 enables it. input() writes a sentinel
  (CODEUP::INPUT_REQUEST::<prompt>\n) to stdout, then blocks reading one
  line from the FIFO at $CODEUP_INPUT_FIFO. Parent process drives the
  conversation over SSE. Falls back to RuntimeError if FIFO read fails.

Never imported by the parent app — only ever executed via
`python sandbox_runner.py`.
"""
import sys, time, json, traceback, os

ALLOWED_MODULES = {'math', 'random', 'string', 'datetime', 'date'}

import math as _math
import random as _random
import string as _string
import datetime as _datetime
_PRELOADED = {'math': _math, 'random': _random, 'string': _string, 'datetime': _datetime}


class SafeFunction:
    def __init__(self, func):
        self._func = func

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)

    def __getattr__(self, name):
        raise AttributeError(f'Access to {name} is blocked')


import ast as _ast
_FORBIDDEN_NAMES = {
    '__subclasses__', '__bases__', '__mro__', '__class__', '__globals__',
    '__builtins__', '__import__', '__loader__', '__spec__', '__getattribute__',
    '__reduce__', '__reduce_ex__', '__dict__', '__code__', '__init_subclass__',
    '__base__', '__new__',
}
_FORBIDDEN_GETATTR_FUNCS = {
    'getattr', 'setattr', 'delattr', 'hasattr', 'vars', 'globals', 'locals',
    'eval', 'exec', 'compile', 'open', '__import__',
}


def _audit_ast(source):
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            raise SyntaxError(f"Access to '{node.attr}' is not allowed in the sandbox")
        if isinstance(node, _ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise SyntaxError(f"Reference to '{node.id}' is not allowed in the sandbox")
        if isinstance(node, _ast.Call):
            func = node.func
            func_name = None
            if isinstance(func, _ast.Name):
                func_name = func.id
            elif isinstance(func, _ast.Attribute):
                func_name = func.attr
            if func_name in _FORBIDDEN_GETATTR_FUNCS:
                raise SyntaxError(f"Use of '{func_name}' is not allowed in the sandbox")
            for arg in node.args:
                if isinstance(arg, _ast.Constant) and isinstance(arg.value, str) and arg.value in _FORBIDDEN_NAMES:
                    raise SyntaxError(f"Reflective access to '{arg.value}' is not allowed in the sandbox")


def restricted_import(name, *args, **kwargs):
    if name not in ALLOWED_MODULES:
        raise ImportError(f"Module '{name}' is not allowed.")
    if name in _PRELOADED:
        return _PRELOADED[name]
    return __import__(name, *args, **kwargs)


def _strict_import(name, globals_arg=None, locals_arg=None, fromlist=(), level=0):
    if name not in ALLOWED_MODULES:
        raise ImportError(f"Module '{name}' is not allowed.")
    if level != 0:
        raise ImportError("Relative imports are not allowed in the sandbox.")
    if fromlist:
        for item in fromlist:
            if not isinstance(item, str) or item.startswith('_') or item in _FORBIDDEN_NAMES:
                raise ImportError(f"Import of '{item}' is not allowed in the sandbox.")
    return restricted_import(name)


# ---------------------------------------------------------------------------
# Input mechanisms
# ---------------------------------------------------------------------------
_INPUT_QUEUE = []
_INPUT_INDEX = [0]
_INTERACTIVE = os.environ.get('CODEUP_INTERACTIVE', '0') == '1'
_INPUT_FIFO = os.environ.get('CODEUP_INPUT_FIFO', '')

# Sentinel pattern that the parent process watches for in stdout.
# Format chosen to be unambiguous and unlikely to appear in user output.
_INPUT_SENTINEL_PREFIX = "CODEUP::INPUT_REQUEST::"


def _interactive_input(prompt=''):
    """Mechanism B: write sentinel to stdout, block on FIFO read."""
    # Echo prompt to stdout BEFORE the sentinel so the user hears it
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    # Sentinel tells parent process: pause output streaming, ask user for input
    sys.stdout.write(f"\n{_INPUT_SENTINEL_PREFIX}{prompt}\n")
    sys.stdout.flush()

    if not _INPUT_FIFO or not os.path.exists(_INPUT_FIFO):
        raise RuntimeError(
            "Interactive input is enabled but the input channel is not "
            "available. Switch to pre-flight inputs or restart the run."
        )
    try:
        # Open the FIFO for reading. This blocks until parent writes a line.
        # Open inside a `with` so the file handle is closed after each read,
        # which is required for FIFOs to allow subsequent writes from parent.
        with open(_INPUT_FIFO, 'r', encoding='utf-8') as fifo:
            line = fifo.readline()
        if not line:
            raise RuntimeError(
                "Input channel closed unexpectedly. The run may have been "
                "interrupted."
            )
        value = line.rstrip('\n').rstrip('\r')
        # Echo what the user "typed" so it appears in the transcript
        sys.stdout.write(value + '\n')
        sys.stdout.flush()
        return value
    except (OSError, IOError) as e:
        raise RuntimeError(f"Could not read input: {e}")


def _queued_input(prompt=''):
    """Mechanism A: pop from pre-declared queue."""
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    idx = _INPUT_INDEX[0]
    if idx >= len(_INPUT_QUEUE):
        needed = idx + 1
        provided = len(_INPUT_QUEUE)
        raise RuntimeError(
            f"Your code asked for input number {needed}, but you only "
            f"provided {provided}. Add more inputs in the inputs panel, or "
            f"say 'set inputs to' followed by your values. For example: "
            f"'set inputs to Alice and 17'. Alternatively, switch to live "
            f"input mode."
        )
    value = _INPUT_QUEUE[idx]
    _INPUT_INDEX[0] = idx + 1
    sys.stdout.write(value + '\n')
    sys.stdout.flush()
    return value


def _select_input_func():
    """Pick the input function based on environment configuration."""
    if _INTERACTIVE and _INPUT_FIFO:
        return _interactive_input
    return _queued_input


SAFE_GLOBALS = {
    'print': SafeFunction(print),
    'range': SafeFunction(range),
    'len': SafeFunction(len),
    'int': SafeFunction(int),
    'float': SafeFunction(float),
    'str': SafeFunction(str),
    'bool': SafeFunction(bool),
    'list': SafeFunction(list),
    'dict': SafeFunction(dict),
    'tuple': SafeFunction(tuple),
    'set': SafeFunction(set),
    'sum': SafeFunction(sum),
    'min': SafeFunction(min),
    'max': SafeFunction(max),
    'abs': SafeFunction(abs),
    'round': SafeFunction(round),
    'sorted': SafeFunction(sorted),
    'enumerate': SafeFunction(enumerate),
    'zip': SafeFunction(zip),
    'map': SafeFunction(map),
    'filter': SafeFunction(filter),
    'pow': SafeFunction(pow),
    'repr': SafeFunction(repr),
    '__builtins__': {
        'None': None, 'False': False, 'True': True,
        'isinstance': isinstance,
        'AttributeError': AttributeError, 'TypeError': TypeError,
        'ValueError': ValueError, 'Exception': Exception,
        'BaseException': BaseException, 'StopIteration': StopIteration,
        'RuntimeError': RuntimeError, 'ImportError': ImportError,
        'NameError': NameError, 'IndexError': IndexError,
        'KeyError': KeyError, 'ZeroDivisionError': ZeroDivisionError,
        'OverflowError': OverflowError, 'MemoryError': MemoryError,
        'NotImplemented': NotImplemented,
        '__import__': _strict_import,
    },
    '__import__': _strict_import,
    'input': _select_input_func(),
    'math': _math,
    'random': _random,
    'string': _string,
    'datetime': _datetime,
}


def _safe_repr(v):
    try:
        r = repr(v)
    except Exception:
        try:
            r = str(v)
        except Exception:
            r = "<" + type(v).__name__ + ">"
    if len(r) > 200:
        return r[:197] + '...'
    return r


def _load_input_queue():
    inputs_file = os.environ.get('CODEUP_INPUTS_FILE', '')
    if not inputs_file or not os.path.exists(inputs_file):
        return
    try:
        with open(inputs_file, encoding='utf-8') as f:
            for line in f:
                _INPUT_QUEUE.append(line.rstrip('\n').rstrip('\r'))
    except Exception:
        pass


def main():
    code_file = os.environ.get('CODEUP_CODE_FILE', '')
    if code_file and os.path.exists(code_file):
        with open(code_file, encoding='utf-8') as f:
            code = f.read()
    else:
        code = ''

    _load_input_queue()

    trace = []
    last_locals = {}
    start = time.time()
    MAX_TRACE_EVENTS = 5000
    overflow_logged = [False]

    def tracer(frame, event, arg):
        nonlocal last_locals
        if frame.f_code.co_filename != '<user>':
            return tracer

        if len(trace) >= MAX_TRACE_EVENTS:
            if not overflow_logged[0]:
                trace.append({'type': 'overflow', 'note': 'event limit reached; further events dropped'})
                overflow_logged[0] = True
            return tracer

        if event == 'line':
            line = frame.f_lineno
            trace.append({'type': 'line_exec', 'line': line})
            current = {k: _safe_repr(v) for k, v in frame.f_locals.items()}
            changes = []
            for k, v_repr in current.items():
                if k not in last_locals:
                    changes.append(k + " initialized to " + v_repr)
                else:
                    try:
                        if last_locals[k] != v_repr:
                            changes.append(k + " changed from " + last_locals[k] + " to " + v_repr)
                    except Exception:
                        changes.append(k + " changed (uncomparable value)")
            for k in last_locals:
                if k not in current:
                    changes.append(k + " went out of scope")
            if changes:
                trace.append({'type': 'state_change', 'line': line, 'changes': changes})
            last_locals = current
        elif event == 'call':
            trace.append({'type': 'call', 'function': frame.f_code.co_name, 'line': frame.f_lineno})
        elif event == 'return':
            trace.append({'type': 'return', 'value': _safe_repr(arg)})
        return tracer

    try:
        _audit_ast(code)
        compiled = compile(code, '<user>', 'exec')
        sys.settrace(tracer)
        exec(compiled, SAFE_GLOBALS, {})
    except Exception:
        traceback.print_exc(file=sys.stderr)
    finally:
        sys.settrace(None)
        trace_file = os.environ.get('CODEUP_TRACE_FILE', '')
        if trace_file:
            try:
                with open(trace_file, 'w', encoding='utf-8') as f:
                    json.dump({
                        'trace': trace,
                        'duration_ms': int((time.time() - start) * 1000),
                        'inputs_consumed': _INPUT_INDEX[0],
                    }, f)
            except Exception:
                pass


if __name__ == '__main__':
    main()
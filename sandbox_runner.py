"""
CodeUp sandbox runner. Executed as a subprocess by app.py /run handler.

Reads the user's code from the file at $CODEUP_CODE_FILE and writes a JSON
trace to $CODEUP_TRACE_FILE. Never imported by the parent app — only ever
executed via `python sandbox_runner.py`.
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


def _blocked_input(prompt=''):
    if prompt:
        print(prompt)
    raise RuntimeError(
        "CodeUp doesn't use input(). Instead, just write the value directly. "
        "For example, change  name = input('Your name?')  to  name = 'Alice' . "
        "Then run again."
    )


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
    'input': _blocked_input,
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


def main():
    code_file = os.environ.get('CODEUP_CODE_FILE', '')
    if code_file and os.path.exists(code_file):
        with open(code_file, encoding='utf-8') as f:
            code = f.read()
    else:
        code = ''

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
            with open(trace_file, 'w', encoding='utf-8') as f:
                json.dump({'trace': trace, 'duration_ms': int((time.time() - start) * 1000)}, f)


if __name__ == '__main__':
    main()
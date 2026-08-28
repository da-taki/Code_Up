import ast as _ast
import builtins as _builtins
import collections as _collections
import csv as _csv
import datetime as _datetime
import itertools as _itertools
import json
import math as _math
import os
import pathlib as _pathlib
import random as _random
import statistics as _statistics
import string as _string
import sys
import time
import traceback
import types as _types
import typing as _typing

# Force UTF-8 stdout/stderr regardless of the parent process's locale (on Windows,
# the default is the system ANSI codepage, e.g. cp1252, which cannot encode emoji,
# Hindi/Devanagari, or most non-Latin-1 text - print(emoji_or_hindi_text) would
# otherwise crash with UnicodeEncodeError instead of printing). errors='replace'
# keeps a single malformed character from crashing an otherwise-valid run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_MODULE_OBJECTS = {
    'math': _math,
    'random': _random,
    'string': _string,
    'datetime': _datetime,
    'statistics': _statistics,
    'json': json,
    'csv': _csv,
    'pathlib': None,
    'typing': _typing,
    'collections': _collections,
    'itertools': _itertools,
}
_THIRD_PARTY_MODULES = {'numpy', 'pandas', 'matplotlib'}
ALLOWED_MODULES = frozenset(set(_MODULE_OBJECTS) | _THIRD_PARTY_MODULES)
_PRELOADED = dict(_MODULE_OBJECTS)
_ALLOWED_RUNTIME_IMPORTS = {'_strptime'}
_SAFE_OPEN_ROOT = os.path.abspath(os.environ.get('CODEUP_SAFE_OPEN_ROOT') or os.getcwd())
_PROJECT_ROOT = os.path.abspath(os.environ.get('CODEUP_PROJECT_ROOT') or _SAFE_OPEN_ROOT)
try:
    _PROJECT_FILES = set(json.loads(os.environ.get('CODEUP_PROJECT_FILES') or '[]'))
except json.JSONDecodeError:
    _PROJECT_FILES = set()
_LOCAL_MODULES = {m for m in os.environ.get('CODEUP_LOCAL_MODULES', '').split(',') if m}
_LOCAL_MODULE_CACHE = {}
_LOCAL_MODULE_LOADING = set()


def _resolve_safe_path(file):
    if isinstance(file, int):
        raise PermissionError("File descriptors are not available in the CodeUp sandbox.")
    raw = os.fspath(file)
    candidate = raw if os.path.isabs(raw) else os.path.join(_SAFE_OPEN_ROOT, raw)
    resolved = os.path.abspath(candidate)
    root = os.path.abspath(_SAFE_OPEN_ROOT)
    try:
        common = os.path.commonpath([root, resolved])
    except ValueError:
        common = ""
    if common != root:
        raise PermissionError("File access is limited to this CodeUp project root.")
    return resolved


def _safe_open(file, mode='r', buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
    if opener is not None:
        raise PermissionError("Custom file openers are not available in the CodeUp sandbox.")
    mode = str(mode or 'r')
    if any(flag in mode for flag in ('+',)):
        raise PermissionError("Read-write file mode is not available in the CodeUp sandbox.")
    if not set(mode) <= set('rwaxtbU'):
        raise PermissionError("That file mode is not available in the CodeUp sandbox.")
    resolved = _resolve_safe_path(file)
    if any(flag in mode for flag in ('w', 'a', 'x')):
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
    return _builtins.open(resolved, mode, buffering=buffering, encoding=encoding, errors=errors, newline=newline, closefd=closefd)


class _SafePath:
    def __init__(self, *parts):
        raw = os.path.join(*(os.fspath(part) for part in parts)) if parts else "."
        self._path = _resolve_safe_path(raw)

    def __fspath__(self):
        return self._path

    def __str__(self):
        try:
            return os.path.relpath(self._path, _SAFE_OPEN_ROOT)
        except ValueError:
            return self._path

    def __truediv__(self, other):
        return _SafePath(os.path.join(self._path, os.fspath(other)))

    @property
    def name(self):
        return os.path.basename(self._path)

    @property
    def suffix(self):
        return os.path.splitext(self._path)[1]

    @property
    def parent(self):
        return _SafePath(os.path.dirname(self._path))

    def exists(self):
        return os.path.exists(self._path)

    def is_file(self):
        return os.path.isfile(self._path)

    def is_dir(self):
        return os.path.isdir(self._path)

    def read_text(self, encoding='utf-8'):
        with _safe_open(self._path, 'r', encoding=encoding) as handle:
            return handle.read()

    def write_text(self, data, encoding='utf-8'):
        with _safe_open(self._path, 'w', encoding=encoding) as handle:
            return handle.write(str(data))

    def open(self, mode='r', *args, **kwargs):
        return _safe_open(self._path, mode, *args, **kwargs)


_PRELOADED['pathlib'] = _types.SimpleNamespace(Path=_SafePath, PurePath=_pathlib.PurePath)


class SafeFunction:
    def __init__(self, func):
        self._func = func

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)

    def __getattr__(self, name):
        raise AttributeError(f'Access to {name} is blocked')


_FORBIDDEN_NAMES = {
    '__subclasses__', '__bases__', '__mro__', '__class__', '__globals__',
    '__builtins__', '__import__', '__loader__', '__spec__', '__getattribute__',
    '__reduce__', '__reduce_ex__', '__dict__', '__code__', '__init_subclass__',
    '__base__', '__new__',
    # Frame/traceback/generator introspection: an exception's __traceback__ (or a
    # generator's gi_frame, a coroutine's cr_frame, ...) exposes a real frame object
    # whose f_back chain walks all the way up to THIS module's own frame - and from
    # there f_globals hands back the real, unrestricted os/sys/traceback module
    # objects imported at the top of this file, completely bypassing every other
    # check here (no getattr/eval/import needed - confirmed exploitable via
    # `except Exception as e: f = e.__traceback__.tb_frame` then walking f.f_back).
    # No beginner Python program legitimately needs any of these.
    '__traceback__', 'tb_frame', 'tb_next', 'tb_lasti', 'tb_lineno',
    'f_back', 'f_globals', 'f_locals', 'f_builtins', 'f_code', 'f_lasti', 'f_lineno', 'f_trace',
    'gi_frame', 'gi_code', 'gi_yieldfrom',
    'cr_frame', 'cr_code', 'cr_await',
    'ag_frame', 'ag_code',
    '__closure__', 'cell_contents', '__func__', '__self__', '__wrapped__',
    '__cause__', '__context__',
}
_FORBIDDEN_GETATTR_FUNCS = {
    'getattr', 'setattr', 'delattr', 'hasattr', 'vars', 'globals', 'locals',
    'eval', 'exec', 'compile', '__import__',
}


def _audit_ast(source):
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                top_level = _top_level_module(alias.name)
                if top_level not in ALLOWED_MODULES and top_level not in _LOCAL_MODULES:
                    raise SyntaxError(f"Module '{alias.name}' is not allowed in the sandbox")
        if isinstance(node, _ast.ImportFrom):
            if node.level:
                raise SyntaxError("Relative imports are not allowed in the sandbox")
            top_level = _top_level_module(node.module or '')
            if top_level not in ALLOWED_MODULES and top_level not in _LOCAL_MODULES:
                raise SyntaxError(f"Module '{node.module}' is not allowed in the sandbox")
            for alias in node.names:
                if alias.name.startswith('_') or alias.name in _FORBIDDEN_NAMES:
                    raise SyntaxError(f"Import of '{alias.name}' is not allowed in the sandbox")
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
                raise SyntaxError(
                    f"Use of '{func_name}' is not allowed in the sandbox "
                    f"(NameError: name '{func_name}' is not defined)"
                )
            for arg in node.args:
                if isinstance(arg, _ast.Constant) and isinstance(arg.value, str) and arg.value in _FORBIDDEN_NAMES:
                    raise SyntaxError(f"Reflective access to '{arg.value}' is not allowed in the sandbox")


def _top_level_module(name):
    return str(name).split('.', 1)[0]


def _local_module_path(name):
    if not _LOCAL_MODULES:
        return None
    candidates = []
    module_path = str(name).replace('.', '/') + '.py'
    candidates.append(module_path)
    top = _top_level_module(name) + '.py'
    candidates.append(top)
    for rel in candidates:
        if rel in _PROJECT_FILES:
            abs_path = os.path.abspath(os.path.join(_PROJECT_ROOT, rel))
            try:
                common = os.path.commonpath([_PROJECT_ROOT, abs_path])
            except ValueError:
                common = ""
            if common == _PROJECT_ROOT and os.path.exists(abs_path):
                return rel, abs_path
    return None


def _load_local_module(name):
    top_level = _top_level_module(name)
    if top_level not in _LOCAL_MODULES:
        raise ImportError(f"Module '{name}' is not allowed.")
    cached = _LOCAL_MODULE_CACHE.get(name) or _LOCAL_MODULE_CACHE.get(top_level)
    if cached is not None:
        return cached
    if name in _LOCAL_MODULE_LOADING:
        raise ImportError(f"Circular import involving '{name}' is not supported in this sandbox.")
    resolved = _local_module_path(name) or _local_module_path(top_level)
    if not resolved:
        raise ImportError(f"Local module '{name}' was not found in this project.")
    rel_path, abs_path = resolved
    _LOCAL_MODULE_LOADING.add(name)
    try:
        with _builtins.open(abs_path, encoding='utf-8') as handle:
            source = handle.read()
        _audit_ast(source)
        module_name = name if rel_path.replace('/', '.').endswith(name + '.py') else top_level
        module = _types.ModuleType(module_name)
        module.__file__ = abs_path
        module.__package__ = ""
        namespace = _make_execution_namespace(module_name)
        namespace['__file__'] = abs_path
        module.__dict__.update(namespace)
        sys.modules[module_name] = module
        _LOCAL_MODULE_CACHE[module_name] = module
        _LOCAL_MODULE_CACHE[top_level] = module
        compiled = compile(source, rel_path, 'exec')
        exec(compiled, module.__dict__, module.__dict__)
        return module
    finally:
        _LOCAL_MODULE_LOADING.discard(name)


def restricted_import(name, *args, **kwargs):
    top_level = _top_level_module(name)
    if top_level in _LOCAL_MODULES:
        return _load_local_module(name)
    if top_level not in ALLOWED_MODULES:
        raise ImportError(f"Module '{name}' is not allowed.")
    if top_level in _PRELOADED:
        return _PRELOADED[top_level]
    return _builtins.__import__(name, *args, **kwargs)


def _strict_import(name, globals_arg=None, locals_arg=None, fromlist=(), level=0):
    if level != 0:
        raise ImportError("Relative imports are not allowed in the sandbox.")
    top_level = _top_level_module(name)
    if fromlist:
        for item in fromlist:
            if not isinstance(item, str) or item.startswith('_') or item in _FORBIDDEN_NAMES:
                raise ImportError(f"Import of '{item}' is not allowed in the sandbox.")

    if top_level in ALLOWED_MODULES:
        return restricted_import(name, globals_arg, locals_arg, fromlist, level)

    if top_level in _LOCAL_MODULES:
        return _load_local_module(name)

    if top_level in _ALLOWED_RUNTIME_IMPORTS:
        return _builtins.__import__(name, globals_arg, locals_arg, fromlist, level)

    raise ImportError(f"Module '{name}' is not allowed.")


_INPUT_QUEUE = []
_INPUT_INDEX = [0]
_INPUT_LOAD_ERROR = [None]
_INTERACTIVE = os.environ.get('CODEUP_INTERACTIVE', '0') == '1'
_INPUT_FIFO = os.environ.get('CODEUP_INPUT_FIFO', '')

_INPUT_SENTINEL_PREFIX = "CODEUP::INPUT_REQUEST::"


def _interactive_input(prompt=''):
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    sys.stdout.write(f"\n{_INPUT_SENTINEL_PREFIX}{prompt}\n")
    sys.stdout.flush()

    if not _INPUT_FIFO or not os.path.exists(_INPUT_FIFO):
        raise RuntimeError(
            "Interactive input is enabled but the input channel is not "
            "available. Switch to pre-flight inputs or restart the run."
        )
    try:
        with open(_INPUT_FIFO, 'r', encoding='utf-8') as fifo:
            line = fifo.readline()
        if not line:
            raise RuntimeError(
                "Input channel closed unexpectedly. The run may have been "
                "interrupted."
            )
        value = line.rstrip('\n').rstrip('\r')
        sys.stdout.write(value + '\n')
        sys.stdout.flush()
        return value
    except (OSError, IOError) as e:
        raise RuntimeError(f"Could not read input: {e}")


def _queued_input_available() -> bool:
    return _INPUT_INDEX[0] < len(_INPUT_QUEUE)


def _hybrid_input(prompt=''):
    if _queued_input_available():
        return _queued_input(prompt)
    return _interactive_input(prompt)


def _queued_input(prompt=''):
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
    if _INTERACTIVE:
        return _hybrid_input
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
        'object': object, 'super': super,
        'staticmethod': staticmethod, 'classmethod': classmethod, 'property': property,
        '__build_class__': _builtins.__build_class__,
        '__import__': _strict_import,
        'open': _safe_open,
    },
    '__import__': _strict_import,
    'open': SafeFunction(_safe_open),
    'input': _select_input_func(),
    'math': _math,
    'random': _random,
    'string': _string,
    'datetime': _datetime,
    'statistics': _statistics,
    'json': json,
    'csv': _csv,
    'pathlib': _PRELOADED['pathlib'],
    'collections': _collections,
    'itertools': _itertools,
}


def _make_execution_namespace(module_name='__main__'):
    namespace = dict(SAFE_GLOBALS)
    namespace['__builtins__'] = dict(SAFE_GLOBALS['__builtins__'])
    namespace['__name__'] = module_name
    return namespace


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
    except (OSError, UnicodeDecodeError) as e:
        _INPUT_LOAD_ERROR[0] = f"Could not load pre-flight inputs: {e}"
        print(_INPUT_LOAD_ERROR[0], file=sys.stderr)


def main():
    code_file = os.environ.get('CODEUP_CODE_FILE', '')
    if code_file and os.path.exists(code_file):
        with open(code_file, encoding='utf-8') as f:
            code = f.read()
    else:
        code = ''

    _load_input_queue()

    trace = []
    last_locals_by_frame = {}
    start = time.time()
    MAX_TRACE_EVENTS = 5000
    overflow_logged = [False]
    main_file_label = os.environ.get('CODEUP_MAIN_FILE') or '<user>'
    trace_filenames = {'<user>', main_file_label}
    trace_filenames.update(_PROJECT_FILES)
    execution_namespace = _make_execution_namespace('__main__')
    execution_namespace['__file__'] = os.path.abspath(code_file) if code_file else main_file_label
    initial_names = set(execution_namespace)

    def _traceable_locals(frame):
        current = {}
        is_module_frame = frame.f_code.co_name == '<module>'
        for k, v in frame.f_locals.items():
            if is_module_frame and k in initial_names:
                continue
            if k.startswith('__') and k.endswith('__'):
                continue
            current[k] = _safe_repr(v)
        return current

    def tracer(frame, event, arg):
        frame_file = frame.f_code.co_filename
        if frame_file not in trace_filenames:
            return tracer

        if len(trace) >= MAX_TRACE_EVENTS:
            if not overflow_logged[0]:
                trace.append({'type': 'overflow', 'note': 'event limit reached; further events dropped'})
                overflow_logged[0] = True
            return tracer

        if event == 'line':
            line = frame.f_lineno
            trace.append({'type': 'line_exec', 'line': line, 'file': frame_file})
            frame_key = id(frame)
            last_locals = last_locals_by_frame.get(frame_key, {})
            current = _traceable_locals(frame)
            changes = []
            structured_changes = []
            for k, v_repr in current.items():
                if k not in last_locals:
                    changes.append(k + " initialized to " + v_repr)
                    structured_changes.append({
                        'variable': k,
                        'kind': 'initialized',
                        'before': None,
                        'after': v_repr,
                    })
                else:
                    try:
                        if last_locals[k] != v_repr:
                            changes.append(k + " changed from " + last_locals[k] + " to " + v_repr)
                            structured_changes.append({
                                'variable': k,
                                'kind': 'changed',
                                'before': last_locals[k],
                                'after': v_repr,
                            })
                    except Exception:
                        changes.append(k + " changed (uncomparable value)")
                        structured_changes.append({
                            'variable': k,
                            'kind': 'changed',
                            'before': None,
                            'after': v_repr,
                        })
            for k in last_locals:
                if k not in current:
                    changes.append(k + " went out of scope")
                    structured_changes.append({
                        'variable': k,
                        'kind': 'scope_exit',
                        'before': last_locals[k],
                        'after': None,
                    })
            if changes:
                trace.append({
                    'type': 'state_change',
                    'line': line,
                    'file': frame_file,
                    'changes': changes,
                    'structured_changes': structured_changes,
                })
            last_locals_by_frame[frame_key] = current
        elif event == 'call':
            trace.append({'type': 'call', 'function': frame.f_code.co_name, 'line': frame.f_lineno, 'file': frame_file})
        elif event == 'return':
            trace.append({'type': 'return', 'file': frame_file, 'value': _safe_repr(arg)})
            last_locals_by_frame.pop(id(frame), None)
        return tracer

    try:
        if _INPUT_LOAD_ERROR[0]:
            raise RuntimeError(_INPUT_LOAD_ERROR[0])
        _audit_ast(code)
        compiled = compile(code, main_file_label, 'exec')
        sys.settrace(tracer)
        exec(compiled, execution_namespace, execution_namespace)
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
            except (OSError, TypeError) as e:
                print(f"Could not write trace file: {e}", file=sys.stderr)


if __name__ == '__main__':
    main()

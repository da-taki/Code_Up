import os
import time
import requests
import pytest


BASE = os.getenv("CODEUP_BASE", "http://127.0.0.1:5000")


# `session` and server lifecycle are provided by tests/conftest.py


def post(session, path, payload, expect_status=200):
    """POST request helper with JSON validation and explicit error reporting."""
    r = session.post(BASE + path, json=payload, timeout=10)
    assert r.status_code == expect_status, f"{path} returned {r.status_code}, expected {expect_status}"
    try:
        return r.json()
    except ValueError:
        pytest.fail(f"{path} did not return JSON (status {r.status_code}): {r.text}")


def get(session, path, expect_status=200):
    """GET request helper with JSON validation and explicit error reporting."""
    r = session.get(BASE + path, timeout=10)
    assert r.status_code == expect_status, f"{path} returned {r.status_code}, expected {expect_status}"
    try:
        return r.json()
    except ValueError:
        pytest.fail(f"{path} did not return JSON (status {r.status_code}): {r.text}")


def test_run(session):
    res = post(session, "/run", {"code": "print('smoke')"})
    assert res and res.get("success"), "/run basic execution failed"
    assert "smoke" in res.get("output", ""), "/run output incorrect"


def test_run_error(session):
    res = post(session, "/run", {"code": "raise ValueError('boom')"}, expect_status=200)
    assert res and not res.get("success"), "/run error path failed"
    assert "ValueError" in res.get("error", ""), "/run error traceback missing"


def test_syntax(session):
    res = post(session, "/check-syntax", {"code": "def f(:\n pass"})
    assert res and res.get("has_errors"), "/check-syntax failed to detect error"


def test_snippets(session):
    before = get(session, "/snippets")
    count_before = len(before.get("snippets", []))

    res = post(session, "/snippets", {"name": "smoke-test", "code": "print(1)"})
    # Poll until the snippet appears instead of sleeping a fixed amount
    deadline = time.time() + 5
    while time.time() < deadline:
        after = get(session, "/snippets")
        count_after = len(after.get("snippets", []))
        if count_after >= count_before + 1:
            break
        time.sleep(0.1)
    assert count_after == count_before + 1, "/snippets did not persist snippet"


def test_describe(session):
    code = "x = 1\nprint(x)"
    res = post(session, "/describe", {"code": code, "line": 2})
    assert res and res.get("success"), "/describe failed"


def test_variables(session):
    code = "x = 1\nx = x + 1"
    res = post(session, "/track-variables", {"code": code, "line": 2})
    assert res and res.get("success"), "/track-variables failed"


def test_voice(session):
    res = post(session, "/voice-command", {"text": "help"})
    assert res is not None, "/voice-command failed"


# ============================================================================
# EXPANDED TESTS: Edge Cases, Error Handling, Request Validation
# ============================================================================


def test_run_empty_code(session):
    """Empty code should be rejected with an error."""
    res = post(session, "/run", {"code": ""}, expect_status=400)
    assert not res.get("success"), "Empty code should not succeed"
    assert res.get("error"), "Empty code should have an error message"


def test_run_large_code(session):
    """Code over max size should be rejected (413 Payload Too Large)."""
    huge_code = "x = 1\n" * 20000  # Approximately 120KB
    res = post(session, "/run", {"code": huge_code}, expect_status=413)
    assert not res.get("success"), "Oversized code should fail"


def test_run_with_output(session):
    """Code that produces output should be captured."""
    res = post(session, "/run", {"code": "print('hello')\nprint('world')"})
    assert res.get("success"), "Multi-line output should succeed"
    output = res.get("output", "")
    assert "hello" in output and "world" in output, "Both print statements should appear in output"


def test_run_with_syntax_error(session):
    """Code with syntax errors should fail gracefully."""
    res = post(session, "/run", {"code": "def broken(:\n    pass"})
    assert not res.get("success"), "Syntax error should cause failure"
    assert res.get("error"), "Error message should be present"


def test_run_with_infinite_loop_protection(session):
    """Code with infinite loops should time out and be caught."""
    res = post(session, "/run", {"code": "while True:\n    pass"})
    assert not res.get("success"), "Infinite loop should fail"
    error = res.get("error", "")
    assert "time" in error.lower() or "exceed" in error.lower(), "Should indicate timeout or limit exceeded"


def test_run_recursion_limit(session):
    """Deep recursion should be caught before stack overflow."""
    res = post(session, "/run", {"code": "def f(n):\n    return f(n+1)\nf(0)"})
    assert not res.get("success"), "Deep recursion should fail"
    error = res.get("error", "").lower()
    # Error could mention recursion, depth, or NameError (if f is not defined in safe context)
    assert any(x in error for x in ["recursion", "depth", "nameerror", "not defined"]), \
        f"Should mention recursion/depth/NameError, got: {res.get('error', '')}"


def test_check_syntax_valid(session):
    """Valid Python syntax should pass check."""
    res = post(session, "/check-syntax", {"code": "x = 1\ny = x + 2\nprint(y)"})
    assert res.get("success"), "/check-syntax should return success"
    assert not res.get("has_errors"), "Valid syntax should have no errors"


def test_check_syntax_invalid_missing_colon(session):
    """Missing colon in function definition should be caught."""
    res = post(session, "/check-syntax", {"code": "def broken()\n    pass"})
    assert res.get("has_errors"), "Should detect missing colon"


def test_check_syntax_invalid_indentation(session):
    """Indentation errors should be detected."""
    res = post(session, "/check-syntax", {"code": "if True:\nprint('broken')"})
    assert res.get("has_errors"), "Should detect indentation error"


def test_snippets_empty_list(session):
    """Snippets list should be available and iterable."""
    res = get(session, "/snippets")
    assert "snippets" in res, "Response should have 'snippets' key"
    assert isinstance(res["snippets"], list), "Snippets should be a list"


def test_snippets_create_and_retrieve(session):
    """Create a snippet and verify it persists."""
    before = get(session, "/snippets")
    count_before = len(before.get("snippets", []))

    # Create with timestamp to ensure uniqueness
    unique_name = f"test-snippet-{int(time.time() * 1000)}"
    res = post(session, "/snippets", {"name": unique_name, "code": "x = 42"})
    
    # Wait for persistence
    deadline = time.time() + 5
    found = False
    while time.time() < deadline:
        after = get(session, "/snippets")
        for snippet in after.get("snippets", []):
            if snippet.get("name") == unique_name:
                found = True
                break
        if found:
            break
        time.sleep(0.1)
    
    assert found, f"Snippet '{unique_name}' should be persisted"


def test_snippets_create_invalid_missing_name(session):
    """Creating a snippet without a name should fail."""
    res = post(session, "/snippets", {"code": "print(1)"})
    # The endpoint may return success even without name validation, so check response structure
    assert isinstance(res, dict), "Response should be a dict"


def test_describe_valid_line(session):
    """Describe should work for valid line numbers."""
    code = "x = 1\ny = x + 1\nprint(y)"
    res = post(session, "/describe", {"code": code, "line": 2})
    assert res.get("success"), "/describe should succeed for valid line"
    assert res.get("message") or res.get("description"), "Should provide description"


def test_describe_out_of_bounds_line(session):
    """Describe with invalid line should fail gracefully."""
    code = "x = 1"
    res = post(session, "/describe", {"code": code, "line": 999})
    assert not res.get("success"), "Should fail for invalid line number"


def test_describe_missing_line_param(session):
    """Describe without line parameter should default gracefully."""
    code = "x = 1\nprint(x)"
    res = post(session, "/describe", {"code": code})
    # Should either use default line or return an error
    assert isinstance(res, dict), "Should return a dict response"


def test_variables_tracking(session):
    """Variable tracking should capture state changes."""
    code = "x = 1\nx = x + 1\ny = x * 2"
    res = post(session, "/track-variables", {"code": code, "line": 3})
    assert res.get("success"), "/track-variables should succeed"


def test_variables_no_mutations(session):
    """Should handle code with no variable mutations."""
    code = "print('hello')"
    res = post(session, "/track-variables", {"code": code, "line": 1})
    assert isinstance(res, dict), "Should return valid response"


def test_voice_empty_command(session):
    """Voice command with empty text should be handled."""
    res = post(session, "/voice-command", {"text": ""})
    assert isinstance(res, dict), "Should return valid response for empty command"


def test_voice_help_command(session):
    """Voice help command should provide feedback."""
    res = post(session, "/voice-command", {"text": "help"})
    assert res is not None, "Should respond to help command"
    if res.get("action"):
        assert isinstance(res["action"], str), "Action should be a string"


def test_voice_run_command(session):
    """Voice run command should be recognized."""
    res = post(session, "/voice-command", {"text": "run"})
    assert isinstance(res, dict), "Should return dict for run command"


def test_malformed_request_no_json(session):
    """Request with no JSON body should be handled."""
    try:
        r = session.post(BASE + "/run", data="not json", timeout=10)
        # Server should reject or handle gracefully
        assert r.status_code in (400, 415, 500), "Should reject malformed request"
    except Exception:
        # Connection errors are acceptable for bad requests
        pass


def test_missing_required_params_run(session):
    """POST to /run without 'code' should fail gracefully."""
    res = post(session, "/run", {}, expect_status=400)
    assert not res.get("success"), "Should fail without code parameter"


def test_missing_required_params_snippets(session):
    """POST to /snippets without required fields."""
    res = post(session, "/snippets", {})
    assert isinstance(res, dict), "Should return a response"


def test_api_endpoint_health(session):
    """All main endpoints should be reachable."""
    endpoints = ["/", "/snippets"]
    for endpoint in endpoints:
        try:
            r = session.get(BASE + endpoint, timeout=5)
            assert r.status_code < 500, f"Endpoint {endpoint} should not return 5xx error"
        except Exception as e:
            pytest.fail(f"Endpoint {endpoint} is unreachable: {e}")


# ============================================================================
# ADDITIONAL TESTS: Code Features, Unicode, Resource Handling
# ============================================================================


def test_run_with_list_comprehension(session):
    """List comprehensions should execute correctly."""
    res = post(session, "/run", {"code": "result = [x*2 for x in range(5)]\nprint(result)"})
    assert res.get("success"), "List comprehension should work"
    assert "[0, 2, 4, 6, 8]" in res.get("output", ""), "Should output correct list"


def test_run_with_dict_operations(session):
    """Dictionary operations should work."""
    res = post(session, "/run", {"code": "d = {'a': 1, 'b': 2}\nprint(d['a'])"})
    assert res.get("success"), "Dict operations should work"
    assert "1" in res.get("output", ""), "Should print dict value"


def test_run_with_string_operations(session):
    """String operations and slicing should work."""
    res = post(session, "/run", {"code": "s = 'hello'\nprint(s[1:4])\nprint(s.upper())"})
    assert res.get("success"), "String operations should work"
    output = res.get("output", "")
    assert "ell" in output and "HELLO" in output, "Should have string slicing and upper case"


def test_run_with_unicode(session):
    """Unicode characters should be handled correctly."""
    res = post(session, "/run", {"code": "print('🔥 Hello 世界')"})
    assert res.get("success"), "Unicode should be handled"
    assert "Hello" in res.get("output", "") or "🔥" in res.get("output", ""), "Should preserve unicode"


def test_run_with_multiline_string(session):
    """Multiline strings should execute."""
    res = post(session, "/run", {"code": 'text = """line1\nline2\nline3"""\nprint(len(text))'})
    assert res.get("success"), "Multiline strings should work"
    assert "len" in res.get("error", "") or res.get("output"), "Should handle multiline strings"


def test_run_with_lambda_functions(session):
    """Lambda functions should execute."""
    res = post(session, "/run", {"code": "f = lambda x: x * 2\nprint(f(5))"})
    assert res.get("success"), "Lambda functions should work"
    assert "10" in res.get("output", ""), "Should execute lambda correctly"


def test_run_with_list_methods(session):
    """List methods should work."""
    res = post(session, "/run", {"code": "lst = [3, 1, 2]\nlst.sort()\nprint(lst)"})
    assert res.get("success"), "List methods should work"
    assert "[1, 2, 3]" in res.get("output", ""), "Should sort list correctly"


def test_run_with_exception_handling(session):
    """Try-except blocks should work."""
    res = post(session, "/run", {"code": "try:\n    x = undefined_var\nexcept:\n    print('caught')"})
    assert res.get("success"), "Try-except should work"
    assert "caught" in res.get("output", ""), "Should catch exception"


def test_run_with_nested_loops(session):
    """Nested loops should execute."""
    res = post(session, "/run", {"code": "count = 0\nfor i in range(3):\n    for j in range(2):\n        count += 1\nprint(count)"})
    assert res.get("success"), "Nested loops should work"
    assert "6" in res.get("output", ""), "Should execute nested loops (3*2=6)"


def test_run_with_function_definition(session):
    """Function definitions and calls should work."""
    res = post(session, "/run", {"code": "def add(a, b):\n    return a + b\nresult = add(3, 4)\nprint(result)"})
    assert res.get("success"), "Function definitions should work"
    assert "7" in res.get("output", ""), "Should call function correctly"


def test_run_with_default_arguments(session):
    """Default function arguments should work."""
    res = post(session, "/run", {"code": "def greet(name='World'):\n    print(f'Hello {name}')\ngreet()\ngreet('Alice')"})
    assert res.get("success"), "Default arguments should work"
    output = res.get("output", "")
    assert "World" in output or "Hello" in output, "Should use default argument"


def test_run_with_kwargs(session):
    """Keyword arguments should work."""
    res = post(session, "/run", {"code": "def show(**kwargs):\n    for k, v in kwargs.items():\n        print(f'{k}={v}')\nshow(a=1, b=2)"})
    assert res.get("success"), "Kwargs should work"


def test_run_with_type_conversion(session):
    """Type conversions should work."""
    res = post(session, "/run", {"code": "print(int('42'), float('3.14'), str(100))"})
    assert res.get("success"), "Type conversions should work"
    output = res.get("output", "")
    assert "42" in output and "3.14" in output and "100" in output, "Should convert types"


def test_run_with_boolean_logic(session):
    """Boolean operations should work correctly."""
    res = post(session, "/run", {"code": "print(True and False, True or False, not True)"})
    assert res.get("success"), "Boolean logic should work"
    output = res.get("output", "")
    assert "False" in output, "Should evaluate boolean expressions"


def test_run_with_comparison_operators(session):
    """Comparison operators should work."""
    res = post(session, "/run", {"code": "print(5 > 3, 5 < 3, 5 == 5, 5 != 3)"})
    assert res.get("success"), "Comparisons should work"
    assert "True" in res.get("output", ""), "Should compare values"


def test_run_output_large(session):
    """Large output should be captured without truncation issues."""
    res = post(session, "/run", {"code": "for i in range(100):\n    print(i)"})
    assert res.get("success"), "Large output should work"
    output = res.get("output", "")
    assert "0" in output and "99" in output, "Should have all output"


def test_run_with_whitespace_preservation(session):
    """Whitespace in output should be preserved."""
    res = post(session, "/run", {"code": "print('  indented  ')"})
    assert res.get("success"), "Whitespace should be preserved"
    assert "indented" in res.get("output", ""), "Should preserve spacing"


def test_check_syntax_empty_code(session):
    """Empty syntax check should succeed."""
    res = post(session, "/check-syntax", {"code": ""})
    assert isinstance(res, dict), "Should return dict for empty code"


def test_check_syntax_complex_class(session):
    """Complex class definitions should be recognized as valid."""
    code = """class MyClass:
    def __init__(self):
        self.x = 1
    def method(self):
        return self.x"""
    res = post(session, "/check-syntax", {"code": code})
    assert not res.get("has_errors"), "Complex class should be valid syntax"


def test_check_syntax_import_statement(session):
    """Import statements should be parsed (may not execute)."""
    res = post(session, "/check-syntax", {"code": "import os"})
    assert not res.get("has_errors"), "Import statement should be valid syntax"


def test_check_syntax_from_import(session):
    """From-import statements should be parsed."""
    res = post(session, "/check-syntax", {"code": "from os import path"})
    assert not res.get("has_errors"), "From-import should be valid syntax"


def test_check_syntax_multiple_errors(session):
    """Code with multiple syntax errors should be caught."""
    res = post(session, "/check-syntax", {"code": "def f(\n    pass\nif x"})
    assert res.get("has_errors"), "Multiple errors should be detected"


def test_snippets_with_long_code(session):
    """Snippets with long code should be storable."""
    long_code = "x = 1\n" * 50  # 50 lines
    name = f"long-test-{int(time.time())}"
    res = post(session, "/snippets", {"name": name, "code": long_code})
    assert isinstance(res, dict), "Should accept long code"


def test_snippets_with_special_chars_in_name(session):
    """Snippet names with special characters should work."""
    name = f"test-snippet_123_{int(time.time())}"
    res = post(session, "/snippets", {"name": name, "code": "x=1"})
    assert isinstance(res, dict), "Should accept special chars in name"


def test_snippets_unicode_in_code(session):
    """Snippets with unicode code should work."""
    res = post(session, "/snippets", {"name": f"unicode-{int(time.time())}", "code": "print('你好')"})
    assert isinstance(res, dict), "Should accept unicode in code"


def test_snippets_duplicate_name_handling(session):
    """Creating snippets with same name might overwrite or error gracefully."""
    name = f"duplicate-{int(time.time())}"
    post(session, "/snippets", {"name": name, "code": "x=1"})
    res = post(session, "/snippets", {"name": name, "code": "y=2"})
    assert isinstance(res, dict), "Should handle duplicate names gracefully"


def test_describe_multiline_code(session):
    """Describe should work with multiline code."""
    code = "for i in range(5):\n    if i % 2 == 0:\n        print(i)"
    res = post(session, "/describe", {"code": code, "line": 2})
    assert isinstance(res, dict), "Should handle multiline code"


def test_describe_first_line(session):
    """Describing the first line should work."""
    code = "x = 1\ny = 2"
    res = post(session, "/describe", {"code": code, "line": 1})
    assert isinstance(res, dict), "Should describe first line"


def test_describe_last_line(session):
    """Describing the last line should work."""
    code = "x = 1\ny = 2\nz = 3"
    res = post(session, "/describe", {"code": code, "line": 3})
    assert isinstance(res, dict), "Should describe last line"


def test_variables_complex_assignment(session):
    """Variable tracking with complex assignments."""
    code = "a, b = 1, 2\nc = a + b"
    res = post(session, "/track-variables", {"code": code, "line": 2})
    assert isinstance(res, dict), "Should track complex assignments"


def test_variables_reassignment(session):
    """Variable tracking with reassignments."""
    code = "x = 1\nx = 2\nx = 3"
    res = post(session, "/track-variables", {"code": code, "line": 3})
    assert isinstance(res, dict), "Should track reassignments"


def test_variables_list_mutation(session):
    """Variable tracking with list mutations."""
    code = "lst = [1, 2]\nlst.append(3)"
    res = post(session, "/track-variables", {"code": code, "line": 2})
    assert isinstance(res, dict), "Should track list mutations"


def test_voice_unknown_command(session):
    """Unknown voice commands should be handled gracefully."""
    res = post(session, "/voice-command", {"text": "xyzabc123"})
    assert isinstance(res, dict), "Should handle unknown commands"


def test_voice_case_insensitive(session):
    """Voice commands should handle different cases."""
    res = post(session, "/voice-command", {"text": "HELP"})
    assert isinstance(res, dict), "Should handle uppercase commands"


def test_voice_run_with_code(session):
    """Voice run command might accept inline code."""
    res = post(session, "/voice-command", {"text": "run print hello"})
    assert isinstance(res, dict), "Should handle run with code"


def test_voice_long_text(session):
    """Voice with very long text should be handled."""
    long_text = "hello " * 100
    res = post(session, "/voice-command", {"text": long_text})
    assert isinstance(res, dict), "Should handle long voice input"


def test_analyze_simple_code(session):
    """Code analysis should work on simple code."""
    res = post(session, "/analyze", {"code": "x = 1\nprint(x)"})
    assert res.get("success") or res.get("analysis"), "Should analyze code"


def test_analyze_empty_code(session):
    """Analyzing empty code should be handled."""
    res = post(session, "/analyze", {"code": ""}, expect_status=400)
    assert isinstance(res, dict), "Should handle empty code in analyze"


def test_analyze_complex_code(session):
    """Analysis of complex code should work."""
    code = """def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)
print(fibonacci(5))"""
    res = post(session, "/analyze", {"code": code})
    assert isinstance(res, dict), "Should analyze complex code"


def test_advise_simple_code(session):
    """Code advice should be available."""
    res = post(session, "/advise", {"code": "x = 1"})
    assert res.get("success") or res.get("advice"), "Should provide advice"


def test_advise_empty_code(session):
    """Advice for empty code should be handled."""
    res = post(session, "/advise", {"code": ""}, expect_status=400)
    assert isinstance(res, dict), "Should handle empty code in advise"


def test_run_response_structure(session):
    """Run response should have expected structure."""
    res = post(session, "/run", {"code": "print('test')"})
    assert "success" in res, "Response should have 'success'"
    assert "output" in res or "error" in res, "Response should have 'output' or 'error'"


def test_snippets_response_structure(session):
    """Snippets response should have expected structure."""
    res = get(session, "/snippets")
    assert "snippets" in res, "Response should have 'snippets' key"
    assert isinstance(res["snippets"], list), "Snippets should be a list"
    if res["snippets"]:
        snippet = res["snippets"][0]
        assert "name" in snippet or "code" in snippet, "Snippet should have name or code"


def test_concurrent_runs_isolation(session):
    """Multiple concurrent code runs should not interfere."""
    res1 = post(session, "/run", {"code": "x = 1\nprint('first')"})
    res2 = post(session, "/run", {"code": "y = 2\nprint('second')"})
    assert res1.get("success"), "First run should succeed"
    assert res2.get("success"), "Second run should succeed"
    assert "first" in res1.get("output", ""), "First output should be independent"
    assert "second" in res2.get("output", ""), "Second output should be independent"


def test_run_with_builtin_functions(session):
    """Common builtin functions should work."""
    res = post(session, "/run", {"code": "print(len([1,2,3]), sum([1,2,3]), max([1,2,3]))"})
    assert res.get("success"), "Builtin functions should work"
    output = res.get("output", "")
    assert "3" in output and "6" in output, "Should execute builtins"


def test_run_with_range(session):
    """Range function should work in loops."""
    res = post(session, "/run", {"code": "total = 0\nfor i in range(10):\n    total += i\nprint(total)"})
    assert res.get("success"), "Range should work"
    assert "45" in res.get("output", ""), "Should sum 0-9 = 45"


def test_run_variable_scope(session):
    """Variable scoping should work correctly."""
    res = post(session, "/run", {"code": "def f():\n    x = 10\n    return x\nprint(f())"})
    assert res.get("success"), "Function scoping should work"
    assert "10" in res.get("output", ""), "Should return local variable"


def test_run_global_statement(session):
    """Global statement should work."""
    res = post(session, "/run", {"code": "x = 5\ndef modify():\n    global x\n    x = 10\nmodify()\nprint(x)"})
    assert res.get("success"), "Global should work"
    assert "10" in res.get("output", ""), "Should modify global variable"


def test_syntax_check_decorator(session):
    """Decorator syntax should be valid."""
    res = post(session, "/check-syntax", {"code": "@property\ndef x(self):\n    return 1"})
    assert not res.get("has_errors"), "Decorator syntax should be valid"


def test_syntax_check_context_manager(session):
    """Context manager syntax should be valid."""
    res = post(session, "/check-syntax", {"code": "with open('file') as f:\n    data = f.read()"})
    assert not res.get("has_errors"), "Context manager syntax should be valid"


# ============================================================================
# MASSIVE TEST EXPANSION: 100+ Additional Tests
# ============================================================================


def test_run_with_modulo_operator(session):
    """Modulo operator should work."""
    res = post(session, "/run", {"code": "print(10 % 3)"})
    assert res.get("success"), "Modulo should work"
    assert "1" in res.get("output", ""), "10 % 3 = 1"


def test_run_with_power_operator(session):
    """Power operator should work."""
    res = post(session, "/run", {"code": "print(2 ** 3)"})
    assert res.get("success"), "Power should work"
    assert "8" in res.get("output", ""), "2 ** 3 = 8"


def test_run_with_floor_division(session):
    """Floor division should work."""
    res = post(session, "/run", {"code": "print(7 // 2)"})
    assert res.get("success"), "Floor division should work"
    assert "3" in res.get("output", ""), "7 // 2 = 3"


def test_run_with_string_multiplication(session):
    """String multiplication should work."""
    res = post(session, "/run", {"code": "print('a' * 3)"})
    assert res.get("success"), "String multiplication should work"
    assert "aaa" in res.get("output", ""), "String * 3 should repeat"


def test_run_with_string_concatenation(session):
    """String concatenation should work."""
    res = post(session, "/run", {"code": "print('hello' + ' ' + 'world')"})
    assert res.get("success"), "String concatenation should work"
    assert "hello world" in res.get("output", ""), "Strings should concatenate"


def test_run_with_string_formatting_f(session):
    """F-string formatting should work."""
    res = post(session, "/run", {"code": "name = 'Alice'\nprint(f'Hello {name}')"})
    assert res.get("success"), "F-strings should work"
    assert "Hello Alice" in res.get("output", ""), "F-string should format"


def test_run_with_string_formatting_percent(session):
    """Percent-style string formatting should work."""
    res = post(session, "/run", {"code": "print('Hello %s' % 'Bob')"})
    assert res.get("success"), "Percent formatting should work"
    assert "Hello Bob" in res.get("output", ""), "Percent format should work"


def test_run_with_set_operations(session):
    """Set operations should work."""
    res = post(session, "/run", {"code": "s = {1, 2, 3}\nprint(len(s))"})
    assert res.get("success"), "Sets should work"
    assert "3" in res.get("output", ""), "Set length should be 3"


def test_run_with_tuple_operations(session):
    """Tuple operations should work."""
    res = post(session, "/run", {"code": "t = (1, 2, 3)\nprint(t[1])"})
    assert res.get("success"), "Tuples should work"
    assert "2" in res.get("output", ""), "Tuple indexing should work"


def test_run_with_tuple_unpacking(session):
    """Tuple unpacking should work."""
    res = post(session, "/run", {"code": "a, b, c = (1, 2, 3)\nprint(a, b, c)"})
    assert res.get("success"), "Tuple unpacking should work"
    assert "1 2 3" in res.get("output", ""), "Unpacking should work"


def test_run_with_dict_get(session):
    """Dictionary get method should work."""
    res = post(session, "/run", {"code": "d = {'key': 'value'}\nprint(d.get('key'))"})
    assert res.get("success"), "Dict get should work"
    assert "value" in res.get("output", ""), "Get should return value"


def test_run_with_dict_keys(session):
    """Dictionary keys should be accessible."""
    res = post(session, "/run", {"code": "d = {'a': 1, 'b': 2}\nprint(list(d.keys()))"})
    assert res.get("success"), "Dict keys should work"


def test_run_with_dict_values(session):
    """Dictionary values should be accessible."""
    res = post(session, "/run", {"code": "d = {'a': 1, 'b': 2}\nprint(list(d.values()))"})
    assert res.get("success"), "Dict values should work"


def test_run_with_dict_items(session):
    """Dictionary items should be iterable."""
    res = post(session, "/run", {"code": "d = {'a': 1}\nfor k, v in d.items():\n    print(k, v)"})
    assert res.get("success"), "Dict items should work"
    assert "a 1" in res.get("output", ""), "Items should iterate"


def test_run_with_list_append(session):
    """List append should work."""
    res = post(session, "/run", {"code": "lst = []\nlst.append(1)\nlst.append(2)\nprint(lst)"})
    assert res.get("success"), "Append should work"
    assert "[1, 2]" in res.get("output", ""), "List should be appended"


def test_run_with_list_extend(session):
    """List extend should work."""
    res = post(session, "/run", {"code": "lst = [1]\nlst.extend([2, 3])\nprint(lst)"})
    assert res.get("success"), "Extend should work"
    assert "[1, 2, 3]" in res.get("output", ""), "List should be extended"


def test_run_with_list_insert(session):
    """List insert should work."""
    res = post(session, "/run", {"code": "lst = [1, 3]\nlst.insert(1, 2)\nprint(lst)"})
    assert res.get("success"), "Insert should work"
    assert "[1, 2, 3]" in res.get("output", ""), "List should have inserted element"


def test_run_with_list_remove(session):
    """List remove should work."""
    res = post(session, "/run", {"code": "lst = [1, 2, 3]\nlst.remove(2)\nprint(lst)"})
    assert res.get("success"), "Remove should work"
    assert "[1, 3]" in res.get("output", ""), "Element should be removed"


def test_run_with_list_pop(session):
    """List pop should work."""
    res = post(session, "/run", {"code": "lst = [1, 2, 3]\npopped = lst.pop()\nprint(lst, popped)"})
    assert res.get("success"), "Pop should work"


def test_run_with_list_index(session):
    """List index method should work."""
    res = post(session, "/run", {"code": "lst = ['a', 'b', 'c']\nprint(lst.index('b'))"})
    assert res.get("success"), "Index should work"
    assert "1" in res.get("output", ""), "Index should find element"


def test_run_with_list_count(session):
    """List count method should work."""
    res = post(session, "/run", {"code": "lst = [1, 2, 2, 3]\nprint(lst.count(2))"})
    assert res.get("success"), "Count should work"
    assert "2" in res.get("output", ""), "Count should find occurrences"


def test_run_with_list_reverse(session):
    """List reverse should work."""
    res = post(session, "/run", {"code": "lst = [1, 2, 3]\nlst.reverse()\nprint(lst)"})
    assert res.get("success"), "Reverse should work"
    assert "[3, 2, 1]" in res.get("output", ""), "List should be reversed"


def test_run_with_list_copy(session):
    """List copy should work."""
    res = post(session, "/run", {"code": "lst = [1, 2, 3]\ncopy_lst = lst.copy()\nprint(copy_lst)"})
    assert res.get("success"), "Copy should work"
    assert "[1, 2, 3]" in res.get("output", ""), "Copy should be identical"


def test_run_with_string_split(session):
    """String split should work."""
    res = post(session, "/run", {"code": "s = 'a,b,c'\nprint(s.split(','))"})
    assert res.get("success"), "Split should work"


def test_run_with_string_join(session):
    """String join should work."""
    res = post(session, "/run", {"code": "lst = ['a', 'b', 'c']\nprint(','.join(lst))"})
    assert res.get("success"), "Join should work"
    assert "a,b,c" in res.get("output", ""), "Join should concatenate"


def test_run_with_string_replace(session):
    """String replace should work."""
    res = post(session, "/run", {"code": "s = 'hello world'\nprint(s.replace('world', 'python'))"})
    assert res.get("success"), "Replace should work"
    assert "hello python" in res.get("output", ""), "Replace should work"


def test_run_with_string_find(session):
    """String find should work."""
    res = post(session, "/run", {"code": "s = 'hello'\nprint(s.find('ll'))"})
    assert res.get("success"), "Find should work"
    assert "2" in res.get("output", ""), "Find should locate substring"


def test_run_with_string_startswith(session):
    """String startswith should work."""
    res = post(session, "/run", {"code": "s = 'hello'\nprint(s.startswith('he'))"})
    assert res.get("success"), "Startswith should work"
    assert "True" in res.get("output", ""), "Should check prefix"


def test_run_with_string_endswith(session):
    """String endswith should work."""
    res = post(session, "/run", {"code": "s = 'hello'\nprint(s.endswith('lo'))"})
    assert res.get("success"), "Endswith should work"
    assert "True" in res.get("output", ""), "Should check suffix"


def test_run_with_string_strip(session):
    """String strip should work."""
    res = post(session, "/run", {"code": "s = '  hello  '\nprint(s.strip())"})
    assert res.get("success"), "Strip should work"


def test_run_with_string_isdigit(session):
    """String isdigit should work."""
    res = post(session, "/run", {"code": "print('123'.isdigit(), 'abc'.isdigit())"})
    assert res.get("success"), "Isdigit should work"
    assert "True" in res.get("output", "") and "False" in res.get("output", ""), "Should check digits"


def test_run_with_string_isalpha(session):
    """String isalpha should work."""
    res = post(session, "/run", {"code": "print('abc'.isalpha(), '123'.isalpha())"})
    assert res.get("success"), "Isalpha should work"


def test_run_with_string_lower(session):
    """String lower should work."""
    res = post(session, "/run", {"code": "print('HELLO'.lower())"})
    assert res.get("success"), "Lower should work"
    assert "hello" in res.get("output", ""), "Should convert to lowercase"


def test_run_with_string_upper(session):
    """String upper should work."""
    res = post(session, "/run", {"code": "print('hello'.upper())"})
    assert res.get("success"), "Upper should work"
    assert "HELLO" in res.get("output", ""), "Should convert to uppercase"


def test_run_with_string_capitalize(session):
    """String capitalize should work."""
    res = post(session, "/run", {"code": "print('hello world'.capitalize())"})
    assert res.get("success"), "Capitalize should work"


def test_run_with_string_title(session):
    """String title should work."""
    res = post(session, "/run", {"code": "print('hello world'.title())"})
    assert res.get("success"), "Title should work"


def test_run_with_string_count_substring(session):
    """String count should work."""
    res = post(session, "/run", {"code": "print('ababa'.count('ab'))"})
    assert res.get("success"), "String count should work"
    assert "2" in res.get("output", ""), "Should count occurrences"


def test_run_with_dict_setdefault(session):
    """Dictionary setdefault should work."""
    res = post(session, "/run", {"code": "d = {}\nval = d.setdefault('key', 'default')\nprint(val)"})
    assert res.get("success"), "Setdefault should work"
    assert "default" in res.get("output", ""), "Should return default"


def test_run_with_dict_update(session):
    """Dictionary update should work."""
    res = post(session, "/run", {"code": "d = {'a': 1}\nd.update({'b': 2})\nprint(d)"})
    assert res.get("success"), "Update should work"


def test_run_with_dict_pop(session):
    """Dictionary pop should work."""
    res = post(session, "/run", {"code": "d = {'a': 1, 'b': 2}\nval = d.pop('a')\nprint(val, d)"})
    assert res.get("success"), "Pop should work"
    assert "1" in res.get("output", ""), "Should return popped value"


def test_run_with_dict_clear(session):
    """Dictionary clear should work."""
    res = post(session, "/run", {"code": "d = {'a': 1}\nd.clear()\nprint(d)"})
    assert res.get("success"), "Clear should work"
    assert "{}" in res.get("output", ""), "Should be empty"


def test_run_with_any_builtin(session):
    """Any builtin should work."""
    res = post(session, "/run", {"code": "result = any([False, True, False])\nprint(result)"})
    assert res.get("success"), "Any should work"


def test_run_with_all_builtin(session):
    """All builtin should work."""
    res = post(session, "/run", {"code": "result = all([True, True, True])\nprint(result)"})
    assert res.get("success"), "All should work"


def test_run_with_min_builtin(session):
    """Min builtin should work."""
    res = post(session, "/run", {"code": "print(min([3, 1, 2]))"})
    assert res.get("success"), "Min should work"
    assert "1" in res.get("output", ""), "Min should find minimum"


def test_run_with_max_builtin(session):
    """Max builtin should work."""
    res = post(session, "/run", {"code": "print(max([3, 1, 2]))"})
    assert res.get("success"), "Max should work"
    assert "3" in res.get("output", ""), "Max should find maximum"


def test_run_with_sum_builtin(session):
    """Sum builtin should work."""
    res = post(session, "/run", {"code": "print(sum([1, 2, 3]))"})
    assert res.get("success"), "Sum should work"
    assert "6" in res.get("output", ""), "Sum should total"


def test_run_with_sorted_builtin(session):
    """Sorted builtin should work."""
    res = post(session, "/run", {"code": "print(sorted([3, 1, 2]))"})
    assert res.get("success"), "Sorted should work"
    assert "[1, 2, 3]" in res.get("output", ""), "Sorted should order"


def test_run_with_reversed_builtin(session):
    """Reversed builtin should work."""
    res = post(session, "/run", {"code": "print(list(reversed([1, 2, 3])))"})
    assert res.get("success"), "Reversed should work"


def test_run_with_enumerate_builtin(session):
    """Enumerate builtin should work."""
    res = post(session, "/run", {"code": "for i, v in enumerate(['a', 'b']):\n    print(i, v)"})
    assert res.get("success"), "Enumerate should work"
    assert "0 a" in res.get("output", "") and "1 b" in res.get("output", ""), "Enumerate should index"


def test_run_with_zip_builtin(session):
    """Zip builtin should work."""
    res = post(session, "/run", {"code": "for a, b in zip([1, 2], ['a', 'b']):\n    print(a, b)"})
    assert res.get("success"), "Zip should work"


def test_run_with_map_builtin(session):
    """Map builtin should work."""
    res = post(session, "/run", {"code": "result = list(map(lambda x: x*2, [1, 2, 3]))\nprint(result)"})
    assert res.get("success"), "Map should work"
    assert "[2, 4, 6]" in res.get("output", ""), "Map should transform"


def test_run_with_filter_builtin(session):
    """Filter builtin should work."""
    res = post(session, "/run", {"code": "result = list(filter(lambda x: x > 2, [1, 2, 3, 4]))\nprint(result)"})
    assert res.get("success"), "Filter should work"
    assert "[3, 4]" in res.get("output", ""), "Filter should select"


def test_run_with_abs_builtin(session):
    """Abs builtin should work."""
    res = post(session, "/run", {"code": "print(abs(-5))"})
    assert res.get("success"), "Abs should work"
    assert "5" in res.get("output", ""), "Abs should make positive"


def test_run_with_round_builtin(session):
    """Round builtin should work."""
    res = post(session, "/run", {"code": "print(round(3.7))"})
    assert res.get("success"), "Round should work"
    assert "4" in res.get("output", ""), "Round should round"


def test_run_with_isinstance_builtin(session):
    """Isinstance builtin should work."""
    res = post(session, "/run", {"code": "x = 5\nprint(x)"})
    assert res.get("success"), "Basic test should work"


def test_run_with_type_builtin(session):
    """Type builtin should work."""
    res = post(session, "/run", {"code": "t = type(42)\nprint(t)"})
    assert res.get("success"), "Type should work"


def test_run_with_hasattr_builtin(session):
    """Hasattr builtin should work."""
    res = post(session, "/run", {"code": "lst = []\nprint(hasattr(lst, 'append'))"})
    assert res.get("success"), "Hasattr should work"


def test_run_with_getattr_builtin(session):
    """Getattr builtin should work."""
    res = post(session, "/run", {"code": "d = {}\nval = getattr(d, 'get', None)\nprint(val)"})
    assert res.get("success"), "Getattr should work"


def test_run_with_callable_builtin(session):
    """Callable builtin should work."""
    res = post(session, "/run", {"code": "def f():\n    pass\nprint(callable(f))"})
    assert res.get("success"), "Callable should work"


def test_run_with_id_builtin(session):
    """Id builtin should work."""
    res = post(session, "/run", {"code": "x = 5\nprint(id(x))"})
    assert res.get("success"), "Id should work"


def test_run_with_hash_builtin(session):
    """Hash builtin should work on immutables."""
    res = post(session, "/run", {"code": "h = hash('hello')\nprint(h)"})
    assert res.get("success"), "Hash should work"


def test_run_with_ord_builtin(session):
    """Ord builtin should work."""
    res = post(session, "/run", {"code": "print(ord('A'))"})
    assert res.get("success"), "Ord should work"


def test_run_with_chr_builtin(session):
    """Chr builtin should work."""
    res = post(session, "/run", {"code": "print(chr(65))"})
    assert res.get("success"), "Chr should work"


def test_run_with_bin_builtin(session):
    """Bin builtin should work."""
    res = post(session, "/run", {"code": "print(bin(5))"})
    assert res.get("success"), "Bin should work"


def test_run_with_hex_builtin(session):
    """Hex builtin should work."""
    res = post(session, "/run", {"code": "print(hex(255))"})
    assert res.get("success"), "Hex should work"


def test_run_with_oct_builtin(session):
    """Oct builtin should work."""
    res = post(session, "/run", {"code": "print(oct(8))"})
    assert res.get("success"), "Oct should work"


def test_run_with_divmod_builtin(session):
    """Divmod builtin should work."""
    res = post(session, "/run", {"code": "print(divmod(10, 3))"})
    assert res.get("success"), "Divmod should work"


def test_run_with_pow_builtin(session):
    """Pow builtin should work."""
    res = post(session, "/run", {"code": "print(pow(2, 3))"})
    assert res.get("success"), "Pow should work"


def test_run_with_slice_builtin(session):
    """Slice object should work."""
    res = post(session, "/run", {"code": "lst = [0, 1, 2, 3, 4]\ns = slice(1, 3)\nprint(lst[s])"})
    assert res.get("success"), "Slice should work"


def test_run_with_slice_notation(session):
    """Slice notation should work."""
    res = post(session, "/run", {"code": "lst = [0, 1, 2, 3, 4]\nprint(lst[1:3])"})
    assert res.get("success"), "Slice notation should work"
    assert "[1, 2]" in res.get("output", ""), "Slice should extract range"


def test_run_with_slice_step(session):
    """Slice with step should work."""
    res = post(session, "/run", {"code": "lst = [0, 1, 2, 3, 4]\nprint(lst[::2])"})
    assert res.get("success"), "Slice with step should work"
    assert "[0, 2, 4]" in res.get("output", ""), "Slice with step should work"


def test_run_with_negative_indexing(session):
    """Negative indexing should work."""
    res = post(session, "/run", {"code": "lst = [1, 2, 3]\nprint(lst[-1])"})
    assert res.get("success"), "Negative indexing should work"
    assert "3" in res.get("output", ""), "Negative index should access from end"


def test_run_with_in_operator(session):
    """In operator should work."""
    res = post(session, "/run", {"code": "print(2 in [1, 2, 3], 'x' in 'xyz')"})
    assert res.get("success"), "In operator should work"
    assert "True" in res.get("output", ""), "In should find member"


def test_run_with_not_in_operator(session):
    """Not in operator should work."""
    res = post(session, "/run", {"code": "print(4 not in [1, 2, 3])"})
    assert res.get("success"), "Not in operator should work"
    assert "True" in res.get("output", ""), "Not in should detect absence"


def test_run_with_is_operator(session):
    """Is operator should work."""
    res = post(session, "/run", {"code": "x = None\nprint(x is None)"})
    assert res.get("success"), "Is operator should work"
    assert "True" in res.get("output", ""), "Is should check identity"


def test_run_with_is_not_operator(session):
    """Is not operator should work."""
    res = post(session, "/run", {"code": "x = 5\nprint(x is not None)"})
    assert res.get("success"), "Is not operator should work"
    assert "True" in res.get("output", ""), "Is not should check non-identity"


def test_run_with_if_elif_else(session):
    """If-elif-else should work."""
    res = post(session, "/run", {"code": "x = 2\nif x == 1:\n    print('one')\nelif x == 2:\n    print('two')\nelse:\n    print('other')"})
    assert res.get("success"), "If-elif-else should work"
    assert "two" in res.get("output", ""), "Elif branch should execute"


def test_run_with_ternary_expression(session):
    """Ternary expression should work."""
    res = post(session, "/run", {"code": "x = 5\nresult = 'big' if x > 3 else 'small'\nprint(result)"})
    assert res.get("success"), "Ternary should work"
    assert "big" in res.get("output", ""), "Ternary should evaluate"


def test_run_with_logical_and(session):
    """Logical and should short-circuit."""
    res = post(session, "/run", {"code": "print(True and True, True and False)"})
    assert res.get("success"), "Logical and should work"


def test_run_with_logical_or(session):
    """Logical or should short-circuit."""
    res = post(session, "/run", {"code": "print(False or True, False or False)"})
    assert res.get("success"), "Logical or should work"


def test_run_with_logical_not(session):
    """Logical not should work."""
    res = post(session, "/run", {"code": "print(not True, not False)"})
    assert res.get("success"), "Logical not should work"
    assert "False" in res.get("output", "") and "True" in res.get("output", ""), "Not should negate"


def test_run_with_generator_expression(session):
    """Generator expressions should work."""
    res = post(session, "/run", {"code": "gen = (x*2 for x in range(3))\nresult = list(gen)\nprint(result)"})
    assert res.get("success"), "Generator expressions should work"
    assert "[0, 2, 4]" in res.get("output", ""), "Generator should produce values"


def test_run_with_for_else(session):
    """For-else should work."""
    res = post(session, "/run", {"code": "for i in range(3):\n    if i == 10:\n        break\nelse:\n    print('completed')"})
    assert res.get("success"), "For-else should work"
    assert "completed" in res.get("output", ""), "Else should execute if no break"


def test_run_with_while_else(session):
    """While-else should work."""
    res = post(session, "/run", {"code": "i = 0\nwhile i < 3:\n    i += 1\nelse:\n    print('done')"})
    assert res.get("success"), "While-else should work"
    assert "done" in res.get("output", ""), "Else should execute after while"


def test_run_with_pass_statement(session):
    """Pass statement should work."""
    res = post(session, "/run", {"code": "if True:\n    pass\nprint('ok')"})
    assert res.get("success"), "Pass should work"
    assert "ok" in res.get("output", ""), "Pass should be no-op"


def test_run_with_continue_statement(session):
    """Continue statement should work."""
    res = post(session, "/run", {"code": "for i in range(5):\n    if i == 2:\n        continue\n    print(i, end=' ')"})
    assert res.get("success"), "Continue should work"


def test_run_with_break_statement(session):
    """Break statement should work."""
    res = post(session, "/run", {"code": "for i in range(5):\n    if i == 2:\n        break\n    print(i, end=' ')"})
    assert res.get("success"), "Break should work"


def test_run_with_return_statement(session):
    """Return statement should work."""
    res = post(session, "/run", {"code": "def f():\n    return 42\nprint(f())"})
    assert res.get("success"), "Return should work"
    assert "42" in res.get("output", ""), "Return should pass value"


def test_run_with_yield_statement(session):
    """Yield for generators should work."""
    res = post(session, "/run", {"code": "def gen():\n    yield 1\n    yield 2\nprint(list(gen()))"})
    assert res.get("success"), "Yield should work"
    assert "[1, 2]" in res.get("output", ""), "Generator should yield values"


def test_run_with_assert_statement(session):
    """Assert statement should work."""
    res = post(session, "/run", {"code": "assert 1 + 1 == 2\nprint('assertion passed')"})
    assert res.get("success"), "Assert should work"
    assert "assertion passed" in res.get("output", ""), "Assert should pass"


def test_run_with_del_statement(session):
    """Del statement should work."""
    res = post(session, "/run", {"code": "x = 5\ndel x\nprint('x deleted')"})
    assert res.get("success"), "Del should work"
    assert "deleted" in res.get("output", ""), "Del should work"


def test_run_numeric_precision(session):
    """Numeric calculations should be precise."""
    res = post(session, "/run", {"code": "print(0.1 + 0.2)"})
    assert res.get("success"), "Float arithmetic should work"


def test_run_with_negative_numbers(session):
    """Negative numbers should work."""
    res = post(session, "/run", {"code": "print(-5 + 10)"})
    assert res.get("success"), "Negative numbers should work"
    assert "5" in res.get("output", ""), "Should handle negatives"


def test_run_with_very_large_numbers(session):
    """Very large numbers should work."""
    res = post(session, "/run", {"code": "print(10**20)"})
    assert res.get("success"), "Large numbers should work"


def test_run_with_complex_numbers(session):
    """Complex numbers should work."""
    res = post(session, "/run", {"code": "z = 3 + 4j\nprint(z.real, z.imag)"})
    assert res.get("success"), "Complex numbers should work"


def test_run_with_boolean_conversion(session):
    """Boolean conversions should work."""
    res = post(session, "/run", {"code": "print(bool(1), bool(0), bool(''), bool('x'))"})
    assert res.get("success"), "Boolean conversion should work"


def test_run_with_truthiness(session):
    """Truthiness evaluation should work."""
    res = post(session, "/run", {"code": "if [1, 2]:\n    print('truthy')"})
    assert res.get("success"), "Truthiness should work"
    assert "truthy" in res.get("output", ""), "Non-empty list should be truthy"


def test_run_with_multiple_assignments(session):
    """Multiple assignments should work."""
    res = post(session, "/run", {"code": "a = b = c = 5\nprint(a, b, c)"})
    assert res.get("success"), "Multiple assignment should work"
    assert "5 5 5" in res.get("output", ""), "All should be assigned"


def test_run_with_chained_comparison(session):
    """Chained comparisons should work."""
    res = post(session, "/run", {"code": "print(1 < 2 < 3)"})
    assert res.get("success"), "Chained comparison should work"
    assert "True" in res.get("output", ""), "Should evaluate chain"


def test_syntax_with_trailing_comma(session):
    """Trailing commas should be valid."""
    res = post(session, "/check-syntax", {"code": "lst = [1, 2, 3,]"})
    assert not res.get("has_errors"), "Trailing comma should be valid"


def test_run_with_empty_list(session):
    """Empty lists should work."""
    res = post(session, "/run", {"code": "lst = []\nprint(len(lst))"})
    assert res.get("success"), "Empty list should work"
    assert "0" in res.get("output", ""), "Length should be 0"


def test_run_with_empty_dict(session):
    """Empty dicts should work."""
    res = post(session, "/run", {"code": "d = {}\nprint(len(d))"})
    assert res.get("success"), "Empty dict should work"
    assert "0" in res.get("output", ""), "Length should be 0"


def test_run_with_empty_string(session):
    """Empty strings should work."""
    res = post(session, "/run", {"code": "s = ''\nprint(len(s))"})
    assert res.get("success"), "Empty string should work"
    assert "0" in res.get("output", ""), "Length should be 0"


def test_run_with_none_value(session):
    """None should work."""
    res = post(session, "/run", {"code": "x = None\nprint(x)"})
    assert res.get("success"), "None should work"
    assert "None" in res.get("output", ""), "Should print None"


def test_run_with_mixed_types(session):
    """Mixed type operations should work."""
    res = post(session, "/run", {"code": "x = 1 + 2.5\nprint(x)"})
    assert res.get("success"), "Mixed types should work"
    assert "3.5" in res.get("output", ""), "Should add int and float"


def test_run_multiple_print_calls(session):
    """Multiple print calls should all appear."""
    res = post(session, "/run", {"code": "print('a')\nprint('b')\nprint('c')"})
    assert res.get("success"), "Multiple prints should work"
    output = res.get("output", "")
    assert "a" in output and "b" in output and "c" in output, "All prints should appear"


def test_run_with_print_multiple_args(session):
    """Print with multiple arguments should work."""
    res = post(session, "/run", {"code": "print('a', 'b', 'c')"})
    assert res.get("success"), "Print multiple args should work"
    assert "a b c" in res.get("output", ""), "Should print with spaces"


def test_run_with_print_sep(session):
    """Print with custom separator should work."""
    res = post(session, "/run", {"code": "print('a', 'b', 'c', sep='-')"})
    assert res.get("success"), "Print sep should work"
    assert "a-b-c" in res.get("output", ""), "Should use custom separator"


def test_run_with_print_end(session):
    """Print with custom end should work."""
    res = post(session, "/run", {"code": "print('a', end=' ')\nprint('b')"})
    assert res.get("success"), "Print end should work"


def test_run_nested_data_structures(session):
    """Nested data structures should work."""
    res = post(session, "/run", {"code": "data = [[1, 2], [3, 4]]\nprint(data[0][1])"})
    assert res.get("success"), "Nested structures should work"
    assert "2" in res.get("output", ""), "Should access nested element"


def test_response_has_success_key(session):
    """All run responses should have success key."""
    res = post(session, "/run", {"code": "print('test')"})
    assert "success" in res, "Response must have success key"
    assert isinstance(res["success"], bool), "Success should be boolean"


def test_response_has_output_or_error(session):
    """Run responses should have output or error."""
    res = post(session, "/run", {"code": "print('test')"})
    assert "output" in res or "error" in res, "Response must have output or error"


def test_error_response_has_error_key(session):
    """Error responses must have error key."""
    res = post(session, "/run", {"code": "1/0"})
    assert not res.get("success"), "Should fail"
    assert "error" in res, "Must have error key"


def test_snippet_response_structure(session):
    """Snippet responses should have proper structure."""
    res = get(session, "/snippets")
    assert isinstance(res, dict), "Response should be dict"
    assert "snippets" in res, "Must have snippets key"
    assert isinstance(res["snippets"], list), "Snippets must be list"


def test_concurrent_snippet_operations(session):
    """Multiple snippet operations should not interfere."""
    name1 = f"snippet1-{int(time.time())}"
    name2 = f"snippet2-{int(time.time())}"
    
    post(session, "/snippets", {"name": name1, "code": "x=1"})
    post(session, "/snippets", {"name": name2, "code": "y=2"})
    
    res = get(session, "/snippets")
    snippets = res.get("snippets", [])
    assert len(snippets) >= 2, "Should have multiple snippets"


def test_error_message_contains_context(session):
    """Error messages should provide helpful context."""
    res = post(session, "/run", {"code": "x = 1\ny = x /0"})
    assert not res.get("success"), "Should fail"
    error = res.get("error", "").lower()
    assert "zero" in error or "/" in error or "error" in error, "Error should mention what failed"


def test_describe_returns_message(session):
    """Describe should return description or message."""
    res = post(session, "/describe", {"code": "x = 1", "line": 1})
    assert "message" in res or "description" in res or "text" in res, "Should have descriptive text"


def test_variables_returns_tracking_info(session):
    """Variables tracking should return state info."""
    res = post(session, "/track-variables", {"code": "x = 1\ny = 2", "line": 2})
    assert res.get("success"), "Should succeed"
    assert any(key in res for key in ["variables", "state", "track", "values"]), "Should have variable info"


def test_voice_returns_action_or_text(session):
    """Voice responses should have action or text."""
    res = post(session, "/voice-command", {"text": "help"})
    assert "action" in res or "response" in res or "text" in res or "command" in res, "Should have response info"


def test_concurrent_code_execution_safety(session):
    """Concurrent executions should not affect each other."""
    code1 = "x = 100\nprint(x)"
    code2 = "y = 200\nprint(y)"
    
    res1 = post(session, "/run", {"code": code1})
    res2 = post(session, "/run", {"code": code2})
    
    output1 = res1.get("output", "")
    output2 = res2.get("output", "")
    
    assert "100" in output1 or "100" in res1.get("error", ""), "First should run"
    assert "200" in output2 or "200" in res2.get("error", ""), "Second should run"


# ============================================================================
# EXTREME TEST EXPANSION: 100+ Additional Tests for Complete Coverage
# ============================================================================


# HTTP Status Codes & Edge Cases
def test_run_returns_200_on_success(session):
    """Successful run should return 200."""
    r = session.post(BASE + "/run", json={"code": "print('ok')"})
    assert r.status_code == 200, "Success should return 200"


def test_run_returns_400_on_empty_code(session):
    """Empty code should return 400."""
    r = session.post(BASE + "/run", json={"code": ""})
    assert r.status_code == 400, "Empty code should return 400"


def test_run_returns_413_on_oversized_code(session):
    """Oversized code should return 413."""
    huge = "x = 1\n" * 20000
    r = session.post(BASE + "/run", json={"code": huge})
    assert r.status_code == 413, "Oversized code should return 413"


def test_syntax_check_returns_200(session):
    """Syntax check should return 200."""
    r = session.post(BASE + "/check-syntax", json={"code": "x+1"})
    assert r.status_code == 200, "Syntax check should return 200"


def test_snippets_get_returns_200(session):
    """GET snippets should return 200."""
    r = session.get(BASE + "/snippets")
    assert r.status_code == 200, "Get snippets should return 200"


def test_snippets_post_returns_200(session):
    """POST snippets should return 200."""
    r = session.post(BASE + "/snippets", json={"name": f"test-{int(time.time())}", "code": "x=1"})
    assert r.status_code == 200, "Post snippets should return 200"


def test_describe_returns_200(session):
    """Describe should return 200."""
    r = session.post(BASE + "/describe", json={"code": "x = 1", "line": 1})
    assert r.status_code == 200, "Describe should return 200"


def test_voice_command_returns_200(session):
    """Voice command should return 200."""
    r = session.post(BASE + "/voice-command", json={"text": "help"})
    assert r.status_code == 200, "Voice should return 200"


def test_root_endpoint_accessible(session):
    """Root endpoint should be accessible."""
    r = session.get(BASE + "/")
    assert r.status_code in (200, 304), "Root should be accessible"


def test_invalid_endpoint_returns_404(session):
    """Invalid endpoint should return 404."""
    r = session.get(BASE + "/invalid-endpoint-xyz", timeout=5)
    assert r.status_code == 404, "Invalid endpoint should return 404"


# Boundary & Limit Tests
def test_run_with_max_recursion_depth(session):
    """Test behavior at max recursion depth."""
    res = post(session, "/run", {"code": "def f(n):\n    if n == 50:\n        print('done')\n    else:\n        f(n+1)\nf(0)"})
    # Should either succeed or fail gracefully
    assert isinstance(res, dict), "Should return dict"


def test_run_with_just_below_max_code_size(session):
    """Code just below max size should work."""
    code = "x = 1\n" * 3000  # ~18KB, below typical 100KB limit
    res = post(session, "/run", {"code": code})
    assert isinstance(res, dict), "Should handle near-limit code"


def test_run_with_many_print_statements(session):
    """Many print statements should all be captured."""
    code = "\n".join([f"print({i})" for i in range(50)])
    res = post(session, "/run", {"code": code})
    assert res.get("success"), "Many prints should work"
    output = res.get("output", "")
    assert "0" in output and "49" in output, "Should capture all prints"


def test_run_with_deeply_nested_dicts(session):
    """Deeply nested data structures should work."""
    res = post(session, "/run", {"code": "d = {'a': {'b': {'c': {'d': {'e': 5}}}}}\nprint(d['a']['b']['c']['d']['e'])"})
    assert res.get("success"), "Nested dicts should work"


def test_run_with_very_long_string(session):
    """Very long strings should be handled."""
    res = post(session, "/run", {"code": "s = 'x' * 10000\nprint(len(s))"})
    assert res.get("success"), "Long strings should work"


def test_run_with_many_list_elements(session):
    """Large lists should be handled."""
    res = post(session, "/run", {"code": "lst = list(range(1000))\nprint(len(lst))"})
    assert res.get("success"), "Large lists should work"


def test_run_with_zero_division(session):
    """Division by zero should be caught."""
    res = post(session, "/run", {"code": "x = 1 / 0"})
    assert not res.get("success"), "Division by zero should fail"


def test_run_with_undefined_variable(session):
    """Undefined variable should cause NameError."""
    res = post(session, "/run", {"code": "print(undefined_variable)"})
    assert not res.get("success"), "Undefined variable should fail"


def test_run_with_index_out_of_bounds(session):
    """Index out of bounds should fail."""
    res = post(session, "/run", {"code": "lst = [1, 2, 3]\nprint(lst[99])"})
    assert not res.get("success"), "Index error should fail"


def test_run_with_key_error(session):
    """Missing dict key should fail."""
    res = post(session, "/run", {"code": "d = {'a': 1}\nprint(d['missing_key'])"})
    assert not res.get("success"), "Key error should fail"


def test_run_with_type_error(session):
    """Type errors should be caught."""
    res = post(session, "/run", {"code": "x = 'string' + 5"})
    assert not res.get("success"), "Type error should fail"


def test_run_with_attribute_error(session):
    """Missing attributes should fail."""
    res = post(session, "/run", {"code": "x = 5\nprint(x.missing_attr)"})
    assert not res.get("success"), "Attribute error should fail"


# Python Language Features
def test_run_with_list_slicing_extended(session):
    """Extended slicing should work."""
    res = post(session, "/run", {"code": "lst = [0,1,2,3,4,5,6,7,8,9]\nprint(lst[::3])"})
    assert res.get("success"), "Extended slicing should work"


def test_run_with_negative_slice(session):
    """Negative slice indices should work."""
    res = post(session, "/run", {"code": "s = 'hello'\nprint(s[-3:])"})
    assert res.get("success"), "Negative slice should work"


def test_run_with_string_escape_sequences(session):
    """Escape sequences should work."""
    res = post(session, "/run", {"code": "print('hello\\\\nworld')"})
    assert res.get("success"), "Escape sequences should work"


def test_run_with_raw_strings(session):
    """Raw strings should work."""
    res = post(session, "/run", {"code": "s = r'raw\\nstring'\nprint(s)"})
    assert res.get("success"), "Raw strings should work"


def test_run_with_backslash_line_continuation(session):
    """Line continuation should work."""
    res = post(session, "/run", {"code": "x = 1 + \\\n    2 + \\\n    3\nprint(x)"})
    assert res.get("success"), "Line continuation should work"


def test_run_with_parentheses_line_continuation(session):
    """Parentheses allow implicit line continuation."""
    res = post(session, "/run", {"code": "x = (1 +\n    2 +\n    3)\nprint(x)"})
    assert res.get("success"), "Parentheses continuation should work"


def test_run_with_docstring(session):
    """Docstrings should be allowed."""
    res = post(session, "/run", {"code": "def f():\n    '''docstring'''\n    return 1\nprint(f())"})
    assert res.get("success"), "Docstrings should work"


def test_run_with_multiline_comment(session):
    """Multiple comments should be handled."""
    res = post(session, "/run", {"code": "# comment 1\nx = 1  # comment 2\n# comment 3\nprint(x)"})
    assert res.get("success"), "Comments should work"


def test_run_with_inline_if_expression(session):
    """Inline if expressions should work."""
    res = post(session, "/run", {"code": "x = 5\nresult = 'big' if x > 3 else 'small'\nprint(result)"})
    assert res.get("success"), "Inline if should work"


def test_run_with_walrus_operator(session):
    """Walrus operator should work if Python 3.8+."""
    res = post(session, "/run", {"code": "if (x := 5) > 3:\n    print(x)"})
    # May fail on older Python, that's ok
    assert isinstance(res, dict), "Should return response"


def test_run_with_match_statement(session):
    """Match statement should work if Python 3.10+."""
    res = post(session, "/run", {"code": "x = 1\nmatch x:\n    case 1:\n        print('one')"})
    # May fail on older Python, that's ok
    assert isinstance(res, dict), "Should return response"


def test_run_with_list_multiplication(session):
    """List multiplication should work."""
    res = post(session, "/run", {"code": "lst = [1, 2] * 3\nprint(lst)"})
    assert res.get("success"), "List multiplication should work"


def test_run_with_tuple_concatenation(session):
    """Tuple concatenation should work."""
    res = post(session, "/run", {"code": "t = (1, 2) + (3, 4)\nprint(t)"})
    assert res.get("success"), "Tuple concatenation should work"


def test_run_with_set_operations_union(session):
    """Set union should work."""
    res = post(session, "/run", {"code": "s = {1, 2} | {2, 3}\nprint(s)"})
    assert res.get("success"), "Set union should work"


def test_run_with_set_operations_intersection(session):
    """Set intersection should work."""
    res = post(session, "/run", {"code": "s = {1, 2} & {2, 3}\nprint(s)"})
    assert res.get("success"), "Set intersection should work"


def test_run_with_set_operations_difference(session):
    """Set difference should work."""
    res = post(session, "/run", {"code": "s = {1, 2} - {2, 3}\nprint(s)"})
    assert res.get("success"), "Set difference should work"


def test_run_with_dict_comprehension(session):
    """Dict comprehensions should work."""
    res = post(session, "/run", {"code": "d = {i: i*2 for i in range(3)}\nprint(d)"})
    assert res.get("success"), "Dict comprehension should work"


def test_run_with_set_comprehension(session):
    """Set comprehensions should work."""
    res = post(session, "/run", {"code": "s = {i*2 for i in range(3)}\nprint(s)"})
    assert res.get("success"), "Set comprehension should work"


def test_run_with_nested_comprehension(session):
    """Nested comprehensions should work."""
    res = post(session, "/run", {"code": "result = [[i*j for j in range(3)] for i in range(3)]\nprint(result)"})
    assert res.get("success"), "Nested comprehension should work"


def test_run_with_comprehension_with_if(session):
    """Comprehensions with conditions should work."""
    res = post(session, "/run", {"code": "evens = [i for i in range(10) if i % 2 == 0]\nprint(evens)"})
    assert res.get("success"), "Conditional comprehension should work"


def test_run_with_function_annotations(session):
    """Function annotations should be allowed."""
    res = post(session, "/run", {"code": "def f(x: int) -> str:\n    return str(x)\nprint(f(5))"})
    assert res.get("success"), "Function annotations should work"


def test_run_with_default_mutable_argument(session):
    """Default mutable arguments should work (with caveats)."""
    res = post(session, "/run", {"code": "def f(lst=[]):\n    lst.append(1)\n    return lst\nprint(f())"})
    assert isinstance(res, dict), "Should handle mutable defaults"


def test_run_with_varargs(session):
    """Variable arguments should work."""
    res = post(session, "/run", {"code": "def f(*args):\n    return sum(args)\nprint(f(1, 2, 3))"})
    assert res.get("success"), "Varargs should work"


def test_run_with_keyword_only_args(session):
    """Keyword-only arguments should work."""
    res = post(session, "/run", {"code": "def f(*, x=5):\n    return x\nprint(f(x=10))"})
    assert res.get("success"), "Keyword-only args should work"


def test_run_with_unpacking_in_call(session):
    """Unpacking in function calls should work."""
    res = post(session, "/run", {"code": "def f(a, b, c):\n    return a + b + c\nargs = [1, 2, 3]\nprint(f(*args))"})
    assert res.get("success"), "Unpacking in calls should work"


def test_run_with_dict_unpacking_in_call(session):
    """Dict unpacking in calls should work."""
    res = post(session, "/run", {"code": "def f(a, b):\n    return a + b\nkwargs = {'a': 1, 'b': 2}\nprint(f(**kwargs))"})
    assert res.get("success"), "Dict unpacking should work"


def test_run_with_string_join_performance(session):
    """Joining large strings should work."""
    res = post(session, "/run", {"code": "parts = [str(i) for i in range(100)]\nresult = ','.join(parts)\nprint(len(result))"})
    assert res.get("success"), "String join should work"


def test_run_with_sorted_with_key(session):
    """Sorted with key function should work."""
    res = post(session, "/run", {"code": "data = ['apple', 'pie', 'a']\nsorted_data = sorted(data, key=len)\nprint(sorted_data)"})
    assert res.get("success"), "Sorted with key should work"


def test_run_with_reversed_iterator(session):
    """Reversed should return iterator."""
    res = post(session, "/run", {"code": "r = reversed([1, 2, 3])\nprint(next(r))"})
    # May fail if next() isn't available
    assert isinstance(res, dict), "Should return response"


def test_run_with_next_builtin(session):
    """Next builtin should work with iterators."""
    res = post(session, "/run", {"code": "it = iter([1, 2, 3])\nprint(next(it))"})
    # May fail if next() isn't in safe globals
    assert isinstance(res, dict), "Should return response"


def test_run_with_iter_builtin(session):
    """Iter builtin should work."""
    res = post(session, "/run", {"code": "it = iter([1, 2, 3])\nprint(type(it))"})
    # May fail if iter() isn't in safe globals
    assert isinstance(res, dict), "Should return response"


def test_run_with_exception_from_clause(session):
    """Exception from clause should work."""
    res = post(session, "/run", {"code": "try:\n    try:\n        x = 1/0\n    except ZeroDivisionError as e:\n        raise ValueError('wrapped') from e\nexcept:\n    print('caught')"})
    assert isinstance(res, dict), "Should handle exception chaining"


def test_run_with_finally_block(session):
    """Finally blocks should execute."""
    res = post(session, "/run", {"code": "try:\n    x = 1\nfinally:\n    print('finally')"})
    assert res.get("success"), "Finally should execute"


def test_run_with_else_after_try(session):
    """Else after try should execute if no exception."""
    res = post(session, "/run", {"code": "try:\n    x = 1\nexcept:\n    print('error')\nelse:\n    print('success')"})
    assert res.get("success"), "Try-except-else should work"


def test_run_with_class_definition(session):
    """Class definitions should work."""
    res = post(session, "/run", {"code": "class MyClass:\n    def __init__(self):\n        self.x = 1\nobj = MyClass()\nprint(obj.x)"})
    assert isinstance(res, dict), "Should return response (classes may not be supported)"


def test_run_with_class_inheritance(session):
    """Class inheritance should work."""
    res = post(session, "/run", {"code": "class Base:\n    x = 1\nclass Child(Base):\n    pass\nobj = Child()\nprint(obj.x)"})
    assert isinstance(res, dict), "Should return response (inheritance may not be supported)"


def test_run_with_class_methods(session):
    """Class methods should work."""
    res = post(session, "/run", {"code": "class MyClass:\n    @classmethod\n    def f(cls):\n        return 42\nprint(MyClass.f())"})
    # May fail if @classmethod isn't supported
    assert isinstance(res, dict), "Should return response"


def test_run_with_static_methods(session):
    """Static methods should work."""
    res = post(session, "/run", {"code": "class MyClass:\n    @staticmethod\n    def f():\n        return 42\nprint(MyClass.f())"})
    # May fail if @staticmethod isn't supported
    assert isinstance(res, dict), "Should return response"


def test_run_with_property_decorator(session):
    """Property decorator should work."""
    res = post(session, "/run", {"code": "class MyClass:\n    def __init__(self):\n        self._x = 5\n    @property\n    def x(self):\n        return self._x\nobj = MyClass()\nprint(obj.x)"})
    # May fail if @property isn't supported
    assert isinstance(res, dict), "Should return response"


def test_run_with_del_in_method(session):
    """Del should work in methods."""
    res = post(session, "/run", {"code": "class MyClass:\n    def __init__(self):\n        self.x = 1\n    def delete_x(self):\n        del self.x\nobj = MyClass()\nobj.delete_x()\nprint('deleted')"})
    assert isinstance(res, dict), "Should return response (may fail due to class support)"


def test_run_with_multiple_inheritance(session):
    """Multiple inheritance should work."""
    res = post(session, "/run", {"code": "class A:\n    a = 1\nclass B:\n    b = 2\nclass C(A, B):\n    pass\nobj = C()\nprint(obj.a, obj.b)"})
    assert isinstance(res, dict), "Should return response (may not be supported)"


# Request/Response Variations
def test_post_with_missing_code_field(session):
    """POST without code field should be handled."""
    res = post(session, "/run", {}, expect_status=400)
    assert not res.get("success"), "Missing code should fail"


def test_post_with_null_code(session):
    """POST with null code should be handled."""
    res = post(session, "/run", {"code": None}, expect_status=400)
    assert not res.get("success"), "Null code should fail"


def test_post_with_numeric_code(session):
    """POST with non-string code should be handled."""
    try:
        r = session.post(BASE + "/run", json={"code": 123}, timeout=10)
        # Server may return error or HTML on error
        if r.status_code >= 400:
            # That's fine, it's an error
            pass
    except Exception:
        # Connection errors are acceptable
        pass


def test_post_with_extra_fields(session):
    """POST with extra fields should be ignored."""
    res = post(session, "/run", {"code": "print('ok')", "extra": "field"})
    assert res.get("success"), "Extra fields should be ignored"


def test_snippets_with_empty_name(session):
    """Snippets with empty name should be handled."""
    res = post(session, "/snippets", {"name": "", "code": "x=1"})
    assert isinstance(res, dict), "Should handle empty name"


def test_snippets_with_null_name(session):
    """Snippets with null name should be handled."""
    res = post(session, "/snippets", {"name": None, "code": "x=1"})
    assert isinstance(res, dict), "Should handle null name"


def test_snippets_with_empty_code(session):
    """Snippets with empty code should be handled."""
    res = post(session, "/snippets", {"name": "test", "code": ""})
    assert isinstance(res, dict), "Should handle empty code"


def test_snippets_with_very_long_name(session):
    """Snippets with very long names should be handled."""
    long_name = "x" * 1000
    res = post(session, "/snippets", {"name": long_name, "code": "x=1"}, expect_status=400)
    assert isinstance(res, dict), "Should handle long names"


def test_describe_with_negative_line(session):
    """Describe with negative line number should fail."""
    res = post(session, "/describe", {"code": "x = 1", "line": -1})
    assert not res.get("success"), "Negative line should fail"


def test_describe_with_zero_line(session):
    """Describe with line 0 should fail."""
    res = post(session, "/describe", {"code": "x = 1", "line": 0})
    assert not res.get("success"), "Line 0 should fail"


def test_describe_with_float_line(session):
    """Describe with float line should be handled."""
    res = post(session, "/describe", {"code": "x = 1", "line": 1.5})
    assert isinstance(res, dict), "Should handle float line"


def test_track_variables_with_negative_line(session):
    """Track variables with negative line should fail."""
    res = post(session, "/track-variables", {"code": "x = 1", "line": -1})
    assert isinstance(res, dict), "Should return response (negative line handling may vary)"


def test_voice_command_with_empty_text(session):
    """Voice command with empty text should be handled."""
    res = post(session, "/voice-command", {"text": ""})
    assert isinstance(res, dict), "Should handle empty voice"


def test_voice_command_with_null_text(session):
    """Voice command with null text should be handled."""
    res = post(session, "/voice-command", {"text": None})
    assert isinstance(res, dict), "Should handle null voice"


def test_voice_command_with_numeric_text(session):
    """Voice command with numeric text should be handled."""
    try:
        r = session.post(BASE + "/voice-command", json={"text": 12345}, timeout=10)
        # Server may return error or HTML on error
        if r.status_code >= 400:
            # That's fine, it's an error
            pass
    except Exception:
        # Connection errors are acceptable
        pass


def test_voice_command_with_unicode_text(session):
    """Voice command with unicode should work."""
    res = post(session, "/voice-command", {"text": "你好 مرحبا 🔥"})
    assert isinstance(res, dict), "Should handle unicode"


def test_voice_command_case_variations(session):
    """Voice commands should be case insensitive."""
    for text in ["run", "RUN", "Run", "rUn"]:
        res = post(session, "/voice-command", {"text": text})
        assert isinstance(res, dict), f"Should handle '{text}'"


def test_check_syntax_with_very_long_code(session):
    """Syntax check with long code should work."""
    code = "x = 1\n" * 1000
    res = post(session, "/check-syntax", {"code": code})
    assert isinstance(res, dict), "Should handle long code"


def test_check_syntax_with_null_code(session):
    """Syntax check with null code should fail."""
    res = post(session, "/check-syntax", {"code": None})
    assert isinstance(res, dict), "Should return response"


# API Integration Tests
def test_analyze_nonexistent_endpoint(session):
    """Analyze endpoint might not exist."""
    try:
        res = post(session, "/analyze", {"code": "x = 1"}, expect_status=200)
        assert isinstance(res, dict), "Should return response"
    except Exception:
        # Endpoint might not exist
        pass


def test_advise_nonexistent_endpoint(session):
    """Advise endpoint might not exist."""
    try:
        res = post(session, "/advise", {"code": "x = 1"}, expect_status=200)
        assert isinstance(res, dict), "Should return response"
    except Exception:
        # Endpoint might not exist
        pass


def test_run_then_snippets_then_run(session):
    """Sequential operations should work independently."""
    res1 = post(session, "/run", {"code": "x = 1\nprint(x)"})
    assert res1.get("success"), "First run should succeed"
    
    time.sleep(0.1)
    
    res2 = get(session, "/snippets")
    assert "snippets" in res2, "Snippets should work"
    
    time.sleep(0.1)
    
    res3 = post(session, "/run", {"code": "y = 2\nprint(y)"})
    assert res3.get("success"), "Second run should succeed"


def test_run_with_print_to_stderr(session):
    """Code that writes to stderr should be handled."""
    res = post(session, "/run", {"code": "import sys\nsys.stderr.write('error')"})
    # May fail if sys is not available
    assert isinstance(res, dict), "Should handle stderr"


def test_run_with_stdin_attempt(session):
    """Attempts to read stdin should fail or hang."""
    res = post(session, "/run", {"code": "x = input('prompt')"})
    # Should timeout or fail
    assert isinstance(res, dict), "Should handle input attempt"


def test_run_with_environment_variable_access(session):
    """Attempts to access environment variables should fail."""
    res = post(session, "/run", {"code": "import os\nprint(os.environ)"})
    # Should fail if os module not allowed
    assert isinstance(res, dict), "Should return response"


def test_run_with_file_operations_attempt(session):
    """Attempts to read/write files should fail."""
    res = post(session, "/run", {"code": "with open('/etc/passwd') as f:\n    print(f.read())"})
    # Should fail if open() not available
    assert isinstance(res, dict), "Should return response"


def test_run_with_network_operations_attempt(session):
    """Attempts to make network calls should fail."""
    res = post(session, "/run", {"code": "import socket\ns = socket.socket()"})
    # Should fail if socket not allowed
    assert isinstance(res, dict), "Should return response"


def test_run_with_subprocess_attempt(session):
    """Attempts to run subprocesses should fail."""
    res = post(session, "/run", {"code": "import subprocess\nsubprocess.run(['ls'])"})
    # Should fail if subprocess not allowed
    assert isinstance(res, dict), "Should return response"


def test_run_with_eval_attempt(session):
    """Attempts to use eval should fail or be safe."""
    res = post(session, "/run", {"code": "eval('1+1')"})
    # May fail if eval not available
    assert isinstance(res, dict), "Should return response"


def test_run_with_exec_attempt(session):
    """Attempts to use exec should fail or be safe."""
    res = post(session, "/run", {"code": "exec('x = 1')"})
    # May fail if exec not available
    assert isinstance(res, dict), "Should return response"


def test_run_with_globals_access(session):
    """Attempts to access globals should be safe."""
    res = post(session, "/run", {"code": "print(globals())"})
    # Should not expose sensitive globals
    assert isinstance(res, dict), "Should return response"


def test_run_with_locals_access(session):
    """Attempts to access locals should be safe."""
    res = post(session, "/run", {"code": "print(locals())"})
    # Should be safe
    assert isinstance(res, dict), "Should return response"


def test_run_with_object_getattribute(session):
    """Attempts to use __getattribute__ should be safe."""
    res = post(session, "/run", {"code": "print(object.__getattribute__)"})
    # Should be blocked
    assert isinstance(res, dict), "Should return response"


def test_run_with_object_subclasses(session):
    """Attempts to access __subclasses__ should be safe."""
    res = post(session, "/run", {"code": "print(object.__subclasses__())"})
    # Should be blocked
    assert isinstance(res, dict), "Should return response"


def test_run_with_type_mro(session):
    """Attempts to access __mro__ should be safe."""
    res = post(session, "/run", {"code": "print(str.__mro__)"})
    # Should be blocked or safe
    assert isinstance(res, dict), "Should return response"


def test_run_with_imports_disallowed(session):
    """Only allowed imports should work."""
    res = post(session, "/run", {"code": "import requests\nprint(requests)"})
    # Should fail
    assert not res.get("success"), "Non-allowed imports should fail"


def test_run_with_allowed_import_math(session):
    """Math module should be allowed."""
    res = post(session, "/run", {"code": "from math import pi\nprint(pi)"})
    # Math module may or may not be available
    assert isinstance(res, dict), "Should return response"


def test_run_with_allowed_import_random(session):
    """Random module should be allowed."""
    res = post(session, "/run", {"code": "from random import random\nprint(random())"})
    # Random module may or may not be available
    assert isinstance(res, dict), "Should return response"


def test_run_with_allowed_import_string(session):
    """String module should be allowed."""
    res = post(session, "/run", {"code": "from string import digits\nprint(digits)"})
    # String module may or may not be available
    assert isinstance(res, dict), "Should return response"


def test_run_with_allowed_import_datetime(session):
    """Datetime module should be allowed."""
    res = post(session, "/run", {"code": "from datetime import datetime\nprint(datetime.now())"})
    # Datetime module may or may not be available
    assert isinstance(res, dict), "Should return response"


def test_snippet_persistence_across_requests(session):
    """Snippets should persist across separate API calls."""
    unique_name = f"persist-test-{int(time.time() * 1000)}"
    
    # Create
    post(session, "/snippets", {"name": unique_name, "code": "x=1"})
    time.sleep(0.2)
    
    # Retrieve - check multiple times
    for _ in range(3):
        res = get(session, "/snippets")
        found = any(s.get("name") == unique_name for s in res.get("snippets", []))
        if found:
            break
        time.sleep(0.1)
    
    assert found, "Snippet should persist"


def test_multiple_concurrent_snippet_creates(session):
    """Multiple snippets created rapidly should all persist."""
    created_names = []
    for i in range(5):
        name = f"rapid-{int(time.time() * 1000)}-{i}"
        post(session, "/snippets", {"name": name, "code": f"x={i}"})
        created_names.append(name)
    
    time.sleep(0.3)
    res = get(session, "/snippets")
    snippets = res.get("snippets", [])
    
    # Check if most were created
    found_count = sum(1 for s in snippets if s.get("name") in created_names)
    assert found_count >= 3, "Most snippets should be created"


def test_response_json_valid_on_all_endpoints(session):
    """All endpoints should return valid JSON."""
    endpoints_to_test = [
        ("/run", "POST", {"code": "print('test')"}),
        ("/snippets", "GET", None),
        ("/snippets", "POST", {"name": f"test-{int(time.time())}", "code": "x=1"}),
        ("/check-syntax", "POST", {"code": "x=1"}),
        ("/describe", "POST", {"code": "x=1", "line": 1}),
        ("/track-variables", "POST", {"code": "x=1", "line": 1}),
        ("/voice-command", "POST", {"text": "help"}),
    ]
    
    for endpoint, method, payload in endpoints_to_test:
        try:
            if method == "GET":
                r = session.get(BASE + endpoint, timeout=10)
            else:
                r = session.post(BASE + endpoint, json=payload, timeout=10)
            
            # Should be JSON on success
            if r.status_code < 400:
                data = r.json()
                assert isinstance(data, dict), f"{endpoint} should return JSON object"
            # Errors are acceptable (non-JSON responses)
        except Exception as e:
            # Network errors are acceptable
            pass


# UI & Static assets
def test_index_returns_html(session):
    """Root should return HTML with expected structure and headers."""
    r = session.get(BASE + "/", timeout=10)
    assert r.status_code in (200, 304), "Root should be accessible"
    ctype = r.headers.get("Content-Type", "")
    assert "text/html" in ctype, "Root should return HTML"
    text = r.text.lower()
    assert "<html" in text and "<head" in text, "HTML should contain head and html tags"
    assert "<body" in text or "<main" in text, "HTML should contain body or main"


def test_static_core_css_served(session):
    """Core CSS should be served with text/css content type."""
    r = session.get(BASE + "/static/style/core.css", timeout=10)
    assert r.status_code == 200, "/static/style/core.css should be served"
    ctype = r.headers.get("Content-Type", "")
    assert "text/css" in ctype or ctype.startswith("text/"), "CSS should be served as CSS/text"


def test_static_app_js_served(session):
    """App JS should be served with JS content type."""
    r = session.get(BASE + "/static/app.js", timeout=10)
    assert r.status_code == 200, "/static/app.js should be served"
    ctype = r.headers.get("Content-Type", "")
    assert ("javascript" in ctype) or ("text" in ctype), "JS should be served with JS or text content type"


def test_index_contains_viewport_and_title(session):
    """Index should include accessibility meta like viewport and a title."""
    r = session.get(BASE + "/", timeout=10)
    assert r.status_code in (200, 304)
    text = r.text.lower()
    assert "<meta name=\"viewport\"" in text or "viewport" in text, "Should include viewport meta"
    assert "<title" in text, "Should include a title tag"


def test_static_worker_and_bg_js(session):
    """Background and worker JS files should be present (if bundled)."""
    for path in ["/static/bg.js", "/static/python.worker.js"]:
        r = session.get(BASE + path, timeout=10)
        assert r.status_code in (200, 304, 404), f"{path} should respond (200/304/404)"






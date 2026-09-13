from codeup.accessibility import code_explainer
import pytest
import app as app_module


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as test_client:
        yield test_client


CODE = '''name = input("Name: ")

for i in range(3):
    print("Hello", name)

print("Done")
'''


def test_explain_code_is_line_by_line_and_skips_blank_lines():
    result = code_explainer.explain_code(CODE)
    text = result["analysis"]
    assert result["total"] == 4
    assert "Line 1:" in text
    assert "Line 3:" in text
    assert "Line 4:" in text
    assert "Line 6:" in text
    assert "Line 2:" not in text
    assert "indented 4 spaces" in text
    assert "for loop starting on line 3" in text


def test_deep_analysis_uses_context_sensitive_symbol_meanings():
    code = '''items = ["a", "b"]
piece = items[0:1]
if piece != []:
    print("piece", piece)
'''
    text = code_explainer.explain_code(code, deep=True)["analysis"]
    assert "square brackets create a list" in text
    assert "square brackets select an item or slice" in text
    assert "colon separates the start and end of a slice" in text
    assert "colon says that an indented block follows" in text
    assert "Not equals" in text
    assert "parentheses contain the arguments" in text


def test_large_program_is_paginated_by_non_empty_lines():
    code = "\n".join(f"value_{i} = {i}" for i in range(1, 48))
    first = code_explainer.explain_code(code, deep=True)
    assert first["total"] == 47
    assert first["next_start"] == 10
    assert "Line 1:" in first["analysis"]
    assert "Line 10:" in first["analysis"]
    assert "Line 11:" not in first["analysis"]
    assert "continue analysis" in first["analysis"]

    second = code_explainer.explain_code(code, deep=True, start=first["next_start"])
    assert "Line 11:" in second["analysis"]
    assert "Line 20:" in second["analysis"]


def test_analyze_endpoints_never_call_ai_provider(client, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("AI provider called for deterministic code analysis")

    monkeypatch.setattr(app_module, "call_gemini_capability", fail)
    basic = client.post("/analyze", json={"code": CODE}).get_json()
    deep = client.post("/analyze-deep", json={"code": CODE}).get_json()
    assert "Line 1:" in basic["analysis"]
    assert "colon says that an indented block follows" in deep["analysis"]
    assert basic["structural_source"] == "ast-tokenize"
    assert deep["structural_source"] == "ast-tokenize"

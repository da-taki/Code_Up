"""Natural variable and container creation (CREATE_VARIABLE slot filling).

Root causes fixed:
  * the only variable parser (intent_repair.parse_variable_assignment) needed
    English "insert/make ... variable" at the very start and built one scalar:
    "Variable insert karo with value taki" fell to append_line (the words
    were typed into the editor), containers were not understood at all, and
    "first name" was cut to "first";
  * clarification kept at most one slot: "make a variable" -> "score" wrote
    score = "" instead of asking for the value;
  * "set score to 95" appended a duplicate assignment;
  * the semantic resolver had no variable intent, so free-form requests could
    not be mapped to one.
All behaviour tests go through /voice-command.
"""

import json

import pytest

import app as app_module
from codeup.commands import semantic_intent, variable_creation
from test_semantic_intent_and_clarification import FakeModel


def _vc(client, text, code="", **extra):
    return client.post("/voice-command", json={"text": text, "code": code, **extra}).get_json()


def _code(data):
    # The edit response drops the program's final newline; compare without it.
    return ((data.get("ai_action") or {}).get("code") or "").rstrip("\n")


def _run(code):
    namespace = {}
    exec(compile(code, "<variable>", "exec"), {"__builtins__": {"set": set, "print": lambda *a: None}}, namespace)
    return namespace


def _spoken(data):
    return f"{data.get('speech') or ''} {data.get('message') or ''}".lower()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    fake = FakeModel()
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fake)
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    app_module.app.config["TESTING"] = True
    return fake


@pytest.fixture
def ai_client(model):
    return app_module.app.test_client()


def _block_generation(monkeypatch):
    monkeypatch.setattr(app_module, "_ai_capability_check",
                        lambda cap: (False, {}, "Code changes are off for this assignment.") if cap == "generate"
                        else (True, {}, ""))


# ---- required acceptance cases --------------------------------------------------------------

def test_acceptance_a_value_given_name_missing(client):
    first = _vc(client, "Variable insert karo with value taki")
    assert first["action"] == "clarify" and first["needs_clarification"] is True
    assert "name the variable" in first["message"].lower()
    assert first["action"] != "append_line"
    second = _vc(client, "username")
    assert _code(second) == 'username = "taki"'


def test_acceptance_b_variable_called_name(client):
    data = _vc(client, "Let's make a variable name with value taki")
    assert data.get("needs_clarification") is not True
    assert _code(data) == 'name = "taki"'


def test_acceptance_c_value_missing(client):
    first = _vc(client, "make variable score")
    assert first["message"] == "What value should score have?"
    assert _code(_vc(client, "95")) == "score = 95"


def test_acceptance_d_key_value_pairs(client):
    data = _vc(client, "make a library with key value pairs, Paris expensive, Amsterdam cheap, "
                       "Mumbai cheap, Galesburg cheap")
    assert data.get("needs_clarification") is not True
    assert _code(data) == ('library = {\n    "Paris": "expensive",\n    "Amsterdam": "cheap",\n'
                           '    "Mumbai": "cheap",\n    "Galesburg": "cheap",\n}')
    assert _run(_code(data))["library"]["Galesburg"] == "cheap"


def test_acceptance_e_free_form_hinglish_uses_semantic_fallback(ai_client, model):
    text = "acha ek variable bana dete hain jiska naam score ho aur uski value 95 rakh do"
    model.set(text, intent="CREATE_VARIABLE",
              parameters={"name": "score", "value_type": "integer", "value": 95}, confidence=0.94)
    data = _vc(ai_client, text)
    assert model.calls, "the semantic resolver must be consulted"
    assert _code(data) == "score = 95"
    assert data["source"] == "semantic_ai"


def test_acceptance_f_hinglish_dictionary(ai_client, model):
    text = "ek dictionary bana do library naam ki jisme Paris expensive aur Amsterdam cheap ho"
    model.set(text, intent="CREATE_VARIABLE", confidence=0.95,
              parameters={"name": "library", "value_type": "dictionary",
                          "value": {"Paris": "expensive", "Amsterdam": "cheap"}})
    data = _vc(ai_client, text)
    assert _code(data) == 'library = {\n    "Paris": "expensive",\n    "Amsterdam": "cheap",\n}'


def test_unseen_hinglish_dictionary_reaches_semantic_resolver(ai_client, model):
    text = "mujhe ek dictionary chahiye library naam ki, usme Paris expensive aur Amsterdam cheap daal do"
    model.set(text, intent="CREATE_VARIABLE", confidence=0.93,
              parameters={"name": "library", "value_type": "dictionary",
                          "value": {"Paris": "expensive", "Amsterdam": "cheap"}})
    data = _vc(ai_client, text)
    assert model.calls
    assert _code(data) == 'library = {\n    "Paris": "expensive",\n    "Amsterdam": "cheap",\n}'


def test_free_form_english_semantic_path(ai_client, model):
    text = "I'd like score to hold ninety five please"
    model.set(text, intent="CREATE_VARIABLE", confidence=0.92,
              parameters={"name": "score", "value_type": "integer", "value": 95})
    data = _vc(ai_client, text)
    assert _code(data) == "score = 95"


# ---- scalars ---------------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,expected", [
    ("make username taki", 'username = "taki"'),
    ("make greeting good morning", 'greeting = "Good morning"'),
    ("make score 95", "score = 95"),
    ("make percentage 95.5", "percentage = 95.5"),
    ("make passed true", "passed = True"),
    ("make logged in false", "logged_in = False"),
    ("make result none", "result = None"),
    ("make count zero", "count = 0"),
    ("make variable first name with value taki", 'first_name = "taki"'),
    ("set age to 16", "age = 16"),
])
def test_scalar_values(client, phrase, expected):
    data = _vc(client, phrase)
    assert data.get("needs_clarification") is not True, data
    assert _code(data) == expected
    _run(expected)   # never an undefined identifier


# ---- containers -----------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,expected", [
    ("make variable fruits with apple banana mango", 'fruits = ["apple", "banana", "mango"]'),
    ("create a list called marks with 95 88 91", "marks = [95, 88, 91]"),
    ("make cities list Paris Amsterdam Mumbai", 'cities = ["Paris", "Amsterdam", "Mumbai"]'),
    ("make list mixed with 5 hello true", 'mixed = [5, "hello", True]'),
    ("make empty list called students", "students = []"),
    ("make list called marks", "marks = []"),
    ("make coordinates tuple 10 20", "coordinates = (10, 20)"),
    ("create RGB tuple with 255 120 0", "rgb = (255, 120, 0)"),
    ("make a set called colours with red blue green", 'colours = {"red", "blue", "green"}'),
    ("make empty set called seen", "seen = set()"),
    ("make empty dictionary called students", "students = {}"),
    ("make dictionary called prices", "prices = {}"),
])
def test_container_values(client, phrase, expected):
    data = _vc(client, phrase)
    assert data.get("needs_clarification") is not True, data
    assert _code(data) == expected
    _run(expected)


def test_empty_set_is_never_braces(client):
    code = _code(_vc(client, "make empty set called seen"))
    assert code == "seen = set()" and "{}" not in code
    assert isinstance(_run(code)["seen"], set)


@pytest.mark.parametrize("phrase,expected", [
    ("make prices dictionary apples 50 bananas 30 mangoes 80",
     'prices = {\n    "apples": 50,\n    "bananas": 30,\n    "mangoes": 80,\n}'),
    ("create marks with math 95 english 91 physics 88",
     'marks = {\n    "math": 95,\n    "english": 91,\n    "physics": 88,\n}'),
    ("make students dictionary, Taki marks 95, Aman marks 88",
     'students = {\n    "Taki": {"marks": 95},\n    "Aman": {"marks": 88},\n}'),
])
def test_dictionaries(client, phrase, expected):
    data = _vc(client, phrase)
    assert _code(data) == expected
    _run(expected)


def test_malformed_odd_pairs_ask_only_for_the_pairs(client):
    first = _vc(client, "make prices dictionary apples 50 bananas")
    assert first["action"] == "clarify"
    assert "key" in first["message"].lower() and "value" in first["message"].lower()
    second = _vc(client, "apples 50 bananas 30")
    assert _code(second) == 'prices = {\n    "apples": 50,\n    "bananas": 30,\n}'


def test_keys_without_values_are_not_paired_with_each_other(client):
    data = _vc(client, "make a dictionary called person with name and age")
    assert data["action"] == "clarify" and not _code(data)
    assert "key" in data["message"].lower()


# ---- existing variables and expressions -----------------------------------------------------

def test_existing_variable_is_not_quoted(client):
    data = _vc(client, "make total equal to score", code="score = 95\n")
    assert "total = score" in _code(data)
    assert _run(_code(data))["total"] == 95


def test_expression_with_existing_variable(client):
    data = _vc(client, "make doubled equal to score times 2", code="score = 95\n")
    assert _run(_code(data))["doubled"] == 190


def test_list_containing_existing_variable(client):
    data = _vc(client, "make list results with score 80 90", code="score = 95\nprint(score)\n")
    assert _code(data) == "score = 95\nresults = [score, 80, 90]\nprint(score)"


def test_dictionary_containing_existing_variable(client):
    data = _vc(client, "make dictionary results with total score best 99", code="score = 95\n")
    code = _code(data)
    assert '"total": score' in code
    assert _run(code)["results"] == {"total": 95, "best": 99}


def test_unknown_words_stay_strings(client):
    for phrase in ("make username taki", "make variable fruits with apple banana mango",
                   "make a library with key value pairs, Paris expensive, Amsterdam cheap"):
        _run(_code(_vc(client, phrase)))   # would raise NameError on a bare identifier


# ---- clarification --------------------------------------------------------------------------

def test_missing_name(client):
    first = _vc(client, "make a variable with value 95")
    assert "name the variable" in first["message"].lower()
    assert _code(_vc(client, "score")) == "score = 95"


def test_both_missing_are_asked_one_at_a_time(client):
    assert "name the variable" in _vc(client, "make a variable")["message"].lower()
    second = _vc(client, "score")
    assert second["message"] == "What value should score have?"
    assert _code(_vc(client, "95")) == "score = 95"


def test_missing_dictionary_name_keeps_the_pairs(client):
    first = _vc(client, "make dictionary with Paris expensive Amsterdam cheap")
    assert first["message"] == "What should I name the dictionary?"
    second = _vc(client, "prices")
    assert _code(second) == 'prices = {\n    "Paris": "expensive",\n    "Amsterdam": "cheap",\n}'


@pytest.mark.parametrize("reply", ["name", "call it name", "naam name", "name rakho", "variable ka naam name"])
def test_natural_name_replies(client, reply):
    _vc(client, "variable insert karo with value taki")
    assert _code(_vc(client, reply)) == 'name = "taki"'


@pytest.mark.parametrize("reply,expected", [("95", "score = 95"), ("taki", 'score = "taki"'),
                                            ("true", "score = True"), ("value 7", "score = 7")])
def test_natural_value_replies(client, reply, expected):
    _vc(client, "make variable score")
    assert _code(_vc(client, reply)) == expected


def test_a_list_without_a_name_asks_for_the_name(client):
    assert _vc(client, "make a list")["message"] == "What should I name the list?"
    assert _code(_vc(client, "students")) == "students = []"


def test_clarification_is_session_isolated(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    learner_a = app_module.app.test_client()
    learner_b = app_module.app.test_client()
    assert _vc(learner_a, "make variable score")["action"] == "clarify"
    assert _code(_vc(learner_b, "95")) != "score = 95"
    assert _code(_vc(learner_a, "95")) == "score = 95"


def test_clarification_expires(client, monkeypatch):
    _vc(client, "make variable score")
    monkeypatch.setattr(semantic_intent, "PENDING_TTL_SECONDS", -1)
    assert _code(_vc(client, "95")) != "score = 95"


def test_unrelated_command_supersedes_the_question(client):
    _vc(client, "make a variable", code='print("Hello")\n')
    assert _vc(client, "run", code='print("Hello")\n')["action"] == "run"
    assert "username" not in _code(_vc(client, "username", code='print("Hello")\n'))


def test_cancel_drops_the_question(client):
    _vc(client, "make variable score")
    assert _vc(client, "cancel")["action"] == "deterministic_message"
    assert _code(_vc(client, "95")) != "score = 95"


# ---- names -----------------------------------------------------------------------------------

def test_keyword_name_is_corrected_not_inserted(client):
    data = _vc(client, "make variable for with value 5")
    assert data["action"] == "clarify" and "keyword" in data["message"]
    assert not _code(data)
    assert _code(_vc(client, "count")) == "count = 5"


def test_builtin_name_offers_a_safe_name(client):
    data = _vc(client, "make variable sum with value zero")
    assert data["action"] == "clarify" and "total" in data["message"]
    assert _code(_vc(client, "yes")) == "total = 0"


def test_invalid_identifier_reply_reasks(client):
    _vc(client, "make a variable with value 5")
    data = _vc(client, "123")
    assert data.get("needs_clarification") is True and not _code(data)


def test_multi_word_names_become_identifiers(client):
    assert _code(_vc(client, "make variable first name with value taki")) == 'first_name = "taki"'


# ---- updates and insertion into current code ------------------------------------------------

def test_set_updates_the_existing_assignment(client):
    data = _vc(client, "set score to 95", code="score = 80\nprint(score)\n")
    assert _code(data) == "score = 95\nprint(score)"


def test_add_to_existing_list(client):
    data = _vc(client, "add Amsterdam to cities", code='cities = ["Paris", "Mumbai"]\n')
    assert _code(data) == 'cities = ["Paris", "Mumbai", "Amsterdam"]'


def test_add_to_existing_dictionary(client):
    data = _vc(client, "add banana 30 to prices", code='prices = {"apple": 50}\n')
    assert _code(data) == 'prices = {\n    "apple": 50,\n    "banana": 30,\n}'


def test_add_number_to_a_number_is_not_a_container_update(client):
    data = _vc(client, "add 5 to score", code="score = 80\n")
    assert data.get("source") != "variable_update"


def test_insertion_preserves_current_code(client):
    data = _vc(client, "make variable name with value taki", code='print("Hello")\n')
    assert _code(data) == 'name = "taki"\nprint("Hello")'
    assert "share" not in _spoken(data)


def test_insertion_inside_function(client):
    code = 'def calculate():\n    print("Starting")\n'
    data = _vc(client, "inside the function make variable total with value 0", code=code)
    assert _code(data) == 'def calculate():\n    print("Starting")\n    total = 0'


def test_insertion_inside_loop(client):
    code = "for i in range(5):\n    print(i)\n"
    data = _vc(client, "in the loop make variable message with value hello", code=code)
    assert _code(data) == 'for i in range(5):\n    print(i)\n    message = "Hello"'


def test_structural_insertion_survives_clarification(client):
    code = "for i in range(5):\n    print(i)\n"
    assert _vc(client, "in the loop make variable message", code=code)["action"] == "clarify"
    data = _vc(client, "hello", code=code)
    assert _code(data) == 'for i in range(5):\n    print(i)\n    message = "Hello"'


# ---- broken English / Hinglish ---------------------------------------------------------------

@pytest.mark.parametrize("phrase,expected", [
    ("variable insert karo value taki", None),
    ("insert variable name taki", 'name = "taki"'),
    ("make variable score value 95", "score = 95"),
    ("variable score 95", "score = 95"),
    ("create score variable value 95", "score = 95"),
    ("variable banana hai score value 95", "score = 95"),
    ("score variable 95", "score = 95"),
    ("make dictionary prices apple 50 banana 30", 'prices = {\n    "apple": 50,\n    "banana": 30,\n}'),
    ("make list marks 90 80 95", "marks = [90, 80, 95]"),
    ("variable banao score naam se value 95", "score = 95"),
    ("score naam ka variable bana do value 95", "score = 95"),
    ("variable ka naam username rakho aur value taki", 'username = "taki"'),
    ("naam name rakho value taki", 'name = "taki"'),
    ("dictionary banao prices naam se apple 50 banana 30", 'prices = {\n    "apple": 50,\n    "banana": 30,\n}'),
    ("marks ki list banao 90 80 95", "marks = [90, 80, 95]"),
])
def test_broken_english_and_hinglish(client, phrase, expected):
    data = _vc(client, phrase)
    if expected is None:
        assert data["action"] == "clarify" and "name the variable" in data["message"].lower()
    else:
        assert _code(data) == expected, data


# ---- semantic path: provider failure and validation ------------------------------------------

def test_provider_failure_keeps_deterministic_creation(ai_client, model):
    model.fail = True
    assert _code(_vc(ai_client, "make variable score value 95")) == "score = 95"


def test_provider_failure_on_free_form_asks_concisely(ai_client, model):
    model.fail = True
    text = "acha ek variable bana dete hain jiska naam score ho aur uski value 95 rakh do"
    data = _vc(ai_client, text)
    assert data["action"] == "clarify" and not _code(data)
    assert "score = 95" in data["message"]
    assert _code(_vc(ai_client, "haan")) == "score = 95"


def test_model_output_is_validated_not_executed(ai_client, model):
    text = "acha ek variable bana dete hain jiska naam score ho aur uski value 95 rakh do"
    model.set(text, intent="CREATE_VARIABLE", confidence=0.95,
              parameters={"name": "score", "value_type": "expression", "value": "__import__('os').system('x')"})
    data = _vc(ai_client, text)
    assert "__import__" not in _code(data) and "system" not in _code(data)


def test_model_variable_value_must_exist(ai_client, model):
    text = "acha ek variable bana dete hain jiska naam total ho aur uski value score rakh do"
    model.set(text, intent="CREATE_VARIABLE", confidence=0.95,
              parameters={"name": "total", "value_type": "variable", "value": "score"})
    assert _code(_vc(ai_client, text)) == 'total = "score"'
    assert _code(_vc(ai_client, text, code="score = 5\n")) == "score = 5\ntotal = score"


def test_semantic_validator_rejects_bad_parameters():
    base = {"intent": "CREATE_VARIABLE", "confidence": 0.95}
    ok = semantic_intent.validate({**base, "parameters": {"name": "score", "value_type": "integer", "value": 95}})
    assert ok.status == "resolved" and ok.params["value"] == 95
    assert semantic_intent.validate({**base, "parameters": {"name": "x; import os", "value": 1}}).status == "invalid"
    assert semantic_intent.validate({**base, "parameters": {"value_type": "code", "value": 1}}).status == "invalid"
    deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    assert semantic_intent.validate({**base, "parameters": {"name": "x", "value": deep}}).status == "invalid"
    assert semantic_intent.validate({**base, "parameters": {"name": "x", "value": list(range(60))}}).status == "invalid"


def test_rendered_python_is_ast_checked():
    assert variable_creation.render("score", ["expr", "__import__('os')"]) is None
    assert variable_creation.render("for", ["int", 5]) is None
    assert variable_creation.render("score", ["int", 95]) == "score = 95"
    assert variable_creation.is_safe_assignment("seen = set()")
    assert not variable_creation.is_safe_assignment("seen = open('x')")


def test_structured_representation_is_reported(client):
    data = _vc(client, "create a list called marks with 95 88 91")
    rep = data["variable_request"]
    assert rep["intent"] == "CREATE_VARIABLE"
    assert rep["parameters"] == {"name": "marks", "value_type": "list", "value": [95, 88, 91]}
    json.dumps(rep)


# ---- classroom policy ------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,code", [
    ("make variable score value 95", ""),
    ("make a library with key value pairs, Paris expensive, Amsterdam cheap", ""),
    ("add Amsterdam to cities", 'cities = ["Paris"]\n'),
    ("inside the function make variable total with value 0", 'def calculate():\n    print("x")\n'),
    ("set score to 95", "score = 80\n"),
])
def test_classroom_policy_blocks_every_creation_path(client, monkeypatch, phrase, code):
    _block_generation(monkeypatch)
    data = _vc(client, phrase, code=code)
    assert data.get("policy_blocked") is True, data
    assert not _code(data)


def test_classroom_policy_blocks_clarification_completion(client, monkeypatch):
    _vc(client, "make variable score")
    _block_generation(monkeypatch)
    data = _vc(client, "95")
    assert data.get("policy_blocked") is True and not _code(data)


def test_classroom_policy_blocks_semantic_creation(ai_client, model, monkeypatch):
    text = "acha ek variable bana dete hain jiska naam score ho aur uski value 95 rakh do"
    model.set(text, intent="CREATE_VARIABLE", confidence=0.95,
              parameters={"name": "score", "value_type": "integer", "value": 95})
    _block_generation(monkeypatch)
    data = _vc(ai_client, text)
    assert data.get("policy_blocked") is True and not _code(data)


# ---- other variable tools are not creation ---------------------------------------------------

CODE = "score = 95\nprint(score)\n"


def test_list_variables_regression(client):
    data = _vc(client, "what variables exist", code=CODE)
    assert data["action"] == "deterministic_message" and "score" in data["speech"]
    assert _vc(client, "variables batao", code=CODE)["action"] == "list_variables_voice"


def test_watch_and_unwatch_regression(client):
    assert _vc(client, "watch score", code=CODE)["action"] == "watch_variable"
    assert _vc(client, "stop watching score", code=CODE)["action"] == "stop_watching"


def test_program_state_regression(client):
    data = _vc(client, "show program state", code=CODE)
    assert data["action"] == "deterministic_message" and "95" in data["speech"]


def test_rename_regression(client):
    data = _vc(client, "rename score to marks", code=CODE)
    assert _code(data) == "marks = 95\nprint(marks)"


@pytest.mark.parametrize("phrase", [
    "make a calculator", "make it better", "make it use a dictionary", "make the text bigger",
    "create a list of even numbers", "create a function called add", "add a loop", "insert print hello",
    "insert x equals 5", "make trainer notes", "set inputs to Alice and 17", "make loop run 5 times",
    "score ki value kya hai", "list variables", "change the variable name to total",
    "insert print variable name", "make a list example", "make a variable example for marks",
])
def test_non_creation_commands_are_not_hijacked(client, phrase):
    data = _vc(client, phrase, code=CODE)
    assert data.get("source") not in {"variable_creation", "variable_update"}, data
    assert "variable_request" not in data

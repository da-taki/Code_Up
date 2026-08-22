import pytest

from codeup.classroom import ai_policy


@pytest.mark.parametrize("policy,capability,expected", [
    ("FULL", "generate", True),
    ("FULL", "explain", True),
    ("FULL", "hint", True),
    ("FULL", "error_help", True),
    ("FULL", "assessment", True),
    ("EXPLANATIONS_ONLY", "explain", True),
    ("EXPLANATIONS_ONLY", "error_help", True),
    ("EXPLANATIONS_ONLY", "generate", False),
    ("EXPLANATIONS_ONLY", "hint", False),
    ("HINTS_ONLY", "hint", True),
    ("HINTS_ONLY", "explain", False),
    ("HINTS_ONLY", "generate", False),
    ("ERROR_HELP_ONLY", "error_help", True),
    ("ERROR_HELP_ONLY", "explain", False),
    ("ASSESSMENT", "assessment", True),
    ("ASSESSMENT", "generate", False),
    ("ASSESSMENT", "explain", False),
    ("OFF", "generate", False),
    ("OFF", "explain", False),
    ("OFF", "hint", False),
    ("OFF", "error_help", False),
    ("OFF", "assessment", False),
])
def test_is_allowed_matrix(policy, capability, expected):
    assert ai_policy.is_allowed(capability, policy) is expected


def test_normalize_policy_defaults_to_full_for_unknown():
    assert ai_policy.normalize_policy("not-a-real-policy") == "FULL"
    assert ai_policy.normalize_policy(None) == "FULL"
    assert ai_policy.normalize_policy("full") == "FULL"


def test_blocked_message_mentions_editor_still_works():
    msg = ai_policy.blocked_message("generate", "EXPLANATIONS_ONLY")
    assert "edit" in msg.lower()
    assert "run" in msg.lower()


@pytest.mark.parametrize("text,expected", [
    ("write the code for me please", "generate"),
    ("give me the answer", "generate"),
    ("what happened, why did it fail", "error_help"),
    ("I got a traceback", "error_help"),
    ("can I get a hint", "hint"),
    ("I'm stuck", "hint"),
    ("what does this function do", "explain"),
    ("explain this concept", "explain"),
])
def test_classify_chat_capability(text, expected):
    assert ai_policy.classify_chat_capability(text) == expected


def test_resolve_policy_for_request_defaults_full_with_no_assignment_context():
    policy = ai_policy.resolve_policy_for_request(
        lambda name: None, lambda name: None, lambda aid: None,
    )
    assert policy == "FULL"


def test_resolve_policy_for_request_uses_cookie_assignment():
    def get_assignment(assignment_id):
        assert assignment_id == 42
        return {"ai_policy": "HINTS_ONLY"}

    policy = ai_policy.resolve_policy_for_request(
        lambda name: "42" if name == ai_policy.ASSIGNMENT_COOKIE else None,
        lambda name: None,
        get_assignment,
    )
    assert policy == "HINTS_ONLY"


def test_resolve_policy_for_request_json_field_overrides_cookie():
    def get_assignment(assignment_id):
        return {"ai_policy": "OFF"} if assignment_id == 7 else {"ai_policy": "FULL"}

    policy = ai_policy.resolve_policy_for_request(
        lambda name: "1",
        lambda name: 7 if name == "assignment_id" else None,
        get_assignment,
    )
    assert policy == "OFF"

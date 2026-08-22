import pytest

from codeup.classroom import ai_policy


def test_presets_cover_all_capabilities():
    for preset, settings in ai_policy.PRESETS.items():
        assert set(settings.keys()) == set(ai_policy.CAPABILITIES), preset


@pytest.mark.parametrize("preset,capability,expected", [
    ("FULL", "generate", True),
    ("FULL", "fix", True),
    ("FULL", "explain", True),
    ("FULL", "hint", True),
    ("FULL", "error_help", True),
    ("FULL", "concept_qa", True),
    ("FULL", "audio_code_map", True),
    ("FULL", "step_narration", True),
    ("FULL", "watch_variable", True),
    ("FULL", "assessment", True),
    ("EXPLANATIONS_ONLY", "explain", True),
    ("EXPLANATIONS_ONLY", "error_help", True),
    ("EXPLANATIONS_ONLY", "generate", False),
    ("EXPLANATIONS_ONLY", "fix", False),
    ("EXPLANATIONS_ONLY", "hint", False),
    ("HINTS_ONLY", "hint", True),
    ("HINTS_ONLY", "explain", False),
    ("HINTS_ONLY", "generate", False),
    ("ERROR_HELP_ONLY", "error_help", True),
    ("ERROR_HELP_ONLY", "explain", False),
    ("ASSESSMENT", "generate", False),
    ("ASSESSMENT", "explain", False),
    ("ASSESSMENT", "assessment", False),  # assessment capability mirrors explain
    ("OFF", "generate", False),
    ("OFF", "explain", False),
    ("OFF", "hint", False),
    ("OFF", "error_help", False),
    ("OFF", "assessment", False),
])
def test_is_allowed_matrix(preset, capability, expected):
    settings = ai_policy.default_settings_for_preset(preset)
    assert ai_policy.is_allowed(capability, settings) is expected


def test_normalize_policy_defaults_to_full_for_unknown():
    assert ai_policy.normalize_policy("not-a-real-policy") == "FULL"
    assert ai_policy.normalize_policy(None) == "FULL"
    assert ai_policy.normalize_policy("full") == "FULL"


def test_normalize_settings_fails_open_on_missing_or_malformed():
    assert ai_policy.normalize_settings(None) == {cap: True for cap in ai_policy.CAPABILITIES}
    partial = ai_policy.normalize_settings({"generate": False})
    assert partial["generate"] is False
    assert partial["explain"] is True  # missing key defaults to allowed, not blocked


def test_assessment_capability_mirrors_explain_toggle():
    settings = ai_policy.normalize_settings({"explain": True, "generate": False})
    assert ai_policy.is_allowed("assessment", settings) is True
    settings2 = ai_policy.normalize_settings({"explain": False})
    assert ai_policy.is_allowed("assessment", settings2) is False


def test_blocked_message_mentions_editor_still_works():
    settings = ai_policy.default_settings_for_preset("EXPLANATIONS_ONLY")
    msg = ai_policy.blocked_message("generate", settings)
    assert "edit" in msg.lower()
    assert "run" in msg.lower()


def test_summarize_settings_full_is_short():
    settings = ai_policy.default_settings_for_preset("FULL")
    assert ai_policy.summarize_settings(settings) == "Full AI assistance is available for this assignment."


def test_summarize_settings_mentions_disabled_and_allowed():
    settings = ai_policy.default_settings_for_preset("EXPLANATIONS_ONLY")
    summary = ai_policy.summarize_settings(settings)
    assert "disabled" in summary.lower()
    assert "allowed" in summary.lower()


def test_summarize_settings_assessment_prefix():
    settings = ai_policy.default_settings_for_preset("ASSESSMENT")
    summary = ai_policy.summarize_settings(settings, is_assessment=True)
    assert summary.startswith("Assessment mode.")


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


def test_resolve_settings_for_request_defaults_full_with_no_assignment_context():
    settings = ai_policy.resolve_settings_for_request(
        lambda name: None, lambda name: None, lambda aid: None,
    )
    assert settings == {cap: True for cap in ai_policy.CAPABILITIES}


def test_resolve_settings_for_request_uses_cookie_assignment():
    def get_assignment(assignment_id):
        assert assignment_id == 42
        return {"ai_policy": "HINTS_ONLY", "capability_settings": None}

    settings = ai_policy.resolve_settings_for_request(
        lambda name: "42" if name == ai_policy.ASSIGNMENT_COOKIE else None,
        lambda name: None,
        get_assignment,
    )
    assert settings["hint"] is True
    assert settings["generate"] is False


def test_resolve_settings_for_request_prefers_stored_capability_settings_over_preset():
    def get_assignment(assignment_id):
        return {"ai_policy": "OFF", "capability_settings": {"generate": False, "fix": True}}

    settings = ai_policy.resolve_settings_for_request(
        lambda name: "1", lambda name: None, get_assignment,
    )
    # stored settings win over the OFF preset label - explicit fine-tuning
    assert settings["fix"] is True
    assert settings["generate"] is False


def test_resolve_settings_for_request_json_field_overrides_cookie():
    def get_assignment(assignment_id):
        return {"ai_policy": "OFF", "capability_settings": None} if assignment_id == 7 else {"ai_policy": "FULL", "capability_settings": None}

    settings = ai_policy.resolve_settings_for_request(
        lambda name: "1",
        lambda name: 7 if name == "assignment_id" else None,
        get_assignment,
    )
    assert settings["generate"] is False

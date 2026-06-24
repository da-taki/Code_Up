import os
from pathlib import Path

import pytest

os.environ.setdefault("FLASK_TESTING", "true")


INTEGRATION_MODULES = {
    "test_assistive_technology_integration.py",
    "test_chrome_english_onboarding.py",
    "test_conversation_orchestrator.py",
    "test_grounded_ai.py",
    "test_multifile_projects.py",
    "test_tutorial_frontend.py",
    "test_voice_engine.py",
    "test_voice_recognition_frontend.py",
    "test_voice_speech_frontend.py",
}

SLOW_MODULES = {
    "test_accessible_learning_packs.py",
    "test_openvino_intent_demo.py",
    "test_tts_voice_consistency.py",
}

EXHAUSTIVE_MODULES = {
    "test_advanced_concept_qa.py",
    "test_audio_features.py",
    "test_blind_debugger_mode.py",
    "test_command_clarifier.py",
    "test_concept_questions.py",
    "test_concept_tutor.py",
    "test_demo_voice_commands.py",
    "test_deterministic_editing_tools.py",
    "test_deterministic_ide_navigation.py",
    "test_exact_symbol_routes.py",
    "test_final_demo_command_routing.py",
    "test_followup_code_edits.py",
    "test_generation_prompt_quality.py",
    "test_groq_key_manager.py",
    "test_hint_engine.py",
    "test_input_concierge.py",
    "test_intent_repair.py",
    "test_landmarks.py",
    "test_learning_recap.py",
    "test_lesson_builder.py",
    "test_meaning_navigation.py",
    "test_nab_hardening_routes.py",
    "test_natural_code_editor.py",
    "test_natural_command_mapper.py",
    "test_onboarding_guidance.py",
    "test_onboarding_tutorial_readiness.py",
    "test_real_voice_repair.py",
    "test_session_memory.py",
    "test_speech_accessibility_controls.py",
    "test_speech_cancel_semantics.py",
    "test_symbolic_specs.py",
    "test_template_command_routes.py",
    "test_tutorial_engine.py",
    "test_tutorial_routes.py",
    "test_vague_generation_clarification.py",
    "test_voice_control_and_variable_insert.py",
    "test_walkthrough_narration.py",
}

DOC_MODULES = {"test_default_mode_and_docs.py"}

CORE_SECURITY_SUBSTRINGS = (
    "test_production_session_cookie_secure_by_default",
    "test_sandbox_blocks_escape_attempts",
    "test_sandbox_allowed_modules",
    "test_sandbox_normal_execution",
    "test_sandbox_loop_output",
    "test_max_code_size_rejected_run",
    "test_empty_code_rejected",
    "test_stale_mixed_html_autosave_shape_rejected",
    "test_python_string_containing_html_is_allowed",
    "test_voice_command_non_string_and_too_long_payloads",
    "test_missing_body_handled",
    "test_voice_unknown_command",
    "test_deterministic_voice_command_does_not_call_conversational_ai",
    "test_fs_path_traversal_blocked",
    "TestPerSessionSandbox::test_separate_sessions_get_different_workspaces",
    "TestRunRateLimit::test_rate_limit_blocks_after_threshold",
    "TestInputSandboxBackend::test_input_call_fails",
    "TestRegressionAfterFixes::test_basic_print",
    "TestRegressionAfterFixes::test_os_import_blocked",
    "TestRegressionAfterFixes::test_math_import_works",
)

# Focused behavior tests that live inside an otherwise broad/slow module but
# cover core behavior required in the default quick suite. Mirrors the security
# carve-out above so a broad regression sweep can stay slow without hiding the
# one focused test that must always run quickly.
CORE_QUICK_SUBSTRINGS = (
    "test_teacher_report_privacy_counts_and_export",
)


def pytest_addoption(parser):
    parser.addoption(
        "--run-full",
        action="store_true",
        default=False,
        help="Run slow, integration, and exhaustive regression tests too.",
    )


def _test_file_name(item):
    return Path(str(item.fspath)).name


def _is_core_security_item(item):
    return any(fragment in item.nodeid for fragment in CORE_SECURITY_SUBSTRINGS)


def _is_core_quick_item(item):
    return any(fragment in item.nodeid for fragment in CORE_QUICK_SUBSTRINGS)


def pytest_collection_modifyitems(config, items):
    run_full = config.getoption("--run-full")
    selected = []
    deselected = []

    for item in items:
        file_name = _test_file_name(item)
        skip_by_default = False

        if file_name in DOC_MODULES:
            item.add_marker(pytest.mark.docs)

        if _is_core_quick_item(item):
            # Focused core-behavior test inside an otherwise broad/slow module:
            # always keep it in the default quick suite and never down-rank it.
            pass
        elif file_name in INTEGRATION_MODULES:
            item.add_marker(pytest.mark.integration)
            skip_by_default = True
        elif file_name in SLOW_MODULES:
            item.add_marker(pytest.mark.slow)
            skip_by_default = True
        elif file_name in EXHAUSTIVE_MODULES:
            item.add_marker(pytest.mark.exhaustive)
            skip_by_default = True
        elif file_name == "test_security_voice.py" and not _is_core_security_item(item):
            item.add_marker(pytest.mark.exhaustive)
            skip_by_default = True

        if skip_by_default and not run_full:
            deselected.append(item)
        else:
            selected.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


@pytest.fixture(autouse=True)
def enable_testing_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_TESTING", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as app_module
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "RUN_RATE_WINDOW", 5)
    yield

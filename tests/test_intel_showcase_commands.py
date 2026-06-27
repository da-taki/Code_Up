from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import app as app_module
import intel_showcase
from intent_parser import parse_intent


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.setattr(intel_showcase, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(intel_showcase, "_is_package_available", lambda package: False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text):
    return client.post("/voice-command", json={"text": text}).get_json()


def _spoken(data):
    return (data.get("speech") or data.get("message") or "").strip()


@pytest.mark.parametrize("phrase", [
    "intel toolkit status",
    "show intel toolkit status",
    "intel status",
    "show intel optimization report",
    "what intel tools are used",
])
def test_intel_status_aliases_parse_deterministically(phrase):
    parsed = parse_intent(phrase)

    assert parsed["intent"] == "intel_toolkit_status"
    assert parsed["confidence"] >= 0.75


@pytest.mark.parametrize("phrase", [
    "intel toolkit status",
    "show intel toolkit status",
    "intel status",
    "show intel optimization report",
    "what intel tools are used",
])
def test_intel_status_command_routes_deterministically(client, phrase):
    data = _vc(client, phrase)
    speech = _spoken(data).lower()

    assert data["action"] == "deterministic_message"
    assert data["intent"] == "intel_toolkit_status"
    assert "intel toolkit status" in speech
    assert "openvino" in speech
    assert "intel neural compressor" in speech
    assert "intel extension for scikit-learn" in speech
    assert "optional" in speech
    assert "does not require" in speech
    assert data["action"] != "clarify"


def test_intel_status_command_does_not_call_cloud_ai(client, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("Intel status command must be deterministic")

    monkeypatch.setattr(app_module, "call_gemini", boom)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", boom)

    data = _vc(client, "what intel tools are used")

    assert data["action"] == "deterministic_message"
    assert "Intel toolkit status" in _spoken(data)


def test_missing_packages_produce_honest_output(client):
    data = _vc(client, "intel toolkit status")
    speech = _spoken(data).lower()

    assert "missing" in speech
    assert "no compressed model artifact has been generated yet" in speech
    assert "no local benchmark result has been recorded yet" in speech
    assert "measured local speedup ratio" not in speech
    assert "compression run used" not in speech


def test_status_module_uses_lazy_detection(monkeypatch):
    calls = []

    def fake_available(package):
        calls.append(package)
        return False

    monkeypatch.setattr(intel_showcase, "_is_package_available", fake_available)
    status = intel_showcase.build_status(report_dir="missing-report-dir")

    assert status["openvino"]["runtime_available"] is False
    assert status["neural_compressor"]["package_available"] is False
    assert status["sklearnex"]["package_available"] is False
    assert calls == ["openvino", "neural_compressor", "sklearnex"]


def test_status_command_reads_report_files(client, tmp_path, monkeypatch):
    neural_report = {
        "toolkit": "Intel Neural Compressor",
        "compression_run": False,
        "reason": "no local intent model artifact found",
    }
    sklearnex_report = {
        "toolkit": "Intel Extension for Scikit-learn, powered by oneDAL",
        "accelerated_benchmark_run": True,
        "baseline_seconds": 0.02,
        "accelerated_seconds": 0.01,
        "reason": "local benchmark completed",
    }
    (tmp_path / intel_showcase.NEURAL_REPORT).write_text(json.dumps(neural_report), encoding="utf-8")
    (tmp_path / intel_showcase.SKLEARNEX_REPORT).write_text(json.dumps(sklearnex_report), encoding="utf-8")
    monkeypatch.setattr(intel_showcase, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(intel_showcase, "_is_package_available", lambda package: True)

    data = _vc(client, "show intel optimization report")
    speech = _spoken(data).lower()

    assert "runtime available" in speech
    assert "no compressed model artifact has been generated yet" in speech
    assert "local benchmark recorded baseline 0.020000 seconds and accelerated 0.010000 seconds" in speech


def test_app_startup_does_not_import_optional_intel_packages_subprocess():
    code = textwrap.dedent(
        """
        import builtins
        import os

        os.environ.setdefault("FLASK_TESTING", "true")
        real_import = builtins.__import__
        blocked = {"neural_compressor", "sklearnex"}

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            root = name.split(".", 1)[0]
            if root in blocked:
                raise AssertionError(f"app startup imported optional Intel package: {name}")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = guarded_import
        import app  # noqa: F401
        print("app-import-ok")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(app_module.__file__).resolve().parent,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "app-import-ok" in result.stdout

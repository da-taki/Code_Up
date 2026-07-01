from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NEURAL_SCRIPT = ROOT / "tools" / "intel" / "neural_compressor_demo.py"
SKLEARNEX_SCRIPT = ROOT / "tools" / "intel" / "sklearnex_benchmark.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_script(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_neural_compressor_check_env_exits_safely():
    result = _run_script(NEURAL_SCRIPT, "--check-env")

    assert result.returncode == 0
    assert "Intel Neural Compressor" in result.stdout
    assert result.stderr == ""


def test_neural_compressor_missing_dependency_does_not_crash(monkeypatch, capsys):
    module = _load_module(NEURAL_SCRIPT, "neural_compressor_demo_missing")
    monkeypatch.setattr(module, "_dependency_available", lambda: False)

    assert module.check_environment() == 0

    output = capsys.readouterr().out
    assert "Intel Neural Compressor is optional. Install it to run this demo." in output


def test_neural_compressor_dry_run_is_honest(monkeypatch, capsys):
    module = _load_module(NEURAL_SCRIPT, "neural_compressor_demo_dry_run")
    monkeypatch.setattr(module, "_dependency_available", lambda: False)

    assert module.dry_run() == 0

    output = capsys.readouterr().out
    assert "Dry-run" in output
    assert "No benchmark numbers or performance claims" in output
    assert "No model artifact was provided" in output
    assert "Intel Neural Compressor is optional" in output


def test_neural_compressor_dry_run_can_write_report(tmp_path):
    output = tmp_path / "neural_report.json"
    result = _run_script(NEURAL_SCRIPT, "--dry-run", "--write-report", "--output", str(output))

    assert result.returncode == 0
    assert output.exists()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["toolkit"] == "Intel Neural Compressor"
    assert report["compression_run"] is False
    assert report["reason"] == "no local intent model artifact found"
    assert "speedup" not in json.dumps(report).lower()


def test_sklearnex_check_env_exits_safely():
    result = _run_script(SKLEARNEX_SCRIPT, "--check-env")

    assert result.returncode == 0
    assert "Intel Extension for Scikit-learn" in result.stdout
    assert result.stderr == ""


def test_sklearnex_missing_dependency_does_not_crash(monkeypatch, capsys):
    module = _load_module(SKLEARNEX_SCRIPT, "sklearnex_benchmark_missing")
    monkeypatch.setattr(module, "_sklearnex_available", lambda: False)

    assert module.check_environment() == 0

    output = capsys.readouterr().out
    assert (
        "Intel Extension for Scikit-learn is optional. "
        "Install scikit-learn-intelex to run the accelerated benchmark."
    ) in output


def test_sklearnex_dry_run_does_not_claim_measured_speedup(monkeypatch, capsys):
    module = _load_module(SKLEARNEX_SCRIPT, "sklearnex_benchmark_dry_run")
    monkeypatch.setattr(module, "_sklearnex_available", lambda: False)

    assert module.dry_run() == 0

    output = capsys.readouterr().out
    assert "Dry-run" in output
    assert "No timing result, acceleration claim, or hardware claim" in output
    assert "Measured local speedup ratio" not in output
    assert "Baseline scikit-learn mean seconds" not in output


def test_sklearnex_dry_run_can_write_report(tmp_path):
    output = tmp_path / "sklearnex_report.json"
    result = _run_script(SKLEARNEX_SCRIPT, "--dry-run", "--write-report", "--output", str(output))

    assert result.returncode == 0
    assert output.exists()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["toolkit"] == "Intel Extension for Scikit-learn, powered by oneDAL"
    assert report["accelerated_benchmark_run"] is False
    assert report["reason"] == "dry run only; benchmark was not executed"
    assert "speedup_ratio" not in report


def test_app_startup_does_not_import_optional_intel_packages():
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
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "app-import-ok" in result.stdout

"""Deterministic Intel toolkit status reporting for CodeUp.

This module is safe to import during app startup. It checks optional Intel
packages with importlib metadata only and reads small JSON reports when they
exist. It never imports neural_compressor or sklearnex.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Optional


ROOT_DIR = Path(__file__).resolve().parent
REPORT_DIR = ROOT_DIR / "reports" / "intel"
NEURAL_REPORT = "neural_compressor_report.json"
SKLEARNEX_REPORT = "sklearnex_benchmark_report.json"

OPENVINO_ARTIFACT_CANDIDATES = (
    ROOT_DIR / "artifacts" / "intel" / "openvino_intent_model.xml",
    ROOT_DIR / "artifacts" / "openvino" / "intent_model.xml",
    ROOT_DIR / "models" / "openvino_intent_model.xml",
)


def _is_package_available(package_name: str) -> bool:
    return importlib.util.find_spec(package_name) is not None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists() or not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - status should never break the app
        return None


def _first_existing(paths: tuple[Path, ...]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def build_status(report_dir: str | Path | None = None) -> Dict[str, Any]:
    reports = Path(report_dir) if report_dir is not None else REPORT_DIR
    openvino_artifact = _first_existing(OPENVINO_ARTIFACT_CANDIDATES)
    neural_report = _read_json(reports / NEURAL_REPORT)
    sklearnex_report = _read_json(reports / SKLEARNEX_REPORT)

    return {
        "openvino": {
            "runtime_available": _is_package_available("openvino"),
            "local_intent_path": True,
            "artifact_exists": openvino_artifact is not None,
            "artifact_path": str(openvino_artifact) if openvino_artifact else "",
        },
        "neural_compressor": {
            "package_available": _is_package_available("neural_compressor"),
            "report_exists": neural_report is not None,
            "report": neural_report or {},
        },
        "sklearnex": {
            "package_available": _is_package_available("sklearnex"),
            "report_exists": sklearnex_report is not None,
            "report": sklearnex_report or {},
        },
        "optional": True,
    }


def _availability_word(available: bool) -> str:
    return "available" if available else "missing"


def _neural_line(status: Dict[str, Any]) -> str:
    package = status["neural_compressor"]
    report = package.get("report") or {}
    package_text = _availability_word(bool(package.get("package_available")))

    if report.get("compression_run") is True:
        artifact = report.get("artifact_path") or "a local artifact"
        return (
            "Intel Neural Compressor: optional and "
            f"{package_text}. A compression report says a local compression run used {artifact}."
        )

    if package.get("report_exists"):
        reason = str(report.get("reason") or "no compressed model artifact has been generated yet")
        return (
            "Intel Neural Compressor: optional and "
            f"{package_text}. No compressed model artifact has been generated yet; {reason}."
        )

    return (
        "Intel Neural Compressor: optional and "
        f"{package_text}. No compressed model artifact has been generated yet."
    )


def _sklearnex_line(status: Dict[str, Any]) -> str:
    package = status["sklearnex"]
    report = package.get("report") or {}
    package_text = _availability_word(bool(package.get("package_available")))

    if report.get("accelerated_benchmark_run") is True:
        baseline = report.get("baseline_seconds")
        accelerated = report.get("accelerated_seconds")
        if isinstance(baseline, (int, float)) and isinstance(accelerated, (int, float)):
            return (
                "Intel Extension for Scikit-learn, powered by oneDAL: optional and "
                f"{package_text}. Local benchmark recorded baseline {baseline:.6f} seconds "
                f"and accelerated {accelerated:.6f} seconds."
            )
        return (
            "Intel Extension for Scikit-learn, powered by oneDAL: optional and "
            f"{package_text}. A local benchmark report exists."
        )

    if package.get("report_exists"):
        reason = str(report.get("reason") or "no local benchmark result has been recorded yet")
        return (
            "Intel Extension for Scikit-learn, powered by oneDAL: optional and "
            f"{package_text}. Benchmark tooling is available, but no local benchmark result "
            f"has been recorded yet; {reason}."
        )

    return (
        "Intel Extension for Scikit-learn, powered by oneDAL: optional and "
        f"{package_text}. Benchmark tooling is available, but no local benchmark result "
        "has been recorded yet."
    )


def format_status_for_speech(status: Dict[str, Any] | None = None) -> str:
    status = status or build_status()
    openvino = status["openvino"]
    openvino_runtime = _availability_word(bool(openvino.get("runtime_available")))
    if openvino.get("artifact_exists"):
        openvino_detail = "a local intent model artifact is present"
    else:
        openvino_detail = "no local intent model artifact is configured"

    lines = [
        "Intel toolkit status:",
        (
            "OpenVINO: supported for the local intent-classification demo path; "
            f"runtime {openvino_runtime}, and {openvino_detail}."
        ),
        _neural_line(status),
        _sklearnex_line(status),
        "The deployed app does not require these optional Intel packages.",
    ]
    return " ".join(lines)

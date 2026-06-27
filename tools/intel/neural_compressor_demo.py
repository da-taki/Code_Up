"""Optional Intel Neural Compressor demo tooling for CodeUp.

This script is intentionally outside normal app startup. It gives developers a
truthful path for checking whether Intel Neural Compressor is available and for
describing compression experiments around CodeUp's local intent demo without
making the package mandatory for the deployed app.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Sequence


OPTIONAL_MESSAGE = "Intel Neural Compressor is optional. Install it to run this demo."
DEFAULT_REPORT_PATH = Path("reports") / "intel" / "neural_compressor_report.json"
DEFAULT_MODEL_NOTE = (
    "No model artifact was provided. CodeUp's current local intent demo uses "
    "deterministic rules when OpenVINO model artifacts are not configured."
)


def _dependency_available() -> bool:
    return importlib.util.find_spec("neural_compressor") is not None


def _write_report(report: dict, output: str | Path | None) -> Path:
    path = Path(output) if output else DEFAULT_REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _report(mode: str, model_path: str | None, compression_run: bool, reason: str) -> dict:
    return {
        "toolkit": "Intel Neural Compressor",
        "mode": mode,
        "dependency_available": _dependency_available(),
        "model_path": model_path or "",
        "compression_run": compression_run,
        "reason": reason,
    }


def _model_note(model_path: str | None) -> str:
    if not model_path:
        return DEFAULT_MODEL_NOTE

    path = Path(model_path)
    if path.exists():
        return f"Model/artifact path found: {path}. A real compression run would use this input."
    return f"Model/artifact path not found: {path}. Dry-run only; no compression was attempted."


def check_environment() -> int:
    if _dependency_available():
        print("Intel Neural Compressor detected. Optional compression demo tooling can run.")
    else:
        print(OPTIONAL_MESSAGE)
    return 0


def check_environment_report() -> dict:
    available = _dependency_available()
    reason = "dependency available" if available else "optional dependency is not installed"
    return _report("check-env", None, False, reason)


def dry_run(model_path: str | None = None) -> int:
    print("Dry-run: Intel Neural Compressor integration path for CodeUp.")
    print(_model_note(model_path))
    print(
        "This demo would evaluate a small local intent-classification artifact, "
        "then record compression or quantization results only after a real local run."
    )
    print("No benchmark numbers or performance claims are produced in dry-run mode.")
    if not _dependency_available():
        print(OPTIONAL_MESSAGE)
    return 0


def dry_run_report(model_path: str | None = None) -> dict:
    if not model_path:
        reason = "no local intent model artifact found"
    elif Path(model_path).exists():
        reason = "dry run only; compression was not attempted"
    else:
        reason = "no local intent model artifact found"
    return _report("dry-run", model_path, False, reason)


def run_demo(model_path: str | None) -> int:
    try:
        import neural_compressor  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001 - optional dependency may fail to import
        print(OPTIONAL_MESSAGE)
        return 0

    if not model_path or not Path(model_path).exists():
        print("Intel Neural Compressor is installed, but no usable model artifact was provided.")
        print(_model_note(model_path))
        print("Run with --dry-run to see the planned compression workflow.")
        return 0

    print("Intel Neural Compressor is installed and a model/artifact path was provided.")
    print(
        "CodeUp does not ship a committed compression recipe for this artifact yet, "
        "so no compression or benchmark result was generated."
    )
    return 0


def run_demo_report(model_path: str | None) -> dict:
    if not _dependency_available():
        return _report("run", model_path, False, "optional dependency is not installed")
    if not model_path or not Path(model_path).exists():
        return _report("run", model_path, False, "no local intent model artifact found")
    return _report("run", model_path, False, "no committed compression recipe exists for this artifact")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-env", action="store_true", help="Check optional dependency availability.")
    parser.add_argument("--dry-run", action="store_true", help="Describe the demo workflow without compression.")
    parser.add_argument("--model-path", help="Optional local model or artifact path to describe.")
    parser.add_argument("--write-report", action="store_true", help="Write a small JSON status report.")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH), help="Report path for --write-report.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check_env:
        code = check_environment()
        report = check_environment_report()
    elif args.dry_run:
        code = dry_run(args.model_path)
        report = dry_run_report(args.model_path)
    else:
        code = run_demo(args.model_path)
        report = run_demo_report(args.model_path)
    if args.write_report:
        path = _write_report(report, args.output)
        print(f"Wrote Intel Neural Compressor report: {path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

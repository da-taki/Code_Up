"""Optional Intel Extension for Scikit-learn / oneDAL benchmark for CodeUp."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import time
from typing import Sequence


OPTIONAL_MESSAGE = (
    "Intel Extension for Scikit-learn is optional. "
    "Install scikit-learn-intelex to run the accelerated benchmark."
)
DEFAULT_REPORT_PATH = Path("reports") / "intel" / "sklearnex_benchmark_report.json"

SYNTHETIC_COMMANDS = [
    ("run code", "run_code"),
    ("please run the program", "run_code"),
    ("stop speaking", "stop_speaking"),
    ("be quiet please", "stop_speaking"),
    ("insert print hello", "insert_code"),
    ("add a line that prints hi", "insert_code"),
    ("write a program for even numbers", "generate_code"),
    ("create a python function", "generate_code"),
    ("what is a loop", "concept_question"),
    ("why does range stop early", "concept_question"),
]


def _sklearnex_available() -> bool:
    return importlib.util.find_spec("sklearnex") is not None


def _sklearn_available() -> bool:
    return importlib.util.find_spec("sklearn") is not None


def _write_report(report: dict, output: str | Path | None) -> Path:
    path = Path(output) if output else DEFAULT_REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _base_report(mode: str, accelerated_benchmark_run: bool, reason: str) -> dict:
    return {
        "toolkit": "Intel Extension for Scikit-learn, powered by oneDAL",
        "mode": mode,
        "sklearn_available": _sklearn_available(),
        "sklearnex_available": _sklearnex_available(),
        "accelerated_benchmark_run": accelerated_benchmark_run,
        "reason": reason,
    }


def check_environment() -> int:
    if _sklearnex_available():
        print("Intel Extension for Scikit-learn detected. Optional oneDAL benchmark can run.")
    else:
        print(OPTIONAL_MESSAGE)
    return 0


def check_environment_report() -> dict:
    reason = "dependency available" if _sklearnex_available() else "optional dependency is not installed"
    return _base_report("check-env", False, reason)


def dry_run() -> int:
    print("Dry-run: optional Intel Extension for Scikit-learn / oneDAL benchmark path.")
    print(
        "The benchmark uses a tiny deterministic command-intent dataset and only "
        "prints timing numbers after an actual local run."
    )
    print("No timing result, acceleration claim, or hardware claim is produced in dry-run mode.")
    if not _sklearnex_available():
        print(OPTIONAL_MESSAGE)
    return 0


def dry_run_report() -> dict:
    return _base_report("dry-run", False, "dry run only; benchmark was not executed")


def _run_classifier_once() -> float:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    texts = [row[0] for row in SYNTHETIC_COMMANDS]
    labels = [row[1] for row in SYNTHETIC_COMMANDS]
    started = time.perf_counter()
    model = make_pipeline(TfidfVectorizer(), LogisticRegression(max_iter=200, random_state=0))
    model.fit(texts, labels)
    model.predict(texts)
    return time.perf_counter() - started


def _measure(iterations: int) -> float:
    samples = [_run_classifier_once() for _ in range(iterations)]
    return statistics.mean(samples)


def run_benchmark(iterations: int) -> int:
    if not _sklearn_available():
        print("scikit-learn is required for this optional benchmark. Install requirements-intel.txt.")
        return 0

    if not _sklearnex_available():
        print(OPTIONAL_MESSAGE)
        return 0

    baseline_seconds = _measure(iterations)

    from sklearnex import patch_sklearn  # type: ignore

    patch_sklearn()
    accelerated_seconds = _measure(iterations)

    print(f"Baseline scikit-learn mean seconds over {iterations} runs: {baseline_seconds:.6f}")
    print(
        "Intel Extension for Scikit-learn / oneDAL mean seconds "
        f"over {iterations} runs: {accelerated_seconds:.6f}"
    )
    if accelerated_seconds > 0:
        print(f"Measured local speedup ratio: {baseline_seconds / accelerated_seconds:.3f}x")
    print("These numbers are local measurements only and are not a hardware claim.")
    return 0


def run_benchmark_report(iterations: int) -> dict:
    if not _sklearn_available():
        return _base_report("run", False, "scikit-learn is not installed")
    if not _sklearnex_available():
        return _base_report("run", False, "optional dependency is not installed")

    baseline_seconds = _measure(iterations)
    from sklearnex import patch_sklearn  # type: ignore

    patch_sklearn()
    accelerated_seconds = _measure(iterations)
    report = _base_report("run", True, "local benchmark completed")
    report.update({
        "iterations": iterations,
        "baseline_seconds": baseline_seconds,
        "accelerated_seconds": accelerated_seconds,
    })
    if accelerated_seconds > 0:
        report["speedup_ratio"] = baseline_seconds / accelerated_seconds
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-env", action="store_true", help="Check optional dependency availability.")
    parser.add_argument("--dry-run", action="store_true", help="Describe the benchmark without running it.")
    parser.add_argument("--iterations", type=int, default=5, help="Benchmark repetitions when dependencies exist.")
    parser.add_argument("--write-report", action="store_true", help="Write a small JSON status report.")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH), help="Report path for --write-report.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check_env:
        code = check_environment()
        report = check_environment_report()
    elif args.dry_run:
        code = dry_run()
        report = dry_run_report()
    else:
        iterations = max(1, args.iterations)
        code = run_benchmark(iterations)
        report = run_benchmark_report(iterations)
    if args.write_report:
        path = _write_report(report, args.output)
        print(f"Wrote Intel Extension for Scikit-learn report: {path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

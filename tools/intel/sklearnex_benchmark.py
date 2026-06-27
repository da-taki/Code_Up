"""Optional Intel Extension for Scikit-learn / oneDAL benchmark for CodeUp."""

from __future__ import annotations

import argparse
import importlib.util
import statistics
import time
from typing import Sequence


OPTIONAL_MESSAGE = (
    "Intel Extension for Scikit-learn is optional. "
    "Install scikit-learn-intelex to run the accelerated benchmark."
)

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


def check_environment() -> int:
    if _sklearnex_available():
        print("Intel Extension for Scikit-learn detected. Optional oneDAL benchmark can run.")
    else:
        print(OPTIONAL_MESSAGE)
    return 0


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-env", action="store_true", help="Check optional dependency availability.")
    parser.add_argument("--dry-run", action="store_true", help="Describe the benchmark without running it.")
    parser.add_argument("--iterations", type=int, default=5, help="Benchmark repetitions when dependencies exist.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check_env:
        return check_environment()
    if args.dry_run:
        return dry_run()
    return run_benchmark(max(1, args.iterations))


if __name__ == "__main__":
    raise SystemExit(main())

import ast
import json
import re
import time
from typing import Any, Dict, Iterable, List, Tuple


PROJECT_ROOT_DIR = "project"
PROJECT_MANIFEST = "codeup.project.json"
MAX_PROJECT_FILES = 80
MAX_PROJECT_FILE_BYTES = 250_000
MAX_PROJECT_TOTAL_BYTES = 1_500_000

SAFE_STDLIB_MODULES = {
    "math",
    "random",
    "statistics",
    "datetime",
    "json",
    "csv",
    "pathlib",
    "typing",
    "collections",
    "itertools",
    "string",
}
THIRD_PARTY_MODULES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
}
SUPPORTED_IMPORTS = SAFE_STDLIB_MODULES | set(THIRD_PARTY_MODULES)


class ProjectPathError(ValueError):
    pass


def normalize_project_path(path: Any) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    raw = re.sub(r"\s+dot\s+", ".", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s+slash\s+", "/", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*/\s*", "/", raw)
    raw = raw.strip("/ ")
    if " " in raw:
        raw = "/".join(part.strip().replace(" ", "_") for part in raw.split("/"))
    if not raw:
        raise ProjectPathError("File path is required.")
    if len(raw) > 220:
        raise ProjectPathError("File path is too long.")
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise ProjectPathError("Use a project-relative file path.")
    parts = [part for part in raw.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ProjectPathError("File path must stay inside the project.")
    if any(".." in part for part in parts):
        raise ProjectPathError("File path cannot contain parent directory markers.")
    cleaned = "/".join(parts)
    if not re.match(r"^[A-Za-z0-9._\-/]+$", cleaned):
        raise ProjectPathError("File path can only use letters, numbers, dashes, underscores, dots, and slashes.")
    if cleaned == PROJECT_MANIFEST or cleaned.endswith("/" + PROJECT_MANIFEST):
        raise ProjectPathError("The project manifest is managed by CodeUp.")
    return cleaned


def project_path_to_module(path: str) -> str:
    path = normalize_project_path(path)
    if not path.endswith(".py"):
        return ""
    stem = path[:-3]
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def local_module_names(paths: Iterable[str]) -> List[str]:
    names = set()
    for path in paths:
        module = project_path_to_module(path)
        if module:
            names.add(module)
            names.add(module.split(".", 1)[0])
    return sorted(names)


def normalize_file_map(files: Any) -> Dict[str, str]:
    if isinstance(files, dict):
        items = files.items()
    elif isinstance(files, list):
        items = []
        for item in files:
            if isinstance(item, dict):
                items.append((item.get("path") or item.get("name"), item.get("content") or ""))
    else:
        items = []

    normalized: Dict[str, str] = {}
    total = 0
    for raw_path, raw_content in items:
        path = normalize_project_path(raw_path)
        content = str(raw_content or "")
        size = len(content.encode("utf-8"))
        if size > MAX_PROJECT_FILE_BYTES:
            raise ProjectPathError(f"{path} is too large for a CodeUp project.")
        total += size
        if total > MAX_PROJECT_TOTAL_BYTES:
            raise ProjectPathError("Project files are too large.")
        normalized[path] = content
        if len(normalized) > MAX_PROJECT_FILES:
            raise ProjectPathError("Project has too many files.")
    return dict(sorted(normalized.items()))


def infer_requirements(files: Dict[str, str]) -> List[str]:
    requirements = set()
    for content in files.values():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names: List[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".", 1)[0]
                req = THIRD_PARTY_MODULES.get(top)
                if req:
                    requirements.add(req)
    req_text = files.get("requirements.txt", "")
    for line in req_text.splitlines():
        clean = line.strip()
        if clean and not clean.startswith("#"):
            requirements.add(re.split(r"[<>=!~]", clean, maxsplit=1)[0].strip())
    return sorted(r for r in requirements if r)


def make_manifest(
    files: Dict[str, str],
    *,
    name: str = "CodeUp Project",
    entry: str = "main.py",
    active_file: str = "",
    requirements: Iterable[str] = (),
) -> Dict[str, Any]:
    if not files:
        files = {"main.py": 'print("Hello CodeUp!")\n'}
    entry = normalize_project_path(
        entry if entry in files else next((p for p in files if p.endswith(".py")), next(iter(files), "main.py"))
    )
    active = normalize_project_path(active_file if active_file in files else entry)
    inferred = set(infer_requirements(files))
    inferred.update(str(req).strip() for req in requirements if str(req).strip())
    now = int(time.time())
    return {
        "schema": 1,
        "name": str(name or "CodeUp Project")[:80],
        "mode": "multifile",
        "root": PROJECT_ROOT_DIR,
        "entry": entry,
        "active_file": active,
        "files": [{"path": path, "size": len(content.encode("utf-8"))} for path, content in sorted(files.items())],
        "requirements": sorted(inferred),
        "created_at": now,
        "updated_at": now,
    }


def project_summary(manifest: Dict[str, Any]) -> str:
    files = manifest.get("files") or []
    names = [item.get("path", "") for item in files if item.get("path")]
    entry = manifest.get("entry") or "main.py"
    reqs = manifest.get("requirements") or []
    if not names:
        return "This project has no files yet."
    if len(names) == 1:
        text = f"Project has 1 file: {names[0]}. Entry file is {entry}."
    else:
        text = f"Project has {len(names)} files: {', '.join(names)}. Entry file is {entry}."
    if reqs:
        text += f" Requirements: {', '.join(reqs)}."
    return text


def _quiz_template() -> Tuple[Dict[str, str], str, List[str]]:
    files = {
        "main.py": """from questions import QUESTIONS
from score import grade_quiz


def run_quiz():
    # These sample answers let the project run without typing input.
    sample_answers = ["b", "a", "c"]
    print("Quiz game starting.")
    score = grade_quiz(QUESTIONS, sample_answers)
    print(f"Final score: {score} out of {len(QUESTIONS)}")


if __name__ == "__main__":
    run_quiz()
""",
        "questions.py": """QUESTIONS = [
    {
        "prompt": "Which keyword starts a function in Python?",
        "choices": {"a": "loop", "b": "def", "c": "print"},
        "answer": "b",
    },
    {
        "prompt": "Which type stores true or false?",
        "choices": {"a": "bool", "b": "text", "c": "decimal"},
        "answer": "a",
    },
    {
        "prompt": "Which command shows output?",
        "choices": {"a": "input", "b": "range", "c": "print"},
        "answer": "c",
    },
]
""",
        "score.py": """def grade_quiz(questions, answers):
    # Count each matching answer.
    score = 0
    for question, answer in zip(questions, answers):
        if answer.lower() == question["answer"]:
            score += 1
    return score
""",
        "README.md": """# Beginner Quiz Game

Run `main.py`. The sample answers are stored in code so the project runs without input.
Change `sample_answers` in `main.py` to try different quiz results.
""",
        "requirements.txt": "",
    }
    return files, "main.py", []


def _pandas_marks_template() -> Tuple[Dict[str, str], str, List[str]]:
    files = {
        "main.py": """from data_loader import load_marks


def summarize_marks():
    # Load the small sample CSV from this project.
    marks = load_marks("data/marks.csv")
    print("Student marks summary")
    print("Average mark:", round(marks["mark"].mean(), 2))
    top_row = marks.sort_values("mark", ascending=False).iloc[0]
    print("Top student:", top_row["name"], "with", int(top_row["mark"]))


if __name__ == "__main__":
    summarize_marks()
""",
        "data_loader.py": """try:
    import pandas as pd
except ImportError as exc:
    raise ImportError(
        "Missing dependency: pandas. Add pandas to requirements.txt and install it before running this project."
    ) from exc


def load_marks(path):
    # Pandas reads the CSV relative to the project root.
    return pd.read_csv(path)
""",
        "data/marks.csv": "name,mark\nAsha,88\nKabir,76\nMeera,93\n",
        "README.md": """# Student Marks Analysis

Run `main.py`. The project reads `data/marks.csv` with pandas and prints a spoken-friendly summary.
""",
        "requirements.txt": "pandas\n",
    }
    return files, "main.py", ["pandas"]


def _numpy_stats_template() -> Tuple[Dict[str, str], str, List[str]]:
    files = {
        "main.py": """from stats_utils import describe_numbers


def main():
    # The sample data is small so the output is easy to hear.
    numbers = [4, 7, 9, 10, 12]
    summary = describe_numbers(numbers)
    print("Numpy statistics project")
    print("Mean:", summary["mean"])
    print("Maximum:", summary["max"])


if __name__ == "__main__":
    main()
""",
        "stats_utils.py": """try:
    import numpy as np
except ImportError as exc:
    raise ImportError(
        "Missing dependency: numpy. Add numpy to requirements.txt and install it before running this project."
    ) from exc


def describe_numbers(values):
    # Convert the list to a numpy array for numeric summaries.
    arr = np.array(values)
    return {
        "mean": float(np.mean(arr)),
        "max": int(np.max(arr)),
    }
""",
        "tests/test_main.py": """from stats_utils import describe_numbers


def test_describe_numbers():
    summary = describe_numbers([1, 2, 3])
    assert summary["mean"] == 2.0
    assert summary["max"] == 3


if __name__ == "__main__":
    test_describe_numbers()
    print("Tests passed")
""",
        "README.md": """# Numpy Statistics

Run `main.py` for the spoken summary. Run `tests/test_main.py` to check the helper.
""",
        "requirements.txt": "numpy\n",
    }
    return files, "main.py", ["numpy"]


def _expense_tracker_template() -> Tuple[Dict[str, str], str, List[str]]:
    files = {
        "main.py": """from storage import load_expenses, total_by_category


def main():
    expenses = load_expenses("data/expenses.csv")
    print("Expense tracker summary")
    for category, total in total_by_category(expenses).items():
        print(category, "total:", total)


if __name__ == "__main__":
    main()
""",
        "storage.py": """import csv


def load_expenses(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append({"category": row["category"], "amount": float(row["amount"])})
    return rows


def total_by_category(expenses):
    totals = {}
    for item in expenses:
        totals[item["category"]] = totals.get(item["category"], 0) + item["amount"]
    return totals
""",
        "data/expenses.csv": "category,amount\nfood,120\nbooks,250\nfood,80\n",
        "README.md": """# Expense Tracker

Run `main.py`. The project reads a CSV file from the project root and totals spending by category.
""",
        "requirements.txt": "",
    }
    return files, "main.py", []


PROJECT_TEMPLATES = {
    "quiz": {
        "title": "Beginner quiz game",
        "description": "A three-file quiz with questions and scoring separated from the runner.",
        "factory": _quiz_template,
    },
    "pandas_marks": {
        "title": "Pandas marks analysis",
        "description": "A pandas project with a sample marks CSV and a data loader.",
        "factory": _pandas_marks_template,
    },
    "numpy_stats": {
        "title": "Numpy statistics",
        "description": "A numpy helper module plus a small test file.",
        "factory": _numpy_stats_template,
    },
    "expense_tracker": {
        "title": "CSV expense tracker",
        "description": "A multi-file project using the standard csv module for storage.",
        "factory": _expense_tracker_template,
    },
}


def build_template(template_id: str) -> Dict[str, Any]:
    key = str(template_id or "").strip().lower()
    if key not in PROJECT_TEMPLATES:
        raise KeyError(template_id)
    files, entry, requirements = PROJECT_TEMPLATES[key]["factory"]()
    manifest = make_manifest(
        files,
        name=PROJECT_TEMPLATES[key]["title"],
        entry=entry,
        active_file=entry,
        requirements=requirements,
    )
    return {
        "template_id": key,
        "title": PROJECT_TEMPLATES[key]["title"],
        "description": PROJECT_TEMPLATES[key]["description"],
        "files": files,
        "entry": entry,
        "active_file": entry,
        "requirements": requirements,
        "manifest": manifest,
        "speech": project_summary(manifest),
    }


def choose_template_for_prompt(prompt: str) -> str:
    lower = str(prompt or "").lower()
    if "quiz" in lower:
        return "quiz"
    if "pandas" in lower or "marks" in lower or "analysis" in lower:
        return "pandas_marks"
    if "numpy" in lower or "statistics" in lower or "stats" in lower:
        return "numpy_stats"
    if "expense" in lower or "csv storage" in lower or "tracker" in lower:
        return "expense_tracker"
    return ""


def looks_like_multifile_prompt(prompt: str) -> bool:
    lower = str(prompt or "").lower()
    signals = (
        "multi-file",
        "multifile",
        "multiple files",
        "split into",
        "project",
        "with tests",
        "requirements.txt",
        "pandas",
        "numpy",
        "csv storage",
        "data_loader",
    )
    return any(signal in lower for signal in signals)


def extract_project_json(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        files = normalize_file_map(data.get("files") or data.get("file_map"))
    except ProjectPathError:
        return {}
    if not files:
        return {}
    entry = data.get("entry") or data.get("main") or "main.py"
    active = data.get("active_file") or entry
    reqs = data.get("requirements") if isinstance(data.get("requirements"), list) else []
    manifest = make_manifest(
        files,
        name=data.get("name") or "Generated CodeUp Project",
        entry=entry,
        active_file=active,
        requirements=reqs,
    )
    return {
        "files": files,
        "entry": manifest["entry"],
        "active_file": manifest["active_file"],
        "requirements": manifest["requirements"],
        "manifest": manifest,
        "speech": data.get("speech") or data.get("explanation") or project_summary(manifest),
    }

import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app as app_module
from app import app
from project_support import extract_project_json, normalize_project_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("SNIPPETS_FILE", str(tmp_path / "snippets.json"))
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "DATA_DIR", str(data_dir))
    with app.test_client() as flask_client:
        yield flask_client


def test_single_file_generation_still_returns_code(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: "print('single file')")
    data = client.post("/generate-code", json={"prompt": "print hello", "language": "en"}).get_json()
    assert data["success"] is True
    assert data.get("project") is not True
    assert data["code"] == "print('single file')"


def test_project_json_file_map_is_parsed():
    raw = json.dumps({
        "name": "Demo",
        "entry": "main.py",
        "files": {"main.py": "from utils import answer\nprint(answer())", "utils.py": "def answer():\n    return 42\n"},
    })
    parsed = extract_project_json(raw)
    assert parsed["entry"] == "main.py"
    assert parsed["files"]["utils.py"].startswith("def answer")


def test_project_json_with_bad_path_is_ignored():
    raw = json.dumps({"files": {"../escape.py": "print('no')"}})
    assert extract_project_json(raw) == {}


def test_spoken_file_name_normalization():
    assert normalize_project_path("data loader dot py") == "data_loader.py"
    assert normalize_project_path("tests slash test main dot py") == "tests/test_main.py"


def test_generate_quiz_project_writes_files(client):
    data = client.post(
        "/generate-code",
        json={"prompt": "create a quiz game split into multiple files", "language": "en"},
    ).get_json()
    assert data["success"] is True
    assert data["project"] is True
    assert {"main.py", "questions.py", "score.py"}.issubset(data["files"])

    project = client.get("/project").get_json()
    assert project["success"] is True
    assert "questions.py" in project["files"]
    assert "Project has" in project["speech"]


@pytest.mark.timeout(15)
@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("create a quiz game split into multiple files", "Final score: 3 out of 3"),
        ("make a numpy statistics project with tests", "Numpy statistics project"),
        ("build a simple expense tracker with csv storage", "Expense tracker summary"),
    ],
)
def test_generated_multifile_project_prompts_run(client, prompt, expected):
    if "numpy" in prompt and importlib.util.find_spec("numpy") is None:
        pytest.skip("numpy is not installed")
    project = client.post("/generate-code", json={"prompt": prompt, "language": "en"}).get_json()
    assert project["success"] is True
    assert project["project"] is True

    data = client.post(
        "/run",
        json={"project": {"files": project["files"], "entry": project["entry"]}, "file": project["entry"]},
    ).get_json()
    assert data["success"] is True
    assert expected in data["output"]


@pytest.mark.timeout(15)
@pytest.mark.parametrize(
    "template_id,expected",
    [
        ("quiz", "Final score: 3 out of 3"),
        ("numpy_stats", "Numpy statistics project"),
        ("expense_tracker", "Expense tracker summary"),
    ],
)
def test_working_project_templates_run_from_root(client, template_id, expected):
    if template_id == "numpy_stats" and importlib.util.find_spec("numpy") is None:
        pytest.skip("numpy is not installed")
    project = client.post(f"/project/templates/{template_id}").get_json()
    assert project["success"] is True
    assert project["project"] is True

    data = client.post(
        "/run",
        json={"project": {"files": project["files"], "entry": project["entry"]}, "file": project["entry"]},
    ).get_json()
    assert data["success"] is True
    assert expected in data["output"]


@pytest.mark.timeout(15)
def test_pandas_template_runs_or_reports_dependency(client):
    project = client.post("/project/templates/pandas_marks").get_json()
    assert project["success"] is True
    assert "pandas" in project["requirements"]
    assert "pandas" in project["files"]["requirements.txt"]

    data = client.post(
        "/run",
        json={"project": {"files": project["files"], "entry": project["entry"]}, "file": project["entry"]},
    ).get_json()
    if importlib.util.find_spec("pandas") is None:
        assert data["success"] is False
        combined = (data.get("error") or "") + (data.get("explanation") or "")
        assert "pandas" in combined.lower()
        assert "requirements" in combined.lower() or "not installed" in combined.lower()
    else:
        assert data["success"] is True
        assert "Student marks summary" in data["output"]


@pytest.mark.timeout(15)
def test_run_project_file_resolves_local_imports(client):
    files = {
        "main.py": "from utils import double\nprint(double(21))\n",
        "utils.py": "def double(value):\n    return value * 2\n",
        "requirements.txt": "",
    }
    data = client.post("/run", json={"project": {"files": files, "entry": "main.py"}, "file": "main.py"}).get_json()
    assert data["success"] is True
    assert "42" in data["output"]


@pytest.mark.timeout(15)
def test_project_error_names_imported_file_and_line(client):
    files = {
        "main.py": "from utils import fail\nfail()\n",
        "utils.py": "def fail():\n    return 1 / 0\n",
    }
    data = client.post("/run", json={"project": {"files": files, "entry": "main.py"}, "file": "main.py"}).get_json()
    assert data["success"] is False
    assert "utils.py line 2" in data["error"]


def test_project_file_open_rename_delete(client):
    created = client.post("/project/files", json={"path": "data loader dot py", "content": "VALUE = 1\n"}).get_json()
    assert created["success"] is True
    assert created["path"] == "data_loader.py"

    opened = client.post("/project/open", json={"path": "data_loader.py"}).get_json()
    assert opened["success"] is True
    assert "VALUE" in opened["content"]

    renamed = client.post(
        "/project/rename",
        json={"old_path": "data_loader.py", "new_path": "analysis dot py"},
    ).get_json()
    assert renamed["success"] is True
    assert renamed["path"] == "analysis.py"

    deleted = client.post("/project/delete", json={"path": "analysis.py"}).get_json()
    assert deleted["success"] is True


def test_voice_project_commands_route(client):
    assert client.post("/voice-command", json={"text": "read project files"}).get_json()["action"] == "read_project_files"
    open_data = client.post("/voice-command", json={"text": "open main dot py"}).get_json()
    assert open_data["action"] == "open_project_file"
    assert open_data["path"] == "main dot py"
    create_data = client.post("/voice-command", json={"text": "create file data loader dot py"}).get_json()
    assert create_data["action"] == "create_project_file"
    run_data = client.post("/voice-command", json={"text": "run main dot py"}).get_json()
    assert run_data["action"] == "run_project_file"


@pytest.mark.parametrize(
    "text,prompt_fragment",
    [
        ("create a quiz game split into multiple files", "quiz game split into multiple files"),
        ("make a student marks analysis project using pandas", "student marks analysis project using pandas"),
        ("make a numpy statistics project with tests", "numpy statistics project with tests"),
    ],
)
def test_voice_natural_project_generation_routes(client, text, prompt_fragment):
    data = client.post("/voice-command", json={"text": text}).get_json()
    assert data["action"] == "generate_code"
    assert prompt_fragment in data["prompt"]


@pytest.mark.timeout(15)
def test_safe_open_can_read_project_csv(client):
    files = {
        "main.py": "import csv\nwith open('data/items.csv', newline='', encoding='utf-8') as handle:\n    rows = list(csv.DictReader(handle))\nprint(rows[0]['name'])\n",
        "data/items.csv": "name\nAsha\n",
    }
    data = client.post("/run", json={"project": {"files": files, "entry": "main.py"}, "file": "main.py"}).get_json()
    assert data["success"] is True
    assert "Asha" in data["output"]


@pytest.mark.timeout(15)
def test_safe_pathlib_can_read_project_file(client):
    files = {
        "main.py": "from pathlib import Path\nprint(Path('data/message.txt').read_text(encoding='utf-8'))\n",
        "data/message.txt": "hello from pathlib",
    }
    data = client.post("/run", json={"project": {"files": files, "entry": "main.py"}, "file": "main.py"}).get_json()
    assert data["success"] is True
    assert "hello from pathlib" in data["output"]


@pytest.mark.timeout(15)
def test_outside_project_file_access_is_blocked(client):
    data = client.post("/run", json={"code": "open('../outside.txt', 'w').write('x')"}).get_json()
    assert data["success"] is False
    assert "project root" in data["error"].lower() or "limited" in data["error"].lower()


@pytest.mark.timeout(15)
def test_dangerous_imports_still_blocked(client):
    data = client.post("/run", json={"code": "import os\nprint(os.getcwd())"}).get_json()
    assert data["success"] is False
    assert "not allowed" in data["error"].lower()


@pytest.mark.timeout(15)
def test_numpy_import_works_if_installed(client):
    if importlib.util.find_spec("numpy") is None:
        pytest.skip("numpy is not installed")
    data = client.post("/run", json={"code": "import numpy as np\nprint(int(np.array([3]).sum()))"}).get_json()
    assert data["success"] is True
    assert "3" in data["output"]


@pytest.mark.timeout(15)
def test_pandas_import_works_or_fails_friendly(client):
    data = client.post("/run", json={"code": "import pandas as pd\nprint(pd.DataFrame({'a': [1]}).shape[0])"}).get_json()
    if importlib.util.find_spec("pandas") is None:
        assert data["success"] is False
        combined = (data.get("error") or "") + (data.get("explanation") or "")
        assert "pandas" in combined.lower()
        assert "requirements" in combined.lower() or "not installed" in combined.lower() or "no module named" in combined.lower()
    else:
        assert data["success"] is True
        assert "1" in data["output"]

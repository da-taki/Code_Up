import pytest

import app as app_module
import report_support


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


SAMPLE = (
    "def greet(name):\n"
    "    if name:\n"
    "        for i in range(2):\n"
    "            print(f'hi {name}')\n"
    "\n"
    "greeting = 'world'\n"
    "greet(greeting)\n"
)



class TestConcepts:

    def test_detects_core_concepts(self):
        concepts = report_support.detect_python_concepts(SAMPLE)
        assert "print output" in concepts
        assert "variables" in concepts
        assert "loops" in concepts
        assert "functions" in concepts
        assert "conditionals (if/else)" in concepts

    def test_empty_code_no_concepts(self):
        assert report_support.detect_python_concepts("") == []

    def test_broken_code_still_reports_something(self):
        concepts = report_support.detect_python_concepts("for i in range(3)\n    print(i)")
        assert "loops" in concepts and "print output" in concepts



class TestFileRoles:

    def test_roles_inferred(self):
        files = {
            "main.py": 'if __name__ == "__main__":\n    pass\n',
            "helper.py": "def f():\n    return 1\n",
            "tests/test_main.py": "def test_x():\n    assert True\n",
            "data/marks.csv": "a,b\n",
            "requirements.txt": "pandas\n",
            "README.md": "# Docs\n",
        }
        by_path = {r["path"]: r["role"] for r in report_support.summarize_files(files)}
        assert by_path["main.py"] in ("entry point / runner", "runnable module")
        assert by_path["tests/test_main.py"] == "tests"
        assert by_path["data/marks.csv"] == "sample data"
        assert by_path["requirements.txt"] == "dependency list"
        assert by_path["README.md"] == "documentation"



class TestReport:

    def test_single_file_report(self, client):
        d = client.post("/project-report", json={"code": SAMPLE}).get_json()
        assert d["success"] is True
        assert any(f["path"] == "main.py" for f in d["files"])
        assert "How to run" in d["report_md"]
        assert {"print output", "loops", "functions"} <= set(d["concepts"])

    def test_multi_file_report_lists_files(self, client):
        project = {"name": "Marks", "files": {
            "main.py": "from loader import load\nprint(load())\n",
            "loader.py": "def load():\n    return 42\n",
        }, "entry": "main.py"}
        d = client.post("/project-report", json={"project": project}).get_json()
        paths = {f["path"] for f in d["files"]}
        assert paths == {"main.py", "loader.py"}
        assert d["is_project"] is True
        assert "main.py" in d["run_instruction"]

    def test_report_includes_run_instruction(self, client):
        d = client.post("/project-report", json={"code": "print(1)"}).get_json()
        assert d["run_instruction"]
        assert "run" in d["run_instruction"].lower()

    def test_report_includes_last_run_result(self, client):
        import session_memory
        mem = session_memory.new_memory()
        session_memory.record_run(mem, error="NameError: name 'x' is not defined", ran_ok=False)
        rep = report_support.build_project_report({"is_project": False, "code": "print(x)"}, mem)
        assert "error" in rep["last_run"].lower()
        assert "NameError" in rep["last_run"]

    def test_report_does_not_invent_files(self, client):
        d = client.post("/project-report", json={"code": "x = 1\n"}).get_json()
        assert [f["path"] for f in d["files"]] == ["main.py"]

    def test_empty_has_no_content(self, client):
        d = client.post("/project-report", json={"code": "   "}).get_json()
        assert d["has_content"] is False

    def test_concise_verbosity_shortens_speech(self):
        full = report_support.build_project_report({"is_project": False, "code": SAMPLE}, None, verbosity="normal")
        concise = report_support.build_project_report({"is_project": False, "code": SAMPLE}, None, verbosity="concise")
        assert len(concise["speech"]) <= len(full["speech"])

    def test_voice_command_routes_to_report(self, client):
        d = client.post("/voice-command", json={"text": "make a project report"}).get_json()
        assert d["action"] == "project_report"



class TestBehaviorExplanation:

    LOOP = "for i in range(3):\n    print(i)\n"

    def test_single_file_loop_report_explains_loop_behavior(self, client):
        d = client.post("/project-report", json={"code": self.LOOP}).get_json()
        speech = d["speech"].lower()
        assert "for loop" in speech
        assert "range(3)" in speech
        assert "0, 1, and 2" in speech
        assert "repeat" in speech
        assert "What this program does" in d["report_md"]
        assert d["behavior"]

    def test_variable_print_report_explains_assignment_and_use(self, client):
        d = client.post("/project-report", json={"code": 'name = "Asha"\nprint(name)\n'}).get_json()
        speech = d["speech"].lower()
        assert "variable name" in speech
        assert "print" in speech

    def test_function_report_explains_function_role(self, client):
        d = client.post("/project-report", json={"code": "def greet(n):\n    print(n)\n\ngreet(2)\n"}).get_json()
        assert "greet" in d["speech"]
        assert "function" in d["speech"].lower()

    def test_report_speech_mentions_last_output_when_present(self):
        import session_memory
        mem = session_memory.new_memory()
        session_memory.record_run(mem, output="0\n1\n2\n", ran_ok=True)
        rep = report_support.build_project_report({"is_project": False, "code": self.LOOP}, mem)
        assert "last successful output was 0, 1, 2" in rep["speech"].lower()

    def test_report_speech_mentions_last_error_when_present(self):
        import session_memory
        mem = session_memory.new_memory()
        session_memory.record_run(mem, error="NameError: name 'x' is not defined", ran_ok=False)
        rep = report_support.build_project_report({"is_project": False, "code": "print(x)\n"}, mem)
        assert "error" in rep["speech"].lower()

    def test_multifile_report_explains_file_roles_and_how_they_connect(self, client):
        project = {"name": "Marks", "files": {
            "main.py": "from loader import load\nprint(load())\n",
            "loader.py": "def load():\n    return 42\n",
        }, "entry": "main.py"}
        d = client.post("/project-report", json={"project": project}).get_json()
        md = d["report_md"]
        assert "loader.py" in md and "main.py" in md
        speech_and_md = (d["speech"] + " " + md).lower()
        assert "entry point" in speech_and_md
        assert "loader" in d["speech"].lower()

    def test_behavior_does_not_invent_for_plain_statements(self):
        sents = " ".join(report_support.describe_program_behavior("x = 1\ny = 2\nprint(x + y)\n")).lower()
        assert "for loop" not in sents
        assert "function" not in sents

    def test_syntax_error_code_reported_honestly(self):
        sents = report_support.describe_program_behavior("for i in range(3)\n    print(i)\n")
        assert any("syntax error" in s.lower() for s in sents)

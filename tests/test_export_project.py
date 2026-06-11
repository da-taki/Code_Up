"""
Safe project export (ZIP).

Covers the pure safety/zip helpers in export_support.py and the Flask routes
(/export-project, /download-export/<id>). The export is read-only: it never runs
code, never calls cloud AI, and writes no ZIP to disk (the bytes live only in a
short-lived in-memory per-session store).
"""
import io
import os
import zipfile

import pytest

import app as app_module
import export_support


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _names(zip_bytes):
    return set(zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist())


# =====================================================================
# Pure safety helpers
# =====================================================================

class TestSafety:

    @pytest.mark.parametrize("path", [
        ".env", ".env.local", "sub/.env", ".git/config", "x/__pycache__/m.pyc",
        "node_modules/lib/index.js", ".claude/launch.json", ".venv/pyvenv.cfg",
        "secret.key", "cert.pem", "app.log", "build/out.bin",
    ])
    def test_excluded_paths(self, path):
        assert export_support.is_excluded_path(path) is True

    @pytest.mark.parametrize("path", ["main.py", "utils.py", "data/marks.csv", "README.md", "tests/test_main.py"])
    def test_included_paths(self, path):
        assert export_support.is_excluded_path(path) is False

    def test_secret_content_detected(self):
        assert export_support.content_has_secret("GROQ_API_KEY=abc123")
        assert export_support.content_has_secret("API_KEY=zzz")
        assert export_support.content_has_secret("-----BEGIN PRIVATE KEY-----")
        assert not export_support.content_has_secret('password = input("pw: ")')

    def test_safe_file_map_filters(self):
        files = {
            "main.py": "print('hi')",
            ".env": "GROQ_API_KEY=secret",
            "leak.py": "OPENAI_API_KEY=sk-xxx",
            "data/marks.csv": "a,b\n1,2\n",
            ".git/config": "[core]",
            "node_modules/x.js": "x",
            "__pycache__/m.pyc": "x",
        }
        kept, excluded = export_support.safe_file_map(files)
        assert set(kept) == {"main.py", "data/marks.csv"}
        reasons = {e["path"]: e["reason"] for e in excluded}
        assert reasons[".env"] == "excluded_path"
        assert reasons["leak.py"] == "secret_content"


# =====================================================================
# Routes
# =====================================================================

class TestExportRoute:

    def test_single_file_export_has_main_py(self, client):
        data = client.post("/export-project", json={"code": "print('hello')"}).get_json()
        assert data["success"] is True
        assert data["download_url"].startswith("/download-export/")
        assert data["included"] == ["main.py"]

    def test_multi_file_export(self, client):
        project = {"name": "Demo", "files": {"main.py": "import helper", "helper.py": "x = 1"}}
        data = client.post("/export-project", json={"project": project}).get_json()
        assert data["success"] is True
        assert set(data["included"]) == {"main.py", "helper.py"}

    def test_export_excludes_junk_and_secrets(self, client):
        project = {"files": {
            "main.py": "print(1)",
            ".env": "GROQ_API_KEY=secret",
            ".git/config": "x",
            "__pycache__/m.pyc": "x",
            ".claude/launch.json": "x",
            "node_modules/lib.js": "x",
            "creds.py": "SECRET=topsecret",
        }}
        data = client.post("/export-project", json={"project": project}).get_json()
        dl = client.get(data["download_url"])
        names = _names(dl.data)
        assert names == {"main.py"}
        for bad in (".env", ".git/config", "__pycache__/m.pyc", ".claude/launch.json", "node_modules/lib.js", "creds.py"):
            assert bad not in names

    def test_download_returns_zip(self, client):
        data = client.post("/export-project", json={"code": "print('z')"}).get_json()
        dl = client.get(data["download_url"])
        assert dl.status_code == 200
        assert dl.headers["Content-Type"] == "application/zip"
        assert "attachment" in dl.headers["Content-Disposition"]
        assert _names(dl.data) == {"main.py"}

    def test_no_content_asks_to_create(self, client):
        data = client.post("/export-project", json={"code": "   "}).get_json()
        assert data["success"] is False
        assert data.get("needs_content") is True

    def test_export_does_not_call_cloud_ai(self, client, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("export must not call cloud AI")
        monkeypatch.setattr(app_module, "call_gemini", boom)
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", boom)
        data = client.post("/export-project", json={"code": "print('x')"}).get_json()
        assert data["success"] is True

    def test_voice_command_routes_to_export(self, client):
        d = client.post("/voice-command", json={"text": "export this project"}).get_json()
        assert d["action"] == "export_project"


# =====================================================================
# No ZIP is written to a tracked path
# =====================================================================

class TestNoDiskWrites:

    def test_prepare_export_writes_no_files(self, client, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = set(os.listdir(tmp_path))
        result = export_support.prepare_export({"main.py": "print(1)"})
        assert result["success"] and isinstance(result["bytes"], bytes)
        # Nothing was written to disk; the bytes are in-memory only.
        assert set(os.listdir(tmp_path)) == before

    def test_repo_root_has_no_committed_zip(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        zips = [f for f in os.listdir(repo_root) if f.lower().endswith(".zip")]
        assert zips == []

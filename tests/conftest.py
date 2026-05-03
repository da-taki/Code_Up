# tests/conftest.py
import os
import pytest

@pytest.fixture(autouse=True)
def enable_testing_mode(tmp_path, monkeypatch):
    """Disable SameSite on session cookie so Flask test client sends it back.
    Also redirect snippet storage to a temp directory so per-session
    snippet files don't pollute the working directory during tests.
    """
    os.environ["FLASK_TESTING"] = "true"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as app_module
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    yield
    os.environ.pop("FLASK_TESTING", None)
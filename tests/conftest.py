import os

import pytest

os.environ.setdefault("FLASK_TESTING", "true")


@pytest.fixture(autouse=True)
def enable_testing_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_TESTING", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as app_module
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "RUN_RATE_WINDOW", 5)
    yield

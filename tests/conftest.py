# tests/conftest.py
import os
import pytest

@pytest.fixture(autouse=True)
def enable_testing_mode(monkeypatch):
    """Force Flask test client to preserve cookies between requests."""
    monkeypatch.setenv("FLASK_TESTING", "true")
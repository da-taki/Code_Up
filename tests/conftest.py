# tests/conftest.py
import os
import pytest

@pytest.fixture(autouse=True)
def enable_testing_mode():
    """Disable SameSite on session cookie so Flask test client sends it back."""
    os.environ["FLASK_TESTING"] = "true"
    yield
    os.environ.pop("FLASK_TESTING", None)
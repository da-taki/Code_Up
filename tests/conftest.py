import os
import time
import subprocess
import signal
import requests
import sys
import pytest


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="session")
def server_process():
    """Start the Flask dev server in a subprocess for tests and tear it down afterwards."""
    env = os.environ.copy()
    # ensure the app binds to 127.0.0.1:5000 unless overridden
    base = env.get("CODEUP_BASE", "http://127.0.0.1:5000")
    env["CODEUP_BASE"] = base
    # disable external Gemini API calls during tests to avoid timeouts
    env.setdefault("GEMINI_ENABLED", "0")

    # Launch the app using the same Python interpreter running pytest
    # Write server output to a log file so we can inspect errors without blocking
    log_path = os.path.join(PROJECT_ROOT, "tests", "server.log")
    log_fd = open(log_path, "ab")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=PROJECT_ROOT, env=env, stdout=log_fd, stderr=log_fd)

    # Wait for server to be available
    deadline = time.time() + 10
    url = base + "/snippets"
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code in (200, 204, 404):
                break
        except Exception:
            time.sleep(0.2)

    try:
        yield proc
    finally:
        # Teardown: terminate the process and close log file
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            log_fd.flush()
            log_fd.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def session(server_process):
    """Provide a requests.Session that tests can use. Depends on server_process to ensure server is running."""
    s = requests.Session()
    yield s
    s.close()

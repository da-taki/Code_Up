import os
import subprocess
import sys


def _read_model(env_value=None):
    env = os.environ.copy()
    env["FLASK_TESTING"] = "true"
    if env_value is None:
        env.pop("GROQ_MODEL", None)
    else:
        env["GROQ_MODEL"] = env_value
    result = subprocess.run(
        [sys.executable, "-c", "import app; print(app.GEMINI_MODEL)"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip().splitlines()[-1]


def test_default_main_groq_model_is_gpt_oss_120b():
    assert _read_model() == "openai/gpt-oss-120b"


def test_groq_model_env_overrides_main_model():
    assert _read_model("llama-3.1-8b-instant") == "llama-3.1-8b-instant"


def test_blank_groq_model_falls_back_to_default():
    assert _read_model("   ") == "openai/gpt-oss-120b"

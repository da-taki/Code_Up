from app import app
import json


def pretty(resp):
    try:
        print(json.dumps(resp.get_json(), indent=2))
    except Exception:
        print(resp.data.decode()[:1000])


with app.test_client() as c:
    print("\n== /run")
    r = c.post("/run", json={"code": "print('smoke')"})
    pretty(r)
    assert r.status_code == 200

    print("\n== /check-syntax")
    r = c.post("/check-syntax", json={"code": "def f(:\n pass"})
    pretty(r)
    assert r.get_json().get("has_errors") is True

    print("\n== /snippets (GET)")
    r = c.get("/snippets")
    pretty(r)
    assert r.status_code == 200

    print("\n== /voice-command (help)")
    r = c.post("/voice-command", json={"text": "help"})
    pretty(r)
    assert r.status_code == 200

    print("\n✅ Flask test client sanity checks passed")

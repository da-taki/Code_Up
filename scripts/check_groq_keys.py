#!/usr/bin/env python
"""Safe, on-demand Groq key health checker.

    python scripts/check_groq_keys.py

Uses the exact same key-loading logic as production (codeup.providers.
groq_pool, which itself reuses codeup.integrations.groq_key_manager) and
makes exactly ONE tiny request per configured key to classify it.

Never prints a raw key value, an env var's raw content, or an auth header -
only safe internal IDs (groq-key-01, ...) and result classifications.

This script is never imported by the test suite and never runs
automatically - it is meant to be run by a person, on demand, when they
want to know which of the configured keys currently work. It costs real
Groq API quota (one tiny request per key), so don't run it in a loop.
"""

from __future__ import annotations

import os
import sys

# Load .env the same way app.py does, before reading any GROQ_* env vars.
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codeup.providers import groq_pool  # noqa: E402


def main() -> int:
    values = groq_pool.load_pool_key_values()
    if not values:
        print("Configured Groq keys: 0")
        print("No GROQ_API_KEY / GROQ_API_KEYS / GROQ_API_KEY_2..15 found in the environment.")
        return 1

    print(f"Configured Groq keys: {len(values)}")
    print("Checking each key with one minimal request...")
    print()

    results = groq_pool.health_check_all()

    counts = {"healthy": 0, "rate_limited": 0, "invalid": 0, "unhealthy": 0}
    for result in results.values():
        counts[result] = counts.get(result, 0) + 1

    print(f"Healthy: {counts.get('healthy', 0)}")
    print(f"Rate limited: {counts.get('rate_limited', 0)}")
    print(f"Invalid: {counts.get('invalid', 0)}")
    print(f"Other transient errors: {counts.get('unhealthy', 0)}")
    print()

    for internal_id, result in results.items():
        print(f"{internal_id}: {result}")

    return 0 if counts.get("invalid", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

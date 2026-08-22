#!/usr/bin/env python
"""Manual local concurrency simulation for the Groq key pool.

    python scripts/groq_pool_load_check.py --requests 20 --concurrency 10

(Named without a "test_" prefix on purpose - pytest's default discovery
would otherwise try to collect this as a test module and collide with
tests/test_groq_pool.py, which is the real automated test suite.)

Sends a small number of genuinely tiny, harmless requests ("Say OK", capped
at a few output tokens) through the real pool (codeup.providers.groq_pool)
using whatever GROQ_API_KEY(s) are configured, from several threads at
once, to prove the pool actually distributes load, queues fairly, and
leaves no leaked capacity behind - NOT to probe or bypass Groq's rate
limits. Keep --requests small; this is a smoke test, not a load test.

Never prints a raw key value - only the safe internal IDs (groq-key-01,
...) that groq_pool itself uses everywhere.

This script is never imported by the test suite and never runs
automatically.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codeup.providers import groq_pool  # noqa: E402


def _tiny_request(api_key: str, internal_id: str) -> str:
    from groq import Groq
    client = Groq(api_key=api_key, timeout=15, max_retries=0)
    client.chat.completions.create(
        model=groq_pool.health_check_model(),
        messages=[{"role": "user", "content": "Say OK"}],
        max_tokens=3,
    )
    return internal_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    if not groq_pool.has_configured_keys():
        print("No Groq keys configured (GROQ_API_KEY / GROQ_API_KEYS / GROQ_API_KEY_2..15). Nothing to test.")
        return 1

    configured = len(groq_pool.load_pool_key_values())
    print(f"Configured Groq keys: {configured}")
    print(f"Sending {args.requests} tiny requests with up to {args.concurrency} concurrent workers...")
    print()

    succeeded = 0
    failed = 0
    keys_used: set = set()
    lock = threading.Lock()
    stop_monitor = threading.Event()
    max_queue_depth = [0]

    def monitor():
        while not stop_monitor.is_set():
            try:
                depth = groq_pool.status()["queued_requests"]
                with lock:
                    if depth > max_queue_depth[0]:
                        max_queue_depth[0] = depth
            except Exception:
                pass
            stop_monitor.wait(0.05)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()

    def worker(task_id: int):
        nonlocal succeeded, failed
        try:
            internal_id = groq_pool.call_with_pool(_tiny_request, timeout=groq_pool.queue_wait_timeout())
            with lock:
                succeeded += 1
                keys_used.add(internal_id)
        except Exception as exc:
            with lock:
                failed += 1
            print(f"  request {task_id} failed: {type(exc).__name__}")

    threads = []
    started_at = time.time()
    sem = threading.Semaphore(args.concurrency)

    def bounded_worker(task_id: int):
        with sem:
            worker(task_id)

    for i in range(args.requests):
        t = threading.Thread(target=bounded_worker, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    stop_monitor.set()
    monitor_thread.join(timeout=1)
    elapsed = time.time() - started_at

    status = groq_pool.status()
    slot_leaks = status["active_requests"]

    print()
    print(f"Requests: {args.requests}")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")
    print(f"Keys used: {len(keys_used)}/{configured}")
    print(f"Maximum queue depth: {max_queue_depth[0]}")
    print(f"Slot leaks (should be 0): {slot_leaks}")
    print(f"Elapsed: {elapsed:.1f}s")

    return 0 if failed == 0 and slot_leaks == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

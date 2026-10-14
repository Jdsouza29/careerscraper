#!/usr/bin/env python3
"""Per-host throttle checks for core.http.HttpClient.

No network and no test framework - run it directly:

    python tests/test_http_throttle.py
"""

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.http import HttpClient  # noqa: E402


def stamps_for(client, urls, workers):
    """Call _throttle for each url concurrently, returning (url, time) per call."""
    out, lock = [], threading.Lock()

    def claim(url):
        client._throttle(url)
        with lock:
            out.append((url, time.monotonic()))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(claim, urls))
    return out


def test_same_host_is_spaced_even_under_concurrency():
    interval = 0.1
    client = HttpClient(min_interval=interval)
    urls = ["https://one.example.com/jobs"] * 6

    stamps = sorted(time for _, time in stamps_for(client, urls, workers=6))
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]

    assert all(gap >= interval * 0.9 for gap in gaps), f"calls bunched up: {gaps}"
    print(f"  same host: 6 calls, min gap {min(gaps):.3f}s (interval {interval}s)")


def test_different_hosts_do_not_block_each_other():
    interval = 0.5
    client = HttpClient(min_interval=interval)
    urls = [f"https://host{n}.example.com/jobs" for n in range(8)]

    started = time.monotonic()
    stamps_for(client, urls, workers=8)
    elapsed = time.monotonic() - started

    # Serial would be 8 * 0.5 = 4s; per-host means the first call to each host is free.
    assert elapsed < interval, f"hosts blocked each other: {elapsed:.2f}s"
    print(f"  8 distinct hosts: {elapsed:.3f}s (serial would be {8 * interval:.1f}s)")


def test_per_host_override_applies():
    client = HttpClient(min_interval=1.0, host_intervals={"fast.example.com": 0.05})

    started = time.monotonic()
    stamps_for(client, ["https://fast.example.com/a"] * 4, workers=4)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"override ignored, took {elapsed:.2f}s"
    print(f"  overridden host: 4 calls in {elapsed:.3f}s (default would be 3.0s)")


def test_zero_interval_disables_throttle():
    client = HttpClient(min_interval=0)
    started = time.monotonic()
    stamps_for(client, ["https://open.example.com/a"] * 20, workers=4)
    assert time.monotonic() - started < 0.2
    print("  interval 0: no delay")


def test_each_thread_gets_its_own_session():
    client = HttpClient()
    # A barrier forces all four tasks to be in flight at once; without it the pool is
    # free to run every task on one reused thread and the check proves nothing.
    barrier = threading.Barrier(4)
    seen, lock = set(), threading.Lock()

    def record(_):
        session = client.session
        barrier.wait(timeout=5)
        with lock:
            seen.add(id(session))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(4)))

    assert len(seen) == 4, f"threads shared sessions: {len(seen)} for 4 threads"
    print(f"  sessions: {len(seen)} distinct across 4 concurrent threads")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        print(f"{test.__name__}:")
        test()
    print(f"\n{len(tests)} passed")

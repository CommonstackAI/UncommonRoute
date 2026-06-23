"""Measure routing overhead end-to-end on a warm process.

Runs `route()` N times on a fixed set of prompts and reports the per-call
wall-clock latency distribution (cold start, p50, p90, p99).

Usage:
    python scripts/bench_overhead.py                    # 200 iterations, default prompts
    python scripts/bench_overhead.py --iterations 1000  # heavier run
    python scripts/bench_overhead.py --json             # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uncommon_route.router.api import route

PROMPTS = [
    "hello",
    "fix the typo on line 3",
    "explain how asyncio.gather differs from asyncio.wait",
    "refactor this 500-line module into smaller focused files",
    "design a distributed scheduler that survives network partitions, "
    "supports exactly-once delivery, and scales to 10k workers",
]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[idx]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bench route() overhead")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    # Cold start: first call loads the embedding model. Measure it separately.
    cold_t0 = time.perf_counter()
    route(PROMPTS[0])
    cold_ms = (time.perf_counter() - cold_t0) * 1000.0

    # Warm: cycle through the prompt fixture.
    samples_ms: list[float] = []
    for i in range(args.iterations):
        prompt = PROMPTS[i % len(PROMPTS)]
        t0 = time.perf_counter()
        route(prompt)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)

    stats = {
        "iterations": args.iterations,
        "cold_start_ms": round(cold_ms, 2),
        "mean_ms": round(statistics.fmean(samples_ms), 2),
        "median_ms": round(statistics.median(samples_ms), 2),
        "p50_ms": round(_percentile(samples_ms, 50), 2),
        "p90_ms": round(_percentile(samples_ms, 90), 2),
        "p99_ms": round(_percentile(samples_ms, 99), 2),
        "min_ms": round(min(samples_ms), 2),
        "max_ms": round(max(samples_ms), 2),
    }

    if args.json:
        print(json.dumps(stats, indent=2))
        return

    print()
    print("=" * 48)
    print("  UncommonRoute — route() overhead")
    print("=" * 48)
    print(f"  Iterations     : {stats['iterations']}")
    print(f"  Cold start     : {stats['cold_start_ms']:>7.2f} ms")
    print(f"  Mean           : {stats['mean_ms']:>7.2f} ms")
    print(f"  Median (p50)   : {stats['p50_ms']:>7.2f} ms")
    print(f"  p90            : {stats['p90_ms']:>7.2f} ms")
    print(f"  p99            : {stats['p99_ms']:>7.2f} ms")
    print(f"  Min / Max      : {stats['min_ms']:>7.2f} / {stats['max_ms']:.2f} ms")
    print("=" * 48)


if __name__ == "__main__":
    main()

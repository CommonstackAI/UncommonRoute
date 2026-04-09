"""3-way stratified split of LLMRouterBench question bank.

Usage:
    python scripts/split_data.py --input ../LLMRouterBench/data/question_bank.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


def stratified_3way_split(
    rows: list[dict[str, Any]],
    seed: int = 42,
    train_frac: float = 0.64,
    cal_frac: float = 0.16,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(seed)
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = f"{row.get('benchmark', 'unknown')}_{row.get('target_tier', 'unknown')}"
        strata[key].append(row)

    train, cal, holdout = [], [], []
    for key in sorted(strata.keys()):
        group = list(strata[key])
        rng.shuffle(group)
        n = len(group)
        n_train = max(1, round(n * train_frac))
        n_cal = max(0, round(n * cal_frac))
        if n > 1:
            n_holdout = n - n_train - n_cal
            if n_holdout < 1:
                n_cal = max(0, n_cal - 1)
        train.extend(group[:n_train])
        cal.extend(group[n_train:n_train + n_cal])
        holdout.extend(group[n_train + n_cal:])

    rng.shuffle(train)
    rng.shuffle(cal)
    rng.shuffle(holdout)
    return train, cal, holdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", default="uncommon_route/data/v2_splits")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows = []
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    train, cal, holdout = stratified_3way_split(rows, seed=args.seed)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, split in [("train", train), ("calibration", cal), ("holdout", holdout)]:
        path = out_dir / f"{name}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for row in split:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{name}: {len(split)} rows -> {path}")
    print(f"\nTotal: {len(train)} + {len(cal)} + {len(holdout)} = {len(train) + len(cal) + len(holdout)}")


if __name__ == "__main__":
    main()

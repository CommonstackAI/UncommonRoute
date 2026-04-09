import sys
sys.path.insert(0, ".")

from scripts.split_data import stratified_3way_split


def _make_rows():
    rows = []
    for i in range(10):
        rows.append({"id": f"bk1_low_{i}", "benchmark": "bk1", "target_tier": "low", "target_tier_id": 0, "messages": []})
    for i in range(5):
        rows.append({"id": f"bk1_high_{i}", "benchmark": "bk1", "target_tier": "high", "target_tier_id": 3, "messages": []})
    for i in range(5):
        rows.append({"id": f"bk2_low_{i}", "benchmark": "bk2", "target_tier": "low", "target_tier_id": 0, "messages": []})
    return rows


def test_split_sizes():
    rows = _make_rows()
    train, cal, holdout = stratified_3way_split(rows, seed=42)
    total = len(train) + len(cal) + len(holdout)
    assert total == len(rows)
    assert len(train) >= 10
    assert len(cal) >= 2
    assert len(holdout) >= 2


def test_split_no_overlap():
    rows = _make_rows()
    train, cal, holdout = stratified_3way_split(rows, seed=42)
    train_ids = {r["id"] for r in train}
    cal_ids = {r["id"] for r in cal}
    holdout_ids = {r["id"] for r in holdout}
    assert train_ids.isdisjoint(cal_ids)
    assert train_ids.isdisjoint(holdout_ids)
    assert cal_ids.isdisjoint(holdout_ids)


def test_split_deterministic():
    rows = _make_rows()
    t1, c1, h1 = stratified_3way_split(rows, seed=42)
    t2, c2, h2 = stratified_3way_split(rows, seed=42)
    assert [r["id"] for r in t1] == [r["id"] for r in t2]
    assert [r["id"] for r in h1] == [r["id"] for r in h2]


def test_split_stratified():
    rows = _make_rows()
    train, cal, holdout = stratified_3way_split(rows, seed=42)
    train_bks = {r["benchmark"] for r in train}
    holdout_bks = {r["benchmark"] for r in holdout}
    assert "bk1" in train_bks
    assert "bk2" in train_bks
    assert "bk1" in holdout_bks

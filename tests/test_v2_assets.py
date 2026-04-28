from uncommon_route.v2_assets import V2_RUNTIME_ASSETS, ensure_v2_assets_deployed


def test_v2_runtime_assets_deploy_without_training_artifacts(monkeypatch, tmp_path) -> None:
    import uncommon_route.paths as paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)

    deployed = ensure_v2_assets_deployed()

    assert deployed == tmp_path / "v2_splits"
    for asset in V2_RUNTIME_ASSETS:
        assert (deployed / asset).is_file()
    assert not (deployed / "train.jsonl").exists()
    assert not (deployed / "holdout.jsonl").exists()

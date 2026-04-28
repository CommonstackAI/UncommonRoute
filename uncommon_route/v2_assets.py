"""Runtime deployment for v2 routing assets."""

from __future__ import annotations

import shutil
from pathlib import Path


V2_RUNTIME_ASSETS: tuple[str, ...] = (
    "seed_embeddings.npy",
    "seed_labels.json",
    "embedding_classifier.pkl",
    "meta_scaler.pkl",
    "calibration_params.json",
)


def package_v2_splits_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "v2_splits"


def ensure_v2_assets_deployed() -> Path:
    """Copy packaged v2 runtime assets into the user data directory.

    We keep training/evaluation artifacts out of the wheel, but the production
    seed index, classifier, scaler, and confidence calibration must be present
    for Signal C and calibrated routing to run after a normal install.
    """
    from uncommon_route.paths import data_dir

    user_splits = data_dir() / "v2_splits"
    pkg_splits = package_v2_splits_dir()
    user_splits.mkdir(parents=True, exist_ok=True)

    for fname in V2_RUNTIME_ASSETS:
        user_file = user_splits / fname
        pkg_file = pkg_splits / fname
        if not user_file.exists() and pkg_file.exists():
            shutil.copy2(pkg_file, user_file)

    return user_splits

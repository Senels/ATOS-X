"""Deterministic dataset validation and manifest helpers.

This module is deliberately exchange-specific at the contract level: the
training dataset is expected to originate from Binance Global USDⓈ-M Futures.
It does not add an exchange adapter or perform live trading.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def validate_ohlcv(df: pd.DataFrame, *, require_sorted: bool = True) -> list[str]:
    """Return deterministic data-quality errors for an OHLCV frame."""
    errors: list[str] = []
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"missing_columns:{','.join(missing)}")
        return errors

    if df.empty:
        errors.append("empty_dataset")
        return errors

    if df.index.has_duplicates:
        errors.append("duplicate_timestamps")
    if require_sorted and not df.index.is_monotonic_increasing:
        errors.append("timestamps_not_sorted")

    numeric = df.loc[:, REQUIRED_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        errors.append("non_numeric_or_null_ohlcv")

    if (numeric["high"] < numeric[["open", "close"]].max(axis=1)).any():
        errors.append("high_below_open_or_close")
    if (numeric["low"] > numeric[["open", "close"]].min(axis=1)).any():
        errors.append("low_above_open_or_close")
    if (numeric["volume"] < 0).any():
        errors.append("negative_volume")
    return errors


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest of a dataset file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    files: Iterable[str | Path],
    *,
    exchange: str = "binance_global_usdm",
    interval: str = "4h",
) -> dict:
    """Build a stable JSON-serializable manifest for dataset inputs."""
    entries = []
    for raw in files:
        path = Path(raw)
        entries.append({
            "path": str(path.as_posix()),
            "sha256": file_sha256(path),
            "bytes": path.stat().st_size,
        })
    entries.sort(key=lambda item: item["path"])
    payload = {
        "schema_version": 1,
        "exchange": exchange,
        "interval": interval,
        "files": entries,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload

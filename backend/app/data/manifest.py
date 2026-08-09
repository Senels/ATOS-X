"""Reproducible dataset manifests for historical market data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    files: Iterable[str | Path],
    *,
    exchange: str = "binance_um_futures",
    symbols: Iterable[str] = (),
    timeframes: Iterable[str] = (),
    source_version: str = "1",
) -> dict:
    entries = []
    for raw in sorted((str(Path(p)) for p in files)):
        path = Path(raw)
        if not path.is_file():
            raise FileNotFoundError(raw)
        entries.append({"path": raw, "sha256": sha256_file(path), "bytes": path.stat().st_size})

    return {
        "schema_version": 1,
        "source_version": source_version,
        "exchange": exchange,
        "symbols": sorted({s.upper() for s in symbols}),
        "timeframes": sorted(set(timeframes)),
        "files": entries,
    }


def write_manifest(manifest: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

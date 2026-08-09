from pathlib import Path

import pandas as pd

from app.ai.dataset import build_manifest, file_sha256, validate_ohlcv


def _frame():
    idx = pd.date_range("2026-01-01", periods=3, freq="4h")
    return pd.DataFrame(
        {
            "open": [100, 101, 102],
            "high": [102, 103, 104],
            "low": [99, 100, 101],
            "close": [101, 102, 103],
            "volume": [10, 11, 12],
        },
        index=idx,
    )


def test_validate_ohlcv_accepts_valid_frame():
    assert validate_ohlcv(_frame()) == []


def test_validate_ohlcv_detects_duplicates_and_bad_prices():
    df = _frame()
    df.index = [df.index[0], df.index[0], df.index[2]]
    df.loc[df.index[2], "volume"] = -1
    assert "duplicate_timestamps" in validate_ohlcv(df)
    assert "negative_volume" in validate_ohlcv(df)


def test_manifest_is_deterministic(tmp_path: Path):
    a = tmp_path / "BTCUSDT_4h.csv"
    b = tmp_path / "ETHUSDT_4h.csv"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    first = build_manifest([b, a])
    second = build_manifest([a, b])

    assert first == second
    assert first["exchange"] == "binance_global_usdm"
    assert first["manifest_sha256"]
    assert file_sha256(a) != file_sha256(b)

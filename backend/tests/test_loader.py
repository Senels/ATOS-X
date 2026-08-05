from pathlib import Path

import pandas as pd
import pytest

from app.data import loader


def test_is_stablecoin_symbol():
    assert loader.is_stablecoin_symbol("USDCUSDT") is True
    assert loader.is_stablecoin_symbol("FDUSDUSDT") is True
    assert loader.is_stablecoin_symbol("DAIUSDT") is True
    assert loader.is_stablecoin_symbol("BTCUSDT") is False
    assert loader.is_stablecoin_symbol("1000PEPEUSDT") is False
    assert loader.is_stablecoin_symbol("XAUUSDT") is False
    assert loader.is_stablecoin_symbol("SOLUSDT") is False


def test_data_dir_default_and_custom(tmp_path):
    assert loader._data_dir("4h") == loader.DEFAULT_DATA_DIR / "futures_4h_data"
    assert loader._data_dir("1h", str(tmp_path)) == Path(tmp_path)


def _write_csv(data_dir, symbol="BTCUSDT", interval="4h", rows=3):
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    lines = ["timestamp,open,high,low,close,volume"]
    start = 1712304000000
    for i in range(rows):
        lines.append(
            f"{start + i * 3600000},{65000 + i}.1,{65100 + i}.0,"
            f"{64800 + i}.5,{65050 + i}.0,{100 + i}.5"
        )
    (d / f"{symbol}_{interval}.csv").write_text("\n".join(lines), encoding="utf-8")


def test_load_csv_reads_and_sets_index(tmp_path):
    _write_csv(tmp_path)
    df = loader.load_csv("BTCUSDT", "4h", data_dir=str(tmp_path))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 3
    assert df.index.tz is not None
    assert df["close"].dtype == float
    assert df["close"].iloc[-1] == 65052.0


def test_load_csv_limit(tmp_path):
    _write_csv(tmp_path, rows=5)
    df = loader.load_csv("BTCUSDT", "4h", data_dir=str(tmp_path), limit=2)
    assert len(df) == 2


def test_load_csv_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        loader.load_csv("NOPEUSDT", "4h", data_dir=str(tmp_path))


def test_list_symbols(tmp_path):
    _write_csv(tmp_path, symbol="BTCUSDT")
    _write_csv(tmp_path, symbol="ETHUSDT")
    assert loader.list_symbols("4h", data_dir=str(tmp_path)) == ["BTCUSDT", "ETHUSDT"]
    assert loader.list_symbols("4h", data_dir=str(tmp_path / "empty")) == []


def test_dataframe_from_klines_returns_copy():
    df = pd.DataFrame({"a": [1]})
    out = loader.dataframe_from_klines(df)
    assert out is not df
    assert out.equals(df)

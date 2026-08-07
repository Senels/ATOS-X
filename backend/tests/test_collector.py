import numpy as np
import pandas as pd

from app.data import collector
from app.data.loader import load_csv


class FakeClient:
    def __init__(self, n_bars=120, raise_for=None):
        self.n_bars = n_bars
        self.raise_for = set(raise_for or [])
        self.calls = []

    async def get_klines(self, symbol, interval="4h", limit=1000, start_time=None):
        self.calls.append((symbol, interval, limit, start_time))
        if symbol in self.raise_for:
            raise Exception("kline error")
        rng = np.random.default_rng(3)
        close = 100 + np.cumsum(rng.normal(0, 0.3, self.n_bars))
        idx = pd.date_range("2026-01-01", periods=self.n_bars, freq="4h", tz="UTC")
        return pd.DataFrame({
            "open": close - 0.1, "high": close + 0.4, "low": close - 0.4,
            "close": close, "volume": rng.uniform(50, 300, self.n_bars),
        }, index=idx)


async def test_collect_writes_loader_compatible_csv(tmp_path):
    fb = FakeClient()
    res = await collector.collect(fb, ["BTCUSDT"], interval="4h", bars=120,
                            data_dir=str(tmp_path))
    assert res["written"] == ["BTCUSDT"]
    assert res["failed"] == []
    df = load_csv("BTCUSDT", interval="4h", data_dir=str(tmp_path))
    assert len(df) == 120
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.tz is not None


async def test_collect_skips_stablecoins(tmp_path):
    fb = FakeClient()
    res = await collector.collect(fb, ["BTCUSDT", "USDCUSDT"], interval="4h",
                            data_dir=str(tmp_path))
    assert res["written"] == ["BTCUSDT"]
    assert res["skipped"] == ["USDCUSDT"]


async def test_collect_marks_failed_symbol(tmp_path):
    fb = FakeClient(raise_for={"ETHUSDT"})
    res = await collector.collect(fb, ["BTCUSDT", "ETHUSDT"], interval="4h",
                            data_dir=str(tmp_path))
    assert "BTCUSDT" in res["written"]
    assert "ETHUSDT" in res["failed"]


async def test_collect_short_data_fails(tmp_path):
    fb = FakeClient(n_bars=1)
    res = await collector.collect(fb, ["BTCUSDT"], interval="4h", data_dir=str(tmp_path))
    assert res["written"] == []
    assert res["failed"] == ["BTCUSDT"]


async def test_backfill_writes_csv(tmp_path):
    fb = FakeClient()
    res = await collector.backfill(fb, ["BTCUSDT"], interval="4h", days=10,
                             data_dir=str(tmp_path))
    assert res["written"] == ["BTCUSDT"]
    df = load_csv("BTCUSDT", interval="4h", data_dir=str(tmp_path))
    assert len(df) >= 2


async def test_backfill_passes_start_time(tmp_path):
    fb = FakeClient()
    await collector.backfill(fb, ["BTCUSDT"], interval="4h", days=10,
                       data_dir=str(tmp_path))
    assert any(call[3] is not None for call in fb.calls)


async def test_backfill_skips_stablecoin(tmp_path):
    fb = FakeClient()
    res = await collector.backfill(fb, ["USDCUSDT"], interval="4h", days=10,
                             data_dir=str(tmp_path))
    assert res["written"] == []
    assert fb.calls == []


async def test_backfill_marks_failed(tmp_path):
    fb = FakeClient(raise_for={"ETHUSDT"})
    res = await collector.backfill(fb, ["ETHUSDT"], interval="4h", days=10,
                             data_dir=str(tmp_path))
    assert res["written"] == []
    assert res["failed"] == ["ETHUSDT"]




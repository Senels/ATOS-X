"""TTPTSL canli pipeline dogrulama testleri (gercek CSV verisi + paper mod).

AutoTrader zaten `get_strategy` uzerinden strateji-agnostik calisir; bu testler
TTPTSL'nin gercek veriyle canli akista (sinyal -> paper pozisyon) dogru
calistigini uctan uca dogrular.
"""
import app.strategy.auto_trader as at_mod
import pytest
from app.strategy import get_strategy
from app.strategy import settings as ss
from app.strategy.ttp import TtpTsl


class FakeBinancePaper:
    def __init__(self):
        self.client = None
        self.testnet = False

    async def connect(self):
        self.client = True
        return True

    async def load_all_symbols(self):
        return ["BTCUSDT"]

    async def get_all_tickers(self):
        return {"BTCUSDT": 0.0}

    async def get_klines(self, symbol, interval, limit):
        return None

    async def get_price(self, symbol="BTCUSDT"):
        return 0.0

    async def place_market_order(self, symbol, side, quantity):
        return {"symbol": symbol, "side": side, "quantity": quantity, "paper": True}

    async def close_position(self, symbol):
        return {"symbol": symbol}


@pytest.fixture
def paper_trader(tmp_path, monkeypatch):
    db = __import__("app.core.database", fromlist=["Database"]).Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    prev = ss.get_settings()
    ss.update_settings({"active_strategy": "ttp"})
    try:
        tr = at_mod.AutoTrader(FakeBinancePaper(), paper=True)
        tr.trading_symbols = ["BTCUSDT"]
        yield tr
    finally:
        ss.update_settings(prev)


def _first_signal(btc_df):
    bot = TtpTsl()
    orders = bot.analyze(btc_df)["orders"]
    sig_bars = orders.index[orders["signal"] != 0]
    assert len(sig_bars) > 0, "gercek veride TTPTSL sinyali uretilmeli"
    i = sig_bars[0]
    pos = btc_df.index.get_loc(i)
    return {
        "close": float(btc_df.iloc[pos]["close"]),
        "sl": float(orders.loc[i, "sl"]),
        "tp": float(orders.loc[i, "tp"]),
        "signal": int(orders.loc[i, "signal"]),
    }


def test_ttp_analyze_real_data_contract(btc_df):
    r = TtpTsl().analyze(btc_df)
    orders = r["orders"]
    assert len(orders) == len(btc_df)
    sig = orders[orders["signal"] != 0]
    assert len(sig) > 0
    assert sig["sl"].notna().all()
    assert sig["tp"].notna().all()
    for i in sig.index[:5]:
        pos = btc_df.index.get_loc(i)
        close = float(btc_df.iloc[pos]["close"])
        side = int(orders.loc[i, "signal"])
        sl = float(orders.loc[i, "sl"])
        tp = float(orders.loc[i, "tp"])
        if side == 1:
            assert sl < close < tp
        else:
            assert sl > close > tp


def test_ttp_live_bot_selection(paper_trader):
    bot = get_strategy(ss.get_settings())
    assert isinstance(bot, TtpTsl)


async def test_ttp_signal_to_paper_position(tmp_path, monkeypatch, btc_df, paper_trader):
    tr = paper_trader
    info = _first_signal(btc_df)
    side = "BUY" if info["signal"] == 1 else "SELL"
    await tr.process_signals([{
        "symbol": "BTCUSDT",
        "signal": side,
        "price": info["close"],
        "sl": info["sl"],
        "tp": info["tp"],
        "reason": "TTPTSL canli dogrulama",
    }])
    assert "BTCUSDT" in tr.active_positions
    pos = tr.active_positions["BTCUSDT"]
    assert pos["side"] == side
    assert pos["sl"] == info["sl"]
    assert pos["tp"] == info["tp"]


def test_ttp_rank_symbols_real_data(tmp_path, monkeypatch, btc_df, paper_trader):
    tr = paper_trader
    ranked = tr.rank_symbols(limit=300)
    assert isinstance(ranked, list)
    assert ranked == [] or all(s in tr.trading_symbols for s in ranked)

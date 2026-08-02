from app.notifications.telegram import format_daily_summary


def _trade(symbol, pnl):
    return [0, symbol, "BUY", 100.0, 110.0, 1.0, pnl, "CLOSED", "t", "t"]


def test_daily_summary_counts_and_pnl():
    trades = [_trade("BTCUSDT", 500.0), _trade("ETHUSDT", -200.0), _trade("SOLUSDT", 100.0)]
    msg = format_daily_summary(trades, equity=10500.0, open_positions={"XRPUSDT": {}})
    assert "Kapanan islem: 3 (2W/1L)" in msg
    assert "Win Rate: 66.7%" in msg
    assert "Gunluk PnL: <b>+400.00</b>" in msg
    assert "En iyi: BTCUSDT +500.00" in msg
    assert "Equity: <b>$10500.00</b>" in msg
    assert "Acik pozisyon: 1" in msg


def test_daily_summary_empty():
    msg = format_daily_summary([], equity=10000.0, open_positions={})
    assert "Kapanan islem: 0 (0W/0L)" in msg
    assert "Win Rate: 0.0%" in msg
    assert "Gunluk PnL: <b>+0.00</b>" in msg
    assert "Acik pozisyon: 0" in msg
    assert "Tarama:" not in msg


def test_daily_summary_top_symbols():
    msg = format_daily_summary([], equity=10000.0, open_positions={}, top_symbols=["BTCUSDT", "ETHUSDT"])
    assert "Tarama: BTCUSDT, ETHUSDT" in msg

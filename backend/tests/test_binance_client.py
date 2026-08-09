import time
import types

import pytest

import app.exchange.binance_client as bc_mod
from app.exchange.binance_client import BinanceClient


async def _async_none(*a, **k):
    return None


async def _async_false(*a, **k):
    return False


def make_client(bc, **methods):
    """bc.client'i sahte yontemlerle doldurur; gercek `_run` (thread executor) kullanilir."""
    fc = types.SimpleNamespace()
    for name, fn in methods.items():
        setattr(fc, name, fn)
    bc.client = fc
    return fc


def recorder(result=None):
    calls = []

    def fn(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception):
            raise result
        return result

    return fn, calls


# ---- connect / close ----

async def test_connect_success(monkeypatch):
    class _FakeCC:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(bc_mod, "_FuturesOnlyClient", _FakeCC)
    bc = BinanceClient()
    bc.load_all_symbols = _async_none
    bc._sync_time_offset = _async_none
    assert await bc.connect() is True
    assert bc.client is not None


async def test_connect_already_connected():
    bc = BinanceClient()
    bc.client = object()
    assert await bc.connect() is True


async def test_connect_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("creds")

    monkeypatch.setattr(bc_mod, "_FuturesOnlyClient", _boom)
    bc = BinanceClient()
    assert await bc.connect() is False


async def test_close_calls_client():
    bc = BinanceClient()
    fn, calls = recorder(None)
    make_client(bc, close_connection=fn)
    await bc.close()
    assert calls


async def test_close_no_client():
    bc = BinanceClient()
    await bc.close()


# ---- semboller / filtreler ----

async def test_load_all_symbols_filters():
    bc = BinanceClient()
    symbols = [
        {"symbol": "BTCUSDT", "status": "TRADING", "filters": []},
        {"symbol": "ETHUSDT", "status": "TRADING", "filters": []},
        {"symbol": "USDCUSDT", "status": "TRADING", "filters": []},
        {"symbol": "BUSDUSDT", "status": "TRADING", "filters": []},
        {"symbol": "1000PEPEUSDT", "status": "TRADING", "filters": []},
        {"symbol": "PAUSEUSDT", "status": "BREAK", "filters": []},
        {"symbol": "BTCBUSD", "status": "TRADING", "filters": []},
    ]
    make_client(bc, futures_exchange_info=lambda: {"symbols": symbols})
    res = await bc.load_all_symbols()
    assert res == ["BTCUSDT", "ETHUSDT", "1000PEPEUSDT"]
    assert bc.all_symbols == res
    assert bc.symbol_filters["BTCUSDT"] == {}


async def test_load_all_symbols_fallback():
    bc = BinanceClient()

    def _boom(*a, **k):
        raise RuntimeError("api")

    make_client(bc, futures_exchange_info=_boom)
    res = await bc.load_all_symbols()
    assert res == ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT"]


async def test_load_all_symbols_strict_raises_without_fallback():
    bc = BinanceClient()

    def _boom(*a, **k):
        raise RuntimeError("api")

    make_client(bc, futures_exchange_info=_boom)
    with pytest.raises(RuntimeError, match="api"):
        await bc.load_all_symbols(allow_fallback=False)


def test_filter_uses_tick_and_step():
    bc = BinanceClient()
    bc.symbol_filters["BTCUSDT"] = {
        "PRICE_FILTER": {"tickSize": "0.10"},
        "LOT_SIZE": {"stepSize": "0.001"},
    }
    assert bc._filter("BTCUSDT", "PRICE_FILTER") == "0.10"
    assert bc._filter("BTCUSDT", "LOT_SIZE") == "0.001"
    assert bc._filter("NOPE", "PRICE_FILTER") == "0.00000001"


def test_decimal_places():
    assert BinanceClient._decimal_places("0.00000001") == 8
    assert BinanceClient._decimal_places("0.1") == 1
    assert BinanceClient._decimal_places("1") == 0


# ---- fiyatlar ----

async def test_get_all_tickers_filters_usdt():
    bc = BinanceClient()
    make_client(bc, futures_ticker=lambda: [
        {"symbol": "BTCUSDT", "lastPrice": "65000.5"},
        {"symbol": "ETHUSDT", "lastPrice": "3000.25"},
        {"symbol": "BTCUSD", "lastPrice": "99"},
    ])
    res = await bc.get_all_tickers()
    assert res == {"BTCUSDT": 65000.5, "ETHUSDT": 3000.25}


async def test_get_price():
    bc = BinanceClient()
    make_client(bc, futures_ticker=lambda symbol=None: {"lastPrice": "65123.45"})
    assert await bc.get_price("BTCUSDT") == 65123.45
    assert bc.last_price == 65123.45


async def test_get_price_fallback_on_error():
    bc = BinanceClient()
    bc.last_price = 42.0

    def _boom(*a, **k):
        raise RuntimeError("x")

    make_client(bc, futures_ticker=_boom)
    assert await bc.get_price("BTCUSDT") == 42.0


async def test_get_price_none_when_never_fetched():
    bc = BinanceClient()
    assert bc.last_price is None

    def _boom(*a, **k):
        raise RuntimeError("x")

    make_client(bc, futures_ticker=_boom)
    assert await bc.get_price("BTCUSDT") is None


# ---- klines ----

async def test_get_klines_dataframe():
    bc = BinanceClient()
    raw = [
        [1712304000000, "65000.1", "65100.0", "64800.5", "65050.0", "123.45"] + [""] * 6,
        [1712307600000, "65050.0", "65200.0", "64900.0", "65150.0", "99.99"] + [""] * 6,
    ]
    make_client(bc, futures_klines=lambda symbol, interval, limit, startTime=None: raw)
    df = await bc.get_klines("BTCUSDT", "1h", limit=100)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df["open"].dtype == float
    assert df["close"].iloc[-1] == 65150.0
    assert df.index.tz is not None


async def test_get_klines_start_time():
    bc = BinanceClient()
    seen = {}

    def fn(symbol, interval, limit, startTime=None):
        seen.update(symbol=symbol, interval=interval, limit=limit, startTime=startTime)
        return [[1712304000000, "1", "2", "3", "4", "5"] + [""] * 6]

    make_client(bc, futures_klines=fn)
    await bc.get_klines("BTCUSDT", "1h", limit=10, start_time=1712304000000)
    assert seen["symbol"] == "BTCUSDT"
    assert seen["startTime"] == 1712304000000
    assert seen["limit"] == 10


async def test_get_klines_raises():
    bc = BinanceClient()

    def _boom(*a, **k):
        raise RuntimeError("net")

    make_client(bc, futures_klines=_boom)
    with pytest.raises(Exception, match="Kline cekilemedi"):
        await bc.get_klines("BTCUSDT")


# ---- emirler ----

async def test_place_market_order_formats_qty():
    bc = BinanceClient()
    bc.symbol_filters["BTCUSDT"] = {"LOT_SIZE": {"stepSize": "0.001"}}
    fn, calls = recorder({"orderId": 1})
    make_client(bc, futures_create_order=fn)
    res = await bc.place_market_order("BTCUSDT", "buy", 1.23456)
    assert res == {"orderId": 1}
    kwargs = calls[0][1]
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["side"] == "BUY"
    assert kwargs["type"] == "MARKET"
    assert kwargs["quantity"] == "1.235"


async def test_place_market_order_raises():
    bc = BinanceClient()
    bc.symbol_filters["BTCUSDT"] = {"LOT_SIZE": {"stepSize": "0.001"}}
    fn, _calls = recorder(RuntimeError("borsa"))
    make_client(bc, futures_create_order=fn)
    with pytest.raises(Exception, match="Emir gonderilemedi"):
        await bc.place_market_order("BTCUSDT", "BUY", 0.1)


async def test_close_position_reduces_long():
    bc = BinanceClient()
    bc.symbol_filters["BTCUSDT"] = {"LOT_SIZE": {"stepSize": "0.001"}}
    fn, calls = recorder({"orderId": 9})
    make_client(bc,
                futures_position_information=lambda symbol=None: [{"positionAmt": "0.5"}],
                futures_create_order=fn)
    res = await bc.close_position("BTCUSDT")
    assert res == {"orderId": 9}
    kwargs = calls[0][1]
    assert kwargs["side"] == "SELL"
    assert kwargs["reduceOnly"] is True
    assert kwargs["quantity"] == "0.500"


async def test_close_position_short_buys():
    bc = BinanceClient()
    bc.symbol_filters["BTCUSDT"] = {"LOT_SIZE": {"stepSize": "0.001"}}
    fn, calls = recorder({"orderId": 9})
    make_client(bc,
                futures_position_information=lambda symbol=None: [{"positionAmt": "-0.25"}],
                futures_create_order=fn)
    await bc.close_position("BTCUSDT")
    assert calls[0][1]["side"] == "BUY"
    assert calls[0][1]["quantity"] == "0.250"


async def test_close_position_none_when_flat():
    bc = BinanceClient()
    make_client(bc, futures_position_information=lambda symbol=None: [{"positionAmt": "0"}])
    assert await bc.close_position("BTCUSDT") is None


async def test_close_position_raises():
    bc = BinanceClient()

    def _boom(*a, **k):
        raise RuntimeError("x")

    make_client(bc, futures_position_information=_boom)
    with pytest.raises(Exception, match="Pozisyon kapatma hatasi"):
        await bc.close_position("BTCUSDT")


async def test_get_open_positions_filters_flat():
    bc = BinanceClient()
    make_client(bc, futures_position_information=lambda: [
        {"symbol": "BTCUSDT", "positionAmt": "0.5"},
        {"symbol": "ETHUSDT", "positionAmt": "0"},
        {"symbol": "BNBUSDT", "positionAmt": "-0.2"},
    ])
    res = await bc.get_open_positions()
    assert [p["symbol"] for p in res] == ["BTCUSDT", "BNBUSDT"]


async def test_get_account_balance():
    bc = BinanceClient()
    make_client(bc, futures_account=lambda: {
        "totalWalletBalance": "12000.5", "availableBalance": "8000.25",
        "totalUnrealizedProfit": "500.75",
    })
    res = await bc.get_account_balance()
    assert res == {"balance": 12000.5, "available": 8000.25, "unrealized": 500.75}


# ---- TP/SL algo emirleri ----

async def test_set_tp_sl_long():
    bc = BinanceClient()
    bc.symbol_filters["BTCUSDT"] = {"PRICE_FILTER": {"tickSize": "0.01"}}
    fn, calls = recorder({"algoId": 100})
    make_client(bc,
                futures_position_information=lambda symbol=None: [{"positionAmt": "0.5"}],
                futures_create_order=fn)
    res = await bc.set_tp_sl("BTCUSDT", "LONG", 63000.0, 69000.0)
    assert res == {"sl": 100, "tp": 100}
    assert len(calls) == 2
    sl_kw = calls[0][1]
    assert sl_kw["type"] == "STOP_MARKET"
    assert sl_kw["triggerPrice"] == "63000.00"
    assert sl_kw["closePosition"] is True
    assert sl_kw["side"] == "SELL"
    tp_kw = calls[1][1]
    assert tp_kw["type"] == "TAKE_PROFIT_MARKET"
    assert tp_kw["triggerPrice"] == "69000.00"


async def test_set_tp_sl_short_side_buy():
    bc = BinanceClient()
    bc.symbol_filters["BTCUSDT"] = {"PRICE_FILTER": {"tickSize": "0.01"}}
    fn, calls = recorder({"algoId": 1})
    make_client(bc,
                futures_position_information=lambda symbol=None: [{"positionAmt": "-0.5"}],
                futures_create_order=fn)
    await bc.set_tp_sl("BTCUSDT", "SHORT", 64000.0, 61000.0)
    assert calls[0][1]["side"] == "BUY"


async def test_set_tp_sl_only_sl():
    bc = BinanceClient()
    bc.symbol_filters["BTCUSDT"] = {"PRICE_FILTER": {"tickSize": "0.01"}}
    fn, calls = recorder({"algoId": 1})
    make_client(bc,
                futures_position_information=lambda symbol=None: [{"positionAmt": "0.5"}],
                futures_create_order=fn)
    res = await bc.set_tp_sl("BTCUSDT", "LONG", 63000.0, None)
    assert res == {"sl": 1, "tp": None}
    assert len(calls) == 1


async def test_set_tp_sl_no_position():
    bc = BinanceClient()
    bc._wait_for_position = _async_false
    fn, calls = recorder({"algoId": 1})
    make_client(bc, futures_create_order=fn)
    res = await bc.set_tp_sl("BTCUSDT", "LONG", 63000.0, 69000.0)
    assert res == {"sl": None, "tp": None}
    assert calls == []


# ---- algo emir listeleme / iptal ----

async def test_cancel_algo_order_none():
    bc = BinanceClient()
    assert await bc.cancel_algo_order("BTCUSDT", None) is None


async def test_cancel_algo_order_calls():
    bc = BinanceClient()
    fn, calls = recorder({"success": True})
    make_client(bc, futures_cancel_algo_order=fn)
    res = await bc.cancel_algo_order("BTCUSDT", 55)
    assert res == {"success": True}
    assert calls[0][1] == {"symbol": "BTCUSDT", "algoId": 55}


async def test_cancel_algo_order_error_none():
    bc = BinanceClient()

    def _boom(*a, **k):
        raise RuntimeError("x")

    make_client(bc, futures_cancel_algo_order=_boom)
    assert await bc.cancel_algo_order("BTCUSDT", 55) is None


async def test_get_open_algo_orders_with_symbol():
    bc = BinanceClient()
    fn, calls = recorder({"orders": []})
    make_client(bc, futures_get_open_algo_orders=fn)
    await bc.get_open_algo_orders("BTCUSDT")
    assert calls[0][1] == {"symbol": "BTCUSDT"}


async def test_get_open_algo_orders_all():
    bc = BinanceClient()
    fn, calls = recorder({"orders": []})
    make_client(bc, futures_get_open_algo_orders=fn)
    await bc.get_open_algo_orders()
    assert calls[0][1] == {}


# ---- saat senkronu ----

async def test_sync_time_offset_applies():
    bc = BinanceClient()
    now = int(time.time() * 1000)
    fc = make_client(bc, futures_time=lambda: {"serverTime": now + 1000})
    await bc._sync_time_offset()
    assert abs(fc.timestamp_offset - 1000) < 100


async def test_sync_time_offset_small_ignored():
    bc = BinanceClient()
    now = int(time.time() * 1000)
    fc = make_client(bc, futures_time=lambda: {"serverTime": now + 100})
    await bc._sync_time_offset()
    assert getattr(fc, "timestamp_offset", None) is None

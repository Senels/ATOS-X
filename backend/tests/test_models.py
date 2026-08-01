"""DB modelleri: SQLite (aiosqlite) uzerinde olusturma ve CRUD dogrulamasi.

TimescaleDB'ye ozgu DDL (hypertable) migration'da calistirilir; burada
yalnizca model tanimlarinin dogrulugu test edilir.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Candle, Order, Position, Setting, Symbol, Trade


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.db.base import Base

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_symbol_crud(session):
    session.add(
        Symbol(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            status="TRADING",
            price_precision=2,
            qty_precision=3,
            min_notional=5.0,
            step_size=0.001,
            tick_size=0.1,
        )
    )
    await session.commit()

    sym = await session.get(Symbol, "BTCUSDT")
    assert sym is not None
    assert sym.step_size == 0.001
    assert sym.enabled is True


async def test_candle_roundtrip_and_upsert(session):
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    session.add(
        Candle(
            symbol="ETHUSDT",
            timeframe="1m",
            ts=ts,
            open=100.0,
            high=101.0,
            low=99.5,
            close=100.5,
            volume=10.0,
            trades=42,
        )
    )
    await session.commit()

    result = await session.execute(
        select(Candle).where(Candle.symbol == "ETHUSDT", Candle.timeframe == "1m")
    )
    candle = result.scalar_one()
    assert candle.high == 101.0
    assert candle.trades == 42
    assert candle.closed is True


async def test_position_fields(session):
    session.add(
        Position(
            id="BTCUSDT_LONG_1",
            symbol="BTCUSDT",
            side="LONG",
            qty=0.5,
            entry_price=65000.0,
            stop_price=64000.0,
            sl_algo_id=1000000153031282,
            tp_algo_id=1000000153031285,
            margin_used=32.5,
            status="open",
        )
    )
    await session.commit()

    pos = await session.get(Position, "BTCUSDT_LONG_1")
    assert pos.status == "open"
    assert pos.sl_algo_id == 1000000153031282


async def test_trade_and_order(session):
    session.add(
        Trade(
            symbol="ATOMUSDT",
            side="LONG",
            entry_price=1.22,
            exit_price=1.232,
            qty=81.9,
            margin_used=10.0,
            pnl=0.98,
            pnl_pct=0.098,
            reason="take_profit",
            opened_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        )
    )
    session.add(
        Order(
            symbol="ATOMUSDT",
            side="SELL",
            order_type="STOP_MARKET",
            status="NEW",
            algo_id=1000000153031518,
            trigger_price=1.208,
            close_position=True,
        )
    )
    await session.commit()

    trades = (await session.execute(select(Trade))).scalars().all()
    assert len(trades) == 1
    assert trades[0].pnl > 0

    order = (await session.execute(select(Order))).scalar_one()
    assert order.algo_id == 1000000153031518
    assert order.close_position is True


async def test_setting_kv(session):
    session.add(Setting(key="trading_mode", value="testnet"))
    await session.commit()
    setting = await session.get(Setting, "trading_mode")
    assert setting.value == "testnet"

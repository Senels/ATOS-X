from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Symbol(Base):
    """Binance USDM Futures sembol meta verisi."""

    __tablename__ = "symbols"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    base_asset: Mapped[str] = mapped_column(String(16))
    quote_asset: Mapped[str] = mapped_column(String(16), default="USDT")
    status: Mapped[str] = mapped_column(String(16), default="TRADING")
    contract_type: Mapped[str] = mapped_column(String(16), default="PERPETUAL")
    price_precision: Mapped[int] = mapped_column(Integer, default=0)
    qty_precision: Mapped[int] = mapped_column(Integer, default=0)
    min_notional: Mapped[float] = mapped_column(Float, default=0.0)
    step_size: Mapped[float] = mapped_column(Float, default=0.0)
    tick_size: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Candle(Base):
    """OHLCV mum verisi — TimescaleDB hypertable.

    Zaman sütunu (ts) birincil anahtarin parcasidir (hypertable kurali).
    """

    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "ts", name="uq_candle_key"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    quote_volume: Mapped[float] = mapped_column(Float, default=0.0)
    trades: Mapped[int] = mapped_column(BigInteger, default=0)
    closed: Mapped[bool] = mapped_column(default=True)

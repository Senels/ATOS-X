"""initial schema: symbols, candles (hypertable), positions, trades, orders, app_settings

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-01

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "symbols",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("base_asset", sa.String(16), nullable=False),
        sa.Column("quote_asset", sa.String(16), nullable=False, server_default="USDT"),
        sa.Column("status", sa.String(16), nullable=False, server_default="TRADING"),
        sa.Column("contract_type", sa.String(16), nullable=False, server_default="PERPETUAL"),
        sa.Column("price_precision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qty_precision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_notional", sa.Float(), nullable=False, server_default="0"),
        sa.Column("step_size", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tick_size", sa.Float(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "candles",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quote_volume", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trades", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "ts"),
        sa.UniqueConstraint("symbol", "timeframe", "ts", name="uq_candle_key"),
    )
    op.execute(
        "SELECT create_hypertable('candles', 'ts', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    )

    op.create_table(
        "positions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("tp_price", sa.Float(), nullable=True),
        sa.Column("sl_algo_id", sa.BigInteger(), nullable=True),
        sa.Column("tp_algo_id", sa.BigInteger(), nullable=True),
        sa.Column("margin_used", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.String(64), nullable=True),
    )
    op.create_index("ix_positions_symbol", "positions", ["symbol"])
    op.create_index("ix_positions_status", "positions", ["status"])

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("margin_used", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pnl_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(64), nullable=False, server_default=""),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_trades_symbol", "trades", ["symbol"])
    op.create_index("ix_trades_closed_at", "trades", ["closed_at"])

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="NEW"),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("algo_id", sa.BigInteger(), nullable=True),
        sa.Column("client_order_id", sa.String(64), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("trigger_price", sa.Float(), nullable=True),
        sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("close_position", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_status", "orders", ["status"])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("orders")
    op.drop_table("trades")
    op.drop_table("positions")
    op.execute("SELECT drop_chunks('candles', older_than => INTERVAL '0')")
    op.drop_table("candles")
    op.drop_table("symbols")

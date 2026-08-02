import asyncio
from datetime import datetime
from loguru import logger

from app.backtest.engine import BacktestEngine
from app.core.database import Database
from app.strategy import settings as strat_settings
from app.strategy.tradebot_v23 import TradeBotV23


class AutoTrader:
    """Canli islem motoru: v23 sinyalleri -> risk bazli boyutlandirma -> DB.

    Boyutlandirma BacktestEngine.position_size ile ayni mantigi kullanir;
    acilip kapanan tum islemler DB'ye yazilir (acilis ve kapanis kaydi).
    Websocket'ten gelen fiyatlar `update_price` ile buraya akar, REST
    taramasi fallback olarak kalir.
    """

    def __init__(self, binance_client, telegram=None):
        self.binance = binance_client
        self.db = Database()
        self.telegram = telegram
        s = strat_settings.get_settings()
        self.engine = BacktestEngine(
            initial_equity=s["initial_equity"],
            risk_per_trade=s["risk_per_trade"],
            fee_rate=s["fee_rate"],
            slippage=0.0001,
            max_leverage=s["max_leverage"],
        )
        self.running = False
        self.trading_symbols = []
        self.active_positions = {}
        self.trade_history = []
        self.live_prices = {}
        self.equity = float(s["initial_equity"])
        self.max_positions = int(s["max_open_positions"])
        self.scan_interval = 30
        self.scan_limit = 50

    def update_price(self, symbol: str, price: float):
        self.live_prices[symbol] = float(price)

    async def start(self):
        self.running = True
        logger.info("Otomatik islem motoru baslatildi")
        self.trading_symbols = await self.binance.load_all_symbols()
        logger.info(f"{len(self.trading_symbols)} coin taranacak")

        while self.running:
            try:
                bot = TradeBotV23(strat_settings.get_settings())
                all_prices = await self.binance.get_all_tickers()
                signals = []

                for symbol in self.trading_symbols[: self.scan_limit]:
                    try:
                        klines = await self.binance.get_klines(symbol, "4h", 200)
                    except Exception:
                        continue
                    if klines is None or len(klines) < 30:
                        continue

                    signal = bot.generate_signal(klines)
                    price = float(klines["close"].iloc[-1])

                    if signal.get("signal") in ("BUY", "SELL") and signal.get("sl") and signal.get("tp"):
                        signals.append({
                            "symbol": symbol,
                            "signal": signal["signal"],
                            "price": price,
                            "sl": signal["sl"],
                            "tp": signal["tp"],
                            "reason": signal.get("reason", ""),
                        })
                        logger.info(f"{symbol}: {signal['signal']} @ {price}")

                await self.process_signals(signals)
                await self.check_positions(all_prices)
                await self.update_equity()
                await asyncio.sleep(self.scan_interval)

            except Exception as e:
                logger.error(f"Otomatik islem hatasi: {e}")
                await asyncio.sleep(10)

    async def process_signals(self, signals):
        for signal in signals:
            symbol = signal["symbol"]
            if symbol in self.active_positions:
                # Acik pozisyonda ters yonde sinyal -> kapat
                if signal["signal"] != self.active_positions[symbol]["side"]:
                    await self.close_position(symbol, signal["price"], "signal_exit")
            else:
                if len(self.active_positions) >= self.max_positions:
                    continue
                await self.open_position(
                    symbol, signal["signal"], signal["price"],
                    signal["sl"], signal["tp"], signal.get("reason"),
                )

    async def open_position(self, symbol: str, side: str, price: float, sl: float, tp: float, reason: str = ""):
        try:
            side = "BUY" if side == "BUY" else "SELL"
            sizing = self.engine.position_size(price, sl, self.equity)
            qty = float(sizing["qty"])
            if qty <= 0:
                return

            order = await self.binance.place_market_order(symbol, side, qty)
            if order:
                self.equity -= float(sizing["entry_fee"])
                self.active_positions[symbol] = {
                    "side": side,
                    "entry_price": price,
                    "quantity": qty,
                    "sl": sl,
                    "tp": tp,
                    "entry_fee": float(sizing["entry_fee"]),
                    "open_time": datetime.utcnow().isoformat(),
                }
                self.db.save_trade(symbol, side, price, qty)
                self.db.save_signal(symbol, side, price, 0.0, reason or "auto")
                if self.telegram:
                    await self.telegram.send_signal(symbol, side, price, reason)
                logger.success(f"Pozisyon acildi: {symbol} {side} {qty:.4f} @ {price}")
        except Exception as e:
            logger.error(f"Pozisyon acma hatasi {symbol}: {e}")

    async def close_position(self, symbol: str, price: float, reason: str):
        try:
            order = await self.binance.close_position(symbol)
            if order:
                pos = self.active_positions.pop(symbol, None)
                if pos:
                    pnl = (price - pos["entry_price"]) * pos["quantity"] \
                        if pos["side"] == "BUY" \
                        else (pos["entry_price"] - price) * pos["quantity"]
                    exit_fee = price * pos["quantity"] * self.engine.fee_rate
                    net = pnl - exit_fee - pos.get("entry_fee", 0.0)
                    self.equity += pnl - exit_fee
                    self.db.close_trade_by_symbol(symbol, price, net)
                    self.trade_history.append({
                        "symbol": symbol,
                        "side": pos["side"],
                        "entry": pos["entry_price"],
                        "exit": price,
                        "qty": pos["quantity"],
                        "pnl": net,
                        "reason": reason,
                        "time": datetime.utcnow().isoformat(),
                    })
                    if self.telegram:
                        await self.telegram.send_trade(symbol, pos["side"], price, pos["quantity"], reason)
                    logger.success(f"Pozisyon kapatildi: {symbol} PnL: {net:.2f}")
        except Exception as e:
            logger.error(f"Kapatma hatasi {symbol}: {e}")

    async def check_positions(self, prices):
        for symbol, pos in list(self.active_positions.items()):
            current_price = self.live_prices.get(symbol) or prices.get(symbol)
            if not current_price:
                continue
            side = pos["side"]
            if side == "BUY" and current_price <= pos["sl"]:
                await self.close_position(symbol, pos["sl"], "stop_loss")
            elif side == "SELL" and current_price >= pos["sl"]:
                await self.close_position(symbol, pos["sl"], "stop_loss")
            elif side == "BUY" and current_price >= pos["tp"]:
                await self.close_position(symbol, pos["tp"], "take_profit")
            elif side == "SELL" and current_price <= pos["tp"]:
                await self.close_position(symbol, pos["tp"], "take_profit")

    async def update_equity(self):
        self.db.save_performance(self.equity, len(self.active_positions), len(self.trade_history), 0)

    async def stop(self):
        self.running = False
        for symbol in list(self.active_positions.keys()):
            price = self.live_prices.get(symbol)
            if not price:
                price = await self.binance.get_price(symbol)
            await self.close_position(symbol, price, "system_stop")
        logger.info("Otomatik islem motoru durduruldu")

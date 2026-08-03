import asyncio
import time
from datetime import datetime
from typing import List
from loguru import logger

from app.backtest.engine import BacktestEngine
from app.core.config import get_settings
from app.core.database import Database
from app.data import loader
from app.strategy import settings as strat_settings
from app.strategy.tradebot_v23 import TradeBotV23


class AutoTrader:
    """Canli islem motoru: v23 sinyalleri -> risk bazli boyutlandirma -> DB.

    Boyutlandirma BacktestEngine.position_size ile ayni mantigi kullanir;
    acilip kapanan tum islemler DB'ye yazilir (acilis ve kapanis kaydi).
    Websocket'ten gelen fiyatlar `update_price` ile buraya akar, REST
    taramasi fallback olarak kalir.

    `paper=True` iken emirler borsaya gitmez; sinyal fiyatindan simule
    edilerek kaydedilir (canli riski olmadan uctan uca deneme icin).
    """

    def __init__(self, binance_client, telegram=None, paper=None):
        self.binance = binance_client
        self.db = Database()
        self.telegram = telegram
        self.paper = get_settings().PAPER_TRADING if paper is None else bool(paper)
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
        self.priority: List[str] = []
        self.top_symbols: List[str] = []
        self._last_rank = time.time()
        self.equity = float(s["initial_equity"])
        self.max_positions = int(s["max_open_positions"])
        self.max_position_pct = float(s.get("max_position_pct", 75.0))
        self.max_side_pct = float(s.get("max_side_pct", 150.0))
        self.max_drawdown_pct = float(s.get("max_drawdown_pct", 20.0))
        self.max_position_age_hours = float(s.get("max_position_age_hours", 8.0))
        self.trailing_activate_pct = float(s.get("trailing_activate_pct", 3.0))
        self.trailing_sl_pct = float(s.get("trailing_sl_pct", 1.5))
        self.peak_equity = float(s["initial_equity"])
        self.drawdown_pct = 0.0
        self.risk_halted = False
        self._conc_alerts = {"symbols": set(), "sides": set()}
        self._conc_blocks = set()
        self._last_block_state = set()
        self._last_block_summary = 0.0
        self.block_summary_interval = 3600
        self.risk_events = []
        self.risk_events_max = 200
        self.scan_interval = 30
        self.scan_limit = 50
        self.perf_interval = 60
        self.reconcile_interval = 300
        self._last_reconcile = 0.0
        self._last_perf = 0.0

    def _log_risk_event(self, event_type: str, message: str, **extra):
        """Risk/blok olaylarini son-N halka tamponuna kaydeder."""
        entry = {"time": datetime.utcnow().isoformat(), "type": event_type,
                 "message": message, **extra}
        self.risk_events.append(entry)
        if len(self.risk_events) > self.risk_events_max:
            self.risk_events = self.risk_events[-self.risk_events_max:]

    def rank_symbols(self, limit: int = 500) -> List[str]:
        """Yerel OHLCV arsivinde backtest kalitesine gore sembol siralamasi.

        Bu siralama, canli taramada ilk `scan_limit` sembolun nereden
        secilecegini belirler. Verisi olmayan ya da yeterli islem
        uretmeyen semboller elenir.
        """
        bot = TradeBotV23(strat_settings.get_settings())
        rows = []
        for symbol in self.trading_symbols:
            try:
                df = loader.load_csv(symbol, "4h", limit=limit)
            except Exception:
                continue
            if len(df) < 200:
                continue
            try:
                orders = bot.analyze(df)["orders"]
                m = self.engine.run(df, orders, "4h")
            except Exception:
                continue
            if m.get("total_trades", 0) < 5:
                continue
            rows.append((float(m.get("sharpe", 0.0) or 0.0),
                         float(m.get("net_profit", 0.0) or 0.0),
                         symbol))
        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
        return [r[2] for r in rows]

    async def _refresh_ranking(self):
        loop = asyncio.get_running_loop()
        ranked = await loop.run_in_executor(None, self.rank_symbols)
        if ranked:
            self.priority = ranked
            self.top_symbols = ranked[: self.scan_limit]
            self._last_rank = time.time()
            logger.info(
                f"Backtest oncelik listesi: {len(ranked)} sembol, "
                f"tarama secimi: {', '.join(self.top_symbols[:10])}"
            )

    def update_price(self, symbol: str, price: float):
        self.live_prices[symbol] = float(price)

    async def _fetch_klines_batch(self, candidates: list) -> dict:
        """Aday sembollerin kline'larini paralel ceker (siralı REST gecikmesini azaltir)."""
        async def fetch(symbol):
            try:
                return symbol, await self.binance.get_klines(symbol, "4h", 200)
            except Exception:
                return symbol, None

        results = await asyncio.gather(*(fetch(s) for s in candidates))
        return {symbol: klines for symbol, klines in results}

    async def _ensure_connected(self, max_attempts: int = 30, delay: int = 10) -> bool:
        """Binance baglantisi kurulana kadar sinirli sure dener (flaky network)."""
        attempts = 0
        failed = False
        while self.running:
            if await self.binance.connect():
                if failed and self.telegram:
                    await self.telegram.send("ATOS X: Binance baglantisi yeniden kuruldu")
                return True
            failed = True
            attempts += 1
            if max_attempts and attempts >= max_attempts:
                logger.error(f"Binance baglantisi {attempts} denemede kurulamadi")
                if self.telegram:
                    await self.telegram.send(
                        f"ATOS X: Binance baglantisi {attempts} denemede kurulamadi!"
                    )
                return False
            logger.warning(
                f"Binance baglantisi yok; {delay}s sonra tekrar deneniyor ({attempts})"
            )
            await asyncio.sleep(delay)
        return False

    async def start(self):
        self.running = True
        logger.info("Otomatik islem motoru baslatildi")
        if not await self._ensure_connected():
            logger.error("Binance baglantisi kurulamadi; motor baslatilamiyor")
            self.running = False
            return
        self.trading_symbols = await self.binance.load_all_symbols()
        logger.info(f"{len(self.trading_symbols)} coin taranacak")
        await self.reconcile_positions()
        await self._check_concentration()
        await self._notify_startup_state()
        asyncio.create_task(self._refresh_ranking())

        while self.running:
            try:
                s = strat_settings.get_settings()
                bot = TradeBotV23(s)
                # Risk ayarlari UI'dan degistirilirse sonraki dongude gecerli olur
                self._apply_risk_settings(s)
                all_prices = await self.binance.get_all_tickers()
                if not all_prices:
                    # Baglanti koptuysa yeniden kur, fallback sembollerde kaldiysa tazele
                    logger.warning("Borsa fiyatlari alinamadi; baglanti yeniden deneniyor")
                    await self.binance.connect()
                    if self.binance.client and len(self.trading_symbols) < 10:
                        self.trading_symbols = await self.binance.load_all_symbols()
                        logger.info(
                            f"{len(self.trading_symbols)} coin taranacak (yeniden yuklendi)"
                        )
                    await asyncio.sleep(self.scan_interval)
                    continue
                signals = []

                if self.priority and time.time() - self._last_rank > 1800:
                    asyncio.create_task(self._refresh_ranking())
                ranked = [s for s in self.priority if s in all_prices] if self.priority else None
                candidates = ranked[: self.scan_limit] if ranked \
                    else self.trading_symbols[: self.scan_limit]

                klines_map = await self._fetch_klines_batch(candidates)

                for symbol in candidates:
                    klines = klines_map.get(symbol)
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
                if time.time() - self._last_reconcile > self.reconcile_interval:
                    await self.reconcile_positions()
                    self._last_reconcile = time.time()
                await self.update_equity()
                await self._check_concentration()
                await self._check_drawdown()
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
                if self.risk_halted:
                    logger.info(
                        f"{symbol}: drawdown korumasi aktif, giris engellendi"
                    )
                    continue
                if len(self.active_positions) >= self.max_positions:
                    continue
                side = "LONG" if signal["signal"] == "BUY" else "SHORT"
                notional = self._projected_notional(signal["price"], signal["sl"])
                if await self._blocked_by_side(side, notional):
                    logger.warning(
                        f"{symbol}: {side} yonunde asiri pozisyon, giris engellendi"
                    )
                    continue
                if await self._blocked_by_symbol(symbol, notional):
                    logger.warning(
                        f"{symbol}: projeksiyon pozisyonu asiri, giris engellendi"
                    )
                    continue
                await self.open_position(
                    symbol, signal["signal"], signal["price"],
                    signal["sl"], signal["tp"], signal.get("reason"),
                )

    def _apply_risk_settings(self, s: dict):
        """Risk ayarlarini canli uygular (UI'dan degisimler bir sonraki dongude)."""
        self.max_positions = int(s["max_open_positions"])
        self.engine.risk_per_trade = float(s["risk_per_trade"])
        self.engine.max_leverage = float(s["max_leverage"])
        self.max_drawdown_pct = float(s.get("max_drawdown_pct", self.max_drawdown_pct))
        self.max_position_age_hours = float(s.get("max_position_age_hours", self.max_position_age_hours))
        self.trailing_activate_pct = float(s.get("trailing_activate_pct", self.trailing_activate_pct))
        self.trailing_sl_pct = float(s.get("trailing_sl_pct", self.trailing_sl_pct))

    def _projected_notional(self, price: float, sl: float) -> float:
        """Yeni bir pozisyonun boyutlandirma sonrasi nominal degeri."""
        try:
            sizing = self.engine.position_size(price, sl, self.equity)
            return price * float(sizing["qty"])
        except Exception:
            return 0.0

    def _side_total(self, side: str) -> float:
        return sum(
            float(p["entry_price"]) * float(p["quantity"])
            for p in self.active_positions.values()
            if p["side"] == side
        )

    async def _blocked_by_side(self, side: str, notional: float) -> bool:
        """Tek yon toplam maruziyet esigi asarsa o yonde giris engellenir."""
        pct = (self._side_total(side) + notional) / (self.equity or 1.0) * 100.0
        if pct <= self.max_side_pct:
            self._conc_blocks.discard(f"side:{side}")
            return False
        key = f"side:{side}"
        if key not in self._conc_blocks:
            self._conc_blocks.add(key)
        return True

    async def _blocked_by_symbol(self, symbol: str, notional: float) -> bool:
        """Projeksiyon pozisyonu sembol esigini asarsa giris engellenir."""
        pct = notional / (self.equity or 1.0) * 100.0
        if pct <= self.max_position_pct:
            self._conc_blocks.discard(f"sym:{symbol}")
            return False
        key = f"sym:{symbol}"
        if key not in self._conc_blocks:
            self._conc_blocks.add(key)
        return True

    async def _submit_open(self, symbol: str, side: str, qty: float):
        if self.paper:
            return {"symbol": symbol, "side": side, "quantity": qty, "paper": True}
        return await self.binance.place_market_order(symbol, side, qty)

    async def _submit_close(self, symbol: str):
        if self.paper:
            return {"symbol": symbol, "paper": True}
        return await self.binance.close_position(symbol)

    async def open_position(self, symbol: str, side: str, price: float, sl: float, tp: float, reason: str = ""):
        try:
            side = "BUY" if side == "BUY" else "SELL"
            sizing = self.engine.position_size(price, sl, self.equity)
            qty = float(sizing["qty"])
            if qty <= 0:
                return

            order = await self._submit_open(symbol, side, qty)
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
                if not self.paper:
                    position_side = "LONG" if side == "BUY" else "SHORT"
                    algo = await self.binance.set_tp_sl(symbol, position_side, sl, tp)
                    self.active_positions[symbol]["sl_order_id"] = algo.get("sl")
                    self.active_positions[symbol]["tp_order_id"] = algo.get("tp")
                logger.success(f"Pozisyon acildi: {symbol} {side} {qty:.4f} @ {price}")
        except Exception as e:
            logger.error(f"Pozisyon acma hatasi {symbol}: {e}")

    async def close_position(self, symbol: str, price: float, reason: str):
        try:
            pos = self.active_positions.get(symbol)
            if not pos:
                return
            order = await self._submit_close(symbol)
            if self.paper or order:
                self.active_positions.pop(symbol, None)
            elif order is None:
                # Borsadaki pozisyon zaten yok: exchange-side SL/TP tetiklenmis.
                self.active_positions.pop(symbol, None)
                order = {"symbol": symbol, "algo_closed": True}
            else:
                return
            if not self.paper:
                if pos.get("sl_order_id"):
                    await self.binance.cancel_algo_order(symbol, pos["sl_order_id"])
                if pos.get("tp_order_id"):
                    await self.binance.cancel_algo_order(symbol, pos["tp_order_id"])
            await self._record_closed_position(symbol, pos, price, reason)
        except Exception as e:
            logger.error(f"Kapatma hatasi {symbol}: {e}")

    async def _record_closed_position(self, symbol: str, pos: dict, exit_price: float,
                                      reason: str):
        """Kapanan pozisyonun PnL hesabi, DB kaydi ve bildirimini yapar."""
        pnl = (exit_price - pos["entry_price"]) * pos["quantity"] \
            if pos["side"] == "BUY" \
            else (pos["entry_price"] - exit_price) * pos["quantity"]
        exit_fee = exit_price * pos["quantity"] * self.engine.fee_rate
        net = pnl - exit_fee - pos.get("entry_fee", 0.0)
        self.equity += pnl - exit_fee
        self.db.close_trade_by_symbol(symbol, exit_price, net)
        self.trade_history.append({
            "symbol": symbol,
            "side": pos["side"],
            "entry": pos["entry_price"],
            "exit": exit_price,
            "qty": pos["quantity"],
            "pnl": net,
            "reason": reason,
            "time": datetime.utcnow().isoformat(),
        })
        if self.telegram:
            await self.telegram.send_trade(
                symbol, pos["side"], exit_price, pos["quantity"], reason
            )
        logger.success(f"Pozisyon kapatildi: {symbol} PnL: {net:.2f}")

    async def reconcile_positions(self):
        """Restart sonrasi acik pozisyonlari borsadan geri yukler ve drift temizler.

        Exchange-side SL/TP emirleri süreç ölse de borsada korumaya devam eder;
        bu metod `active_positions`'i borsa gercegiyle hizalar:
          - Borsada acik ama takip edilmeyen -> geri yukle
          - Takip edilen ama borsada artik yok -> algo SL/TP kapatmis, kapanis kaydet
        Hata durumunda tum hizalama iptal edilir (yanlis kapanis kaydi yazilmaz).
        """
        if self.paper:
            return
        try:
            positions = await self.binance.get_open_positions()
            algos = await self.binance.get_open_algo_orders()
            algo_map = {}
            for a in algos or []:
                sym = a.get("symbol")
                entry = algo_map.setdefault(sym, {"sl": None, "sl_id": None,
                                                 "tp": None, "tp_id": None})
                order_type = a.get("orderType") or a.get("type")
                if order_type == "STOP_MARKET":
                    entry["sl"] = float(a.get("triggerPrice") or 0) or None
                    entry["sl_id"] = a.get("algoId")
                elif order_type == "TAKE_PROFIT_MARKET":
                    entry["tp"] = float(a.get("triggerPrice") or 0) or None
                    entry["tp_id"] = a.get("algoId")
            exchange_symbols = {p["symbol"] for p in positions}
            for symbol, pos in list(self.active_positions.items()):
                if symbol in exchange_symbols:
                    if pos.get("sl_order_id") or pos.get("tp_order_id"):
                        info = algo_map.get(symbol, {})
                        missing = []
                        if info.get("sl_id") is None:
                            missing.append("SL")
                        if info.get("tp_id") is None:
                            missing.append("TP")
                        if missing:
                            await self._repair_protection(symbol, pos, missing)
                    continue
                exit_price, reason = self._exchange_close_estimate(symbol, pos)
                self.active_positions.pop(symbol, None)
                await self._record_closed_position(symbol, pos, exit_price, reason)
                logger.warning(
                    f"{symbol}: borsada pozisyon yok; exchange kapanisi kaydedildi ({reason})"
                )
            restored = 0
            for p in positions:
                symbol = p["symbol"]
                if symbol in self.active_positions:
                    continue
                info = algo_map.get(symbol, {})
                if info.get("sl_id") is None and info.get("tp_id") is None:
                    logger.warning(
                        f"{symbol}: borsada pozisyon var ama SL/TP emri yok; "
                        f"takip disi birakildi (acik kaldigindan emin olun)"
                    )
                    if self.telegram:
                        await self.telegram.send(
                            f"ATOS X UYARI: {symbol} borsada acik ama SL/TP emri yok! "
                            f"Pozisyon korumasiz, manuel müdahale gerekebilir."
                        )
                    continue
                amt = float(p["positionAmt"])
                db_opened = self.db.get_open_trade_entry_time(symbol)
                restored_open = datetime.utcnow().isoformat()
                if db_opened:
                    try:
                        restored_open = datetime.fromisoformat(db_opened).isoformat()
                    except Exception:
                        pass
                self.active_positions[symbol] = {
                    "side": "BUY" if amt > 0 else "SELL",
                    "entry_price": float(p.get("entryPrice", 0)),
                    "quantity": abs(amt),
                    "sl": info.get("sl") or 0.0,
                    "tp": info.get("tp") or 0.0,
                    "sl_order_id": info.get("sl_id"),
                    "tp_order_id": info.get("tp_id"),
                    "entry_fee": 0.0,
                    "open_time": restored_open,
                    "restored": True,
                }
                restored += 1
            if restored:
                logger.info(f"Borsadan {restored} pozisyon geri yuklendi")
        except Exception as e:
            logger.error(f"Pozisyon geri yukleme hatasi: {e}")

    async def _check_drawdown(self):
        """Peak equity'den düşüş esigi asilinca yeni girisleri durdurur.

        `max_drawdown_pct` esigi asildiginda bir kez uyarir ve `risk_halted`
        bayragini kaldirir; drawdown esigin yarisina duserse tekrar serbest
        birakir (flap'ı önlemek icin histerezis).
        """
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        peak = self.peak_equity or 1.0
        dd = (peak - self.equity) / peak * 100.0
        self.drawdown_pct = round(dd, 2)
        threshold = self.max_drawdown_pct
        if threshold <= 0:
            return
        if dd >= threshold and not self.risk_halted:
            self.risk_halted = True
            self._log_risk_event("drawdown_halt",
                                 f"Drawdown %{dd:.1f} (%{threshold:.0f} esigi) asildi")
            logger.warning(
                f"Drawdown %{dd:.1f} (%{threshold:.0f} esigi) - yeni girisler durduruldu"
            )
            if self.telegram:
                await self.telegram.send(
                    f"ATOS X UYARI: Drawdown %{dd:.1f} (%{threshold:.0f} esigi) "
                    f"asildi - yeni girisler durduruldu."
                )
        elif self.risk_halted and dd <= threshold * 0.5:
            self.risk_halted = False
            self._log_risk_event("drawdown_clear",
                                 f"Drawdown %{dd:.1f}'e geri geldi, girisler serbest")
            logger.info(f"Drawdown %{dd:.1f}'e geri geldi - yeni girisler serbest")
            if self.telegram:
                await self.telegram.send(
                    f"ATOS X: Drawdown %{dd:.1f}'e geri geldi - yeni girisler serbest."
                )

    async def _notify_startup_state(self):
        """Baslangicta aktif risk esiklerini ve mevcut engelleri bildirir."""
        if not self.telegram:
            return
        msg = (
            f"ATOS X: Risk ayarlari - max pos %{self.max_position_pct:.0f}, "
            f"max side %{self.max_side_pct:.0f}"
        )
        blocks = sorted(self._conc_blocks)
        if blocks:
            msg += f"; {len(blocks)} engel aktif: {', '.join(blocks)}"
        else:
            msg += "; engel yok"
        await self.telegram.send(msg)

    async def _sync_block_state(self):
        """Engel kumesi degistiginde Telegram'dan tek bir ozet bildirimi gonderir."""
        cur = set(self._conc_blocks)
        prev = self._last_block_state
        if cur == prev:
            return
        self._last_block_state = cur
        added = sorted(cur - prev)
        removed = sorted(prev - cur)
        for b in added:
            self._log_risk_event("block_add", f"Engel: {b}")
        for b in removed:
            self._log_risk_event("block_remove", f"Engel kalkti: {b}")
        if not self.telegram:
            return
        parts = []
        if added:
            parts.append(f"engellendi: {', '.join(added)}")
        if removed:
            parts.append(f"kaldirildi: {', '.join(removed)}")
        msg = "ATOS X: Konsantrasyon durumu degisti - " + "; ".join(parts)
        if cur:
            msg += f" | aktif: {', '.join(sorted(cur))}"
        await self.telegram.send(msg)

    async def _check_concentration(self):
        """Tek sembol / tek yonde asiri pozisyon yogunlugunu izler ve uyarir.

        Esikler `max_position_pct` ve `max_side_pct` (equity yuzdesi).
        Esigi asan ilk anda bir kez uyarir; pozisyon esigin altina inip
        yeniden asarsa tekrar uyarir (spam yok).
        """
        long_notional = 0.0
        short_notional = 0.0
        symbol_notional = {}
        for symbol, pos in self.active_positions.items():
            notional = float(pos["entry_price"]) * float(pos["quantity"])
            symbol_notional[symbol] = notional
            if pos["side"] == "BUY":
                long_notional += notional
            else:
                short_notional += notional
        equity = self.equity or 1.0
        over_symbols = set()
        for symbol, notional in symbol_notional.items():
            pct = notional / equity * 100.0
            if pct > self.max_position_pct:
                over_symbols.add(symbol)
                if symbol not in self._conc_alerts["symbols"]:
                    self._conc_alerts["symbols"].add(symbol)
                    if self.telegram:
                        await self.telegram.send(
                            f"ATOS X UYARI: {symbol} pozisyonu equity'nin "
                            f"%{pct:.0f}'i ({self.max_position_pct:.0f} esigi), "
                            f"asiri konsantrasyon!"
                        )
        self._conc_alerts["symbols"] &= over_symbols
        over_sides = {}
        if long_notional / equity * 100.0 > self.max_side_pct:
            over_sides["LONG"] = long_notional
        if short_notional / equity * 100.0 > self.max_side_pct:
            over_sides["SHORT"] = short_notional
        for side, notional in over_sides.items():
            if side not in self._conc_alerts["sides"]:
                self._conc_alerts["sides"].add(side)
                pct = notional / equity * 100.0
                if self.telegram:
                    await self.telegram.send(
                        f"ATOS X UYARI: {side} yonunde toplam %{pct:.0f} equity "
                        f"pozisyon ({self.max_side_pct:.0f} esigi), "
                        f"asiri konsantrasyon!"
                    )
        self._conc_alerts["sides"] &= set(over_sides)
        for s in ("LONG", "SHORT"):
            if s not in over_sides:
                self._conc_blocks.discard(f"side:{s}")
        if self._conc_blocks:
            if time.time() - self._last_block_summary > self.block_summary_interval:
                self._last_block_summary = time.time()
                if self.telegram:
                    await self.telegram.send(
                        f"ATOS X: {len(self._conc_blocks)} konsantrasyon engeli aktif: "
                        f"{', '.join(sorted(self._conc_blocks))}"
                    )
        else:
            self._last_block_summary = 0.0
        await self._sync_block_state()

    async def _repair_protection(self, symbol: str, pos: dict, missing: list):
        """Kayip SL/TP algo emrini yeniden yerleştirir; basarisizsa uyarir."""
        sl_price = pos.get("sl") or 0.0
        tp_price = pos.get("tp") or 0.0
        if "SL" not in missing:
            sl_price = 0.0
        if "TP" not in missing:
            tp_price = 0.0
        position_side = "LONG" if pos["side"] == "BUY" else "SHORT"
        try:
            algo = await self.binance.set_tp_sl(
                symbol, position_side, sl_price, tp_price
            )
            repaired = []
            if "SL" in missing and algo.get("sl"):
                pos["sl_order_id"] = algo["sl"]
                repaired.append("SL")
            if "TP" in missing and algo.get("tp"):
                pos["tp_order_id"] = algo["tp"]
                repaired.append("TP")
            if repaired:
                logger.warning(
                    f"{symbol}: kayip koruma tamir edildi ({'/'.join(repaired)})"
                )
                return
        except Exception as e:
            logger.error(f"{symbol}: koruma tamiri hatasi: {e}")
        if self.telegram:
            await self.telegram.send(
                f"ATOS X UYARI: {symbol} pozisyonunun {'/'.join(missing)} emri "
                f"borsada yok ve yeniden yerleştirilemedi! "
                f"Koruma kayboldu, manuel müdahale gerekebilir."
            )

    def _exchange_close_estimate(self, symbol: str, pos: dict):
        """Algo SL/TP ile kapanmis pozisyonda en olası cikis fiyati ve nedeni."""
        last = self.live_prices.get(symbol)
        if pos["side"] == "BUY":
            if pos.get("tp") and last is not None and last >= pos["tp"]:
                return pos["tp"], "take_profit"
            if pos.get("sl") and last is not None and last <= pos["sl"]:
                return pos["sl"], "stop_loss"
        else:
            if pos.get("tp") and last is not None and last <= pos["tp"]:
                return pos["tp"], "take_profit"
            if pos.get("sl") and last is not None and last >= pos["sl"]:
                return pos["sl"], "stop_loss"
        return last or pos.get("tp") or pos.get("sl") or pos["entry_price"], "exchange_closed"

    async def check_positions(self, prices):
        now = datetime.utcnow()
        for symbol, pos in list(self.active_positions.items()):
            if self.max_position_age_hours > 0:
                try:
                    opened = datetime.fromisoformat(pos["open_time"])
                    age_hours = (now - opened).total_seconds() / 3600.0
                except Exception:
                    age_hours = 0.0
                if age_hours > self.max_position_age_hours:
                    price = self.live_prices.get(symbol) or prices.get(symbol)
                    if price:
                        logger.info(
                            f"{symbol}: pozisyon {age_hours:.1f} saat acik "
                            f"(max {self.max_position_age_hours:.0f}); time_stop"
                        )
                        await self.close_position(symbol, price, "time_stop")
                    continue
            current_price = self.live_prices.get(symbol) or prices.get(symbol)
            if not current_price:
                continue
            side = pos["side"]
            if self.trailing_activate_pct > 0 and self.trailing_sl_pct > 0:
                await self._check_trailing(symbol, pos, side, current_price)
            sl = pos.get("sl")
            tp = pos.get("tp")
            if sl and side == "BUY" and current_price <= sl:
                await self.close_position(symbol, sl, "stop_loss")
            elif sl and side == "SELL" and current_price >= sl:
                await self.close_position(symbol, sl, "stop_loss")
            elif tp and side == "BUY" and current_price >= tp:
                await self.close_position(symbol, tp, "take_profit")
            elif tp and side == "SELL" and current_price <= tp:
                await self.close_position(symbol, tp, "take_profit")

    async def _check_trailing(self, symbol: str, pos: dict, side: str, price: float):
        """Kar esigini asan pozisyonun SL'sini fiyati takip edecek sekilde kaydirir.

        `trailing_activate_pct` kari asilinca SL, fiyatin
        `trailing_sl_pct` kadar gerisinde durur; SL yalnizca kari yonunde
        hareket eder (geri cekilmez). Exchange'te eski SL iptal edilip
        yenisi yerlestirilir.
        """
        entry = float(pos["entry_price"])
        if side == "BUY":
            profit_pct = (price - entry) / entry * 100.0
            new_sl = price * (1.0 - self.trailing_sl_pct / 100.0)
            cur_sl = pos.get("sl") or 0.0
            better = new_sl > cur_sl
        else:
            profit_pct = (entry - price) / entry * 100.0
            new_sl = price * (1.0 + self.trailing_sl_pct / 100.0)
            cur_sl = pos.get("sl") or float("inf")
            better = new_sl < cur_sl
        if profit_pct < self.trailing_activate_pct or not better:
            return
        if not pos.get("trailing"):
            self._log_risk_event("trailing_activate",
                                 f"{symbol} SL takibi: kar %{profit_pct:.1f}, SL {new_sl:.2f}")
        pos["sl"] = new_sl
        pos["trailing"] = True
        if self.paper or not pos.get("sl_order_id"):
            logger.info(f"{symbol}: SL takibe girdi -> {new_sl:.2f} (kar %{profit_pct:.1f})")
            return
        try:
            await self.binance.cancel_algo_order(symbol, pos["sl_order_id"])
            algo = await self.binance.set_tp_sl(
                symbol, "LONG" if side == "BUY" else "SHORT", new_sl, 0.0
            )
            if algo.get("sl"):
                pos["sl_order_id"] = algo["sl"]
                logger.info(f"{symbol}: trailing SL {new_sl:.2f} borsaya yerleştirildi")
        except Exception as e:
            logger.error(f"{symbol}: trailing SL guncelleme hatasi: {e}")

    async def update_equity(self):
        now = time.monotonic()
        if now - self._last_perf < self.perf_interval:
            return
        self._last_perf = now
        closed = self.trade_history
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        win_rate = wins / len(closed) * 100 if closed else 0.0
        self.db.save_performance(self.equity, len(self.active_positions), len(self.trade_history), win_rate)

    async def stop(self):
        self.running = False
        self._log_risk_event("system_stop", "Motor durduruldu - tum pozisyonlar kapatiliyor")
        for symbol in list(self.active_positions.keys()):
            price = self.live_prices.get(symbol)
            if not price:
                price = await self.binance.get_price(symbol)
            await self.close_position(symbol, price, "system_stop")
        logger.info("Otomatik islem motoru durduruldu")

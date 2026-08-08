import asyncio
import time
from datetime import datetime, timezone
from typing import List

from loguru import logger

from app.backtest.engine import BacktestEngine
from app.core.config import get_settings
from app.core.database import Database
from app.core.time import utc_now
from app.data import loader
from app.data.collector import backfill as backfill_klines
from app.strategy import get_strategy
from app.strategy import settings as strat_settings
from app.strategy.coin_intel import coin_score as score_symbol
from app.strategy.decision import decide as decide_council
from app.strategy.tradebot_v23 import atr as atr_series

_SCORE_POOL = 200  # skor bazli siralama icin canli degerlendirilen sembol sayisi


def _fmt_px(x: float) -> str:
    """Kucuk fiyatli sembollerde (0.0032 vb.) .2f yuvarlamasini onler."""
    if x >= 1000:
        return f"{x:.0f}"
    if x >= 1:
        return f"{x:.2f}"
    if x >= 0.01:
        return f"{x:.4f}"
    return f"{x:.6f}"


class AutoTrader:
    """Canli islem motoru: v23 sinyalleri -> risk bazli boyutlandirma -> DB.

    Boyutlandirma BacktestEngine.position_size ile ayni mantigi kullanir;
    acilip kapanan tum islemler DB'ye yazilir (acilis ve kapanis kaydi).
    Websocket'ten gelen fiyatlar `update_price` ile buraya akar, REST
    taramasi fallback olarak kalir.

    `paper=True` iken emirler borsaya gitmez; sinyal fiyatindan simule
    edilerek kaydedilir (canli riski olmadan uctan uca deneme icin).
    """

    def __init__(
        self,
        binance_client,
        telegram=None,
        paper=None,
        live_trading_enabled=None,
        min_notional=None,
    ):
        self.binance = binance_client
        self.db = Database()
        self.telegram = telegram
        self.paper = get_settings().PAPER_TRADING if paper is None else bool(paper)
        self.live_trading_enabled = (
            bool(get_settings().LIVE_TRADING_ENABLED)
            if live_trading_enabled is None
            else bool(live_trading_enabled)
        )
        self.min_notional = (
            float(get_settings().MIN_NOTIONAL or 0.0)
            if min_notional is None
            else float(min_notional)
        )
        self.halt_entries = False
        self.trading_mode = self._resolve_mode()
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
        self.trailing_min_move_pct = float(s.get("trailing_min_move_pct", 0.1))
        self.breakeven_activate_pct = float(s.get("breakeven_activate_pct", 2.0))
        self.peak_equity = float(s["initial_equity"])
        self.drawdown_pct = 0.0
        self.risk_halted = False
        self.max_consecutive_losses = int(s.get("max_consecutive_losses", 5))
        self.consecutive_losses = 0
        self.loss_halted = False
        self.max_daily_loss_pct = float(s.get("max_daily_loss_pct", 5.0))
        self.day_pnl = 0.0
        self.day_start_date = utc_now().date().isoformat()
        self.daily_loss_halted = False
        self.min_equity = float(s.get("min_equity", 5000.0))
        self.equity_halted = False
        self._conc_alerts = {"symbols": set(), "sides": set()}
        self._conc_blocks = set()
        self._last_block_state = set()
        self._stale_restore_queue: list = []
        self._ai_predictor_cache = None
        self._retrain_running = False
        self._last_retrain_check = 0.0
        self._last_block_summary = 0.0
        self.block_summary_interval = 3600
        self.risk_events = []
        self.risk_events_max = 200
        try:
            self.risk_events = list(
                reversed(self.db.get_risk_events(self.risk_events_max))
            )
        except Exception:
            self.risk_events = []
        try:
            self.trade_history = list(reversed(self.db.get_closed_trades(200)))
        except Exception:
            self.trade_history = []
        self.consecutive_losses = self._count_consecutive_losses()
        if (
            self.max_consecutive_losses > 0
            and self.consecutive_losses >= self.max_consecutive_losses
        ):
            self.loss_halted = True
        self._restore_risk_state()
        self.scan_interval = 30
        self.scan_limit = 50
        self.perf_interval = 60
        self.reconcile_interval = 300
        self._last_reconcile = 0.0
        self._last_perf = 0.0
        self.live_balance = None
        self._agent_klines_map = {}
        self._agent_macro = {}
        self._agent_micro = {}
        self._agent_corr = {}
        self._agent_data_ts = 0.0
        self._agent_oi_cache: dict = {}
        try:
            from app.marketdata.whale_tracker import WhaleTracker

            self._whale = WhaleTracker()
        except Exception:
            self._whale = None

    def _resolve_mode(self) -> str:
        """Calisma modunu belirler: paper / kill-switch / live."""
        if self.paper:
            return "paper"
        if not self.live_trading_enabled:
            return "kill-switch"
        return "live"

    def _log_risk_event(self, event_type: str, message: str, **extra):
        """Risk/blok olaylarini son-N halka tamponuna ve DB'ye kalici yazar."""
        entry = {
            "time": utc_now().isoformat(),
            "type": event_type,
            "message": message,
            **extra,
        }
        self.risk_events.append(entry)
        if len(self.risk_events) > self.risk_events_max:
            self.risk_events = self.risk_events[-self.risk_events_max :]
        try:
            self.db.save_risk_event(event_type, message, entry["time"])
        except Exception as e:
            logger.warning(f"Risk olayi DB'ye yazilamadi: {e}")

    def _risk_state(self) -> dict:
        """Restart sonrasi geri yuklenecek runtime risk durumu."""
        return {
            "equity": self.equity,
            "peak_equity": self.peak_equity,
            "drawdown_pct": self.drawdown_pct,
            "day_pnl": self.day_pnl,
            "day_start_date": self.day_start_date,
            "consecutive_losses": self.consecutive_losses,
            "risk_halted": 1 if self.risk_halted else 0,
            "loss_halted": 1 if self.loss_halted else 0,
            "daily_loss_halted": 1 if self.daily_loss_halted else 0,
            "equity_halted": 1 if self.equity_halted else 0,
        }

    def _persist_risk_state(self):
        """Risk durumunu DB'ye yazar (restart dayanikliligi)."""
        try:
            self.db.save_state_batch(self._risk_state())
        except Exception as e:
            logger.warning(f"Risk durumu DB'ye yazilamadi: {e}")

    def _restore_risk_state(self):
        """Risk durumunu DB'den geri yukler.

        Gun degismisse gunluk sayaçlar sifirlanir; deterministik olarak
        yeniden hesaplanabilen bayraklar (`loss_halted`, `equity_halted`)
        geri yuklenmez, kaynagindan tekrar turetilir.
        """
        try:
            state = self.db.get_all_state()
        except Exception:
            return
        if not state:
            return
        self.equity = float(state.get("equity", self.equity))
        self.peak_equity = float(state.get("peak_equity", self.peak_equity))
        if self.peak_equity < self.equity:
            self.peak_equity = self.equity
        self.drawdown_pct = float(state.get("drawdown_pct", self.drawdown_pct))
        saved_day = state.get("day_start_date")
        today = utc_now().date().isoformat()
        if saved_day == today:
            self.day_start_date = saved_day
            self.day_pnl = float(state.get("day_pnl", 0.0))
            self.daily_loss_halted = bool(int(state.get("daily_loss_halted", 0)))
        elif saved_day:
            self.day_start_date = today
            self.day_pnl = 0.0
            self.daily_loss_halted = False
        if int(state.get("risk_halted", 0)):
            self.risk_halted = True
        if self.min_equity > 0:
            self.equity_halted = self.equity < self.min_equity

    def rank_symbols(self, limit: int = 500) -> List[str]:
        """Yerel OHLCV arsivinde backtest kalitesine gore sembol siralamasi.

        Bu siralama, canli taramada ilk `scan_limit` sembolun nereden
        secilecegini belirler. Verisi olmayan ya da yeterli islem
        uretmeyen semboller elenir.
        """
        bot = get_strategy(strat_settings.get_settings())
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
            rows.append(
                (
                    float(m.get("sharpe", 0.0) or 0.0),
                    float(m.get("net_profit", 0.0) or 0.0),
                    symbol,
                )
            )
        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
        return [r[2] for r in rows]

    async def _refresh_ranking(self):
        """Backtest kalitesine gore oncelik listesi; skor siralamasi aciksa canli momentumla birlestirir."""
        loop = asyncio.get_running_loop()
        ranked = await loop.run_in_executor(None, self.rank_symbols)
        if not ranked:
            return
        if strat_settings.get_settings().get("use_score_ranking", False):
            ranked = await self._rank_by_score(ranked)
        self.priority = ranked
        self.top_symbols = ranked[: self._scan_limit()]
        self._last_rank = time.time()
        logger.info(
            f"Backtest oncelik listesi: {len(ranked)} sembol, "
            f"tarama secimi: {', '.join(self.top_symbols[:10])}"
        )

    async def _rank_by_score(self, ranked: List[str]) -> List[str]:
        """Canli coin_score'a gore oncelik listesini yeniden siralar.

        Ilk `_SCORE_POOL` sembol canli 4h kline ile skorlanir ve skor azalan
        siralama listeye bas koyar; skoru alinamayan ya da havuz disindaki
        semboller mevcut (backtest) siralama korunarak arkaya eklenir.
        """
        pool = ranked[:_SCORE_POOL]
        klines_map = await self._fetch_klines_batch(pool)
        scored = []
        for symbol in pool:
            df = klines_map.get(symbol)
            if df is None or len(df) < 25:
                continue
            try:
                info = score_symbol(df)
                scored.append((info["score"], symbol))
            except Exception:
                continue
        scored.sort(key=lambda x: x[0], reverse=True)
        reordered = [s for _, s in scored]
        seen = set(reordered)
        reordered += [s for s in ranked if s not in seen]
        logger.info(
            f"Skor siralamasi: {len(scored)}/{len(pool)} sembol skorlandi; "
            f"ilk 5: {', '.join(reordered[:5])}"
        )
        return reordered

    async def _ensure_data_freshness(self):
        """Ranking icin gerekli yerel CSV verisini taze tutar.

        Top sembollerin CSV'si kontrol edilir; eksik ya da son bari
        `data_freshness_hours`'tan eski olanlar `backfill_klines` ile tazelenir.
        """
        s = strat_settings.get_settings()
        fresh_h = float(s.get("data_freshness_hours", 12.0))
        stale = []
        for symbol in self.priority[:100]:
            try:
                df = loader.load_csv(symbol, "4h", limit=30)
            except Exception:
                stale.append(symbol)
                continue
            last = df.index[-1].to_pydatetime()
            if last.tzinfo is not None:
                last = last.replace(tzinfo=None)
            age_h = (utc_now() - last).total_seconds() / 3600.0
            if age_h > fresh_h:
                stale.append(symbol)
        if not stale:
            logger.info("Veri tazeligi kontrolu: tum semboller guncel")
            return
        logger.info(
            f"{len(stale)} sembol verisi eski; backfill basliyor: "
            f"{', '.join(stale[:10])}"
        )
        try:
            await backfill_klines(self.binance, stale, interval="4h", days=30)
        except Exception as e:
            logger.error(f"Otomatik backfill hatasi: {e}")

    async def _data_backfill_loop(self):
        """`data_backfill_hours` arayla eski CSV verisini otomatik tazeler."""
        while self.running:
            try:
                hours = float(
                    strat_settings.get_settings().get("data_backfill_hours", 0.0) or 0.0
                )
                if hours > 0:
                    await asyncio.sleep(hours * 3600)
                    await self._ensure_data_freshness()
                else:
                    await asyncio.sleep(600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Veri tazelik dongusu hatasi: {e}")
                await asyncio.sleep(300)

    async def _agent_market_data_loop(self):
        """Ajan konseyi icin piyasa geneli verileri toplar (macro/micro/corr).

        - macro (Stooq DXY/VIX/SPX/GLD/EURUSD): 6 saat TTL cache, ilk cagri hizli
        - micro (OI/funding/L-S/taker/orderbook/premium/whale): ~5 dk'da bir,
          yalnizca oncelikli ~20 sembol icin (200 sembol x 6 fapi cagrisi cok agir)
        - korelasyon matrisi: ~30 dk'da bir, son klines_map uzerinden
        Hatalar sessiz gecilir; veri yoksa ajanlar cekimser kalir.
        """
        while self.running:
            try:
                try:
                    from app.marketdata.stooq import macro_summary

                    self._agent_macro = macro_summary() or {}
                except Exception as e:
                    logger.warning(f"Makro veri hatasi: {e}")
                try:
                    await self._collect_agent_micro()
                except Exception as e:
                    logger.warning(f"Mikro veri hatasi: {e}")
                try:
                    from app.marketdata.correlation import correlation_report

                    symbols = self.priority or list(self._agent_klines_map.keys())
                    if symbols:
                        self._agent_corr = (
                            correlation_report(
                                self._agent_klines_map, symbols, lookback=90, top_n=40
                            )
                            or {}
                        )
                except Exception as e:
                    logger.warning(f"Korelasyon hatasi: {e}")
                await asyncio.sleep(300)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ajan veri dongusu hatasi: {e}")
                await asyncio.sleep(300)

    async def _collect_agent_micro(self):
        """Oncelikli sembollerin mikro yapi verilerini toplar (5 dk cache)."""
        from app.marketdata.binance_extra import BinanceExtraData

        extra = BinanceExtraData(self.binance)
        top = (
            self.priority or self.trading_symbols or list(self._agent_klines_map.keys())
        )[:20]
        micro = {}
        for sym in top:
            entry = {}
            try:
                oi = await extra.open_interest(sym)
                if oi:
                    hist = self._agent_oi_cache.setdefault(sym, [])
                    hist.append(oi["oi"])
                    del hist[:-10]
                    k = self._agent_klines_map.get(sym)
                    price_trend = 0.0
                    if k is not None and len(k) > 3:
                        c = k["close"]
                        price_trend = float(c.iloc[-1]) / float(c.iloc[-4]) - 1
                    entry["open_interest"] = {
                        "history": list(hist),
                        "price_trend": round(price_trend, 4),
                    }
            except Exception:
                pass
            try:
                f = await extra.funding_rate(sym)
                if f:
                    entry["funding"] = {"last": f["last"], "avg10": f["avg10"]}
            except Exception:
                pass
            try:
                ls = await extra.long_short_ratio(sym)
                if ls:
                    entry["long_short"] = ls
            except Exception:
                pass
            try:
                tk = await extra.taker_flow(sym)
                if tk:
                    entry["taker"] = tk
            except Exception:
                pass
            try:
                ob = await extra.orderbook(sym)
                if ob:
                    entry["orderbook"] = ob
            except Exception:
                pass
            try:
                pm = await extra.premium_index(sym)
                if pm:
                    entry["premium"] = pm
            except Exception:
                pass
            try:
                k = self._agent_klines_map.get(sym)
                if k is not None and len(k) > 1 and "open_interest" in entry:
                    hi = float(k["high"].tail(24).max())
                    lo = float(k["low"].tail(24).min())
                    close = float(k["close"].iloc[-1])
                    pos = (close - lo) / (hi - lo) if hi > lo else 0.5
                    hist = entry["open_interest"]["history"]
                    oi_high = len(hist) > 1 and hist[-1] > hist[0] * 1.05
                    entry["liquidation"] = {
                        "position_pct": round(pos, 3),
                        "oi_high": oi_high,
                    }
            except Exception:
                pass
            if self._whale is not None:
                try:
                    w = self._whale.flow(sym)
                    if w:
                        entry["whale"] = w
                except Exception:
                    pass
            if entry:
                micro[sym] = entry
        self._agent_micro = micro

    def update_price(self, symbol: str, price: float):
        self.live_prices[symbol] = float(price)

    async def _fetch_klines_batch(self, candidates: list) -> dict:
        """Aday sembollerin kline'larini paralel ceker (asilan istekler atlanir).

        Her istek 20 sn ile sinirlidir: testnet/ag takilmasinda asili kalan bir
        istek thread pool'daki gorevi tuketiyor ve ana tarama dongusu
        dakikalarca donuyordu (gather asla tamamlanmiyordu).
        """

        async def fetch(symbol):
            try:
                return symbol, await asyncio.wait_for(
                    self.binance.get_klines(symbol, "4h", 200), timeout=20
                )
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
                    await self.telegram.send(
                        "ATOS X: Binance baglantisi yeniden kuruldu"
                    )
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
        logger.info(f"Otomatik islem motoru baslatildi (mod: {self.trading_mode})")
        if self.trading_mode == "kill-switch":
            logger.warning(
                "LIVE_TRADING_ENABLED=False: yeni pozisyon acis emirleri kod seviyesinde engellendi"
            )
            if self.telegram:
                await self.telegram.send(
                    "ATOS X: LIVE_TRADING_ENABLED=False — yeni pozisyon acis emirleri "
                    "kill-switch tarafindan engellendi (mevcut pozisyon koruması aktif)"
                )
        if not await self._ensure_connected():
            logger.error("Binance baglantisi kurulamadi; motor baslatilamiyor")
            self.running = False
            return
        self.trading_mode = self._resolve_mode()
        self.trading_symbols = await self.binance.load_all_symbols()
        logger.info(f"{len(self.trading_symbols)} coin taranacak")
        await self.reconcile_positions()
        self._restore_paper_positions()
        await self._close_stale_restores()
        if not self.paper and self.live_balance is None:
            # Canli bakiye senkronu basarisiz: equity varsayilan deger
            # (initial_equity) uzerinden kalir, boyutlandirma yanlis olur.
            # Motor, gercek bakiye cekilmeden islem dongusune GIRMEZ.
            logger.critical(
                "Canli bakiye senkronu yapilamadi; motor durduruluyor "
                "(yanlis boyutta ilk emir onlenir)"
            )
            self._log_risk_event(
                "balance_sync_blocked",
                "Canli bakiye senkronu yapilamadi, motor baslatilmadi",
            )
            if self.telegram:
                await self.telegram.send(
                    "ATOS X UYARI: Canli bakiye senkronu yapilamadi! "
                    "Motor baslatilmadi — yanlis boyutta emir onlendi."
                )
            self.running = False
            return
        await self._check_concentration()
        await self._notify_startup_state()
        asyncio.create_task(self._refresh_ranking())
        asyncio.create_task(self._data_backfill_loop())
        asyncio.create_task(self._agent_market_data_loop())

        while self.running:
            try:
                s = strat_settings.get_settings()
                bot = get_strategy(s)
                # Risk ayarlari UI'dan degistirilirse sonraki dongude gecerli olur
                self._apply_risk_settings(s)
                all_prices = await self.binance.get_all_tickers()
                if not all_prices:
                    # Baglanti koptuysa yeniden kur, fallback sembollerde kaldiysa tazele
                    logger.warning(
                        "Borsa fiyatlari alinamadi; baglanti yeniden deneniyor"
                    )
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
                ranked = (
                    [s for s in self.priority if s in all_prices]
                    if self.priority
                    else None
                )
                candidates = (
                    ranked[: self._scan_limit()]
                    if ranked
                    else self.trading_symbols[: self._scan_limit()]
                )
                candidates = self._filter_banned(candidates, s)

                klines_map = await self._fetch_klines_batch(candidates)
                self._agent_klines_map = klines_map
                try:
                    self._resolve_pending_predictions(klines_map)
                except Exception as e:
                    logger.warning(f"AI tahmin cozumleme hatasi: {e}")
                try:
                    self._resolve_agent_votes(klines_map)
                except Exception as e:
                    logger.warning(f"Ajan oy cozumleme hatasi: {e}")

                for symbol in candidates:
                    klines = klines_map.get(symbol)
                    if klines is None or len(klines) < 30:
                        continue

                    signal = bot.generate_signal(klines)
                    price = float(klines["close"].iloc[-1])
                    signal["bar_ts"] = str(klines.index[-1])
                    signal["atr_ratio"] = self._signal_atr_ratio(klines)

                    if (
                        signal.get("signal") in ("BUY", "SELL")
                        and signal.get("sl")
                        and signal.get("tp")
                    ):
                        (
                            allow_ai,
                            ai_info,
                            allow,
                            decision,
                            allow_str,
                            str_info,
                            allow_agents,
                            agent_info,
                        ) = self._gate_and_record(symbol, signal, klines, s)
                        if not allow:
                            logger.info(
                                f"{symbol}: council karari sinyali engelledi"
                                f" ({decision['verdict']}, guven {decision['confidence']})"
                            )
                            continue
                        if not allow_agents:
                            logger.info(
                                f"{symbol}: ajan konseyi sinyali engelledi"
                                f" ({agent_info['verdict']}, guven {agent_info['confidence']})"
                            )
                            self._log_risk_event(
                                "agent_gate_block",
                                f"{symbol} {signal['signal']} sinyali ajan konseyi tarafindan engellendi"
                                f" ({agent_info.get('verdict')}, "
                                f"blok: {','.join(agent_info.get('block_sources', []))})",
                                confidence=float(agent_info.get("confidence", 0.0)),
                            )
                            continue
                        if not allow_str:
                            logger.info(
                                f"{symbol}: sinyal gucu esigin altinda engellendi"
                                f" (%{str_info['strength'] * 100:.0f} < %{str_info['threshold'] * 100:.0f})"
                            )
                            self._log_risk_event(
                                "low_signal_strength",
                                f"{symbol} {signal['signal']} sinyali engellendi",
                                strength=float(str_info["strength"]),
                                threshold=float(str_info["threshold"]),
                            )
                            continue
                        if not allow_ai:
                            logger.info(
                                f"{symbol}: AI tahmini sinyali engelledi"
                                f" ({ai_info['direction']}, guven {ai_info['confidence']:.2f})"
                            )
                            self._log_risk_event(
                                "ai_gate_block",
                                f"{symbol} {signal['signal']} sinyali AI tarafindan engellendi",
                                confidence=float(ai_info["confidence"]),
                            )
                            continue
                        entry = {
                            "symbol": symbol,
                            "signal": signal["signal"],
                            "price": price,
                            "sl": signal["sl"],
                            "tp": signal["tp"],
                            "reason": signal.get("reason", ""),
                            "entry_ts": str(klines.index[-1]),
                        }
                        if decision:
                            entry["council_confidence"] = decision["confidence"]
                            entry["council_reason"] = decision["reason"]
                        if ai_info:
                            entry["ai_direction"] = ai_info["direction"]
                            entry["ai_confidence"] = ai_info["confidence"]
                        if agent_info:
                            entry["agent_confidence"] = agent_info.get("confidence")
                            entry["agent_verdict"] = agent_info.get("verdict")
                            entry["agent_votes"] = agent_info.get("votes")
                            entry["agent_adjusts"] = agent_info.get("adjusts")
                        signals.append(entry)
                        logger.info(f"{symbol}: {signal['signal']} @ {price}")

                await self.process_signals(signals)
                await self.check_positions(all_prices)
                if time.time() - self._last_reconcile > self.reconcile_interval:
                    await self.reconcile_positions()
                    self._last_reconcile = time.time()
                await self.update_equity()
                await self._check_concentration()
                await self._check_drawdown()
                await self._check_equity_floor()
                self._maybe_retrain_ai()
                self._maybe_retrain_agents()
                await asyncio.sleep(self.scan_interval)

            except Exception as e:
                logger.error(f"Otomatik islem hatasi: {e}")
                await asyncio.sleep(10)

    def _gate_and_record(self, symbol: str, signal: dict, klines, settings: dict):
        """Kapilarin tamamini isletir ve sinyali her durumda AI kaydina yazar.

        Kayit kapilardan ONCE yapilir ki council/guc engeli sinyali AI feedback
        dongusunden kacirmasin (`executed` yalnizca tum kapilar gecerse 1).
        Donus: (allow_ai, ai_info, allow, decision, allow_str, str_info,
                allow_agents, agent_info).
        """
        allow_ai, ai_info = self._ai_gate(signal, klines, settings)
        allow, decision = self._council_gate(signal["signal"], klines, settings)
        allow_str, str_info = self._strength_gate(signal, settings)
        allow_agents, agent_info = self._agent_gate(symbol, signal, klines, settings)
        try:
            self._record_prediction(
                symbol,
                signal,
                decision,
                ai_info,
                executed=bool(allow and allow_str and allow_ai and allow_agents),
            )
        except Exception as e:
            logger.warning(f"AI tahmin kaydi hatasi {symbol}: {e}")
        return (
            allow_ai,
            ai_info,
            allow,
            decision,
            allow_str,
            str_info,
            allow_agents,
            agent_info,
        )

    def _scan_limit(self) -> int:
        """Tarama limiti: ayarlardan (runtime degistirilebilir), yoksa sabitten."""
        return max(
            1, int(strat_settings.get_settings().get("scan_limit", self.scan_limit))
        )

    def _council_gate(self, signal, klines, settings):
        """Decision Council filtresi.

        Kapi kapaliysa (use_decision_council=False) her sinyali gecirir. Acikken
        council karari sinyal yonunde degilse veya guven esigin altindaysa sinyali
        reddeder. TTP modunda (`active_strategy=ttp`) sinyalin kendisi birincil oy
        olur (v23 zorunlulugu kalkar); v23 modunda v23 sinyali birincildir.
        Donus: (allow, decision|None).
        """
        if not settings.get("use_decision_council", False):
            return True, None
        if settings.get("active_strategy") == "ttp":
            decision = decide_council(
                klines,
                settings=settings,
                primary_signal={"signal": signal, "source": "ttp"},
            )
        else:
            decision = decide_council(klines, settings=settings)
        if decision["verdict"] != signal or decision["confidence"] < float(
            settings.get("council_min_confidence", 0.6)
        ):
            return False, decision
        return True, decision

    def _strength_gate(self, signal, settings):
        """Minimum sinyal gucu (konfirmasyon orani) filtresi.

        Esik 0 ise kapali, her sinyali gecirir. Esik > 0 ise sinyalin
        `strength` alani esigin altindaysa gecirmez. Donus: (allow, info|None).
        """
        threshold = float(settings.get("min_signal_strength", 0.0) or 0.0)
        if threshold <= 0:
            return True, None
        strength = float(signal.get("strength", 0.0) or 0.0)
        if strength < threshold:
            return False, {"strength": strength, "threshold": threshold}
        return True, None

    def _agent_gate(self, symbol: str, signal: dict, klines, settings: dict):
        """50 ajan konseyi kapisi (app/agents) + analog bellek + oy kaydi.

        `use_agent_council` kapaliysa gecirir. Acikken `run_council` akisi
        isletilir (calistir -> tur 2 danisma -> konsensus karari). Risk
        vetosu, yetersiz quorum/kategori veya zayif konsensus sinyali
        engeller; `agent_min_confidence` guven esigidir. Analog bellek varsa
        oy veren ajanlar `agent_votes` tablosuna kaydedilir (feedback dongusu).
        Donus: (allow, agent_info|None).
        """
        if not settings.get("use_agent_council", False):
            return True, None
        try:
            from app.agents.analog import get_memory
            from app.agents.context import AgentContext
            from app.agents.feedback import record_votes
            from app.agents.orchestrator import run_council

            analog = {}
            mem = get_memory()
            if mem is not None:
                for key in ("trend", "momentum", "reversal", "regime"):
                    res = mem.query(klines, key=key)
                    if res:
                        analog[key] = res
            ctx = AgentContext(
                symbol=symbol,
                df=klines,
                klines_map=self._agent_klines_map or {symbol: klines},
                prices=dict(self.live_prices or {}),
                macro=self._agent_macro,
                micro=(self._agent_micro or {}).get(symbol, {}),
                portfolio=[
                    {"symbol": s, "side": p.get("side"), "status": "OPEN"}
                    for s, p in self.active_positions.items()
                ],
                settings=settings,
                corr=self._agent_corr,
                extra={
                    "drawdown_pct": float(self.drawdown_pct or 0.0),
                    "risk_halted": bool(self.risk_halted),
                    "predictor": self._ai_predictor()
                    if settings.get("use_ai_model")
                    else None,
                    "analog": analog,
                },
            )
            results, verdict_info = run_council(ctx, settings)
            adjusts = verdict_info["adjustments"]
            try:
                record_votes(
                    self.db,
                    symbol,
                    signal.get("bar_ts") or str(klines.index[-1]),
                    results,
                    price=float(klines["close"].iloc[-1]),
                )
            except Exception as e:
                logger.warning(f"{symbol}: ajan oy kaydi hatasi: {e}")
            info = {
                "verdict": verdict_info["verdict"],
                "confidence": verdict_info["confidence"],
                "consensus": verdict_info["consensus"],
                "net": round(verdict_info["buy"] - verdict_info["sell"], 3),
                "buy": verdict_info["buy"],
                "sell": verdict_info["sell"],
                "votes": verdict_info["votes"],
                "agree_categories": verdict_info["agree_categories"],
                "blocked": verdict_info["blocked"],
                "hold_reason": verdict_info["hold_reason"],
                "consulted": sum(1 for r in results if r.meta.get("consulted")),
                "adjusts": adjusts,
            }
            if verdict_info["blocked"]:
                info["block_sources"] = adjusts["block_sources"]
                return False, info
            threshold = float(settings.get("agent_min_confidence", 0.5) or 0.0)
            if (
                verdict_info["verdict"] != signal.get("signal")
                or verdict_info["confidence"] < threshold
            ):
                return False, info
            return True, info
        except Exception as e:
            logger.warning(f"{symbol}: ajan konseyi hatasi: {e}")
            return True, None

    def _ai_gate(self, signal, df, settings):
        """TensorFlow AI yon tahmini kapisi.

        `use_ai_model` kapali ya da model yuklenememisse her sinyali gecirir
        (pasif). Model yukluyse tahmin yonu sinyal yonunde degilse veya guven
        `ai_min_confidence` altindaysa sinyali engeller.
        """
        if not settings.get("use_ai_model", False):
            return True, None
        predictor = self._ai_predictor()
        if predictor is None:
            return True, None
        pred = predictor.predict(df)
        threshold = float(settings.get("ai_min_confidence", 0.0) or 0.0)
        if pred["direction"] != signal.get("signal") or pred["confidence"] < threshold:
            return False, pred
        return True, pred

    def _ai_predictor(self):
        """Yuklenmis AI predictorunu modul seviyesinde otelemeli dondurur."""
        if self._ai_predictor_cache is None:
            try:
                from app.ai.model import load_predictor

                model_name = str(
                    strat_settings.get_settings().get("ai_model_path", "ai_direction")
                )
                self._ai_predictor_cache = load_predictor(model_name) or False
            except Exception as e:
                logger.warning(f"AI predictor yuklenemedi: {e}")
                self._ai_predictor_cache = False
        return None if self._ai_predictor_cache is False else self._ai_predictor_cache

    def _maybe_retrain_ai(self, now: float = None):
        """Otomatik yeniden egitim kontrolu (zaman + canli accuracy tetikleyicileri).

        En fazla 15 dakikada bir degerlendirir; tetiklenirse egitimi arka plan
        gorevi olarak baslatir (event loop bloke olmaz). Egitim bittiginde
        predictor cache'i gecersizlesir, sonraki tahminde yeni model yuklenir.
        """
        if getattr(self, "_retrain_running", False):
            return
        now = now or time.time()
        if now - self._last_retrain_check < 900:
            return
        self._last_retrain_check = now
        try:
            s = strat_settings.get_settings()
        except Exception:
            return
        if not s.get("ai_auto_retrain", False):
            return
        try:
            from app.ai.retrain import accuracy_trigger, last_trained_at, retrain_due

            model_name = str(s.get("ai_model_path", "ai_direction"))
            last = last_trained_at(model_name)
            interval = float(s.get("ai_retrain_interval_hours", 24.0) or 0.0)
            due = interval > 0 and retrain_due(last, now, interval)
            if not due:
                try:
                    stats = self.db.ai_stats()
                except Exception:
                    stats = {}
                due = accuracy_trigger(
                    int(stats.get("resolved", 0) or 0),
                    float(stats.get("accuracy", 0.0) or 0.0),
                    int(s.get("ai_retrain_min_samples", 30)),
                    float(s.get("ai_retrain_min_acc", 0.55) or 0.0),
                    last,
                    now,
                )
            if not due:
                return
        except Exception as e:
            logger.warning(f"AI yeniden egitim kontrolu hatasi: {e}")
            return
        self._retrain_running = True
        asyncio.create_task(self._run_retrain(s, model_name))

    async def _run_retrain(self, settings: dict, model_name: str):
        """Arka plan egitim gorevi: alt sureci calistirir, sonucu bildirir."""
        try:
            from app.ai.retrain import RetrainRunner

            symbols = int(settings.get("ai_retrain_symbols", 400) or 400)
            epochs = int(settings.get("ai_retrain_epochs", 30) or 30)
            horizon = int(settings.get("ai_horizon", 24) or 24)
            atr_mult = float(settings.get("ai_atr_mult", 1.0) or 1.0)
            if self.telegram:
                await self.telegram.send(
                    f"AI yeniden egitimi basladi ({model_name}, "
                    f"{symbols} sembol, {epochs} epoch, h={horizon})..."
                )
            logger.info(
                f"AI yeniden egitimi basladi: {model_name} "
                f"({symbols} sembol, {epochs} epoch, h={horizon})"
            )
            ok, tail = await RetrainRunner(model_name=model_name).train(
                symbols=symbols, epochs=epochs, horizon=horizon, atr_mult=atr_mult
            )
            if ok:
                self._ai_predictor_cache = None
                logger.info(f"AI modeli yeniden egitildi: {model_name}")
                if self.telegram:
                    msg = f"AI modeli yeniden egitildi ve yuklendi ({model_name})."
                    if tail:
                        msg += f"\n{tail}"
                    await self.telegram.send(msg)
            else:
                logger.warning(f"AI yeniden egitimi basarisiz: {tail}")
                if self.telegram:
                    await self.telegram.send(
                        f"AI yeniden egitimi BASARISIZ ({model_name}): {tail}"
                    )
        except Exception as e:
            logger.warning(f"AI yeniden egitim hatasi: {e}")
            if self.telegram:
                try:
                    await self.telegram.send(
                        f"AI yeniden egitimi hata ile sonlandi: {e}"
                    )
                except Exception:
                    pass
        finally:
            self._retrain_running = False

    def _resolve_agent_votes(self, klines_map: dict, resolution_bars: int = None):
        """Bekleyen ajan oylarini bar-bazli cozumler (feedback dongusu).

        Oy barindan `agent_feedback_horizon` (varsayilan 24) bar sonraki
        kapanisla karsilastirir; veri yetmiyorsa oy bekler. Cok eski
        bekleyenler `na` yapilir. Hata sessizce atlanir.
        """
        try:
            from app.agents.feedback import resolve_stale, resolve_symbol

            if resolution_bars is None:
                resolution_bars = int(
                    strat_settings.get_settings().get("agent_feedback_horizon", 24)
                    or 24
                )
            for symbol, klines in (klines_map or {}).items():
                if klines is None or len(klines) == 0:
                    continue
                try:
                    resolve_symbol(self.db, klines, symbol, resolution_bars)
                except Exception:
                    continue
            resolve_stale(self.db, days=30)
        except Exception as e:
            logger.warning(f"Ajan oy cozumleme hatasi: {e}")

    def _maybe_retrain_agents(self, now: float = None):
        """Agent konseyi otomatik egitim kontrolu (zaman + isabet tetikleyicileri).

        En fazla 15 dakikada bir degerlendirir; tetiklenirse analog bellek +
        agirlik egitimini arka plan gorevi olarak baslatir. Egitim bittiginde
        bellek cache'i temizlenir ve sonraki kapida yeniden yuklenir.
        """
        if getattr(self, "_agent_retrain_running", False):
            return
        now = now or time.time()
        if now - getattr(self, "_agent_retrain_check", 0.0) < 900:
            return
        self._agent_retrain_check = now
        try:
            s = strat_settings.get_settings()
        except Exception:
            return
        if not s.get("agent_auto_retrain", False):
            return
        try:
            from app.agents.retrain import (
                accuracy_trigger,
                agent_accuracy,
                agent_retrain_due,
                last_trained_at,
            )

            last = last_trained_at()
            interval = float(s.get("agent_retrain_interval_hours", 24.0) or 0.0)
            due = interval > 0 and agent_retrain_due(last, now, interval)
            if not due:
                due = accuracy_trigger(
                    agent_accuracy(self.db),
                    float(s.get("agent_min_acc", 0.40) or 0.0),
                    last,
                    now,
                )
        except Exception as e:
            logger.warning(f"Agent egitim kontrolu hatasi: {e}")
            return
        if not due:
            return
        self._agent_retrain_running = True
        asyncio.create_task(self._run_retrain_agents(s))

    async def _run_retrain_agents(self, settings: dict):
        """Arka plan agent egitim gorevi: alt sureci calistirir, sonucu bildirir."""
        try:
            from app.agents.retrain import AgentRetrainRunner

            symbols = int(settings.get("agent_retrain_symbols", 150) or 150)
            horizon = int(settings.get("agent_feedback_horizon", 24) or 24)
            if self.telegram:
                await self.telegram.send(
                    f"Agent konseyi egitimi basladi ({symbols} sembol, h={horizon})..."
                )
            logger.info(f"Agent konseyi egitimi basladi ({symbols} sembol)")
            ok, tail = await AgentRetrainRunner().train(
                symbols=symbols, horizon=horizon
            )
            if ok:
                logger.info("Agent konseyi egitildi (analog bellek + agirliklar)")
                if self.telegram:
                    msg = "Agent konseyi egitildi (analog bellek + agirliklar)."
                    if tail:
                        msg += f"\n{tail}"
                    await self.telegram.send(msg)
            else:
                logger.warning(f"Agent konseyi egitimi basarisiz: {tail}")
                if self.telegram:
                    await self.telegram.send(f"Agent konseyi egitimi BASARISIZ: {tail}")
        except Exception as e:
            logger.warning(f"Agent konseyi egitim hatasi: {e}")
        finally:
            self._agent_retrain_running = False

    def _record_prediction(
        self,
        symbol: str,
        signal: dict,
        decision: dict,
        ai_info: dict,
        executed: bool = False,
    ):
        """Her BUY/SELL sinyali icin AI yon tahminini DB'ye yazar (feedback dongusu).

        `executed` = AI kapisindan gecildi (entry olusturuldu). AI kapali ya da
        model yoksa `ai_direction` NULL kalir; yine de kaydedilir ki sinyal-tahmin
        karsilastirmasi yapilabilsin.
        """
        self.db.save_prediction(
            symbol=symbol,
            signal=signal.get("signal", ""),
            price=float(signal.get("price", 0.0) or 0.0),
            ai_direction=ai_info.get("direction") if ai_info else None,
            ai_confidence=ai_info.get("confidence") if ai_info else None,
            council_confidence=decision.get("confidence") if decision else None,
            strength=float(signal.get("strength", 0.0) or 0.0),
            executed=executed,
            bar_ts=signal.get("bar_ts"),
        )

    def _resolve_pending_predictions(
        self, klines_map: dict, resolution_bars: int = None
    ):
        """Bekleyen AI tahminlerini bar-bazli cozer.

        Tahmin barindan `resolution_bars` bar sonraki kapanis, tahmin anindaki
        fiyatla karsilastirilir: BUY -> yukseldiyse hit, SELL -> dustuyse hit.
        `resolution_bars` verilmezse modelin horizon'u kullanilir (model yoksa 12).
        Veri yetmiyorsa bekler; sembol cikmis ya da cok eskiyse `na`.
        """
        if resolution_bars is None:
            try:
                pred = self._ai_predictor()
                resolution_bars = pred.horizon if pred is not None else 12
            except Exception:
                resolution_bars = 12
        pending = self.db.list_pending_predictions(limit=200)
        if not pending:
            return
        for pred in pending:
            df = klines_map.get(pred["symbol"])
            if df is None or len(df) < 2:
                continue
            bar_ts = pred.get("bar_ts")
            if not bar_ts:
                self.db.resolve_prediction(pred["id"], "na")
                continue
            try:
                idxs = list(df.index.astype(str))
                pos = idxs.index(bar_ts)
            except ValueError:
                self.db.resolve_prediction(pred["id"], "na")
                continue
            if pos + resolution_bars >= len(df):
                continue
            p0 = float(pred["price"] or df["close"].iloc[pos])
            p1 = float(df["close"].iloc[pos + resolution_bars])
            direction = pred.get("ai_direction")
            if direction == "BUY":
                outcome = "hit" if p1 > p0 else "miss"
            elif direction == "SELL":
                outcome = "hit" if p1 < p0 else "miss"
            else:
                outcome = "na"
            self.db.resolve_prediction(pred["id"], outcome)
        self.db.resolve_stale_predictions()

    async def process_signals(self, signals):
        for signal in signals:
            symbol = signal["symbol"]
            if symbol in self.active_positions:
                # Acik pozisyonda ters yonde sinyal -> kapat
                if signal["signal"] != self.active_positions[symbol]["side"]:
                    await self.close_position(symbol, signal["price"], "signal_exit")
            else:
                self._rollover_day()
                if self.risk_halted:
                    logger.info(f"{symbol}: drawdown korumasi aktif, giris engellendi")
                    continue
                if self.halt_entries:
                    logger.info(
                        f"{symbol}: yeni girisler durduruldu (halt_entries), giris atlandi"
                    )
                    continue
                if self.loss_halted:
                    logger.info(
                        f"{symbol}: ardısık zarar korumasi aktif, giris engellendi"
                    )
                    continue
                if self.daily_loss_halted:
                    logger.info(
                        f"{symbol}: gunluk zarar korumasi aktif, giris engellendi"
                    )
                    continue
                if self.equity_halted:
                    logger.info(
                        f"{symbol}: equity taban korumasi aktif, giris engellendi"
                    )
                    continue
                if len(self.active_positions) >= self.max_positions:
                    continue
                side = "LONG" if signal["signal"] == "BUY" else "SHORT"
                notional = self._projected_notional(
                    signal["price"],
                    signal["sl"],
                    signal.get("atr_ratio"),
                    signal.get("agent_adjusts"),
                )
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
                    symbol,
                    signal["signal"],
                    signal["price"],
                    signal["sl"],
                    signal["tp"],
                    signal.get("reason"),
                    float(signal.get("strength", 0.0) or 0.0),
                    entry_ts=signal.get("entry_ts"),
                    council_confidence=signal.get("council_confidence"),
                    ai_direction=signal.get("ai_direction"),
                    ai_confidence=signal.get("ai_confidence"),
                    atr_ratio=signal.get("atr_ratio"),
                    agent_adjusts=signal.get("agent_adjusts"),
                )

    def _apply_risk_settings(self, s: dict):
        """Risk ayarlarini canli uygular (UI'dan degisimler bir sonraki dongude)."""
        self.max_positions = int(s["max_open_positions"])
        self.engine.risk_per_trade = float(s["risk_per_trade"])
        self.engine.max_leverage = float(s["max_leverage"])
        self.max_drawdown_pct = float(s.get("max_drawdown_pct", self.max_drawdown_pct))
        self.max_position_age_hours = float(
            s.get("max_position_age_hours", self.max_position_age_hours)
        )
        self.max_consecutive_losses = int(
            s.get("max_consecutive_losses", self.max_consecutive_losses)
        )
        self.trailing_activate_pct = float(
            s.get("trailing_activate_pct", self.trailing_activate_pct)
        )
        self.trailing_sl_pct = float(s.get("trailing_sl_pct", self.trailing_sl_pct))
        self.trailing_min_move_pct = float(
            s.get("trailing_min_move_pct", self.trailing_min_move_pct)
        )
        self.breakeven_activate_pct = float(
            s.get("breakeven_activate_pct", self.breakeven_activate_pct)
        )
        self.max_daily_loss_pct = float(
            s.get("max_daily_loss_pct", self.max_daily_loss_pct)
        )
        self.min_equity = float(s.get("min_equity", self.min_equity))
        self.engine.vol_sizing_enabled = bool(s.get("vol_sizing_enabled", False))
        self.engine.vol_mult_hi = float(s.get("vol_mult_hi", 1.5))
        self.engine.vol_mult_lo = float(s.get("vol_mult_lo", 0.6))
        self.engine.vol_mult_factor = float(s.get("vol_mult_factor", 0.5))

    def _signal_atr_ratio(self, klines) -> float:
        """Sinyal bari ATR / 20 bar ortalama ATR orani (volatilite rejimi)."""
        try:
            a = atr_series(klines, 14)
            cur = float(a.iloc[-1])
            mean20 = float(a.rolling(20).mean().iloc[-1])
            if mean20 > 0 and cur > 0:
                return cur / mean20
        except Exception:
            pass
        return 1.0

    def _projected_notional(
        self,
        price: float,
        sl: float,
        atr_ratio: float = None,
        agent_adjusts: dict = None,
    ) -> float:
        """Yeni bir pozisyonun boyutlandirma sonrasi nominal degeri."""
        try:
            sizing = self.engine.position_size(
                price, sl, self.equity, atr_ratio=atr_ratio, agent_adjusts=agent_adjusts
            )
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
        if not self.live_trading_enabled:
            logger.error(
                f"{symbol}: CANLI EMIR ENGELLENDI (LIVE_TRADING_ENABLED=False, kill-switch)"
            )
            self._log_risk_event(
                "live_order_blocked",
                f"{symbol} canli acilis emri kill-switch tarafindan engellendi",
            )
            return None
        return await self.binance.place_market_order(symbol, side, qty)

    async def _submit_close(self, symbol: str):
        if self.paper:
            return {"symbol": symbol, "paper": True}
        return await self.binance.close_position(symbol)

    async def open_position(
        self,
        symbol: str,
        side: str,
        price: float,
        sl: float,
        tp: float,
        reason: str = "",
        strength: float = 0.0,
        entry_ts=None,
        council_confidence: float = None,
        ai_direction: str = None,
        ai_confidence: float = None,
        atr_ratio: float = None,
        agent_adjusts: dict = None,
    ):
        try:
            side = "BUY" if side == "BUY" else "SELL"
            sizing = self.engine.position_size(
                price, sl, self.equity, atr_ratio=atr_ratio, agent_adjusts=agent_adjusts
            )
            qty = float(sizing["qty"])
            if qty <= 0:
                return
            if self.min_notional > 0 and price * qty < self.min_notional:
                notional = price * qty
                logger.warning(
                    f"{symbol}: notional ${notional:.2f} < min ${self.min_notional:.2f}, giris engellendi"
                )
                self._log_risk_event(
                    "min_notional_blocked",
                    f"{symbol} {side} girisi notional esiginin altinda kaldi",
                    notional=round(notional, 2),
                    min_notional=self.min_notional,
                )
                return

            if not self.paper:
                # Borsada sembolde zaten acik pozisyon varsa (kullaniciya ait
                # olabilir) girise izin verilmez — mevcut pozisyon buyutulmez.
                try:
                    ex_pos = await self.binance.get_position(symbol)
                except Exception as e:
                    logger.warning(f"{symbol}: pozisyon kontrolu yapilamadi: {e}")
                    ex_pos = None
                if ex_pos is not None:
                    logger.warning(
                        f"{symbol}: borsada mevcut pozisyon var (amt "
                        f"{ex_pos.get('positionAmt')}), cift giris engellendi"
                    )
                    self._log_risk_event(
                        "position_exists_blocked",
                        f"{symbol} {side} girisi engellendi: borsada pozisyon var",
                    )
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
                    "open_time": utc_now().isoformat(),
                    "entry_ts": str(entry_ts) if entry_ts else None,
                    "ttp_tp_hit": False,
                }
                self.db.save_trade(
                    symbol,
                    side,
                    price,
                    qty,
                    entry_ts=str(entry_ts) if entry_ts else None,
                )
                conf = (
                    float(ai_confidence)
                    if ai_confidence is not None
                    else (
                        float(council_confidence)
                        if council_confidence is not None
                        else 0.0
                    )
                )
                self.db.save_signal(symbol, side, price, conf, reason or "auto")
                if self.telegram:
                    await self.telegram.send_signal(
                        symbol,
                        side,
                        price,
                        reason,
                        sl=sl,
                        tp=tp,
                        strength=strength,
                        ai_direction=ai_direction,
                        ai_confidence=ai_confidence,
                    )
                if not self.paper:
                    position_side = "LONG" if side == "BUY" else "SHORT"
                    algo = {}
                    try:
                        if (
                            strat_settings.get_settings().get("active_strategy")
                            == "ttp"
                        ):
                            # TTPTSL: TP cikisini strateji yonetir (kismi TP icin tam
                            # pozisyon TP emri yerine SL koruma emri konur).
                            algo = await self.binance.set_tp_sl(
                                symbol, position_side, sl, 0.0
                            )
                        else:
                            algo = await self.binance.set_tp_sl(
                                symbol, position_side, sl, tp
                            )
                    except Exception as e:
                        logger.error(f"{symbol}: SL/TP koruma emri hatasi: {e}")
                    self.active_positions[symbol]["sl_order_id"] = algo.get("sl")
                    self.active_positions[symbol]["tp_order_id"] = algo.get("tp")
                    if not algo.get("sl"):
                        logger.critical(
                            f"{symbol}: SL koruma emri borsaya yerlestirilemedi; "
                            f"pozisyon korumasiz (sonraki taramada tamir edilir)"
                        )
                        if self.telegram:
                            await self.telegram.send(
                                f"ATOS X UYARI: {symbol} pozisyonu acik ama SL koruma "
                                f"emri yerlestirilemedi! Pozisyon korumasiz, sonraki "
                                f"taramada tamir edilecek."
                            )
                logger.success(f"Pozisyon acildi: {symbol} {side} {qty:.4f} @ {price}")
                self._persist_risk_state()
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

    async def close_all(self, reason: str = "manual_close_all"):
        """Acik tum pozisyonlari canli fiyatla kapatir.

        `live_prices`'da fiyat yoksa borsa taramasindan alinir; fiyat
        bulunamayanlar atlanir ve kapali sembol listesi doner.
        """
        closed = []
        for symbol in list(self.active_positions.keys()):
            price = self.live_prices.get(symbol)
            if price is None:
                try:
                    prices = await self.binance.get_all_tickers()
                    price = prices.get(symbol)
                except Exception:
                    price = None
            if price is None:
                logger.warning(f"{symbol}: guncel fiyat bulunamadi, kapatma atlandi")
                continue
            await self.close_position(symbol, price, reason)
            if symbol not in self.active_positions:
                closed.append(symbol)
        return closed

    async def update_sl(self, symbol: str, new_sl: float) -> dict:
        """Acik pozisyonun stop-loss'unu manuel gunceller (Telegram `/sl`).

        Borsadaki eski SL algo emri iptal edilir ve yeni SL yerlesir; TP
        korunur. Yon hatasi onlenir: BUY'da SL giris fiyatinin altinda,
        SELL'de ustunde olmali. Manuel muhalefet sonrasi trailing/breakeven
        bayraklari sifirlanir (DB'ye de yazilir).
        """
        pos = self.active_positions.get(symbol)
        if not pos:
            return {"ok": False, "error": "position_not_found"}
        new_sl = float(new_sl)
        entry = float(pos["entry_price"])
        side = pos["side"]
        if side == "BUY" and new_sl >= entry:
            return {"ok": False, "error": "sl_above_entry"}
        if side == "SELL" and new_sl <= entry:
            return {"ok": False, "error": "sl_below_entry"}
        old_sl = pos.get("sl")
        pos["sl"] = new_sl
        pos["trailing"] = False
        pos["breakeven"] = False
        try:
            self.db.update_trade_protection(symbol, trailing=False, breakeven=False)
        except Exception as e:
            logger.warning(f"{symbol}: koruma bayragi sifirlanamadi: {e}")
        self._log_risk_event(
            "manual_sl_update", f"{symbol}: SL {old_sl} -> {new_sl} (manuel)"
        )
        if not self.paper and pos.get("sl_order_id"):
            try:
                await self.binance.cancel_algo_order(symbol, pos["sl_order_id"])
                algo = await self.binance.set_tp_sl(
                    symbol, "LONG" if side == "BUY" else "SHORT", new_sl, 0.0
                )
                if not algo.get("sl"):
                    raise RuntimeError("set_tp_sl SL id dondurmedi")
                pos["sl_order_id"] = algo["sl"]
                logger.info(f"{symbol}: manuel SL borsaya yerleştirildi")
            except Exception as e:
                logger.error(f"{symbol}: manuel SL guncelleme hatasi: {e}")
                pos["sl"] = old_sl
                try:
                    await self.binance.set_tp_sl(
                        symbol, "LONG" if side == "BUY" else "SHORT", old_sl, 0.0
                    )
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error": "exchange_sl_update_failed",
                    "symbol": symbol,
                }
        logger.info(f"{symbol}: SL manuel olarak {old_sl} -> {new_sl}")
        return {"ok": True, "symbol": symbol, "old_sl": old_sl, "new_sl": new_sl}

    async def update_tp(self, symbol: str, new_tp: float) -> dict:
        """Acik pozisyonun take-profit'ini manuel gunceller (Telegram `/tp`).

        Borsadaki eski TP algo emri iptal edilir ve yeni TP yerlesir; SL
        korunur. Yon hatasi onlenir: BUY'da TP giris fiyatinin ustunde,
        SELL'de altinda olmali.
        """
        pos = self.active_positions.get(symbol)
        if not pos:
            return {"ok": False, "error": "position_not_found"}
        new_tp = float(new_tp)
        entry = float(pos["entry_price"])
        side = pos["side"]
        if side == "BUY" and new_tp <= entry:
            return {"ok": False, "error": "tp_below_entry"}
        if side == "SELL" and new_tp >= entry:
            return {"ok": False, "error": "tp_above_entry"}
        old_tp = pos.get("tp")
        pos["tp"] = new_tp
        self._log_risk_event(
            "manual_tp_update", f"{symbol}: TP {old_tp} -> {new_tp} (manuel)"
        )
        if not self.paper and pos.get("tp_order_id"):
            try:
                await self.binance.cancel_algo_order(symbol, pos["tp_order_id"])
                algo = await self.binance.set_tp_sl(
                    symbol, "LONG" if side == "BUY" else "SHORT", 0.0, new_tp
                )
                if not algo.get("tp"):
                    raise RuntimeError("set_tp_sl TP id dondurmedi")
                pos["tp_order_id"] = algo["tp"]
                logger.info(f"{symbol}: manuel TP borsaya yerleştirildi")
            except Exception as e:
                logger.error(f"{symbol}: manuel TP guncelleme hatasi: {e}")
                pos["tp"] = old_tp
                try:
                    await self.binance.set_tp_sl(
                        symbol, "LONG" if side == "BUY" else "SHORT", 0.0, old_tp
                    )
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error": "exchange_tp_update_failed",
                    "symbol": symbol,
                }
        logger.info(f"{symbol}: TP manuel olarak {old_tp} -> {new_tp}")
        return {"ok": True, "symbol": symbol, "old_tp": old_tp, "new_tp": new_tp}

    async def _record_closed_position(
        self, symbol: str, pos: dict, exit_price: float, reason: str
    ):
        """Kapanan pozisyonun PnL hesabi, DB kaydi ve bildirimini yapar."""
        pnl = (
            (exit_price - pos["entry_price"]) * pos["quantity"]
            if pos["side"] == "BUY"
            else (pos["entry_price"] - exit_price) * pos["quantity"]
        )
        exit_fee = exit_price * pos["quantity"] * self.engine.fee_rate
        net = pnl - exit_fee - pos.get("entry_fee", 0.0)
        self.equity += pnl - exit_fee
        self.db.close_trade_by_symbol(symbol, exit_price, net, reason)
        self.trade_history.append(
            {
                "symbol": symbol,
                "side": pos["side"],
                "entry": pos["entry_price"],
                "exit": exit_price,
                "qty": pos["quantity"],
                "pnl": net,
                "reason": reason,
                "trailing": bool(pos.get("trailing")),
                "breakeven": bool(pos.get("breakeven")),
                "time": utc_now().isoformat(),
            }
        )
        if self.telegram:
            await self.telegram.send_trade(
                symbol, pos["side"], exit_price, pos["quantity"], reason
            )
        await self._update_consecutive_losses()
        await self._update_daily_pnl(net)
        self._persist_risk_state()
        logger.success(f"Pozisyon kapatildi: {symbol} PnL: {net:.2f}")

    async def _sync_balance(self):
        """Canli borsadan gercek USDT dengesi ile ic equity'yi hizalar.

        `balance + unrealized` (margin balance) gercek pozisyon PnL'ini yansitir;
        peak_equity/drawdown buna gore guncellenir ve kalici durum yazilir.
        Borsa yontemi yoksa (test) ya da denge gecersizse sessizce atlanir.
        """
        fn = getattr(self.binance, "get_account_balance", None)
        if fn is None:
            return
        try:
            bal = await fn()
            if not bal or bal.get("balance") is None:
                return
            total = float(bal["balance"]) + float(bal.get("unrealized", 0.0))
            if total <= 0:
                return
            self.live_balance = bal
            self.equity = total
            if self.equity > self.peak_equity:
                self.peak_equity = self.equity
            if self.peak_equity > 0:
                self.drawdown_pct = round(
                    (self.peak_equity - self.equity) / self.peak_equity * 100.0, 2
                )
            self._persist_risk_state()
            logger.info(f"Bakiye senkronlandi: equity ${self.equity:.2f}")
        except Exception as e:
            logger.warning(f"Bakiye senkronu atlandi: {e}")

    @staticmethod
    def _filter_banned(candidates, settings=None):
        """`banned_symbols` listesindeki sembolleri tarama adaylarindan cikarir."""
        s = settings if settings is not None else strat_settings.get_settings()
        banned = {str(x).upper() for x in s.get("banned_symbols", [])}
        return [x for x in candidates if str(x).upper() not in banned]

    def _entry_age_days(self, t: dict) -> float:
        """DB kaydinin giris yasini gun cinsinden doner (entry_ts oncekli)."""
        raw = t.get("entry_ts") or t.get("entry_time")
        if not raw:
            return 0.0
        if isinstance(raw, (int, float)):
            try:
                dt = datetime.fromtimestamp(float(raw), tz=timezone.utc).replace(
                    tzinfo=None
                )
            except (ValueError, OSError):
                return 0.0
        else:
            try:
                dt = datetime.fromisoformat(str(raw))
            except ValueError:
                return 0.0
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return max(0.0, (utc_now() - dt).total_seconds() / 86400.0)

    def _restore_paper_positions(self):
        """Paper modunda restart sonrasi acik pozisyonlari DB'den geri yukler.

        Borsa emri yoktur (paper); SL/TP burada saklanmaz — ilk TTP manage /
        check dongusunde strateji `entry_ts`'ten itibaren yeniden hesaplar.
        `restore_age_limit` gununden eski kayitlar `_stale_restore_queue`'ya
        eklenir ve `_close_stale_restores` ile restart akisinda kapatilir
        (birikmis, yonetimsiz kayiplarin tek gune yuklenen gecis sokunu onler).
        """
        if not self.paper:
            return
        try:
            open_trades = self.db.list_open_trades()
        except Exception as e:
            logger.warning(f"Paper pozisyon restore hatasi: {e}")
            return
        age_limit = float(strat_settings.get_settings().get("restore_age_limit", 7.0))
        restored = 0
        for t in open_trades:
            symbol = t["symbol"]
            if symbol in self.active_positions:
                continue
            entry_time = (
                t.get("entry_time") or t.get("entry_ts") or utc_now().isoformat()
            )
            self.active_positions[symbol] = {
                "side": "BUY" if t["side"] == "BUY" else "SELL",
                "entry_price": t["entry_price"],
                "quantity": t["quantity"],
                "sl": None,
                "tp": None,
                "entry_fee": t["entry_price"] * t["quantity"] * self.engine.fee_rate,
                "open_time": entry_time,
                "entry_ts": str(t["entry_ts"]) if t["entry_ts"] else None,
                "ttp_tp_hit": t["ttp_tp_hit"],
            }
            if age_limit > 0 and self._entry_age_days(t) > age_limit:
                self._stale_restore_queue.append(symbol)
            restored += 1
        if restored:
            logger.info(
                f"Paper restart restore: {restored} pozisyon DB'den geri yuklendi"
            )
        if self._stale_restore_queue:
            logger.warning(
                f"Restore yas politikasi: {len(self._stale_restore_queue)} eski kayit "
                f"(>{age_limit:g} gun) restart akisinda kapatilacak"
            )

    async def _close_stale_restores(self):
        """Restore'da yas politikasina takilan kayitlari kapatir.

        Fiyat `live_prices`'tan alinir; yoksa giris fiyatiyla kapanir (PnL ~0).
        Kapanis `restore_stale_close` nedeniyle DB'ye islenir (day_pnl dahil).
        """
        stale, self._stale_restore_queue = self._stale_restore_queue, []
        for symbol in stale:
            pos = self.active_positions.get(symbol)
            if not pos:
                continue
            price = self.live_prices.get(symbol) or float(pos["entry_price"])
            await self.close_position(symbol, price, "restore_stale_close")

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
            await self._sync_balance()
            positions = await self.binance.get_open_positions()
            algos = await self.binance.get_open_algo_orders()
            algo_map = {}
            for a in algos or []:
                sym = a.get("symbol")
                entry = algo_map.setdefault(
                    sym, {"sl": None, "sl_id": None, "tp": None, "tp_id": None}
                )
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
                    info = algo_map.get(symbol, {})
                    ttp = strat_settings.get_settings().get("active_strategy") == "ttp"
                    missing = []
                    if pos.get("sl") and info.get("sl_id") is None:
                        missing.append("SL")
                    if not ttp and pos.get("tp") and info.get("tp_id") is None:
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
                # SAHIPLIK: DB'de OPEN sistem kaydi yoksa pozisyon kullaniciya
                # aittir (manuel acilis) — yonetim disi birakilir, ASLA kapatilmaz.
                db_opened = self.db.get_open_trade_entry_time(symbol)
                if db_opened is None:
                    self._log_risk_event(
                        "foreign_position",
                        f"{symbol} borsada acik ama sistem kaydi yok; "
                        f"kullanici pozisyonu sayildi, yonetilmiyor",
                        side="BUY" if float(p.get("positionAmt", 0)) > 0 else "SELL",
                    )
                    logger.warning(
                        f"{symbol}: borsada acik ama sistem kaydi yok - "
                        f"kullanici pozisyonu sayildi, yonetim disi"
                    )
                    if self.telegram:
                        await self.telegram.send(
                            f"ATOS X BILGI: {symbol} borsada acik ama sistem "
                            f"tarafindan acilmamis - yonetilmiyor, dokunulmuyor."
                        )
                    continue
                info = algo_map.get(symbol, {})
                ttp = strat_settings.get_settings().get("active_strategy") == "ttp"
                if info.get("sl_id") is None and (not ttp or info.get("tp_id") is None):
                    logger.warning(
                        f"{symbol}: borsada pozisyon var ama {'SL' if ttp else 'SL/TP'} emri yok; "
                        f"takip disi birakildi (acik kaldigindan emin olun)"
                    )
                    if self.telegram:
                        await self.telegram.send(
                            f"ATOS X UYARI: {symbol} borsada acik ama SL/TP emri yok! "
                            f"Pozisyon korumasiz, manuel müdahale gerekebilir."
                        )
                    continue
                amt = float(p["positionAmt"])
                db_trailing, db_breakeven = self.db.get_open_trade_protection(symbol)
                db_entry_ts, db_ttp_tp_hit = self.db.get_open_trade_ttp_state(symbol)
                restored_open = utc_now().isoformat()
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
                    "entry_ts": db_entry_ts,
                    "ttp_tp_hit": bool(db_ttp_tp_hit),
                    "restored": True,
                    "trailing": bool(db_trailing),
                    "breakeven": bool(db_breakeven),
                }
                restored += 1
            if restored:
                logger.info(f"Borsadan {restored} pozisyon geri yuklendi")
        except Exception as e:
            logger.error(f"Pozisyon geri yukleme hatasi: {e}")

    def _count_consecutive_losses(self) -> int:
        """`trade_history` uzerinden guncel ardısık zarar sayisini sayar."""
        streak = 0
        for t in reversed(self.trade_history):
            if t.get("pnl", 0) < 0:
                streak += 1
            else:
                break
        return streak

    async def _update_consecutive_losses(self):
        """Ardısık zarar sayacini gunceller; esik asilinca girisleri durdurur.

        Zararlar `trade_history` uzerinden geriye dogru sayilir; kar ya da
        basabasa (pnl >= 0) seriyi kirmaya yeter. Esik asildiginda `loss_halted`
        aktif olur, bir kar sonrasi otomatik serbest birakilir.
        """
        streak = self._count_consecutive_losses()
        self.consecutive_losses = streak
        self._persist_risk_state()
        if self.max_consecutive_losses <= 0:
            return
        if streak >= self.max_consecutive_losses and not self.loss_halted:
            self.loss_halted = True
            self._log_risk_event(
                "loss_streak_halt", f"{streak} ardısık zarar - yeni girisler durduruldu"
            )
            logger.warning(
                f"{streak} ardısık zarar (esik {self.max_consecutive_losses}) "
                f"- yeni girisler durduruldu"
            )
            if self.telegram:
                await self.telegram.send(
                    f"ATOS X UYARI: {streak} ardısık zarar - yeni girisler "
                    f"durduruldu. Bir sonraki kar seriyi acar."
                )
        elif self.loss_halted and streak < self.max_consecutive_losses:
            self.loss_halted = False
            self._log_risk_event(
                "loss_streak_clear", "Kar sonrasi ardısık zarar korumasi serbest"
            )
            logger.info("Kar sonrasi ardısık zarar korumasi kaldirildi")
            if self.telegram:
                await self.telegram.send(
                    "ATOS X: Kar sonrasi ardısık zarar korumasi kaldirildi "
                    "- yeni girisler serbest."
                )
        self._persist_risk_state()

    def _rollover_day(self):
        """Gun degisti ise gunluk PnL sayacini sifirlar ve halt'i kaldirir."""
        today = utc_now().date().isoformat()
        if self.day_start_date == today:
            return
        self.day_start_date = today
        self.day_pnl = 0.0
        if self.daily_loss_halted:
            self.daily_loss_halted = False
            self._log_risk_event(
                "daily_loss_clear", "Yeni gun - gunluk zarar korumasi serbest"
            )
            logger.info("Yeni gun - gunluk zarar korumasi kaldirildi")
        self._persist_risk_state()

    async def _update_daily_pnl(self, net: float):
        """Gunluk toplam PnL'i isler; esik asilinca girisleri durdurur.

        `max_daily_loss_pct`, `_record_closed_position` anindaki equity'nin
        %'si olarak gunluk net zarar siniri belirler. Asildiginda bir kez
        uyarir ve `daily_loss_halted` bayragini kaldirir; yeni gun
        `_rollover_day` ile otomatik serbest birakir.
        """
        self._rollover_day()
        self.day_pnl += net
        self._persist_risk_state()
        if self.max_daily_loss_pct <= 0:
            return
        limit = self.equity * self.max_daily_loss_pct / 100.0
        if self.day_pnl <= -limit and not self.daily_loss_halted:
            self.daily_loss_halted = True
            self._log_risk_event(
                "daily_loss_halt",
                f"Gunluk zarar {self.day_pnl:.2f} esigi asti "
                f"(-%{self.max_daily_loss_pct:.1f}) - girisler durduruldu",
            )
            logger.warning(
                f"Gunluk zarar {self.day_pnl:.2f} esigi asti "
                f"(-%{self.max_daily_loss_pct:.1f}) - girisler durduruldu"
            )
            if self.telegram:
                await self.telegram.send(
                    f"ATOS X UYARI: Gunluk zarar {self.day_pnl:.2f} USDT - "
                    f"gunluk sinir (-%{self.max_daily_loss_pct:.1f}) asildi, "
                    f"yeni girisler durduruldu. Yeni gun korumayi acar."
                )
        self._persist_risk_state()

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
        self._persist_risk_state()
        threshold = self.max_drawdown_pct
        if threshold <= 0:
            return
        if dd >= threshold and not self.risk_halted:
            self.risk_halted = True
            self._log_risk_event(
                "drawdown_halt", f"Drawdown %{dd:.1f} (%{threshold:.0f} esigi) asildi"
            )
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
            self._log_risk_event(
                "drawdown_clear", f"Drawdown %{dd:.1f}'e geri geldi, girisler serbest"
            )
            logger.info(f"Drawdown %{dd:.1f}'e geri geldi - yeni girisler serbest")
            if self.telegram:
                await self.telegram.send(
                    f"ATOS X: Drawdown %{dd:.1f}'e geri geldi - yeni girisler serbest."
                )
        self._persist_risk_state()

    async def _check_equity_floor(self):
        """Equity mutlak taban sinirin altina duserse girisleri durdurur.

        `min_equity` (USDT) altina dusuldugunde bir kez uyarir ve
        `equity_halted` bayragini kaldirir; equity taban sinirin uzerine
        dondugunde otomatik serbest birakir.
        """
        if self.min_equity <= 0:
            return
        if self.equity < self.min_equity and not self.equity_halted:
            self.equity_halted = True
            self._log_risk_event(
                "equity_floor",
                f"Equity {self.equity:.2f} taban sinirin "
                f"({self.min_equity:.2f}) altina dustu",
            )
            logger.warning(
                f"Equity {self.equity:.2f} taban sinirin ({self.min_equity:.2f}) "
                f"altina dustu - yeni girisler durduruldu"
            )
            if self.telegram:
                await self.telegram.send(
                    f"ATOS X UYARI: Equity ${self.equity:.2f} taban sinirin "
                    f"(${self.min_equity:.2f}) altina dustu - yeni girisler durduruldu."
                )
        elif self.equity_halted and self.equity >= self.min_equity:
            self.equity_halted = False
            self._log_risk_event(
                "equity_clear", "Equity taban sinirin uzerine dondu, girisler serbest"
            )
            logger.info("Equity taban sinirin uzerine dondu - yeni girisler serbest")
            if self.telegram:
                await self.telegram.send(
                    f"ATOS X: Equity ${self.equity:.2f} taban sinirin uzerine dondu "
                    "- yeni girisler serbest."
                )
        self._persist_risk_state()

    async def _notify_startup_state(self):
        """Baslangicta aktif risk esiklerini ve mevcut engelleri bildirir."""
        if not self.telegram:
            return
        halted = "AKTIF" if self.risk_halted else "yok"
        age = (
            f"{self.max_position_age_hours:.0f} saat"
            if self.max_position_age_hours > 0
            else "devre disi"
        )
        trail = "devre disi"
        if self.trailing_activate_pct > 0 and self.trailing_sl_pct > 0:
            trail = f"kar %{self.trailing_activate_pct:.0f}+, SL %{self.trailing_sl_pct:.1f} geri"
        be = (
            f"%{self.breakeven_activate_pct:.0f}"
            if self.breakeven_activate_pct > 0
            else "devre disi"
        )
        dl = (
            f"%{self.max_daily_loss_pct:.0f}"
            if self.max_daily_loss_pct > 0
            else "devre disi"
        )
        eq_floor = f"${self.min_equity:.0f}" if self.min_equity > 0 else "devre disi"
        msg = (
            f"ATOS X: Motor baslatildi\n"
            f"Mod: {self.trading_mode.upper()} | "
            f"Yeni giris: {'KAPALI' if self.halt_entries else 'acik'}\n"
            f"Risk - max pos %{self.max_position_pct:.0f}, max side %{self.max_side_pct:.0f}, "
            f"max drawdown %{self.max_drawdown_pct:.0f} ({halted}), "
            f"max ardisik zarar {self.max_consecutive_losses}, "
            f"gunluk zarar {dl}, breakeven {be}, equity taban {eq_floor}\n"
            f"Max pozisyon yasi: {age} | Trailing: {trail}"
        )
        blocks = sorted(self._conc_blocks)
        if blocks:
            msg += f"\nEngeller: {', '.join(blocks)}"
        else:
            msg += "\nEngel yok"
        if self.risk_events:
            last = self.risk_events[-1]
            msg += f"\nSon risk olayi: {last['type']} ({last['time'][:16].replace('T', ' ')})"
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
        return last or pos.get("tp") or pos.get("sl") or pos[
            "entry_price"
        ], "exchange_closed"

    async def check_positions(self, prices):
        if strat_settings.get_settings().get("active_strategy") == "ttp":
            await self._ttp_manage_positions(prices)
            return
        now = utc_now()
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
            if self.breakeven_activate_pct > 0:
                await self._check_breakeven(symbol, pos, side, current_price)
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

    async def _ttp_manage_positions(self, prices):
        """TTPTSL pozisyon yonetimi: stratejinin durum makinesine gore SL/TP.

        Her taramada pozisyon sembolunun guncel 4h kline'lari cekilir ve
        `TtpTsl.manage` giristen itibaren durum makinesini calistirir; cikis
        direktifine (sl / tp_partial / trail_tp / reversal) gore pozisyon
        kapatilir veya kismi TP uygulanir. Pozisyon aktifken SL/TP stratejinin
        tasidigi degerlerle tazelenir (genel v23 trailing/breakeven uygulanmaz).
        """
        from app.strategy.ttp import TtpTsl

        bot = get_strategy(strat_settings.get_settings())
        if not isinstance(bot, TtpTsl):
            return
        for symbol, pos in list(self.active_positions.items()):
            try:
                current_price = self.live_prices.get(symbol) or prices.get(symbol)
                klines = await self.binance.get_klines(symbol, "4h", 400)
                if klines is None or len(klines) < 30:
                    continue
                res = bot.manage(
                    klines,
                    pos.get("entry_ts") or pos.get("open_time"),
                    float(pos["entry_price"]),
                    pos["side"],
                    float(pos["quantity"]),
                    tp_already_hit=bool(pos.get("ttp_tp_hit")),
                )
            except Exception as e:
                logger.error(f"{symbol}: TTPTSL pozisyon yonetimi hatasi: {e}")
                continue

            ex = res.get("exit") or ""
            ep = res.get("exit_price")
            qfrac = float(res.get("exit_qty_pct") or 0.0)
            if not res.get("active") and not ex:
                ex, qfrac = "reversal", 1.0
            exit_px = (
                ep
                if ep is not None
                else (
                    current_price
                    if current_price is not None
                    else float(pos.get("entry_price") or 0)
                )
            )

            if ex == "sl":
                await self.close_position(symbol, float(exit_px), "stop_loss")
            elif ex == "tp_partial":
                if qfrac >= 1.0 - 1e-9:
                    await self.close_position(symbol, float(exit_px), "take_profit")
                elif ep is not None:
                    await self._close_portion(
                        symbol, float(ep), float(pos["quantity"]) * qfrac, "take_profit"
                    )
            elif ex == "trail_tp":
                await self.close_position(symbol, float(exit_px), "trail_tp")
            elif ex == "reversal":
                await self.close_position(symbol, float(exit_px), "reversal")
            else:
                new_sl = res.get("sl")
                new_tp = res.get("tp")
                old_sl = float(pos.get("sl") or 0)
                changed = False
                move_sl = False
                if new_sl is not None and float(new_sl) > 0 and float(new_sl) != old_sl:
                    changed = True
                    move_sl = bool(not self.paper and pos.get("sl_order_id"))
                    if not move_sl:
                        pos["sl"] = float(new_sl)
                if (
                    new_tp is not None
                    and float(new_tp) > 0
                    and float(new_tp) != float(pos.get("tp") or 0)
                ):
                    pos["tp"] = float(new_tp)
                    changed = True
                if changed:
                    logger.info(
                        f"{symbol}: TTPTSL SL/TP guncellendi -> sl {pos['sl']:.6f}, tp {pos['tp']:.6f}"
                    )
                if move_sl:
                    try:
                        await self.binance.cancel_algo_order(symbol, pos["sl_order_id"])
                        algo = await self.binance.set_tp_sl(
                            symbol,
                            "LONG" if pos["side"] == "BUY" else "SHORT",
                            float(new_sl),
                            0.0,
                        )
                        if not algo.get("sl"):
                            raise RuntimeError("set_tp_sl SL id dondurmedi")
                        pos["sl"] = float(new_sl)
                        pos["sl_order_id"] = algo["sl"]
                        logger.info(
                            f"{symbol}: TTPTSL SL borsaya tasindi -> sl {pos['sl']:.6f}"
                        )
                    except Exception as e:
                        logger.error(
                            f"{symbol}: TTPTSL SL borsa guncelleme hatasi: {e}"
                        )
                        last_alert = float(pos.get("sl_alert_ts") or 0)
                        if self.telegram and time.time() - last_alert > 600:
                            pos["sl_alert_ts"] = time.time()
                            await self.telegram.send(
                                f"ATOS X UYARI: {symbol} TTPTSL trailing SL borsaya "
                                f"tasinamadi (mevcut {old_sl:.6f}, hedef {float(new_sl):.6f}). "
                                f"Koruma eski seviyede; sonraki taramada yeniden denenir."
                            )
                if (
                    current_price
                    and pos.get("sl")
                    and pos["side"] == "BUY"
                    and current_price <= float(pos["sl"])
                ):
                    await self.close_position(symbol, float(pos["sl"]), "stop_loss")
                elif (
                    current_price
                    and pos.get("sl")
                    and pos["side"] == "SELL"
                    and current_price >= float(pos["sl"])
                ):
                    await self.close_position(symbol, float(pos["sl"]), "stop_loss")
                elif (
                    current_price
                    and pos.get("tp")
                    and pos["side"] == "BUY"
                    and current_price >= float(pos["tp"])
                ):
                    await self.close_position(symbol, float(pos["tp"]), "take_profit")
                elif (
                    current_price
                    and pos.get("tp")
                    and pos["side"] == "SELL"
                    and current_price <= float(pos["tp"])
                ):
                    await self.close_position(symbol, float(pos["tp"]), "take_profit")

    async def _close_portion(
        self, symbol: str, exit_price: float, exit_qty: float, reason: str
    ):
        """Pozisyonun `exit_qty` kadarlik kismini kapatir; kalan miktar durur."""
        pos = self.active_positions.get(symbol)
        if not pos or exit_qty <= 0:
            return
        full_qty = float(pos["quantity"])
        exit_qty = min(exit_qty, full_qty)
        if exit_qty >= full_qty - 1e-12:
            await self.close_position(symbol, exit_price, reason)
            return
        if not self.paper:
            try:
                close_side = "SELL" if pos["side"] == "BUY" else "BUY"
                await self.binance.place_market_order(
                    symbol, close_side, exit_qty, reduce_only=True
                )
            except Exception as e:
                logger.error(f"{symbol}: kismi kapanis emri gonderilemedi: {e}")
                return
        pnl = (
            (exit_price - float(pos["entry_price"])) * exit_qty
            if pos["side"] == "BUY"
            else (float(pos["entry_price"]) - exit_price) * exit_qty
        )
        exit_fee = exit_price * exit_qty * self.engine.fee_rate
        entry_fee_part = float(pos.get("entry_fee", 0.0)) * (exit_qty / full_qty)
        net = pnl - exit_fee - entry_fee_part
        self.equity += pnl - exit_fee
        pos["quantity"] = full_qty - exit_qty
        pos["entry_fee"] = float(pos.get("entry_fee", 0.0)) - entry_fee_part
        pos["ttp_tp_hit"] = True
        self.trade_history.append(
            {
                "symbol": symbol,
                "side": pos["side"],
                "entry": pos["entry_price"],
                "exit": exit_price,
                "qty": exit_qty,
                "pnl": net,
                "reason": reason,
                "trailing": bool(pos.get("trailing")),
                "breakeven": bool(pos.get("breakeven")),
                "time": utc_now().isoformat(),
            }
        )
        try:
            self.db.reduce_trade_quantity(symbol, pos["quantity"])
            self.db.update_trade_protection(symbol, ttp_tp_hit=True)
        except Exception as e:
            logger.warning(f"{symbol}: kismi kapanis DB guncellenemedi: {e}")
        await self._update_consecutive_losses()
        await self._update_daily_pnl(net)
        self._persist_risk_state()
        if self.telegram:
            await self.telegram.send_trade(
                symbol,
                pos["side"],
                exit_price,
                exit_qty,
                f"{reason} (kismi, kalan {pos['quantity']:.4f})",
            )
        logger.success(
            f"{symbol}: kismi {reason} {exit_qty:.4f} @ {exit_price} (kalan {pos['quantity']:.4f})"
        )

    async def _check_breakeven(self, symbol: str, pos: dict, side: str, price: float):
        """Kar esigini asan pozisyonun SL'sini giris fiyatina tasir (zararsiz).

        `breakeven_activate_pct` kari asilinca SL giris fiyatina cekilir;
        fiyat geri donerse pozisyon zarar etmeden kapanir. SL zaten daha
        iyi (trailing) ise dokunulmaz.
        """
        entry = float(pos["entry_price"])
        if side == "BUY":
            profit_pct = (price - entry) / entry * 100.0
            cur_sl = pos.get("sl") or 0.0
            need_move = cur_sl < entry
        else:
            profit_pct = (entry - price) / entry * 100.0
            cur_sl = pos.get("sl") or float("inf")
            need_move = cur_sl > entry
        if profit_pct < self.breakeven_activate_pct or not need_move:
            return
        if self.paper or not pos.get("sl_order_id"):
            pos["sl"] = entry
            pos["breakeven"] = True
            self.db.update_trade_protection(symbol, breakeven=True)
            self._log_risk_event(
                "breakeven_move",
                f"{symbol} SL giris fiyatina tasindi ({_fmt_px(entry)})",
            )
            logger.info(
                f"{symbol}: SL giris fiyatina tasindi -> {_fmt_px(entry)} (kar %{profit_pct:.1f})"
            )
            return
        try:
            await self.binance.cancel_algo_order(symbol, pos["sl_order_id"])
            algo = await self.binance.set_tp_sl(
                symbol, "LONG" if side == "BUY" else "SHORT", entry, 0.0
            )
            if not algo.get("sl"):
                raise RuntimeError("set_tp_sl SL id dondurmedi")
            pos["sl"] = entry
            pos["breakeven"] = True
            pos["sl_order_id"] = algo["sl"]
            self.db.update_trade_protection(symbol, breakeven=True)
            self._log_risk_event(
                "breakeven_move",
                f"{symbol} SL giris fiyatina tasindi ({_fmt_px(entry)})",
            )
            logger.info(
                f"{symbol}: SL giris fiyatina tasindi -> {_fmt_px(entry)} (kar %{profit_pct:.1f})"
            )
        except Exception as e:
            logger.error(f"{symbol}: breakeven SL guncelleme hatasi: {e}")
            last_alert = float(pos.get("sl_alert_ts") or 0)
            if self.telegram and time.time() - last_alert > 600:
                pos["sl_alert_ts"] = time.time()
                await self.telegram.send(
                    f"ATOS X UYARI: {symbol} breakeven SL borsaya tasinamadi "
                    f"(mevcut {pos.get('sl')}, hedef {entry:.2f}). Koruma eski "
                    f"seviyede; sonraki taramada yeniden denenir."
                )

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
            move_pct = (new_sl - cur_sl) / (cur_sl or 1.0) * 100.0 if better else 0.0
        else:
            profit_pct = (entry - price) / entry * 100.0
            new_sl = price * (1.0 + self.trailing_sl_pct / 100.0)
            cur_sl = pos.get("sl") or float("inf")
            better = new_sl < cur_sl
            move_pct = (cur_sl - new_sl) / (cur_sl or 1.0) * 100.0 if better else 0.0
        if profit_pct < self.trailing_activate_pct or not better:
            return
        if self.trailing_min_move_pct > 0 and move_pct < self.trailing_min_move_pct:
            return
        was_trailing = bool(pos.get("trailing"))
        if self.paper or not pos.get("sl_order_id"):
            pos["sl"] = new_sl
            pos["trailing"] = True
            self.db.update_trade_protection(symbol, trailing=True)
            if not was_trailing:
                self._log_risk_event(
                    "trailing_activate",
                    f"{symbol} SL takibi: kar %{profit_pct:.1f}, SL {_fmt_px(new_sl)}",
                )
            else:
                self._log_risk_event(
                    "trailing_move",
                    f"{symbol} SL {_fmt_px(cur_sl)} -> {_fmt_px(new_sl)} (kar %{profit_pct:.1f})",
                )
            logger.info(
                f"{symbol}: SL takibe girdi -> {_fmt_px(new_sl)} (kar %{profit_pct:.1f})"
            )
            return
        try:
            await self.binance.cancel_algo_order(symbol, pos["sl_order_id"])
            algo = await self.binance.set_tp_sl(
                symbol, "LONG" if side == "BUY" else "SHORT", new_sl, 0.0
            )
            if not algo.get("sl"):
                raise RuntimeError("set_tp_sl SL id dondurmedi")
            pos["sl"] = new_sl
            pos["trailing"] = True
            pos["sl_order_id"] = algo["sl"]
            self.db.update_trade_protection(symbol, trailing=True)
            if not was_trailing:
                self._log_risk_event(
                    "trailing_activate",
                    f"{symbol} SL takibi: kar %{profit_pct:.1f}, SL {_fmt_px(new_sl)}",
                )
            else:
                self._log_risk_event(
                    "trailing_move",
                    f"{symbol} SL {_fmt_px(cur_sl)} -> {_fmt_px(new_sl)} (kar %{profit_pct:.1f})",
                )
            logger.info(
                f"{symbol}: trailing SL {_fmt_px(new_sl)} borsaya yerleştirildi"
            )
        except Exception as e:
            logger.error(f"{symbol}: trailing SL guncelleme hatasi: {e}")
            last_alert = float(pos.get("sl_alert_ts") or 0)
            if self.telegram and time.time() - last_alert > 600:
                pos["sl_alert_ts"] = time.time()
                await self.telegram.send(
                    f"ATOS X UYARI: {symbol} trailing SL borsaya tasinamadi "
                    f"(mevcut {pos.get('sl')}, hedef {new_sl:.2f}). Koruma eski "
                    f"seviyede; sonraki taramada yeniden denenir."
                )

    async def update_equity(self):
        now = time.monotonic()
        if now - self._last_perf < self.perf_interval:
            return
        self._last_perf = now
        closed = self.trade_history
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        win_rate = wins / len(closed) * 100 if closed else 0.0
        self.db.save_performance(
            self.equity, len(self.active_positions), len(self.trade_history), win_rate
        )
        self._persist_risk_state()

    async def stop(self):
        self.running = False
        self._log_risk_event(
            "system_stop", "Motor durduruldu - tum pozisyonlar kapatiliyor"
        )
        before = len(self.trade_history)
        for symbol in list(self.active_positions.keys()):
            price = self.live_prices.get(symbol)
            if not price:
                price = await self.binance.get_price(symbol)
            if not price:
                logger.warning(f"{symbol}: fiyat alinamadi, kapatma atlandi")
                continue
            await self.close_position(symbol, price, "system_stop")
        closed = self.trade_history[before:]
        if self.telegram:
            await self.telegram.send_stop_summary(closed)
        logger.info("Otomatik islem motoru durduruldu")

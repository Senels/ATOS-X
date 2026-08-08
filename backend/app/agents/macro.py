"""K2 Capiraz/Makro ajanlar: piyasa geneli veriler (Stooq, BTC.D, sektorler).

Tier 1-2: bu ajanlarin girdisi orchestrator tarafindan cache'li verilerden
(macro, klines_map, corr) hazirlanir; ajanlarin kendisi veri cekmez.
Sektör haritasi bilinen semboller icin kuresel bir taslak sunar (yeni
semboller ayri bolumde eslestirilir).
"""
from typing import Any, Dict, List, Optional

import pandas as pd

from app.agents.base import Agent, AgentResult

SECTORS: Dict[str, List[str]] = {
    "L1": ["SOLUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT", "SEIUSDT", "INJUSDT"],
    "L2": ["ARBUSDT", "OPUSDT", "MATICUSDT", "POLUSDT", "IMXUSDT"],
    "AI": ["FETUSDT", "RNDRUSDT", "TAOUSDT", "ARKMUSDT", "WLDUSDT", "AGIXUSDT", "OCEANUSDT"],
    "DeFi": ["UNIUSDT", "AAVEUSDT", "LDOUSDT", "MKRUSDT", "CRVUSDT", "COMPUSDT", "SNXUSDT"],
    "Meme": ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT", "1000PEPEUSDT"],
    "Infra": ["LINKUSDT", "GRTUSDT", "FILUSDT", "ARUSDT", "ICPUSDT", "HNTUSDT"],
    "GameFi": ["SANDUSDT", "MANAUSDT", "AXSUSDT", "GALAUSDT", "ENJUSDT"],
    "RWA": ["ONDOUSDT", "PENDLEUSDT", "TRUUSDT", "OMUSDT"],
    "Exchanges": ["BNBUSDT", "OKBUSDT", "CROUSDT", "LEVERUSDT"],
}


def sector_of(symbol: str) -> Optional[str]:
    for name, members in SECTORS.items():
        if symbol in members:
            return name
    return None


def _ret(df: pd.DataFrame, bars: int) -> Optional[float]:
    if df is None or len(df) <= bars:
        return None
    try:
        return float(df["close"].iloc[-1] / df["close"].iloc[-1 - bars] - 1)
    except (IndexError, TypeError):
        return None


class DxyDollarAgent(Agent):
    agent_id = "dxy_dollar"
    name = "DXY Dolar Endeksi"
    category = "macro"
    tier = 1
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        dxy = (ctx.macro or {}).get("dxy")
        if not dxy or dxy.get("chg5d_pct") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "DXY verisi yok", confidence=0.3)
        chg = dxy["chg5d_pct"]
        vote = "SELL" if chg > 0.3 else ("BUY" if chg < -0.3 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"DXY %{chg:.2f} (5g) — kripto ile ters korelasyon",
                           confidence=min(abs(chg) / 1.2, 0.8) if vote else 0.4, meta=dxy)


class MacroRiskAgent(Agent):
    agent_id = "macro_risk"
    name = "Makro Risk (VIX/SPX)"
    category = "macro"
    tier = 1
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        vix = (ctx.macro or {}).get("vix")
        spx = (ctx.macro or {}).get("spx")
        if not vix or vix.get("price") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "VIX verisi yok", confidence=0.3)
        v = vix["price"]
        spx_chg = (spx or {}).get("chg5d_pct", 0.0)
        vote = None
        if v < 18 and spx_chg > 0:
            vote = "BUY"
            reason = f"Risk-on (VIX {v:.1f} < 18, SPX %{spx_chg:.2f})"
        elif v > 30 or (v > 22 and spx_chg < -1.5):
            vote = "SELL"
            reason = f"Risk-off (VIX {v:.1f}, SPX %{spx_chg:.2f})"
        else:
            reason = f"Noktral makro (VIX {v:.1f}, SPX %{spx_chg:.2f})"
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=0.7 if vote else 0.4, meta={"vix": v, "spx_chg5d": spx_chg})


class BtcDominanceAgent(Agent):
    agent_id = "btc_dominance"
    name = "BTC Hakimiyeti"
    category = "macro"
    tier = 2
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        btc = ctx.klines_map.get("BTCUSDT")
        if btc is None or len(ctx.klines_map) < 10:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        btc_r = _ret(btc, 7)
        alt_ret = []
        for sym, df in list(ctx.klines_map.items())[:30]:
            if sym == "BTCUSDT":
                continue
            r = _ret(df, 7)
            if r is not None:
                alt_ret.append(r)
        if btc_r is None or not alt_ret:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        avg_alt = sum(alt_ret) / len(alt_ret)
        vote = "SELL" if btc_r - avg_alt > 0.02 else ("BUY" if avg_alt - btc_r > 0.02 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"BTC %{btc_r * 100:.1f} vs altcoin ort %{avg_alt * 100:.1f} (7g)",
                           confidence=min(abs(btc_r - avg_alt) * 8.0, 0.8) if vote else 0.4,
                           meta={"btc_7d": round(btc_r * 100, 2), "avg_alt_7d": round(avg_alt * 100, 2)})


class EthBtcRatioAgent(Agent):
    agent_id = "eth_btc_ratio"
    name = "ETH/BTC Oranı"
    category = "macro"
    tier = 2
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        eth = ctx.klines_map.get("ETHUSDT")
        btc = ctx.klines_map.get("BTCUSDT")
        if eth is None or btc is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        ratio = _ret(eth, 5)
        btc_r = _ret(btc, 5)
        if ratio is None or btc_r is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        rel = ratio - btc_r
        vote = "BUY" if rel > 0.01 else ("SELL" if rel < -0.01 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"ETH %{ratio * 100:.1f} vs BTC %{btc_r * 100:.1f} (5g) — altcoin mevsimi",
                           confidence=min(abs(rel) * 10.0, 0.8) if vote else 0.4,
                           meta={"eth_rel": round(rel * 100, 2)})


class SectorRotationAgent(Agent):
    agent_id = "sector_rotation"
    name = "Sektör Rotasyonu"
    category = "macro"
    tier = 2
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        sector = sector_of(ctx.symbol)
        if sector is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Sektor atanmamis", confidence=0.3)
        members = SECTORS[sector]
        sec_ret, all_ret = [], []
        for sym, df in ctx.klines_map.items():
            r = _ret(df, 5)
            if r is None:
                continue
            if sym in members:
                sec_ret.append(r)
            elif len(all_ret) < 60:
                all_ret.append(r)
        if not sec_ret or not all_ret:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        sec = sum(sec_ret) / len(sec_ret)
        allm = sum(all_ret) / len(all_ret)
        rel = sec - allm
        vote = "BUY" if rel > 0.01 else ("SELL" if rel < -0.01 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Sektor {sector}: %{sec * 100:.1f} vs piyasa %{allm * 100:.1f} (5g)",
                           confidence=min(abs(rel) * 10.0, 0.8) if vote else 0.4,
                           meta={"sector": sector, "rel_pct": round(rel * 100, 2)})


class AltcoinSeasonAgent(Agent):
    agent_id = "altcoin_season"
    name = "Altcoin Sezonu"
    category = "macro"
    tier = 2
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        btc = ctx.klines_map.get("BTCUSDT")
        if btc is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        btc_r = _ret(btc, 30)
        alts = []
        for sym, df in list(ctx.klines_map.items())[:30]:
            if sym == "BTCUSDT":
                continue
            r = _ret(df, 30)
            if r is not None:
                alts.append(r)
        if btc_r is None or not alts:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        win = sum(1 for r in alts if r > btc_r)
        pct = win / len(alts)
        vote = "BUY" if pct >= 0.6 else ("SELL" if pct <= 0.4 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Altcoinler BTC'yi gecen: %{pct * 100:.0f} (30g)",
                           confidence=min(abs(pct - 0.5) * 2.4, 0.8) if vote else 0.4,
                           meta={"season_pct": round(pct, 2)})


class MarketBreadthAgent(Agent):
    agent_id = "market_breadth"
    name = "Piyasa Genişliği"
    category = "macro"
    tier = 2
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        up = down = 0
        for sym, df in ctx.klines_map.items():
            r = _ret(df, 5)
            if r is None:
                continue
            if r > 0:
                up += 1
            else:
                down += 1
        total = up + down
        if total < 15:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        pct = up / total
        vote = "BUY" if pct >= 0.6 else ("SELL" if pct <= 0.4 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Yukselen sembol: %{pct * 100:.0f} ({up}/{total})",
                           confidence=min(abs(pct - 0.5) * 2.4, 0.8) if vote else 0.4,
                           meta={"breadth": round(pct, 3)})


class GlobalBetaAgent(Agent):
    agent_id = "global_beta"
    name = "Piyasa Betası"
    category = "macro"
    tier = 2
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        corr = ctx.corr or {}
        beta = corr.get("market_avg", {}).get(ctx.symbol)
        if beta is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Korelasyon verisi yok", confidence=0.3)
        breadth = None
        up = down = 0
        for sym, df in ctx.klines_map.items():
            r = _ret(df, 1)
            if r is None:
                continue
            if r > 0:
                up += 1
            else:
                down += 1
        if up + down >= 15:
            breadth = up / (up + down)
        if breadth is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Genislik verisi yok", confidence=0.3)
        market_dir = "BUY" if breadth > 0.5 else "SELL"
        if beta < 0.5:
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Dusuk beta {beta:.2f} — piyasadan bagimsiz", confidence=0.4,
                               meta={"beta": beta})
        conf = min(beta * 0.8, 0.75)
        return AgentResult(self.agent_id, market_dir, self.default_weight,
                           f"Beta {beta:.2f} — piyasa yonu {market_dir}",
                           confidence=conf, meta={"beta": beta, "breadth": round(breadth, 3)})


AGENT_CLASSES = [DxyDollarAgent, MacroRiskAgent, BtcDominanceAgent, EthBtcRatioAgent,
                 SectorRotationAgent, AltcoinSeasonAgent, MarketBreadthAgent, GlobalBetaAgent]

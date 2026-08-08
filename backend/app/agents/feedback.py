"""Ajan geri bildirim dongusu: oylari kalici kaydeder, bar bittikten sonra
sonuclari cozumler ve EWMA ile agirlik + otomatik devre disi yonetimi yapar.

- `record_votes`: her kapi calismasinda oy veren ajanlari `agent_votes`
  tablosuna yazar (oy verenler, cekimserler degil).
- `resolve_symbol`: sembolun bekleyen oylarini guncel fiyat ile cozumler;
  `hit` (yon dogru), `miss` (yanlis) veya `na` (fiyat yok).
- `update_weights`: ajan basina EWMA isabet orani (a=0.2); carpan
  `clamp(acc/0.5, 0.25, 1.5)`; `agent_min_samples` (20) uzerinde ve
  `agent_min_acc_enable` (0.40) altinda ise ajan otomatik devre disi kalir.
  Risk kategorisi egitimden muaftir (dokunulmaz). Agirlik guncellemesi
  `settings.update_settings` + `persist` ile kalici yazilir.
"""
import sqlite3
from typing import Any, Dict, List, Optional

from loguru import logger

VOTES_TABLE = """
CREATE TABLE IF NOT EXISTS agent_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    bar_ts TEXT,
    agent_id TEXT NOT NULL,
    category TEXT NOT NULL,
    vote TEXT NOT NULL,
    confidence REAL,
    weight REAL,
    price REAL,
    outcome TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
)
"""

DEFAULTS = {
    "agent_feedback_alpha": 0.2,
    "agent_min_samples": 20,
    "agent_min_acc_enable": 0.40,
    "agent_weight_min": 0.25,
    "agent_weight_max": 1.5,
    "agent_feedback_horizon_bars": 24,
}


def _params(settings: Optional[Dict[str, Any]]) -> Dict[str, float]:
    out = dict(DEFAULTS)
    block = (settings or {}).get("agents_council") or {}
    out.update({k: float(v) for k, v in block.items() if k in DEFAULTS})
    return out


def ensure_table(db) -> None:
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(VOTES_TABLE)


def record_votes(db, symbol: str, bar_ts: str, results: List[Any],
                 price: float = None) -> int:
    """Oy veren ajanlarin oylarini kaydeder; eklenen satir sayisi doner."""
    ensure_table(db)
    rows = [(symbol, bar_ts, r.agent_id, r.category, r.vote, r.confidence,
             r.weight, price)
            for r in results if r.vote in ("BUY", "SELL")]
    if not rows:
        return 0
    with sqlite3.connect(db.db_path) as conn:
        conn.executemany(
            "INSERT INTO agent_votes (symbol, bar_ts, agent_id, category, vote, "
            "confidence, weight, price) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def resolve_symbol(db, klines, symbol: str, resolution_bars: int = 24) -> Dict[str, int]:
    """Sembolun bekleyen oylarini bar-bazli cozumler: hit | miss | na.

    Oy barindan `resolution_bars` bar sonraki kapanis, oy anindaki fiyatla
    karsilastirilir (AI tahmin cozumlemesiyle ayni desen). Veri yetmiyorsa
    oy bekler; bar_ts bulunamazsa (sembol cikmis/eski) `na`.
    """
    ensure_table(db)
    counts = {"hit": 0, "miss": 0, "na": 0}
    if klines is None or len(klines) < 2:
        return counts
    try:
        idxs = list(klines.index.astype(str))
    except Exception:
        return counts
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute(
            "SELECT id, vote, price, bar_ts FROM agent_votes "
            "WHERE symbol = ? AND outcome = 'pending'", (symbol,)).fetchall()
        for vid, vote, price, bar_ts in rows:
            if not bar_ts:
                outcome = "na"
            else:
                try:
                    pos = idxs.index(bar_ts)
                except ValueError:
                    outcome = "na"
                else:
                    if pos + resolution_bars >= len(klines):
                        continue
                    p0 = float(price) if price else float(klines["close"].iloc[pos])
                    p1 = float(klines["close"].iloc[pos + resolution_bars])
                    if (vote == "BUY" and p1 > p0) or (vote == "SELL" and p1 < p0):
                        outcome = "hit"
                    else:
                        outcome = "miss"
            conn.execute(
                "UPDATE agent_votes SET outcome = ?, resolved_at = CURRENT_TIMESTAMP "
                "WHERE id = ?", (outcome, vid))
            counts[outcome] += 1
    return counts


def resolve_stale(db, days: int = 30) -> int:
    """Cok eski bekleyen oylari 'na' yapar; islenen satir sayisi doner."""
    ensure_table(db)
    with sqlite3.connect(db.db_path) as conn:
        cur = conn.execute(
            "UPDATE agent_votes SET outcome = 'na', resolved_at = CURRENT_TIMESTAMP "
            "WHERE outcome = 'pending' AND created_at < datetime('now', ?)",
            (f"-{int(days)} days",))
        return int(cur.rowcount)


def _outcome_series(db, agent_id: str, limit: int = 500) -> List[int]:
    """Ajanin eski -> yeni isabet serisi (hit=1, miss=0; na atlanir)."""
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute(
            "SELECT outcome FROM agent_votes WHERE agent_id = ? "
            "AND outcome IN ('hit','miss') ORDER BY id ASC LIMIT ?",
            (agent_id, int(limit))).fetchall()
    return [1 if r[0] == "hit" else 0 for r in rows]


def _ewma(series: List[int], alpha: float) -> Optional[float]:
    if not series:
        return None
    acc = float(series[0])
    for x in series[1:]:
        acc = alpha * x + (1.0 - alpha) * acc
    return acc


def update_weights(db, settings: Optional[Dict[str, Any]] = None,
                   apply: bool = False) -> Dict[str, Any]:
    """Ajan agirliklarini EWMA isabetiyle gunceller; ozet doner.

    `apply=True` iken sonuc `settings.update_settings` + `persist` ile
    kalici yazilir (durdurma gerektirmez). Risk kategorisi atlanir.
    """
    p = _params(settings)
    ensure_table(db)
    from app.agents.registry import all_agents
    summary: Dict[str, Any] = {"updated": {}, "disabled": []}
    for agent in all_agents():
        if agent.category == "risk":
            continue
        series = _outcome_series(db, agent.agent_id)
        acc = _ewma(series, p["agent_feedback_alpha"])
        if acc is None:
            continue
        factor = max(p["agent_weight_min"],
                     min(p["agent_weight_max"], acc / 0.5))
        entry = {"agent_id": agent.agent_id, "accuracy": round(acc, 4),
                 "samples": len(series), "factor": round(factor, 3),
                 "weight": round(agent.default_weight * factor, 3)}
        if len(series) >= p["agent_min_samples"] and acc < p["agent_min_acc_enable"]:
            entry["enabled"] = False
            entry["reason"] = "dusuk isabet"
            summary["disabled"].append(entry)
        else:
            entry["enabled"] = True
            summary["updated"][agent.agent_id] = entry
    if apply and (summary["updated"] or summary["disabled"]):
        _write_settings(summary)
    return summary


def _write_settings(summary: Dict[str, Any]) -> None:
    from app.strategy import settings as strategy_settings
    try:
        patch: Dict[str, Any] = {"agents": {}}
        for agent_id, e in summary["updated"].items():
            patch["agents"][agent_id] = {"weight": e["weight"]}
        for e in summary["disabled"]:
            patch["agents"][e["agent_id"]] = {"enabled": False}
        strategy_settings.update_settings(patch)
        strategy_settings.persist()
    except Exception as exc:  # pragma: no cover
        logger.warning("Agent agirlik yazilamadi: %s", exc)


def vote_history(db, agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Bir ajanin son oy gecmisini doner (dashboard icin)."""
    ensure_table(db)
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, bar_ts, vote, confidence, weight, price, outcome, "
            "created_at FROM agent_votes WHERE agent_id = ? "
            "ORDER BY id DESC LIMIT ?", (agent_id, int(limit))).fetchall()
    return [{"symbol": r[0], "bar_ts": r[1], "vote": r[2],
             "confidence": r[3], "weight": r[4], "price": r[5],
             "outcome": r[6], "created_at": r[7]} for r in rows]

"""Tur 2 danisma (deliberation): dusuk guvenli ajanlarin karsi goruse
gonulden katilmasi.

Kurallar (deterministik, sirasiyla):
1. Kategori egilimi: her kategorinin agirlik x (0.5 + guven) toplami BUY/SELL
   tarafinda; mutlak deger `consult_strength` (0.60) uzeri = guclu egilim.
2. Dusuk guvenli ajan (conf < consult_confidence) kategorisinde guclu egilim
   varsa o egilime katilir: oy = egilim, guven tavani `consult_bonus_conf`
   (0.50), `meta["consulted"]=True`.
3. Kategorisi bolunmuse (guclu egilim yok) dusuk guvenli ajan cekimser kalir.
4. Ciftler arasi bonus: (macro, ai) ve (technical, statistical) ciftlerinin
   ikisinde de ayni yone >= %80 mutabakat varsa, o yonde oy veren uyelerin
   guveni `max(conf, 0.55)`'e yukseltilir (`cross_bonus` meta).
5. Risk kategorisi dokunulmaz (veto/ayarlamalar korunur).

Girdi listesi degistirilmez; yeni AgentResult kopyalari doner.
"""
from typing import Any, Dict, List

from app.agents.base import AgentResult

CROSS_PAIRS = (("macro", "ai"), ("technical", "statistical"))

# Varsayilanlar; settings["council"] ile ezilebilir
DEFAULTS = {
    "consult_confidence": 0.45,
    "consult_strength": 0.60,
    "consult_cross_agree": 0.80,
    "consult_bonus_conf": 0.50,
    "consult_cross_bonus_conf": 0.55,
}


def _params(settings: Dict[str, Any]) -> Dict[str, float]:
    block = (settings or {}).get("council") or {}
    out = dict(DEFAULTS)
    out.update({k: float(v) for k, v in block.items() if k in DEFAULTS})
    return out


def _strength(r: AgentResult) -> float:
    return r.weight * (0.5 + r.confidence)


def _category_lean(results: List[AgentResult], category: str) -> Dict[str, Any]:
    buy = sell = 0.0
    for r in results:
        if r.category != category or r.vote not in ("BUY", "SELL"):
            continue
        s = _strength(r)
        if r.vote == "BUY":
            buy += s
        else:
            sell += s
    net = buy - sell
    total = buy + sell
    return {"net": net, "buy": buy, "sell": sell, "total": total}


def _group_agreement(results: List[AgentResult], category: str) -> float:
    """Kategori icinde egilim yonundeki oy agirliginin payi (0-1)."""
    lean = _category_lean(results, category)
    if lean["total"] <= 0:
        return 0.0
    if lean["net"] >= 0:
        return lean["buy"] / lean["total"]
    return lean["sell"] / lean["total"]


def _cross_bonus(results: List[AgentResult], p: Dict[str, float]) -> None:
    """Cift kategorilerde ayni yonde %80 mutabakat bonusu (in-place conf)."""
    for a, b in CROSS_PAIRS:
        la = _category_lean(results, a)
        lb = _category_lean(results, b)
        if la["total"] <= 0 or lb["total"] <= 0:
            continue
        same_dir = (la["net"] >= 0) == (lb["net"] >= 0)
        if not same_dir:
            continue
        agree = min(_group_agreement(results, a), _group_agreement(results, b))
        if agree >= p["consult_cross_agree"]:
            direction = "BUY" if la["net"] >= 0 else "SELL"
            for r in results:
                if r.category in (a, b) and r.vote == direction:
                    r.confidence = max(r.confidence, p["consult_cross_bonus_conf"])
                    r.meta["cross_bonus"] = True
                    r.meta["consulted"] = True


def deliberate(results: List[AgentResult],
               settings: Dict[str, Any] = None) -> List[AgentResult]:
    """Tur 2 danismayi uygular ve kopyalanmis listeyi dondurur."""
    p = _params(settings or {})
    out: List[AgentResult] = []
    for r in results:
        out.append(AgentResult(
            agent_id=r.agent_id, vote=r.vote, weight=r.weight, reason=r.reason,
            confidence=r.confidence,
            adjustments=dict(r.adjustments or {}),
            meta=dict(r.meta or {}),
            category=r.category,
        ))
    if len(out) < 3:
        return out

    by_cat: Dict[str, List[AgentResult]] = {}
    for r in out:
        by_cat.setdefault(r.category, []).append(r)

    for category, members in by_cat.items():
        if category == "risk":
            continue
        lean = _category_lean(out, category)
        for r in members:
            if r.confidence >= p["consult_confidence"]:
                continue
            r.meta["consulted"] = True
            if lean["net"] >= p["consult_strength"]:
                r.vote = "BUY"
                r.confidence = min(r.confidence + 0.15, p["consult_bonus_conf"])
                r.reason = f"{r.reason} [danisma: {category} cogunlugu BUY]"
            elif lean["net"] <= -p["consult_strength"]:
                r.vote = "SELL"
                r.confidence = min(r.confidence + 0.15, p["consult_bonus_conf"])
                r.reason = f"{r.reason} [danisma: {category} cogunlugu SELL]"
            elif r.vote is not None:
                r.vote = None
                r.reason = f"{r.reason} [danisma: {category} bolunmus, cekimser]"

    _cross_bonus(out, p)
    return out

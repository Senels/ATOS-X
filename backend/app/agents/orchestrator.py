"""Agent orchestrator: ajanlari ayar/cache yonetimiyle calistirir ve oylari
toplar.

- `run_for_symbol`: tek sembol icin tum etkin ajanlari seri calistirir.
- `run_council`: ajanlari calistirir, tur 2 danismayi (deliberation) uygular
  ve `consensus_verdict` ile nihai karari uretir (agent kapisi icin).
- `aggregate`: BUY/SELL oylarini agirlik x guven carpani ile toplar,
  esik (0.8, eski konseyle uyumlu) uzerinde karar verir.
- `consensus_verdict`: quorum, kategori sayisi ve konsensus esikleriyle
  nihai karar; veto (risk block) onceliklidir.
- `collect_adjustments`: risk ajanlarinin size/sl/tp carpanlarini birlestirir
  (carpanlar carpilir, boyut 1.0'in ustune cikmaz); block varsa giris durur.

Ajan hatasi sessizce atlanir (tek ajanin cokmesi tum konseyi dusurmaz).
Ayarlar: settings["agents"][agent_id] = {"enabled": bool, "weight": float},
settings["council"] = esikler.
"""
from typing import Any, Dict, List, Tuple

from app.agents.base import AgentResult
from app.agents.deliberation import deliberate
from app.agents.registry import all_agents
from loguru import logger

VOTE_THRESHOLD = 0.8

COUNCIL_DEFAULTS = {
    "agent_min_quorum": 15,
    "agent_min_agree_categories": 4,
    "agent_min_consensus": 0.25,
    "agent_max_size_mult": 1.0,
    "agent_max_sl_mult": 4.0,
    "agent_max_tp_mult": 4.0,
    "agent_min_sl_mult": 0.5,
    "agent_min_tp_mult": 0.5,
}


def agent_configs(settings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Settings'teki `agents` blogunu agent nesneleriyle birlestirir."""
    block = (settings or {}).get("agents") or {}
    configs = {}
    for agent in all_agents():
        cfg = block.get(agent.agent_id, {})
        enabled = cfg.get("enabled", True)
        weight = cfg.get("weight", agent.default_weight)
        configs[agent.agent_id] = {"agent": agent, "enabled": enabled, "weight": float(weight)}
    return configs


def run_for_symbol(ctx: Any, settings: Dict[str, Any]) -> List[AgentResult]:
    """Etkin ajanlari calistirir; settings'teki agirliklar uygulanir."""
    results: List[AgentResult] = []
    for agent_id, cfg in agent_configs(settings).items():
        if not cfg["enabled"]:
            continue
        try:
            res = cfg["agent"].analyze(ctx)
        except Exception as e:
            logger.warning(f"ajan calistirma hatasi ({cfg.get('name', cfg)}): {e}")
            continue
        if res is None:
            continue
        res.weight = cfg["weight"]
        res.category = cfg["agent"].category
        results.append(res)
    return results


def aggregate(results: List[AgentResult],
              threshold: float = VOTE_THRESHOLD) -> Tuple[str, float, float, float, float]:
    """Oylari toplar: (verdict, confidence, net, buy, sell)."""
    buy = sell = 0.0
    for r in results:
        strength = r.weight * (0.5 + r.confidence)
        if r.vote == "BUY":
            buy += strength
        elif r.vote == "SELL":
            sell += strength
    net = buy - sell
    if net >= threshold:
        verdict = "BUY"
    elif net <= -threshold:
        verdict = "SELL"
    else:
        verdict = "HOLD"
    confidence = round(min(abs(net) / (threshold * 2.0), 1.0), 2)
    return verdict, confidence, round(net, 3), round(buy, 3), round(sell, 3)


def collect_adjustments(results: List[AgentResult]) -> Dict[str, Any]:
    """Risk ajanlarinin mudahalelerini birlestirir (block + carpanlar)."""
    size = sl = tp = 1.0
    blocked = False
    block_sources: List[str] = []
    for r in results:
        a = r.adjustments or {}
        size *= float(a.get("size_mult", 1.0))
        sl *= float(a.get("sl_mult", 1.0))
        tp *= float(a.get("tp_mult", 1.0))
        if a.get("block"):
            blocked = True
            block_sources.append(r.agent_id)
    return {
        "size_mult": round(min(size, 1.0), 3),
        "sl_mult": round(min(sl, 1.0), 3),
        "tp_mult": round(min(tp, 1.0), 3),
        "blocked": blocked,
        "block_sources": block_sources,
    }


def enabled_count(settings: Dict[str, Any]) -> int:
    return sum(1 for cfg in agent_configs(settings).values() if cfg["enabled"])


def _council_params(settings: Dict[str, Any]) -> Dict[str, float]:
    block = (settings or {}).get("council") or {}
    out = dict(COUNCIL_DEFAULTS)
    out.update({k: float(v) for k, v in block.items() if k in COUNCIL_DEFAULTS})
    return out


def consensus_verdict(results: List[AgentResult],
                      settings: Dict[str, Any] = None) -> Dict[str, Any]:
    """Nihai konsey karari: quorum + kategori mutabakati + konsensus + veto.

    `consensus` = (buy - sell) / (buy + sell) oranidir; verdict HOLD ise
    gerekcesi `hold_reason` ile bildirilir. Risk ajanlarinin ayarlamalari
    (size/sl/tp carpanlari, block) karara gomulur.
    """
    p = _council_params(settings or {})
    buy = sell = 0.0
    votes = 0
    agree: Dict[str, str] = {}
    adjustments = collect_adjustments(results)
    for r in results:
        s = r.weight * (0.5 + r.confidence)
        if r.vote == "BUY":
            buy += s
            votes += 1
            agree[r.category] = "BUY"
        elif r.vote == "SELL":
            sell += s
            votes += 1
            agree[r.category] = "SELL"
    total = buy + sell
    net = buy - sell
    consensus = round(net / total, 3) if total > 0 else 0.0
    agree_categories = len(agree)
    quorum_ok = votes >= p["agent_min_quorum"]
    cats_ok = agree_categories >= p["agent_min_agree_categories"]

    blocked = adjustments["blocked"]
    if blocked:
        hold_reason = "risk vetosu"
    elif not quorum_ok:
        hold_reason = "yetersiz quorum"
    elif not cats_ok:
        hold_reason = "yetersiz kategori"
    elif abs(consensus) < p["agent_min_consensus"]:
        hold_reason = "zayif konsensus"
    elif net == 0:
        hold_reason = "esit oy"
    else:
        hold_reason = None

    verdict = "HOLD" if hold_reason else ("BUY" if net > 0 else "SELL")

    return {
        "verdict": verdict,
        "confidence": round(abs(consensus), 3),
        "consensus": consensus,
        "buy": round(buy, 3),
        "sell": round(sell, 3),
        "votes": votes,
        "quorum_ok": quorum_ok,
        "agree_categories": agree_categories,
        "blocked": blocked,
        "hold_reason": hold_reason,
        "adjustments": adjustments,
    }


def run_council(ctx: Any, settings: Dict[str, Any]) -> Tuple[List[AgentResult], Dict[str, Any]]:
    """Agent kapsinin tam akisi: calistir -> danis -> nihai karar."""
    results = run_for_symbol(ctx, settings)
    results = deliberate(results, settings)
    return results, consensus_verdict(results, settings)

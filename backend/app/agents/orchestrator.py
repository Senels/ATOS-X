"""Agent orchestrator: ajanlari ayar/cache yonetimiyle calistirir ve oylari
toplar.

- `run_for_symbol`: tek sembol icin tum etkin ajanlari seri calistirir.
- `aggregate`: BUY/SELL oylarini agirlik x guven carpani ile toplar,
  esik (0.8, eski konseyle uyumlu) uzerinde karar verir.
- `collect_adjustments`: risk ajanlarinin size/sl/tp carpanlarini birlestirir
  (carpanlar carpilir, boyut 1.0'in ustune cikmaz); block varsa giris durur.

Ajan hatasi sessizce atlanir (tek ajanin cokmesi tum konseyi dusurmaz).
Ayarlar: settings["agents"][agent_id] = {"enabled": bool, "weight": float}.
"""
from typing import Any, Dict, List, Tuple

from app.agents.base import AgentResult
from app.agents.registry import all_agents

VOTE_THRESHOLD = 0.8


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
        except Exception:
            continue
        if res is None:
            continue
        res.weight = cfg["weight"]
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

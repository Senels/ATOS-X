"""Agent konseyi API endpoint'leri: durum ozeti ve oy gecmisi.

Canli ajan konseyinin (50 ajan) bellek durumu, agirliklari ve geri bildirim
istatistiklerini gosterir; salt-okunurdur.
"""
from fastapi import APIRouter, Request

from app.agents.registry import all_agents
from loguru import logger

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("/summary", summary="Ajan konseyi durum ozeti")
async def agent_summary(request: Request):
    """Bellek durumu, ajan agirliklari ve oy istatistiklerini doner."""
    from app.agents.feedback import vote_history
    from app.agents.retrain import last_trained_at, memory_summary
    from app.core.database import Database

    db = Database()
    settings = {}
    auto_trader = getattr(request.app.state, "auto_trader", None)
    if auto_trader is not None:
        from app.strategy import settings as strat_settings
        try:
            settings = strat_settings.get_settings()
        except Exception:
            settings = {}

    agents_block = (settings.get("agents") or {})
    agents = []
    for a in all_agents():
        cfg = agents_block.get(a.agent_id, {})
        agents.append({
            "agent_id": a.agent_id,
            "name": a.name,
            "category": a.category,
            "tier": a.tier,
            "default_weight": a.default_weight,
            "weight": float(cfg.get("weight", a.default_weight)),
            "enabled": bool(cfg.get("enabled", True)),
        })

    vote_stats = {}
    try:
        for a in all_agents()[:50]:
            h = vote_history(db, a.agent_id, limit=200)
            if not h:
                continue
            resolved = [x for x in h if x["outcome"] in ("hit", "miss")]
            vote_stats[a.agent_id] = {
                "resolved": len(resolved),
                "accuracy": round(sum(1 for x in resolved if x["outcome"] == "hit")
                                  / len(resolved), 4) if resolved else None,
            }
    except Exception as e:
        logger.debug(f"ajan istatistigi hesaplanamadi: {e}")

    return {
        "memory": memory_summary(),
        "last_trained_at": last_trained_at(),
        "use_agent_council": bool(settings.get("use_agent_council", False)),
        "agent_min_confidence": float(settings.get("agent_min_confidence", 0.5) or 0.0),
        "agents": agents,
        "vote_stats": vote_stats,
    }


@router.get("/history", summary="Bir ajanin oy gecmisi")
async def agent_history(agent_id: str, limit: int = 50):
    """Belirli bir ajanin son oylarini (hit/miss durumuyla) doner."""
    from app.agents.feedback import vote_history
    from app.core.database import Database

    known = {a.agent_id for a in all_agents()}
    if agent_id not in known:
        return {"agent_id": agent_id, "votes": [], "error": "bilinmeyen ajan"}
    return {"agent_id": agent_id,
            "votes": vote_history(Database(), agent_id, limit=int(limit))}

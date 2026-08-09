"""Agent konseyi + Executive AI Assistant API."""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.agents.registry import all_agents

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("/summary", summary="Ajan konseyi durum ozeti")
async def agent_summary(request: Request):
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

    agents_block = settings.get("agents") or {}
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
            resolved = [x for x in h if x["outcome"] in ("hit", "miss")]
            if resolved:
                vote_stats[a.agent_id] = {
                    "resolved": len(resolved),
                    "accuracy": round(sum(1 for x in resolved if x["outcome"] == "hit") / len(resolved), 4),
                }
    except Exception:
        pass

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
    from app.agents.feedback import vote_history
    from app.core.database import Database

    known = {a.agent_id for a in all_agents()}
    if agent_id not in known:
        return {"agent_id": agent_id, "votes": [], "error": "bilinmeyen ajan"}
    return {"agent_id": agent_id, "votes": vote_history(Database(), agent_id, limit=int(limit))}


@router.get("/assistant/capabilities", summary="Executive AI Assistant yetenekleri")
async def assistant_capabilities():
    from app.ai.assistant import ExecutiveAssistant
    assistant = ExecutiveAssistant()
    return {"capabilities": assistant.capabilities(), "policy": assistant.admin_policy(),
            "knowledge": assistant.knowledge_map()}


@router.get("/assistant/snapshot", summary="Executive AI Assistant sistem snapshot")
async def assistant_snapshot(request: Request):
    from app.ai.assistant import ExecutiveAssistant
    return ExecutiveAssistant().build_snapshot(getattr(request.app.state, "auto_trader", None))


class AssistantExplainRequest(BaseModel):
    decision: dict
    context: dict = {}


@router.post("/assistant/explain", summary="Karari yonetici seviyesinde acikla")
async def assistant_explain(body: AssistantExplainRequest):
    from app.ai.assistant import ExecutiveAssistant
    return ExecutiveAssistant().explain(body.decision, body.context)


@router.get("/assistant/plan/{action}", summary="Yonetici eylemi icin guvenli action plan")
async def assistant_plan(action: str, live: bool = False, confirmation: bool = False):
    from app.ai.assistant import ExecutiveAssistant
    return {"plan": ExecutiveAssistant().plan(action, live=live, confirmation=confirmation)}

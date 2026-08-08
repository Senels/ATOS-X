"""Agent kayit defteri: tum kategorilerdeki ajanlarin tek cikis noktasi.

Moduller `AGENT_CLASSES` listesi tanimlar; `_load_all` hepsini tek ornekle
(instance) kaydeder. Ajan ayarlari (enabled/weight) settings.json'daki
`agents` blogundan okunur; kayit defteri yalnizca sinif/metadata tasir.
"""
from typing import Dict, List

from app.agents.base import Agent, AgentResult

_AGENTS: Dict[str, Agent] = {}
_LOADED = False


def _load_all() -> None:
    global _LOADED
    if _LOADED:
        return
    from app.agents import (
        ai_agents,
        macro,
        microstructure,
        risk,
        statistical,
        technical,
    )
    for mod in (technical, statistical, macro, microstructure, risk, ai_agents):
        for cls in getattr(mod, "AGENT_CLASSES", ()):
            _AGENTS[cls.agent_id] = cls()
    _LOADED = True


def all_agents() -> List[Agent]:
    _load_all()
    return list(_AGENTS.values())


def get_agent(agent_id: str) -> Agent:
    _load_all()
    return _AGENTS[agent_id]


def has_agent(agent_id: str) -> bool:
    _load_all()
    return agent_id in _AGENTS


def agents_by_category(category: str) -> List[Agent]:
    return [a for a in all_agents() if a.category == category]


def categories() -> List[str]:
    seen: List[str] = []
    for a in all_agents():
        if a.category not in seen:
            seen.append(a.category)
    return seen


__all__ = ["Agent", "AgentResult", "all_agents", "get_agent", "has_agent",
           "agents_by_category", "categories"]

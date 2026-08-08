"""Agent paketi: 50 uzman finansal ajan + grafik desen motoru.

Registry (kayit defteri) ve orchestrator (calistirici/oy toplayici) bu paketin
cikis noktalaridir:
    all_agents()                      -> tum ajanlar
    orchestrator.run_for_symbol(...)  -> bir sembol icin ajan sonuclari
    orchestrator.aggregate(...)       -> konsey karari (verdict/confidence)
"""
from app.agents.base import Agent, AgentResult
from app.agents.registry import (
           agents_by_category,
           all_agents,
           categories,
           get_agent,
           has_agent,
)

__all__ = ["Agent", "AgentResult", "all_agents", "get_agent", "has_agent",
           "agents_by_category", "categories"]

"""Agent calisma baglami: tek sembol analizi icin tum girdileri tasir.

`klines_map`/`corr`/`macro`/`micro` gibi pahali veriler orchestrator tarafindan
bir kez hesaplanip paylasilir; per-sembol ajanlar yalnizca `df` + `portfolio` +
`settings` ile calisir.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class AgentContext:
    symbol: str
    df: Optional[pd.DataFrame] = None
    klines_map: Dict[str, pd.DataFrame] = field(default_factory=dict)
    prices: Dict[str, float] = field(default_factory=dict)
    macro: Dict[str, Any] = field(default_factory=dict)
    micro: Dict[str, Any] = field(default_factory=dict)
    portfolio: List[Dict[str, Any]] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    corr: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

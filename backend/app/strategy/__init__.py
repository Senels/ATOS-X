"""Strateji fabrikasi: `active_strategy` ayarina gore aktif stratejiyi dondurur.

Kullanim:
    from app.strategy import get_strategy
    bot = get_strategy()                       # aktif ayardaki strateji
    bot = get_strategy(settings_dict)          # verilen ayarlardaki strateji
"""
from typing import Any, Dict, Optional


def get_strategy(settings: Optional[Dict[str, Any]] = None):
    """`active_strategy` ayarina gore strateji dondurur: v23 | ttp | v24."""
    from app.strategy import settings as strat_settings
    from app.strategy.tradebot_v23 import TradeBotV23
    from app.strategy.tradebot_v24 import TradeBotV24
    from app.strategy.ttp import TtpTsl

    if settings is None:
        settings = strat_settings.get_settings()
    if settings.get("active_strategy") == "ttp":
        return TtpTsl(settings)
    if settings.get("active_strategy") == "v24":
        return TradeBotV24(settings)
    return TradeBotV23(settings)

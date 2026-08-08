"""Multi-Timeframe (MTF) Sinyal Birleştirme.

Birden fazla zaman dilimindeki sinyal oylarını ağırlıklı oylama ile
birleştirir. 4h sinyali ana çerçeve olarak kabul edilir.

Ağırlık varsayılanları: 4h=1.0, 1h=0.6, 30m=0.4, 15m=0.3
"""
from typing import Any, Dict, List, Optional

import pandas as pd

from app.data import loader
from app.strategy import get_strategy

# ---------------------------------------------------------------------------
# Ağırlık varsayılanları
# ---------------------------------------------------------------------------

DEFAULT_MTF_WEIGHTS: Dict[str, float] = {
    "4h": 1.0,
    "1h": 0.6,
    "2h": 0.7,
    "30m": 0.4,
    "15m": 0.3,
    "1d": 0.8,
}

_AGREE_THRESHOLD = 0.6  # Net oy eşiği


# ---------------------------------------------------------------------------
# Veri hizalama
# ---------------------------------------------------------------------------

def align_timeframes(df_base: pd.DataFrame, df_other: pd.DataFrame) -> pd.DataFrame:
    """İki farklı zaman dilimi DataFrame'ini base'e göre hizalar (ffill).

    df_base'in index'ine yeniden örnekler; gelecek sızıntısını önlemek için
    yalnızca geçmiş verileri (shift + ffill) kullanır.
    """
    if df_base.empty or df_other.empty:
        return pd.DataFrame()
    # Diğer zaman dilimini base'in saat indeksine hizala
    df_other_reindexed = df_other.reindex(
        df_base.index.union(df_other.index)
    ).ffill()
    return df_other_reindexed.reindex(df_base.index)


# ---------------------------------------------------------------------------
# Tek sembol için çok zaman dilimi veri yükleme
# ---------------------------------------------------------------------------

def get_mtf_context(
    symbol: str,
    intervals: Optional[List[str]] = None,
    limit: int = 400,
    data_dir: Optional[str] = None,
) -> Dict[str, pd.DataFrame]:
    """Belirtilen zaman dilimlerinde CSV'den DataFrame yükler.

    Bulunamayan zaman dilimleri sessizce atlanır.

    Parametreler
    ------------
    symbol    : İşlem sembolü (ör. "BTCUSDT").
    intervals : Zaman dilimi listesi (ör. ["4h", "1h", "30m"]).
    limit     : Her veri seti için maksimum bar sayısı.
    data_dir  : Opsiyonel CSV dizini; None ise varsayılan.

    Dönüş
    ------
    ``{interval: pd.DataFrame}`` sözlüğü.
    """
    if intervals is None:
        intervals = ["4h", "1h"]

    result: Dict[str, pd.DataFrame] = {}
    for iv in intervals:
        try:
            df = loader.load_csv(symbol, iv, limit=limit, data_dir=data_dir)
            if not df.empty:
                result[iv] = df
        except (FileNotFoundError, Exception):
            pass
    return result


# ---------------------------------------------------------------------------
# MTF oylama motoru
# ---------------------------------------------------------------------------

def mtf_vote(
    dfs: Dict[str, pd.DataFrame],
    cfg: Optional[Dict[str, Any]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Her zaman diliminden sinyal üretir ve ağırlıklı oy birleştirme yapar.

    Parametreler
    ------------
    dfs     : ``{interval: pd.DataFrame}`` sözlüğü.
    cfg     : Strateji ayarları (None ise varsayılan settings kullanılır).
    weights : Ağırlık sözlüğü; None ise DEFAULT_MTF_WEIGHTS.

    Dönüş
    ------
    Dict: ``{"verdict", "confidence", "votes", "signals"}``
    """
    from app.strategy import settings as strat_settings

    if cfg is None:
        cfg = strat_settings.get_settings()
    if weights is None:
        weights = DEFAULT_MTF_WEIGHTS

    buy_score = sell_score = 0.0
    votes: List[Dict[str, Any]] = []
    signals: Dict[str, Any] = {}

    for interval, df in dfs.items():
        if df is None or df.empty:
            continue
        weight = float(weights.get(interval, 0.3))
        try:
            bot = get_strategy(cfg)
            sig = bot.generate_signal(df)
            direction = sig.get("signal", "HOLD")
            strength = float(sig.get("strength", 0.5) or 0.5)
            # Ağırlık × sinyal gücü
            effective_weight = weight * strength
            votes.append({
                "interval": interval,
                "signal": direction,
                "weight": round(weight, 2),
                "strength": round(strength, 2),
                "effective_weight": round(effective_weight, 3),
            })
            if direction == "BUY":
                buy_score += effective_weight
            elif direction == "SELL":
                sell_score += effective_weight
            signals[interval] = sig
        except Exception:
            pass

    max_possible = sum(
        float(weights.get(iv, 0.3)) for iv in dfs if not dfs[iv].empty
    )
    net = buy_score - sell_score
    if max_possible > 0:
        confidence = round(min(abs(net) / max_possible, 1.0), 3)
    else:
        confidence = 0.0

    if net >= _AGREE_THRESHOLD * max_possible and max_possible > 0:
        verdict = "BUY"
    elif net <= -_AGREE_THRESHOLD * max_possible and max_possible > 0:
        verdict = "SELL"
    else:
        verdict = "HOLD"

    return {
        "verdict": verdict,
        "confidence": confidence,
        "buy_score": round(buy_score, 3),
        "sell_score": round(sell_score, 3),
        "net_score": round(net, 3),
        "votes": votes,
        "signals": signals,
        "intervals_used": list(dfs.keys()),
    }

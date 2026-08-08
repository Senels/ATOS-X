"""Stres testi modülü: tarihsel senaryo simülasyonu.

Bilinen piyasa krizlerini portföye uygulayarak beklenen kayıpları hesaplar.
Her senaryo bir fiyat şoku yüzdesi ile tanımlanır.
"""
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Yerleşik senaryo kataloğu
# ---------------------------------------------------------------------------

BUILTIN_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "covid_2020": {
        "name": "2020 COVID Dump",
        "description": "Mart 2020 sert düşüş (BTC ~-63%)",
        "price_shock_pct": -50.0,
        "duration_days": 30,
    },
    "luna_2022": {
        "name": "2022 LUNA Çöküşü",
        "description": "Mayıs 2022 LUNA/UST çöküşü",
        "price_shock_pct": -70.0,
        "duration_days": 7,
    },
    "ftx_2022": {
        "name": "2022 FTX İflası",
        "description": "Kasım 2022 FTX iflası, BTC ~-25%",
        "price_shock_pct": -30.0,
        "duration_days": 7,
    },
    "flash_crash": {
        "name": "Anlık Flash Crash",
        "description": "Kısa vadeli %20 ani düşüş",
        "price_shock_pct": -20.0,
        "duration_days": 1,
    },
    "bull_run_2021": {
        "name": "2021 Boğa Koşusu",
        "description": "Yüksek volatilite döneminde BTC +100%",
        "price_shock_pct": 100.0,
        "duration_days": 180,
    },
}


# ---------------------------------------------------------------------------
# Temel hesaplama fonksiyonları
# ---------------------------------------------------------------------------

def scenario_pnl(
    positions: List[Dict[str, Any]],
    price_shock_pct: float,
) -> List[Dict[str, Any]]:
    """Her açık pozisyon için belirli bir fiyat şoku sonucundaki PnL'i hesaplar.

    Parametreler
    ------------
    positions : Açık pozisyonlar listesi.
        Her eleman: ``{"symbol", "side", "entry_price", "quantity",
        "notional" (opsiyonel)}``
    price_shock_pct : Fiyat değişim yüzdesi (ör. -30 = %30 düşüş).

    Dönüş
    ------
    Her pozisyon için ``{"symbol", "side", "entry_price", "quantity",
    "shock_pct", "exit_price", "pnl_usdt", "pnl_pct"}`` içeren liste.
    """
    results = []
    shock = price_shock_pct / 100.0
    for pos in positions:
        entry = float(pos.get("entry_price", 0) or 0)
        qty = float(pos.get("quantity", 0) or 0)
        side = str(pos.get("side", "BUY")).upper()
        symbol = str(pos.get("symbol", "?"))
        notional = entry * qty

        exit_price = entry * (1 + shock)
        if side == "BUY":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty

        results.append({
            "symbol": symbol,
            "side": side,
            "entry_price": round(entry, 6),
            "quantity": round(qty, 6),
            "notional": round(notional, 2),
            "shock_pct": round(price_shock_pct, 2),
            "exit_price": round(exit_price, 6),
            "pnl_usdt": round(pnl, 2),
            "pnl_pct": round(pnl / notional * 100, 2) if notional > 0 else 0.0,
        })
    return results


def stress_test(
    positions: List[Dict[str, Any]],
    scenarios: Optional[Dict[str, Dict[str, Any]]] = None,
    equity: float = 10000.0,
) -> Dict[str, Any]:
    """Tüm senaryolar için stres testi çalıştırır.

    Parametreler
    ------------
    positions : Açık pozisyonlar listesi (``scenario_pnl`` formatı).
    scenarios : Senaryo sözlüğü; None ise ``BUILTIN_SCENARIOS`` kullanılır.
    equity    : Mevcut portföy değeri (USDT); yüzde hesabı için.

    Dönüş
    ------
    Dict: her senaryo için ``{"total_pnl_usdt", "total_pnl_pct",
    "positions", "equity_after"}`` içeren sonuçlar.
    """
    if scenarios is None:
        scenarios = BUILTIN_SCENARIOS

    results: Dict[str, Any] = {}
    for key, scenario in scenarios.items():
        shock = float(scenario.get("price_shock_pct", 0))
        pos_results = scenario_pnl(positions, shock)
        total_pnl = sum(p["pnl_usdt"] for p in pos_results)
        equity_after = equity + total_pnl
        pnl_pct = (total_pnl / equity * 100) if equity > 0 else 0.0
        results[key] = {
            "name": scenario.get("name", key),
            "description": scenario.get("description", ""),
            "price_shock_pct": shock,
            "total_pnl_usdt": round(total_pnl, 2),
            "total_pnl_pct": round(pnl_pct, 2),
            "equity_after": round(equity_after, 2),
            "positions": pos_results,
        }

    # Özet: en kötü senaryo
    if results:
        worst_key = min(results, key=lambda k: results[k]["total_pnl_usdt"])
        best_key = max(results, key=lambda k: results[k]["total_pnl_usdt"])
    else:
        worst_key = best_key = None

    return {
        "scenarios": results,
        "position_count": len(positions),
        "equity": round(equity, 2),
        "worst_scenario": worst_key,
        "best_scenario": best_key,
    }


def available_scenarios() -> Dict[str, str]:
    """Yerleşik senaryo adlarını döndürür (key -> name)."""
    return {k: v["name"] for k, v in BUILTIN_SCENARIOS.items()}

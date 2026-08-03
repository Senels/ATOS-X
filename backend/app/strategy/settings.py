"""TradeBot v23 strateji ayarlari - tek kaynak (single source of truth).

main.py, backtest API'si, canli sinyal ureticisi ve otomatik trader buradaki
durumu paylasir. Ayarlar `settings.json` dosyasina kalici yazilabilir (persist)
ve baslangicta geri yuklenir (load). `optimized_settings.json` varsa optimize
edilmis parametreler varsayilan olarak uygulanir.
"""
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

DEFAULT_STRATEGY_SETTINGS: Dict[str, Any] = {
    # Leading indicator secimi
    "leading_indicator": "Range Filter",
    # Sinyal expiry: leading kosul kac bar boyunca gecerli kalsin
    "signal_expiry": 3,
    # Alternat sinyal: onceki CondIni ayni yonde ise tekrar giris alma
    "alternate_signal": True,
    # Gorsel ayarlar (dashboard icin, sinyali etkilemez)
    "show_signal": True,
    "show_dashboard": True,
    "dashboard_position": "bottom-right",
    "dashboard_size": "Normal",
    # Risk yonetimi (BacktestEngine ile ortak tek kaynak)
    "initial_equity": 10000.0,   # Backtest baslangic sermayesi
    "risk_per_trade": 0.02,      # Trade basina risk orani (%2)
    "fee_rate": 0.0005,          # Taker komisyonu
    "max_leverage": 10.0,        # Kaldirac siniri
    "max_open_positions": 3,     # Canli acik pozisyon limiti
    "max_position_pct": 75.0,    # Tek sembol nominal pozisyon esigi (% equity, uyari)
    "max_side_pct": 150.0,       # Tek yonde toplam nominal pozisyon esigi (% equity, uyari)
    "max_drawdown_pct": 20.0,    # Peak equity'den düşüş esigi (%); asilinca yeni giris durur
    "max_position_age_hours": 8, # Pozisyon max acik kalma suresi (saat; 0 = devre disi)
    "trailing_activate_pct": 3.0,  # Bu kar esiginde SL takibi baslar (%)
    "trailing_sl_pct": 1.5,        # Takip eden SL'nin fiyata uzakligi (%; 0 = devre disi)
    "trailing_min_move_pct": 0.1,  # SL'nin guncellenmesi icin gereken min hareket (%; 0 = her seferinde)
    # Strateji parametreleri
    "rr_ratio": 1.5,             # TP = SL mesafesi * rr_ratio
    "sl_lookback": 5,            # pivot swing uzunlugu
    "sl_timeframe": "",          # coklu zaman dilimi (bos = ayni tf)
    "atr_fallback": True,        # pivot bulunamazsa ATR fallback kullan
    "atr_mult": 1.5,             # ATR carpani (fallback SL)
    # Range Filter parametreleri
    "rangefilt_length": 3,
    "range_filt_mult": 2.5,
    # Konfirmasyonlar (True = aktif, yonde bazli kosul aranir)
    "confirmations": {
        "ema": False,
        "2ma": False,
        "3ma": False,
        "rf": False,
        "rqk": True,
        "st": False,
        "ht": False,
        "rsi": False,
        "macd": False,
        "stoch": False,
        "ichi": False,
        "ce": False,
    },
}

_STRATEGY_FILE = Path(__file__).resolve().parent / "settings.json"
_OPTIMIZED_FILE = Path(__file__).resolve().parent / "optimized_settings.json"


def _merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Ic ici dict'leri (confirmations) birlestirerek gunceller."""
    for key, value in patch.items():
        if key in base:
            if isinstance(base[key], dict) and isinstance(value, dict):
                base[key].update(value)
            else:
                base[key] = value
    return base


def _defaults() -> Dict[str, Any]:
    """Optimize edilmis ayarlar varsa onlari da iceren varsayilan durum."""
    d = deepcopy(DEFAULT_STRATEGY_SETTINGS)
    if _OPTIMIZED_FILE.exists():
        try:
            with _OPTIMIZED_FILE.open("r", encoding="utf-8") as f:
                d = _merge(d, json.load(f))
        except Exception:
            pass
    return d


# Calisma zamaninda degisebilen kopya (baslangicta dosyadan yuklenir)
_state: Dict[str, Any] = _defaults()


def get_settings() -> Dict[str, Any]:
    return deepcopy(_state)


def default_settings() -> Dict[str, Any]:
    return _defaults()


def update_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in patch.items():
        if key in _state:
            if isinstance(_state[key], dict) and isinstance(value, dict):
                _state[key].update(value)
            else:
                _state[key] = value
    return get_settings()


def reset_settings() -> Dict[str, Any]:
    global _state
    _state = _defaults()
    return get_settings()


def load() -> Dict[str, Any]:
    """Kalici ayarlar dosyasini (settings.json) geri yukler."""
    global _state
    if _STRATEGY_FILE.exists():
        try:
            with _STRATEGY_FILE.open("r", encoding="utf-8") as f:
                persisted = json.load(f)
            _state = _merge(_defaults(), persisted)
        except Exception:
            _state = _defaults()
    else:
        _state = _defaults()
    return get_settings()


def persist() -> Dict[str, Any]:
    """Mevcut durumu settings.json dosyasina yazar (yeniden baslatmada korunur)."""
    with _STRATEGY_FILE.open("w", encoding="utf-8") as f:
        json.dump(_state, f, indent=2, ensure_ascii=False)
    return get_settings()

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
    # Aktif strateji secimi: v23 (TradeBotV23) | ttp (TTPTSL)
    "active_strategy": "v23",
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
    "max_consecutive_losses": 5, # Ardısık zarar siniri; asilinca girisler durur (0 = devre disi)
    "trailing_activate_pct": 3.0,  # Bu kar esiginde SL takibi baslar (%)
    "trailing_sl_pct": 1.5,        # Takip eden SL'nin fiyata uzakligi (%; 0 = devre disi)
    "trailing_min_move_pct": 0.1,  # SL'nin guncellenmesi icin gereken min hareket (%; 0 = her seferinde)
    "breakeven_activate_pct": 2.0, # Bu kar esiginde SL giris fiyatina tasinir (%; 0 = devre disi)
    "max_daily_loss_pct": 5.0,     # Gunluk toplam zarar siniri (% equity; asilinca girisler durur; 0 = devre disi)
    "min_equity": 5000.0,          # Equity bu taban degerin altina duserse girisler durur (USDT; 0 = devre disi)
    # Decision Council: canli girislerde coklu sinyal oylamasi filtresi
    "use_decision_council": True,   # True iken girisler council kararina ve min guvene tabi
    "council_min_confidence": 0.6,  # Council kararina gerekli minimum guven (0-1)
    "min_signal_strength": 0.6,     # Sinyal gucu esigi (0-1; alti giris engellenir; 0 = devre disi)
    # Coin Intelligence: canli momentum skoruna gore sembol onceligi
    "use_score_ranking": True,      # True iken ranking canli coin_score'a gore yeniden siralanir
    # AI (TensorFlow): derin ogrenme yon tahmini kapisi
    "use_ai_model": True,           # True iken AI tahmini sinyal yonuyle uyusmali (model yoksa pasif)
    "ai_min_confidence": 0.55,      # AI tahminini gecirmek icin gereken min guven (0-1)
    "ai_model_path": "ai_direction",  # backend/models/ altindaki model adi
    # Otomatik yeniden egitim (feedback dongusu)
    "ai_auto_retrain": False,        # True iken zaman/accuracy tetikleyicisiyle yeniden egitir
    "ai_retrain_interval_hours": 24.0,  # Zaman tetikleyicisi (saat; 0 = kapali)
    "ai_retrain_min_acc": 0.55,      # Canli accuracy bu degerin altina duserse + yeterli ornek + soguma -> tetikler
    "ai_retrain_min_samples": 30,    # Accuracy tetikleyicisi icin gereken cozulmus tahmin sayisi
    "ai_retrain_symbols": 400,       # Egitimde kullanilacak sembol sayisi
    "ai_retrain_epochs": 30,         # Egitim epoch sayisi
    # Market Collector: ranking icin yerel CSV tazeligi
    "data_backfill_hours": 24.0,    # Otomatik backfill araligi (saat; 0 = devre disi)
    "data_freshness_hours": 12.0,   # Son bar bu saatten eskiyse sembol eski sayilir
    # Strateji parametreleri
    "rr_ratio": 1.5,             # TP = SL mesafesi * rr_ratio
    "sl_lookback": 5,            # pivot swing uzunlugu
    "sl_timeframe": "",          # coklu zaman dilimi (bos = ayni tf)
    "atr_fallback": True,        # pivot bulunamazsa ATR fallback kullan
    "atr_mult": 1.5,             # ATR carpani (fallback SL)
    # Range Filter parametreleri
    "rangefilt_length": 3,
    "range_filt_mult": 2.5,
    # TTPTSL strateji parametreleri (optimize_ttp.py unified/OOS sonuclari)
    "ttp": {
        "fast_ma_len": 31,
        "slow_ma_len": 92,
        "atr_len": 24,
        "sl_method": "atr",
        "sl_long_perc": 0.0606,
        "sl_short_perc": 0.0713,
        "sl_long_atr_mul": 3.25,
        "sl_short_atr_mul": 4.8125,
        "sl_trail_mode": "TP",
        "be_enabled": True,
        "tp_qty_pct": 0.6125,
        "tp_method": "rr",
        "tp_long_perc": 0.0925,
        "tp_short_perc": 0.085,
        "tp_long_atr_mul": 11.375,
        "tp_short_atr_mul": 7.25,
        "tp_long_rr": 3.1,
        "tp_short_rr": 1.95,
        "tp_trail_enabled": True,
        "dist_method": "perc",
        "dist_perc": 0.0284,
        "dist_atr_mul": 3.4,
    },
    # Konfirmasyonlar (True = aktif, yonde bazli kosul aranir)
    "confirmations": {
        "ema": True,
        "2ma": True,
        "3ma": True,
        "rf": True,
        "rqk": True,
        "st": True,
        "ht": True,
        "rsi": True,
        "macd": True,
        "stoch": True,
        "ichi": True,
        "ce": True,
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


# Optimizasyon sonucu dosyasindan uygulanabilir parametre anahtarlari
_OPTIMIZED_KEYS = ("rangefilt_length", "range_filt_mult", "signal_expiry", "rr_ratio", "sl_lookback")


def load_optimized() -> Dict[str, Any]:
    """optimized_settings.json icerigini doner (yoksa bos dict)."""
    if not _OPTIMIZED_FILE.exists():
        return {}
    try:
        with _OPTIMIZED_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def apply_optimized() -> Dict[str, Any]:
    """Kayitli optimize edilmis parametreleri canli duruma uygular ve kalici yazar.

    Dosyada olmayan anahtarlar degismez; `_` on ekli meta alanlari yok sayilir.
    "ttp" stratejisi icin `ttp` blogu birlestirilir ve `active_strategy` "ttp" olarak
    ayarlanir; "v23" icin yalnizca `_OPTIMIZED_KEYS` top-level anahtarlari uygulanir.
    """
    payload = load_optimized()
    applied = []
    for key in _OPTIMIZED_KEYS:
        if key in payload:
            update_settings({key: payload[key]})
            applied.append(key)
    if isinstance(payload.get("ttp"), dict):
        update_settings({"ttp": payload["ttp"]})
        applied.append("ttp")
    if payload.get("active_strategy") in ("v23", "ttp"):
        update_settings({"active_strategy": payload["active_strategy"]})
        applied.append("active_strategy")
    if applied:
        persist()
    return {"applied": applied, "settings": get_settings()}

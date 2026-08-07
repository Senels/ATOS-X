"""OHLCV veri yukleyici: yerel CSV arsivi veya Binance canli kline."""
import re
from pathlib import Path
from typing import List

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = REPO_ROOT / "legacy" / "data"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _safe_token(value: str) -> str:
    """Path traversal'a karşı sembol/interval değerini doğrular."""
    if not _TOKEN_RE.match(str(value)):
        raise ValueError(f"Geçersiz sembol/interval değeri: {value!r}")
    return value

STABLECOIN_BASES = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "BUSD", "USDE", "USD1",
    "PYUSD", "EURI", "EUR", "EURV", "USDP", "GUSD", "LUSD", "FRAX", "TETHER",
}


def is_stablecoin_symbol(symbol: str) -> bool:
    """Sembolun baz varliginin stablecoin olup olmadigini dondurur.

    Orn: USDCUSDT -> True, 1000PEPEUSDT -> False, XAUUSDT -> False.
    """
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    while base and base[0].isdigit():
        base = base[1:]
    return base in STABLECOIN_BASES


def _data_dir(interval: str, data_dir: str | None = None) -> Path:
    if data_dir:
        return Path(data_dir)
    return DEFAULT_DATA_DIR / f"futures_{interval}_data"


def load_csv(symbol: str, interval: str = "4h", data_dir: str | None = None,
             limit: int | None = None) -> pd.DataFrame:
    """Yerel CSV arsivinden OHLCV DataFrame yukler (index = utc datetime)."""
    safe_sym = _safe_token(symbol)
    safe_iv = _safe_token(interval)
    d = _data_dir(safe_iv, data_dir)
    path = d / f"{safe_sym}_{safe_iv}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Veri dosyasi yok: {path}")
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if limit and len(df) > limit:
        df = df.iloc[-int(limit):]
    return df


def list_symbols(interval: str = "4h", data_dir: str | None = None) -> List[str]:
    d = _data_dir(interval, data_dir)
    if not d.exists():
        return []
    return sorted(p.name.replace(f"_{interval}.csv", "") for p in d.glob(f"*_{interval}.csv"))


def dataframe_from_klines(raw: pd.DataFrame) -> pd.DataFrame:
    """Binance get_klines ciktisini normalize eder (gerekirse)."""
    return raw.copy()

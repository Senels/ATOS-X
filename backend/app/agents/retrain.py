"""Agent konseyi otomatik egitim yoneticisi.

Iki tetikleyici destekler (AI retrain ile ayni desen):
- Zaman tabanli: `agent_retrain_interval_hours` gectiyse (analog bellek hic
  yoksa da).
- Canli isabet tabanli: cozulmus oy sayisi `agent_min_samples` ustundeyken
  ajan ortalamasi `agent_min_acc` altina dustuyse (soguma suresi gecmisse).

Egitim `scripts/train_agents.py`'yi AYRI bir Python sureci olarak calistirir;
analog bellek kurulumu ve geri bildirim agirliklari bu surecte uygulanir.
Bellek yenilendiginde `app.agents.analog.reset_memory` ile cache temizlenir.
"""
import asyncio
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.agents.analog import MEMORY_PATH, get_memory


def last_trained_at() -> Optional[float]:
    """Analog bellek artifact'inin mtime'i; yoksa None."""
    if not MEMORY_PATH.exists():
        return None
    return MEMORY_PATH.stat().st_mtime


def agent_retrain_due(last_ts: Optional[float], now: float,
                      interval_hours: float) -> bool:
    """Interval gectiyse (veya hic egitim yoksa) yeniden egitim zamani."""
    if last_ts is None:
        return True
    return (now - last_ts) >= interval_hours * 3600.0


def accuracy_trigger(agent_accuracy: float, min_acc: float,
                     last_ts: Optional[float], now: float,
                     cooldown_hours: float = 6.0) -> bool:
    """Canli ajan isabeti esigin altinda + soguma gecmisse (None = yetersiz ornek)."""
    if agent_accuracy is None:
        return False
    if agent_accuracy >= min_acc:
        return False
    if last_ts is None:
        return True
    return (now - last_ts) >= cooldown_hours * 3600.0


class AgentRetrainRunner:
    """`scripts/train_agents.py` alt sureci uzerinden konsey egitim calistirici."""

    def __init__(self, python_exe: Optional[str] = None):
        self.python_exe = python_exe or sys.executable
        self.backend_dir = Path(__file__).resolve().parents[2]
        self._create_subprocess = asyncio.create_subprocess_exec

    async def train(self, symbols: int = 150, max_bars: int = 1500,
                    horizon: int = 24, timeout: int = 1800,
                    skip_weights: bool = False) -> Tuple[bool, str]:
        """Alt sureci baslatir; (basarili mi, cikti sonu) dondurur."""
        script = self.backend_dir / "scripts" / "train_agents.py"
        args = [self.python_exe, str(script),
                "--symbols", str(symbols),
                "--max-bars", str(max_bars),
                "--horizon", str(horizon)]
        if skip_weights:
            args.append("--skip-weights")
        proc = await self._create_subprocess(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self.backend_dir),
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "egitim zaman asimi (alt surec sonlandirildi)"
        text = out.decode("utf-8", errors="replace") if out else ""
        tail = "\n".join(text.splitlines()[-8:]).strip()
        if proc.returncode == 0:
            from app.agents.analog import reset_memory
            reset_memory()
            return True, tail
        return False, tail or "bilinmeyen hata"


def agent_accuracy(db, window: int = 200) -> Optional[float]:
    """Son `window` oyun genel isabet ortalamasi; oy yoksa None."""
    import sqlite3
    if not Path(db.db_path).exists():
        return None
    try:
        with sqlite3.connect(db.db_path) as conn:
            rows = conn.execute(
                "SELECT outcome FROM agent_votes WHERE outcome IN ('hit','miss') "
                "ORDER BY id DESC LIMIT ?", (int(window),)).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    hits = sum(1 for r in rows if r[0] == "hit")
    return hits / len(rows)


def memory_summary() -> dict:
    """Yuklu bellegin ozeti (dashboard icin); yoksa bos."""
    mem = get_memory()
    if mem is None:
        return {"loaded": False}
    return mem.describe()

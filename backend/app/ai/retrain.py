"""Otomatik AI yeniden egitim yoneticisi.

Iki tetikleyici destekler:
- Zaman tabanli: `ai_retrain_interval_hours` gectiyse (model dosyalari hic
  yoksa da).
- Canli accuracy tabanli: cozulmus tahmin sayisi `ai_retrain_min_samples`
  ustundeyken accuracy `ai_retrain_min_acc` altina dustuyse (soguma suresi
  gecmisse).

Egitim `scripts/train_ai.py`'yi AYRI bir Python sureci olarak calistirir;
bu sayede sunucunun event loop'u egitim suresince bloke olmaz. TensorFlow
bu modul icinde gerekmez (alt surec kendi ortaminda TF kullanir).
"""
import asyncio
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.ai import model as m


def last_trained_at(model_name: str = "ai_direction") -> Optional[float]:
    """Model dizinindeki en guncel dosyanin mtime'i; model yoksa None."""
    d = m.model_dir(model_name)
    if not d.exists():
        return None
    ts = [p.stat().st_mtime for p in d.iterdir() if p.is_file()]
    return max(ts) if ts else None


def retrain_due(last_ts: Optional[float], now: float,
                interval_hours: float) -> bool:
    """Interval gectiyse (veya hic egitim yoksa) yeniden egitim zamani."""
    if last_ts is None:
        return True
    return (now - last_ts) >= interval_hours * 3600.0


def accuracy_trigger(resolved: int, accuracy: float, min_samples: int,
                     min_acc: float, last_ts: Optional[float], now: float,
                     cooldown_hours: float = 6.0) -> bool:
    """Canli accuracy esigin altinda + yeterli ornek + soguma gectiyse tetikle."""
    if resolved < min_samples:
        return False
    if accuracy >= min_acc:
        return False
    if last_ts is None:
        return True
    return (now - last_ts) >= cooldown_hours * 3600.0


class RetrainRunner:
    """`scripts/train_ai.py` alt surec uzerinden egitim calistirici."""

    def __init__(self, python_exe: Optional[str] = None,
                 model_name: str = "ai_direction"):
        self.python_exe = python_exe or sys.executable
        self.model_name = model_name
        self.backend_dir = Path(__file__).resolve().parents[2]
        self._create_subprocess = asyncio.create_subprocess_exec

    async def train(self, symbols: int = 400, epochs: int = 30,
                    horizon: int = 24, atr_mult: float = 1.0,
                    timeout: int = 1800) -> Tuple[bool, str]:
        """Alt sureci baslatir; (basarili mi, cikti sonu) dondurur."""
        script = self.backend_dir / "scripts" / "train_ai.py"
        proc = await self._create_subprocess(
            self.python_exe, str(script),
            "--symbols", str(symbols),
            "--epochs", str(epochs),
            "--horizon", str(horizon),
            "--atr-mult", str(atr_mult),
            "--model", self.model_name,
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
        tail = "\n".join(text.splitlines()[-5:]).strip()
        if proc.returncode == 0:
            return True, tail
        return False, tail or "bilinmeyen hata"

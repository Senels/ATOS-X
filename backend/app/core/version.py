"""Diskteki git HEAD commit hash'ini okur.

Kod guncellenip sunucu yeniden baslatilmazsa /health uzerinden
in_sync=False ile gorunur olur (eski surecte giris engeli olmadan
calisma riskini onler).
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GIT_DIR = _REPO_ROOT / ".git"


def git_head() -> str:
    """Diskteki git HEAD commit hash'i; okunamazsa 'unknown'."""
    try:
        head = (_GIT_DIR / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:].strip()
            head = (_GIT_DIR / ref).read_text(encoding="utf-8").strip()
        if len(head) == 40:
            return head
    except Exception:
        pass
    return "unknown"

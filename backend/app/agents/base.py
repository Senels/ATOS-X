"""Agent framework: 50 uzman finansal ajanin ortak sozlesmesi.

Her ajan `Agent.analyze(context)` ile tek sembol/piyasa baglaminda calisir ve
`AgentResult` doner: oy (BUY/SELL/None), agirlik, guven, aciklama ve istege
bagli risk ayarlamalari (size/sl/tp carpanlari veya giris engelleme).

Tier'lar (orchestrator tarafindan yonetilir):
- tier 0: her taramada, per-sembol (teknik/istatistik/AI)
- tier 1: ~5 dk'da bir (makro/mikro yapi; veri cache'e bagli)
- tier 2: ~30 dk'da bir (korelasyon, sektor rotasyonu, breadth)

Tum ajanlar deterministik olmalidir (rastgelelik yok); `meta` yalnizca
gosterim/debug amaclidir ve dashboard'a yansir.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

BUY = "BUY"
SELL = "SELL"


@dataclass
class AgentResult:
    """Bir ajanin analiz ciktisi.

    `vote`: BUY | SELL | None (cekimser/HOLD).
    `adjustments`: risk ajanlarinin pozisyon boyutlandirma/SL-TP mudahaleleri:
        - size_mult:  giris boyutu carpani (0-1 arasi kucultur)
        - sl_mult:    SL mesafesi carpani (1.0 ustu genisletir)
        - tp_mult:    TP mesafesi carpani
        - block:      True ise giris engellenir (korelasyon/risk carpismasi)
    """
    agent_id: str
    vote: Optional[str]
    weight: float
    reason: str
    confidence: float = 0.5
    adjustments: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    category: str = ""

    @property
    def blocking(self) -> bool:
        return bool(self.adjustments.get("block"))


class Agent:
    """Ajan taban sinifi. Alt siniflar `AGENT_CLASSES` listesine kaydedilir."""

    agent_id = "base"
    name = "Base Agent"
    category = "generic"
    tier = 0
    default_weight = 0.3

    def analyze(self, context: Any) -> AgentResult:  # pragma: no cover
        raise NotImplementedError

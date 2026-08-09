"""Persistent research-time registry for model/agent OOS performance."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AgentRegistry:
    def __init__(self, path: str | Path = "backend/data/agent_registry.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "agents": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
                raise ValueError("invalid registry")
            return data
        except Exception as exc:
            raise RuntimeError(f"Agent registry okunamadi: {self.path}") from exc

    def record(self, agent: str, symbol: str, timeframe: str, metrics: dict[str, Any],
               model_version: str = "unknown") -> None:
        key = f"{agent}:{symbol}:{timeframe}"
        entry = self._data["agents"].setdefault(key, {
            "agent": agent, "symbol": symbol, "timeframe": timeframe,
            "model_version": model_version, "runs": [],
        })
        entry["model_version"] = model_version
        entry["runs"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
        })
        self._save()

    def latest(self, agent: str, symbol: str, timeframe: str) -> dict[str, Any] | None:
        key = f"{agent}:{symbol}:{timeframe}"
        entry = self._data["agents"].get(key)
        if not entry or not entry.get("runs"):
            return None
        return entry["runs"][-1]

    def snapshot(self) -> dict[str, Any]:
        return self._data

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

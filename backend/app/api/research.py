"""Read-only status API for the Binance USD-M Futures research lifecycle."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app.ai.promotion import ensemble_weights

router = APIRouter(prefix="/api/v1/research", tags=["research"])

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REPORTS_ROOT = _REPO_ROOT / "reports"
_REGISTRY_PATH = _REPO_ROOT / "backend" / "data" / "agent_registry.json"
_MAX_REPORT_BYTES = 2 * 1024 * 1024


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read bounded local research output without creating or modifying files."""
    if not path.is_file():
        return None, "not_run"
    if path.stat().st_size > _MAX_REPORT_BYTES:
        return None, "report_too_large"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "invalid_report"
    if not isinstance(payload, dict):
        return None, "invalid_report"
    return payload, None


def _stage(identifier: str, label: str, path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload, issue = _read_json(path)
    if issue:
        return {
            "id": identifier,
            "label": label,
            "status": "NOT_RUN" if issue == "not_run" else "INVALID",
            "source": str(path.relative_to(_REPO_ROOT)),
            "detail": issue,
        }, None
    return {
        "id": identifier,
        "label": label,
        "status": str(payload.get("status", "READY")),
        "source": str(path.relative_to(_REPO_ROOT)),
        "detail": None,
    }, payload


def _scorecard_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    rows = payload.get("scorecards") or payload.get("reports") or []
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("models"), dict):
            for name, metrics in row["models"].items():
                if isinstance(metrics, dict):
                    compact.append({"symbol": row.get("symbol", "unknown"), "model": name, **metrics})
            continue
        compact.append(row)
    return compact[:200]


def _registry_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    agents = (payload or {}).get("agents", {})
    if not isinstance(agents, dict):
        return {"entries": [], "decisions": {}, "ensembles": []}
    entries = []
    decisions: dict[str, int] = {}
    groups: set[tuple[str, str]] = set()
    for value in agents.values():
        if not isinstance(value, dict) or not value.get("runs"):
            continue
        latest = value["runs"][-1]
        metrics = latest.get("metrics", {}) if isinstance(latest, dict) else {}
        decision = str(metrics.get("promotion_decision", "PENDING"))
        decisions[decision] = decisions.get(decision, 0) + 1
        symbol = str(value.get("symbol", "unknown"))
        timeframe = str(value.get("timeframe", "unknown"))
        groups.add((symbol, timeframe))
        entries.append({
            "agent": value.get("agent", "unknown"),
            "symbol": symbol,
            "timeframe": timeframe,
            "model_version": value.get("model_version", "unknown"),
            "decision": decision,
            "timestamp": latest.get("timestamp"),
            "metrics": metrics,
        })
    ensembles = [
        {"symbol": symbol, "timeframe": timeframe, "weights": ensemble_weights(payload or {}, symbol, timeframe)}
        for symbol, timeframe in sorted(groups)
    ]
    return {"entries": entries[-100:], "decisions": decisions, "ensembles": ensembles}


def build_research_summary() -> dict[str, Any]:
    archive_stage, archive = _stage("archive", "5 yıllık arşiv doğrulama", _REPORTS_ROOT / "research_manifest.json")
    oos_stage, oos = _stage("oos", "Dense / LSTM per-symbol OOS", _REPORTS_ROOT / "model_oos_pipeline.json")
    score_stage, scorecards = _stage("scorecard", "Maliyetli OOS scorecard", _REPORTS_ROOT / "symbol_oos_scorecard.json")
    registry_stage, registry = _stage("registry", "Agent Registry ve promotion", _REGISTRY_PATH)
    stages = [archive_stage, oos_stage, score_stage, registry_stage]

    reasons: list[str] = []
    for payload in (archive, oos):
        if payload:
            reasons.extend(str(item) for item in payload.get("blocking_reasons", []) if item)
            if payload.get("status") == "INSUFFICIENT_HISTORY":
                reasons.append("five_year_archive_not_ready")
    statuses = {stage["status"] for stage in stages}
    if statuses & {"BLOCKED", "DOWNLOAD_FAILED", "INSUFFICIENT_HISTORY"}:
        status = "BLOCKED"
    elif "INVALID" in statuses:
        status = "INVALID"
    elif statuses == {"READY"}:
        status = "READY"
    elif "READY_FOR_OOS" in statuses:
        status = "PENDING_OOS"
    else:
        status = "NOT_RUN"

    return {
        "exchange": "Binance Global USD-M Futures",
        "live_trading": False,
        "status": status,
        "blocking_reasons": sorted(set(reasons)),
        "stages": stages,
        "archive": archive or {},
        "model_oos": oos or {},
        "scorecards": _scorecard_rows(scorecards),
        "registry": _registry_summary(registry),
    }


@router.get("/summary", summary="Araştırma zinciri salt-okunur durum özeti")
async def research_summary() -> dict[str, Any]:
    return build_research_summary()

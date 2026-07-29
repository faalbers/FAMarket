"""
Scoring rules: read, edit, preview and apply.

Saving a rule also refreshes the stored `*_goodness`, category and overall score
columns in `analysis.db` — a fast recompute (no fetch, no per-symbol pass) — so
the filterable/sortable scores match the heat map immediately rather than
waiting for the next full analysis.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from analysis_layer import scoring_rules as SR
from services import rules_data

router = APIRouter(prefix="/api/scoring")


@router.get("/rules")
def rules() -> dict[str, Any]:
    return {
        "metrics": rules_data.editable_metrics(),
        "rules": SR.load_rules(),
        "defaults": SR.DEFAULT_RULES,
        "overview": rules_data.overview(),
    }


class RuleRequest(BaseModel):
    metric: str
    rule: dict


@router.post("/preview")
def preview(req: RuleRequest) -> dict[str, Any]:
    """What the candidate rule would do to the real universe, as a histogram."""
    return rules_data.preview(req.metric, req.rule)


@router.get("/suggest")
def suggest(metric: str) -> dict[str, Any]:
    return {"rule": rules_data.suggest(metric)}


class SaveRulesRequest(BaseModel):
    rules: dict[str, dict]
    refresh: bool = True


@router.put("/rules")
def save(req: SaveRulesRequest) -> dict[str, Any]:
    SR.save_rules(req.rules)
    if not req.refresh:
        return {"saved": True, "refreshed": None}

    from analysis_layer import scoring

    result = scoring.refresh_scores()
    return {"saved": True, "refreshed": result}

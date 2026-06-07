"""
Analysis orchestrator (Topic 4.2 — FETCH / ANALYSIS PHASE DESIGN).

Entry point that runs the full clean-slate recalculation after a fetch:
  1. Load active+validated symbols and their data from the Phase 1 DBs (pandas).
  2. Compute raw metrics (metrics.py) and technical indicators (technical.py).
  3. Compute peer comparisons (peers.py).
  4. Compute intrinsic values (intrinsic_value.py).
  5. Compute category scores + Overall Score (scoring.py), rs_rank last.
  6. Database.replace() the analysis table — full rebuild, no deltas.
  7. Record "Analysis calculated: <timestamp>" and "Prices as of: <session date>".

SKELETON — Phase 2.
"""

from __future__ import annotations


def run_analysis() -> None:
    """Full clean-slate recalculation of analysis.db. See module docstring."""
    raise NotImplementedError("Phase 2")

"""
Scoring & ranking (Topic 4.4). Both raw metrics and composite scores available.

  * Category scores (0-100): Value, Quality, Growth, Momentum, Income.
  * Overall Score: weighted combination, weights in settings.OVERALL_SCORE_WEIGHTS
    (default Quality 25 / Growth 25 / Momentum 20 / Value 20 / Income 10).
  * Metric weights within each category: sensible Claude defaults, all adjustable
    from the Settings page (sliders -> config).

SKELETON — Phase 2.
"""

from __future__ import annotations

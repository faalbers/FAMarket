"""
Intrinsic value (Topic 4.1 — go deep here).

  * intrinsic_value_graham — Graham Number from EPS + book value
  * intrinsic_value_lynch  — Peter Lynch Fair Value from EPS + growth rate
  * intrinsic_value_dcf    — simple DCF: FCF history + growth estimates +
                             FRED risk-free rate (from macro.db) + beta
  * margin_of_safety       — current price vs intrinsic value

SKELETON — Phase 2.
"""

from __future__ import annotations

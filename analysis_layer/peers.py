"""
Peer comparison (Topic 4.3) — sector/industry medians.

For each peer-comparable metric, store TWO columns: `<metric>_vs_sector` and
`<metric>_vs_industry`, each the % the symbol sits above/below the peer median.
The median values themselves are NOT stored — only the relative % difference.

Peer-comparable metrics: P/E, Fwd P/E, PEG, profit margins, ROE, ROA, EV/EBITDA,
revenue growth, Debt/Equity. Two grouping levels: Sector (broad), Industry (narrow).

SKELETON — Phase 2.
"""

from __future__ import annotations

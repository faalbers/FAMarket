"""
Dev runner for symbol discovery (Phase 1).

    python -m scripts.discover_symbols            # Polygon (if key) + EDGAR + funds
    python -m scripts.discover_symbols --edgar    # EDGAR operating cos only (no key)
    python -m scripts.discover_symbols --polygon  # Polygon only
    python -m scripts.discover_symbols --funds    # SEC mutual funds only

Inspect the result with the VSCode SQLite viewer on databases/symbols.db, or:
    python -m scripts.discover_symbols --show
"""

from __future__ import annotations

import argparse

from config import settings
from core.database import Database
from core.logging_config import setup_logging
from data_layer import symbols


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(description="Run symbol discovery into symbols.db")
    p.add_argument("--polygon", action="store_true", help="Polygon source only")
    p.add_argument("--edgar", action="store_true", help="EDGAR operating-companies only")
    p.add_argument("--funds", action="store_true", help="SEC mutual-fund tickers only")
    p.add_argument("--show", action="store_true", help="Print a summary and exit")
    args = p.parse_args()

    if args.show:
        _show()
        return

    # Default (no source flag) = all sources; naming any flag limits to those.
    any_flag = args.polygon or args.edgar or args.funds
    use_polygon = args.polygon or not any_flag
    use_edgar = args.edgar or not any_flag
    use_funds = args.funds or not any_flag
    n = symbols.run_discovery(
        use_polygon=use_polygon, use_edgar=use_edgar, use_edgar_funds=use_funds
    )
    print(f"Discovery complete — {n} symbols upserted into {settings.SYMBOLS_DB.name}")
    _show()


def _show() -> None:
    with Database(settings.SYMBOLS_DB) as db:
        if not db.table_exists(symbols.TABLE):
            print("symbols table is empty — run discovery first.")
            return
        total = db.read(symbols.TABLE)
        print(f"\nsymbols.db — {len(total)} rows")
        if "source" in total.columns:
            print("by source:", total["source"].value_counts().to_dict())
        if "security_type" in total.columns:
            top = total["security_type"].value_counts(dropna=False).head(8).to_dict()
            print("by security_type:", top)
        print("sample:", total["symbol"].head(10).tolist())


if __name__ == "__main__":
    main()

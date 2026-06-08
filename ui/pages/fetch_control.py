"""
Fetch Control panel (Topic 8 — FETCH CONTROL PANEL).

Drives the same pipeline as `scripts/run_fetch.py`, from the browser:

  * Group 1 — Symbol Discovery (Polygon + SEC EDGAR), the slow first stage.
  * Group 2 — Data Fetch (yfinance quotes/OHLCV/financials, EDGAR backfill, FRED),
    then is_active/is_validated reassessment.
  * Group 3 — Analysis rebuild (analysis.db), wired into run_full_fetch.

Only the fetchers that are actually wired into the orchestrator are exposed here
(FMP and E*Trade from the original blueprint are deferred and intentionally
absent). A run triggers a rotating backup of every .db first (core.backup).

Streamlit note: the script reruns top-to-bottom on every interaction, and a fetch
is one long call. To show the log *live*, the fetch runs on a background worker
thread (it never touches st.*, so it needs no Streamlit context) while the main
script polls the log file into a placeholder every few seconds until the worker
finishes. The final result is kept in st.session_state so it survives the reruns
caused by other widgets.
"""

from __future__ import annotations

import argparse
import threading
import time

import streamlit as st

from config import settings
from core.database import Database
from data_layer.orchestrator import run_full_fetch

ANALYSIS_META = "analysis_meta"
_LOG_TAIL_LINES = 200
_POLL_SECONDS = 2.0
# Pre-filled when "Dev subset" scope is chosen — the canonical type-spanning set.
_DEFAULT_SUBSET = ["AAPL", "MSFT", "JNJ", "KO", "PG", "O", "SPY", "VOO", "TSM", "VFIAX"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse_subset(raw: str) -> list[str] | None:
    """Comma/space/newline-separated symbols -> upper-cased list, or None for all."""
    parts = [p.strip().upper() for p in raw.replace("\n", ",").replace(" ", ",").split(",")]
    syms = [p for p in parts if p]
    return syms or None


def _cli_subset() -> str | None:
    """Subset passed on the command line, as a display string for the field default.

    Usage: `streamlit run app.py -- --subset AAPL,MSFT` (the `--` hands the rest to
    the script's argv). Comma- or space-separated; None when the flag is absent.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--subset", default=None)
    try:
        args, _ = parser.parse_known_args()
    except SystemExit:  # argparse never exits here (parse_known_args), but stay safe
        return None
    if not args.subset:
        return None
    syms = [p.strip().upper() for p in args.subset.replace(",", " ").split()]
    return ", ".join(syms) or None


def _log_tail(n: int = _LOG_TAIL_LINES) -> str:
    """Last n lines of the run log (empty string when there is no log yet)."""
    path = settings.LOG_FILE
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def _run_worker(result: dict, **kwargs) -> None:
    """Run the pipeline on a background thread; report into the shared `result`.

    Pure backend (no st.* calls), so it needs no Streamlit ScriptRunContext. The
    main thread watches `result["done"]` and renders the log + summary/error.
    """
    try:
        result["summary"] = run_full_fetch(**kwargs)
    except Exception as exc:  # carried back to the page instead of a blank screen
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["done"] = True


def _analysis_meta() -> dict | None:
    """The single analysis_meta row (analyzed_at / prices_as_of / n_symbols)."""
    if not settings.ANALYSIS_DB.exists():
        return None
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists(ANALYSIS_META):
            return None
        meta = db.read(ANALYSIS_META)
    return meta.iloc[-1].to_dict() if not meta.empty else None


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Fetch Control")
st.caption(
    "Run the weekly pipeline: discovery → data fetch → reassessment → analysis. "
    "A rotating backup of every database is taken before the run starts."
)

# -- last analysis snapshot (always shown, top of page) --------------------- #
meta = _analysis_meta()
if meta:
    c1, c2, c3 = st.columns(3)
    c1.metric("Symbols analyzed", int(meta.get("n_symbols", 0)))
    c2.metric("Prices as of", str(meta.get("prices_as_of", "—")))
    c3.metric("Last analyzed", str(meta.get("analyzed_at", "—")))
else:
    st.info("No analysis run yet — run a fetch below (or `python -m scripts.run_fetch`).")

st.divider()

# -- run configuration ------------------------------------------------------ #
# Note: not wrapped in st.form so the scope radio can enable/disable the subset
# box live (form widgets only take effect on submit, which would freeze the toggle).
st.subheader("Group 1 — Symbol Discovery")
discover = st.checkbox(
    "Run discovery (Polygon + SEC EDGAR)",
    value=False,
    help="Populates symbols.db. Slow (~6 min on Polygon's free 5 req/min tier); "
    "leave off to fetch/analyse the symbols already discovered.",
)

st.subheader("Group 2 — Data Fetch")
st.markdown(
    "- **yfinance** — quotes, OHLCV, financials\n"
    "- **SEC EDGAR** — deep-history financials backfill\n"
    "- **FRED** — macro series (Treasury, Fed funds, CPI, GDP)\n\n"
    "These run as one orchestrated sequence; selective per-fetcher re-runs are a "
    "later addition."
)

st.subheader("Options")
scope = st.radio(
    "Run scope", ["Dev subset", "Full universe"], index=0, horizontal=True,
    help="Dev subset limits Group 2 + analysis to the symbols below. Full universe "
    "processes every symbol in symbols.db — much longer.",
)
subset_raw = st.text_input(
    "Subset symbols (comma-separated)",
    value=_cli_subset() or ", ".join(_DEFAULT_SUBSET),
    disabled=(scope == "Full universe"),
    help="Used only when scope is Dev subset. Prefill from the CLI with "
    "`streamlit run app.py -- --subset AAPL,MSFT`. Discovery still populates the full symbols.db.",
)
col_a, col_b = st.columns(2)
respect_lock = col_a.checkbox(
    "Respect 5-day fetch lock", value=True,
    help="Skip (symbol, fetcher) pairs fetched within the last 5 days.",
)
run_backup = col_b.checkbox(
    "Back up databases first", value=True,
    help="Rotating 5-version backup of every .db before the run.",
)

submitted = st.button("▶ Run Fetch", type="primary")

# -- run (background thread + live log tail) -------------------------------- #
if submitted:
    if scope == "Full universe":
        subset = None
    else:
        subset = _parse_subset(subset_raw)
        if subset is None:  # subset scope but the box was cleared — don't run everything
            st.warning("Dev subset selected but no symbols entered. Add symbols or switch to Full universe.")
            st.stop()
    label = f"{len(subset)} symbols" if subset else "full universe"
    result: dict = {}
    worker = threading.Thread(
        target=_run_worker,
        kwargs={
            "result": result, "discover": discover, "subset": subset,
            "respect_lock": respect_lock, "run_backup": run_backup,
        },
        daemon=True,
    )
    worker.start()

    status = st.status(f"Running pipeline ({label})…", expanded=True)
    log_box = status.empty()
    while not result.get("done"):  # main thread is free to repaint while it runs
        log_box.code(_log_tail() or "(starting…)", language="log")
        time.sleep(_POLL_SECONDS)
    log_box.code(_log_tail() or "(no log output)", language="log")

    if result.get("error"):
        status.update(label="Fetch failed", state="error")
        st.session_state["fetch_summary"] = None
        st.session_state["fetch_error"] = result["error"]
    else:
        status.update(label="Fetch complete", state="complete")
        st.session_state["fetch_summary"] = result.get("summary")
        st.session_state["fetch_error"] = None
    st.rerun()  # repaint with the fresh analysis snapshot + persisted summary

# -- results of the most recent run (persist across reruns) ----------------- #
if st.session_state.get("fetch_error"):
    st.error(f"Fetch failed — {st.session_state['fetch_error']}")
elif st.session_state.get("fetch_summary") is not None:
    st.success("Fetch complete.")
    st.json(st.session_state["fetch_summary"])

tail = _log_tail()
if tail:
    with st.expander("Run log (tail)", expanded=bool(submitted)):
        st.code(tail, language="log")

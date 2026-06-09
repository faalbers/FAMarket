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
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from config import settings
from core.database import Database
from data_layer import cancel
from data_layer.orchestrator import run_full_fetch

ANALYSIS_META = "analysis_meta"
_LOG_TAIL_LINES = 200
_POLL_SECONDS = 2.0
# Shown as a greyed-out placeholder hint in the subset box (never prefilled as a
# value) — the canonical type-spanning set for dev runs.
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


def _fmt_local(value) -> str:
    """A stored UTC ISO timestamp shown in the machine's local time.

    `analyzed_at` is recorded as UTC (offset-aware ISO); convert it to local time
    for display only — storage stays UTC. Returns the raw value unchanged if it
    isn't a parseable timestamp, and "—" when missing.
    """
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value)
    if dt.tzinfo is None:  # legacy/naive value — treat as the UTC it was stored in
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _stat(col, label: str, value: object) -> None:
    """A compact label/value stat — much smaller value text than st.metric."""
    col.markdown(
        "<div style='line-height:1.3'>"
        "<div style='font-size:0.72rem;color:rgba(128,128,128,0.95);"
        "text-transform:uppercase;letter-spacing:0.03em'>"
        f"{label}</div>"
        f"<div style='font-size:0.95rem;font-weight:600'>{value}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def _flatten(d: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Flatten a nested run-summary into (dotted-key, value) rows.

    The orchestrator returns a dict-of-dicts (one inner dict per fetcher/stage,
    plus scalar flags like `cancelled`). Flatten it so the page can show a plain
    parameter -> value list instead of a raw nested-dict widget.
    """
    rows: list[tuple[str, object]] = []
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            rows.extend(_flatten(v, f"{key}."))
        else:
            rows.append((key, v))
    return rows


def _show_summary(summary: dict) -> None:
    """Render a run summary as a flat Parameter/Value table (not a nested dict)."""
    rows = _flatten(summary)
    if not rows:
        st.caption("No run details reported.")
        return
    df = pd.DataFrame(rows, columns=["Parameter", "Value"])
    df["Value"] = df["Value"].map(lambda v: "" if v is None else str(v))
    st.dataframe(df, hide_index=True, use_container_width=True)


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
    _stat(c1, "Symbols analyzed", int(meta.get("n_symbols", 0)))
    _stat(c2, "Prices as of", str(meta.get("prices_as_of", "—")))
    _stat(c3, "Last analyzed", _fmt_local(meta.get("analyzed_at")))
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
# Default scope: "Full universe" unless a --subset was passed on the CLI, in which
# case start on "Dev subset" so the prefilled symbols are actually used.
scope = st.radio(
    "Run scope", ["Dev subset", "Full universe"],
    index=0 if _cli_subset() else 1, horizontal=True,
    help="Dev subset limits Group 2 + analysis to the symbols below. Full universe "
    "processes every symbol in symbols.db — much longer.",
)
subset_raw = st.text_input(
    "Subset symbols (comma-separated)",
    value=_cli_subset() or "",
    placeholder=", ".join(_DEFAULT_SUBSET),
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

running = st.session_state.get("fetch_running", False)
run_col, stop_col = st.columns(2)
submitted = run_col.button("▶ Run Fetch", type="primary", disabled=running)
# Stop is a cooperative request: the worker keeps running until it reaches the
# next safe boundary (between fetchers / between batches), where every completed
# batch is already committed, then unwinds. So it's enabled only while a run is live.
stop_clicked = stop_col.button("■ Stop Fetch", disabled=not running)

# -- start a run ------------------------------------------------------------ #
# The worker runs on a daemon thread and the page does NOT block waiting for it:
# each script run renders the log tail, then schedules a rerun a couple of seconds
# later. That keeps the script free to process the Stop button between polls — a
# blocking wait loop would freeze the page and the Stop click would never land.
if submitted:
    if scope == "Full universe":
        subset = None
    else:
        subset = _parse_subset(subset_raw)
        if subset is None:  # subset scope but the box was cleared — don't run everything
            st.warning("Dev subset selected but no symbols entered. Add symbols or switch to Full universe.")
            st.stop()
    label = f"{len(subset)} symbols" if subset else "full universe"
    cancel.clear()  # drop any leftover stop before the worker checks it
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
    st.session_state["fetch_running"] = True
    st.session_state["fetch_stopping"] = False
    st.session_state["fetch_result"] = result
    st.session_state["fetch_label"] = label
    st.session_state["fetch_summary"] = None
    st.session_state["fetch_error"] = None
    st.rerun()

# -- request a stop (honoured at the next safe boundary) -------------------- #
if stop_clicked and running:
    cancel.request_cancel()
    st.session_state["fetch_stopping"] = True

# -- poll a live run (non-blocking) ----------------------------------------- #
# The poll sleep is deferred to the very END of the script (see _poll_again
# below), NOT taken here. Streamlit streams element deltas as the script runs and
# only prunes a previous frame's elements once the run advances past their slot
# (or finishes). A blocking sleep *here* — above the results section — froze the
# script with the PREVIOUS run's summary still painted below, so a stopped run's
# dict lingered on screen for the whole next run. Rendering the (now-empty)
# results slot before sleeping prunes it immediately.
_poll_again = False
if st.session_state.get("fetch_running"):
    result = st.session_state.get("fetch_result", {})
    label = st.session_state.get("fetch_label", "")
    stopping = st.session_state.get("fetch_stopping", False)
    verb = "Stopping" if stopping else "Running"
    status = st.status(f"{verb} pipeline ({label})…", expanded=True)
    if stopping:
        status.write("Stop requested — finishing the current batch (already committed), then unwinding.")
    status.empty().code(_log_tail(10) or "(starting…)", language="log")

    if result.get("done"):
        st.session_state["fetch_running"] = False
        st.session_state["fetch_stopping"] = False
        summary = result.get("summary")
        if result.get("error"):
            status.update(label="Fetch failed", state="error")
            st.session_state["fetch_summary"] = None
            st.session_state["fetch_error"] = result["error"]
        elif isinstance(summary, dict) and summary.get("cancelled"):
            status.update(label="Fetch stopped", state="error")
            st.session_state["fetch_summary"] = summary
            st.session_state["fetch_error"] = None
        else:
            status.update(label="Fetch complete", state="complete")
            st.session_state["fetch_summary"] = summary
            st.session_state["fetch_error"] = None
        st.rerun()  # repaint with the fresh analysis snapshot + persisted summary
    else:
        _poll_again = True  # render the whole page first, then sleep at the end

# -- results of the most recent run (persist across reruns) ----------------- #
# While a run is live, fetch_summary is None (cleared at submit), so this slot
# renders nothing — which prunes any prior run's summary instead of leaving it on
# screen under the new run. Shown as a flat Parameter/Value list, not a raw dict.
_last_summary = st.session_state.get("fetch_summary")
if st.session_state.get("fetch_error"):
    st.error(f"Fetch failed — {st.session_state['fetch_error']}")
elif isinstance(_last_summary, dict) and _last_summary.get("cancelled"):
    st.warning(
        "Fetch stopped before completing. All fetched batches are committed and the "
        "run is resumable — re-run to continue. Analysis was not rebuilt."
    )
    _show_summary(_last_summary)
elif _last_summary is not None:
    st.success("Fetch complete.")
    _show_summary(_last_summary)

# -- danger zone: reset to a clean slate ------------------------------------ #
# Kept at the very bottom, collapsed, behind a two-step confirm (checkbox + button)
# so it's never triggered by accident. Versioned-backs-up the current DBs and log
# first, then deletes the live databases; backups + logs are preserved (recoverable).
st.divider()
with st.expander("⚠️ Danger zone — reset all data"):
    st.warning(
        "Takes a versioned backup of the current databases **and** the log (rotating, "
        "same as a normal run), then **deletes the live databases**. Backups and logs "
        "are kept, so this is recoverable — but the working dataset is gone and the "
        "next run becomes a full initial load."
    )
    # A fresh checkbox key after each reset (nonce) so it returns to unchecked.
    _nonce = st.session_state.get("reset_nonce", 0)
    confirm_reset = st.checkbox(
        "I understand — back up databases + log, then delete all databases.",
        key=f"reset_confirm_{_nonce}",
    )
    reset_clicked = st.button(
        "🗑 Reset all data",
        type="secondary",
        disabled=running or not confirm_reset,
        help="Disabled while a fetch is running. Close the VSCode SQLite viewer first, "
        "or locked .db files can't be deleted.",
    )
    if reset_clicked and confirm_reset and not running:
        from core.reset import reset_all_data

        res = reset_all_data()
        # Clear persisted run state so the page reflects the clean slate.
        for k in ("fetch_summary", "fetch_error", "fetch_result", "fetch_label"):
            st.session_state.pop(k, None)
        st.session_state["reset_nonce"] = _nonce + 1  # reset the confirm checkbox
        st.session_state["reset_result"] = res
        st.rerun()

# Reset outcome (shown once, after the rerun).
_reset = st.session_state.pop("reset_result", None)
if _reset is not None:
    if _reset["failed"]:
        st.error(
            f"Reset incomplete — deleted {len(_reset['deleted'])} database file(s), but these "
            "were locked (close the SQLite viewer and retry):\n\n- "
            + "\n- ".join(_reset["errors"])
        )
    else:
        st.success(
            f"Reset complete — versioned-backed-up the databases and log, then deleted "
            f"{len(_reset['deleted'])} database file(s). Backups and logs are preserved."
        )

# -- deferred poll tick (must be the LAST thing in the script) -------------- #
# The whole page — including the now-empty results slot above — has rendered, so
# the previous run's summary is already pruned from the frame. Only now is it safe
# to block on the poll interval and rerun; sleeping any earlier would freeze the
# script with stale content still on screen.
if _poll_again:
    time.sleep(_POLL_SECONDS)
    st.rerun()

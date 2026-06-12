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
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from analysis_layer.pipeline import run_analysis
from config import settings
from core.database import Database
from core.logging_config import get_logger, roll_log
from data_layer import cancel
from data_layer.orchestrator import report_fetch, run_full_fetch

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
    # Register this thread so app shutdown (tab close) can stop it gracefully.
    cancel.set_worker(threading.current_thread())
    try:
        result["summary"] = run_full_fetch(**kwargs)
    except Exception as exc:  # carried back to the page instead of a blank screen
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["done"] = True


def _run_analysis_worker(result: dict, subset: list[str] | None) -> None:
    """TEMPORARY (dev): analysis-only run — rebuild analysis.db, no fetch.

    Same worker contract as `_run_worker` (no st.* calls, reports into `result`)
    so it plugs into the same live-status fragment. The summary is wrapped under
    an "analysis" key to match the shape `run_full_fetch` returns.
    """
    cancel.set_worker(threading.current_thread())
    try:
        # Fresh log for this run (previous one archived), same as the orchestrator
        # does at the top of a fetch — otherwise the live tail shows the OLD log.
        roll_log()
        result["summary"] = {"analysis": run_analysis(subset=subset)}
    except Exception as exc:
        # Log the full traceback too — the UI banner only shows type+message,
        # and run_full_fetch logs its own failures; this path must as well.
        get_logger("analysis").exception(
            "Analysis-only run failed — %s: %s", type(exc).__name__, exc
        )
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["done"] = True


@st.fragment(run_every=_POLL_SECONDS)
def _live_status() -> None:
    """Live status + log tail for an in-flight run, on its own refresh timer.

    A fragment — NOT a `time.sleep + st.rerun` loop on the main script. Only THIS
    block reruns every _POLL_SECONDS; the rest of the page stays idle and fully
    responsive, so Stop and the "Analyze after stop" checkbox are never dropped
    (the old blocking sleep froze the script between ticks and ate those clicks).
    On completion it triggers a full-app rerun to repaint the results + snapshot.
    """
    result = st.session_state.get("fetch_result", {})
    label = st.session_state.get("fetch_label", "")
    stopping = st.session_state.get("fetch_stopping", False)
    verb = "Stopping" if stopping else "Running"
    status = st.status(f"{verb} pipeline ({label})…", expanded=True)
    if stopping:
        _tail = (
            " then reassessing the fetched symbols and rebuilding analysis from the "
            "data fetched so far."
            if st.session_state.get("analyze_after_stop", True)
            else " then reassessing the fetched symbols (analysis skipped)."
        )
        status.write(
            "Stop requested — finishing the current batch (already committed)," + _tail
        )
    status.empty().code(_log_tail(10) or "(starting…)", language="log")

    if not result.get("done"):
        return
    # Finished — persist the outcome, then repaint the whole page.
    st.session_state["fetch_running"] = False
    st.session_state["fetch_stopping"] = False
    if result.get("error"):
        st.session_state["fetch_summary"] = None
        st.session_state["fetch_error"] = result["error"]
    else:
        st.session_state["fetch_summary"] = result.get("summary")
        st.session_state["fetch_error"] = None
    st.rerun(scope="app")  # repaint the whole page: results section + fresh snapshot


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
    st.dataframe(df, hide_index=True, width="stretch")


@st.dialog("Next fetch — gate report", width="large")
def _show_report(report: dict) -> None:
    """Modal report of how many symbols each Group 2 step would fetch right now.

    Dismissed by clicking outside / Esc / the ✕ (standard st.dialog). Pure display
    of the dict returned by `orchestrator.report_fetch` — no fetching happened.
    """
    universe = report.get("universe", 0)
    respect_lock = report.get("respect_lock", True)
    abandonment_on = report.get("abandonment_enabled", True)
    lock_days = report.get("lock_days", settings.FETCH_LOCK_DAYS)
    st.markdown(f"**Fetch universe:** {universe:,} symbols  ·  *(active, non-index)*")
    rows = [
        {
            "Step": s["step"],
            "Candidates": s["candidates"],
            "Locked": s["locked"],
            "Abandoned": s["abandoned"],
            "Stale": s["stale"],
            "Not due": s["not_due"],
            "Will fetch": s["due"],
        }
        for s in report.get("steps", [])
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    # The cadence lock and the viability gates are independent switches — report each.
    if not respect_lock:
        st.info(
            f"{lock_days}-day lock is **off** — symbols inside the cadence window are "
            "refetched. The viability gates (abandonment / staleness / due-date) still apply."
        )
    if not abandonment_on:
        st.warning(
            "Viability gates are **off** (`FETCH_ABANDONMENT_ENABLED`) — abandoned, "
            "stale and not-due symbols are fetched too. Combine with the lock off "
            "for a full refetch of everything."
        )
    st.caption(
        "- **Candidates** — symbols of the types this step handles\n"
        f"- **Locked** — skipped by the {lock_days}-day cadence lock (the lock checkbox)\n"
        "- **Abandoned** — hit the no-data cap (a viability gate)\n"
        "- **Stale** — data too old to keep fetching (a viability gate)\n"
        "- **Not due** — next statement can't exist yet, financials only (a viability gate)\n"
        "- **Will fetch** — what this step would actually fetch now\n\n"
        "The lock checkbox toggles only **Locked**; the viability gates "
        "(Abandoned / Stale / Not due) are governed by `FETCH_ABANDONMENT_ENABLED` "
        "on the Settings page. FRED macro series always run (not symbol-gated). "
        "OHLCV/financials counts use the currently stored security types — quotes "
        "resolves types first in a real run, so they can shift slightly."
    )


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
# Note: not wrapped in st.form so the scope radio can show/hide the subset
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
    "Run scope", ["Full universe", "Dev subset"],
    index=1 if _cli_subset() else 0, horizontal=True,
    help="Full universe processes every symbol in symbols.db (clean-slate analysis "
    "rebuild) — much longer. Dev subset limits Group 2 + analysis to the symbols you "
    "enter; their analysis rows are updated in place and every other symbol's row is kept.",
)
# The subset box only exists while Dev subset is selected (hidden, not just
# disabled, on Full universe — where it would be ignored anyway).
subset_raw = ""
if scope == "Dev subset":
    subset_raw = st.text_input(
        "Subset symbols (comma-separated)",
        value=_cli_subset() or "",
        placeholder=", ".join(_DEFAULT_SUBSET),
        help="Prefill from the CLI with `streamlit run app.py -- --subset AAPL,MSFT`. "
        "Discovery still populates the full symbols.db.",
    )
col_a, col_b = st.columns(2)
respect_lock = col_a.checkbox(
    f"Respect {settings.FETCH_LOCK_DAYS}-day fetch lock", value=True,
    help=f"Skip (symbol, fetcher) pairs fetched within the last "
    f"{settings.FETCH_LOCK_DAYS} days (set by FETCH_LOCK_DAYS on the Settings page). "
    "This controls ONLY the cadence lock — the viability gates (abandonment, "
    "staleness, financials due-date) stay active and are governed separately by "
    "FETCH_ABANDONMENT_ENABLED on the Settings page. For a full refetch of "
    "everything, turn both off.",
)
run_backup = col_b.checkbox(
    "Back up databases first", value=True,
    help="Rotating 5-version backup of every .db before the run.",
)

running = st.session_state.get("fetch_running", False)

# Row 1: Run Fetch | Stop Fetch | Analyze after stop. (Stop's button is created
# further down — after its on_click callback is defined — but into this row's
# column, so it lands here alongside Run Fetch.)
run_col, stop_col, opt_col = st.columns(
    [1, 1, 1.4], vertical_alignment="center"
)
submitted = run_col.button("▶ Run Fetch", type="primary", disabled=running)

# Decided at Stop time, not run-start: the on_click callback reads this checkbox's
# live value, so toggling it during a run is honoured. On by default so a stopped
# run still yields fresh analysis over the data fetched so far.
opt_col.checkbox(
    "Analyze after stop",
    value=True,
    key="analyze_after_stop",
    help="When you Stop a run, still rebuild analysis.db from the data fetched so "
    "far. Off leaves the existing analysis untouched.",
)

# Row 2: Report Fetch — a dry run of the Group 2 gates, directly below Run Fetch.
# Same style/icon as the others; opens a dismissible modal with the per-step counts,
# fetches nothing.
report_col = st.columns([1, 2.4])[0]
report_clicked = report_col.button(
    "▶ Report Fetch",
    type="primary",
    disabled=running,
    help="Preview the next fetch: how many symbols each step would fetch right now "
    "given the current gate options and symbol states. No data is fetched.",
)

# Row 3: Run Analysis on its own line. Same style/icon as Run Fetch (primary ▶) so
# the run actions read as a matching set.
# TEMPORARY (dev): rebuild analysis.db from the already-fetched data, no fetch.
# Honors the same Dev subset / Full universe scope. Remove once development settles.
analyze_col = st.columns([1, 2.4])[0]
analyze_clicked = analyze_col.button(
    "▶ Run Analysis",
    type="primary",
    disabled=running,
    help="TEMPORARY dev button — analysis rebuild only, no fetch. Uses the run "
    "scope above. Note: Stop does not interrupt an analysis run.",
)


def _request_stop() -> None:
    """Set the cancel flag the instant the click is processed (on_click callback).

    A callback (not a `stop_clicked = st.button(...)` return-value check) so the stop
    is registered the moment Streamlit handles the click, before any rerun. Pairs with
    the self-refreshing status fragment: because the main script no longer blocks in a
    sleep loop, this click is processed promptly even mid-run. It also carries the
    live "Analyze after stop" choice into the stop request.
    """
    cancel.request_cancel(analyze_after=st.session_state.get("analyze_after_stop", True))
    st.session_state["fetch_stopping"] = True


# Stop is a cooperative request: the worker keeps running until it reaches the
# next safe boundary (between fetchers / between batches), where every completed
# batch is already committed, then unwinds. So it's enabled only while a run is live.
stop_col.button("■ Stop Fetch", disabled=not running, on_click=_request_stop)

# -- report fetch (dry run of the Group 2 gates) ---------------------------- #
# Computes inline (no worker) and pops a dismissible modal — it only reads the DBs,
# never the network, so it's quick enough to block for. Uses the SAME scope + lock
# options as a real run so the preview matches what Run Fetch would do.
if report_clicked:
    if scope == "Dev subset" and _parse_subset(subset_raw) is None:
        st.warning("Dev subset selected but no symbols entered. Add symbols or switch to Full universe.")
    else:
        report_subset = None if scope == "Full universe" else _parse_subset(subset_raw)
        with st.spinner("Checking fetch gates…"):
            _report = report_fetch(subset=report_subset, respect_lock=respect_lock)
        _show_report(_report)

# -- start a run ------------------------------------------------------------ #
# The worker runs on a daemon thread and the page does NOT block waiting for it:
# each script run renders the log tail, then schedules a rerun a couple of seconds
# later. That keeps the script free to process the Stop button between polls — a
# blocking wait loop would freeze the page and the Stop click would never land.
if submitted or analyze_clicked:
    if scope == "Full universe":
        subset = None
    else:
        subset = _parse_subset(subset_raw)
        if subset is None:  # subset scope but the box was cleared — don't run everything
            st.warning("Dev subset selected but no symbols entered. Add symbols or switch to Full universe.")
            st.stop()
    scope_label = f"{len(subset)} symbols" if subset else "full universe"
    cancel.clear()  # drop any leftover stop before the worker checks it
    result: dict = {}
    if analyze_clicked:
        label = f"analysis only, {scope_label}"
        worker = threading.Thread(
            target=_run_analysis_worker,
            kwargs={"result": result, "subset": subset},
            daemon=True,
        )
    else:
        label = scope_label
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

# Stop requests are handled in the _request_stop on_click callback (above). With the
# main script no longer blocking in a sleep loop, that click is processed promptly.

# -- live status for an in-flight run (self-refreshing fragment) ------------ #
# Only the fragment reruns on its timer; the main page stays idle between ticks, so
# Stop / "Analyze after stop" are never dropped. The fragment finalizes the run.
if st.session_state.get("fetch_running"):
    _live_status()

# -- results of the most recent run (persist across reruns) ----------------- #
# While a run is live, fetch_summary is None (cleared at submit), so this slot
# renders nothing — which prunes any prior run's summary instead of leaving it on
# screen under the new run. Shown as a flat Parameter/Value list, not a raw dict.
_last_summary = st.session_state.get("fetch_summary")
if st.session_state.get("fetch_error"):
    st.error(f"Fetch failed — {st.session_state['fetch_error']}")
elif isinstance(_last_summary, dict) and _last_summary.get("cancelled"):
    _analysed = _last_summary.get("analysis") is not None
    st.warning(
        "Fetch stopped before completing. All fetched batches are committed and the "
        "run is resumable — re-run to continue. The fetched symbols were reassessed. "
        + (
            "Analysis was rebuilt from the data fetched so far."
            if _analysed
            else "Analysis was not rebuilt."
        )
    )
    _show_summary(_last_summary)
elif _last_summary is not None:
    st.success("Fetch complete.")
    _show_summary(_last_summary)

# -- danger zone (one collapsible holding every destructive action) --------- #
# Kept at the very bottom, collapsed, with each action behind its own two-step
# confirm (checkbox + button) so nothing fires by accident. The action outcomes
# render BELOW the expander so they stay visible after the action collapses it.
st.divider()
with st.expander("⚠️ Danger zone"):
    # Each action lives in its OWN popover, so the whole thing — the info, the
    # "I understand" confirm, and the button — stays collapsed until you open it.
    # (Popover, not a second expander: Streamlit forbids expander-in-expander.)
    # Both popovers are disabled outright while a fetch is running, so neither
    # destructive action can even be opened mid-run (the inner buttons + handlers
    # also re-check `running` as defense in depth).
    if running:
        st.caption("Disabled while a fetch is running.")

    # --- Reset all data --------------------------------------------------- #
    with st.popover("🗑 Reset all data", disabled=running):
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
            "Delete the databases now",
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

    # --- Revert databases to a backup snapshot ---------------------------- #
    # Overwrites the live .db files (databases only — never the log) with a chosen
    # dated backup snapshot; the current DBs are copied to backups/pre_restore first.
    with st.popover("↩ Revert databases to a backup snapshot", disabled=running):
        from core.restore import list_snapshots

        _snaps = list_snapshots()
        st.warning(
            "Overwrites the live **databases** (not the log) with a previous dated "
            "backup — one is taken before every fetch run. The current databases are "
            "copied to `backups/pre_restore` first, so a wrong choice is undoable. Close "
            "the VSCode SQLite viewer first, or locked .db files can't be overwritten."
        )
        if not _snaps:
            st.info("No backups yet — a backup is taken before each fetch run.")
        else:
            _labels = {
                f"{s['saved_at']} ({s['count']} databases)": s["stamp"] for s in _snaps
            }
            _choice = st.selectbox(
                "Backup snapshot to restore (newest first)", list(_labels.keys())
            )
            _sel_stamp = _labels[_choice]
            _sel_saved_at = next(s["saved_at"] for s in _snaps if s["stamp"] == _sel_stamp)
            # Fresh checkbox key after each revert (nonce) so it returns to unchecked.
            _rnonce = st.session_state.get("revert_nonce", 0)
            confirm_revert = st.checkbox(
                "I understand — overwrite the live databases with this backup snapshot.",
                key=f"revert_confirm_{_rnonce}",
            )
            revert_clicked = st.button(
                "Revert to selected snapshot",
                type="secondary",
                disabled=running or not confirm_revert,
                help="Disabled while a fetch is running. Databases only — the log is left "
                "untouched.",
            )
            if revert_clicked and confirm_revert and not running:
                from core.restore import restore_snapshot

                res = restore_snapshot(_sel_stamp)
                # Clear persisted run state so the page reflects the reverted databases.
                for k in ("fetch_summary", "fetch_error", "fetch_result", "fetch_label"):
                    st.session_state.pop(k, None)
                st.session_state["revert_nonce"] = _rnonce + 1  # reset the confirm checkbox
                st.session_state["revert_result"] = (_sel_saved_at, res)
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

# Revert outcome (shown once, after the rerun).
_revert = st.session_state.pop("revert_result", None)
if _revert is not None:
    _when, _res = _revert
    _restored = ", ".join(_res["restored"]) or "none"
    if _res["failed"]:
        st.error(
            f"Revert to {_when} incomplete — restored {len(_res['restored'])} "
            "database(s), but these were locked (close the SQLite viewer and retry):\n\n- "
            + "\n- ".join(_res["errors"])
        )
    else:
        _msg = (
            f"Reverted to backup {_when} — restored {len(_res['restored'])} "
            f"database(s): {_restored}. The previous databases were saved to "
            "`backups/pre_restore` (undo point)."
        )
        if _res["missing"]:
            _msg += f" No backup in this snapshot for: {', '.join(_res['missing'])} (left as-is)."
        st.success(_msg)

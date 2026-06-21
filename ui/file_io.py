"""Native OS file dialogs (Open / Save) — the app's official file chooser.

DECISION (2026-06-21): FAMarket is a local, single-user, this-machine-only app, so we
use real OS dialogs (tkinter) instead of the browser's upload/download. This lets a
dialog start in a chosen folder (e.g. the filters library) and lets Save write
straight there — neither of which a browser file chooser can do. The tradeoff: it
only works when the Streamlit server and the user share one desktop. If the app is
ever moved to a remote/cloud host or opened from another device, these dialogs won't
appear and a browser-based chooser would be needed instead (see git history for the
browser version that was prototyped).

tkinter must run on the main thread, but Streamlit runs the script on a worker
thread, so we run the dialog in a SHORT-LIVED SEPARATE PROCESS and read the chosen
path back over stdout. That side-steps the threading crash/freeze entirely. The
dialog blocks until the user picks or cancels (fine for a single user).

Pages import this as `from ui import file_io as FIO`. The reusable primitives are
`ask_open_path()` / `ask_save_path()`; callers do their own read/write so each file
type owns its (de)serialisation (e.g. ui/filter_engine for .filt).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import streamlit as st

# (label, pattern) pairs for the dialog's file-type filter, e.g. ("Filter files", "*.filt").
FileTypes = Sequence[tuple[str, str]]

# A tiny program run in its own process so tkinter gets a clean main thread there.
# All options arrive as one JSON argv; the chosen path comes back on stdout.
_DIALOG_SRC = r"""
import sys, json, tkinter as tk
from tkinter import filedialog
opts = json.loads(sys.argv[1])
mode = opts.pop("mode")
root = tk.Tk(); root.withdraw()
root.attributes("-topmost", True)  # surface above other windows where the OS allows
fn = filedialog.asksaveasfilename if mode == "save" else filedialog.askopenfilename
path = fn(**opts)
root.destroy()
sys.stdout.write(path or "")
"""


def _run_dialog(opts: dict) -> Path | None:
    """Spawn the dialog process and return the chosen Path, or None if cancelled."""
    try:
        res = subprocess.run(
            [sys.executable, "-c", _DIALOG_SRC, json.dumps(opts)],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        st.error("File dialog timed out.")
        return None
    if res.returncode != 0:
        st.error(f"File dialog failed: {res.stderr.strip() or 'unknown error'}")
        return None
    chosen = res.stdout.strip()
    return Path(chosen) if chosen else None


def _opts(mode: str, *, initialdir, filetypes, title, extra: dict | None = None) -> dict:
    opts: dict[str, Any] = {"mode": mode, "title": title}
    if initialdir is not None:
        opts["initialdir"] = str(initialdir)
    if filetypes:
        opts["filetypes"] = [list(ft) for ft in filetypes]  # tk accepts sequences
    if extra:
        opts.update(extra)
    return opts


def ask_open_path(
    *,
    initialdir: Path | str | None = None,
    filetypes: FileTypes | None = None,
    title: str = "Open",
) -> Path | None:
    """Pop a native Open dialog; return the chosen path or None if cancelled."""
    return _run_dialog(_opts("open", initialdir=initialdir, filetypes=filetypes, title=title))


def ask_save_path(
    *,
    initialdir: Path | str | None = None,
    default_name: str = "",
    defaultextension: str = "",
    filetypes: FileTypes | None = None,
    title: str = "Save",
) -> Path | None:
    """Pop a native Save-As dialog; return the chosen path or None if cancelled.
    The OS appends `defaultextension` (e.g. ".filt") when the typed name has no suffix.
    """
    extra: dict[str, Any] = {}
    if default_name:
        extra["initialfile"] = default_name
    if defaultextension:
        extra["defaultextension"] = defaultextension
    return _run_dialog(
        _opts("save", initialdir=initialdir, filetypes=filetypes, title=title, extra=extra)
    )

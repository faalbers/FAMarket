"""
Out-of-process native file dialog (tkinter) — the same approach as
`ui/file_io.py`, kept because the app is local and single-user: a native dialog
can start in `filters/` or `selections/` and save straight there, which a
browser file picker cannot.

Runs as a child process so a tkinter mainloop never lives inside the server
process. Prints the chosen path to stdout ("" if cancelled).

    python -m api.file_dialog_child open --initial-dir C:/x --ext .filt
    python -m api.file_dialog_child save --initial-dir C:/x --ext .filt

`--fake-path P --delay-ms N` skips tkinter and prints P after N ms — used by
headless checks to exercise the endpoint plumbing without popping a dialog.
(A tkinter `after` auto-close does NOT work here: on Windows the native modal
dialog blocks the Tcl event loop, so scheduled callbacks never fire.)
"""

from __future__ import annotations

import argparse
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["open", "save"])
    parser.add_argument("--initial-dir", default="")
    parser.add_argument("--initial-file", default="")
    parser.add_argument("--ext", default="")
    parser.add_argument("--title", default="Select file")
    parser.add_argument("--fake-path", default="")
    parser.add_argument("--delay-ms", type=int, default=0)
    args = parser.parse_args()

    if args.fake_path:
        time.sleep(args.delay_ms / 1000)
        print(args.fake_path)
        return

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    # Bring the dialog to the front of every window, including the browser.
    root.attributes("-topmost", True)

    filetypes = [("All files", "*.*")]
    if args.ext:
        filetypes.insert(0, (f"{args.ext} files", f"*{args.ext}"))

    if args.mode == "open":
        path = filedialog.askopenfilename(
            parent=root,
            title=args.title,
            filetypes=filetypes,
            initialdir=args.initial_dir or None,
            initialfile=args.initial_file or None,
        )
    else:
        path = filedialog.asksaveasfilename(
            parent=root,
            title=args.title,
            filetypes=filetypes,
            initialdir=args.initial_dir or None,
            initialfile=args.initial_file or None,
            defaultextension=args.ext or None,
        )

    print(path or "")


if __name__ == "__main__":
    main()

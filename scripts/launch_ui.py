"""Desktop-icon entry point for the React UI.

Rebuilds the frontend **only when it is out of date**, then hands over to
`serve_ui.py`.

`frontend/dist` is a build artifact and is not in the repo, so what is on disk
is only as new as the last `npm run build`. A stale bundle is the worst kind of
wrong -- the app comes up, looks perfectly fine, and silently predates the
change you meant to look at.

Building every time would fix that and charge a few seconds plus a hard
dependency on npm to every launch, for a problem that only exists while the
frontend is being edited. So the mtimes decide: nothing touched, nothing built,
and the launch costs a directory walk. This is the only thing this file adds
over pointing the icon straight at `serve_ui.py`.

**A failed build is not fatal if there is something to serve.** npm missing, a
half-finished edit mid-save -- the previous bundle is still better than no app
at all, so it warns and carries on. Only a failed build with no existing
`dist` stops it, because then there is genuinely nothing to serve.

Run through python.exe rather than pythonw.exe: the console window is wanted,
so warnings and the uvicorn log stay visible.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = PROJECT_ROOT / "frontend"
DIST = FRONTEND / "dist"

os.chdir(PROJECT_ROOT)


#: Everything the built bundle is derived from. The configs and the lockfile
#: count as much as `src` does -- a dependency bump or a changed Vite setting
#: rewrites the output without a single source file being touched. `public` is
#: copied into `dist` verbatim, so it counts too.
WATCHED = ("src", "public", "index.html", "vite.config.ts", "package.json", "package-lock.json")
WATCHED_GLOBS = ("tsconfig*.json",)


def _newest_source_mtime() -> float:
    """The most recent mtime across everything a build reads."""
    newest = 0.0
    candidates = [FRONTEND / name for name in WATCHED]
    for pattern in WATCHED_GLOBS:
        candidates.extend(FRONTEND.glob(pattern))

    for path in candidates:
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    newest = max(newest, child.stat().st_mtime)
        elif path.is_file():
            newest = max(newest, path.stat().st_mtime)
    return newest


def needs_rebuild() -> bool:
    """Whether the bundle on disk predates the sources it was built from.

    `dist/index.html` is the stamp rather than the directory's own mtime: Vite
    rewrites the file every build, while a directory's timestamp says only when
    something was last added to it.

    Missing entirely counts as stale -- `dist` is gitignored, so a fresh clone
    has none and the first launch has to build.
    """
    stamp = DIST / "index.html"
    if not stamp.is_file():
        return True
    return _newest_source_mtime() > stamp.stat().st_mtime


def rebuild() -> bool:
    """Rebuild the frontend. False means the bundle on disk is whatever it was.

    `npm` is resolved through `shutil.which` rather than invoked by name:
    on Windows it is `npm.cmd`, which `subprocess` will not find without the
    extension, and a missing npm is a case worth naming anyway.
    """
    npm = shutil.which("npm")
    if npm is None:
        print("npm is not on PATH - serving the existing build.", flush=True)
        return False

    print("Building the frontend...", flush=True)
    result = subprocess.run(
        [npm, "run", "build"], cwd=FRONTEND, capture_output=True, text=True
    )
    if result.returncode != 0:
        # The compiler's own message is the useful part; keep the tail of it
        # rather than a summary that hides which file failed.
        tail = (result.stdout + result.stderr).strip().splitlines()[-15:]
        print("\nThe frontend build failed:", flush=True)
        for line in tail:
            print("  " + line, flush=True)
        return False

    print("Frontend built.", flush=True)
    return True


def _serve() -> None:
    """Hand over to the server.

    Imported here rather than at module scope: it pulls in the whole API, and
    the analysis/data layers behind it, which is wasted work on the paths that
    never get this far.
    """
    import serve_ui

    # Anything passed to the launcher belongs to the server: --port, --no-browser.
    sys.argv = ["serve_ui.py", *sys.argv[1:]]
    serve_ui.main()


def main() -> None:
    if not needs_rebuild():
        print("Frontend is up to date.", flush=True)
        _serve()
        return

    built = rebuild()
    if not built and not DIST.is_dir():
        raise SystemExit(
            "The frontend build failed and there is no existing build to fall back on. "
            "Fix the error above, or run `npm run build` in frontend/ by hand."
        )
    if not built:
        print("Warning: serving the previous build - it may be out of date.\n", flush=True)

    _serve()


def _hold_the_window() -> None:
    """Keep the console up so the message above it can actually be read.

    Started from the desktop icon, the window closes the instant the process
    does -- which turns every startup failure into "I double-clicked it and
    nothing happened". A port already in use is the likely one, and its message
    names the PID to stop, so it is worth reading.

    Only on the failure paths: a normal exit means the last tab closed, and
    making that wait for a keypress would leave a dead window on screen every
    single time.
    """
    try:
        input("\nPress Enter to close...")
    except (EOFError, KeyboardInterrupt):
        pass  # no console attached, or the user is already done with it


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Ctrl-C is the user stopping the server on purpose, not a failure.
        sys.exit(130)
    except SystemExit as exc:
        # A string exit code is a message -- SystemExit prints it itself only
        # when it goes uncaught, so it has to be printed here.
        if isinstance(exc.code, str):
            print(f"\n{exc.code}", flush=True)
            _hold_the_window()
        elif exc.code:
            _hold_the_window()
        raise
    except Exception:
        traceback.print_exc()
        _hold_the_window()
        raise

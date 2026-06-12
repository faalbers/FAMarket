"""
Local settings overrides — `settings.local.json` (ROADMAP Topic 3.4).

`config/settings.py` holds the committed DEFAULTS. The Settings UI no longer edits
that file; it writes ONLY the changed keys here, as a flat ``{dotted.path: value}``
JSON that is gitignored and machine-local. On startup `settings.py` defines its
defaults and then calls `apply()` to lay these overrides on top. Delete the file
(or a single key) to fall back to the default.

Why JSON-on-top instead of rewriting `settings.py` (the previous AST approach):
  * `settings.py` stays pristine in git — no churn, no accidental commits of
    machine-specific values.
  * the writable/read-only split is exe-ready — the override can live in a
    user-data dir when frozen while the code + defaults stay in the read-only
    bundle (see the "Standalone executable" Future Idea in ROADMAP).
  * a plain `json.dump` replaces fragile source rewriting.

Dotted paths address nested leaves: ``RATE_LIMITS.yfinance``,
``OVERALL_SCORE_WEIGHTS.quality``. On load each value is coerced back to the
DEFAULT's type (JSON has no Path/tuple), and only paths that already exist as
defaults are applied — a stale/unknown key is ignored with a warning, never
injected. Nothing here is allowed to break startup: a bad file is logged and
skipped, leaving the defaults in force.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class SettingsWriteError(RuntimeError):
    """Raised when an override file cannot be written."""


# --------------------------------------------------------------------------- #
# read / coerce
# --------------------------------------------------------------------------- #
def _read(path: Path | None) -> dict[str, Any]:
    """The raw override dict, or {} if the file is missing/unreadable/not an object."""
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable settings overrides %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("Settings overrides %s is not a JSON object — ignoring.", path)
        return {}
    return data


def load() -> dict[str, Any]:
    """The current override dict from the configured path (public; used by the UI)."""
    from config import settings  # late import: settings imports this module at load
    return _read(settings.SETTINGS_OVERRIDES_PATH)


def _coerce(default: Any, value: Any) -> Any:
    """Coerce a JSON-decoded value back to the default leaf's type where it matters."""
    if isinstance(default, bool):       # before int — bool is an int subclass
        return bool(value)
    if isinstance(default, Path):
        return Path(value)
    if isinstance(default, tuple):
        return tuple(value)
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value


def _jsonable(value: Any) -> Any:
    """Make a Python setting value JSON-serializable (Path -> str, tuple -> list)."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value


# --------------------------------------------------------------------------- #
# apply / save
# --------------------------------------------------------------------------- #
def _leaf(ns: dict, parts: list[str]) -> tuple[Any, bool]:
    """The current default at a dotted path within namespace `ns`, and whether it exists."""
    if parts[0] not in ns:
        return None, False
    cur = ns[parts[0]]
    for key in parts[1:]:
        if not isinstance(cur, dict) or key not in cur:
            return None, False
        cur = cur[key]
    return cur, True


def _set(ns: dict, parts: list[str], value: Any) -> None:
    if len(parts) == 1:
        ns[parts[0]] = value
        return
    container = ns[parts[0]]
    for key in parts[1:-1]:
        container = container[key]
    container[parts[-1]] = value


def apply(ns: dict) -> None:
    """Lay the override file on top of a settings namespace (its ``vars()``/globals).

    Called once at the bottom of `settings.py` (and again after each save so the
    change is live this session). Reads the path straight from `ns` so it never
    re-imports the half-initialized settings module during its own import.
    """
    overrides = _read(ns.get("SETTINGS_OVERRIDES_PATH"))
    for path, raw in overrides.items():
        parts = path.split(".")
        default, exists = _leaf(ns, parts)
        if not exists:
            log.warning("Ignoring unknown settings override: %s", path)
            continue
        try:
            _set(ns, parts, _coerce(default, raw))
        except Exception as exc:  # never let one bad override break startup
            log.warning("Ignoring bad settings override %s=%r: %s", path, raw, exc)


def update_settings(changed: dict[str, Any]) -> dict[str, Any]:
    """Merge ``{dotted_path: value}`` into the override file and apply it live.

    Returns the full override dict after the merge. The Settings page passes only
    the keys that differ from the current value, so the file accumulates just the
    user's deviations from the committed defaults.
    """
    from config import settings
    path: Path = settings.SETTINGS_OVERRIDES_PATH
    data = _read(path)
    data.update({k: _jsonable(v) for k, v in changed.items()})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise SettingsWriteError(f"Could not write {path}: {exc}") from exc
    apply(vars(settings))  # reflect the change in the running session immediately
    return data


def reset() -> None:
    """Remove the override file entirely (all settings revert to defaults next run)."""
    from config import settings
    try:
        settings.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
    except OSError as exc:
        raise SettingsWriteError(f"Could not delete {settings.SETTINGS_OVERRIDES_PATH}: {exc}") from exc

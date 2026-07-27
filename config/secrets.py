"""
API keys, loaded from `.env` (never committed). Kept separate from settings.py,
which holds non-sensitive config the UI is allowed to edit.

Usage:
    from config import secrets
    key = secrets.POLYGON_API_KEY
    secrets.require("POLYGON_API_KEY")   # raises if missing, with a clear message
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from config import settings

# Load .env from the project root if present (no-op when absent).
load_dotenv(settings.BASE_DIR / ".env")

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
ETRADE_CONSUMER_KEY = os.environ.get("ETRADE_CONSUMER_KEY", "")
ETRADE_CONSUMER_SECRET = os.environ.get("ETRADE_CONSUMER_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# SEC requires a descriptive User-Agent (their fair-access policy).
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "FAMarket contact@example.com")


def get(name: str, default: str = "") -> str:
    """Read an arbitrary key from the environment (.env)."""
    return os.environ.get(name, default)


def has(name: str) -> bool:
    """True if a non-empty value is configured for `name`."""
    return bool(os.environ.get(name, "").strip())


def require(name: str) -> str:
    """Return a key's value or raise a clear error if it's missing/empty."""
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required key '{name}'. Add it to your .env "
            f"(copy .env.template to .env and fill it in)."
        )
    return val

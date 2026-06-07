"""
TLS / certificate setup for all outbound HTTPS.

This machine (and many corporate/AV-protected setups) sits behind TLS
interception: a proxy or antivirus re-signs HTTPS with its own root CA. That CA
lives in the Windows trust store but NOT in Python's bundled `certifi`, so every
fetcher would fail with CERTIFICATE_VERIFY_FAILED unless we teach Python to trust
the OS store. The two HTTP stacks the data layer uses need different handling:

  * stdlib `ssl` / `requests` (SEC EDGAR, Polygon, FMP, FRED, E*Trade) — patched
    via `truststore`, which delegates verification to the OS (Windows SChannel).
    This is lenient about quirks in real-world corporate CAs.

  * `curl_cffi` (used internally by yfinance) — does NOT honour truststore, so we
    hand it a CA bundle file that merges certifi with the Windows root/CA store,
    via the standard CURL_CA_BUNDLE environment variable.

Call configure_tls() once at every entry point (the Streamlit app and any fetch
run) before the first network call. It is idempotent and a no-op off Windows /
when no interception is present.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

import certifi

from config import settings

_configured = False
_BUNDLE_PATH: Path = settings.BASE_DIR / ".ca_bundle.pem"


def _build_merged_bundle() -> Path | None:
    """Write certifi + the Windows trust store to a single PEM; return its path.

    Returns None where the OS trust store can't be enumerated (e.g. non-Windows),
    in which case curl_cffi keeps using its default bundle.
    """
    if not hasattr(ssl, "enum_certificates"):  # Windows-only API
        return None

    parts = [Path(certifi.where()).read_text(encoding="utf-8")]
    for store in ("ROOT", "CA"):
        try:
            for cert_bytes, _enc, _trust in ssl.enum_certificates(store):
                try:
                    parts.append(ssl.DER_cert_to_PEM_cert(cert_bytes))
                except (ValueError, ssl.SSLError):
                    continue  # skip certs that won't convert
        except OSError:
            continue
    _BUNDLE_PATH.write_text("\n".join(parts), encoding="utf-8")
    return _BUNDLE_PATH


def configure_tls() -> None:
    """Make every HTTP client trust the OS certificate store. Idempotent."""
    global _configured
    if _configured:
        return

    # 1) stdlib ssl / requests -> OS-native verification.
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # truststore optional / unavailable -> fall back to certifi
        pass

    # 2) curl_cffi (yfinance) -> merged bundle file. Respect a user override.
    if "CURL_CA_BUNDLE" not in os.environ:
        bundle = _build_merged_bundle()
        if bundle is not None:
            os.environ["CURL_CA_BUNDLE"] = str(bundle)

    _configured = True

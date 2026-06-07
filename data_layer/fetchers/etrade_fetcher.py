"""
E*Trade fetcher (Topic 3.1) — secondary quote data source. OAuth1.

Auth flow (Phase 1, single-threaded):
  * Opens the auth URL via the webbrowser module; user pastes the code via input().
  * Auth runs once per fetch session.
  * Token revoked automatically in the class destructor (__del__):
        https://api.etrade.com/oauth/revoke_access_token
  * On API error: revoke immediately, log, retry affected symbols next session.
Phase 2: auth in the main process before workers start; token passed to workers.

SKELETON — Phase 1.
"""

from __future__ import annotations

# from data_layer.fetchers.base import BaseFetcher

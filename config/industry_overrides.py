"""
Industry overrides — symbol -> industry, replacing Yahoo's tag (fix list §25).

Applied once to the quotes frame in `analysis_layer/pipeline.run_analysis`, so it
reaches everything that groups by industry: the `screen_type` routing
(`analysis_layer/screen_type.py`), peer comparisons (`_vs_industry`, scores) and
the sector / sub-industry index. Sector is never changed. quotes.db keeps
Yahoo's original tag.

An explicit list, not a rule: predictable, and a new misfit is added by hand.
Decided 2026-11-12:

* Bitcoin / crypto miners: Yahoo files them as "Capital Markets", which the
  screen_type rule routes to `bank` — bank metrics (efficiency ratio) on miners.
  Built from the 22 "Capital Markets" companies whose business summary describes
  crypto mining; Galaxy Digital (GLXY, broad crypto finance), Bit Digital (BTBT,
  now an ether treasury / data-center business) and Soluna's preferred (SLNHP)
  left out.
* Sun Life: "Insurance - Diversified" puts it among property & casualty insurers;
  its closest peer Manulife is "Insurance - Life", and the SEC files it as Life
  Insurance (SIC 6311).
* Not changed: self-storage REITs stay "REIT - Industrial" — the data provider
  files them there on purpose, and a separate group would hold ~5 names.
"""

CRYPTO_MINING = "Crypto Mining"

INDUSTRY_OVERRIDES: dict[str, str] = {
    # -- crypto miners (Yahoo: Capital Markets) --
    "IREN": CRYPTO_MINING,
    "HUT": CRYPTO_MINING,
    "RIOT": CRYPTO_MINING,
    "WULF": CRYPTO_MINING,
    "IOND": CRYPTO_MINING,
    "MARA": CRYPTO_MINING,
    "CLSK": CRYPTO_MINING,
    "SLNH": CRYPTO_MINING,
    "CANG": CRYPTO_MINING,
    "NCTY": CRYPTO_MINING,
    "VIP": CRYPTO_MINING,
    "GREE": CRYPTO_MINING,
    "FUFU": CRYPTO_MINING,
    "MGTI": CRYPTO_MINING,
    "ABTS": CRYPTO_MINING,
    "LMFA": CRYPTO_MINING,
    "NBND": CRYPTO_MINING,
    "CBLO": CRYPTO_MINING,
    "PWCM": CRYPTO_MINING,
    # -- insurers --
    "SLF": "Insurance - Life",   # Yahoo: Insurance - Diversified
}

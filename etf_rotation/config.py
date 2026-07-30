"""
Central configuration for the ETF Relative-Strength Rotation strategy.
Change parameters here — nothing else in the codebase should hardcode these.
"""
import os

# ---------------------------------------------------------------------------
# ETF universe: (yfinance ticker, display name, short code)
# ---------------------------------------------------------------------------
ETFS = [
    ("GOLDBEES.NS",   "GoldBees",      "GOLD"),
    ("SILVERBEES.NS", "SilverBees",    "SILV"),
    ("NIFTYBEES.NS",  "NiftyBees",     "NFTY"),
    ("JUNIORBEES.NS", "JuniorBees",    "JNBR"),
    ("MID150BEES.NS", "Midcap150",     "MIDM"),
    ("NIF100BEES.NS", "Nifty100",      "NF10"),
    ("BANKBEES.NS",   "BankBees",      "BANK"),
    ("ITBEES.NS",     "ITBees",        "ITMC"),
    ("PHARMABEES.NS", "PharmaBees",    "PHRM"),
    ("AUTOBEES.NS",   "AutoBees",      "AUTO"),
    ("INFRABEES.NS",  "InfraBees",     "INFR"),
    ("CONSUMBEES.NS", "ConsumeBees",   "CNSM"),
    ("PSUBNKBEES.NS", "PSUBankBees",   "PSUB"),
    ("CPSEETF.NS",    "CPSE ETF",      "CETF"),
    ("LTGILTBEES.NS", "LT Gilt",       "GSCP"),
    ("GILT5YBEES.NS", "GSec 5Y",       "GS5Y"),
    ("LIQUIDBEES.NS", "LiquidBees",    "LIQD"),
    ("MOM100.NS",     "Momentum100",   "MOM"),
    ("MOMENTUM30.NS", "Momentum30",    "MOM3"),
    ("NV20BEES.NS",   "Value20",       "NV20"),
    ("DIVOPPBEES.NS", "DivOpp",        "DIVO"),
    ("HNGSNGBEES.NS", "HangSeng",      "HNGS"),
    ("MAFANG.NS",     "FANGPlus",      "FANG"),
    ("MON100.NS",     "Nasdaq100",     "NSDQ"),
]

TICKERS = [t[0] for t in ETFS]
NAME_MAP = {t[0]: t[1] for t in ETFS}
CODE_MAP = {t[0]: t[2] for t in ETFS}

# ---------------------------------------------------------------------------
# Strategy parameters
# ---------------------------------------------------------------------------
LOOKBACK_DAYS = 90                     # RS smoothing / momentum window (trading days)
TOP_N = int(os.environ.get("TOP_N", 5))  # number of ETFs to hold, overridable via env var
MIN_HISTORY_DAYS = LOOKBACK_DAYS + 20    # minimum price history required before an ETF is eligible for ranking

INITIAL_CAPITAL = 1_000_000.0          # Rs 10,00,000 -- used fresh for EACH segment (see backtest.py)
TXN_COST_BPS = 0.0005                  # 0.05% per executed trade (buy or sell), covers brokerage+STT+slippage

LOOKBACK_SWEEP = list(range(15, 151, 5))  # 15, 20, 25, ..., 90 -- for parameter-stability testing

RS_METHODS = ["mansfield", "momentum"]   # signal styles compared side-by-side on the dashboard
REBALANCE_MODE = "diff"  #"full_liquidate"        # "full_liquidate": sell ALL holdings + equal-weight rebuy top_n
                                          # whenever the top_n SET changes (even continuing names churn).
                                          # "diff": only trade the diffs (kept as an option in portfolio.py).

# ---------------------------------------------------------------------------
# Backtest / reporting date ranges
# ---------------------------------------------------------------------------
DATA_START = "2017-06-01"              # buffer so 45d RS is valid from day 1 of BACKTEST_START
BACKTEST_START = "2018-01-01"

# ---------------------------------------------------------------------------
# Data source
# ---------------------------------------------------------------------------
DATA_SOURCE = os.environ.get("DATA_SOURCE", "yfinance")   # "yfinance" or "fyers"

# Fyers API v3 credentials -- read from env vars, which in GitHub Actions
# come from repository secrets (FYERS_CLIENT_ID, FYERS_ACCESS_TOKEN). See
# README "Using Fyers instead of yfinance" for setup and IMPORTANT caveats
# about daily token expiry -- there is deliberately NO auto-refresh here
# (no refresh_token/PIN available), so an expired token fails loudly rather
# than silently falling back to something else.
FYERS_CLIENT_ID = os.environ.get("FYERS_CLIENT_ID")
FYERS_ACCESS_TOKEN = os.environ.get("FYERS_ACCESS_TOKEN")
FYERS_BASE_URL = "https://api-t1.fyers.in"
FYERS_HISTORY_PATH = "/data/history"
FYERS_CHUNK_DAYS = 365          # Fyers' daily-resolution history API is chunked per request
FYERS_REQUEST_DELAY_SEC = 0.35  # be polite to the rate limit across ~24 tickers x several chunks

# Override if a specific ETF's Fyers symbol doesn't follow the standard
# "NSE:<SYM>-EQ" pattern (verify against Fyers symbol master before relying
# on this for any ticker not already confirmed working).
FYERS_SYMBOL_OVERRIDES = {}
SEGMENT_1 = ("2018-01-01", "2024-12-31")   # in-sample backtest
SEGMENT_2 = ("2025-01-01", "2025-12-31")   # forward test (no parameter tuning done on this period)
SEGMENT_3 = ("2026-01-01", None)           # forward test, None = up to latest available date

# ---------------------------------------------------------------------------
# File locations
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
PRICE_CACHE = os.path.join(DATA_DIR, "prices.csv")
PRICE_CACHE_FYERS = os.path.join(DATA_DIR, "prices_fyers.csv")
DOCS_DIR = os.path.join(ROOT, "docs")
DASHBOARD_HTML = os.path.join(DOCS_DIR, "index.html")
TRADE_LOG_CSV = os.path.join(DATA_DIR, "trade_log.csv")
EQUITY_CSV = os.path.join(DATA_DIR, "equity_curve.csv")
SIGNAL_JSON = os.path.join(DATA_DIR, "latest_signal.json")
ROBUSTNESS_JSON = os.path.join(DATA_DIR, "robustness.json")

# Any sweep run whose max drawdown breaches this gets its full trade log and
# worst round-trip trades auto-dumped to disk for manual inspection -- see
# etf_rotation/robustness.py: dump_catastrophic_run().
CATASTROPHIC_DD_THRESHOLD_PCT = -40.0
CATASTROPHIC_RUNS_DIR = os.path.join(DATA_DIR, "catastrophic_runs")
DATA_QUALITY_FLAGS_CSV = os.path.join(DATA_DIR, "data_quality_flags.csv")

# Multi-day cumulative move detection windows/threshold -- catches gradual
# bad-tick drift that a single-day-only check misses entirely.
MULTIDAY_WINDOWS = (3, 5, 10)
MULTIDAY_MOVE_THRESHOLD = 0.25

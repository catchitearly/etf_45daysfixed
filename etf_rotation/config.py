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
LOOKBACK_DAYS = 45                     # RS smoothing / momentum window (trading days)
TOP_N = int(os.environ.get("TOP_N", 3))  # number of ETFs to hold, overridable via env var
MIN_HISTORY_DAYS = LOOKBACK_DAYS + 20    # minimum price history required before an ETF is eligible for ranking

INITIAL_CAPITAL = 1_000_000.0          # Rs 10,00,000 -- used fresh for EACH segment (see backtest.py)
TXN_COST_BPS = 0.0005                  # 0.05% per executed trade (buy or sell), covers brokerage+STT+slippage

LOOKBACK_SWEEP = list(range(15, 91, 5))  # 15, 20, 25, ..., 90 -- for parameter-stability testing

RS_METHODS = ["mansfield", "momentum"]   # signal styles compared side-by-side on the dashboard
REBALANCE_MODE = "full_liquidate"        # "full_liquidate": sell ALL holdings + equal-weight rebuy top_n
                                          # whenever the top_n SET changes (even continuing names churn).
                                          # "diff": only trade the diffs (kept as an option in portfolio.py).

# ---------------------------------------------------------------------------
# Backtest / reporting date ranges
# ---------------------------------------------------------------------------
DATA_START = "2017-06-01"              # buffer so 45d RS is valid from day 1 of BACKTEST_START
BACKTEST_START = "2018-01-01"
SEGMENT_1 = ("2018-01-01", "2024-12-31")   # in-sample backtest
SEGMENT_2 = ("2025-01-01", "2025-12-31")   # forward test (no parameter tuning done on this period)
SEGMENT_3 = ("2026-01-01", None)           # forward test, None = up to latest available date

# ---------------------------------------------------------------------------
# File locations
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
PRICE_CACHE = os.path.join(DATA_DIR, "prices.csv")
DOCS_DIR = os.path.join(ROOT, "docs")
DASHBOARD_HTML = os.path.join(DOCS_DIR, "index.html")
TRADE_LOG_CSV = os.path.join(DATA_DIR, "trade_log.csv")
EQUITY_CSV = os.path.join(DATA_DIR, "equity_curve.csv")
SIGNAL_JSON = os.path.join(DATA_DIR, "latest_signal.json")
ROBUSTNESS_JSON = os.path.join(DATA_DIR, "robustness.json")

"""
Price data fetching via yfinance, with local CSV caching so repeated runs
(and re-runs of the backtest) don't hammer Yahoo Finance.
"""
import os
import pandas as pd

from . import config


def fetch_prices(start=config.DATA_START, end=None, use_cache=True, tickers=None):
    """
    Returns a DataFrame of daily *adjusted* close prices, columns=tickers, index=date.
    Uses local cache at config.PRICE_CACHE when available and use_cache=True; otherwise
    (or for any tickers/dates missing from cache) fetches fresh from yfinance and updates
    the cache.
    """
    tickers = tickers or config.TICKERS
    os.makedirs(config.DATA_DIR, exist_ok=True)

    cached = None
    if use_cache and os.path.exists(config.PRICE_CACHE):
        cached = pd.read_csv(config.PRICE_CACHE, index_col=0, parse_dates=True)

    need_fetch = True
    if cached is not None:
        missing_cols = [t for t in tickers if t not in cached.columns]
        cache_end = cached.index.max()
        # refetch if any tickers are missing, or cache doesn't reach close to today
        stale = (pd.Timestamp.today().normalize() - cache_end).days > 3
        if not missing_cols and not stale:
            need_fetch = False

    if not need_fetch:
        df = cached[tickers].copy()
        df = df[(df.index >= pd.Timestamp(start))]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        return df

    import yfinance as yf

    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today()
    raw = yf.download(
        tickers,
        start=start,
        end=(end_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=True,   # gives split/dividend-adjusted "Close" directly
        progress=False,
        group_by="ticker",
        threads=True,
    )

    if isinstance(raw.columns, pd.MultiIndex):
        prices = pd.DataFrame({t: raw[t]["Close"] for t in tickers if t in raw.columns.get_level_values(0)})
    else:
        # single-ticker case
        prices = raw[["Close"]].rename(columns={"Close": tickers[0]})

    prices = prices.sort_index()
    prices.index.name = "Date"

    # merge with cache (union of columns/rows), cached wins are overwritten by fresh data
    if cached is not None:
        merged = cached.combine_first(prices)
        merged.update(prices)
        for t in tickers:
            if t not in merged.columns and t in prices.columns:
                merged[t] = prices[t]
    else:
        merged = prices

    merged = merged.sort_index()
    merged.to_csv(config.PRICE_CACHE)

    df = merged[tickers].copy()
    df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def make_synthetic_prices(start="2017-06-01", end="2026-07-27", tickers=None, seed=42):
    """
    Generates plausible synthetic daily price series for all tickers, purely for
    offline pipeline validation (used because this sandbox cannot reach Yahoo
    Finance). NOT for real analysis.
    """
    import numpy as np
    tickers = tickers or config.TICKERS
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    n = len(dates)

    data = {}
    for i, t in enumerate(tickers):
        # give each ETF a distinct drift/vol + occasional regime shifts so RS rotation has something to bite on
        base_drift = rng.uniform(0.0002, 0.0006)
        vol = rng.uniform(0.008, 0.02)
        regime_len = rng.integers(60, 250)
        rets = rng.normal(base_drift, vol, n)
        # inject regime "trend bursts" so some ETFs clearly outperform for stretches
        pos = 0
        while pos < n:
            span = min(regime_len, n - pos)
            boost = rng.normal(0, 0.0006)
            rets[pos:pos + span] += boost
            pos += span
        prices = 100 * np.cumprod(1 + rets)
        # simulate some ETFs listing later than others (a few of the momentum/international ones)
        listing_delay = rng.integers(0, 400) if t in ("MOM100.NS", "MOMENTUM30.NS", "MON100.NS") else 0
        prices[:listing_delay] = np.nan
        data[t] = prices

    df = pd.DataFrame(data, index=dates)
    return df

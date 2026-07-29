"""
Price data fetching, with local CSV caching so repeated runs (and re-runs
of the backtest) don't hammer the upstream API. Two sources are supported:

  - "yfinance" (default): fetch_prices_yfinance()
  - "fyers": fetch_prices_fyers() in etf_rotation.fyers_data

fetch_prices() dispatches between them based on config.DATA_SOURCE (or an
explicit `source=` argument), and uses a SEPARATE cache file per source so
switching sources never silently mixes vendors in one cached series.
"""
import os
import pandas as pd

from . import config


def fetch_prices(start=config.DATA_START, end=None, use_cache=True, tickers=None, source=None):
    """
    Returns a DataFrame of daily close prices, columns=tickers, index=date.
    Dispatches to the yfinance or Fyers fetcher based on `source`
    (defaults to config.DATA_SOURCE, i.e. the DATA_SOURCE env var).
    """
    source = source or config.DATA_SOURCE
    if source == "fyers":
        from . import fyers_data
        return _fetch_with_cache(
            fetch_fn=lambda tks, s, e: fyers_data.fetch_prices_fyers(tickers=tks, start=s, end=e),
            cache_path=config.PRICE_CACHE_FYERS,
            start=start, end=end, use_cache=use_cache, tickers=tickers,
        )
    elif source == "yfinance":
        return _fetch_with_cache(
            fetch_fn=_fetch_yfinance_raw,
            cache_path=config.PRICE_CACHE,
            start=start, end=end, use_cache=use_cache, tickers=tickers,
        )
    else:
        raise ValueError(f"Unknown data source: {source!r} (expected 'yfinance' or 'fyers')")


def _fetch_with_cache(fetch_fn, cache_path, start, end, use_cache, tickers):
    tickers = tickers or config.TICKERS
    os.makedirs(config.DATA_DIR, exist_ok=True)

    cached = None
    if use_cache and os.path.exists(cache_path):
        cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)

    need_fetch = True
    if cached is not None:
        missing_cols = [t for t in tickers if t not in cached.columns]
        cache_end = cached.index.max()
        stale = (pd.Timestamp.today().normalize() - cache_end).days > 3
        if not missing_cols and not stale:
            need_fetch = False

    if not need_fetch:
        df = cached[tickers].copy()
        df = df[(df.index >= pd.Timestamp(start))]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        return df

    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today()
    prices = fetch_fn(tickers, start, (end_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    prices = prices.sort_index()
    prices.index.name = "Date"

    if cached is not None:
        merged = cached.combine_first(prices)
        merged.update(prices)
        for t in tickers:
            if t not in merged.columns and t in prices.columns:
                merged[t] = prices[t]
    else:
        merged = prices

    merged = merged.sort_index()
    merged.to_csv(cache_path)

    df = merged[tickers].copy()
    df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def _fetch_yfinance_raw(tickers, start, end):
    import yfinance as yf

    raw = yf.download(
        tickers,
        start=start,
        end=end,
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
    return prices


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

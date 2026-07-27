"""
Mansfield Relative Strength, computed for each ETF against an equal-weight
basket of ALL OTHER ETFs in the universe (i.e. "this ETF vs the rest of the
list"), rather than against a single fixed benchmark.

Classic Mansfield RS:
    ratio   = Price(security) / Price(benchmark)
    RS      = (ratio / SMA(ratio, N) - 1) * 100

Here, for ETF i:
    benchmark_i(t) = mean over j != i of [ Price_j(t) / Price_j(t0_j) * 100 ]
                     (each peer normalized to start at 100 on ITS OWN first
                     available date, so late-listed ETFs don't distort the
                     basket before they exist)
    ratio_i(t)  = [ Price_i(t) / Price_i(t0_i) * 100 ] / benchmark_i(t)
    RS_i(t)     = (ratio_i(t) / SMA(ratio_i, LOOKBACK)(t) - 1) * 100

RS > 0 and rising  => ETF strengthening vs the peer group (uptrend leadership)
RS < 0 and falling => ETF weakening vs the peer group

Ranking each week is simply: sort by RS_i(t) descending; top N = "in trend".
"""
import numpy as np
import pandas as pd

from . import config


def normalize(prices: pd.DataFrame) -> pd.DataFrame:
    """Rebase each column to start at 100 on its own first valid observation."""
    norm = pd.DataFrame(index=prices.index, columns=prices.columns, dtype=float)
    for col in prices.columns:
        s = prices[col]
        first_valid = s.first_valid_index()
        if first_valid is None:
            continue
        base = s.loc[first_valid]
        norm[col] = s / base * 100.0
    return norm


def compute_mansfield_rs(prices: pd.DataFrame, lookback: int = config.LOOKBACK_DAYS) -> pd.DataFrame:
    """
    prices: DataFrame of raw adjusted close prices, columns=tickers, index=date.
    Returns: DataFrame of Mansfield RS values, same shape, NaN where not computable
             (insufficient history for that ticker or for the lookback SMA).
    """
    norm = normalize(prices)
    tickers = list(prices.columns)
    rs = pd.DataFrame(index=prices.index, columns=tickers, dtype=float)

    for t in tickers:
        peers = [c for c in tickers if c != t]
        basket = norm[peers].mean(axis=1, skipna=True)
        ratio = norm[t] / basket
        sma = ratio.rolling(lookback, min_periods=lookback).mean()
        rs[t] = (ratio / sma - 1.0) * 100.0

    return rs


def eligible_mask(prices: pd.DataFrame, min_history_days: int = config.MIN_HISTORY_DAYS) -> pd.DataFrame:
    """
    Boolean DataFrame: True where a ticker has at least `min_history_days` of
    trailing price history as of that date (so newly-listed ETFs aren't ranked
    before they have a meaningful track record).
    """
    has_price = prices.notna()
    days_since_listing = has_price.cumsum()
    return (days_since_listing >= min_history_days) & has_price


def rank_on_date(rs: pd.DataFrame, prices: pd.DataFrame, date, top_n: int = config.TOP_N):
    """
    Returns a list of (ticker, rs_value) for the top_n eligible ETFs as of `date`,
    sorted descending by RS. Ineligible / NaN-RS tickers are excluded entirely.
    """
    elig = eligible_mask(prices)
    if date not in rs.index:
        # snap back to the most recent available trading date <= requested date
        valid_dates = rs.index[rs.index <= date]
        if len(valid_dates) == 0:
            return []
        date = valid_dates[-1]

    row = rs.loc[date]
    elig_row = elig.loc[date] if date in elig.index else pd.Series(False, index=rs.columns)
    candidates = row[elig_row & row.notna()].sort_values(ascending=False)
    top = list(candidates.items())[:top_n]
    return top

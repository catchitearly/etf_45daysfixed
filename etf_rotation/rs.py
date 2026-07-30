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


def compute_momentum_rs(prices: pd.DataFrame, lookback: int = config.LOOKBACK_DAYS) -> pd.DataFrame:
    """
    "Momentum" RS style: for ETF i, score = raw N-day return of i minus the
    average N-day return of all other ETFs (peers), i.e.

        score_i(t) = ret_i(t) - mean_{j != i}( ret_j(t) )

    This is unsmoothed (no moving average of a ratio) so it reacts to the
    current N-day window immediately, unlike Mansfield RS which requires
    the ratio to be above its OWN 45-day moving average before it fires.
    Included for direct, apples-to-apples comparison against Mansfield RS
    under identical top_n / rebalance / cost rules.
    """
    rets = prices / prices.shift(lookback) - 1.0
    total = rets.sum(axis=1, skipna=True)
    count = rets.notna().sum(axis=1)

    score = pd.DataFrame(index=prices.index, columns=prices.columns, dtype=float)
    for col in prices.columns:
        own = rets[col]
        others_sum = total - own.fillna(0)
        others_count = count - own.notna().astype(int)
        peer_mean = others_sum / others_count.replace(0, np.nan)
        score[col] = own - peer_mean

    return score


def compute_rs(prices: pd.DataFrame, lookback: int = config.LOOKBACK_DAYS,
                method: str = "mansfield") -> pd.DataFrame:
    """Dispatcher: method in {"mansfield", "momentum"}."""
    if method == "mansfield":
        return compute_mansfield_rs(prices, lookback=lookback)
    elif method == "momentum":
        return compute_momentum_rs(prices, lookback=lookback)
    else:
        raise ValueError(f"Unknown RS method: {method!r}")


def eligible_mask(prices: pd.DataFrame, min_history_days: int = config.MIN_HISTORY_DAYS) -> pd.DataFrame:
    """
    Boolean DataFrame: True where a ticker has at least `min_history_days` of
    trailing price history as of that date (so newly-listed ETFs aren't ranked
    before they have a meaningful track record).
    """
    has_price = prices.notna()
    days_since_listing = has_price.cumsum()
    return (days_since_listing >= min_history_days) & has_price


def compute_rs_zscore(rs: pd.DataFrame, window: int = config.PARABOLIC_ZSCORE_WINDOW) -> pd.DataFrame:
    """
    For each ticker, how many standard deviations is TODAY's RS score above/
    below that same ticker's own trailing `window`-day RS history? A high
    positive z-score means "this ETF's relative strength is unusually
    stretched even by its own historical standards" -- a known precursor to
    momentum-crash-style reversals (e.g. an asset riding a parabolic move
    that then violently mean-reverts).

    Uses a shorter min_periods (1/4 of window) so the z-score is available
    reasonably early rather than only after a full year of history.
    """
    min_periods = max(30, window // 4)
    rolling_mean = rs.rolling(window, min_periods=min_periods).mean()
    rolling_std = rs.rolling(window, min_periods=min_periods).std()
    return (rs - rolling_mean) / rolling_std


def compute_parabolic_mask(rs: pd.DataFrame, window: int = config.PARABOLIC_ZSCORE_WINDOW,
                            threshold: float = config.PARABOLIC_ZSCORE_THRESHOLD) -> pd.DataFrame:
    """
    Boolean DataFrame, True where a ticker's RS z-score (see
    compute_rs_zscore) exceeds `threshold` -- i.e. "too parabolic/overextended
    to chase right now," even if it would otherwise rank in the top-N.
    NaN z-scores (insufficient history) are treated as NOT excluded.
    """
    z = compute_rs_zscore(rs, window=window)
    return (z > threshold).fillna(False)


def rank_on_date(rs: pd.DataFrame, prices: pd.DataFrame, date, top_n: int = config.TOP_N,
                  exclude_mask: pd.DataFrame = None):
    """
    Returns a list of (ticker, rs_value) for the top_n eligible ETFs as of `date`,
    sorted descending by RS. Ineligible / NaN-RS tickers are excluded entirely.

    exclude_mask: optional boolean DataFrame (same shape as rs) -- tickers
    marked True for this date are skipped even if they'd otherwise rank in
    the top_n (used by the parabolic/overextended filter). The next
    best-ranked eligible ticker takes the freed slot instead.
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
    keep = elig_row & row.notna()
    if exclude_mask is not None:
        excl_row = exclude_mask.loc[date] if date in exclude_mask.index else pd.Series(False, index=rs.columns)
        keep = keep & (~excl_row)
    candidates = row[keep].sort_values(ascending=False)
    top = list(candidates.items())[:top_n]
    return top

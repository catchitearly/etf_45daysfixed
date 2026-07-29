"""
Lightweight data sanity checks, informed by the "bad price tick" failure
mode: illiquid/thin instruments occasionally have garbage prints in
historical data (stale quote, unadjusted corporate action, vendor glitch).
A single bad tick used as a rebalance-execution price can fabricate an
impossible trade return and poison the whole equity curve and drawdown
calc, so we flag -- not silently trust -- any outsized single-day move.

This is diagnostic only: it does not modify the price data or the backtest,
it just surfaces a list of dates/tickers worth eyeballing against the
actual NSE data before trusting the numbers around them.
"""
import os
import pandas as pd

from . import config


def flag_suspicious_moves(prices: pd.DataFrame, threshold: float = 0.20):
    """
    Returns a list of dicts for every single-day close-to-close move whose
    absolute magnitude exceeds `threshold` (default 20%). ETFs -- even gold
    and silver -- essentially never move >20% in a single session under
    normal conditions, so hits here are worth a manual look (they CAN be
    legitimate, e.g. a large unadjusted dividend/bonus on a thin ETF, but
    they're exactly the kind of print the debugging playbook says to check
    by hand before trusting anything computed near it).
    """
    flags = []
    for col in prices.columns:
        s = prices[col].dropna()
        if len(s) < 2:
            continue
        pct_move = s.pct_change()
        hits = pct_move[pct_move.abs() > threshold]
        for dt, move in hits.items():
            idx = s.index.get_loc(dt)
            prev_dt = s.index[idx - 1] if idx > 0 else None
            flags.append({
                "ticker": col,
                "date": str(dt.date()),
                "prev_date": str(prev_dt.date()) if prev_dt is not None else None,
                "prev_price": round(float(s.loc[prev_dt]), 2) if prev_dt is not None else None,
                "price": round(float(s.loc[dt]), 2),
                "pct_move": round(float(move) * 100, 2),
            })
    flags.sort(key=lambda f: -abs(f["pct_move"]))
    return flags


def flag_multiday_moves(prices: pd.DataFrame, windows=None, threshold: float = None):
    """
    Catches the failure mode a single-day check MISSES ENTIRELY: a price
    that drifts to a bad level over several small daily moves (each under
    the single-day threshold) rather than one dramatic jump. For each
    window in `windows` (trading days), flags any |N-day cumulative move|
    exceeding `threshold`.

    Deliberately NOT deduplicated against single-day flags -- a genuine
    single-day 900% jump will also show up here trivially (any window
    containing it exceeds threshold too), which is fine: the two checks are
    independent evidence, and seeing both fire on the same date/ticker is
    itself a useful corroborating signal.
    """
    windows = windows or config.MULTIDAY_WINDOWS
    threshold = threshold if threshold is not None else config.MULTIDAY_MOVE_THRESHOLD

    flags = []
    for col in prices.columns:
        s = prices[col].dropna()
        if len(s) < 2:
            continue
        for w in windows:
            if len(s) <= w:
                continue
            cum_move = s.pct_change(periods=w)
            hits = cum_move[cum_move.abs() > threshold]
            for dt_end, move in hits.items():
                idx = s.index.get_loc(dt_end)
                idx_start = idx - w
                if idx_start < 0:
                    continue
                dt_start = s.index[idx_start]
                flags.append({
                    "ticker": col,
                    "window_days": w,
                    "start_date": str(dt_start.date()),
                    "end_date": str(dt_end.date()),
                    "start_price": round(float(s.loc[dt_start]), 2),
                    "end_price": round(float(s.loc[dt_end]), 2),
                    "cum_pct_move": round(float(move) * 100, 2),
                })
    flags.sort(key=lambda f: -abs(f["cum_pct_move"]))
    return flags


def dump_flags_csv(flags, path):
    """
    Writes the FULL (uncapped) list of flagged moves to a CSV file for
    manual audit -- unlike embedding in the dashboard JSON, which is capped
    to keep the page size reasonable, this file has everything.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if flags:
        pd.DataFrame(flags).to_csv(path, index=False)
    else:
        pd.DataFrame(columns=["ticker", "date", "prev_date", "prev_price", "price", "pct_move"]).to_csv(path, index=False)
    return path


def check_portfolio_invariants(equity_curve: pd.DataFrame):
    """
    Cheap sanity pass over a completed equity curve: for an unlevered,
    long-only, cash-settled strategy, equity should never go negative and
    should never contain NaN. Returns a list of violation dicts (empty if
    clean) -- does not raise, since Portfolio.market_value() already asserts
    in real time; this is a second, independent check over the final series.
    """
    violations = []
    eq = equity_curve["equity"]
    if eq.isna().any():
        bad_dates = eq[eq.isna()].index
        violations.append({
            "type": "nan_equity",
            "count": int(len(bad_dates)),
            "first_date": str(bad_dates[0].date()) if len(bad_dates) else None,
        })
    negative = eq[eq < -1e-6]
    if len(negative) > 0:
        violations.append({
            "type": "negative_equity",
            "count": int(len(negative)),
            "first_date": str(negative.index[0].date()),
            "min_value": round(float(negative.min()), 2),
        })
    return violations

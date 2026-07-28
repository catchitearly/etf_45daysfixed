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
import pandas as pd


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

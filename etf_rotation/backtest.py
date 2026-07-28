"""
Weekly rotation engine: for each week -
  - "scan" happens using the close of the last trading day on/before Saturday
    (i.e. Friday's close, or Thursday's if Friday was a holiday)
  - "execution" happens at the close of the following Monday (or the next
    trading day if Monday was a holiday)

This module is the single source of truth for the rotation logic - both the
historical backtest and the live GitHub Actions scripts call into it, so
there is exactly one implementation of "what does the strategy do" and no
risk of backtest/live logic drifting apart.
"""
import numpy as np
import pandas as pd

from . import config
from .rs import compute_rs, rank_on_date
from .portfolio import Portfolio


def build_week_anchors(price_index: pd.DatetimeIndex, start, end):
    """
    Returns a list of (scan_date, execute_date) pairs covering every week
    (Mon-based) in [start, end], using actual trading dates present in
    price_index.
    """
    mondays = pd.date_range(start=start, end=end, freq="W-MON")
    pairs = []
    for m in mondays:
        prior = price_index[price_index < m]
        if len(prior) == 0:
            continue
        scan_date = prior[-1]

        on_or_after = price_index[(price_index >= m) & (price_index <= end)]
        if len(on_or_after) == 0:
            continue
        execute_date = on_or_after[0]

        if execute_date <= scan_date:
            continue
        pairs.append((scan_date, execute_date))
    return pairs


def run_backtest(prices: pd.DataFrame, start=config.BACKTEST_START, end=None,
                  top_n: int = config.TOP_N, lookback: int = config.LOOKBACK_DAYS,
                  initial_capital: float = config.INITIAL_CAPITAL,
                  rs_method: str = "mansfield", rebalance_mode: str = None):
    """
    Runs the full weekly rotation simulation over prices.index restricted to
    [start, end]. `prices` should already include the warm-up buffer before
    `start` needed for the RS lookback to be valid on day 1.

    Each call is an INDEPENDENT simulation: it always starts with
    `initial_capital` in cash and no holdings, regardless of what happened
    before `start` in the underlying price data. This is what lets the three
    reporting segments (backtest / FT1 / FT2) each be reported as their own
    fresh ₹10L run, rather than one continuously-compounding curve.

    rs_method: "mansfield" or "momentum" (see etf_rotation.rs)
    rebalance_mode: "full_liquidate" or "diff" (see etf_rotation.portfolio);
                     defaults to config.REBALANCE_MODE.

    Returns dict with:
      equity_curve: DataFrame indexed by date with columns [equity, cash]
      trade_log: DataFrame of individual trades
      signal_log: DataFrame, one row per week: scan_date, execute_date, top_n tickers + RS values
      holdings_log: DataFrame, weekly snapshot of holdings after execution
      final_portfolio: Portfolio object
    """
    rebalance_mode = rebalance_mode or config.REBALANCE_MODE
    end = pd.Timestamp(end) if end else prices.index.max()
    start = pd.Timestamp(start)

    rs = compute_rs(prices, lookback=lookback, method=rs_method)

    sim_index = prices.index[(prices.index >= start) & (prices.index <= end)]
    if len(sim_index) == 0:
        raise ValueError("No trading dates in requested backtest window.")

    anchors = build_week_anchors(prices.index, sim_index[0], sim_index[-1])

    pf = Portfolio(cash=initial_capital)
    signal_rows = []
    holdings_rows = []

    anchor_map = {}
    for sc, ex in anchors:
        anchor_map[ex] = sc

    equity_rows = []

    for date in sim_index:
        if date in anchor_map:
            scan_date = anchor_map[date]
            top = rank_on_date(rs, prices, scan_date, top_n=top_n)
            top_tickers = [t for t, _ in top]

            signal_rows.append({
                "scan_date": scan_date,
                "execute_date": date,
                **{f"rank_{i+1}": (top[i][0] if i < len(top) else None) for i in range(top_n)},
                **{f"rs_{i+1}": (round(top[i][1], 3) if i < len(top) else None) for i in range(top_n)},
            })

            prices_on_date = prices.loc[date].to_dict()
            if len(top_tickers) > 0:
                pf.rebalance(date, top_tickers, prices_on_date, mode=rebalance_mode)

            holdings_rows.append({
                "date": date,
                "holdings": ",".join(f"{t}:{u}" for t, u in pf.holdings.items()),
                "cash": pf.cash,
            })

        prices_on_date = prices.loc[date].to_dict()
        mv = pf.market_value(prices_on_date)
        equity_rows.append({"date": date, "equity": mv, "cash": pf.cash})

    equity_curve = pd.DataFrame(equity_rows).set_index("date")
    trade_log = pd.DataFrame(pf.trade_log)
    signal_log = pd.DataFrame(signal_rows)
    holdings_log = pd.DataFrame(holdings_rows)

    return {
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "signal_log": signal_log,
        "holdings_log": holdings_log,
        "final_portfolio": pf,
        "rs": rs,
        "rs_method": rs_method,
        "rebalance_mode": rebalance_mode,
    }


def run_all_segments(prices: pd.DataFrame, methods=None, top_n: int = config.TOP_N,
                      lookback: int = config.LOOKBACK_DAYS,
                      initial_capital: float = config.INITIAL_CAPITAL,
                      rebalance_mode: str = None):
    """
    Runs EVERY (rs_method x segment) combination as an independent simulation
    (fresh initial_capital, no carryover between segments), for side-by-side
    comparison on the dashboard.

    Returns: { method: { "backtest": result, "ft1": result, "ft2": result } }
    """
    methods = methods or config.RS_METHODS
    s1, e1 = config.SEGMENT_1
    s2, e2 = config.SEGMENT_2
    s3, e3 = config.SEGMENT_3
    e3 = e3 or str(prices.index.max().date())

    segment_defs = [("backtest", s1, e1), ("ft1", s2, e2), ("ft2", s3, e3)]

    out = {}
    for method in methods:
        out[method] = {}
        for key, seg_start, seg_end in segment_defs:
            out[method][key] = run_backtest(
                prices, start=seg_start, end=seg_end, top_n=top_n, lookback=lookback,
                initial_capital=initial_capital, rs_method=method, rebalance_mode=rebalance_mode,
            )
    return out


# ---------------------------------------------------------------------------
# Performance metrics
# ---------------------------------------------------------------------------
def compute_metrics(equity_curve: pd.DataFrame, trade_log: pd.DataFrame = None,
                     start=None, end=None, periods_per_year=252):
    ec = equity_curve.copy()
    if start:
        ec = ec[ec.index >= pd.Timestamp(start)]
    if end:
        ec = ec[ec.index <= pd.Timestamp(end)]
    if len(ec) < 2:
        return {"error": "insufficient data in segment"}

    eq = ec["equity"]
    daily_ret = eq.pct_change().dropna()

    n_days = (eq.index[-1] - eq.index[0]).days
    years = max(n_days / 365.25, 1e-9)
    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan

    running_max = eq.cummax()
    drawdown = eq / running_max - 1
    max_dd = drawdown.min()

    vol_annual = daily_ret.std() * np.sqrt(periods_per_year)
    sharpe = (daily_ret.mean() * periods_per_year) / vol_annual if vol_annual > 0 else np.nan
    calmar = cagr / abs(max_dd) if max_dd < 0 else np.nan

    metrics = {
        "start_date": str(eq.index[0].date()),
        "end_date": str(eq.index[-1].date()),
        "start_equity": round(eq.iloc[0], 2),
        "end_equity": round(eq.iloc[-1], 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2) if cagr == cagr else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "annual_vol_pct": round(vol_annual * 100, 2) if vol_annual == vol_annual else None,
        "sharpe": round(sharpe, 2) if sharpe == sharpe else None,
        "calmar": round(calmar, 2) if calmar == calmar else None,
    }

    if trade_log is not None and len(trade_log) > 0:
        tl = trade_log.copy()
        tl["date"] = pd.to_datetime(tl["date"])
        if start:
            tl = tl[tl["date"] >= pd.Timestamp(start)]
        if end:
            tl = tl[tl["date"] <= pd.Timestamp(end)]
        metrics["num_trades"] = int(len(tl))
        metrics["num_buys"] = int((tl["action"] == "BUY").sum())
        metrics["num_sells"] = int((tl["action"] == "SELL").sum())
        metrics["total_txn_cost"] = round(tl["cost"].sum(), 2)

    return metrics


def drawdown_series(equity_curve: pd.DataFrame) -> pd.Series:
    eq = equity_curve["equity"]
    running_max = eq.cummax()
    return eq / running_max - 1

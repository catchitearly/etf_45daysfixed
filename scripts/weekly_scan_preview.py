#!/usr/bin/env python3
"""
Runs on the Saturday cron. Uses Friday's close (the latest available data)
to compute this week's RS ranking (for every method in config.RS_METHODS)
and prints/saves a preview of what Monday's rebalance WOULD do, given
current simulated holdings for that method. This is informational only - it
does not change any state. The dashboard's official trade log/holdings are
only updated by scripts/run.py after Monday's close (see monday_refresh
workflow).
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_rotation import config, data, backtest
from etf_rotation.rs import compute_rs, rank_on_date


def _segment_start_for(scan_date):
    """Which reporting segment does scan_date fall in? (segments are run as
    independent simulations, so 'current holdings' must be computed from
    that segment's own start, not from 2018.)"""
    s3, _ = config.SEGMENT_3
    s2, e2 = config.SEGMENT_2
    s1, e1 = config.SEGMENT_1
    if scan_date >= pd_ts(s3):
        return s3
    elif pd_ts(s2) <= scan_date <= pd_ts(e2):
        return s2
    else:
        return s1


def pd_ts(x):
    import pandas as pd
    return pd.Timestamp(x)


def preview_for_method(prices, method, scan_date, rebalance_mode):
    rs = compute_rs(prices, lookback=config.LOOKBACK_DAYS, method=method)
    top = rank_on_date(rs, prices, scan_date, top_n=config.TOP_N)
    top_tickers = [t for t, _ in top]

    seg_start = _segment_start_for(scan_date)
    result = backtest.run_backtest(
        prices, start=seg_start, end=scan_date, top_n=config.TOP_N,
        rs_method=method, rebalance_mode=rebalance_mode,
    )
    current_holdings = set(result["final_portfolio"].holdings.keys())
    target_set = set(top_tickers)

    if rebalance_mode == "full_liquidate":
        if target_set == current_holdings:
            planned_sell, planned_buy, planned_hold = [], [], sorted(current_holdings)
        else:
            planned_sell = sorted(current_holdings)          # everything gets liquidated
            planned_buy = top_tickers                         # then rebought fresh
            planned_hold = []
    else:  # diff
        planned_sell = sorted(current_holdings - target_set)
        planned_buy = [t for t in top_tickers if t not in current_holdings]
        planned_hold = sorted(current_holdings & target_set)

    return {
        "method": method,
        "segment_start_used_for_current_holdings": seg_start,
        "ranking": [{"ticker": t, "rs": round(v, 3), "name": config.NAME_MAP.get(t, t)} for t, v in top],
        "current_holdings": sorted(current_holdings),
        "planned_sell": planned_sell,
        "planned_buy": planned_buy,
        "planned_hold": planned_hold,
    }


def main():
    print(f"[weekly_scan] DATA_SOURCE = {config.DATA_SOURCE!r} "
          f"(set via env var / repo variable; defaults to 'yfinance' if unset)")
    prices = data.fetch_prices(start=config.DATA_START)
    scan_date = prices.index.max()
    rebalance_mode = config.REBALANCE_MODE

    preview = {
        "scan_date": str(scan_date.date()),
        "top_n": config.TOP_N,
        "rebalance_mode": rebalance_mode,
        "note": "Executes at next Monday's close (or next trading day if Monday is a holiday).",
        "methods": {},
    }
    for method in config.RS_METHODS:
        preview["methods"][method] = preview_for_method(prices, method, scan_date, rebalance_mode)

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.SIGNAL_JSON, "w") as f:
        json.dump(preview, f, indent=2, default=str)

    print(json.dumps(preview, indent=2, default=str))


if __name__ == "__main__":
    main()

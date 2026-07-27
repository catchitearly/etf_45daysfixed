#!/usr/bin/env python3
"""
Runs on the Saturday cron. Uses Friday's close (the latest available data)
to compute this week's Mansfield RS ranking and prints/saves a preview of
what Monday's rebalance WOULD do, given current simulated holdings. This is
informational only - it does not change any state. The dashboard's official
trade log/holdings are only updated by scripts/run.py after Monday's close
(see monday_refresh workflow).
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_rotation import config, data, backtest
from etf_rotation.rs import compute_mansfield_rs, rank_on_date


def main():
    prices = data.fetch_prices(start=config.DATA_START)
    scan_date = prices.index.max()

    rs = compute_mansfield_rs(prices, lookback=config.LOOKBACK_DAYS)
    top = rank_on_date(rs, prices, scan_date, top_n=config.TOP_N)
    top_tickers = [t for t, _ in top]

    # simulate up to (but not including) this week, to know current holdings
    result = backtest.run_backtest(prices, start=config.BACKTEST_START, end=scan_date, top_n=config.TOP_N)
    current_holdings = set(result["final_portfolio"].holdings.keys())

    to_sell = current_holdings - set(top_tickers)
    to_buy = [t for t in top_tickers if t not in current_holdings]
    to_hold = current_holdings & set(top_tickers)

    preview = {
        "scan_date": str(scan_date.date()),
        "top_n": config.TOP_N,
        "ranking": [{"ticker": t, "rs": round(v, 3), "name": config.NAME_MAP.get(t, t)} for t, v in top],
        "current_holdings": sorted(current_holdings),
        "planned_sell": sorted(to_sell),
        "planned_buy": to_buy,
        "planned_hold": sorted(to_hold),
        "note": "Executes at next Monday's close (or next trading day if Monday is a holiday).",
    }

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.SIGNAL_JSON, "w") as f:
        json.dump(preview, f, indent=2)

    print(json.dumps(preview, indent=2))


if __name__ == "__main__":
    main()

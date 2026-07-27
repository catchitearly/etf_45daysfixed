#!/usr/bin/env python3
"""
The single entry point used by both GitHub Actions workflows (Saturday scan
preview and Monday post-close refresh), and for local runs.

Since the strategy is fully rules-based and deterministic, we don't maintain
mutable portfolio state between runs. Instead every run:
  1. Fetches/updates the cached price history from Yahoo Finance.
  2. Re-simulates the ENTIRE weekly rotation from BACKTEST_START to today.
  3. Regenerates the GitHub Pages dashboard (docs/index.html) from that
     simulation.

This guarantees the dashboard, trade log and current holdings are always
100% reproducible from (price data + rules) with no drift/corruption risk.
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_rotation import config, data, backtest, dashboard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=config.TOP_N)
    ap.add_argument("--end", default=None, help="YYYY-MM-DD, defaults to latest available")
    ap.add_argument("--no-cache", action="store_true", help="force refetch from yfinance")
    ap.add_argument("--synthetic", action="store_true", help="use synthetic data (for offline testing only)")
    args = ap.parse_args()

    print(f"[run] fetching price data (cache={not args.no_cache}, synthetic={args.synthetic}) ...")
    if args.synthetic:
        prices = data.make_synthetic_prices(start=config.DATA_START, end=args.end or "2026-07-27")
    else:
        prices = data.fetch_prices(start=config.DATA_START, end=args.end, use_cache=not args.no_cache)

    print(f"[run] price data shape: {prices.shape}, last date: {prices.index.max()}")

    print(f"[run] running weekly rotation backtest from {config.BACKTEST_START}, top_n={args.top_n} ...")
    result = backtest.run_backtest(prices, start=config.BACKTEST_START, end=args.end, top_n=args.top_n)

    print(f"[run] {len(result['trade_log'])} trades, "
          f"final equity: Rs {result['equity_curve']['equity'].iloc[-1]:,.0f}")

    result["trade_log"].to_csv(config.TRADE_LOG_CSV, index=False)
    result["equity_curve"].to_csv(config.EQUITY_CSV)

    print("[run] rendering dashboard ...")
    out = dashboard.render_dashboard(result, prices, top_n=args.top_n)
    print(f"[run] dashboard written to {out}")


if __name__ == "__main__":
    main()

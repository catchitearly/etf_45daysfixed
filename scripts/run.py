#!/usr/bin/env python3
"""
The single entry point used by both GitHub Actions workflows (Saturday scan
preview and Monday post-close refresh), and for local runs.

Since the strategy is fully rules-based and deterministic, we don't maintain
mutable portfolio state between runs. Instead every run:
  1. Fetches/updates the cached price history from Yahoo Finance.
  2. Re-simulates EVERY (rs_method x segment) combination as an independent
     simulation -- each segment starts fresh with config.INITIAL_CAPITAL.
  3. Regenerates the GitHub Pages dashboard (docs/index.html) with both RS
     methods shown side-by-side, under identical top_n / rebalance / cost
     rules, so any performance gap is attributable to the signal itself.

This guarantees the dashboard, trade logs and current holdings are always
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
    ap.add_argument("--methods", nargs="+", default=config.RS_METHODS,
                     help="RS methods to compare, e.g. --methods mansfield momentum")
    ap.add_argument("--rebalance-mode", default=config.REBALANCE_MODE,
                     choices=["full_liquidate", "diff"])
    ap.add_argument("--end", default=None, help="YYYY-MM-DD, defaults to latest available")
    ap.add_argument("--no-cache", action="store_true", help="force refetch from yfinance")
    ap.add_argument("--synthetic", action="store_true", help="use synthetic data (for offline testing only)")

    # -- risk overlays (both default to config.*_ENABLED, i.e. OFF, unless a flag is passed) --
    ap.add_argument("--stop-loss", dest="stop_loss", action="store_true", default=None,
                     help=f"enable daily hard stop-loss (default: {config.STOP_LOSS_ENABLED})")
    ap.add_argument("--no-stop-loss", dest="stop_loss", action="store_false")
    ap.add_argument("--stop-loss-pct", type=float, default=None,
                     help=f"force-exit a position down more than this %% from entry (default: {config.STOP_LOSS_PCT})")
    ap.add_argument("--parabolic-filter", dest="parabolic_filter", action="store_true", default=None,
                     help=f"enable overextended-RS exclusion filter (default: {config.PARABOLIC_FILTER_ENABLED})")
    ap.add_argument("--no-parabolic-filter", dest="parabolic_filter", action="store_false")
    ap.add_argument("--parabolic-zscore", type=float, default=None,
                     help=f"z-score threshold above which a ticker is excluded as overextended (default: {config.PARABOLIC_ZSCORE_THRESHOLD})")
    ap.add_argument("--parabolic-window", type=int, default=None,
                     help=f"trailing days used for the RS z-score's own mean/std (default: {config.PARABOLIC_ZSCORE_WINDOW})")
    args = ap.parse_args()

    print(f"[run] DATA_SOURCE = {config.DATA_SOURCE!r} "
          f"(set via env var / repo variable; defaults to 'yfinance' if unset)")
    print(f"[run] fetching price data (cache={not args.no_cache}, synthetic={args.synthetic}) ...")
    if args.synthetic:
        prices = data.make_synthetic_prices(start=config.DATA_START, end=args.end or "2026-07-27")
    else:
        prices = data.fetch_prices(start=config.DATA_START, end=args.end, use_cache=not args.no_cache)

    print(f"[run] price data shape: {prices.shape}, last date: {prices.index.max()}")
    cache_path = config.PRICE_CACHE_FYERS if config.DATA_SOURCE == "fyers" else config.PRICE_CACHE
    print(f"[run] (cache file: {cache_path})")
    print(f"[run] top_n={args.top_n}, methods={args.methods}, rebalance_mode={args.rebalance_mode}")

    stop_loss_enabled = config.STOP_LOSS_ENABLED if args.stop_loss is None else args.stop_loss
    parabolic_enabled = config.PARABOLIC_FILTER_ENABLED if args.parabolic_filter is None else args.parabolic_filter
    print(f"[run] stop_loss_enabled={stop_loss_enabled} "
          f"(pct={args.stop_loss_pct if args.stop_loss_pct is not None else config.STOP_LOSS_PCT})")
    print(f"[run] parabolic_filter_enabled={parabolic_enabled} "
          f"(zscore={args.parabolic_zscore if args.parabolic_zscore is not None else config.PARABOLIC_ZSCORE_THRESHOLD}, "
          f"window={args.parabolic_window if args.parabolic_window is not None else config.PARABOLIC_ZSCORE_WINDOW})")
    print("[run] running independent simulations per (method x segment) ...")

    out = dashboard.render_dashboard(
        prices, top_n=args.top_n, methods=args.methods, rebalance_mode=args.rebalance_mode,
        stop_loss_enabled=args.stop_loss, stop_loss_pct=args.stop_loss_pct,
        parabolic_filter_enabled=args.parabolic_filter, parabolic_zscore_threshold=args.parabolic_zscore,
        parabolic_zscore_window=args.parabolic_window,
    )
    print(f"[run] dashboard written to {out}")


if __name__ == "__main__":
    main()

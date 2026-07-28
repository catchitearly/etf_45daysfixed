#!/usr/bin/env python3
"""
Runs the full curve-fit-avoidance suite and writes data/robustness.json,
which the dashboard's "Robustness" tab reads. This is deliberately kept
OUT of the weekly Monday refresh (scripts/run.py) because it's much more
expensive: 16 lookbacks x 2 methods for the main sweep, another 16x2 for
the walk-forward training window, plus bootstrap/shuffle resampling.

Run it manually (or on a slower schedule, e.g. monthly) via:
    python scripts/run_robustness.py
or the "Robustness Sweep" GitHub Actions workflow (workflow_dispatch).
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_rotation import config, data
from etf_rotation.robustness import (
    run_lookback_sweep, walk_forward_validate, regime_split_metrics,
    extract_round_trip_trades, bootstrap_trade_distribution, shuffle_order_test,
)
from etf_rotation.data_quality import flag_suspicious_moves, check_portfolio_invariants


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="use synthetic data (offline testing only)")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    print("[robustness] fetching price data ...")
    if args.synthetic:
        prices = data.make_synthetic_prices(start=config.DATA_START, end=args.end or "2026-07-27")
    else:
        prices = data.fetch_prices(start=config.DATA_START, end=args.end)
    end_date = str(prices.index.max().date())
    print(f"[robustness] price data shape: {prices.shape}, last date: {end_date}")

    # -- 0. Data quality pass --------------------------------------------
    print("[robustness] checking for suspicious price ticks ...")
    suspicious = flag_suspicious_moves(prices, threshold=0.20)
    print(f"[robustness]   {len(suspicious)} single-day moves > 20% flagged")

    # -- 1. Full-period lookback stability sweep --------------------------
    print(f"[robustness] running lookback sweep {config.LOOKBACK_SWEEP[0]}-{config.LOOKBACK_SWEEP[-1]} "
          f"step 5, both methods, full period {config.BACKTEST_START}..{end_date} ...")
    sweep_df, best_full_period = run_lookback_sweep(
        prices, start=config.BACKTEST_START, end=end_date,
    )
    print("[robustness]   done:", len(sweep_df), "runs")

    invariant_issues = []
    for method, (sharpe, lb, result) in best_full_period.items():
        issues = check_portfolio_invariants(result["equity_curve"])
        for i in issues:
            i["method"] = method
            i["lookback"] = lb
        invariant_issues.extend(issues)
    if invariant_issues:
        print(f"[robustness]   WARNING: {len(invariant_issues)} portfolio invariant violations found!")
    else:
        print("[robustness]   portfolio invariants clean (no negative/NaN equity in best-lookback runs)")

    # -- 2. Walk-forward validation (train 2018-2022, test unseen) --------
    print("[robustness] running walk-forward validation (train 2018-2022, lock, test 2023-24/2025/2026) ...")
    wf = walk_forward_validate(prices, train_start="2018-01-01", train_end="2022-12-31")
    for method, r in wf.items():
        print(f"[robustness]   {method}: locked lookback = {r['locked_lookback']} "
              f"(train Sharpe {r['train_sharpe']})")

    # -- 2b. Regime-split testing, using the walk-forward LOCKED lookback -
    print("[robustness] running regime-split tests on locked lookbacks ...")
    locked = {m: wf[m]["locked_lookback"] for m in wf}
    regimes = regime_split_metrics(prices, locked)

    # -- 3-5. Round-trip trades -> bootstrap + shuffle, for the FULL-PERIOD
    #         best-Sharpe config per method (found in step 1) -------------
    print("[robustness] extracting round-trip trades + running bootstrap/shuffle tests ...")
    bootstrap_results = {}
    shuffle_results = {}
    best_config_summary = {}
    for method, (sharpe, lb, result) in best_full_period.items():
        trades_df = extract_round_trip_trades(result["trade_log"])
        returns = trades_df["return_pct"].tolist() if len(trades_df) else []
        bootstrap_results[method] = bootstrap_trade_distribution(returns, n_boot=1000)
        shuffle_results[method] = shuffle_order_test(returns, n_shuffle=500)
        best_config_summary[method] = {
            "best_lookback_full_period": lb,
            "full_period_sharpe": round(sharpe, 3),
            "n_round_trip_trades": len(trades_df),
        }
        print(f"[robustness]   {method}: best full-period lookback={lb}, "
              f"{len(trades_df)} round-trip trades")

    payload = {
        "generated_at": end_date,
        "lookback_sweep_range": [config.LOOKBACK_SWEEP[0], config.LOOKBACK_SWEEP[-1], 5],
        "top_n": config.TOP_N,
        "rebalance_mode": config.REBALANCE_MODE,
        "data_quality": {
            "suspicious_moves_threshold_pct": 20,
            "flags": suspicious[:100],  # cap payload size
            "n_flags": len(suspicious),
        },
        "portfolio_invariant_issues": invariant_issues,
        "sweep": sweep_df.to_dict(orient="records"),
        "best_full_period_config": best_config_summary,
        "walk_forward": {
            method: {
                "train_window": r["train_window"],
                "locked_lookback": r["locked_lookback"],
                "train_sharpe": r["train_sharpe"],
                "train_sweep": r["train_sweep"],
                "test_results": r["test_results"],
            } for method, r in wf.items()
        },
        "regime_split": regimes,
        "bootstrap": bootstrap_results,
        "shuffle_test": shuffle_results,
    }

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.ROBUSTNESS_JSON, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[robustness] written to {config.ROBUSTNESS_JSON}")


if __name__ == "__main__":
    main()

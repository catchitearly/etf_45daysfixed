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
from etf_rotation.data_quality import flag_suspicious_moves, flag_multiday_moves, dump_flags_csv


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="use synthetic data (offline testing only)")
    ap.add_argument("--end", default=None)
    ap.add_argument("--stop-loss", dest="stop_loss", action="store_true", default=None,
                     help=f"enable daily hard stop-loss for the sweep (default: {config.STOP_LOSS_ENABLED})")
    ap.add_argument("--no-stop-loss", dest="stop_loss", action="store_false")
    ap.add_argument("--stop-loss-pct", type=float, default=None)
    ap.add_argument("--parabolic-filter", dest="parabolic_filter", action="store_true", default=None,
                     help=f"enable overextended-RS exclusion filter for the sweep (default: {config.PARABOLIC_FILTER_ENABLED})")
    ap.add_argument("--no-parabolic-filter", dest="parabolic_filter", action="store_false")
    ap.add_argument("--parabolic-zscore", type=float, default=None)
    ap.add_argument("--parabolic-window", type=int, default=None)
    ap.add_argument("--wf-selection-metric", default=None, choices=["sharpe", "calmar", "sharpe_dd_penalty"],
                     help=f"how walk-forward locks a lookback from training data (default: {config.WALK_FORWARD_SELECTION_METRIC})")
    ap.add_argument("--wf-dd-penalty-weight", type=float, default=None,
                     help="only used by --wf-selection-metric sharpe_dd_penalty")
    ap.add_argument("--wf-report-lookbacks", default=None,
                     help="comma-separated lookbacks to report full train+test metrics for, e.g. 50,100,150,200 "
                          f"(default: {','.join(str(x) for x in config.WALK_FORWARD_REPORT_LOOKBACKS)})")
    ap.add_argument("--lookback-min", type=int, default=None, help=f"default: {config.LOOKBACK_SWEEP_MIN}")
    ap.add_argument("--lookback-max", type=int, default=None, help=f"default: {config.LOOKBACK_SWEEP_MAX}")
    ap.add_argument("--lookback-step", type=int, default=None, help=f"default: {config.LOOKBACK_SWEEP_STEP}")
    args = ap.parse_args()
    wf_report_lookbacks = ([int(x) for x in args.wf_report_lookbacks.split(",")]
                            if args.wf_report_lookbacks else None)
    lookback_sweep = list(range(
        args.lookback_min or config.LOOKBACK_SWEEP_MIN,
        (args.lookback_max or config.LOOKBACK_SWEEP_MAX) + 1,
        args.lookback_step or config.LOOKBACK_SWEEP_STEP,
    ))

    print(f"[robustness] DATA_SOURCE = {config.DATA_SOURCE!r} "
          f"(set via env var / repo variable; defaults to 'yfinance' if unset)")
    print("[robustness] fetching price data ...")
    if args.synthetic:
        prices = data.make_synthetic_prices(start=config.DATA_START, end=args.end or "2026-07-27")
    else:
        prices = data.fetch_prices(start=config.DATA_START, end=args.end)
    end_date = str(prices.index.max().date())
    print(f"[robustness] price data shape: {prices.shape}, last date: {end_date}")
    cache_path = config.PRICE_CACHE_FYERS if config.DATA_SOURCE == "fyers" else config.PRICE_CACHE
    print(f"[robustness] (cache file: {cache_path})")

    # -- 0. Data quality pass ---------------------------------------------
    print("[robustness] checking for suspicious price ticks (single-day) ...")
    single_day_flags = flag_suspicious_moves(prices, threshold=0.20)
    print(f"[robustness]   {len(single_day_flags)} single-day moves > 20% flagged")

    print(f"[robustness] checking for gradual/multi-day drift ({config.MULTIDAY_WINDOWS}-day windows, "
          f"{config.MULTIDAY_MOVE_THRESHOLD*100:.0f}% threshold) -- catches bad ticks a single-day check misses ...")
    multiday_flags = flag_multiday_moves(prices)
    print(f"[robustness]   {len(multiday_flags)} multi-day cumulative moves flagged")

    all_flags_for_csv = (
        [{"check": "single_day", **f} for f in single_day_flags]
        + [{"check": "multi_day", **f} for f in multiday_flags]
    )
    flags_csv_path = dump_flags_csv(all_flags_for_csv, config.DATA_QUALITY_FLAGS_CSV)
    print(f"[robustness]   full flag list ({len(all_flags_for_csv)} rows) dumped to {flags_csv_path}")

    # -- 1. Full-period lookback stability sweep --------------------------
    print(f"[robustness] running lookback sweep {lookback_sweep[0]}-{lookback_sweep[-1]} "
          f"step {lookback_sweep[1]-lookback_sweep[0] if len(lookback_sweep)>1 else '-'}, "
          f"both methods, full period {config.BACKTEST_START}..{end_date} ...")
    print(f"[robustness] any run with max drawdown worse than {config.CATASTROPHIC_DD_THRESHOLD_PCT}% "
          f"will have its full trade log + worst trades auto-dumped to {config.CATASTROPHIC_RUNS_DIR}/")
    stop_loss_enabled = config.STOP_LOSS_ENABLED if args.stop_loss is None else args.stop_loss
    parabolic_enabled = config.PARABOLIC_FILTER_ENABLED if args.parabolic_filter is None else args.parabolic_filter
    print(f"[robustness] stop_loss_enabled={stop_loss_enabled}, parabolic_filter_enabled={parabolic_enabled}")

    sweep_df, best_full_period, invariant_issues, catastrophic_runs = run_lookback_sweep(
        prices, lookbacks=lookback_sweep, start=config.BACKTEST_START, end=end_date,
        stop_loss_enabled=args.stop_loss, stop_loss_pct=args.stop_loss_pct,
        parabolic_filter_enabled=args.parabolic_filter, parabolic_zscore_threshold=args.parabolic_zscore,
        parabolic_zscore_window=args.parabolic_window,
    )
    print("[robustness]   done:", len(sweep_df), "runs (invariants re-checked on ALL of them, not just the best)")

    if invariant_issues:
        print(f"[robustness]   WARNING: {len(invariant_issues)} portfolio invariant violations found!")
        for iss in invariant_issues:
            print(f"[robustness]     {iss}")
    else:
        print("[robustness]   portfolio invariants clean across all sweep runs")

    if catastrophic_runs:
        print(f"[robustness]   WARNING: {len(catastrophic_runs)} run(s) breached the "
              f"{config.CATASTROPHIC_DD_THRESHOLD_PCT}% drawdown threshold -- auto-dumped for inspection:")
        for c in catastrophic_runs:
            print(f"[robustness]     {c['method']} lookback={c['lookback']}: "
                  f"max_dd={c['max_drawdown_pct']}%, {c['n_trades_return_below_neg50pct']} trades "
                  f"with return < -50%, dumped to {c['trade_log_csv']} and {c['worst_trades_csv']}")
    else:
        print(f"[robustness]   no runs breached the {config.CATASTROPHIC_DD_THRESHOLD_PCT}% drawdown threshold")

    # -- 2. Walk-forward validation (train 2018-2022, test unseen) --------
    print("[robustness] running walk-forward validation (train 2018-2022, lock, test 2023-24/2025/2026) ...")
    wf_selection_metric = args.wf_selection_metric or config.WALK_FORWARD_SELECTION_METRIC
    print(f"[robustness] walk-forward selection metric: {wf_selection_metric} "
          f"(candidates reported: {wf_report_lookbacks or config.WALK_FORWARD_REPORT_LOOKBACKS})")
    wf = walk_forward_validate(
        prices, lookbacks=lookback_sweep, train_start="2018-01-01", train_end="2022-12-31",
        selection_metric=args.wf_selection_metric, dd_penalty_weight=args.wf_dd_penalty_weight,
        report_lookbacks=wf_report_lookbacks,
    )
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
        "data_source": config.DATA_SOURCE,
        "lookback_sweep_range": [lookback_sweep[0], lookback_sweep[-1],
                                  (lookback_sweep[1] - lookback_sweep[0]) if len(lookback_sweep) > 1 else 0],
        "top_n": config.TOP_N,
        "rebalance_mode": config.REBALANCE_MODE,
        "catastrophic_dd_threshold_pct": config.CATASTROPHIC_DD_THRESHOLD_PCT,
        "data_quality": {
            "single_day_threshold_pct": 20,
            "multiday_windows": list(config.MULTIDAY_WINDOWS),
            "multiday_threshold_pct": config.MULTIDAY_MOVE_THRESHOLD * 100,
            "single_day_flags": single_day_flags[:100],   # capped for dashboard payload size
            "multiday_flags": multiday_flags[:100],
            "n_single_day_flags": len(single_day_flags),
            "n_multiday_flags": len(multiday_flags),
            "flags_csv_path": flags_csv_path,
        },
        "portfolio_invariant_issues": invariant_issues,
        "catastrophic_runs": catastrophic_runs,
        "sweep": sweep_df.to_dict(orient="records"),
        "best_full_period_config": best_config_summary,
        "walk_forward": {
            method: {
                "train_window": r["train_window"],
                "selection_metric": r["selection_metric"],
                "locked_lookback": r["locked_lookback"],
                "locked_score": r["locked_score"],
                "train_sharpe": r["train_sharpe"],
                "train_sweep": r["train_sweep"],
                "test_results": r["test_results"],
                "candidates_report": r["candidates_report"],
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

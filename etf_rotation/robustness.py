"""
Curve-fit-avoidance suite. Implements, against THIS strategy's actual
mechanics (long-only, whole-unit, cash-settled, no shorts/no F&O):

  1. Parameter stability sweep: run every lookback in config.LOOKBACK_SWEEP
     (15..90 in steps of 5) for both RS methods over the full period. A real
     edge shows a smooth hill in Sharpe/CAGR vs. lookback; a spike at one
     specific value with noise on either side is a curve-fit artifact.

  2. Walk-forward validation: pick the best-Sharpe lookback using ONLY a
     2018-2022 training window, lock it, then test that exact (unchanged)
     config on 2023-2024, 2025, and 2026 without re-tuning. If it doesn't
     hold up walking forward, the original result was fit to the training
     window.

  3. Round-trip trade extraction: pairs each BUY with its subsequent SELL
     for the same ticker (this strategy never holds overlapping lots of the
     same ticker, so simple sequential pairing is exact) to get genuine
     per-trade returns for the tests below.

  4. Bootstrap resampling: resample trades with replacement 1000x and look
     at the DISTRIBUTION of return/Sharpe-like stats, not just the single
     point estimate. If the 5th percentile is near zero or negative, the
     edge isn't statistically robust.

  5. Trade-order shuffle test: reorder the same trades' returns randomly
     500x and rebuild a synthetic equity curve each time. If Max Drawdown
     swings wildly with the order, the risk profile is largely
     luck-of-sequencing rather than a property of the strategy.
"""
import os

import numpy as np
import pandas as pd

from . import config
from .backtest import run_backtest, compute_metrics
from .data_quality import check_portfolio_invariants


# ---------------------------------------------------------------------------
# 1. Lookback stability sweep
# ---------------------------------------------------------------------------
def run_lookback_sweep(prices, methods=None, lookbacks=None, top_n=config.TOP_N,
                        rebalance_mode=None, start=config.BACKTEST_START, end=None,
                        initial_capital=config.INITIAL_CAPITAL,
                        catastrophic_dd_threshold=config.CATASTROPHIC_DD_THRESHOLD_PCT,
                        stop_loss_enabled=None, stop_loss_pct=None,
                        parabolic_filter_enabled=None, parabolic_zscore_threshold=None,
                        parabolic_zscore_window=None):
    """
    Runs every (method, lookback) combination as an independent simulation
    over [start, end].

    For EVERY run (not just the best one per method) this:
      - re-checks the portfolio-invariant assertions (equity never negative/
        NaN) independently of the real-time asserts in Portfolio itself
      - if max drawdown breaches `catastrophic_dd_threshold` (e.g. a run
        that lost >80% when neighboring lookbacks lost <20%), immediately
        dumps its full trade log + worst round-trip trades to disk via
        dump_catastrophic_run(), so nothing anomalous can pass through
        silently just because it wasn't the "best" run kept for later steps.

    Returns (sweep_df, best_result, invariant_issues, catastrophic_runs):
      - best_result: {method: (sharpe, lookback, full_backtest_result)} for
        the highest-Sharpe lookback per method (kept for the bootstrap/
        shuffle tests below, without re-running anything).
      - invariant_issues: list of violation dicts across ALL 32 runs, each
        tagged with method/lookback.
      - catastrophic_runs: list of summary dicts (one per run that breached
        the drawdown threshold), including paths to the dumped CSVs and the
        worst trades inline for direct dashboard display.
    """
    methods = methods or config.RS_METHODS
    lookbacks = lookbacks or config.LOOKBACK_SWEEP
    rebalance_mode = rebalance_mode or config.REBALANCE_MODE

    rows = []
    best = {}
    invariant_issues = []
    catastrophic_runs = []

    for method in methods:
        for lb in lookbacks:
            result = run_backtest(
                prices, start=start, end=end, top_n=top_n, lookback=lb,
                initial_capital=initial_capital, rs_method=method, rebalance_mode=rebalance_mode,
                stop_loss_enabled=stop_loss_enabled, stop_loss_pct=stop_loss_pct,
                parabolic_filter_enabled=parabolic_filter_enabled,
                parabolic_zscore_threshold=parabolic_zscore_threshold,
                parabolic_zscore_window=parabolic_zscore_window,
            )
            metrics = compute_metrics(result["equity_curve"], result["trade_log"])
            rows.append({"method": method, "lookback": lb, **metrics})

            issues = check_portfolio_invariants(result["equity_curve"])
            for iss in issues:
                iss["method"] = method
                iss["lookback"] = lb
            invariant_issues.extend(issues)

            max_dd = metrics.get("max_drawdown_pct")
            if max_dd is not None and max_dd < catastrophic_dd_threshold:
                summary = dump_catastrophic_run(method, lb, result, metrics)
                catastrophic_runs.append(summary)

            sharpe = metrics.get("sharpe")
            sharpe_val = sharpe if sharpe is not None else -999
            if method not in best or sharpe_val > best[method][0]:
                best[method] = (sharpe_val, lb, result)

    return pd.DataFrame(rows), best, invariant_issues, catastrophic_runs


def dump_catastrophic_run(method: str, lookback: int, result: dict, metrics: dict,
                           out_dir=None, n_worst=15):
    """
    For a sweep run whose drawdown breached the catastrophic threshold:
    dumps its FULL trade log and its worst N round-trip trades to CSV for
    manual inspection, and returns a JSON-friendly summary (including the
    worst trades inline, so the dashboard can show them without needing to
    open a CSV).
    """
    out_dir = out_dir or config.CATASTROPHIC_RUNS_DIR
    os.makedirs(out_dir, exist_ok=True)

    tag = f"{method}_lb{lookback}"
    trade_log_path = os.path.join(out_dir, f"{tag}_full_tradelog.csv")
    worst_trades_path = os.path.join(out_dir, f"{tag}_worst_trades.csv")

    trade_log = result["trade_log"]
    if trade_log is not None and len(trade_log) > 0:
        trade_log.to_csv(trade_log_path, index=False)
    else:
        pd.DataFrame(columns=["date", "action", "ticker", "units", "price", "gross", "cost"]).to_csv(trade_log_path, index=False)

    round_trips = extract_round_trip_trades(trade_log)
    if len(round_trips) > 0:
        worst = round_trips.sort_values("return_pct", ascending=True).head(n_worst)
        worst.to_csv(worst_trades_path, index=False)
        worst_records = worst.to_dict(orient="records")
        n_extreme = int((round_trips["return_pct"] < -50).sum())
    else:
        pd.DataFrame(columns=["ticker", "entry_date", "exit_date", "holding_days",
                               "entry_price", "exit_price", "return_pct"]).to_csv(worst_trades_path, index=False)
        worst_records = []
        n_extreme = 0

    return {
        "method": method,
        "lookback": lookback,
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "cagr_pct": metrics.get("cagr_pct"),
        "total_return_pct": metrics.get("total_return_pct"),
        "n_round_trip_trades": int(len(round_trips)),
        "n_trades_return_below_neg50pct": n_extreme,
        "trade_log_csv": trade_log_path,
        "worst_trades_csv": worst_trades_path,
        "worst_trades": worst_records,
    }


# ---------------------------------------------------------------------------
# 2. Walk-forward validation
# ---------------------------------------------------------------------------
def walk_forward_validate(prices, methods=None, lookbacks=None, top_n=config.TOP_N,
                           rebalance_mode=None, train_start="2018-01-01", train_end="2022-12-31",
                           test_segments=None, initial_capital=config.INITIAL_CAPITAL):
    """
    For each method: sweep lookbacks on [train_start, train_end] ONLY, lock
    the best-Sharpe lookback, then run that exact config (no re-tuning) on
    each of `test_segments` = [(label, start, end), ...] as independent
    ₹10L simulations.
    """
    methods = methods or config.RS_METHODS
    lookbacks = lookbacks or config.LOOKBACK_SWEEP
    rebalance_mode = rebalance_mode or config.REBALANCE_MODE
    if test_segments is None:
        test_segments = [
            ("2023_2024", "2023-01-01", "2024-12-31"),
            ("2025", config.SEGMENT_2[0], config.SEGMENT_2[1]),
            ("2026_ytd", config.SEGMENT_3[0], config.SEGMENT_3[1] or str(prices.index.max().date())),
        ]

    out = {}
    for method in methods:
        train_rows = []
        best_lb, best_sharpe = None, -999
        for lb in lookbacks:
            result = run_backtest(
                prices, start=train_start, end=train_end, top_n=top_n, lookback=lb,
                rs_method=method, rebalance_mode=rebalance_mode, initial_capital=initial_capital,
            )
            metrics = compute_metrics(result["equity_curve"], result["trade_log"])
            train_rows.append({"lookback": lb, **metrics})
            sharpe = metrics.get("sharpe")
            sharpe_val = sharpe if sharpe is not None else -999
            if sharpe_val > best_sharpe:
                best_sharpe, best_lb = sharpe_val, lb

        test_results = {}
        for label, seg_start, seg_end in test_segments:
            result = run_backtest(
                prices, start=seg_start, end=seg_end, top_n=top_n, lookback=best_lb,
                rs_method=method, rebalance_mode=rebalance_mode, initial_capital=initial_capital,
            )
            test_results[label] = compute_metrics(result["equity_curve"], result["trade_log"])

        out[method] = {
            "train_window": [train_start, train_end],
            "locked_lookback": best_lb,
            "train_sharpe": round(best_sharpe, 3) if best_sharpe != -999 else None,
            "train_sweep": train_rows,
            "test_results": test_results,
        }
    return out


def regime_split_metrics(prices, locked_lookbacks: dict, top_n=config.TOP_N,
                          rebalance_mode=None, initial_capital=config.INITIAL_CAPITAL):
    """
    Runs each method's LOCKED (walk-forward-chosen) lookback across distinct
    market regimes as independent ₹10L simulations, so a method that only
    works in trending markets doesn't get to hide behind a good blended
    CAGR. Regime windows are broad-strokes NSE/Nifty regime labels, not
    strategy-tuned:
      - 2018            : choppy/correction (Jan 2018 melt-up unwind, NBFC crisis)
      - 2019            : mostly sideways/rangebound into pre-COVID
      - 2020-2021       : COVID crash + sharp bull recovery (high trend content)
      - 2022            : bear/choppy (rate-hike drawdown)
      - 2023-2024        : bull/grind-up
    """
    regimes = [
        ("2018_choppy", "2018-01-01", "2018-12-31"),
        ("2019_sideways", "2019-01-01", "2019-12-31"),
        ("2020_2021_bull", "2020-01-01", "2021-12-31"),
        ("2022_bear_choppy", "2022-01-01", "2022-12-31"),
        ("2023_2024_bull", "2023-01-01", "2024-12-31"),
    ]
    rebalance_mode = rebalance_mode or config.REBALANCE_MODE
    out = {}
    for method, lb in locked_lookbacks.items():
        out[method] = {"locked_lookback": lb, "regimes": {}}
        for label, start, end in regimes:
            result = run_backtest(
                prices, start=start, end=end, top_n=top_n, lookback=lb,
                rs_method=method, rebalance_mode=rebalance_mode, initial_capital=initial_capital,
            )
            out[method]["regimes"][label] = compute_metrics(result["equity_curve"], result["trade_log"])
    return out
def extract_round_trip_trades(trade_log: pd.DataFrame) -> pd.DataFrame:
    """
    Pairs each BUY with the NEXT SELL of the same ticker, in the order
    trades actually occurred. Because `rebalance_full_liquidate` always
    sells a ticker's ENTIRE position before ever buying it again, a ticker
    never has more than one open lot at a time -- so this simple sequential
    pairing is exact, not an approximation.
    """
    cols = ["ticker", "entry_date", "exit_date", "holding_days", "entry_price", "exit_price", "return_pct"]
    if trade_log is None or len(trade_log) == 0:
        return pd.DataFrame(columns=cols)

    open_lots = {}
    rows = []
    for r in trade_log.to_dict(orient="records"):
        t = r["ticker"]
        if r["action"] == "BUY":
            open_lots[t] = r
        elif r["action"] == "SELL":
            buy = open_lots.pop(t, None)
            if buy is None:
                continue  # position was already open before the simulation window started
            entry_cost = buy["gross"] + buy["cost"]
            exit_proceeds = r["gross"] - r["cost"]
            ret = (exit_proceeds - entry_cost) / entry_cost if entry_cost else None
            entry_dt = pd.Timestamp(buy["date"])
            exit_dt = pd.Timestamp(r["date"])
            rows.append({
                "ticker": t,
                "entry_date": str(entry_dt.date()),
                "exit_date": str(exit_dt.date()),
                "holding_days": (exit_dt - entry_dt).days,
                "entry_price": buy["price"],
                "exit_price": r["price"],
                "return_pct": round(ret * 100, 3) if ret is not None else None,
            })
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# 4. Bootstrap resampling
# ---------------------------------------------------------------------------
def bootstrap_trade_distribution(returns_pct, n_boot=1000, seed=42):
    """
    Resamples the trade-return series WITH REPLACEMENT n_boot times and
    reports the distribution (not just the point estimate) of: mean
    per-trade return, a Sharpe-like stat (mean/std across the sample), and
    total compounded return if those trades had occurred in that resampled
    order. A 5th percentile near/below zero means the observed edge could
    plausibly be noise.
    """
    arr = np.asarray([v for v in returns_pct if v is not None], dtype=float) / 100.0
    n = len(arr)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)

    means, sharpes, totals = [], [], []
    for _ in range(n_boot):
        sample = rng.choice(arr, size=n, replace=True)
        means.append(sample.mean())
        std = sample.std()
        sharpes.append(sample.mean() / std if std > 0 else 0.0)
        totals.append(float(np.prod(1 + sample) - 1))

    def pctiles(a):
        return {p: round(float(np.percentile(a, p)), 4) for p in (5, 25, 50, 75, 95)}

    return {
        "n_trades": n,
        "n_boot": n_boot,
        "mean_return_pct": {k: round(v * 100, 3) for k, v in pctiles(means).items()},
        "sharpe_like": pctiles(sharpes),
        "total_compounded_return_pct": {k: round(v * 100, 2) for k, v in pctiles(totals).items()},
    }


# ---------------------------------------------------------------------------
# 5. Trade-order shuffle test
# ---------------------------------------------------------------------------
def shuffle_order_test(returns_pct, n_shuffle=500, seed=42):
    """
    Randomly reorders the SAME set of trade returns n_shuffle times,
    compounds each ordering into a synthetic equity curve, and reports the
    distribution of Max Drawdown and a Calmar-like ratio. Wide spread here
    means the strategy's headline drawdown/Calmar numbers are heavily
    dependent on the luck of *when* winners and losers happened to land,
    not a stable property of the strategy.
    """
    arr = np.asarray([v for v in returns_pct if v is not None], dtype=float) / 100.0
    n = len(arr)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)

    max_dds, calmars, totals = [], [], []
    for _ in range(n_shuffle):
        order = rng.permutation(arr)
        equity = np.cumprod(1 + order)
        running_max = np.maximum.accumulate(equity)
        dd = equity / running_max - 1
        max_dd = float(dd.min())
        total_ret = float(equity[-1] - 1)
        max_dds.append(max_dd)
        totals.append(total_ret)
        if max_dd < 0:
            calmars.append(total_ret / abs(max_dd))

    def pctiles(a):
        return {p: round(float(np.percentile(a, p)), 4) for p in (5, 25, 50, 75, 95)}

    return {
        "n_trades": n,
        "n_shuffle": n_shuffle,
        "max_dd_pct": {k: round(v * 100, 2) for k, v in pctiles(max_dds).items()},
        "total_return_pct": {k: round(v * 100, 2) for k, v in pctiles(totals).items()},
        "calmar_like": pctiles(calmars) if calmars else None,
    }

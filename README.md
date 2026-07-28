# ETF Relative-Strength Rotation (NSE)

A systematic sector-rotation strategy across 24 NSE-listed ETFs. It ranks all
ETFs weekly by relative strength, holds the top-N strongest, and — on the
dashboard — runs **two RS signal styles side-by-side under identical
top-N / rebalance / cost rules**, so any performance difference between them
is attributable to the signal itself and not to some other config quirk.

**Live dashboard:** enable GitHub Pages (see setup below) and it'll be at
`https://<your-username>.github.io/<repo-name>/`

---

## Strategy rules

1. **Universe:** 24 NSE ETFs (gold, silver, broad market, sectors, factor
   indices, gilt/liquid, international) — see `etf_rotation/config.py`.
2. **Relative Strength — two methods compared side-by-side:**
   - **Mansfield RS** (`rs.compute_mansfield_rs`): for each ETF, build an
     equal-weight basket of all *other* ETFs (each rebased to start at 100),
     then `RS = (ratio / SMA(ratio, 45d) - 1) * 100` where `ratio =
     ETF_price / basket_price`. Smoothed — only turns positive once
     outperformance has been sustained long enough to pull the 45-day
     average of the ratio up with it.
   - **Momentum RS** (`rs.compute_momentum_rs`): `score_i = raw 45-day
     return of ETF i − average 45-day return of all other ETFs`. Unsmoothed
     — reacts to the current 45-day window immediately, no lag.
   - Both are computed identically otherwise (same peer universe, same
     45-day window, same eligibility rule) so the comparison isolates the
     effect of the signal's *shape*, not its inputs.
3. **Weekly scan (Saturday):** rank all eligible ETFs (must have ≥65 trading
   days of history) by RS descending, as of the most recent close (Friday,
   or Thursday if Friday was a holiday). Top **N** (default **3**,
   configurable) = "in trend."
4. **Execution (Monday close) — `full_liquidate` mode (default):**
   compare Saturday's top-N to current holdings. If the **top-N set is
   identical** to current holdings, nothing trades that week. If it
   changed **at all** (even by one name), **every current holding is sold**
   and the new top-N is **bought fresh at equal weight** — continuing names
   are not left alone, they're sold and rebought too. (A gentler `diff`
   mode — only trade the names that actually changed, leave continuing
   holdings untouched — is also implemented in `portfolio.py` and
   selectable via `--rebalance-mode diff`.)
   - If Monday is an NSE holiday, executes at the next trading day's close.
5. **Costs:** 0.05% per executed trade (buy or sell) to approximate
   brokerage + STT + slippage.
6. **Capital:** ₹10,00,000, whole-unit position sizing (no fractional
   units). **Each reporting segment (2018–2024 / 2025 / 2026 YTD) is run as
   an independent simulation, restarting fresh with ₹10,00,000** — segment
   results are NOT a slice of one continuously-compounding curve.

## Dashboard

The dashboard has one tab per reporting segment (Backtest 2018–2024,
Forward Test 2025, Forward Test 2026 YTD). Each tab shows **Mansfield RS and
Momentum RS side-by-side**: metric cards for both (CAGR, total return, max
drawdown, Sharpe, Calmar, trade count), plus an **overlaid equity curve**
and **overlaid drawdown chart** — both methods on the same axes, same
segment, same rules, so you can see the genuine signal-vs-signal difference
at a glance. Below that: a **Method Comparison Summary** table across all
6 (segment × method) combinations, then RS leaderboards, current holdings,
signal logs, and trade logs — each with a sub-tab to switch between the two
methods.

| Segment | Period | Nature |
|---|---|---|
| Backtest | 2018-01-01 → 2024-12-31 | In-sample, independent ₹10L run |
| Forward Test 2025 | 2025-01-01 → 2025-12-31 | Out-of-sample, independent ₹10L run |
| Forward Test 2026 | 2026-01-01 → today | Out-of-sample, ongoing, independent ₹10L run |

## Robustness & curve-fit-avoidance suite

A separate, deliberately-not-weekly script/workflow that stress-tests the
strategy so a good backtest number can't hide overfitting or luck:

| Check | What it does | Where |
|---|---|---|
| **Lookback stability sweep** | Runs lookback = 15, 20, 25, ..., 90 (step 5) for both RS methods over the full period. A real edge is a smooth hill in Sharpe/CAGR vs. lookback; a spike at one value with noise around it is curve-fitting. | Dashboard → *Robustness* tab, section A |
| **Walk-forward validation** | Picks the best-Sharpe lookback using ONLY 2018–2022 (train), locks it, then tests that exact config — no re-tuning — on 2023–2024, 2025, and 2026 YTD. | Section B |
| **Regime-split testing** | The locked config run separately across 2018 (choppy), 2019 (sideways), 2020–2021 (COVID crash + bull), 2022 (bear/choppy), 2023–2024 (bull), so a trend-only strategy can't hide behind a good blended average. | Section C |
| **Bootstrap resampling** | Extracts genuine round-trip trades (BUY paired with its subsequent SELL — exact, since a ticker never has overlapping lots under `full_liquidate`), resamples them with replacement 1000x, and reports the *distribution* of return/Sharpe — not just the point estimate. If the 5th percentile is near/below zero, the edge may not be statistically robust. | Section D |
| **Trade-order shuffle test** | Same trades, reordered randomly 500x, equity curve rebuilt each time. Total return is necessarily identical (same multiset of returns), but Max Drawdown/Calmar are path-dependent — wide spread means the headline drawdown number owes a lot to sequencing luck. | Section E |
| **Data-quality / bad-tick check** | Flags any single-day price move >20% (ETFs essentially never move this much normally — could be a stale/bad print or an unadjusted corporate action) and independently re-verifies the portfolio's equity never went negative or NaN. | Section F |

Run it:
```bash
python scripts/run_robustness.py     # writes data/robustness.json (~1-3 min with real data)
python scripts/run.py                # rebuild dashboard so it picks up the new file
```
Or trigger the **Robustness Sweep** GitHub Actions workflow manually (or let
it run on its monthly schedule) — see `.github/workflows/robustness_sweep.yml`.
It's kept separate from the Monday refresh because it's ~16x more
compute (16 lookbacks × 2 methods, twice over, plus resampling).

**Defensive checks always on** (not just in the sweep): `Portfolio` asserts
cash and market value can never go negative for this unlevered, long-only,
cash-settled strategy — if a bad price tick or a sizing bug ever produced
an impossible number, the simulation fails loudly immediately rather than
silently producing a corrupted equity curve and drawdown stat.

## Repo layout

```
etf_rotation/
  config.py        # ETF list + all strategy parameters (TOP_N, RS_METHODS, REBALANCE_MODE, LOOKBACK_SWEEP, etc.)
  data.py          # yfinance fetch + local CSV cache, and a synthetic-data
                    # generator used only for offline pipeline testing
  data_quality.py   # bad-price-tick flagging + portfolio invariant re-check
  rs.py            # RS engines: compute_mansfield_rs, compute_momentum_rs, compute_rs (dispatcher)
  portfolio.py      # both rebalance modes (full_liquidate / diff) + transaction costs + invariant asserts
  backtest.py       # weekly simulation loop, run_all_segments() (method x segment grid), metrics
  robustness.py      # lookback sweep, walk-forward validation, regime split, bootstrap, shuffle test
  dashboard.py       # renders docs/index.html with side-by-side method comparison + Robustness tab
scripts/
  run.py                    # fetch data -> run_all_segments -> dashboard (used by Monday workflow)
  weekly_scan_preview.py    # Saturday-only preview of what Monday would trade, for both methods
  run_robustness.py         # the curve-fit-avoidance suite (separate, less frequent)
.github/workflows/
  saturday_scan.yml     # Sat 11:30am IST — publishes preview of next Monday's trades (both methods)
  monday_refresh.yml    # Mon 6pm IST (+Tue fallback) — runs all simulations, updates dashboard, deploys to Pages
  robustness_sweep.yml  # manual / monthly — lookback sweep + walk-forward + bootstrap, updates dashboard, deploys to Pages
data/                  # price cache, latest signal, robustness.json (all regenerated)
docs/index.html        # the dashboard (GitHub Pages serves this folder)
```

### Why there's no separate "live portfolio state" file

The strategy is 100% rules-based and deterministic. Rather than tracking
mutable portfolio state between runs (which risks drift/corruption bugs),
every run **recomputes each segment's simulation from scratch** from price
data + rules. This is idempotent — safe to re-run any time — and the
dashboard, trade logs, and current holdings are always exactly reproducible.

## Setup

1. Push this repo to GitHub.
2. **Settings → Pages → Source → GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions → "Read and write
   permissions"** (needed so the workflows can commit updated data/dashboard).
4. That's it — the Saturday and Monday workflows run on schedule. You can
   also trigger either manually via **Actions → (workflow) → Run workflow**.

## Running locally

```bash
pip install -r requirements.txt
python scripts/run.py                  # fetch data, run both methods x 3 segments, build docs/index.html
python scripts/weekly_scan_preview.py  # Saturday-style preview only, both methods
open docs/index.html                   # or just open the file in a browser
```

Change parameters without editing code:
```bash
python scripts/run.py --top-n 5 --methods mansfield --rebalance-mode diff
```

## Important caveats

- **This sandbox could not reach Yahoo Finance** to run a real historical
  backtest (network is restricted here to package registries / GitHub). The
  entire pipeline was built and validated against **synthetic price data**
  to confirm the logic is correct end-to-end and produces a valid dashboard.
  **The first real run happens on your GitHub Actions runner**, which has
  normal internet access — that's where you'll get real historical numbers.
- Some ETFs in your list (e.g. `MOM100.NS`, `MOMENTUM30.NS`, `MON100.NS`)
  listed more recently; they're automatically excluded from ranking until
  they have ≥65 trading days of history, and excluded from any given ETF's
  peer basket before their own listing date.
- Gilt/liquid ETFs (`LTGILTBEES`, `GILT5YBEES`, `LIQUIDBEES`) are included
  in the RS universe as requested — during equity drawdowns they can
  legitimately rank in the top-N as "least weak," which is standard
  behavior for this style of rotation system (a soft flight-to-safety).
- `auto_adjust=True` is used in the yfinance fetch, so prices are
  dividend/split-adjusted.
- **`full_liquidate` rebalancing is deliberately expensive.** Selling and
  rebuying continuing positions every time the top-N set changes at all
  pays transaction cost on names that didn't need to trade. With TOP_N=3
  this can churn frequently. This is intentional per your spec — the
  gentler `diff` mode (only trade what changed) is available via
  `--rebalance-mode diff` if you want to isolate cost drag from signal
  differences too.
- **TOP_N=3 concentrates the book** — each position is ~33% of capital, so
  a single ETF's move (or a thinly-traded/recently-listed name entering the
  top-3) has outsized portfolio impact versus a higher TOP_N.
- The Mansfield vs. Momentum comparison isolates the **signal shape**
  (smoothed-ratio-trend vs. raw-return-vs-peers) since both run under
  identical top_n, rebalance mode, cost assumptions, and peer universe. It
  does **not** control for TOP_N or rebalance mode across different
  strategy variants — if you want to test those independently, rerun with
  different `--top-n` / `--rebalance-mode` flags.
- This is a research/educational simulation, not investment advice or a
  broker integration — no real orders are placed.

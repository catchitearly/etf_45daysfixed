# ETF Relative-Strength Rotation (NSE)

A systematic sector-rotation strategy across 24 NSE-listed ETFs: every week it
ranks all ETFs by **Mansfield Relative Strength versus the rest of the list**,
and holds the top-N strongest ones.

**Live dashboard:** enable GitHub Pages (see setup below) and it'll be at
`https://<your-username>.github.io/<repo-name>/`

---

## Strategy rules

1. **Universe:** 24 NSE ETFs (gold, silver, broad market, sectors, factor
   indices, gilt/liquid, international) — see `etf_rotation/config.py`.
2. **Relative Strength:** for each ETF, build an equal-weight basket of all
   the *other* ETFs in the list (each rebased to start at 100), then compute
   the classic **Mansfield RS**:
   ```
   ratio = ETF_price(normalized) / basket_price(normalized)
   RS    = (ratio / SMA(ratio, 45 days) - 1) * 100
   ```
   RS > 0 and rising = the ETF is outperforming the rest of the group.
3. **Weekly scan (Saturday):** rank all eligible ETFs (must have ≥65 trading
   days of history) by RS descending, as of the most recent close (Friday,
   or Thursday if Friday was a holiday). Top **N** (default 5, configurable)
   = "in trend."
4. **Execution (Monday close):** compare Saturday's top-N to current
   holdings:
   - Still in top-N → **hold**, untouched (no forced rebalance, minimizes
     churn).
   - Dropped out of top-N → **sell** in full.
   - Newly entered top-N → **buy**, using all freed + idle cash split
     equally across new entrants.
   - If Monday is an NSE holiday, executes at the next trading day's close.
5. **Costs:** 0.05% per executed trade (buy or sell) to approximate
   brokerage + STT + slippage.
6. **Capital:** ₹10,00,000 starting capital, whole-unit position sizing (no
   fractional units).

## Dashboard segments

The dashboard reports the same continuous simulation split into three
windows so you can see backtest vs. genuinely out-of-sample performance:

| Segment | Period | Nature |
|---|---|---|
| Backtest | 2018-01-01 → 2024-12-31 | In-sample |
| Forward Test 2025 | 2025-01-01 → 2025-12-31 | Out-of-sample (rules were not tuned on this period) |
| Forward Test 2026 | 2026-01-01 → today | Out-of-sample, ongoing |

Each segment shows CAGR, total return, max drawdown, Sharpe, Calmar, and
trade count, plus equity-curve and drawdown charts. There's also a live RS
leaderboard, current holdings, the weekly signal log, and the recent trade
log.

## Repo layout

```
etf_rotation/
  config.py        # ETF list + all strategy parameters (edit TOP_N etc. here)
  data.py          # yfinance fetch + local CSV cache, and a synthetic-data
                    # generator used only for offline pipeline testing
  rs.py            # Mansfield RS engine
  portfolio.py      # diff-based weekly rebalancing + transaction costs
  backtest.py       # weekly simulation loop + performance metrics
  dashboard.py       # renders docs/index.html
scripts/
  run.py                    # fetch data -> full simulation -> dashboard (used by Monday workflow)
  weekly_scan_preview.py    # Saturday-only preview of what Monday would trade (informational)
.github/workflows/
  saturday_scan.yml   # Sat 11:30am IST — publishes preview of next Monday's trades
  monday_refresh.yml  # Mon 6pm IST (+Tue fallback) — runs full sim, updates dashboard, deploys to Pages
data/                  # price cache, trade log, equity curve, latest signal (all regenerated)
docs/index.html        # the dashboard (GitHub Pages serves this folder)
```

### Why there's no separate "live portfolio state" file

The strategy is 100% rules-based and deterministic. Rather than tracking
mutable portfolio state between runs (which risks drift/corruption bugs),
every run **recomputes the entire simulation from 2018-01-01 to today** from
price data + rules. This is idempotent — safe to re-run any time — and the
dashboard, trade log, and current holdings are always exactly reproducible.

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
python scripts/run.py                  # fetch data, full sim, build docs/index.html
python scripts/weekly_scan_preview.py  # Saturday-style preview only
open docs/index.html                   # or just open the file in a browser
```

Change the number of holdings without editing code:
```bash
TOP_N=3 python scripts/run.py
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
- This is a research/educational simulation, not investment advice or a
  broker integration — no real orders are placed.

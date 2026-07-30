"""
Portfolio simulation with two selectable weekly rebalancing modes, plus an
optional daily hard stop-loss overlay.

  "full_liquidate" (current default, config.REBALANCE_MODE):
      Whenever the top-N SET changes at all (even by one name), sell EVERY
      current holding and rebuy the new top-N fresh at equal weight. Even
      names that continue to be in the top-N get sold and rebought. If the
      top-N set is identical to what's currently held, no trades happen at
      all that week.

  "diff" (kept as an option):
      Continuing holdings are left completely untouched; only sell drop-outs
      and buy new entrants with the freed + idle cash, split equally across
      new entrants.

Both modes: whole-unit share sizing (NSE ETFs trade in single-unit lots),
transaction cost (config.TXN_COST_BPS) applied on both buys and sells.

Stop-loss overlay (config.STOP_LOSS_ENABLED, off by default): checked EVERY
trading day (not just the weekly rebalance day) via check_stop_losses().
Since a ticker never has more than one open lot at a time under either
rebalance mode (a full sell always happens before any rebuy), a single
entry_price per ticker is exact, not an approximation.
"""
from dataclasses import dataclass, field

from . import config


@dataclass
class Portfolio:
    cash: float = config.INITIAL_CAPITAL
    holdings: dict = field(default_factory=dict)      # ticker -> units
    entry_price: dict = field(default_factory=dict)   # ticker -> price paid (for stop-loss tracking)
    cost_bps: float = config.TXN_COST_BPS
    trade_log: list = field(default_factory=list)  # list of dicts

    def market_value(self, prices_on_date: dict) -> float:
        mv = self.cash
        for t, units in self.holdings.items():
            px = prices_on_date.get(t)
            if px is not None and px == px:  # not NaN
                mv += units * px
        assert mv >= -1e-6, (
            f"Portfolio invariant violated: market value went negative ({mv:.2f}). "
            f"This is an unlevered, long-only, cash-only strategy -- equity should "
            f"never go negative. Likely a bad price tick or a sizing bug; inspect "
            f"holdings={self.holdings} and prices_on_date={prices_on_date}."
        )
        return mv

    # ------------------------------------------------------------------
    def _sell(self, date, ticker, prices_on_date, action="SELL"):
        px = prices_on_date.get(ticker)
        units = self.holdings.pop(ticker, 0)
        entry = self.entry_price.pop(ticker, None)
        if px is None or px != px or units == 0:
            return
        gross = units * px
        cost = gross * self.cost_bps
        proceeds = gross - cost
        self.cash += proceeds
        row = {
            "date": date, "action": action, "ticker": ticker,
            "units": units, "price": px, "gross": gross,
            "cost": cost, "cash_after": self.cash,
        }
        if entry is not None:
            row["entry_price"] = entry
            row["pct_from_entry"] = round((px / entry - 1) * 100, 3)
        self.trade_log.append(row)

    def _buy_equal_weight(self, date, tickers, prices_on_date):
        buyable = [t for t in tickers if prices_on_date.get(t) == prices_on_date.get(t) and prices_on_date.get(t)]
        if not buyable:
            return
        alloc_each = self.cash / len(buyable)
        for t in buyable:
            px = prices_on_date[t]
            spendable = alloc_each / (1 + self.cost_bps)
            units = int(spendable // px)
            if units <= 0:
                continue
            gross = units * px
            cost = gross * self.cost_bps
            total_spend = gross + cost
            self.cash -= total_spend
            assert self.cash >= -1e-6, (
                f"Portfolio invariant violated: cash went negative ({self.cash:.2f}) "
                f"buying {t} on {date}. Position sizing should always floor to what's "
                f"affordable -- this indicates a sizing bug, not a market event."
            )
            self.holdings[t] = self.holdings.get(t, 0) + units
            self.entry_price[t] = px
            self.trade_log.append({
                "date": date, "action": "BUY", "ticker": t,
                "units": units, "price": px, "gross": gross,
                "cost": cost, "cash_after": self.cash,
            })

    # ------------------------------------------------------------------
    def rebalance_diff(self, date, target_tickers: list, prices_on_date: dict):
        """Only trade the diffs; continuing holdings are left untouched."""
        target_set = set(target_tickers)
        held_set = set(self.holdings.keys())

        to_sell = held_set - target_set
        to_buy = [t for t in target_tickers if t not in held_set]  # preserve rank order

        for t in to_sell:
            self._sell(date, t, prices_on_date)
        self._buy_equal_weight(date, to_buy, prices_on_date)

    def rebalance_full_liquidate(self, date, target_tickers: list, prices_on_date: dict):
        """Sell everything and rebuy fresh at equal weight, but ONLY if the
        top-N set actually changed vs. current holdings (no-op otherwise)."""
        target_set = set(target_tickers)
        held_set = set(self.holdings.keys())
        if target_set == held_set:
            return  # nothing changed this week -> no trades, no cost

        for t in list(self.holdings.keys()):
            self._sell(date, t, prices_on_date)
        self._buy_equal_weight(date, target_tickers, prices_on_date)

    def rebalance(self, date, target_tickers: list, prices_on_date: dict, mode: str = None):
        mode = mode or config.REBALANCE_MODE
        if mode == "full_liquidate":
            self.rebalance_full_liquidate(date, target_tickers, prices_on_date)
        elif mode == "diff":
            self.rebalance_diff(date, target_tickers, prices_on_date)
        else:
            raise ValueError(f"Unknown rebalance mode: {mode!r}")

    # ------------------------------------------------------------------
    def check_stop_losses(self, date, prices_on_date: dict, stop_loss_pct: float = None):
        """
        Checked EVERY trading day, independent of the weekly rebalance
        schedule. Any held position down more than `stop_loss_pct` from its
        entry_price is force-sold immediately at today's close, logged with
        action="STOP_LOSS_SELL" (distinct from a normal weekly-rotation
        "SELL" so it's identifiable in the trade log). Freed capital sits in
        cash until the next scheduled weekly rebalance redeploys it -- this
        does NOT immediately buy a replacement position.

        Returns the list of tickers stopped out today (empty if none).
        """
        stop_loss_pct = stop_loss_pct if stop_loss_pct is not None else config.STOP_LOSS_PCT
        stopped = []
        for t in list(self.holdings.keys()):
            entry = self.entry_price.get(t)
            px = prices_on_date.get(t)
            if entry is None or px is None or px != px:
                continue
            pct_move = (px / entry - 1) * 100
            if pct_move <= -abs(stop_loss_pct):
                self._sell(date, t, prices_on_date, action="STOP_LOSS_SELL")
                stopped.append(t)
        return stopped

    def snapshot(self):
        return {"cash": self.cash, "holdings": dict(self.holdings), "entry_price": dict(self.entry_price)}

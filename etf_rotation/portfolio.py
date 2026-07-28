"""
Portfolio simulation with two selectable weekly rebalancing modes.

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
"""
from dataclasses import dataclass, field

from . import config


@dataclass
class Portfolio:
    cash: float = config.INITIAL_CAPITAL
    holdings: dict = field(default_factory=dict)   # ticker -> units
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
    def _sell(self, date, ticker, prices_on_date):
        px = prices_on_date.get(ticker)
        units = self.holdings.pop(ticker, 0)
        if px is None or px != px or units == 0:
            return
        gross = units * px
        cost = gross * self.cost_bps
        proceeds = gross - cost
        self.cash += proceeds
        self.trade_log.append({
            "date": date, "action": "SELL", "ticker": ticker,
            "units": units, "price": px, "gross": gross,
            "cost": cost, "cash_after": self.cash,
        })

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

    def snapshot(self):
        return {"cash": self.cash, "holdings": dict(self.holdings)}

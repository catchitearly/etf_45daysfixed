"""
Diff-based portfolio rebalancing.

Rule (per user spec):
  - Every Monday close, compare current holdings to the latest Saturday scan's
    top-N list.
  - ETFs that are in BOTH (continuing) are left completely untouched (no
    forced rebalance -> minimizes churn/costs, weights are allowed to drift).
  - ETFs held but no longer in top-N are SOLD in full.
  - ETFs in top-N but not currently held are BOUGHT, using all cash freed by
    sells (plus any idle cash), split equally across the number of new
    entrants this week.
  - Units are whole numbers (NSE ETFs trade in single-unit lots); leftover
    cash from rounding stays in cash.
  - Transaction cost (config.TXN_COST_BPS) applied on both buys and sells.
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
        return mv

    def rebalance(self, date, target_tickers: list, prices_on_date: dict):
        """
        target_tickers: ordered list of top-N tickers as of the preceding scan.
        prices_on_date: dict ticker -> close price on the execution (Monday) date.
        """
        target_set = set(target_tickers)
        held_set = set(self.holdings.keys())

        to_sell = held_set - target_set
        to_buy = [t for t in target_tickers if t not in held_set]  # preserve rank order

        # --- sells ---
        for t in to_sell:
            px = prices_on_date.get(t)
            units = self.holdings.pop(t, 0)
            if px is None or px != px or units == 0:
                continue
            gross = units * px
            cost = gross * self.cost_bps
            proceeds = gross - cost
            self.cash += proceeds
            self.trade_log.append({
                "date": date, "action": "SELL", "ticker": t,
                "units": units, "price": px, "gross": gross,
                "cost": cost, "cash_after": self.cash,
            })

        # --- buys ---
        buyable = [t for t in to_buy if prices_on_date.get(t) == prices_on_date.get(t) and prices_on_date.get(t)]
        if buyable:
            alloc_each = self.cash / len(buyable)
            for t in buyable:
                px = prices_on_date[t]
                # reserve for cost: spend so that units*px*(1+cost_bps) <= alloc_each
                spendable = alloc_each / (1 + self.cost_bps)
                units = int(spendable // px)
                if units <= 0:
                    continue
                gross = units * px
                cost = gross * self.cost_bps
                total_spend = gross + cost
                self.cash -= total_spend
                self.holdings[t] = self.holdings.get(t, 0) + units
                self.trade_log.append({
                    "date": date, "action": "BUY", "ticker": t,
                    "units": units, "price": px, "gross": gross,
                    "cost": cost, "cash_after": self.cash,
                })

    def snapshot(self):
        return {"cash": self.cash, "holdings": dict(self.holdings)}

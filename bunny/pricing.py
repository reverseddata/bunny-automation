"""Pricing for Bhopp offers. All money in CENTS.

  undercut(lowest, pct)          clearly under the cheapest other seller, rounded clean
  floor_cents(cost_cents)        break-even: what the item cost you + Bhopp's fee
  target_price(lowest, floor)    undercut, but never below the floor
  markdown_steps(since, now, h)  whole intervals unsold since listing / the last sale
  marked_down(base, floor, n, %) -% per interval, rounded clean, never below the floor
  markdown_pct_for(base, ...)    gentler markdown for expensive items
"""
from __future__ import annotations

from datetime import datetime

FEE = 0.05          # Bhopp's seller fee (feeBps 500)


def _step(cents: int) -> int:
    """Clean price steps: 1c under $1, 5c under $10, 10c under $100, $1 above."""
    return 1 if cents < 100 else 5 if cents < 1000 else 10 if cents < 10000 else 100


def undercut(lowest_cents: int, pct: float = 2.0) -> int:
    """A clean price clearly under the lowest: $3.60 at 2% -> $3.50."""
    raw = lowest_cents * (1 - pct / 100)
    step = _step(int(raw))
    price = int(raw // step * step)
    if price >= lowest_cents:
        price = lowest_cents - step
    return max(1, price)


def floor_cents(cost_cents: float, fee: float = FEE) -> int:
    """Never sell below this: the cost plus the fee taken from the sale (rounded up)."""
    return max(1, int(-(-float(cost_cents) // (1 - fee))))


def target_price(lowest_other: int | None, floor: int, pct: float = 2.0, no_competition_markup: float = 1.25) -> int:
    """Undercut the cheapest other seller, never below the floor. With nobody
    else selling it: floor x `no_competition_markup`."""
    if lowest_other is not None:
        return max(floor, undercut(lowest_other, pct))
    return max(floor, int(floor * no_competition_markup))


def markdown_steps(since: datetime | None, now: datetime, hours: float = 3.0) -> int:
    """Whole `hours` intervals between `since` (listed / last sold) and `now`."""
    if since is None or hours <= 0:
        return 0
    return max(0, int((now - since).total_seconds() // (hours * 3600)))


def marked_down(base: int, floor: int, steps: int, pct: float) -> int:
    """`base` lowered by `pct` per step, rounded clean, never under the floor."""
    if steps <= 0 or pct <= 0 or base <= floor:
        return max(base, floor)
    raw = base * (1 - pct / 100) ** steps
    step = _step(int(raw))
    return max(floor, int(raw // step * step))


def markdown_pct_for(base_cents: int, small_pct: float = 5.0, big_pct: float = 2.0, big_from_usd: float = 10.0) -> float:
    """Expensive items step down gently: `big_pct` from `big_from_usd` (judged
    on the full price), `small_pct` below it."""
    return big_pct if base_cents >= big_from_usd * 100 else small_pct

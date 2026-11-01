"""Shared term-index resets for EUR bonds and mortgages.

Resets align to payment boundaries and are no more frequent than payments.
Projection uses the full index tenor, even when it extends past final redemption.
Today's already-set coupon uses the supplied known fixing, never a forecast.
"""
from bisect import bisect_right
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from math import isfinite
from bond_engine import Curve, CashFlow, Valuation, year_fraction


def add_months(d: date, months: int) -> date:
    year, month = divmod(d.year * 12 + d.month - 1 + months, 12)
    month += 1
    last = monthrange(year, month)[1]
    eom = d.day == monthrange(d.year, d.month)[1]
    return date(year, month, last if eom else min(d.day, last))


@dataclass(frozen=True)
class ResetRate:
    reset: date
    end: date
    reference: float
    spread: float
    source: str
    accrual: float
    start_df: float | None = None
    end_df: float | None = None

    @property
    def coupon(self):
        return self.reference + self.spread


class RateSchedule:
    def __init__(self, dates, settlement, frequency, tenor, reset_months, basis,
                 fixing, spread, next_reset=None):
        if tenor not in (1, 2, 3, 4, 6, 12) or isinstance(tenor, bool):
            raise ValueError('Index tenor must be 1, 2, 3, 4, 6 or 12 months')
        step = 12 // frequency
        if reset_months not in (1, 2, 3, 4, 6, 12) or reset_months < step or reset_months % step:
            raise ValueError('Reset interval must be a multiple of the payment interval, and no shorter than it')
        if not all(isfinite(v) for v in (fixing, spread)):
            raise ValueError('Fixing and spread must be finite')
        year_fraction(dates[0], dates[-1], basis)
        self.dates, self.settlement, self.tenor = dates, settlement, tenor
        self.basis, self.fixing, self.spread = basis, fixing, spread
        self.curve = None
        self.cache = {}
        stride = reset_months // step
        current_period = bisect_right(dates, settlement) - 1
        if current_period < 0 or current_period >= len(dates) - 1:
            raise ValueError('Settlement must be inside the payment schedule')
        if next_reset is not None:
            if next_reset <= settlement or next_reset not in dates:
                raise ValueError('Next reset must be a payment boundary strictly after settlement, up to maturity')
            next_i = dates.index(next_reset)
            current_i = max(0, next_i - stride)
            if dates[current_i] > settlement:
                raise ValueError('Next reset is inconsistent with reset interval and settlement')
        else:
            current_i = (current_period // stride) * stride
            next_i = current_i + stride
        self.current_reset = dates[current_i]
        self.next_reset = dates[next_i] if next_i < len(dates) else None
        self.resets = [self.current_reset] + [dates[i] for i in range(next_i, len(dates)-1, stride)]
        self.required_end = max([settlement] + [add_months(d, tenor) for d in self.resets[1:]])

    def bind(self, curve: Curve):
        if curve.as_of > self.settlement:
            raise ValueError('Projection curve must start on or before settlement')
        if len(self.resets) > 1:
            curve.df(self.resets[1])
            curve.df(self.required_end)
        self.curve = curve
        return self

    def at(self, period_start):
        i = max(0, bisect_right(self.resets, period_start) - 1)
        reset = self.resets[i]
        if reset not in self.cache:
            end = add_months(reset, self.tenor)
            accrual = year_fraction(reset, end, self.basis)
            if i == 0:
                result = ResetRate(reset, end, self.fixing, self.spread, 'Known fixing + spread', accrual)
            else:
                if self.curve is None:
                    raise ValueError('Projection curve required for future resets')
                d1, d2 = self.curve.df(reset), self.curve.df(end)
                result = ResetRate(reset, end, (d1 / d2 - 1) / accrual, self.spread,
                                   'Projected forward + spread', accrual, d1, d2)
            self.cache[reset] = result
        return self.cache[reset]


def value_reset_bond(bond, settlement, discount_curve, resets):
    settle_df = discount_curve.df(settlement)
    discount_curve.df(bond.maturity)
    flows, accrued = [], 0.0
    dates = bond.schedule()
    for start, end in zip(dates, dates[1:]):
        if end <= settlement:
            continue
        coupon_rate = resets.at(start).coupon
        accrual = year_fraction(start, end, bond.day_count)
        interest = bond.notional * coupon_rate * accrual
        principal = bond.notional if end == bond.maturity else 0.0
        factor = discount_curve.df(end) / settle_df
        flows.append(CashFlow(start, end, coupon_rate, accrual, interest, principal,
                              factor, (interest + principal) * factor))
        if start < settlement < end:
            accrued = bond.notional * coupon_rate * year_fraction(start, settlement, bond.day_count)
    dirty = sum(f.present_value for f in flows)
    clean = dirty - accrued
    return Valuation(dirty, accrued, clean, dirty / bond.notional * 100,
                     clean / bond.notional * 100, tuple(flows))

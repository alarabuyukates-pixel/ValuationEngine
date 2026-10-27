"""Dependency-free, deterministic fixed and simple floating-rate bond valuation."""
from dataclasses import dataclass, field
from datetime import date
from calendar import monthrange
from math import exp, log, isfinite
from typing import Mapping


def year_fraction(start: date, end: date, basis: str = 'ACT/365F') -> float:
    if end < start:
        raise ValueError('End date precedes start date')
    if basis == 'ACT/365F':
        return (end - start).days / 365
    if basis == 'ACT/360':
        return (end - start).days / 360
    if basis == '30E/360':
        return ((end.year-start.year)*360 + (end.month-start.month)*30
                + min(end.day, 30)-min(start.day, 30))/360
    raise ValueError('Supported day counts: ACT/365F, ACT/360, 30E/360')


@dataclass(frozen=True)
class Curve:
    """Discount factors at dated pillars; log-linear interpolation, no extrapolation."""
    as_of: date
    pillars: tuple[tuple[date, float], ...]

    def __post_init__(self):
        previous = self.as_of
        for d, df in self.pillars:
            if d <= previous or not isfinite(df) or df <= 0:
                raise ValueError('Pillars must increase after as_of with positive finite factors')
            previous = d
        if not self.pillars:
            raise ValueError('At least one curve pillar is required')

    @classmethod
    def flat(cls, as_of: date, end: date, rate: float):
        """Continuously compounded annual rate on ACT/365F."""
        if not isfinite(rate):
            raise ValueError('Rate must be finite')
        return cls(as_of, ((end, exp(-rate * year_fraction(as_of, end))),))

    def df(self, d: date) -> float:
        if d < self.as_of or d > self.pillars[-1][0]:
            raise ValueError(f'Date {d} outside curve coverage')
        left, left_df = self.as_of, 1.0
        for right, right_df in self.pillars:
            if d <= right:
                weight = (d-left).days / (right-left).days
                return exp(log(left_df) + weight*(log(right_df)-log(left_df)))
            left, left_df = right, right_df
        raise ValueError('Date outside curve coverage')

    def forward(self, start: date, end: date, basis: str) -> float:
        accrual = year_fraction(start, end, basis)
        if accrual <= 0:
            raise ValueError('Forward period must have positive length')
        return (self.df(start)/self.df(end)-1)/accrual


@dataclass(frozen=True)
class Bond:
    issue: date
    maturity: date
    notional: float = 100.0
    frequency: int = 2
    kind: str = 'fixed'
    coupon: float = 0.05
    spread: float = 0.0
    day_count: str = 'ACT/360'
    fixings: Mapping[date, float] = field(default_factory=dict)

    def __post_init__(self):
        if self.issue >= self.maturity:
            raise ValueError('Maturity must follow issue')
        if self.frequency not in (1, 2, 3, 4, 6, 12):
            raise ValueError('Frequency must divide 12')
        if self.kind not in ('fixed', 'floating'):
            raise ValueError('Kind must be fixed or floating')
        if not isfinite(self.notional) or self.notional <= 0:
            raise ValueError('Notional must be positive and finite')
        if not all(isfinite(x) for x in (self.coupon, self.spread, *self.fixings.values())):
            raise ValueError('Rates must be finite decimal values')
        year_fraction(self.issue, self.maturity, self.day_count)

    def schedule(self) -> list[date]:
        """Generate backward from maturity; short first stub; preserve maturity EOM."""
        dates = [self.maturity]
        step = 12 // self.frequency
        eom = self.maturity.day == monthrange(self.maturity.year, self.maturity.month)[1]
        n = 1
        while True:
            total = self.maturity.year*12 + self.maturity.month-1 - n*step
            y, m = divmod(total, 12)
            m += 1
            last = monthrange(y, m)[1]
            d = date(y, m, last if eom else min(self.maturity.day, last))
            if d <= self.issue:
                break
            dates.append(d)
            n += 1
        return [self.issue] + list(reversed(dates))


@dataclass(frozen=True)
class CashFlow:
    start: date
    payment: date
    rate: float
    accrual: float
    coupon: float
    principal: float
    discount_factor: float
    present_value: float


@dataclass(frozen=True)
class Valuation:
    dirty_pv: float
    accrued_interest: float
    clean_pv: float
    dirty_price_per_100: float
    clean_price_per_100: float
    cashflows: tuple[CashFlow, ...]


def value_bond(bond: Bond, settlement: date, discount_curve: Curve,
               projection_curve: Curve | None = None) -> Valuation:
    """PV at settlement. Payments on settlement are excluded (ex-payment)."""
    if settlement < bond.issue:
        raise ValueError('Settlement before issue is unsupported')
    if discount_curve.as_of > settlement:
        raise ValueError('Discount curve must start on or before settlement')
    if bond.kind == 'floating' and projection_curve and projection_curve.as_of > settlement:
        raise ValueError('Projection curve must start on or before settlement')
    if settlement >= bond.maturity:
        return Valuation(0, 0, 0, 0, 0, ())
    settle_df = discount_curve.df(settlement)
    flows = []
    accrued = 0.0
    dates = bond.schedule()
    for start, end in zip(dates, dates[1:]):
        if end <= settlement:
            continue
        rate = bond.coupon
        if bond.kind == 'floating':
            if start in bond.fixings:
                rate = bond.fixings[start] + bond.spread
            elif start <= settlement:
                raise ValueError(f'Missing floating reset fixing for {start}')
            elif projection_curve is None:
                raise ValueError('Projection curve required for future floating resets')
            else:
                rate = projection_curve.forward(start, end, bond.day_count) + bond.spread
        accrual = year_fraction(start, end, bond.day_count)
        coupon = bond.notional * rate * accrual
        principal = bond.notional if end == bond.maturity else 0.0
        df = discount_curve.df(end)/settle_df
        flows.append(CashFlow(start, end, rate, accrual, coupon, principal,
                              df, (coupon+principal)*df))
        if start < settlement < end:
            accrued = bond.notional*rate*year_fraction(start, settlement, bond.day_count)
    dirty = sum(flow.present_value for flow in flows)
    clean = dirty-accrued
    return Valuation(dirty, accrued, clean, dirty/bond.notional*100,
                     clean/bond.notional*100, tuple(flows))

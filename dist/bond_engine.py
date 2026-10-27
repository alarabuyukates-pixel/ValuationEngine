"""Dependency-free fixed, term-floating, and zero-coupon bond valuation."""
from dataclasses import dataclass, field
from datetime import date
from calendar import monthrange
from math import exp, log, isfinite
from typing import Mapping


EURIBOR_TENORS = {'EURIBOR-1M': 1, 'EURIBOR-3M': 3, 'EURIBOR-6M': 6, 'EURIBOR-12M': 12}


def normalize_index(value: str) -> str:
    raw = str(value or '').strip().upper()
    compact = raw.replace(' ', '').replace('-', '')
    if compact.startswith('EURIBOR'):
        suffix = compact[len('EURIBOR'):]
        candidate = 'EURIBOR-' + suffix
        if candidate not in EURIBOR_TENORS:
            raise ValueError('Supported EURIBOR tenors: 1M, 3M, 6M, 12M')
        return candidate
    if compact in ('SOFR', 'SONIA', 'ESTR', '€STR', 'TLREF'):
        raise ValueError('Overnight indices require compounding and are outside the term-index model')
    return raw


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

    @classmethod
    def from_record(cls, record: Mapping):
        """Build dated DFs from quoted zero rates (decimals) or supplied factors.

        Compounding and rate day count must be declared at curve level. The
        interpolation remains log-linear in ACTUAL calendar days between factors.
        """
        as_of = date.fromisoformat(str(record.get('as_of', '')).strip())
        compounding = str(record.get('compounding') or 'continuous').strip().lower()
        basis = str(record.get('day_count') or 'ACT/365F').strip().upper()
        year_fraction(as_of, as_of, basis)
        if compounding not in ('continuous', 'annual', 'simple'):
            raise ValueError('Curve compounding must be continuous, annual or simple')
        pillars = []
        for pillar in record.get('pillars', []):
            if not isinstance(pillar, Mapping):
                raise ValueError('Each curve pillar must be an object')
            d = date.fromisoformat(str(pillar.get('date', '')).strip())
            if d <= as_of:
                raise ValueError('Curve pillars must follow the as-of date')
            has_df = pillar.get('discount_factor') is not None and str(pillar.get('discount_factor')).strip() != ''
            has_rate = pillar.get('zero_rate') is not None and str(pillar.get('zero_rate')).strip() != ''
            if has_df == has_rate:
                raise ValueError('Each curve pillar needs exactly one of discount_factor or zero_rate')
            if has_df:
                factor = float(pillar['discount_factor'])
            else:
                rate = float(pillar['zero_rate'])
                if not isfinite(rate):
                    raise ValueError('Curve zero rates must be finite decimals')
                t = year_fraction(as_of, d, basis)
                if compounding == 'continuous':
                    factor = exp(-rate * t)
                elif compounding == 'annual':
                    if rate <= -1:
                        raise ValueError('Annual-compounded zero rate must exceed -100%')
                    factor = (1 + rate) ** (-t)
                else:
                    if 1 + rate * t <= 0:
                        raise ValueError('Simple zero rate gives a nonpositive discount denominator')
                    factor = 1 / (1 + rate * t)
            pillars.append((d, factor))
        pillars.sort(key=lambda item: item[0])
        return cls(as_of, tuple(pillars))

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
    index: str = ''
    index_tenor_months: int | None = None
    index_day_count: str | None = None

    def __post_init__(self):
        if self.issue >= self.maturity:
            raise ValueError('Maturity must follow issue')
        aliases = {'zero-coupon': 'zero', 'zero coupon': 'zero', 'discount': 'zero'}
        kind = aliases.get(self.kind, self.kind)
        object.__setattr__(self, 'kind', kind)
        if kind == 'zero':
            object.__setattr__(self, 'coupon', 0.0)
            object.__setattr__(self, 'spread', 0.0)
            object.__setattr__(self, 'frequency', 1)
            object.__setattr__(self, 'day_count', 'ACT/365F')
        if self.frequency not in (1, 2, 3, 4, 6, 12):
            raise ValueError('Frequency must divide 12')
        if self.kind not in ('fixed', 'floating', 'zero'):
            raise ValueError('Kind must be fixed, floating or zero')
        if not isfinite(self.notional) or self.notional <= 0:
            raise ValueError('Notional must be positive and finite')
        if not all(isfinite(x) for x in (self.coupon, self.spread, *self.fixings.values())):
            raise ValueError('Rates must be finite decimal values')
        year_fraction(self.issue, self.maturity, self.day_count)
        if self.kind == 'floating' and self.index:
            index = normalize_index(self.index)
            object.__setattr__(self, 'index', index)
            tenor = EURIBOR_TENORS.get(index, self.index_tenor_months)
            if tenor is None:
                raise ValueError('A custom term index requires its tenor in months')
            if isinstance(tenor, bool) or int(tenor) != tenor or tenor not in (1, 2, 3, 4, 6, 12):
                raise ValueError('Index tenor must be 1, 2, 3, 4, 6 or 12 months')
            if self.index_tenor_months is not None and self.index_tenor_months != tenor:
                raise ValueError('Index tenor does not match the named EURIBOR index')
            if self.frequency != 12 // tenor:
                raise ValueError(f'{index} tenor requires {12 // tenor} coupon payments per year')
            object.__setattr__(self, 'index_tenor_months', tenor)
            if index in EURIBOR_TENORS and self.index_day_count not in (None, 'ACT/360'):
                raise ValueError('EURIBOR reference forwards use ACT/360')
            basis = self.index_day_count or ('ACT/360' if index in EURIBOR_TENORS else self.day_count)
            year_fraction(self.issue, self.maturity, basis)
            object.__setattr__(self, 'index_day_count', basis)

    def schedule(self) -> list[date]:
        """Generate backward from maturity; short first stub; preserve maturity EOM."""
        if self.kind == 'zero':
            return [self.issue, self.maturity]
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
    if bond.kind == 'zero':
        factor = discount_curve.df(bond.maturity) / settle_df
        dirty = bond.notional * factor
        flow = CashFlow(bond.issue, bond.maturity, 0.0, 0.0, 0.0,
                        bond.notional, factor, dirty)
        price = factor * 100
        return Valuation(dirty, 0.0, dirty, price, price, (flow,))
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
                rate = projection_curve.forward(start, end, bond.index_day_count or bond.day_count) + bond.spread
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

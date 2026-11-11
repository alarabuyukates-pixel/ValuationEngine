"""Fixed-rate time deposits with arbitrary maturity and explicit cash-flow side."""
from dataclasses import dataclass
from datetime import date
from math import isfinite
from bond_engine import Bond, CashFlow, Curve, Valuation, year_fraction


@dataclass(frozen=True)
class TimeDeposit:
    issue: date
    maturity: date
    notional: float
    coupon: float
    day_count: str = 'ACT/365F'
    payout: str = 'maturity'
    frequency: int = 12
    side: str = 'liability'

    def __post_init__(self):
        if self.issue >= self.maturity:
            raise ValueError('Deposit maturity must follow opening date')
        if (self.maturity-self.issue).days > 366*50:
            raise ValueError('Maximum deposit term is 50 years')
        if not isfinite(self.notional) or self.notional <= 0:
            raise ValueError('Deposit principal must be positive and finite')
        if not isfinite(self.coupon):
            raise ValueError('Deposit annual rate must be finite')
        if self.payout not in ('maturity', 'periodic'):
            raise ValueError('Deposit interest payment must be maturity or periodic')
        if self.side not in ('liability', 'asset'):
            raise ValueError('Deposit side must be liability (bank) or asset (holder)')
        if self.frequency not in (1, 2, 3, 4, 6, 12):
            raise ValueError('Deposit frequency must divide 12')
        year_fraction(self.issue, self.maturity, self.day_count)

    @property
    def sign(self):
        return -1 if self.side == 'liability' else 1

    def schedule(self):
        if self.payout == 'maturity':
            return [self.issue, self.maturity]
        # Reuse the established maturity-anchored schedule with a short first stub.
        return Bond(self.issue, self.maturity, self.notional, self.frequency,
                    'fixed', self.coupon, day_count=self.day_count).schedule()


def value_deposit(deposit: TimeDeposit, settlement: date, discount_curve: Curve) -> Valuation:
    if not deposit.issue <= settlement < deposit.maturity:
        raise ValueError('Require deposit opening <= settlement < maturity')
    if discount_curve.as_of > settlement:
        raise ValueError('Deposit discount curve must start on or before settlement')
    settle_df = discount_curve.df(settlement)
    discount_curve.df(deposit.maturity)
    flows, accrued = [], 0.0
    signed = deposit.sign * deposit.notional
    dates = deposit.schedule()
    for start, end in zip(dates, dates[1:]):
        if end <= settlement:
            continue
        fraction = year_fraction(start, end, deposit.day_count)
        interest = signed * deposit.coupon * fraction
        principal = signed if end == deposit.maturity else 0.0
        if not isfinite(interest) or deposit.notional + deposit.sign*interest <= 0:
            raise ValueError('Deposit rate and term produce a nonpositive repayment or nonfinite interest')
        factor = discount_curve.df(end)/settle_df
        flows.append(CashFlow(start, end, deposit.coupon, fraction, interest,
                              principal, factor, (interest+principal)*factor))
        if start < settlement < end:
            accrued = signed * deposit.coupon * year_fraction(start, settlement, deposit.day_count)
    dirty = sum(f.present_value for f in flows)
    clean = dirty-accrued
    if not all(isfinite(v) for v in (dirty, accrued, clean)):
        raise ValueError('Nonfinite deposit valuation')
    return Valuation(dirty, accrued, clean, dirty/deposit.notional*100,
                     clean/deposit.notional*100, tuple(flows))

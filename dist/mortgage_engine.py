"""Contractual EUR fixed-rate mortgages, assuming scheduled past payments were made.

Nominal annual coupon / frequency; equal regular calendar periods only. No
prepayment, arrears, credit losses, rate resets or holiday adjustments.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from math import exp, expm1, isfinite, log1p
from bond_engine import CashFlow, Curve, Valuation, year_fraction


@dataclass(frozen=True)
class MortgageFlow(CashFlow):
    opening_balance: float
    closing_balance: float


@dataclass(frozen=True)
class Mortgage:
    issue: date
    maturity: date
    notional: float
    coupon: float
    frequency: int = 12
    repayment: str = 'annuity'
    discount_spread_bps: float = 0.0

    def __post_init__(self):
        if self.issue >= self.maturity:
            raise ValueError('Mortgage maturity must follow origination')
        if self.frequency not in (1, 2, 4, 12):
            raise ValueError('Mortgage payments per year must be 1, 2, 4 or 12')
        if self.repayment not in ('annuity', 'linear', 'interest-only'):
            raise ValueError('Mortgage repayment must be annuity, linear or interest-only')
        if not isfinite(self.notional) or self.notional <= 0:
            raise ValueError('Mortgage original principal must be positive and finite')
        if not isfinite(self.coupon) or self.coupon < 0:
            raise ValueError('Mortgage fixed rate must be nonnegative and finite')
        if not isfinite(self.discount_spread_bps):
            raise ValueError('Mortgage discount spread must be finite')
        months = (self.maturity.year - self.issue.year) * 12 + self.maturity.month - self.issue.month
        if not 0 < months <= 600:
            raise ValueError('Mortgage term must be at most 50 years')
        if months % (12 // self.frequency) or self.payment_date(months) != self.maturity:
            raise ValueError('Mortgage maturity must align with regular payment dates from origination; stub periods are unsupported')

    def payment_date(self, months):
        total = self.issue.year * 12 + self.issue.month - 1 + months
        year, month = divmod(total, 12)
        month += 1
        last = monthrange(year, month)[1]
        eom = self.issue.day == monthrange(self.issue.year, self.issue.month)[1]
        return date(year, month, last if eom else min(self.issue.day, last))

    def schedule(self):
        months = (self.maturity.year - self.issue.year) * 12 + self.maturity.month - self.issue.month
        return [self.issue] + [self.payment_date(m) for m in range(12 // self.frequency, months + 1, 12 // self.frequency)]


def value_mortgage(loan: Mortgage, settlement: date, discount_curve: Curve) -> Valuation:
    if not loan.issue <= settlement < loan.maturity:
        raise ValueError('Require origination <= settlement < mortgage maturity')
    if discount_curve.as_of > settlement:
        raise ValueError('Discount curve date must be on or before settlement')
    settle_df = discount_curve.df(settlement)
    discount_curve.df(loan.maturity)  # Validate coverage before generating flows.
    dates = loan.schedule()
    count = len(dates) - 1
    periodic_rate = loan.coupon / loan.frequency
    payment = (loan.notional / count if periodic_rate == 0 else
               loan.notional * periodic_rate / -expm1(-count * log1p(periodic_rate)))
    balance, accrued, outstanding = loan.notional, 0.0, None
    flows = []
    for i, (start, end) in enumerate(zip(dates, dates[1:]), 1):
        opening = balance
        interest = opening * periodic_rate
        if loan.repayment == 'annuity':
            principal = payment - interest
        elif loan.repayment == 'linear':
            principal = loan.notional / count
        else:
            principal = 0.0
        principal = opening if i == count else min(opening, max(0.0, principal))
        balance = max(0.0, opening - principal)
        if end <= settlement:
            continue
        if outstanding is None:
            outstanding = opening
        if start < settlement < end:
            accrued = interest * (settlement - start).days / (end - start).days
        factor = discount_curve.df(end) / settle_df * exp(
            -loan.discount_spread_bps / 10000 * year_fraction(settlement, end))
        flows.append(MortgageFlow(start, end, loan.coupon, 1 / loan.frequency,
                                 interest, principal, factor, (interest + principal) * factor,
                                 opening, balance))
    dirty = sum(f.present_value for f in flows)
    clean = dirty - accrued
    if outstanding is None or outstanding <= 0 or not all(isfinite(v) for v in (dirty, clean, accrued)):
        raise ValueError('Non-finite or empty mortgage valuation; check principal, rates and term')
    return Valuation(dirty, accrued, clean, dirty / outstanding * 100,
                     clean / outstanding * 100, tuple(flows))


def annuity_payment(balance, annual_rate, frequency, count):
    periodic = annual_rate / frequency
    if not isfinite(periodic) or periodic <= -1:
        raise ValueError('Mortgage periodic rate must exceed -100%')
    return balance / count if periodic == 0 else balance * periodic / -expm1(-count * log1p(periodic))


def value_floating_mortgage(loan, settlement, discount_curve, resets,
                            outstanding=None, current_payment=None):
    """Value remaining balances without inventing historic floating-rate payments.

    A seasoned annuity uses its contractual current instalment until next reset.
    Future resets re-amortize outstanding principal over the remaining maturity.
    Linear loans repay starting outstanding / remaining payments each period.
    """
    if not loan.issue <= settlement < loan.maturity:
        raise ValueError('Require origination <= settlement < mortgage maturity')
    if settlement > loan.issue and outstanding is None:
        raise ValueError('Floating mortgage requires current outstanding principal at settlement')
    balance = loan.notional if outstanding is None else outstanding
    if not isfinite(balance) or balance <= 0 or balance > loan.notional + .01:
        raise ValueError('Outstanding principal must be positive and no greater than original principal')
    if current_payment is not None and (not isfinite(current_payment) or current_payment <= 0):
        raise ValueError('Current instalment must be positive and finite')
    if loan.repayment == 'annuity' and settlement > loan.issue and current_payment is None:
        raise ValueError('Seasoned floating annuity requires the current contractual instalment')
    settle_df = discount_curve.df(settlement)
    discount_curve.df(loan.maturity)
    dates = loan.schedule()
    periods = [(a, b) for a, b in zip(dates, dates[1:]) if b > settlement]
    count, initial = len(periods), balance
    constant_principal = initial / count
    flows, accrued, previous_reset = [], 0.0, None
    payment = current_payment
    for i, (start, end) in enumerate(periods):
        quote = resets.at(start)
        if loan.repayment == 'annuity' and quote.reset != previous_reset:
            if i != 0 or payment is None:
                payment = annuity_payment(balance, quote.coupon, loan.frequency, count - i)
        previous_reset = quote.reset
        opening = balance
        interest = opening * quote.coupon / loan.frequency
        if loan.repayment == 'annuity':
            principal = payment - interest
            if principal <= 0:
                raise ValueError('Current instalment does not amortize the mortgage; negative amortization is unsupported')
            if i < count - 1 and principal > opening + .01:
                raise ValueError('Current instalment repays the mortgage before contractual maturity; check the input')
        elif loan.repayment == 'linear':
            principal = constant_principal
        else:
            principal = 0.0
        if i == count - 1:
            principal = opening
        principal = min(opening, principal)
        balance = max(0.0, opening - principal)
        if start < settlement < end:
            accrued = interest * (settlement-start).days / (end-start).days
        factor = discount_curve.df(end) / settle_df * exp(
            -loan.discount_spread_bps / 10000 * year_fraction(settlement, end))
        flows.append(MortgageFlow(start, end, quote.coupon, 1 / loan.frequency,
                                 interest, principal, factor, (interest + principal) * factor,
                                 opening, balance))
    dirty = sum(f.present_value for f in flows)
    clean = dirty - accrued
    if not all(isfinite(v) for v in (dirty, clean, accrued)):
        raise ValueError('Non-finite mortgage valuation; check rates and term')
    return Valuation(dirty, accrued, clean, dirty / initial * 100,
                     clean / initial * 100, tuple(flows))

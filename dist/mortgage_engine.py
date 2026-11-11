"""EUR mortgages with prospective CPR and payment-reduction prepayment."""
import ast
import operator
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from math import exp, expm1, isfinite, log1p, log, sqrt
from bond_engine import CashFlow, Curve, Valuation, year_fraction


# A small arithmetic language, deliberately separate from Python execution.
class CPRFormula:
    variables = {'age_months', 'remaining_months', 'coupon_pct', 'market_rate_pct',
                 'balance', 'original_principal', 'period', 'base_cpr_pct'}
    functions = {'min': min, 'max': max, 'abs': abs, 'exp': exp, 'log': log, 'sqrt': sqrt}
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.Pow: operator.pow}

    def __init__(self, expression):
        if not isinstance(expression, str) or not expression.strip() or len(expression) > 500:
            raise ValueError('CPR formula must contain 1–500 characters')
        try:
            self.tree = ast.parse(expression.replace('^', '**'), mode='eval').body
        except (SyntaxError, RecursionError) as error:
            raise ValueError('Invalid CPR formula syntax') from error
        nodes = list(ast.walk(self.tree))
        if len(nodes) > 100:
            raise ValueError('CPR formula is too complex (maximum 100 nodes)')
        allowed = (ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call,
                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.UAdd, ast.USub)
        for node in nodes:
            if not isinstance(node, allowed):
                raise ValueError('CPR formula supports arithmetic and min/max/abs/exp/log/sqrt only')
            if isinstance(node, ast.Name) and node.id not in self.variables | self.functions.keys():
                raise ValueError('Unknown CPR formula variable: ' + node.id)
            if isinstance(node, ast.Constant) and (type(node.value) not in (int, float) or abs(node.value) > 1e12):
                raise ValueError('Invalid CPR formula constant')
            if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or
                    node.func.id not in self.functions or node.keywords or not 1 <= len(node.args) <= 8):
                raise ValueError('Invalid CPR formula function')

    def evaluate(self, context):
        def visit(node):
            if isinstance(node, ast.Constant):
                result = float(node.value)
            elif isinstance(node, ast.Name):
                if node.id not in context:
                    raise ValueError('CPR function names must be called')
                result = context[node.id]
            elif isinstance(node, ast.UnaryOp):
                result = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Pow) and abs(right) > 100:
                    raise ValueError('CPR formula exponent must be between -100 and 100')
                result = self.binary[type(node.op)](left, right)
            else:
                result = self.functions[node.func.id](*[visit(a) for a in node.args])
            if isinstance(result, complex) or not isfinite(result) or abs(result) > 1e15:
                raise ValueError('CPR formula produced an invalid or excessive value')
            return result
        try:
            result = visit(self.tree)
        except (ArithmeticError, TypeError, ValueError) as error:
            raise ValueError('CPR formula: ' + str(error)) from error
        if not 0 <= result <= 100:
            raise ValueError('CPR formula must return an annual percentage between 0 and 100; use min/max to bound it')
        return result / 100


def prepayment(loan, formula, settlement, start, end, opening, scheduled, coupon,
               discount_curve, period):
    # First partial period gets only its remaining regular-period exposure.
    exposure = (end - max(start, settlement)).days / (end - start).days / loan.frequency
    if loan.prepayment_model == 'none' or end == loan.maturity:
        return 0.0, 0.0, 0.0, exposure
    cpr = loan.cpr / 100
    if formula:
        months = lambda d: (d - loan.issue).days * 12 / 365.25
        market = -log(discount_curve.df(end) / discount_curve.df(settlement)) / year_fraction(settlement, end) * 100
        cpr = formula.evaluate(dict(age_months=months(end),
            remaining_months=(loan.maturity-end).days*12/365.25, coupon_pct=coupon*100,
            market_rate_pct=market, balance=opening, original_principal=loan.notional,
            period=period, base_cpr_pct=loan.cpr))
    smm = 1.0 if cpr == 1 else -expm1(log1p(-cpr) * exposure)
    return cpr, smm, max(0.0, opening-scheduled) * smm, exposure


@dataclass(frozen=True)
class MortgageFlow(CashFlow):
    opening_balance: float
    closing_balance: float
    scheduled_principal: float = 0.0
    prepayment: float = 0.0
    cpr: float = 0.0
    smm: float = 0.0
    prepayment_exposure: float = 0.0


@dataclass(frozen=True)
class Mortgage:
    issue: date
    maturity: date
    notional: float
    coupon: float
    frequency: int = 12
    repayment: str = 'annuity'
    discount_spread_bps: float = 0.0
    prepayment_model: str = 'none'
    cpr: float = 0.0
    cpr_formula: str = ''

    def __post_init__(self):
        if self.prepayment_model not in ('none', 'cpr', 'formula'):
            raise ValueError('Prepayment model must be none, cpr or formula')
        if not isfinite(self.cpr) or not 0 <= self.cpr <= 100:
            raise ValueError('Annual CPR must be between 0 and 100 percent')
        if self.prepayment_model == 'formula':
            CPRFormula(self.cpr_formula)
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
    formula = CPRFormula(loan.cpr_formula) if loan.prepayment_model == 'formula' else None
    dates = loan.schedule()
    count = len(dates) - 1
    periodic_rate = loan.coupon / loan.frequency
    payment = (loan.notional / count if periodic_rate == 0 else
               loan.notional * periodic_rate / -expm1(-count * log1p(periodic_rate)))
    balance, accrued, outstanding = loan.notional, 0.0, None
    flows = []
    for i, (start, end) in enumerate(zip(dates, dates[1:]), 1):
        if balance <= 0:
            break
        opening = balance
        # Recast only after a prospective prepayment, preserving contractual history.
        if loan.repayment == 'annuity' and end > settlement and flows and flows[-1].prepayment:
            payment = annuity_payment(balance, loan.coupon, loan.frequency, count-i+1)
        interest = opening * periodic_rate
        if loan.repayment == 'annuity':
            principal = payment - interest
        elif loan.repayment == 'linear':
            principal = balance / (count-i+1) if end > settlement and loan.prepayment_model != 'none' else loan.notional / count
        else:
            principal = 0.0
        principal = opening if i == count else min(opening, max(0.0, principal))
        scheduled = principal
        cpr, smm, prepaid, exposure = (prepayment(loan, formula, settlement, start, end, opening,
            scheduled, loan.coupon, discount_curve, i) if end > settlement else (0, 0, 0, 0))
        principal += prepaid
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
                                 opening, balance, scheduled, prepaid, cpr, smm, exposure))
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
    formula = CPRFormula(loan.cpr_formula) if loan.prepayment_model == 'formula' else None
    dates = loan.schedule()
    periods = [(a, b) for a, b in zip(dates, dates[1:]) if b > settlement]
    count, initial = len(periods), balance
    constant_principal = initial / count
    flows, accrued, previous_reset = [], 0.0, None
    payment = current_payment
    survival = 1.0
    for i, (start, end) in enumerate(periods):
        if balance <= 0:
            break
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
            principal = constant_principal * survival
        else:
            principal = 0.0
        if i == count - 1:
            principal = opening
        principal = min(opening, principal)
        scheduled = principal
        cpr, smm, prepaid, exposure = prepayment(loan, formula, settlement, start, end, opening,
            scheduled, quote.coupon, discount_curve, dates.index(start)+1)
        principal += prepaid
        remaining = max(0.0, opening-scheduled)
        ratio = 1-prepaid/remaining if remaining > 0 else 1.0
        survival *= ratio
        if payment is not None:
            payment *= ratio
        balance = max(0.0, opening - principal)
        if start < settlement < end:
            accrued = interest * (settlement-start).days / (end-start).days
        factor = discount_curve.df(end) / settle_df * exp(
            -loan.discount_spread_bps / 10000 * year_fraction(settlement, end))
        flows.append(MortgageFlow(start, end, quote.coupon, 1 / loan.frequency,
                                 interest, principal, factor, (interest + principal) * factor,
                                 opening, balance, scheduled, prepaid, cpr, smm, exposure))
    dirty = sum(f.present_value for f in flows)
    clean = dirty - accrued
    if not all(isfinite(v) for v in (dirty, clean, accrued)):
        raise ValueError('Non-finite mortgage valuation; check rates and term')
    return Valuation(dirty, accrued, clean, dirty / initial * 100,
                     clean / initial * 100, tuple(flows))

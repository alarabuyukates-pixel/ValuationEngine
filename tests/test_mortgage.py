"""Independent amortization identities, settlement boundaries and workflow audit."""
from datetime import date
from math import exp
import unittest
from bond_engine import Curve
from mortgage_engine import Mortgage, value_mortgage
from workflow import run_workflow


def loan(**changes):
    values = dict(issue=date(2024, 1, 31), maturity=date(2026, 1, 31),
                  notional=120000, coupon=.06, frequency=12)
    values.update(changes)
    return Mortgage(**values)


def row(**changes):
    values = dict(_row=2, bond_id='MORTGAGE', kind='mortgage', currency='EUR',
                  issue='2024-01-31', maturity='2026-01-31', settlement='2024-01-31',
                  notional=120000, coupon=6, frequency=12, discount=4,
                  repayment='annuity')
    values.update(changes)
    return values


class MortgageTests(unittest.TestCase):
    def value(self, item=None, settlement=None, discount=0):
        item = item or loan()
        settlement = settlement or item.issue
        return value_mortgage(item, settlement, Curve.flat(settlement, item.maturity, discount))

    def test_annuity_matches_independent_closed_form_and_reconciles(self):
        item = loan()
        result = self.value(item)
        expected = 120000 * .005 / (1 - 1.005 ** -24)
        self.assertEqual(len(result.cashflows), 24)
        for f in result.cashflows:
            self.assertAlmostEqual(f.coupon + f.principal, expected, places=6)
            self.assertAlmostEqual(f.coupon, f.opening_balance * .005)
            self.assertAlmostEqual(f.opening_balance - f.principal, f.closing_balance)
        self.assertAlmostEqual(sum(f.principal for f in result.cashflows), 120000)
        self.assertEqual(result.cashflows[-1].closing_balance, 0)
        self.assertAlmostEqual(result.dirty_pv, expected * 24)

    def test_linear_and_interest_only(self):
        linear = self.value(loan(repayment='linear'))
        self.assertTrue(all(abs(f.principal - 5000) < 1e-8 for f in linear.cashflows))
        payments = [f.principal + f.coupon for f in linear.cashflows]
        self.assertTrue(all(a > b for a, b in zip(payments, payments[1:])))
        bullet = self.value(loan(repayment='interest-only'))
        self.assertTrue(all(f.principal == 0 for f in bullet.cashflows[:-1]))
        self.assertEqual(bullet.cashflows[-1].principal, 120000)
        self.assertTrue(all(f.coupon == 600 for f in bullet.cashflows))

    def test_zero_and_tiny_interest_annuity(self):
        for rate in (0, 1e-16):
            result = self.value(loan(coupon=rate))
            self.assertAlmostEqual(result.dirty_pv, 120000)
            self.assertAlmostEqual(result.cashflows[0].principal, 5000)

    def test_mid_period_accrued_and_payment_on_settlement_excluded(self):
        item = loan(repayment='linear')
        result = self.value(item, date(2024, 2, 15))
        self.assertAlmostEqual(result.accrued_interest, 600 * 15 / 29)
        boundary = self.value(item, date(2024, 2, 29))
        self.assertEqual(len(boundary.cashflows), 23)
        self.assertEqual(boundary.cashflows[0].opening_balance, 115000)
        self.assertEqual(boundary.accrued_interest, 0)
        self.assertAlmostEqual(boundary.clean_price_per_100, boundary.clean_pv / 115000 * 100)

    def test_pv_settlement_normalization_spread_and_discount_sensitivity(self):
        item = loan(discount_spread_bps=75)
        settlement = date(2024, 3, 15)
        curve = Curve.flat(item.issue, item.maturity, .03)
        result = value_mortgage(item, settlement, curve)
        expected = sum((f.coupon + f.principal) * exp(-.0375 * (f.payment-settlement).days / 365) for f in result.cashflows)
        self.assertAlmostEqual(result.dirty_pv, expected)
        self.assertGreater(self.value(discount=.02).dirty_pv, self.value(discount=.05).dirty_pv)

    def test_eom_leap_and_no_day_drift(self):
        dates = loan().schedule()
        self.assertEqual(dates[1], date(2024, 2, 29))
        self.assertEqual(dates[2], date(2024, 3, 31))
        item = loan(issue=date(2024, 1, 30), maturity=date(2026, 1, 30))
        self.assertEqual(item.schedule()[2], date(2024, 3, 30))
        for frequency in (1, 2, 4, 12):
            self.assertEqual(len(self.value(loan(frequency=frequency)).cashflows), 2 * frequency)

    def test_unsupported_terms_rejected(self):
        for changes in (dict(maturity=date(2026, 2, 1)), dict(coupon=-.01),
                        dict(notional=0), dict(frequency=3), dict(repayment='variable'),
                        dict(maturity=date(2075, 1, 31)), dict(discount_spread_bps=float('nan'))):
            with self.assertRaises(ValueError):
                loan(**changes)
        with self.assertRaises(ValueError):
            self.value(settlement=date(2026, 1, 31))
        with self.assertRaisesRegex(ValueError, 'coverage'):
            value_mortgage(loan(), date(2024, 1, 31), Curve.flat(date(2024, 1, 31), date(2025, 1, 31), .03))

    def test_workflow_mixed_portfolio_and_audit(self):
        fixed = row(_row=3, bond_id='BOND', kind='fixed', day_count='ACT/365F')
        result = run_workflow({'bonds': [row(), fixed, row(_row=4, rate_type='floating')], 'curves': []})
        self.assertEqual([s['Status'] for s in result['summary']], ['OK', 'OK', 'ERROR'])
        mortgage = result['summary'][0]
        self.assertEqual(mortgage['Outstanding principal'], 120000)
        self.assertEqual(mortgage['Repayment'], 'annuity')
        self.assertIn('past payments', mortgage['Warnings'])
        flows = [f for f in result['cashflows'] if f['Source row'] == 2]
        self.assertEqual(flows[-1]['Closing balance'], 0)
        self.assertAlmostEqual(sum(f['Present value'] for f in flows), mortgage['Dirty PV'])
        for changes in (dict(fixed_until='2025-01-31'), dict(currency='USD'), dict(coupon='')):
            self.assertEqual(len(run_workflow([row(**changes)])['errors']), 1)

    def test_named_curve_and_metadata_retained(self):
        curve = dict(id='ECB-TEST', currency='EUR', as_of='2024-01-30', source='Fixture',
                     curve_type='sovereign', pillars=[dict(date='2027-01-31', zero_rate=.03)])
        result = run_workflow({'bonds': [row(discount_curve='ECB-TEST')], 'curves': [curve]})
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['curves'][0]['Source'], 'Fixture')
        self.assertIn('Sovereign benchmark', result['summary'][0]['Warnings'])
        self.assertIn('1 calendar days', result['summary'][0]['Warnings'])


if __name__ == '__main__':
    unittest.main()

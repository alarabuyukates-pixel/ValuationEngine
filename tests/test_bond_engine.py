import unittest
from datetime import date
from math import exp
from bond_engine import Bond, Curve, value_bond, year_fraction

class BondTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2026, 1, 1)
        self.end = date(2028, 1, 1)
        self.curve = Curve.flat(self.start, self.end, 0.04)

    def test_zero_coupon(self):
        bond = Bond(self.start, self.end, coupon=0)
        self.assertAlmostEqual(value_bond(bond, self.start, self.curve).dirty_pv,
                               100*exp(-0.04*2))

    def test_fixed_manual_cashflows(self):
        bond = Bond(self.start, self.end, coupon=0.05, day_count='30E/360')
        result = value_bond(bond, self.start, self.curve)
        expected = sum(2.5*self.curve.df(d) for d in bond.schedule()[1:]) + 100*self.curve.df(self.end)
        self.assertAlmostEqual(result.dirty_pv, expected)

    def test_floating_par_with_same_curve(self):
        first = date(2026, 7, 1)
        rate = self.curve.forward(self.start, first, 'ACT/360')
        bond = Bond(self.start, self.end, kind='floating', fixings={self.start: rate})
        self.assertAlmostEqual(value_bond(bond, self.start, self.curve, self.curve).dirty_pv, 100)

    def test_accrued_and_settlement_normalization(self):
        settlement = date(2026, 4, 1)
        bond = Bond(self.start, self.end, coupon=0.06, day_count='30E/360')
        result = value_bond(bond, settlement, self.curve)
        self.assertAlmostEqual(result.accrued_interest, 1.5)
        self.assertAlmostEqual(result.clean_pv, result.dirty_pv-1.5)
        self.assertAlmostEqual(result.cashflows[0].discount_factor,
                               self.curve.df(date(2026, 7, 1))/self.curve.df(settlement))

    def test_missing_fixing(self):
        with self.assertRaisesRegex(ValueError, 'Missing floating'):
            value_bond(Bond(self.start, self.end, kind='floating'), self.start, self.curve, self.curve)

    def test_negative_rate_and_spread(self):
        curve = Curve.flat(self.start, self.end, -0.01)
        bond = Bond(self.start, self.end, kind='floating', spread=0.002,
                    fixings={self.start: -0.01})
        result = value_bond(bond, self.start, curve, curve)
        self.assertLess(result.cashflows[0].coupon, 0)
        self.assertGreater(result.cashflows[0].discount_factor, 1)

    def test_schedule_eom_and_stub(self):
        bond = Bond(date(2026, 3, 15), date(2027, 8, 31))
        self.assertEqual(bond.schedule(), [date(2026, 3, 15), date(2026, 8, 31),
                                         date(2027, 2, 28), date(2027, 8, 31)])

    def test_coupon_date_and_maturity(self):
        bond = Bond(self.start, self.end)
        result = value_bond(bond, date(2026, 7, 1), self.curve)
        self.assertEqual(result.accrued_interest, 0)
        self.assertEqual(len(result.cashflows), 3)
        self.assertEqual(value_bond(bond, self.end, self.curve).dirty_pv, 0)

    def test_curve_interpolation_and_coverage(self):
        curve = Curve(self.start, ((date(2027, 1, 1), 0.96), (self.end, 0.90)))
        self.assertAlmostEqual(curve.df(self.start), 1)
        self.assertAlmostEqual(curve.df(date(2027, 1, 1)), 0.96)
        with self.assertRaises(ValueError):
            curve.df(date(2029, 1, 1))
        with self.assertRaises(ValueError):
            Curve(self.start, ((self.end, 0),))

if __name__ == '__main__':
    unittest.main()

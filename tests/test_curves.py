"""Independent PV identities and model-boundary checks for market-curve valuation."""
from copy import deepcopy
from datetime import date
from math import exp
import unittest
from bond_engine import Bond, Curve, value_bond
from workflow import run_workflow


def bond(**changes):
    row = dict(_row=2, bond_id='EUR', currency='EUR', kind='fixed',
               issue='2026-01-01', maturity='2028-01-01', settlement='2026-01-01',
               notional=100000, frequency=4, day_count='ACT/365F', coupon=4,
               spread=.5, fixing=3, discount=4, projection=3.5)
    row.update(changes)
    return row


def curve(identifier='EUR-DISCOUNT', **changes):
    record = dict(id=identifier, currency='EUR', as_of='2026-01-01',
                  source='Verified test fixture', reference_index=None,
                  compounding='continuous', day_count='ACT/365F',
                  pillars=[dict(date='2027-01-01', discount_factor=.97),
                           dict(date='2028-01-01', discount_factor=.90)])
    record.update(changes)
    return record


class MarketCurveTests(unittest.TestCase):
    def test_euribor_day_count_cannot_be_overridden(self):
        with self.assertRaisesRegex(ValueError, 'EURIBOR.*ACT/360'):
            Bond(date(2026, 1, 1), date(2028, 1, 1), kind='floating',
                 frequency=4, index='EURIBOR-3M', index_day_count='ACT/365F')

    def test_malformed_pillar_is_a_row_error(self):
        result = run_workflow({'bonds': [bond(discount_curve='EUR-DISCOUNT')],
                               'curves': [curve(pillars=['invalid'])]})
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('Each curve pillar must be an object', result['errors'][0]['Error'])

    def value(self, row=None, curves=None):
        return run_workflow({'bonds': [row or bond()], 'curves': curves or []})

    def test_zero_has_one_principal_payment_and_no_coupon_inputs(self):
        row = {k: v for k, v in bond(kind='discount', discount_curve='EUR-DISCOUNT').items()
               if k not in ('coupon', 'spread', 'frequency', 'day_count', 'fixing')}
        result = self.value(row, [curve()])
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['cashflows']), 1)
        flow = result['cashflows'][0]
        self.assertEqual(flow['Interest'], 0)
        self.assertEqual(flow['Principal'], 100000)
        self.assertEqual(flow['Payment date'], '2028-01-01')
        self.assertAlmostEqual(flow['Present value'], 90000)
        self.assertEqual(result['summary'][0]['Accrued interest'], 0)
        self.assertEqual(result['summary'][0]['Clean PV'], result['summary'][0]['Dirty PV'])

    def test_zero_settlement_normalizes_discount_factor(self):
        row = bond(kind='zero', coupon=0, settlement='2027-01-01', discount_curve='EUR-DISCOUNT')
        result = self.value(row, [curve()])
        self.assertAlmostEqual(result['summary'][0]['Dirty PV'], 100000*.90/.97)
        self.assertIn('365 calendar days', result['summary'][0]['Warnings'])

    def test_discount_curve_shape_changes_pv(self):
        first = self.value(bond(discount_curve='EUR-DISCOUNT'), [curve()])
        other = curve(pillars=[dict(date='2027-01-01', discount_factor=.99), dict(date='2028-01-01', discount_factor=.9)])
        second = self.value(bond(discount_curve='EUR-DISCOUNT'), [other])
        self.assertGreater(second['summary'][0]['Dirty PV'], first['summary'][0]['Dirty PV'])

    def test_zero_quotes_support_explicit_compounding(self):
        for compounding, expected in [('continuous', exp(-.04*2)), ('annual', 1/1.04**2), ('simple', 1/1.08)]:
            item = curve(compounding=compounding, pillars=[dict(date='2028-01-01', zero_rate=.04)])
            self.assertAlmostEqual(Curve.from_record(item).df(date(2028,1,1)), expected)

    def test_floating_projection_uses_index_day_count_and_different_curve(self):
        projection = curve('EURIBOR-3M-FWD', reference_index='EURIBOR-3M',
                           pillars=[dict(date='2028-01-01', zero_rate=.03)])
        row = bond(kind='floating', index='EURIBOR-3M', discount_curve='EUR-DISCOUNT', projection_curve='EURIBOR-3M-FWD')
        result = self.value(row, [curve(), projection])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['cashflows'][0]['Rate source'], 'Known fixing + spread')
        self.assertAlmostEqual(result['cashflows'][0]['Coupon rate %'], 3.5)
        flow = result['cashflows'][1]
        start, end = date.fromisoformat(flow['Period start']), date.fromisoformat(flow['Payment date'])
        forward = (exp(.03*(end-start).days/365)-1)/((end-start).days/360)
        self.assertAlmostEqual(flow['Reference rate %'], forward*100)
        self.assertAlmostEqual(flow['Interest'], 100000*(forward+.005)*(end-start).days/365)
        self.assertEqual(result['summary'][0]['Index day count'], 'ACT/360')
        self.assertEqual(len(result['curves']), 3)

    def test_floating_par_reconciliation_with_shared_index_curve(self):
        projection = curve('EURIBOR', reference_index='EURIBOR-3M',
                           pillars=[dict(date='2028-01-01', zero_rate=.03)])
        fixing = Curve.from_record(projection).forward(date(2026,1,1),date(2026,4,1),'ACT/360')*100
        row = bond(kind='floating', index='EURIBOR-3M', day_count='ACT/360', spread=0,
                   fixing=fixing, discount_curve='EURIBOR', projection_curve='EURIBOR')
        result = self.value(row, [projection])
        self.assertAlmostEqual(result['summary'][0]['Dirty PV'], 100000)
        self.assertEqual(len(result['curves']),1)

    def test_wrong_index_and_sovereign_projection_rejected(self):
        row = bond(kind='floating', index='EURIBOR-3M', projection_curve='EUR-DISCOUNT')
        for reference in (None, 'EURIBOR-6M'):
            result = self.value(row, [curve(reference_index=reference)])
            self.assertIn('not tagged for EURIBOR-3M', result['errors'][0]['Error'])

    def test_unsupported_tenor_frequency_or_daycount_rejected(self):
        for changes in [dict(frequency=2), dict(index='EURIBOR-2M'), dict(index_day_count='ACT/365F')]:
            row = bond(kind='floating', index='EURIBOR-3M', **{k:v for k,v in changes.items() if k!='index'})
            row.update(changes)
            self.assertEqual(len(self.value(row)['errors']),1)

    def test_custom_term_index_requires_tenor(self):
        row = bond(kind='floating', index='CUSTOM-TERM')
        self.assertIn('requires its tenor', self.value(row)['errors'][0]['Error'])
        row['index_tenor'] = 3
        self.assertEqual(self.value(row)['errors'],[])

    def test_overnight_indices_are_not_misvalued_as_term_indices(self):
        row = bond(kind='floating', index='SOFR', index_tenor=3)
        self.assertIn('Overnight indices', self.value(row)['errors'][0]['Error'])

    def test_modern_floating_needs_index_and_fixing(self):
        self.assertIn('Missing floating index', self.value(bond(kind='floating'))['errors'][0]['Error'])
        self.assertIn('Missing fixing', self.value(bond(kind='floating', index='EURIBOR-3M', fixing=''))['errors'][0]['Error'])
        self.assertEqual(run_workflow([bond(kind='floating')])['errors'],[])

    def test_currency_and_curve_date_mismatches_are_errors(self):
        row = bond(discount_curve='EUR-DISCOUNT')
        for changes in [dict(currency='USD'), dict(as_of='2026-01-02')]:
            self.assertEqual(len(self.value(row, [curve(**changes)])['errors']),1)
        self.assertIn('requires bond currency EUR', self.value(bond(kind='floating',index='EURIBOR-3M',currency='USD'))['errors'][0]['Error'])

    def test_coverage_missing_curve_and_duplicates_never_fall_back(self):
        row = bond(discount_curve='EUR-DISCOUNT')
        self.assertIn('Unknown discount curve',self.value(row)['errors'][0]['Error'])
        short = curve(pillars=[dict(date='2027-01-01',discount_factor=.97)])
        self.assertIn('outside curve coverage', self.value(row,[short])['errors'][0]['Error'])
        self.assertIn('Duplicate curve ID',self.value(row,[curve(),curve()])['errors'][0]['Error'])

    def test_invalid_pillars_rejected_and_negative_rates_allowed(self):
        for pillars in [[dict(date='2028-01-01',discount_factor=0)],
                        [dict(date='2028-01-01',discount_factor=.9,zero_rate=.04)],
                        [dict(date='2028-01-01',zero_rate=float('nan'))]]:
            with self.assertRaises(ValueError):
                Curve.from_record(curve(pillars=pillars))
        item = curve(pillars=[dict(date='2028-01-01',zero_rate=-.01)])
        self.assertGreater(Curve.from_record(item).df(date(2028,1,1)),1)

    def test_zero_direct_api_and_conflicting_coupon(self):
        item = Bond(date(2026,1,1),date(2028,1,1),kind='zero',frequency=0)
        result = value_bond(item,date(2026,1,1),Curve.flat(date(2026,1,1),date(2028,1,1),.04))
        self.assertEqual(len(result.cashflows),1)
        self.assertAlmostEqual(result.dirty_pv,100*exp(-.08))
        self.assertIn('blank or zero coupon',self.value(bond(kind='zero',coupon=5))['errors'][0]['Error'])


if __name__ == '__main__':
    unittest.main()

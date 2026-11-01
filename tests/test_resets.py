"""Independent reset, amortization and full-tenor coverage regressions."""
from datetime import date
from math import exp
import unittest
from workflow import run_workflow


def instrument(**changes):
    row = dict(bond_id='FLOAT-MTG', kind='mortgage', currency='EUR', rate_type='floating',
               issue='2026-01-01', maturity='2028-01-01', settlement='2026-01-01',
               notional=120000, frequency=12, day_count='ACT/360', repayment='annuity',
               index_mode='curve', index_tenor=3, reset_months=3, index_day_count='ACT/360',
               fixing=3, spread=1, projection=5, discount=4)
    row.update(changes)
    return row


def value(**changes):
    return run_workflow({'bonds':[instrument(**changes)], 'curves':[]})


class ResetTests(unittest.TestCase):
    def test_monthly_payments_quarterly_resets_and_forward_identity(self):
        result = value()
        self.assertEqual(result['errors'], [])
        f = result['cashflows']
        self.assertEqual(len(f), 24)
        for row in f[:3]:
            self.assertEqual(row['Rate source'], 'Known fixing + spread')
            self.assertAlmostEqual(row['Coupon rate %'], 4)
        # April reset projects APR -> JUL, not each monthly payment interval.
        apr = f[3]
        expected = (exp(.05 * 91 / 365) - 1) / (91 / 360)
        self.assertAlmostEqual(apr['Reference rate %'], expected * 100)
        self.assertEqual(apr['Index end date'], '2026-07-01')
        self.assertEqual(f[4]['Reference rate %'], apr['Reference rate %'])
        self.assertEqual(f[5]['Reference rate %'], apr['Reference rate %'])
        self.assertAlmostEqual(sum(row['Principal'] for row in f), 120000)
        self.assertEqual(f[-1]['Closing balance'], 0)
        self.assertAlmostEqual(f[3]['Total payment'], f[4]['Total payment'])
        self.assertNotAlmostEqual(f[2]['Total payment'], f[3]['Total payment'])
        r = (expected + .01) / 12
        expected_payment = apr['Opening balance'] * r / (1 - (1+r)**-21)
        self.assertAlmostEqual(apr['Total payment'], expected_payment)

    def test_seasoned_loan_uses_supplied_balance_payment_and_current_fixing(self):
        result = value(settlement='2026-02-15', outstanding=110000, current_payment=5200)
        self.assertEqual(result['errors'], [])
        f = result['cashflows']
        self.assertEqual(f[0]['Opening balance'], 110000)
        self.assertAlmostEqual(f[0]['Total payment'], 5200)
        self.assertAlmostEqual(result['summary'][0]['Accrued interest'], 110000 * .04 / 12 * 14/28)
        self.assertAlmostEqual(sum(row['Principal'] for row in f), 110000)
        self.assertEqual(f[0]['Rate source'], 'Known fixing + spread')
        self.assertAlmostEqual(f[1]['Total payment'], 5200)
        self.assertNotAlmostEqual(f[2]['Total payment'], 5200)
        self.assertEqual(f[2]['Reset date'], '2026-04-01')

    def test_missing_seasoned_balance_or_current_payment_rejected(self):
        self.assertIn('outstanding', value(settlement='2026-02-01')['errors'][0]['Error'])
        self.assertIn('instalment', value(settlement='2026-02-01',outstanding=110000)['errors'][0]['Error'])
        self.assertIn('does not amortize', value(settlement='2026-02-01',outstanding=110000,current_payment=10)['errors'][0]['Error'])

    def test_linear_and_interest_only_float(self):
        for repayment in ('linear', 'interest-only'):
            r = value(repayment=repayment,settlement='2026-02-01',outstanding=100000)
            self.assertEqual(r['errors'], [])
            flows = r['cashflows']
            self.assertAlmostEqual(sum(f['Principal'] for f in flows), 100000)
            if repayment == 'linear':
                self.assertTrue(all(abs(f['Principal']-100000/23) < 1e-7 for f in flows))
            else:
                self.assertEqual(flows[0]['Principal'], 0)
                self.assertEqual(flows[-1]['Principal'], 100000)

    def test_same_reset_logic_for_bonds(self):
        r = value(kind='floating')
        self.assertEqual(r['errors'], [])
        f = r['cashflows']
        self.assertAlmostEqual(f[3]['Interest'], 120000 * f[3]['Coupon rate %']/100 * 30/360)
        self.assertEqual(sum(row['Principal'] for row in f),120000)
        self.assertEqual(f[0]['Reference rate %'],f[2]['Reference rate %'])

    def test_named_index_cannot_silently_use_sovereign(self):
        c = dict(id='AAA',currency='EUR',as_of='2026-01-01',curve_type='sovereign',source='ECB fixture',
                 pillars=[dict(date='2029-01-01',zero_rate=.04)])
        row=instrument(index_mode='named', index='EURIBOR-3M',projection_curve='AAA')
        r=run_workflow({'bonds':[row],'curves':[c]})
        self.assertIn('not tagged',r['errors'][0]['Error'])
        row.update(index_mode='curve',index='')
        r=run_workflow({'bonds':[row],'curves':[c]})
        self.assertEqual(r['errors'],[])
        self.assertIn('not a quoted EURIBOR',r['summary'][0]['Warnings'])

    def test_full_index_tenor_after_final_payment_requires_coverage(self):
        c=dict(id='SHORT',currency='EUR',as_of='2026-01-01',pillars=[dict(date='2028-01-01',zero_rate=.04)])
        r=run_workflow({'bonds':[instrument(reset_months=1,projection_curve='SHORT')],'curves':[c]})
        self.assertIn('coverage',r['errors'][0]['Error'])
        r=value(reset_months=1)
        self.assertEqual(r['errors'],[])
        self.assertEqual(r['cashflows'][-1]['Index end date'],'2028-03-01')

    def test_next_reset_validation_and_payment_boundary(self):
        for changes in (dict(next_reset='2026-02-15'),dict(next_reset='2026-01-01'),
                        dict(next_reset='2027-01-01'),dict(frequency=4,reset_months=1)):
            self.assertEqual(len(value(**changes)['errors']),1)
        r=value(next_reset='2026-02-01')
        self.assertEqual(r['errors'],[])
        self.assertEqual(r['cashflows'][1]['Reset date'],'2026-02-01')

    def test_zero_spread_par_identity_for_matched_coupon_and_reset(self):
        # Same 3M projection/discount curve and correctly fixed first coupon -> par.
        fixing=(exp(.04*90/365)-1)/(90/360)*100
        r=value(kind='floating',frequency=4,spread=0,projection=4,fixing=fixing)
        self.assertEqual(r['errors'],[])
        self.assertAlmostEqual(r['summary'][0]['Dirty PV'],120000)

    def test_negative_rates_remain_explicit_no_hidden_floor(self):
        r=value(fixing=-.5,spread=0,projection=-.5)
        self.assertEqual(r['errors'],[])
        self.assertLess(r['cashflows'][0]['Interest'],0)
        self.assertEqual(r['cashflows'][-1]['Closing balance'],0)

    def test_mixed_failure_isolation(self):
        r=run_workflow({'bonds':[instrument(_row=1),instrument(_row=2,fixing=''),instrument(_row=3,kind='zero',coupon=0)],'curves':[]})
        self.assertEqual([s['Status'] for s in r['summary']],['OK','ERROR','OK'])
        self.assertFalse(any(f['Source row']==2 for f in r['cashflows']))


if __name__ == '__main__':
    unittest.main()

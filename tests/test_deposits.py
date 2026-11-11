"""Time-deposit economics, liability signs, irregular terms and scenario repricing."""
from datetime import date, timedelta
from math import exp
import unittest
from bond_engine import Curve, year_fraction
from deposit_engine import TimeDeposit, value_deposit
from scenario_engine import run_scenarios
from workflow import run_workflow


def row(**changes):
    data=dict(_row=1,bond_id='TD-32D',kind='deposit',issue='2026-01-01',
              maturity='2026-02-02',settlement='2026-01-01',notional=100000,
              coupon=4,discount=3,day_count='ACT/365F',deposit_side='liability',
              deposit_payout='maturity')
    data.update(changes)
    return data


class DepositTests(unittest.TestCase):
    def result(self, **changes):
        result=run_workflow([row(**changes)])
        self.assertEqual(result['errors'],[],result['errors'])
        return result

    def test_bank_maturity_payment_and_pv_independent(self):
        result=self.result()
        f=result['cashflows'][0]
        interest=-100000*.04*32/365
        self.assertAlmostEqual(f['Interest'],interest)
        self.assertEqual(f['Principal'],-100000)
        self.assertAlmostEqual(f['Present value'],(-100000+interest)*exp(-.03*32/365))
        self.assertEqual(f['Opening balance'],-100000)
        self.assertEqual(f['Closing balance'],0)
        self.assertEqual(result['summary'][0]['Type'],'deposit')
        self.assertEqual(result['summary'][0]['Term days'],32)
        self.assertEqual(result['summary'][0]['Position side'],'liability')
        self.assertEqual(result['summary'][0]['Signed principal'],-100000)

    def test_holder_is_exact_opposite_without_changing_input_notional(self):
        bank=self.result();holder=self.result(deposit_side='asset')
        for k in ['Interest','Principal','Total payment','Present value','Opening balance']:
            self.assertEqual(bank['cashflows'][0][k],-holder['cashflows'][0][k])
        for k in ['Dirty PV','Clean PV','Accrued interest','Total interest','Total principal']:
            self.assertEqual(bank['summary'][0][k],-holder['summary'][0][k])
        self.assertEqual(bank['summary'][0]['Notional'],100000)

    def test_arbitrary_day_terms_and_leap_day(self):
        start=date(2024,2,27)
        rows=[row(_row=i,issue=str(start),settlement=str(start),maturity=str(start+timedelta(days=n)))
              for i,n in enumerate([1,7,14,32,46,92,181,365,730],1)]
        result=run_workflow(rows)
        self.assertEqual(result['errors'],[])
        self.assertEqual(len(result['cashflows']),len(rows))
        for r,f in zip(rows,result['cashflows']):
            days=(date.fromisoformat(r['maturity'])-start).days
            self.assertAlmostEqual(f['Interest'],-100000*.04*days/365)

    def test_all_day_counts(self):
        for basis in ('ACT/365F','ACT/360','30E/360'):
            result=self.result(issue='2024-01-31',settlement='2024-02-15',maturity='2024-03-31',day_count=basis)
            self.assertAlmostEqual(result['cashflows'][0]['Interest'],-4000*year_fraction(date(2024,1,31),date(2024,3,31),basis))
            self.assertAlmostEqual(result['summary'][0]['Accrued interest'],-4000*year_fraction(date(2024,1,31),date(2024,2,15),basis))

    def test_periodic_full_year_no_capitalization(self):
        result=self.result(maturity='2027-01-01',deposit_payout='periodic',frequency=12)
        fs=result['cashflows']
        self.assertEqual(len(fs),12)
        self.assertAlmostEqual(sum(f['Interest'] for f in fs),-4000)
        self.assertTrue(all(f['Opening balance']==-100000 for f in fs))
        self.assertTrue(all(f['Principal']==0 for f in fs[:-1]))
        self.assertEqual(fs[-1]['Principal'],-100000)
        for f in fs:
            self.assertAlmostEqual(f['Interest'],-4000*(date.fromisoformat(f['Payment date'])-date.fromisoformat(f['Period start'])).days/365)

    def test_short_first_stub_and_periodic_settlement_boundary(self):
        full=self.result(issue='2026-01-15',settlement='2026-01-15',maturity='2026-07-31',deposit_payout='periodic',frequency=12)
        fs=full['cashflows']
        self.assertEqual(fs[0]['Payment date'],'2026-01-31')
        self.assertEqual(len(fs),7)
        self.assertAlmostEqual(fs[0]['Interest'],-4000*16/365)
        boundary=self.result(issue='2026-01-15',settlement='2026-02-28',maturity='2026-07-31',deposit_payout='periodic',frequency=12)
        self.assertEqual(boundary['cashflows'][0]['Payment date'],'2026-03-31')
        self.assertEqual(boundary['summary'][0]['Accrued interest'],0)
        mid=self.result(issue='2026-01-15',settlement='2026-03-15',maturity='2026-07-31',deposit_payout='periodic',frequency=12)
        self.assertAlmostEqual(mid['summary'][0]['Accrued interest'],-4000*15/365)

    def test_periodic_frequencies_and_settled_coupon_exclusion(self):
        for frequency in (1,2,3,4,6,12):
            result=self.result(maturity='2028-01-01',settlement='2027-01-01',deposit_payout='periodic',frequency=frequency)
            self.assertEqual(len(result['cashflows']),frequency)
            self.assertAlmostEqual(sum(f['Interest'] for f in result['cashflows']),-4000)
            self.assertEqual(result['summary'][0]['Accrued interest'],0)

    def test_maturity_payout_ignores_frequency_and_defaults(self):
        for frequency in ('',99,'not used'):
            result=self.result(frequency=frequency,deposit_side='',deposit_payout='')
            self.assertEqual(len(result['cashflows']),1)
            self.assertEqual(result['summary'][0]['Position side'],'liability')

    def test_zero_negative_rates_and_repayment_guard(self):
        self.assertEqual(self.result(coupon=0)['cashflows'][0]['Interest'],0)
        self.assertGreater(self.result(coupon=-1)['cashflows'][0]['Interest'],0)
        self.assertEqual(len(run_workflow([row(coupon=-2000)])['errors']),1)

    def test_invalid_rows_isolate_and_emit_no_flows(self):
        for changes in [dict(notional=0),dict(coupon='nan'),dict(deposit_side='short'),
                dict(deposit_payout='compound'),dict(day_count='ACT/ACT'),dict(rate_type='floating'),
                dict(prepayment_model='cpr',cpr=6),dict(settlement='2025-12-31'),dict(settlement='2026-02-02'),
                dict(deposit_payout='periodic',frequency=5),dict(maturity='2077-01-01')]:
            result=run_workflow([row(**changes),row(_row=2)])
            self.assertEqual([s['Status'] for s in result['summary']],['ERROR','OK'],changes)
            self.assertTrue(all(f['Source row']==2 for f in result['cashflows']))

    def test_aliases(self):
        for kind in ('deposit','time deposit','time_deposit','term deposit','td'):
            self.assertEqual(self.result(kind=kind)['summary'][0]['Type'],'deposit')

    def test_dated_curve_normalization_and_coverage(self):
        curve=dict(id='DISC',currency='EUR',as_of='2025-12-01',source='fixture',
                   pillars=[dict(date='2027-01-01',zero_rate=.03)])
        result=run_workflow(dict(bonds=[row(discount_curve='DISC',settlement='2026-01-15')],curves=[curve]))
        self.assertEqual(result['errors'],[])
        self.assertAlmostEqual(result['cashflows'][0]['Present value'],result['cashflows'][0]['Total payment']*exp(-.03*18/365))
        short={**curve,'pillars':[dict(date='2026-01-20',zero_rate=.03)]}
        self.assertEqual(len(run_workflow(dict(bonds=[row(discount_curve='DISC')],curves=[short]))['errors']),1)

    def test_discount_scenarios_preserve_flows_and_liability_direction(self):
        result=run_scenarios(dict(bonds=[row()],curves=[],scenarios=[
            dict(id='UP',name='Up',kind='parallel',bps=100,role='discount',curve_id='all'),
            dict(id='PROJ',name='Projection',kind='parallel',bps=100,role='projection',curve_id='all')]))
        self.assertEqual(result['errors'],[])
        base=result['summary'][0]['Dirty PV']
        self.assertLess(base,result['scenario_results'][0]['summary'][0]['Dirty PV'])
        self.assertEqual(base,result['scenario_results'][1]['summary'][0]['Dirty PV'])
        for scenario in result['scenario_results']:
            self.assertEqual(scenario['cashflows'][0]['Interest'],result['cashflows'][0]['Interest'])
            self.assertEqual(scenario['cashflows'][0]['Principal'],-100000)
        self.assertIn('signed',result['scenario_totals'][0]['Note'])

    def test_mixed_asset_liability_totals_and_mixed_date_withholding(self):
        result=run_scenarios(dict(bonds=[row(),row(_row=2,deposit_side='asset',notional=200000)],curves=[],
            scenarios=[dict(id='UP',name='Up',kind='parallel',bps=100,role='discount',curve_id='all')]))
        self.assertAlmostEqual(result['scenario_totals'][0]['Base dirty PV'],-result['summary'][0]['Dirty PV'])
        result=run_scenarios(dict(bonds=[row(),row(_row=2,settlement='2026-01-02')],curves=[],
            scenarios=[dict(id='UP',name='Up',kind='parallel',bps=100,role='discount',curve_id='all')]))
        self.assertEqual(result['scenario_totals'][0]['Status'],'MIXED DATES')


if __name__=='__main__':
    unittest.main()

import unittest
from copy import deepcopy
from datetime import date
from math import exp
from bond_engine import Curve
from scenario_engine import ShockedCurve, shock_bps, validate_scenarios, run_scenarios


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(_row=1,bond_id='A',kind='zero',currency='EUR',issue='2026-01-01',
                        settlement='2026-11-01',maturity='2031-01-01',notional=100000,discount=3.5)
        self.up = dict(id='UP',name='+100 bps',kind='parallel',bps=100,role='both')
    def run_rows(self, rows, scenarios=None, curves=None):
        return run_scenarios(dict(bonds=rows,curves=curves or [],scenarios=scenarios if scenarios is not None else [self.up]))
    def floating(self, mortgage=False):
        return dict(self.row,kind='mortgage' if mortgage else 'floating',rate_type='floating',
                    frequency=12 if mortgage else 4,day_count='ACT/360',spread=.6,fixing=3.4,
                    index_mode='curve',index_tenor=3,reset_months=3,projection=3.3,
                    repayment='annuity',outstanding=98000,current_payment=1900)
    def test_parallel_zero_analytic(self):
        r=self.run_rows([self.row]); b=r['summary'][0]['Dirty PV']; s=r['scenario_results'][0]['summary'][0]['Dirty PV']
        t=(date(2031,1,1)-date(2026,11,1)).days/365
        self.assertAlmostEqual(s,b*exp(-.01*t),places=8)
        self.assertAlmostEqual(r['sensitivity'][0]['Delta PV'],s-b)
        self.assertEqual(r['scenario_totals'][0]['Status'],'OK')
    def test_zero_shock_identity_and_no_mutation(self):
        payload=dict(bonds=[self.floating(True)],curves=[],scenarios=[dict(self.up,bps=0)])
        original=deepcopy(payload);r=run_scenarios(payload)
        self.assertEqual(payload,original)
        self.assertEqual(r['sensitivity'][0]['Delta PV'],0)
        self.assertEqual(r['cashflows'][0]['Total payment'],r['scenario_results'][0]['cashflows'][0]['Total payment'])
    def test_fixed_projection_only_has_no_effect(self):
        r=self.run_rows([dict(self.row,kind='fixed',frequency=2,day_count='30E/360',coupon=4)], [dict(self.up,role='projection')])
        self.assertEqual(r['sensitivity'][0]['Delta PV'],0)
    def test_projection_recasts_mortgage_not_current_fixing(self):
        r=self.run_rows([self.floating(True)],[dict(self.up,role='projection')]); base=r['cashflows']; shocked=r['scenario_results'][0]['cashflows']
        self.assertFalse(r['sensitivity'][0]['Discount shocked']);self.assertTrue(r['sensitivity'][0]['Projection shocked'])
        self.assertEqual(base[0]['Coupon rate %'],shocked[0]['Coupon rate %'])
        self.assertEqual(base[0]['Total payment'],shocked[0]['Total payment'])
        future=next(i for i,f in enumerate(base) if f['Rate source'].startswith('Projected'))
        self.assertGreater(shocked[future]['Coupon rate %'],base[future]['Coupon rate %'])
        self.assertGreater(shocked[future]['Total payment'],base[future]['Total payment'])
        self.assertEqual(shocked[-1]['Closing balance'],0)
        self.assertEqual(base[-1]['Discount factor'],shocked[-1]['Discount factor'])
    def test_discount_only_keeps_cashflows(self):
        r=self.run_rows([self.floating(True)],[dict(self.up,role='discount')])
        for b,s in zip(r['cashflows'],r['scenario_results'][0]['cashflows']):
            self.assertEqual(b['Total payment'],s['Total payment']);self.assertLess(s['Discount factor'],b['Discount factor'])
    def test_tenor_exact_query_and_endpoint_hold(self):
        s=validate_scenarios([dict(id='T',name='Twist',kind='tenor',knots=[dict(years=1,bps=-50),dict(years=5,bps=100)])])[0]
        self.assertEqual(shock_bps(s,0),-50);self.assertEqual(shock_bps(s,3),25);self.assertEqual(shock_bps(s,10),100)
        b=Curve.flat(date(2026,1,1),date(2036,1,1),.03);c=ShockedCurve(b,date(2026,11,1),s);d=date(2028,5,1);t=(d-date(2026,11,1)).days/365
        self.assertAlmostEqual(c.df(d),b.df(d)*exp(-shock_bps(s,t)/10000*t))
        self.assertEqual(c.df(date(2026,11,1)),b.df(date(2026,11,1)))
        with self.assertRaises(ValueError):c.df(date(2040,1,1))
    def test_named_curve_shared_role_isolation(self):
        curve=dict(id='C',currency='EUR',as_of='2026-01-01',reference_index='EURIBOR-3M',compounding='annual',day_count='ACT/360',pillars=[dict(date='2033-01-01',zero_rate=.03)])
        row=dict(self.floating(),index_mode='named',index='EURIBOR-3M',discount_curve='C',projection_curve='C')
        r=self.run_rows([row],[dict(self.up,role='projection',curve_ids=['C'])],[curve])
        self.assertFalse(r['sensitivity'][0]['Discount shocked']);self.assertTrue(r['sensitivity'][0]['Projection shocked'])
        self.assertEqual(r['cashflows'][-1]['Discount factor'],r['scenario_results'][0]['cashflows'][-1]['Discount factor'])
    def test_multiple_independent_not_cumulative(self):
        r=self.run_rows([self.row],[self.up,dict(self.up,id='DOWN',bps=-100),dict(self.up,id='UP2')])
        changes=[x['Delta PV'] for x in r['sensitivity']]
        self.assertLess(changes[0],0);self.assertGreater(changes[1],0);self.assertEqual(changes[0],changes[2])
    def test_targeting_other_curve_has_no_effect(self):
        curve=dict(id='UNUSED',currency='EUR',as_of='2026-01-01',pillars=[dict(date='2033-01-01',zero_rate=.03)])
        r=self.run_rows([self.row],[dict(self.up,curve_ids=['UNUSED'])],[curve]);self.assertEqual(r['sensitivity'][0]['Delta PV'],0)
    def test_errors_never_sum_as_zero(self):
        r=self.run_rows([self.row,dict(self.row,_row=2,notional=-1)])
        self.assertEqual(r['scenario_totals'][0]['Status'],'INCOMPLETE');self.assertEqual(r['scenario_totals'][0]['Delta PV'],'')
        self.assertEqual(r['sensitivity'][1]['Delta PV'],'');self.assertEqual(r['sensitivity'][0]['Status'],'OK')
    def test_mixed_dates_no_misleading_total(self):
        r=self.run_rows([self.row,dict(self.row,_row=2,settlement='2026-12-01')]);self.assertEqual(r['scenario_totals'][0]['Status'],'MIXED DATES')
    def test_validation(self):
        for s in [dict(self.up,bps=float('nan')),dict(self.up,bps=True),dict(self.up,id='BASE'),dict(self.up,role='bad'),dict(self.up,kind='tenor',knots=[dict(years=1,bps=1)]),dict(self.up,kind='tenor',knots=[dict(years=1,bps=1),dict(years=1,bps=2)])]:
            with self.subTest(s=s),self.assertRaises(ValueError):validate_scenarios([s])
        with self.assertRaises(ValueError):self.run_rows([self.row],[dict(self.up,curve_ids=['MISSING'])])
        with self.assertRaises(ValueError):self.run_rows([self.row,self.row])
    def test_no_selected_scenarios_base_works(self):
        r=self.run_rows([self.row],[]);self.assertEqual(len(r['summary']),1);self.assertEqual(r['scenario_results'],[])

if __name__=='__main__':unittest.main()

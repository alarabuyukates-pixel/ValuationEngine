"""Prospective CPR, independent survival identities, formula safety and scenarios."""
import unittest
from dataclasses import replace
from datetime import date
from math import exp
from bond_engine import Curve
from mortgage_engine import CPRFormula, Mortgage, value_mortgage
from workflow import run_workflow
from scenario_engine import run_scenarios


def row(**changes):
    r = dict(_row=1, bond_id='PREPAY', kind='mortgage', currency='EUR',
             issue='2024-01-01', maturity='2026-01-01', settlement='2024-01-01',
             notional=120000, coupon=6, frequency=12, discount=3,
             repayment='annuity', prepayment_model='cpr', cpr=12)
    r.update(changes)
    return r


def run(**changes):
    result = run_workflow([row(**changes)])
    assert not result['errors'], result['errors']
    return result


class PrepaymentTests(unittest.TestCase):
    def test_zero_cpr_and_none_match_existing_values(self):
        for repayment in ('annuity', 'linear', 'interest-only'):
            base = run(prepayment_model='none', repayment=repayment)
            zero = run(cpr=0, repayment=repayment)
            for a, b in zip(base['cashflows'], zero['cashflows']):
                for key in ('Interest', 'Principal', 'Closing balance', 'Present value'):
                    self.assertAlmostEqual(a[key], b[key], places=7)

    def test_constant_cpr_survival_independent_interest_only(self):
        result = run(repayment='interest-only')
        for n, f in enumerate(result['cashflows']):
            opening = 120000 * .88 ** (n/12)
            self.assertAlmostEqual(f['Opening balance'], opening)
            expected = opening*(1-.88**(1/12)) if n < 23 else 0
            self.assertAlmostEqual(f['Prepayment'], expected)
        self.assertAlmostEqual(sum(f['Principal'] for f in result['cashflows']),120000)

    def test_all_repayment_types_reconcile_and_reduce_payments(self):
        for method in ('annuity','linear','interest-only'):
            result = run(repayment=method)
            base = run(repayment=method, prepayment_model='none')
            fs = result['cashflows']
            self.assertEqual(len(fs),24)
            self.assertEqual(fs[-1]['Closing balance'],0)
            self.assertAlmostEqual(sum(f['Principal'] for f in fs),120000)
            self.assertLess(result['summary'][0]['Total interest'],base['summary'][0]['Total interest'])
            for i, f in enumerate(fs):
                self.assertAlmostEqual(f['Principal'], f['Scheduled principal']+f['Prepayment'])
                self.assertAlmostEqual(f['Opening balance']-f['Principal'],f['Closing balance'])
                if i: self.assertEqual(f['Opening balance'],fs[i-1]['Closing balance'])
            if method == 'annuity':
                self.assertLess(fs[1]['Interest']+fs[1]['Scheduled principal'],fs[0]['Interest']+fs[0]['Scheduled principal'])

    def test_full_cpr_early_payoff_all_repayments(self):
        for method in ('annuity','linear','interest-only'):
            result=run(cpr=100,repayment=method)
            self.assertEqual(len(result['cashflows']),1)
            self.assertAlmostEqual(result['cashflows'][0]['Principal'],120000)
            self.assertEqual(result['cashflows'][0]['Closing balance'],0)

    def test_partial_period_exposure_and_historical_balance(self):
        base=run(settlement='2024-07-16',prepayment_model='none')
        result=run(settlement='2024-07-16')
        f=result['cashflows'][0]
        self.assertEqual(f['Opening balance'],base['cashflows'][0]['Opening balance'])
        self.assertEqual(result['summary'][0]['Accrued interest'],base['summary'][0]['Accrued interest'])
        exposure=16/31/12
        self.assertAlmostEqual(f['Prepayment exposure years'],exposure)
        self.assertAlmostEqual(f['Prepayment'],(f['Opening balance']-f['Scheduled principal'])*(1-.88**exposure))
        boundary=run(settlement='2024-07-01')['cashflows'][0]
        self.assertEqual(boundary['Payment date'],'2024-08-01')
        self.assertEqual(boundary['Prepayment exposure years'],1/12)

    def test_frequencies_zero_rate_and_final_maturity(self):
        for frequency in (1,2,4,12):
            result=run(frequency=frequency,coupon=0)
            first=result['cashflows'][0]
            self.assertAlmostEqual(first['Period prepayment rate %']/100,1-.88**(1/frequency))
            self.assertEqual(result['cashflows'][-1]['Prepayment'],0)
            self.assertAlmostEqual(result['summary'][0]['Total principal'],120000)

    def test_constant_formula_matches_constant_cpr(self):
        a=run(cpr=6)
        b=run(cpr=6,prepayment_model='formula',cpr_formula='base_cpr_pct')
        self.assertEqual(a['cashflows'],b['cashflows'])
        self.assertEqual(a['summary'][0]['Dirty PV'],b['summary'][0]['Dirty PV'])

    def test_formula_variables_and_operations(self):
        formula=CPRFormula('min(100, max(0, base_cpr_pct + abs(coupon_pct-market_rate_pct) + sqrt(4) + log(exp(1)) + 2^2))')
        self.assertAlmostEqual(formula.evaluate(dict(base_cpr_pct=6,coupon_pct=5,market_rate_pct=3)),.15)
        result=run(prepayment_model='formula',cpr_formula='min(25, age_months * 0.2 + remaining_months * 0.01 + balance / original_principal + period)')
        self.assertGreater(result['cashflows'][0]['Annual CPR %'],0)
        self.assertNotEqual(result['cashflows'][0]['Annual CPR %'],result['cashflows'][1]['Annual CPR %'])

    def test_invalid_formulas_and_cpr_are_isolated(self):
        bad=['__import__("os")','balance.real','[1][0]','1 if balance else 0', 'sum([1])',
             'unknown + 1','1/0','exp(1000)','(-1)^0.5','2^1000','min()',
             'log(-1)','101','-1','nan','min(1, a=2)','True', '1'*501,
             '+'.join(['1']*60),'exp']
        for expression in bad:
            result=run_workflow([row(prepayment_model='formula',cpr_formula=expression),row(_row=2)])
            self.assertEqual([s['Status'] for s in result['summary']],['ERROR','OK'],expression)
            self.assertTrue(all(f['Source row']==2 for f in result['cashflows']))
        for changes in (dict(cpr=-1),dict(cpr=101),dict(cpr=float('nan')),dict(prepayment_model='bad')):
            self.assertEqual(len(run_workflow([row(**changes)])['errors']),1)

    def test_floating_repayment_and_current_instalment_reduction(self):
        for repayment in ('annuity','linear','interest-only'):
            result=run(rate_type='floating',repayment=repayment,settlement='2024-07-01',
                outstanding=95000,current_payment=6000,index_mode='curve',index_tenor=3,
                reset_months=3,fixing=3,spread=1,projection=3)
            fs=result['cashflows']
            self.assertEqual(fs[0]['Opening balance'],95000)
            self.assertAlmostEqual(sum(f['Principal'] for f in fs),95000)
            self.assertEqual(fs[-1]['Closing balance'],0)
            if repayment=='annuity':
                self.assertAlmostEqual(fs[0]['Interest']+fs[0]['Scheduled principal'],6000)
                self.assertLess(fs[1]['Interest']+fs[1]['Scheduled principal'],6000)
            full=run(rate_type='floating',repayment=repayment,settlement='2024-07-01',
                outstanding=95000,current_payment=6000,index_mode='curve',index_tenor=3,
                reset_months=3,fixing=3,spread=1,projection=3,cpr=100)
            self.assertEqual(len(full['cashflows']),1)

    def test_rate_incentive_recomputed_in_discount_scenarios(self):
        payload=dict(bonds=[row(prepayment_model='formula',cpr=6,
            cpr_formula='min(40, max(0, base_cpr_pct + 2 * (coupon_pct-market_rate_pct)))')],curves=[],
            scenarios=[dict(id='DOWN',name='Down',kind='parallel',bps=-100,role='discount',curve_id='all')])
        result=run_scenarios(payload)
        self.assertEqual(result['errors'],[])
        scenario=result['scenario_results'][0]
        self.assertEqual(scenario['errors'],[])
        self.assertGreater(scenario['cashflows'][0]['Annual CPR %'],result['cashflows'][0]['Annual CPR %'])
        self.assertGreater(scenario['summary'][0]['Total prepayment'],result['summary'][0]['Total prepayment'])

    def test_pv_matches_discounted_projected_flows(self):
        result=run(discount_spread_bps=50)
        expected=sum(f['Total payment']*exp(-.035*(date.fromisoformat(f['Payment date'])-date(2024,1,1)).days/365) for f in result['cashflows'])
        self.assertAlmostEqual(result['summary'][0]['Dirty PV'],expected)


if __name__=='__main__':
    unittest.main()

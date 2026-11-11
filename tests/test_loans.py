"""Loan product identity, shared schedule math and scenario/export metadata."""
import unittest
from workflow import run_workflow
from scenario_engine import run_scenarios
from test_prepayment import row


class LoanTests(unittest.TestCase):
    def test_repayment_math_matches_mortgage_without_losing_identity(self):
        for method in ('annuity', 'linear', 'interest-only'):
            for category in ('consumer','commercial','other'):
                for model in ('none','cpr','formula'):
                    terms=dict(repayment=method,prepayment_model=model,cpr_formula='base_cpr_pct')
                    mortgage=run_workflow([row(**terms)])
                    loan=run_workflow([row(kind='loan',loan_type=category,**terms)])
                    self.assertEqual(loan['errors'],[])
                    self.assertEqual(loan['summary'][0]['Type'],'loan')
                    self.assertEqual(loan['summary'][0]['Loan category'],category)
                    self.assertEqual(loan['summary'][0]['Dirty PV'],mortgage['summary'][0]['Dirty PV'])
                    self.assertEqual([{k:v for k,v in f.items() if k!='Rate source'} for f in loan['cashflows']],
                                     [{k:v for k,v in f.items() if k!='Rate source'} for f in mortgage['cashflows']])

    def test_fixed_aliases_and_defaults(self):
        for kind,category in [('loan','consumer'),('consumer loan','consumer'),('commercial loan','commercial'),('credit','consumer')]:
            result=run_workflow([row(kind=kind)])
            self.assertEqual(result['errors'],[])
            self.assertEqual(result['summary'][0]['Loan category'],category)
            self.assertEqual(result['inputs'][0]['kind'],kind)

    def test_floating_seasoned_loans_and_principal_conservation(self):
        for method in ('annuity','linear','interest-only'):
            result=run_workflow([row(kind='loan',loan_type='commercial',rate_type='floating',
                repayment=method,settlement='2024-07-01',outstanding=95000,current_payment=6000,
                index_mode='curve',index_tenor=3,reset_months=3,fixing=3,spread=1,projection=3)])
            self.assertEqual(result['errors'],[])
            self.assertEqual(result['summary'][0]['Rate type'],'floating')
            self.assertEqual(result['cashflows'][0]['Opening balance'],95000)
            self.assertAlmostEqual(sum(f['Principal'] for f in result['cashflows']),95000)
            self.assertEqual(result['cashflows'][-1]['Closing balance'],0)

    def test_bad_loan_isolated_and_error_uses_loan_language(self):
        result=run_workflow([row(kind='loan',loan_type='bad'),row(_row=2),row(_row=3,kind='loan',frequency=3)])
        self.assertEqual([s['Status'] for s in result['summary']],['ERROR','OK','ERROR'])
        self.assertIn('Loan category',result['errors'][0]['Error'])
        self.assertIn('Loan payments',result['errors'][1]['Error'])
        self.assertTrue(all(f['Source row']==2 for f in result['cashflows']))

    def test_scenarios_recompute_loan_cpr_and_keep_category(self):
        result=run_scenarios(dict(bonds=[row(kind='loan',loan_type='commercial',
            prepayment_model='formula',cpr_formula='max(0, 20 - market_rate_pct)')],curves=[],
            scenarios=[dict(id='UP',name='Up',kind='parallel',bps=100,role='discount',curve_id='all')]))
        scenario=result['scenario_results'][0]
        self.assertEqual(scenario['errors'],[])
        self.assertEqual(scenario['summary'][0]['Type'],'loan')
        self.assertEqual(scenario['summary'][0]['Loan category'],'commercial')
        self.assertLess(scenario['cashflows'][0]['Annual CPR %'],result['cashflows'][0]['Annual CPR %'])


if __name__=='__main__':
    unittest.main()

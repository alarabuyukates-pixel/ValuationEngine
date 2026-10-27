import unittest
import json
from workflow import run_workflow, run_json


def fixed(**overrides):
    row = dict(_row=2, bond_id='TEST', currency='EUR', kind='fixed',
               issue='2026-01-01', maturity='2028-01-01', settlement='2026-04-01',
               notional=100000, frequency=2, day_count='30E/360', coupon=5,
               discount=4, spread=0, projection=3.5, fixing=3.5)
    row.update(overrides)
    return row


class WorkflowTests(unittest.TestCase):
    def test_full_cashflows_and_reconciliation(self):
        result = run_workflow([fixed()])
        self.assertEqual(result['errors'], [])
        summary = result['summary'][0]
        flows = result['cashflows']
        self.assertEqual(len(flows), 4)
        self.assertEqual(flows[-1]['Payment date'], '2028-01-01')
        self.assertEqual(sum(f['Principal'] for f in flows), 100000)
        self.assertAlmostEqual(sum(f['Present value'] for f in flows), summary['Dirty PV'])
        self.assertAlmostEqual(summary['Dirty PV']-summary['Accrued interest'], summary['Clean PV'])
        for f in flows:
            self.assertAlmostEqual((f['Interest']+f['Principal'])*f['Discount factor'], f['Present value'])

    def test_floating_and_known_fixing(self):
        result = run_workflow([fixed(kind='floating', spread=0.5, fixing=3)])
        self.assertEqual(result['errors'], [])
        self.assertAlmostEqual(result['cashflows'][0]['Coupon rate %'], 3.5)

    def test_invalid_row_does_not_stop_valid_row(self):
        result = run_workflow([fixed(), fixed(_row=3, bond_id='BAD', notional=-10)])
        self.assertEqual([s['Status'] for s in result['summary']], ['OK', 'ERROR'])
        self.assertEqual(result['errors'][0]['Source row'], 3)
        self.assertTrue(all(f['Source row']==2 for f in result['cashflows']))

    def test_missing_fixing_rejected(self):
        result = run_workflow([fixed(kind='floating', fixing='')])
        self.assertIn('Missing fixing', result['errors'][0]['Error'])
        self.assertEqual(result['cashflows'], [])

    def test_strict_dates_and_frequencies(self):
        for overrides in [dict(issue='01/02/2026'), dict(frequency=2.5),
                          dict(settlement='2028-01-01'), dict(discount='NaN')]:
            self.assertEqual(len(run_workflow([fixed(**overrides)])['errors']), 1)

    def test_json_bridge_and_percentage_points(self):
        result = json.loads(run_json(json.dumps([fixed(coupon='5%', discount='4') ])))
        self.assertAlmostEqual(result['cashflows'][0]['Interest'], 2500)

    def test_distinct_currencies_preserved(self):
        result = run_workflow([fixed(), fixed(_row=3, currency='USD')])
        self.assertEqual([s['Currency'] for s in result['summary']], ['EUR', 'USD'])
        self.assertNotIn('total_pv', result)


if __name__ == '__main__':
    unittest.main()

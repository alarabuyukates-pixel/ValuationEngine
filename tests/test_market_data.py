"""ECB quote semantics and curve completeness, using explicit synthetic fixtures."""
import csv
from datetime import date
from io import StringIO
import importlib.util
from math import exp
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('market', Path(__file__).resolve().parents[1]/'scripts/refresh_market_data.py')
market = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market)


def fixture(change=None, omit=None):
    rows = []
    for tenor in market.TENORS:
        if tenor == omit:
            continue
        row = dict(DATA_TYPE_FM='SR_'+tenor, TIME_PERIOD='2026-01-30', OBS_VALUE='4',
                   CURRENCY='EUR', UNIT='PCPA', OBS_STATUS='A',
                   TITLE_COMPL='Svensson model - continuous compounding - Yield curve spot rate')
        if change and tenor == '1Y':
            row.update(change)
        rows.append(row)
    stream = StringIO(); writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return stream.getvalue()


class MarketDataTests(unittest.TestCase):
    def test_source_units_and_tenor_discounting(self):
        result = market.parse_ecb(fixture(), fetched_at='2026-01-31T12:00:00+00:00')
        curve = result['curves'][0]
        self.assertEqual(curve['as_of'], '2026-01-30')
        self.assertIsNone(curve['reference_index'])
        one = next(p for p in curve['pillars'] if p['tenor']=='1Y')
        self.assertEqual(one['quoted_zero_rate'], .04)
        self.assertAlmostEqual(one['discount_factor'], exp(-.04))
        self.assertEqual(len(curve['pillars']), len(market.TENORS))

    def test_incomplete_curve_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing curve tenors'):
            market.parse_ecb(fixture(omit='30Y'))

    def test_mixed_market_dates_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different observation dates'):
            market.parse_ecb(fixture(change={'TIME_PERIOD':'2026-01-29'}))

    def test_wrong_quote_convention_or_units_rejected(self):
        for change in [{'UNIT':'OTHER'}, {'CURRENCY':'USD'}, {'TITLE_COMPL':'par yield'},
                       {'OBS_VALUE':'NaN'}, {'OBS_STATUS':'M'}]:
            with self.assertRaises(ValueError):
                market.parse_ecb(fixture(change=change))

    def test_calendar_pillar_clips_month_end(self):
        self.assertEqual(market.maturity(date(2026, 8, 31), 6), date(2027, 2, 28))


if __name__ == '__main__':
    unittest.main()

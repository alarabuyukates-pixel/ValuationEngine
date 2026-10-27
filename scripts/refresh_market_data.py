"""Retrieve a dated ECB AAA sovereign curve; no EURIBOR/OIS proxy is implied."""
import argparse
import calendar
import csv
from datetime import date, datetime, timezone
from io import StringIO
import json
from math import exp, isfinite
from pathlib import Path
import sys
import urllib.request

TENORS = ('3M', '6M', '9M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '15Y', '20Y', '30Y')
SERIES = '+'.join('SR_' + tenor for tenor in TENORS)
URL = ('https://data-api.ecb.europa.eu/service/data/YC/'
       'B.U2.EUR.4F.G_N_A.SV_C_YM.' + SERIES + '?format=csvdata&lastNObservations=1')
OUTPUT = Path(__file__).resolve().parents[1] / 'dist' / 'market-data.json'


def tenor_months(tenor):
    return int(tenor[:-1]) * (12 if tenor.endswith('Y') else 1)


def maturity(as_of, months):
    year, month0 = divmod(as_of.year * 12 + as_of.month - 1 + months, 12)
    month = month0 + 1
    return date(year, month, min(as_of.day, calendar.monthrange(year, month)[1]))


def parse_ecb(content, fetched_at=None):
    rows = list(csv.DictReader(StringIO(content.lstrip('\ufeff'))))
    points, dates = {}, set()
    for row in rows:
        tenor = row.get('DATA_TYPE_FM', '').removeprefix('SR_')
        if tenor not in TENORS:
            continue
        if tenor in points:
            raise ValueError('Duplicate ECB tenor: ' + tenor)
        as_of = date.fromisoformat(row['TIME_PERIOD'])
        if as_of > datetime.now(timezone.utc).date():
            raise ValueError('ECB observation date is in the future')
        if row.get('CURRENCY') != 'EUR' or row.get('UNIT') != 'PCPA':
            raise ValueError('Unexpected ECB rate currency or units')
        if 'continuous compounding' not in row.get('TITLE_COMPL', '').lower():
            raise ValueError('ECB quote convention is not continuously compounded')
        if row.get('OBS_STATUS') not in ('A', 'E'):
            raise ValueError('ECB observation is not usable')
        quote = float(row['OBS_VALUE']) / 100
        if not isfinite(quote):
            raise ValueError('ECB rate is not finite')
        months = tenor_months(tenor)
        # ECB Svensson spot yields are quoted at T in YEARS. Preserve that
        # tenor in exp(-r*T), then attach the DF to its calendar maturity.
        factor = exp(-quote * months / 12)
        if not isfinite(factor) or factor <= 0:
            raise ValueError('ECB quote produced an invalid discount factor')
        points[tenor] = {'date': str(maturity(as_of, months)),
                         'discount_factor': factor, 'quoted_zero_rate': quote,
                         'tenor': tenor}
        dates.add(as_of)
    if set(points) != set(TENORS):
        raise ValueError('ECB response is missing curve tenors: ' + ', '.join(sorted(set(TENORS)-set(points))))
    if len(dates) != 1:
        raise ValueError('ECB tenors have different observation dates')
    observed = str(next(iter(dates)))
    return {'schema_version': 2,
            'fetched_at': fetched_at or datetime.now(timezone.utc).isoformat(),
            'refresh': {'url': URL, 'tenors': list(TENORS), 'tenor_column': 'DATA_TYPE_FM',
                        'date_column': 'TIME_PERIOD', 'value_column': 'OBS_VALUE'},
            'curves': [{'id': 'ECB_EUR_AAA', 'name': 'ECB euro-area AAA government zero curve',
                        'currency': 'EUR', 'as_of': observed, 'source': 'European Central Bank',
                        'source_url': URL, 'reference_index': None, 'curve_type': 'sovereign',
                        'compounding': 'continuous', 'day_count': 'ACT/365F',
                        'quote_time_basis': 'Quoted tenor in years; DFs placed at calendar maturities',
                        'description': 'AAA government-bond Svensson spot curve. Not an EURIBOR or OIS curve.',
                        'pillars': sorted(points.values(), key=lambda p: p['date'])}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-stale', action='store_true',
                        help='Keep an existing official snapshot unchanged if ECB retrieval fails')
    args = parser.parse_args()
    try:
        request = urllib.request.Request(URL, headers={'Accept': 'text/csv', 'User-Agent': 'Bondflow-market-data/2.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            snapshot = parse_ecb(response.read().decode('utf-8-sig'))
        if OUTPUT.exists():
            previous = json.loads(OUTPUT.read_text())
            if previous['curves'][0]['as_of'] > snapshot['curves'][0]['as_of']:
                raise ValueError('ECB response is older than the stored snapshot')
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        temp = OUTPUT.with_suffix('.tmp')
        temp.write_text(json.dumps(snapshot, indent=2) + '\n')
        temp.replace(OUTPUT)
        print('ECB AAA sovereign curve refreshed: ' + snapshot['curves'][0]['as_of'])
    except Exception as error:
        if not args.allow_stale or not OUTPUT.exists():
            raise
        existing = json.loads(OUTPUT.read_text())
        if not existing.get('curves') or existing['curves'][0].get('source') != 'European Central Bank':
            raise ValueError('No validated official snapshot is available') from error
        print(f'ECB refresh unavailable: {error}. Retaining dated snapshot '
              f'{existing["curves"][0]["as_of"]}; retrieval date was not changed.', file=sys.stderr)


if __name__ == '__main__':
    main()

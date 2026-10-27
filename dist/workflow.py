"""Shared browser/CLI workflow: normalized input rows -> valuations and cash flows."""
import json
from datetime import date, datetime
from math import isfinite
from bond_engine import Bond, Curve, value_bond


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def numeric(row, key):
    value = row.get(key)
    if value is None or str(value).strip() == '':
        raise ValueError(f'Missing {key.replace("_", " ")}')
    number = float(str(value).strip().replace(',', '').replace('%', ''))
    if not isfinite(number):
        raise ValueError(f'{key} must be finite')
    return number


def rate(row, key):
    return numeric(row, key) / 100


def run_workflow(rows):
    summaries, cashflows, errors, inputs = [], [], [], []
    if len(rows) > 5000:
        raise ValueError('Maximum 5,000 bonds per run')
    for index, row in enumerate(rows, 1):
        bond_id = str(row.get('bond_id') or f'Bond-{index}')
        source_row = row.get('_row', index + 1)
        currency = str(row.get('currency') or 'Unspecified').strip().upper()
        identity = {'Source row': source_row, 'Bond ID': bond_id, 'Currency': currency}
        inputs.append({**identity, **{k: v for k, v in row.items() if k != '_row'}})
        try:
            kind = str(row.get('kind') or '').strip().lower()
            aliases = {'frn': 'floating', 'float': 'floating', 'floating rate': 'floating',
                       'fixed rate': 'fixed', 'fixed-rate': 'fixed', 'floating-rate': 'floating'}
            kind = aliases.get(kind, kind)
            issue = parse_date(row.get('issue'))
            maturity = parse_date(row.get('maturity'))
            settlement = parse_date(row.get('settlement'))
            if not issue <= settlement < maturity:
                raise ValueError('Require issue date <= settlement date < maturity date')
            if (maturity - issue).days > 366 * 150:
                raise ValueError('Maximum bond term is 150 years')
            frequency = numeric(row, 'frequency')
            if not frequency.is_integer():
                raise ValueError('Payments per year must be an integer')
            frequency = int(frequency)
            coupon = rate(row, 'coupon') if kind == 'fixed' else 0
            spread = rate(row, 'spread') if kind == 'floating' else 0
            bond = Bond(issue, maturity, notional=numeric(row, 'notional'),
                        frequency=frequency, kind=kind, coupon=coupon, spread=spread,
                        day_count=str(row.get('day_count') or '').strip().upper())
            discount = Curve.flat(settlement, maturity, rate(row, 'discount'))
            projection = None
            if kind == 'floating':
                projection = Curve.flat(settlement, maturity, rate(row, 'projection'))
                fixing = rate(row, 'fixing')
                schedule = bond.schedule()
                for start, end in zip(schedule, schedule[1:]):
                    if start <= settlement < end:
                        bond.fixings[start] = fixing
                        break
            result = value_bond(bond, settlement, discount, projection)
            interest = sum(f.coupon for f in result.cashflows)
            principal = sum(f.principal for f in result.cashflows)
            summaries.append({**identity, 'Status': 'OK', 'Type': kind,
                'Settlement': str(settlement), 'Maturity': str(maturity),
                'Notional': bond.notional, 'Dirty PV': result.dirty_pv,
                'Accrued interest': result.accrued_interest, 'Clean PV': result.clean_pv,
                'Dirty price / 100': result.dirty_price_per_100,
                'Clean price / 100': result.clean_price_per_100,
                'Total interest': interest, 'Total principal': principal,
                'Total payments': interest + principal, 'Error': ''})
            for f in result.cashflows:
                cashflows.append({**identity, 'Period start': str(f.start),
                    'Payment date': str(f.payment), 'Coupon rate %': f.rate * 100,
                    'Accrual fraction': f.accrual, 'Interest': f.coupon,
                    'Principal': f.principal, 'Total payment': f.coupon + f.principal,
                    'Discount factor': f.discount_factor, 'Present value': f.present_value})
            if not all(isfinite(v) for v in (result.dirty_pv, result.accrued_interest, result.clean_pv)):
                raise ValueError('Non-finite valuation: check rates and term')
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as error:
            cashflows[:] = [f for f in cashflows if f['Source row'] != source_row]
            summaries[:] = [s for s in summaries if s['Source row'] != source_row]
            message = str(error)
            errors.append({**identity, 'Error': message})
            summaries.append({**identity, 'Status': 'ERROR', 'Error': message})
    return {'summary': summaries, 'cashflows': cashflows, 'errors': errors, 'inputs': inputs}


def run_json(payload):
    return json.dumps(run_workflow(json.loads(payload)), allow_nan=False)

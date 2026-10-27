"""Shared workflow: normalized bonds and dated market curves -> audited results."""
import json
from datetime import date, datetime
from math import isfinite
from bond_engine import Bond, Curve, value_bond, normalize_index, EURIBOR_TENORS


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
    """Normalized bond rate inputs are percentage points, e.g. 5 means 5%."""
    return numeric(row, key) / 100


def run_workflow(payload):
    """Accept legacy bond rows or {bonds: [...], curves: [...]}.

    Curve zero_rate inputs are DECIMAL annual rates, unlike percentage-point bond
    inputs. Named curves never extrapolate or fall back to flat rates. The current
    reference fixing remains a supplied contractual input, not a model forecast.
    """
    modern = isinstance(payload, dict)
    rows = payload.get('bonds', []) if modern else payload
    records = payload.get('curves', []) if modern else []
    if not isinstance(rows, list) or not isinstance(records, list):
        raise ValueError('Expected a bonds list and a curves list')
    if len(rows) > 5000:
        raise ValueError('Maximum 5,000 bonds per run')
    catalog, duplicate_ids = {}, set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Each curve must be an object')
        identifier = str(record.get('id') or '').strip()
        if not identifier:
            raise ValueError('Each curve requires a nonempty ID')
        if identifier in catalog:
            duplicate_ids.add(identifier)
        catalog[identifier] = record
    curve_cache, curve_audit = {}, {}
    summaries, cashflows, errors, inputs = [], [], [], []

    def resolve_curve(identifier, row, currency, settlement, maturity, role, index=''):
        identifier = str(identifier or '').strip()
        if not identifier or identifier.lower() in ('flat', 'flat override'):
            return (Curve.flat(settlement, maturity, rate(row, role)),
                    'FLAT OVERRIDE', str(settlement), 'User-supplied flat rate')
        if identifier in duplicate_ids:
            raise ValueError(f'Duplicate curve ID: {identifier}')
        if identifier not in catalog:
            raise ValueError(f'Unknown {role} curve: {identifier}')
        record = catalog[identifier]
        curve_currency = str(record.get('currency') or '').strip().upper()
        if not curve_currency:
            raise ValueError(f'Curve {identifier} requires a currency')
        if curve_currency != currency:
            raise ValueError(f'{role.title()} curve {identifier} currency {curve_currency} does not match bond {currency}')
        if role == 'projection':
            if not index:
                raise ValueError('A named projection curve requires a floating index')
            reference = normalize_index(record.get('reference_index') or '')
            if reference != index:
                raise ValueError(f'Projection curve {identifier} is not tagged for {index}; supply its index-specific projection curve')
        if identifier not in curve_cache:
            if len(record.get('pillars', [])) > 10000:
                raise ValueError(f'Curve {identifier} has too many pillars')
            curve_cache[identifier] = Curve.from_record(record)
        curve = curve_cache[identifier]
        if curve.as_of > settlement:
            raise ValueError(f'Curve {identifier} as-of date {curve.as_of} follows settlement {settlement}')
        # Enforce coverage before allocating any cash flows. No extrapolation.
        curve.df(settlement)
        curve.df(maturity)
        source = str(record.get('source') or 'Uploaded curve')
        if identifier not in curve_audit:
            quoted = {str(p['date']): p.get('zero_rate', p.get('quoted_zero_rate')) for p in record.get('pillars', [])}
            curve_audit[identifier] = [{
                'Curve ID': identifier, 'Currency': curve_currency,
                'As of': str(curve.as_of), 'Source': source,
                'Source URL': str(record.get('source_url') or ''),
                'Reference index': str(record.get('reference_index') or ''),
                'Compounding': str(record.get('compounding') or 'continuous'),
                'Rate day count': str(record.get('day_count') or 'ACT/365F'),
                'Quote time basis': str(record.get('quote_time_basis') or 'Declared curve day count'),
                'Pillar date': str(d), 'Discount factor': factor,
                'Zero rate %': float(quoted[str(d)]) * 100 if quoted.get(str(d)) is not None and str(quoted.get(str(d))).strip() else '',
            } for d, factor in curve.pillars]
        return curve, identifier, str(curve.as_of), source

    for position, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError('Each bond row must be an object')
        bond_id = str(row.get('bond_id') or f'Bond-{position}')
        source_row = row.get('_row', position + 1)
        currency = str(row.get('currency') or 'Unspecified').strip().upper()
        identity = {'Source row': source_row, 'Bond ID': bond_id, 'Currency': currency}
        inputs.append({**identity, **{k: v for k, v in row.items() if k != '_row'}})
        row_flows = []
        try:
            kind = str(row.get('kind') or '').strip().lower()
            aliases = {'frn': 'floating', 'float': 'floating', 'floating rate': 'floating',
                       'fixed rate': 'fixed', 'fixed-rate': 'fixed', 'floating-rate': 'floating',
                       'discount': 'zero', 'zero-coupon': 'zero', 'zero coupon': 'zero',
                       'zero coupon bond': 'zero', 'discount bond': 'zero'}
            kind = aliases.get(kind, kind)
            issue = parse_date(row.get('issue'))
            maturity = parse_date(row.get('maturity'))
            settlement = parse_date(row.get('settlement'))
            if not issue <= settlement < maturity:
                raise ValueError('Require issue date <= settlement date < maturity date')
            if (maturity - issue).days > 366 * 150:
                raise ValueError('Maximum bond term is 150 years')
            index = normalize_index(row.get('index') or '') if kind == 'floating' else ''
            if kind == 'floating' and modern and not index:
                raise ValueError('Missing floating index; select EURIBOR tenor or define a custom term index')
            if index in EURIBOR_TENORS and currency != 'EUR':
                raise ValueError(f'{index} requires bond currency EUR')
            tenor = None
            if kind == 'floating' and row.get('index_tenor') is not None and str(row.get('index_tenor')).strip():
                tenor = numeric(row, 'index_tenor')
                if not tenor.is_integer():
                    raise ValueError('Index tenor must be an integer number of months')
                tenor = int(tenor)
            if kind == 'zero':
                frequency, basis = 1, 'ACT/365F'
                if row.get('coupon') is not None and str(row.get('coupon')).strip() and numeric(row, 'coupon') != 0:
                    raise ValueError('Zero-coupon bond must have a blank or zero coupon')
            else:
                frequency = numeric(row, 'frequency')
                if not frequency.is_integer():
                    raise ValueError('Payments per year must be an integer')
                frequency = int(frequency)
                basis = str(row.get('day_count') or '').strip().upper()
            coupon = rate(row, 'coupon') if kind == 'fixed' else 0
            spread = rate(row, 'spread') if kind == 'floating' else 0
            bond = Bond(issue, maturity, notional=numeric(row, 'notional'),
                        frequency=frequency, kind=kind, coupon=coupon, spread=spread,
                        day_count=basis, index=index, index_tenor_months=tenor,
                        index_day_count=str(row.get('index_day_count') or '').strip().upper() or None)
            discount, discount_id, discount_date, discount_source = resolve_curve(
                row.get('discount_curve'), row, currency, settlement, maturity, 'discount')
            projection, projection_id, projection_date, projection_source = None, '', '', ''
            if kind == 'floating':
                projection, projection_id, projection_date, projection_source = resolve_curve(
                    row.get('projection_curve'), row, currency, settlement, maturity, 'projection', index)
                fixing = rate(row, 'fixing')
                schedule = bond.schedule()
                for start, end in zip(schedule, schedule[1:]):
                    if start <= settlement < end:
                        bond.fixings[start] = fixing
                        break
            result = value_bond(bond, settlement, discount, projection)
            interest = sum(f.coupon for f in result.cashflows)
            principal = sum(f.principal for f in result.cashflows)
            if not all(isfinite(v) for v in (result.dirty_pv, result.accrued_interest, result.clean_pv)):
                raise ValueError('Non-finite valuation: check rates and term')
            warnings = []
            for role, curve in [('Discount', discount), ('Projection', projection)]:
                if curve is not None and curve.as_of < settlement:
                    days = (settlement - curve.as_of).days
                    warnings.append(f'{role} curve snapshot is {days} calendar days before settlement; factors normalized to settlement')
            if kind == 'floating' and not index:
                warnings.append('Unspecified index accepted for legacy flat input')
            summaries.append({**identity, 'Status': 'OK', 'Type': kind,
                'Index': index, 'Index tenor months': bond.index_tenor_months or '',
                'Index day count': bond.index_day_count or '',
                'Settlement': str(settlement), 'Maturity': str(maturity),
                'Notional': bond.notional, 'Dirty PV': result.dirty_pv,
                'Accrued interest': result.accrued_interest, 'Clean PV': result.clean_pv,
                'Dirty price / 100': result.dirty_price_per_100,
                'Clean price / 100': result.clean_price_per_100,
                'Total interest': interest, 'Total principal': principal,
                'Total payments': interest + principal,
                'Discount curve': discount_id, 'Discount curve date': discount_date,
                'Discount curve source': discount_source,
                'Projection curve': projection_id, 'Projection curve date': projection_date,
                'Projection curve source': projection_source,
                'Warnings': '; '.join(warnings), 'Error': ''})
            for f in result.cashflows:
                rate_source = 'Fixed coupon' if kind == 'fixed' else 'Zero coupon'
                if kind == 'floating':
                    rate_source = 'Known fixing + spread' if f.start in bond.fixings else 'Projected forward + spread'
                row_flows.append({**identity, 'Period start': str(f.start),
                    'Payment date': str(f.payment), 'Index': index,
                    'Reference rate %': (f.rate - spread) * 100 if kind == 'floating' else '',
                    'Rate source': rate_source, 'Coupon rate %': f.rate * 100,
                    'Accrual fraction': f.accrual, 'Interest': f.coupon,
                    'Principal': f.principal, 'Total payment': f.coupon + f.principal,
                    'Discount curve': discount_id, 'Discount factor': f.discount_factor,
                    'Present value': f.present_value})
            cashflows.extend(row_flows)
        except (ValueError, TypeError, OverflowError, ZeroDivisionError, KeyError) as error:
            message = str(error)
            errors.append({**identity, 'Error': message})
            summaries.append({**identity, 'Status': 'ERROR', 'Error': message})
    return {'summary': summaries, 'cashflows': cashflows, 'errors': errors, 'inputs': inputs,
            'curves': [item for rows in curve_audit.values() for item in rows]}


def run_json(payload):
    return json.dumps(run_workflow(json.loads(payload)), allow_nan=False)

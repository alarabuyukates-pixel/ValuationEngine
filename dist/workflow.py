"""Shared workflow: normalized bonds and dated market curves -> audited results."""
import json
from datetime import date, datetime
from math import isfinite
from bond_engine import Bond, Curve, value_bond, normalize_index, EURIBOR_TENORS, year_fraction
from deposit_engine import TimeDeposit, value_deposit
from mortgage_engine import Mortgage, value_mortgage, value_floating_mortgage
from rate_projection import RateSchedule, value_reset_bond
from scenario_engine import shock_bps


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


def run_workflow(payload, scenario=None):
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

    def apply_shock(curve, identifier, row, settlement, role):
        from scenario_engine import applies, ShockedCurve, shock_bps
        if not applies(scenario, role, identifier):
            return curve
        shocked = ShockedCurve(curve, settlement, scenario)
        key = (identifier, role, str(settlement), row.get('_row'))
        curve_audit[key] = [{
            'Curve ID': identifier, 'Role': role, 'Scenario ID': scenario['id'],
            'Scenario': scenario['name'], 'Source row': row.get('_row', ''),
            'Currency': 'EUR', 'As of': str(curve.as_of), 'Shock anchor': str(settlement),
            'Pillar date': str(d), 'Base discount factor': factor,
            'Discount factor': shocked.df(d),
            'Shock bps': shock_bps(scenario, max(0, (d-settlement).days/365)) if d > settlement else 0,
            'Shock convention': 'Additive continuous zero; ACT/365F from settlement'
        } for d, factor in curve.pillars]
        return shocked

    def resolve_curve(identifier, row, currency, settlement, maturity, role, index='', index_mode='named'):
        identifier = str(identifier or '').strip()
        if not identifier or identifier.lower() in ('flat', 'flat override'):
            return (apply_shock(Curve.flat(settlement, maturity, rate(row, role)), 'FLAT OVERRIDE', row, settlement, role),
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
        if role == 'projection' and index_mode != 'curve':
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
        return apply_shock(curve, identifier, row, settlement, role), identifier, str(curve.as_of), source

    for position, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError('Each bond row must be an object')
        bond_id = str(row.get('bond_id') or f'Bond-{position}')
        source_row = row.get('_row', position + 1)
        currency = str(row.get('currency') or 'EUR').strip().upper()
        identity = {'Source row': source_row, 'Bond ID': bond_id, 'Currency': currency,
                    'Source file': row.get('_source_file', ''), 'Source worksheet': row.get('_source_sheet', ''),
                    'Workbook row': row.get('_source_row', '')}
        inputs.append({**identity, **{k: v for k, v in row.items() if k != '_row'}})
        row_flows = []
        try:
            kind = str(row.get('kind') or '').strip().lower()
            aliases = {'frn': 'floating', 'float': 'floating', 'floating rate': 'floating',
                       'fixed rate': 'fixed', 'fixed-rate': 'fixed', 'floating-rate': 'floating',
                       'discount': 'zero', 'zero-coupon': 'zero', 'zero coupon': 'zero',
                       'zero coupon bond': 'zero', 'discount bond': 'zero', 'mortgage loan': 'mortgage',
                       'consumer loan': 'loan', 'commercial loan': 'loan', 'credit': 'loan', 'time deposit': 'deposit',
                       'time_deposit': 'deposit', 'term deposit': 'deposit', 'td': 'deposit'}
            raw_kind = kind
            kind = aliases.get(kind, kind)
            amortizing = kind in ('mortgage', 'loan')
            loan_type = str(row.get('loan_type') or ('commercial' if raw_kind == 'commercial loan' else 'consumer')).strip().lower()
            if kind == 'loan' and loan_type not in ('consumer', 'commercial', 'other'):
                raise ValueError('Loan category must be consumer, commercial or other')
            if currency != 'EUR':
                raise ValueError('This workspace supports EUR only; EURIBOR requires bond currency EUR. No FX conversion is performed')
            issue = parse_date(row.get('issue'))
            maturity = parse_date(row.get('maturity'))
            settlement = parse_date(row.get('settlement'))
            if not issue <= settlement < maturity:
                raise ValueError('Require issue date <= settlement date < maturity date')
            if (maturity - issue).days > 366 * 150:
                raise ValueError('Maximum bond term is 150 years')
            rate_type = str(row.get('rate_type') or ('floating' if kind == 'floating' else 'fixed')).strip().lower()
            is_floating = kind == 'floating' or amortizing and rate_type == 'floating'
            if amortizing and rate_type not in ('fixed', 'floating'):
                raise ValueError('Mortgage rate type must be fixed or floating')
            if kind == 'deposit' and rate_type != 'fixed':
                raise ValueError('Time deposits currently support fixed interest only')
            deposit_payout = str(row.get('deposit_payout') or 'maturity').strip().lower()
            if kind == 'deposit' and deposit_payout not in ('maturity', 'periodic'):
                raise ValueError('Deposit interest payment must be maturity or periodic')
            if kind == 'deposit' and str(row.get('prepayment_model') or 'none').strip().lower() != 'none':
                raise ValueError('Time deposits do not support loan CPR; early withdrawal is not modeled')
            index_mode = str(row.get('index_mode') or 'named').strip().lower()
            if is_floating and index_mode not in ('named', 'curve'):
                raise ValueError('Index mode must be named or curve (explicit curve-implied reference)')
            index = normalize_index(row.get('index') or '') if is_floating else ''
            if is_floating and modern and not index and index_mode == 'named':
                raise ValueError('Missing floating index; select EURIBOR tenor or define a custom term index')
            if kind == 'zero':
                frequency, basis = 1, 'ACT/365F'
                if row.get('coupon') is not None and str(row.get('coupon')).strip() and numeric(row, 'coupon') != 0:
                    raise ValueError('Zero-coupon bond must have a blank or zero coupon')
            elif kind == 'deposit' and deposit_payout == 'maturity':
                frequency, basis = 1, str(row.get('day_count') or 'ACT/365F').strip().upper()
            else:
                frequency = numeric(row, 'frequency')
                if not frequency.is_integer() or frequency not in (1, 2, 3, 4, 6, 12):
                    raise ValueError('Payments per year must be an integer divisor of 12')
                frequency = int(frequency)
                basis = str(row.get('day_count') or '').strip().upper()
            tenor, reset_months, index_basis, resets = None, None, '', None
            spread, fixing = (rate(row, 'spread'), rate(row, 'fixing')) if is_floating else (0.0, 0.0)
            if is_floating:
                supplied_tenor = numeric(row, 'index_tenor') if str(row.get('index_tenor') or '').strip() else None
                if supplied_tenor is not None and (not supplied_tenor.is_integer() or supplied_tenor not in (1, 2, 3, 4, 6, 12)):
                    raise ValueError('Index tenor must be an integer: 1, 2, 3, 4, 6 or 12 months')
                if index_mode == 'curve':
                    if supplied_tenor is None:
                        raise ValueError('Curve-implied reference requires index tenor months')
                    tenor = int(supplied_tenor)
                    if index and index != f'CURVE-{tenor}M':
                        raise ValueError('Curve-implied mode must use a blank index or CURVE-nM, never a named benchmark')
                    index = f'CURVE-{tenor}M'
                elif index in EURIBOR_TENORS:
                    tenor = EURIBOR_TENORS[index]
                    if supplied_tenor is not None and supplied_tenor != tenor:
                        raise ValueError('Index tenor does not match the named EURIBOR index')
                else:
                    tenor = int(supplied_tenor) if supplied_tenor is not None else (12 // frequency if not index and not modern else None)
                    if tenor is None:
                        raise ValueError('A custom term index requires its tenor in months')
                index_basis = str(row.get('index_day_count') or ('ACT/360' if index in EURIBOR_TENORS or amortizing or index_mode == 'curve' else basis)).strip().upper()
                if index in EURIBOR_TENORS and index_basis != 'ACT/360':
                    raise ValueError('EURIBOR reference forwards use ACT/360')
                explicit_reset = str(row.get('reset_months') or '').strip()
                reset_value = numeric(row, 'reset_months') if explicit_reset else tenor
                if int(reset_value) != reset_value:
                    raise ValueError('Reset interval must be an integer number of months')
                reset_months = int(reset_value)
                if kind == 'floating' and index_mode == 'named' and not explicit_reset and frequency != 12 // tenor:
                    raise ValueError('Set reset months explicitly when payment frequency differs from index tenor')
            coupon = rate(row, 'coupon') if not is_floating and kind in ('fixed', 'mortgage', 'loan', 'deposit') else 0
            if amortizing:
                if str(row.get('fixed_until') or '').strip():
                    if is_floating or parse_date(row['fixed_until']) != maturity:
                        raise ValueError('Fixed-until applies only to loans fixed through maturity; hybrid loans are unsupported')
                bond = Mortgage(issue, maturity, numeric(row, 'notional'), coupon, frequency,
                                str(row.get('repayment') or 'annuity').strip().lower(),
                                numeric(row, 'discount_spread_bps') if str(row.get('discount_spread_bps') or '').strip() else 0.0,
                                str(row.get('prepayment_model') or 'none').strip().lower(),
                                numeric(row, 'cpr') if str(row.get('cpr') or '').strip() else 0.0,
                                str(row.get('cpr_formula') or '').strip())
            elif kind == 'deposit':
                bond = TimeDeposit(issue, maturity, numeric(row, 'notional'), coupon, basis,
                                   deposit_payout, frequency,
                                   str(row.get('deposit_side') or 'liability').strip().lower())
            else:
                # ResetSchedule owns index conventions, independently of payment frequency.
                bond = Bond(issue, maturity, notional=numeric(row, 'notional'),
                            frequency=frequency, kind=kind, coupon=coupon, spread=spread,
                            day_count=basis)
            discount, discount_id, discount_date, discount_source = resolve_curve(
                row.get('discount_curve'), row, currency, settlement, maturity, 'discount')
            projection, projection_id, projection_date, projection_source = None, '', '', ''
            if is_floating:
                resets = RateSchedule(bond.schedule(), settlement, frequency, tenor, reset_months,
                                      index_basis, fixing, spread,
                                      parse_date(row['next_reset']) if str(row.get('next_reset') or '').strip() else None)
                projection, projection_id, projection_date, projection_source = resolve_curve(
                    row.get('projection_curve'), row, currency, settlement,
                    max(maturity, resets.required_end), 'projection', index, index_mode)
                resets.bind(projection)
            if amortizing and is_floating:
                outstanding = numeric(row, 'outstanding') if str(row.get('outstanding') or '').strip() else None
                current_payment = numeric(row, 'current_payment') if str(row.get('current_payment') or '').strip() else None
                result = value_floating_mortgage(bond, settlement, discount, resets, outstanding, current_payment)
            elif amortizing:
                result = value_mortgage(bond, settlement, discount)
            elif kind == 'deposit':
                result = value_deposit(bond, settlement, discount)
            elif is_floating:
                result = value_reset_bond(bond, settlement, discount, resets)
            else:
                result = value_bond(bond, settlement, discount)
            interest = sum(f.coupon for f in result.cashflows)
            principal = sum(f.principal for f in result.cashflows)
            if not all(isfinite(v) for v in (result.dirty_pv, result.accrued_interest, result.clean_pv)):
                raise ValueError('Non-finite valuation: check rates and term')
            warnings = []
            for role, curve in [('Discount', discount), ('Projection', projection)]:
                if curve is not None and curve.as_of < settlement:
                    days = (settlement - curve.as_of).days
                    warnings.append(f'{role} curve snapshot is {days} calendar days before settlement; factors normalized to settlement')
            if kind == 'deposit':
                warnings.append('Fixed simple interest; no rollover, capitalization, tax or early withdrawal. Initial funding is excluded; remaining contractual payments only')
            if amortizing:
                warnings.append(('No prepayment' if bond.prepayment_model == 'none' else 'Prospective CPR; payment reduction, contractual maturity retained') + '; no credit losses. ' +
                                ('Current outstanding balance is supplied; annuity instalments recast at future resets; linear principal = outstanding / remaining payments' if is_floating
                                 else 'Fixed-rate scheduled past payments assumed paid'))
                if discount_id != 'FLAT OVERRIDE' and catalog[discount_id].get('curve_type') == 'sovereign':
                    warnings.append('Sovereign benchmark basis, not a loan market quote; optional discount spread is user-supplied')
            if is_floating and index_mode == 'curve':
                warnings.append('Explicit curve-implied reference; not a quoted EURIBOR fixing. Source: ' + projection_source)
            if is_floating and not index:
                warnings.append('Unspecified index accepted for legacy flat input')
            summaries.append({**identity, 'Status': 'OK', 'Type': kind,
                **({'Loan category': loan_type} if kind == 'loan' else {}),
                **({'Position side': bond.side, 'Signed principal': bond.sign*bond.notional,
                    'Interest payment': bond.payout, 'Day count': bond.day_count,
                    'Term days': (maturity-issue).days, 'Remaining days': (maturity-settlement).days,
                    'Annual deposit rate %': bond.coupon*100} if kind == 'deposit' else {}),
                'Rate type': 'floating' if is_floating else 'fixed',
                **({'Discount shocked': hasattr(discount, 'scenario'), 'Projection shocked': hasattr(projection, 'scenario')} if scenario else {}),
                'Index': index, 'Index mode': index_mode if is_floating else '',
                'Index tenor months': tenor or '', 'Index day count': index_basis,
                'Reset interval months': reset_months or '',
                'Next reset': str(resets.next_reset or '') if resets else '',
                'Settlement': str(settlement), 'Maturity': str(maturity),
                'Notional': bond.notional,
                **({'Repayment': bond.repayment, 'Outstanding principal': result.cashflows[0].opening_balance,
                    'Next payment': result.cashflows[0].coupon + result.cashflows[0].principal,
                    'Remaining payments': len(result.cashflows), 'Discount spread bps': bond.discount_spread_bps,
                    'Interest convention': 'Nominal annual / frequency; accrual prorated by elapsed days in period',
                    'Prepayment model': bond.prepayment_model, 'Annual CPR %': bond.cpr,
                    'CPR formula': bond.cpr_formula if bond.prepayment_model == 'formula' else '',
                    'Total prepayment': sum(f.prepayment for f in result.cashflows),
                    'Prepayment treatment': 'Reduce payments; retain contractual maturity',
                    'Fixed rate until': '' if is_floating else str(maturity)} if amortizing else {}),
                'Dirty PV': result.dirty_pv,
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
                rate_source = 'Fixed deposit rate' if kind == 'deposit' else ('Fixed loan rate' if kind == 'loan' else 'Fixed mortgage rate') if amortizing else 'Fixed coupon' if kind == 'fixed' else 'Zero coupon'
                q = resets.at(f.start) if resets else None
                if q:
                    rate_source = q.source
                row_flows.append({**identity, 'Period start': str(f.start),
                    'Payment date': str(f.payment), 'Index': index,
                    'Reference rate %': q.reference * 100 if q else '',
                    'Rate source': rate_source, 'Coupon rate %': f.rate * 100,
                    'Accrual fraction': f.accrual, 'Interest': f.coupon,
                    'Principal': f.principal, 'Total payment': f.coupon + f.principal,
                    'Discount curve': discount_id, 'Discount factor': f.discount_factor,
                    'Present value': f.present_value,
                    'Base payment DF': discount.df(f.payment), 'Settlement DF': discount.df(settlement),
                    'Discount time': year_fraction(settlement,f.payment,'ACT/365F'),
                    **({'Scenario ID': scenario['id'], 'Scenario': scenario['name'],
                        'Applied discount shock bps': shock_bps(scenario, (f.payment-settlement).days/365) if hasattr(discount,'scenario') else 0,
                        'Unshocked payment DF': discount.base.df(f.payment) if hasattr(discount,'base') else discount.df(f.payment)} if scenario else {}),
                    'Spread %': spread * 100 if is_floating else 0,
                    'Reset date': str(q.reset) if q else '',
                    'Index end date': str(q.end) if q else '',
                    'Index accrual fraction': q.accrual if q else '',
                    'Projection start DF': q.start_df if q and q.start_df is not None else '',
                    'Projection end DF': q.end_df if q and q.end_df is not None else '',
                    'Projection curve': projection_id,
                    'Accrued contribution': (f.coupon * (settlement-f.start).days / (f.payment-f.start).days
                                              if amortizing else bond.notional*(bond.sign if kind == 'deposit' else 1)*f.rate*year_fraction(f.start,settlement,basis))
                                             if f.start < settlement < f.payment else 0,
                    'Discount spread bps': bond.discount_spread_bps if amortizing else 0,
                    **({'Position side': bond.side, 'Opening balance': bond.sign*bond.notional,
                        'Closing balance': 0 if f.payment == maturity else bond.sign*bond.notional}
                       if kind == 'deposit' else {}),
                    **({'Opening balance': f.opening_balance, 'Closing balance': f.closing_balance,
                        'Scheduled principal': f.scheduled_principal, 'Prepayment': f.prepayment,
                        'Annual CPR %': f.cpr * 100, 'Period prepayment rate %': f.smm * 100,
                        'Prepayment exposure years': f.prepayment_exposure,
                        'Discount spread bps': bond.discount_spread_bps} if amortizing else {})})
            cashflows.extend(row_flows)
        except (ValueError, TypeError, OverflowError, ZeroDivisionError, KeyError) as error:
            message = str(error)
            if kind == 'loan':
                message = message.replace('Mortgage', 'Loan').replace('mortgage', 'loan')
            errors.append({**identity, 'Error': message})
            summaries.append({**identity, 'Status': 'ERROR', 'Error': message})
    return {'summary': summaries, 'cashflows': cashflows, 'errors': errors, 'inputs': inputs,
            'curves': [item for rows in curve_audit.values() for item in rows]}


def run_json(payload):
    data = json.loads(payload)
    if isinstance(data, dict) and data.get('scenarios') is not None:
        from scenario_engine import run_scenarios
        return json.dumps(run_scenarios(data), allow_nan=False)
    return json.dumps(run_workflow(data), allow_nan=False)


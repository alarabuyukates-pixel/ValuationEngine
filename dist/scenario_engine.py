"""Full revaluation under additive, continuously compounded zero-rate shocks.

Shock tenors are ACT/365F years from each position's settlement. Existing curve
coverage is unchanged; only the shock function holds its endpoint values outside
its tenor knots. Known fixings and contractual spreads are never shocked.
"""
from bisect import bisect_right
from math import exp, isfinite
from bond_engine import year_fraction


def finite(value, label):
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a finite number')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a finite number') from None
    if not isfinite(number):
        raise ValueError(f'{label} must be a finite number')
    return number


def validate_scenarios(records):
    if not isinstance(records, list) or len(records) > 10:
        raise ValueError('Select at most 10 scenarios per run')
    result, ids = [], {'BASE'}
    for r in records:
        if not isinstance(r, dict):
            raise ValueError('Each scenario must be an object')
        sid, name = str(r.get('id') or '').strip(), str(r.get('name') or '').strip()
        if not sid or len(sid) > 80 or sid in ids or not name or len(name) > 100:
            raise ValueError('Scenarios need unique IDs (not BASE) and names of at most 100 characters')
        ids.add(sid)
        role, kind = r.get('role', 'both'), r.get('kind', 'parallel')
        if role not in ('both', 'discount', 'projection'):
            raise ValueError('Shock target must be both, discount or projection')
        targets = r.get('curve_ids', [])
        if not isinstance(targets, list) or any(not isinstance(x, str) or not x.strip() for x in targets):
            raise ValueError('Scenario curve IDs must be a list of nonempty strings')
        item = {'id': sid, 'name': name, 'role': role, 'kind': kind, 'curve_ids': sorted(set(targets))}
        if kind == 'parallel':
            item['bps'] = finite(r.get('bps'), 'Parallel shock bps')
            shocks = [item['bps']]
        elif kind == 'tenor':
            knots = r.get('knots', [])
            if not isinstance(knots, list) or not 2 <= len(knots) <= 50:
                raise ValueError('Tenor shocks need 2 to 50 distinct tenor / bps points')
            points = []
            for k in knots:
                if not isinstance(k, dict):
                    raise ValueError('Each shock point needs years and bps')
                t, b = finite(k.get('years'), 'Tenor years'), finite(k.get('bps'), 'Tenor shock bps')
                if not 0 <= t <= 150:
                    raise ValueError('Shock tenors must be between 0 and 150 years')
                points.append({'years': t, 'bps': b})
            points.sort(key=lambda k: k['years'])
            if len({k['years'] for k in points}) != len(points):
                raise ValueError('Duplicate shock tenors are not allowed')
            item['knots'] = points
            shocks = [k['bps'] for k in points]
        else:
            raise ValueError('Scenario kind must be parallel or tenor')
        if any(abs(b) > 5000 for b in shocks):
            raise ValueError('Supported shock range is -5000 to +5000 bps')
        result.append(item)
    return result


def shock_bps(scenario, years):
    if scenario['kind'] == 'parallel':
        return scenario['bps']
    knots = scenario['knots']
    i = bisect_right([k['years'] for k in knots], years)
    if i == 0:
        return knots[0]['bps']
    if i == len(knots):
        return knots[-1]['bps']
    a, b = knots[i-1], knots[i]
    return a['bps'] + (b['bps']-a['bps']) * (years-a['years']) / (b['years']-a['years'])


class ShockedCurve:
    def __init__(self, base, settlement, scenario):
        self.base, self.settlement, self.scenario = base, settlement, scenario
        self.as_of = base.as_of
        # Preserve the original coverage dates. Interpolate base DFs first, then
        # evaluate the zero shock at the exact queried date (not only at pillars).
        self.pillars = base.pillars

    def df(self, d):
        factor = self.base.df(d)  # coverage check before applying the shock
        t = max(0, (d-self.settlement).days / 365)
        value = factor * exp(-shock_bps(self.scenario, t) / 10000 * t)
        if not isfinite(value) or value <= 0:
            raise ValueError('Shock produces an invalid discount factor')
        return value

    def forward(self, start, end, basis):
        return (self.df(start)/self.df(end)-1)/year_fraction(start, end, basis)


def applies(scenario, role, identifier):
    return scenario and scenario['role'] in ('both', role) and (
        not scenario['curve_ids'] or identifier in scenario['curve_ids'])


def run_scenarios(payload):
    from workflow import run_workflow
    scenarios = validate_scenarios(payload.get('scenarios', []))
    # Stable positional keys also disambiguate duplicate product IDs.
    rows = payload.get('bonds', [])
    keys = [r.get('_row', i+2) for i, r in enumerate(rows) if isinstance(r, dict)]
    if len(set(str(x) for x in keys)) != len(keys):
        raise ValueError('Source row identifiers must be unique within a scenario run')
    catalog = {str(c.get('id')) for c in payload.get('curves', []) if isinstance(c, dict)} | {'FLAT OVERRIDE'}
    for s in scenarios:
        if set(s['curve_ids']) - catalog:
            raise ValueError('Unknown scenario target curve: ' + ', '.join(sorted(set(s['curve_ids']) - catalog)))
    base = run_workflow(payload)
    base['scenario_results'], base['sensitivity'], base['scenario_totals'] = [], [], []
    base['scenario_definitions'] = scenarios
    by_row = {str(r['Source row']): r for r in base['summary']}
    for s in scenarios:
        output = run_workflow(payload, scenario=s)
        output.update({'id': s['id'], 'name': s['name'], 'definition': s})
        base['scenario_results'].append(output)
        matched_base, matched_scenario, matched_clean_base, matched_clean_scenario = [], [], [], []
        for r in output['summary']:
            b = by_row[str(r['Source row'])]
            ok = b['Status'] == r['Status'] == 'OK'
            item = {'Scenario ID': s['id'], 'Scenario': s['name'], 'Source row': r['Source row'],
                    'Bond ID': r['Bond ID'], 'Currency': r['Currency'],
                    'Status': 'OK' if ok else 'ERROR', 'Base status': b['Status'], 'Scenario status': r['Status'],
                    'Base dirty PV': b.get('Dirty PV', ''), 'Scenario dirty PV': r.get('Dirty PV', ''),
                    'Delta PV': r['Dirty PV']-b['Dirty PV'] if ok else '',
                    'Delta PV %': (r['Dirty PV']/b['Dirty PV']-1)*100 if ok and b['Dirty PV'] else '',
                    'Base clean PV': b.get('Clean PV', ''), 'Scenario clean PV': r.get('Clean PV', ''),
                    'Delta clean PV': r['Clean PV']-b['Clean PV'] if ok else '',
                    'Discount shocked': r.get('Discount shocked', False), 'Projection shocked': r.get('Projection shocked', False),
                    'Error': '; '.join(x for x in [b.get('Error', ''), r.get('Error', '')] if x)}
            base['sensitivity'].append(item)
            if ok:
                matched_base.append(b['Dirty PV']); matched_scenario.append(r['Dirty PV'])
                matched_clean_base.append(b['Clean PV']); matched_clean_scenario.append(r['Clean PV'])
        complete = len(matched_base) == len(rows)
        same_date = len({b.get('Settlement') for b in base['summary'] if b['Status'] == 'OK'}) <= 1
        total_ok = complete and same_date
        base['scenario_totals'].append({'Scenario ID': s['id'], 'Scenario': s['name'],
            'Status': 'OK' if total_ok else 'INCOMPLETE' if not complete else 'MIXED DATES',
            'Comparable products': len(matched_base), 'Total products': len(rows),
            'Base dirty PV': sum(matched_base) if total_ok else '',
            'Scenario dirty PV': sum(matched_scenario) if total_ok else '',
            'Delta PV': sum(matched_scenario)-sum(matched_base) if total_ok else '',
            'Delta PV %': (sum(matched_scenario)/sum(matched_base)-1)*100 if total_ok and sum(matched_base) else '',
            'Delta clean PV': sum(matched_clean_scenario)-sum(matched_clean_base) if total_ok else '',
            'Note': 'EUR asset total; common settlement date' if total_ok else 'Total withheld: all products must value at the same settlement date in base and scenario'})
    return base

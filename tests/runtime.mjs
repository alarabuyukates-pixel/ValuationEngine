// Exercises the actual WebAssembly Python distribution used by the browser worker.
import {loadPyodide} from '../dist/vendor/pyodide/pyodide.mjs';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const py = await loadPyodide();
for(const file of ['bond_engine.py', 'deposit_engine.py','mortgage_engine.py','rate_projection.py','scenario_engine.py','workflow.py'])py.FS.writeFile('/home/pyodide/'+file,readFileSync(new URL('../dist/'+file,import.meta.url),'utf8'));
const payload=[{_row:2,bond_id:'FIXED',kind:'fixed',currency:'EUR',issue:'2026-01-01',maturity:'2028-01-01',settlement:'2026-04-01',notional:100000,frequency:2,day_count:'30E/360',coupon:5,discount:4},
{_row:3,bond_id:'FLOAT',kind:'floating',currency:'EUR',issue:'2026-01-01',maturity:'2028-01-01',settlement:'2026-04-01',notional:100000,frequency:2,day_count:'ACT/360',spread:.5,discount:4,projection:3.5,fixing:3},
{_row:4,bond_id:'BAD',kind:'fixed'}];
py.globals.set('payload',JSON.stringify(payload));
const result=JSON.parse(await py.runPythonAsync('from workflow import run_json\nrun_json(payload)'));
assert.equal(result.summary.length,3);
assert.equal(result.errors.length,1);
assert.equal(result.cashflows.length,8);
assert.equal(result.cashflows.at(-1)['Principal'],100000);
assert.equal(result.summary[0]['Status'],'OK');
console.log('WebAssembly Python workflow passed: fixed + floating + invalid row.');
const dated = {id:'EUR-DISC',currency:'EUR',as_of:'2026-01-01',source:'Runtime fixture',compounding:'continuous',day_count:'ACT/365F',pillars:[{date:'2027-01-01',zero_rate:.03},{date:'2028-01-01',zero_rate:.035}]};
const modern={curves:[dated,{...dated,id:'EUR-3M',reference_index:'EURIBOR-3M'}],bonds:[
{_row:2,bond_id:'ZERO',kind:'zero',currency:'EUR',issue:'2026-01-01',maturity:'2028-01-01',settlement:'2026-04-01',notional:100000,discount_curve:'EUR-DISC'},
{_row:3,bond_id:'EURIBOR',kind:'floating',currency:'EUR',issue:'2026-01-01',maturity:'2028-01-01',settlement:'2026-04-01',notional:100000,frequency:4,day_count:'ACT/365F',spread:.5,fixing:3,index:'EURIBOR-3M',discount_curve:'EUR-DISC',projection_curve:'EUR-3M'}]};
py.globals.set('payload',JSON.stringify(modern));
const market=JSON.parse(await py.runPythonAsync('run_json(payload)'));
assert.equal(market.errors.length,0);
assert.equal(market.cashflows.filter(f=>f['Bond ID']==='ZERO').length,1);
assert.equal(market.summary[0]['Accrued interest'],0);
assert.equal(market.summary[1]['Index'],'EURIBOR-3M');
assert.equal(market.curves.length,4);
console.log('WebAssembly market curves, EURIBOR and zero-coupon workflow passed.');

const mortgages={curves:[{...dated,pillars:[{date:'2047-01-01',zero_rate:.03}]}],bonds:[
{_row:2,bond_id:'MORTGAGE-WASM',kind:'mortgage',currency:'EUR',issue:'2026-01-01',maturity:'2046-01-01',settlement:'2026-04-01',notional:300000,frequency:12,coupon:4,repayment:'annuity',discount_curve:'EUR-DISC',discount_spread_bps:50},
{_row:3,bond_id:'UNSUPPORTED',kind:'mortgage',currency:'EUR',issue:'2026-01-01',maturity:'2046-01-01',settlement:'2026-04-01',notional:300000,frequency:12,coupon:4,rate_type:'floating'}]};
py.globals.set('payload',JSON.stringify(mortgages));
const mortgage=JSON.parse(await py.runPythonAsync('run_json(payload)'));
assert.equal(mortgage.summary[0].Status,'OK');
assert.equal(mortgage.errors.length,1);
assert.equal(mortgage.cashflows.length,237);
assert.equal(mortgage.cashflows.at(-1)['Closing balance'],0);
assert.ok(mortgage.summary[0]['Outstanding principal']<300000);
assert.ok(Math.abs(mortgage.cashflows.reduce((s,f)=>s+f.Principal,0)-mortgage.summary[0]['Outstanding principal'])<1e-6);
console.log('WebAssembly mortgage workflow passed: balances, spread, settlement and unsupported reset.');

// Existing deposits: irregular maturity, periodic payments and explicit bank signs.
py.globals.set('payload',JSON.stringify({bonds:[
{_row:10,bond_id:'TD-32D',kind:'deposit',issue:'2026-01-01',settlement:'2026-01-15',maturity:'2026-02-02',notional:100000,coupon:4,day_count:'ACT/365F',discount:3},
{_row:11,bond_id:'TD-MONTHLY',kind:'deposit',issue:'2026-01-01',settlement:'2026-01-15',maturity:'2027-01-01',notional:100000,coupon:4,day_count:'ACT/365F',discount:3,deposit_payout:'periodic',frequency:12}],curves:[]}));
const deposits=JSON.parse(await py.runPythonAsync('run_json(payload)'));
assert.equal(deposits.errors.length,0);
assert.ok(deposits.summary.every(s=>s['Dirty PV']<0&&s['Total principal']===-100000));
assert.equal(deposits.cashflows.filter(f=>f['Source row']===10).length,1);
assert.equal(deposits.cashflows.filter(f=>f['Source row']===11).length,12);
assert.ok(Math.abs(deposits.cashflows[0].Interest+100000*.04*32/365)<1e-8);
console.log('WebAssembly time deposits passed: 32-day maturity, monthly interest, accrued and bank liability signs.');

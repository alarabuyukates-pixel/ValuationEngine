// Exercises the actual WebAssembly Python distribution used by the browser worker.
import {loadPyodide} from '../dist/vendor/pyodide/pyodide.mjs';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const py = await loadPyodide();
for(const file of ['bond_engine.py','workflow.py'])py.FS.writeFile('/home/pyodide/'+file,readFileSync(new URL('../dist/'+file,import.meta.url),'utf8'));
const payload=[{_row:2,bond_id:'FIXED',kind:'fixed',currency:'EUR',issue:'2026-01-01',maturity:'2028-01-01',settlement:'2026-04-01',notional:100000,frequency:2,day_count:'30E/360',coupon:5,discount:4},
{_row:3,bond_id:'FLOAT',kind:'floating',currency:'USD',issue:'2026-01-01',maturity:'2028-01-01',settlement:'2026-04-01',notional:100000,frequency:2,day_count:'ACT/360',spread:.5,discount:4,projection:3.5,fixing:3},
{_row:4,bond_id:'BAD',kind:'fixed'}];
py.globals.set('payload',JSON.stringify(payload));
const result=JSON.parse(await py.runPythonAsync('from workflow import run_json\nrun_json(payload)'));
assert.equal(result.summary.length,3);
assert.equal(result.errors.length,1);
assert.equal(result.cashflows.length,8);
assert.equal(result.cashflows.at(-1)['Principal'],100000);
assert.equal(result.summary[0]['Status'],'OK');
console.log('WebAssembly Python workflow passed: fixed + floating + invalid row.');

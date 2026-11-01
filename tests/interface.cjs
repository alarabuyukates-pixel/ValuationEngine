const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
(async()=>{
 const {loadPyodide}=await import(path.join(root,'dist/vendor/pyodide/pyodide.mjs'));
 const py=await loadPyodide();
 for(const f of ['bond_engine.py','mortgage_engine.py','rate_projection.py','workflow.py'])py.FS.writeFile('/home/pyodide/'+f,fs.readFileSync(path.join(root,'dist',f),'utf8'));
 await py.runPythonAsync('from workflow import run_json');
 const dom=new JSDOM(fs.readFileSync(path.join(root,'dist/index.html'),'utf8'),{url:'http://localhost/',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,$=id=>d.getElementById(id);
 w.scrollTo=()=>{};w.structuredClone=structuredClone;
 w.fetch=async()=>({ok:true,json:async()=>JSON.parse(fs.readFileSync(path.join(root,'dist/market-data.json'),'utf8'))});
 w.Worker=class{postMessage(data){Promise.resolve().then(async()=>{py.globals.set('input_json',JSON.stringify(data.rows));const result=JSON.parse(await py.runPythonAsync('run_json(input_json)'));this.onmessage({data:{id:data.id,result}})}).catch(e=>this.onerror(e))}terminate(){}};
 for(const f of ['vendor/xlsx.full.min.js','charts.js','curves.js','audit.js','app.js'])w.eval(fs.readFileSync(path.join(root,'dist',f),'utf8')+(f==='app.js'?'\nwindow.testLoad=(wb)=>{workbook=wb;filename="test.xlsx";loadWorkbook();};window.testNormalize=normalizeRows;':''));
 await w.CurveManager.ready;assert.equal(d.querySelectorAll('#curve-chart circle').length,12);
 $('sample-btn').click();$('apply-import').click();assert.equal($('portfolio-count').textContent,'5');assert.equal($('portfolio-view').hidden,false);
 await $('run-btn').onclick();assert.equal($('metric-valued').textContent,'5',$('notice').textContent);assert.equal($('metric-errors').textContent,'0');
 let saved;w.XLSX.writeFile=(wb,name)=>saved={wb,name};$('export-btn').click();const captured=saved.wb;const flows=w.XLSX.utils.sheet_to_json(captured.Sheets['Cash flows']);assert.ok(flows.length>450);const mtg=flows.filter(f=>f['Bond ID']==='MORTGAGE-FLOAT');assert.equal(mtg.at(-1)['Closing balance'],0);assert.equal(new Set(mtg.slice(0,2).map(f=>f['Reset date'])).size,1);
 $('validation-select').value='5';$('validation-export').click();assert.equal(saved.wb.Sheets.Checks.C2.f,"SUM('Cash flow audit'!M2:M231)");assert.ok(saved.wb.Sheets['Reset audit'].H3.f.includes('/'));assert.equal(w.XLSX.utils.sheet_to_json(saved.wb.Sheets.Inputs).length,1);assert.equal(w.XLSX.utils.sheet_to_json(saved.wb.Sheets.Summary)[0]['Bond ID'],'MORTGAGE-FLOAT');
 assert.ok(w.XLSX.utils.sheet_to_json(saved.wb.Sheets.Checks).every(r=>r.Result==='PASS'));
 const fixture={summary:w.XLSX.utils.sheet_to_json(captured.Sheets.Summary),cashflows:flows,errors:[],curves:[],sourceInputs:[{_row:5}],runInfo:{}};fixture.cashflows=structuredClone(flows);fixture.cashflows.find(r=>r['Source row']===5).Interest+=10;const tampered=w.ValuationExport.validation(fixture,5);assert.equal(tampered.Sheets.Checks.E6.v,'FAIL');
 const bytes=w.XLSX.write(saved.wb,{type:'buffer',bookType:'xlsx'}),round=w.XLSX.read(bytes,{type:'buffer',cellFormula:true});assert.equal(round.Sheets.Checks.E2.f,'IF(ABS(D2)<=0.01,"PASS","FAIL")');
 $('download-inputs').click();const inputs=saved.wb;w.testLoad(inputs);$('apply-import').click();await $('run-btn').onclick();assert.equal($('metric-valued').textContent,'5',$('notice').textContent);
 $('portfolio-select').value='10';$('edit-position').click();$('edit-current_payment').value='';$('position-form').onsubmit({preventDefault(){}});assert.equal($('export-btn').disabled,true);await $('run-btn').onclick();assert.equal($('metric-valued').textContent,'4');assert.equal($('metric-errors').textContent,'1');$('validation-select').value='10';$('validation-export').click();assert.ok(saved.wb.Sheets.Errors);assert.equal(saved.wb.Sheets.Checks,undefined);
 // Manual common form adds a fixed bond and removes it without stale results.
 $('add-position').click();$('position-form').onsubmit({preventDefault(){}});assert.equal($('portfolio-count').textContent,'6');$('remove-position').click();assert.equal($('portfolio-count').textContent,'5');assert.equal($('export-btn').disabled,true);
 // Native Excel percent cells and 1904 dates are normalized, not double-scaled.
 const ws=w.XLSX.utils.aoa_to_sheet([['Bond ID','Type','Issue date','Maturity date','Settlement date','Notional','Coupon %','Discount rate %'],['PERCENT','fixed',45000,46000,45100,100000,0.04,0.035]]);ws.G2.z='0.00%';ws.H2.z='0.00%';const wb=w.XLSX.utils.book_new();w.XLSX.utils.book_append_sheet(wb,ws,'Positions');wb.Workbook={WBProps:{date1904:true}};w.testLoad(wb);const normalized=w.testNormalize();assert.equal(normalized[0].coupon,4);assert.equal(normalized[0].discount,3.5000000000000004);assert.equal(normalized[0].issue,'2027-03-16');
 console.log('UI integration passed: unified import, 5-product valuation, reset audit formulas, isolated errors, round-trip Excel, editing, deletion, percentage/date normalization.');dom.window.close();
})().catch(e=>{console.error(e);process.exit(1)});

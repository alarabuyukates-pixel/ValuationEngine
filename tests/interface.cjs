const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
(async()=>{
 const {loadPyodide}=await import(path.join(root,'dist/vendor/pyodide/pyodide.mjs'));
 const py=await loadPyodide();
 for(const f of ['bond_engine.py','mortgage_engine.py','rate_projection.py','scenario_engine.py','workflow.py'])py.FS.writeFile('/home/pyodide/'+f,fs.readFileSync(path.join(root,'dist',f),'utf8'));
 await py.runPythonAsync('from workflow import run_json');
 const dom=new JSDOM(fs.readFileSync(path.join(root,'dist/index.html'),'utf8'),{url:'http://localhost/',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,$=id=>d.getElementById(id);
 w.scrollTo=()=>{};w.structuredClone=structuredClone;
 w.fetch=async()=>({ok:true,json:async()=>JSON.parse(fs.readFileSync(path.join(root,'dist/market-data.json'),'utf8'))});
 w.Worker=class{postMessage(data){Promise.resolve().then(async()=>{py.globals.set('input_json',JSON.stringify(data.rows));const result=JSON.parse(await py.runPythonAsync('run_json(input_json)'));this.onmessage({data:{id:data.id,result}})}).catch(e=>this.onerror(e))}terminate(){}};
 for(const f of ['vendor/xlsx.full.min.js','charts.js','curves.js','scenarios.js','audit.js','app.js'])w.eval(fs.readFileSync(path.join(root,'dist',f),'utf8')+(f==='app.js'?'\nwindow.testLoad=(wb)=>{workbook=wb;filename="test.xlsx";loadWorkbook();};window.testNormalize=normalizeRows;':''));
 await w.CurveManager.ready;assert.equal(d.querySelectorAll('#curve-chart circle').length,12);
 $('sample-btn').click();assert.equal($('fallback-index_mode').value,'named');$('apply-import').click();assert.equal($('portfolio-count').textContent,'5');assert.equal($('portfolio-view').hidden,false);
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

 // Multiple independently shocked runs and per-scenario audit exports.
 $('sample-btn').click();$('apply-import').click();
 d.querySelector('[data-shock="100"]').click();d.querySelector('[data-shock="-100"]').click();
 $('scenario-name').value='Twist';$('scenario-kind').value='tenor';$('scenario-kind').onchange();$('scenario-knots').value='0, 0\n2, 25\n5, 100\n30, 50';$('scenario-role').value='projection';$('scenario-form').onsubmit({preventDefault(){}});
 assert.equal(w.ScenarioManager.selected().length,3);await $('run-btn').onclick();
 assert.match($('run-caption').textContent,/base \+ 3 scenarios/);assert.equal($('result-scenario').options.length,4);assert.equal($('metric-errors').textContent,'0',$('notice').textContent);
 $('export-btn').click();const scenarioExport=saved.wb;const sensitivity=w.XLSX.utils.sheet_to_json(scenarioExport.Sheets.Sensitivity);assert.equal(sensitivity.length,15);
 const fixedUp=sensitivity.find(x=>x.Scenario==='+100 bps'&&x['Bond ID']==='BOND-FIXED');const fixedDown=sensitivity.find(x=>x.Scenario==='-100 bps'&&x['Bond ID']==='BOND-FIXED');assert.ok(fixedUp['Delta PV']<0);assert.ok(fixedDown['Delta PV']>0);
 assert.equal(sensitivity.find(x=>x.Scenario==='Twist'&&x['Bond ID']==='BOND-FIXED')['Delta PV'],0);
 const scenarioFlows=w.XLSX.utils.sheet_to_json(scenarioExport.Sheets['Scenario cash flows']);assert.equal(new Set(scenarioFlows.map(x=>x.Scenario)).size,3);
 $('result-scenario').value=w.ScenarioManager.selected()[0].id;$('result-scenario').onchange();$('validation-select').value=String(w.XLSX.utils.sheet_to_json(scenarioExport.Sheets.Summary).find(x=>x['Bond ID']==='MORTGAGE-FLOAT')['Source row']);$('validation-export').click();assert.ok(w.XLSX.utils.sheet_to_json(saved.wb.Sheets.Checks).every(x=>x.Result==='PASS'));assert.equal(w.XLSX.utils.sheet_to_json(saved.wb.Sheets['Run info']).find(x=>x.Setting==='Scenario').Value,'+100 bps');
 const box=$('run-scenarios').querySelector('input');box.checked=false;box.onchange();assert.equal($('export-btn').disabled,true);assert.equal(w.ScenarioManager.selected().length,2);
 $('scenario-name').value='Bad';$('scenario-kind').value='tenor';$('scenario-knots').value='1, 50\n1, 100';$('scenario-form').onsubmit({preventDefault(){}});assert.match($('scenario-notice').textContent,/unique/);assert.equal(w.ScenarioManager.all().length,3);
 // Manual CPR, formula editing, workbook persistence and prepayment arithmetic audits.
 $('sample-btn').click();$('apply-import').click();
 const mortgageOption=[...$('portfolio-select').options].find(o=>o.textContent.startsWith('MORTGAGE-FIXED'));
 $('portfolio-select').value=mortgageOption.value;$('edit-position').click();
 assert.equal($('edit-prepayment_model').value,'none');assert.equal($('field-cpr').hidden,true);
 $('edit-prepayment_model').value='cpr';$('edit-prepayment_model').onchange();
 assert.equal($('field-cpr').hidden,false);assert.equal($('field-cpr_formula').hidden,true);
 $('edit-cpr').value='6';$('position-form').onsubmit({preventDefault(){}});
 await $('run-btn').onclick();assert.equal($('metric-errors').textContent,'0',$('notice').textContent);
 $('validation-select').value=mortgageOption.value;$('validation-export').click();
 const cprSummary=w.XLSX.utils.sheet_to_json(saved.wb.Sheets.Summary)[0];assert.ok(cprSummary['Total prepayment']>0);
 assert.ok(w.XLSX.utils.sheet_to_json(saved.wb.Sheets['Prepayment checks']).every(x=>x.Result==='PASS'));
 assert.ok(saved.wb.Sheets['Prepayment audit'].G2.f.includes('^E2'));
 const preRound=w.XLSX.read(w.XLSX.write(saved.wb,{type:'buffer',bookType:'xlsx'}),{type:'buffer',cellFormula:true});
 assert.ok(preRound.Sheets['Prepayment checks'].B2.f.includes('SUMPRODUCT'));
 $('edit-position').click();$('edit-prepayment_model').value='formula';$('edit-prepayment_model').onchange();
 assert.equal($('field-cpr_formula').hidden,false);assert.equal($('prepayment-guidance').hidden,false);
 $('edit-cpr_formula').value='base_cpr_pct';$('position-form').onsubmit({preventDefault(){}});assert.equal($('export-btn').disabled,true);
 await $('run-btn').onclick();$('validation-select').value=mortgageOption.value;$('validation-export').click();
 assert.equal(w.XLSX.utils.sheet_to_json(saved.wb.Sheets.Summary)[0]['Dirty PV'],cprSummary['Dirty PV']);
 $('download-inputs').click();w.testLoad(saved.wb);assert.equal(w.testNormalize().find(r=>r.bond_id==='MORTGAGE-FIXED').cpr_formula,'base_cpr_pct');
 $('apply-import').click();await $('run-btn').onclick();assert.equal($('metric-errors').textContent,'0',$('notice').textContent);
 $('export-btn').click();const preCaptured=saved.wb;const preFlows=w.XLSX.utils.sheet_to_json(preCaptured.Sheets['Cash flows']);
 const preSummaries=w.XLSX.utils.sheet_to_json(preCaptured.Sheets.Summary),preSummary=preSummaries.find(x=>x['Bond ID']==='MORTGAGE-FIXED');
 const badPre=structuredClone(preFlows);badPre.find(x=>x['Source row']===preSummary['Source row']).Prepayment+=20;
 const badWorkbook=w.ValuationExport.validation({summary:preSummaries,cashflows:badPre,errors:[],curves:[],sourceInputs:[],runInfo:{}},preSummary['Source row']);
 assert.equal(badWorkbook.Sheets['Prepayment checks'].C2.v,'FAIL');
 const editOption=[...$('portfolio-select').options].find(o=>o.textContent.startsWith('MORTGAGE-FIXED'));$('portfolio-select').value=editOption.value;$('edit-position').click();
 $('edit-cpr_formula').value='balance.__class__';$('position-form').onsubmit({preventDefault(){}});await $('run-btn').onclick();assert.equal($('metric-errors').textContent,'1');
 $('validation-select').value=editOption.value;$('validation-export').click();assert.ok(saved.wb.Sheets.Errors);
 // Native Excel percentage CPR is normalized to annual percentage points.
 const cprSheet=w.XLSX.utils.aoa_to_sheet([['Type','Annual CPR (%)'],['mortgage',.06]]);cprSheet.B2.z='0.00%';
 const cprBook=w.XLSX.utils.book_new();w.XLSX.utils.book_append_sheet(cprBook,cprSheet,'Positions');w.testLoad(cprBook);assert.equal(w.testNormalize()[0].cpr,6);
 console.log('Prepayment integration passed: manual CPR/formula, real Pyodide repricing, Excel round trips, CPR audit formulas, tamper detection, row errors and percentage normalization.');
 console.log('Scenario integration passed: multi-select, parallel/twist, independent repricing, scenario Excel audit, stale results and invalid definitions.');
 console.log('UI integration passed: unified import, 5-product valuation, reset audit formulas, isolated errors, round-trip Excel, editing, deletion, percentage/date normalization.');dom.window.close();
})().catch(e=>{console.error(e);process.exit(1)});

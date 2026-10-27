'use strict';
const $ = id => document.getElementById(id);
const fields = [
  ['bond_id','Bond ID','Optional · generated if blank',['bondid','isin','id','securityid','security','name'],''],
  ['kind','Bond type','fixed or floating',['type','kind','bondtype','ratetype'],'fixed'],
  ['currency','Currency','Optional · reporting only, no FX conversion',['currency','ccy'],'Unspecified'],
  ['issue','Issue date','Required · YYYY-MM-DD',['issuedate','issue','dateddate','startdate'],''],
  ['maturity','Maturity date','Required · YYYY-MM-DD',['maturitydate','maturity','enddate'],''],
  ['settlement','Settlement date','Required · YYYY-MM-DD',['settlementdate','settlement','valuationdate','asofdate'],''],
  ['notional','Principal / notional','Required · currency amount',['notional','principal','facevalue','faceamount','nominal'],''],
  ['frequency','Payments per year','1, 2, 3, 4, 6 or 12',['paymentsperyear','frequency','couponfrequency'],'2'],
  ['day_count','Coupon day count','ACT/360, ACT/365F or 30E/360',['daycount','coupondaycount','daycountconvention'],'ACT/360'],
  ['coupon','Fixed coupon (%)','Required for fixed bonds',['coupon','couponrate','fixedcoupon','annualcouponrate'],''],
  ['spread','Floating spread (%)','0.5 = 50 basis points',['spread','margin','floatingspread'],'0'],
  ['discount','Discount zero rate (%)','Required · continuously compounded',['discountrate','discountzerorate','discount','zerorate'],''],
  ['projection','Projection zero rate (%)','Required for floating bonds',['projectionrate','projectionzerorate','projection','forwardcurverate'],''],
  ['fixing','Current reference fixing (%)','Required for floating · excluding spread',['currentfixing','fixing','referencefixing','referencerate','currentreferencerate'],'']
];
const rateKeys = new Set(['coupon','spread','discount','projection','fixing']);
const dateKeys = new Set(['issue','maturity','settlement']);
let workbook, grid=[], columns=[], sourceRows=[], filename='', results, runInputs=[], resultView='summary';
let worker, messageId=0;
const pending = new Map();
const normalize = value => String(value??'').toLowerCase().replace(/[^a-z0-9]/g,'');
const fmt = (value, digits=2) => Number(value).toLocaleString(undefined,{minimumFractionDigits:digits,maximumFractionDigits:digits});
const text = (element, value) => { element.textContent = value; return element; };
function notify(message,error=false) { $('notice').hidden=false; $('notice').className=error?'error':''; $('notice').textContent=message; }
function progress(stage) { for(let i=1;i<=4;i++) $('step-'+i).className='step'+(i===stage?' current':i<stage?' done':''); }
function showView(name) {
  for(const key of ['studio','workflow','method']) $(key+'-view').hidden=key!==name;
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.view===name));
  $('page-label').textContent={studio:'Valuation studio',workflow:'The workflow',method:'Method & code'}[name];
  window.scrollTo({top:0,behavior:'smooth'});
}
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>showView(el.dataset.view)));
const nodes=[['Import','Choose a workbook, worksheet and header row.','app.js / readFile'],['Map','Connect source columns and explicit fallbacks.','app.js / normalizeRows'],['Validate','Check dates, rates, fixings and conventions.','workflow.py'],['Calculate','Generate and discount each future payment.','bond_engine.py'],['Export','Review results and download an Excel workbook.','app.js / exportResults']];
for(const [i,node] of nodes.entries()) { const div=document.createElement('div'); div.className='flow-node'; const n=document.createElement('b'); n.textContent=String(i+1).padStart(2,'0'); const title=document.createElement('strong');title.textContent=node[0];const p=document.createElement('p');p.textContent=node[1];const code=document.createElement('code');code.textContent=node[2];div.append(n,title,p,code);$('workflow-diagram').append(div); }

function makeWorkbook(rows,name='Bonds') { const wb=XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet(rows),name); return wb; }
function sampleRows() { return [
 ['Bond ID','Type','Currency','Issue date','Maturity date','Settlement date','Notional','Payments per year','Day count','Coupon %','Spread %','Discount rate %','Projection rate %','Current fixing %'],
 ['CORP-2029','fixed','EUR','2025-01-15','2029-01-15','2026-10-27',100000,2,'30E/360',4.75,'',3.8,'',''],
 ['FRN-2030','floating','EUR','2025-04-01','2030-04-01','2026-10-27',150000,4,'ACT/360','',0.75,3.8,3.25,3.4],
 ['TREAS-2031','fixed','USD','2026-03-31','2031-03-31','2026-10-27',200000,2,'ACT/365F',4.25,'',4.1,'',''] ]; }
$('template-btn').onclick=()=> {
 try { const wb=makeWorkbook(sampleRows());const notes=[['Instructions'],['Replace the example bonds with your own. One bond per row.'],['Enter 5 for 5% (or use an Excel percentage cell containing 5%).'],['Text dates must be YYYY-MM-DD. Native Excel dates are also supported.'],['Floating fixing is the current reference rate excluding spread.'],['Discount and projection rates are flat continuously compounded annual zero rates.'],['Fixed and simple term-floating bullet bonds only. No business-day adjustments.']];XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet(notes),'Instructions');XLSX.writeFile(wb,'bondflow-input-template.xlsx'); } catch(e) {notify(e.message,true);} };
$('sample-btn').onclick=()=>{workbook=makeWorkbook(sampleRows());filename='sample-portfolio.xlsx';loadWorkbook();notify('Sample portfolio loaded. Review the mappings, then click Run valuation.');};
async function readFile(file) {
 if(!file)return;
 if(!/\.(xlsx|xls|csv)$/i.test(file.name)) {notify('Choose an .xlsx, .xls or .csv file.',true);return;}
 if(file.size>10*1024*1024) {notify('Please choose a workbook under 10 MB.',true);return;}
 try {notify('Reading workbook…');const bytes=await file.arrayBuffer();workbook=XLSX.read(bytes,{type:'array',cellDates:false,cellNF:true,dense:false});filename=file.name;loadWorkbook();notify('Workbook loaded. Check the worksheet, header row and column mappings.');}
 catch(e) {notify('Could not open this workbook. Make sure it is not password protected. '+e.message,true);}
}
$('file-input').onchange=e=>readFile(e.target.files[0]);
for(const name of ['dragenter','dragover']) $('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.add('dragover');});
for(const name of ['dragleave','drop']) $('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.remove('dragover');});
$('dropzone').addEventListener('drop',e=>readFile(e.dataTransfer.files[0]));
function loadWorkbook() {
 if(!workbook.SheetNames.length)throw new Error('No worksheets found.');
 $('sheet-select').replaceChildren();
 for(const name of workbook.SheetNames) {const option=new Option(name,name);$('sheet-select').add(option);}
 $('header-row').value='1';$('upload-layout').hidden=true;$('mapping-card').hidden=false;$('results-section').hidden=true;results=null;
 $('filename').textContent=filename;progress(2);readSheet();
}
$('sheet-select').onchange=()=>safeReadSheet();$('header-row').onchange=()=>safeReadSheet();
function safeReadSheet(){try{readSheet();}catch(e){sourceRows=[];notify(e.message,true);$('run-btn').disabled=true;}}
function readSheet() {
 const sheet=workbook.Sheets[$('sheet-select').value];
 const header=Number($('header-row').value);
 if(!Number.isInteger(header)||header<1||header>100)throw new Error('Header row must be between 1 and 100.');
 const range=sheet['!ref']?XLSX.utils.decode_range(sheet['!ref']):null;
 if(!range || range.e.r>100000 || range.e.c>500)throw new Error('Worksheet is empty or too large. Use at most 100,000 rows and 501 columns.');
 // Absolute coordinates preserve correct source row numbers even if used range starts later.
 grid=XLSX.utils.sheet_to_json(sheet,{header:1,raw:true,defval:null,blankrows:true,range:{s:{r:0,c:0},e:range.e}});
 columns=(grid[header-1]||[]).map((value,index)=>({index,label:String(value??'').trim()||'Column '+XLSX.utils.encode_col(index)}));
 sourceRows=[];
 for(let r=header;r<grid.length;r++)if(grid[r].some(v=>v!==null&&String(v).trim()!==''))sourceRows.push({values:grid[r],row:r+1});
 if(sourceRows.length>5000)throw new Error('Maximum 5,000 bonds per run. Choose a smaller worksheet.');
 $('row-count').textContent=sourceRows.length+' bonds found';
 $('mapping-fields').replaceChildren();
 for(const [key,label,hint,aliases,fallback]of fields){
  const row=document.createElement('div');row.className='mapping-row';
  const lab=document.createElement('label');lab.htmlFor='map-'+key;lab.textContent=label;
  const small=document.createElement('small');small.textContent=hint;lab.append(small);
  const select=document.createElement('select');select.id='map-'+key;select.setAttribute('aria-label',label+' source column');select.add(new Option('— Use fallback —',''));
  for(const column of columns)select.add(new Option(column.label+' ('+XLSX.utils.encode_col(column.index)+')',String(column.index)));
  const match=columns.find(c=>aliases.includes(normalize(c.label)));
  if(match)select.value=String(match.index);
  const input=document.createElement('input');input.id='fallback-'+key;input.value=fallback;input.placeholder=dateKeys.has(key)?'YYYY-MM-DD':'No fallback';input.setAttribute('aria-label',label+' fallback');
  row.append(lab,select,input);$('mapping-fields').append(row);
 }
 const preview=sourceRows.slice(0,5).map(r=>Object.fromEntries(columns.map(c=>[c.label+' ('+XLSX.utils.encode_col(c.index)+')',r.values[c.index]])));
 renderTable($('source-preview'),preview);
 $('mapping-status').textContent=sourceRows.length?'Review mappings before running':'No data rows found';
 $('run-btn').disabled=!sourceRows.length;results=null;$('results-section').hidden=true;progress(2);
}
function normalizeRows(){
 const sheet=workbook.Sheets[$('sheet-select').value];
 const date1904=!!workbook.Workbook?.WBProps?.date1904;
 return sourceRows.map(({values,row})=>{
  const record={_row:row};
  for(const [key]of fields){
   const selected=$('map-'+key).value;
   let value=selected!==''?values[Number(selected)]:null;
   const fromCell=selected!==''&&value!==null&&value!==undefined&&String(value).trim()!=='';
   if(!fromCell)value=$('fallback-'+key).value.trim();
   if(dateKeys.has(key)&&value instanceof Date) value=value.toISOString().slice(0,10);
   if(dateKeys.has(key)&&typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) value=value.slice(0,10);
   if(dateKeys.has(key)&&typeof value==='number'){
    const parsed=XLSX.SSF.parse_date_code(value,{date1904});
    value=parsed?`${String(parsed.y).padStart(4,'0')}-${String(parsed.m).padStart(2,'0')}-${String(parsed.d).padStart(2,'0')}`:String(value);
   }
   if(rateKeys.has(key)&&value!==''&&value!==null&&value!==undefined){
    const cell=fromCell?sheet[XLSX.utils.encode_cell({r:row-1,c:Number(selected)})]:null;
    if(typeof value==='number'&&cell?.z&&/%/.test(cell.z))value*=100;
    else if(typeof value==='string'&&value.includes('%'))value=value.replace('%','').trim();
    else if(fromCell&&$('rate-unit').value==='decimal'&&Number.isFinite(Number(value)))value=Number(value)*100;
   }
   record[key]=value??'';
  }
  return record;
 });
}
function pythonRequest(rows){
 if(!worker){worker=new Worker('worker.js');worker.onmessage=({data})=>{const request=pending.get(data.id);if(!request)return;clearTimeout(request.timer);pending.delete(data.id);data.error?request.reject(new Error(data.error)):request.resolve(data.result);};worker.onerror=e=>{for(const request of pending.values()){clearTimeout(request.timer);request.reject(new Error(e.message||'Python worker could not start'));}pending.clear();worker.terminate();worker=null;};}
 return new Promise((resolve,reject)=>{const id=++messageId;const timer=setTimeout(()=>{worker?.terminate();worker=null;pending.delete(id);reject(new Error('Python startup or valuation timed out. Check your connection and try again.'));},120000);pending.set(id,{resolve,reject,timer});worker.postMessage({id,type:'run',rows});});
}
$('run-btn').onclick=async()=>{
 const controls=[...$('mapping-card').querySelectorAll('button,input,select')];
 try{
  runInputs=normalizeRows();
  controls.forEach(c=>c.disabled=true);progress(3);$('run-btn').textContent='Calculating…';
  notify('Starting the Python engine and calculating cash flows. The first run may take a moment.');$('runtime-status').textContent='Python engine running locally…';
  const started=performance.now();results=await pythonRequest(runInputs);
  results.runInfo={timestamp:new Date().toISOString(),file:filename,sheet:$('sheet-select').value,header:Number($('header-row').value),seconds:((performance.now()-started)/1000).toFixed(2),rateUnit:$('rate-unit').selectedOptions[0].textContent};
  $('mapping-card').hidden=true;$('results-section').hidden=false;progress(4);
  const ok=results.summary.filter(r=>r.Status==='OK').length;
  $('metric-valued').textContent=ok;$('metric-flows').textContent=results.cashflows.length.toLocaleString();$('metric-errors').textContent=results.errors.length;
  $('run-caption').textContent=`${filename} · ${results.summary.length} bonds · ${results.runInfo.seconds}s · ${new Date(results.runInfo.timestamp).toLocaleString()}`;
  $('bond-filter').replaceChildren(new Option('All bonds',''));
  for(const row of results.summary.filter(r=>r.Status==='OK'))$('bond-filter').add(new Option(`${row['Bond ID']} · row ${row['Source row']}`,String(row['Source row'])));
  resultView='summary';showResults();notify(`${ok} bonds valued. ${results.errors.length} rows need review. Your Excel export includes all results and errors.`,!!results.errors.length);
  $('runtime-status').textContent='Python engine ready · data stays local';
 }catch(e){notify('Valuation could not complete: '+e.message,true);progress(2);$('runtime-status').textContent='Python engine stopped · retry available';}
 finally{controls.forEach(c=>c.disabled=false);$('run-btn').innerHTML='Run valuation <span>→</span>';}
};
function renderTable(container,rows,headers){
 container.replaceChildren();
 if(!rows.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No rows to display.';container.append(empty);return;}
 headers=headers||[...new Set(rows.flatMap(r=>Object.keys(r)))];
 const table=document.createElement('table'),thead=document.createElement('thead'),tr=document.createElement('tr');
 for(const h of headers){const th=document.createElement('th');th.textContent=h;tr.append(th);}thead.append(tr);table.append(thead);
 const tbody=document.createElement('tbody');
 for(const row of rows.slice(0,500)){
  const tr=document.createElement('tr');
  for(const h of headers){const td=document.createElement('td');const value=row[h];
   if(typeof value==='number'){td.className='num';const digits=h==='Discount factor'||h==='Accrual fraction'?8:h.includes('rate')||h.includes('/ 100')?4:h==='Source row'?0:2;td.textContent=fmt(value,digits);}
   else if(h==='Status'){const badge=document.createElement('span');badge.className=value==='OK'?'status-ok':'status-error';badge.textContent=value||'';td.append(badge);}
   else td.textContent=value===null||value===undefined?'—':String(value);
   tr.append(td);
  }tbody.append(tr);
 }table.append(tbody);container.append(table);
}
function showResults(){
 document.querySelectorAll('[data-result]').forEach(b=>b.classList.toggle('selected',b.dataset.result===resultView));
 $('bond-filter-label').hidden=resultView!=='cashflows';
 let rows=results[resultView];
 if(resultView==='cashflows'&&$('bond-filter').value)rows=rows.filter(r=>String(r['Source row'])===$('bond-filter').value);
 const headers=resultView==='summary'?['Source row','Bond ID','Currency','Status','Type','Dirty PV','Accrued interest','Clean PV','Clean price / 100','Error']:undefined;
 renderTable($('result-table'),rows,headers);
 $('result-footer').textContent=`Showing ${Math.min(rows.length,500)} of ${rows.length.toLocaleString()} rows. Excel download includes every row and full precision. Amounts are in each bond’s currency; no cross-currency total is calculated.`;
}
document.querySelectorAll('[data-result]').forEach(b=>b.onclick=()=>{resultView=b.dataset.result;showResults();});$('bond-filter').onchange=showResults;
function exportResults(){
 if(!results)return;
 const wb=XLSX.utils.book_new();
 const info=[{Setting:'Run time (UTC)',Value:results.runInfo.timestamp},{Setting:'Input file',Value:results.runInfo.file},{Setting:'Worksheet',Value:results.runInfo.sheet},{Setting:'Header row',Value:results.runInfo.header},{Setting:'Numeric input rates',Value:results.runInfo.rateUnit},{Setting:'Normalized input rates',Value:'Percentage points: 5 = 5%'},{Setting:'Valuation convention',Value:'Settlement PV; payments on settlement excluded'},{Setting:'Discount curve',Value:'Flat continuous annual zero rate, ACT/365F'},{Setting:'Floating coupons',Value:'Current fixing + spread; future simple forwards + spread'},{Setting:'Schedule',Value:'Backward from maturity; month-end preserved; short first stub; no holiday adjustments'},{Setting:'Model scope',Value:'Fixed and simple term-floating bullet bonds. No overnight compounding or embedded options.'},{Setting:'Workflow version',Value:'1.0.0'},{Setting:'Source code',Value:'https://github.com/alarabuyukates-pixel/ValuationEngine'}];
 for(const [name,rows]of [['Valuations',results.summary],['Cash Flows',results.cashflows],['Inputs',results.inputs],['Errors',results.errors.length?results.errors:[{Status:'No errors'}]],['Run Info',info]]){
  const ws=XLSX.utils.json_to_sheet(rows);
  if(ws['!ref']){const range=XLSX.utils.decode_range(ws['!ref']);ws['!autofilter']={ref:ws['!ref']};ws['!cols']=Array.from({length:range.e.c+1},()=>({wch:21}));
   const h=rows.length?Object.keys(rows[0]):[];
   for(let r=1;r<=range.e.r;r++)for(let c=0;c<=range.e.c;c++){const cell=ws[XLSX.utils.encode_cell({r,c})];if(cell?.t==='n')cell.z=h[c]==='Discount factor'||h[c]==='Accrual fraction'?'0.00000000':h[c]==='Source row'?'0':'#,##0.00';}
  }XLSX.utils.book_append_sheet(wb,ws,name);
 }
 XLSX.writeFile(wb,filename.replace(/\.[^.]+$/,'')+'-valuations.xlsx');
}
$('export-btn').onclick=()=>{try{exportResults();}catch(e){notify('Export failed: '+e.message,true);}};
$('remap-btn').onclick=()=>{$('results-section').hidden=true;$('mapping-card').hidden=false;progress(2);$('notice').hidden=true;};
$('reset-btn').onclick=()=>{workbook=null;results=null;sourceRows=[];$('mapping-card').hidden=true;$('results-section').hidden=true;$('upload-layout').hidden=false;$('file-input').value='';$('notice').hidden=true;progress(1);};

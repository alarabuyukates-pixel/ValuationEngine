'use strict';
const $ = id => document.getElementById(id);
const fields = [
  ['bond_id','Bond ID','Optional · generated if blank',['productid','instrumentid','bondid','isin','id','securityid','security','name'],''],
  ['kind','Instrument type','fixed, floating, zero or mortgage',['type','kind','bondtype','ratetype'],'fixed'],
  ['currency','Currency','EUR only · blank cells default to EUR',['currency','ccy'],'EUR'],
  ['issue','Issue date','Required · YYYY-MM-DD',['issuedate','issue','dateddate','startdate','originationdate'],''],
  ['maturity','Maturity date','Required · YYYY-MM-DD',['maturitydate','maturity','enddate'],''],
  ['settlement','Settlement date','Required · YYYY-MM-DD',['settlementdate','settlement','valuationdate','asofdate'],''],
  ['notional','Original principal / notional','Mortgage: original loan amount · EUR',['notional','principal','facevalue','faceamount','nominal'],''],
  ['frequency','Payments per year','1, 2, 3, 4, 6 or 12',['paymentsperyear','frequency','couponfrequency'],'2'],
  ['day_count','Coupon day count','ACT/360, ACT/365F or 30E/360',['daycount','coupondaycount','daycountconvention'],'ACT/360'],
  ['coupon','Fixed coupon / mortgage rate (%)','Required for fixed bonds and mortgages',['coupon','couponrate','fixedcoupon','annualcouponrate','fixedrate','mortgagerate'],''],
  ['spread','Floating spread (%)','0.5 = 50 basis points',['spread','margin','floatingspread'],'0'],
  ['discount','Flat discount zero rate (%)','Only when no discount curve ID is selected',['discountrate','discountzerorate','discount','zerorate'],''],
  ['projection','Flat projection zero rate (%)','Floating override when no projection curve ID is selected',['projectionrate','projectionzerorate','projection','forwardcurverate'],''],
  ['discount_curve','Discount curve ID','Use a library ID, or leave blank for flat override',['discountcurve','discountcurveid'],''],
  ['projection_curve','Projection curve ID','Named mode: match index; curve mode: explicit proxy',['projectioncurve','projectioncurveid'],''],
  ['index','Floating index','Named mode only: EURIBOR or custom term index',['index','floatingindex','referenceindex'],''],
  ['index_tenor','Custom index tenor (months)','Full index tenor; inferred for named EURIBOR',['indextenor','indextenormonths'],''],
  ['index_day_count','Index day count','EURIBOR uses ACT/360; custom defaults to coupon basis',['indexdaycount'],''],
  ['repayment','Mortgage repayment','annuity, linear or interest-only',['repayment','repaymentmethod','amortization'],'annuity'],
  ['discount_spread_bps','Mortgage discount spread (bps)','Additional continuous discount spread · 100 bps = 1%',['discountspreadbps','mortgagespreadbps'],'0'],
  ['rate_type','Mortgage rate type','fixed or floating',['ratetype','mortgageratetype'],'fixed'],
  ['fixed_until','Mortgage fixed until','Blank means maturity; earlier reset dates are rejected',['fixeduntil','fixedrateuntil','resetdate'],''],
  ['fixing','Current reference fixing (%)','Required for floating · excluding spread',['currentfixing','fixing','referencefixing','referencerate','currentreferencerate'],'']
];
fields.push(
 ['index_mode','Index mode','named or curve',['indexmode'],'named'],
 ['reset_months','Reset interval (months)','Separate from payment frequency',['resetmonths','resetintervalmonths'],''],
 ['next_reset','Next reset date','Optional explicit payment boundary',['nextreset','nextresetdate'],''],
 ['outstanding','Current outstanding principal','Required for seasoned floating mortgages',['outstanding','currentoutstandingprincipal','currentoutstanding'],''],
 ['current_payment','Current instalment','Required for seasoned floating annuities',['currentpayment','currentinstalment'],''],
 ['prepayment_model','Prepayment model','none, cpr or formula · reduce payments, retain maturity',['prepaymentmodel'],'none'],
 ['cpr','Annual CPR (%)','6 means 6% yearly; also base_cpr_pct in a formula',['cpr','annualcpr'],'0'],
 ['cpr_formula','CPR equation (%)','Example: min(20, base_cpr_pct + 0.1 * age_months) · result must be 0–100',['cprformula','cprequation'],'']
);
for(const f of fields) f[3].push(normalizeKey(f[0]),normalizeKey(f[1]));
function normalizeKey(v){return String(v).toLowerCase().replace(/[^a-z0-9]/g,'');}
const rateKeys = new Set(['coupon','spread','discount','projection','fixing','cpr']);
const dateKeys = new Set(['issue','maturity','settlement','fixed_until','next_reset']);

let workbook,grid=[],columns=[],sourceRows=[],filename='',results,runInputs=[],resultView='summary';
let portfolio=[],sequence=0,editing=null,revision=0,busy=false,worker,messageId=0;
const pending=new Map();
const normalize=normalizeKey;
const fmt=(v,d=2)=>Number(v).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
function notify(message,error=false){$('notice').hidden=false;$('notice').className=error?'error':'';$('notice').textContent=message;}
function showView(name){for(const k of ['studio','portfolio','results','curves','method'])$(k+'-view').hidden=k!==name;document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.view===name));$('page-label').textContent={studio:'Data & valuation',portfolio:'Balance sheet',results:'Results & validation',curves:'Market curves',method:'Method & assumptions'}[name];window.scrollTo({top:0});}
document.querySelectorAll('[data-view]').forEach(e=>e.onclick=()=>showView(e.dataset.view));
function invalidate(){revision++;results=null;$('results-content').hidden=true;$('results-empty').hidden=false;$('export-btn').disabled=true;$('run-caption').textContent='Inputs changed. Run valuation to generate current results.';}
function makeWorkbook(rows,name='Positions'){const w=XLSX.utils.book_new();XLSX.utils.book_append_sheet(w,XLSX.utils.aoa_to_sheet(rows),name);return w;}
function sampleRows(){const common={currency:'EUR',issue:'2026-01-01',maturity:'2031-01-01',settlement:'2026-11-01',notional:100000,frequency:4,day_count:'ACT/360',discount:3.5,discount_curve:'flat'};return [
 {...common,bond_id:'BOND-FIXED',kind:'fixed',coupon:4},
 {...common,bond_id:'BOND-FLOAT',kind:'floating',index_mode:'curve',index_tenor:3,reset_months:3,projection:3.3,projection_curve:'flat',spread:0.6,fixing:3.4},
 {...common,bond_id:'ZERO',kind:'zero'},
 {...common,bond_id:'MORTGAGE-FIXED',kind:'mortgage',rate_type:'fixed',coupon:4,frequency:12,repayment:'annuity',notional:300000,maturity:'2046-01-01'},
 {...common,bond_id:'MORTGAGE-FLOAT',kind:'mortgage',rate_type:'floating',frequency:12,repayment:'annuity',notional:300000,maturity:'2046-01-01',outstanding:291696.8,current_payment:1817.94,index_mode:'curve',index_tenor:3,reset_months:3,projection:3.3,projection_curve:'flat',spread:0.6,fixing:3.4}];}
function inputWorkbook(rows){const w=makeWorkbook([fields.map(f=>f[1]),...rows.map(r=>fields.map(f=>r[f[0]]??''))]);const cr=CurveManager.all().filter(c=>rows.some(r=>r.discount_curve===c.id||r.projection_curve===c.id));if(cr.length){const data=[['Curve ID','Currency','As of','Source','Reference index','Pillar date','Zero rate %','Discount factor','Compounding','Day count']];for(const c of cr)for(const p of c.pillars)data.push([c.id,c.currency,c.as_of,c.source,c.reference_index||'',p.date,p.discount_factor===undefined?p.zero_rate*100:'',p.discount_factor??'',c.compounding,c.day_count]);XLSX.utils.book_append_sheet(w,XLSX.utils.aoa_to_sheet(data),'Curves');}return w;}
$('template-btn').onclick=()=>XLSX.writeFile(inputWorkbook(sampleRows()),'balance-sheet-template.xlsx');
$('sample-btn').onclick=()=>{workbook=inputWorkbook(sampleRows());filename='mixed-sample.xlsx';loadWorkbook();notify('Five sample positions loaded for import. Flat rates are fictional assumptions. Select Load into balance sheet to continue.');};

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
 if(workbook.Sheets.Curves)CurveManager.importSheet(workbook);
 for(const name of workbook.SheetNames.filter(n=>!['Curves','Instructions'].includes(n))) {const option=new Option(name,name);$('sheet-select').add(option);}
 if(!$('sheet-select').options.length)throw new Error('No bond worksheet found. Upload curve-only files in Market curves.');
 $('header-row').value='1';$('mapping-card').hidden=false;
 $('filename').textContent=filename;readSheet();
}
$('sheet-select').onchange=()=>safeReadSheet();$('header-row').onchange=()=>safeReadSheet();
function safeReadSheet(){try{readSheet();}catch(e){sourceRows=[];notify(e.message,true);$('apply-import').disabled=true;}}
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
 if(sourceRows.length>5000)throw new Error('Maximum 5,000 instruments per run. Choose a smaller worksheet.');
 $('row-count').textContent=sourceRows.length+' instruments found';
 $('mapping-fields').replaceChildren();
 for(const [key,label,hint,aliases,fallback]of fields){
  const row=document.createElement('div');row.className='mapping-row';
  const lab=document.createElement('label');lab.htmlFor='map-'+key;lab.textContent=label;
  const small=document.createElement('small');small.textContent=hint;lab.append(small);
  const select=document.createElement('select');select.id='map-'+key;select.setAttribute('aria-label',label+' source column');select.add(new Option('— Use fallback —',''));
  for(const column of columns)select.add(new Option(column.label+' ('+XLSX.utils.encode_col(column.index)+')',String(column.index)));
  const match=columns.find(c=>aliases.includes(normalize(c.label)));
  if(match)select.value=String(match.index);
  const input=document.createElement('input');input.id='fallback-'+key;input.value=CurveManager.defaults()[key]||fallback;input.placeholder=dateKeys.has(key)?'YYYY-MM-DD':'No fallback';input.setAttribute('aria-label',label+' fallback');
  if(['discount_curve','projection_curve'].includes(key))input.setAttribute('list','curve-ids');
  if(key==='index')input.setAttribute('list','index-ids');
  row.append(lab,select,input);$('mapping-fields').append(row);
 }
 const preview=sourceRows.slice(0,5).map(r=>Object.fromEntries(columns.map(c=>[c.label+' ('+XLSX.utils.encode_col(c.index)+')',r.values[c.index]])));
 renderTable($('source-preview'),preview);

 $('apply-import').disabled=!sourceRows.length;
}
function normalizeRows(){
 const sheet=workbook.Sheets[$('sheet-select').value];
 const date1904=!!workbook.Workbook?.WBProps?.date1904;
 return sourceRows.map(({values,row})=>{
  const record={_source_row:row,_source_file:filename,_source_sheet:$('sheet-select').value};
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
 if(!worker){worker=new Worker('worker.js?v=3.2.0');worker.onmessage=({data})=>{const request=pending.get(data.id);if(!request)return;clearTimeout(request.timer);pending.delete(data.id);data.error?request.reject(new Error(data.error)):request.resolve(data.result);};worker.onerror=e=>{for(const request of pending.values()){clearTimeout(request.timer);request.reject(new Error(e.message||'Python worker could not start'));}pending.clear();worker.terminate();worker=null;};}
 return new Promise((resolve,reject)=>{const id=++messageId;const timer=setTimeout(()=>{worker?.terminate();worker=null;pending.delete(id);reject(new Error('Python startup or valuation timed out. Check your connection and try again.'));},120000);pending.set(id,{resolve,reject,timer});worker.postMessage({id,type:'run',rows});});
}

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
   if(typeof value==='number'){td.className='num';const digits=h==='Discount factor'||h==='Accrual fraction'?8:h.includes('rate')||h.includes('/ 100')?4:['Source row','Position','Workbook row','Payments / year','Reset months','Remaining payments','Index tenor months','Reset interval months'].includes(h)?0:2;td.textContent=fmt(value,digits);}
   else if(h==='Status'){const badge=document.createElement('span');badge.className=value==='OK'?'status-ok':'status-error';badge.textContent=value||'';td.append(badge);}
   else td.textContent=value===null||value===undefined?'—':String(value);
   tr.append(td);
  }tbody.append(tr);
 }table.append(tbody);container.append(table);
}
function entry(mode){$('excel-entry').hidden=mode!=='excel';$('manual-entry').hidden=mode!=='manual';$('entry-excel').classList.toggle('selected',mode==='excel');$('entry-manual').classList.toggle('selected',mode==='manual');}
$('entry-excel').onclick=()=>entry('excel');$('entry-manual').onclick=()=>entry('manual');
$('apply-import').onclick=()=>{try{const rows=normalizeRows();if(!rows.length)throw Error('No positions to import.');if(rows.length+($('import-mode').value==='append'?portfolio.length:0)>5000)throw Error('Maximum 5,000 positions.');if($('import-mode').value==='replace')portfolio=[];portfolio.push(...rows.map(r=>({...r,_row:++sequence})));invalidate();renderPortfolio();showView('portfolio');notify(rows.length+' positions loaded. Review their terms, then run valuation.');}catch(e){notify(e.message,true);}};
function renderPortfolio(){const n=portfolio.length;$('ready-count').textContent=n;$('portfolio-count').textContent=n;$('floating-count').textContent=portfolio.filter(r=>r.kind==='floating'||r.rate_type==='floating').length;for(const id of ['run-btn','portfolio-run','download-inputs'])$(id).disabled=!n||busy;$('run-status').textContent=n?'Ready to calculate all products together.':'Import or enter positions to begin.';const q=$('portfolio-search').value.toLowerCase();const rows=portfolio.filter(r=>Object.values(r).join(' ').toLowerCase().includes(q));renderTable($('portfolio-table'),rows.map(r=>({'Position':r._row,'Product ID':r.bond_id,'Type':r.kind,'Interest':r.kind==='floating'?'floating':r.rate_type||'fixed','Currency':r.currency||'EUR','Original principal':r.notional,'Outstanding input':r.outstanding,'Prepayment model':r.prepayment_model||'none','Maturity':r.maturity,'Payments / year':r.frequency,'Reset months':r.reset_months,'Discount curve':r.discount_curve||'flat','Projection curve':r.projection_curve||'flat'})));$('portfolio-footer').textContent=`${rows.length} of ${n} positions · displayed amounts are inputs, not valuation results.`;const prev=$('portfolio-select').value;$('portfolio-select').replaceChildren();for(const r of portfolio)$('portfolio-select').add(new Option(`${r.bond_id||'Unnamed'} · position ${r._row}`,r._row));if(portfolio.some(r=>String(r._row)===prev))$('portfolio-select').value=prev;detail();}
function selected(){return portfolio.find(r=>String(r._row)===$('portfolio-select').value);}
function detail(){const r=selected();$('position-properties').replaceChildren();$('edit-position').disabled=$('remove-position').disabled=!r;if(!r)return;for(const [k,label]of fields){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=r[k]===''||r[k]==null?'—':r[k];$('position-properties').append(dt,dd);}for(const [label,v]of [['Source file',r._source_file],['Worksheet',r._source_sheet],['Workbook row',r._source_row]]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=v||'Direct entry';$('position-properties').append(dt,dd);}}
$('portfolio-select').onchange=detail;$('portfolio-search').oninput=renderPortfolio;
$('download-inputs').onclick=()=>XLSX.writeFile(inputWorkbook(portfolio),'balance-sheet-inputs.xlsx');
const choices={frequency:['1','2','3','4','6','12'],day_count:['ACT/360','ACT/365F','30E/360'],index_mode:['curve','named'],index_day_count:['ACT/360','ACT/365F','30E/360'],prepayment_model:['none','cpr','formula'],repayment:['annuity','linear','interest-only']};
for(const [k,label,hint,,fallback]of fields){if(['kind','currency','rate_type','fixed_until'].includes(k))continue;const l=document.createElement('label');l.id='field-'+k;l.textContent=label;const input=document.createElement(choices[k]?'select':k==='cpr_formula'?'textarea':'input');input.id='edit-'+k;if(choices[k])for(const v of choices[k])input.add(new Option(k==='prepayment_model'?{none:'No prepayment',cpr:'Constant annual CPR',formula:'CPR equation'}[v]:v,v));else if(k!=='cpr_formula')input.type=dateKeys.has(k)?'date':'text';if(k==='cpr_formula'){input.rows=3;input.maxLength=500;input.placeholder='min(20, base_cpr_pct + 0.1 * age_months)';}input.value=fallback;if(['discount_curve','projection_curve'].includes(k))input.setAttribute('list','curve-ids');if(k==='index')input.setAttribute('list','index-ids');const small=document.createElement('small');small.textContent=hint;l.append(input,small);$('editor-fields').append(l);}
function editorVisibility(){const mortgage=$('edit-product').value==='mortgage',zero=$('edit-product').value==='zero',floating=!zero&&$('edit-rate-type').value==='floating';$('edit-rate-label').hidden=zero;const floatKeys=['spread','projection','projection_curve','index_mode','index','index_tenor','index_day_count','fixing','reset_months','next_reset'];for(const k of floatKeys)$('field-'+k).hidden=!floating;for(const k of ['repayment','discount_spread_bps','prepayment_model'])$('field-'+k).hidden=!mortgage;for(const k of ['outstanding','current_payment'])$('field-'+k).hidden=!mortgage||!floating||k==='current_payment'&&$('edit-repayment').value!=='annuity';$('field-cpr').hidden=!mortgage||$('edit-prepayment_model').value==='none';$('field-cpr_formula').hidden=!mortgage||$('edit-prepayment_model').value!=='formula';$('field-coupon').hidden=zero||floating;$('field-day_count').hidden=mortgage||zero;$('field-frequency').hidden=zero;if(floating)$('field-index').hidden=$('edit-index_mode').value==='curve';$('prepayment-guidance').hidden=!mortgage||$('edit-prepayment_model').value==='none';$('editor-guidance').textContent=floating?'Current fixing excludes spread. Future coupons use full-tenor forward rates + spread. Reset intervals must align to payment dates. Seasoned floating mortgages need current outstanding principal; annuities also need the current instalment.':'All amounts are EUR. Fixed mortgages use the original principal and scheduled repayments. Flat rates are explicit assumptions, not market quotes.';}
function resetEditor(r){editing=r?._row??null;$('editor-title').textContent=r?'Edit position':'Add a position';$('save-position').textContent=r?'Save changes →':'Add to balance sheet →';$('edit-product').value=r?.kind==='mortgage'?'mortgage':r?.kind==='zero'?'zero':'bond';$('edit-rate-type').value=r?.kind==='floating'||r?.rate_type==='floating'?'floating':'fixed';const defaults={bond_id:'POSITION-'+(sequence+1),issue:'2026-01-01',maturity:'2036-01-01',settlement:new Date().toISOString().slice(0,10),notional:100000,coupon:4,frequency:2,discount:3.5,discount_curve:'flat',projection_curve:'flat',projection:3.3,index_mode:'curve',index_tenor:3,reset_months:6,index_day_count:'ACT/360',fixing:3.4};for(const [k,,, ,fallback]of fields)if($('edit-'+k))$('edit-'+k).value=r?(r[k]??''):(defaults[k]??CurveManager.defaults()[k]??fallback);editorVisibility();}
$('edit-product').onchange=()=>{if(!editing){$('edit-frequency').value=$('edit-product').value==='mortgage'?'12':'2';$('edit-reset_months').value=$('edit-product').value==='mortgage'?'3':'6';}editorVisibility();};for(const id of ['edit-rate-type','edit-index_mode','edit-repayment','edit-prepayment_model'])$(id).onchange=editorVisibility;
$('add-position').onclick=()=>{resetEditor();entry('manual');showView('studio');};$('edit-position').onclick=()=>{resetEditor(selected());entry('manual');showView('studio');};$('cancel-edit').onclick=()=>resetEditor();
$('remove-position').onclick=()=>{const r=selected();if(!r)return;portfolio=portfolio.filter(x=>x!==r);invalidate();renderPortfolio();notify('Position removed. Download inputs to preserve your current portfolio.');};
$('position-form').onsubmit=e=>{e.preventDefault();if(portfolio.length>=5000&&!editing){notify('Maximum 5,000 positions.',true);return;}const old=portfolio.find(r=>r._row===editing);const r={...old,_row:editing||++sequence,_source_file:'Direct entry',_source_sheet:'',_source_row:'',currency:'EUR'};for(const [k]of fields)if($('edit-'+k)&&!$('field-'+k)?.hidden)r[k]=$('edit-'+k).value.trim();r.kind=$('edit-product').value==='bond'?$('edit-rate-type').value:$('edit-product').value;r.rate_type=r.kind==='zero'?'fixed':$('edit-rate-type').value;if(r.index_mode==='curve')r.index='';r.fixed_until='';if(r.kind!=='mortgage'){r.prepayment_model='none';r.cpr='';r.cpr_formula='';}else if(r.prepayment_model!=='formula')r.cpr_formula='';if(r.prepayment_model==='none')r.cpr='0';if(old)portfolio[portfolio.indexOf(old)]=r;else portfolio.push(r);invalidate();renderPortfolio();$('portfolio-select').value=r._row;detail();resetEditor();showView('portfolio');notify('Position saved. Run valuation to validate inputs and calculate.');};
function selectedResult(){if(!results)return null;const r=$('result-scenario').value==='BASE'?results:results.scenario_results.find(s=>s.id===$('result-scenario').value);return {...r,sourceInputs:results.sourceInputs,runInfo:{...results.runInfo,Scenario:r.name||'Base',Definition:r.definition?JSON.stringify(r.definition):'No shock'}};}
function resetRows(){const seen=new Set();return selectedResult().cashflows.filter(r=>{const key=r['Source row']+'|'+r['Reset date'];if(!r['Reset date']||seen.has(key))return false;seen.add(key);return true;}).map(r=>Object.fromEntries(['Source row','Bond ID','Index','Reset date','Index end date','Rate source','Reference rate %','Spread %','Coupon rate %','Index accrual fraction','Projection start DF','Projection end DF','Projection curve'].map(k=>[k,r[k]])));}
function showResults(){if(!results)return;document.querySelectorAll('[data-result]').forEach(b=>b.classList.toggle('selected',b.dataset.result===resultView));let rows=resultView==='sensitivity'?(results.sensitivity||[]):resultView==='resets'?resetRows():selectedResult()[resultView];if($('result-filter').value)rows=rows.filter(r=>String(r['Source row'])===$('result-filter').value);const priority=resultView==='sensitivity'?['Scenario','Bond ID','Status','Base dirty PV','Scenario dirty PV','Delta PV','Delta PV %','Delta clean PV','Discount shocked','Projection shocked','Error']:resultView==='summary'?['Bond ID','Status','Type','Dirty PV','Accrued interest','Clean PV','Outstanding principal','Clean price / 100','Currency','Rate type','Index','Warnings','Error']:resultView==='cashflows'?['Bond ID','Payment date','Opening balance','Interest','Scheduled principal','Prepayment','Principal','Annual CPR %','Total payment','Closing balance','Discount factor','Present value','Coupon rate %']:[];const headers=[...new Set([...priority,...rows.flatMap(r=>Object.keys(r))])];renderTable($('result-table'),rows,headers);$('result-footer').textContent=`Showing ${Math.min(rows.length,500)} of ${rows.length} rows. Downloads contain full schedules and precision. EUR asset values.`;}
async function run(){if(busy||!portfolio.length)return;busy=true;renderPortfolio();const rev=revision;document.querySelectorAll('#position-form input,#position-form select,#position-form textarea,#position-form button,#apply-import,#remove-position,#clear-imports,#refresh-ecb,#curve-file,#sample-btn,#file-input,#scenario-form input,#scenario-form select,#scenario-form textarea,#scenario-form button,#scenario-list button,#scenario-list input,#run-scenarios input,[data-shock]').forEach(e=>e.disabled=true);try{await CurveManager.ready;const snapshot=structuredClone(portfolio),curves=structuredClone(CurveManager.all());notify('Calculating all positions in the local Python engine…');$('runtime-status').textContent='Calculating…';const start=performance.now();const scenarios=ScenarioManager.selected();const output=await pythonRequest({bonds:snapshot,curves,scenarios});if(revision!==rev)throw Error('Inputs changed during the run. Please run valuation again.');results=output;runInputs=snapshot;results.sourceInputs=snapshot;results.sourceCurves=curves;results.runInfo={timestamp:new Date().toISOString(),seconds:((performance.now()-start)/1000).toFixed(2),engine:'3.2.0',currency:'EUR'};$('metric-valued').textContent=results.summary.filter(r=>r.Status==='OK').length;$('metric-flows').textContent=results.cashflows.length;$('metric-errors').textContent=results.errors.length;$('run-caption').textContent=`${snapshot.length} positions · base + ${results.scenario_results?.length||0} scenarios · ${results.runInfo.seconds}s · ${results.runInfo.timestamp}`;$('result-filter').replaceChildren(new Option('All products',''));$('validation-select').replaceChildren();for(const r of results.summary){const label=`${r['Bond ID']} · position ${r['Source row']} · ${r.Status}`;$('result-filter').add(new Option(label,r['Source row']));$('validation-select').add(new Option(label,r['Source row']));}$('results-empty').hidden=true;$('results-content').hidden=false;$('export-btn').disabled=false;$('result-scenario').replaceChildren(new Option('Base (no shock)','BASE'));for(const scenario of results.scenario_results||[])$('result-scenario').add(new Option(scenario.name,scenario.id));$('sensitivity-overview').hidden=!(results.scenario_results||[]).length;renderTable($('scenario-totals'),results.scenario_totals||[]);resultView=results.scenario_results?.length?'sensitivity':'summary';updateScenarioContext();showView('results');notify(`Base: ${results.summary.length-results.errors.length} products valued; ${results.errors.length} need review. ${(results.scenario_results||[]).length} scenarios completed; ${(results.scenario_results||[]).reduce((n,s)=>n+s.errors.length,0)} scenario row errors.`,!!results.errors.length);}catch(e){notify('Valuation could not complete: '+e.message,true);}finally{busy=false;document.querySelectorAll('#position-form input,#position-form select,#position-form textarea,#position-form button,#apply-import,#remove-position,#clear-imports,#refresh-ecb,#curve-file,#sample-btn,#file-input,#scenario-form input,#scenario-form select,#scenario-form textarea,#scenario-form button,#scenario-list button,#scenario-list input,#run-scenarios input,[data-shock]').forEach(e=>e.disabled=false);renderPortfolio();$('runtime-status').textContent='Python engine ready · data stays local';}}
$('run-btn').onclick=run;$('portfolio-run').onclick=run;
document.querySelectorAll('[data-result]').forEach(b=>b.onclick=()=>{resultView=b.dataset.result;showResults();});$('result-filter').onchange=showResults;
function validationStatus(){const r=selectedResult()?.summary.find(r=>String(r['Source row'])===$('validation-select').value);$('validation-status').textContent=r?.Status==='OK'?'Includes formula-based arithmetic checks. PASS is internal reconciliation, not independent market-price validation.':'This product failed validation. Its workbook includes the inputs and error details; no valuation is fabricated.';}
$('validation-select').onchange=validationStatus;$('validation-export').onclick=()=>{if(results)XLSX.writeFile(ValuationExport.validation(selectedResult(),$('validation-select').value),'product-validation-'+$('validation-select').value+'-'+$('result-scenario').value+'.xlsx');};$('export-btn').onclick=()=>{if(results)XLSX.writeFile(ValuationExport.all(results),'balance-sheet-results.xlsx');};
window.addEventListener('curves-updated',()=>{if(results)invalidate();});
resetEditor();renderPortfolio();

function updateScenarioContext(){const r=selectedResult();if(!r)return;$('scenario-context-note').textContent=r.definition?ScenarioManager.describe(r.definition)+' · fixed current fixing; future resets recomputed':'Base curves · no scenario shock';$('metric-valued').textContent=r.summary.filter(x=>x.Status==='OK').length;$('metric-flows').textContent=r.cashflows.length;$('metric-errors').textContent=r.errors.length;const prev=$('validation-select').value;$('validation-select').replaceChildren();for(const x of r.summary)$('validation-select').add(new Option(`${x['Bond ID']} · position ${x['Source row']} · ${x.Status}`,x['Source row']));if(r.summary.some(x=>String(x['Source row'])===prev))$('validation-select').value=prev;showResults();validationStatus();}
$('result-scenario').onchange=updateScenarioContext;
window.addEventListener('scenarios-updated',()=>{invalidate();});

'use strict';
window.CurveManager = (() => {
 const byId=new Map(); const builtin=new Set(); let snapshot={};
 const get=id=>document.getElementById(id);
 const norm=value=>String(value??'').toLowerCase().replace(/[^a-z0-9]/g,'');
 let selectedDiscount='',selectedProjection='',selectedIndex='';
 function message(value,error=false){get('curve-notice').hidden=false;get('curve-notice').textContent=value;get('curve-notice').className=error?'error':'';}
 function dateValue(value,wb){
  if(value instanceof Date)return value.toISOString().slice(0,10);
  if(typeof value==='number'){const d=XLSX.SSF.parse_date_code(value,{date1904:!!wb.Workbook?.WBProps?.date1904});if(d)return `${d.y}-${String(d.m).padStart(2,'0')}-${String(d.d).padStart(2,'0')}`;}
  return String(value??'').trim().slice(0,10);
 }
 function number(value){const n=Number(String(value).replace(/,/g,'').replace('%','').trim());if(!Number.isFinite(n))throw new Error('Curve values must be finite numbers.');return n;}
 function add(curves,official=false){
  const checked=[];const ids=new Set();
  for(const c of curves){
   if(!c.id||!c.currency||!c.as_of||!Array.isArray(c.pillars)||!c.pillars.length)throw new Error('Each curve needs an ID, currency, as-of date and pillars.');
   if(ids.has(c.id))throw new Error('Duplicate curve ID in import: '+c.id);ids.add(c.id);
   if(builtin.has(c.id)&&!official)throw new Error('Choose a different ID: '+c.id+' is reserved for the official snapshot.');
   checked.push({...c,currency:String(c.currency).toUpperCase(),compounding:c.compounding||'continuous',day_count:c.day_count||'ACT/365F',source:c.source||'User supplied'});
  }
  for(const c of checked){byId.set(c.id,c);if(official)builtin.add(c.id);}
  render();return checked.length;
 }
 function importSheet(wb,name='Curves'){
  const ws=wb.Sheets[name];if(!ws)return 0;
  const grid=XLSX.utils.sheet_to_json(ws,{header:1,defval:null,raw:true});if(grid.length<2)throw new Error('Curve worksheet has no pillar rows.');
  const heads=grid[0].map(norm);
  function col(...names){return heads.findIndex(h=>names.includes(h));}
  const cols={id:col('curveid','id'),currency:col('currency','ccy'),as_of:col('asof','asofdate','curvedate'),source:col('source','provider'),reference_index:col('referenceindex','index'),date:col('pillardate','date','maturitydate'),zero:col('zerorate','zeroratepercent','zeroyield'),df:col('discountfactor','df'),compounding:col('compounding'),day_count:col('daycount','curvedaycount')};
  if([cols.id,cols.currency,cols.as_of,cols.date].some(c=>c<0)||cols.zero<0&&cols.df<0)throw new Error('Use the curve template headings: Curve ID, Currency, As of, Pillar date, Zero rate % or Discount factor.');
  const grouped=new Map();
  for(let r=1;r<grid.length;r++){
   const row=grid[r];if(row.every(v=>v===null||v===''))continue;
   const value=key=>cols[key]>=0?row[cols[key]]:null;
   const id=String(value('id')??'').trim();if(!id)throw new Error('Curve ID missing at curve row '+(r+1));
   const meta={id,currency:String(value('currency')??'').trim().toUpperCase(),as_of:dateValue(value('as_of'),wb),source:String(value('source')||'User supplied').trim(),reference_index:String(value('reference_index')||'').trim()||null,compounding:String(value('compounding')||'continuous').trim().toLowerCase(),day_count:String(value('day_count')||'ACT/365F').trim().toUpperCase()};
   if(!grouped.has(id))grouped.set(id,{...meta,pillars:[]});
   const curve=grouped.get(id);for(const key of Object.keys(meta))if(curve[key]!==meta[key])throw new Error('Inconsistent '+key+' for curve '+id+' at row '+(r+1));
   const pillar={date:dateValue(value('date'),wb)};
   const hasZero=value('zero')!==null&&value('zero')!==undefined&&String(value('zero')).trim()!=='';
   const hasDF=value('df')!==null&&value('df')!==undefined&&String(value('df')).trim()!=='';
   if(hasZero===hasDF)throw new Error('Enter exactly one zero rate or discount factor at curve row '+(r+1));
   if(hasDF)pillar.discount_factor=number(value('df'));
   else{const cell=ws[XLSX.utils.encode_cell({r,c:cols.zero})];pillar.zero_rate=typeof value('zero')==='number'&&cell?.z?.includes('%')?value('zero'):number(value('zero'))/100;}
   curve.pillars.push(pillar);
  }
  return add([...grouped.values()]);
 }
 function table(container,rows){container.replaceChildren();if(!rows.length){container.textContent='No curves loaded.';return;}const headers=[...new Set(rows.flatMap(r=>Object.keys(r)))],t=document.createElement('table'),h=document.createElement('tr');for(const k of headers){const th=document.createElement('th');th.textContent=k;h.append(th);}const thead=document.createElement('thead');thead.append(h);t.append(thead);const body=document.createElement('tbody');for(const row of rows){const tr=document.createElement('tr');for(const k of headers){const td=document.createElement('td');td.textContent=row[k]??'—';tr.append(td);}body.append(tr);}t.append(body);container.append(t);}
 function render(){
  const all=[...byId.values()];
  table(get('curve-library-table'),all.map(c=>({'Curve ID':c.id,Currency:c.currency,'As of':c.as_of,Source:c.source,'Projection index':c.reference_index||'—','Last pillar':c.pillars.at(-1)?.date,Pillars:c.pillars.length})));
  const previous=get('curve-preview-select').value;get('curve-preview-select').replaceChildren();get('curve-ids').replaceChildren();
  for(const c of all){get('curve-preview-select').add(new Option(c.id+' · '+c.as_of,c.id));const option=document.createElement('option');option.value=c.id;get('curve-ids').append(option);}
  if(byId.has(previous))get('curve-preview-select').value=previous;
  preview();
  const official=all.find(c=>builtin.has(c.id));
  get('market-status').textContent=official?`ECB EUR AAA curve · as of ${official.as_of} · ${official.pillars.length} pillars`:'No official curve loaded · import a dated market curve';
  if(official)get('ecb-detail').textContent=`Source: ${official.source}. Market date: ${official.as_of}. Retrieved: ${snapshot.fetched_at||'not recorded'}. Coverage through ${official.pillars.at(-1).date}.`;
 }
 function preview(){
  const c=byId.get(get('curve-preview-select').value);get('use-discount-curve').disabled=!c;get('use-projection-curve').disabled=!c?.reference_index;
  if(!c){get('curve-description').textContent='Import curves or refresh the ECB benchmark.';get('curve-pillars').replaceChildren();return;}
  get('curve-description').textContent=`${c.id} · ${c.currency} · ${c.compounding} · ${c.day_count} · source: ${c.source}. Curve date must not be after settlement. Older curves produce an age warning in results.`;
  table(get('curve-pillars'),c.pillars.map(p=>({'Pillar date':p.date,'Zero rate %':(p.zero_rate??p.quoted_zero_rate)===undefined?'—':((p.zero_rate??p.quoted_zero_rate)*100).toFixed(6),'Discount factor':p.discount_factor===undefined?'—':Number(p.discount_factor).toFixed(10)})));
 }
 get('curve-preview-select').onchange=preview;
 get('use-discount-curve').onclick=()=>{selectedDiscount=get('curve-preview-select').value;if(get('fallback-discount_curve'))get('fallback-discount_curve').value=selectedDiscount;message('Discount fallback set to '+selectedDiscount+'. Open Valuation studio to load or value your bonds.');};
 get('use-projection-curve').onclick=()=>{const c=byId.get(get('curve-preview-select').value);if(!c?.reference_index)return;selectedProjection=c.id;selectedIndex=c.reference_index;if(get('fallback-projection_curve'))get('fallback-projection_curve').value=c.id;if(get('fallback-index'))get('fallback-index').value=c.reference_index;message('Projection fallback set to '+c.id+' for '+c.reference_index+'. Existing mapped columns take precedence.');};
 get('clear-imports').onclick=()=>{for(const id of byId.keys())if(!builtin.has(id))byId.delete(id);selectedDiscount='';selectedProjection='';selectedIndex='';render();message('Imported and demonstration curves cleared.');};
 get('curve-file').onchange=async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>5*1024*1024)throw new Error('Choose a curve file under 5 MB.');let count;if(file.name.toLowerCase().endsWith('.json')){const data=JSON.parse(await file.text());count=add(Array.isArray(data)?data:data.curves);}else{const wb=XLSX.read(await file.arrayBuffer(),{type:'array',cellNF:true});count=importSheet(wb,wb.SheetNames.includes('Curves')?'Curves':wb.SheetNames[0]);}message(count+' curves imported. Select an ID in the bond mappings.');}catch(e){message(e.message,true);}finally{event.target.value='';}};
 const templateHeaders=['Curve ID','Currency','As of','Source','Reference index','Pillar date','Zero rate %','Discount factor','Compounding','Day count'];
 function demoRows(){return [templateHeaders,
 ['DEMO_EUR_DISC','EUR','2026-10-27','Demonstration only','','2027-10-27',3.2,'','continuous','ACT/365F'],
 ['DEMO_EUR_DISC','EUR','2026-10-27','Demonstration only','','2029-10-27',3.4,'','continuous','ACT/365F'],
 ['DEMO_EUR_DISC','EUR','2026-10-27','Demonstration only','','2036-10-27',3.7,'','continuous','ACT/365F'],
 ['DEMO_EURIBOR_3M','EUR','2026-10-27','Demonstration only','EURIBOR-3M','2027-10-27',3.3,'','continuous','ACT/365F'],
 ['DEMO_EURIBOR_3M','EUR','2026-10-27','Demonstration only','EURIBOR-3M','2029-10-27',3.5,'','continuous','ACT/365F'],
 ['DEMO_EURIBOR_3M','EUR','2026-10-27','Demonstration only','EURIBOR-3M','2036-10-27',3.8,'','continuous','ACT/365F']];}
 get('curve-template').onclick=()=>{const wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet(demoRows()),'Curves');XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet([['Instructions'],['Example rates are fictional. Replace every curve point with your provider data.'],['Use zero rates (not par yields) or discount factors. Set compounding and day count explicitly.'],['Repeat consistent metadata on every row; dates use YYYY-MM-DD.'],['Projection curves must specify the same Reference index as the floating bonds.'],['Native Excel percentage cells are supported. Otherwise 3 means 3%.']]),'Instructions');XLSX.writeFile(wb,'bondflow-curve-template.xlsx');};
 async function refresh(){
  const config=snapshot.refresh||{};
  if(!config.url)throw new Error('Live ECB refresh is unavailable. The bundled snapshot is retained; download a fresh curve from your data provider or redeploy with the market-data refresh script.');
  const response=await fetch(config.url,{cache:'no-store',signal:AbortSignal.timeout(30000)});if(!response.ok)throw new Error('ECB returned HTTP '+response.status);
  const wb=XLSX.read(await response.text(),{type:'string',raw:true}),rows=XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]],{raw:true});
  const points=[];const seen=new Set();let asof='';
  for(const row of rows){const tenor=String(row[config.tenor_column||'DATA_TYPE_FM']||'').replace(/^SR_/,''),match=tenor.match(/^(\d+)([MY])$/);if(!match)continue;if(seen.has(tenor))throw new Error('Duplicate ECB tenor.');seen.add(tenor);if(row.CURRENCY!=='EUR'||row.UNIT!=='PCPA'||!String(row.TITLE_COMPL||'').toLowerCase().includes('continuous compounding'))throw new Error('Unexpected ECB quote convention.');const observed=String(row[config.date_column||'TIME_PERIOD']||'');if(asof&&asof!==observed)throw new Error('ECB series have different market dates; retained the prior snapshot.');if(!/^\d{4}-\d{2}-\d{2}$/.test(observed)||observed>new Date().toISOString().slice(0,10))throw new Error('Invalid ECB observation date.');if(!['A','E'].includes(row.OBS_STATUS))throw new Error('ECB returned an unusable observation.');asof=observed;const raw=String(row[config.value_column||'OBS_VALUE']??'').trim();if(!raw)throw new Error('ECB returned an empty rate.');const n=Number(raw);if(!Number.isFinite(n))throw new Error('ECB returned a missing rate.');const months=Number(match[1])*(match[2]==='Y'?12:1);const d=new Date(asof+'T00:00:00Z'),day=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()+months);const last=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate();d.setUTCDate(Math.min(day,last));points.push({date:d.toISOString().slice(0,10),discount_factor:Math.exp(-(n/100)*(months/12)),quoted_zero_rate:n/100,tenor:tenor});}
  if(!asof||points.length<3||(config.tenors||[]).some(t=>!seen.has(t)))throw new Error('ECB response did not contain a complete curve.');
  points.sort((a,b)=>a.date.localeCompare(b.date));const prior=byId.get('ECB_EUR_AAA');if(prior&&asof<prior.as_of)throw new Error('ECB response is older than the retained snapshot.');
  snapshot.fetched_at=new Date().toISOString();add([{...prior,id:'ECB_EUR_AAA',name:'ECB euro area AAA government zero curve',currency:'EUR',as_of:asof,source:'European Central Bank',source_url:config.url,reference_index:null,compounding:'continuous',day_count:'ACT/365F',pillars:points}],true);message('ECB curve refreshed. Market date: '+asof+'.');
 }
 get('refresh-ecb').onclick=async()=>{const b=get('refresh-ecb');b.disabled=true;try{await ready;await refresh();}catch(e){message('Refresh failed: '+e.message+' The previously loaded curve is unchanged.',true);}finally{b.disabled=false;}};
 const ready=fetch('market-data.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('Snapshot unavailable');return r.json();}).then(data=>{snapshot=data;add(data.curves,true);}).catch(e=>{get('market-status').textContent='ECB snapshot unavailable · import your market curve';get('ecb-detail').textContent=e.message+'. You can still import provider curves or use explicit flat-rate assumptions.';});
 return {ready,all:()=>[...byId.values()],importSheet,demoRows,add,defaults:()=>({discount_curve:selectedDiscount,projection_curve:selectedProjection,index:selectedIndex})};
})();

'use strict';
// No external chart dependencies. Every plotted point is inspectable in the table.
window.drawYieldCurve = (container, curve) => {
 container.replaceChildren();
 if(!curve)return;
 const origin=Date.parse(curve.as_of+'T00:00:00Z');
 const points=curve.pillars.map(p=>{
  const calendarYears=(Date.parse(p.date+'T00:00:00Z')-origin)/86400000/365;
  const tenor=String(p.tenor||'').match(/^(\d+)([MY])$/);
  const years=tenor?Number(tenor[1])/(tenor[2]==='M'?12:1):calendarYears;
  let factor=p.discount_factor;
  if(factor===undefined&&p.zero_rate!==undefined){
   const end=new Date(p.date+'T00:00:00Z'),start=new Date(origin);
   const t=curve.day_count==='ACT/360'?calendarYears*365/360:curve.day_count==='30E/360'?((end.getUTCFullYear()-start.getUTCFullYear())*360+(end.getUTCMonth()-start.getUTCMonth())*30+Math.min(end.getUTCDate(),30)-Math.min(start.getUTCDate(),30))/360:calendarYears;
   factor=curve.compounding==='annual'?Math.pow(1+p.zero_rate,-t):curve.compounding==='simple'?1/(1+p.zero_rate*t):Math.exp(-p.zero_rate*t);
  }
  const rate=p.quoted_zero_rate??(factor>0?-Math.log(factor)/years:NaN);
  return {x:years,y:rate*100,date:p.date};
 }).filter(p=>p.x>0&&Number.isFinite(p.x)&&Number.isFinite(p.y)).sort((a,b)=>a.x-b.x);
 if(!points.length){container.textContent='No valid zero-rate points to plot. Check curve inputs.';return;}
 const ns='http://www.w3.org/2000/svg';
 const el=(name,attrs={},value)=>{const n=document.createElementNS(ns,name);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(value!==undefined)n.textContent=value;return n;};
 const svg=el('svg',{viewBox:'0 0 900 320',role:'img','aria-label':`${curve.id}, ${curve.as_of}: zero rate by maturity. Exact data follows in the table.`});
 svg.append(el('title',{},`${curve.id} · ${curve.currency} · ${curve.as_of}`));
 const lo=Math.min(...points.map(p=>p.y)),hi=Math.max(...points.map(p=>p.y)),pad=Math.max(.15,(hi-lo)*.15);
 const ymin=lo-pad,ymax=hi+pad,xmax=points.at(-1).x;
 const x=v=>68+v/xmax*802,y=v=>265-(v-ymin)/(ymax-ymin)*225;
 for(let i=0;i<=4;i++){const value=ymin+(ymax-ymin)*i/4;svg.append(el('line',{x1:68,x2:870,y1:y(value),y2:y(value),stroke:'#e1e6df'}),el('text',{x:54,y:y(value)+4,'text-anchor':'end',fill:'#607468','font-size':12},value.toFixed(2)+'%'));}
 const ticks=[0,...[1,2,5,10,15,20,30,40,50].filter(t=>t<=xmax)];
 if(xmax<1)ticks.push(xmax);
 for(const t of ticks)svg.append(el('text',{x:x(t),y:288,'text-anchor':'middle',fill:'#607468','font-size':12},Number(t.toFixed(2))+'Y'));
 svg.append(el('text',{x:870,y:22,'text-anchor':'end',fill:'#607468','font-size':12},'Continuous zero rate (%)'));
 svg.append(el('path',{d:points.map((p,i)=>(i?'L':'M')+x(p.x).toFixed(2)+','+y(p.y).toFixed(2)).join(' '),fill:'none',stroke:'#225848','stroke-width':3}));
 for(const p of points){const dot=el('circle',{cx:x(p.x),cy:y(p.y),r:4.5,fill:'#225848',stroke:'#fff','stroke-width':2});dot.append(el('title',{},`${p.x.toFixed(2)} years · ${p.date} · ${p.y.toFixed(6)}%`));svg.append(dot);}
 container.append(svg);
};

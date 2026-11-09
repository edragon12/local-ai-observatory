'use strict';
const $=id=>document.getElementById(id),el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
const bytes=n=>typeof n==='number'?(n/1024**3).toFixed(1)+' GiB':'Unavailable';
const IDLE='No tests yet.';
const HINT='Open a chat with a model in Ollama first, then refresh. No models are loaded automatically.';
const PROBE_NOTE='Diagnostic probe, not a benchmark. Fixed prompt, up to 128 output tokens, 4K context, temperature 0. Generated text is not retained.';
let busy=false,loading=false,latestHardware=null,latestResults=[];
const secs=(n,digits)=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(digits)+' s':'—';
function quantFor(name){
  const ollama=(latestHardware&&latestHardware.ollama)||{};
  const rows=[...(ollama.loaded||[]),...(ollama.installed||[])];
  const row=rows.find(x=>x&&x.name===name);
  return row&&row.quantization&&row.quantization!=='Unknown'?row.quantization:'unknown';
}
function expiryLabel(iso){if(!iso)return '—';const d=new Date(iso);return Number.isNaN(d.getTime())?'Unavailable':'until '+d.toLocaleString();}
function renderTokens(prefix,calls){
const valid=n=>Number.isInteger(n)&&n>=0;
const sum=key=>calls.length&&calls.every(x=>valid(x[key]))?calls.reduce((n,x)=>n+x[key],0):null;
const input=sum('input_tokens'),output=sum('output_tokens'),total=input!==null&&output!==null?input+output:null;
const fmt=n=>n===null?'Unavailable':n.toLocaleString();
$(prefix+'-tokens').replaceChildren(card('Input tokens',fmt(input),null,'Prompt + supplied context'),card('Output tokens',fmt(output),null,'Runtime-reported generation'),card('Combined tokens',fmt(total),null,calls.length+' recorded requests'));
table(prefix+'-token-calls',['Prompt preview (10 chars; oldest first)','Input','Output','Combined'],calls.map((x,i)=>[typeof x.prompt_preview==='string'&&x.prompt_preview?Array.from(x.prompt_preview).slice(0,10).join(''):'No preview',valid(x.input_tokens)?fmt(x.input_tokens):'Unavailable',valid(x.output_tokens)?fmt(x.output_tokens):'Unavailable',valid(x.input_tokens)&&valid(x.output_tokens)?fmt(x.input_tokens+x.output_tokens):'Unavailable']));
}
function table(id,headers,rows){const wrap=el('div'),t=el('table'),head=el('tr');wrap.className='table-wrap';headers.forEach(x=>head.append(el('th',x)));t.append(head);for(const row of rows){const tr=el('tr');row.forEach(x=>tr.append(el('td',String(x??'—'))));t.append(tr);}wrap.append(t);$(id).replaceChildren(wrap);}
function card(label,value,pct,note){const c=el('div');c.className='card';c.append(el('h3',label));if(Number.isFinite(pct)){const p=Math.min(100,Math.max(0,pct)),ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 100 100');svg.setAttribute('role','img');svg.setAttribute('aria-label',label+' '+p.toFixed(1)+' percent');for(const cls of ['track','fill']){const circle=document.createElementNS(ns,'circle');for(const [k,v] of Object.entries({cx:50,cy:50,r:40,class:cls}))circle.setAttribute(k,v);if(cls==='fill'){circle.setAttribute('pathLength','100');circle.setAttribute('stroke-dasharray',`${p} ${100-p}`);circle.setAttribute('transform','rotate(-90 50 50)');}svg.append(circle);}c.append(svg);}c.append(el('strong',value),el('p',note));return c;}
function platformNote(h){
  const gpu='GPU utilization is not collected; missing metrics are never shown as zero.';
  $('platform-note').textContent=h.unified_memory?'Apple Silicon shares memory between CPU and GPU. '+gpu:gpu;
}
function renderModels(h){
  const ollama=h.ollama||{installed:[],loaded:[],status:'Unavailable'};
  const installed=ollama.installed||[],loaded=ollama.loaded||[];
  const loadedBy=new Map(loaded.map(x=>[x.name,x]));
  const showVram=loaded.some(x=>Number.isFinite(x.vram)&&x.vram>0);
  const headers=['Model','State','Parameters','Quantization','Resident','Context','Keep-alive'];
  if(showVram)headers.push('GPU memory');
  const rowFor=(name,state,meta,live)=>{
    const row=[name,state,meta.parameters,meta.quantization,
      live&&typeof live.size==='number'?bytes(live.size):'—',
      live&&Number.isInteger(live.context_length)?live.context_length.toLocaleString():'—',
      live?expiryLabel(live.expires_at):'—'];
    if(showVram)row.push(live&&Number.isFinite(live.vram)?bytes(live.vram):'—');
    return row;
  };
  const rows=installed.map(x=>rowFor(x.name,loadedBy.has(x.name)?'Loaded':'Installed',x,loadedBy.get(x.name)));
  for(const live of loaded){if(!installed.some(x=>x.name===live.name))rows.push(rowFor(live.name,'Loaded',live,live));}
  table('models',headers,rows);
  return new Set(loaded.map(x=>x.name));
}
function renderHistory(){
  table('history',['Model','Quant','tok/s','First token','Load','Wall','Output tokens'],latestResults.slice().reverse().map(x=>[
    x.model,quantFor(x.model),x.tokens_per_second,secs(x.first_token_seconds,3),secs(x.load_seconds,3),secs(x.wall_seconds,3),x.output_tokens]));
  $('copy').disabled=!latestResults.length;
}
function summaryText(){
  const lines=['Local AI Observatory',PROBE_NOTE,'Machine: '+($('specs').textContent||''),'','Model | Quant | tok/s | First token | Load | Wall | Output tokens'];
  for(const x of latestResults.slice().reverse())lines.push([x.model,quantFor(x.model),x.tokens_per_second,secs(x.first_token_seconds,3),secs(x.load_seconds,3),secs(x.wall_seconds,3),x.output_tokens].join(' | '));
  return lines.join('\n');
}
async function copyResults(){
  if(!latestResults.length)return;
  const text=summaryText();
  let copied=false;
  try{if(navigator.clipboard&&navigator.clipboard.writeText){await navigator.clipboard.writeText(text);copied=true;}}catch{}
  if(!copied){const ta=el('textarea');ta.value=text;ta.setAttribute('readonly','');document.body.appendChild(ta);ta.select();try{copied=document.execCommand('copy');}catch{copied=false;}ta.remove();}
  $('probe-status').textContent=copied?'Results copied.':'Could not copy results.';
}
async function refresh(){if(loading)return;loading=true;try{const r=await fetch('/api/hardware');if(!r.ok)throw Error('Hardware unavailable');const h=await r.json(),m=h.memory,d=h.disk;latestHardware=h;$('specs').textContent=h.chip+' · '+bytes(m.total)+(h.unified_memory?' unified memory':' RAM')+' · '+h.os;$('status').textContent=''+new Date(h.sampled_at*1000).toLocaleString();$('cards').replaceChildren(card('CPU busy',h.cpu_percent.toFixed(1)+'%',h.cpu_percent,'Current host utilization'),card('Memory used',m.percent.toFixed(1)+'%',m.percent,bytes(m.available)+' available of '+bytes(m.total)),card('Storage used',(100*d.used/d.total).toFixed(1)+'%',100*d.used/d.total,bytes(d.free)+' free of '+bytes(d.total)),card('Memory pressure',m.pressure,null,'Swap used: '+bytes(m.swap_used)));$('memory-note').textContent='Compressed memory: '+bytes(m.compressed)+'. Available memory is an OS estimate, not guaranteed model capacity.';platformNote(h);$('runtime').textContent='Ollama: '+h.ollama.status;const loaded=renderModels(h);const previous=$('model').value;$('model').replaceChildren();for(const name of loaded){const o=el('option',name);o.value=name;$('model').append(o);}if(loaded.has(previous))$('model').value=previous;$('run').disabled=busy||!loaded.size;const status=$('probe-status');if(!loaded.size&&!busy&&status.textContent===IDLE)status.textContent=HINT;if(loaded.size&&status.textContent===HINT)status.textContent=IDLE;renderHistory();}catch(e){$('status').textContent='Telemetry unavailable or stale: '+e.message;}finally{loading=false;}}
async function results(){const r=await fetch('/api/performance');const data=await r.json(),last=data.results.at(-1);latestResults=Array.isArray(data.results)?data.results:[];renderTokens('mac',latestResults.map(x=>({prompt_preview:'Explain in',input_tokens:x.prompt_tokens,output_tokens:x.output_tokens})));if(last){$('results').replaceChildren(card('Generation',last.tokens_per_second+' tok/s',null,'Runtime generation duration'),card('First output',last.first_token_seconds===null?'Unavailable':last.first_token_seconds.toFixed(2)+' s',null,'Includes request and load time'),card('Total',last.wall_seconds.toFixed(2)+' s',null,last.output_tokens+' output tokens'),card('Load time',secs(last.load_seconds,3),null,last.model));}renderHistory();}
$('refresh').onclick=refresh;$('copy').onclick=copyResults;$('run').onclick=async()=>{if(busy)return;busy=true;$('run').disabled=true;$('probe-status').textContent='Testing locally… allow up to 90 seconds.';try{const r=await fetch('/api/performance?model='+encodeURIComponent($('model').value),{method:'POST'});const data=await r.json();if(!r.ok)throw Error(data.error||'Test could not complete.');await results();$('probe-status').textContent='Local test complete. No prompt or generated text retained.';}catch(e){$('probe-status').textContent=e.message;}finally{busy=false;await refresh();}};
function setTheme(theme){document.documentElement.dataset.theme=theme;$('theme').textContent=theme==='light'?'Dark theme':'Light theme';try{localStorage.setItem('observatory-theme',theme);}catch{}}
let savedTheme='dark';try{savedTheme=localStorage.getItem('observatory-theme')||'dark';}catch{}setTheme(savedTheme==='light'?'light':'dark');$('theme').onclick=()=>setTheme(document.documentElement.dataset.theme==='light'?'dark':'light');
setInterval(()=>{if(!document.hidden&&!busy)refresh();},10000);refresh();results().catch(()=>{$('probe-status').textContent='History unavailable';});

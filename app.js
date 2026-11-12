'use strict';
const bytes=n=>typeof n==='number'&&Number.isFinite(n)?(n/1024**3).toFixed(1)+' GiB':'Unavailable';
const secs=(n,digits)=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(digits)+' s':'—';
const num=n=>typeof n==='number'&&Number.isFinite(n);
const IDLE='No tests yet.';
const PROBE_NOTE='Diagnostic probe, not a benchmark. Fixed prompt, up to 128 output tokens, 4K context, temperature 0. Generated text is not retained.';
const HISTORY_HEADERS=['Model','Quant','Gen tok/s','Prompt tok/s','First token','Load','Wall','Output tokens','Measured'];
const RUN_FIELDS=['model','sampled_at','tokens_per_second','prompt_tokens_per_second','prompt_eval_seconds','eval_seconds','first_token_seconds','load_seconds','wall_seconds','output_tokens','prompt_tokens'];
const RUN_CAP=30;
const TK_SAMPLE='Explain in plain language how a computer processes a request.\nWrite about 150 words — keep it friendly.\n\tcount = 12345 tokens?';

function expiryLabel(iso){if(!iso)return '—';const d=new Date(iso);return Number.isNaN(d.getTime())?'Unavailable':'until '+d.toLocaleString();}
function classifyRuntime(runtime){
  const installed=(runtime&&Array.isArray(runtime.installed)?runtime.installed:[]).filter(x=>x&&x.name);
  const loaded=(runtime&&Array.isArray(runtime.loaded)?runtime.loaded:[]).filter(x=>x&&x.name);
  const status=(runtime&&runtime.status)||'';
  let state='available';
  if(/unavailable/i.test(status)&&!loaded.length&&!installed.length)state='unreachable';
  else if(/partial|unavailable/i.test(status))state='partial';
  return {installed,loaded,state,status};
}
function runtimeLine(label,runtime){
  const c=classifyRuntime(runtime);
  const word=c.state==='unreachable'?'Unreachable':c.state==='partial'?'Partial':'Available';
  return label+' · '+word+(c.status?' · '+c.status:'');
}
function modelRows(runtime,opts){
  opts=opts||{};
  const c=classifyRuntime(runtime);
  const loadedBy=new Map(c.loaded.map(x=>[x.name,x]));
  const showVram=c.loaded.some(x=>num(x.vram)&&x.vram>0);
  const headers=['Model','State','Parameters','Quantization','Resident','Context'];
  if(!opts.post)headers.push('Keep-alive');
  if(showVram)headers.push('GPU memory');
  const rowFor=(name,state,meta,live)=>{
    const row=[name,state,(meta&&meta.parameters)||'—',(meta&&meta.quantization)||'—',
      live&&num(live.size)?bytes(live.size):'—',
      live&&Number.isInteger(live.context_length)?live.context_length.toLocaleString():(live&&Number.isInteger(live.context)?live.context.toLocaleString():'—')];
    if(!opts.post)row.push(live?expiryLabel(live.expires_at):'—');
    if(showVram)row.push(live&&num(live.vram)?bytes(live.vram):'—');
    return row;
  };
  const rows=c.installed.map(x=>rowFor(x.name,loadedBy.has(x.name)?'Loaded · generation unknown':'Installed',x,loadedBy.get(x.name)));
  for(const live of c.loaded){if(!c.installed.some(x=>x.name===live.name))rows.push(rowFor(live.name,'Loaded · not in installed list',live,live));}
  return {headers,rows,loadedNames:c.loaded.map(x=>x.name)};
}
function quantFor(runtime,name){
  const c=classifyRuntime(runtime);
  const row=[...c.loaded,...c.installed].find(x=>x.name===name);
  return row&&row.quantization&&row.quantization!=='Unknown'?row.quantization:'unknown';
}
function historyRows(results,runtime){
  return results.slice().reverse().map(x=>[x.model,quantFor(runtime,x.model),x.tokens_per_second,num(x.prompt_tokens_per_second)?x.prompt_tokens_per_second:'—',secs(x.first_token_seconds,3),secs(x.load_seconds,3),secs(x.wall_seconds,3),x.output_tokens,typeof x.sampled_at==='number'?new Date(x.sampled_at*1000).toLocaleTimeString():'—']);
}
function summaryText(label,specs,results,runtime){
  const lines=['Local AI Observatory','Host: '+label,PROBE_NOTE,'Machine: '+(specs||'Unavailable'),'',HISTORY_HEADERS.join(' | ')];
  for(const row of historyRows(results,runtime))lines.push(row.join(' | '));
  return lines.join('\n');
}
function tokenTotals(calls,opts){
  const valid=n=>Number.isInteger(n)&&n>=0;
  const sum=key=>calls.length&&calls.every(x=>valid(x[key]))?calls.reduce((n,x)=>n+x[key],0):null;
  const input=sum('input_tokens'),output=sum('output_tokens'),total=input!==null&&output!==null?input+output:null;
  const fmt=n=>n===null?'Unavailable':n.toLocaleString();
  const rows=(calls||[]).map((x,i)=>{
    const preview=opts&&opts.post?'#'+(i+1):(typeof x.prompt_preview==='string'&&x.prompt_preview?Array.from(x.prompt_preview).slice(0,10).join(''):'No preview');
    return [preview,valid(x.input_tokens)?fmt(x.input_tokens):'Unavailable',valid(x.output_tokens)?fmt(x.output_tokens):'Unavailable',valid(x.input_tokens)&&valid(x.output_tokens)?fmt(x.input_tokens+x.output_tokens):'Unavailable'];
  });
  return {input,output,total,rows,headers:[(opts&&opts.post)?'Request (previews hidden in post mode)':'Prompt preview (10 chars; oldest first)','Input','Output','Combined']};
}
function gpuThermalCards(g){
  const out=[];if(!g)return out;
  if(num(g.temperature)&&g.temperature>0)out.push({label:'GPU temperature',value:Math.round(g.temperature)+' °C',pct:null,note:'NVIDIA device · reported by nvidia-smi'});
  if(num(g.power_draw)){const lim=num(g.power_limit)&&g.power_limit>0?g.power_limit:null;
    out.push({label:'GPU power draw',value:g.power_draw.toFixed(1)+' W',pct:lim?100*g.power_draw/lim:null,note:lim?'of '+lim.toFixed(0)+' W power limit':'Power limit not reported by this GPU'});}
  return out;
}
function memoryFit(hw){
  const c=classifyRuntime(hw&&hw.runtime),unified=!!(hw&&(hw.unified||hw.unified_memory));
  const g=hw&&(hw.gpu||(Array.isArray(hw.gpus)?hw.gpus[0]:null));
  const memTotal=hw&&(num(hw.memTotal)?hw.memTotal:(hw.memory&&num(hw.memory.total)?hw.memory.total:null));
  const models=c.loaded.filter(x=>x&&x.name).map(x=>{
    const size=num(x.size)&&x.size>0?x.size:null;
    const onGpu=unified?(num(x.gpu_resident)?x.gpu_resident:null):(num(x.vram)?x.vram:(g?0:null));
    let verdict,kind,gpuPct=null,spill=null;
    if(size===null){verdict='Resident size not reported';kind='unknown';}
    else if(onGpu===null){verdict=unified?'GPU share not reported by Ollama':'No GPU reported · runs on CPU/RAM';kind=unified?'unknown':'cpu';}
    else{gpuPct=Math.min(100,100*onGpu/size);spill=Math.max(0,size-onGpu);
      if(onGpu>=size*0.999){verdict=unified?'Fully GPU-mapped (Metal) in unified memory':'Fits fully in VRAM';kind='fits';spill=0;gpuPct=100;}
      else if(onGpu<=0){verdict=unified?'Running on CPU (no GPU-mapped share)':'Runs on CPU/RAM only (0 in VRAM)';kind='cpu';}
      else{verdict=(unified?'Partly on CPU · ':'Spills ')+bytes(spill)+(unified?' not GPU-mapped':' to CPU/RAM')+' ('+gpuPct.toFixed(0)+'% on GPU)';kind='spill';}}
    return {name:x.name,size,onGpu,spill,gpuPct,verdict,kind};
  });
  let pool=null;
  if(unified&&num(memTotal))pool={label:'Unified memory',total:memTotal,used:models.reduce((n,m)=>n+(m.size||0),0),usedLabel:'resident in loaded models'};
  else if(g&&num(g.total)&&g.total>0&&num(g.used))pool={label:'VRAM',total:g.total,used:g.used,usedLabel:'in use on '+(g.name||'GPU')};
  return {unified,models,pool};
}
function speedPair(r){
  if(!r)return null;
  const gen=num(r.tokens_per_second)?r.tokens_per_second:null,prompt=num(r.prompt_tokens_per_second)?r.prompt_tokens_per_second:null;
  return {gen,prompt,ratio:gen&&prompt?prompt/gen:null,promptTokens:Number.isInteger(r.prompt_tokens)?r.prompt_tokens:null,
    promptSeconds:num(r.prompt_eval_seconds)?r.prompt_eval_seconds:null,outputTokens:Number.isInteger(r.output_tokens)?r.output_tokens:null,
    genSeconds:num(r.eval_seconds)?r.eval_seconds:null};
}
function mergeRuns(stored,incoming){
  const byKey=new Map();
  for(const r of [...(Array.isArray(stored)?stored:[]),...(Array.isArray(incoming)?incoming:[])]){
    if(!r||typeof r.model!=='string'||!num(r.sampled_at)||!num(r.tokens_per_second))continue;
    const clean={};for(const k of RUN_FIELDS)clean[k]=k==='model'?r.model.slice(0,200):(num(r[k])?r[k]:null);
    byKey.set(clean.model+'@'+clean.sampled_at.toFixed(3),clean);
  }
  return [...byKey.values()].sort((a,b)=>a.sampled_at-b.sampled_at).slice(-RUN_CAP);
}
function sparkGeometry(values,w,h,pad){
  pad=pad===undefined?6:pad;const v=(values||[]).filter(num);if(!v.length)return {points:[],min:null,max:null};
  const min=Math.min(...v),max=Math.max(...v),span=max-min||1;
  const step=v.length>1?(w-2*pad)/(v.length-1):0;
  return {min,max,points:v.map((y,i)=>[+(pad+(v.length>1?i*step:(w-2*pad)/2)).toFixed(1),+(h-pad-(max===min?(h-2*pad)/2:(y-min)/span*(h-2*pad))).toFixed(1)])};
}
function comparePick(localRuns,remoteRuns){
  const local=Array.isArray(localRuns)?localRuns:[],remote=Array.isArray(remoteRuns)?remoteRuns:[];
  for(let i=remote.length-1;i>=0;i--){const same=local.filter(r=>r.model===remote[i].model);if(same.length)return {local:same[same.length-1],remote:remote[i],sameModel:true};}
  return {local:local.length?local[local.length-1]:null,remote:remote.length?remote[remote.length-1]:null,sameModel:false};
}
function compareSummary(pick,labels,remoteEnabled){
  labels=labels||{local:'This computer',remote:'Remote host'};
  if(remoteEnabled===false)return {kind:'unconfigured',text:'Remote host is optional and not configured. Set one up to compare the same probe on two machines.'};
  if(!pick.local&&!pick.remote)return {kind:'empty',text:'No tests yet on either host. Run a test on '+labels.local+' and on '+labels.remote+' to fill this row.'};
  if(!pick.local||!pick.remote)return {kind:'partial',text:'Only '+(pick.local?labels.local:labels.remote)+' has a test so far. Run one on '+(pick.local?labels.remote:labels.local)+' to compare.'};
  if(!pick.sameModel)return {kind:'different',text:'DIFFERENT MODELS · not like-for-like: '+labels.local+' ran '+pick.local.model+', '+labels.remote+' ran '+pick.remote.model+'. Load the same model on both hosts for a fair comparison.'};
  const a=pick.local.tokens_per_second,b=pick.remote.tokens_per_second;
  const lead=a>0&&b>0?(b>=a?labels.remote+' generated '+(b/a).toFixed(1)+'× faster':labels.local+' generated '+(a/b).toFixed(1)+'× faster'):'';
  return {kind:'same',text:'Same model on both hosts: '+pick.local.model+'.'+(lead?' '+lead+' on this probe.':'')};
}
function compareRows(pick,labels,remoteEnabled){
  labels=labels||{local:'This computer',remote:'Remote host'};
  const row=(label,r)=>r?[label,r.model,r.tokens_per_second,num(r.prompt_tokens_per_second)?r.prompt_tokens_per_second:'—',secs(r.first_token_seconds,2),secs(r.load_seconds,3),new Date(r.sampled_at*1000).toLocaleString()]:[label,'No test yet','—','—','—','—','—'];
  const remoteRow=remoteEnabled===false?[labels.remote,'Not configured','—','—','—','—','—']:row(labels.remote,pick.remote);
  return {headers:['Host','Model','Gen tok/s','Prompt tok/s','First token','Load','Measured'],rows:[row(labels.local,pick.local),remoteRow]};
}
function visibleWS(piece){return String(piece).replace(/ /g,'·').replace(/\t/g,'→').replace(/\r/g,'␍').replace(/\n/g,'↵\n');}
function pickContext(ctx){
  ctx=ctx||{};
  if(num(ctx.loaded))return {n:ctx.loaded,label:'loaded num_ctx',note:'Current context of the loaded model (Ollama /api/ps).'};
  if(num(ctx.modelfile))return {n:ctx.modelfile,label:'Modelfile num_ctx',note:'Not loaded. The Modelfile sets num_ctx '+ctx.modelfile.toLocaleString()+', which applies when it loads.'};
  if(num(ctx.trained_max))return {n:ctx.trained_max,label:'model maximum',note:'Not loaded. Ollama picks num_ctx at load time, capped at this model maximum of '+ctx.trained_max.toLocaleString()+'. The dashboard probe uses '+(ctx.probe||4096).toLocaleString()+'.'};
  return {n:null,label:'unknown',note:'Context length not reported.'};
}
function tokenStats(res){
  const c=pickContext(res&&res.context),count=res&&Number.isInteger(res.count)?res.count:null;
  return {count,cpt:count?res.chars/count:null,ctx:c,pct:count!==null&&c.n?100*count/c.n:null};
}
function tokenChunks(res){const sp=new Set((res&&res.special_ids)||[]);return ((res&&res.tokens)||[]).map((t,i)=>({id:t[0],text:t[1]===''?'◌':visibleWS(t[1]),raw:t[1],partial:t[1]==='',special:sp.has(t[0]),shade:i%6}));}
function compareLine(a,b){
  if(!a||!b||!a.count||!b.count)return '';
  if(a.count===b.count)return 'Both models use '+a.count+' tokens for this text.';
  const lo=a.count<b.count?a:b,hi=a.count<b.count?b:a;
  return lo.model+' needs '+(hi.count-lo.count)+' fewer tokens than '+hi.model+' ('+(100*(hi.count-lo.count)/hi.count).toFixed(0)+'% fewer) for the same text.';
}
function familyOf(name){const head=String(name).split(':')[0];const cut=head.split(/[0-9.]/)[0];return cut||head||name;}
function livePlacement(route,pl){
  if(route==='external')return {kind:'external',label:'EXTERNAL',text:'External route · not running on this GPU'};
  if(!pl||pl.loaded===null||pl.loaded===undefined)return {kind:'unknown',label:'UNKNOWN',text:'Placement unavailable'};
  if(pl.loaded===false)return {kind:'unknown',label:'NOT LOADED',text:'Model not resident right now'};
  if(!num(pl.size)||pl.size<=0||!num(pl.vram))return {kind:'unknown',label:'UNKNOWN',text:'Resident size not reported'};
  const pct=Math.min(100,100*pl.vram/pl.size),spill=Math.max(0,pl.size-pl.vram);
  if(pl.vram>=pl.size*0.999)return {kind:'fits',label:'GPU · FITS',text:bytes(pl.size)+' fully in VRAM'+(pl.context?' · '+pl.context.toLocaleString()+' ctx':'')};
  if(pl.vram<=0)return {kind:'cpu',label:'CPU',text:'Runs on CPU/RAM only'};
  return {kind:'spill',label:'SPILLS',text:bytes(spill)+' on CPU/RAM ('+pct.toFixed(0)+'% on GPU)'+(pl.context?' · '+pl.context.toLocaleString()+' ctx':'')};
}
function liveStateBadge(d){
  if(!d)return {kind:'offline',text:'IDLE'};
  if(d.state==='streaming')return {kind:'live',text:'STREAMING'};
  if(d.state==='waiting')return {kind:'stale',text:'WAITING FOR FIRST TOKEN'};
  if(d.state==='done')return {kind:'live',text:'DONE'};
  return {kind:'offline',text:'IDLE'};
}
function liveTapMessage(d){
  if(!d)return '';
  const t=d.tap||{};
  if(!t.listening)return 'Live tap is off. It listens only after you create a token file (see the README). Prompt tokens still work without it.';
  if(!t.events)return 'Ready. Waiting for the Open WebUI filter to send its first chat turn.';
  if(d.state==='idle')return 'Ask something in Open WebUI and it will appear here as it streams. Turns leave memory after 10 minutes.';
  return '';
}
function liveCards(d){
  const reply=(d&&d.reply)||{},prompt=(d&&d.prompt)||{},ctx=(d&&d.context)||{};
  const pctx=pickContext(reply.context||prompt.context||{});
  const pct=pctx.n&&num(ctx.count)?100*ctx.count/pctx.n:null;
  const fixed=n=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(2):'—';
  return [
    {label:'First token',value:num(d&&d.first_token_seconds)?fixed(d.first_token_seconds)+' s':'—',note:'From send until the first reply content. A leading route label is not timed.'},
    {label:'Live tok/s',value:num(d&&d.tokens_per_second)?(d.tokens_per_second).toFixed(1):'—',note:d&&d.state==='done'?'Average over the whole reply':'Generated tokens per second so far'},
    {label:'Prompt tokens',value:num(prompt.count)?prompt.count.toLocaleString():'—',note:'Your message · whole conversation '+(num(ctx.count)?ctx.count.toLocaleString():'—')},
    {label:'Generated tokens',value:num(reply.count)?reply.count.toLocaleString():'—',note:num(d&&d.elapsed_seconds)?d.elapsed_seconds.toFixed(1)+' s elapsed':''},
    {label:'Context used',value:pct===null?'—':(pct<0.1&&pct>0?'<0.1':pct.toFixed(pct<10?2:1))+'%',pct,note:pctx.n?'conversation of '+pctx.n.toLocaleString()+' ('+pctx.label+')':'Context unknown'}];
}
function liveChunks(part){
  if(!part)return [];
  if(Array.isArray(part.tokens)&&part.tokens.length)return tokenChunks(part);
  if(typeof part.text==='string'&&part.text)return [{id:null,text:visibleWS(part.text),special:false,partial:false,redacted:false,shade:0}];
  if(Array.isArray(part.lengths))return part.lengths.map((r,i)=>({id:null,text:'█'.repeat(Math.max(1,Math.min(r[0],14))),special:r[1]===1,partial:false,redacted:true,shade:i%6}));
  return [];
}
function localSharePct(local,external){
  const l=Number(local),e=Number(external);
  if(!Number.isFinite(l)||!Number.isFinite(e)||l<0||e<0)return null;
  const tot=l+e;if(!tot)return null;
  return Math.round((l/tot)*1000)/10;
}
function formatShare(pct){return pct===null||pct===undefined?'—':((Number.isInteger(pct)?String(pct):pct.toFixed(1))+'%');}
function parseLogDate(s){
  if(!s||typeof s!=='string')return null;
  const m=s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if(!m)return null;
  const d=new Date(+m[1],+m[2]-1,+m[3],+m[4],+m[5]);
  return Number.isFinite(d.getTime())?d:null;
}
function compactLogTime(s){const d=parseLogDate(s);return d?d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):(s||'Unavailable');}
function routingWindowText(first,last,compact){
  if(!first&&!last)return 'Retained log window unavailable';
  if(!compact)return 'Retained '+(first||'Unavailable')+' → '+(last||'Unavailable');
  const ta=parseLogDate(first),tb=parseLogDate(last);
  if(ta&&tb&&ta.getFullYear()===tb.getFullYear()&&ta.getMonth()===tb.getMonth()&&ta.getDate()===tb.getDate()){
    const day=ta.toLocaleString(undefined,{month:'short',day:'numeric'}),opt={hour:'numeric',minute:'2-digit'};
    return day+', '+ta.toLocaleTimeString(undefined,opt)+'–'+tb.toLocaleTimeString(undefined,opt);
  }
  return compactLogTime(first||'')+' → '+compactLogTime(last||'');
}
function routingGlance(route){
  const counts=(route&&route.all)||{};
  const share=localSharePct(counts.local,counts.external);
  const shown=v=>(typeof v==='number'&&Number.isFinite(v))||(typeof v==='string'&&v!==''&&Number.isFinite(Number(v)))?String(Number(v)):'Unavailable';
  return {window:routingWindowText(route&&route.first_time,route&&route.last_time,true),
    windowTitle:routingWindowText(route&&route.first_time,route&&route.last_time,false),
    tiles:[{label:'Local',value:shown(counts.local)},{label:'External',value:shown(counts.external)},{label:'Unknown',value:shown(counts.unknown)},
      {label:'Local share',value:formatShare(share),pct:share,title:'Local share is local ÷ (local + external).'}]};
}

if(typeof document!=='undefined'&&document.getElementById('host-core')){
const $=id=>document.getElementById(id);
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
function table(target,headers,rows){const wrap=el('div'),t=el('table'),head=el('tr');wrap.className='table-wrap';headers.forEach(x=>head.append(el('th',x)));t.append(head);for(const row of rows){const tr=el('tr');row.forEach(x=>tr.append(el('td',String(x??'—'))));t.append(tr);}wrap.append(t);target.replaceChildren(wrap);}
function card(label,value,pct,note){const c=el('div');c.className='card';c.append(el('h3',label));if(Number.isFinite(pct)){const p=Math.min(100,Math.max(0,pct)),ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 100 100');svg.setAttribute('role','img');svg.setAttribute('aria-label',label+' '+p.toFixed(1)+' percent');for(const cls of ['track','fill']){const circle=document.createElementNS(ns,'circle');for(const [k,v] of Object.entries({cx:50,cy:50,r:40,class:cls}))circle.setAttribute(k,v);if(cls==='fill'){circle.setAttribute('pathLength','100');circle.setAttribute('stroke-dasharray',`${p} ${100-p}`);circle.setAttribute('transform','rotate(-90 50 50)');}svg.append(circle);}c.append(svg);}c.append(el('strong',value),el('p',note||''));return c;}
function glanceTile(label,value,pct,note,title){const c=el('div');c.className='card glance-tile';if(title)c.title=title;if(Number.isFinite(pct)){const p=Math.min(100,Math.max(0,pct)),ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 100 100');svg.setAttribute('role','img');svg.setAttribute('aria-label',label+' '+p.toFixed(1)+' percent');for(const cls of ['track','fill']){const circle=document.createElementNS(ns,'circle');for(const [k,v] of Object.entries({cx:50,cy:50,r:40,class:cls}))circle.setAttribute(k,v);if(cls==='fill'){circle.setAttribute('pathLength','100');circle.setAttribute('stroke-dasharray',`${p} ${100-p}`);circle.setAttribute('transform','rotate(-90 50 50)');}svg.append(circle);}c.append(svg);}const copy=el('div');copy.className='glance-copy';copy.append(el('strong',value),el('h3',label));if(note)copy.append(el('p',note));c.append(copy);return c;}
const badge=(kind,text)=>{const b=el('span',text);b.className='badge '+kind;return b;};
const DEFS={
  local:{hardwareUrl:'/api/hardware',historyUrl:'/api/performance',runUrl:'/api/performance',tokenizeUrl:'/api/tokenize',liveUrl:'/api/live',runLabel:'Run local test',running:'Testing locally… allow up to 90 seconds.',done:'Local test complete. No prompt or generated text retained.',empty:'No model is loaded. Open a chat with a model in Ollama first, then refresh. Nothing is downloaded or loaded automatically.',modelsTitle:'Local models',modelsNote:'Loaded means resident, not necessarily generating. Resident size, context and keep-alive come from the running model. Ollama only. No automatic downloads or model loading.'},
  remote:{hardwareUrl:'/api/remote/hardware',historyUrl:'/api/remote/performance',runUrl:'/api/remote/performance',tokenizeUrl:'/api/remote/tokenize',liveUrl:'/api/remote/live',runLabel:'Run test on remote host',running:'Testing on the remote host… allow up to 2 minutes. Timing is measured there.',done:'Remote test complete. Measured on the remote host. No prompt or generated text retained.',empty:'No model is loaded on the remote host. Load one there yourself, then refresh. This dashboard never downloads or loads models.',modelsTitle:'Models · remote host',modelsNote:'Read from the remote dashboard. Loaded means resident, not necessarily generating. No automatic downloads or model loading.'}
};
const RUNS_KEY='observatory-runs-v1-',POST_KEY='observatory-post-mode',TK_TEXT_KEY='observatory-prompt-text';
const views={};
let postMode=false,host='local',remoteEnabled=false;
let labels={local:'This computer',remote:'Remote host'};
function activeLabels(){return postMode?{local:'This computer',remote:'Remote host'}:labels;}
function loadRuns(key){try{return mergeRuns(JSON.parse(localStorage.getItem(RUNS_KEY+key)||'[]'),[]);}catch{return [];}}
function saveRuns(v){try{localStorage.setItem(RUNS_KEY+v.key,JSON.stringify(v.runs));}catch{}}
for(const key of ['local','remote']){
  const root=$(key+'-view'),slot=root.querySelector('[data-slot=core]');
  slot.append($('host-core').content.cloneNode(true));
  const q=role=>slot.querySelector('[data-role="'+role+'"]');
  const part=name=>slot.querySelector('[data-part="'+name+'"]');
  const cfg=DEFS[key];
  q('models-title').textContent=cfg.modelsTitle;
  q('models-note').textContent=cfg.modelsNote;
  q('run').textContent=cfg.runLabel;
  q('overview-tag').textContent=key==='local'?'THIS COMPUTER':'REMOTE HOST';
  q('lv-title').textContent='Live from Open WebUI · '+(key==='local'?'this computer':'remote host');
  q('tk-title').textContent='Prompt tokens · '+(key==='local'?'this computer':'remote host');
  views[key]={key,cfg,q,part,hardware:null,results:[],runs:loadRuns(key),busy:false,loading:false,stale:false,lastOk:null,tkBusy:false,tkResults:[],liveData:null,liveTimer:null,liveShown:null,routeHistory:[],routeSource:null,routeExample:null,routeTimer:null,routeRaf:0,routeLive:null};
}
function fitBar(pool){
  const bar=el('div');bar.className='fitbar';bar.setAttribute('role','img');
  const pct=pool.total?Math.min(100,Math.max(0,100*pool.used/pool.total)):0;
  bar.setAttribute('aria-label',pool.label+' '+pct.toFixed(0)+' percent');
  const fill=el('span');fill.className='fitbar-used';fill.style.width=pct+'%';bar.append(fill);return bar;
}
function renderFit(v){
  const h=v.hardware||{},box=v.q('fit');
  const f=memoryFit({unified:!!h.unified_memory,gpu:(h.gpus||[])[0]||null,memTotal:h.memory&&h.memory.total,runtime:h.ollama||{}});
  v.q('fit-title').textContent=f.unified?'Memory fit · unified memory':'Memory fit · VRAM vs CPU/RAM';
  box.replaceChildren();
  if(f.pool){const p=el('div');p.className='fit-pool';p.append(el('strong',f.pool.label+': '+bytes(f.pool.used)+' of '+bytes(f.pool.total)),el('span',' '+f.pool.usedLabel),fitBar(f.pool));box.append(p);}
  for(const m of f.models){const r=el('div');r.className='fit-row fit-'+m.kind;const b=el('span',m.kind==='fits'?'FITS':m.kind==='spill'?'SPILLS':m.kind==='cpu'?'CPU':'UNKNOWN');b.className='badge fit-badge';
    r.append(el('strong',m.name),b,el('p',(m.size!==null?bytes(m.size)+' resident · ':'')+m.verdict));
    if(m.size!==null&&m.gpuPct!==null){const seg=el('div');seg.className='fitbar split';const a=el('span');a.className='fitbar-gpu';a.style.width=m.gpuPct+'%';const c=el('span');c.className='fitbar-cpu';c.style.width=(100-m.gpuPct)+'%';seg.append(a,c);seg.setAttribute('role','img');seg.setAttribute('aria-label',m.name+' '+m.gpuPct.toFixed(0)+' percent on GPU');r.append(seg);}
    box.append(r);}
  if(!f.models.length){const e=el('p','No model is loaded, so there is nothing to fit yet. Load one yourself and refresh. This dashboard never loads models.');e.className='note';box.append(e);}
  v.q('fit-note').textContent=f.unified?'Unified memory is one pool. The GPU-mapped share is part of the same resident memory, not separate VRAM. Below 100% means some layers run on the CPU.':'From Ollama: size is the resident footprint and VRAM is the part on the GPU. The rest runs from system RAM on the CPU.';
}
function renderSpeeds(v){
  const pair=speedPair(v.results.at(-1)),box=v.q('speeds');box.replaceChildren();if(!pair)return;
  const max=Math.max(pair.gen||0,pair.prompt||0,1);
  const block=(cls,label,value,note,width)=>{const s=el('div');s.className='speed '+cls;s.append(el('h3',label),el('strong',value),el('p',note));if(width!==null){const bar=el('div');bar.className='speedbar';const fill=el('span');fill.style.width=width+'%';bar.append(fill);s.append(bar);}return s;};
  box.append(block('prompt','Prompt processing',pair.prompt===null?'—':pair.prompt+' tok/s',pair.promptTokens!==null?pair.promptTokens+' prompt tokens'+(pair.promptSeconds!==null?' · '+pair.promptSeconds.toFixed(2)+' s':''):'Prompt timing not reported',pair.prompt===null?null:100*pair.prompt/max));
  box.append(block('','Generation',pair.gen===null?'—':pair.gen+' tok/s',pair.outputTokens!==null?pair.outputTokens+' output tokens'+(pair.genSeconds!==null?' · '+pair.genSeconds.toFixed(2)+' s':''):'Generation timing not reported',pair.gen===null?null:100*pair.gen/max));
  if(pair.ratio){const note=el('p','Prompt processing was '+pair.ratio.toFixed(1)+'× the generation speed on this probe.');note.className='note speed-ratio';box.append(note);}
}
function renderSpark(v){
  const runs=v.runs,box=v.q('spark');box.replaceChildren();
  if(!runs.length){v.q('spark-meta').textContent='';v.q('spark-note').textContent='No test runs yet. The sparkline fills in after each test (last '+RUN_CAP+' runs, kept in this browser only).';box.hidden=true;return;}
  box.hidden=false;
  const W=600,H=80,ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),g=sparkGeometry(runs.map(r=>r.tokens_per_second),W,H);
  svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('role','img');svg.setAttribute('aria-label','Generation tokens per second over '+runs.length+' runs');
  if(g.points.length>1){const line=document.createElementNS(ns,'polyline');line.setAttribute('points',g.points.map(p=>p.join(',')).join(' '));line.setAttribute('class','spark-line');svg.append(line);}
  g.points.forEach((p,i)=>{const c=document.createElementNS(ns,'circle');c.setAttribute('cx',p[0]);c.setAttribute('cy',p[1]);c.setAttribute('r',i===g.points.length-1?3.5:2.2);c.setAttribute('class',i===g.points.length-1?'spark-last':'spark-dot');svg.append(c);});
  box.append(svg);
  const latest=runs[runs.length-1],models=[...new Set(runs.map(r=>r.model))];
  v.q('spark-meta').textContent=runs.length+' run'+(runs.length===1?'':'s')+' · latest '+latest.tokens_per_second+' tok/s · range '+g.min+'–'+g.max;
  v.q('spark-note').textContent=(runs.length===1?'One run so far; the line appears after the next test. ':'')+(models.length>1?'Mixed models: '+models.join(', ')+'. ':'Model: '+models[0]+'. ')+'Kept in this browser only (last '+RUN_CAP+').';
}
function renderCompare(){
  const names=activeLabels();
  const pick=comparePick(views.local.runs,views.remote.runs);
  const sum=compareSummary(pick,names,remoteEnabled);
  const rows=compareRows(pick,names,remoteEnabled);
  for(const v of Object.values(views)){
    v.q('compare-label').textContent=sum.text;
    if(sum.kind==='empty')v.q('compare').replaceChildren();
    else table(v.q('compare'),rows.headers,rows.rows);
  }
}
function renderOverview(v){
  const h=v.hardware;if(!h)return;
  const m=h.memory||{},d=h.disk||{};
  v.q('specs').textContent=(h.chip||'Unknown')+' · '+bytes(m.total)+(h.unified_memory?' unified memory':' RAM')+(h.os?' · '+h.os:'');
  v.q('status').textContent=h.sampled_at?new Date(h.sampled_at*1000).toLocaleString():'';
  const cards=[card('CPU busy',num(h.cpu_percent)?h.cpu_percent.toFixed(1)+'%':'Unavailable',num(h.cpu_percent)?h.cpu_percent:null,'Current host utilization'),
    card(h.unified_memory?'Memory used':'RAM used',num(m.percent)?m.percent.toFixed(1)+'%':'Unavailable',num(m.percent)?m.percent:null,bytes(m.available)+' available of '+bytes(m.total))];
  const g=(h.gpus||[])[0];
  if(g&&!h.unified_memory&&num(g.percent))cards.push(card('GPU busy',g.percent.toFixed(1)+'%',g.percent,(g.name||'GPU')+' · not aggregated'));
  if(g&&!h.unified_memory&&num(g.total)&&g.total>0&&num(g.used))cards.push(card('VRAM used',(100*g.used/g.total).toFixed(1)+'%',100*g.used/g.total,bytes(g.used)+' of '+bytes(g.total)));
  if(num(d.total)&&d.total>0)cards.push(card('Storage used',(100*d.used/d.total).toFixed(1)+'%',100*d.used/d.total,bytes(d.free)+' free of '+bytes(d.total)));
  cards.push(card('Memory pressure',m.pressure||'Unavailable',null,'Swap used: '+bytes(m.swap_used)));
  for(const extra of gpuThermalCards(h.unified_memory?null:g))cards.push(card(extra.label,extra.value,extra.pct,extra.note));
  v.q('cards').replaceChildren(...cards);
  v.q('memory-note').textContent='Compressed memory: '+bytes(m.compressed)+'. Available memory is an OS estimate, not guaranteed model capacity.';
  const gpuNote=h.unified_memory?'Unified memory is shared by the CPU and GPU. There is no separate VRAM total. Missing metrics are never shown as zero.':((h.gpus||[]).length?'GPU power and temperature come from the first NVIDIA device when nvidia-smi reports them. Missing metrics are never shown as zero.':'GPU utilization is not collected; missing metrics are never shown as zero.');
  v.q('platform-note').textContent=gpuNote;
  const rows=modelRows(h.ollama||{},{post:postMode});
  v.q('runtime').textContent=runtimeLine(v.key==='local'?'This computer':'Remote host',h.ollama||{});
  table(v.q('models'),rows.headers,rows.rows);
  const empty=v.q('models-empty');empty.hidden=rows.loadedNames.length>0;empty.textContent=v.cfg.empty;
  const select=v.q('model'),previous=select.value;select.replaceChildren();
  for(const name of rows.loadedNames){const o=el('option',name);o.value=name;select.append(o);}
  if(rows.loadedNames.includes(previous))select.value=previous;
  v.q('run').disabled=v.busy||!rows.loadedNames.length;
  const status=v.q('probe-status');
  if(!rows.loadedNames.length&&!v.busy&&(status.textContent===IDLE||status.textContent===''))status.textContent=v.cfg.empty;
  if(rows.loadedNames.length&&status.textContent===v.cfg.empty)status.textContent=IDLE;
  renderFit(v);fillTokenModels(v,h.ollama||{});
  v.part('overview').classList.toggle('is-stale',!!v.stale);
  v.q('stale').hidden=!v.stale;
  if(v.stale)v.q('stale').textContent='STALE · last successful sample '+(v.lastOk?new Date(v.lastOk).toLocaleString():'unknown');
}
function renderHistory(v){
  table(v.q('history'),postMode?HISTORY_HEADERS.filter(x=>x!=='Measured'):HISTORY_HEADERS,historyRows(v.results,v.hardware&&v.hardware.ollama).map(row=>postMode?row.slice(0,-1):row));
  v.q('copy').disabled=!v.results.length;
  const last=v.results.at(-1);
  if(last)v.q('results').replaceChildren(card('Generation',last.tokens_per_second+' tok/s',null,'Runtime generation duration'),card('First output',last.first_token_seconds===null||last.first_token_seconds===undefined?'Unavailable':Number(last.first_token_seconds).toFixed(2)+' s',null,'Includes request and load time'),card('Total',num(last.wall_seconds)?last.wall_seconds.toFixed(2)+' s':'—',null,(last.output_tokens||0)+' output tokens'),card('Load time',secs(last.load_seconds,3),null,last.model));
  renderSpeeds(v);renderSpark(v);
  const totals=tokenTotals(v.results.map(x=>({prompt_preview:'Explain in',input_tokens:x.prompt_tokens,output_tokens:x.output_tokens})),{post:postMode});
  const fmt=n=>n===null?'Unavailable':n.toLocaleString();
  v.q('tokens').replaceChildren(card('Input tokens',fmt(totals.input),null,'Prompt + supplied context'),card('Output tokens',fmt(totals.output),null,'Runtime-reported generation'),card('Combined tokens',fmt(totals.total),null,v.results.length+' recorded requests'));
  table(v.q('token-calls'),totals.headers,totals.rows);
}
async function refreshHardware(v){
  if(!v||v.loading)return;
  if(v.key==='remote'&&!remoteEnabled){v.q('specs').textContent='Remote host is not configured.';v.q('probe-status').textContent='Turn on a remote host in observatory.json to measure it.';return;}
  v.loading=true;
  try{const r=await fetch(v.cfg.hardwareUrl);if(!r.ok)throw Error('Hardware unavailable');v.hardware=await r.json();v.lastOk=Date.now();v.stale=false;renderOverview(v);}
  catch(e){if(v.hardware){v.stale=true;renderOverview(v);}else{v.q('status').textContent=v.key==='remote'?'OFFLINE':'Telemetry unavailable or stale: '+e.message;if(v.key==='remote')v.q('specs').textContent='Remote host is not reachable.';}}
  finally{v.loading=false;}
}
async function refreshHistory(v){
  if(!v||(v.key==='remote'&&!remoteEnabled))return;
  try{const r=await fetch(v.cfg.historyUrl);if(!r.ok)throw Error('History unavailable');const data=await r.json();v.results=Array.isArray(data.results)?data.results:[];v.runs=mergeRuns(v.runs,v.results);saveRuns(v);renderHistory(v);renderCompare();}
  catch(e){if(v.q('probe-status').textContent===IDLE)v.q('probe-status').textContent='History unavailable';}
}
async function runTest(v){
  if(v.busy||v.q('run').disabled)return;
  const model=v.q('model').value;if(!model)return;
  v.busy=true;v.q('run').disabled=true;v.q('probe-status').textContent=v.cfg.running;
  try{const r=await fetch(v.cfg.runUrl+'?model='+encodeURIComponent(model),{method:'POST'});let data={};try{data=await r.json();}catch{}
    if(!r.ok)throw Error(data.error||'Test could not complete.');
    v.runs=mergeRuns(v.runs,[data]);saveRuns(v);await refreshHistory(v);v.q('probe-status').textContent=v.cfg.done;}
  catch(e){v.q('probe-status').textContent=e.message;}
  finally{v.busy=false;await refreshHardware(v);}
}
async function copyResults(v){
  if(!v.results.length)return;
  const text=summaryText(activeLabels()[v.key],v.q('specs').textContent,v.results,v.hardware&&v.hardware.ollama);
  let copied=false;
  try{if(navigator.clipboard&&navigator.clipboard.writeText){await navigator.clipboard.writeText(text);copied=true;}}catch{}
  if(!copied){const ta=el('textarea');ta.value=text;ta.setAttribute('readonly','');document.body.appendChild(ta);ta.select();try{copied=document.execCommand('copy');}catch{copied=false;}ta.remove();}
  v.q('probe-status').textContent=copied?'Results copied.':'Could not copy results.';
}
function fillTokenModels(v,runtime){
  const c=classifyRuntime(runtime),names=[...new Set([...c.loaded,...c.installed].map(x=>x.name))];
  for(const role of ['tk-model','tk-model2']){
    const sel=v.q(role),prev=sel.value;sel.replaceChildren();
    for(const name of names){const o=el('option',name+(c.loaded.some(x=>x.name===name)?' (loaded)':''));o.value=name;sel.append(o);}
    if(names.includes(prev))sel.value=prev;
    else if(role==='tk-model2'){const other=names.find(x=>familyOf(x)!==familyOf(v.q('tk-model').value));if(other)sel.value=other;}
  }
  v.q('tk-model2').disabled=!v.q('tk-compare-on').checked;
  v.q('tk-run').disabled=!names.length||v.tkBusy;
}
async function tokenizeRequest(v,model,text){
  const r=await fetch(v.cfg.tokenizeUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model,text})});
  let data={};try{data=await r.json();}catch{}
  if(!r.ok)throw Error(data.error||'Tokenizer request failed.');
  return data;
}
function renderTokenResult(res){
  const col=el('div');col.className='tk-col';
  const head=el('div');head.className='tk-head';head.append(el('strong',res.model),badge(res.accuracy==='verified'?'live':'stale',res.accuracy==='verified'?'VERIFIED':'APPROXIMATE'));
  const fam=el('span',' '+(res.family||'')+(res.vocab_size?' · '+Number(res.vocab_size).toLocaleString()+' vocab':''));fam.className='note';head.append(fam);col.append(head);
  const st=tokenStats(res),grid=el('div');grid.className='grid tk-stats';
  grid.append(card('Tokens',st.count===null?'—':st.count.toLocaleString(),null,res.bos_added_by_runtime?'The runtime also adds a beginning-of-sequence token.':'Text only. A chat template is not included.'),
    card('Characters per token',st.cpt?st.cpt.toFixed(2):'—',null,(res.chars||0).toLocaleString()+' characters'),
    card('Context used',st.pct===null?'—':(st.pct<0.1&&st.pct>0?'<0.1':st.pct.toFixed(st.pct<10?2:1))+'%',st.pct,st.ctx.n?'of '+st.ctx.n.toLocaleString()+' ('+st.ctx.label+')':'Context unknown'));
  col.append(grid);
  const ctxNote=el('p',st.ctx.note);ctxNote.className='note';col.append(ctxNote);
  const box=el('div');box.className='tk-chunks';box.setAttribute('aria-label','Token pieces for '+res.model);
  for(const c of tokenChunks(res)){const s=el('span',c.text);s.className='tk s'+c.shade+(c.special?' special':'')+(c.partial?' partial':'');if(!postMode)s.title='token id '+c.id+(c.partial?' (first bytes of a character)':'');box.append(s);}
  col.append(box);
  if(res.truncated){const t=el('p','Showing the first '+(res.tokens||[]).length.toLocaleString()+' tokens. The count includes the rest.');t.className='note';col.append(t);}
  if(res.accuracy!=='verified'){const t=el('p','Approximate: this pre-tokenizer was not checked against a reference tokenizer.');t.className='note';col.append(t);}
  const src=el('p',postMode?'':(res.source||''));src.className='note private';col.append(src);
  return col;
}
function renderTokenResults(v){
  const out=v.q('tk-results');out.replaceChildren();const rs=v.tkResults||[];if(!rs.length)return;
  const wrap=el('div');wrap.className='tk-compare'+(rs.length>1?' two':'');rs.forEach(r=>wrap.append(renderTokenResult(r)));out.append(wrap);
  if(rs.length>1){const line=el('p',compareLine(rs[0],rs[1]));line.className='status tk-diff';out.prepend(line);}
}
async function runTokenize(v){
  if(v.tkBusy)return;
  const text=v.q('tk-input').value,m1=v.q('tk-model').value,cmp=v.q('tk-compare-on').checked,m2=v.q('tk-model2').value;
  if(!m1){v.q('tk-status').textContent='Pick a model first.';return;}
  if(!text){v.q('tk-status').textContent='Type or paste some text first.';v.tkResults=[];renderTokenResults(v);return;}
  if(v.q('tk-remember').checked){try{localStorage.setItem(TK_TEXT_KEY,text);}catch{}}
  v.tkBusy=true;v.q('tk-run').disabled=true;v.q('tk-status').textContent='Tokenizing… the first use of a model reads its tokenizer metadata.';
  try{const res=[await tokenizeRequest(v,m1,text)];
    if(cmp&&m2&&m2!==m1)res.push(await tokenizeRequest(v,m2,text));
    v.tkResults=res;renderTokenResults(v);
    v.q('tk-status').textContent='Tokenized from each model’s GGUF metadata. Nothing was loaded or generated.'+(v.q('tk-remember').checked?' Text saved in this browser only.':' The text was not stored.');}
  catch(e){v.q('tk-status').textContent=e.message;}
  finally{v.tkBusy=false;v.q('tk-run').disabled=!v.q('tk-model').options.length;}
}
function liveShowText(v){return !postMode&&v.q('lv-show-text').checked;}
function syncShowText(v){const box=v.q('lv-show-text'),lab=box.closest('label');box.disabled=postMode;lab.classList.toggle('on',liveShowText(v));lab.querySelector('span').textContent=postMode?'Text hidden in Post mode':(box.checked?'Showing prompt & reply text':'Show prompt & reply text');}
function routeReduced(){return !!(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches);}
function renderRouteHistory(box,history){
  box.replaceChildren();
  box.append(el('span',history.length?'Recent':'Recent · no turns yet'));
  box.lastChild.className='note';
  for(const item of history){const dot=el('span');dot.className='route-dot '+(item.route==='local'||item.route==='external'?item.route:'direct');dot.title=routeHistoryNote(item.route);box.append(dot);}
}
function paintRoute(v){
  const panel=v.q('route-map'),box=v.q('route-canvas');
  if(!panel||panel.hidden||typeof routeMapFrame!=='function')return;
  const source=v.routeSource,example=!!(source&&source.example);
  const frame=routeMapFrame(source?source.turn:null);
  v.q('route-example').hidden=!example;
  const ns='http://www.w3.org/2000/svg',layout=frame.layout,svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox','0 0 '+layout.width+' '+layout.height);svg.setAttribute('role','img');svg.setAttribute('aria-label',routeMapLabel(frame,postMode));
  const zone=layout.zone,z=document.createElementNS(ns,'rect');
  z.setAttribute('x',zone.x);z.setAttribute('y',zone.y);z.setAttribute('width',zone.w);z.setAttribute('height',zone.h);z.setAttribute('rx','12');z.setAttribute('class','route-zone '+frame.zone);svg.append(z);
  const zt=document.createElementNS(ns,'text');zt.setAttribute('x',zone.x+14);zt.setAttribute('y',zone.y+22);zt.setAttribute('class','zone-label');zt.textContent=zone.title;svg.append(zt);
  for(const edge of frame.edges){const path=document.createElementNS(ns,'path');path.setAttribute('d',routePathD(edge.points));path.setAttribute('class','route-edge '+edge.tone+(edge.completed?' completed':'')+(edge.pulse?' pulse':''));svg.append(path);}
  const card=layout.card;
  for(const node of frame.nodes){
    const g=document.createElementNS(ns,'g');g.setAttribute('class','route-node '+node.tone);
    const rect=document.createElementNS(ns,'rect');rect.setAttribute('x',node.x);rect.setAttribute('y',node.y);rect.setAttribute('width',card.w);rect.setAttribute('height',card.h);rect.setAttribute('rx',card.r);g.append(rect);
    const title=document.createElementNS(ns,'text');title.setAttribute('x',node.x+card.w/2);title.setAttribute('y',node.y+(node.sub&&!node.host?22:30));title.setAttribute('text-anchor','middle');title.setAttribute('class','node-title');title.textContent=node.title;g.append(title);
    if(node.sub&&!node.host){const sub=document.createElementNS(ns,'text');sub.setAttribute('x',node.x+card.w/2);sub.setAttribute('y',node.y+38);sub.setAttribute('text-anchor','middle');sub.setAttribute('class','node-sub');sub.textContent=node.sub;g.append(sub);}
    if(node.host&&node.sub&&routeHostVisible(postMode)){const sub=document.createElementNS(ns,'text');sub.setAttribute('x',node.x+card.w/2);sub.setAttribute('y',node.y+card.h+16);sub.setAttribute('text-anchor','middle');sub.setAttribute('class','node-sub private');sub.textContent=node.sub;g.append(sub);}
    if(node.slot==='prompt'&&routePromptVisible(frame,postMode)){const sub=document.createElementNS(ns,'text');sub.setAttribute('x',node.x+card.w/2);sub.setAttribute('y',node.y+card.h+16);sub.setAttribute('text-anchor','middle');sub.setAttribute('class','node-sub private');const text=frame.prompt.length>32?frame.prompt.slice(0,31)+'…':frame.prompt;sub.textContent=text;g.append(sub);}
    svg.append(g);
  }
  if(frame.pulseTravel.length){
    const motion=routeMapMotion(frame,routeReduced());
    const at=routePulsePosition(frame,motion.animate?0:1);
    if(at){const dot=document.createElementNS(ns,'circle');dot.setAttribute('class','route-pulse '+(frame.route==='external'?'external':frame.route==='local'?'local':'common'));dot.setAttribute('cx',at.x);dot.setAttribute('cy',at.y);dot.setAttribute('r','6');svg.append(dot);}
  }
  box.replaceChildren(svg);
  v.q('route-stats').replaceChildren(...frame.stats.map(s=>{const c=el('div');c.className='route-stat';c.append(el('span',s.label),el('strong',s.value));return c;}));
  v.q('route-caption').textContent=example?'Scripted path only. It is left out of Recent.':frame.phase==='idle'?'No turn yet. The map fills in when a chat turn reaches the live view.':'The pulse is on the stage this turn is in. A dim path was not taken.';
  renderRouteHistory(v.q('route-history'),v.routeHistory||[]);
  animateRoutePulse(v,frame);
}
function animateRoutePulse(v,frame){
  if(v.routeRaf)cancelAnimationFrame(v.routeRaf);v.routeRaf=0;
  const dot=v.q('route-canvas').querySelector('.route-pulse');
  const motion=routeMapMotion(frame,routeReduced());
  if(!dot||!frame.pulseTravel.length)return;
  if(!motion.animate){const at=routePulsePosition(frame,1);if(at){dot.setAttribute('cx',at.x);dot.setAttribute('cy',at.y);}return;}
  const started=performance.now(),dur=1100;
  const step=now=>{
    if(v.routeFrameStamp!==frame)return;
    const t=Math.min(1,(now-started)/dur),at=routePulsePosition(frame,t);
    if(at){dot.setAttribute('cx',at.x);dot.setAttribute('cy',at.y);}
    if(t<1)v.routeRaf=requestAnimationFrame(step);
  };
  v.routeFrameStamp=frame;v.routeRaf=requestAnimationFrame(step);
}
function stopRouteExample(v,restore){
  clearTimeout(v.routeTimer);v.routeTimer=null;
  if(v.routeRaf)cancelAnimationFrame(v.routeRaf);v.routeRaf=0;
  v.routeExample=null;
  if(!restore)return;
  const live=v.routeLive;
  v.routeSource=live?{turn:live,example:false}:{turn:null,example:false};
  paintRoute(v);
}
function playRouteExample(v){
  if(v.q('route-map').hidden)return;
  const live=v.routeLive;
  if(live&&(live.state==='waiting'||live.state==='streaming')){v.q('route-caption').textContent='A live turn is in progress. Replay waits until it finishes.';return;}
  stopRouteExample(v,false);
  const steps=routeExampleScript();
  v.routeExample={playing:true,hold:false};
  let i=0;
  const run=()=>{
    if(!v.routeExample)return;
    const step=steps[i];
    v.routeSource={turn:step.turn,example:true};
    paintRoute(v);
    i+=1;
    if(i<steps.length)v.routeTimer=setTimeout(run,steps[i].t-step.t);
    else{v.routeExample.playing=false;v.routeExample.hold=true;}
  };
  run();
}
function updateRouteMap(v,d){
  const panel=v.q('route-map');
  if(!panel||typeof routeMapFrame!=='function')return;
  if(v.key==='remote')panel.hidden=!(d&&d.tap&&d.tap.listening);
  if(panel.hidden)return;
  if(d&&d.unchanged)return;
  if(d&&d.state&&d.state!=='idle')v.routeLive=d;else if(d&&!d.unchanged)v.routeLive=null;
  const live=v.routeLive;
  if(v.routeExample&&live&&(live.state==='waiting'||live.state==='streaming')){
    v.routeHistory=applyRouteHistory(v.routeHistory,d,false);
    stopRouteExample(v,true);
    return;
  }
  if(!(v.routeExample&&(v.routeExample.playing||v.routeExample.hold))){
    v.routeHistory=applyRouteHistory(v.routeHistory||[],d,false);
    v.routeSource=live?{turn:live,example:false}:{turn:null,example:false};
    paintRoute(v);
  }else{
    v.routeHistory=applyRouteHistory(v.routeHistory||[],d,false);
    renderRouteHistory(v.q('route-history'),v.routeHistory);
  }
}
function renderLiveChips(box,part,label){
  box.replaceChildren();
  for(const c of liveChunks(part)){const s=el('span',c.text);s.className='tk s'+c.shade+(c.special?' special':'')+(c.partial?' partial':'')+(c.redacted?' redacted':'');if(c.id!==null&&!postMode)s.title='token id '+c.id;box.append(s);}
  if(!box.childNodes.length){const e=el('span',label);e.className='note';box.append(e);}
}
function renderLive(v,d){
  v.liveData=d;const b=liveStateBadge(d);v.q('lv-state').replaceChildren(badge(b.kind,b.text));
  v.q('lv-status').textContent=liveTapMessage(d);
  const active=d&&d.state&&d.state!=='idle';
  v.q('lv-model').textContent=active?(d.model||'unknown model')+(d.openwebui_model&&d.openwebui_model!==d.model?' · via '+d.openwebui_model:''):'';
  const routeName=d&&d.route==='local'?'LOCAL':d&&d.route==='external'?'EXTERNAL':'DIRECT';
  v.q('lv-route').replaceChildren(...(active?[badge(d.route==='external'?'stale':'live',routeName)]:[]));
  const pl=active?livePlacement(d.route,d.placement):null;
  v.q('lv-place').replaceChildren(...(pl?[badge('fit-badge lv-'+pl.kind,pl.label),document.createTextNode(' '+pl.text)]:[]));
  v.q('lv-cards').replaceChildren(...(active?liveCards(d).map(c=>card(c.label,c.value,c.pct,c.note)):[]));
  const hidden=active&&!d.text_included;
  v.q('lv-prompt-head').textContent='Prompt'+(active&&num(d.prompt&&d.prompt.count)?' · '+d.prompt.count.toLocaleString()+' tokens':'')+(hidden?' · text hidden':'');
  v.q('lv-reply-head').textContent='Reply'+(active&&num(d.reply&&d.reply.count)?' · '+d.reply.count.toLocaleString()+' tokens':'')+(active&&d.route_label?' · route '+d.route_label:'')+(hidden?' · text hidden':'');
  renderLiveChips(v.q('lv-prompt'),active?d.prompt:null,'—');
  renderLiveChips(v.q('lv-reply'),active?d.reply:null,active&&d.state==='waiting'?'Waiting for the first reply content…':'—');
  if(active&&d.state==='streaming'){const cur=el('span','▍');cur.className='lv-cursor';v.q('lv-reply').append(cur);}
  v.q('lv-note').textContent='Shown from memory only: the newest few turns, for about 10 minutes, never written to disk. Post mode, or turning off Show text, requests counts and token lengths only.';
  updateRouteMap(v,d);
}
async function livePoll(v){
  clearTimeout(v.liveTimer);v.liveTimer=null;
  if(!v.part('live').open||host!==v.key||document.hidden)return;
  if(v.key==='remote'&&!remoteEnabled){v.q('lv-status').textContent='The remote live view is off until a remote host is configured.';return;}
  const show=liveShowText(v),since=v.liveData&&v.liveData.state==='done'&&v.liveShown===show&&Number.isInteger(v.liveData.seq)?'&since='+v.liveData.seq:'';
  let delay=5000;
  try{const r=await fetch(v.cfg.liveUrl+'?text='+(show?'1':'0')+since);if(!r.ok)throw Error('Live view unavailable');const d=await r.json();
    if(!d.unchanged){v.liveShown=show;renderLive(v,d);}
    if(d.state==='streaming'||d.state==='waiting')delay=400;else if(d.tap&&d.tap.listening)delay=1500;}
  catch(e){v.q('lv-status').textContent=e.message;delay=5000;}
  if(v.part('live').open&&host===v.key&&!document.hidden)v.liveTimer=setTimeout(()=>livePoll(v),delay);
}
async function refreshGlance(){
  try{const r=await fetch('/api/glance');if(!r.ok)throw Error();const data=await r.json();
    if(!data.enabled||!data.routing){
      $('glance-window').textContent=data.detail||'No log source configured';
      $('glance-window').title='';
      $('glance-tiles').replaceChildren(glanceTile('Local','—'),glanceTile('External','—'),glanceTile('Unknown','—'),glanceTile('Local share','—',null,null,'Local share is local ÷ (local + external).'));
      return;}
    const g=routingGlance(data.routing);
    $('glance-window').textContent=g.window;
    $('glance-window').title=g.windowTitle;
    $('glance-tiles').replaceChildren(...g.tiles.map(x=>glanceTile(x.label,x.value,x.pct,x.note,x.title)));}
  catch{$('glance-window').textContent='Glance unavailable';}
}
function selectHost(next){
  if(next==='remote'&&!remoteEnabled)return;
  host=next;
  for(const key of ['local','remote']){$(key+'-view').hidden=key!==next;$(key+'-tab').setAttribute('aria-pressed',String(key===next));}
  $('host-label').textContent=next==='local'?'On this computer · loopback only':(postMode?'Remote host':labels.remote);
  refreshHardware(views[next]);livePoll(views[next]);
}
function setTheme(theme){document.documentElement.dataset.theme=theme;$('theme').textContent=theme==='light'?'Dark theme':'Light theme';try{localStorage.setItem('observatory-theme',theme);}catch{}}
function setPost(on){
  postMode=!!on;document.documentElement.dataset.post=postMode?'on':'off';
  const b=$('post-mode');b.textContent='Post mode: '+(postMode?'On':'Off');b.setAttribute('aria-pressed',String(postMode));$('post-pill').hidden=!postMode;
  $('remote-tab').textContent=postMode?'Remote host':labels.remote;
  $('host-label').textContent=host==='local'?'On this computer · loopback only':(postMode?'Remote host':labels.remote);
  try{localStorage.setItem(POST_KEY,postMode?'on':'off');}catch{}
  for(const v of Object.values(views)){if(v.hardware)renderOverview(v);renderHistory(v);syncShowText(v);if(v.tkResults.length)renderTokenResults(v);
    v.liveData=null;v.q('lv-prompt').replaceChildren();v.q('lv-reply').replaceChildren();paintRoute(v);}
  renderCompare();livePoll(views[host]);
}
for(const v of Object.values(views)){
  v.q('refresh').onclick=()=>refreshHardware(v);
  v.q('run').onclick=()=>runTest(v);
  v.q('copy').onclick=()=>copyResults(v);
  v.q('tk-run').onclick=()=>runTokenize(v);
  v.q('tk-sample').onclick=()=>{v.q('tk-input').value=TK_SAMPLE;};
  v.q('tk-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.altKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();runTokenize(v);}};
  v.q('tk-compare-on').onchange=()=>{v.q('tk-model2').disabled=!v.q('tk-compare-on').checked;};
  v.q('tk-remember').onchange=()=>{const on=v.q('tk-remember').checked;for(const w of Object.values(views))w.q('tk-remember').checked=on;try{if(on)localStorage.setItem(TK_TEXT_KEY,v.q('tk-input').value);else localStorage.removeItem(TK_TEXT_KEY);}catch{}};
  v.part('live').addEventListener('toggle',()=>livePoll(v));
  v.q('lv-show-text').onchange=()=>{syncShowText(v);v.liveData=null;livePoll(v);};
  v.q('route-replay').onclick=()=>playRouteExample(v);
  syncShowText(v);
}
try{const saved=localStorage.getItem(TK_TEXT_KEY);if(saved!==null)for(const v of Object.values(views)){v.q('tk-input').value=saved;v.q('tk-remember').checked=true;}}catch{}
$('local-tab').onclick=()=>selectHost('local');
$('remote-tab').onclick=()=>selectHost('remote');
$('theme').onclick=()=>setTheme(document.documentElement.dataset.theme==='light'?'dark':'light');
$('post-mode').onclick=()=>setPost(!postMode);
let savedTheme='dark',savedPost='off';
try{savedTheme=localStorage.getItem('observatory-theme')||'dark';savedPost=localStorage.getItem(POST_KEY)||'off';}catch{}
setTheme(savedTheme==='light'?'light':'dark');
setPost(savedPost==='on');
async function boot(){
  try{const r=await fetch('/api/config');if(r.ok){const cfg=await r.json();remoteEnabled=!!(cfg.remote&&cfg.remote.enabled);if(cfg.remote&&cfg.remote.label)labels.remote=cfg.remote.label;$('build-label').textContent=(cfg.version||'0.3.0')+' · 10-second updates while visible · No cloud fallback';const liveOn=!!(cfg.live_tap&&cfg.live_tap.enabled);views.local.q('route-map').hidden=!liveOn;views.remote.q('route-map').hidden=true;if(liveOn)paintRoute(views.local);}}catch{}
  $('remote-tab').hidden=!remoteEnabled;
  if(!postMode)$('remote-tab').textContent=labels.remote;
  renderCompare();
  await refreshHardware(views.local);await refreshHistory(views.local);
  if(remoteEnabled){await refreshHistory(views.remote);}
  refreshGlance();livePoll(views.local);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){refreshHardware(views[host]);livePoll(views[host]);}});
if(window.matchMedia){const mq=window.matchMedia('(prefers-reduced-motion: reduce)');const apply=()=>{for(const v of Object.values(views))if(!v.q('route-map').hidden)paintRoute(v);};if(mq.addEventListener)mq.addEventListener('change',apply);}
setInterval(()=>{if(document.hidden)return;const v=views[host];if(!v.busy)refreshHardware(v);refreshHistory(views.local);if(remoteEnabled)refreshHistory(views.remote);refreshGlance();},10000);
boot();
}

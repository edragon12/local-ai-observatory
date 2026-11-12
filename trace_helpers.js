'use strict';
/* Hop trace for one live turn.

A row is drawn only when some source reported that hop. Untimed facts stay off
the time axis. Hops that could exist and were not reported share one line.
Every row names its source. Tool and MCP names are hidden in Post mode unless
the page opts in.
*/

function traceStepLabel(step, post, showNames, index){
  if(!step) return 'Step';
  if(step.kind==='agent') return 'Agent'+(step.name?' · '+step.name:'');
  if(step.kind==='llm'){
    var where = step.where==='external'?'External model':step.where==='local'?'Local model':'Model';
    return where+(step.name?' · '+step.name:'');
  }
  if(post && !showNames && step.kind==='tool') return 'Tool call '+index;
  if(post && !showNames && step.kind==='mcp') return 'MCP call · server hidden';
  if(step.kind==='mcp') return 'MCP · '+(step.server||'server')+' · '+(step.name||'tool');
  return 'Tool · '+(step.name||'tool');
}

function traceView(turn, opts){
  opts = opts||{};
  var post = !!opts.post, showNames = !!opts.showToolNames;
  if(!turn || !turn.state || turn.state==='idle') return {rows:[], untimed:[], missing:'', total:0};
  var obs = turn.observatory||{};
  var decision = obs.decision||null;
  var classifier = obs.classifier||null;
  var steps = Array.isArray(obs.steps)?obs.steps:[];
  var source = turn.observatory_source||'router stream';
  var rows = [];
  function add(row){row.source = row.source||'live tap'; rows.push(row);}
  add({id:'prompt', lane:'Chat', label:'Prompt sent', start:0, end:0, status:'ok', source:'live tap', kind:'prompt'});
  (turn.tasks||[]).forEach(function(task, i){
    var at = typeof task.at_seconds==='number' && isFinite(task.at_seconds) ? task.at_seconds : null;
    add({id:'task'+i, lane:'Chat', label:'Background task · '+task.name, start:at, end:at, status:'ok', source:'Open WebUI', kind:'task'});
  });
  if(decision){
    var dur = typeof decision.decide_ms==='number' && isFinite(decision.decide_ms) ? decision.decide_ms/1000 : null;
    var label = 'Router decision'+(decision.label?' · '+decision.label:'');
    var code = decision.reason && decision.reason.code;
    if(code) label += ' · '+code;
    var detail = !post && turn.text_included && decision.reason && decision.reason.text ? decision.reason.text : '';
    add({id:'decision', lane:'Router', label:label, detail:detail, start:0, end:dur, status:'ok', source:source, kind:'router'});
  }
  if(classifier){
    var skipped = classifier.result==='skipped';
    var why = classifier.why||'not reported';
    var ms = typeof classifier.ms==='number' && isFinite(classifier.ms) ? classifier.ms : (skipped ? 0 : null);
    add({id:'classifier', lane:'Router', label:skipped?('Classifier · skipped: '+why):('Classifier · '+(classifier.result||'not reported')),
      start:0, end:ms===null?null:ms/1000, status:skipped?'skipped':(classifier.result==='error'?'error':'ok'), source:source, kind:'classifier'});
  }
  var toolN = 0, mcpN = 0;
  steps.forEach(function(step, i){
    var index = step.kind==='tool'?++toolN:step.kind==='mcp'?++mcpN:0;
    var lane = step.kind==='agent'?'Agent':step.kind==='llm'?'Model':step.kind==='mcp'?'MCP':'Tools';
    var start = typeof step.start_ms==='number' && isFinite(step.start_ms) ? step.start_ms/1000 : null;
    var end = typeof step.end_ms==='number' && isFinite(step.end_ms) ? step.end_ms/1000 : null;
    var hideName = post && !showNames && (step.kind==='tool' || step.kind==='mcp');
    add({id:step.id||('step'+i), lane:lane, label:traceStepLabel(step, post, showNames, index), start:start, end:end,
      status:step.status||'ok', source:source, kind:step.kind, name:hideName?'':(step.name||''), server:hideName?'':(step.server||'')});
  });
  (turn.owui_tools||[]).forEach(function(tool, i){
    var n = toolN+i+1;
    var name = (post && !showNames) ? ('Tool call '+n) : ('Tool · '+tool.name);
    add({id:'owui'+i, lane:'Tools', label:name+' · as seen by Open WebUI', start:typeof tool.start_seconds==='number'?tool.start_seconds:null,
      end:typeof tool.end_seconds==='number'?tool.end_seconds:null, status:'ok', source:'Open WebUI', kind:'tool'});
  });
  var hasLlm = steps.some(function(step){return step.kind==='llm';});
  if(!hasLlm && (turn.route==='local' || turn.route==='external' || turn.state==='streaming' || turn.state==='done' || typeof turn.first_token_seconds==='number')){
    var modelLabel = turn.route==='local'?'Local model':turn.route==='external'?'External model':'Model';
    add({id:'model', lane:'Model', label:modelLabel, start:0, end:typeof turn.elapsed_seconds==='number'?turn.elapsed_seconds:null,
      status:turn.state==='done'?'ok':'started', source:turn.route_source||'live tap', kind:'model'});
  }
  if(decision && turn.route==='external' && typeof decision.upstream_ms==='number' && isFinite(decision.upstream_ms)){
    add({id:'upstream', lane:'Model', label:'Leaves your network', start:0, end:decision.upstream_ms/1000, status:'ok', source:source, kind:'upstream'});
  }
  if(turn.state==='streaming' || turn.state==='done' || typeof turn.first_token_seconds==='number'){
    add({id:'reply', lane:'Reply', label:'Reply', start:typeof turn.first_token_seconds==='number'?turn.first_token_seconds:null,
      end:typeof turn.elapsed_seconds==='number'?turn.elapsed_seconds:null, status:turn.state==='done'?'ok':'started', source:'live tap', kind:'reply'});
  }
  var untimed = [];
  var builtins = turn.builtins||{};
  [['web_search','Web search'],['image_generation','Image generation'],['code_interpreter','Code interpreter'],['memory','Memory']].forEach(function(pair){
    if(typeof builtins[pair[0]]==='boolean') untimed.push({label:pair[1], value:builtins[pair[0]]?'on':'off', source:'Open WebUI'});
  });
  if(typeof builtins.files==='number') untimed.push({label:'Files', value:String(builtins.files), source:'Open WebUI'});
  if(typeof builtins.tool_ids==='number') untimed.push({label:'Tool ids', value:String(builtins.tool_ids), source:'Open WebUI'});
  if(typeof turn.sources==='number') untimed.push({label:'Sources', value:String(turn.sources), source:'Open WebUI'});
  var usage = turn.usage||{};
  if(typeof usage.load_ms==='number') untimed.push({label:'Load', value:usage.load_ms+' ms', source:'Open WebUI'});
  if(typeof usage.prompt_eval_ms==='number') untimed.push({label:'Prompt eval', value:usage.prompt_eval_ms+' ms', source:'Open WebUI'});
  if(typeof usage.eval_ms==='number') untimed.push({label:'Generation eval', value:usage.eval_ms+' ms', source:'Open WebUI'});
  var reported = {
    router:!!decision,
    classifier:!!classifier,
    agent:steps.some(function(step){return step.kind==='agent';}),
    tools:steps.some(function(step){return step.kind==='tool';}) || (turn.owui_tools||[]).length>0,
    mcp:steps.some(function(step){return step.kind==='mcp';})
  };
  var missingNames = [];
  if(!reported.router) missingNames.push('router');
  if(!reported.classifier) missingNames.push('classifier');
  if(!reported.agent) missingNames.push('agent');
  if(!reported.tools) missingNames.push('tools');
  if(!reported.mcp) missingNames.push('MCP');
  var total = 0;
  rows.forEach(function(row){
    if(typeof row.end==='number') total = Math.max(total, row.end);
    if(typeof row.start==='number') total = Math.max(total, row.start);
  });
  if(typeof turn.elapsed_seconds==='number') total = Math.max(total, turn.elapsed_seconds);
  return {rows:rows, untimed:untimed, missing:missingNames.length?('Not reported by this setup: '+missingNames.join(', ')):'', total:total};
}

function playbackPlan(totalSeconds){
  if(typeof totalSeconds!=='number' || !isFinite(totalSeconds) || totalSeconds<=0) return {speed:1, playSeconds:0, spedUp:false};
  if(totalSeconds<=8) return {speed:1, playSeconds:totalSeconds, spedUp:false};
  return {speed:totalSeconds/8, playSeconds:8, spedUp:true};
}

function playbackTime(plan, wallSeconds, reduced){
  if(!plan || !plan.playSeconds) return 0;
  var end = plan.playSeconds * plan.speed;
  if(reduced) return end;
  var wall = typeof wallSeconds==='number' && isFinite(wallSeconds) ? Math.max(0, wallSeconds) : 0;
  return Math.min(plan.playSeconds, wall) * plan.speed;
}

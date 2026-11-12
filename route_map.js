'use strict';
/* Route map state for one live-view turn.

The pulse follows stages the live view has already reported: waiting, streaming
(the first token has arrived), and done. Local is the teal path. External is
the amber path through "Leaves your network". A path that was not classified
stays dim. Missing numbers stay "not reported"; nothing here invents a route,
a host, or a duration.

Optional event fields, when a caller's own proxy sends them:
  route_reason: short text
  timing.route_seconds: seconds attributed to the route step
  timing.left_network_seconds: seconds from the start until the request left
    the network. Compared with elapsed_seconds, this is the only value that
    moves the pulse across that zone. It never implies the internet hop.
*/

var ROUTE_NOT_REPORTED = 'not reported';

function routeLayout(){
  var card = {w:128, h:50, r:10};
  var nodes = [
    {id:'chat', x:18, y:28, title:'Your prompt', sub:'Chat UI', slot:'prompt'},
    {id:'live', x:198, y:28, title:'Live view', sub:''},
    {id:'route', x:378, y:28, title:'Route', sub:''},
    {id:'local', x:620, y:28, title:'Local model', sub:'Ollama on this machine', host:true},
    {id:'external', x:620, y:214, title:'External model', sub:''},
    {id:'internet', x:808, y:214, title:'Internet', sub:''}
  ];
  var by = {};
  for(var i=0;i<nodes.length;i++) by[nodes[i].id] = nodes[i];
  function midY(n){return n.y + card.h/2;}
  function right(n){return n.x + card.w;}
  var zone = {x:548, y:168, w:420, h:156, title:'Leaves your network'};
  var routeMid = by.route.x + card.w/2;
  var extY = midY(by.external);
  var edges = [
    {id:'chat-live', kind:'common', points:[[right(by.chat), midY(by.chat)], [by.live.x, midY(by.live)]]},
    {id:'live-route', kind:'common', points:[[right(by.live), midY(by.live)], [by.route.x, midY(by.route)]]},
    {id:'route-local', kind:'local', points:[[right(by.route), midY(by.route)], [by.local.x, midY(by.local)]]},
    {id:'route-leave', kind:'external', points:[[routeMid, by.route.y+card.h], [routeMid, extY], [zone.x, extY]]},
    {id:'leave-external', kind:'external', points:[[zone.x, extY], [by.external.x, extY]]},
    {id:'external-internet', kind:'external', points:[[right(by.external), extY], [by.internet.x, extY]]}
  ];
  return {width:980, height:360, card:card, nodes:nodes, zone:zone, edges:edges};
}

function routeFinite(value){
  return typeof value==='number' && isFinite(value) && value>=0 ? value : null;
}

function routeTiming(turn){
  var out = {route_seconds:null, left_network_seconds:null};
  var raw = turn && turn.timing;
  if(!raw || typeof raw!=='object') return out;
  if(routeFinite(raw.route_seconds)!==null) out.route_seconds = raw.route_seconds;
  if(routeFinite(raw.left_network_seconds)!==null) out.left_network_seconds = raw.left_network_seconds;
  return out;
}

function routeReasonText(turn){
  if(!turn || typeof turn.route_reason!=='string') return null;
  var text = turn.route_reason.replace(/\s+/g,' ').trim();
  return text ? text.slice(0,120) : null;
}

function routeFormat(value, digits, suffix){
  if(routeFinite(value)===null) return ROUTE_NOT_REPORTED;
  var shown = digits ? value.toFixed(digits) : String(Math.round(value));
  return suffix ? shown+suffix : shown;
}

function routeClassOf(turn){
  if(!turn) return null;
  if(turn.route==='local' || turn.route==='external' || turn.route==='direct') return turn.route;
  return 'direct';
}

function routePhaseOf(turn){
  if(!turn || turn.state==='idle' || !turn.state) return 'idle';
  if(turn.state==='waiting' || turn.state==='streaming' || turn.state==='done') return turn.state;
  return 'idle';
}

function routeStats(turn){
  var timing = routeTiming(turn||{});
  var reply = turn && turn.reply;
  var tokens = reply && routeFinite(reply.count)!==null ? reply.count : null;
  var reason = routeReasonText(turn);
  return [
    {key:'first_token', label:'First token', value:routeFormat(turn && turn.first_token_seconds, 3, ' s')},
    {key:'stream', label:'Streaming', value:routeFormat(turn && turn.stream_seconds, 3, ' s')},
    {key:'total', label:'Total', value:routeFormat(turn && turn.elapsed_seconds, 2, ' s')},
    {key:'rate', label:'tok/s', value:routeFormat(turn && turn.tokens_per_second, 2, '')},
    {key:'tokens', label:'Tokens', value:routeFormat(tokens, 0, '')},
    {key:'route_reason', label:'Route reason', value:reason || ROUTE_NOT_REPORTED},
    {key:'left_network', label:'Left network', value:routeFormat(timing.left_network_seconds, 3, ' s')},
    {key:'route_time', label:'Route time', value:routeFormat(timing.route_seconds, 3, ' s')}
  ];
}

function routePromptText(turn){
  if(!turn || !turn.text_included || !turn.prompt || typeof turn.prompt.text!=='string') return null;
  var text = turn.prompt.text.replace(/\s+/g,' ').trim();
  return text ? text.slice(0,80) : null;
}

function routePromptVisible(frame, post){
  return !post && !!(frame && frame.prompt);
}

function routeHostVisible(post){
  return !post;
}

function routeEdgeTone(edge, route){
  if(!edge || !edge.taken) return 'dim';
  if(edge.kind==='local') return 'local';
  if(edge.kind==='external') return 'external';
  if(route==='local') return 'local';
  if(route==='external') return 'external';
  return 'common';
}

function routeNodeTone(nodeId, frame){
  if(!frame || frame.phase==='idle') return 'dim';
  if(nodeId==='local') return frame.route==='local' ? 'local' : 'dim';
  if(nodeId==='external' || nodeId==='internet') return frame.route==='external' ? 'external' : 'dim';
  if(frame.route==='local') return 'local';
  if(frame.route==='external') return 'external';
  return 'common';
}

function routeMapFrame(turn){
  var layout = routeLayout();
  var phase = routePhaseOf(turn);
  var route = phase==='idle' ? null : routeClassOf(turn);
  var edges = layout.edges.map(function(edge){
    return {id:edge.id, kind:edge.kind, points:edge.points.map(function(p){return p.slice();}), taken:false, completed:false, pulse:false, tone:'dim'};
  });
  var byId = {};
  for(var i=0;i<edges.length;i++) byId[edges[i].id] = edges[i];
  var pulseTravel = [];
  if(phase!=='idle'){
    var taken = ['chat-live','live-route'];
    if(route==='local') taken.push('route-local');
    if(route==='external') taken.push('route-leave','leave-external','external-internet');
    for(var t=0;t<taken.length;t++) byId[taken[t]].taken = true;
    var completed = [];
    if(phase==='waiting'){
      if(route==='direct'){
        completed = ['chat-live'];
        pulseTravel = [{id:'live-route', reverse:false}];
      }else if(route==='local'){
        completed = ['chat-live','live-route'];
        pulseTravel = [{id:'route-local', reverse:false}];
      }else{
        completed = ['chat-live','live-route'];
        var elapsed = routeFinite(turn.elapsed_seconds);
        var left = routeTiming(turn).left_network_seconds;
        if(left!==null && elapsed!==null && elapsed>=left){
          completed.push('route-leave');
          pulseTravel = [{id:'leave-external', reverse:false}];
        }else{
          pulseTravel = [{id:'route-leave', reverse:false}];
        }
      }
    }else{
      completed = taken.slice();
      if(phase==='streaming'){
        if(route==='local'){
          pulseTravel = [{id:'route-local', reverse:true},{id:'live-route', reverse:true},{id:'chat-live', reverse:true}];
        }else if(route==='external'){
          pulseTravel = [{id:'external-internet', reverse:true},{id:'leave-external', reverse:true},{id:'route-leave', reverse:true},{id:'live-route', reverse:true},{id:'chat-live', reverse:true}];
        }else{
          pulseTravel = [{id:'live-route', reverse:true},{id:'chat-live', reverse:true}];
        }
      }
    }
    for(var c=0;c<completed.length;c++) byId[completed[c]].completed = true;
    for(var p=0;p<pulseTravel.length;p++) byId[pulseTravel[p].id].pulse = true;
    for(var e=0;e<edges.length;e++) edges[e].tone = routeEdgeTone(edges[e], route);
  }
  var leave = byId['route-leave'];
  var zone = 'dim';
  if(route==='external') zone = leave && leave.completed ? 'crossed' : 'active';
  var direction = pulseTravel.length ? (phase==='streaming' ? 'back' : 'out') : null;
  return {
    phase:phase,
    route:route,
    direction:direction,
    pulseTravel:pulseTravel,
    edges:edges,
    nodes:layout.nodes.map(function(node){
      return {id:node.id, x:node.x, y:node.y, title:node.title, sub:node.sub||'', host:!!node.host, slot:node.slot||'', tone:routeNodeTone(node.id, {phase:phase, route:route})};
    }),
    zone:zone,
    layout:layout,
    stats:routeStats(phase==='idle' ? null : turn),
    prompt:phase==='idle' ? null : routePromptText(turn),
    routeReason:phase==='idle' ? null : routeReasonText(turn)
  };
}

function routeMapMotion(frame, reduced){
  var travel = frame && frame.pulseTravel ? frame.pulseTravel : [];
  return {animate:!reduced && travel.length>0, hold:!!reduced && travel.length>0};
}

function routeSegmentLength(points){
  var total = 0;
  for(var i=1;i<points.length;i++) total += Math.hypot(points[i][0]-points[i-1][0], points[i][1]-points[i-1][1]);
  return total;
}

function routePointOn(points, t){
  var total = routeSegmentLength(points);
  if(!points.length) return null;
  if(points.length===1 || !total) return {x:points[0][0], y:points[0][1]};
  var remain = Math.min(1, Math.max(0, t)) * total;
  for(var i=1;i<points.length;i++){
    var dx = points[i][0]-points[i-1][0], dy = points[i][1]-points[i-1][1];
    var len = Math.hypot(dx, dy);
    if(remain<=len || i===points.length-1){
      var u = len ? remain/len : 0;
      return {x:points[i-1][0]+dx*u, y:points[i-1][1]+dy*u};
    }
    remain -= len;
  }
  return {x:points[points.length-1][0], y:points[points.length-1][1]};
}

function routePulsePosition(frame, t){
  var travel = frame && frame.pulseTravel;
  if(!travel || !travel.length) return null;
  var layout = frame.layout || routeLayout();
  var byId = {};
  for(var i=0;i<layout.edges.length;i++) byId[layout.edges[i].id] = layout.edges[i];
  var pieces = [];
  for(var s=0;s<travel.length;s++){
    var edge = byId[travel[s].id];
    if(!edge) continue;
    var points = edge.points.map(function(p){return p.slice();});
    if(travel[s].reverse) points.reverse();
    pieces.push({points:points, len:routeSegmentLength(points)});
  }
  var total = 0;
  for(var n=0;n<pieces.length;n++) total += pieces[n].len;
  if(!total) return null;
  var remain = (typeof t==='number' && isFinite(t) ? Math.min(1, Math.max(0, t)) : 0) * total;
  for(var k=0;k<pieces.length;k++){
    if(remain<=pieces[k].len || k===pieces.length-1) return routePointOn(pieces[k].points, pieces[k].len ? remain/pieces[k].len : 0);
    remain -= pieces[k].len;
  }
  return null;
}

function routePathD(points){
  var d = '';
  for(var i=0;i<points.length;i++) d += (i ? 'L' : 'M') + points[i][0] + ',' + points[i][1];
  return d;
}

function routeMapLabel(frame, post){
  if(!frame || frame.phase==='idle') return 'Route map. No turn yet.';
  var dest = frame.route==='local' ? 'Local model' : frame.route==='external' ? 'External model' : 'destination not reported';
  var label = 'Route map. ' + frame.phase + '. ' + dest + '.';
  if(routePromptVisible(frame, post)) label += ' Prompt text is shown.';
  return label;
}

function routeHistoryNote(route){
  if(route==='local') return 'Local';
  if(route==='external') return 'External';
  return ROUTE_NOT_REPORTED;
}

function rememberRouteTurn(history, turn, example){
  var prev = [];
  if(Array.isArray(history)){
    for(var i=0;i<history.length;i++) if(history[i] && typeof history[i].id==='string') prev.push({id:history[i].id, route:history[i].route});
  }
  if(example || (turn && turn.example)) return prev;
  if(!turn || typeof turn.id!=='string' || !turn.id || turn.state==='idle') return prev;
  var route = turn.route==='local' || turn.route==='external' ? turn.route : 'direct';
  var next = [];
  for(var n=0;n<prev.length;n++) if(prev[n].id!==turn.id) next.push(prev[n]);
  next.push({id:turn.id, route:route});
  return next.slice(-8);
}

function applyRouteHistory(history, snapshot, example){
  var next = Array.isArray(history) ? history.slice() : [];
  if(example) return next;
  var recent = snapshot && Array.isArray(snapshot.recent) ? snapshot.recent : [];
  for(var i=0;i<recent.length;i++) next = rememberRouteTurn(next, recent[i], false);
  if(snapshot && snapshot.id && snapshot.state && snapshot.state!=='idle') next = rememberRouteTurn(next, snapshot, false);
  return next;
}

function routeExampleScript(){
  var prompt = {text:'Example prompt'};
  return [
    {t:0, turn:{example:true, id:'example', state:'waiting', route:'direct', elapsed_seconds:0.05, text_included:true, prompt:prompt}},
    {t:1000, turn:{example:true, id:'example', state:'waiting', route:'external', elapsed_seconds:0.40, text_included:true, prompt:prompt}},
    {t:2200, turn:{example:true, id:'example', state:'streaming', route:'external', elapsed_seconds:1.10, first_token_seconds:0.70, stream_seconds:0.40, text_included:true, prompt:prompt, reply:{}}},
    {t:4200, turn:{example:true, id:'example', state:'done', route:'external', elapsed_seconds:2.40, first_token_seconds:0.70, stream_seconds:1.70, tokens_per_second:18.4, text_included:true, prompt:{text:'Example prompt', count:3}, reply:{count:32}}}
  ];
}

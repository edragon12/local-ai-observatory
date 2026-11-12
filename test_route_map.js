'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(__dirname + '/route_map.js', 'utf8');
const ctx = {Math, Number, String, Array, Object, isFinite};
vm.createContext(ctx);
vm.runInContext(source, ctx);
const run = code => vm.runInContext(code, ctx);
let n = 0;
const ok = (name, fn) => { fn(); n++; };

function edge(frame, id){ return frame.edges.find(item => item.id === id); }

ok('idle and unknown states report nothing and light nothing', () => {
  const idle = run('routeMapFrame(null)');
  assert.equal(idle.phase, 'idle');
  assert.equal(idle.pulseTravel.length, 0);
  assert.ok(idle.edges.every(item => !item.taken));
  assert.ok(idle.stats.every(item => item.value === 'not reported'));
  const odd = run("routeMapFrame({state:'thinking', route:'local', first_token_seconds:1})");
  assert.equal(odd.phase, 'idle');
  assert.equal(odd.stats[0].value, 'not reported');
});

ok('waiting without a route stays on the route edge', () => {
  const frame = run("routeMapFrame({state:'waiting', route:'direct', elapsed_seconds:0.2})");
  assert.equal(frame.direction, 'out');
  assert.deepEqual(frame.pulseTravel, [{id:'live-route', reverse:false}]);
  assert.equal(edge(frame, 'chat-live').completed, true);
  assert.equal(edge(frame, 'route-local').taken, false);
  assert.equal(edge(frame, 'route-leave').taken, false);
  assert.equal(edge(frame, 'route-local').tone, 'dim');
  assert.equal(edge(frame, 'live-route').tone, 'common');
  assert.equal(frame.zone, 'dim');
  assert.equal(run("routeNodeTone('local', routeMapFrame({state:'waiting', route:'direct'}))"), 'dim');
});

ok('a local wait pulses toward the local model and leaves the amber path dim', () => {
  const frame = run("routeMapFrame({state:'waiting', route:'local'})");
  assert.deepEqual(frame.pulseTravel, [{id:'route-local', reverse:false}]);
  assert.equal(edge(frame, 'live-route').completed, true);
  assert.equal(edge(frame, 'route-local').tone, 'local');
  assert.equal(edge(frame, 'chat-live').tone, 'local');
  assert.equal(edge(frame, 'route-leave').taken, false);
  assert.equal(frame.zone, 'dim');
  const start = run("routePulsePosition(routeMapFrame({state:'waiting', route:'local'}), 0)");
  const end = run("routePulsePosition(routeMapFrame({state:'waiting', route:'local'}), 1)");
  assert.ok(start.x < end.x);
  assert.equal(start.y, end.y);
});

ok('external wait stops at the zone until left_network_seconds is reached', () => {
  const waiting = run("routeMapFrame({state:'waiting', route:'external', elapsed_seconds:1, timing:{route_seconds:0.01}})");
  assert.equal(waiting.pulseTravel[0].id, 'route-leave');
  assert.equal(edge(waiting, 'route-leave').completed, false);
  assert.equal(edge(waiting, 'external-internet').completed, false);
  assert.equal(edge(waiting, 'external-internet').taken, true);
  assert.equal(edge(waiting, 'route-local').taken, false);
  assert.equal(waiting.zone, 'active');
  assert.equal(waiting.stats.find(item => item.key === 'route_time').value, '0.010 s');
  assert.equal(waiting.stats.find(item => item.key === 'left_network').value, 'not reported');
  const early = run("routeMapFrame({state:'waiting', route:'external', elapsed_seconds:0.2, timing:{left_network_seconds:0.5}})");
  assert.equal(early.pulseTravel[0].id, 'route-leave');
  const crossed = run("routeMapFrame({state:'waiting', route:'external', elapsed_seconds:0.5, timing:{left_network_seconds:0.5}})");
  assert.equal(crossed.pulseTravel[0].id, 'leave-external');
  assert.equal(edge(crossed, 'route-leave').completed, true);
  assert.equal(edge(crossed, 'external-internet').completed, false);
  assert.equal(crossed.zone, 'crossed');
  const untimed = run("routeMapFrame({state:'waiting', route:'external', timing:{left_network_seconds:0}})");
  assert.equal(untimed.pulseTravel[0].id, 'route-leave');
});

ok('streaming returns along the taken path and done settles', () => {
  const back = run("routeMapFrame({state:'streaming', route:'external', first_token_seconds:0.4, stream_seconds:0.2})");
  assert.equal(back.direction, 'back');
  assert.deepEqual(back.pulseTravel.map(item => item.id), ['external-internet', 'leave-external', 'route-leave', 'live-route', 'chat-live']);
  assert.ok(back.pulseTravel.every(item => item.reverse));
  assert.equal(edge(back, 'route-local').taken, false);
  assert.equal(edge(back, 'external-internet').completed, true);
  const origin = run("routePulsePosition(routeMapFrame({state:'streaming', route:'external'}), 0)");
  const home = run("routePulsePosition(routeMapFrame({state:'streaming', route:'external'}), 1)");
  assert.ok(origin.x > home.x);
  const local = run("routeMapFrame({state:'streaming', route:'local'})");
  assert.equal(local.pulseTravel[0].id, 'route-local');
  assert.equal(edge(local, 'external-internet').taken, false);
  const done = run("routeMapFrame({state:'done', route:'external', first_token_seconds:0.4, stream_seconds:1.2, elapsed_seconds:1.6, tokens_per_second:10, reply:{count:12}})");
  assert.equal(done.pulseTravel.length, 0);
  assert.equal(done.direction, null);
  assert.ok(done.edges.filter(item => item.kind === 'external').every(item => item.completed));
  assert.equal(edge(done, 'route-local').taken, false);
  assert.equal(done.stats.find(item => item.key === 'tokens').value, '12');
  assert.equal(done.stats.find(item => item.key === 'rate').value, '10.00');
});

ok('missing numbers stay not reported and zero is a real zero', () => {
  const blank = run("routeMapFrame({state:'done', route:'direct', reply:{chars:40}, prompt:{text:'secret', count:'4'}, tokens_per_second:'9', timing:{route_seconds:'1'}})");
  assert.equal(blank.stats.find(item => item.key === 'tokens').value, 'not reported');
  assert.equal(blank.stats.find(item => item.key === 'rate').value, 'not reported');
  assert.equal(blank.stats.find(item => item.key === 'route_time').value, 'not reported');
  assert.equal(blank.stats.find(item => item.key === 'route_reason').value, 'not reported');
  assert.equal(blank.prompt, null);
  const zero = run("routeMapFrame({state:'done', route:'local', first_token_seconds:0, stream_seconds:0, elapsed_seconds:0, tokens_per_second:0, reply:{count:0}})");
  assert.equal(zero.stats.find(item => item.key === 'first_token').value, '0.000 s');
  assert.equal(zero.stats.find(item => item.key === 'tokens').value, '0');
  const shown = run("routeMapFrame({state:'done', route:'local', text_included:true, prompt:{text:'  Hello\\nthere  '}})");
  assert.equal(shown.prompt, 'Hello there');
  assert.equal(run('routePromptVisible(routeMapFrame({state:"done", route:"local", text_included:true, prompt:{text:"Hi"}}), true)'), false);
  assert.equal(run('routeHostVisible(true)'), false);
  assert.equal(run('routeHostVisible(false)'), true);
});

ok('history keeps real turns and refuses the example', () => {
  let history = run('rememberRouteTurn([], {id:"a", state:"done", route:"local"}, false)');
  history = run('rememberRouteTurn(' + JSON.stringify(history) + ', {id:"b", state:"done", route:"external"}, false)');
  history = run('rememberRouteTurn(' + JSON.stringify(history) + ', {id:"a", state:"done", route:"external"}, false)');
  assert.deepEqual(history, [{id:'b', route:'external'}, {id:'a', route:'external'}]);
  const example = run('routeExampleScript()');
  assert.ok(example.every(step => step.turn.example === true));
  assert.equal(example[example.length - 1].turn.route, 'external');
  assert.equal(example[0].turn.route_reason, undefined);
  assert.equal(run('routeMapFrame(routeExampleScript()[0].turn).stats').find(item => item.key === 'tokens').value, 'not reported');
  assert.equal(run('routeMapFrame(routeExampleScript()[3].turn).stats').find(item => item.key === 'tokens').value, '32');
  assert.equal(run('routeMapFrame(routeExampleScript()[3].turn).stats').find(item => item.key === 'route_reason').value, 'policy_match');
  assert.ok(run('routeMapFrame(routeExampleScript()[3].turn).extras').some(item => item.id === 'router'));
  assert.equal(run('routeMapFrame({state:"done", route:"local"}).extras').length, 0);
  const kept = run('applyRouteHistory([{id:"a", route:"local"}], {example:true, id:"example", state:"done", route:"external", recent:[{id:"example", route:"external"}]}, true)');
  assert.deepEqual(kept, [{id:'a', route:'local'}]);
  const blocked = run('rememberRouteTurn([{id:"a", route:"local"}], routeExampleScript()[3].turn, false)');
  assert.deepEqual(blocked, [{id:'a', route:'local'}]);
  let many = [];
  for(let i = 0; i < 9; i++) many = run('rememberRouteTurn(' + JSON.stringify(many) + ', {id:"t' + i + '", state:"done", route:"' + (i % 2 ? 'external' : 'local') + '"}, false)');
  assert.equal(many.length, 8);
  assert.equal(many[0].id, 't1');
  assert.equal(run('routeHistoryNote("direct")'), 'not reported');
  assert.equal(run('routeMapMotion(routeMapFrame({state:"streaming", route:"local"}), true).animate'), false);
  assert.equal(run('routeMapMotion(routeMapFrame({state:"streaming", route:"local"}), false).animate'), true);
  assert.equal(run('routeMapMotion(routeMapFrame({state:"done", route:"local"}), false).animate'), false);
});

console.log(n + ' route map checks passed');

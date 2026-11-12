'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const ctx = {Math, Number, String, Array, Object, isFinite};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname + '/trace_helpers.js', 'utf8'), ctx);
vm.runInContext(fs.readFileSync(__dirname + '/route_map.js', 'utf8'), ctx);
const run = code => vm.runInContext(code, ctx);
let n = 0;
const ok = (name, fn) => { fn(); n++; };

ok('a plain local turn draws only the hops the live view reported', () => {
  const view = run("traceView({state:'done', route:'local', route_source:'Open WebUI', elapsed_seconds:1.2, first_token_seconds:0.2, stream_seconds:1}, {post:false})");
  const labels = view.rows.map(row => row.label);
  assert.ok(labels.includes('Prompt sent'));
  assert.ok(labels.includes('Local model'));
  assert.ok(labels.includes('Reply'));
  assert.ok(!labels.some(label => label.startsWith('Router')));
  assert.ok(!labels.some(label => label.startsWith('Classifier')));
  assert.equal(view.missing, 'Not reported by this setup: router, classifier, agent, tools, MCP');
  assert.ok(view.rows.every(row => row.source));
});

ok('router steps are rows and a skipped classifier is not called', () => {
  const view = run("traceView({state:'done', route:'external', observatory_source:'router stream', elapsed_seconds:2, first_token_seconds:0.4, text_included:true, observatory:{decision:{target:'external', label:'External · example', reason:{code:'policy_match', text:'note'}, decide_ms:12, upstream_ms:40}, classifier:{result:'skipped', why:'too_short', ms:0}, steps:[{id:'a', kind:'agent', name:'planner', status:'ok', start_ms:10, end_ms:20},{id:'t', kind:'tool', name:'lookup', status:'error', start_ms:30, end_ms:40},{id:'m', kind:'mcp', name:'search', server:'notes', status:'ok', start_ms:50, end_ms:80}]}}, {post:false, showToolNames:false})");
  const classifier = view.rows.find(row => row.kind === 'classifier');
  assert.equal(classifier.label, 'Classifier · skipped: too_short');
  assert.equal(classifier.status, 'skipped');
  assert.ok(!classifier.label.includes('called'));
  assert.equal(classifier.end, 0);
  assert.equal(view.rows.find(row => row.kind === 'tool').label, 'Tool · lookup');
  assert.equal(view.rows.find(row => row.kind === 'mcp').source, 'router stream');
  assert.equal(view.missing, '');
  const hidden = run("traceView({state:'done', route:'external', observatory:{steps:[{kind:'tool', name:'lookup', status:'ok'},{kind:'mcp', name:'search', server:'notes', status:'ok'}]}}, {post:true, showToolNames:false})");
  assert.equal(hidden.rows.find(row => row.kind === 'tool').label, 'Tool call 1');
  assert.equal(hidden.rows.find(row => row.kind === 'mcp').label, 'MCP call · server hidden');
  assert.ok(!JSON.stringify(hidden.rows).includes('lookup'));
  assert.ok(!JSON.stringify(hidden.rows).includes('notes'));
});

ok('open webui tools are labelled as seen there and stay off the example history', () => {
  const view = run("traceView({state:'streaming', route:'direct', owui_tools:[{name:'lookup', start_seconds:0.2, end_seconds:0.5}]}, {})");
  assert.ok(view.rows.find(row => row.kind === 'tool').label.includes('as seen by Open WebUI'));
  assert.equal(view.rows.find(row => row.kind === 'tool').source, 'Open WebUI');
});

ok('playback speeds up past eight seconds and reduced motion snaps to the end', () => {
  const plan = run('playbackPlan(16)');
  assert.equal(plan.playSeconds, 8);
  assert.equal(plan.speed, 2);
  assert.equal(run('playbackTime(playbackPlan(16), 4, false)'), 8);
  assert.equal(run('playbackTime(playbackPlan(16), 0, true)'), 16);
  assert.equal(run('playbackPlan(3).spedUp'), false);
  assert.equal(run('playbackTime(playbackPlan(3), 1, false)'), 1);
});

console.log(n + ' trace checks passed');

'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');
const ctx = {Map, Set, Date, Array, Number, Object, String, JSON, Math};
vm.createContext(ctx);
vm.runInContext(source, ctx);
const run = code => vm.runInContext(code, ctx);
let n = 0;
const ok = (name, fn) => { fn(); n++; };

ok('model states and vram column', () => {
  const loaded = run("modelRows({status:'Available',installed:[{name:'a',parameters:'1B',quantization:'Q4'}],loaded:[{name:'a',size:1073741824,context_length:4096,expires_at:'2026-11-11T10:00:00Z',vram:1073741824}]})");
  assert.equal(loaded.rows[0][1], 'Loaded · generation unknown');
  assert.ok(loaded.headers.includes('GPU memory'));
  assert.ok(loaded.headers.includes('Keep-alive'));
  const posted = run("modelRows({status:'Available',installed:[{name:'a'}],loaded:[{name:'a',size:5,context_length:4096,expires_at:'2026-11-11T10:00:00Z'}]},{post:true})");
  assert.ok(!posted.headers.includes('Keep-alive'));
  assert.ok(!JSON.stringify(posted.rows).includes('until'));
  assert.equal(run("classifyRuntime({status:'Unavailable',installed:[],loaded:[]}).state"), 'unreachable');
  assert.ok(run("runtimeLine('Remote host',{status:'Unavailable',installed:[],loaded:[]})").includes('Unreachable'));
});

ok('memory fit for discrete and unified memory', () => {
  const G = 1024 ** 3;
  let fit = run("memoryFit({unified:false,gpu:{name:'GPU',total:" + (8 * G) + ",used:" + (6 * G) + "},runtime:{loaded:[{name:'fit',size:" + (5 * G) + ",vram:" + (5 * G) + "}]}})");
  assert.equal(fit.models[0].kind, 'fits');
  assert.equal(fit.models[0].verdict, 'Fits fully in VRAM');
  assert.equal(fit.pool.label, 'VRAM');
  fit = run("memoryFit({unified:false,gpu:{name:'GPU',total:1,used:1},runtime:{loaded:[{name:'big',size:" + (8 * G) + ",vram:" + (6 * G) + "}]}})");
  assert.equal(fit.models[0].kind, 'spill');
  assert.ok(fit.models[0].verdict.includes('Spills 2.0 GiB to CPU/RAM (75% on GPU)'), fit.models[0].verdict);
  fit = run("memoryFit({unified:false,gpu:{name:'GPU',total:1,used:1},runtime:{loaded:[{name:'cpu',size:" + (2 * G) + ",vram:null}]}})");
  assert.equal(fit.models[0].kind, 'cpu');
  fit = run("memoryFit({unified:false,gpu:null,runtime:{loaded:[{name:'x',size:1,vram:null}]}})");
  assert.ok(fit.models[0].verdict.includes('No GPU'));
  assert.equal(fit.pool, null);
  fit = run("memoryFit({unified:true,memTotal:" + (16 * G) + ",runtime:{loaded:[{name:'m',size:" + G + ",gpu_resident:" + G + "}]}})");
  assert.equal(fit.pool.label, 'Unified memory');
  assert.equal(fit.models[0].kind, 'fits');
  assert.ok(fit.models[0].verdict.includes('Metal'));
  assert.ok(!fit.models[0].verdict.includes('VRAM'));
  fit = run("memoryFit({unified:true,memTotal:1,runtime:{loaded:[{name:'m',size:100,gpu_resident:60}]}})");
  assert.equal(fit.models[0].kind, 'spill');
  assert.ok(fit.models[0].verdict.startsWith('Partly on CPU'));
});

ok('thermal cards, speeds, sparkline, runs', () => {
  assert.equal(run('gpuThermalCards(null)').length, 0);
  const cards = run('gpuThermalCards({temperature:50,power_draw:10.89,power_limit:null})');
  assert.deepEqual(cards.map(x => x.label), ['GPU temperature', 'GPU power draw']);
  assert.equal(cards[1].pct, null);
  assert.equal(run('gpuThermalCards({power_draw:40,power_limit:80})')[0].pct, 50);
  const pair = run('speedPair({tokens_per_second:50,prompt_tokens_per_second:400,prompt_tokens:36,prompt_eval_seconds:0.09,output_tokens:128,eval_seconds:2.56})');
  assert.equal(pair.ratio, 8);
  assert.equal(run('speedPair({tokens_per_second:50})').prompt, null);
  const many = JSON.stringify(Array.from({length: 40}, (_, i) => ({model: 'm', sampled_at: 1000 + i, tokens_per_second: i + 1, prompt: 'SECRET'})));
  const merged = run('mergeRuns(' + many + ",[{model:'m',sampled_at:1039,tokens_per_second:40},{model:'bad'}])");
  assert.equal(merged.length, 30);
  assert.ok(!JSON.stringify(merged).includes('SECRET'));
  assert.deepEqual(run('sparkGeometry([],100,20)').points, []);
  assert.equal(run('sparkGeometry([5],100,20,0)').points[0][0], 50);
  const flat = run('sparkGeometry([3,3,3],100,20,0)');
  assert.ok(flat.points.every(p => p[1] === 10));
  assert.deepEqual(run('sparkGeometry([1,2,3],100,20,0)').points.map(p => p[1]), [20, 10, 0]);
});

ok('comparison uses local and remote labels', () => {
  const local = "[{model:'small',sampled_at:1,tokens_per_second:20},{model:'tiny',sampled_at:9,tokens_per_second:60}]";
  const remote = "[{model:'small',sampled_at:5,tokens_per_second:50},{model:'other',sampled_at:6,tokens_per_second:90}]";
  const pick = run('comparePick(' + local + ',' + remote + ')');
  assert.equal(pick.sameModel, true);
  assert.equal(pick.local.model, 'small');
  const summary = run('compareSummary(comparePick(' + local + ",[{model:'small',sampled_at:5,tokens_per_second:50}]),{local:'This computer',remote:'Remote host'},true)");
  assert.ok(summary.text.includes('Remote host generated 2.5× faster'));
  const different = run("compareSummary(comparePick([{model:'a',sampled_at:1,tokens_per_second:1}],[{model:'b',sampled_at:1,tokens_per_second:2}]),null,true)");
  assert.equal(different.kind, 'different');
  assert.ok(different.text.includes('DIFFERENT MODELS'));
  assert.equal(run('compareSummary(comparePick([],[]),null,false)').kind, 'unconfigured');
  const rows = run("compareRows(comparePick([],[{model:'b',sampled_at:1,tokens_per_second:2}]),{local:'This computer',remote:'Remote host'},true)");
  assert.equal(rows.rows[0][1], 'No test yet');
  assert.equal(rows.rows[1][0], 'Remote host');
});

ok('prompt tokens, live placement and local share', () => {
  assert.equal(run("visibleWS(' a\\tb\\n')"), '·a→b↵\n');
  assert.equal(run('pickContext({loaded:4096,modelfile:65536,trained_max:40960}).label'), 'loaded num_ctx');
  assert.equal(run('pickContext({trained_max:40960,probe:4096}).label'), 'model maximum');
  const stats = run('tokenStats({count:36,chars:144,context:{loaded:4096}})');
  assert.equal(stats.cpt, 4);
  const chunks = run("tokenChunks({tokens:[[151644,'<|im_start|>'],[872,'user']],special_ids:[151644]})");
  assert.equal(chunks[0].special, true);
  assert.equal(chunks[1].special, false);
  assert.ok(run("compareLine({model:'a',count:10},{model:'b',count:12})").startsWith('a needs 2 fewer'));
  assert.equal(run("familyOf('qwen3:8b')"), 'qwen');
  assert.equal(run("familyOf('llama3.2:3b')"), 'llama');
  const place = run("livePlacement('local',{loaded:true,size:800,vram:600,context:4096})");
  assert.equal(place.kind, 'spill');
  assert.equal(run("livePlacement('external',{loaded:true,size:1,vram:1})").label, 'EXTERNAL');
  assert.equal(run('localSharePct(3,1)'), 75);
  assert.equal(run('formatShare(localSharePct(3,1))'), '75%');
  assert.equal(run('localSharePct(0,0)'), null);
  const glance = run("routingGlance({all:{local:3,external:1,unknown:2},first_time:'2026-01-02 03:04:05',last_time:'2026-01-02 03:10:00'})");
  assert.equal(glance.tiles[3].value, '75%');
  assert.equal(glance.tiles[3].label, 'Local share');
  const totals = run("tokenTotals([{prompt_preview:'0123456789SECRET',input_tokens:3,output_tokens:2}],{post:false})");
  assert.equal(Array.from(totals.rows[0][0]).length, 10);
  assert.ok(!totals.rows[0][0].includes('SECRET'));
  assert.equal((source.match(/method:'POST'/g) || []).length, 2);
});

console.log(n + ' feature checks passed');

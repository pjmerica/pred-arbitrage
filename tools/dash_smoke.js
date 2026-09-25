// Dashboard smoke test (2026-09-25): loads docs/index.html's data files and
// inline script into a minimal DOM stub, runs startup plus an optional
// exercise script, and EXITS 1 on any runtime exception. CI runs it after
// the pipeline and before committing, so data that would crash the live
// page is never published. A syntax check can't catch these.
// Usage: node tools/dash_smoke.js docs [tools/dash_exercise.js]
const fs = require('fs'), path = require('path'), vm = require('vm');
const dir = process.argv[2];
const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
const els = {};
function mk(id) {
  const e = { id, innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, checked: false, children: [],
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    addEventListener(){}, removeEventListener(){}, appendChild(c){ this.children.push(c); return c; },
    setAttribute(){}, getAttribute(){ return null; }, querySelector(){ return mk('q'); }, querySelectorAll(){ return []; },
    closest(){ return null; }, focus(){}, scrollIntoView(){}, getContext(){ return new Proxy({}, { get: () => () => {} }); },
    options: [], selectedIndex: 0, remove(){}, insertAdjacentHTML(){}, click(){} };
  return e;
}
const document = {
  getElementById: id => (els[id] = els[id] || mk(id)),
  querySelector: () => mk('q'), querySelectorAll: () => [],
  createElement: t => mk(t), addEventListener(){}, body: mk('body'), documentElement: mk('html'),
};
const errors = [];
const ctx = { document, window: {}, console, localStorage: { getItem(){ return null; }, setItem(){} },
  location: { hash: '', search: '', href: '' }, history: { replaceState(){} , pushState(){} },
  setTimeout: (f) => { try { f(); } catch (e) { errors.push('timeout: ' + e.stack.split('\n')[0]); } return 0; },
  clearTimeout(){}, setInterval(){ return 0; }, requestAnimationFrame: f => f(), navigator: { clipboard: {} },
  Chart: function(){ return { destroy(){}, update(){} }; }, Plotly: new Proxy({}, { get: () => () => Promise.resolve() }), URLSearchParams, Intl, Date, Math, JSON };
ctx.window = ctx; ctx.self = ctx;
vm.createContext(ctx);
for (const m of html.matchAll(/<script\s+src="([^"]+)"/g)) {
  const f = path.join(dir, m[1]);
  if (fs.existsSync(f)) vm.runInContext(fs.readFileSync(f, 'utf8').replace(/^const (\w+) =/m, 'var $1 ='), ctx, { filename: m[1] });
}
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
try { vm.runInContext(inline, ctx, { filename: 'inline' }); } catch (e) { errors.push('load: ' + e.stack.split('\n').slice(0,2).join(' | ')); }
if (process.argv[3]) {
  try { vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), ctx, { filename: 'extra' }); }
  catch (e) { errors.push('extra: ' + e.stack.split('\n').slice(0,2).join(' | ')); }
}
const big = Object.values(els).filter(e => (e.innerHTML || '').length > 500).map(e => `${e.id}:${e.innerHTML.length}ch/${(e.innerHTML.match(/<tr/g)||[]).length}tr`);
console.log(JSON.stringify({ errors, rendered: big }, null, 1));
if (errors.length || !big.length) { console.error('DASHBOARD SMOKE TEST FAILED'); process.exit(1); }

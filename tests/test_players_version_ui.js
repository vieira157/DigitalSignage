// Run with node tests/test_players_version_ui.js. No browser or network required.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const template = fs.readFileSync(path.join(__dirname, '../templates/players.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = Object.fromEntries(['search', 'problems', 'sort', 'group', 'players', 'm-total', 'm-ok', 'm-alert', 'm-offline']
  .map(id => [id, {value: '', checked: false, innerHTML: '', textContent: ''}]));
elements.sort.value = 'health';
elements.group.value = 'none';
let timer;
let fetches = 0;
const healthy = {tv_id: 'A', name: 'Healthy preview', health: 'ok', player_version: 'preview',
  version_status: 'different', version_status_label: 'Versão diferente da recomendada', version_outdated: true};
const incident = {tv_id: 'B', name: 'Disk incident', health: 'warning', player_version: 'stable',
  version_status: 'current', version_status_label: 'Versão recomendada'};
const context = vm.createContext({document: {getElementById: id => elements[id]}, Date,
  setInterval: (fn, delay) => { assert.equal(delay, 3000); timer = fn; },
  fetch: async () => { fetches++; return {ok: true, json: async () => ({tvs: [healthy, incident]})}; },
  dsToast: message => { throw new Error(message); }});
vm.runInContext(script, context);
(async () => {
  // Flush the initial automatic load and verify the registered polling callback.
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(fetches, 1);
  assert.equal(elements['m-ok'].textContent, 1);
  assert.equal(elements['m-alert'].textContent, 1);
  const html = elements.players.innerHTML;
  assert.ok(html.indexOf('Disk incident') < html.indexOf('Healthy preview'));
  assert.match(html, /card ok/);
  assert.match(html, /version-status different/);
  assert.ok(!html.includes('⚠'));
  elements.problems.checked = true;
  vm.runInContext('render()', context);
  assert.ok(!elements.players.innerHTML.includes('Healthy preview'));
  assert.ok(elements.players.innerHTML.includes('Disk incident'));
  healthy.player_version = '';
  healthy.version_status = 'unknown';
  healthy.version_status_label = 'Versão não informada';
  elements.problems.checked = false;
  await timer();
  assert.equal(fetches, 2);
  assert.match(elements.players.innerHTML, /Versão não informada/);
  assert.equal(elements['m-alert'].textContent, 1);
  const card = vm.runInContext('card', context);
  assert.ok(card({...healthy, player_version: '<script>alert(1)</script>'}).includes('&lt;script&gt;'));
  assert.match(template, /\.version-status\.different\{color:var\(--blue\)\}/);
  console.log('Players UI: polling, counters, sorting, filtering, version labels and escaping passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const crypto = require('node:crypto');

async function main() {
  let now = 1000;
  let monotonic = 0;
  const callbacks = {};
  const updates = [];
  const reloads = [];
  const sent = [];
  const storage = {};
  const event = name => ({addListener(fn) { callbacks[name] = fn; }});
  const port = {
    onMessage: event('native'), onDisconnect: event('disconnect'),
    postMessage(message) { sent.push(message); }, disconnect() { callbacks.disconnect(); }
  };
  const browser = {
    runtime: {id: 'focus-blocker@local.invalid', getURL: p => 'moz-extension://test/' + p,
      connectNative: () => port, onMessage: event('message')},
    management: {getSelf: async () => ({installType: 'admin'})},
    extension: {isAllowedIncognitoAccess: async () => true},
    storage: {managed: {get: async () => ({localBridgeConsent: true})}, local: {
      get: async key => ({[key]: storage[key]}),
      set: async values => Object.assign(storage, values),
      remove: async key => { delete storage[key]; }
    }},
    webRequest: {onBeforeRequest: event('request')},
    webNavigation: {getAllFrames: async () => [{frameId: 1, url: 'https://youtube.com/embed/1'}]},
    tabs: {query: async () => [{id: 1, url: 'https://youtube.com'}, {id: 2, url: 'https://example.org'}],
      update: async (id, value) => updates.push([id, value]),
      reload: async id => reloads.push(id), onUpdated: event('updated')}
  };
  const intervals = [];
  const context = vm.createContext({browser, crypto, URL, URLSearchParams, console,
    Date: {now: () => now}, performance: {now: () => monotonic},
    setInterval: fn => intervals.push(fn), setTimeout: () => {}});
  for (const file of ['rules.js', 'background.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../firefox', file), 'utf8'), context);
  }
  const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };
  await flush();
  assert.equal(sent[0].managed, true);
  assert.equal(sent[0].private, true);
  const config = {mode: 'blacklist', rules: [
    {host: 'youtube.com', action: 'block', subdomains: true},
    {host: 'music.youtube.com', action: 'allow', subdomains: true}
  ]};
  callbacks.native({phase: 'active', revision: 'session-a', session_id: 'session-a', remaining: 60, config});
  await flush();
  const status = await callbacks.message({type: 'status'}, {id: browser.runtime.id});
  assert.equal(JSON.stringify(status.config), JSON.stringify(config), 'popup receives website rules');
  assert.equal(await callbacks.message({type: 'status'}, {id: 'untrusted-page'}), undefined);
  assert.equal(updates[0][0], 1, 'already-open blocked tab replaced');
  assert.equal(reloads[0], 2, 'allowed page with previously loaded blocked frame reloads');
  assert.ok(callbacks.request({url: 'https://youtube.com'}).redirectUrl.includes('blocked.html'));
  assert.equal(Object.keys(callbacks.request({url: 'https://music.youtube.com'})).length, 0);
  await intervals[0]();
  assert.equal(sent.at(-1).revision, 'session-a', 'acknowledgement follows rule application');
  callbacks.disconnect();
  assert.ok(callbacks.request({url: 'https://youtube.com'}).redirectUrl, 'disconnect preserves active rules');
  now -= 3600000; // Same-boot wall-clock rollback must not extend a local session.
  monotonic += 60001;
  assert.equal(Object.keys(callbacks.request({url: 'https://youtube.com'})).length, 0, 'cached restrictions expire while offline');
  console.log('Firefox coordination checks passed: existing tabs, embedded frames, acknowledgement, disconnect, expiry');
}
main().catch(error => { console.error(error); process.exitCode = 1; });

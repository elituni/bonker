"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const active = {phase: 'active', revision: 'session-a', session_id: 'session-a', remaining: 60,
  config: {mode: 'blacklist', rules: [{host: 'youtube.com', action: 'block', subdomains: true}]}};
const idle = {phase: 'idle', revision: 'idle', config: null, remaining: 0};
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };

async function harness(options = {}) {
  const h = {wall: options.wall ?? 1000, mono: 0, managementError: options.managementError, ports: [], intervals: [], updates: [], reloads: [], frames: [], callbacks: {},
    storage: options.cache || {}, tabs: [{id: 1, url: 'https://youtube.com'}]};
  const event = (target, name) => ({addListener(fn) { target[name] = fn; }});
  const browser = {
    runtime: {id: 'focus-blocker@local.invalid', getURL: p => 'moz-extension://test/' + p,
      connectNative() {
        const p = {sent: [], disconnected: false, handlers: {}};
        p.onMessage = event(p.handlers, 'message');
        p.onDisconnect = event(p.handlers, 'disconnect');
        p.postMessage = msg => p.sent.push(msg);
        p.disconnect = () => { p.disconnected = true; p.handlers.disconnect(); };
        h.ports.push(p);
        return p;
      }, onMessage: event(h.callbacks, 'message')},
    management: {getSelf: async () => {
      if (h.managementError) throw Error('temporary management failure');
      return {installType: options.installType || 'admin'};
    }},
    extension: {isAllowedIncognitoAccess: async () => true},
    storage: {managed: {get: async () => ({localBridgeConsent: options.consent !== false})}, local: {
      get: async () => { if (options.readError) throw Error('cache unreadable'); return h.storage; },
      set: async values => {
        if (h.writeGate) await h.writeGate;
        if (h.writeError) throw Error('cache full');
        Object.assign(h.storage, values);
      },
      remove: async key => { delete h.storage[key]; }
    }},
    webRequest: {onBeforeRequest: {addListener(fn, filter, extra) {
      h.callbacks.request = fn; h.requestFilter = filter; h.requestExtra = extra;
    }}},
    webNavigation: {getAllFrames: async () => h.frames},
    tabs: {
      query: async () => h.tabs,
      get: async id => { const tab = h.tabs.find(t => t.id === id); if (!tab) throw Error('closed'); return tab; },
      update: async (id, value) => { if (h.updateError) throw Error('update failed'); h.updates.push([id, value]); },
      reload: async id => h.reloads.push(id), onUpdated: event(h.callbacks, 'updated')
    }
  };
  const context = vm.createContext({browser, crypto, URL, URLSearchParams, console,
    Date: {now: () => h.wall}, performance: {now: () => h.mono},
    setInterval: fn => h.intervals.push(fn), setTimeout: () => {}});
  for (const name of ['rules.js', 'background.js']) vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../firefox', name), 'utf8'), context);
  await flush();
  h.send = async (message, port = h.ports.at(-1)) => { port.handlers.message(message); await flush(); };
  h.tick = async () => { await h.intervals[0](); await flush(); };
  h.status = () => h.callbacks.message({type: 'status'}, {id: browser.runtime.id});
  return h;
}

async function main() {
  for (const options of [{consent: false}, {installType: 'normal'}]) {
    const h = await harness(options);
    assert.equal(h.ports.length, 0, 'no native communication without managed installation and explicit policy consent');
    assert.equal((await h.status()).setupRequired, true);
    assert.equal((await h.status()).phase, 'idle');
  }
  {
    const h = await harness({readError: true});
    assert.equal(h.ports.length, 1, 'cache read failure still connects native host');
    await h.send(idle);
    assert.equal((await h.status()).phase, 'idle');
  }
  {
    const h = await harness({cache: {session: {state: active, expiresWall: 61000}}});
    await h.send(active);
    assert.equal(h.updates.length, 1, 'restored session still sweeps already-open tabs');
  }
  {
    const h = await harness();
    h.updateError = true;
    await h.send(active);
    await h.tick();
    assert.equal(h.ports[0].sent.at(-1).revision, null, 'live tab failure is not acknowledged');
    assert.equal((await h.status()).connected, false);
    h.updateError = false;
    await h.send(active);
    await h.tick();
    assert.equal(h.ports[0].sent.at(-1).revision, 'session-a', 'same revision retries failed enforcement');
    assert.equal(h.updates.length, 1);
  }
  {
    const h = await harness({managementError: true});
    assert.equal(h.ports.length, 0);
    assert.equal(h.intervals.length, 1, 'startup API failure must not prevent retry scheduling');
    await h.tick();
    h.managementError = false;
    await h.tick();
    await h.send(active);
    assert.equal((await h.status()).phase, 'active', 'poll recovers after management API failure');
  }
  {
    const h = await harness();
    h.writeError = true;
    await h.send(active);
    await h.tick();
    assert.equal(h.ports[0].sent.at(-1).revision, null, 'cache write failure is not acknowledged');
    h.writeError = false;
    await h.send(active);
    await h.tick();
    assert.equal(h.ports[0].sent.at(-1).revision, 'session-a');
  }
  {
    const h = await harness();
    let unblock;
    h.writeGate = new Promise(resolve => { unblock = resolve; });
    const old = h.ports[0];
    await h.send(active, old);
    h.mono = 5000;
    h.wall = -1000000; // Timeout must use monotonic time, not the adjusted clock.
    await h.tick();
    const fresh = h.ports.at(-1);
    assert.notEqual(fresh, old);
    await h.send(idle, fresh);
    unblock();
    await flush();
    assert.equal((await h.status()).phase, 'idle', 'latest response wins after a slow stale write');
    assert.equal(h.storage.session, undefined, 'stale cache removed after release');
    old.handlers.disconnect();
    await h.send(active, old);
    assert.equal((await h.status()).phase, 'idle', 'late stale-port messages ignored');
    assert.equal((await h.status()).connected, true, 'late disconnect cannot clear fresh connection');
  }
  console.log('Firefox failure/recovery checks passed (cache failure, restored tabs, failed enforcement, stale ports and clock rollback)');
}
module.exports = {harness, flush, active, idle};
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });

"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {harness, active, idle, flush} = require('./test_extension_failures.js');
const pause = {...active, phase: 'break', revision: 'session-a:break-1',
  resume_revision: 'session-a:resume-1', break_remaining: 10};
const blocked = h => Boolean(h.callbacks.request({url: 'https://youtube.com'}).redirectUrl);

async function main() {
  {
    const h = await harness();
    await h.send(active);
    await h.send(pause);
    assert.equal(blocked(h), false, 'navigation allowed during an authorized break');
    h.callbacks.updated(1, {url: 'https://youtube.com'}, h.tabs[0]);
    await flush();
    assert.equal(h.updates.length, 1, 'tab update listener also respects the break');
    assert.equal((await h.status()).breakRemaining, 10);
    await h.tick();
    assert.equal(h.ports.at(-1).sent.at(-1).revision, pause.revision);
    h.ports.at(-1).disconnect();
    h.wall += 10001;
    h.mono += 10001;
    assert.equal(blocked(h), true, 'navigation resumes blocking immediately at deadline while offline');
    h.tabs.push({id: 2, url: 'https://example.org'});
    h.frames = [{frameId: 1, url: 'https://youtube.com/embed/1'}];
    await h.tick(); // Reconnect attempt; no service response is supplied.
    await h.tick();
    assert.equal(h.updates.length, 2, 'open blocked tab is swept without a service response');
    assert.deepEqual(h.reloads, [2], 'embedded blocked frame loaded during break is interrupted');
    assert.equal((await h.status()).phase, 'active');
    assert.equal(h.storage.session.state.revision, pause.resume_revision);
    h.wall += 4001; h.mono += 4001; // Let the unanswered native poll time out.
    await h.tick();
    assert.equal(h.ports.at(-1).sent.at(-1).revision, pause.resume_revision);
    await h.send(pause);
    assert.equal(blocked(h), true, 'late duplicate cannot reopen an expired break');
    const restarted = await harness({cache: JSON.parse(JSON.stringify(h.storage)), wall: h.wall});
    await restarted.send(pause);
    assert.equal(blocked(restarted), true, 'completed break remains completed across browser restart');
    await h.send({...pause, revision: 'session-a:break-2', resume_revision: 'session-a:resume-2'});
    assert.equal(blocked(h), false, 'a distinct later break can be authorized');
  }
  {
    const h = await harness();
    await h.send(pause);
    h.wall += 5000; h.mono += 5000;
    await h.send(pause);
    assert.equal((await h.status()).breakRemaining, 5, 'duplicate replies cannot extend the deadline');
    h.wall -= 3600000; h.mono += 5001;
    await h.tick();
    assert.equal(blocked(h), true, 'monotonic deadline survives same-process wall-clock rollback');
  }
  {
    const h = await harness();
    await h.send(pause);
    h.wall += 10001; // Suspend-like advance without performance clock progress.
    await h.tick();
    assert.equal(blocked(h), true, 'wall deadline handles suspend');
  }
  {
    const h = await harness();
    await h.send(pause);
    const cache = JSON.parse(JSON.stringify(h.storage));
    const restarted = await harness({cache, wall: 6000});
    assert.equal((await restarted.status()).breakRemaining, 5, 'restart preserves original deadline');
    assert.equal(blocked(restarted), false);
    restarted.wall = 11001; restarted.mono = 5001;
    await restarted.tick();
    assert.equal(blocked(restarted), true);
    assert.equal(restarted.updates.length, 1, 'restored break resumes existing-tab enforcement');
    const expired = await harness({cache: JSON.parse(JSON.stringify(h.storage)), wall: 12000});
    assert.equal(blocked(expired), true, 'restart after break expiry restores rules immediately');
    await expired.tick();
    assert.equal(expired.updates.length, 1);
    delete cache.session.breakWindow;
    const missing = await harness({cache, wall: 6000});
    assert.equal(blocked(missing), true, 'missing cached break deadline never grants unrestricted browsing');
  }
  {
    const h = await harness();
    await h.send({...pause, remaining: 10});
    h.wall += 10001; h.mono += 10001;
    await h.tick();
    assert.equal((await h.status()).phase, 'idle', 'session expiry takes precedence over break expiry');
    assert.equal(blocked(h), false);
    assert.equal(h.storage.session, undefined);
  }
  {
    const h = await harness();
    await h.send(pause);
    h.updateError = true;
    h.wall += 10001; h.mono += 10001;
    await h.tick(); await h.tick();
    assert.equal(blocked(h), true);
    assert.equal(h.ports.at(-1).sent.at(-1).revision, null, 'failed resume sweep is never acknowledged');
    h.updateError = false;
    h.wall += 4001; h.mono += 4001;
    await h.tick(); await h.tick();
    assert.equal(h.updates.length, 1, 'failed offline resume is retried without new service state');
    h.wall += 4001; h.mono += 4001;
    await h.tick();
    assert.equal(h.ports.at(-1).sent.at(-1).revision, pause.resume_revision);
  }
  {
    const h = await harness();
    await h.send(active);
    for (const bad of [{break_remaining: -1}, {break_remaining: 61}, {break_remaining: NaN},
      {break_remaining: '10'}, {resume_revision: ''}, {resume_revision: pause.revision},
      {session_id: null}, {config: null}, {remaining: 21601}]) {
      await h.send({...pause, ...bad});
      assert.equal(blocked(h), true, `invalid break rejected: ${JSON.stringify(bad)}`);
    }
    await h.send({...pause, break_remaining: 0});
    assert.equal(blocked(h), true, 'zero-length break never releases rules');
    await h.send({...pause, revision: 'next-break', resume_revision: 'next-resume'});
    await h.send({...active, revision: 'next-resume'});
    assert.equal(blocked(h), true, 'service can end break early');
    await h.send({...pause, revision: 'next-break', resume_revision: 'next-resume'});
    assert.equal(blocked(h), true, 'stale response cannot undo an early break end');
    await h.send({...pause, revision: 'last-break', resume_revision: 'last-resume'});
    await h.send(idle);
    assert.equal(blocked(h), false, 'service can end the whole session during a break');
  }
  {
    const h = await harness();
    let unblock;
    h.writeGate = new Promise(resolve => { unblock = resolve; });
    await h.send(pause);
    h.wall += 10001; h.mono += 10001;
    assert.equal(blocked(h), true, 'slow persistence cannot delay navigation protection');
    unblock();
    await flush();
    assert.equal(h.storage.session.state.phase, 'active', 'expiry during cache write persists resumed rules');
    assert.equal(h.updates.length, 1);
    await h.tick();
    assert.equal(h.ports.at(-1).sent.at(-1).revision, pause.resume_revision, 'expired break is never acknowledged as unrestricted');
  }
  {
    const h = await harness();
    await h.send(pause);
    h.writeError = true;
    h.wall += 10001; h.mono += 10001;
    await h.tick();
    assert.equal(blocked(h), true, 'cache failure cannot extend a break');
    assert.equal(h.updates.length, 1, 'open tabs are restricted even while cache writes fail');
    h.writeError = false;
    await h.tick();
    assert.equal(h.storage.session.state.phase, 'active');
    assert.equal(h.updates.length, 2, 'offline resume retries a failed cache write and confirms protection');
  }
  for (const file of ['popup.js', 'blocked.js']) {
    const elements = {};
    let status = {phase: 'break', breakRemaining: 65, remaining: 300, connected: false};
    let refresh;
    const context = vm.createContext({URLSearchParams, location: {search: '?host=youtube.com'},
      history: {back() {}}, browser: {runtime: {sendMessage: async () => status}},
      document: {getElementById: id => elements[id] ||= {textContent: '', addEventListener() {}, replaceChildren() {}},
        createElement: () => ({textContent: ''})},
      setInterval: fn => { refresh = fn; }});
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../firefox/presentation.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../firefox', file), 'utf8'), context);
    await flush();
    if (file === 'popup.js') assert.match(elements.state.textContent, /Bonks on break · 1:05/);
    else {
      assert.equal(elements.timer, undefined);
      assert.match(elements.status.textContent, /on break/);
    }
    status = {phase: 'active', remaining: 235, breakRemaining: 0, connected: true};
    await refresh();
    if (file === 'popup.js') assert.doesNotMatch(elements.state.textContent, /Bonks on break/);
    else {
      assert.equal(elements.status.textContent, '');
      assert.equal(elements.status.hidden, true);
    }
  }
  console.log('Firefox break checks passed: deadlines, restart, offline resume, tab/frame sweeps, validation and acknowledgements');
}
main().catch(error => { console.error(error); process.exitCode = 1; });

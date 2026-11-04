"use strict";
// Actual extension code, simulated browser/native APIs; NOT live enforcement.
const assert = require('node:assert/strict');
const {harness, active, flush} = require('./test_extension_failures.js');
const rules = require('../firefox/rules.js');

async function main() {
  const h = await harness();
  const config = {mode: 'whitelist', rules: [
    {host: 'chatgpt.com', action: 'allow', subdomains: true},
    {host: 'gemini.google.com', action: 'allow', subdomains: false},
    {host: 'blocked.chatgpt.com', action: 'block', subdomains: true}
  ]};
  h.tabs = [{id: 1, url: 'https://example.com'}, {id: 2, url: 'https://chatgpt.com'},
    {id: 3, url: 'file:///home/user/Documents/book.pdf'}];
  await h.send({...active, config});
  assert.deepEqual(h.updates.map(x => x[0]), [1], 'only the unlisted open page is redirected');
  for (const url of ['https://chatgpt.com', 'https://www.chatgpt.com',
    'https://gemini.google.com/app', 'file:///tmp/book.pdf', 'about:blank']) {
    assert.equal(rules.evaluate(url, config).allowed, true, url);
  }
  for (const url of ['https://example.com', 'https://chatgpt.com.evil.example',
    'https://google.com', 'https://blocked.chatgpt.com']) {
    const result = h.callbacks.request({url, type: 'main_frame'});
    assert.ok(result.redirectUrl.includes('blocked.html'), url);
  }
  assert.deepEqual(Array.from(h.requestFilter.types), ['main_frame', 'sub_frame'],
    'API/fetch/XHR/websocket traffic is outside the navigation listener');
  assert.deepEqual(Array.from(h.requestFilter.urls), ['http://*/*', 'https://*/*'],
    'local file/PDF navigation is outside the listener');
  // Simulate browser event filtering, rather than calling a navigation handler
  // with a request type Firefox would never deliver to it.
  const dispatch = details => h.requestFilter.types.includes(details.type)
    ? h.callbacks.request(details) : {};
  assert.equal(Object.keys(dispatch({type: 'xmlhttprequest', url: 'https://api.example/search'})).length, 0);
  assert.ok(dispatch({type: 'sub_frame', url: 'https://example.com'}).redirectUrl);
  h.callbacks.updated(4, {url: 'https://example.net'}, {id: 4});
  await flush();
  assert.equal(h.updates.at(-1)[0], 4, 'new unlisted page is redirected');
  h.ports.at(-1).disconnect();
  assert.ok(h.callbacks.request({url: 'https://example.com'}).redirectUrl);
  const restarted = await harness({cache: JSON.parse(JSON.stringify(h.storage)), wall: 2000});
  assert.ok(restarted.callbacks.request({url: 'https://example.com'}).redirectUrl,
    'cached whitelist remains restrictive after restart');
  h.wall += 60001; h.mono += 60001;
  assert.equal(Object.keys(h.callbacks.request({url: 'https://example.com'})).length, 0);
  console.log('PASS: whitelist defaults/exceptions, existing/new tabs, local PDFs, API request filter, restart/disconnect/expiry. Mock APIs; no live session.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });

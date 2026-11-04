'use strict';
const assert = require('node:assert/strict');
const view = require('../firefox/presentation.js');
assert.equal(view.popup({phase: 'idle'}).state, 'No bonks happening right now');
assert.equal(view.popup({phase: 'offline'}).showRules, false);
assert.equal(view.popup({phase: 'idle', setupRequired: true}).state.includes('not connected'), true);
const rules = [
  {host: 'youtube.com', action: 'block', subdomains: true},
  {host: 'music.youtube.com', action: 'allow', subdomains: true},
  {host: 'example.org', action: 'block', subdomains: false},
];
const status = {phase: 'active', remaining: 90, connected: true, config: {mode: 'blacklist', rules}};
assert.equal(view.popup(status).state, '1:30 remaining');
assert.equal(view.popup(status).blocked.length, 2);
assert.equal(view.popup(status).allowed.length, 1);
assert.equal(view.popup({...status, config: {...status.config, mode: 'whitelist'}}).whitelist, true);
assert.equal(view.popup({...status, connected: false}).state.includes('reconnecting'), true);
assert.equal(view.popup({...status, phase: 'break', breakRemaining: 10}).heading, 'Bonks resume after this break');
assert.equal(view.blocked(status).state, '');
assert.equal('timer' in view.blocked(status), false);
assert.match(view.blocked({...status, phase: 'idle'}).state, /over/);
assert.match(view.blocked({...status, phase: 'break', breakRemaining: 10}).state, /temporarily available/);
assert.equal(view.duration(3661), '1:01:01');
assert.equal(view.duration(-1), '0:00');
assert.equal(view.ruleLabel(rules[2]), 'example.org · exact host');
console.log('Bonker presentation checks passed: idle, rules/exceptions, whitelist default, break/offline/expiry and duration');

/* Display helpers only. The native service and background own all restrictions. */
"use strict";
const BonkerView = (() => {
  function duration(value) {
    const seconds = Math.max(0, Math.ceil(Number.isFinite(value) ? value : 0));
    return seconds >= 3600 ? `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}` :
      `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  }
  function popup(status) {
    const active = ['starting', 'active', 'break'].includes(status.phase);
    const config = active && !status.setupRequired ? status.config : null;
    let state;
    if (status.setupRequired) state = 'Bonker is not connected. Finish the managed installation.';
    else if (status.phase === 'idle') state = 'No bonks happening right now';
    else if (status.phase === 'offline') state = 'Bonker’s local service is unavailable.';
    else if (status.phase === 'starting') state = 'Preparing the bonks…';
    else if (status.phase === 'break') state = `Bonks on break · ${duration(status.breakRemaining)} remaining`;
    else state = `${duration(status.remaining)} remaining`;
    if (active && !status.connected && !status.setupRequired) state += ' · reconnecting';
    return {
      state, showRules: !!config, whitelist: config?.mode === 'whitelist',
      heading: status.phase === 'break' ? 'Bonks resume after this break' :
        config?.mode === 'blacklist' && !config.rules.some(r => r.action === 'block') ?
          'No websites are getting bonked' : 'These sites will get you bonked',
      blocked: (config?.rules || []).filter(r => r.action === 'block'),
      allowed: (config?.rules || []).filter(r => r.action === 'allow')
    };
  }
  function ruleLabel(rule) {
    return rule.host + (rule.subdomains ? ' · includes subdomains' : ' · exact host');
  }
  function blocked(status, offline = false) {
    if (status.phase === 'idle') return {state: 'Bonks are over. You can visit this site again.'};
    if (status.phase === 'break') return {state: 'Bonks are on break. This site is temporarily available.'};
    if (status.phase === 'offline') return {state: 'Bonker’s local service is unavailable.'};
    if (status.phase === 'starting') return {state: 'Preparing the bonks…'};
    return {state: offline ? 'Firefox could not verify this session with Bonker.' :
      status.connected ? '' : 'Bonks continue · reconnecting to Bonker'};
  }
  return {duration, popup, ruleLabel, blocked};
})();
if (typeof module !== 'undefined') module.exports = BonkerView;

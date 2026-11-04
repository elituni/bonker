/* Shared contract with focus_blocker/rules.py. No remote code or network calls. */
"use strict";
const FocusRules = (() => {
  function hostname(address) {
    const url = new URL(address);
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    return url.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  }
  function evaluate(address, config) {
    let host;
    try { host = hostname(address); } catch (_) { return {allowed: true, rule: null}; }
    if (!host || !config) return {allowed: true, rule: null};
    const matches = config.rules.filter(r => host === r.host || (r.subdomains && host.endsWith('.' + r.host)));
    matches.sort((a, b) => b.host.split('.').length - a.host.split('.').length || Number(a.subdomains) - Number(b.subdomains));
    const rule = matches[0] || null;
    return {host, allowed: rule ? rule.action === 'allow' : config.mode === 'blacklist', rule};
  }
  return {evaluate, hostname};
})();
if (typeof module !== 'undefined') module.exports = FocusRules;

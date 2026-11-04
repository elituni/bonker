"use strict";
function renderRules(id, rules) {
  const list = document.getElementById(id);
  list.replaceChildren(...rules.map(rule => {
    const item = document.createElement('li');
    item.textContent = BonkerView.ruleLabel(rule);
    return item;
  }));
  list.hidden = rules.length === 0;
}
async function refresh() {
  try {
    const status = await browser.runtime.sendMessage({type: 'status'});
    const view = BonkerView.popup(status);
    document.getElementById('state').textContent = view.state;
    document.getElementById('rules-section').hidden = !view.showRules;
    document.getElementById('rule-heading').textContent = view.heading;
    document.getElementById('default-rule').hidden = !view.whitelist;
    document.getElementById('default-rule').textContent = view.allowed.length ?
      'All other websites get bonked.' : 'All websites get bonked.';
    document.getElementById('allowed-section').hidden = view.allowed.length === 0;
    renderRules('blocked-rules', view.blocked);
    renderRules('allowed-rules', view.allowed);
  } catch (_) {
    document.getElementById('state').textContent = 'Unable to check the bonks.';
    document.getElementById('rules-section').hidden = true;
  }
}
refresh(); setInterval(refresh, 1000);

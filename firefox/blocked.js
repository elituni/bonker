"use strict";
const params = new URLSearchParams(location.search);
document.getElementById('host').textContent = params.get('host') || '';
document.getElementById('back').addEventListener('click', () => history.back());
async function refresh() {
  try {
    const status = await browser.runtime.sendMessage({type: 'status'});
    const view = BonkerView.blocked(status, params.has('offline'));
    document.getElementById('status').textContent = view.state;
    document.getElementById('status').hidden = !view.state;
  } catch (_) {
    document.getElementById('status').textContent = '';
    document.getElementById('status').hidden = true;
  }
}
refresh(); setInterval(refresh, 1000);

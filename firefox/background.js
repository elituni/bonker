"use strict";

let state = null;
let deadline = 0;
let expiresWall = 0;
let breakWindow = null;
let needsSync = false;
let connected = false;
let port = null;
let inFlight = false;
let initialized = false;
let appliedRevision = null;
let setupRequired = false;
let sentAt = 0;
let replies = Promise.resolve();
const instance = crypto.randomUUID();
const blockedUrl = browser.runtime.getURL('blocked.html');
let releaseStartup;
const startup = new Promise(resolve => { releaseStartup = resolve; });

function current() {
  if (state && state.phase !== 'idle' && (performance.now() >= deadline || Date.now() >= expiresWall)) {
    state = {phase: 'idle', revision: 'idle', config: null, remaining: 0};
    appliedRevision = null; // Must be acknowledged by the service before claiming idle.
    breakWindow = null;
    needsSync = true;
  } else if (state?.phase === 'break' && (!breakWindow ||
      performance.now() >= breakWindow.deadline || Date.now() >= breakWindow.expiresWall)) {
    state = {...state, phase: 'active', revision: state.resume_revision};
    appliedRevision = null;
    needsSync = true;
  }
  return state;
}

function snapshot() {
  const s = current();
  return {phase: s ? s.phase : 'offline', connected, setupRequired,
          config: s?.config ? {mode: s.config.mode, rules: s.config.rules.map(r =>
            ({host: r.host, action: r.action, subdomains: r.subdomains}))} : null,
          breakRemaining: s?.phase === 'break' ? Math.max(0, Math.ceil(Math.min(
            breakWindow.deadline - performance.now(), breakWindow.expiresWall - Date.now()) / 1000)) : 0,
          remaining: s && s.phase !== 'idle' ? Math.max(0, Math.ceil((deadline - performance.now()) / 1000)) : 0};
}

function blockPage(url, rule) {
  const params = new URLSearchParams({host: FocusRules.hostname(url) || '', rule: rule ? rule.host : 'Session default'});
  return blockedUrl + '?' + params;
}

function decision(details) {
  const s = current();
  if (!s) return {redirectUrl: blockedUrl + '?offline=1'};
  if (s.phase === 'break') return {};
  const match = FocusRules.evaluate(details.url, s.config);
  return match.allowed ? {} : {redirectUrl: blockPage(details.url, match.rule)};
}

browser.webRequest.onBeforeRequest.addListener(details => {
  // Wait for persisted-state loading / the first native reply before allowing navigation.
  return initialized ? decision(details) : startup.then(() => decision(details));
}, {urls: ['http://*/*', 'https://*/*'], types: ['main_frame', 'sub_frame']}, ['blocking']);

async function ifTabExists(id, action) {
  try {
    return await action();
  } catch (error) {
    // Closing a tab during the sweep is fine. A live tab that cannot be
    // restricted must not produce a successful protection acknowledgement.
    const tabs = await browser.tabs.query({});
    if (tabs.some(tab => tab.id === id)) throw error;
    return null;
  }
}

async function enforceOpenTabs(source) {
  const tabs = await browser.tabs.query({});
  for (const tab of tabs) {
    if (port !== source) throw new Error('Native connection changed during rule application');
    const s = current();
    if (!s || !s.config || s.phase === 'break') return;
    if (!tab.url || tab.url.startsWith(blockedUrl)) continue;
    if (!FocusRules.hostname(tab.url)) continue;
    const result = FocusRules.evaluate(tab.url, s.config);
    if (!result.allowed) {
      await ifTabExists(tab.id, () => browser.tabs.update(tab.id, {url: blockPage(tab.url, result.rule)}));
    } else {
      // Existing embedded documents must obey the same policy as future frames.
      const frames = await ifTabExists(tab.id, () => browser.webNavigation.getAllFrames({tabId: tab.id}));
      if ((frames || []).some(f => f.frameId !== 0 && !FocusRules.evaluate(f.url, s.config).allowed)) {
        // Reload interrupts old playback and re-applies the frame request filter.
        await ifTabExists(tab.id, () => browser.tabs.reload(tab.id));
      }
    }
  }
}

function validState(message) {
  if (!message || !['idle', 'starting', 'active', 'break'].includes(message.phase) || typeof message.revision !== 'string') return false;
  if (message.phase === 'idle') return message.config === null;
  if (message.phase === 'break' && !(typeof message.session_id === 'string' && message.session_id &&
      message.revision && typeof message.resume_revision === 'string' && message.resume_revision &&
      message.resume_revision !== message.revision && Number.isFinite(message.break_remaining) &&
      message.break_remaining >= 0 && message.break_remaining <= message.remaining)) return false;
  return Number.isFinite(message.remaining) && message.remaining >= 0 && message.remaining <= 21600 &&
    message.config && ['blacklist', 'whitelist'].includes(message.config.mode) &&
    Array.isArray(message.config.rules) && message.config.rules.length <= 500 &&
    message.config.rules.every(r => r && typeof r.host === 'string' &&
      ['allow', 'block'].includes(r.action) && typeof r.subdomains === 'boolean');
}

// Native replies and local deadline transitions share one persistence/enforcement queue.
// A resumed revision is acknowledged only after the cache and existing tabs agree.
async function synchronize(source) {
  while (port === source) {
    const observed = current();
    if (!needsSync || !observed) return;
    let cacheError;
    try {
      if (observed.phase === 'idle') await browser.storage.local.remove('session');
      else await browser.storage.local.set({session: {state: observed, expiresWall,
        breakWindow: breakWindow && {revision: breakWindow.revision, sessionId: breakWindow.sessionId,
          expiresWall: breakWindow.expiresWall}}});
    } catch (error) { cacheError = error; }
    if (port !== source) return;
    if (current() !== observed) continue;
    if (appliedRevision !== observed.revision) await enforceOpenTabs(source);
    if (port !== source) return;
    if (current() !== observed) continue;
    // Disk failure cannot leave already-open pages unrestricted after a break.
    // Still withhold acknowledgement until both enforcement and persistence work.
    if (cacheError) throw cacheError;
    needsSync = false;
    appliedRevision = observed.revision;
    return;
  }
}

function syncFailed(source) {
  if (port === source) { appliedRevision = null; connected = false; inFlight = false; }
}

async function accept(message, source) {
  if (port !== source) return;
  if (!message || message.error) { connected = false; inFlight = false; return; }
  if (!validState(message)) { connected = false; inFlight = false; return; }
  current();
  const sameSession = state && state.phase !== 'idle' && state.session_id === message.session_id;
  const oldDeadline = deadline;
  const oldExpiresWall = expiresWall;
  if (!sameSession) breakWindow = null;
  else if (message.phase === 'active' && breakWindow) {
    breakWindow = {...breakWindow, deadline: Math.min(breakWindow.deadline, performance.now()),
      expiresWall: Math.min(breakWindow.expiresWall, Date.now())};
  }
  if (appliedRevision !== message.revision) appliedRevision = null;
  state = message;
  const remaining = Math.max(0, Math.min(21600, Number(message.remaining) || 0));
  deadline = performance.now() + remaining * 1000;
  expiresWall = Date.now() + remaining * 1000;
  if (message.phase === 'break') {
    if (sameSession) {
      deadline = Math.min(deadline, oldDeadline);
      expiresWall = Math.min(expiresWall, oldExpiresWall);
    }
    const next = {revision: message.revision, sessionId: message.session_id,
      deadline: Math.min(deadline, performance.now() + message.break_remaining * 1000),
      expiresWall: Math.min(expiresWall, Date.now() + message.break_remaining * 1000)};
    // Repeated/stale replies for one break cannot restart or lengthen that break.
    if (breakWindow?.revision === next.revision) {
      next.deadline = Math.min(next.deadline, breakWindow.deadline);
      next.expiresWall = Math.min(next.expiresWall, breakWindow.expiresWall);
    }
    breakWindow = next;
  }
  needsSync = true;
  current();
  initialized = true;
  releaseStartup();
  await synchronize(source);
  if (port !== source) return;
  connected = true;
  inFlight = false;
}

async function heartbeat() {
  current();
  // Enterprise deployment explicitly authorizes local status reporting. Merely
  // loading the add-on must not transmit settings or identifiers to a native host.
  const self = await browser.management.getSelf();
  const consent = await browser.storage.managed.get('localBridgeConsent').catch(() => ({}));
  setupRequired = self.installType !== 'admin' || consent.localBridgeConsent !== true;
  if (setupRequired) {
    const oldPort = port;
    port = null;
    oldPort?.disconnect();
    inFlight = false;
    connected = false;
    state = {phase: 'idle', revision: 'idle', config: null};
    breakWindow = null;
    needsSync = true;
    appliedRevision = null;
    initialized = true;
    releaseStartup();
    return;
  }
  if (needsSync) {
    replies = replies.then(async () => {
      const source = port;
      try { await synchronize(source); } catch (_) { syncFailed(source); }
    });
  }
  if (inFlight && performance.now() - sentAt < 4000) return;
  if (inFlight) {
    const oldPort = port;
    port = null;
    oldPort?.disconnect();
    inFlight = false;
  }
  if (!port) {
    try {
      const source = browser.runtime.connectNative('org.focusblocker.bridge');
      port = source;
      source.onMessage.addListener(message => {
        // Serialize cache writes and rule sweeps, including across reconnection.
        replies = replies.then(() => accept(message, source)).catch(() => syncFailed(source));
      });
      source.onDisconnect.addListener(() => {
        if (port === source) { port = null; connected = false; inFlight = false; }
      });
    } catch (_) { connected = false; return; }
  }
  const source = port;
  inFlight = true;
  sentAt = performance.now();
  try {
    const privateAccess = await browser.extension.isAllowedIncognitoAccess();
    if (port !== source) return;
    source.postMessage({instance, revision: appliedRevision, private: privateAccess, managed: self.installType === 'admin'});
  } catch (_) {
    if (port === source) { port = null; connected = false; inFlight = false; source?.disconnect(); }
  }
}

browser.tabs.onUpdated.addListener((tabId, changes, tab) => {
  if (!initialized || !changes.url || changes.url.startsWith(blockedUrl)) return;
  const s = current();
  if (!s || s.phase === 'break') return;
  const result = FocusRules.evaluate(changes.url, s.config);
  if (!result.allowed) browser.tabs.update(tabId, {url: blockPage(changes.url, result.rule)}).catch(() => {});
});

browser.runtime.onMessage.addListener((message, sender) => {
  if (sender.id === browser.runtime.id && message.type === 'status') return Promise.resolve(snapshot());
});

(async () => {
  // Cache corruption/failure must not prevent contacting the authoritative service.
  const saved = await browser.storage.local.get('session').catch(() => ({}));
  if (saved.session && validState(saved.session.state) && Number.isFinite(saved.session.expiresWall) && saved.session.expiresWall > Date.now()) {
    state = saved.session.state;
    expiresWall = saved.session.expiresWall;
    deadline = performance.now() + Math.min(21600000, expiresWall - Date.now());
    const cachedBreak = saved.session.breakWindow;
    if (cachedBreak && typeof cachedBreak.revision === 'string' &&
        cachedBreak.sessionId === state.session_id && Number.isFinite(cachedBreak.expiresWall)) {
      const limit = state.phase === 'break' ? state.break_remaining * 1000 : 0;
      const end = Math.min(expiresWall, cachedBreak.expiresWall, Date.now() + limit);
      breakWindow = {...cachedBreak, expiresWall: end, deadline: performance.now() + end - Date.now()};
    }
    // Never restore an unrestricted break without its original persisted deadline.
    if (state.phase === 'break' && breakWindow?.revision !== state.revision) breakWindow = null;
    needsSync = true;
    current();
    initialized = true;
    releaseStartup();
  } else if (saved.session && validState(saved.session.state) && Number.isFinite(saved.session.expiresWall) && saved.session.expiresWall <= Date.now()) {
    state = {phase: 'idle', revision: 'idle', config: null};
    initialized = true;
    releaseStartup();
  }
  const poll = () => heartbeat().catch(() => { connected = false; });
  await poll();
  setInterval(poll, 1000);
  // Show a local explanation instead of hanging if installation is incomplete.
  setTimeout(() => { initialized = true; releaseStartup(); }, 5000);
})();

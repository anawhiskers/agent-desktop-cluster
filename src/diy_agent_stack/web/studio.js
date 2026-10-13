import {cursorStep, createMotionBudget, renderPresenceChip, renderCursorLabel} from './motion_v68.js';

const $ = selector => document.querySelector(selector);
const viewerKey = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
const views = new Map(), cursors = new Map(), budget = createMotionBudget();
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let inventory = [], current = null, connection = null, mode = 'watch', epoch = 0;
let timer = null, polling = false, animation = null, nativeSize = [1280, 800], fixture = false, novnc = false;

async function api(path, body) {
  const response = await fetch(path, {method: body ? 'POST' : 'GET', cache: 'no-store',
    headers: {'X-Stack-Viewer-Key': viewerKey, ...(body ? {'Content-Type': 'application/json'} : {})},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const value = await response.json();
  if (!response.ok) throw Error(value.error || 'Host request unavailable');
  return value;
}

function note(value) { $('#status').textContent = value; }
function host(id) { return inventory.find(item => item.id === id); }
function setMode(next) {
  mode = next;
  document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('selected', button.dataset.mode === mode));
  if (connection?.rfb) connection.rfb.viewOnly = mode !== 'control';
}
function disposeConnection() {
  if (connection?.rfb) { connection.rfb.viewOnly = true; connection.rfb.disconnect(); }
  connection = null;
  $('#vnc-password').value = '';
  if ($('#vnc-dialog').open) $('#vnc-dialog').close('cancel');
}
function errorScreen(value) {
  const notice = document.createElement('p'); notice.className = 'error-notice'; notice.textContent = value;
  $('#screen').replaceChildren(notice);
}

async function connectPrimary() {
  const token = ++epoch, selected = current, item = host(selected);
  disposeConnection(); setMode('watch');
  $('#screen').replaceChildren(); cursors.clear(); $('#presence-layer').replaceChildren();
  note(fixture ? 'Public fixture. No input or host connection.' : 'Connecting read-only view');
  if (!item?.vnc_websocket || !novnc || fixture) {
    $('#screen').append(document.createElement('img'));
    await poll();
    return;
  }
  try {
    const {default: RFB} = await import('/novnc/core/rfb.js');
    if (token !== epoch || current !== selected || document.hidden) return;
    const rfb = new RFB($('#screen'), item.vnc_websocket, {shared: true});
    connection = {rfb, token, host: selected}; rfb.viewOnly = true; rfb.scaleViewport = true;
    rfb.addEventListener('connect', () => {
      if (connection?.rfb !== rfb) return;
      note('Authenticated VNC view connected. Watch sends no input.');
      $('[data-mode="control"]').disabled = false;
    });
    rfb.addEventListener('disconnect', () => {
      if (connection?.rfb !== rfb) return;
      rfb.viewOnly = true; setMode('watch'); $('[data-mode="control"]').disabled = true;
      note('VNC disconnected. Agent pause stays in effect; reconnect explicitly.');
    });
    rfb.addEventListener('securityfailure', () => note('VNC authentication failed. Inspect the tunnel and owner password.'));
    rfb.addEventListener('credentialsrequired', () => {
      if (connection?.rfb !== rfb) return;
      $('#vnc-password').value = ''; $('#vnc-dialog').showModal();
      $('#vnc-dialog').addEventListener('close', () => {
        const value = $('#vnc-password').value; $('#vnc-password').value = '';
        if ($('#vnc-dialog').returnValue === 'connect' && connection?.rfb === rfb) rfb.sendCredentials({password: value});
        else if (connection?.rfb === rfb) { rfb.viewOnly = true; rfb.disconnect(); }
      }, {once: true});
    });
  } catch {
    if (token !== epoch) return;
    errorScreen('VNC view is unavailable. Check the local noVNC directory and owner-run tunnel. Snapshot view remains available after reconnecting without a VNC URL.');
  }
}

function selectHost(id) {
  if (!host(id)) return;
  current = id; $('[data-mode="control"]').disabled = true;
  $('#host-name').textContent = host(id).label; $('#host-description').textContent = host(id).description;
  $('#compact-machine').value = id;
  document.querySelectorAll('.machine').forEach(button => button.classList.toggle('selected', button.dataset.host === id));
  if (views.has(id)) { views.get(id).remove(); views.delete(id); }
  connectPrimary().catch(() => note('View request failed. Check the owner configuration.'));
}

function renderWork(status) {
  $('#work-state').replaceChildren();
  const fields = [status.paused ? 'Owner pause active' : 'Owner pause clear',
    status.work?.exclusive ? 'Exclusive reservation active' : 'Shared work allowed',
    status.work?.active_or_uncertain_work ? 'Running or uncertain work needs receipt checks' : 'No active work recorded'];
  for (const text of fields) { const span = document.createElement('span'); span.textContent = text; $('#work-state').append(span); }
}

function framebufferRect() {
  const pixels = $('#screen canvas') || $('#screen img');
  if (!pixels) return null;
  const rect = pixels.getBoundingClientRect(), parent = $('#presence-layer').getBoundingClientRect();
  // Snapshot images use contain; center the native aspect ratio within their CSS box.
  let width = rect.width, height = rect.height;
  if (pixels.tagName === 'IMG') {
    const scale = Math.min(width / nativeSize[0], height / nativeSize[1]);
    width = nativeSize[0] * scale; height = nativeSize[1] * scale;
  }
  return {left: rect.left - parent.left + (rect.width - width) / 2,
    top: rect.top - parent.top + (rect.height - height) / 2, width, height};
}

function presence(items) {
  const active = new Set(); $('#presence-roster').replaceChildren();
  for (const item of (Array.isArray(items) ? items : []).slice(0, 12)) {
    const id = String(item.agent_id || 'agent').slice(0, 48); active.add(id);
    const presentation = {...item, id, preset: item.color};
    const chip = document.createElement('span'); chip.className = 'presence-chip';
    renderPresenceChip(chip, presentation); $('#presence-roster').append(chip);
    if (!Number.isFinite(item.x) || !Number.isFinite(item.y) || item.x < 0 || item.y < 0 || item.x >= nativeSize[0] || item.y >= nativeSize[1]) continue;
    let cursor = cursors.get(id);
    if (!cursor) {
      const node = document.createElement('div'); node.className = 'collab-cursor';
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 30');
      const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', 'M1 1 L4 24 L10 16 L20 16 Z');
      path.setAttribute('fill', 'currentColor'); path.setAttribute('stroke', '#191022'); svg.append(path);
      const label = document.createElement('span'); label.className = 'collab-label'; node.append(svg, label);
      $('#presence-layer').append(node); cursor = {node, label, point: null, target: null}; cursors.set(id, cursor);
    }
    renderCursorLabel(cursor.node, cursor.label, presentation);
    cursor.target = {x: item.x, y: item.y};
  }
  for (const [id, cursor] of cursors) if (!active.has(id)) { cursor.node.remove(); cursors.delete(id); }
  animatePresence();
}

function animatePresence() {
  if (animation !== null || document.hidden) return;
  let last = performance.now();
  const paint = now => {
    animation = null; const rect = framebufferRect(); if (!rect) return;
    const motion = budget.sample(now, {active: true, reduced: reduced.matches});
    document.body.classList.toggle('motion-low', motion.low); let moving = false;
    for (const cursor of cursors.values()) {
      cursor.point = cursorStep(cursor.point, cursor.target, now - last, {snap: motion.low});
      const x = rect.left + cursor.point.x / nativeSize[0] * rect.width;
      const y = rect.top + cursor.point.y / nativeSize[1] * rect.height;
      cursor.node.style.transform = `translate(${x}px,${y}px)`; moving ||= !cursor.point.settled;
    }
    last = now; if (moving) animation = requestAnimationFrame(paint);
  };
  animation = requestAnimationFrame(paint);
}

async function updateHost(id, main, token) {
  const status = await api('/api/status?host=' + encodeURIComponent(id));
  if (main && (token !== epoch || id !== current)) return;
  if (main) {
    if (status.status === 'unknown_outcome') throw Error('Host status is unavailable. No state or success is assumed.');
    nativeSize = status.native_size || nativeSize; renderWork(status); presence(status.presence);
  }
  if (main && connection?.rfb || !main && !views.has(id)) return;
  const value = await api('/api/frame?host=' + encodeURIComponent(id));
  if (main && (token !== epoch || id !== current)) return;
  const image = main ? $('#screen img') : views.get(id)?.querySelector('img');
  if (!image || !value.image || !['image/jpeg', 'image/svg+xml'].includes(value.image_mime)) return;
  image.onload = () => { if (main) animatePresence(); };
  image.src = 'data:' + value.image_mime + ';base64,' + value.image;
  if (main) note(fixture ? 'Public fixture. No input or host connection.' : 'Fresh sampled screen. Take control requires authenticated VNC.');
}

async function poll() {
  if (polling || document.hidden || !current) return;
  polling = true; const selected = current, token = epoch;
  try {
    const results = await Promise.allSettled([updateHost(selected, true, token), ...[...views.keys()].map(id => updateHost(id, false, token))]);
    if (results[0].status === 'rejected' && token === epoch) note(results[0].reason.message || 'Host unavailable');
    results.slice(1).forEach((result, index) => { if (result.status === 'rejected') views.get([...views.keys()][index])?.classList.add('unavailable'); });
  } finally { polling = false; }
}

document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', async () => {
  const token = epoch, selected = current; if (!connection?.rfb) return;
  connection.rfb.viewOnly = true; setMode('watch'); button.disabled = true;
  try {
    const value = await api('/api/mode', {host: selected, mode: button.dataset.mode});
    if (token !== epoch || current !== selected || !connection?.rfb) return;
    setMode(value.mode); note(value.mode === 'control' ? 'Your controls are active. Agents are paused.' : 'Read-only view. Agents remain paused until private owner resume.');
  } catch (error) { note(error.message); }
  finally { if (token === epoch) button.disabled = false; }
}));
$('#compact-machine').addEventListener('change', event => selectHost(event.target.value));
$('#reconnect').onclick = () => connectPrimary().catch(() => note('Reconnect failed. Inspect the tunnel.'));
$('#fit').onclick = () => {
  $('#screen').classList.toggle('native');
  const native = $('#screen').classList.contains('native');
  if (connection?.rfb) connection.rfb.scaleViewport = !native;
  $('#fit').textContent = native ? 'Fit preview' : 'Native size'; animatePresence();
};
$('#fullscreen').onclick = () => (document.fullscreenElement ? document.exitFullscreen() : $('.workspace').requestFullscreen()).catch(() => note('Full screen is unavailable in this browser'));
document.querySelectorAll('.close-dialog').forEach(button => button.onclick = () => button.closest('dialog').close());
$('#history').onclick = async () => {
  const selected = current; $('#history-dialog').showModal(); $('#action-history').textContent = 'Loading metadata';
  try {
    const value = await api('/api/history?host=' + encodeURIComponent(selected)); $('#action-history').replaceChildren();
    for (const item of value.events || []) {
      const row = document.createElement('div'); row.className = 'history-row';
      const body = document.createElement('div'), name = document.createElement('strong'), state = document.createElement('small'), code = document.createElement('code');
      name.textContent = item.kind; state.textContent = item.status; code.textContent = item.id;
      body.append(name, state, code); row.append(body); $('#action-history').append(row);
    }
  } catch { $('#action-history').textContent = 'History unavailable. No completion is assumed.'; }
};
$('#workspace-add').onclick = () => {
  $('#view-options').replaceChildren();
  for (const item of inventory) {
    if (item.id === current || views.has(item.id)) continue;
    const button = document.createElement('button'); button.textContent = item.label; button.disabled = views.size >= 3;
    button.onclick = () => {
      if (views.size >= 3) return;
      const view = document.createElement('section'); view.className = 'secondary-view';
      const heading = document.createElement('h2'), image = document.createElement('img'), close = document.createElement('button');
      heading.textContent = item.label + ' · sampled watch'; image.alt = item.label + ' read-only desktop'; close.textContent = 'Close';
      close.onclick = () => { view.remove(); views.delete(item.id); };
      view.append(heading, image, close); $('#secondary-views').append(view); views.set(item.id, view); $('#views-dialog').close(); poll();
    };
    $('#view-options').append(button);
  }
  $('#views-dialog').showModal();
};
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    clearInterval(timer); timer = null;
    if (animation !== null) cancelAnimationFrame(animation); animation = null;
    disposeConnection(); setMode('watch'); $('[data-mode="control"]').disabled = true;
  } else { connectPrimary().catch(() => note('View unavailable after returning')); timer = setInterval(poll, 1200); }
});
window.addEventListener('pagehide', () => { clearInterval(timer); disposeConnection(); }, {once: true});
window.addEventListener('resize', animatePresence);

try {
  if (!viewerKey) throw Error('Open the private viewer URL from its owner terminal. Reloading after the key is cleared requires that URL again.');
  const config = await api('/api/config'); inventory = config.hosts; fixture = config.fixture; novnc = config.novnc_available;
  $('#demo-banner').hidden = !fixture; $('#fixture-label').textContent = fixture ? 'Public fixture only' : 'Loopback owner session';
  for (const item of inventory) {
    const button = document.createElement('button'); button.className = 'machine'; button.dataset.host = item.id;
    const text = document.createElement('div'), name = document.createElement('strong'), description = document.createElement('small');
    name.textContent = item.label; description.textContent = item.description; text.append(name, description); button.append(text);
    button.onclick = () => selectHost(item.id); $('#machines').append(button);
    const option = document.createElement('option'); option.value = item.id; option.textContent = item.label; $('#compact-machine').append(option);
  }
  selectHost(inventory[0].id); timer = setInterval(poll, 1200);
} catch (error) { note(error.message); errorScreen(error.message); }

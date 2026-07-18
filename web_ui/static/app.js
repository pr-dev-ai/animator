/**
 * Kids Animation Studio — studio workspace controller (vanilla ES module).
 *
 * The server is the source of truth: the pipeline stepper is hydrated from
 * GET /api/project/<p>/status, and every long operation streams progress
 * through ONE shared SSE helper (runSSE) into the persistent activity log.
 */

// ---------------------------------------------------------------------------
// Pipeline definition
// ---------------------------------------------------------------------------

const STAGES = [
  { id: 'project',    icon: '📁', name: 'Project' },
  { id: 'lyrics',     icon: '✍️', name: 'Lyrics' },
  { id: 'music',      icon: '🎵', name: 'Music' },
  { id: 'storyboard', icon: '🖼️', name: 'Storyboard' },
  { id: 'animate',    icon: '🎬', name: 'Animate' },
  { id: 'video',      icon: '🎞️', name: 'Video' },
];

const LANG_NAME_TO_CODE = { English: 'en', Hindi: 'hi' };
const LANG_CODE_TO_NAME = { en: 'English', hi: 'Hindi' };

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  project: null,
  activeStage: 'project',
  status: null,        // last /status snapshot for the active project
  running: null,       // stage id currently streaming, or null
  errored: null,       // stage id in an error state, or null
};

// ---------------------------------------------------------------------------
// Tiny DOM helpers
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;      // only used with trusted static strings
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, '');
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

function toast(message, type = 'info') {
  const container = $('toast-container');
  if (!container) return;
  const node = el('div', { class: `toast toast-${type}`, text: message });
  container.appendChild(node);
  node.getBoundingClientRect();
  node.classList.add('show');
  setTimeout(() => {
    node.classList.remove('show');
    node.addEventListener('transitionend', () => node.remove(), { once: true });
    setTimeout(() => node.remove(), 600);
  }, 4200);
}

function announce(msg) {
  const a = $('sr-announcer');
  if (a) a.textContent = msg;
}

async function api(method, path, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== null) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) throw new Error(`API error ${res.status} (non-JSON)`);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || `API error ${res.status}`);
  return data.data;
}

function busy(btn, on, label = 'Working…') {
  if (!btn) return;
  if (on) {
    btn.disabled = true;
    if (!btn.dataset.orig) btn.dataset.orig = btn.innerHTML;
    btn.innerHTML = `<span class="spin" aria-hidden="true"></span> ${label}`;
  } else {
    btn.disabled = false;
    if (btn.dataset.orig) { btn.innerHTML = btn.dataset.orig; delete btn.dataset.orig; }
  }
}

// ---------------------------------------------------------------------------
// Activity log + SSE (the ONE shared realtime helper)
// ---------------------------------------------------------------------------

function openDrawer(open) {
  const drawer = $('activity-drawer');
  const toggle = $('activity-toggle');
  drawer.hidden = !open;
  toggle.setAttribute('aria-expanded', String(open));
  const caret = open ? '▼' : '▲';
  toggle.lastChild.textContent = ` Activity ${caret}`;
}

function logLine(line) {
  const log = $('activity-log');
  log.textContent += line + '\n';
  log.scrollTop = log.scrollHeight;
}

function setLive(on) {
  const dot = $('activity-live');
  if (dot) dot.hidden = !on;
}

/**
 * Run a Server-Sent Events stream, funnelling every line into the shared
 * activity log. This is the single EventSource handler used by ALL stages.
 *
 * @param {string} url
 * @param {{stage?:string, label?:string, onLine?:Function, onDone?:Function, onError?:Function}} opts
 */
function runSSE(url, opts = {}) {
  const { stage, label, onLine, onDone, onError } = opts;
  openDrawer(true);
  setLive(true);
  if (label) logLine(`\n▸ ${label}`);
  if (stage) setStageRunning(stage);

  const src = new EventSource(url);
  let finished = false;

  const stop = () => { finished = true; src.close(); setLive(false); };

  src.onmessage = (e) => {
    const line = e.data;
    if (line === 'DONE') {
      stop();
      if (stage) clearStageRunning(stage);
      onDone && onDone();
    } else if (line.startsWith('ERROR')) {
      stop();
      logLine(line);
      if (stage) setStageError(stage);
      onError && onError(line);
    } else {
      logLine(line);
      onLine && onLine(line);
    }
  };

  src.onerror = () => {
    if (finished) return;
    stop();
    logLine('[stream disconnected]');
    if (stage) setStageError(stage);
    onError && onError('stream disconnected');
  };

  return src;
}

// ---------------------------------------------------------------------------
// Stepper
// ---------------------------------------------------------------------------

/** Compute {status, locked} for a stage from the current status snapshot. */
function stageState(id) {
  const s = state.status;
  const hasProject = !!state.project;

  // transient states win over disk-derived state
  if (state.running === id) return { status: 'progress', locked: false };
  if (state.errored === id) return { status: 'error', locked: false };

  if (!hasProject) {
    return id === 'project'
      ? { status: 'available', locked: false }
      : { status: 'locked', locked: true };
  }
  if (!s) return { status: id === 'project' ? 'done' : 'available', locked: id !== 'project' };

  const hasLyrics = s.has_lyrics;
  const hasImages = s.image_count > 0;
  const hasAnim = s.has_animation;
  const hasVideo = s.has_animatic || s.has_animation;

  switch (id) {
    case 'project':
      return { status: 'done', locked: false };
    case 'lyrics':
      return { status: hasLyrics ? 'done' : 'available', locked: false };
    case 'music':
      if (!hasLyrics) return { status: 'locked', locked: true };
      return { status: s.has_song ? 'done' : 'available', locked: false };
    case 'storyboard':
      if (!hasLyrics) return { status: 'locked', locked: true };
      return { status: hasImages ? 'done' : 'available', locked: false };
    case 'animate':
      if (hasAnim) return { status: 'done', locked: false };  // completed artifact never locks
      if (!hasImages) return { status: 'locked', locked: true };
      return { status: 'available', locked: false };
    case 'video':
      if (!hasImages && !hasAnim) return { status: 'locked', locked: true };
      return { status: hasVideo ? 'done' : 'available', locked: false };
    default:
      return { status: 'available', locked: false };
  }
}

function renderStepper() {
  const ol = $('stepper');
  ol.innerHTML = '';
  STAGES.forEach((stg, i) => {
    const { status, locked } = stageState(stg.id);
    const btn = el('button', {
      class: `step ${status}${stg.id === state.activeStage ? ' current' : ''}`,
      type: 'button',
      'data-stage': stg.id,
      'aria-current': stg.id === state.activeStage ? 'step' : false,
      title: locked ? `${stg.name} — locked` : stg.name,
      onclick: () => { if (!locked) focusStage(stg.id); else nudgeLocked(stg.id); },
    },
      el('span', { class: 'step-idx', 'aria-hidden': 'true', text: String(i + 1) }),
      el('span', { class: 'step-dot', 'aria-hidden': 'true' }),
      el('span', { class: 'step-body' },
        el('span', { class: 'step-name', text: stg.name }),
        el('span', { class: 'step-status', text: statusLabel(status) }),
      ),
      el('span', { class: 'step-icon', 'aria-hidden': 'true', text: stg.icon }),
    );
    if (locked) btn.classList.add('is-locked');
    ol.appendChild(btn);
  });
}

function statusLabel(status) {
  return { locked: 'Locked', available: 'Ready', progress: 'Working…', done: 'Done', error: 'Error' }[status] || '';
}

function nudgeLocked(id) {
  const prereq = {
    lyrics: 'Select a project first.',
    music: 'Generate lyrics first.',
    storyboard: 'Generate lyrics first.',
    animate: 'Generate storyboard images first.',
    video: 'Generate a storyboard or animation first.',
  }[id] || 'This stage is locked.';
  toast(prereq, 'warn');
}

function setStageRunning(id) { state.running = id; state.errored = null; renderStepper(); }
function clearStageRunning(id) { if (state.running === id) state.running = null; renderStepper(); }
function setStageError(id) { state.running = null; state.errored = id; renderStepper(); }

// ---------------------------------------------------------------------------
// Stage focus / navigation
// ---------------------------------------------------------------------------

function focusStage(id) {
  state.activeStage = id;
  // A stale error dot from an earlier failed run should not follow the user
  // around the session — clear it when they navigate.
  state.errored = null;
  document.querySelectorAll('.stage-panel').forEach(p => {
    p.classList.toggle('active', p.dataset.stage === id);
  });
  renderStepper();
  $('stage').scrollTop = 0;

  if (id === 'music') refreshMusicStage();
  if (id === 'storyboard') loadGallery();
  if (id === 'animate') refreshAnimateStage();
  if (id === 'video') refreshVideoStage();
}

// ---------------------------------------------------------------------------
// Status hydration
// ---------------------------------------------------------------------------

async function hydrateStatus() {
  if (!state.project) { state.status = null; renderStepper(); updateChrome(); return; }
  try {
    state.status = await api('GET', `/api/project/${encodeURIComponent(state.project)}/status`);
  } catch (err) {
    state.status = null;
  }
  renderStepper();
  updateChrome();
}

/** Update the top-bar Final Video affordance and the bottom media bar. */
function updateChrome() {
  const s = state.status;
  const hasVideo = !!(s && (s.has_animation || s.has_animatic));

  const fv = $('final-video-btn');
  fv.classList.toggle('dim', !hasVideo);
  fv.disabled = !hasVideo;

  // Media bar audio (song)
  const mbAudio = $('mb-audio');
  const mbTitle = $('mb-title');
  const mbVideoBtn = $('mb-video-btn');
  if (s && s.has_song && state.project) {
    const src = `/api/music/song/${encodeURIComponent(state.project)}`;
    if (mbAudio.dataset.for !== src) { mbAudio.src = src; mbAudio.dataset.for = src; }
    mbAudio.hidden = false;
    mbTitle.textContent = `${state.project} — song`;
  } else {
    mbAudio.hidden = true;
    mbAudio.removeAttribute('src');
    delete mbAudio.dataset.for;
    mbTitle.textContent = state.project ? `${state.project}` : 'Nothing loaded';
  }
  mbVideoBtn.hidden = !hasVideo;
}

// ---------------------------------------------------------------------------
// Project stage
// ---------------------------------------------------------------------------

async function loadProjects() {
  let projects = [];
  try { projects = await api('GET', '/api/project/list'); } catch (err) {
    toast(`Failed to load projects: ${err.message}`, 'error');
  }

  // dropdown
  const sel = $('project-select');
  const keep = sel.value;
  Array.from(sel.options).forEach(o => { if (o.value) o.remove(); });
  projects.forEach(name => sel.appendChild(el('option', { value: name, text: name })));
  if (state.project && projects.includes(state.project)) sel.value = state.project;
  else if (keep && projects.includes(keep)) sel.value = keep;

  // grid of cards
  const grid = $('project-grid');
  grid.innerHTML = '';
  if (!projects.length) {
    grid.appendChild(el('p', { class: 'empty', text: 'No projects yet — create one to begin.' }));
    return;
  }
  projects.forEach(name => {
    const card = el('button', {
      class: `proj-card${name === state.project ? ' selected' : ''}`,
      type: 'button',
      onclick: () => selectProject(name),
    },
      el('span', { class: 'proj-card-icon', 'aria-hidden': 'true', text: '🎬' }),
      el('span', { class: 'proj-card-name', text: name }),
    );
    grid.appendChild(card);
  });
}

async function selectProject(name) {
  state.project = name;
  $('project-select').value = name;
  document.querySelectorAll('.proj-card').forEach(c =>
    c.classList.toggle('selected', c.querySelector('.proj-card-name')?.textContent === name));
  toast(`Project “${name}” selected`, 'success');
  announce(`Project ${name} selected`);
  await hydrateAll();
}

/** Full re-hydration of every stage's server-backed data for the active project. */
async function hydrateAll() {
  await hydrateStatus();
  await loadLyricsIntoEditor();
  await loadPromptsIntoList();
  refreshMusicStage();
  refreshAnimateStage();
  refreshVideoStage();
  loadGallery();
}

async function createProject() {
  const name = $('project-name').value.trim();
  const type = $('project-type').value;
  if (!name) { toast('Enter a project name', 'error'); return; }
  if (!/^[A-Za-z0-9_-]+$/.test(name)) { toast('Name: letters, numbers, - and _ only', 'error'); return; }
  const btn = $('create-project-btn');
  busy(btn, true, 'Creating…');
  try {
    await api('POST', '/api/project/create', { name, type });
    $('project-name').value = '';
    toast(`Project “${name}” created`, 'success');
    await loadProjects();
    await selectProject(name);
    focusStage('lyrics');
  } catch (err) {
    toast(`Create failed: ${err.message}`, 'error');
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Lyrics stage
// ---------------------------------------------------------------------------

async function loadLyricsIntoEditor() {
  const ta = $('lyrics-textarea');
  const card = $('lyrics-editor-card');
  ta.value = '';
  card.hidden = true;
  if (!state.project) return;
  try {
    const lyrics = await api('GET', `/api/lyrics/${encodeURIComponent(state.project)}`);
    if (lyrics) { ta.value = lyrics; card.hidden = false; }
  } catch (_) {}
  // restore language selection from status
  if (state.status && state.status.language) {
    const nm = LANG_CODE_TO_NAME[state.status.language];
    if (nm) $('lyrics-language').value = nm;
  }
}

async function generateLyrics() {
  if (!requireProject()) return;
  const theme = $('lyrics-theme').value.trim();
  const style = $('lyrics-style').value.trim();
  const language = $('lyrics-language').value;
  const template = $('lyrics-template') ? $('lyrics-template').value : '';
  const verses = parseInt($('lyrics-verses').value, 10) || 3;
  if (!theme) { toast('Enter a theme', 'error'); return; }
  if (!await preflight()) return;

  const btn = $('generate-lyrics-btn');
  busy(btn, true, 'Writing…');
  try {
    const data = await api('POST', '/api/generate/lyrics', { theme, style, verses, language, template });
    const text = data.lyrics_text || '';
    $('lyrics-textarea').value = text;
    $('lyrics-editor-card').hidden = false;
    await saveLyrics(true);
    toast('Lyrics generated', 'success');
    focusStage('lyrics');
  } catch (err) {
    toast(`Lyrics failed: ${err.message}`, 'error');
  } finally {
    busy(btn, false);
  }
}

const TEMPLATE_STYLE = {};
async function loadTemplates() {
  const sel = $('lyrics-template');
  if (!sel) return;
  try {
    const data = await api('GET', '/api/templates');
    (data.templates || []).forEach((t) => {
      TEMPLATE_STYLE[t.id] = t.style;
      const opt = document.createElement('option');
      opt.value = t.id; opt.textContent = t.label;
      sel.appendChild(opt);
    });
  } catch (_) { /* templates are optional — leave the free-form choice only */ }
}

function applyTemplateStyle() {
  const st = TEMPLATE_STYLE[$('lyrics-template').value];
  const styleSel = $('lyrics-style');
  if (st && styleSel && Array.from(styleSel.options).some((o) => o.value === st)) {
    styleSel.value = st;                    // pre-fill a matching music style
  }
}

async function saveLyrics(silent) {
  if (!state.project) return;
  const lyrics = $('lyrics-textarea').value.trim();
  if (!lyrics) return;
  const language = $('lyrics-language').value;
  try {
    await api('POST', '/api/lyrics/save', { project: state.project, lyrics, language });
    if (!silent) toast('Lyrics saved', 'success');
    await hydrateStatus();
    refreshMusicStage();
  } catch (err) {
    if (!silent) toast(`Save failed: ${err.message}`, 'error');
  }
}

// ---------------------------------------------------------------------------
// Music stage
// ---------------------------------------------------------------------------

function refreshMusicStage() {
  const s = state.status;
  const label = $('music-lyrics-label');
  const bar = $('music-lyrics-state');
  const btn = $('generate-song-btn');
  const hasLyrics = !!(s && s.has_lyrics);

  bar.classList.toggle('ok', hasLyrics);
  bar.classList.toggle('warn', !hasLyrics);
  if (!state.project) label.textContent = 'Select a project first.';
  else if (hasLyrics) {
    const lang = s.language ? (LANG_CODE_TO_NAME[s.language] || s.language) : 'English';
    label.textContent = `Lyrics ready — will be sung in ${lang}.`;
  } else label.textContent = 'No lyrics yet — write them in the Lyrics stage.';
  if (btn) btn.disabled = !hasLyrics;

  // song player
  const player = $('song-player');
  if (s && s.has_song && state.project) {
    const base = `/api/music/song/${encodeURIComponent(state.project)}`;
    $('song-audio').src = `${base}?t=${Date.now()}`;
    $('song-download').href = base;
    player.hidden = false;
  } else {
    player.hidden = true;
  }
}

function generateSong() {
  if (!requireProject()) return;
  if (!(state.status && state.status.has_lyrics)) { toast('Generate lyrics first', 'warn'); return; }
  const btn = $('generate-song-btn');
  const langCode = LANG_NAME_TO_CODE[$('lyrics-language').value] || 'en';
  busy(btn, true, 'Generating song…');
  runSSE(
    `/api/music/generate?project=${encodeURIComponent(state.project)}&language=${langCode}`,
    {
      stage: 'music',
      label: `Music — generating sung song (${state.project})`,
      onDone: async () => {
        busy(btn, false);
        toast('Song ready!', 'success');
        await hydrateStatus();
        refreshMusicStage();
      },
      onError: (line) => { busy(btn, false); toast(line, 'error'); },
    },
  );
}

// ---------------------------------------------------------------------------
// Storyboard stage
// ---------------------------------------------------------------------------

async function loadPromptsIntoList() {
  const list = $('prompts-list');
  list.innerHTML = '';
  list.hidden = true;
  if (!state.project) return;
  try {
    const prompts = await api('GET', `/api/prompts/${encodeURIComponent(state.project)}`);
    if (prompts && prompts.length) renderPrompts(prompts);
  } catch (_) {}
}

function renderPrompts(prompts) {
  const list = $('prompts-list');
  list.innerHTML = '';
  prompts.forEach(p => {
    const card = el('div', { class: 'prompt-card' },
      el('div', { class: 'prompt-shot', text: p.shot_id || '' }),
      el('div', { class: 'prompt-text', text: p.prompt || '' }),
    );
    list.appendChild(card);
  });
  list.hidden = false;
}

async function generatePrompts() {
  if (!requireProject()) return;
  if (!await preflight()) return;
  const btn = $('generate-prompts-btn');
  busy(btn, true, 'Prompts…');
  try {
    const lyrics = $('lyrics-textarea').value.trim() || '';
    const prompts = await api('POST', '/api/generate/prompts',
      { project: state.project, style: 'cartoon 2d flat color', lyrics });
    renderPrompts(prompts || []);
    toast(`Generated ${prompts.length} shot prompts`, 'success');
    await hydrateStatus();
  } catch (err) {
    toast(`Prompts failed: ${err.message}`, 'error');
  } finally {
    busy(btn, false);
  }
}

function generateImages() {
  if (!requireProject()) return;
  const btn = $('generate-images-btn');
  busy(btn, true, 'Painting…');
  runSSE(
    `/api/storyboards/generate?project=${encodeURIComponent(state.project)}`,
    {
      stage: 'storyboard',
      label: `Storyboard — generating images (${state.project})`,
      onDone: async () => {
        busy(btn, false);
        toast('All images generated', 'success');
        await hydrateStatus();
        loadGallery();
      },
      onError: (line) => { busy(btn, false); toast(line, 'error'); },
    },
  );
}

async function loadGallery() {
  if (!state.project) return;
  const grid = $('gallery');
  const count = $('gallery-count');
  try {
    const images = await api('GET', `/api/storyboards/${encodeURIComponent(state.project)}`);
    grid.innerHTML = '';
    if (!images || !images.length) {
      grid.appendChild(el('p', { class: 'empty', text: 'No images yet — generate prompts, then images.' }));
      count.hidden = true;
      return;
    }
    count.textContent = String(images.length);
    count.hidden = false;
    images.forEach(fn => {
      const shot = fn.replace(/\.[^.]+$/, '');
      const src = `/api/image/${encodeURIComponent(state.project)}/${encodeURIComponent(fn)}`;
      const fig = el('figure', { class: 'shot' },
        el('img', { src, alt: shot, loading: 'lazy' }),
        el('figcaption', { text: shot }),
      );
      grid.appendChild(fig);
    });
  } catch (err) {
    toast(`Gallery failed: ${err.message}`, 'error');
  }
}

// ---------------------------------------------------------------------------
// Animate stage
// ---------------------------------------------------------------------------

function refreshAnimateStage() {
  const s = state.status;
  const player = $('animation-player');
  if (s && s.has_animation && state.project) {
    const base = `/api/animated/${encodeURIComponent(state.project)}`;
    $('animation-video').src = `${base}?t=${Date.now()}`;
    $('animation-download').href = base;
    player.hidden = false;
  } else {
    player.hidden = true;
  }
}

function buildAnimation() {
  if (!requireProject()) return;
  const btn = $('build-animation-btn');
  busy(btn, true, 'Animating…');
  runSSE(
    `/api/animation/build?project=${encodeURIComponent(state.project)}`,
    {
      stage: 'animate',
      label: `Animate — building 2.5D animation (${state.project})`,
      onDone: async () => {
        busy(btn, false);
        toast('Animation ready!', 'success');
        await hydrateStatus();
        refreshAnimateStage();
        refreshVideoStage();
      },
      onError: (line) => {
        busy(btn, false);
        // A "not available yet" ERROR is expected until the sibling module lands:
        // treat it as a "coming soon" notice, not a red error dot on the stepper.
        if (line.includes('not available yet')) {
          toast('Animation module is coming soon', 'warn');
          state.errored = null;
          renderStepper();
        } else {
          toast(line, 'error');
        }
      },
    },
  );
}

// ---------------------------------------------------------------------------
// Video stage
// ---------------------------------------------------------------------------

function refreshVideoStage() {
  const s = state.status;
  const player = $('final-player');
  const empty = $('final-empty');
  const title = $('final-player-title');

  let base = null;
  if (s && s.has_animation && state.project) {
    base = `/api/animated/${encodeURIComponent(state.project)}`;
    title.textContent = '▶️ Final music video (animated)';
  } else if (s && s.has_animatic && state.project) {
    base = `/api/animatic/${encodeURIComponent(state.project)}`;
    title.textContent = '▶️ Final music video (animatic)';
  }
  if (base) {
    $('final-video').src = `${base}?t=${Date.now()}`;
    $('final-download').href = base;
    player.hidden = false;
    empty.hidden = true;
  } else {
    player.hidden = true;
    empty.hidden = false;
  }
  renderSummary();
}

function renderSummary() {
  const s = state.status;
  const ul = $('summary-list');
  ul.innerHTML = '';
  const rows = [
    ['Project', state.project || '—', !!state.project],
    ['Lyrics', s && s.has_lyrics ? 'Written' : 'Not yet', !!(s && s.has_lyrics)],
    ['Song', s && s.has_song ? 'Generated (sung vocals)' : 'Not yet', !!(s && s.has_song)],
    ['Storyboard', s && s.image_count ? `${s.image_count} images` : 'Not yet', !!(s && s.image_count)],
    ['Animation', s && s.has_animation ? 'Built' : 'Not yet', !!(s && s.has_animation)],
    ['Final video', s && (s.has_animatic || s.has_animation) ? 'Ready' : 'Not yet', !!(s && (s.has_animatic || s.has_animation))],
  ];
  rows.forEach(([k, v, ok]) => {
    ul.appendChild(el('li', { class: ok ? 'ok' : '' },
      el('span', { class: 'sum-k', text: k }),
      el('span', { class: 'sum-v', text: v }),
    ));
  });
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

async function checkHealth() {
  try {
    const h = await api('GET', '/api/health');
    $('dot-comfyui').className = `svc-dot ${h.comfyui ? 'online' : 'offline'}`;
    $('dot-gpu').className = `svc-dot ${h.gpu ? 'online' : 'offline'}`;
  } catch (_) {
    $('dot-comfyui').className = 'svc-dot offline';
    $('dot-gpu').className = 'svc-dot offline';
  }
}

// ---------------------------------------------------------------------------
// Guards
// ---------------------------------------------------------------------------

function requireProject() {
  if (!state.project) { toast('Select a project first', 'warn'); focusStage('project'); return false; }
  return true;
}

let _configOk = null;
async function preflight() {
  if (_configOk === null) {
    try { await api('GET', '/api/config/check'); _configOk = true; }
    catch (err) { _configOk = false; toast(`API key error: ${err.message}`, 'error'); return false; }
  } else if (!_configOk) {
    toast('ANTHROPIC_API_KEY not configured — add it to .env and restart', 'error');
    return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function wire() {
  $('create-project-btn').addEventListener('click', createProject);
  $('new-project-btn').addEventListener('click', () => { focusStage('project'); $('project-name').focus(); });
  $('project-select').addEventListener('change', (e) => { if (e.target.value) selectProject(e.target.value); });

  $('generate-lyrics-btn').addEventListener('click', generateLyrics);
  if ($('lyrics-template')) $('lyrics-template').addEventListener('change', applyTemplateStyle);
  $('save-lyrics-btn').addEventListener('click', () => saveLyrics(false));
  $('lyrics-textarea').addEventListener('blur', () => saveLyrics(true));
  $('lyrics-language').addEventListener('change', () => saveLyrics(true));
  $('copy-lyrics-btn').addEventListener('click', () => {
    navigator.clipboard.writeText($('lyrics-textarea').value)
      .then(() => toast('Lyrics copied', 'success')).catch(() => toast('Copy failed', 'error'));
  });

  $('generate-song-btn').addEventListener('click', generateSong);
  $('generate-prompts-btn').addEventListener('click', generatePrompts);
  $('generate-images-btn').addEventListener('click', generateImages);
  $('refresh-gallery-btn').addEventListener('click', loadGallery);
  $('build-animation-btn').addEventListener('click', buildAnimation);

  $('final-video-btn').addEventListener('click', () => focusStage('video'));
  $('mb-video-btn').addEventListener('click', () => focusStage('video'));

  $('activity-toggle').addEventListener('click', () => openDrawer($('activity-drawer').hidden));
  $('clear-log-btn').addEventListener('click', () => { $('activity-log').textContent = ''; });

  $('rail-collapse').addEventListener('click', () => {
    const rail = $('rail');
    rail.classList.toggle('collapsed');
    $('rail-collapse').textContent = rail.classList.contains('collapsed') ? '›' : '‹';
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  wire();
  renderStepper();
  focusStage('project');
  loadTemplates();
  await loadProjects();
  checkHealth();
  setInterval(checkHealth, 30000);

  // Optional deep-link: ?p=<project>&s=<stage> selects a project and focuses a
  // stage on load, so a particular step can be bookmarked or shared.
  const params = new URLSearchParams(location.search);
  const p = params.get('p');
  if (p) {
    const known = Array.from($('project-select').options).some(o => o.value === p);
    if (known) {
      await selectProject(p);
      const s = params.get('s');
      if (s && STAGES.some(st => st.id === s)) {
        const { locked } = stageState(s);
        if (!locked) focusStage(s);
      }
    }
  }
});

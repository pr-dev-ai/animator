/**
 * Kids Animation Studio — Frontend JavaScript
 */

// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------
const state = {
  project: null,
  lyrics: null,
  prompts: [],
};

// ---------------------------------------------------------------------------
// State persistence (localStorage)
// ---------------------------------------------------------------------------
const STORAGE_KEY = 'kidsAnimStudio_v1';

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      project: state.project,
      lyrics: state.lyrics,
      tab: document.querySelector('.sidebar-nav .nav-item.active')?.dataset.tab || 'project',
    }));
  } catch (_) {}
}

function loadPersistedState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) { return null; }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  el.getBoundingClientRect();
  el.classList.add('toast-visible');
  setTimeout(() => {
    el.classList.remove('toast-visible');
    el.addEventListener('transitionend', () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 500);
  }, 4000);
}

function setLoading(btn, loading, loadingText = 'Working...') {
  if (!btn) return;
  if (loading) {
    btn.disabled = true;
    btn.dataset.originalText = btn.textContent;
    btn.textContent = loadingText;
  } else {
    btn.disabled = false;
    btn.textContent = btn.dataset.originalText || btn.textContent;
  }
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function api(method, path, body = null) {
  const options = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== null) options.body = JSON.stringify(body);
  const response = await fetch(path, options);
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new Error(`API error: ${response.status} (non-JSON response)`);
  }
  const data = await response.json();
  if (!data.ok) throw new Error(data.error || `API error: ${response.status}`);
  return data;
}

function appendLog(preEl, line) {
  preEl.textContent += line + '\n';
  preEl.scrollTop = preEl.scrollHeight;
}

// Show a CSS-hidden element (for elements hidden by ID rules in the stylesheet).
function show(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'block';
}

function hide(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}

// ---------------------------------------------------------------------------
// Pre-flight validation
// ---------------------------------------------------------------------------

// Cache result so we don't hit the endpoint on every button click.
let _configOk = null;

async function preflight(requireProject = false) {
  // Check API key once, cache result
  if (_configOk === null) {
    try {
      await api('GET', '/api/config/check');
      _configOk = true;
    } catch (err) {
      _configOk = false;
      toast(`API key error: ${err.message}`, 'error');
      return false;
    }
  } else if (!_configOk) {
    toast('ANTHROPIC_API_KEY is not configured in .env — restart the server after adding it', 'error');
    return false;
  }

  if (requireProject && !state.project) {
    toast('Select a project first (Project tab)', 'error');
    return false;
  }

  return true;
}

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------

function switchTab(tabName) {
  // CSS uses .active class on .tab-panel — toggle that, not the hidden attribute
  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.classList.remove('active');
  });

  // Panel IDs are panel-<name>, not tab-<name>
  const target = document.getElementById(`panel-${tabName}`);
  if (target) target.classList.add('active');

  // Sidebar items are <li class="nav-item"> with data-tab attribute
  document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => {
    const active = item.dataset.tab === tabName;
    item.classList.toggle('active', active);
    item.setAttribute('aria-selected', active ? 'true' : 'false');
    item.setAttribute('tabindex', active ? '0' : '-1');
  });

  // Load tab-specific data
  if (tabName === 'audio' && state.project) loadShotTable();
  if (tabName === 'storyboard' && state.project) loadGallery();

  saveState();
}

// ---------------------------------------------------------------------------
// Project tab
// ---------------------------------------------------------------------------

async function loadProjectList() {
  try {
    const data = await api('GET', '/api/project/list');
    const projects = data.data || [];

    // Populate project list
    const listEl = document.getElementById('project-list');
    if (listEl) {
      listEl.innerHTML = '';
      if (projects.length === 0) {
        const li = document.createElement('li');
        li.className = 'projects-empty';
        li.textContent = 'No projects yet. Create one above to get started.';
        listEl.appendChild(li);
      } else {
        projects.forEach(name => {
          const li = document.createElement('li');
          li.textContent = name;
          if (name === state.project) li.classList.add('selected');
          li.addEventListener('click', () => setProject(name));
          listEl.appendChild(li);
        });
      }
    }

    // Populate header dropdown
    const selectEl = document.getElementById('project-select');
    if (selectEl) {
      const currentValue = selectEl.value;
      Array.from(selectEl.options).forEach(opt => { if (opt.value !== '') opt.remove(); });
      projects.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        selectEl.appendChild(opt);
      });
      if (state.project && projects.includes(state.project)) {
        selectEl.value = state.project;
      } else if (currentValue && projects.includes(currentValue)) {
        selectEl.value = currentValue;
      }
    }
  } catch (err) {
    toast(`Failed to load projects: ${err.message}`, 'error');
  }
}

function setProject(name) {
  state.project = name;

  // Sync header dropdown
  const selectEl = document.getElementById('project-select');
  if (selectEl) selectEl.value = name;

  // Highlight selected item in list
  document.querySelectorAll('#project-list li:not(.projects-empty)').forEach(li => {
    li.classList.toggle('selected', li.textContent === name);
  });

  // Update the hint text
  const hintSpan = document.querySelector('#project-hint .hint-text');
  if (hintSpan) {
    hintSpan.textContent = `Project "${name}" selected. Click a tab in the sidebar to continue.`;
  }

  toast(`Project "${name}" selected`, 'success');
  saveState();
}

async function createProject() {
  // HTML input is id="project-name" (not "new-project-name")
  const nameInput = document.getElementById('project-name');
  const typeInput = document.getElementById('project-type');
  const btn = document.getElementById('create-project-btn');

  const name = nameInput ? nameInput.value.trim() : '';
  const type = typeInput ? typeInput.value : 'kids';

  if (!name) { toast('Project name is required', 'error'); return; }
  if (!/^[A-Za-z0-9_-]+$/.test(name)) {
    toast('Name may only contain letters, numbers, underscores, and hyphens', 'error');
    return;
  }

  setLoading(btn, true, 'Creating...');
  try {
    await api('POST', '/api/project/create', { name, type });
    toast(`Project "${name}" created!`, 'success');
    if (nameInput) nameInput.value = '';
    await loadProjectList();
    setProject(name);
  } catch (err) {
    toast(`Failed to create project: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Lyrics & Music tab
// ---------------------------------------------------------------------------

async function generateLyrics() {
  const themeInput = document.getElementById('lyrics-theme');
  const styleInput = document.getElementById('lyrics-style');
  const versesInput = document.getElementById('lyrics-verses');
  const languageInput = document.getElementById('lyrics-language');
  const btn = document.getElementById('generate-lyrics-btn');

  const theme = themeInput ? themeInput.value.trim() : '';
  const style = styleInput ? styleInput.value.trim() : '';
  const verses = versesInput ? parseInt(versesInput.value, 10) || 3 : 3;
  const language = languageInput ? languageInput.value.trim() : 'English';

  // Validate locally before any network call
  if (!theme) { toast('Enter a theme before generating lyrics', 'error'); themeInput?.focus(); return; }
  if (verses < 1 || verses > 10) { toast('Number of verses must be between 1 and 10', 'error'); versesInput?.focus(); return; }
  if (!await preflight()) return;

  setLoading(btn, true, 'Generating...');
  try {
    const data = await api('POST', '/api/generate/lyrics', { theme, style, verses, language });
    state.lyrics = data.data.lyrics_text;

    // CSS hides #lyrics-result with display:none — must use style.display to show it
    show('lyrics-result');
    show('chords-section');

    const textarea = document.getElementById('lyrics-textarea');
    if (textarea) textarea.value = data.data.lyrics_text;

    saveState();
    toast('Lyrics generated!', 'success');
  } catch (err) {
    toast(`Failed to generate lyrics: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

async function generateChords() {
  const btn = document.getElementById('generate-chords-btn');

  const textarea = document.getElementById('lyrics-textarea');
  if (textarea && textarea.value.trim()) state.lyrics = textarea.value.trim();

  // Validate before any network call
  if (!state.lyrics || state.lyrics.trim().length < 20) {
    toast('Add more lyrics before generating chords (at least a few lines)', 'error');
    return;
  }
  if (!await preflight()) return;

  setLoading(btn, true, 'Generating chords...');
  try {
    const data = await api('POST', '/api/generate/chords', { lyrics: state.lyrics, project: state.project || '' });

    // HTML: <div id="chords-display" class="hidden"> + <pre id="chords-pre">
    const chordsDisplay = document.getElementById('chords-display');
    if (chordsDisplay) chordsDisplay.classList.remove('hidden');

    const pre = document.getElementById('chords-pre');
    if (pre) {
      const parts = [];
      if (data.data.key) parts.push(`Key: ${data.data.key}`);
      if (data.data.tempo_bpm) parts.push(`Tempo: ${data.data.tempo_bpm} BPM`);
      if (data.data.strumming_pattern) parts.push(`Strumming: ${data.data.strumming_pattern}`);
      if (data.data.chord_chart) parts.push('', data.data.chord_chart);
      pre.textContent = parts.join('\n');
    }

    toast('Chords generated!', 'success');
  } catch (err) {
    toast(`Failed to generate chords: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

function copyToClipboard(text, label) {
  navigator.clipboard.writeText(text)
    .then(() => toast(`${label} copied!`, 'success'))
    .catch(() => toast(`Failed to copy ${label}`, 'error'));
}

// ---------------------------------------------------------------------------
// Storyboards tab
// ---------------------------------------------------------------------------

function renderPromptCard(shot) {
  const safeId = CSS.escape(shot.shot_id);
  const eShotId = escapeHtml(shot.shot_id);
  const eNegPrompt = escapeHtml(shot.negative_prompt);
  return `
    <div class="prompt-card" data-shot-id="${eShotId}">
      <div class="prompt-card-header">
        <span class="prompt-card-shot">${eShotId}</span>
      </div>
      <label class="prompt-label" for="prompt-${safeId}">Prompt</label>
      <textarea
        class="prompt-textarea form-control"
        id="prompt-${safeId}"
        rows="4"
        data-shot-id="${eShotId}"
      ></textarea>
      ${eNegPrompt ? `<div class="negative-prompt section-note">Negative: ${eNegPrompt}</div>` : ''}
    </div>
  `;
}

async function generatePrompts() {
  // Validate before any network call
  if (!await preflight(true)) return;

  const btn = document.getElementById('generate-prompts-btn');
  const styleInput = document.getElementById('styleguide-style') || document.getElementById('prompt-style');
  const style = (styleInput && styleInput.value.trim()) ? styleInput.value.trim() : 'cartoon 2d flat color';

  setLoading(btn, true, 'Generating prompts...');
  try {
    // Include current lyrics so Claude can write scene-specific prompts
    const lyricsNow = document.getElementById('lyrics-textarea')?.value.trim() || state.lyrics || '';
    const data = await api('POST', '/api/generate/prompts', { project: state.project, style, lyrics: lyricsNow });
    state.prompts = data.data || [];

    // CSS hides #prompts-list with display:none
    const listEl = document.getElementById('prompts-list');
    if (listEl) {
      listEl.style.display = 'block';
      listEl.innerHTML = state.prompts.map(renderPromptCard).join('');
      state.prompts.forEach(shot => {
        const ta = listEl.querySelector(`#prompt-${CSS.escape(shot.shot_id)}`);
        if (ta) ta.value = shot.prompt || '';
      });
    }

    saveState();
    toast(`Generated ${state.prompts.length} shot prompts!`, 'success');
  } catch (err) {
    toast(`Failed to generate prompts: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

async function generateImages() {
  if (!await preflight(true)) return;

  const logEl = document.getElementById('generate-images-log');
  const btn = document.getElementById('generate-images-btn');
  if (!logEl) return;

  logEl.classList.add('visible');
  logEl.textContent = '';
  setLoading(btn, true, 'Generating images...');

  const source = new EventSource(
    `/api/storyboards/generate?project=${encodeURIComponent(state.project)}`
  );
  let streamDone = false;

  source.onmessage = event => {
    const line = event.data;
    if (line === 'DONE') {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      toast('All images generated!', 'success');
      loadGallery();
    } else if (line.startsWith('ERROR:')) {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      toast(line, 'error');
      appendLog(logEl, line);
    } else {
      appendLog(logEl, line);
    }
  };

  source.onerror = () => {
    if (streamDone) return;
    source.close();
    setLoading(btn, false);
    toast('Image generation stream disconnected', 'error');
  };
}

async function loadGallery() {
  if (!state.project) return;

  // HTML: <div id="image-gallery"> (not "gallery-grid")
  const gridEl = document.getElementById('image-gallery');
  if (!gridEl) return;

  try {
    const data = await api('GET', `/api/storyboards/${state.project}`);
    const images = data.data || [];

    if (images.length === 0) {
      gridEl.innerHTML = '<p class="gallery-empty">No images yet — generate them in ComfyUI</p>';
      return;
    }

    gridEl.innerHTML = images.map(filename => {
      const shotId = filename.replace(/\.[^.]+$/, '');
      const encodedProject = encodeURIComponent(state.project);
      const encodedFile = encodeURIComponent(filename);
      const eShotId = escapeHtml(shotId);
      // CSS uses .gallery-item not .gallery-figure
      return `
        <div class="gallery-item">
          <img src="/api/image/${encodedProject}/${encodedFile}" alt="${eShotId}" loading="lazy" />
          <div class="shot-label">${eShotId}</div>
        </div>
      `;
    }).join('');
  } catch (err) {
    toast(`Failed to load gallery: ${err.message}`, 'error');
    gridEl.innerHTML = '<p class="gallery-empty">Failed to load images</p>';
  }
}

// ---------------------------------------------------------------------------
// Audio tab
// ---------------------------------------------------------------------------

function renderStatusBadge(hasWav) {
  return hasWav
    ? '<span class="badge badge-success">WAV ready</span>'
    : '<span class="badge badge-muted">No audio</span>';
}

async function loadShotTable() {
  if (!state.project) return;

  // HTML: <tbody id="shots-tbody"> (not #shot-table tbody)
  const tbody = document.getElementById('shots-tbody');
  if (!tbody) return;

  try {
    const data = await api('GET', `/api/project/${state.project}/shots`);
    const shots = data.data || [];

    if (shots.length === 0) {
      tbody.innerHTML = `<tr class="table-empty-row"><td colspan="5">No shots found for this project.</td></tr>`;
      return;
    }

    tbody.innerHTML = shots.map(shot => {
      const eShotId = escapeHtml(shot.shot_id);
      const eChar = escapeHtml(shot.character || '');
      const eDesc = escapeHtml(shot.description || '');
      const duration = shot.duration != null ? escapeHtml(String(shot.duration)) + 's' : '—';
      return `
        <tr data-shot-id="${eShotId}" data-character="${eChar}">
          <td>${eShotId}</td>
          <td>${eDesc}</td>
          <td>${duration}</td>
          <td>
            <input type="file" accept="audio/wav,audio/*" class="audio-upload"
              data-shot-id="${eShotId}" data-character="${eChar}" />
          </td>
          <td class="status-cell">${renderStatusBadge(shot.has_wav)}</td>
        </tr>
      `;
    }).join('');

    tbody.querySelectorAll('.audio-upload').forEach(input => {
      input.addEventListener('change', () => {
        if (input.files.length > 0) uploadAudio(input.dataset.shotId, input.dataset.character, input);
      });
    });
  } catch (err) {
    toast(`Failed to load shots: ${err.message}`, 'error');
  }
}

async function uploadAudio(shotId, character, fileInput) {
  if (!state.project) { toast('Please select a project first', 'error'); return; }
  const file = fileInput.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append('project', state.project);
  formData.append('shot_id', shotId);
  formData.append('character', character);
  formData.append('file', file);

  try {
    const response = await fetch('/api/audio/import', { method: 'POST', body: formData });
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'Upload failed');

    const row = document.querySelector(`#shots-tbody tr[data-shot-id="${CSS.escape(shotId)}"]`);
    if (row) {
      const cell = row.querySelector('.status-cell');
      if (cell) cell.innerHTML = renderStatusBadge(true);
    }
    toast(`Audio uploaded for ${shotId}`, 'success');
  } catch (err) {
    toast(`Failed to upload audio for ${shotId}: ${err.message}`, 'error');
  }
}

// ---------------------------------------------------------------------------
// Music — instrumental generation, vocals upload, mixing
// ---------------------------------------------------------------------------

async function generateInstrumental() {
  if (!await preflight(true)) return;
  const logEl = document.getElementById('instrumental-log');
  const btn = document.getElementById('gen-instrumental-btn');
  const playerDiv = document.getElementById('instrumental-player');
  if (!logEl) return;

  logEl.classList.add('visible');
  logEl.textContent = '';
  hide('instrumental-player');
  setLoading(btn, true, 'Generating...');

  const source = new EventSource(`/api/music/generate?project=${encodeURIComponent(state.project)}`);
  source.onmessage = event => {
    const line = event.data;
    if (line === 'DONE') {
      source.close();
      setLoading(btn, false);
      // Show player
      const audioEl = document.getElementById('instrumental-audio');
      const dlEl = document.getElementById('instrumental-download');
      if (audioEl) {
        audioEl.src = `/api/music/instrumental/${encodeURIComponent(state.project)}?t=${Date.now()}`;
        audioEl.load();
      }
      if (dlEl) dlEl.href = `/api/music/instrumental/${encodeURIComponent(state.project)}`;
      if (playerDiv) playerDiv.style.display = 'flex';
      toast('Instrumental ready!', 'success');
    } else if (line.startsWith('ERROR')) {
      appendLog(logEl, line);
      source.close();
      setLoading(btn, false);
      toast(line, 'error');
    } else {
      appendLog(logEl, line);
    }
  };
  source.onerror = () => { source.close(); setLoading(btn, false); };
}

async function uploadVocals() {
  if (!await preflight(true)) return;
  const fileInput = document.getElementById('vocal-file-input');
  const btn = document.getElementById('upload-vocals-btn');
  const statusEl = document.getElementById('vocals-status');

  if (!fileInput || !fileInput.files.length) {
    toast('Choose a WAV file first', 'error');
    return;
  }
  const file = fileInput.files[0];
  if (!file.name.toLowerCase().endsWith('.wav')) {
    toast('Please upload a .wav file', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('project', state.project);
  formData.append('file', file);

  setLoading(btn, true, 'Uploading...');
  try {
    const resp = await fetch('/api/music/vocals', { method: 'POST', body: formData });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || 'Upload failed');

    if (statusEl) statusEl.textContent = `Uploaded: ${data.data.saved} (${data.data.size_kb} KB)`;
    const audioEl = document.getElementById('vocal-audio');
    if (audioEl) {
      audioEl.src = `/api/music/vocals/${encodeURIComponent(state.project)}?t=${Date.now()}`;
      audioEl.load();
    }
    show('vocal-player');
    toast('Vocals uploaded!', 'success');
  } catch (err) {
    toast(`Upload failed: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

async function mixSong() {
  if (!await preflight(true)) return;
  const btn = document.getElementById('mix-song-btn');
  const ivol = (document.getElementById('mix-ivol')?.value || 70) / 100;
  const vvol = (document.getElementById('mix-vvol')?.value || 100) / 100;

  setLoading(btn, true, 'Mixing...');
  try {
    const data = await api('POST', '/api/music/mix', {
      project: state.project,
      instrumental_vol: ivol,
      vocal_vol: vvol,
    });
    const audioEl = document.getElementById('song-audio');
    const dlEl = document.getElementById('song-download');
    if (audioEl) {
      audioEl.src = `/api/music/song/${encodeURIComponent(state.project)}?t=${Date.now()}`;
      audioEl.load();
    }
    if (dlEl) dlEl.href = `/api/music/song/${encodeURIComponent(state.project)}`;
    show('song-player');
    toast(`Final song ready! (${data.data.size_kb} KB)`, 'success');
  } catch (err) {
    toast(`Mix failed: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

async function runLipsync() {
  if (!state.project) { toast('Please select a project first', 'error'); return; }

  const logEl = document.getElementById('lipsync-log');
  if (!logEl) return;

  // CSS uses .log-panel.visible to show — add class, clear text
  logEl.classList.add('visible');
  logEl.textContent = '';

  const btn = document.getElementById('run-lipsync-btn');
  setLoading(btn, true, 'Running lip-sync...');

  const source = new EventSource(`/api/lipsync/run?project=${encodeURIComponent(state.project)}`);
  let streamDone = false;

  source.onmessage = event => {
    const line = event.data;
    if (line === 'DONE') {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      toast('Lip-sync complete!', 'success');
      loadShotTable();
    } else if (line.startsWith('ERROR')) {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      toast(`Lip-sync error: ${line}`, 'error');
    } else {
      appendLog(logEl, line);
    }
  };

  source.onerror = () => {
    if (streamDone) return;
    source.close();
    setLoading(btn, false);
    toast('Lip-sync stream disconnected', 'error');
  };
}

// ---------------------------------------------------------------------------
// Video tab
// ---------------------------------------------------------------------------

async function buildAnimatic() {
  if (!state.project) { toast('Please select a project first', 'error'); return; }

  const logEl = document.getElementById('animatic-log');
  if (!logEl) return;

  const btn = document.getElementById('build-animatic-btn');
  // HTML: <video id="animatic-video"> (not "video-player")
  const videoEl = document.getElementById('animatic-video');
  const videoSection = document.getElementById('video-player-section');

  logEl.classList.add('visible');
  logEl.textContent = '';

  // CSS hides #video-player-section with display:none
  if (videoSection) videoSection.style.display = 'none';

  setLoading(btn, true, 'Building animatic...');

  const source = new EventSource(`/api/animatic/build?project=${encodeURIComponent(state.project)}`);
  let streamDone = false;

  source.onmessage = event => {
    const line = event.data;
    if (line === 'DONE') {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      if (videoSection) videoSection.style.display = 'block';
      if (videoEl) {
        videoEl.src = `/api/animatic/${state.project}`;
        videoEl.load();
      }
      const downloadBtn = document.getElementById('download-video-btn');
      if (downloadBtn) downloadBtn.href = `/api/animatic/${state.project}`;
      toast('Animatic ready!', 'success');
    } else if (line.startsWith('ERROR')) {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      toast(`Build error: ${line}`, 'error');
    } else {
      appendLog(logEl, line);
    }
  };

  source.onerror = () => {
    if (streamDone) return;
    source.close();
    setLoading(btn, false);
    toast('Build stream disconnected', 'error');
  };
}

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

async function checkHealth() {
  try {
    const data = await api('GET', '/api/health');

    // HTML: <span class="status-dot" id="dot-comfyui"> (not "comfyui-status")
    const dotComfy = document.getElementById('dot-comfyui');
    if (dotComfy) {
      dotComfy.className = `status-dot ${data.comfyui ? 'online' : 'offline'}`;
    }

    // Merge project list from health into dropdown
    if (Array.isArray(data.projects) && data.projects.length > 0) {
      const selectEl = document.getElementById('project-select');
      if (selectEl) {
        data.projects.forEach(name => {
          if (!Array.from(selectEl.options).some(o => o.value === name)) {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            selectEl.appendChild(opt);
          }
        });
        if (state.project) selectEl.value = state.project;
      }
    }
  } catch {
    const dotComfy = document.getElementById('dot-comfyui');
    if (dotComfy) dotComfy.className = 'status-dot offline';
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function restoreSession() {
  const saved = loadPersistedState();
  if (!saved) return;

  // Restore project silently (no toast — user didn't just select it)
  if (saved.project) {
    state.project = saved.project;
    const selectEl = document.getElementById('project-select');
    if (selectEl) selectEl.value = saved.project;
    document.querySelectorAll('#project-list li:not(.projects-empty)').forEach(li => {
      li.classList.toggle('selected', li.textContent === saved.project);
    });
    const hintSpan = document.querySelector('#project-hint .hint-text');
    if (hintSpan) hintSpan.textContent = `Project "${saved.project}" selected. Click a tab in the sidebar to continue.`;

    // Load saved prompts from disk
    try {
      const data = await api('GET', `/api/prompts/${encodeURIComponent(saved.project)}`);
      state.prompts = data.data || [];
      if (state.prompts.length) {
        const listEl = document.getElementById('prompts-list');
        if (listEl) {
          listEl.style.display = 'block';
          listEl.innerHTML = state.prompts.map(renderPromptCard).join('');
          state.prompts.forEach(shot => {
            const ta = listEl.querySelector(`#prompt-${CSS.escape(shot.shot_id)}`);
            if (ta) ta.value = shot.prompt || '';
          });
        }
      }
    } catch (_) {}

    // Reload gallery images from disk
    loadGallery();
  }

  // Restore lyrics — prefer localStorage, fall back to disk
  let restoredLyrics = saved.lyrics || null;
  if (!restoredLyrics && saved.project) {
    try {
      const ld = await api('GET', `/api/lyrics/${encodeURIComponent(saved.project)}`);
      if (ld.data) restoredLyrics = ld.data;
    } catch (_) {}
  }
  if (restoredLyrics) {
    state.lyrics = restoredLyrics;
    const textarea = document.getElementById('lyrics-textarea');
    if (textarea) textarea.value = restoredLyrics;
    show('lyrics-result');
    show('chords-section');
  }

  // Restore active tab
  if (saved.tab) switchTab(saved.tab);
}

document.addEventListener('DOMContentLoaded', async () => {
  switchTab('project');
  await loadProjectList();
  await restoreSession();
  checkHealth();
  setInterval(checkHealth, 30000);

  // Prevent form submission (buttons are type="submit" — would trigger GET reload)
  document.getElementById('create-project-form')?.addEventListener('submit', e => e.preventDefault());
  document.getElementById('lyrics-form')?.addEventListener('submit', e => e.preventDefault());

  // Button handlers
  document.getElementById('create-project-btn')?.addEventListener('click', createProject);
  document.getElementById('generate-lyrics-btn')?.addEventListener('click', generateLyrics);
  document.getElementById('generate-chords-btn')?.addEventListener('click', generateChords);
  document.getElementById('generate-prompts-btn')?.addEventListener('click', generatePrompts);
  document.getElementById('generate-images-btn')?.addEventListener('click', generateImages);
  document.getElementById('refresh-gallery-btn')?.addEventListener('click', loadGallery);
  document.getElementById('refresh-shots-btn')?.addEventListener('click', loadShotTable);
  document.getElementById('gen-instrumental-btn')?.addEventListener('click', generateInstrumental);
  document.getElementById('upload-vocals-btn')?.addEventListener('click', uploadVocals);
  document.getElementById('mix-song-btn')?.addEventListener('click', mixSong);
  document.getElementById('run-lipsync-btn')?.addEventListener('click', runLipsync);

  // Volume slider labels
  document.getElementById('mix-ivol')?.addEventListener('input', e => {
    const label = document.getElementById('mix-ivol-label');
    if (label) label.textContent = e.target.value + '%';
  });
  document.getElementById('mix-vvol')?.addEventListener('input', e => {
    const label = document.getElementById('mix-vvol-label');
    if (label) label.textContent = e.target.value + '%';
  });
  document.getElementById('build-animatic-btn')?.addEventListener('click', buildAnimatic);

  // Sidebar nav — HTML uses <li class="nav-item"> NOT <a> tags
  document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => {
    item.addEventListener('click', () => switchTab(item.dataset.tab));
    item.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); switchTab(item.dataset.tab); }
    });
  });

  // Header project dropdown
  document.getElementById('project-select')?.addEventListener('change', e => {
    if (e.target.value) setProject(e.target.value);
  });

  // Copy buttons
  document.getElementById('copy-lyrics-btn')?.addEventListener('click', () => {
    const ta = document.getElementById('lyrics-textarea');
    copyToClipboard(ta ? ta.value : '', 'Lyrics');
  });

  document.getElementById('copy-chords-btn')?.addEventListener('click', () => {
    const pre = document.getElementById('chords-pre');
    copyToClipboard(pre ? pre.textContent : '', 'Chords');
  });
});

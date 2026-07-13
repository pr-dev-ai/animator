/**
 * Kids Animation Studio — Frontend JavaScript
 * Single-page application controller for the 5-tab animation pipeline UI.
 */

// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------
const state = {
  project: null,   // currently selected project name
  lyrics: null,    // last generated lyrics text
  prompts: [],     // last generated storyboard prompts [{shot_id, prompt, negative_prompt}]
};

// ---------------------------------------------------------------------------
// Utility functions
// ---------------------------------------------------------------------------

/**
 * Show a toast notification.
 * @param {string} message
 * @param {'info'|'success'|'error'} type
 */
function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);

  // Trigger reflow so CSS transition fires
  el.getBoundingClientRect();
  el.classList.add('toast-visible');

  setTimeout(() => {
    el.classList.remove('toast-visible');
    el.addEventListener('transitionend', () => el.remove(), { once: true });
    // Fallback removal in case transitionend never fires
    setTimeout(() => el.remove(), 500);
  }, 4000);
}

/**
 * Set button loading state.
 * @param {HTMLButtonElement|null} btn
 * @param {boolean} loading
 * @param {string} loadingText
 */
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

/**
 * Escape a string for safe insertion into HTML content or attribute values.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Generic API call.
 * @param {'GET'|'POST'|'PUT'|'DELETE'} method
 * @param {string} path
 * @param {object|null} body
 * @returns {Promise<object>} parsed JSON
 */
async function api(method, path, body = null) {
  const options = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== null) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(path, options);

  // Guard against non-JSON bodies (e.g. Flask HTML error page on 500)
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new Error(`API error: ${response.status} (non-JSON response)`);
  }

  const data = await response.json();

  if (!data.ok) {
    throw new Error(data.error || `API error: ${response.status}`);
  }

  return data;
}

/**
 * Append a line to a log <pre> element and scroll to bottom.
 * @param {HTMLPreElement} preEl
 * @param {string} line
 */
function appendLog(preEl, line) {
  preEl.textContent += line + '\n';
  preEl.scrollTop = preEl.scrollHeight;
}

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------

/**
 * Switch the active tab panel.
 * @param {string} tabName
 */
function switchTab(tabName) {
  // Hide all tab panels
  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.hidden = true;
  });

  // Show the requested panel
  const target = document.getElementById(`tab-${tabName}`);
  if (target) target.hidden = false;

  // Update sidebar active state
  document.querySelectorAll('.sidebar-nav a').forEach(a => {
    a.classList.remove('active');
    if (a.dataset.tab === tabName) {
      a.classList.add('active');
    }
  });

  // Load tab-specific data
  if (tabName === 'audio' && state.project) {
    loadShotTable();
  }
  if (tabName === 'storyboards' && state.project) {
    loadGallery();
  }
}

// ---------------------------------------------------------------------------
// Project tab
// ---------------------------------------------------------------------------

/**
 * Load the project list from the API and populate UI elements.
 */
async function loadProjectList() {
  try {
    const data = await api('GET', '/api/project/list');
    const projects = data.data || [];

    // Populate sidebar project list
    const listEl = document.getElementById('project-list');
    if (listEl) {
      listEl.innerHTML = '';
      projects.forEach(name => {
        const li = document.createElement('li');
        li.textContent = name;
        li.addEventListener('click', () => setProject(name));
        listEl.appendChild(li);
      });
    }

    // Populate header dropdown
    const selectEl = document.getElementById('project-select');
    if (selectEl) {
      // Keep the placeholder option, rebuild project options
      const currentValue = selectEl.value;
      // Remove non-placeholder options
      Array.from(selectEl.options).forEach(opt => {
        if (opt.value !== '') opt.remove();
      });
      projects.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        selectEl.appendChild(opt);
      });
      // Restore selection if still valid
      if (currentValue && projects.includes(currentValue)) {
        selectEl.value = currentValue;
      } else if (state.project && projects.includes(state.project)) {
        selectEl.value = state.project;
      }
    }
  } catch (err) {
    toast(`Failed to load projects: ${err.message}`, 'error');
  }
}

/**
 * Select a project as the current working project.
 * @param {string} name
 */
function setProject(name) {
  state.project = name;

  // Update top bar display
  const currentProjectEl = document.getElementById('current-project');
  if (currentProjectEl) currentProjectEl.textContent = name;

  // Sync dropdown
  const selectEl = document.getElementById('project-select');
  if (selectEl) selectEl.value = name;

  toast(`Project: ${name}`, 'info');
}

/**
 * Create a new project from the form inputs.
 */
async function createProject() {
  const nameInput = document.getElementById('new-project-name');
  const typeInput = document.getElementById('project-type');
  const btn = document.getElementById('create-project-btn');

  const name = nameInput ? nameInput.value.trim() : '';
  const type = typeInput ? typeInput.value : 'kids_animation';

  if (!name) {
    toast('Project name is required', 'error');
    return;
  }

  if (!/^[A-Za-z0-9_-]+$/.test(name)) {
    toast('Project name may only contain letters, numbers, underscores, and hyphens', 'error');
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

/**
 * Generate lyrics using the API.
 */
async function generateLyrics() {
  const themeInput = document.getElementById('lyrics-theme');
  const styleInput = document.getElementById('lyrics-style');
  const versesInput = document.getElementById('lyrics-verses');
  const btn = document.getElementById('generate-lyrics-btn');

  const theme = themeInput ? themeInput.value.trim() : '';
  const style = styleInput ? styleInput.value.trim() : '';
  const verses = versesInput ? parseInt(versesInput.value, 10) || 2 : 2;

  if (!theme) {
    toast('Please enter a theme', 'error');
    return;
  }

  setLoading(btn, true, 'Generating...');
  try {
    const data = await api('POST', '/api/generate/lyrics', { theme, style, verses });
    state.lyrics = data.data.lyrics_text;

    // Show the result section
    const resultSection = document.getElementById('lyrics-result');
    if (resultSection) resultSection.hidden = false;

    // Fill the textarea
    const textarea = document.getElementById('lyrics-textarea');
    if (textarea) textarea.value = data.data.lyrics_text;

    // Show the generate chords button
    const chordsBtn = document.getElementById('generate-chords-btn');
    if (chordsBtn) chordsBtn.hidden = false;

    toast('Lyrics generated!', 'success');
  } catch (err) {
    toast(`Failed to generate lyrics: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

/**
 * Generate chord progressions for the current lyrics.
 */
async function generateChords() {
  const btn = document.getElementById('generate-chords-btn');

  if (!state.lyrics) {
    // Read from textarea in case user edited it
    const textarea = document.getElementById('lyrics-textarea');
    if (textarea && textarea.value.trim()) {
      state.lyrics = textarea.value.trim();
    } else {
      toast('Please generate lyrics first', 'error');
      return;
    }
  }

  // Sync lyrics state with textarea in case user edited it
  const textarea = document.getElementById('lyrics-textarea');
  if (textarea && textarea.value.trim()) {
    state.lyrics = textarea.value.trim();
  }

  setLoading(btn, true, 'Generating chords...');
  try {
    const data = await api('POST', '/api/generate/chords', { lyrics: state.lyrics });

    // Show the chords result card
    const resultCard = document.getElementById('chords-result');
    if (resultCard) resultCard.hidden = false;

    // Fill in chord details
    const keyEl = document.getElementById('chords-key');
    if (keyEl) keyEl.textContent = data.data.key || '—';

    const tempoEl = document.getElementById('chords-tempo');
    if (tempoEl) tempoEl.textContent = data.data.tempo_bpm ? `${data.data.tempo_bpm} BPM` : '—';

    const strummingEl = document.getElementById('chords-strumming');
    if (strummingEl) strummingEl.textContent = data.data.strumming_pattern || '—';

    const chartEl = document.getElementById('chords-chart');
    if (chartEl) chartEl.textContent = data.data.chord_chart || '';

    toast('Chords generated!', 'success');
  } catch (err) {
    toast(`Failed to generate chords: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

/**
 * Copy text to clipboard with a toast notification.
 * @param {string} text
 * @param {string} label
 */
function copyToClipboard(text, label) {
  navigator.clipboard.writeText(text)
    .then(() => toast(`${label} copied!`, 'success'))
    .catch(() => toast(`Failed to copy ${label}`, 'error'));
}

// ---------------------------------------------------------------------------
// Storyboards tab
// ---------------------------------------------------------------------------

/**
 * Build HTML for a single prompt card.
 * Uses escapeHtml() for all server-supplied fields.
 * The textarea value is set via .value after insertion to avoid `</textarea>` injection.
 * @param {{shot_id: string, prompt: string, negative_prompt: string}} shot
 * @returns {string} HTML string
 */
function renderPromptsCard(shot) {
  const safeId = CSS.escape(shot.shot_id);
  const eShotId = escapeHtml(shot.shot_id);
  const eNegPrompt = escapeHtml(shot.negative_prompt);
  // Note: prompt text is NOT placed inside the textarea here — it is set via
  // .value after the card is inserted into the DOM (see generatePrompts).
  return `
    <div class="prompt-card" data-shot-id="${eShotId}">
      <div class="prompt-card-header">
        <span class="shot-label">${eShotId}</span>
      </div>
      <label class="prompt-label">Prompt</label>
      <textarea
        class="prompt-textarea"
        id="prompt-${safeId}"
        rows="4"
        data-shot-id="${eShotId}"
        data-field="prompt"
      ></textarea>
      <label class="prompt-label negative-label">Negative prompt</label>
      <div class="negative-prompt">${eNegPrompt}</div>
    </div>
  `;
}

/**
 * Generate storyboard prompts from the API.
 */
async function generatePrompts() {
  if (!state.project) {
    toast('Please select a project first', 'error');
    return;
  }

  const btn = document.getElementById('generate-prompts-btn');
  // Try to read a style from a style input, fall back to default
  const styleInput = document.getElementById('styleguide-style') || document.getElementById('prompt-style');
  const style = (styleInput && styleInput.value.trim()) ? styleInput.value.trim() : 'cartoon 2d flat color';

  setLoading(btn, true, 'Generating prompts...');
  try {
    const data = await api('POST', '/api/generate/prompts', { project: state.project, style });
    state.prompts = data.data || [];

    // Render prompt cards, then set textarea .value per card (avoids </textarea> injection)
    const listEl = document.getElementById('prompts-list');
    if (listEl) {
      listEl.innerHTML = state.prompts.map(renderPromptsCard).join('');
      // Set prompt text safely via the DOM property, not via innerHTML
      state.prompts.forEach(shot => {
        const ta = listEl.querySelector(`#prompt-${CSS.escape(shot.shot_id)}`);
        if (ta) ta.value = shot.prompt || '';
      });
    }

    // Show ComfyUI section
    const comfySection = document.getElementById('comfyui-section');
    if (comfySection) comfySection.hidden = false;

    toast(`Generated ${state.prompts.length} shot prompts!`, 'success');
  } catch (err) {
    toast(`Failed to generate prompts: ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

/**
 * Load and render the storyboard image gallery.
 */
async function loadGallery() {
  if (!state.project) {
    toast('Please select a project first', 'error');
    return;
  }

  const gridEl = document.getElementById('gallery-grid');
  if (!gridEl) return;

  try {
    const data = await api('GET', `/api/storyboards/${state.project}`);
    const images = data.data || [];

    if (images.length === 0) {
      gridEl.innerHTML = '<p class="gallery-empty">No images yet — generate them in ComfyUI</p>';
      return;
    }

    gridEl.innerHTML = images
      .map(filename => {
        // Derive shot_id from filename (e.g. "SH010.png" → "SH010")
        const shotId = filename.replace(/\.[^.]+$/, '');
        // Encode for URL path segments; escape for HTML attributes/content
        const encodedProject = encodeURIComponent(state.project);
        const encodedFile = encodeURIComponent(filename);
        const eShotId = escapeHtml(shotId);
        return `
          <figure class="gallery-figure">
            <img
              src="/api/image/${encodedProject}/${encodedFile}"
              alt="${eShotId}"
              loading="lazy"
            />
            <figcaption>${eShotId}</figcaption>
          </figure>
        `;
      })
      .join('');
  } catch (err) {
    toast(`Failed to load gallery: ${err.message}`, 'error');
    gridEl.innerHTML = '<p class="gallery-error">Failed to load images</p>';
  }
}

// ---------------------------------------------------------------------------
// Audio tab
// ---------------------------------------------------------------------------

/**
 * Render a status badge for a shot's WAV state.
 * @param {boolean} hasWav
 * @returns {string} HTML string
 */
function renderStatusBadge(hasWav) {
  if (hasWav) {
    return '<span class="badge badge-success">WAV ready</span>';
  }
  return '<span class="badge badge-muted">No audio</span>';
}

/**
 * Load and render the shot table for the current project.
 */
async function loadShotTable() {
  if (!state.project) {
    return;
  }

  const tbody = document.querySelector('#shot-table tbody');
  if (!tbody) return;

  try {
    const data = await api('GET', `/api/project/${state.project}/shots`);
    const shots = data.data || [];

    tbody.innerHTML = shots
      .map(shot => {
        const eShotId = escapeHtml(shot.shot_id);
        const eChar = escapeHtml(shot.character);
        const eDesc = escapeHtml(shot.description);
        const duration = shot.duration != null ? escapeHtml(String(shot.duration)) + 's' : '—';
        return `
          <tr data-shot-id="${eShotId}" data-character="${eChar}">
            <td>${eShotId}</td>
            <td>${eDesc}</td>
            <td>${duration}</td>
            <td>
              <input
                type="file"
                accept="audio/wav,audio/*"
                class="audio-upload"
                data-shot-id="${eShotId}"
                data-character="${eChar}"
              />
            </td>
            <td class="status-cell">${renderStatusBadge(shot.has_wav)}</td>
          </tr>
        `;
      })
      .join('');

    // Wire upload handlers
    tbody.querySelectorAll('.audio-upload').forEach(input => {
      input.addEventListener('change', () => {
        const shotId = input.dataset.shotId;
        const character = input.dataset.character;
        if (input.files.length > 0) {
          uploadAudio(shotId, character, input);
        }
      });
    });
  } catch (err) {
    toast(`Failed to load shots: ${err.message}`, 'error');
  }
}

/**
 * Upload an audio file for a specific shot.
 * @param {string} shotId
 * @param {string} character
 * @param {HTMLInputElement} fileInput
 */
async function uploadAudio(shotId, character, fileInput) {
  if (!state.project) {
    toast('Please select a project first', 'error');
    return;
  }

  const file = fileInput.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append('project', state.project);
  formData.append('shot_id', shotId);
  formData.append('character', character);
  formData.append('file', file);

  try {
    const response = await fetch('/api/audio/import', {
      method: 'POST',
      body: formData,
    });
    const data = await response.json();

    if (!data.ok) {
      throw new Error(data.error || 'Upload failed');
    }

    // Update status badge for this row without full reload
    const row = document.querySelector(`#shot-table tbody tr[data-shot-id="${CSS.escape(shotId)}"]`);
    if (row) {
      const statusCell = row.querySelector('.status-cell');
      if (statusCell) statusCell.innerHTML = renderStatusBadge(true);
    }

    toast(`Audio uploaded for ${shotId}`, 'success');
  } catch (err) {
    toast(`Failed to upload audio for ${shotId}: ${err.message}`, 'error');
  }
}

/**
 * Run lipsync processing via SSE stream.
 */
async function runLipsync() {
  if (!state.project) {
    toast('Please select a project first', 'error');
    return;
  }

  const logEl = document.getElementById('lipsync-log');
  if (!logEl) return;

  // Show and clear log
  logEl.hidden = false;
  logEl.textContent = '';

  const btn = document.getElementById('run-lipsync-btn');
  setLoading(btn, true, 'Running lipsync...');

  const source = new EventSource(`/api/lipsync/run?project=${encodeURIComponent(state.project)}`);
  // Guard: some browsers fire onerror after close() is called from onmessage
  let streamDone = false;

  source.onmessage = event => {
    const line = event.data;
    if (line === 'DONE') {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      toast('Lipsync complete!', 'success');
      loadShotTable();
    } else if (line.startsWith('ERROR')) {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      toast(`Lipsync error: ${line}`, 'error');
    } else {
      appendLog(logEl, line);
    }
  };

  source.onerror = () => {
    if (streamDone) return; // suppress spurious onerror after intentional close
    source.close();
    setLoading(btn, false);
    toast('Lipsync stream disconnected', 'error');
  };
}

// ---------------------------------------------------------------------------
// Video tab
// ---------------------------------------------------------------------------

/**
 * Build the animatic video via SSE stream.
 */
async function buildAnimatic() {
  if (!state.project) {
    toast('Please select a project first', 'error');
    return;
  }

  const logEl = document.getElementById('animatic-log');
  if (!logEl) return;

  const btn = document.getElementById('build-animatic-btn');
  const videoEl = document.getElementById('video-player');

  // Show and clear log
  logEl.hidden = false;
  logEl.textContent = '';

  // Hide video player while building
  if (videoEl) videoEl.hidden = true;

  setLoading(btn, true, 'Building animatic...');

  const source = new EventSource(`/api/animatic/build?project=${encodeURIComponent(state.project)}`);
  // Guard: some browsers fire onerror after close() is called from onmessage
  let streamDone = false;

  source.onmessage = event => {
    const line = event.data;
    if (line === 'DONE') {
      streamDone = true;
      source.close();
      setLoading(btn, false);
      if (videoEl) {
        videoEl.hidden = false;
        videoEl.src = `/api/animatic/${state.project}`;
        videoEl.load();
      }
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
    if (streamDone) return; // suppress spurious onerror after intentional close
    source.close();
    setLoading(btn, false);
    toast('Animatic build stream disconnected', 'error');
  };
}

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

/**
 * Poll the health endpoint and update the ComfyUI status indicator.
 */
async function checkHealth() {
  try {
    const data = await api('GET', '/api/health');

    const statusEl = document.getElementById('comfyui-status');
    if (statusEl) {
      if (data.comfyui) {
        statusEl.textContent = '● ComfyUI online';
        statusEl.className = 'status-indicator status-online';
      } else {
        statusEl.textContent = '● ComfyUI offline';
        statusEl.className = 'status-indicator status-offline';
      }
    }

    // Merge project list from health into dropdown if available
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
        // Restore current project selection
        if (state.project) selectEl.value = state.project;
      }
    }
  } catch {
    // Health check failure is silent — status indicator stays as-is
    const statusEl = document.getElementById('comfyui-status');
    if (statusEl) {
      statusEl.textContent = '● ComfyUI offline';
      statusEl.className = 'status-indicator status-offline';
    }
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  switchTab('project');
  loadProjectList();
  checkHealth();
  setInterval(checkHealth, 30000);

  // ── Button handlers ─────────────────────────────────────────────────────
  const createProjectBtn = document.getElementById('create-project-btn');
  if (createProjectBtn) createProjectBtn.addEventListener('click', createProject);

  const generateLyricsBtn = document.getElementById('generate-lyrics-btn');
  if (generateLyricsBtn) generateLyricsBtn.addEventListener('click', generateLyrics);

  const generateChordsBtn = document.getElementById('generate-chords-btn');
  if (generateChordsBtn) generateChordsBtn.addEventListener('click', generateChords);

  const generatePromptsBtn = document.getElementById('generate-prompts-btn');
  if (generatePromptsBtn) generatePromptsBtn.addEventListener('click', generatePrompts);

  const refreshGalleryBtn = document.getElementById('refresh-gallery-btn');
  if (refreshGalleryBtn) refreshGalleryBtn.addEventListener('click', loadGallery);

  const refreshShotsBtn = document.getElementById('refresh-shots-btn');
  if (refreshShotsBtn) refreshShotsBtn.addEventListener('click', loadShotTable);

  const runLipsyncBtn = document.getElementById('run-lipsync-btn');
  if (runLipsyncBtn) runLipsyncBtn.addEventListener('click', runLipsync);

  const buildAnimaticBtn = document.getElementById('build-animatic-btn');
  if (buildAnimaticBtn) buildAnimaticBtn.addEventListener('click', buildAnimatic);

  // ── Sidebar nav ──────────────────────────────────────────────────────────
  document.querySelectorAll('.sidebar-nav a').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      switchTab(a.dataset.tab);
    });
  });

  // ── Header project dropdown ──────────────────────────────────────────────
  const projectSelect = document.getElementById('project-select');
  if (projectSelect) {
    projectSelect.addEventListener('change', e => {
      if (e.target.value) setProject(e.target.value);
    });
  }

  // ── Copy buttons ─────────────────────────────────────────────────────────
  const copyLyricsBtn = document.getElementById('copy-lyrics-btn');
  if (copyLyricsBtn) {
    copyLyricsBtn.addEventListener('click', () => {
      const textarea = document.getElementById('lyrics-textarea');
      copyToClipboard(textarea ? textarea.value : '', 'Lyrics');
    });
  }

  const copyChordsBtn = document.getElementById('copy-chords-btn');
  if (copyChordsBtn) {
    copyChordsBtn.addEventListener('click', () => {
      const chart = document.getElementById('chords-chart');
      copyToClipboard(chart ? chart.textContent : '', 'Chords');
    });
  }
});

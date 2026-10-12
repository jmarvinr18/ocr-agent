/* ==========================================================
   Document Processor — upload UI (vanilla JS)

   Flow per file
   1. POST { action: 'create_upload', filename } to the API (ALB → Lambda)
      → presigned S3 POST (url + fields)
   2. Upload the file straight to S3 with that presigned POST
   3. POST { action: 'process', key } → Lambda queues the conversion and
      returns at once (the ALB would time out on long documents)
   4. POST { action: 'status', key } every few seconds until the .md is
      saved → presigned links

   Structure
   - CONFIG   : limits and accepted file types
   - settings : API path + access key, kept in localStorage
   - api      : calls to the API and S3
   - state    : list of uploads
   - render   : DOM renderer driven by state
   - events   : wiring
   ========================================================== */

(function () {
  'use strict';

  /* ---------- Config ---------- */

  const CONFIG = {
    maxFileBytes: 50 * 1024 * 1024,
    acceptedExt: ['pdf', 'doc', 'docx', 'pptx', 'png', 'jpg', 'jpeg', 'webp'],
    // Same-origin path the ALB routes to the Lambda (Terraform `alb_path_pattern`).
    defaultApiUrl: '/api/document-processor',
    pollIntervalMs: 4000,
    pollTimeoutMs: 20 * 60 * 1000,
    storageKey: 'docproc.settings',
  };

  /* ---------- Icons ---------- */

  const ICON = {
    open: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/></svg>',
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  };

  /* ---------- Helpers ---------- */

  const $ = (sel) => document.querySelector(sel);

  const esc = (str) =>
    String(str ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);

  const uid = () => 'u_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

  const sizeLabel = (bytes) =>
    bytes < 1024 * 1024
      ? Math.max(1, Math.round(bytes / 1024)) + ' KB'
      : (bytes / 1048576).toFixed(1) + ' MB';

  const extOf = (name) => {
    const parts = name.split('.');
    return parts.length > 1 ? parts.pop().toLowerCase() : '';
  };

  const elapsed = (since) => {
    const s = Math.floor((Date.now() - since) / 1000);
    return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2, '0') + 's';
  };

  /* ---------- Settings ---------- */

  const settings = {
    load() {
      try {
        return { apiUrl: CONFIG.defaultApiUrl, apiKey: '', ...JSON.parse(localStorage.getItem(CONFIG.storageKey) || '{}') };
      } catch (_) {
        return { apiUrl: CONFIG.defaultApiUrl, apiKey: '' };
      }
    },
    save(value) {
      try { localStorage.setItem(CONFIG.storageKey, JSON.stringify(value)); } catch (_) { /* private mode */ }
    },
  };

  let current = settings.load();

  /* ---------- API ---------- */

  const api = {
    async call(body) {
      if (!current.apiUrl) throw new Error('Set the API endpoint under Connection first.');

      const headers = { 'Content-Type': 'application/json' };
      if (current.apiKey) headers['x-api-key'] = current.apiKey;

      let res;
      try {
        res = await fetch(current.apiUrl, { method: 'POST', headers, body: JSON.stringify(body) });
      } catch (_) {
        throw new Error('Could not reach the API. Check the endpoint under Connection.');
      }

      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || data.message || 'Request failed (' + res.status + ')');
      return data;
    },

    createUpload(file) {
      return api.call({ action: 'create_upload', filename: file.name });
    },

    /** Upload to S3 with the presigned POST. XHR (not fetch) so we get progress events. */
    uploadToS3(upload, file, onProgress) {
      return new Promise((resolve, reject) => {
        const form = new FormData();
        Object.entries(upload.fields).forEach(([k, v]) => form.append(k, v));
        form.append('file', file); // must be the last field

        const xhr = new XMLHttpRequest();
        xhr.open('POST', upload.url);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () =>
          xhr.status >= 200 && xhr.status < 300
            ? resolve()
            : reject(new Error('Upload to S3 failed (' + xhr.status + ')'));
        xhr.onerror = () => reject(new Error('Upload to S3 failed. Check the bucket CORS settings.'));
        xhr.send(form);
      });
    },

    startProcessing(key) {
      return api.call({ action: 'process', key });
    },

    /** Poll until the conversion finishes. Resolves with the links, rejects on failure. */
    async waitForResult(key) {
      const deadline = Date.now() + CONFIG.pollTimeoutMs;
      let failures = 0;

      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, CONFIG.pollIntervalMs));

        let status;
        try {
          status = await api.call({ action: 'status', key });
          failures = 0;
        } catch (err) {
          // Ride out a blip or two; give up if the API stays unreachable.
          if (++failures >= 3) throw err;
          continue;
        }

        if (status.state === 'done') return status;
        if (status.state === 'error') throw new Error(status.error || 'Processing failed.');
      }
      throw new Error('Still processing after 20 minutes. Check the Lambda logs.');
    },
  };

  /* ---------- State ---------- */

  const state = { uploads: [] };

  const update = (id, patch) => {
    const item = state.uploads.find((u) => u.id === id);
    if (item) Object.assign(item, patch);
    render();
  };

  /* ---------- Render ---------- */

  const els = {
    list: $('#uploadList'),
    title: $('#resultsTitle'),
    uploader: $('#uploader'),
    fileInput: $('#fileInput'),
    conn: $('#connStatus'),
    connLabel: $('#connLabel'),
    dialog: $('#settingsDialog'),
    apiUrl: $('#apiUrl'),
    apiKey: $('#apiKey'),
  };

  function renderConnection() {
    const on = Boolean(current.apiUrl);
    els.conn.classList.toggle('is-on', on);
    els.connLabel.textContent = on ? 'Endpoint set' : 'No endpoint set';
  }

  function statusText(u) {
    switch (u.status) {
      case 'uploading': return 'Uploading ' + u.progress + '%';
      case 'processing': return 'Processing · ' + elapsed(u.processingSince);
      case 'done': return 'Done';
      case 'error': return 'Failed';
      default: return 'Waiting';
    }
  }

  function linkRow(label, href, text) {
    return (
      '<div class="link-row">' +
        '<span class="link-row__label">' + esc(label) + '</span>' +
        '<a class="link-row__value" href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(text) + '</a>' +
        '<a class="icon-btn" href="' + esc(href) + '" target="_blank" rel="noopener" aria-label="Open ' + esc(label) + '">' + ICON.open + '</a>' +
      '</div>'
    );
  }

  function renderLinks(r) {
    const mdName = r.key.split('/').pop();
    const docName = r.document_key.split('/').pop();
    return (
      '<div class="links">' +
        linkRow('Markdown output', r.markdown_url, mdName) +
        linkRow('Uploaded document', r.document_url, docName) +
        '<div class="link-row">' +
          '<span class="link-row__label">S3 location</span>' +
          '<span class="link-row__value link-row__value--mono">' + esc(r.s3_uri) + '</span>' +
          '<button class="icon-btn" type="button" data-copy="' + esc(r.s3_uri) + '" aria-label="Copy S3 location">' + ICON.copy + '</button>' +
        '</div>' +
      '</div>'
    );
  }

  function renderUpload(u) {
    const statusClass = u.status === 'done' ? ' is-ready' : u.status === 'error' ? ' is-error' : '';
    const busy = u.status === 'uploading' || u.status === 'processing';
    const progress = busy
      ? '<div class="progress' + (u.status === 'processing' ? ' is-indeterminate' : '') + '">' +
          '<div class="progress__bar" style="width:' + (u.status === 'uploading' ? u.progress : 30) + '%"></div>' +
        '</div>'
      : '';
    const body =
      u.status === 'done' ? renderLinks(u.result)
      : u.status === 'error' ? '<p class="upload__error">' + esc(u.error) + '</p>'
      : '';
    const remove = busy
      ? ''
      : '<button class="icon-btn" type="button" data-remove="' + u.id + '" aria-label="Remove ' + esc(u.file.name) + '">' + ICON.close + '</button>';

    return (
      '<article class="upload">' +
        '<div class="upload__row">' +
          '<span class="file-ext">' + esc(extOf(u.file.name) || 'file') + '</span>' +
          '<span class="file-info">' +
            '<span class="file-name" title="' + esc(u.file.name) + '">' + esc(u.file.name) + '</span>' +
            '<span class="file-size">' + sizeLabel(u.file.size) + '</span>' +
          '</span>' +
          '<span class="upload__status' + statusClass + '">' + esc(statusText(u)) + '</span>' +
          remove +
        '</div>' +
        progress +
        body +
      '</article>'
    );
  }

  function render() {
    els.title.hidden = state.uploads.length === 0;
    els.list.innerHTML = state.uploads.map(renderUpload).join('');
  }

  // Keep the "Processing · 12s" timers ticking.
  setInterval(() => {
    if (state.uploads.some((u) => u.status === 'processing')) render();
  }, 1000);

  /* ---------- Upload flow ---------- */

  function validate(file) {
    if (!CONFIG.acceptedExt.includes(extOf(file.name))) {
      return 'Unsupported file type. Use PDF, DOC, DOCX, PPTX, PNG or JPG.';
    }
    if (file.size === 0) return 'The file is empty.';
    if (file.size > CONFIG.maxFileBytes) return 'The file is larger than 50 MB.';
    return null;
  }

  async function handle(file) {
    const id = uid();
    const error = validate(file);
    state.uploads.unshift({ id, file, status: error ? 'error' : 'uploading', progress: 0, error });
    render();
    if (error) return;

    try {
      const upload = await api.createUpload(file);
      await api.uploadToS3(upload, file, (progress) => update(id, { progress }));
      update(id, { status: 'processing', processingSince: Date.now() });

      await api.startProcessing(upload.key);
      const result = await api.waitForResult(upload.key);
      update(id, { status: 'done', result });
    } catch (err) {
      update(id, { status: 'error', error: err.message || String(err) });
    }
  }

  function addFiles(fileList) {
    Array.from(fileList).forEach(handle);
  }

  /* ---------- Events ---------- */

  els.fileInput.addEventListener('change', () => {
    addFiles(els.fileInput.files);
    els.fileInput.value = '';
  });

  // Drag & drop on the upload area.
  let dragDepth = 0;
  els.uploader.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragDepth++;
    els.uploader.classList.add('is-dragging');
  });
  els.uploader.addEventListener('dragover', (e) => e.preventDefault());
  els.uploader.addEventListener('dragleave', () => {
    if (--dragDepth <= 0) {
      dragDepth = 0;
      els.uploader.classList.remove('is-dragging');
    }
  });
  els.uploader.addEventListener('drop', (e) => {
    e.preventDefault();
    dragDepth = 0;
    els.uploader.classList.remove('is-dragging');
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });

  // Dropping a file anywhere else shouldn't navigate away from the page.
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => e.preventDefault());

  els.list.addEventListener('click', (e) => {
    const copyBtn = e.target.closest('[data-copy]');
    if (copyBtn) {
      navigator.clipboard?.writeText(copyBtn.dataset.copy).then(() => {
        copyBtn.setAttribute('aria-label', 'Copied');
        copyBtn.title = 'Copied';
      });
      return;
    }
    const removeBtn = e.target.closest('[data-remove]');
    if (removeBtn) {
      state.uploads = state.uploads.filter((u) => u.id !== removeBtn.dataset.remove);
      render();
    }
  });

  // Connection settings
  function openSettings() {
    els.apiUrl.value = current.apiUrl || '';
    els.apiKey.value = current.apiKey || '';
    els.dialog.showModal();
  }

  $('#openSettings').addEventListener('click', openSettings);
  $('#cancelSettings').addEventListener('click', () => els.dialog.close());

  $('#settingsForm').addEventListener('submit', () => {
    current = { apiUrl: els.apiUrl.value.trim(), apiKey: els.apiKey.value };
    settings.save(current);
    renderConnection();
  });

  /* ---------- Init ---------- */

  renderConnection();
  render();
  if (!current.apiUrl) openSettings();
})();

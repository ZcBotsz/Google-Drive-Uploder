/* DriveUp Studio — wizard steps: files → review.
   Classic script (no bundler): app-core.js defines state + helpers,
   app-views.js the wizard steps, app.js wires the page and boots. */
// ── step 3: naming ──────────────────────────────────────────────────────────
/** Whatever the folder step decided about splitting, remembered for review+start. */
function currentSplit() {
  const sel = $('#splitMode');
  if (!sel) return S.split || null;
  const mode = sel.value;
  if (mode === 'none') return null;
  if (mode === 'tree') {
    const folders = (S.manualFolders || []).map(path => [...path]);
    const plan = {};
    for (const f of S.files) plan[f.id] = [...((S.manualRoutes || {})[f.id] || [])];
    const existing_plan = {}; const existing_folders = {...(S.manualExistingFolders || {})};
    for (const f of S.files) if (S.manualExistingRoutes?.[f.id]) existing_plan[f.id] = S.manualExistingRoutes[f.id];
    return S.files.length ? {mode, folders, plan, existing_plan, existing_folders} : null;
  }
  if (mode === 'selected') {
    const plan = {}; const destinations = {};
    for (const f of S.files) {
      const target = (S.fileRoutes || {})[f.id];
      if (!target || !target.id) return null;
      plan[f.id] = target.id; destinations[target.id] = target.name;
    }
    return S.files.length ? {mode, plan, destinations} : null;
  }
  if (mode === 'batches') {
    const groups = (S.folderGroups || []).map((g) => ({
      name: String(g.name || '').trim(), count: Number(g.count) || 0,
    }));
    const total = groups.reduce((sum, g) => sum + g.count, 0);
    const uniqueNames = new Set(groups.map((g) => g.name.toLocaleLowerCase()));
    if (!S.files.length || groups.some((g) => !g.name || g.count < 1)
        || uniqueNames.size !== groups.length || total !== S.files.length) return null;
    const plan = {}; let offset = 0;
    for (const group of groups) {
      for (const file of S.files.slice(offset, offset + group.count)) plan[file.id] = [group.name];
      offset += group.count;
    }
    return { mode: 'batches', groups, plan, rules: [], else: 'Other' };
  }
  const parts = ($('#splitRule') && $('#splitRule').value || '').split('→').map((x) => x.trim());
  return {
    mode,
    rules: parts.length === 2 && parts[0] ? [{ match: parts[0], folder: parts[1] }] : [],
    'else': ($('#splitElse') && $('#splitElse').value.trim()) || 'Other',
  };
}

function viewNaming(view) {
  const c = el('div', 'card');
  c.innerHTML = `
    <h2>Naming</h2>
    <p class="dim">Template variables: {original} {ext} {seq:03d} {index} {total} {date}
       {time} {size} {type} {caption} {message_id}</p>
    <label class="field"><span>Template</span>
      <input type="text" id="tmpl" value="${esc(S.naming.template)}" spellcheck="false"></label>
    <div class="grid two">
      <label class="field"><span>Start number</span>
        <input type="number" id="nstart" value="${S.naming.start}" min="0"></label>
      <label class="field"><span>Step</span>
        <input type="number" id="nstep" value="${S.naming.step}" min="1"></label>
      <label class="field"><span>Prefix</span>
        <input type="text" id="nprefix" value="${esc(S.naming.prefix)}"></label>
      <label class="field"><span>Suffix</span>
        <input type="text" id="nsuffix" value="${esc(S.naming.suffix)}"></label>
      <label class="field"><span>Find</span>
        <input type="text" id="nfind" value="${esc(S.naming.find)}"></label>
      <label class="field"><span>Replace</span>
        <input type="text" id="nreplace" value="${esc(S.naming.replace)}"></label>
      <label class="field"><span>Case</span>
        <select id="ncase">
          ${['keep', 'title', 'lower', 'upper'].map((v) =>
            `<option value="${v}" ${S.naming.case === v ? 'selected' : ''}>${v}</option>`).join('')}
        </select></label>
      <label class="field"><span>Spaces</span>
        <select id="nspace">
          ${[[' ', 'keep'], ['_', 'underscore'], ['-', 'dash']].map(([v, l]) =>
            `<option value="${v}" ${S.naming.space === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select></label>
    </div>
    <div class="row" style="margin:8px 0 12px">
      <label class="switch"><input type="checkbox" id="nregex" ${S.naming.regex ? 'checked' : ''}>
        <span>Use regex in Find</span></label>
      <label class="switch"><input type="checkbox" id="nemoji" ${S.naming.strip_emoji ? 'checked' : ''}>
        <span>Strip emoji</span></label>
    </div>
    <div class="row tight" style="margin-bottom:10px">
      <button class="btn sm" data-tpl="{seq:03d} - {original}.{ext}">001 - Intro</button>
      <button class="btn sm" data-tpl="Module 1 - {index:02d} {original}">Module 1 - 01</button>
      <button class="btn sm" data-tpl="{date} {original}.{ext}">date + name</button>
    </div>`;
  view.appendChild(c);

  const prev = el('div', 'card');
  prev.innerHTML = `<h3>Preview</h3><div class="scroll-x"><table class="ptable" id="ptable">
      <thead><tr><th>#</th><th>Original</th><th>New name</th></tr></thead>
      <tbody></tbody></table></div>`;
  view.appendChild(prev);

  const sync = () => {
    // a debounced redraw can land after the user moved on: never assume the
    // inputs are still on screen.
    if (!$('#tmpl')) return false;
    S.naming.template = $('#tmpl').value || '{original}.{ext}';
    S.naming.start = Number($('#nstart')?.value || 1);
    S.naming.step = Math.max(1, Number($('#nstep')?.value || 1));
    S.naming.prefix = $('#nprefix')?.value ?? '';
    S.naming.suffix = $('#nsuffix')?.value ?? '';
    S.naming.find = $('#nfind')?.value ?? '';
    S.naming.replace = $('#nreplace')?.value ?? '';
    S.naming.case = $('#ncase')?.value ?? 'keep';
    S.naming.space = $('#nspace')?.value ?? ' ';
    S.naming.regex = !!$('#nregex')?.checked;
    S.naming.strip_emoji = !!$('#nemoji')?.checked;
    return true;
  };
  const redraw = debounce(async () => {
    if (!sync()) return;                     // the naming screen is gone
    await refreshPreview();
    if ($('#ptable')) drawPreview();
  }, 250);
  $$('#view input, #view select').forEach((i) => { i.oninput = redraw; i.onchange = redraw; });
  $$('#view [data-tpl]').forEach((b) => {
    b.onclick = () => { $('#tmpl').value = b.dataset.tpl; redraw(); };
  });
  refreshPreview().then(drawPreview);
}

function drawPreview() {
  const tb = $('#ptable tbody');
  if (!tb) return;
  tb.innerHTML = '';
  (S.preview || []).forEach((r) => {
    const tr = el('tr', r.duplicate ? 'dup' : '');
    tr.innerHTML = `<td><span class="badge-num">${r.index}</span></td>
      <td class="old">${esc(r.original)}</td>
      <td class="new">${esc(r.final || '—')}${r.truncated ? ' <span class="pill warn">trimmed</span>' : ''}</td>`;
    tb.appendChild(tr);
  });
}

async function refreshPreview() {
  if (!S.jobId) return S.preview;
  try {
    S.preview = await api(`/api/jobs/${S.jobId}/preview-names`, { method: 'POST', body: S.naming })
      .then((r) => r.rows || []);
  } catch (e) { /* preview is cosmetic; never block the wizard */ }
  if (S.preview.length) S.preview[S.preview.length - 1].__n = 1;
  return S.preview;
}

// ── step 4: sequence (drag & drop + FLIP) ──────────────────────────────────
function viewSequence(view) {
  const c = el('div', 'card');
  c.innerHTML = `
    <h2>Upload sequence</h2>
    <p class="dim">Drag the handles, or pick a sort. Strict mode uploads one-by-one in this order.</p>
    <div class="row tight" style="margin-bottom:12px">
      ${[['telegram_order', 'Telegram order'], ['name_natural', 'Name'], ['size_asc', 'Size ↑'],
         ['size_desc', 'Size ↓'], ['date', 'Date'], ['manual', 'Manual']]
        .map(([v, l]) => `<button class="btn sm ${S.sequence.mode === v ? 'primary' : ''}"
           data-mode="${v}">${l}</button>`).join('')}
      <button class="btn sm" id="revBtn">⇅ Reverse</button>
    </div>
    <div class="row" style="margin-bottom:12px">
      <label class="switch"><input type="checkbox" id="strict" ${S.sequence.strict ? 'checked' : ''}>
        <span>Strict order (one at a time)</span></label>
    </div>`;
  view.appendChild(c);

  const list = el('div', 'card');
  list.id = 'seqList';
  view.appendChild(list);

  const draw = () => {
    const order = S.sequence.mode === 'manual' ? S.sequence.order.map((id) => S.files.find((f) => f.id === id)).filter(Boolean)
      : sortFiles(S.files, S.sequence.mode);
    S.sequence.order = order.map((f) => f.id);
    list.innerHTML = '';
    order.forEach((f, i) => {
      const [icon, kind] = typeIcon(f.mime, f.name);
      const r = el('div', 'file-row');
      r.draggable = true;
      r.dataset.id = f.id;
      r.dataset.idx = i;
      r.innerHTML = `<div class="handle" aria-label="drag">⠿</div>
        <div class="grow"><div class="fname">${esc(f.name)}</div>
          <div class="fmeta">${humanBytes(f.size)}</div></div>
        <span class="badge-num" data-num>${String(i + 1).padStart(2, '0')}</span>
        <button class="btn icon sm top" title="Move to top">⇱</button>
        <button class="btn icon sm bot" title="Move to bottom">⇲</button>`;
      r.querySelector('.top').onclick = () => moveItem(f.id, 0);
      r.querySelector('.bot').onclick = () => moveItem(f.id, S.files.length - 1);
      list.appendChild(r);
    });
    wireDnD(list);
  };

  const sortFiles = (files, mode) => {
    const arr = [...files];
    if (mode === 'name_natural') arr.sort((a, b) => cmpNatural(a.name, b.name));
    else if (mode === 'size_asc') arr.sort((a, b) => a.size - b.size);
    else if (mode === 'size_desc') arr.sort((a, b) => b.size - a.size);
    else if (mode === 'date') arr.sort((a, b) => (a.msg_id || 0) - (b.msg_id || 0));
    else if (mode === 'manual' && S.sequence.order.length) {
      const rank = new Map(S.sequence.order.map((id, i) => [id, i]));
      arr.sort((a, b) => (rank.get(a.id) ?? 1e9) - (rank.get(b.id) ?? 1e9));
    } else arr.sort((a, b) => (a.msg_id || 0) - (b.msg_id || 0));
    return arr;
  };

  const moveItem = (id, to) => {
    S.sequence.mode = 'manual';
    const cur = [...S.sequence.order];
    const from = cur.indexOf(id);
    if (from < 0) { draw(); return; }
    cur.splice(from, 1);
    cur.splice(Math.max(0, Math.min(cur.length, to)), 0, id);
    S.sequence.order = cur;
    draw();
    popNumbers();
  };

  const popNumbers = () => {
    $$('[data-num]').forEach((n) => {
      n.classList.remove('pop');
      void n.offsetWidth;
      n.classList.add('pop');
    });
  };

  // real drag & drop with FLIP so rows slide instead of jumping
  const wireDnD = (listEl) => {
    let dragId = null;
    listEl.querySelectorAll('.file-row').forEach((row) => {
      row.addEventListener('dragstart', (e) => {
        dragId = row.dataset.id;
        row.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', dragId); } catch (_) {}
      });
      row.addEventListener('dragend', () => row.classList.remove('dragging'));
      row.addEventListener('dragover', (e) => {
        e.preventDefault();
        $$('.drop-target', listEl).forEach((n) => n.classList.remove('drop-target'));
        row.classList.add('drop-target');
      });
      row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
      row.addEventListener('drop', (e) => {
        e.preventDefault();
        row.classList.remove('drop-target');
        if (!dragId || dragId === row.dataset.id) return;
        const before = $$('.file-row', listEl).map((r) => [r.dataset.id, r.getBoundingClientRect().top]);
        moveItem(dragId, Number(row.dataset.idx));
        if (!motionOK()) return;
        const after = $$('.file-row', listEl);
        after.forEach((r) => {
          const prev = before.find(([id]) => id === r.dataset.id);
          if (!prev) return;
          const dy = prev[1] - r.getBoundingClientRect().top;
          if (!dy) return;
          r.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }],
                    { duration: 260, easing: 'cubic-bezier(.22,.61,.36,1)' });
        });
      });
      // touch fallback: tap handle then tap the row you want it before
      row.querySelector('.handle').addEventListener('pointerup', () => {
        if (dragId === row.dataset.id) { dragId = null; toast('Selection cleared'); return; }
        dragId = row.dataset.id;
        toast('Pick the row to move it before, or drag the handle');
      });
    });
  };

  $$('[data-mode]').forEach((b) => {
    b.onclick = () => {
      S.sequence.mode = b.dataset.mode;
      if (S.sequence.mode === 'manual' && !S.sequence.order.length) {
        S.sequence.order = sortFiles(S.files, 'telegram_order').map((f) => f.id);
      }
      if (b.dataset.mode !== 'manual') S.sequence.order = [];
      render(true);
    };
  });
  $('#strict').onchange = (e) => { S.sequence.strict = e.target.checked; };
  $('#revBtn').onclick = () => {
    S.sequence.mode = 'manual';
    const cur = S.sequence.order.length ? [...S.sequence.order] : S.files.map((f) => f.id);
    S.sequence.order = cur.reverse();
    draw();
    popNumbers();
  };
  if (!S.sequence.order.length && S.sequence.mode === 'manual') {
    S.sequence.order = sortFiles(S.files, 'telegram_order').map((f) => f.id);
  }
  draw();
}

// ── step 5: review ──────────────────────────────────────────────────────────
async function viewReview(view) {
  const total = S.files.reduce((a, f) => a + f.size, 0);
  const speed = S.measuredSpeed || 0;
  const eta = speed > 0 ? total / speed : 0;
  const batchSummary = S.split?.mode === 'batches'
    ? (S.split.groups || []).map((g) => `${esc(g.name)} (${Number(g.count) || 0})`).join(' · ')
    : '';
  const selectedReview = S.split?.mode === 'selected' ? S.files.map(f => {
    const id = S.split.plan[f.id];
    return `<li>${esc(f.name)} → ${esc(S.split.destinations[id] || id)}</li>`;
  }).join('') : '';
  const manualReview = S.split?.mode === 'tree' ? `
    <details open class="route-review"><summary>Folder tree (${(S.split.folders || []).length} folders)</summary>
      <ul>${(S.split.folders || []).map(path => `<li>📁 ${esc(path.join(' / '))}</li>`).join('')}
      ${Object.values(S.split.existing_folders || {}).map(name => `<li>📁 Existing: ${esc(name)}</li>`).join('')}
      ${S.files.map(f => { const id = S.split.existing_plan?.[f.id]; const name = id ? (S.split.existing_folders?.[id] || id) : ((S.split.plan[f.id] || []).join(' / ') || '(parent folder)'); return `<li>📄 ${esc(f.name)} → ${esc(name)}</li>`; }).join('')}</ul></details>` : '';
  const c = el('div', 'card');
  c.innerHTML = `
    <h2>Review</h2>
    <div class="kv"><span>Files</span><span>${S.files.length}</span></div>
    <div class="kv"><span>Total size</span><span>${humanBytes(total)}</span></div>
    <div class="kv"><span>Destination</span><span>${esc(S.folder.path)}</span></div>
    ${batchSummary ? `<div class="kv"><span>Folder batches</span><span>${batchSummary}</span></div>` : ''}
    ${selectedReview ? `<details open class="route-review"><summary>File destinations (${S.files.length})</summary><ul>${selectedReview}</ul></details>` : ''}
    ${manualReview}
    <div class="kv"><span>Naming</span><span class="mono">${esc(S.naming.template)}</span></div>
    <div class="kv"><span>Order</span><span>${esc(S.sequence.mode)}${S.sequence.strict ? ' · strict' : ''}</span></div>
    <div class="kv"><span>Conflicts</span><span>${esc(S.conflict)}</span></div>
    <div class="kv"><span>Estimated time</span><span>${eta ? humanSecs(eta) : '—'}</span></div>
    <div class="kv"><span>Quota</span><span id="quotaBadge" class="pill">checking…</span></div>
    <div class="row" style="margin-top:12px">
      <label class="switch"><input type="checkbox" id="presetChk">
        <span>Save as preset</span></label>
      <input type="text" id="presetName" placeholder="Preset name" style="max-width:220px">
    </div>`;
  view.appendChild(c);

  const quota = $('#quotaBadge');
  try {
    const q = await api('/api/drive/about');
    if (q.free < 0) { quota.className = 'pill info'; quota.textContent = 'unlimited'; }
    else if (q.free < total) {
      quota.className = 'pill bad';
      quota.textContent = `not enough space (${humanBytes(q.free)} free)`;
    } else {
      quota.className = 'pill ok';
      quota.textContent = `${humanBytes(q.free)} free ✔`;
    }
  } catch (e) {
    quota.className = 'pill warn';
    quota.textContent = 'unknown';
  }
}

// ── start / cancel ──────────────────────────────────────────────────────────
async function startUpload() {
  const btn = $('#startBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'starting…'; }
  try {
    const split = currentSplit();          // null when "one folder for all"
    S.split = split;
    await api(`/api/jobs/${S.jobId}`, { method: 'PATCH', body: {
      destination: { ...S.folder, ...(split ? { split } : {}) },
      naming: S.naming, sequence_mode: S.sequence.mode,
      strict_order: S.sequence.strict, conflict_policy: S.conflict, share_mode: S.share,
      order: S.sequence.order,
    } });
    const res = await api(`/api/jobs/${S.jobId}/start`, { method: 'POST' });
    if (res.preset_saved) toast('Preset saved', 'ok');
    S.startedAt = Date.now();
    go('live');
  } catch (e) {
    toast(e.message, 'err');
    if (btn) { btn.disabled = false; btn.textContent = '🚀 Start upload'; }
  }
}

async function cancelJob() {
  try {
    await api(`/api/jobs/${S.jobId}/cancel`, { method: 'POST' });
    toast('Cancelling…', '');
  } catch (e) { toast(e.message, 'err'); }
}


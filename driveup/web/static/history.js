/* Private Studio history dialog. Data is fetched from the session-bound API. */
async function showUploadHistory() {
  if ($('#historyDialog')) return;
  const dialog = el('div', 'history-backdrop');
  dialog.id = 'historyDialog';
  const trigger = $('#historyBtn');
  const closeDialog = () => {
    dialog.remove();
    document.removeEventListener('keydown', onHistoryKey);
    if (trigger) trigger.focus();
  };
  const onHistoryKey = (event) => {
    if (event.key === 'Escape') return closeDialog();
    if (event.key === 'Tab') {
      const focusable = Array.from(dialog.querySelectorAll('button:not([disabled]), a[href], input:not([disabled])'));
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  };
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-labelledby', 'historyTitle');
  const panel = el('section', 'history-panel card');
  const head = el('div', 'row history-head');
  const title = el('h2', '', 'Your past uploads');
  title.id = 'historyTitle';
  head.append(title);
  const close = el('button', 'btn ghost', 'Close');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close upload history');
  close.onclick = closeDialog;
  head.append(close);
  panel.append(head);
  const status = el('p', 'history-status', 'Loading your uploads…');
  status.setAttribute('role', 'status');
  panel.append(status);
  dialog.append(panel);
  dialog.addEventListener('click', (event) => { if (event.target === dialog) closeDialog(); });
  document.addEventListener('keydown', onHistoryKey);
  document.body.append(dialog);
  close.focus();
  try {
    const response = await api('/api/jobs?limit=30');
    const jobs = response.jobs || [];
    status.remove();
    if (!jobs.length) {
      const empty = el('div', 'history-empty');
      empty.append(el('h3', '', 'No past uploads yet'));
      empty.append(el('p', '', 'Your completed and previous upload jobs will appear here. Start a new upload from the Studio.'));
      panel.append(empty);
      return;
    }
    const list = el('div', 'history-list');
    for (const job of jobs) {
      const row = el('button', 'history-row');
      row.type = 'button';
      row.setAttribute('aria-label', `Open ${job.status} upload`);
      const main = el('span', 'history-main');
      main.append(el('strong', '', esc((job.destination && job.destination.path) || 'Google Drive upload')));
      const dt = job.created_at ? new Date(Number(job.created_at) * 1000) : null;
      main.append(el('small', '', dt && !Number.isNaN(dt.valueOf()) ? dt.toLocaleString() : 'Date unavailable'));
      row.append(main);
      const badge = el('span', `history-status-badge ${String(job.status || '').toLowerCase()}`,
        esc(String(job.status || 'unknown').replaceAll('_', ' ')));
      row.append(badge);
      row.onclick = () => { location.href = `/studio?job=${encodeURIComponent(job.id)}`; };
      list.append(row);
    }
    panel.append(list);
  } catch (error) {
    status.textContent = error.message || 'Could not load upload history. Check your connection and try again.';
    status.classList.add('bad');
    const retry = el('button', 'btn', 'Try again');
    retry.type = 'button';
    retry.onclick = () => { closeDialog(); showUploadHistory(); };
    panel.append(retry);
  }
}

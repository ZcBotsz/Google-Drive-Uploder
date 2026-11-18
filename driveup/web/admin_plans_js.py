"""Plans pane behaviour for the admin panel.

Split out of `admin_page.py` so the page module stays readable. Everything here
is about keeping the per-file limit honest: the table shows what Telegram can
actually deliver, the editor warns before you save a number it cannot honour.
"""
from __future__ import annotations

PLANS_JS = r"""
// ── plans ──────────────────────────────────────────────────────────────
let PLANS = [], PLAN_CAP = null;
async function loadPlans() {
  const d = await api('/admin/api/plans');
  PLANS = d.plans;
  const capMb = PLAN_CAP = (d.cfg && d.cfg.max_file_mb_reachable) || null;
  $('#plansTbl tbody').innerHTML = d.plans.map(p => {
    // Never advertise a per-file size the transport cannot deliver.
    const real = capMb ? Math.min(p.max_file_mb, capMb) : p.max_file_mb;
    const warn = capMb && p.max_file_mb > capMb
      ? ' <span class="pill warn" title="Telegram cloud API caps getFile at ' + capMb +
        ' MB">≤' + capMb + ' real</span>' : '';
    return '<tr data-plan="' + esc(p.id) + '" style="cursor:pointer">' +
      '<td class="mono" data-l="Plan ID">' + esc(p.id) + '</td>' +
      '<td data-l="Name">' + esc(p.name) + '</td>' +
      '<td data-l="Price">' + esc(p.price_text||'—') + '</td>' +
      '<td data-l="Duration">' + p.duration_days + ' d</td>' +
      '<td data-l="Max file">' + real + ' MB' + warn + '</td>' +
      '<td data-l="Daily GB">' + p.daily_gb + '</td>' +
      '<td data-l="Files/job">' + p.max_files_job + '</td></tr>';
  }).join('');
  $('#gPlan').innerHTML = d.plans.map(p =>
    '<option value="' + esc(p.id) + '">' + esc(p.name) + '</option>').join('');
  // keep the editor's plan picker in sync
  $('#eId').innerHTML = d.plans.map(p =>
    '<option value="' + esc(p.id) + '">' + esc(p.id) + ' · ' + esc(p.name) + '</option>').join('');
  $$('#plansTbl tr[data-plan]').forEach(tr => tr.onclick = () => fillPlan(tr.dataset.plan));
  $('#expiring').innerHTML = d.expiring.length
    ? d.expiring.map(x => '<div class="kv"><span>' + x.user_id + ' · ' + esc(x.name||x.plan_id) +
        '</span><span>' + when(x.expires_at) + '</span></div>').join('')
    : 'none';
}
function fillPlan(id) {
  const p = PLANS.find(x => x.id === id);
  if (!p) return;
  $('#eId').value = p.id;
  $('#eName').value = p.name || '';
  $('#ePrice').value = p.price_text || '';
  $('#eDays').value = p.duration_days ?? 30;
  $('#eMaxFile').value = p.max_file_mb ?? 2000;
  $('#eDailyGb').value = p.daily_gb ?? 10;
  $('#eConc').value = p.concurrent ?? 1;
  $('#eFiles').value = p.max_files_job ?? 200;
  planHint();
}
// Live feedback: the moment the number goes above what Telegram can fetch,
// say so — before the admin taps Save, not after a user's job dies at 0 %.
function planHint() {
  const want = Number($('#eMaxFile').value || 0);
  const over = PLAN_CAP && want > PLAN_CAP;
  $('#eHint').innerHTML = over
    ? '⚠ You asked for ' + want + ' MB, but this server can only download '
      + PLAN_CAP + ' MB per file (Telegram cloud API). Users will be capped at '
      + PLAN_CAP + ' MB until you set TELEGRAM_API_URL to a local Bot API server.'
    : (want ? '✓ ' + Math.min(want, PLAN_CAP || want) + ' MB per file is deliverable.' : '');
}
$('#eMaxFile').oninput = planHint;
$('#eLoad').onclick = () => fillPlan($('#eId').value);
$('#eId').onchange = () => fillPlan($('#eId').value);
$('#eSave').onclick = async () => {
  const id = ($('#eId').value || '').trim();
  if (!id) return toast('pick a plan first', 'err');
  try {
    await api('/admin/api/plans', { method:'POST', body:{
      id, name: $('#eName').value || id, price_text: $('#ePrice').value,
      duration_days: Number($('#eDays').value || 0),
      max_file_mb: Number($('#eMaxFile').value || 1),
      daily_gb: Number($('#eDailyGb').value || 1),
      concurrent: Number($('#eConc').value || 1),
      max_files_job: Number($('#eFiles').value || 1),
    }});
    toast('Plan saved', 'ok'); await loadPlans();
  } catch (e) { toast(e.message, 'err'); }
};
$('#grantBtn').onclick = async () => {
  try {
    await api('/admin/api/users/' + Number($('#gUserId').value) + '/plan',
      { method:'POST', body:{ plan_id: $('#gPlan').value, days: Number($('#gDays').value||30) } });
    toast('Granted', 'ok'); loadPlans();
  } catch (e) { toast(e.message, 'err'); }
};
$('#revokeBtn').onclick = async () => {
  try {
    await api('/admin/api/users/' + Number($('#gUserId').value) + '/plan',
      { method:'POST', body:{ revoke: true } });
    toast('Revoked', 'ok'); loadPlans();
  } catch (e) { toast(e.message, 'err'); }
};

"""

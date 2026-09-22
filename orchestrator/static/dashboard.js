/* AgentOS dashboard. Task snapshots are authoritative; SSE triggers early refreshes. */
'use strict';

const paths = {
  layout: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 3v18M9 9h12"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  chart: '<path d="M4 3v17h17M8 15v-4m5 4V7m5 8v-6"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  bot: '<rect x="4" y="7" width="16" height="13" rx="4"/><path d="M12 3v4M1 12v4m22-4v4M9 12v2m6-2v2m-6 3h6"/>',
  book: '<path d="M12 5v16M12 5C9 3 5 3 3 4v15c3-1 6 0 9 2 3-2 6-3 9-2V4c-2-1-6-1-9 1Z"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3m0 3h.01"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/>',
  moon: '<path d="M20 14a8 8 0 0 1-10-10 9 9 0 1 0 10 10Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
  history: '<path d="M3 3v6h6M3 9a9 9 0 1 1 0 6m9-9v6l4 2"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  'git-branch': '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M6 7v10m12-10c0 7-12 4-12 10"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
  'arrow-right': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  play: '<path d="m8 4 12 8-12 8V4Z"/>',
  sliders: '<path d="M4 7h4m4 0h8M4 17h10m4 0h2"/><circle cx="10" cy="7" r="2"/><circle cx="16" cy="17" r="2"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  download: '<path d="M12 3v12m0 0-4-4m4 4 4-4M4 21h16"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3m-8 0 1 13a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2l1-13"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m10.5 12.5 8-8M16 6l2 2M19 3l2 2"/>',
  bookmark: '<path d="M6 3h12v18l-6-4-6 4V3Z"/>',
  repeat: '<path d="M17 2l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 22l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3"/>',
};
const $ = id => document.getElementById(id);
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.layers}</svg>`;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('tr-TR', {maximumFractionDigits:digits}) : '—';
const time = value => value ? new Date(value * 1000).toLocaleString('tr-TR', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}) : '—';
const state = {jobs:[], workers:[], job:null, jobId:null, agentRun:null, agentRunId:null, agentRunNotified:null, traces:[], traceSummary:null, filter:'all', search:'', view:'overview', source:null, reviewed:new Set(), errors:{}, loaded:false};
const DAILY_TASK_BUDGET = 500;
let refreshBusy = false;
let detailRequest = null;
let streamTimer = null;
let toastTimer = null;

function html(id, value) {
  const node = $(id);
  if (node.innerHTML !== value) node.innerHTML = value;
}
function empty(title, description, action = false) {
  return `<div class="empty-list"><span class="empty-icon">${icon('layers')}</span><h3>${escapeHTML(title)}</h3><p>${escapeHTML(description)}</p>${action ? '<button class="button" data-new-job>Yeni iş oluştur</button>' : ''}</div>`;
}
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').className = `toast${error ? ' error' : ''}`;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}
function getApiKey() { try { return localStorage.getItem('agentos-api-key') || ''; } catch { return ''; } }
function setApiKey(value) { try { if (value) localStorage.setItem('agentos-api-key',value); else localStorage.removeItem('agentos-api-key'); } catch { /* key still works for this session */ } }

function todayKey() { return `agentos-budget-${new Date().toISOString().slice(0,10)}`; }
function getBudgetUsed() { try { return Number(localStorage.getItem(todayKey())) || 0; } catch { return 0; } }
function recordBudgetUsage(taskCount) { try { localStorage.setItem(todayKey(), String(getBudgetUsed() + taskCount)); } catch { /* budget note just won't persist across reloads */ } }
function updateBudgetNote(pendingTaskCount = 0) {
  const used = getBudgetUsed();
  const projected = used + pendingTaskCount;
  const note = $('budgetNote');
  note.textContent = `Bugün gönderilen görev: ${used}${pendingTaskCount ? ` (+${pendingTaskCount} bu çalışmada = ${projected})` : ''} / ${DAILY_TASK_BUDGET} önerilen bütçe`;
  note.classList.toggle('over-budget', projected > DAILY_TASK_BUDGET);
}

function getTemplates() { try { return JSON.parse(localStorage.getItem('agentos-templates') || '[]'); } catch { return []; } }
function saveTemplates(templates) { try { localStorage.setItem('agentos-templates', JSON.stringify(templates)); } catch { /* template still applied this session */ } }
function renderTemplateOptions() {
  const templates = getTemplates();
  const select = $('templateSelect');
  const current = select.value;
  select.innerHTML = '<option value="">Şablon seç…</option>' + templates.map(t => `<option value="${escapeHTML(t.name)}">${escapeHTML(t.name)}</option>`).join('');
  select.value = templates.some(t => t.name === current) ? current : '';
  $('deleteTemplateButton').disabled = !select.value;
}

function downloadBlob(filename, content, mime) {
  const blob = new Blob([content], {type:mime});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
function exportResultsJson() {
  if (!state.job) return;
  downloadBlob(`${state.jobId.slice(0,12)}-sonuclar.json`, JSON.stringify(sortedResults(),null,2), 'application/json');
  toast('Sonuçlar JSON olarak indirildi.');
}
function exportResultsCsv() {
  if (!state.job) return;
  const rows = sortedResults();
  const paramKeys = [...new Set(rows.flatMap(r => Object.keys(r.params || {})))];
  const header = ['task_id','worker_id','score','duration_ms','llm_provider',...paramKeys,'commentary'];
  const csvCell = value => `"${String(value ?? '').replace(/"/g,'""')}"`;
  const lines = [header.join(',')].concat(rows.map(r => header.map(key => csvCell(paramKeys.includes(key) ? r.params?.[key] : r[key])).join(',')));
  downloadBlob(`${state.jobId.slice(0,12)}-sonuclar.csv`, lines.join('\r\n'), 'text/csv');
  toast('Sonuçlar CSV olarak indirildi.');
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.method ? 60000 : 12000);
  const apiKey = getApiKey();
  try {
    const response = await fetch(path, {...options, signal:controller.signal, headers:{'Content-Type':'application/json', ...(apiKey ? {'X-API-Key':apiKey} : {}), ...options.headers}});
    if (!response.ok) {
      let message = `İstek tamamlanamadı (HTTP ${response.status}).`;
      if (response.status === 401) message = 'Yetkisiz istek. Ayarlar’dan geçerli bir API anahtarı girin.';
      else try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch { /* Non-JSON server error. */ }
      throw new Error(message);
    }
    if (response.status === 204) return null;
    return await response.json();
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Sunucu yanıt vermedi. Bağlantınızı kontrol edip yeniden deneyin.');
    if (error instanceof TypeError) throw new Error('Sunucuya ulaşılamıyor. Servisin çalıştığından emin olun.');
    throw error;
  } finally { clearTimeout(timeout); }
}
function status(job) {
  if (job.pending_or_running > 0) return {label:'Devam ediyor', cls:'warning'};
  if (job.failed > 0) return {label:'Hata ile tamamlandı', cls:'danger'};
  return {label:'Tamamlandı', cls:'success'};
}
function badge(label, cls = 'neutral') { return `<span class="badge ${cls}">${escapeHTML(label)}</span>`; }
function heartbeatFreshness(worker) {
  if (!worker.last_heartbeat) return 'heartbeat yok';
  const ageSeconds = Math.max(0, Math.round(Date.now()/1000 - worker.last_heartbeat));
  if (ageSeconds < 60) return `${ageSeconds}sn önce nabız`;
  return `${Math.round(ageSeconds/60)}dk önce nabız`;
}
function activeWorkers() { return state.workers.filter(worker => worker.status === 'running'); }
function results() { return (state.job?.tasks || []).filter(task => task.status === 'done' && task.result).map(task => ({...task.result, task_id:task.task_id})); }
function sortedResults() {
  return results().sort($('resultSort').value === 'duration' ? (a,b) => a.duration_ms - b.duration_ms : (a,b) => b.score - a.score);
}
function bestResults() { return results().sort((a,b) => b.score - a.score); }

function renderJobs() {
  const jobs = state.jobs;
  $('jobCount').textContent = jobs.length;
  $('allCount').textContent = jobs.length;
  $('activeCount').textContent = jobs.filter(job => job.pending_or_running > 0).length;
  $('completedCount').textContent = jobs.filter(job => job.pending_or_running === 0).length;
  const filtered = jobs.filter(job => job.job_id.toLowerCase().includes(state.search.toLowerCase()) &&
    (state.filter === 'all' || (state.filter === 'active' ? job.pending_or_running > 0 : job.pending_or_running === 0)));
  if (!filtered.length) {
    html('jobList', state.errors.jobs && !state.loaded ? empty('Bağlantı kurulamadı', 'Orchestrator ve Redis bağlantısını kontrol edip yenileyin.') :
      jobs.length ? empty('Eşleşen iş bulunamadı', 'Aramayı veya durum filtresini değiştirin.') : empty('Henüz bir çalışma yok', 'İlk grid’inizi oluşturun, agent’lar birlikte çalışmaya başlasın.', true));
    return;
  }
  html('jobList', filtered.map(job => {
    const s = status(job);
    const progress = job.total ? Math.round((job.done + job.failed) / job.total * 100) : 0;
    return `<button class="job-item${job.job_id === state.jobId ? ' selected' : ''}" data-job-id="${escapeHTML(job.job_id)}" aria-pressed="${job.job_id === state.jobId}"><div class="job-item-top"><span class="agent-avatar">${icon('bot')}</span><strong>Agent çalışması</strong><span class="job-time">${escapeHTML(time(job.created_at))}</span></div><div class="job-item-body"><h3>Çalışma #${escapeHTML(job.job_id.slice(0,8))}</h3><p>${job.total} görev · ${escapeHTML(job.job_id.slice(0,12))}</p><div class="job-item-bottom">${badge(s.label,s.cls)}<span>${job.done}/${job.total} sonuç</span></div><div class="job-progress"><span style="width:${progress}%"></span></div></div></button>`;
  }).join(''));
}
function renderWorkers() {
  const active = activeWorkers();
  $('workerCount').textContent = state.workers.length;
  $('clusterCount').textContent = `${active.length} aktif`;
  html('workerPreview', state.workers.length ? state.workers.slice(0,3).map(worker => `<div class="worker-preview-item"><span class="worker-avatar">${icon('bot')}</span><div><strong>${escapeHTML(worker.worker_id)}</strong><small>${worker.status === 'running' ? 'Çalışıyor' : 'Durdu'} · PID ${escapeHTML(worker.pid)}</small></div><span class="dot ${worker.status === 'running' ? 'green' : 'red'}"></span></div>`).join('') + (state.workers.length > 3 ? `<p class="more-workers">+${state.workers.length - 3} worker daha</p>` : '') : `<p class="muted">${state.errors.workers ? 'Worker bilgilerine ulaşılamıyor.' : 'Henüz worker yok. İlk worker’ınızı ekleyerek kümenizi hazırlayın.'}</p>`);
  html('workersList', state.workers.length ? state.workers.map(worker => `<div class="worker-row"><span class="worker-avatar">${icon('bot')}</span><div><strong>${escapeHTML(worker.worker_id)}</strong>${worker.agent_profile ? badge(worker.agent_profile,'neutral') : ''}<p>PID ${escapeHTML(worker.pid)} · ${escapeHTML(worker.branch)}</p><p>${escapeHTML(worker.worktree_path)}</p><p class="worker-meta">${worker.current_task_id ? `Görev #${escapeHTML(worker.current_task_id.slice(0,8))}` : 'Boşta'} · ${escapeHTML(heartbeatFreshness(worker))}${worker.capabilities?.length ? ` · ${escapeHTML(worker.capabilities.join(', '))}` : ''}</p>${worker.llm_provider ? `<p class="worker-meta">${icon('sparkles')} ${escapeHTML(worker.llm_provider)}</p>` : ''}</div>${badge(worker.status === 'running' ? 'Çalışıyor' : 'Durdu', worker.status === 'running' ? 'success' : 'danger')}<button class="text-button" data-stop-worker="${escapeHTML(worker.worker_id)}">Durdur</button></div>`).join('') : empty('Kümeniz hazır olmayı bekliyor', 'Yukarıdan worker sayısını belirleyip kümeyi büyütün.'));
  $('noWorkersWarning').hidden = active.length > 0;
  renderChecklist();
}
function renderConnection() {
  const offline = Object.keys(state.errors).length > 0;
  $('connectionIndicator').className = `connection-indicator ${offline ? 'offline' : 'online'}`;
  $('connectionText').textContent = offline ? 'Bağlantı sorunu' : 'Bağlı';
  $('connectionIndicator').title = offline ? Object.values(state.errors).join('\n') : 'Orchestrator ve Redis bağlantısı aktif';
  html('systemStatus', offline ? badge('Bağlantı kontrol edilmeli','danger') : activeWorkers().length ? badge('● Sistem hazır','success') : badge('Worker bekleniyor','warning'));
  if (offline) $('lastUpdated').textContent = 'Bağlantı sorunu · Yeniden deneniyor';
  else $('lastUpdated').textContent = `Son güncelleme ${new Date().toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'})}`;
}
function renderChecklist() {
  const steps = {stepWorkers:activeWorkers().length > 0, stepJob:!!state.job, stepResults:!!state.job && state.job.pending_or_running === 0, stepReview:state.reviewed.has(state.jobId)};
  for (const [id,done] of Object.entries(steps)) {
    $(id).classList.toggle('done',done);
    $(id).querySelector('.check-box').innerHTML = done ? icon('check') : '';
  }
}
function renderAgentRun() {
  const panel = $('agentRunPanel');
  const run = state.agentRun;
  panel.hidden = !run;
  if (!run) return;
  const labels = {
    planning:'Planner hedefi görevlere çeviriyor',
    running:`Trader agent’lar ${run.current_round || 1}. turu çalışıyor`,
    reviewing:`Reviewer ${run.current_round || 1}. turu değerlendiriyor`,
    completed:'Agent akışı tamamlandı',
    failed:'Agent akışı tamamlanamadı',
  };
  const stageLabels = {planner:'Planner',traders:'Trader’lar',reviewer:'Reviewer',final:'Final',failed:'Hata'};
  const review = run.reviews?.at(-1);
  const summary = run.final_result?.summary || review?.summary || run.plan?.summary || 'Agent’lar hedef üzerinde çalışmaya hazırlanıyor.';
  const provider = review?.provider || run.plan?.provider;
  const best = run.final_result?.best_result;
  panel.className = `agent-run-panel ${run.status}`;
  html('agentRunPanel', `<div class="agent-run-heading">${icon('sparkles')}<strong>${escapeHTML(labels[run.status] || 'Agent akışı çalışıyor')}</strong><span class="agent-run-stage">${escapeHTML(stageLabels[run.stage] || run.stage)}</span></div><p>${escapeHTML(run.goal)}</p><p class="agent-run-summary">${escapeHTML(run.error || summary)}</p><div class="agent-run-meta"><span>${icon('repeat')} Tur ${run.current_round || 0}/${run.max_rounds}</span><span>${icon('bot')} ${run.trader_count} trader</span>${provider ? `<span>${icon('sparkles')} ${escapeHTML(provider)}</span>` : ''}${best ? `<span>${icon('chart')} En iyi skor ${number(best.score)}</span>` : ''}</div>`);
}
function renderDetail() {
  const job = state.job;
  $('copyButton').disabled = !job;
  $('exportJsonButton').disabled = !job || !job.done;
  $('exportCsvButton').disabled = !job || !job.done;
  $('rerunJobButton').disabled = !job;
  $('deleteJobButton').disabled = !job;
  $('cancelJobButton').hidden = !job || !job.pending_or_running;
  $('cancelJobButton').disabled = !job || !job.pending_or_running;
  $('refineJobButton').hidden = !job || !!job.pending_or_running || !job.done;
  $('refineJobButton').disabled = !job || !!job.pending_or_running || !job.done;
  if (job) {
    const s = status(job);
    const grid = (job.tasks || []).length > 0 && job.tasks.every(task => task.payload?.params && 'fast_ma' in task.payload.params && 'slow_ma' in task.payload.params);
    $('jobTitle').textContent = grid ? 'Strateji parametre taraması' : 'Agent çalışması';
    $('jobId').textContent = `#${job.job_id.slice(0,12)}`;
    $('jobId').title = job.job_id;
    $('jobStatus').className = `badge ${s.cls}`;
    $('jobStatus').textContent = s.label;
    $('jobContext').textContent = grid ? 'Parametre grid’i · Backtest' : 'Dağıtık görev çalışması';
    $('jobType').textContent = grid ? 'Grid taraması' : 'Görev grubu';
    $('totalTasks').textContent = `${job.total} görev`;
    $('jobDone').textContent = job.done;
    $('jobFailed').textContent = job.failed;
    $('jobPending').textContent = job.pending_or_running;
    const progress = job.total ? Math.round((job.done + job.failed) / job.total * 100) : 0;
    $('progressRing').style.setProperty('--progress',`${progress}%`);
    $('progressRing').setAttribute('aria-valuenow',progress);
    $('progressPercent').textContent = `${progress}%`;
    $('progressTitle').textContent = job.pending_or_running ? 'Agent’lar sonuçları hazırlıyor' : job.failed ? 'Çalışma hatalarla tamamlandı' : 'Tüm görevler tamamlandı';
    $('progressDescription').textContent = `${job.total} görevin ${job.done + job.failed} tanesi sonlandı · ${job.done} başarılı sonuç`;
    let notice = 'Sonuçlar geldikçe otomatik güncellenir. Bu sayfayı açık tutabilirsiniz.';
    if (!job.pending_or_running) notice = job.failed ? `${job.failed} görev başarısız oldu. Hata ayrıntılarını aktivite akışından inceleyebilirsiniz.` : 'Çalışma tamamlandı. Sonuçları karşılaştırarak en iyi parametreleri keşfedin.';
    else if (!activeWorkers().length) notice = 'Aktif worker yok. Bekleyen görevleri çalıştırmak için worker kümenizi büyütün.';
    $('jobNotice').className = `notice${!job.pending_or_running && !job.failed ? ' success' : ''}`;
    html('jobNotice', icon('info') + `<span>${escapeHTML(notice)}</span>`);
    $('streamStatus').textContent = job.pending_or_running ? (state.source?.readyState === 1 ? 'Canlı bağlantı' : 'Otomatik takip') : 'Çalışma tamamlandı';
  }
  $('gettingStarted').hidden = !!job;
  renderResults();
  renderActivity();
  renderInsights();
  renderChecklist();
  renderAgentRun();
  renderTraces();
}
function resultRow(result,index) {
  const params = Object.entries(result.params || {}).map(([key,value]) => `${key}: ${value}`).join(' · ');
  return `<button class="result-row" data-result-id="${escapeHTML(result.task_id)}"><span class="result-rank">${String(index+1).padStart(2,'0')}</span><span class="result-info"><strong>${escapeHTML(result.worker_id)}</strong><p>${escapeHTML(params || 'Parametre yok')}</p></span><span class="result-score">${number(result.score)}<small>${number(result.duration_ms)} ms</small></span>${icon('arrow-right')}</button>`;
}
function renderResults() {
  const sorted = sortedResults();
  $('resultCount').textContent = sorted.length;
  const placeholder = empty(state.job ? 'Sonuçlar bekleniyor' : 'Henüz sonuç yok', state.job?.failed && !state.job.pending_or_running ? 'Görev hatalarını aktivite akışından inceleyin.' : 'Tamamlanan görevlerin sonuçları burada listelenecek.');
  html('topResults', state.job ? (sorted.length ? bestResults().slice(0,3).map(resultRow).join('') : placeholder) : '');
  html('allResults', sorted.length ? sorted.map(resultRow).join('') : placeholder);
}
function renderActivity() {
  const tasks = [...(state.job?.tasks || [])].sort((a,b) => b.updated_at - a.updated_at);
  const labels = {done:'Görev tamamlandı',failed:'Görev başarısız',running:'Görev çalışıyor',pending:'Görev sırada'};
  html('activityList', tasks.length ? tasks.map(task => `<div class="activity-item"><span class="activity-marker ${escapeHTML(task.status)}">${icon(task.status === 'done' ? 'check' : task.status === 'failed' ? 'x' : 'clock')}</span><div><strong>${labels[task.status] || 'Görev güncellendi'}</strong><p>${escapeHTML(task.assigned_worker || 'Worker bekleniyor')} · #${escapeHTML(task.task_id.slice(0,8))}${task.result ? ` · Skor ${number(task.result.score)}` : ''}${task.attempt_count > 1 ? ` · ${task.attempt_count}. deneme` : ''}</p>${task.error ? `<p class="form-error">${escapeHTML(task.error)}</p>` : ''}<time>${escapeHTML(time(task.updated_at))}</time></div>${(task.status === 'failed' || task.status === 'cancelled') ? `<button class="text-button" data-retry-task="${escapeHTML(task.task_id)}">Yeniden dene</button>` : ''}</div>`).join('') : empty('Akış henüz başlamadı', 'Bir iş seçtiğinizde görevlerin durumu burada görünecek.'));
}
function renderTraces() {
  const summary = state.traceSummary;
  const traces = state.traces || [];
  $('traceCallCount').textContent = summary?.calls || 0;
  $('traceScope').textContent = state.job?.meta?.agent_run_id ? 'Agent run' : state.jobId ? 'Seçili iş' : 'İş bekleniyor';
  const metric = (label,value,detail) => `<div><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong><small>${escapeHTML(detail)}</small></div>`;
  html('traceMetrics', summary ? [
    metric('Toplam token',number(summary.total_tokens,0),`${number(summary.calls,0)} LLM çağrısı`),
    metric('Giriş',number(summary.input_tokens,0),'prompt token'),
    metric('Çıkış',number(summary.output_tokens,0),'yanıt token'),
    metric('Toplam süre',`${number(summary.duration_ms,0)} ms`,summary.failed_calls ? `${summary.failed_calls} hatalı çağrı` : 'Çağrılar tamamlandı'),
    metric('Tahmini maliyet',summary.cost_usd == null ? '—' : `$${number(summary.cost_usd,6)}`,summary.cost_usd == null ? 'Fiyat yapılandırılmadı' : 'Yerel modeller $0'),
  ].join('') : '');
  const maxTokens = Math.max(1,...(summary?.agents || []).map(agent => agent.total_tokens));
  html('traceAgents', summary?.agents?.length ? summary.agents.map(agent => `<div class="trace-agent-row"><span class="trace-agent-avatar">${icon(agent.roles.includes('planner') ? 'sparkles' : agent.roles.includes('reviewer') ? 'shield' : 'bot')}</span><div class="trace-agent-info"><div><strong>${escapeHTML(agent.agent_id)}</strong><span>${escapeHTML(agent.roles.join(', '))}</span></div><p>${escapeHTML(agent.providers.join(', '))} · ${agent.calls} çağrı · ${number(agent.duration_ms,0)} ms${agent.estimated_calls ? ` · ${agent.estimated_calls} tahmini` : ''}</p><span class="trace-token-bar"><i style="width:${Math.max(4,agent.total_tokens/maxTokens*100)}%"></i></span></div><div class="trace-token-total"><strong>${number(agent.total_tokens,0)}</strong><span>token</span></div></div>`).join('') : empty('Henüz LLM izi yok', 'Bir agent çalışması başladığında kullanım metrikleri burada görünecek.'));
  const statusLabels = {completed:'Tamamlandı',failed:'Başarısız',timeout:'Zaman aşımı'};
  html('traceList', traces.length ? traces.map(trace => `<div class="trace-item"><span class="activity-marker ${trace.status === 'completed' ? 'done' : 'failed'}">${icon(trace.status === 'completed' ? 'check' : 'x')}</span><div class="trace-item-main"><div><strong>${escapeHTML(trace.agent_id)}</strong>${badge(statusLabels[trace.status] || trace.status,trace.status === 'completed' ? 'success' : 'danger')}</div><p>${escapeHTML(trace.role)} · ${escapeHTML(trace.provider + (trace.model ? `:${trace.model}` : ''))}${trace.task_id ? ` · görev #${escapeHTML(trace.task_id.slice(0,8))}` : ''}</p><time>${escapeHTML(time(trace.started_at))}</time>${trace.error ? `<p class="form-error">${escapeHTML(trace.error)}</p>` : ''}</div><div class="trace-call-metrics"><strong>${number(trace.total_tokens,0)} token${trace.tokens_estimated ? ' ~' : ''}</strong><span>${number(trace.input_tokens,0)} giriş / ${number(trace.output_tokens,0)} çıkış</span><span>${number(trace.duration_ms,0)} ms</span></div></div>`).join('') : empty('Çağrı zaman çizelgesi boş', 'Planner, Reviewer veya trader bir model çağırdığında kayıtlar burada sıralanacak.'));
}
function renderInsights() {
  const best = bestResults()[0];
  $('insightTitle').textContent = best ? `En iyi skor: ${number(best.score)}` : 'Fikirden sonuca, birlikte.';
  $('insightText').textContent = best ? (best.commentary || 'Bu sonuç için agent yorumu bulunmuyor.') : 'İlk çalışma tamamlandığında en iyi skor ve agent değerlendirmesi burada yer alacak.';
  $('insightProvider').textContent = best ? `${best.llm_provider === 'mock' ? 'Mock sağlayıcı · Örnek değerlendirme' : `${best.llm_provider || 'Agent'} değerlendirmesi`} · ${state.job.pending_or_running ? 'Şu ana kadarki en iyi sonuç' : 'En yüksek skorlu sonuç'}` : 'Çalışma sonuçlarından beslenir';
}
function closeStream() {
  if (state.source) state.source.close();
  state.source = null;
  clearTimeout(streamTimer);
}
function connectStream(jobId) {
  if (state.source || !state.job?.pending_or_running) return;
  const source = new EventSource(`/jobs/${encodeURIComponent(jobId)}/events`);
  state.source = source;
  source.onopen = () => { if (state.jobId === jobId) $('streamStatus').textContent = 'Canlı bağlantı'; };
  source.onmessage = () => {
    if (state.jobId !== jobId) return;
    clearTimeout(streamTimer);
    streamTimer = setTimeout(() => refreshDetail(), 150);
  };
  source.onerror = () => { if (state.jobId === jobId) $('streamStatus').textContent = 'Yeniden bağlanıyor · Otomatik takip'; };
}
async function refreshTraces() {
  const runId = state.job?.meta?.agent_run_id || (state.agentRun?.current_job_id === state.jobId ? state.agentRunId : null);
  const scope = runId ? `run_id=${encodeURIComponent(runId)}` : state.jobId ? `job_id=${encodeURIComponent(state.jobId)}` : '';
  if (!scope) { state.traces = []; state.traceSummary = null; renderTraces(); return; }
  try {
    const [traces,summary] = await Promise.all([api(`/traces?${scope}&limit=200`),api(`/traces/summary?${scope}`)]);
    state.traces = traces;
    state.traceSummary = summary;
    delete state.errors.traces;
  } catch (error) {
    state.errors.traces = error.message;
  }
  renderTraces();
}
async function refreshDetail() {
  const jobId = state.jobId;
  if (!jobId || detailRequest === jobId) return;
  detailRequest = jobId;
  try {
    const job = await api(`/jobs/${encodeURIComponent(jobId)}`);
    if (state.jobId !== jobId) return;
    state.job = job;
    delete state.errors.detail;
    if (!job.pending_or_running) closeStream();
    else connectStream(jobId);
    renderDetail();
    await refreshTraces();
  } catch (error) {
    if (state.jobId === jobId) {
      state.errors.detail = error.message;
      $('jobTitle').textContent = 'Çalışma bilgisi alınamadı';
      $('jobStatus').textContent = 'Yeniden deneniyor';
      $('jobStatus').className = 'badge danger';
    }
  } finally {
    if (detailRequest === jobId) detailRequest = null;
    renderConnection();
  }
}
async function loadJob(jobId) {
  if (state.jobId === jobId) return;
  closeStream();
  state.jobId = jobId;
  state.job = null;
  state.traces = [];
  state.traceSummary = null;
  delete state.errors.detail;
  $('jobTitle').textContent = 'Çalışma yükleniyor…';
  $('jobId').textContent = `#${jobId.slice(0,12)}`;
  $('jobStatus').textContent = 'Yükleniyor';
  $('jobStatus').className = 'badge neutral';
  for (const id of ['totalTasks','jobDone','jobFailed','jobPending']) $(id).textContent = '—';
  $('progressRing').style.setProperty('--progress','0%');
  $('progressRing').setAttribute('aria-valuenow','0');
  $('progressPercent').textContent = '0%';
  $('progressTitle').textContent = 'Görev bilgileri alınıyor';
  $('progressDescription').textContent = 'Çalışma sunucuyla eşitleniyor.';
  $('streamStatus').textContent = 'Bağlanıyor';
  html('jobNotice',icon('info') + '<span>Çalışma bilgileri yükleniyor.</span>');
  renderJobs();
  renderDetail();
  $('gettingStarted').hidden = true;
  await refreshDetail();
}
async function refreshAgentRun() {
  if (!state.agentRunId) return;
  try {
    const run = await api(`/agent-runs/${encodeURIComponent(state.agentRunId)}`);
    const previousRunJobId = state.agentRun?.current_job_id;
    state.agentRun = run;
    delete state.errors.agentRun;
    if (run.current_job_id && run.current_job_id !== previousRunJobId) await loadJob(run.current_job_id);
    renderAgentRun();
    if (['completed','failed'].includes(run.status) && state.agentRunNotified !== run.run_id) {
      state.agentRunNotified = run.run_id;
      toast(run.status === 'completed' ? 'Planner, trader ve Reviewer akışı tamamlandı.' : `Agent akışı başarısız: ${run.error || 'Bilinmeyen hata'}`, run.status === 'failed');
    }
  } catch (error) {
    state.errors.agentRun = error.message;
    renderAgentRun();
  }
}
async function refreshAll() {
  if (refreshBusy) return;
  refreshBusy = true;
  try {
    await Promise.allSettled([
      (async () => { try { state.workers = await api('/workers'); delete state.errors.workers; } catch (error) { state.errors.workers = error.message; } renderWorkers(); })(),
      (async () => { try { state.jobs = await api('/jobs?limit=20'); state.loaded = true; delete state.errors.jobs; } catch (error) { state.errors.jobs = error.message; } renderJobs(); })(),
      (async () => { if (state.agentRunId) return; try { const runs = await api('/agent-runs?limit=1'); if (runs.length) { state.agentRun = runs[0]; state.agentRunId = runs[0].run_id; } delete state.errors.agentRun; } catch (error) { state.errors.agentRun = error.message; } })(),
    ]);
    await refreshAgentRun();
    if (!state.jobId && state.jobs.length) await loadJob(state.jobs[0].job_id);
    else await refreshDetail();
    renderConnection();
  } finally { refreshBusy = false; }
}
function setView(view) {
  state.view = view;
  for (const name of ['overview','results','activity','traces']) $(`${name}View`).hidden = name !== view;
  document.querySelectorAll('.view-tab[data-view],.rail-button[data-view]').forEach(button => {
    button.classList.toggle('active',button.dataset.view === view);
    if (button.dataset.view === view) button.setAttribute('aria-current','page');
    else button.removeAttribute('aria-current');
  });
}
function openDialog(id) { if (!$(id).open) $(id).showModal(); }
function parseParams(id) {
  const parts = $(id).value.split(',').map(value => value.trim());
  if (!parts.length || parts.some(value => !/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) <= 0)) throw new Error('Parametreler virgülle ayrılmış pozitif tam sayılar olmalı.');
  return [...new Set(parts.map(Number))];
}
function gridParams() {
  const fast = parseParams('fastParams');
  const slow = parseParams('slowParams');
  if (fast.length * slow.length > 100) throw new Error('Bir çalışmada en fazla 100 kombinasyon oluşturabilirsiniz.');
  return {fast,slow};
}
function updateGridSummary() {
  try { const {fast,slow} = gridParams(); $('gridSummary').textContent = `${fast.length} × ${slow.length} parametre = ${fast.length * slow.length} görev`; $('jobFormError').textContent = ''; updateBudgetNote(fast.length * slow.length); }
  catch (error) { $('gridSummary').textContent = 'Parametrelerinizi kontrol edin'; $('jobFormError').textContent = error.message; updateBudgetNote(0); }
}
function openResult(taskId) {
  const result = results().find(item => item.task_id === taskId);
  if (!result) return;
  html('resultDetail', `<div class="result-detail-score"><strong>${number(result.score,4)}</strong><span>backtest skoru</span></div><div class="result-properties"><div><span>WORKER</span><strong>${escapeHTML(result.worker_id)}</strong></div><div><span>SÜRE</span><strong>${number(result.duration_ms)} ms</strong></div><div><span>LLM SAĞLAYICI</span><strong>${escapeHTML(result.llm_provider)}</strong></div><div><span>GÖREV</span><strong>#${escapeHTML(result.task_id.slice(0,12))}</strong></div></div><h3>Parametreler</h3><pre class="params-block">${escapeHTML(JSON.stringify(result.params,null,2))}</pre><h3>${result.llm_provider === 'mock' ? 'Mock sağlayıcı yorumu' : 'Agent yorumu'}</h3><p class="result-commentary">${escapeHTML(result.commentary || 'Yorum bulunmuyor.')}</p>`);
  if (bestResults()[0]?.task_id === taskId) state.reviewed.add(state.jobId);
  renderChecklist();
  openDialog('resultDialog');
}

document.querySelectorAll('i[data-icon]').forEach(node => { node.outerHTML = icon(node.dataset.icon); });
document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if (button.dataset.view) setView(button.dataset.view);
  if (button.hasAttribute('data-new-job')) { $('jobFormError').textContent = ''; renderTemplateOptions(); updateGridSummary(); openDialog('newJobDialog'); }
  if (button.hasAttribute('data-open-workers')) { $('workerFormError').textContent = ''; openDialog('workersDialog'); }
  if (button.hasAttribute('data-close-dialog')) button.closest('dialog').close();
  if (button.dataset.jobId) await loadJob(button.dataset.jobId);
  if (button.dataset.resultId) openResult(button.dataset.resultId);
  if (button.dataset.filter) {
    state.filter = button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(item => { item.classList.toggle('active', item === button); item.setAttribute('aria-pressed',item === button); });
    renderJobs();
  }
  if (button.dataset.stopWorker) {
    button.disabled = true;
    try { await api(`/workers/${encodeURIComponent(button.dataset.stopWorker)}`,{method:'DELETE'}); toast('Worker durduruldu.'); await refreshAll(); }
    catch (error) { $('workerFormError').textContent = error.message; }
    finally { button.disabled = false; }
  }
  if (button.dataset.retryTask) {
    button.disabled = true;
    try { await api(`/jobs/${encodeURIComponent(state.jobId)}/tasks/${encodeURIComponent(button.dataset.retryTask)}/retry`,{method:'POST'}); toast('Görev yeniden kuyruğa alındı.'); await refreshDetail(); }
    catch (error) { toast(error.message,true); }
    finally { button.disabled = false; }
  }
});
for (const id of ['globalSearch','jobSearch']) $(id).addEventListener('input',event => {
  state.search = event.target.value;
  $('globalSearch').value = state.search;
  $('jobSearch').value = state.search;
  renderJobs();
});
$('resultSort').addEventListener('change',renderResults);
$('helpButton').addEventListener('click',() => openDialog('helpDialog'));
$('newAgentRunButton').addEventListener('click',() => { $('agentRunFormError').textContent = ''; openDialog('agentRunDialog'); });
$('insightButton').addEventListener('click',() => { $('insightCard').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',block:'center'}); $('insightCard').focus({preventScroll:true}); });
$('refreshButton').addEventListener('click',async () => { $('refreshButton').disabled = true; await refreshAll(); $('refreshButton').disabled = false; toast(Object.keys(state.errors).length ? 'Bazı verilere ulaşılamadı. Bağlantıyı kontrol edin.' : 'Çalışma alanı güncellendi.',Object.keys(state.errors).length > 0); });
$('copyButton').addEventListener('click',async () => { try { await navigator.clipboard.writeText(state.jobId); toast('İş kimliği kopyalandı.'); } catch { toast('Panoya erişilemiyor. İş kimliğini detaydan seçerek kopyalayabilirsiniz.',true); $('jobId').textContent = state.jobId; } });

$('exportJsonButton').addEventListener('click',exportResultsJson);
$('exportCsvButton').addEventListener('click',exportResultsCsv);

$('cancelJobButton').addEventListener('click',async () => {
  if (!state.jobId) return;
  $('cancelJobButton').disabled = true;
  try { const res = await api(`/jobs/${encodeURIComponent(state.jobId)}/cancel`,{method:'POST'}); toast(`${res.cancelled} görev iptal edildi.`); await refreshDetail(); await refreshAll(); }
  catch (error) { toast(error.message,true); }
  finally { $('cancelJobButton').disabled = false; }
});

$('refineJobButton').addEventListener('click',async () => {
  if (!state.jobId) return;
  $('refineJobButton').disabled = true;
  try {
    const job = await api(`/jobs/${encodeURIComponent(state.jobId)}/refine`,{method:'POST'});
    toast(`${job.task_ids.length} görevlik daraltılmış tarama başlatıldı.`);
    await loadJob(job.job_id);
    await refreshAll();
  } catch (error) { toast(error.message,true); }
  finally { $('refineJobButton').disabled = false; }
});

$('rerunJobButton').addEventListener('click',async () => {
  if (!state.job) return;
  $('rerunJobButton').disabled = true;
  try {
    const tasks = state.job.tasks.map(task => ({params:task.payload?.params ?? task.payload}));
    const job = await api('/jobs',{method:'POST',body:JSON.stringify({tasks})});
    recordBudgetUsage(tasks.length);
    toast(`${tasks.length} görevle iş tekrar başlatıldı.`);
    await loadJob(job.job_id);
    await refreshAll();
  } catch (error) { toast(error.message,true); }
  finally { $('rerunJobButton').disabled = false; }
});

$('deleteJobButton').addEventListener('click',async () => {
  if (!state.jobId) return;
  if (!confirm('Bu işi ve tüm görevlerini silmek istediğinize emin misiniz? Bu işlem geri alınamaz.')) return;
  $('deleteJobButton').disabled = true;
  try {
    await api(`/jobs/${encodeURIComponent(state.jobId)}`,{method:'DELETE'});
    toast('İş silindi.');
    closeStream();
    state.jobId = null; state.job = null;
    await refreshAll();
    if (!state.jobs.length) {
      $('jobTitle').textContent = 'Birlikte daha fazlası mümkün';
      $('jobId').textContent = 'İlk işinizi oluşturun';
      $('jobStatus').className = 'badge neutral';
      $('jobStatus').textContent = 'Başlamaya hazır';
      $('gettingStarted').hidden = false;
    }
  } catch (error) { toast(error.message,true); }
  finally { $('deleteJobButton').disabled = false; }
});

$('apiKeyButton').addEventListener('click',() => {
  $('apiKeyInput').value = '';
  $('apiKeyFormNote').textContent = getApiKey() ? 'Bir anahtar kayıtlı. Değiştirmek için yeni değeri girip kaydedin.' : 'Kayıtlı anahtar yok. Orchestrator ORCH_API_KEY olmadan çalışıyorsa bu adım gerekmez.';
  openDialog('apiKeyDialog');
});
$('apiKeyForm').addEventListener('submit',event => {
  event.preventDefault();
  const value = $('apiKeyInput').value.trim();
  if (value) { setApiKey(value); toast('API anahtarı kaydedildi.'); }
  $('apiKeyDialog').close();
});
$('clearApiKeyButton').addEventListener('click',() => { setApiKey(''); $('apiKeyInput').value = ''; $('apiKeyFormNote').textContent = 'Anahtar temizlendi.'; toast('API anahtarı temizlendi.'); });

$('templateSelect').addEventListener('change',() => {
  const selected = getTemplates().find(t => t.name === $('templateSelect').value);
  $('deleteTemplateButton').disabled = !selected;
  if (selected) { $('fastParams').value = selected.fast; $('slowParams').value = selected.slow; updateGridSummary(); }
});
$('saveTemplateButton').addEventListener('click',() => {
  try {
    gridParams();
    const name = $('templateName').value.trim() || `Şablon ${getTemplates().length + 1}`;
    const templates = getTemplates().filter(t => t.name !== name);
    templates.push({name, fast:$('fastParams').value, slow:$('slowParams').value});
    saveTemplates(templates);
    renderTemplateOptions();
    $('templateSelect').value = name;
    $('deleteTemplateButton').disabled = false;
    $('templateName').value = '';
    toast(`"${name}" şablon olarak kaydedildi.`);
  } catch (error) { $('jobFormError').textContent = error.message; }
});
$('deleteTemplateButton').addEventListener('click',() => {
  const name = $('templateSelect').value;
  if (!name) return;
  saveTemplates(getTemplates().filter(t => t.name !== name));
  renderTemplateOptions();
  toast('Şablon silindi.');
});
function applyTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  html('themeButton',icon(dark ? 'sun' : 'moon'));
  $('themeButton').setAttribute('aria-label',dark ? 'Açık temaya geç' : 'Koyu temaya geç');
}
try { applyTheme(localStorage.getItem('agentos-theme') === 'dark'); } catch { applyTheme(false); }
$('themeButton').addEventListener('click',() => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  applyTheme(dark);
  try { localStorage.setItem('agentos-theme',dark ? 'dark' : 'light'); } catch { /* Theme still works without storage. */ }
});
document.addEventListener('keydown',event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) { event.preventDefault(); const search = $('globalSearch').offsetWidth ? $('globalSearch') : $('jobSearch'); search.focus(); search.select(); }
});
for (const id of ['fastParams','slowParams']) $(id).addEventListener('input',updateGridSummary);
$('newJobForm').addEventListener('submit',async event => {
  event.preventDefault();
  if ($('submitJobButton').disabled) return;
  $('jobFormError').textContent = '';
  try {
    const {fast,slow} = gridParams();
    $('submitJobButton').disabled = true;
    const tasks = fast.flatMap(fast_ma => slow.map(slow_ma => ({params:{fast_ma,slow_ma}})));
    const job = await api('/jobs',{method:'POST',body:JSON.stringify({tasks})});
    recordBudgetUsage(tasks.length);
    $('newJobDialog').close();
    state.search = ''; state.filter = 'all';
    $('globalSearch').value = ''; $('jobSearch').value = '';
    document.querySelectorAll('[data-filter]').forEach(button => { button.classList.toggle('active',button.dataset.filter === 'all'); button.setAttribute('aria-pressed',button.dataset.filter === 'all'); });
    setView('overview');
    await loadJob(job.job_id);
    await refreshAll();
    toast(`${tasks.length} görev oluşturuldu.`);
  } catch (error) { $('jobFormError').textContent = error.message; }
  finally { $('submitJobButton').disabled = false; }
});
$('agentRunForm').addEventListener('submit',async event => {
  event.preventDefault();
  if ($('startAgentRunButton').disabled) return;
  const goal = $('agentGoal').value.trim();
  const max_rounds = Number($('agentMaxRounds').value);
  const max_tasks_per_round = Number($('agentMaxTasks').value);
  const trader_count = Number($('agentTraderCount').value);
  if (goal.length < 3) { $('agentRunFormError').textContent = 'Hedef en az 3 karakter olmalı.'; return; }
  $('startAgentRunButton').disabled = true;
  $('agentRunFormError').textContent = '';
  try {
    const run = await api('/agent-runs',{method:'POST',body:JSON.stringify({goal,max_rounds,max_tasks_per_round,trader_count})});
    state.agentRun = run;
    state.agentRunId = run.run_id;
    state.agentRunNotified = null;
    $('agentRunDialog').close();
    setView('overview');
    renderAgentRun();
    toast('Planner hedefi analiz etmeye başladı.');
    await refreshAll();
  } catch (error) { $('agentRunFormError').textContent = error.message; }
  finally { $('startAgentRunButton').disabled = false; }
});
$('scaleForm').addEventListener('submit',async event => {
  event.preventDefault();
  if ($('scaleButton').disabled) return;
  const count = Number($('scaleInput').value);
  if (!Number.isInteger(count) || count < 0 || count > 50) { $('workerFormError').textContent = '0 ile 50 arasında bir tam sayı girin.'; return; }
  $('scaleButton').disabled = true;
  $('scaleButton').textContent = 'Hazırlanıyor…';
  $('workerFormError').textContent = '';
  try { state.workers = await api('/workers/scale',{method:'POST',body:JSON.stringify({count})}); renderWorkers(); await refreshAll(); toast('Worker kümesi güncellendi.'); }
  catch (error) { $('workerFormError').textContent = error.message; }
  finally { $('scaleButton').disabled = false; $('scaleButton').textContent = 'Kümeyi büyüt'; }
});
renderDetail();
refreshAll();
setInterval(() => { if (!document.hidden) refreshAll(); },4000);
document.addEventListener('visibilitychange',() => { if (document.hidden) closeStream(); else refreshAll(); });
window.addEventListener('pagehide',closeStream);
window.addEventListener('pageshow',event => { if (event.persisted) refreshAll(); });

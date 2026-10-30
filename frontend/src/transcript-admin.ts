import './styles/transcript-admin.css';

const API_BASE = '/api/commercial/admin';
const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

type CDK = {
  id: number;
  code: string;
  duration_limit_minutes: number;
  total_uses: number;
  used_uses: number;
  remaining_uses: number;
  enabled: boolean;
  created_at: number;
  last_used_at: number | null;
  history_count?: number;
};

type HistoryRecord = {
  video_url: string;
  final_text: string;
};

type Statistics = {
  total_codes: number;
  active_codes: number;
  total_uses: number;
  used_uses: number;
  today_uses: number;
  today_duration_seconds: number;
};
type Capabilities = {
  local_ready: boolean;
  dashscope_ready: boolean;
  asr_provider: 'local' | 'dashscope';
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch { /* keep fallback */ }
    if (response.status === 401 && path !== '/login') showLogin();
    throw new Error(message);
  }
  return response.json();
}

function showToast(message: string) {
  const toast = element('adminToast');
  toast.textContent = message;
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 2400);
}

function showLogin(message = '') {
  element('adminApp').hidden = true;
  element('adminLogin').hidden = false;
  const error = element('loginError');
  error.textContent = message;
  error.hidden = !message;
  window.setTimeout(() => element<HTMLInputElement>('adminUsername').focus(), 80);
}

function showAdmin(usingDefaultPassword: boolean) {
  element('adminLogin').hidden = true;
  element('adminApp').hidden = false;
  element('defaultPasswordWarning').hidden = !usingDefaultPassword;
  loadDashboard();
  loadCdks();
  loadSettings();
}

function formatDate(timestamp: number) {
  if (!timestamp) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(timestamp * 1000));
}

function renderStatistics(stats: Statistics) {
  element('statTotalCodes').textContent = String(stats.total_codes);
  element('statActiveCodes').textContent = String(stats.active_codes);
  element('statTotalUses').textContent = String(stats.total_uses);
  element('statUsedUses').textContent = String(stats.used_uses);
  element('statTodayUses').textContent = String(stats.today_uses);
  const minutes = stats.today_duration_seconds / 60;
  element('statTodayMinutes').textContent = minutes >= 10 ? String(Math.round(minutes)) : minutes.toFixed(1).replace(/\.0$/, '');
}

async function loadDashboard() {
  try {
    const result = await request<{ statistics: Statistics }>('/dashboard');
    renderStatistics(result.statistics);
    element('dashboardDate').textContent = new Intl.DateTimeFormat('zh-CN', {
      month: 'long', day: 'numeric', weekday: 'short',
    }).format(new Date());
  } catch (error) {
    showToast(error instanceof Error ? error.message : '首页统计读取失败');
  }
}

function renderCdkHistory(records: HistoryRecord[]) {
  const container = element<HTMLElement>('cdkHistoryRecords');
  container.replaceChildren();
  element('cdkHistoryEmpty').hidden = records.length > 0;
  for (const record of records) {
    const card = document.createElement('article');
    card.className = 'admin-history-card';
    const link = document.createElement('a');
    link.href = record.video_url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = record.video_url;
    const copy = document.createElement('pre');
    copy.textContent = record.final_text;
    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.textContent = '复制文案';
    copyButton.addEventListener('click', async () => {
      await navigator.clipboard.writeText(record.final_text);
      showToast('文案已复制');
    });
    card.append(link, copy, copyButton);
    container.appendChild(card);
  }
}

async function openCdkHistory(cdk: CDK) {
  const dialog = element<HTMLDialogElement>('cdkHistoryDialog');
  element('cdkHistoryCode').textContent = cdk.code;
  element('cdkHistoryEmpty').hidden = true;
  element('cdkHistoryRecords').replaceChildren();
  dialog.showModal();
  try {
    const result = await request<{ records: HistoryRecord[] }>(`/cdks/${cdk.id}/history`);
    renderCdkHistory(result.records);
  } catch (error) {
    dialog.close();
    showToast(error instanceof Error ? error.message : '提取记录读取失败');
  }
}

function renderCdks(cdks: CDK[]) {
  const body = element<HTMLTableSectionElement>('cdkTableBody');
  body.innerHTML = '';
  element('emptyTable').hidden = cdks.length > 0;
  for (const cdk of cdks) {
    const exhausted = cdk.remaining_uses <= 0;
    const status = !cdk.enabled ? '已失效' : exhausted ? '已用完' : '可使用';
    const statusClass = !cdk.enabled ? 'disabled' : exhausted ? 'exhausted' : 'active';
    const row = document.createElement('tr');
    row.innerHTML = `
      <td><div class="code-cell"><code></code><button type="button" data-copy>复制</button></div></td>
      <td><span class="tier-pill">${cdk.duration_limit_minutes} 分钟</span></td>
      <td><strong>${cdk.used_uses} / ${cdk.total_uses}</strong><small>剩余 ${cdk.remaining_uses} 次</small></td>
      <td><span class="status-pill ${statusClass}"><i></i>${status}</span></td>
      <td>${formatDate(cdk.created_at)}</td>
      <td><div class="row-actions"><button class="status-action ${cdk.enabled ? 'danger' : ''}" type="button">${cdk.enabled ? '使其失效' : '重新启用'}</button>${cdk.history_count ? `<button class="history-action" type="button">记录 ${cdk.history_count}</button>` : ''}</div></td>
    `;
    row.querySelector('code')!.textContent = cdk.code;
    row.querySelector<HTMLButtonElement>('[data-copy]')!.addEventListener('click', async () => {
      await navigator.clipboard.writeText(cdk.code);
      showToast('兑换码已复制');
    });
    row.querySelector<HTMLButtonElement>('.status-action')!.addEventListener('click', async event => {
      const button = event.currentTarget as HTMLButtonElement;
      button.disabled = true;
      try {
        await request(`/cdks/${cdk.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !cdk.enabled }) });
        showToast(cdk.enabled ? '兑换码已失效' : '兑换码已重新启用');
        await loadCdks();
        await loadDashboard();
      } catch (error) {
        showToast(error instanceof Error ? error.message : '操作失败');
        button.disabled = false;
      }
    });
    row.querySelector<HTMLButtonElement>('.history-action')?.addEventListener('click', () => openCdkHistory(cdk));
    body.appendChild(row);
  }
}

function currentFilterParams() {
  const params = new URLSearchParams();
  const query = element<HTMLInputElement>('searchInput').value.trim();
  const status = element<HTMLSelectElement>('statusFilter').value;
  const duration = element<HTMLSelectElement>('durationFilter').value;
  const uses = element<HTMLInputElement>('usesFilter').value.trim();
  if (query) params.set('q', query);
  if (status && status !== 'all') params.set('status', status);
  if (duration) params.set('duration_limit_minutes', duration);
  if (uses) params.set('total_uses', uses);
  return params;
}

async function loadCdks() {
  try {
    const params = currentFilterParams();
    const suffix = params.size ? `?${params.toString()}` : '';
    const result = await request<{ cdks: CDK[]; matched_count: number }>(`/cdks${suffix}`);
    renderCdks(result.cdks);
    element('matchedCount').textContent = String(result.matched_count);
  } catch (error) {
    showToast(error instanceof Error ? error.message : '兑换码读取失败');
  }
}

function renderCapabilities(capabilities: Capabilities) {
  const local = element('localStatus');
  local.textContent = capabilities.local_ready ? '可用' : '未就绪';
  local.className = capabilities.local_ready ? 'ready' : 'not-ready';
  const cloud = element('dashscopeStatus');
  cloud.textContent = capabilities.dashscope_ready ? 'API Key 已配置' : '缺少 API Key';
  cloud.className = capabilities.dashscope_ready ? 'ready' : 'not-ready';
}

async function loadSettings() {
  try {
    const result = await request<{
      settings: { asr_provider: 'local' | 'dashscope' };
      capabilities: Capabilities;
    }>('/settings');
    const radio = document.querySelector<HTMLInputElement>(`input[name="asrProvider"][value="${result.settings.asr_provider}"]`);
    if (radio) radio.checked = true;
    renderCapabilities(result.capabilities);
  } catch (error) {
    showToast(error instanceof Error ? error.message : '配置读取失败');
  }
}

element<HTMLFormElement>('adminLoginForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = element<HTMLButtonElement>('adminLoginButton');
  const errorBox = element('loginError');
  button.disabled = true;
  button.textContent = '正在验证…';
  errorBox.hidden = true;
  try {
    const result = await request<{ authorized: boolean; using_default_password: boolean }>('/login', {
      method: 'POST',
      body: JSON.stringify({
        username: element<HTMLInputElement>('adminUsername').value,
        password: element<HTMLInputElement>('adminPassword').value,
      }),
    });
    element<HTMLInputElement>('adminPassword').value = '';
    showAdmin(result.using_default_password);
  } catch (error) {
    errorBox.textContent = error instanceof Error ? error.message : '登录失败';
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = '进入管理后台';
  }
});

element<HTMLFormElement>('generateForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = element<HTMLButtonElement>('generateButton');
  button.disabled = true;
  button.textContent = '生成中…';
  try {
    const result = await request<{ cdks: CDK[]; statistics: Statistics }>('/cdks', {
      method: 'POST',
      body: JSON.stringify({
        duration_limit_minutes: Number(element<HTMLSelectElement>('durationTier').value),
        total_uses: Number(element<HTMLInputElement>('totalUses').value),
        quantity: Number(element<HTMLInputElement>('quantity').value),
      }),
    });
    const codes = result.cdks.map(cdk => cdk.code).join('\n');
    element('generatedResults').hidden = false;
    element('generatedCodes').textContent = codes;
    await loadCdks();
    await loadDashboard();
    showToast(`已生成 ${result.cdks.length} 个兑换码`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : '生成失败');
  } finally {
    button.disabled = false;
    button.textContent = '生成 CDK';
  }
});

element('copyGenerated').addEventListener('click', async () => {
  await navigator.clipboard.writeText(element('generatedCodes').textContent || '');
  showToast('本次生成的兑换码已复制');
});

const historyDialog = element<HTMLDialogElement>('cdkHistoryDialog');
element('closeCdkHistory').addEventListener('click', () => historyDialog.close());
historyDialog.addEventListener('click', event => {
  if (event.target === historyDialog) historyDialog.close();
});

element<HTMLFormElement>('searchForm').addEventListener('submit', event => {
  event.preventDefault();
  loadCdks();
});

element('clearSearch').addEventListener('click', () => {
  element<HTMLInputElement>('searchInput').value = '';
  element<HTMLSelectElement>('statusFilter').value = 'all';
  element<HTMLSelectElement>('durationFilter').value = '';
  element<HTMLInputElement>('usesFilter').value = '';
  loadCdks();
});

element('exportCodes').addEventListener('click', async () => {
  const button = element<HTMLButtonElement>('exportCodes');
  const params = currentFilterParams();
  const suffix = params.size ? `?${params.toString()}` : '';
  button.disabled = true;
  button.textContent = '导出中…';
  try {
    const response = await fetch(`${API_BASE}/cdks/export${suffix}`);
    if (!response.ok) {
      let message = `导出失败 (${response.status})`;
      try {
        const body = await response.json();
        message = body.detail || message;
      } catch { /* keep fallback */ }
      if (response.status === 401) showLogin();
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || 'cdks.txt';
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
    showToast('已按当前查询条件导出 TXT');
  } catch (error) {
    showToast(error instanceof Error ? error.message : '导出失败');
  } finally {
    button.disabled = false;
    button.textContent = '导出 TXT';
  }
});

element('batchDeleteCodes').addEventListener('click', async () => {
  const button = element<HTMLButtonElement>('batchDeleteCodes');
  const params = currentFilterParams();
  const suffix = params.size ? `?${params.toString()}` : '';
  button.disabled = true;
  try {
    const preview = await request<{ cdks: CDK[]; matched_count: number }>(`/cdks${suffix}`);
    if (preview.matched_count < 1) throw new Error('当前查询条件没有可删除的兑换码');
    const scopeText = params.size ? '当前查询条件匹配的' : '全部';
    const confirmed = window.confirm(
      `确定永久删除${scopeText} ${preview.matched_count} 个兑换码吗？\n\n相关登录会话和使用流水也会一并删除，此操作无法恢复。`,
    );
    if (!confirmed) return;
    params.set('expected_count', String(preview.matched_count));
    const result = await request<{ deleted: number; statistics: Statistics }>(`/cdks?${params.toString()}`, { method: 'DELETE' });
    renderStatistics(result.statistics);
    await loadCdks();
    showToast(`已删除 ${result.deleted} 个兑换码`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : '批量删除失败');
  } finally {
    button.disabled = false;
  }
});

element<HTMLFormElement>('settingsForm').addEventListener('submit', async event => {
  event.preventDefault();
  const provider = document.querySelector<HTMLInputElement>('input[name="asrProvider"]:checked')?.value || 'local';
  const button = element<HTMLButtonElement>('saveSettings');
  button.disabled = true;
  try {
    const result = await request<{ settings: unknown; capabilities: Capabilities }>('/settings', {
      method: 'PUT',
      body: JSON.stringify({ asr_provider: provider }),
    });
    renderCapabilities(result.capabilities);
    showToast('语音识别配置已保存');
  } catch (error) {
    showToast(error instanceof Error ? error.message : '保存失败');
  } finally {
    button.disabled = false;
  }
});

document.querySelectorAll<HTMLButtonElement>('[data-view-target]').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelectorAll<HTMLButtonElement>('[data-view-target]').forEach(item => item.classList.toggle('active', item === button));
    document.querySelectorAll<HTMLElement>('.admin-view').forEach(view => { view.hidden = view.id !== button.dataset.viewTarget; });
    element('viewTitle').textContent = {
      homeView: '首页',
      cdkView: 'CDK 管理',
      settingsView: '语音识别配置',
    }[button.dataset.viewTarget || ''] || '首页';
    if (button.dataset.viewTarget === 'homeView') loadDashboard();
  });
});

element('adminLogout').addEventListener('click', async () => {
  await request('/session', { method: 'DELETE' }).catch(() => undefined);
  showLogin();
});

request<{ authorized: boolean; using_default_password: boolean }>('/session')
  .then(result => showAdmin(result.using_default_password))
  .catch(() => showLogin());

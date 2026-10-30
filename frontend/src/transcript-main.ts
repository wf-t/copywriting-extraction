import './styles/transcript-extractor.css';
import { saveLocalHistory } from './services/transcriptHistory';

const API_BASE = '/api/transcript-extractor';
const COMMERCIAL_BASE = '/api/commercial';

type Platform = 'douyin' | 'xiaohongshu' | 'kuaishou' | 'bilibili';

const PLATFORM_CONFIG: Record<Platform, {
  name: string;
  icon: string;
  placeholder: string;
  linkPattern: RegExp;
}> = {
  douyin: {
    name: '抖音',
    icon: '抖',
    placeholder: '粘贴抖音分享链接或完整分享文本，例如：\n这些大开脑洞的懒人设计… https://v.douyin.com/xxxxx/',
    linkPattern: /douyin\.com/i,
  },
  xiaohongshu: {
    name: '小红书',
    icon: '小红书',
    placeholder: '粘贴小红书视频笔记分享链接或完整分享文本，例如：\n发现一篇有趣的视频笔记… https://xhslink.com/xxxxx',
    linkPattern: /(?:xiaohongshu\.com|xhslink\.(?:com|cn)|rednote\.com)/i,
  },
  kuaishou: {
    name: '快手',
    icon: '快',
    placeholder: '粘贴快手视频分享链接或完整分享文本，例如：\n复制打开快手… https://v.kuaishou.com/xxxxx',
    linkPattern: /(?:kuaishou\.(?:com|cn)|chenzhongtech\.(?:com|cn)|gifshow\.com)/i,
  },
  bilibili: {
    name: 'B站',
    icon: 'B',
    placeholder: '粘贴B站视频链接或分享文本；多P视频可保留 ?p=2，例如：\nhttps://www.bilibili.com/video/BVxxxxxxxx?p=2',
    linkPattern: /(?:bilibili\.com|b23\.tv)/i,
  },
};

type CDKInfo = {
  id: number;
  code: string;
  duration_limit_minutes: number;
  total_uses: number;
  used_uses: number;
  remaining_uses: number;
  enabled: boolean;
};

type VideoInfo = {
  platform: Platform;
  video_id: string;
  title: string;
  author: string;
  duration: number;
  source_url: string;
};

type Task = {
  id: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  stage_label: string;
  progress: number;
  error: string;
  quota?: Pick<CDKInfo, 'duration_limit_minutes' | 'remaining_uses'>;
  result?: {
    video: VideoInfo;
    semantic_text: string;
    semantic_model: string;
    semantic_error: string;
  };
};

const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const accessGate = element<HTMLElement>('accessGate');
const appShell = element<HTMLElement>('appShell');
const platformHome = element<HTMLElement>('platformHome');
const workspace = element<HTMLElement>('workspace');
const shareText = element<HTMLTextAreaElement>('shareText');
const semanticText = element<HTMLTextAreaElement>('semanticText');
const extractButton = element<HTMLButtonElement>('extractButton');
const infoButton = element<HTMLButtonElement>('infoButton');
const progressBox = element<HTMLElement>('progressBox');
const errorBox = element<HTMLElement>('errorBox');
const toast = element<HTMLElement>('toast');

let currentTaskId = '';
let pollTimer: number | undefined;
let currentVideo: VideoInfo | null = null;
let currentCdk: CDKInfo | null = null;
let semanticModel = 'deepseek-chat';
let currentPlatform: Platform = 'douyin';

function showToast(message: string) {
  toast.textContent = message;
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 2200);
}

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
    if (response.status === 401) showAccessGate(message);
    throw new Error(message);
  }
  return response.json();
}

async function commercialRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${COMMERCIAL_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch { /* keep fallback */ }
    throw new Error(message);
  }
  return response.json();
}

function renderQuota(cdk: CDKInfo) {
  currentCdk = cdk;
  element('quotaTier').textContent = `${cdk.duration_limit_minutes} 分钟档`;
  element('quotaRemaining').textContent = `剩余 ${cdk.remaining_uses} 次`;
}

function showAccessGate(message = '') {
  appShell.hidden = true;
  accessGate.hidden = false;
  const error = element('accessError');
  error.textContent = message;
  error.hidden = !message;
  window.setTimeout(() => element<HTMLInputElement>('accessCode').focus(), 80);
}

function showApplication(cdk: CDKInfo) {
  renderQuota(cdk);
  accessGate.hidden = true;
  appShell.hidden = false;
  loadHealth();
}

async function bootstrapAccess() {
  try {
    const result = await commercialRequest<{ authorized: boolean; cdk?: CDKInfo }>('/access');
    if (result.authorized && result.cdk) showApplication(result.cdk);
    else showAccessGate();
  } catch {
    showAccessGate('无法连接本地服务，请确认后端已启动');
  }
}

function openPlatform(event: Event) {
  const card = event.currentTarget as HTMLElement;
  const platform = card.dataset.platform as Platform;
  if (!PLATFORM_CONFIG[platform]) return;
  if (platform !== currentPlatform) resetResult();
  currentPlatform = platform;
  const config = PLATFORM_CONFIG[platform];
  const icon = element('workspacePlatformIcon');
  icon.className = `mini-platform-icon ${platform}`;
  icon.textContent = config.icon;
  element('workspacePlatformTitle').textContent = `${config.name}文案提取`;
  element('shareTextLabel').textContent = `${config.name}分享文本`;
  shareText.placeholder = config.placeholder;
  platformHome.hidden = true;
  workspace.hidden = false;
  document.title = `${config.name}文案提取 · 文案提取器`;
  window.scrollTo({ top: 0, behavior: 'smooth' });
  window.setTimeout(() => shareText.focus(), 120);
}

function openHome() {
  workspace.hidden = true;
  platformHome.hidden = false;
  document.title = '文案提取器';
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function setBusy(busy: boolean) {
  extractButton.disabled = busy;
  infoButton.disabled = busy;
  shareText.disabled = busy;
  extractButton.innerHTML = busy ? '<span class="button-spinner"></span> 正在处理' : '<span>▤</span> 提取并整理文案';
}

function showError(message: string) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function clearError() {
  errorBox.hidden = true;
  errorBox.textContent = '';
}

function updateProgress(label: string, progress: number) {
  progressBox.hidden = false;
  element('progressLabel').textContent = label;
  element('progressPercent').textContent = `${progress}%`;
  (element('progressBar') as HTMLElement).style.width = `${progress}%`;
}

function formatDuration(seconds: number) {
  if (!seconds) return '—';
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return minutes ? `${minutes}分${String(remainder).padStart(2, '0')}秒` : `${remainder}秒`;
}

function renderVideo(video: VideoInfo) {
  currentVideo = video;
  element('videoEmpty').hidden = true;
  element('videoDetails').hidden = false;
  element('videoId').textContent = video.video_id || '—';
  element('videoTitle').textContent = video.title || '未命名视频';
  element('videoAuthor').textContent = video.author || '未获取';
  element('videoDuration').textContent = formatDuration(video.duration);
}

function resizeSemanticResult() {
  semanticText.style.height = 'auto';
  semanticText.style.height = `${Math.max(360, semanticText.scrollHeight)}px`;
}

function renderSemanticResult(text: string, error = '', model = 'deepseek-chat') {
  semanticModel = model;
  const semanticEmpty = element('semanticEmpty');
  const semanticError = element('semanticError');
  semanticEmpty.hidden = Boolean(text || error);
  semanticText.hidden = !text;
  semanticError.hidden = !error;
  semanticError.textContent = error ? `DeepSeek 整理未完成：${error}。请稍后重新提取。` : '';
  semanticText.value = text;
  element('semanticWordCount').textContent = `${text.replace(/\s/g, '').length} 字`;
  element('semanticModelLabel').textContent = model;
  ['copySemanticButton', 'downloadSemanticButton'].forEach(id => {
    (element(id) as HTMLButtonElement).disabled = !text;
  });
  if (text) resizeSemanticResult();
}

function resetResult() {
  if (pollTimer) window.clearTimeout(pollTimer);
  pollTimer = undefined;
  if (currentTaskId) {
    request(`/tasks/${currentTaskId}`, { method: 'DELETE' }).catch(() => undefined);
  }
  currentTaskId = '';
  currentVideo = null;
  shareText.value = '';
  semanticText.value = '';
  element('semanticEmpty').hidden = false;
  semanticText.hidden = true;
  element('semanticError').hidden = true;
  progressBox.hidden = true;
  clearError();
  element('videoEmpty').hidden = false;
  element('videoDetails').hidden = true;
  element('videoId').textContent = '等待解析';
  element('semanticWordCount').textContent = '0 字';
  element('inputCount').textContent = '0 / 5000';
  element('clearInput').hidden = true;
  ['copySemanticButton', 'downloadSemanticButton'].forEach(id => {
    (element(id) as HTMLButtonElement).disabled = true;
  });
  setBusy(false);
}

function validateInput() {
  const value = shareText.value.trim();
  const config = PLATFORM_CONFIG[currentPlatform];
  if (!value || !config.linkPattern.test(value)) {
    showError(`请粘贴包含有效${config.name}链接的分享文本`);
    shareText.focus();
    return '';
  }
  return value;
}

async function fetchInfo() {
  const value = validateInput();
  if (!value) return;
  clearError();
  setBusy(true);
  updateProgress('正在解析链接', 15);
  try {
    const video = await request<VideoInfo>('/info', {
      method: 'POST',
      body: JSON.stringify({ share_text: value, platform: currentPlatform }),
    });
    renderVideo(video);
    progressBox.hidden = true;
    showToast('视频信息获取成功');
  } catch (error) {
    progressBox.hidden = true;
    showError(error instanceof Error ? error.message : '视频信息获取失败');
  } finally {
    setBusy(false);
  }
}

async function pollTask() {
  if (!currentTaskId) return;
  try {
    const task = await request<Task>(`/tasks/${currentTaskId}`);
    updateProgress(task.stage_label, task.progress);
    if (task.status === 'completed' && task.result) {
      renderVideo(task.result.video);
      renderSemanticResult(task.result.semantic_text, task.result.semantic_error, task.result.semantic_model);
      if (currentCdk && task.result.semantic_text) {
        await saveLocalHistory(
          currentCdk.id,
          task.result.video.source_url,
          task.result.semantic_text,
        ).catch(() => showToast('提取完成，但当前浏览器未能保存本地记录'));
      }
      await request(`/tasks/${currentTaskId}`, { method: 'DELETE' }).catch(() => undefined);
      currentTaskId = '';
      setBusy(false);
      showToast('语音文案提取完成');
      return;
    }
    if (task.status === 'failed') {
      showError(task.error || '提取失败');
      await request(`/tasks/${currentTaskId}`, { method: 'DELETE' }).catch(() => undefined);
      currentTaskId = '';
      setBusy(false);
      return;
    }
    pollTimer = window.setTimeout(pollTask, 1000);
  } catch (error) {
    showError(error instanceof Error ? error.message : '读取任务状态失败');
    setBusy(false);
  }
}

async function extractTranscript() {
  const value = validateInput();
  if (!value) return;
  clearError();
  setBusy(true);
  updateProgress('正在创建临时任务', 3);
  try {
    const task = await request<Task>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ share_text: value, platform: currentPlatform }),
    });
    if (task.quota && currentCdk) renderQuota({ ...currentCdk, ...task.quota });
    currentTaskId = task.id;
    updateProgress(task.stage_label, task.progress);
    await pollTask();
  } catch (error) {
    showError(error instanceof Error ? error.message : '无法开始提取');
    setBusy(false);
  }
}

function downloadSemanticResult() {
  const text = semanticText.value.trim();
  if (!text) return;
  const title = currentVideo?.title || '视频文案';
  const safeTitle = title.replace(/[\\/:*?"<>|]/g, '_').slice(0, 60);
  const platformName = PLATFORM_CONFIG[currentVideo?.platform || currentPlatform].name;
  const content = `# ${title}\n\n- 平台：${platformName}\n- 视频 ID：${currentVideo?.video_id || ''}\n- 作者：${currentVideo?.author || ''}\n- 整理模型：${semanticModel}\n\n${text}\n`;
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${safeTitle}_DeepSeek整理稿.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}

document.querySelectorAll<HTMLElement>('[data-platform]').forEach(card => {
  card.addEventListener('click', openPlatform);
});
element('backHome').addEventListener('click', openHome);
element('brandHome').addEventListener('click', openHome);
element('resetButton').addEventListener('click', resetResult);
element('clearInput').addEventListener('click', () => {
  shareText.value = '';
  shareText.dispatchEvent(new Event('input'));
  shareText.focus();
});
infoButton.addEventListener('click', fetchInfo);
extractButton.addEventListener('click', extractTranscript);
semanticText.addEventListener('input', () => {
  element('semanticWordCount').textContent = `${semanticText.value.replace(/\s/g, '').length} 字`;
  resizeSemanticResult();
});
shareText.addEventListener('input', () => {
  element('inputCount').textContent = `${shareText.value.length} / 5000`;
  element('clearInput').hidden = !shareText.value;
  clearError();
});
shareText.addEventListener('keydown', event => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault();
    extractTranscript();
  }
});
element('copySemanticButton').addEventListener('click', async () => {
  await navigator.clipboard.writeText(semanticText.value);
  showToast('DeepSeek 整理稿已复制');
});
element('downloadSemanticButton').addEventListener('click', downloadSemanticResult);

element<HTMLFormElement>('accessForm').addEventListener('submit', async event => {
  event.preventDefault();
  const codeInput = element<HTMLInputElement>('accessCode');
  const button = element<HTMLButtonElement>('accessButton');
  const errorBox = element('accessError');
  errorBox.hidden = true;
  button.disabled = true;
  button.textContent = '正在验证…';
  try {
    const result = await commercialRequest<{ authorized: boolean; cdk: CDKInfo }>('/access', {
      method: 'POST',
      body: JSON.stringify({ code: codeInput.value.trim() }),
    });
    codeInput.value = '';
    showApplication(result.cdk);
  } catch (error) {
    errorBox.textContent = error instanceof Error ? error.message : '兑换码验证失败';
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = '验证并进入';
  }
});

element('logoutButton').addEventListener('click', async () => {
  await commercialRequest('/access', { method: 'DELETE' }).catch(() => undefined);
  currentCdk = null;
  showAccessGate();
});

const helpDialog = element<HTMLDialogElement>('helpDialog');
element('helpButton').addEventListener('click', () => helpDialog.showModal());
element('closeHelp').addEventListener('click', () => helpDialog.close());
helpDialog.addEventListener('click', event => {
  if (event.target === helpDialog) helpDialog.close();
});

function loadHealth() {
  request<{
    ready: boolean;
    model: string;
    semantic_model: string;
    deepseek_ready: boolean;
    asr_label: string;
  }>('/health').then(status => {
    const badge = element('apiBadge');
    badge.classList.toggle('offline', !status.ready || !status.deepseek_ready);
    badge.querySelector('span')!.textContent = status.ready && status.deepseek_ready ? `${status.asr_label} · DeepSeek 已就绪` : '处理环境不完整';
    element('semanticModelLabel').textContent = status.semantic_model;
    element('workspaceAsrLabel').textContent = status.asr_label;
    element('progressAsrLabel').textContent = status.asr_label;
    element('asrMethod').textContent = status.asr_label;
    semanticModel = status.semantic_model;
  })
  .catch(() => {
    const badge = element('apiBadge');
    badge.classList.add('offline');
    badge.querySelector('span')!.textContent = '本地服务未连接';
  });
}

window.addEventListener('beforeunload', () => {
  if (currentTaskId) fetch(`${API_BASE}/tasks/${currentTaskId}`, { method: 'DELETE', keepalive: true }).catch(() => undefined);
});

bootstrapAccess();

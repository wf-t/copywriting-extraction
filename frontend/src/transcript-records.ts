import './styles/transcript-records.css';
import {
  clearLocalHistory,
  deleteLocalHistory,
  listLocalHistory,
  type LocalTranscriptHistory,
} from './services/transcriptHistory';

const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const recordsList = element<HTMLElement>('recordsList');
const recordsState = element<HTMLElement>('recordsState');
const recordsToolbar = element<HTMLElement>('recordsToolbar');
let currentCdkId = 0;

function showToast(message: string) {
  const toast = element('recordsToast');
  toast.textContent = message;
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 2200);
}

function showState(title: string, description: string, action = false) {
  recordsState.hidden = false;
  element('stateTitle').textContent = title;
  element('stateDescription').textContent = description;
  element('stateAction').hidden = !action;
}

function createRecordCard(record: LocalTranscriptHistory) {
  const card = document.createElement('article');
  card.className = 'record-card';

  const linkRow = document.createElement('div');
  linkRow.className = 'record-link-row';
  const label = document.createElement('span');
  label.textContent = '视频链接';
  const link = document.createElement('a');
  link.href = record.video_url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = record.video_url;
  linkRow.append(label, link);

  const copy = document.createElement('pre');
  copy.className = 'record-copy';
  copy.textContent = record.final_text;

  const actions = document.createElement('div');
  actions.className = 'record-actions';
  const copyButton = document.createElement('button');
  copyButton.type = 'button';
  copyButton.textContent = '复制文案';
  copyButton.addEventListener('click', async () => {
    await navigator.clipboard.writeText(record.final_text);
    showToast('文案已复制');
  });
  const deleteButton = document.createElement('button');
  deleteButton.type = 'button';
  deleteButton.className = 'delete-record';
  deleteButton.textContent = '删除记录';
  deleteButton.addEventListener('click', async () => {
    deleteButton.disabled = true;
    try {
      await deleteLocalHistory(record.key);
      await loadRecords();
      showToast('本地记录已删除');
    } catch (error) {
      showToast(error instanceof Error ? error.message : '删除失败');
      deleteButton.disabled = false;
    }
  });
  actions.append(copyButton, deleteButton);
  card.append(linkRow, copy, actions);
  return card;
}

async function loadRecords() {
  const records = await listLocalHistory(currentCdkId);
  recordsList.replaceChildren(...records.map(createRecordCard));
  element('recordCount').textContent = String(records.length);
  recordsToolbar.hidden = records.length === 0;
  if (records.length) {
    recordsState.hidden = true;
  } else {
    showState('还没有提取记录', '成功提取并完成语义整理后，视频链接和最终文案会在这里保留 7 天。');
  }
}

async function bootstrap() {
  try {
    const response = await fetch('/api/commercial/access');
    if (!response.ok) throw new Error(`无法验证兑换码 (${response.status})`);
    const result = await response.json() as { authorized: boolean; cdk?: { id: number } };
    if (!result.authorized || !result.cdk?.id) {
      showState('请先输入兑换码', '输入有效兑换码后即可进入系统查看当前浏览器的本地记录。', true);
      return;
    }
    currentCdkId = result.cdk.id;
    await loadRecords();
  } catch (error) {
    showState('本地记录读取失败', error instanceof Error ? error.message : '请刷新页面重试');
  }
}

element('clearRecords').addEventListener('click', async () => {
  if (!window.confirm('确定清空当前兑换码在此浏览器中的全部提取记录吗？')) return;
  try {
    await clearLocalHistory(currentCdkId);
    await loadRecords();
    showToast('本地记录已清空');
  } catch (error) {
    showToast(error instanceof Error ? error.message : '清空失败');
  }
});

bootstrap();

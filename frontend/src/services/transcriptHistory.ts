const DB_NAME = 'copy-studio-transcript-history';
const DB_VERSION = 1;
const STORE_NAME = 'records';
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000;

type StoredTranscriptHistory = {
  video_url: string;
  final_text: string;
};

export type LocalTranscriptHistory = StoredTranscriptHistory & {
  key: IDBValidKey;
};

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('无法打开本地记录库'));
    request.onblocked = () => reject(new Error('本地记录库正在被其他页面占用'));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error('本地记录操作失败'));
    transaction.onabort = () => reject(transaction.error || new Error('本地记录操作已取消'));
  });
}

function recordKey(cdkId: number): IDBValidKey[] {
  const id = crypto.randomUUID?.() || `${Math.random().toString(36).slice(2)}-${Date.now()}`;
  return [cdkId, Date.now(), id];
}

function cdkRange(cdkId: number): IDBKeyRange {
  return IDBKeyRange.bound([cdkId, 0, ''], [cdkId, Number.MAX_SAFE_INTEGER, '\uffff']);
}

export async function cleanupLocalHistory(now = Date.now()): Promise<number> {
  const database = await openDatabase();
  const transaction = database.transaction(STORE_NAME, 'readwrite');
  const store = transaction.objectStore(STORE_NAME);
  const cutoff = now - SEVEN_DAYS_MS;
  let deleted = 0;
  const cursorDone = new Promise<void>((resolve, reject) => {
    const request = store.openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) {
        resolve();
        return;
      }
      const key = cursor.primaryKey;
      const savedAt = Array.isArray(key) ? Number(key[1]) : 0;
      if (!savedAt || savedAt <= cutoff) {
        cursor.delete();
        deleted += 1;
      }
      cursor.continue();
    };
    request.onerror = () => reject(request.error || new Error('本地记录清理失败'));
  });
  await Promise.all([cursorDone, transactionDone(transaction)]);
  database.close();
  return deleted;
}

export async function saveLocalHistory(cdkId: number, videoUrl: string, finalText: string): Promise<void> {
  const cleanUrl = videoUrl.trim();
  const cleanText = finalText.trim();
  if (!Number.isInteger(cdkId) || cdkId < 1 || !cleanUrl || !cleanText) return;
  await cleanupLocalHistory();
  const database = await openDatabase();
  const transaction = database.transaction(STORE_NAME, 'readwrite');
  transaction.objectStore(STORE_NAME).put(
    { video_url: cleanUrl, final_text: cleanText } satisfies StoredTranscriptHistory,
    recordKey(cdkId),
  );
  await transactionDone(transaction);
  database.close();
}

export async function listLocalHistory(cdkId: number): Promise<LocalTranscriptHistory[]> {
  if (!Number.isInteger(cdkId) || cdkId < 1) return [];
  await cleanupLocalHistory();
  const database = await openDatabase();
  const transaction = database.transaction(STORE_NAME, 'readonly');
  const records: LocalTranscriptHistory[] = [];
  const cursorDone = new Promise<void>((resolve, reject) => {
    const request = transaction.objectStore(STORE_NAME).openCursor(cdkRange(cdkId), 'prev');
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) {
        resolve();
        return;
      }
      const value = cursor.value as StoredTranscriptHistory;
      if (value.video_url && value.final_text) {
        records.push({ key: cursor.primaryKey, video_url: value.video_url, final_text: value.final_text });
      }
      cursor.continue();
    };
    request.onerror = () => reject(request.error || new Error('本地记录读取失败'));
  });
  await Promise.all([cursorDone, transactionDone(transaction)]);
  database.close();
  return records;
}

export async function deleteLocalHistory(key: IDBValidKey): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(STORE_NAME, 'readwrite');
  transaction.objectStore(STORE_NAME).delete(key);
  await transactionDone(transaction);
  database.close();
}

export async function clearLocalHistory(cdkId: number): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(STORE_NAME, 'readwrite');
  const store = transaction.objectStore(STORE_NAME);
  const cursorDone = new Promise<void>((resolve, reject) => {
    const request = store.openCursor(cdkRange(cdkId));
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) {
        resolve();
        return;
      }
      cursor.delete();
      cursor.continue();
    };
    request.onerror = () => reject(request.error || new Error('本地记录清空失败'));
  });
  await Promise.all([cursorDone, transactionDone(transaction)]);
  database.close();
}

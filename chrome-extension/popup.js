// popup.js - UI logic for NHK ONE Key Extractor
let wvdBase64 = null;

// ─── Init ───
document.addEventListener('DOMContentLoaded', async () => {
  // Load saved WVD
  chrome.storage.local.get('wvdData', (data) => {
    if (data.wvdData) {
      wvdBase64 = data.wvdData;
      document.getElementById('wvdStatus').textContent = 'WVD \u30d5\u30a1\u30a4\u30eb\u8aad\u307f\u8fbc\u307f\u6e08\u307f';
      document.getElementById('wvdStatus').className = 'status ok';
      updateExtractButton();
    }
  });
  
  // Check current tab
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (tab && tab.url && tab.url.includes('www.web.nhk') && tab.url.includes('/ep/')) {
    document.getElementById('pageInfo').textContent = 'NHK ONE \u30a8\u30d4\u30bd\u30fc\u30c9\u30da\u30fc\u30b8\u3092\u691c\u51fa\u3057\u307e\u3057\u305f';
    document.getElementById('pageInfo').className = 'info ok';
  } else {
    document.getElementById('pageInfo').textContent = 'NHK ONE \u306e\u30a8\u30d4\u30bd\u30fc\u30c9\u30da\u30fc\u30b8\u3092\u958b\u3044\u3066\u304f\u3060\u3055\u3044';
    document.getElementById('pageInfo').className = 'info warn';
  }
  updateExtractButton();
});

function updateExtractButton() {
  const btn = document.getElementById('extractBtn');
  btn.disabled = !wvdBase64;
}

// ─── WVD File Load ───
document.getElementById('wvdFile').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  
  // Validate WVD magic
  if (bytes.length < 10 || String.fromCharCode(bytes[0], bytes[1], bytes[2]) !== 'WVD') {
    document.getElementById('wvdStatus').textContent = '\u7121\u52b9\u306aWVD\u30d5\u30a1\u30a4\u30eb\u3067\u3059';
    document.getElementById('wvdStatus').className = 'status error';
    return;
  }
  
  // Convert to base64 (safe for large files)
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  wvdBase64 = btoa(binary);
  
  chrome.storage.local.set({ wvdData: wvdBase64 });
  document.getElementById('wvdStatus').textContent = `\u8aad\u307f\u8fbc\u307f\u6e08\u307f: ${file.name} (${bytes.length} bytes)`;
  document.getElementById('wvdStatus').className = 'status ok';
  updateExtractButton();
});

// ─── Extract Keys ───
document.getElementById('extractBtn').addEventListener('click', async () => {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (!tab || !tab.url) return;
  
  document.getElementById('loading').classList.remove('hidden');
  document.getElementById('error').classList.add('hidden');
  document.getElementById('result').classList.add('hidden');
  document.getElementById('extractBtn').disabled = true;
  
  chrome.runtime.sendMessage(
    { action: 'extractKeys', wvdData: wvdBase64, episodeUrl: tab.url },
    (response) => {
      document.getElementById('loading').classList.add('hidden');
      document.getElementById('extractBtn').disabled = false;
      
      if (chrome.runtime.lastError) {
        showError(chrome.runtime.lastError.message);
      } else if (response && response.success) {
        displayKeys(response.keys);
      } else {
        showError(response?.error || '\u4e0d\u660e\u306a\u30a8\u30e9\u30fc');
      }
    }
  );
});

function displayKeys(keys) {
  const list = document.getElementById('keysList');
  list.innerHTML = '';
  let count = 0;
  for (const key of keys) {
    if (key.type !== 'CONTENT') continue;
    count++;
    const div = document.createElement('div');
    div.className = 'key-item';
    
    const kidSpan = document.createElement('span');
    kidSpan.className = 'kid';
    kidSpan.textContent = key.kid;
    
    const sep = document.createElement('span');
    sep.textContent = ':';
    
    const keySpan = document.createElement('span');
    keySpan.className = 'key-val';
    keySpan.textContent = key.key;
    
    div.appendChild(kidSpan);
    div.appendChild(sep);
    div.appendChild(keySpan);
    list.appendChild(div);
  }
  document.getElementById('keyCount').textContent = `(${count})`;
  if (count > 0) {
    document.getElementById('result').classList.remove('hidden');
  } else {
    showError('CONTENT \u578b\u306e\u9375\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093\u3067\u3057\u305f');
  }
}

function showError(msg) {
  document.getElementById('error').textContent = msg;
  document.getElementById('error').classList.remove('hidden');
}

// ─── Download Keys ───
document.getElementById('downloadBtn').addEventListener('click', () => {
  const items = document.querySelectorAll('.key-item');
  let text = '';
  items.forEach(item => {
    const kid = item.querySelector('.kid').textContent;
    const key = item.querySelector('.key-val').textContent;
    text += `${kid}:${key}\n`;
  });
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'widevine_keys.txt';
  a.click();
  URL.revokeObjectURL(url);
});

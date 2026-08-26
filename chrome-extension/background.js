// background.js - Service Worker for NHK ONE Widevine Key Extractor
importScripts('lib/widevine.js');

const LICENSE_URL = 'https://licence.hsk.st.nhk/widevine/license';
const API_BASE = 'https://api.web.nhk/r8';

// ─── Bearer Token ───
async function getBearerToken() {
  return new Promise((resolve, reject) => {
    chrome.cookies.get({ url: 'https://www.web.nhk', name: 'z_at' }, function (cookie) {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else if (cookie) {
        resolve(cookie.value);
      } else {
        reject(new Error('z_at cookie not found. NHK ONE \u306e\u30a8\u30d4\u30bd\u30fc\u30c9\u30da\u30fc\u30b8\u3092\u958b\u3044\u3066\u518d\u751f\u3092\u958b\u59cb\u3057\u3066\u304f\u3060\u3055\u3044\u3002'));
      }
    });
  });
}

// ─── Fetch Helper ───
async function fetchUrl(url, bearerToken) {
  const parsed = new URL(url);
  const headers = {};
  if (bearerToken && (parsed.hostname === 'api.web.nhk' || parsed.hostname === 'licence.hsk.st.nhk')) {
    headers['Authorization'] = bearerToken.startsWith('Bearer ') ? bearerToken : 'Bearer ' + bearerToken;
  }
  const resp = await fetch(url, { headers });
  if (!resp.ok && resp.status !== 200) {
    throw new Error(`HTTP ${resp.status} from ${url}`);
  }
  return resp;
}

// ─── Find Descriptor URL (recursive) ───
function findDescriptorUrl(obj) {
  if (typeof obj === 'string') {
    if (/videoinfo-[^"'\s]*\.json/.test(obj)) return obj;
    return null;
  }
  if (Array.isArray(obj)) {
    for (const item of obj) {
      const r = findDescriptorUrl(item);
      if (r) return r;
    }
    return null;
  }
  if (obj && typeof obj === 'object') {
    for (const key of Object.keys(obj)) {
      const r = findDescriptorUrl(obj[key]);
      if (r) return r;
    }
  }
  return null;
}

// ─── Resolve Episode → Descriptor URL ───
async function resolveEpisode(episodeId, bearerToken) {
  const apiUrl = `${API_BASE}/t/tvepisode/te/${episodeId}.json`;
  const resp = await fetchUrl(apiUrl, bearerToken);
  const data = await resp.json();
  
  // Try recursive search first
  let descUrl = findDescriptorUrl(data);
  if (descUrl) return descUrl;
  
  // Try direct fields
  const videos = data.video || [];
  for (const v of videos) {
    const d = v.detailedVideoDescriptor || v.detailed_video_descriptor;
    if (d) return d;
    for (const part of (v.hasPart || [])) {
      const pd = part.detailedVideoDescriptor || part.detailed_video_descriptor;
      if (pd) return pd;
    }
  }
  throw new Error('Could not find descriptor URL in API response. Token may be expired.');
}

// ─── Descriptor → Master Playlist URL ───
async function getMasterPlaylist(descriptorUrl) {
  const resp = await fetchUrl(descriptorUrl, null);
  const data = await resp.json();
  const manifests = data.manifests || [];
  let best = null, bestBw = 0;
  for (const m of manifests) {
    const drm = m.drm_type || m.drmType || '';
    const blt = m.bitrate_limit_type || m.bitrateLimitType || '';
    const url = m.url || '';
    if (drm === 'cenc' && url) {
      const bwM = blt.match(/(\d+)/);
      let bw = bwM ? parseInt(bwM[1]) : 0;
      if (blt.startsWith('m')) bw += 10000;
      if (bw > bestBw) { bestBw = bw; best = m; }
    }
  }
  if (!best) {
    for (const m of manifests) {
      if (m.url) { best = m; break; }
    }
  }
  if (!best) throw new Error('No manifest found in descriptor');
  return best.url;
}

// ─── Master → Init Segment URL ───
async function getInitSegmentUrl(masterUrl) {
  // Parse master playlist
  const resp = await fetchUrl(masterUrl, null);
  const text = await resp.text();
  const lines = text.split('\n');
  let videoUrl = null;
  let bestBw = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.startsWith('#EXT-X-STREAM-INF:')) {
      for (let j = i + 1; j < lines.length; j++) {
        const nl = lines[j].trim();
        if (nl && !nl.startsWith('#')) {
          const bwM = line.match(/BANDWIDTH=(\d+)/);
          const bw = bwM ? parseInt(bwM[1]) : 0;
          if (bw > bestBw) {
            bestBw = bw;
            videoUrl = new URL(nl, masterUrl).href;
          }
          break;
        }
      }
    }
  }
  if (!videoUrl) throw new Error('No video playlist found in master playlist');
  
  // Parse video playlist for init segment
  const vResp = await fetchUrl(videoUrl, null);
  const vText = await vResp.text();
  const vLines = vText.split('\n');
  for (const line of vLines) {
    const l = line.trim();
    if (l.startsWith('#EXT-X-MAP:')) {
      const m = l.match(/URI="([^"]+)"/);
      if (m) return new URL(m[1], videoUrl).href;
    }
  }
  throw new Error('No init segment (EXT-X-MAP) found in video playlist');
}

// ─── Init Segment → PSSH ───
async function getPsshFromInitSegment(initUrl) {
  const resp = await fetchUrl(initUrl, null);
  const data = new Uint8Array(await resp.arrayBuffer());
  const psshBoxes = findPssh(data);
  if (!psshBoxes.length) throw new Error('No Widevine PSSH found in init segment');
  const parsed = parsePssh(psshBoxes[0]);
  if (!parsed) throw new Error('Could not parse PSSH box');
  return new Uint8Array(parsed.initData);
}

// ─── Main: Extract Keys ───
async function extractKeys(episodeUrl, wvdBase64) {
  // 1. Get Bearer token from cookie
  const bearerToken = await getBearerToken();
  
  // 2. Extract episode ID from URL
  const m = episodeUrl.match(/\/ep\/([A-Za-z0-9]+)/);
  if (!m) throw new Error('Could not extract episode ID from URL: ' + episodeUrl);
  const episodeId = m[1];
  
  // 3. Resolve: episode → descriptor → master playlist → init segment → PSSH
  const descUrl = await resolveEpisode(episodeId, bearerToken);
  const masterUrl = await getMasterPlaylist(descUrl);
  const initUrl = await getInitSegmentUrl(masterUrl);
  const psshInitData = await getPsshFromInitSegment(initUrl);
  
  // 4. Create CDM from .wvd and generate challenge
  const binary = atob(wvdBase64);
  const wvdBytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) wvdBytes[i] = binary.charCodeAt(i);
  
  const cdm = new WidevineCDM(wvdBytes);
  const challenge = await cdm.generateChallenge(psshInitData);
  
  // 5. Send challenge to license server
  const resp = await fetch(LICENSE_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/octet-stream',
      'Authorization': bearerToken.startsWith('Bearer ') ? bearerToken : 'Bearer ' + bearerToken,
    },
    body: challenge
  });
  
  if (!resp.ok) throw new Error(`License server returned HTTP ${resp.status}`);
  const licenseResp = new Uint8Array(await resp.arrayBuffer());
  
  // 6. Parse license and extract content keys
  const keys = await cdm.parseLicense(licenseResp);
  return keys;
}

// ─── Message Listener ───
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'extractKeys') {
    extractKeys(message.episodeUrl, message.wvdData)
      .then(keys => sendResponse({ success: true, keys }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true; // async response
  }
});

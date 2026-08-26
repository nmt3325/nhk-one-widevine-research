#!/usr/bin/env node
/*
 * 08-web-decrypt-vod.js — NHK ONE Web版 VOD ダウンロード・復号スクリプト（Playwright不要）
 *
 * 06-decrypt-vod.py の Web版。ブラウザ自動操作は使わず、
 * Chrome DevTools (F12) で手動取得した Bearer トークンを使って
 * ダウンロード＋復号を行う純CLIツール。
 *
 * 必要環境:
 *   Node.js (標準ライブラリのみ、npm install 不要)
 *   ffmpeg（PATH に通っていること）
 *   pywidevine（pip install pywidevine）+ .wvd ファイル（復号時）
 *
 * Bearer トークンの手動取得方法（Chrome）【復号時のみ必要】:
 *   1. NHK ONE のエピソードページを開き F12 → Network タブ
 *   2. 再生ボタンを押す
 *   3. api.web.nhk または archive2.hsk.st.nhk へのリクエストを選ぶ
 *   4. Request Headers の「Authorization: Bearer eyJ...」をコピー
 *      （または Application → Cookies → z_at の値でも可）
 *
 * 使用例:
 *   # エピソードURLから自動解決（API・CDNは認証不要）
 *   node 08-web-decrypt-vod.js \
 *     --url 'https://www.web.nhk/tv/pl/series-tep-XXX/ep/YYY' \
 *     --bearer-token 'eyJ...' --wvd device.wvd --output output.mp4
 *
 *   # マスタープレイリストを直接指定（F12で manifest_m*.m3u8 のURLをコピー）
 *   node 08-web-decrypt-vod.js \
 *     --master 'https://archive2.hsk.st.nhk/.../cenc/manifest_m6000.m3u8' \
 *     --bearer-token 'eyJ...' --wvd device.wvd --output output.mp4
 *
 *   # descriptor URLを直接指定（F12で videoinfo-*.json のURLをコピー）
 *   node 08-web-decrypt-vod.js \
 *     --descriptor-url 'https://archive2.hsk.st.nhk/.../videoinfo-XXX.json' \
 *     --bearer-token 'eyJ...' --wvd device.wvd --output output.mp4
 *
 *   # テスト用に先頭3セグメントのみ
 *   node 08-web-decrypt-vod.js --url '...' --bearer-token 'eyJ...' \
 *     --wvd device.wvd --output test.mp4 --max-segments 3
 *
 *   # 復号せず暗号化セグメントのダウンロードのみ（--wvd 省略）
 *   node 08-web-decrypt-vod.js --master '...' --bearer-token 'eyJ...'
 */

'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('child_process');
const { URL: URLP } = require('url');
const https = require('https');
const http = require('http');

// ============================================================
// 定数
// ============================================================

const WIDEVINE_SID_HEX = 'edef8ba979d64acea3c827dcd51d21ed';
const LICENSE_URL = 'https://licence.hsk.st.nhk/widevine/license';
const API_BASE = 'https://api.web.nhk/r8';
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';
const REFERER = 'https://www.web.nhk/';

// ============================================================
// ユーティリティ
// ============================================================

function log(msg) {
  console.error('[' + new Date().toISOString() + '] ' + msg);
}

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('--')) {
      const key = args[i].slice(2).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
      if (i + 1 < args.length && !args[i + 1].startsWith('--')) {
        opts[key] = args[++i];
      } else {
        opts[key] = true;
      }
    }
  }
  return opts;
}

// URL取得（リダイレクト対応）
function fetchUrl(url, options) {
  options = options || {};
  return new Promise(function (resolve, reject) {
    var parsed = new URLP(url);
    var lib = parsed.protocol === 'https:' ? https : http;
    var headers = {
      'User-Agent': options.userAgent || UA,
      'Referer': REFERER,
      'Origin': 'https://www.web.nhk',
    };
    // Authorization は api.web.nhk / licence.hsk.st.nhk にのみ付ける。
    // CDN (archive2.hsk.st.nhk) は認証なしで配信され、無効なトークンを送ると 400 になる。
    if (options.bearerToken && (parsed.hostname === 'api.web.nhk' || parsed.hostname === 'licence.hsk.st.nhk')) {
      headers['Authorization'] = options.bearerToken.startsWith('Bearer ') ? options.bearerToken : 'Bearer ' + options.bearerToken;
    }
    var req = lib.get(url, { headers: headers, timeout: 60000 }, function (res) {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        var next = new URLP(res.headers.location, url).href;
        return resolve(fetchUrl(next, options));
      }
      var chunks = [];
      res.on('data', function (c) { chunks.push(c); });
      res.on('end', function () { resolve({ data: Buffer.concat(chunks), status: res.statusCode, headers: res.headers }); });
    });
    req.on('error', reject);
    req.on('timeout', function () { req.destroy(new Error('timeout')); });
  });
}

// HLS メディアプレイリスト解析
function parseMediaPlaylist(text, baseUrl) {
  var init = null;
  var segments = [];
  var lines = text.split('\n');
  for (var i = 0; i < lines.length; i++) {
    var l = lines[i].trim();
    if (!l) continue;
    if (l.startsWith('#EXT-X-MAP:')) {
      var m = l.match(/URI="([^"]+)"/);
      if (m) init = new URLP(m[1], baseUrl).href;
    } else if (l.startsWith('#')) {
      continue;
    } else {
      segments.push(new URLP(l, baseUrl).href);
    }
  }
  return { init: init, segments: segments };
}

// HLS マスタープレイリスト解析
function parseMasterPlaylist(text, baseUrl) {
  var variants = [];
  var audios = [];
  var subtitles = [];
  var lines = text.split('\n');
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    if (line.startsWith('#EXT-X-MEDIA:')) {
      var typeM = line.match(/TYPE=(\w+)/);
      var uriM = line.match(/URI="([^"]+)"/);
      var nameM = line.match(/NAME="([^"]+)"/);
      if (typeM && uriM) {
        var uri = new URLP(uriM[1], baseUrl).href;
        var name = nameM ? nameM[1] : '';
        if (typeM[1] === 'AUDIO') audios.push({ uri: uri, name: name });
        else if (typeM[1] === 'SUBTITLES') subtitles.push({ uri: uri, name: name });
      }
    } else if (line.startsWith('#EXT-X-STREAM-INF:')) {
      for (var j = i + 1; j < lines.length; j++) {
        var nl = lines[j].trim();
        if (nl && !nl.startsWith('#')) {
          var sUri = new URLP(nl, baseUrl).href;
          var resM = line.match(/RESOLUTION=(\d+x\d+)/);
          var bwM = line.match(/BANDWIDTH=(\d+)/);
          variants.push({ uri: sUri, resolution: resM ? resM[1] : null, bandwidth: bwM ? parseInt(bwM[1]) : 0 });
          i = j;
          break;
        }
      }
    }
  }
  return { variants: variants, audios: audios, subtitles: subtitles };
}

// fMP4 initセグメントからWidevine PSSHボックスを抽出
function findPsshBoxes(data) {
  var sysId = Buffer.from(WIDEVINE_SID_HEX, 'hex');
  var results = [];
  var start = 0;
  while (true) {
    var idx = data.indexOf(Buffer.from('pssh'), start);
    if (idx === -1) break;
    if (idx >= 4) {
      var size = data.readUInt32BE(idx - 4);
      if (size >= 8 && size <= 512 && idx - 4 + size <= data.length) {
        var box = data.subarray(idx - 4, idx - 4 + size);
        if (box.length >= 32 && box.subarray(12, 28).equals(sysId)) {
          results.push(Buffer.from(box));
        }
      }
    }
    start = idx + 4;
  }
  return results;
}

// セグメント名を取得
function segName(url) {
  var p = new URLP(url).pathname;
  return p.split('/').pop() || 'segment';
}

// JSON を再帰的に探索して videoinfo-*.json のURLを探す
function findDescriptorUrl(obj) {
  if (typeof obj === 'string') {
    if (/videoinfo-[^"'\s]*\.json/.test(obj)) return obj;
    return null;
  }
  if (Array.isArray(obj)) {
    for (var i = 0; i < obj.length; i++) {
      var r = findDescriptorUrl(obj[i]);
      if (r) return r;
    }
    return null;
  }
  if (obj && typeof obj === 'object') {
    var keys = Object.keys(obj);
    for (var k = 0; k < keys.length; k++) {
      var r2 = findDescriptorUrl(obj[keys[k]]);
      if (r2) return r2;
    }
  }
  return null;
}

// エピソードIDから API 経由で descriptor URL を解決
async function resolveEpisode(episodeId, bearerToken) {
  var apiUrl = API_BASE + '/t/tvepisode/te/' + episodeId + '.json';
  log('  API: ' + apiUrl);
  var resp = await fetchUrl(apiUrl, { bearerToken: bearerToken });
  if (resp.status !== 200) {
    log('  API returned status ' + resp.status);
    return null;
  }
  var data = JSON.parse(resp.data.toString('utf-8'));
  return findDescriptorUrl(data);
}

// プレイリストのセグメントをダウンロード
async function downloadPlaylist(playlistUrl, outDir, maxSegments, bearerToken) {
  var resp = await fetchUrl(playlistUrl, { bearerToken: bearerToken });
  var text = resp.data.toString('utf-8');
  var parsed = parseMediaPlaylist(text, playlistUrl);
  var init = parsed.init;
  var segments = parsed.segments;
  var segs = maxSegments ? segments.slice(0, maxSegments) : segments;
  var files = [];
  fs.mkdirSync(outDir, { recursive: true });
  var initPath = null;
  if (init) {
    initPath = path.join(outDir, segName(init));
    var initData = (await fetchUrl(init, { bearerToken: bearerToken })).data;
    fs.writeFileSync(initPath, initData);
    files.push(initPath);
    log('  init: ' + segName(init) + ' (' + initData.length + ' bytes)');
  }
  for (var i = 0; i < segs.length; i++) {
    var segPath = path.join(outDir, String(i).padStart(6, '0') + '-' + segName(segs[i]));
    var segData = (await fetchUrl(segs[i], { bearerToken: bearerToken })).data;
    fs.writeFileSync(segPath, segData);
    files.push(segPath);
    if ((i + 1) % 20 === 0 || i + 1 === segs.length) log('  segments: ' + (i + 1) + '/' + segs.length);
  }
  return { files: files, initPath: initPath };
}

// WebVTT字幕をダウンロード＋結合
async function downloadSubtitles(playlistUrl, outDir, maxSegments, concatPath, bearerToken) {
  var resp = await fetchUrl(playlistUrl, { bearerToken: bearerToken });
  var text = resp.data.toString('utf-8');
  var segments = parseMediaPlaylist(text, playlistUrl).segments;
  var segs = maxSegments ? segments.slice(0, maxSegments) : segments;
  fs.mkdirSync(outDir, { recursive: true });
  var texts = [];
  for (var i = 0; i < segs.length; i++) {
    var data = (await fetchUrl(segs[i], { bearerToken: bearerToken })).data;
    texts.push(data.toString('utf-8'));
    if ((i + 1) % 50 === 0 || i + 1 === segs.length) log('  subtitle segments: ' + (i + 1) + '/' + segs.length);
  }
  if (concatPath) {
    var out = ['WEBVTT', ''];
    for (var t = 0; t < texts.length; t++) {
      var lines = texts[t].replace(/\r\n/g, '\n').split('\n');
      var s = 0;
      if (lines[0] && lines[0].startsWith('WEBVTT')) s = 1;
      var body = lines.slice(s);
      while (body.length && body[0] === '') body.shift();
      out = out.concat(body);
      if (out[out.length - 1] !== '') out.push('');
    }
    fs.mkdirSync(path.dirname(concatPath) || '.', { recursive: true });
    fs.writeFileSync(concatPath, out.join('\n').trimEnd() + '\n', 'utf-8');
    log('  merged subtitles: ' + concatPath + ' (' + fs.statSync(concatPath).size + ' bytes)');
  }
}

// pywidevineでWidevine鍵を取得
function getDecryptionKeys(initPath, wvdPath, licenseUrl, bearerToken) {
  var init = fs.readFileSync(initPath);
  var psshBoxes = findPsshBoxes(init);
  if (!psshBoxes.length) { log('Error: No Widevine PSSH found in init segment'); return null; }
  var psshB64 = psshBoxes[0].toString('base64');
  log('  PSSH: ' + psshBoxes[0].length + ' bytes, ' + psshBoxes.length + ' box(es)');

  var pyScript = [
    'import sys, json, urllib.request',
    'from pywidevine.cdm import Cdm',
    'from pywidevine.device import Device',
    'from pywidevine.pssh import PSSH',
    '',
    'pssh_b64, wvd_path, license_url, bearer = sys.argv[1:5]',
    'device = Device.load(wvd_path)',
    'cdm = Cdm.from_device(device)',
    'pssh = PSSH(pssh_b64)',
    'session = cdm.open()',
    'challenge = cdm.get_license_challenge(session, pssh)',
    'headers = {"Content-Type": "application/octet-stream", "Origin": "https://www.web.nhk", "Referer": "https://www.web.nhk/"}',
    'if bearer:',
    '    if not bearer.startswith("Bearer "): bearer = "Bearer " + bearer',
    '    headers["Authorization"] = bearer',
    'req = urllib.request.Request(license_url, data=challenge, headers=headers, method="POST")',
    'with urllib.request.urlopen(req, timeout=60) as r:',
    '    license_resp = r.read()',
    'cdm.parse_license(session, license_resp)',
    'keys = []',
    'for key in cdm.get_keys(session):',
    '    kid = key.kid.hex if hasattr(key.kid, "hex") else str(key.kid)',
    '    kv = key.key.hex() if hasattr(key.key, "hex") else str(key.key)',
    '    keys.append({"kid": kid, "key": kv, "type": str(key.type)})',
    'cdm.close(session)',
    'print(json.dumps(keys))'
  ].join('\n');

  var pyPath = path.join(os.tmpdir(), 'nhk_get_keys.py');
  fs.writeFileSync(pyPath, pyScript);
  var result = spawnSync('python3', [pyPath, psshB64, wvdPath, licenseUrl, bearerToken || ''], { encoding: 'utf-8', timeout: 60000 });
  if (result.status !== 0) { log('  pywidevine error: ' + (result.stderr || result.stdout || '').slice(0, 500)); return null; }
  var keys = JSON.parse(result.stdout.trim());
  var contentKeys = keys.filter(function (k) { return k.type === 'CONTENT'; });
  log('  Content keys: ' + contentKeys.length);
  return contentKeys;
}

// FFmpegで復号＋マージ
function decryptAndMerge(videoFiles, audioFiles, keys, outputPath, subtitlePath) {
  var contentKey = keys[0].key;
  var vDir = path.dirname(videoFiles[0]);
  var vConcat = path.join(vDir, 'concat.mp4');
  var vOut = fs.createWriteStream(vConcat);
  for (var i = 0; i < videoFiles.length; i++) vOut.write(fs.readFileSync(videoFiles[i]));
  vOut.close();
  var aDir = path.dirname(audioFiles[0]);
  var aConcat = path.join(aDir, 'concat.mp4');
  var aOut = fs.createWriteStream(aConcat);
  for (var j = 0; j < audioFiles.length; j++) aOut.write(fs.readFileSync(audioFiles[j]));
  aOut.close();
  var cmd = ['ffmpeg', '-y', '-decryption_key', contentKey, '-i', vConcat, '-decryption_key', contentKey, '-i', aConcat];
  if (subtitlePath && fs.existsSync(subtitlePath)) cmd.push('-i', subtitlePath);
  cmd.push('-map', '0:v:0', '-map', '1:a:0');
  if (subtitlePath && fs.existsSync(subtitlePath)) cmd.push('-map', '2:s:0', '-c:s', 'mov_text');
  cmd.push('-c:v', 'copy', '-c:a', 'copy', outputPath);
  log('  Running FFmpeg...');
  var result = spawnSync(cmd[0], cmd.slice(1), { encoding: 'utf-8', timeout: 300000 });
  if (result.status !== 0) { log('  FFmpeg error: ' + (result.stderr || '').slice(-800)); return false; }
  log('  Output: ' + outputPath + ' (' + (fs.existsSync(outputPath) ? fs.statSync(outputPath).size : 0) + ' bytes)');
  return true;
}

// ============================================================
// メイン処理
// ============================================================

async function main() {
  var opts = parseArgs();

  if (!opts.url && !opts.episodeId && !opts.descriptorUrl && !opts.master && !opts.videoPlaylist) {
    console.error('Error: --url, --episode-id, --descriptor-url, --master, or --video-playlist is required');
    process.exit(1);
  }
  var needsToken = !!(opts.wvd || opts.url || opts.episodeId);
  if (needsToken && !opts.bearerToken) {
    console.error('Error: --bearer-token is required (F12 → Network → Authorization ヘッダーから取得)');
    console.error('Note: --master / --descriptor-url 直接指定かつ復号なし（--wvd 省略）ならトークン不要です');
    process.exit(1);
  }

  var bearerToken = opts.bearerToken;
  var workDir = opts.workDir || './dl-work';
  fs.mkdirSync(workDir, { recursive: true });

  var descriptorUrl = opts.descriptorUrl;
  var masterUrl = opts.master;

  // --- エピソードURL / ID から descriptor URL を解決 ---
  if (!masterUrl && !descriptorUrl) {
    var episodeId = opts.episodeId;
    if (!episodeId && opts.url) {
      var m = opts.url.match(/\/ep\/([A-Za-z0-9]+)/);
      if (m) episodeId = m[1];
    }
    if (!episodeId) { log('Error: Could not extract episode ID from URL'); process.exit(1); }
    log('Resolving episode: ' + episodeId);
    descriptorUrl = await resolveEpisode(episodeId, bearerToken);
    if (!descriptorUrl) {
      log('Error: Could not resolve descriptor URL from API');
      log('Hint: F12で videoinfo-*.json のURLを確認して --descriptor-url で直接指定してください');
      process.exit(1);
    }
    log('  Descriptor URL: ' + descriptorUrl);
  }

  // --- descriptor URLからマスタープレイリストを解決 ---
  if (!masterUrl && descriptorUrl) {
    log('Parsing video descriptor...');
    var dResp = await fetchUrl(descriptorUrl, { bearerToken: bearerToken });
    var dData = JSON.parse(dResp.data.toString('utf-8'));
    var manifests = dData.manifests || [];
    log('  Manifests: ' + manifests.length);
    var best = null, bestBw = 0;
    for (var mi = 0; mi < manifests.length; mi++) {
      var mf = manifests[mi];
      var drm = mf.drm_type || mf.drmType || '';
      var blt = mf.bitrate_limit_type || mf.bitrateLimitType || '';
      var mUrl = mf.url || '';
      if (drm === 'cenc' && mUrl) {
        var bwM = blt.match(/(\d+)/);
        var bw = bwM ? parseInt(bwM[1]) : 0;
        if (blt.startsWith('m')) bw += 10000;
        if (bw > bestBw) { bestBw = bw; best = mf; }
      }
    }
    if (!best) {
      for (var bi = 0; bi < manifests.length; bi++) {
        if (manifests[bi].url) { best = manifests[bi]; break; }
      }
    }
    if (best) {
      masterUrl = best.url;
      log('  Selected: ' + (best.drm_type || best.drmType) + ' ' + (best.bitrate_limit_type || best.bitrateLimitType));
      log('  Master: ' + masterUrl);
    }
  }

  // --- マスタープレイリストを解析 ---
  var videoUrl = opts.videoPlaylist || null;
  var audioUrl = opts.audioPlaylist || null;
  var subtitleUrl = opts.subtitlePlaylist || null;

  if (!videoUrl) {
    if (!masterUrl) { log('Error: Could not resolve master playlist URL'); process.exit(1); }
    log('Parsing master playlist...');
    var mResp = await fetchUrl(masterUrl, { bearerToken: bearerToken });
    if (mResp.status !== 200) { log('Error: master playlist returned status ' + mResp.status); process.exit(1); }
    var mText = mResp.data.toString('utf-8');
    var mpl = parseMasterPlaylist(mText, masterUrl);
    log('  Variants: ' + mpl.variants.length + ', Audio: ' + mpl.audios.length + ', Subtitles: ' + mpl.subtitles.length);
    mpl.variants.sort(function (a, b) { return b.bandwidth - a.bandwidth; });
    videoUrl = mpl.variants[0] ? mpl.variants[0].uri : null;
    if (!audioUrl) audioUrl = mpl.audios[0] ? mpl.audios[0].uri : null;
    if (!subtitleUrl) subtitleUrl = mpl.subtitles[0] ? mpl.subtitles[0].uri : null;
    if (mpl.variants[0]) log('  Selected video: ' + (mpl.variants[0].resolution || '?') + ' (' + mpl.variants[0].bandwidth + ' bps)');
    if (mpl.audios[0]) log('  Selected audio: ' + mpl.audios[0].name);
    if (mpl.subtitles[0]) log('  Detected subtitles: ' + mpl.subtitles[0].name);
  }
  if (!videoUrl) { log('Error: No video playlist URL'); process.exit(1); }

  var maxSegments = opts.maxSegments ? parseInt(opts.maxSegments) : null;

  // --- 動画セグメントをダウンロード ---
  log('[1/4] Downloading video...');
  var vResult = await downloadPlaylist(videoUrl, path.join(workDir, 'video'), maxSegments, bearerToken);
  var videoFiles = vResult.files;
  var videoInit = vResult.initPath;

  // --- 音声セグメントをダウンロード ---
  var audioFiles = [];
  if (audioUrl) {
    log('[2/4] Downloading audio...');
    audioFiles = (await downloadPlaylist(audioUrl, path.join(workDir, 'audio'), maxSegments, bearerToken)).files;
  }

  // --- 字幕をダウンロード ---
  var subtitlePath = null;
  if (subtitleUrl) {
    subtitlePath = opts.concatSubtitles || path.join(workDir, 'subtitles.vtt');
    log('  Downloading subtitles...');
    await downloadSubtitles(subtitleUrl, path.join(workDir, 'subtitles'), maxSegments, subtitlePath, bearerToken);
  }

  // --- 復号＋マージ ---
  if (!opts.wvd) {
    log('\nNo .wvd file provided. Encrypted segments saved in: ' + workDir);
    log('To decrypt, re-run with --wvd device.wvd');
    return;
  }
  if (!audioFiles.length) { log('Error: Audio is required for a complete MP4'); process.exit(1); }

  log('[3/4] Getting decryption keys...');
  if (!videoInit) { log('Error: No init segment in video playlist'); process.exit(1); }
  var keys = getDecryptionKeys(videoInit, opts.wvd, opts.licenseUrl || LICENSE_URL, bearerToken);
  if (!keys) { log('Error: Could not get decryption keys'); process.exit(1); }

  log('[4/4] Decrypting and merging...');
  var outputPath = opts.output || 'output.mp4';
  var success = decryptAndMerge(videoFiles, audioFiles, keys, outputPath, subtitlePath);
  if (success) log('\nDone!'); else { log('\nFailed!'); process.exit(1); }
}

main().catch(function (e) { log('Fatal error: ' + e.message); process.exit(1); });

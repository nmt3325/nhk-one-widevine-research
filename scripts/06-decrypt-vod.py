#!/usr/bin/env python3
"""Download and decrypt NHK ONE VOD content.

Accepts a program page URL, episode ID, descriptor URL, or direct playlist URLs.
Downloads encrypted HLS segments, extracts PSSH from the init segment,
obtains Widevine license keys via pywidevine, and decrypts using FFmpeg.

Requirements:
    pip install pywidevine
    ffmpeg installed and on PATH
    A .wvd Widevine L3 device file
    A Bearer token for the NHK license server

Usage:
    # From a program page URL (requires --bearer-token for API auth)
    python3 06-decrypt-vod.py \\
        --url 'https://www.web.nhk/tv/pl/series-tep-4N9Y61G7M7/ep/MZ2R1M49J4' \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output output.mp4

    # From a descriptor URL directly
    python3 06-decrypt-vod.py \\
        --descriptor-url 'https://archive2.hsk.st.nhk/npd4/.../videoinfo-XXX.json' \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output output.mp4

    # From a master playlist URL directly
    python3 06-decrypt-vod.py \\
        --master '<master-playlist-url>' \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output output.mp4

    # Specify video and audio playlists directly
    python3 06-decrypt-vod.py \\
        --video-playlist '<video-playlist-url>' \\
        --audio-playlist '<audio-playlist-url>' \\
        --wvd device.wvd \\
        --bearer-token '<token>' \\
        --output output.mp4

    # With subtitles
    python3 06-decrypt-vod.py \\
        --master '<master-playlist-url>' \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output output.mp4 \\
        --concat-subtitles subtitles.vtt

    # Test with limited segments
    python3 06-decrypt-vod.py \\
        --master '<master-playlist-url>' \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output test.mp4 \\
        --max-segments 3
"""
import argparse
import base64
import json
import os
import re
import struct
import subprocess
import sys
import urllib.parse
import urllib.request

WIDEVINE_SYSTEM_ID = bytes.fromhex("edef8ba979d64acea3c827dcd51d21ed")
DEFAULT_LICENSE_URL = "https://licence.hsk.st.nhk/widevine/license"
DEFAULT_API_BASE = "https://api.web.nhk/r8"
UA = "NHKONE-Android/1.1.9"


def fetch(url, dest=None, bearer_token=None):
    headers = {"User-Agent": UA}
    if bearer_token and any(h in url for h in ("api.web.nhk", "licence.hsk.st.nhk")):
        headers["Authorization"] = f"Bearer {bearer_token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if dest:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
    return data


def parse_media_playlist(text, base_url):
    init = None
    segments = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init = urllib.parse.urljoin(base_url, m.group(1))
        elif line.startswith("#"):
            continue
        else:
            segments.append(urllib.parse.urljoin(base_url, line))
    return init, segments


def parse_master_playlist(text, base_url):
    """Return (video_variants, audio_playlists, subtitle_playlists)."""
    video_variants = []
    audio_playlists = []
    subtitle_playlists = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-MEDIA:"):
            mtype = re.search(r"TYPE=(\w+)", line)
            uri_m = re.search(r'URI="([^"]+)"', line)
            name_m = re.search(r'NAME="([^"]+)"', line)
            if mtype and uri_m:
                uri = urllib.parse.urljoin(base_url, uri_m.group(1))
                name = name_m.group(1) if name_m else ""
                if mtype.group(1) == "AUDIO":
                    audio_playlists.append({"uri": uri, "name": name})
                elif mtype.group(1) == "SUBTITLES":
                    subtitle_playlists.append({"uri": uri, "name": name})
        elif line.startswith("#EXT-X-STREAM-INF:"):
            for j in range(i + 1, len(lines)):
                nl = lines[j].strip()
                if nl and not nl.startswith("#"):
                    uri = urllib.parse.urljoin(base_url, nl)
                    res = re.search(r"RESOLUTION=(\d+x\d+)", line)
                    bw = re.search(r"BANDWIDTH=(\d+)", line)
                    video_variants.append({
                        "uri": uri,
                        "resolution": res.group(1) if res else None,
                        "bandwidth": int(bw.group(1)) if bw else 0,
                    })
                    i = j
                    break
        i += 1
    return video_variants, audio_playlists, subtitle_playlists


def seg_name(url):
    path = urllib.parse.urlparse(url).path
    return path.rsplit("/", 1)[-1] or "segment"


def find_pssh_boxes(data):
    """Find Widevine PSSH boxes in fMP4 init segment binary data."""
    results = []
    start = 0
    while True:
        idx = data.find(b"pssh", start)
        if idx == -1:
            break
        if idx >= 4:
            size = struct.unpack(">I", data[idx - 4 : idx])[0]
            if 8 <= size <= 512 and idx - 4 + size <= len(data):
                box = data[idx - 4 : idx - 4 + size]
                if len(box) >= 32 and box[12:28] == WIDEVINE_SYSTEM_ID:
                    results.append(box)
        start = idx + 4
    return results


def parse_tenc_kid(init_path):
    """Extract default_KID from the tenc box of an fMP4 init segment.

    Returns the KID as a lowercase hex string (no dashes), or None.
    """
    with open(init_path, "rb") as f:
        data = f.read()
    idx = data.find(b"tenc")
    if idx < 4:
        return None
    size = struct.unpack(">I", data[idx - 4 : idx])[0]
    if size < 32 or idx - 4 + size > len(data):
        return None
    body = data[idx + 4 : idx - 4 + size]  # skip size+type
    version = body[0]
    if version == 0:
        return body[8:24].hex()
    return body[10:26].hex()


def select_key_for_kid(keys, kid_hex):
    """Select the content key whose KID matches kid_hex (normalized)."""
    norm = kid_hex.replace("-", "").lower()
    for k in keys:
        k_kid = str(k["kid"]).replace("-", "").lower()
        if k_kid == norm:
            return k["key"]
    return None


def extract_episode_id(url):
    """Extract episode ID from an NHK ONE program page URL."""
    m = re.search(r"/ep/([A-Z0-9]+)", url)
    return m.group(1) if m else None


def resolve_episode(episode_id, bearer_token=None):
    """Fetch episode info from the NHK API and return (metadata, descriptor_url).

    Returns (episode_title, descriptor_url) or (None, None) on failure.
    """
    api_url = f"{DEFAULT_API_BASE}/t/tvepisode/te/{episode_id}.json"
    print(f"  Episode API: {api_url}")
    try:
        data = json.loads(fetch(api_url, bearer_token=bearer_token))
    except Exception as e:
        print(f"  Error fetching episode: {e}")
        return None, None

    title = data.get("name", "")
    print(f"  Title: {title}")

    videos = data.get("video", [])
    if not videos:
        print("  No video entries found in episode data")
        return title, None

    video = videos[0]
    ig = video.get("identifierGroup", {})
    print(f"  Stream type: {ig.get('streamType', '?')}")
    print(f"  Content status: {video.get('detailedContentStatus', {}).get('contentStatus', '?')}")
    print(f"  Duration: {video.get('duration', '?')}")

    # Check for detailedVideoDescriptor (camelCase and snake_case)
    descriptor_url = video.get("detailedVideoDescriptor") or video.get("detailed_video_descriptor")
    if descriptor_url:
        print(f"  Descriptor URL: {descriptor_url[:100]}...")
        return title, descriptor_url

    # Check hasPart for divided content
    for part in video.get("hasPart", []):
        if isinstance(part, dict):
            descriptor_url = part.get("detailedVideoDescriptor") or part.get("detailed_video_descriptor")
            if descriptor_url:
                print(f"  Descriptor URL (from part): {descriptor_url[:100]}...")
                return title, descriptor_url

    # Check detailedContent
    for item in video.get("detailedContent", []):
        if isinstance(item, dict):
            content_url = item.get("contentUrl") or item.get("content_url", "")
            if "videoinfo" in content_url or "descriptor" in content_url.lower():
                print(f"  Descriptor URL (from detailedContent): {content_url[:100]}...")
                return title, content_url

    print("  detailedVideoDescriptor not found in API response")
    print("  This field may require authentication. Try providing --descriptor-url directly.")
    return title, None


def parse_descriptor(descriptor_url, bearer_token=None):
    """Fetch a video descriptor JSON and return (master_url, info_dict)."""
    print(f"  Fetching descriptor...")
    try:
        data = json.loads(fetch(descriptor_url))
    except Exception as e:
        print(f"  Error fetching descriptor: {e}")
        return None, None

    manifests = data.get("manifests", [])
    print(f"  Manifests: {len(manifests)}")

    need_l1 = data.get("need_L1_hd", data.get("need_l1_hd", False))
    multi = data.get("allow_multispeed", data.get("allowMultispeed", False))
    print(f"  need_L1_hd: {need_l1}, allow_multispeed: {multi}")

    # Find the best CENC manifest (highest bitrate, 'm' prefix = high quality)
    best = None
    best_bw = 0
    for m in manifests:
        drm = m.get("drm_type", m.get("drmType", ""))
        blt = m.get("bitrate_limit_type", m.get("bitrateLimitType", ""))
        url = m.get("url", "")
        if drm == "cenc" and url:
            # Extract bitrate value from type like "m1500" -> 1500
            bw_m = re.search(r"(\d+)", blt)
            bw = int(bw_m.group(1)) if bw_m else 0
            # Prefer 'm' (multi-bitrate) over 's' (single)
            if blt.startswith("m"):
                bw += 10000
            if bw > best_bw:
                best_bw = bw
                best = m

    if not best:
        # Fall back to any manifest with a URL
        for m in manifests:
            if m.get("url"):
                best = m
                break

    if not best:
        print("  No usable manifest found in descriptor")
        return None, None

    master_url = best.get("url", "")
    drm_type = best.get("drm_type", best.get("drmType", "?"))
    blt = best.get("bitrate_limit_type", best.get("bitrateLimitType", "?"))
    print(f"  Selected: {drm_type} {blt} -> {master_url[:100]}...")

    info = {
        "need_l1_hd": need_l1,
        "allow_multispeed": multi,
        "manifest_count": len(manifests),
        "selected_drm": drm_type,
        "selected_bitrate_type": blt,
    }
    return master_url, info


def download_playlist(playlist_url, out_dir, max_segments=None, skip_segments=0):
    """Download init + media segments. Returns (file_paths, init_path)."""
    text = fetch(playlist_url).decode("utf-8")
    init_url, segments = parse_media_playlist(text, playlist_url)
    if skip_segments:
        segments = segments[skip_segments:]
    if max_segments is not None:
        segments = segments[:max_segments]
    written = []
    init_path = None
    if init_url:
        init_path = os.path.join(out_dir, seg_name(init_url))
        fetch(init_url, init_path)
        written.append(init_path)
        print(f"  init: {seg_name(init_url)} ({os.path.getsize(init_path)} bytes)")
    for i, u in enumerate(segments):
        dest = os.path.join(out_dir, f"{i:06d}-{seg_name(u)}")
        fetch(u, dest)
        written.append(dest)
        if (i + 1) % 20 == 0 or i + 1 == len(segments):
            print(f"  segments: {i + 1}/{len(segments)}")
    return written, init_path


def get_decryption_keys(init_path, wvd_path, license_url, bearer_token):
    """Extract PSSH from init segment and obtain Widevine decryption keys."""
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device
    from pywidevine.pssh import PSSH

    with open(init_path, "rb") as f:
        init_data = f.read()

    pssh_boxes = find_pssh_boxes(init_data)
    if not pssh_boxes:
        print("Error: No Widevine PSSH found in init segment")
        return None

    pssh_b64 = base64.b64encode(pssh_boxes[0]).decode()
    print(f"  PSSH: {len(pssh_boxes[0])} bytes, {len(pssh_boxes)} box(es)")

    device = Device.load(wvd_path)
    cdm = Cdm.from_device(device)

    pssh = PSSH(pssh_b64)
    session_id = cdm.open()
    challenge = cdm.get_license_challenge(session_id, pssh)
    print(f"  Challenge: {len(challenge)} bytes")

    headers = {"User-Agent": UA, "Content-Type": "application/octet-stream"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    req = urllib.request.Request(license_url, data=challenge, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        license_response = r.read()
    print(f"  License response: {len(license_response)} bytes")

    cdm.parse_license(session_id, license_response)

    keys = []
    for key in cdm.get_keys(session_id):
        kid = key.kid.hex if hasattr(key.kid, "hex") else str(key.kid)
        key_val = key.key.hex() if hasattr(key.key, "hex") else str(key.key)
        keys.append({"kid": kid, "key": key_val, "type": str(key.type)})
    cdm.close(session_id)

    content_keys = [k for k in keys if k["type"] == "CONTENT"]
    if not content_keys:
        print("Error: No CONTENT keys in license response")
        return None
    print(f"  Content keys: {len(content_keys)}")
    return content_keys


def decrypt_and_merge(video_files, audio_files, video_key, audio_key, output_path, subtitle_path=None):
    """Decrypt encrypted fMP4 and merge to MP4 using FFmpeg."""

    v_dir = os.path.dirname(video_files[0])
    v_concat = os.path.join(v_dir, "concat.mp4")
    with open(v_concat, "wb") as out:
        for f in video_files:
            with open(f, "rb") as inp:
                out.write(inp.read())

    a_dir = os.path.dirname(audio_files[0])
    a_concat = os.path.join(a_dir, "concat.mp4")
    with open(a_concat, "wb") as out:
        for f in audio_files:
            with open(f, "rb") as inp:
                out.write(inp.read())

    cmd = [
        "ffmpeg", "-y",
        "-decryption_key", video_key,
        "-i", v_concat,
        "-decryption_key", audio_key,
        "-i", a_concat,
    ]
    if subtitle_path and os.path.exists(subtitle_path):
        cmd.extend(["-i", subtitle_path])
    cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
    if subtitle_path and os.path.exists(subtitle_path):
        cmd.extend(["-map", "2:s:0", "-c:s", "mov_text"])
    cmd.extend(["-c:v", "copy", "-c:a", "copy"])
    cmd.append(output_path)

    print("  Running FFmpeg...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FFmpeg stderr (tail): {result.stderr[-800:]}")
        return False
    print(f"  Output: {output_path} ({os.path.getsize(output_path)} bytes)")
    return True


VTT_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})\.(\d{3})")


def parse_vtt_time(t):
    """Parse a WebVTT timestamp to milliseconds."""
    m = VTT_TIME_RE.match(t.strip())
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    millis = int(m.group(4))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def format_vtt_time(ms):
    """Format milliseconds as a WebVTT timestamp (HH:MM:SS.mmm)."""
    if ms < 0:
        ms = 0
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


ARIB_TOKEN_RE = re.compile(r"\[[^\]\[]*\]")


def sanitize_subtitle_text(text, keep_tokens=False):
    """Convert NHK ONE's ARIB-derived caption markup to plain text.

    VTT cues carry ARIB STD-B24 control sequences as bracketed tokens
    (e.g. [CS][SWF_7][SDF_840_480][COL_4]). [APS_x_y] marks a new writing
    position, i.e. a line break. Players such as VLC render these tokens as
    literal text, so we convert or strip them by default.
    """
    if keep_tokens:
        return text
    text = re.sub(r"\[APS[^\]]*\]", "\n", text)
    text = ARIB_TOKEN_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def parse_vtt_cues(segment_texts):
    """Parse VTT segments into cue dicts with rebased (zero-start) timing.

    Returns a list of {"start": ms, "end": ms, "settings": str,
    "lines": [raw text lines]} with the earliest cue shifted to 00:00.
    """
    cue_blocks = []
    for text in segment_texts:
        lines = text.replace("\r\n", "\n").split("\n")
        i = 0
        if lines and lines[0].startswith("WEBVTT"):
            i = 1
        block = []
        for line in lines[i:] + [""]:
            stripped = line.strip()
            if stripped == "":
                if block:
                    cue_blocks.append(block)
                    block = []
                continue
            if stripped.startswith(("X-TIMESTAMP-MAP", "NOTE", "STYLE", "REGION")):
                continue
            block.append(line)
        if block:
            cue_blocks.append(block)

    cues = []
    earliest = None
    for block in cue_blocks:
        cue = None
        for line in block:
            if "-->" in line and cue is None:
                parts = line.split("-->", 1)
                start_ms = parse_vtt_time(parts[0])
                end_and_settings = parts[1].strip()
                em = VTT_TIME_RE.match(end_and_settings)
                if start_ms is not None and em:
                    end_ms = parse_vtt_time(end_and_settings)
                    cue = {
                        "start": start_ms,
                        "end": end_ms,
                        "settings": end_and_settings[em.end():],
                        "lines": [],
                    }
                    if earliest is None or start_ms < earliest:
                        earliest = start_ms
            elif cue is not None:
                cue["lines"].append(line)
            else:
                cue = {"start": None, "end": None, "settings": "", "lines": [line]}
        if cue is not None:
            cues.append(cue)
    if earliest is None:
        earliest = 0
    for cue in cues:
        if cue["start"] is not None:
            cue["start"] -= earliest
            cue["end"] -= earliest
    return cues


def merge_vtt(segment_texts, keep_tokens=False):
    """Merge VTT segments into one plain-text WebVTT document.

    Cue times are rebased to zero and ARIB control tokens are sanitized
    unless keep_tokens is set.
    """
    cues = parse_vtt_cues(segment_texts)
    out = ["WEBVTT", ""]
    for cue in cues:
        if cue["start"] is None:
            continue
        lines = [sanitize_subtitle_text(l, keep_tokens=keep_tokens) for l in cue["lines"]]
        if not any(l.strip() for l in lines):
            continue
        timing = (
            format_vtt_time(cue["start"])
            + " --> "
            + format_vtt_time(cue["end"])
            + cue["settings"]
        )
        out.append(timing)
        out.extend(lines)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --- ARIB caption -> ASS conversion (preserves position / color / size) ---

ARIB_FG_COLORS = {
    "BKF": "&H00000000",
    "RDF": "&H000000FF",
    "GRF": "&H0000FF00",
    "YLF": "&H0000FFFF",
    "BLF": "&H00FF0000",
    "MGF": "&H00FF00FF",
    "CNF": "&H00FFFF00",
    "WHF": "&H00FFFFFF",
}
ARIB_SIZES = {"SSZ": 20, "MSZ": 26, "NSZ": 36}
DEFAULT_CANVAS = (840, 480)
DEFAULT_CELL = (36, 36)
DEFAULT_SPACING = (4, 24)


def ass_time(ms):
    """Format milliseconds as ASS time (H:MM:SS.cc)."""
    if ms < 0:
        ms = 0
    cs = (ms % 1000) // 10
    total_s = ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    sec = total_s % 60
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def cue_to_ass_events(cue, keep_tokens=False):
    """Convert one cue to ASS Dialogue lines, one per positioned segment."""
    canvas = list(DEFAULT_CANVAS)
    cell = list(DEFAULT_CELL)
    spacing = list(DEFAULT_SPACING)
    origin = [0, 0]
    segs = []
    cur = {"x": None, "y": None, "color": None, "size": None, "text": ""}

    def flush():
        if cur["text"].strip():
            segs.append(dict(cur))
        cur["text"] = ""

    for line in cue["lines"]:
        for piece in re.split(r"(\[[^\]\[]*\])", line):
            if not piece:
                continue
            if piece.startswith("[") and not keep_tokens:
                tok = piece[1:-1]
                if tok.startswith("APS_"):
                    parts = tok.split("_")
                    if len(parts) >= 3:
                        flush()
                        pitch_x = cell[0] + spacing[0]
                        # Rows use half line-pitch units (measured: row ~15 maps
                        # to the bottom edge of a 480px canvas with SSM 36/SVS 24).
                        pitch_y = cell[1] + spacing[1]
                        cur["x"] = origin[0] + int(parts[2]) * pitch_x
                        cur["y"] = origin[1] + int(parts[1]) * (pitch_y // 2)
                elif tok.startswith("SDF_"):
                    parts = tok.split("_")
                    if len(parts) >= 3:
                        canvas = [int(parts[1]), int(parts[2])]
                elif tok.startswith("SDP_"):
                    parts = tok.split("_")
                    if len(parts) >= 3:
                        origin = [int(parts[1]), int(parts[2])]
                elif tok.startswith("SSM_"):
                    parts = tok.split("_")
                    if len(parts) >= 3:
                        cell = [int(parts[1]), int(parts[2])]
                elif tok.startswith("SHS_"):
                    spacing[0] = int(tok.split("_")[1])
                elif tok.startswith("SVS_"):
                    spacing[1] = int(tok.split("_")[1])
                elif tok in ARIB_FG_COLORS:
                    cur["color"] = ARIB_FG_COLORS[tok]
                elif tok in ARIB_SIZES:
                    cur["size"] = ARIB_SIZES[tok]
                # CS/SWF/COL/ORN/WHF-like setup tokens are ignored.
            else:
                cur["text"] += piece
    flush()

    events = []
    for seg in segs:
        overrides = ["\\an7"]
        if seg["x"] is not None and seg["y"] is not None:
            overrides.append(f"\\pos({seg['x']},{seg['y']})")
        if seg["color"]:
            overrides.append(f"\\c{seg['color']}&")
        if seg["size"]:
            overrides.append(f"\\fs{seg['size']}")
        text = "{" + "".join(overrides) + "}" + seg["text"].strip()
        events.append(
            "Dialogue: 0,{},{},Default,,0,0,0,,".format(
                ass_time(cue["start"]), ass_time(cue["end"])
            )
            + text
        )
    return events


def cues_to_ass(segment_texts, keep_tokens=False):
    """Render VTT segments as an ASS document preserving position/color/size."""
    cues = parse_vtt_cues(segment_texts)
    header = (
        "[Script Info]\n"
        "Title: NHK ONE subtitles\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 840\n"
        "PlayResY: 480\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Sans-serif,36,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H96000000,-1,0,0,0,100,100,0,0,3,0,0,7,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    body = []
    for cue in cues:
        if cue["start"] is None:
            continue
        body.extend(cue_to_ass_events(cue, keep_tokens=keep_tokens))
    return header + "\n".join(body) + "\n"


def download_subtitles(playlist_url, out_dir, max_segments=None, concat=None, keep_tokens=False, skip_segments=0, ass_concat=None):
    text = fetch(playlist_url).decode("utf-8")
    _, segments = parse_media_playlist(text, playlist_url)
    if skip_segments:
        segments = segments[skip_segments:]
    if max_segments is not None:
        segments = segments[:max_segments]
    texts = []
    for i, u in enumerate(segments):
        dest = os.path.join(out_dir, f"{i:06d}-{seg_name(u)}")
        data = fetch(u, dest)
        texts.append(data.decode("utf-8"))
        if (i + 1) % 50 == 0 or i + 1 == len(segments):
            print(f"  subtitle segments: {i + 1}/{len(segments)}")
    if concat:
        merged = merge_vtt(texts, keep_tokens=keep_tokens)
        os.makedirs(os.path.dirname(concat) or ".", exist_ok=True)
        with open(concat, "w", encoding="utf-8") as f:
            f.write(merged)
        print(f"  merged subtitles: {concat} ({os.path.getsize(concat)} bytes)")
    if ass_concat:
        ass_doc = cues_to_ass(texts, keep_tokens=keep_tokens)
        os.makedirs(os.path.dirname(ass_concat) or ".", exist_ok=True)
        with open(ass_concat, "w", encoding="utf-8") as f:
            f.write(ass_doc)
        print(f"  ASS subtitles (positioned): {ass_concat} ({os.path.getsize(ass_concat)} bytes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="NHK ONE program page URL (e.g., https://www.web.nhk/tv/pl/series-tep-XXX/ep/YYY)")
    src.add_argument("--episode-id", help="NHK ONE episode ID (e.g., MZ2R1M49J4)")
    src.add_argument("--descriptor-url", help="video descriptor JSON URL (e.g., https://archive2.hsk.st.nhk/.../videoinfo-XXX.json)")
    src.add_argument("--master", help="master playlist URL")
    src.add_argument("--video-playlist", help="video media playlist URL")
    ap.add_argument("--audio-playlist", default=None, help="audio media playlist URL")
    ap.add_argument("--wvd", required=True, help="path to Widevine device file (.wvd)")
    ap.add_argument("--license-url", default=DEFAULT_LICENSE_URL, help="Widevine license server URL")
    ap.add_argument("--bearer-token", default=None, help="Bearer token for API and license server")
    ap.add_argument("--output", required=True, help="output MP4 file path")
    ap.add_argument("--subtitle-playlist", default=None, help="subtitle (WebVTT) playlist URL")
    ap.add_argument("--concat-subtitles", default=None, help="write merged .vtt subtitle file")
    ap.add_argument("--max-segments", type=int, default=None, help="limit segments per playlist")
    ap.add_argument("--skip-segments", type=int, default=0, help="skip first N media segments (init is always fetched)")
    ap.add_argument("--keep-subtitle-tokens", action="store_true", help="keep raw ARIB control tokens in subtitles")
    ap.add_argument("--work-dir", default="./dl-work", help="working directory")
    args = ap.parse_args()

    os.makedirs(args.work_dir, exist_ok=True)
    master_url = args.master
    video_url = args.video_playlist
    audio_url = args.audio_playlist
    subtitle_url = args.subtitle_playlist

    # --- Resolve input to a master playlist URL ---
    if args.url or args.episode_id:
        # Step 1: Resolve program URL / episode ID to descriptor URL
        episode_id = args.episode_id
        if args.url:
            episode_id = extract_episode_id(args.url)
            if not episode_id:
                print(f"Error: Could not extract episode ID from URL: {args.url}")
                sys.exit(1)
        print(f"[0/4] Resolving episode {episode_id}...")
        title, descriptor_url = resolve_episode(episode_id, bearer_token=args.bearer_token)
        if not descriptor_url:
            print("\nError: Could not obtain video descriptor URL from episode data.")
            print("The detailedVideoDescriptor field was not present in the API response.")
            print("This may require authentication. You can:")
            print("  1. Provide --bearer-token with a valid NHK ONE auth token")
            print("  2. Provide --descriptor-url directly (obtainable via Frida trace)")
            print("  3. Provide --master with a direct master playlist URL")
            sys.exit(1)

        # Step 2: Parse descriptor to get master playlist URL
        print(f"[0.5/4] Parsing video descriptor...")
        master_url, info = parse_descriptor(descriptor_url, bearer_token=args.bearer_token)
        if not master_url:
            print("Error: Could not get master playlist URL from descriptor")
            sys.exit(1)

    elif args.descriptor_url:
        # Direct descriptor URL
        print(f"[0/4] Parsing video descriptor...")
        master_url, info = parse_descriptor(args.descriptor_url, bearer_token=args.bearer_token)
        if not master_url:
            print("Error: Could not get master playlist URL from descriptor")
            sys.exit(1)

    # --- Parse master playlist to get video/audio/subtitle URLs ---
    if master_url:
        print("[*] Parsing master playlist...")
        text = fetch(master_url).decode("utf-8")
        variants, audios, subtitles = parse_master_playlist(text, master_url)
        print(f"  Variants: {len(variants)}, Audio: {len(audios)}, Subtitles: {len(subtitles)}")
        if variants:
            variants.sort(key=lambda v: v["bandwidth"], reverse=True)
            video_url = variants[0]["uri"]
            print(f"  Selected video: {variants[0].get('resolution', '?')} ({variants[0]['bandwidth']} bps)")
        if audios:
            audio_url = audios[0]["uri"]
            print(f"  Selected audio: {audios[0]['name']}")
        if subtitles and not subtitle_url:
            subtitle_url = subtitles[0]["uri"]
            print(f"  Detected subtitles: {subtitles[0]['name']}")

    if not video_url:
        print("Error: No video playlist URL")
        sys.exit(1)

    print("[1/4] Downloading video...")
    video_files, video_init = download_playlist(video_url, os.path.join(args.work_dir, "video"), args.max_segments, args.skip_segments)

    audio_files = []
    audio_init = None
    if audio_url:
        print("[2/4] Downloading audio...")
        audio_files, audio_init = download_playlist(audio_url, os.path.join(args.work_dir, "audio"), args.max_segments, args.skip_segments)
    else:
        print("[2/4] No audio playlist, skipping")

    print("[3/4] Getting decryption keys...")
    if not video_init:
        print("Error: No init segment in video playlist")
        sys.exit(1)
    keys = get_decryption_keys(video_init, args.wvd, args.license_url, args.bearer_token)
    if not keys:
        sys.exit(1)

    # Match each track's tenc default_KID to the correct content key.
    video_kid = parse_tenc_kid(video_init)
    audio_kid = parse_tenc_kid(audio_init) if audio_files else None
    print(f"  video tenc KID: {video_kid}")
    if audio_kid:
        print(f"  audio tenc KID: {audio_kid}")
    video_key = select_key_for_kid(keys, video_kid) if video_kid else None
    audio_key = select_key_for_kid(keys, audio_kid) if audio_kid else None
    if not video_key or (audio_kid and not audio_key):
        print("  Warning: KID match failed, falling back to first content key")
        print(f"  available KIDs: {[k['kid'] for k in keys]}")
        video_key = video_key or keys[0]["key"]
        audio_key = audio_key or keys[0]["key"]

    subtitle_path = None
    if subtitle_url:
        subtitle_path = args.concat_subtitles or os.path.join(args.work_dir, "subtitles.vtt")
        print("  Downloading subtitles...")
        ass_path = os.path.splitext(args.output)[0] + ".ass"
        download_subtitles(subtitle_url, os.path.join(args.work_dir, "subtitles"), args.max_segments, subtitle_path, args.keep_subtitle_tokens, args.skip_segments, ass_path)

    print("[4/4] Decrypting and merging...")
    if not audio_files:
        print("Error: Audio is required for a complete MP4")
        sys.exit(1)
    success = decrypt_and_merge(video_files, audio_files, video_key, audio_key, args.output, subtitle_path)
    if success:
        print("\nDone!")
    else:
        print("\nFailed!")
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())

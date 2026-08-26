#!/usr/bin/env python3
"""Download and decrypt NHK ONE VOD content using a Widevine device (.wvd) file.

Downloads encrypted HLS segments, extracts PSSH from the init segment,
obtains Widevine license keys via pywidevine, and decrypts the content
using FFmpeg. Subtitles can also be downloaded and merged.

Requirements:
    pip install pywidevine
    ffmpeg installed and on PATH
    A .wvd Widevine L3 device file
    A Bearer token for the NHK license server

Usage:
    # Full download + decrypt via master playlist
    python3 06-decrypt-vod.py \\
        --master <master-playlist-url> \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output output.mp4

    # Specify video and audio playlists directly
    python3 06-decrypt-vod.py \\
        --video-playlist <video-playlist-url> \\
        --audio-playlist <audio-playlist-url> \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output output.mp4

    # With subtitles
    python3 06-decrypt-vod.py \\
        --master <master-playlist-url> \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output output.mp4 \\
        --concat-subtitles subtitles.vtt

    # Test with limited segments
    python3 06-decrypt-vod.py \\
        --master <master-playlist-url> \\
        --wvd device.wvd \\
        --bearer-token <token> \\
        --output output.mp4 \\
        --max-segments 3
"""
import argparse
import base64
import os
import re
import struct
import subprocess
import sys
import urllib.parse
import urllib.request

WIDEVINE_SYSTEM_ID = bytes.fromhex("edef8ba979d64acea3c827dcd51d21ed")
DEFAULT_LICENSE_URL = "https://licence.hsk.st.nhk/widevine/license"
UA = "nhk-one-research/1.0"


def fetch(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
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


def download_playlist(playlist_url, out_dir, max_segments=None):
    """Download init + media segments. Returns (file_paths, init_path)."""
    text = fetch(playlist_url).decode("utf-8")
    init_url, segments = parse_media_playlist(text, playlist_url)
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


def decrypt_and_merge(video_files, audio_files, keys, output_path, subtitle_path=None):
    """Decrypt encrypted fMP4 and merge to MP4 using FFmpeg."""
    content_key = keys[0]["key"]

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
        "-decryption_key", content_key,
        "-i", v_concat,
        "-decryption_key", content_key,
        "-i", a_concat,
        "-c", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
    ]
    if subtitle_path and os.path.exists(subtitle_path):
        cmd.extend(["-i", subtitle_path, "-map", "2:s:0", "-c:s", "mov_text"])
    cmd.append(output_path)

    print("  Running FFmpeg...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FFmpeg stderr (tail): {result.stderr[-800:]}")
        return False
    print(f"  Output: {output_path} ({os.path.getsize(output_path)} bytes)")
    return True


def merge_vtt(segment_texts):
    out = ["WEBVTT", ""]
    for text in segment_texts:
        lines = text.replace("\r\n", "\n").split("\n")
        i = 0
        if lines and lines[0].startswith("WEBVTT"):
            i = 1
        body = lines[i:]
        while body and body[0] == "":
            body = body[1:]
        out.extend(body)
        if out and out[-1] != "":
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def download_subtitles(playlist_url, out_dir, max_segments=None, concat=None):
    text = fetch(playlist_url).decode("utf-8")
    _, segments = parse_media_playlist(text, playlist_url)
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
        merged = merge_vtt(texts)
        os.makedirs(os.path.dirname(concat) or ".", exist_ok=True)
        with open(concat, "w", encoding="utf-8") as f:
            f.write(merged)
        print(f"  merged subtitles: {concat} ({os.path.getsize(concat)} bytes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--master", help="master playlist URL")
    src.add_argument("--video-playlist", help="video media playlist URL")
    ap.add_argument("--audio-playlist", default=None, help="audio media playlist URL")
    ap.add_argument("--wvd", required=True, help="path to Widevine device file (.wvd)")
    ap.add_argument("--license-url", default=DEFAULT_LICENSE_URL, help="Widevine license server URL")
    ap.add_argument("--bearer-token", default=None, help="Bearer token for license server")
    ap.add_argument("--output", required=True, help="output MP4 file path")
    ap.add_argument("--subtitle-playlist", default=None, help="subtitle (WebVTT) playlist URL")
    ap.add_argument("--concat-subtitles", default=None, help="write merged .vtt subtitle file")
    ap.add_argument("--max-segments", type=int, default=None, help="limit segments per playlist")
    ap.add_argument("--work-dir", default="./dl-work", help="working directory")
    args = ap.parse_args()

    os.makedirs(args.work_dir, exist_ok=True)
    video_url = args.video_playlist
    audio_url = args.audio_playlist
    subtitle_url = args.subtitle_playlist

    if args.master:
        print("[*] Parsing master playlist...")
        text = fetch(args.master).decode("utf-8")
        variants, audios, subtitles = parse_master_playlist(text, args.master)
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
    video_files, video_init = download_playlist(video_url, os.path.join(args.work_dir, "video"), args.max_segments)

    audio_files = []
    if audio_url:
        print("[2/4] Downloading audio...")
        audio_files, _ = download_playlist(audio_url, os.path.join(args.work_dir, "audio"), args.max_segments)
    else:
        print("[2/4] No audio playlist, skipping")

    print("[3/4] Getting decryption keys...")
    if not video_init:
        print("Error: No init segment in video playlist")
        sys.exit(1)
    keys = get_decryption_keys(video_init, args.wvd, args.license_url, args.bearer_token)
    if not keys:
        sys.exit(1)

    subtitle_path = None
    if subtitle_url:
        subtitle_path = args.concat_subtitles or os.path.join(args.work_dir, "subtitles.vtt")
        print("  Downloading subtitles...")
        download_subtitles(subtitle_url, os.path.join(args.work_dir, "subtitles"), args.max_segments, subtitle_path)

    print("[4/4] Decrypting and merging...")
    if not audio_files:
        print("Error: Audio is required for a complete MP4")
        sys.exit(1)
    success = decrypt_and_merge(video_files, audio_files, keys, args.output, subtitle_path)
    if success:
        print("\nDone!")
    else:
        print("\nFailed!")
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())

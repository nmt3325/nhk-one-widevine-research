#!/usr/bin/env python3
"""Download NHK ONE VOD HLS segments as-is (still encrypted fMP4) plus subtitles.

Fetches the init segment and media segments referenced by a media playlist
(or all renditions of a master playlist) and writes them to disk without any
decryption. Optionally concatenates init + segments into a single fragmented
MP4 that remains encrypted. WebVTT subtitle segments can be downloaded and
merged into a single .vtt file alongside the video/audio.

Usage:
    python3 05-download-encrypted-vod.py --playlist <media-playlist-url> \
        --output out-dir [--max-segments N] [--concat out.mp4] \
        [--subtitle-playlist <sub-playlist-url>] [--concat-subtitles out.vtt]

    python3 05-download-encrypted-vod.py --master <master-playlist-url> \
        --output out-dir [--max-segments N] [--no-subtitles]

Requires access from a network where the content is available (Japan).
"""
import argparse
import os
import re
import sys
import urllib.parse
import urllib.request

UA = "nhk-one-research/1.0"


def fetch(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if dest:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
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
    variants = []
    subtitles = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=SUBTITLES" in line:
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                subtitles.append(urllib.parse.urljoin(base_url, m.group(1)))
        elif line.startswith("#"):
            continue
        elif line:
            variants.append(urllib.parse.urljoin(base_url, line))
    return variants, subtitles


def seg_name(url):
    path = urllib.parse.urlparse(url).path
    return path.rsplit("/", 1)[-1] or "segment"


def merge_vtt(segment_texts):
    """Merge WebVTT segments: keep one WEBVTT header, drop later headers.

    Per-segment X-TIMESTAMP-MAP header lines are kept so local timestamps can
    be re-based by players that understand them; blank-line separated cues are
    concatenated in order.
    """
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


def download_playlist(playlist_url, out_dir, max_segments=None, concat=None):
    text = fetch(playlist_url).decode("utf-8")
    init, segments = parse_media_playlist(text, playlist_url)
    if max_segments is not None:
        segments = segments[:max_segments]
    written = []
    if init:
        dest = os.path.join(out_dir, seg_name(init))
        fetch(init, dest)
        written.append(dest)
        print(f"init: {seg_name(init)} ({os.path.getsize(dest)} bytes)")
    for i, u in enumerate(segments):
        dest = os.path.join(out_dir, f"{i:06d}-{seg_name(u)}")
        fetch(u, dest)
        written.append(dest)
        if (i + 1) % 20 == 0 or i + 1 == len(segments):
            print(f"segments: {i + 1}/{len(segments)}")
    if concat:
        with open(concat, "wb") as out:
            for p in written:
                with open(p, "rb") as f:
                    out.write(f.read())
        print(f"concat (still encrypted): {concat} ({os.path.getsize(concat)} bytes)")
    return written


def download_subtitles(playlist_url, out_dir, max_segments=None, concat=None):
    """Download WebVTT subtitle segments (unencrypted) and optionally merge."""
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
            print(f"subtitle segments: {i + 1}/{len(segments)}")
    if concat:
        merged = merge_vtt(texts)
        os.makedirs(os.path.dirname(concat) or ".", exist_ok=True)
        with open(concat, "w", encoding="utf-8") as f:
            f.write(merged)
        print(f"merged subtitles: {concat} ({os.path.getsize(concat)} bytes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--playlist", help="media playlist URL (e.g. .../cenc/v1500/playlist.m3u8)")
    src.add_argument("--master", help="master playlist URL; downloads every variant")
    ap.add_argument("--output", required=True, help="output directory")
    ap.add_argument("--max-segments", type=int, default=None, help="limit segments per playlist (for testing)")
    ap.add_argument("--concat", default=None, help="write concatenated fMP4 (only valid with --playlist)")
    ap.add_argument("--subtitle-playlist", default=None, help="subtitle (WebVTT) media playlist URL")
    ap.add_argument("--concat-subtitles", default=None, help="write merged .vtt subtitle file")
    ap.add_argument("--no-subtitles", action="store_true", help="skip subtitles referenced by --master")
    args = ap.parse_args()

    if args.master:
        text = fetch(args.master).decode("utf-8")
        variants, subtitles = parse_master_playlist(text, args.master)
        print(f"master variants: {len(variants)}, subtitle playlists: {len(subtitles)}")
        for v in variants:
            name = re.sub(r"[^A-Za-z0-9_-]+", "_", urllib.parse.urlparse(v).path.strip("/"))
            download_playlist(v, os.path.join(args.output, name), args.max_segments)
        if subtitles and not args.no_subtitles:
            for i, s in enumerate(subtitles):
                concat = args.concat_subtitles or os.path.join(args.output, f"subtitles_{i}.vtt")
                download_subtitles(s, os.path.join(args.output, "subtitles"), args.max_segments, concat)
    else:
        download_playlist(args.playlist, args.output, args.max_segments, args.concat)
        sub_url = args.subtitle_playlist
        if sub_url or args.concat_subtitles:
            if not sub_url:
                ap.error("--concat-subtitles requires --subtitle-playlist when using --playlist")
            download_subtitles(sub_url, os.path.join(args.output, "subtitles"),
                               args.max_segments, args.concat_subtitles)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""NHK Android live manifest/DRM signaling metadata probe.

Fetches GeoIP status, videoinfo descriptors, HLS playlists, and small fMP4
initialization segments.
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

CHANNELS = {
    "G1": "7fe0-0400",
    "G2": "7fe0-0401",
    "E1": "7fe1-0408",
    "E3": "7fe1-040a",
}
UA = {"User-Agent": "manifest-metadata-research/1.0"}


def fetch(url: str, timeout: int) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def parse_attributes(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', text):
        result[match.group(1)] = match.group(2).strip().strip('"')
    return result


def parse_schemes(data: bytes) -> list[str]:
    values: set[str] = set()
    position = 0
    while True:
        position = data.find(b"schm", position)
        if position < 0:
            break
        if position + 12 <= len(data):
            value = data[position + 8 : position + 12]
            if all(32 <= char < 127 for char in value):
                values.add(value.decode("ascii"))
        position += 4
    return sorted(values)


def parse_pssh_system_ids(data: bytes) -> list[str]:
    values: set[str] = set()
    position = 0
    while True:
        position = data.find(b"pssh", position)
        if position < 0:
            break
        if position + 24 <= len(data):
            try:
                values.add(str(uuid.UUID(bytes=data[position + 8 : position + 24])))
            except ValueError:
                pass
        position += 4
    return sorted(values)


def analyze(timeout: int) -> dict:
    _, geo_bytes = fetch("https://location.tools.nhk/geoip/area.json", timeout)
    geo = json.loads(geo_bytes)
    if geo.get("country_code") != "JP":
        raise RuntimeError(
            "This metadata is region-restricted. Use a network that is "
            "legitimately authorized for access in Japan."
        )

    output = {
        "measuredAt": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
        "geo": {"countryCode": geo.get("country_code"), "countryName": geo.get("country_name")},
        "channels": {},
    }

    for channel, channel_id in CHANNELS.items():
        descriptor_url = f"https://simul2.hsk.st.nhk/npd4/{channel_id}/simul/videoinfo.json"
        descriptor_status, descriptor_bytes = fetch(descriptor_url, timeout)
        descriptor = json.loads(descriptor_bytes)
        manifests = descriptor.get("manifests", [])
        channel_result = {
            "descriptorUrl": descriptor_url,
            "descriptorHttpStatus": descriptor_status,
            "manifestCount": len(manifests),
            "drmCounts": {},
            "bitrateLimitTypes": sorted(
                {item["bitrate_limit_type"] for item in manifests if item.get("bitrate_limit_type")}
            ),
            "samples": {},
        }
        for item in manifests:
            drm_type = item.get("drm_type", "unknown")
            channel_result["drmCounts"][drm_type] = channel_result["drmCounts"].get(drm_type, 0) + 1

        for drm_type in ("cenc", "cbcs"):
            item = next(
                entry
                for entry in manifests
                if entry.get("drm_type") == drm_type
                and entry.get("bitrate_limit_type") == "m3000"
            )
            master_url = item["url"]
            master_status, master_bytes = fetch(master_url, timeout)
            master_lines = master_bytes.decode("utf-8", "replace").splitlines()
            variants = []
            for index, line in enumerate(master_lines):
                if not line.startswith("#EXT-X-STREAM-INF:"):
                    continue
                attributes = parse_attributes(line.split(":", 1)[1])
                next_index = index + 1
                while next_index < len(master_lines) and (
                    not master_lines[next_index] or master_lines[next_index].startswith("#")
                ):
                    next_index += 1
                if next_index < len(master_lines):
                    variants.append(
                        (
                            int(attributes.get("BANDWIDTH", "0")),
                            attributes,
                            urllib.parse.urljoin(master_url, master_lines[next_index]),
                        )
                    )
            bandwidth, variant_attributes, variant_url = max(variants, key=lambda value: value[0])
            variant_status, variant_bytes = fetch(variant_url, timeout)
            variant_lines = variant_bytes.decode("utf-8", "replace").splitlines()
            key_line = next(
                (line.split(":", 1)[1] for line in variant_lines if line.startswith("#EXT-X-KEY:")),
                "",
            )
            map_line = next(
                (line.split(":", 1)[1] for line in variant_lines if line.startswith("#EXT-X-MAP:")),
                "",
            )
            key_attributes = parse_attributes(key_line)
            map_attributes = parse_attributes(map_line)
            init_url = urllib.parse.urljoin(variant_url, map_attributes["URI"])
            init_status, init_bytes = fetch(init_url, timeout)
            key_uri_scheme = urllib.parse.urlsplit(key_attributes.get("URI", "")).scheme or "relative"
            media_resources = [
                line for line in variant_lines if line and not line.startswith("#")
            ]
            target_duration = next(
                (
                    line.split(":", 1)[1]
                    for line in variant_lines
                    if line.startswith("#EXT-X-TARGETDURATION:")
                ),
                None,
            )
            channel_result["samples"][drm_type] = {
                "masterUrl": master_url,
                "masterHttpStatus": master_status,
                "variantUrl": variant_url,
                "variantHttpStatus": variant_status,
                "bandwidth": bandwidth,
                "codecs": variant_attributes.get("CODECS"),
                "frameRate": variant_attributes.get("FRAME-RATE"),
                "targetDurationSeconds": int(target_duration) if target_duration else None,
                "mediaResourceCountObserved": len(media_resources),
                "keyMethod": key_attributes.get("METHOD"),
                "keyFormat": key_attributes.get("KEYFORMAT"),
                "keyFormatVersion": key_attributes.get("KEYFORMATVERSIONS")
                or key_attributes.get("KEYFORMATVERSION"),
                "keyUriScheme": key_uri_scheme,
                "initPath": urllib.parse.urlsplit(init_url).path,
                "initHttpStatus": init_status,
                "initBytes": len(init_bytes),
                "initSchemeTypes": parse_schemes(init_bytes),
                "psshSystemIds": parse_pssh_system_ids(init_bytes),
                "encryptedSampleEntryPresent": b"encv" in init_bytes or b"enca" in init_bytes,
            }
        output["channels"][channel] = channel_result
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="android-live-manifest-summary.json")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    try:
        result = analyze(args.timeout)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(result, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")
    for channel, value in result["channels"].items():
        print(channel, value["descriptorHttpStatus"], value["drmCounts"])
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

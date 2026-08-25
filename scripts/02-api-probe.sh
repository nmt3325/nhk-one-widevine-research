#!/bin/bash
# NHK ONE API の GeoIP 制限検証とエピソード情報取得
# 米国 IP からは 403、日本プロキシ経由では 200
set -e

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36'

# 日本プロキシを指定（検証時点で稼働していた公開プロキシ）
PROXY="${PROXY:-http://45.43.60.220:8080}"

echo '=== 出口 IP 確認 ==='
curl -s ipinfo.io | head -c 300; echo

echo '=== 直接アクセス（403 になるはず）==='
curl -s -A "$UA" 'https://api.web.nhk/r8/l/tvepisode/pl/series-tep-WP56ZZMRQ3.json' \
  -o /dev/null -w 'HTTP %{http_code}\n'

echo '=== プロキシ経由（200 になるはず）==='
curl -s -x "$PROXY" -A "$UA" -H 'Referer: https://www.web.nhk/' \
  'https://api.web.nhk/r8/l/tvepisode/pl/series-tep-WP56ZZMRQ3.json' \
  -o /tmp/ep.json -w 'HTTP %{http_code} size=%{size_download}\n'

echo '=== エピソード一覧 ==='
python3 - << 'PYEOF'
import json
d = json.load(open('/tmp/ep.json'))
for item in d.get('result', []):
    ig = item.get('identifierGroup', {})
    print(item.get('id'), '|', item.get('name'), '|', ig.get('broadcastEventId'))
PYEOF

echo '=== 放送イベント詳細 ==='
curl -s -x "$PROXY" -A "$UA" -H 'Referer: https://www.web.nhk/' \
  'https://api.web.nhk/r8/t/broadcastevent/be/g1-130-2026082501264.json' \
  -o /tmp/be.json -w 'HTTP %{http_code} size=%{size_download}\n'

echo '=== エピソード詳細 ==='
curl -s -x "$PROXY" -A "$UA" -H 'Referer: https://www.web.nhk/' \
  'https://api.web.nhk/r8/t/tvepisode/te/RM1ZY9WR96.json' \
  -o /tmp/te.json -w 'HTTP %{http_code} size=%{size_download}\n'

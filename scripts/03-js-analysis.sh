#!/bin/bash
# NHK ONE フロントエンド JS の解析
# 目的: Widevine ライセンス URL とマニフェスト取得ロジックの特定
set -e

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36'
PROXY="${PROXY:-http://45.43.60.220:8080}"
URL='https://www.web.nhk/tv/pl/series-tep-WP56ZZMRQ3/ep/RM1ZY9WR96'

mkdir -p /tmp/nhk/js
cd /tmp/nhk

# 1. ページ HTML 取得
curl -s -A "$UA" "$URL" -o page.html

# 2. JS チャンク一覧を抽出
grep -oE '/_next/static/chunks/[a-zA-Z0-9/._%-]+\.js' page.html | sort -u > chunks.txt

# 3. チャンクを取得
while read -r c; do
  f=$(basename "$c")
  timeout 20 curl -s -x "$PROXY" -A "$UA" --max-time 18 "https://www.web.nhk$c" -o "js/$f" 2>/dev/null || true
done < chunks.txt

# 4. キーワード検索
echo '=== Widevine ライセンス URL ==='
grep -hoE 'https://licence[^" ]+' js/*.js | sort -u

echo '=== マニフェスト取得パターン ==='
grep -hoE '.{80}manifests\[0\]\.url.{80}' js/*.js | head -n 5

echo '=== DRM 設定 ==='
grep -hoE '.{60}com\.widevine\.alpha.{100}' js/*.js | head -n 5

#!/bin/bash
# yt-dlp による NHK ONE の動作検証
# 結果: DRM 保護サイトとして拒否される
set -e

export PATH="$PATH:/home/runner/.local/bin"

URL='https://www.web.nhk/tv/pl/series-tep-WP56ZZMRQ3/ep/RM1ZY9WR96'

echo '=== yt-dlp version ==='
yt-dlp --version

echo '=== list-formats ==='
yt-dlp --list-formats "$URL" 2>&1 | head -n 30

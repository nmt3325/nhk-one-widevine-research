# Evidence

`android-live-manifest-summary.json`は2026-08-26に日本国内経路から取得した配信メタデータの要約です。

`segment-availability.json`は、メディアセグメントURLへのHEAD／先頭1KBのRange GETのみで確認した取得可否の記録です。

`encryption-scheme.json`は、fMP4初期化セグメントのbox構造（`ftyp`/`encv`/`enca`/`frma`/`schm`/`tenc`/`pssh`）とHLS `#EXT-X-KEY`の構造を解析した暗号化シグナリングの記録です。

含むもの:

- 公開ディスクリプタ／HLS URL
- HTTPステータス
- HLS暗号化タグの種類
- fMP4 init segmentのbox/schemeメタデータ
- DRM system UUID

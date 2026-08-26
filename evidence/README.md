# Evidence

`android-live-manifest-summary.json`は2026-08-26に日本国内経路から取得した配信メタデータのサニタイズ済み要約です。

`segment-availability.json`は、メディアセグメントURLへのHEAD／先頭1KBのRange GETのみで確認した取得可否の記録です。セグメント本文は保存していません。

含まないもの:

- 認証トークン、Cookie、`hdnts`値
- `#EXT-X-KEY`のURIペイロード
- PSSH本体やkey ID
- DRM challenge／license response／コンテンツ鍵
- 映像・音声メディアセグメント

含むもの:

- 公開ディスクリプタ／HLS URL
- HTTPステータス
- HLS暗号化タグの種類
- fMP4 init segmentのbox/schemeメタデータ
- DRM system UUID

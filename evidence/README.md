# Evidence

`android-live-manifest-summary.json`は2026-08-26に日本国内経路から取得した配信メタデータの要約です。

`segment-availability.json`は、メディアセグメントURLへのHEAD／先頭1KBのRange GETのみで確認した取得可否の記録です。

`encryption-scheme.json`は、fMP4初期化セグメントのbox構造（`ftyp`/`encv`/`enca`/`frma`/`schm`/`tenc`/`pssh`）とHLS `#EXT-X-KEY`の構造を解析した暗号化シグナリングの記録です。

`segment-senc-detail.json`は、メディアセグメントの`senc`（サンプル毎IV・サブサンプル構成）と、`avcC`/`esds`から復元したコーデック設定、字幕セグメント形式の記録です。

`stream-details.json` / `stream-details2.json` / `stream-details3.json`は、字幕・DASH・DRCS・`control.json`・`need_L1_hd`・DVR・VODパスの追加調査記録です。

`dynamic-playback.json`は、日本経由でアプリのライブ再生を実行した際の実測記録（選択プレイリスト、ライセンス要求、L3フォールバック挙動）です。

`vod-dynamic-analysis.json`は、実アプリでVODを再生した際のコンテンツ固有descriptor、選択HLS、プレイリスト長、字幕、セグメント取得可否、CENC/CBCSのinit/senc構造をまとめた記録です。

含むもの:

- 公開ディスクリプタ／HLS URL
- HTTPステータス
- HLS暗号化タグの種類
- fMP4 init segmentのbox/schemeメタデータ
- DRM system UUID

# NHK ONE / NHKプラス 配信・DRM調査

NHK ONE（旧 NHKプラス）の配信メタデータ、Androidアプリの通信経路、HLS暗号化シグナリングを検証した記録です。

## 結論

| 項目 | 確認結果 |
| --- | --- |
| GeoIP制限 | 日本国外ではアプリが地域制限を表示し、ライブ配信ディスクリプタはHTTP 403。日本経由ではHTTP 200 |
| Androidライブ配信 | G1・G2・E1・E3の`videoinfo.json`と最終HLS URLを確認 |
| Android VOD再生 | コンテンツ固有descriptor、CENC/CBCS HLS、暗号化映像・音声・WebVTT字幕の実再生／取得を確認 |
| 配信形式 | HLS + fragmented MP4（`.m4s`）、AVC/H.264 + AAC |
| Widevine経路 | `cenc`、`SAMPLE-AES-CTR`、Widevine UUID/PSSHを確認 |
| FairPlay経路 | `cbcs`、`SAMPLE-AES`、`com.apple.streamingkeydelivery`を確認 |
| Widevineライセンス先 | `https://licence.hsk.st.nhk/widevine/license` |
| Androidエミュレーター | Widevine L3、CDM `18.0.0@340720000` |

## レポート

- [Androidアプリ静的・動的解析（ライブ配信URL／暗号化）](docs/android-app-analysis.md)
- [Web/VODに関する先行調査](docs/findings.md)
- [ライブ計測結果](evidence/android-live-manifest-summary.json)
- [VOD動的解析結果](evidence/vod-dynamic-analysis.json)

## 再計測

日本国内で正規に利用可能なネットワークから、次を実行します。

```bash
python3 scripts/04-live-manifest-probe.py \
  --output android-live-manifest-summary.json
```


## 注意

- URLやAPI構造は検証時点（2026-08-26）のものです。
- 配信の利用はサービスの利用条件、権利、地域制限に従ってください。

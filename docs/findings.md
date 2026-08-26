# 調査結果の詳細

> **2026-08-26追補:** Androidライブ配信のディスクリプタ、HLS URL、CENC/CBCSシグナリングは追加調査で確定した。詳細は[Androidアプリ静的・動的解析](android-app-analysis.md)を参照。Web/VODとAndroidライブは配信経路が異なるため、結果を混同しないこと。

検証日時: 2026-08-26 (JST)
対象: NHK ONE ニュース動画 `https://www.web.nhk/tv/pl/series-tep-WP56ZZMRQ3/ep/RM1ZY9WR96`
（2026年8月25日午後11:40 ニュース・気象情報、VOD、約10分）

## 1. yt-dlp の動作

```
$ yt-dlp --list-formats 'https://www.web.nhk/tv/pl/series-tep-WP56ZZMRQ3/ep/RM1ZY9WR96'
[DRM] Extracting URL: https://www.web.nhk/tv/pl/series-tep-WP56ZZMRQ3/ep/RM1ZY9WR96
ERROR: [DRM] The requested site is known to use DRM protection. It will NOT be supported.
```

yt-dlp は NHK ONE を DRM 保護サイトとして認識し、ダウンロードを拒否する。

## 2. GeoIP 制限

サンドボックス環境の出口 IP は米国（Microsoft Azure, Virginia）だった。

- `api-local.ss.org.nhk` → 403 Forbidden（日本 IP でも 403。内部用エイリアスの可能性）
- `api.web.nhk/r8` → 米国 IP からは 403、**日本プロキシ経由では 200**

結論: NHK ONE の API は日本国内 IP のみ許可する GeoIP 制限がある。

## 3. 発見した API エンドポイント

### エピソード一覧
```
GET https://api.web.nhk/r8/l/tvepisode/pl/series-tep-WP56ZZMRQ3.json
```
番組シリーズに属するエピソード一覧（id, name, identifierGroup, detailedContentStatus など）。

### 放送イベント
```
GET https://api.web.nhk/r8/t/broadcastevent/be/g1-130-2026082501264.json
```
対象回の放送情報（startDate, endDate, video 配列, streamType: vod, contentStatus: ready）。

### エピソード詳細
```
GET https://api.web.nhk/r8/t/tvepisode/te/RM1ZY9WR96.json
```
エピソード詳細。`video[0]` に identifierGroup（environmentId: hskOriginal, broadcastEventId, serviceId: g1, streamType: vod）と detailedContentStatus（contentStatus: ready）、expires（2026-09-01T23:50:00+09:00）、duration（PT9M59S）を含む。

## 4. DRM 関連の発見（JS 解析より）

NHK ONE のフロントエンド JS（Next.js チャンク）を解析して以下を特定した。

### Widevine ライセンスサーバー
```
https://licence.hsk.st.nhk/widevine/license
```

### FairPlay 証明書 URL
```
https://licence.hsk.st.nhk/fps/license
```

### プレイヤーの DRM 設定
- Key system: `com.widevine.alpha`
- initDataTypes: `cenc`
- robustness: `SW_SECURE_CRYPTO` / `HW_SECURE_CRYPTO`（L1 判定に使用）

### マニフェスト URL の取得方法
プレイヤー JS に以下のコードパターンが存在する:
```js
let l = new URL((await fetch(e).then(e => e.json())).manifests[0].url)
```
つまり MPD マニフェスト URL は「ストリーム情報 API」（変数 `e`）のレスポンス JSON の `manifests[0].url` から取得される。
この API の正確なエンドポイントと認証要件は未特定（要継続調査）。

## 5. 認証フロー（判明分）

JS から以下のトークン構成が判明:
- `accountlessAuthZ`（未ログイン用認可トークン）
- `accountAuthZ`（ログイン済み認可トークン）
- `accountAuthN`（認証トークン）
- Cookie キー: `z_at`（authZ access token）, `n_at`（authN access token）
- API 呼び出し時は `Authorization: Bearer <token>` ヘッダーを付与

認可サーバー:
- `https://r.authz.ac1.nhk`（accountless）
- `https://r.authz.ac2.nhk`（account）
- `https://a.authz.ac1.nhk`（accountless abroad）
- `https://d.authz.ac1.nhk`（emergency）

## 6. 現在の整理

1. Androidライブのストリーム情報APIと最終HLS URLは特定済み。
2. CENC/WidevineとCBCS/FairPlayの暗号化シグナリングを確認済み。
3. Android VODの`detailedVideoDescriptor`からコンテンツ固有URLを取得し、CENC/Widevine再生、CBCSプレイリスト、映像・音声・字幕セグメントを実測済み。
4. ライブとVODはdescriptor取得経路と`need_L1_hd` / `allow_multispeed`設定が異なるため、区別して扱う。

## 7. 使用したツール・リソース

- yt-dlp 2026.08.19（pip）
- pywidevine（pip）
- curl / grep / Python 3.12
- 公開日本プロキシ（SOCKS5 / HTTP）
- NHK ONE フロントエンド JS チャンク（Next.js）

## 8. 参考情報

- Androidライブは検証時点でHLS + fMP4を使用していた。
- Widevine経路は`SAMPLE-AES-CTR` / `cenc`、FairPlay経路は`SAMPLE-AES` / `cbcs`だった。
- 詳細な再現可能データは`evidence/android-live-manifest-summary.json`を参照。

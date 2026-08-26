# Androidアプリ静的・動的解析

検証日: 2026-08-26（JST）

## 1. 対象と範囲

| 項目 | 値 |
| --- | --- |
| アプリ名 | NHKプラス |
| Package | `nhk.app.tep` |
| Version | `1.1.9`（versionCode `10469`） |
| minSdk / targetSdk | 25 / 35 |
| Launcher | `com.sunagalab.nhkbxclient.MainActivity` |
| 配布物 | split APKを含むXAPK/ZIPコンテナ |
| 配布物SHA-256 | `e369fcf566d7e552e25d564356d886d48bc0b9bee32390f2c8e485ae2f95dd5e` |
| Base APK SHA-256 | `314ef30b5401d3292373353767d6d872868584a0d372ac00b7a766fb0f9219d3` |

静的解析（JADX）と、Android 14エミュレーター上でのFridaによるURL／Media3／MediaDrmメタデータトレースを組み合わせた。

## 2. GeoIPによる差分

### 日本国外の出口

- `https://location.tools.nhk/geoip/area.json` は `US` を返した。
- アプリは「お住いの地域からはご利用いただけません」と表示した。
- G1・G2・E1・E3の配信ディスクリプタはすべてHTTP 403だった。
- この状態ではマニフェスト、セグメント、Widevineライセンス要求は開始されなかった。

### 日本の出口

正規に利用可能な日本のTailscale Exit Nodeへ切り替えたところ、GeoIPは`JP / Japan`となり、4チャンネルすべてのディスクリプタがHTTP 200を返した。アプリのライブ画面も地域制限なしで読み込まれた。

## 3. ライブ配信ディスクリプタ

| サービス | ディスクリプタURL |
| --- | --- |
| G1 | `https://simul2.hsk.st.nhk/npd4/7fe0-0400/simul/videoinfo.json` |
| G2 | `https://simul2.hsk.st.nhk/npd4/7fe0-0401/simul/videoinfo.json` |
| E1 | `https://simul2.hsk.st.nhk/npd4/7fe1-0408/simul/videoinfo.json` |
| E3 | `https://simul2.hsk.st.nhk/npd4/7fe1-040a/simul/videoinfo.json` |

各レスポンスには24個の`manifests`が含まれていた。

- `cenc`: 12件
- `cbcs`: 12件
- `bitrate_limit_type`: `m6000`, `m3000`, `m1500`, `m0768`, `m0384`, `m0192`, `s6000`, `s3000`, `s1500`, `s0768`, `s0384`, `s0192`

各ディスクリプタには`manifests`以外に次のフィールドがある。

- `need_L1_hd`: `true`（G1・G2・E1・E3すべて）
- `allow_multispeed`: `false`

`m`/`s`接頭辞は、アプリ内の`BitrateLimitType.isMulti`（先頭`m`ならmulti）で区別される。実測でも`m3000`は複数音声グループ（`l2`=am064, `l4`=am192）を持ち、`s3000`は単一音声グループ（`l0`=am192）のみを持つ。つまり`m`=multi（複数音声）、`s`=single（単一音声）である。

## 4. 最終HLS URL

URLは次の規則になっていた。

```text
https://simul2.hsk.st.nhk/npd4/<channel-id>/simul/<drm>/manifest_<profile>.m3u8
```

代表例（G1、`m3000`）:

```text
https://simul2.hsk.st.nhk/npd4/7fe0-0400/simul/cenc/manifest_m3000.m3u8
https://simul2.hsk.st.nhk/npd4/7fe0-0400/simul/cbcs/manifest_m3000.m3u8
```

選択された映像メディアプレイリストの代表例:

```text
https://simul2.hsk.st.nhk/npd4/vs1a/7fe0-0400/simul/cenc/v3000/playlist.m3u8
https://simul2.hsk.st.nhk/npd4/vs1a/7fe0-0400/simul/cbcs/v3000/playlist.m3u8
```

G1・G2・E1・E3について、ディスクリプタ、`m3000`マスター、映像メディアプレイリストはいずれも日本経由でHTTP 200だった。

## 5. HLS構造

`m3000`の代表サンプルでは次を確認した。

| 項目 | 値 |
| --- | --- |
| マスタープレイリスト | HLS version 4 |
| メディアプレイリスト | HLS version 7 |
| コンテナ | fragmented MP4、メディア拡張子`.m4s` |
| 初期化 | `#EXT-X-MAP` |
| Target duration | 7秒 |
| 最大観測BANDWIDTH | 3,192,000 bit/s |
| 映像 | AVC/H.264（例: `avc1.4d001f`） |
| 音声 | AAC-LC（`mp4a.40.2`） |
| Frame rate | 29.970 |
| 音声トラック | 日本語main/subの記述あり |
| 字幕 | 日本語字幕の記述あり |

これはライブのローリングプレイリストであり、計測時には各メディアプレイリストに4個のセグメントURIが見えていた。

## 6. 暗号化方式

### Widevine / CENC

メディアプレイリスト:

```text
#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,
  KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",
  KEYFORMATVERSIONS="1"
```

確認事項:

- `METHOD=SAMPLE-AES-CTR`
- Widevine system UUID: `edef8ba9-79d6-4ace-a3c8-27dcd51d21ed`
- KEY URI scheme: `data`
- fMP4 init segmentに`encv`, `sinf`, `schm`, `tenc`, `pssh`を確認
- `schm`のscheme typeは`cenc`
- `pssh`のsystem IDはWidevine UUID

したがって、Android向けライブ経路はHLS上のCENC AES-CTR + Widevineであることを、プレイリストと初期化セグメントの両方から確認できた。

### FairPlay / CBCS

メディアプレイリスト:

```text
#EXT-X-KEY:METHOD=SAMPLE-AES,
  KEYFORMAT="com.apple.streamingkeydelivery",
  KEYFORMATVERSION="1"
```

確認事項:

- `METHOD=SAMPLE-AES`
- KEYFORMAT: `com.apple.streamingkeydelivery`
- KEY URI scheme: `skd`
- fMP4 init segmentに`encv`, `sinf`, `schm`, `tenc`を確認
- `schm`のscheme typeは`cbcs`
- Widevine PSSHは存在しなかった

## 6.5 暗号化方式の詳細（fMP4初期化セグメント解析）

G1の`cenc`/`cbcs`それぞれの映像・音声初期化セグメント（`init_1003.mp4`）をbox単位で解析した。

### 共通構造

- `ftyp`: `iso5`（minor version 512）
- 映像トラック: `encv`、元フォーマット`avc1`
- 音声トラック: `enca`、元フォーマット`mp4a`
- `sinf`内の`schm` scheme versionはいずれも`0x00010000`（1.0）

### cenc（Widevine）

| 項目 | 値 |
| --- | --- |
| HLS `METHOD` | `SAMPLE-AES-CTR` |
| `KEYFORMAT` | `urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed`（Widevine） |
| KEY URI | `data:` URI（base64）。復号すると46バイトの`pssh` boxで、system IDはWidevine、data部は14バイト |
| `IV`属性 | なし（サンプル毎IV） |
| `schm` scheme type | `cenc` |
| `tenc` | version 0、`isProtected=1`、`perSampleIvSize=16`、16バイトのdefault KIDあり |
| `pssh` | 初期化セグメント内に2個。いずれもWidevine system ID、data部14バイト |

つまり、映像・音声とも**CENC（AES-CTR、16バイトのサンプル毎IV）+ Widevine**である。

### cbcs（FairPlay）

| 項目 | 値 |
| --- | --- |
| HLS `METHOD` | `SAMPLE-AES` |
| `KEYFORMAT` | `com.apple.streamingkeydelivery` |
| KEY URI | `skd:` scheme |
| `IV`属性 | なし |
| `schm` scheme type | `cbcs` |
| `tenc` | version 1、`isProtected=1`、`perSampleIvSize=0`、16バイトのdefault KID、16バイトのconstant IVあり |
| `pssh` | なし |

つまり、映像・音声とも**CBCS（AES-CBC、constant IV + サブサンプル暗号化）+ FairPlay SPC**である。

`tenc`内のflags直後には2バイトのフィールドがあり、cencでは`0x0000`、cbcsでは`0x0019`だった。`0x19`はcrypt:skipパターン`1:9`（4bit+4bit）の表現と一致するが、仕様上の位置とは異なるため、ベンダー拡張の可能性を含めて「パターン関連フィールド」として記録する。

## 6.6 セグメントレベルの暗号化（senc/saiz/saio）

メディアセグメント（`.m4s`）の`moof`/`traf`内を解析した。トップレベルは`styp`+`sidx`+`moof`、`traf`内は`tfhd`/`tfdt`/`trun`/`saiz`/`saio`/`senc`。

### cenc（Widevine）

- `senc` flags=2（サブサンプルあり）、sampleCount=1
- サンプル毎IV: 16バイト（`tenc`の`perSampleIvSize=16`と一致）
- サブサンプル: clear 137バイト + protected 89,504バイト

### cbcs（FairPlay）

- `senc` flags=2、sampleCount=1
- サンプル毎IVなし（`tenc`のconstant IV 16バイトを使用）
- サブサンプル: clear 35バイト + protected 89,606バイト

つまり両方式とも**サブサンプル暗号化**（NALヘッダー等の平文部を残し、ペイロードのみ暗号化）が使われている。

## 6.7 コーデック設定（avcC / esds）

G1 `m3000`の初期化セグメントから復元した実コーデック設定:

| 項目 | 値 |
| --- | --- |
| 映像 | H.264 Main profile（`avcProfileIndication=0x4D`）、level 3.1（`0x1F`） |
| 解像度 | 1280×720（SPSから復元。`m3000`=720pのマッピングと一致） |
| NAL length size | 4バイト |
| 音声 | AAC-LC（audioObjectType=2）、48kHz、ステレオ |
| 音声ES最大ビットレート | 192,000（`am192`） |

## 6.8 字幕・DRCS・control.json

- 字幕プレイリスト: `https://simul2.hsk.st.nhk/npd4/vs1a/7fe0-0400/simul/subtl/playlist.m3u8`
- 字幕セグメントは**WebVTT**（`WEBVTT`ヘッダー、`.vtt`ファイル）。暗号化なし
- DRCS文字置換テーブル: `https://archive2.hsk.st.nhk/npd4/config/drcs-subst.json`（3,752エントリの`drcs`→`alternative`マッピング、約1.6MB）
- `control.json`: アプリは`videoinfo.json`の兄弟URLとして取得する（`ExoPlayerSurface.metadataUrlsFrom`が`cenc`/`cbcs`/`clear`セグメントまで遡って構築）。認証なしの直接取得は403

## 6.9 need_L1_hd と L3 フォールバック

`videoinfo.json`の`need_L1_hd=true`は、アプリ内で次のように使われる（`VideoInfo.selectL3FallbackUrl`）:

- `needL1HD`が真かつ選択ビットレートが1500超（720p/1080p）の場合、同じdrmType/codec/dynamicRangeで**1500以下（540p以下）の最上位マニフェスト**へフォールバックする
- つまりL3環境では540p以下、L1環境のみ720p/1080pが選択される

## 6.10 DVR・DASH・VODの状況

- DVR: ライブプレイリストは`?dvr=1`等でも同じローリングプレイリスト（4セグメント）を返す。アプリのDVR再生は`getDvrVideoForPublication`で番組のVOD形式ディスクリプタへ切り替える実装
- DASH: アプリコードに`.mpd`参照はなく、`.mpd`パスは403。このアプリはHLS専用
- VOD: 推測した固定パスは403だが、実URLはAPI応答の`detailedVideoDescriptor`フィールドから供給される。実アプリで取得したコンテンツ固有URLはHTTP 200で、descriptor・HLS・init segment・メディアセグメントを取得できた

## 10.6 動的再生の実測（アプリ実挙動）

日本経由でオンボーディングを完了し、ホーム画面のライブ再生を実行した。Fridaトレーサーが記録したアプリの実挙動:

| 項目 | 実測値 |
| --- | --- |
| ディスクリプタ取得 | `https://simul2.hsk.st.nhk/npd4/7fe0-0400/simul/videoinfo.json`（10回） |
| 選択された映像プレイリスト | `.../simul/cenc/v1500/playlist.m3u8`（540p） |
| 選択された音声プレイリスト | `.../simul/cenc/am192/playlist.m3u8` |
| その他の観測プレイリスト | `v0768`（360p）、`am064` |
| DRM | Widevine、`getKeyRequest`のsecurityLevel=`L3` |
| ライセンス要求 | `POST https://licence.hsk.st.nhk/widevine/license` |
| 認可トークン | `POST https://r.authz.ac1.nhk/idp/token`、`VideoTokenResult.getToken`発火 |
| メディアセグメント | `.m4s`を継続取得（再生成功） |

重要な確認: `need_L1_hd=true`の配信で、**L3エミュレーターではアプリが自動的にv1500（540p）を選択**した。1500超（720p/1080p）は選択されなかった。これは6.9のL3フォールバックロジックと動的に一致する。

## 10.7 VOD動的再生の実測

ホーム画面の「アイカタ〜大切な人の【イイところ】撮ってきてください〜」を開き、コンテンツ固有descriptorから暗号化VODを実再生した。

| 項目 | 実測値 |
| --- | --- |
| descriptor | `https://archive2.hsk.st.nhk/npd4/7fe0-0400/20260825/04b4-1-3-b3315424da822ead42c86ee037308b7b/videoinfo-73ffe87e7c0ca515aadbd4de22e6c3be.json`（HTTP 200） |
| descriptor構成 | 24 manifests（CENC 12 / CBCS 12）、`need_L1_hd=false`、`allow_multispeed=true` |
| 実選択 | CENC `manifest_m1500.m3u8`、映像`v1500`、音声`am192` |
| DRM | Widevine L3、`getKeyRequest`とライセンスPOSTを確認 |
| VOD時間 | 映像288セグメント、1,724.357秒、`#EXT-X-ENDLIST`あり |
| 字幕 | WebVTT 747セグメント、1,724.932秒 |
| サムネイル | `thumbnails.json`とサムネイル画像の取得を確認 |

CENC/CBCSの映像・音声について、init segmentはHEAD 200、Range GET 206、先頭メディアセグメントもRange GET 206だった。字幕の先頭VTTもHEAD 200 / Range GET 206で、`WEBVTT`ヘッダーを確認した。

VOD init segmentの構造:

- CENC: `schm=cenc`、`tenc` version 0、16-byte per-sample IV、Widevine PSSHを各init内に2個配置
- CBCS: `schm=cbcs`、`tenc` version 1、16-byte constant IV、PSSHなし
- 映像セグメント: CENC/CBCSとも`senc` flags=2でサブサンプル暗号化
- 音声セグメント: CENCは16-byte per-sample IV、CBCSはconstant IVを使用

実アプリはライセンスPOST後も暗号化された映像・音声セグメントを継続取得し、再生を継続した。機械可読結果は[`evidence/vod-dynamic-analysis.json`](../evidence/vod-dynamic-analysis.json)に収録している。

## 10.8 暗号化状態でのダウンロード再現

ターミナルから暗号化されたままのVODを取得できることを確認した。`scripts/05-download-encrypted-vod.py`はメディアプレイリストのinitセグメントと`.m4s`セグメントをそのまま保存し、任意で連結fMP4を出力する。

検証結果（CENC v1500、先頭2セグメント）:

- init: 987バイト、`ftyp`(iso5)、`encv`、`frma=avc1`、`schm=cenc`、`tenc`、Widevine PSSH×2
- セグメント: `styp`で開始、`moof`/`senc`を含む暗号化fMP4
- 連結ファイル: init + セグメントのバイト列をそのまま連結（暗号化状態を維持）

音声（`am192`）やCBCS経路（`cbcs/v1500`など）も同じ手順で取得できる。

字幕も同時に取得できる。`--subtitle-playlist`に字幕プレイリストURLを渡すとWebVTTセグメントを保存し、`--concat-subtitles`で1つの`.vtt`に結合する（先頭の`WEBVTT`ヘッダーは1つに揃え、各セグメントの`X-TIMESTAMP-MAP`を保持）。`--master`指定時はマスターの`#EXT-X-MEDIA:TYPE=SUBTITLES`から字幕プレイリストを自動検出して取得する。字幕セグメントは暗号化されていない。

## 7. DRM・認証フロー

静的解析で確認した処理:

1. `NVideoDescriptor.Manifest`から`url`, `drmType`, `bitrateLimitType`を選択する。
2. AndroidのDRM構成にはWidevine UUIDを設定する。
3. ライセンスURIとして次を使用する。

```text
https://licence.hsk.st.nhk/widevine/license
```

4. ライセンス要求ヘッダーに次を設定する。

```http
Authorization: Bearer <currentMainAccessToken>
```

5. 一部のコンテンツURLでは、`https://mediatoken.web.nhk/v1/token`から得た値を`hdnts`クエリパラメーターとして付与する。

```text
<content-url>?hdnts=<redacted>
```

ライブディスクリプタ／プレイリストのメタデータは、計測時には日本経由かつ追加Authorizationヘッダーなしでも取得できたが、実際のDRMライセンス処理には上記Bearer認証が実装されている。

## 8. Widevine CDM

Android 14エミュレーター上でプロパティのみ照会した。

| プロパティ | 値 |
| --- | --- |
| Security level | `L3` |
| Vendor | `com.google.android.widevine` |
| Description | `Widevine CDM` |
| CDM version | `18.0.0@340720000` |
| System ID | `28615` |
| Reported algorithms | `AES/CBC/NoPadding,HmacSHA256` |

アプリにはL1利用判定とL3への自動フォールバック処理がある。上記L3はエミュレーターの結果であり、物理端末の状態を示すものではない。

## 9. アプリの主要通信先

| 用途 | URL/ホスト |
| --- | --- |
| アプリ定数 | `https://api.web.nhk/r8/const.json` |
| 運用設定 | `https://apps.web.nhk/ops/api/v1/ProgAppAndroid/` |
| 共通配信設定 | `https://apps.web.nhk/ops/api/v1/common/configExt.json` |
| GeoIP | `https://location.tools.nhk/geoip/area.json` |
| ライブ配信 | `simul2.hsk.st.nhk` |
| VOD配信 | `archive2.hsk.st.nhk` |
| Widevineライセンス | `https://licence.hsk.st.nhk/widevine/license` |
| メディアトークン | `https://mediatoken.web.nhk/v1/token` |

## 10. 動的解析上の留保

日本経由でライブとVODの実再生を確認し、`MediaDrm.getKeyRequest`、WidevineライセンスPOST、暗号化セグメントの継続取得を記録した。ライセンスHTTPレスポンスコードと`provideKeyResponse()`完了イベントの専用計測は別途継続する。

VODはライブ用固定descriptorとは異なり、API応答の`detailedVideoDescriptor`からコンテンツ固有URLを得る。今回の1コンテンツのURL構造を全VODへ一般化しない。

## 10.5 メディアセグメントの取得可否

G1の`cenc`/`cbcs`それぞれの映像（v3000）と音声（am192）について、メディアプレイリストから最初のセグメントURLを取り出し、HEADと先頭1KBのRange GETのみで取得可否を確認した。

| 対象 | プレイリスト | HEAD | Range GET | 先頭box |
| --- | --- | --- | --- | --- |
| cenc 映像 | 200 | 200 | 206 | `styp`, `moof`, `sidx` |
| cenc 音声 | 200 | 200 | 206 | `styp`, `moof`, `sidx` |
| cbcs 映像 | 200 | 200 | 206 | `styp`, `moof`, `sidx` |
| cbcs 音声 | 200 | 200 | 206 | `styp`, `moof`, `sidx` |

つまり、セグメントURLは日本経路・追加認証ヘッダーなしで取得可能だった。先頭は`styp`/`moof`/`sidx`で始まる暗号化fMP4であり、平文の`ftyp`は含まれない。

機械可読結果は[`evidence/android-live-manifest-summary.json`](../evidence/android-live-manifest-summary.json)に収録した。

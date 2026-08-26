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

`m`と`s`の接頭辞の意味は、今回の計測だけでは断定していない。

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

日本経由でアプリの地域制限解除、番組API取得、ライブUI表示までは確認した。対象エミュレーターの今回の再生操作では、MediaDrmのライセンス要求完了までのイベントは記録されなかった。そのため、ライセンスURLとBearerヘッダーは静的コードからの確認、暗号化方式はHLSおよびfMP4 init segmentからの確認として区別している。

Web/VOD系はライブ用固定ディスクリプタとは異なり、コンテンツ固有の認可フローを持つため、今回確定したURL規則をそのままVODへ一般化しない。

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

# NHK ONE Widevine DRM Research

NHK ONE（旧 NHK プラス）の Widevine DRM 保護コンテンツに対する、オープンソースツールの動作検証実験の記録。

> **免責事項**: 本リポジトリは教育・研究目的の技術調査記録です。DRM 保護コンテンツの不正なダウンロード・復号・再配布は法律で禁止されています。

## 目的

- オープンソースの Widevine L3 ツール（yt-dlp / pywidevine など）が NHK ONE のコンテンツに対して実際に動作するかを検証する
- 検証は使い捨てサンドボックス環境（disposable Linux env）で実施
- 生成物は `/tmp` にのみ一時配置し、終了後に削除する

## 結論（サマリ）

| 項目 | 結果 |
| --- | --- |
| yt-dlp による NHK ONE ダウンロード | ❌ DRM 保護サイトとして拒否される |
| GeoIP 制限 | ✅ 日本国内 IP のみ許可。海外 IP は API が 403 |
| ストリーム情報 API へのアクセス | ✅ 日本プロキシ経由で `api.web.nhk/r8` にアクセス可能 |
| Widevine ライセンスサーバー特定 | ✅ `https://licence.hsk.st.nhk/widevine/license` |
| MPD マニフェスト URL の取得方法 | ✅ ストリーム情報 API の `manifests[0].url` から取得 |
| CDM デバイスファイル（.wvd） | ❌ 未取得（完全な復号には必須） |

## 検証環境

- 使い捨て Linux 環境（Ubuntu, Python 3.12）
- ツール: yt-dlp 2026.08.19, pywidevine（pip インストール）
- 日本プロキシ: 公開 SOCKS5/HTTP プロキシ（検証時点で稼働していたもの）

## 詳細

技術的な詳細は [docs/findings.md](docs/findings.md) を参照。

# NHK ONE Widevine Key Extractor (Chrome Extension)

## 概要

WVDファイルを読み込み、NHK ONEのエピソードページからWidevineコンテンツ鍵を自動取得するChrome拡張機能です。

pywidevineのCDMプロトコルを純粋なJavaScriptで実装し、ブラウザ内でライセンスチャレンジの生成・ライセンスの解析・コンテンツ鍵の復号を全て行います。

## 使い方

1. `chrome://extensions` を開き、「デベロッパー モード」を有効にする
2. 「パッケージ化されていない拡張機能を読み込む」で `chrome-extension/` フォルダを選択
3. NHK ONEのエピソードページ（`https://www.web.nhk/tv/pl/.../ep/...`）を開く
4. 拡張機能アイコンをクリック
5. `.wvd` ファイルを選択
6. 「鍵を取得」ボタンをクリック
7. KID:KEY のペアが表示される
8. 「鍵をダウンロード」でテキストファイルとして保存

## 必要なもの

- 有効な `.wvd` ファイル（Widevine L3 デバイスファイル）
- NHK ONEのアカウント（視聴権限のある状態）
- NHK ONEのエピソードページで再生を一度開始した状態（z_atクッキーが存在すること）

## ファイル構成

```
chrome-extension/
├── manifest.json     # MV3マニフェスト
├── background.js     # Service Worker（API解決・CDM・鍵取得）
├── popup.html        # ポップアップUI
├── popup.js          # ポップアップロジック
├── popup.css         # スタイル
└── lib/
    └── widevine.js   # Widevine CDM純JS実装
```

## 動作原理

1. `z_at`クッキーからBearerトークンを取得
2. NHK API (`api.web.nhk/r8`) でエピソードID → descriptor URL → マニフェストURLを解決
3. マスプレイリスト → 動画プレイリスト → 初期化セグメントURLを取得
4. 初期化セグメントからWidevine PSSHを抽出
5. WVDファイルからCDMを構築し、ライセンスチャレンジを生成
6. ライセンスサーバー (`licence.hsk.st.nhk`) にチャレンジを送信
7. ライセンスレスポンスを解析し、コンテンツ鍵を復号
8. KID:KEY形式で表示・ダウンロード

## 注意事項

- この拡張機能は教育・研究目的です
- 取得した鍵は個人的な復号用途にのみ使用してください
- WVDファイルは安全に管理してください

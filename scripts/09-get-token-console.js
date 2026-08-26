// NHK ONE Bearer トークン取得スニペット
//
// 使い方:
//   1. Chrome で NHK ONE のエピソードページ（https://www.web.nhk/...）を開き、再生ボタンを押す
//   2. F12 → Console タブを開き、このファイルの内容を全てペーストして Enter
//      （初めてペーストする場合は「allow pasting」と入力して許可が必要）
//   3. "Bearer eyJ..." 形式のトークンがクリップボードにコピーされ、有効期限が表示される
//   4. 08-web-decrypt-vod.js の --bearer-token にそのまま貼り付ける
//
// 仕組み: トークンは HttpOnly ではない z_at Cookie に格納されているため、
// document.cookie から直接読み取れる（2026-08-26 時点で実測確認済み）。

(function () {
  var m = document.cookie.match(/(?:^|;\s*)z_at=([^;]+)/);
  if (!m) {
    console.error('z_at cookie が見つかりません。www.web.nhk のエピソードページを開き、再生ボタンを押してから実行してください。');
    return;
  }
  var raw = decodeURIComponent(m[1]);
  var token = 'Bearer ' + raw;
  copy(token);
  console.log('✅ クリップボードにコピーしました: ' + token.slice(0, 30) + '...');
  try {
    var payload = JSON.parse(atob(raw.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    var remainMs = payload.exp * 1000 - Date.now();
    console.log('有効期限: ' + new Date(payload.exp * 1000).toLocaleString() +
      '（残り約 ' + Math.floor(remainMs / 3600000) + ' 時間 ' + Math.round((remainMs % 3600000) / 60000) + ' 分）');
  } catch (e) {}
  console.log('--bearer-token オプションにそのまま貼り付けてください');
})();

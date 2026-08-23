/* 投稿台 — Xへ流すための下ごしらえ
 *
 * XのAPIは2026年2月から従量課金だけになり、URLを含む投稿は1件$0.20。
 * 1日5件流すと月30ドルかかるので、APIは使わない。
 * ブラウザを自動操作して投稿するのは規約違反なので、それもやらない。
 *
 * 代わりに、Xの投稿画面を文面つきで開くリンク（intent）を並べる。
 * 押すと本文が入った状態でXが開くので、あとは投稿を押すだけ。
 * 費用はゼロ、規約の中、手間は1件2秒。
 *
 * 貼るのは楽天のアフィリエイトURLではなく、このサイトの商品ページ。
 * ・Xで露骨なアフィリエイトリンクを連投すると凍結の的になる
 * ・サイトに来てもらえば、他の商品も見てもらえる
 * ・商品ページのOGPが効くので、写真つきの大きなカードで表示される
 */
(function () {
  'use strict';

  var host = document.getElementById('postboard');
  if (!host) return;

  var DONE_KEY = 'yasumiru:posted';
  var SITE = host.getAttribute('data-url');
  var DAYS = 3;

  function doneRead() {
    try {
      var v = JSON.parse(localStorage.getItem(DONE_KEY) || '[]');
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }
  function doneWrite(ids) {
    try { localStorage.setItem(DONE_KEY, JSON.stringify(ids.slice(-400))); } catch (e) {}
  }

  function yen(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* Xの文字数は全角1文字が2、URLは長さに関わらず23として数えられる。
     上限280なので、日本語だけなら約128文字ぶんが本文に使える。 */
  function weight(text) {
    var n = 0;
    for (var i = 0; i < text.length; i++) {
      var c = text.charCodeAt(i);
      n += (c < 0x1100) ? 1 : 2;
    }
    return n;
  }

  function trim(text, budget) {
    if (weight(text) <= budget) return text;
    var out = '';
    for (var i = 0; i < text.length; i++) {
      if (weight(out + text[i]) > budget - 2) break;
      out += text[i];
    }
    return out + '…';
  }

  /* 投稿の本文を組み立てる。値段が主役なので、そこを2行目に置く。 */
  function compose(p) {
    var head = p.d ? '【' + p.d + '%OFF】' : '【特価】';
    var title = trim(p.t, 60);
    var price = p.d
      ? '¥' + yen(p.lp) + ' → ¥' + yen(p.pr)
      : '¥' + yen(p.pr);
    var cap = trim(p.cap || '', 96);

    var body = head + title + '\n' + price;
    if (cap) body += '\n\n' + cap;
    return body;
  }

  function intent(p) {
    var url = SITE + '/p/' + p.id + '/';
    return 'https://twitter.com/intent/tweet?text=' +
      encodeURIComponent(compose(p)) + '&url=' + encodeURIComponent(url);
  }

  /* 日付は日本時間で数える。toISOString() はUTCを返すので、
     そのまま使うと9時間ぶん境界がずれ、夜中に前日ぶんまで拾ってしまう。 */
  function jstDate(offsetDays) {
    var t = Date.now() + 9 * 3600000 + (offsetDays || 0) * 86400000;
    return new Date(t).toISOString().slice(0, 10);
  }

  function render(list) {
    var done = doneRead();
    var since = jstDate(-DAYS);

    var items = list
      .filter(function (p) { return (p.at || '').slice(0, 10) >= since; })
      .sort(function (a, b) { return (b.at || '').localeCompare(a.at || ''); });

    var todo = items.filter(function (p) { return done.indexOf(p.id) < 0; });

    document.getElementById('postCount').textContent = todo.length;

    if (!items.length) {
      host.innerHTML = '<div class="empty"><p class="empty-title">' +
        DAYS + '日以内に載った商品がありません</p>' +
        '<p>自動取得が新しい商品を見つけると、ここに並びます。</p></div>';
      return;
    }

    host.innerHTML = items.map(function (p) {
      var isDone = done.indexOf(p.id) >= 0;
      var text = compose(p);
      var w = weight(text) + 1 + 23;          // 本文 + 改行 + URL
      return '<article class="pb-item' + (isDone ? ' is-done' : '') + '">' +
        '<div class="pb-media"><img src="' + esc(p.img) + '" alt="" width="120" height="120" loading="lazy"></div>' +
        '<div class="pb-body">' +
          '<pre class="pb-text">' + esc(text) + '\n' + esc(SITE + '/p/' + p.id + '/') + '</pre>' +
          '<p class="pb-meta">' + w + ' / 280 文字' +
            (w > 280 ? '<b class="pb-over">　長すぎます</b>' : '') + '</p>' +
          '<div class="pb-btns">' +
            '<a class="btn btn-rakuten" href="' + intent(p) + '" target="_blank" rel="noopener" data-open="' + esc(p.id) + '">Xで開く</a>' +
            '<button class="btn btn-ghost" type="button" data-done="' + esc(p.id) + '">' +
              (isDone ? '投稿済みを取り消す' : '投稿した') + '</button>' +
            '<a class="btn btn-ghost" href="/p/' + esc(p.id) + '/" target="_blank" rel="noopener">商品ページ</a>' +
          '</div>' +
        '</div>' +
      '</article>';
    }).join('');
  }

  var all = [];

  host.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-done]');
    if (b) {
      var id = b.getAttribute('data-done');
      var done = doneRead();
      var i = done.indexOf(id);
      if (i >= 0) done.splice(i, 1); else done.push(id);
      doneWrite(done);
      render(all);
      return;
    }
    // Xを開いたら、投稿したものとして印を付ける。
    // 実際に投稿したかまでは分からないので、取り消せるようにしてある。
    var o = ev.target.closest('[data-open]');
    if (o) {
      var oid = o.getAttribute('data-open');
      var d2 = doneRead();
      if (d2.indexOf(oid) < 0) { d2.push(oid); doneWrite(d2); }
      setTimeout(function () { render(all); }, 400);
    }
  });

  fetch('/assets/data/feed.json')
    .then(function (r) { return r.json(); })
    .then(function (list) { all = list; render(all); })
    .catch(function () {
      host.innerHTML = '<div class="empty">' +
        '<p class="empty-title">商品データを読み込めませんでした</p>' +
        '<p>通信を確かめて、ページを再読み込みしてください。</p></div>';
      var c = document.getElementById('postCount');
      if (c) c.textContent = '?';
    });
})();

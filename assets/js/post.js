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
  /* 広告表記。楽天のガイドラインは「ファーストビューの上部」と定めており、
     下部への記載や大量ハッシュタグに埋もれた表記をNG例として挙げている。
     だから末尾のタグではなく、文頭のプレーンな文字として置く。 */
  var PR = host.getAttribute('data-pr') || '';
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

  /* 投稿の型。
   *
   * 同じ商品でも切り口を変えると、刺さる相手が変わる。
   * ただし書けるのは、手元のデータから事実として言えることだけ。
   *
   * 「実際に使ったら〇〇だった」「私も買う」のような体験談の型は
   * 意図的に入れていない。使っていない商品について書けば作り話になる。
   * 【PR】を付けて広告だと明示しているサイトが作り話をするのは筋が通らないし、
   * ステマ規制の観点でも危ない。実際に買って使ったときは、手で書けばいい。
   */
  function endsLabel(p) {
    var m = (p.et || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    return m ? (+m[2]) + '月' + (+m[3]) + '日 ' + m[4] + ':' + m[5] + 'まで' : '';
  }

  function variants(p) {
    var title = trim(p.t, 56);
    var cap = trim(p.cap || '', 92);
    var out = [];

    // ① 価格提示。いくらなのかを最初に見せる。
    out.push({
      name: '価格',
      body: (p.d ? '【' + p.d + '%OFF】' : '【特価】') + title +
            '\n' + (p.d ? '¥' + yen(p.lp) + ' → ¥' + yen(p.pr) : '¥' + yen(p.pr)) +
            (cap ? '\n\n' + cap : '')
    });

    // ② ビフォーアフター。値段の変化そのものを主役にする。
    if (p.d && p.lp) {
      out.push({
        name: '値段の変化',
        body: 'これ、少し前まで' + yen(p.lp) + '円でした。\n' +
              'いまは' + yen(p.pr) + '円です。\n\n' +
              title + (cap ? '\n' + cap : '')
      });
    }

    // ③ 今買う理由。期限があるものだけ。無いのに煽らない。
    var ends = endsLabel(p);
    if (ends) {
      out.push({
        name: '期限',
        body: title + '\n' +
              '¥' + yen(p.pr) + (p.d ? '（' + p.d + '%OFF）' : '') + '\n\n' +
              'この値段は' + ends + '。\n' +
              '過ぎると戻ります。'
      });
    }

    // ④ 実績。レビュー数は自分の感想ではなく、他人が残した事実。
    if ((p.rc || 0) >= 10000) {
      out.push({
        name: 'レビュー',
        body: 'レビュー' + yen(p.rc) + '件。\n' +
              title + '\n' +
              '¥' + yen(p.pr) + (p.d ? '（' + p.d + '%OFF）' : '') +
              (cap ? '\n\n' + cap : '')
      });
    }

    // ⑤ 用途。何に使うものかを先に言う。値段はそのあと。
    if (p.pt && p.pt.length) {
      out.push({
        name: '用途',
        body: title + '\n\n' +
              p.pt.slice(0, 3).map(function (x) { return '・' + x; }).join('\n') +
              '\n\n¥' + yen(p.pr) + (p.d ? '（' + p.d + '%OFF）' : '')
      });
    }

    return out.map(function (v) { return { name: v.name, body: PR + v.body }; });
  }

  /* セール前の商品の型。
   *
   * 通常の型は「いま◯円です」と書くが、開始前の商品にそれを使うと嘘になる。
   * まだ買えないので、書けるのは「いつから、いくらになるか」だけ。
   * 在庫数や「売り切れ必至」は手元のデータに無いので書かない。
   */
  function soonVariants(p) {
    var title = trim(p.t, 54);
    var cap = trim(p.cap || '', 88);
    var when = p.sl || '';                       // 「今夜 20:00 から」
    var out = [];

    // ① 予告。いつ・いくらになるかだけを言う。
    out.push({
      name: '予告',
      body: '【' + when + '】\n' + title + '\n' +
            (p.d ? '¥' + yen(p.lp) + ' → ¥' + yen(p.pr) : '¥' + yen(p.pr)) +
            (cap ? '\n\n' + cap : '')
    });

    // ② カウントダウン。開始時刻を主役にする。
    out.push({
      name: '開始時刻',
      body: when + '、この値段になります。\n\n' +
            title + '\n' +
            '¥' + yen(p.pr) + (p.d ? '（' + p.d + '%OFF）' : '') + '\n\n' +
            'それまではまだ買えません。'
    });

    // ③ 買いまわり向け。1ショップ1,000円のカウントに使える価格帯のときだけ。
    //   1,000円に届かないものを「買いまわりに使える」と書くと嘘になる。
    if (p.pr >= 1000) {
      out.push({
        name: '買いまわり',
        body: '買いまわりの1件に。\n' +
              title + '\n' +
              '¥' + yen(p.pr) + '（' + when + '）' +
              (cap ? '\n\n' + cap : '')
      });
    }

    // ④ 実績。レビュー数は他人が残した事実。
    if ((p.rc || 0) >= 1000) {
      out.push({
        name: 'レビュー',
        body: 'レビュー' + yen(p.rc) + '件の商品が、' + when + '¥' + yen(p.pr) +
              (p.d ? '（' + p.d + '%OFF）' : '') + '。\n\n' + title
      });
    }

    return out.map(function (v) { return { name: v.name, body: PR + v.body }; });
  }

  /* 締めの一言。付けるかどうかは投稿する人が選ぶ。 */
  var TAILS = [
    { name: 'なし', text: '' },
    { name: '保存', text: '\n\nあとで見返せるように保存しておくと楽です。' },
    { name: 'コメント', text: '\n\n使ったことある人いますか？' }
  ];

  /* どの型を使うか。開始前の商品かどうかで変わる。
     選び方は1か所にまとめておく。2か所で分岐すると片方を直し忘れる。 */
  function variantsFor(p) {
    return p.sl ? soonVariants(p) : variants(p);
  }

  function compose(p, vi, ti) {
    var vs = variantsFor(p);
    var v = vs[Math.min(vi || 0, vs.length - 1)];
    return v.body + (TAILS[ti || 0] || TAILS[0]).text;
  }

  function intent(p, vi, ti) {
    var url = SITE + '/p/' + p.id + '/';
    return 'https://twitter.com/intent/tweet?text=' +
      encodeURIComponent(compose(p, vi, ti)) + '&url=' + encodeURIComponent(url);
  }

  /* Threadsの投稿画面を文面つきで開く。
     Xと違い本文とURLを分けて渡せないので、末尾に付けて1本の text にする。

     楽天はThreadsを認定SNSに入れているので、貼ること自体に問題はない。
     ただし貼るのは楽天のアフィリエイトURLではなく、このサイトの商品ページ。
     アフィリエイトリンクは登録済みの自サイト側にあり、
     投稿そのものにはアフィリエイトリンクが含まれない形にしておく。 */
  function threadsIntent(p, vi, ti) {
    var url = SITE + '/p/' + p.id + '/';
    return 'https://www.threads.net/intent/post?text=' +
      encodeURIComponent(compose(p, vi, ti) + '\n' + url);
  }

  // どの型・どの締めを選んでいるかを商品ごとに覚えておく
  var pick = {};

  /* 日付は日本時間で数える。toISOString() はUTCを返すので、
     そのまま使うと9時間ぶん境界がずれ、夜中に前日ぶんまで拾ってしまう。 */
  function jstDate(offsetDays) {
    var t = Date.now() + 9 * 3600000 + (offsetDays || 0) * 86400000;
    return new Date(t).toISOString().slice(0, 10);
  }

  function render() {
    var done = doneRead();
    var since = jstDate(-DAYS);

    var normal = all
      .filter(function (p) { return (p.at || '').slice(0, 10) >= since; })
      .sort(function (a, b) { return (b.at || '').localeCompare(a.at || ''); });

    // セール前の商品は開始時刻の早い順。今夜始まるものから流したい。
    var sale = soon.slice().sort(function (a, b) {
      return (a.st || '').localeCompare(b.st || '');
    });

    var todo = normal.concat(sale)
      .filter(function (p) { return done.indexOf(p.id) < 0; });
    document.getElementById('postCount').textContent = todo.length;

    function column(items, key, head, note, empty) {
      var n = items.filter(function (p) { return done.indexOf(p.id) < 0; }).length;
      var body = items.length
        ? items.map(item).join('')
        : '<div class="empty"><p class="empty-title">' + empty + '</p></div>';
      return '<section class="pb-col pb-col-' + key + '">' +
        '<div class="pb-col-head">' +
          '<h2 class="pb-col-title">' + esc(head) + '<b>' + n + '</b></h2>' +
          '<p class="pb-col-note">' + esc(note) + '</p>' +
        '</div>' + body + '</section>';
    }

    // 画面が狭いときは2列に並べられない。縦に積むと60件を通り過ぎないと
    // もう片方に届かないので、狭いときだけタブで切り替える。
    // 広い画面では両方見えているので、タブは出さない。
    function tab(key, label, n) {
      return '<button class="pb-switch-btn' + (show === key ? ' is-on' : '') +
        '" type="button" data-show="' + key + '">' + esc(label) +
        '<b>' + n + '</b></button>';
    }
    var nUndone = function (items) {
      return items.filter(function (p) { return done.indexOf(p.id) < 0; }).length;
    };

    host.innerHTML =
      '<div class="pb-switch">' +
        tab('normal', '通常', nUndone(normal)) +
        tab('sale', 'セール・マラソン', nUndone(sale)) +
      '</div>' +
      '<div class="pb-cols" data-show="' + esc(show) + '">' +
      column(normal, 'normal', '通常の投稿',
             '直近' + DAYS + '日に載った、いま買える商品です。',
             DAYS + '日以内に載った商品がありません') +
      column(sale, 'sale', 'セール・マラソン用',
             'まだ始まっていない商品です。開始前に告知として流せます。',
             'いま開始待ちの商品はありません') +
      '</div>';

    function item(p) {
      var isDone = done.indexOf(p.id) >= 0;
      var sel = pick[p.id] || { v: 0, t: 0 };
      var vs = variantsFor(p);
      var text = compose(p, sel.v, sel.t);
      var w = weight(text) + 1 + 23;          // Xは本文 + 改行 + URL(23固定)
      // Threadsは500字まで。URLも実際の長さで数えられ、全角も1字。
      var tw = (text + '\n' + SITE + '/p/' + p.id + '/').length;

      var tabs = vs.map(function (v, i) {
        return '<button class="pb-tab' + (i === sel.v ? ' is-on' : '') +
          '" type="button" data-v="' + esc(p.id) + ':' + i + '">' + esc(v.name) + '</button>';
      }).join('');
      var tails = TAILS.map(function (t, i) {
        return '<button class="pb-tail' + (i === sel.t ? ' is-on' : '') +
          '" type="button" data-t="' + esc(p.id) + ':' + i + '">' + esc(t.name) + '</button>';
      }).join('');

      return '<article class="pb-item' + (isDone ? ' is-done' : '') + '">' +
        '<div class="pb-media"><img src="' + esc(p.img) + '" alt="" width="120" height="120" loading="lazy"></div>' +
        '<div class="pb-body">' +
          '<div class="pb-tabs"><span class="pb-tabs-label">型</span>' + tabs + '</div>' +
          '<pre class="pb-text">' + esc(text) + '\n' + esc(SITE + '/p/' + p.id + '/') + '</pre>' +
          '<div class="pb-tabs"><span class="pb-tabs-label">締め</span>' + tails + '</div>' +
          '<p class="pb-meta">' +
            'X ' + w + '/280' + (w > 280 ? '<b class="pb-over">超過</b>' : '') +
            '　Threads ' + tw + '/500' + (tw > 500 ? '<b class="pb-over">超過</b>' : '') +
          '</p>' +
          '<div class="pb-btns">' +
            '<a class="btn btn-rakuten" href="' + intent(p, sel.v, sel.t) + '" target="_blank" rel="noopener" data-open="' + esc(p.id) + '">Xで開く</a>' +
            '<a class="btn btn-threads" href="' + threadsIntent(p, sel.v, sel.t) + '" target="_blank" rel="noopener" data-open="' + esc(p.id) + '">Threadsで開く</a>' +
            '<button class="btn btn-ghost" type="button" data-copy>コピー</button>' +
            '<button class="btn btn-ghost" type="button" data-done="' + esc(p.id) + '">' +
              (isDone ? '投稿済みを取り消す' : '投稿した') + '</button>' +
            '<a class="btn btn-ghost" href="/p/' + esc(p.id) + '/" target="_blank" rel="noopener">商品ページ</a>' +
          '</div>' +
        '</div>' +
      '</article>';
    }
  }

  var all = [];
  var soon = [];
  var show = 'normal';   // 狭い画面でどちらの列を出しているか

  host.addEventListener('click', function (ev) {
    var sw = ev.target.closest('[data-show]');
    if (sw && sw.classList.contains('pb-switch-btn')) {
      show = sw.getAttribute('data-show');
      render();
      host.scrollIntoView({ block: 'start' });
      return;
    }
    var tv = ev.target.closest('[data-v]');
    if (tv) {
      var a = tv.getAttribute('data-v').split(':');
      pick[a[0]] = pick[a[0]] || { v: 0, t: 0 };
      pick[a[0]].v = +a[1];
      render();
      return;
    }
    var tt = ev.target.closest('[data-t]');
    if (tt) {
      var b2 = tt.getAttribute('data-t').split(':');
      pick[b2[0]] = pick[b2[0]] || { v: 0, t: 0 };
      pick[b2[0]].t = +b2[1];
      render();
      return;
    }
    var b = ev.target.closest('[data-done]');
    if (b) {
      var id = b.getAttribute('data-done');
      var done = doneRead();
      var i = done.indexOf(id);
      if (i >= 0) done.splice(i, 1); else done.push(id);
      doneWrite(done);
      render();
      return;
    }
    // Xを開いたら、投稿したものとして印を付ける。
    // 実際に投稿したかまでは分からないので、取り消せるようにしてある。
    var o = ev.target.closest('[data-open]');
    if (o) {
      var oid = o.getAttribute('data-open');
      var d2 = doneRead();
      if (d2.indexOf(oid) < 0) { d2.push(oid); doneWrite(d2); }
      setTimeout(function () { render(); }, 400);
    }
  });

  /* 投稿文をその場でコピーできるようにする。
     Xを開かずに、Threadsや他の場所へ貼りたいことがある。

     押されたボタンの近くにある <pre> の中身をそのまま渡す。
     画面に見えているものと、コピーされるものを必ず一致させたいので、
     文面を組み直さずにDOMから取る。

     clipboard は安全な文脈（https か localhost）でしか使えないので、
     使えないときは選択状態にして、手でコピーしてもらう。 */
  function copyFrom(btn) {
    var box = btn.closest('.pb-item, .pb-link');
    var pre = box && box.querySelector('.pb-text');
    if (!pre) return;
    var text = pre.textContent;
    var done = function () {
      var was = btn.textContent;
      btn.textContent = 'コピーしました';
      btn.classList.add('is-copied');
      setTimeout(function () {
        btn.textContent = was;
        btn.classList.remove('is-copied');
      }, 1600);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { select(pre, btn, done); });
    } else {
      select(pre, btn, done);
    }
  }

  /* clipboard が使えないときの代わり。
     文面を選んだうえで、古い execCommand でのコピーを試す。
     これも駄目なら、選ばれた状態は残るので手でコピーできる。
     「押したのに何も起きない」が一番困る。 */
  function select(pre, btn, done) {
    var r = document.createRange();
    r.selectNodeContents(pre);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    if (ok) {
      sel.removeAllRanges();
      done();
      return;
    }
    btn.textContent = '選択しました。コピーしてください';
    setTimeout(function () { btn.textContent = 'コピー'; }, 2600);
  }

  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-copy]');
    if (b) copyFrom(b);
  });

  Promise.all([
    fetch('/assets/data/feed.json').then(function (r) { return r.json(); }),
    // 開始前の商品。読者向けの画面では読み込まない。
    fetch('/assets/data/soon.json').then(function (r) { return r.json(); })
                                   .catch(function () { return []; })
  ])
    .then(function (d) { all = d[0]; soon = d[1] || []; render(); })
    .catch(function () {
      host.innerHTML = '<div class="empty">' +
        '<p class="empty-title">商品データを読み込めませんでした</p>' +
        '<p>通信を確かめて、ページを再読み込みしてください。</p></div>';
      var c = document.getElementById('postCount');
      if (c) c.textContent = '?';
    });
})();

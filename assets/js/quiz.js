/* 「これ、いくら？」— 値段あてクイズ
 *
 * 商品の写真と名前だけを見せて、値段を当ててもらう。
 * このサイトのテーマは「安さを見抜く」なので、遊びも同じ筋にしてある。
 *
 * データは商品一覧と同じ /assets/data/feed.json を使う。専用の配管は持たない。
 */
(function () {
  'use strict';

  var ROUND = 5;                    // 1セットの問題数
  var BEST_KEY = 'yasumiru:quiz:best';

  var host = document.getElementById('quiz');
  if (!host) return;

  var pool = [];                    // 出題できる商品
  var quiz = [];                    // 今回の5問
  var at = 0;                       // いま何問目か
  var hits = 0;                     // 正解数

  // ------------------------------------------------------------ 下ごしらえ

  /* 値段が文章に書いてある商品は出題しない。答えが見えてしまう。
     商品名は「1000円ポッキリ」「3,990円→2990円」のように
     値段を含むことがよくある。キャプションはほぼ必ず値段を書いている。 */
  function fair(p) {
    if (!p.img || !p.t || !p.pr) return false;
    if (p.pr < 300 || p.pr > 20000) return false;
    return !/[0-9０-９][\s,，]*[0-9０-９]*\s*(円|¥|￥)/.test(p.t);
  }

  function shuffle(a) {
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  /* 選択肢に出しても不自然でない値段に丸める。
     1,853円のような半端な数字が並ぶと、正解だけ生活感があって見破られる。 */
  function tidy(n) {
    if (n < 1000) return Math.max(200, Math.round(n / 10) * 10);
    if (n < 10000) return Math.round(n / 10) * 10;
    return Math.round(n / 100) * 100;
  }

  /* 正解の前後に散らした選択肢を3つ作る。
     倍率を上下から混ぜて選ぶので、正解が毎回いちばん安い、
     という当てずっぽうが効かないようにしている。 */
  function choicesFor(price) {
    var low = shuffle([0.4, 0.5, 0.62, 0.75]);
    var high = shuffle([1.35, 1.6, 2.0, 2.6]);
    var mix = shuffle([low[0], low[1], high[0], high[1]]).slice(0, 3);
    var out = [price];
    for (var i = 0; i < mix.length; i++) {
      var v = tidy(price * mix[i]);
      var guard = 0;
      while (out.indexOf(v) !== -1 && guard < 12) {
        v = tidy(v * (Math.random() < 0.5 ? 0.88 : 1.14));
        guard++;
      }
      if (out.indexOf(v) === -1) out.push(v);
    }
    // 万一そろわなかったぶんを埋める
    while (out.length < 4) out.push(tidy(price * (1.9 + out.length * 0.4)));
    return shuffle(out);
  }

  function yen(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

  function ic(name, cls) {
    return '<svg class="' + (cls || 'ic') + '" aria-hidden="true" focusable="false">' +
      '<use href="' + host.getAttribute('data-sprite') + '#ic-' + name + '"></use></svg>';
  }

  // ------------------------------------------------------------ 画面

  function question() {
    var p = quiz[at];
    var opts = choicesFor(p.pr).map(function (v) {
      return '<button class="q-opt" type="button" data-v="' + v + '">' +
        '<i>¥</i>' + yen(v) + '</button>';
    }).join('');

    host.innerHTML =
      '<div class="q-card">' +
        '<div class="q-step">' +
          '<span class="q-step-no">' + (at + 1) + ' / ' + ROUND + '</span>' +
          '<span class="q-step-bar"><i style="width:' + (at / ROUND * 100) + '%"></i></span>' +
        '</div>' +
        '<div class="q-photo"><img src="' + p.img + '" alt="" width="420" height="420">' +
          '<span class="q-mark">' + ic('quiz', 'ic-qmark') + '</span>' +
        '</div>' +
        '<p class="q-shop">' + esc(p.cl || '') + '</p>' +
        '<h2 class="q-title">' + esc(p.t) + '</h2>' +
        '<p class="q-ask">これ、いくら？</p>' +
        '<div class="q-opts">' + opts + '</div>' +
      '</div>';

    Array.prototype.forEach.call(host.querySelectorAll('.q-opt'), function (b) {
      b.addEventListener('click', function () { answer(parseInt(b.getAttribute('data-v'), 10), b); });
    });
  }

  function answer(picked, btn) {
    var p = quiz[at];
    var right = picked === p.pr;
    if (right) hits++;

    Array.prototype.forEach.call(host.querySelectorAll('.q-opt'), function (b) {
      b.disabled = true;
      var v = parseInt(b.getAttribute('data-v'), 10);
      if (v === p.pr) b.classList.add('is-right');
    });
    if (!right) btn.classList.add('is-wrong');

    // ズレは％ではなく金額で伝える。値段のサイトなので円のほうが体感に近いし、
    // 「100%ちがい」のような、読んで意味を取りにくい言い方にもならない。
    var gap = Math.abs(picked - p.pr);
    var verdict = right
      ? '<span class="q-verdict is-hit">正解</span>'
      : '<span class="q-verdict is-miss">はずれ</span>' +
        '<span class="q-gap">実際より <b>¥' + yen(gap) + '</b> ' +
        (picked > p.pr ? '高く' : '安く') + '見ていました</span>';

    var last = at === ROUND - 1;
    host.querySelector('.q-card').insertAdjacentHTML('beforeend',
      '<div class="q-reveal">' +
        verdict +
        '<p class="q-real">正解は <b><i>¥</i>' + yen(p.pr) + '</b></p>' +
        (p.cap ? '<p class="q-cap">' + esc(p.cap) + '</p>' : '') +
        '<div class="q-reveal-btns">' +
          '<a class="btn btn-ghost" href="/p/' + p.id + '/">この商品を見る</a>' +
          '<button class="btn btn-rakuten" type="button" id="qNext">' +
            (last ? '結果を見る' : '次の問題') + ic('arrow-right', 'ic-arrow') +
          '</button>' +
        '</div>' +
      '</div>');

    host.querySelector('.q-reveal').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    document.getElementById('qNext').addEventListener('click', function () {
      at++;
      if (at >= ROUND) result(); else question();
      window.scrollTo({ top: host.offsetTop - 70, behavior: 'smooth' });
    });
  }

  function result() {
    var best = parseInt(localStorage.getItem(BEST_KEY) || '0', 10);
    var renewed = hits > best;
    if (renewed) { try { localStorage.setItem(BEST_KEY, String(hits)); } catch (e) {} }

    var word = ['相場、いっしょに覚えていきましょう',
                'まだまだ伸びしろがあります',
                'ふつうの感覚です',
                'なかなかの目利きです',
                'かなり分かっています',
                '完璧です。もう騙されません'][hits];

    var text = 'ヤスミルの「これ、いくら？」で' + ROUND + '問中' + hits + '問正解。' +
               '楽天の相場、意外と分からない。';
    var url = host.getAttribute('data-url') + '/quiz/';
    var q = encodeURIComponent;

    host.innerHTML =
      '<div class="q-card q-result">' +
        '<p class="q-result-eyebrow">' + ic('trophy', 'ic-trophy') + '結果</p>' +
        '<p class="q-score"><b>' + hits + '</b><span>/ ' + ROUND + '問正解</span></p>' +
        '<p class="q-word">' + word + '</p>' +
        (renewed && hits > 0 ? '<p class="q-best">自己最高記録を更新しました</p>'
                             : '<p class="q-best">これまでの最高は ' + Math.max(best, hits) + '問</p>') +
        '<div class="q-share">' +
          '<p class="q-share-head">結果を見せる</p>' +
          '<div class="share-row">' +
            '<a class="share-btn share-x" target="_blank" rel="noopener nofollow" href="' +
              'https://twitter.com/intent/tweet?text=' + q(text) + '&url=' + q(url) + '">' +
              ic('sns-x') + '<span>X</span></a>' +
            '<a class="share-btn share-threads" target="_blank" rel="noopener nofollow" href="' +
              'https://www.threads.net/intent/post?text=' + q(text + '\n' + url) + '">' +
              ic('sns-threads') + '<span>Threads</span></a>' +
            '<a class="share-btn share-line" target="_blank" rel="noopener nofollow" href="' +
              'https://social-plugins.line.me/lineit/share?url=' + q(url) + '&text=' + q(text) + '">' +
              ic('sns-line') + '<span>LINE</span></a>' +
          '</div>' +
        '</div>' +
        '<div class="q-again">' +
          '<button class="btn btn-rakuten btn-lg btn-block" type="button" id="qAgain">' +
            ic('again', 'ic-again') + 'もう一度あそぶ</button>' +
          '<a class="btn btn-ghost btn-block" href="/">特価をぜんぶ見る</a>' +
        '</div>' +
      '</div>';

    document.getElementById('qAgain').addEventListener('click', start);
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function start() {
    at = 0; hits = 0;
    quiz = shuffle(pool.slice()).slice(0, ROUND);
    question();
    window.scrollTo({ top: host.offsetTop - 70, behavior: 'smooth' });
  }

  // ------------------------------------------------------------ 起動

  fetch('/assets/data/feed.json')
    .then(function (r) { return r.json(); })
    .then(function (list) {
      pool = list.filter(fair);
      if (pool.length < ROUND) {
        host.innerHTML = '<div class="q-card"><p class="q-ask">いま出題できる商品が足りません。' +
                         'しばらくしてからお越しください。</p></div>';
        return;
      }
      start();
    })
    .catch(function () {
      host.innerHTML = '<div class="q-card"><p class="q-ask">読み込みに失敗しました。' +
                       '通信状況をご確認のうえ、再読み込みしてください。</p></div>';
    });
})();

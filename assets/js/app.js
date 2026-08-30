/* ヤスミル ── フィードの操作まわり */
(function () {
  'use strict';

  var yen = function (n) { return Number(n).toLocaleString('ja-JP'); };
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };

  /* ------------------------------------------------ ドロワー / 検索シート */
  function bindSheet(triggerId, sheetId, closeId) {
    var trigger = document.getElementById(triggerId);
    var sheet = document.getElementById(sheetId);
    if (!trigger || !sheet) return;

    var open = function (state) {
      sheet.dataset.open = state ? 'true' : 'false';
      trigger.setAttribute('aria-expanded', state ? 'true' : 'false');
      document.body.style.overflow = state ? 'hidden' : '';
      if (state) {
        var focusable = sheet.querySelector('input, button, a');
        if (focusable) focusable.focus();
      }
    };

    trigger.addEventListener('click', function () {
      open(sheet.dataset.open !== 'true');
    });
    sheet.addEventListener('click', function (ev) {
      if (ev.target === sheet) open(false);
    });
    var closeBtn = closeId && document.getElementById(closeId);
    if (closeBtn) closeBtn.addEventListener('click', function () { open(false); });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && sheet.dataset.open === 'true') { open(false); trigger.focus(); }
    });
  }

  bindSheet('menuBtn', 'drawer', 'drawerClose');
  bindSheet('searchBtn', 'searchSheet', null);

  /* ------------------------------------------------ 検索フォーム */
  var searchForm = document.getElementById('searchForm');
  if (searchForm) {
    searchForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var q = (document.getElementById('searchInput').value || '').trim();
      if (!q) return;
      window.location.href = '/?q=' + encodeURIComponent(q);
    });
  }

  /* ------------------------------------------------ 買い物メモ
     チェックリストは買う直前に使う道具なので、状態を端末に残す。
     ページを閉じて楽天で買い物してから戻ってきても消えない。 */
  (function checklist() {
    var lists = document.querySelectorAll('[data-checklist]');
    if (!lists.length) return;
    var key = 'yasumiru:check:' + location.pathname;
    var saved = {};
    try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch (e) { saved = {}; }

    var boxes = document.querySelectorAll('[data-check]');
    Array.prototype.forEach.call(boxes, function (b) {
      if (saved[b.dataset.check]) b.checked = true;
      b.addEventListener('change', function () {
        saved[b.dataset.check] = b.checked;
        try { localStorage.setItem(key, JSON.stringify(saved)); } catch (e) { /* 保存できなくても動く */ }
      });
    });

    var reset = document.querySelector('[data-check-reset]');
    if (reset) {
      reset.addEventListener('click', function () {
        Array.prototype.forEach.call(boxes, function (b) { b.checked = false; });
        saved = {};
        try { localStorage.removeItem(key); } catch (e) { /* noop */ }
      });
    }
  })();

  /* ------------------------------------------------ サイドバーの追従
     サイドバーが画面より高いときは top に負の値を入れる。
     こうするとスクロールに合わせて普通に上がってきて、
     下端が画面の底に来たところで止まる（全部見えて、そのあと残る）。
     収まるときは素直に上端へ貼り付ける。 */
  (function stickyRail() {
    var rail = document.querySelector('.layout-side');
    if (!rail) return;
    var GAP = 20;

    function fit() {
      if (window.innerWidth < 1000) { rail.style.top = ''; return; }
      var headerH = parseInt(
        getComputedStyle(document.documentElement).getPropertyValue('--header-h'), 10) || 80;
      var topWhenFits = headerH + 18;
      var h = rail.offsetHeight;
      var avail = window.innerHeight - topWhenFits - GAP;
      rail.style.top = (h > avail)
        ? (window.innerHeight - h - GAP) + 'px'
        : topWhenFits + 'px';
    }

    fit();
    window.addEventListener('resize', fit);
    window.addEventListener('load', fit);
    if (window.ResizeObserver) new ResizeObserver(fit).observe(rail);
  })();

  // ------------------------------------------------------ 気になるリスト
  // 会員登録を作らずに保存できるようにする。保存先はこの端末のブラウザだけで、
  // サーバーには何も送らない。
  //
  // 押した数を集計して見せる「いいね」は作らない。
  // サーバーが無い以上、数はでっち上げるしかなく、それは嘘になる。
  // ハートは1つだけ置いて、押した結果は自分のリストに溜まる形にしてある。
  var WATCH_KEY = 'yasumiru:watch';

  function watchRead() {
    try {
      var v = JSON.parse(localStorage.getItem(WATCH_KEY) || '[]');
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }

  function watchWrite(ids) {
    try { localStorage.setItem(WATCH_KEY, JSON.stringify(ids)); } catch (e) {}
    paintWatchCount();
  }

  function paintWatchCount() {
    var n = watchRead().length;
    Array.prototype.forEach.call(document.querySelectorAll('[data-watch-count]'), function (el) {
      el.textContent = n;
      el.hidden = n === 0;
    });
  }

  /* ボタンの見た目を、保存されているかどうかに合わせる */
  function paintWatchButtons(root) {
    var ids = watchRead();
    var btns = (root || document).querySelectorAll('[data-watch]');
    Array.prototype.forEach.call(btns, function (b) {
      var on = ids.indexOf(b.getAttribute('data-watch')) >= 0;
      b.setAttribute('aria-pressed', String(on));
      b.classList.toggle('is-on', on);
      var lab = b.querySelector('.watch-label');
      if (lab) lab.textContent = on ? 'キープ中！' : (b.classList.contains('watch-detail')
        ? 'あとで見る' : '気になる');
    });
  }

  // 押した本人にだけ効くので、クリックは1か所でまとめて拾う
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest && ev.target.closest('[data-watch]');
    if (!btn) return;
    ev.preventDefault();
    var id = btn.getAttribute('data-watch');
    var ids = watchRead();
    var i = ids.indexOf(id);
    if (i >= 0) ids.splice(i, 1); else ids.unshift(id);   // 新しく押したものが先頭
    watchWrite(ids);
    paintWatchButtons();
    btn.classList.remove('is-pop');
    void btn.offsetWidth;                                  // アニメを繰り返せるようにする
    if (i < 0) btn.classList.add('is-pop');
    if (watchFeed) renderWatch();
  });

  var watchFeed = document.getElementById('watchFeed');
  paintWatchCount();
  paintWatchButtons();

  // ------------------------------------------------------ 終わったセール
  // ビルド時にも外しているが、それだけでは足りない。
  // 取得は1日5回なので、朝の取得のあとに終わるセールは
  // 次のビルドまで最大6時間、終わったまま並び続ける。
  // 読む人の時計で判定して、その場で消す。
  // いつ載せたか。値段は毎日動くので、「いつの話か」が要る。
  function postedLabel(p) {
    var at = (p && p.at) || '';
    var m = at.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return '';
    return '<span class="card-posted"><time datetime="' + at.slice(0, 10) + '">'
      + (+m[2]) + '/' + (+m[3]) + ' 掲載</time></span>';
  }

  function saleOver(p) {
    var end = (p && p.et) || '';
    if (!end) return false;
    // 「2026-08-24 09:59」を日本時間として読む
    var m = end.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (!m) return false;
    var t = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) - 9 * 3600000;
    return Date.now() > t;
  }

  // ---------------------------------------------------------------- マーカー
  // キャプションのマーカーは、画面に入ったところで引きはじめる。
  // 最初から引かれていると、ただの装飾になって目に留まらない。
  var drawer = ('IntersectionObserver' in window)
    ? new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          en.target.classList.add('is-drawn');
          drawer.unobserve(en.target);      // 一度引いたら、もう見張らない
        });
      }, { rootMargin: '0px 0px -12% 0px', threshold: .35 })
    : null;

  function drawMarkers(root) {
    var caps = (root || document).querySelectorAll('.card-cap, .detail-cap');
    Array.prototype.forEach.call(caps, function (el) {
      if (el.classList.contains('is-drawn')) return;
      // 見張れない環境では、引いた状態で置いておく。線が出ないよりはいい。
      if (drawer) drawer.observe(el); else el.classList.add('is-drawn');
    });
  }
  drawMarkers();

  /* ------------------------------------------------ フィード */
  var feed = document.getElementById('feed');
  // 気になるリストのページには #feed が無いが、
  // カードを描く関数はこの先で定義しているので、そこまでは通す。
  if (!feed && !watchFeed) return;

  var state = { all: null, view: [], page: 1, per: 10, sort: 'new', q: '', pmin: 0, pmax: Infinity };
  var countEl = document.getElementById('resultCount');
  var sorter = document.querySelector('.sorter');

  function pricetag(p) {
    var sub = '';
    if (p.u) sub += '<span class="pricetag-unit">' + esc(p.u) + '</span>';
    if (p.d) sub += '<span class="pricetag-was">' + esc(p.b || '通常') + ' ¥' + yen(p.lp) + '</span>';
    // 送料別なら値札に書く。無料のときだけ印を出して、かかるときに
    // 黙っているのでは、安く見せているのと同じになる。
    if ((p.tags || []).indexOf('送料無料') < 0) sub += '<span class="pricetag-ship">＋送料</span>';
    if (sub) sub = '<span class="pricetag-sub">' + sub + '</span>';
    return '<div class="pricetag">' +
      '<span class="pricetag-label">いま</span>' +
      '<span class="pricetag-value"><span class="pricetag-yen">¥</span>' +
      '<span class="pricetag-num">' + yen(p.pr) + '</span></span>' + sub + '</div>';
  }

  function burst(p) {
    var n = p.rc || 0;
    if (n < 500) return '';
    var num = n >= 10000 ? (n / 10000).toFixed(1) : yen(n);
    var unit = n >= 10000 ? '万件' : '件';
    return '<div class="burst" aria-hidden="true"><span class="burst-n">' + num +
      '</span><span class="burst-l">' + unit + 'のレビュー</span></div>';
  }

  function sticker(p) {
    if (p.d < 5) return '';
    return '<div class="sticker" aria-hidden="true"><span class="sticker-num">' + p.d +
      '</span><span class="sticker-off">%OFF</span></div>';
  }

  var HOT = ['在庫わずか', 'タイムセール', '本日限り'];

  /* 絵文字は使わない。スプライトのSVGアイコンを参照する。 */
  function ic(name, cls) {
    return '<svg class="ic' + (cls ? ' ' + cls : '') + '" aria-hidden="true" focusable="false">' +
      '<use href="/assets/img/icons.svg#ic-' + name + '"></use></svg>';
  }

  /* カードのハート。サーバー側 render_watch_btn と同じ形にしておく。
     並べ替え・絞り込み・ガチャで描き直したカードでも消えないように。 */
  function watchBtn(id) {
    return '<button class="watch-btn watch-card" type="button" data-watch="' + esc(id) + '" ' +
      'aria-pressed="false" aria-label="気になるに追加">' +
      ic('heart', 'ic-heart-off') + ic('heart-on', 'ic-heart-on') +
      '<span class="watch-label">気になる</span></button>';
  }

  function card(p) {
    var tags = (p.tags || []).slice(0, 3).map(function (t) {
      var cls = t === 'ウォッチ中' ? 'tag tag-watch' : (HOT.indexOf(t) >= 0 ? 'tag tag-hot' : 'tag');
      return '<span class="' + cls + '">' + esc(t) + '</span>';
    }).join('');
    var cap = p.cap
      ? '<p class="card-cap"><span class="mk mk-' + (p.mk || 1) + '">' + esc(p.cap) + '</span></p>'
      : '';
    var aria = esc(p.t) + (p.d ? ' ' + p.d + '%OFF' : '') + ' ¥' + yen(p.pr);
    return '<article class="card">' +
      '<a class="card-media" href="/p/' + esc(p.id) + '/" aria-label="' + aria + '">' +
      '<img src="' + esc(p.img) + '" alt="' + esc(p.t) + '" loading="lazy" width="640" height="640">' +
      pricetag(p) + sticker(p) + burst(p) + '</a>' +
      '<div class="card-body">' + cap +
      '<h2 class="card-title"><a href="/p/' + esc(p.id) + '/">' + esc(p.t) + '</a></h2>' +
      '<div class="card-tags"><a class="tag" href="/c/' + esc(p.c) + '/">' + ic(p.ci) + esc(p.cl) + '</a>' + tags + '</div>' +
      '<div class="card-foot">' + watchBtn(p.id) +
      '<span class="card-shop">' + esc(p.shop) + '</span>' + postedLabel(p) +
      '<a class="btn btn-rakuten" href="' + esc(p.url) + '" target="_blank" rel="nofollow sponsored noopener">楽天で見る' + ic('arrow-right', 'ic-arrow') + '</a>' +
      '</div></div></article>';
  }

  // 保存した商品を並べる。価格は保存時のものではなく、いまの値を出す。
  // 掲載が終わった商品はデータに無いので、自然にリストからも消える。
  function renderWatch() {
    if (!watchFeed) return;
    var countEl2 = document.getElementById('watchCount');
    var clearBtn = document.getElementById('watchClear');

    ensureData().then(function () {
      var ids = watchRead();
      var byId = {};
      state.all.forEach(function (p) { byId[p.id] = p; });

      var items = ids.map(function (id) { return byId[id]; }).filter(Boolean);

      // 消えた商品のぶんは、保存側からも外しておく
      if (items.length !== ids.length) {
        watchWrite(items.map(function (p) { return p.id; }));
      }

      if (countEl2) countEl2.textContent = items.length;
      if (clearBtn) clearBtn.hidden = items.length === 0;

      if (!items.length) {
        watchFeed.innerHTML =
          '<div class="empty">' + ic('heart', 'ic-xxl') +
          '<p class="empty-title">まだ何も保存していません</p>' +
          '<p>商品のハートを押すと、ここに溜まります。' +
          '登録も名前も要りません。</p>' +
          '<p style="margin-top:14px"><a class="btn btn-rakuten" href="/">特価を見にいく</a></p>' +
          '</div>';
      } else {
        watchFeed.innerHTML = items.map(function (p) {
          // 保存したあとにセールが終わることがある。
          // 消してしまうと「保存したのに無い」になるので、印を付けて残す。
          return saleOver(p)
            ? '<div class="watch-over"><p class="watch-over-note">' +
              'このセールは終了しました。いまの価格は楽天でご確認ください。</p>' +
              card(p) + '</div>'
            : card(p);
        }).join('');
      }
      paintWatchButtons(watchFeed);
      drawMarkers(watchFeed);
    });
  }

  if (watchFeed) {
    renderWatch();
    var clear = document.getElementById('watchClear');
    if (clear) clear.addEventListener('click', function () {
      if (!window.confirm('保存した商品をすべて外します。よろしいですか？')) return;
      watchWrite([]);
      paintWatchButtons();
      renderWatch();
    });
  }

  function applySort(list) {
    var s = list.slice();
    if (state.sort === 'off') s.sort(function (a, b) { return b.d - a.d; });
    else if (state.sort === 'cheap') s.sort(function (a, b) { return a.pr - b.pr; });
    else s.sort(function (a, b) {
      return (b.at || '').localeCompare(a.at || '') || (b.d - a.d) || ((b.rc || 0) - (a.rc || 0));
    });
    return s;
  }

  function matches(p, q) {
    var hay = (p.t + ' ' + p.cap + ' ' + p.cl + ' ' + (p.tags || []).join(' ') + ' ' + p.shop).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
  }

  /* サーバーが出しているページ送りと同じ見た目を、JS側でも作る。
     並べ替えや検索に切り替えても操作感が変わらないように。 */
  function renderPager() {
    var host = document.querySelector('.pager');
    if (!host) return;
    var total = Math.max(1, Math.ceil(state.view.length / state.per));
    if (total <= 1) { host.hidden = true; return; }
    host.hidden = false;
    var prev = state.page > 1
      ? '<button type="button" class="pager-btn" data-page="' + (state.page - 1) + '">' +
        ic('arrow-right', 'ic-arrow ic-flip') + '前のページ</button>'
      : '<span class="pager-btn is-off">前のページ</span>';
    var next = state.page < total
      ? '<button type="button" class="pager-btn pager-next" data-page="' + (state.page + 1) + '">' +
        '次のページ' + ic('arrow-right', 'ic-arrow') + '</button>'
      : '<span class="pager-btn is-off">次のページ</span>';
    host.innerHTML = prev +
      '<span class="pager-count"><b>' + state.page + '</b><i>/</i>' + total + '</span>' + next;
  }

  function render() {
    var start = (state.page - 1) * state.per;
    var slice = state.view.slice(start, start + state.per);
    if (!slice.length) {
      var why = (state.pmin > 0 || state.pmax < Infinity)
        ? '<p>この価格帯にはまだ商品がありません。ほかの価格帯を見てください。</p>'
        : '<p>別のことばで探してみてください。</p>';
      feed.innerHTML = '<div class="empty">' + ic('search', 'ic-xxl') +
        '<p class="empty-title">見つかりませんでした</p>' + why + '</div>';
    } else {
      feed.innerHTML = slice.map(card).join('');
    }
    if (countEl) countEl.textContent = state.view.length;
    renderPager();
    drawMarkers(feed);
    paintWatchButtons(feed);
  }

  function refresh(keepPage) {
    var list = (state.all || []).filter(function (p) { return !saleOver(p); });
    if (state.q) list = list.filter(function (p) { return matches(p, state.q); });
    if (state.pmin > 0 || state.pmax < Infinity) {
      list = list.filter(function (p) { return p.pr >= state.pmin && p.pr < state.pmax; });
    }
    state.view = applySort(list);
    if (!keepPage) state.page = 1;
    render();
    if (keepPage) {
      var top = document.querySelector('.toolbar') || feed;
      top.scrollIntoView({ block: 'start' });
    }
  }

  var loading = null;
  function ensureData() {
    if (state.all) return Promise.resolve();
    if (!loading) {
      loading = fetch('/assets/data/feed.json')
        .then(function (r) { return r.json(); })
        .then(function (d) { state.all = d; });
    }
    return loading;
  }

  // サーバーが出したカードのうち、読み込み時点で終わっているものを消す。
  // データを持たないので、feed.json が来てから照合する。
  function hideExpiredServerCards() {
    if (!feed || !state.all) return;
    var over = {};
    state.all.forEach(function (p) { if (saleOver(p)) over[p.id] = 1; });
    Array.prototype.forEach.call(feed.querySelectorAll('.card'), function (c) {
      var a = c.querySelector('a[href^="/p/"]');
      if (!a) return;
      var id = a.getAttribute('href').split('/')[2];
      if (over[id]) c.remove();
    });
  }
  if (feed) ensureData().then(hideExpiredServerCards);

  var pagerHost = document.querySelector('.pager');
  if (pagerHost) {
    pagerHost.addEventListener('click', function (ev) {
      var btn = ev.target.closest('button[data-page]');
      if (!btn) return;
      state.page = Number(btn.dataset.page);
      refresh(true);
    });
  }

  var pricebar = document.querySelector('.pricebar');
  if (pricebar) {
    pricebar.addEventListener('click', function (ev) {
      var btn = ev.target.closest('button[data-price]');
      if (!btn) return;
      var parts = btn.dataset.price.split('-');
      state.pmin = btn.dataset.price === 'all' ? 0 : Number(parts[0] || 0);
      state.pmax = btn.dataset.price === 'all' || !parts[1] ? Infinity : Number(parts[1]);
      Array.prototype.forEach.call(pricebar.querySelectorAll('button'), function (b) {
        b.setAttribute('aria-pressed', String(b === btn));
      });
      ensureData().then(function () { refresh(); });
    });
  }

  if (sorter) {
    sorter.addEventListener('click', function (ev) {
      var btn = ev.target.closest('button[data-sort]');
      if (!btn) return;
      state.sort = btn.dataset.sort;
      Array.prototype.forEach.call(sorter.querySelectorAll('button'), function (b) {
        b.setAttribute('aria-pressed', String(b === btn));
      });
      ensureData().then(function () { refresh(); });
    });
  }

  // ---------------------------------------------------------------- ガチャ
  // レバーを引くと174件から1つ出る。カードの描き方は一覧と同じものを使う。
  var gachaBtn = document.getElementById('gachaGo');
  if (gachaBtn) {
    var slot = document.getElementById('gachaSlot');
    var rolling = false;

    gachaBtn.addEventListener('click', function () {
      if (rolling) return;
      rolling = true;
      gachaBtn.disabled = true;
      slot.classList.add('is-rolling');

      ensureData().then(function () {
        var pool = state.all.filter(function (p) {
          return p.img && p.pr && !saleOver(p);
        });
        if (!pool.length) { rolling = false; gachaBtn.disabled = false; return; }

        // 回っている感じを出すために、止まるまで数回入れ替える。
        // 間隔を少しずつ広げると、抽選が減速して見える。
        var spins = 9, i = 0, wait = 60;
        (function tick() {
          var p = pool[Math.floor(Math.random() * pool.length)];
          if (i < spins) {
            slot.innerHTML = '<div class="gacha-blur"><img src="' + p.img +
                             '" alt="" width="200" height="200"></div>';
            i++;
            wait = wait * 1.22;
            setTimeout(tick, wait);
          } else {
            slot.classList.remove('is-rolling');
            slot.classList.add('is-out');
            slot.innerHTML = card(p);
            drawMarkers(slot);
            paintWatchButtons(slot);
            rolling = false;
            gachaBtn.disabled = false;
            gachaBtn.innerHTML = ic('capsule', 'ic-capsule') + 'もう一回まわす';
            setTimeout(function () { slot.classList.remove('is-out'); }, 520);
          }
        })();
      });
    });
  }

  // URLの ?q= を拾って絞り込み
  var q = feed ? new URLSearchParams(window.location.search).get('q') : null;
  if (q) {
    state.q = q.trim().toLowerCase();
    var input = document.getElementById('searchInput');
    if (input) input.value = q;
    ensureData().then(function () {
      refresh();
      var head = document.querySelector('.hero-title');
      if (head) head.innerHTML = '「' + esc(q) + '」の<span class="hl">検索結果</span>';
      var lead = document.querySelector('.hero-lead');
      if (lead) lead.textContent = '条件に合う特価だけを表示しています。';
    });
  }
})();

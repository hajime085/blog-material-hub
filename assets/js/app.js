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

  /* ------------------------------------------------ フィード */
  var feed = document.getElementById('feed');
  if (!feed) return;

  var state = { all: null, view: [], shown: 0, per: 24, sort: 'new', q: '' };
  var loadMoreBtn = document.getElementById('loadMore');
  var countEl = document.getElementById('resultCount');
  var sorter = document.querySelector('.sorter');

  function pricetag(p) {
    var sub = '';
    if (p.u) sub += '<span class="pricetag-unit">' + esc(p.u) + '</span>';
    if (p.d) sub += '<span class="pricetag-was">' + esc(p.b || '通常') + ' ¥' + yen(p.lp) + '</span>';
    if (sub) sub = '<span class="pricetag-sub">' + sub + '</span>';
    return '<div class="pricetag">' +
      '<span class="pricetag-label">いま</span>' +
      '<span class="pricetag-value"><span class="pricetag-yen">¥</span>' +
      '<span class="pricetag-num">' + yen(p.pr) + '</span></span>' + sub + '</div>';
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

  function card(p) {
    var tags = (p.tags || []).slice(0, 3).map(function (t) {
      var cls = t === 'ウォッチ中' ? 'tag tag-watch' : (HOT.indexOf(t) >= 0 ? 'tag tag-hot' : 'tag');
      return '<span class="' + cls + '">' + esc(t) + '</span>';
    }).join('');
    var cap = p.cap ? '<p class="card-cap">' + esc(p.cap) + '</p>' : '';
    var aria = esc(p.t) + (p.d ? ' ' + p.d + '%OFF' : '') + ' ¥' + yen(p.pr);
    return '<article class="card">' +
      '<a class="card-media" href="/p/' + esc(p.id) + '/" aria-label="' + aria + '">' +
      '<img src="' + esc(p.img) + '" alt="' + esc(p.t) + '" loading="lazy" width="640" height="640">' +
      pricetag(p) + sticker(p) + '</a>' +
      '<div class="card-body">' + cap +
      '<h2 class="card-title"><a href="/p/' + esc(p.id) + '/">' + esc(p.t) + '</a></h2>' +
      '<div class="card-tags"><a class="tag" href="/c/' + esc(p.c) + '/">' + ic(p.ci) + esc(p.cl) + '</a>' + tags + '</div>' +
      '<div class="card-foot"><span class="card-shop">' + esc(p.shop) + '</span>' +
      '<a class="btn btn-rakuten" href="' + esc(p.url) + '" target="_blank" rel="nofollow sponsored noopener">楽天で見る' + ic('arrow-right', 'ic-arrow') + '</a>' +
      '</div></div></article>';
  }

  function applySort(list) {
    var s = list.slice();
    if (state.sort === 'off') s.sort(function (a, b) { return b.d - a.d; });
    else if (state.sort === 'cheap') s.sort(function (a, b) { return a.pr - b.pr; });
    else s.sort(function (a, b) {
      return (b.at || '').localeCompare(a.at || '') || (b.d - a.d) || (a.pr - b.pr);
    });
    return s;
  }

  function matches(p, q) {
    var hay = (p.t + ' ' + p.cap + ' ' + p.cl + ' ' + (p.tags || []).join(' ') + ' ' + p.shop).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
  }

  function render(reset) {
    if (reset) { feed.innerHTML = ''; state.shown = 0; }
    var next = state.view.slice(state.shown, state.shown + state.per);
    if (!next.length && !state.shown) {
      feed.innerHTML = '<div class="empty">' + ic('search', 'ic-xxl') +
        '<p class="empty-title">見つかりませんでした</p>' +
        '<p>別のことばで探してみてください。</p></div>';
    } else {
      feed.insertAdjacentHTML('beforeend', next.map(card).join(''));
    }
    state.shown += next.length;
    if (countEl) countEl.textContent = state.view.length;
    if (loadMoreBtn) loadMoreBtn.hidden = state.shown >= state.view.length;
  }

  function refresh() {
    var list = state.all;
    if (state.q) list = list.filter(function (p) { return matches(p, state.q); });
    state.view = applySort(list);
    render(true);
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

  if (loadMoreBtn) {
    loadMoreBtn.addEventListener('click', function () {
      loadMoreBtn.disabled = true;
      ensureData().then(function () {
        if (!state.view.length) { state.view = applySort(state.all); }
        // サーバー側で描画済みのぶんを飛ばす
        if (state.shown === 0) state.shown = feed.querySelectorAll('.card').length;
        render(false);
        loadMoreBtn.disabled = false;
      });
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
      ensureData().then(refresh);
    });
  }

  // URLの ?q= を拾って絞り込み
  var q = new URLSearchParams(window.location.search).get('q');
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

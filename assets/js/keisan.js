/* 買いまわり計算ツール
 *
 * 世に出ている計算ツールは「いくらポイントがもらえるか」を答える。
 * こちらが答えるのは「あと1店舗、足すべきか」。
 * うちの記事の主張は「10店舗を目指さない」なので、そこに合わせる。
 *
 * 計算は2か所で増える。
 *   ① 買いまわりが+1倍になるので、買い物“全体”に1%乗る
 *   ② 足した商品自身も、あなたの倍率ぶん稼ぐ
 * 出費は足した商品の値段。この差し引きが答えになる。
 *
 * 分からない言葉には、その場で説明を出す。
 * 知っている人しか使えない道具は、道具として失格なので。
 */
(function () {
  'use strict';

  var host = document.getElementById('keisan');
  if (!host) return;

  var KEY = 'yasumiru:keisan';
  var CAP = 7000;          // 買いまわりで得られるポイントの上限（回ごとに変わる）

  var state = { total: 30000, shops: 3, add: 1000, rate: 10 };
  try {
    var saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (saved) { for (var k in state) if (saved[k] != null) state[k] = saved[k]; }
  } catch (e) {}

  function yen(n) {
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  /* 買いまわりの倍率。10店舗で頭打ち。
     通常の1倍を含むので、3店舗なら3倍。 */
  function kaimawari(shops) {
    return Math.max(1, Math.min(10, shops));
  }

  function calc() {
    var total = Math.max(0, state.total);
    var add = Math.max(0, state.add);
    var rate = Math.max(1, state.rate);
    var now = kaimawari(state.shops);
    var next = kaimawari(state.shops + 1);
    var up = next - now;                       // 増える倍率。10店舗なら0。

    var gainAll = Math.floor(total * up / 100);        // 買い物全体に乗るぶん
    var gainNew = Math.floor(add * rate / 100);        // 足した商品自身のぶん
    var gain = gainAll + gainNew;
    var net = gain - add;

    // 買いまわりぶんが上限に当たるか
    var kaimawariPoints = Math.floor((total + add) * (next - 1) / 100);
    var over = kaimawariPoints > CAP;

    // 損益の境目。ここを超える合計なら、足しても損しない。
    var border = up > 0 ? Math.max(0, (add - gainNew) * 100 / up) : null;

    return {
      now: now, next: next, up: up, add: add,
      gainAll: gainAll, gainNew: gainNew, gain: gain, net: net,
      over: over, kaimawariPoints: kaimawariPoints, border: border
    };
  }

  // 分からない言葉の説明。ここを読まないと使えない道具にはしない。
  var HELP = {
    total: ['いま買う予定の合計とは',
      '今回のセール期間中に買うつもりのもの、全部の合計です。\n' +
      '税込の商品代金だけを入れてください。送料は買いまわりの計算に入りません。'],
    shops: ['店舗数の数え方',
      '同じ店で何回買っても1店舗です。別々の店でないと数が増えません。\n' +
      '1つの店で税込1,000円以上買って、はじめて1店舗として数えられます。'],
    add: ['足そうとしているものの値段',
      '「あと1店舗ぶん」として買い足すか迷っているものの値段です。\n' +
      'これも税込で、送料は含めません。'],
    rate: ['あなたの倍率とは',
      '楽天カードやモバイルなどの条件を満たすと上がる、あなた個人の倍率です。\n' +
      '通常の1倍に、SPUや5と0のつく日などが積み上がった合計を入れてください。\n' +
      '分からないときは楽天の「SPU達成状況」で確認できます。よく分からなければ、そのままで構いません。'],
    cap: ['ポイントの上限',
      '買いまわりで得られるポイントには上限があります。今回は' + yen(CAP) + 'ポイントとして計算しています。\n' +
      '上限は回ごとに変わるので、その回のキャンペーンページで確かめてください。\n' +
      '上限に達すると、店舗を増やしてもポイントは増えません。']
  };

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function field(key, label, unit, min, max, step) {
    return '' +
      '<div class="ks-field">' +
        '<label class="ks-label" for="ks-' + key + '">' + esc(label) +
          '<button class="ks-help" type="button" data-help="' + key + '" ' +
            'aria-label="' + esc(label) + 'の説明">?</button>' +
        '</label>' +
        '<div class="ks-input">' +
          '<input id="ks-' + key + '" type="number" inputmode="numeric" ' +
            'value="' + state[key] + '" min="' + min + '" max="' + max + '" step="' + step + '" ' +
            'data-key="' + key + '">' +
          '<span class="ks-unit">' + esc(unit) + '</span>' +
        '</div>' +
        '<div class="ks-note" id="ks-note-' + key + '" hidden></div>' +
      '</div>';
  }

  function render() {
    var r = calc();
    var win = r.net >= 0;

    var verdict, lead;
    if (r.up === 0) {
      verdict = 'これ以上は増えません';
      lead = '買いまわりは10店舗で頭打ちです。足しても倍率は上がりません。';
    } else if (win) {
      verdict = '足しても損しません';
      lead = '増えるポイントが、足す金額に届いています。もともと必要なものなら、足す価値があります。';
    } else {
      verdict = yen(-r.net) + '円の損です';
      lead = '増えるポイントより、出ていくお金のほうが大きい。'
           + '要らないものなら、足さないほうが得です。';
    }

    host.innerHTML = '' +
      '<div class="ks-form">' +
        field('total', 'いま買う予定の合計', '円', 0, 99999999, 1000) +
        field('shops', 'いま何店舗ぶんか', '店', 0, 20, 1) +
        field('add', '足そうとしているものの値段', '円', 0, 999999, 100) +
        field('rate', 'あなたの倍率', '倍', 1, 30, 1) +
      '</div>' +

      '<div class="ks-result' + (win ? ' is-win' : (r.up === 0 ? ' is-flat' : ' is-lose')) + '">' +
        '<p class="ks-verdict">' + esc(verdict) + '</p>' +
        '<p class="ks-lead">' + esc(lead) + '</p>' +
        '<table class="ks-table"><tbody>' +
          '<tr><th>買いまわりの倍率</th><td>' + r.now + '倍 → <b>' + r.next + '倍</b></td></tr>' +
          '<tr><th>買い物全体に乗るぶん</th><td>+' + yen(r.gainAll) + ' ポイント</td></tr>' +
          '<tr><th>足したもの自身のぶん</th><td>+' + yen(r.gainNew) + ' ポイント</td></tr>' +
          '<tr><th>出ていくお金</th><td>−' + yen(r.add) + ' 円</td></tr>' +
          '<tr class="ks-net"><th>差し引き</th><td>' +
            (r.net >= 0 ? '+' : '−') + yen(Math.abs(r.net)) + ' 円ぶん</td></tr>' +
        '</tbody></table>' +
        (r.border != null && !win
          ? '<p class="ks-border">いまの合計が <b>' + yen(r.border) + '円</b> を超えていれば、' +
            'この買い足しでも損しません。</p>'
          : '') +
        (r.over
          ? '<p class="ks-cap">買いまわりぶんが ' + yen(r.kaimawariPoints) + ' ポイントになり、' +
            '上限の' + yen(CAP) + 'ポイントを超えます。超えたぶんは付きません。' +
            '<button class="ks-help" type="button" data-help="cap" aria-label="上限の説明">?</button></p>'
          : '') +
      '</div>' +

      '<div class="ks-caveat">' +
        '<p class="ks-caveat-head">この計算に入れていないもの</p>' +
        '<ul>' +
          '<li><b>送料</b>は買いまわりの判定にも、ポイントの計算にも入りません。' +
          '払う総額は、ここに送料を足したものになります。</li>' +
          '<li>SPUの項目ごとの<b>上限</b>は見ていません。' +
          '楽天モバイルは2,000ポイントまで、といった個別の上限があります。</li>' +
          '<li>付くのは<b>期間限定ポイント</b>です。有効期限が短く、使い道も楽天の中に限られます。' +
          '現金と同じものとして計算に入れると、あとで困ります。</li>' +
          '<li>端数は切り捨てで計算しています。実際とは数ポイントずれることがあります。</li>' +
        '</ul>' +
        '<p class="ks-caveat-note">正確な金額は、買う直前に楽天のカートでご確認ください。</p>' +
      '</div>';

    paintHelp();
  }

  var openHelp = null;

  function paintHelp() {
    if (!openHelp) return;
    var box = document.getElementById('ks-note-' + openHelp);
    if (!box) return;
    var h = HELP[openHelp];
    box.innerHTML = '<b>' + esc(h[0]) + '</b>' +
      h[1].split('\n').map(function (l) { return '<span>' + esc(l) + '</span>'; }).join('');
    box.hidden = false;
  }

  host.addEventListener('input', function (ev) {
    var el = ev.target.closest('[data-key]');
    if (!el) return;
    var v = parseInt(el.value, 10);
    state[el.getAttribute('data-key')] = isNaN(v) ? 0 : v;
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
    var focused = el.id;
    render();
    var back = document.getElementById(focused);
    if (back) { back.focus(); back.setSelectionRange(back.value.length, back.value.length); }
  });

  host.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-help]');
    if (!b) return;
    var key = b.getAttribute('data-help');
    // 上限の説明だけは置き場所が違うので、その場に出す
    var box = document.getElementById('ks-note-' + key);
    if (!box) {
      var h = HELP[key];
      alert(h[0] + '\n\n' + h[1]);
      return;
    }
    if (openHelp === key) { box.hidden = true; openHelp = null; return; }
    if (openHelp) {
      var prev = document.getElementById('ks-note-' + openHelp);
      if (prev) prev.hidden = true;
    }
    openHelp = key;
    paintHelp();
  });

  render();
})();

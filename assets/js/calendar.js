/* サイドバーの楽天イベントカレンダー
 *
 * 楽天にイベント情報のAPIは無いので、日程は events.json に手で書く。
 * ただし「5と0のつく日」は日付の規則だけで決まるので、こちらで計算する。
 * 手で書く量を減らせるうえ、書き忘れも起きない。
 *
 * 確定と予想は必ず区別して出す。
 * （2026-08-24 再配信。CDNが古い中身を新しいURLで覚えてしまったため、
 *   中身を変えてURLを作り直している）
 * まだ発表されていない日程を確定のように見せるのは、
 * このサイトが批判している「安く見えて安くない」と同じことになる。
 */
(function () {
  'use strict';

  var host = document.getElementById('calendar');
  if (!host) return;

  var WD = ['日', '月', '火', '水', '木', '金', '土'];

  /* 日本時間で今日を出す。閲覧者の端末がどの地域でも、
     楽天のイベントは日本時間で動くのでこちらに合わせる。 */
  function jstNow() {
    return new Date(Date.now() + 9 * 3600000);
  }
  function ymd(d) { return d.toISOString().slice(0, 10); }

  /* 「2026-09-04 20:00」を日本時間として読む */
  function parseJst(s) {
    var m = (s || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (!m) return null;
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)) - 9 * 3600000;
  }

  function fmt(s, withTime) {
    var m = (s || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (!m) return '';
    var t = (+m[2]) + '月' + (+m[3]) + '日';
    if (withTime && m[4]) t += ' ' + m[4] + ':' + m[5];
    return t;
  }

  /* 毎月くり返すもの（5と0のつく日・ワンダフルデー・いちばの日）を、
     表示している月ぶんだけ日付に展開する。
     規則で決まるので手で書かない。書き忘れも起きない。 */
  function expand(recurring, y, mo) {
    var out = [];
    var last = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate();
    (recurring || []).forEach(function (r) {
      var days = [];
      if (r.rule === 'five-zero') {
        for (var d = 5; d <= last; d += 5) days.push(d);
      } else if (r.rule === 'day' && r.day) {
        if (r.day <= last) days.push(r.day);
      }
      days.forEach(function (d) {
        var ds = y + '-' + String(mo + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
        out.push({ name: r.name, kind: r.kind || 'point', status: '確定',
                   start: ds + ' 00:00', end: ds + ' 23:59', note: r.note, recurring: true });
      });
    });
    return out;
  }

  var shown = null;   // いま表示している月

  function build(data) {
    var events = (data && data.events) || [];
    var now = jstNow();
    var today = ymd(now);
    if (!shown) shown = new Date(now.getTime());

    var y = shown.getUTCFullYear(), mo = shown.getUTCMonth();
    var first = new Date(Date.UTC(y, mo, 1));
    var days = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate();
    var lead = first.getUTCDay();

    /* その日にかかっているイベントを引く。
       時刻ではなく日付で比べる。20時開始のイベントでもその日を含めたいので
       前後に余裕を持たせていたが、それだと1日ぶんはみ出して
       前日や翌日まで塗られてしまった（実際に4日や9日が塗られていた）。 */
    function onDay(dateStr) {
      return events.filter(function (ev) {
        var a = (ev.start || '').slice(0, 10);
        var b = (ev.end || ev.start || '').slice(0, 10);
        return a && dateStr >= a && dateStr <= b;
      });
    }

    // 表示している月の定例を足す
    events = events.concat(expand((data && data.recurring) || [], y, mo));

    var cells = '';
    for (var i = 0; i < lead; i++) cells += '<span class="cal-cell is-blank"></span>';
    for (var d = 1; d <= days; d++) {
      var ds = y + '-' + String(mo + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
      var evs = onDay(ds);
      var five = (d % 5 === 0);                     // 5と0のつく日
      var cls = 'cal-cell';
      if (ds === today) cls += ' is-today';
      if (ds < today) cls += ' is-past';
      if (evs.length) cls += ' has-ev ev-' + (evs[0].kind || 'other');
      if (evs.some(function (e) { return e.status === '予想'; })) cls += ' is-guess';
      if (five) cls += ' is-five';
      cells += '<span class="' + cls + '">' + d + '</span>';
    }

    // これから来るもの・開催中のものだけ並べる。終わったものは出さない。
    // 一覧に出すのはセールとマラソンだけ。
    // 定例は毎月あるので、並べても「次はいつか」の情報にならない。
    var upcoming = events.filter(function (ev) {
      return !ev.recurring &&
        parseJst(ev.end || ev.start) + 12 * 3600000 >= Date.now();
    }).sort(function (a, b) { return parseJst(a.start) - parseJst(b.start); });

    var list = upcoming.slice(0, 3).map(function (ev) {
      var a = parseJst(ev.start), b = parseJst(ev.end || ev.start);
      var live = Date.now() >= a && Date.now() <= b;
      // 日をまたぐかどうかで数える。今夜20時開始を「あと1日」と言わない。
      var startDay = (ev.start || '').slice(0, 10);
      var days2 = Math.round(
        (parseJst(startDay + ' 00:00') - parseJst(today + ' 00:00')) / 86400000);
      return '<li class="cal-ev ev-' + (ev.kind || 'other') + (live ? ' is-live' : '') + '">' +
        '<p class="cal-ev-name">' + esc(ev.name) +
          (ev.status === '予想' ? '<span class="cal-guess">予想</span>' : '') +
        '</p>' +
        '<p class="cal-ev-when">' + fmt(ev.start, true) + ' 〜 ' + fmt(ev.end, true) +
          (live ? '<b class="cal-live">開催中</b>'
                : (days2 === 0 ? '<span class="cal-in">今日から</span>'
                   : days2 === 1 ? '<span class="cal-in">明日から</span>'
                   : days2 > 0 ? '<span class="cal-in">あと' + days2 + '日</span>' : '')) +
        '</p>' +
        (ev.note ? '<p class="cal-ev-note">' + esc(ev.note) + '</p>' : '') +
        btns(ev) +
      '</li>';
    }).join('');

    host.innerHTML =
      '<div class="cal-head">' +
        '<button class="cal-nav" type="button" data-mv="-1" aria-label="前の月">‹</button>' +
        '<span class="cal-title">' + y + '年 ' + (mo + 1) + '月</span>' +
        '<button class="cal-nav" type="button" data-mv="1" aria-label="次の月">›</button>' +
      '</div>' +
      '<div class="cal-grid">' +
        WD.map(function (w, i) {
          return '<span class="cal-wd' + (i === 0 ? ' is-sun' : i === 6 ? ' is-sat' : '') + '">' + w + '</span>';
        }).join('') + cells +
      '</div>' +
      '<p class="cal-legend">' +
        '<span class="cal-key ev-sale"></span>スーパーSALE' +
        '<span class="cal-key ev-marathon"></span>マラソン' +
        '<span class="cal-key is-five"></span>5と0のつく日' +
      '</p>' +
      todayNote(events, today) +
      (list ? '<ul class="cal-list">' + list + '</ul>'
            : '<p class="cal-none">予定が入っていません。</p>') +
      '<p class="cal-note">日程は楽天の発表によります。' +
      '「予想」は過去の傾向からの見込みで、変わることがあります。</p>';
  }

  /* 予定に押せるボタンを付ける。
     外部（アフィリエイトのリンク）は別タブで開き、
     rel に nofollow sponsored を付ける。サイト内はそのまま遷移させる。 */
  /* 今日が定例の日（5と0のつく日など）なら、そこだけ短く出す。
     毎月あるので一覧には並べないが、今日なら知らせる価値がある。 */
  function todayNote(events, today) {
    var t = events.filter(function (e) {
      return e.recurring && (e.start || '').slice(0, 10) === today;
    });
    if (!t.length) return '';
    return '<div class="cal-today-note">' +
      '<p class="cal-today-name">今日は' + esc(t[0].name) + '</p>' +
      (t[0].note ? '<p class="cal-today-body">' + esc(t[0].note) + '</p>' : '') +
      btns(t[0]) + '</div>';
  }

  function btns(ev) {
    var ls = ev.links || [];
    if (!ls.length) return '';
    return '<div class="cal-ev-btns">' + ls.map(function (l) {
      return '<a class="cal-btn' + (l.ext ? ' is-ext' : '') + '" href="' + esc(l.url) + '"' +
        (l.ext ? ' target="_blank" rel="nofollow sponsored noopener"' : '') +
        '>' + esc(l.label) + '</a>';
    }).join('') + '</div>';
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  var all = { events: [], recurring: [] };
  host.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-mv]');
    if (!b) return;
    shown = new Date(Date.UTC(shown.getUTCFullYear(),
                              shown.getUTCMonth() + (+b.getAttribute('data-mv')), 1));
    build(all);
  });

  fetch('/assets/data/events.json')
    .then(function (r) { return r.json(); })
    .then(function (d) { all = d || all; build(all); })
    .catch(function () { build(all); });
})();

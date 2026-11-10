#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ヤスミル サイトビルダー

  python3 build.py

products.json と config.json を読み、サイト全体を生成する。
商品を追加したいときは products.json に足して、これを実行するだけ。
"""

import json
import os
import re
import shutil
import statistics
import contextlib
import io
import hashlib
import html
import icons
import markdown
import urllib.parse
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))

# 生成物。クリーンビルド時にこれらを消す。
GENERATED_DIRS = ["p", "c", "categories", "page", "off", "guide", "about", "contact", "privacy", "disclaimer", "terms"]
GENERATED_FILES = ["index.html", "404.html", "sitemap.xml", "feed.xml", "robots.txt",
                   "assets/data/feed.json"]

# 特集ページ（イベント×単一テーマ）。本文は生成物ではなく手で作る静的ページで、
# 原本を event_src/<slug>/index.html に置き、ビルド時に event/<slug>/ へ写す。
# （event/ は GENERATED_DIRS に入っていないので clean() では消えないが、
#   出力側だけに置くと原本がどこか分からなくなるため、写す形に寄せた。）
# 特集を足すときは、原本を event_src/ に置いてここに1行足すだけ。
# sitemap とトップの「特集」欄はこの登録から自動で出る。
EVENT_PAGES = [
    {"slug": "osechi", "title": "おせち特集",
     "desc": "早割・予約の締切カレンダーと、レビュー実績で選んだ楽天市場のおせち10選。"},
    {"slug": "cake", "title": "クリスマスケーキ特集",
     "desc": "予約・早割の締切カレンダーと、レビュー実績で選んだ楽天市場のクリスマスケーキ10選。"},
]


# ---------------------------------------------------------------- utilities
def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return json.load(f)


def tpl(name):
    with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as f:
        return f.read()


def write(relpath, content):
    full = os.path.join(ROOT, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def e(s):
    """HTML本文向けエスケープ"""
    return html.escape(str(s if s is not None else ""), quote=True)


def yen(n):
    return "{:,}".format(int(n))


def sale_soon(p):
    """まだ販売が始まっていない商品か。

    「20時から タイムセール」のように、開始が先の商品がある。
    在庫あり扱いで返ってくるので、そのままだと通常のフィードに並ぶが、
    その時刻までは買えない。買えないものを特価として並べないのが
    このサイトの決まりなので、フィードからは外して専用の枠に集める。
    始まれば自然にフィードへ流れる。
    """
    start = (p.get("startTime") or "").strip()
    if not start:
        return False
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(start, fmt) > datetime.now(JST).replace(tzinfo=None)
        except ValueError:
            continue
    return False


def sale_starts_label(p):
    """「今夜20:00から」「8月25日 10:00から」"""
    start = (p.get("startTime") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(start, fmt)
        except ValueError:
            continue
        today = datetime.now(JST).replace(tzinfo=None).date()
        if d.date() == today:
            return ("今夜" if d.hour >= 17 else "今日") + d.strftime(" %H:%M から")
        return d.strftime("%-m月%-d日 %H:%M から")
    return ""


def sale_over(p):
    """セールがもう終わっているか。

    楽天は商品によって販売期間（endTime）を持っている。
    「24時間限定 半額」のような商品は、その時刻を過ぎると元の値段に戻る。

    取得のときにも見ているが、それだけでは足りない。
    朝7時の取得では「まだ先」だったものが、10時には終わっている。
    次の取得は15時なので、その間ずっと終わったセールを載せ続けることになる。
    だから表示するたびに、いまの時刻で確かめる。
    """
    end = (p.get("endTime") or "").strip()
    if not end:
        return False
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(end, fmt) < datetime.now(JST).replace(tzinfo=None)
        except ValueError:
            continue
    return False


def sale_ends_label(p):
    """「8月24日 9:59まで」。期限があることを、読む人に伝える。"""
    end = (p.get("endTime") or "").strip()
    if not end:
        return ""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(end, fmt)
            return d.strftime("%-m月%-d日 %H:%Mまで")
        except ValueError:
            continue
    return ""


def discount_rate(p):
    """割引率。セールが終わっていたら0を返す。

    ここで一括して落とすことで、%OFFシール・「セール前◯円」の表記・
    ページタイトル・OGP・RSSまで、割引の主張が全部止まる。
    終了の告知だけ出して値札は「50%OFF」のまま、という食い違いを防ぐ。
    """
    if sale_over(p):
        return 0
    lp, pr = p.get("listPrice"), p.get("price")
    if not lp or not pr or lp <= pr:
        return 0
    return int(round((lp - pr) / lp * 100))


def fill(template, mapping):
    for k, v in mapping.items():
        template = template.replace("{{%s}}" % k, v)
    # 未使用トークンを掃除
    return re.sub(r"\{\{[A-Z_]+\}\}", "", template)


# ---------------------------------------------------------------- components
def last_fetch_date(products):
    """最後に取得を回した日。個々の商品の鮮度はこの日と比べて判断する。"""
    return max((p.get("lastSeen") or "" for p in products), default="")


def is_stale(p, fetched):
    """今日の取得ではAPIの結果に入らなかった商品。
    掲載は続けるが、価格が最新である保証はないので、そう分かるように出す。"""
    last = p.get("lastSeen") or p.get("postedAt") or ""
    return bool(fetched) and bool(last) and last < fetched


def stale_label(p):
    """「8月20日の価格」のような表記"""
    last = p.get("lastSeen") or p.get("postedAt") or ""
    try:
        d = datetime.strptime(last, "%Y-%m-%d")
        return d.strftime("%-m月%-d日") + "の価格"
    except ValueError:
        return "取得時点の価格"


def feed_order(p):
    """フィードの既定の並び。新しい順 → 割引率が高い順 → レビュー数が多い順。

    第3基準を価格にすると、値下がりがまだ無い日は全件が同着になり
    「新着」と「安い順」がまったく同じ並びになってしまう。
    レビュー数を使えば「新着」は実績のある順、「安い順」は価格順と、
    それぞれ別の意味を持つ。"""
    # postedAt は日付だけなので、同じ日に載ったものが全部同着になる。
    # それだと「新着」の中身が割引率の順になってしまうため、
    # 時刻まで持つ bumpedAt を第1基準にする。
    # 古いデータには bumpedAt が無いので、その日の0時として扱う。
    at = p.get("bumpedAt") or ((p.get("postedAt") or "") + "T00:00:00")
    return (at, discount_rate(p), p.get("reviewCount") or 0)


def price_basis_label(p):
    """基準価格が何なのかを、そのまま言葉にする。

    楽天APIは定価を返さない。当サイトが自分で観測した最高値は定価ではないので
    「通常」とは書けず「以前」と書く。ショップがタイトルに書いた値は
    セール前の価格の主張なので「セール前」と書く。
    """
    basis = p.get("priceBasis")
    if basis == "history":
        return "以前"
    if basis == "title":
        return "セール前"
    return "通常"


# 買いまわりの最低額。1商品ではなく、1ショップの合計（税込）で判定される。
KAIMAWARI_MIN = 1000


def active_kaimawari_event():
    """いま買いまわりのあるイベントの最中かを返す。

    日程は events.json にしか無いので、そこを読む。
    「予想」の日程では出さない。まだ発表されていない日を前提に
    「買いまわりに使えます」と書くのは、根拠のない案内になる。
    """
    if not os.path.exists(os.path.join(ROOT, "events.json")):
        return None
    doc = load("events.json")
    now = datetime.now(JST).replace(tzinfo=None)
    for ev in doc.get("events", []):
        if ev.get("status") != "確定":
            continue
        if ev.get("kind") not in ("marathon", "sale"):
            continue
        try:
            a = datetime.strptime(ev["start"][:16], "%Y-%m-%d %H:%M")
            b = datetime.strptime((ev.get("end") or ev["start"])[:16], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        if a <= now <= b:
            return ev
    return None


def shop_totals(products):
    """店ごとの、1,000円に届かない商品の合計額。

    買いまわりは1商品ではなく1ショップの合計で数える。
    だから328円の商品でも、同じ店で合わせれば1カウントになる。
    「1,000円未満だから無駄」と切り捨てると、そこを見落とす。
    """
    tot, cnt = {}, {}
    for p in products:
        if sale_over(p) or p["price"] >= KAIMAWARI_MIN:
            continue
        sh = p.get("shop") or ""
        tot[sh] = tot.get(sh, 0) + p["price"]
        cnt[sh] = cnt.get(sh, 0) + 1
    return tot, cnt


def render_kaimawari(p, totals):
    """買いまわりに使えるかどうかの印。イベントの最中だけ出す。

    買いまわりは1ショップの合計（税込）が1,000円以上で1カウント。
    だから最小の出費でカウントを稼ぐ最適解は「ちょうど1,000円前後」で、
    「1,000円ポッキリ」を狙う買い方は理にかなっている。

    ただし送料は1,000円の判定に入らない。
    1,000円＋送料590円は、支払い1,590円でカウントは1。
    1,200円の送料無料のほうが、安くて同じ1カウントになる。
    ここを見ないと「ポッキリ」を選んだつもりで損をする。

    条件は値段と送料で決まるので、機械的に判定できる。人の裁量は入れない。
    """
    if not totals:
        return ""
    tot, cnt = totals
    price, free = p["price"], free_shipping(p)

    if price < KAIMAWARI_MIN:
        sh = p.get("shop") or ""
        others = cnt.get(sh, 0) - 1
        short = KAIMAWARI_MIN - price
        if others > 0 and tot.get(sh, 0) >= KAIMAWARI_MIN:
            return ('<span class="km is-mix">%s単体では%d円たりない'
                    '<b>同じ店の他%d件と合わせれば1カウント</b></span>'
                    % (icons.use("info", "km-ic"), short, others))
        return ('<span class="km is-no">%s単体では買いまわりに数えません'
                '<b>あと%d円</b></span>'
                % (icons.use("info", "km-ic"), short))

    if not free:
        # 送料は1,000円の判定に入らない。額は楽天のデータに無いので書かない。
        return ('<span class="km is-warn">%s買いまわり対象'
                '<b>ただし送料別。合計は上がります</b></span>'
                % icons.use("info", "km-ic"))

    if price < KAIMAWARI_MIN * 2:
        return ('<span class="km is-best">%s買いまわり向き'
                '<b>送料無料で1カウント</b></span>'
                % icons.use("check", "km-ic"))

    return ('<span class="km is-ok">%s買いまわり対象<b>送料無料</b></span>'
            % icons.use("check", "km-ic"))


def free_shipping(p):
    return "送料無料" in (p.get("tags") or [])


def price_label(p):
    """値札の頭に付ける言葉。

    まだ始まっていない商品に「いま」と書くと、その値段で
    いま買えるように読める。実際には開始時刻まで買えないので、
    そういうものには開始時刻を書く。
    """
    if sale_soon(p):
        return sale_starts_label(p).replace(" から", "")
    return "いま"


def posted_label(p):
    """いつ載せたか。店名の隣に小さく置く。

    値段は毎日動くので、「いつの話か」が分からないと 読む人 は判断できない。
    カードの主役は値段と割引率なので、ここは控えめにする。
    """
    at = (p.get("postedAt") or "").strip()
    if len(at) < 10:
        return ""
    try:
        d = datetime.strptime(at[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return '<span class="card-posted"><time datetime="%s">%d/%d 掲載</time></span>' % (
        at[:10], d.month, d.day)


def render_pricetag(p, size="card"):
    d = discount_rate(p)
    sub = []
    if p.get("unitNote"):
        sub.append('<span class="pricetag-unit">%s</span>' % e(p["unitNote"]))
    if d:
        sub.append('<span class="pricetag-was">%s ¥%s</span>' % (
            price_basis_label(p), yen(p["listPrice"])))
    # 送料別なら値札に書く。無料のときだけ印を出して、
    # かかるときは黙っているのでは、安く見せているのと同じになる。
    # 金額は楽天のデータに無いので、額は書かない。
    if not free_shipping(p):
        sub.append('<span class="pricetag-ship">＋送料</span>')
    sub_html = '<span class="pricetag-sub">%s</span>' % "".join(sub) if sub else ""
    return (
        '<div class="pricetag%s">'
        '<span class="pricetag-label">%s</span>'
        '<span class="pricetag-value"><span class="pricetag-yen">¥</span>'
        '<span class="pricetag-num">%s</span></span>'
        '%s</div>'
    ) % (" is-soon" if sale_soon(p) else "", e(price_label(p)),
         yen(p["price"]), sub_html)


def render_burst(p):
    """レビュー件数のバースト。売場の「バカ売れ中！」に当たる位置だが、
    書くのは主観ではなく実際の件数だけ。500件未満には出さない。"""
    n = p.get("reviewCount") or 0
    if n < 500:
        return ""
    if n >= 10000:
        num, unit = "%.1f" % (n / 10000.0), "万件"
    else:
        num, unit = yen(n), "件"
    return ('<div class="burst" aria-hidden="true">'
            '<span class="burst-n">%s</span>'
            '<span class="burst-l">%sのレビュー</span></div>') % (num, unit)


def render_sticker(p):
    d = discount_rate(p)
    if d < 5:
        return ""
    return (
        '<div class="sticker" aria-hidden="true">'
        '<span class="sticker-num">%d</span><span class="sticker-off">%%OFF</span>'
        '</div>'
    ) % d


def render_sidebar(cfg, products, cats, active=None, guides=None, current_guide=None):
    """フィードの脇（PC）／下（スマホ）に出る棚。
    ヘッダーと同じ黒帯をパネルの頭に載せて、本体と地続きに見せる。
    順位マーカーは値札POPの縮小版。順位そのものに意味があるので番号を振る。"""
    fetched = last_fetch_date(products)
    fresh = [p for p in products
             if not is_stale(p, fetched) and not sale_over(p)
             and not sale_soon(p)] or products
    ranked = sorted(fresh, key=lambda p: -(p.get("reviewCount") or 0))[:5]
    rows = ""
    for i, p in enumerate(ranked, 1):
        rows += (
            '<a class="rank" href="/p/{id}/">'
            '<span class="rank-no">{i}</span>'
            '<img class="rank-img" src="{img}" alt="" width="120" height="120" loading="lazy">'
            '<span class="rank-body">'
            '<span class="rank-title">{t}</span>'
            '<span class="rank-meta">{star}{ra}<b>{rc}</b>件のレビュー</span>'
            '</span>'
            '<span class="rank-price"><i>¥</i>{pr}</span>'
            '</a>'
        ).format(id=e(p["id"]), i=i, img=e(p["image"]), t=e(p["title"]),
                 star=icons.use("star", "ic-star"),
                 ra=p.get("reviewAverage") or "-", rc=yen(p.get("reviewCount") or 0),
                 pr=yen(p["price"]))

    counts = {}
    for p in products:
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    cat_rows = "".join(
        '<a class="side-cat%s" href="/c/%s/">%s<span class="sc-name">%s</span>'
        '<span class="sc-count">%d</span></a>'
        % (" is-active" if active == c["slug"] else "", c["slug"],
           icons.use(c["icon"]), e(c["short"]), counts.get(c["slug"], 0))
        for c in cfg["categories"]
    )

    coupon = ""
    cp = cfg["site"].get("coupon") or {}
    # 期限切れのクーポンを出しっぱなしにしない。
    # 特価サイトで一番やってはいけないのは、使えない情報を置いておくこと。
    expired = False
    if cp.get("until"):
        try:
            end = datetime.strptime(cp["until"], "%Y-%m-%d").date()
            expired = datetime.now(JST).date() > end
        except ValueError:
            print("  [注意] coupon.until の日付が読めません: %r（YYYY-MM-DD 形式で）" % cp["until"])

    if cp.get("url") and cp.get("label") and not expired:
        note = cp.get("note", "")
        stamp = ""
        if cp.get("until"):
            try:
                end = datetime.strptime(cp["until"], "%Y-%m-%d")
                left = (end.date() - datetime.now(JST).date()).days
                stamp = end.strftime("%-m月%-d日") + "まで"
                if left <= 3:
                    stamp += "・のこり%d日" % left
            except ValueError:
                pass
        # クーポンは値札ではなく「券」。切り取り線とミシン目のえぐれを持たせる。
        coupon = """<section class="ticket">
  <div class="ticket-top">
    <p class="ticket-eyebrow">{ic}COUPON</p>
    <p class="ticket-label">{label}</p>
    <p class="ticket-note">{note}</p>
    {stamp}
  </div>
  <div class="ticket-tear" aria-hidden="true">{scissors}</div>
  <div class="ticket-bottom">
    <a class="btn btn-ticket btn-block" href="{url}" target="_blank" rel="nofollow sponsored noopener">クーポンを見る{arrow}</a>
  </div>
</section>""".format(
            ic=icons.use("tag"), label=e(cp["label"]), note=e(note),
            stamp=('<p class="ticket-stamp">%s</p>' % e(stamp)) if stamp else "",
            scissors=icons.use("scissors", "ic-scissors"),
            url=e(cp["url"]), arrow=icons.use("arrow-right", "ic-arrow"))

    # 記事は増えていくので、1本だけ出す作りにしない。
    # ただし黒帯を何枚も積むと主張が強すぎるので、1枚の中に並べる。
    guide_banner = ""
    gs = guides if guides is not None else (cfg.get("_guides") or [])
    gs = [g for g in gs if g.get("slug") != current_guide]
    if gs:
        items = "".join(
            '<a class="gb-item" href="/guide/%s/">'
            '<span class="gb-label">%s</span>'
            '<span class="gb-note">%s</span>'
            '<span class="gb-go">読む%s</span>'
            '</a>'
            % (e(g["slug"]), e(g.get("bannerLabel", "")), e(g.get("bannerNote", "")),
               icons.use("arrow-right", "ic-arrow"))
            for g in gs)
        guide_banner = (
            '<section class="guide-shelf">'
            '<p class="gb-eyebrow">%s%s</p>%s'
            '</section>'
        ) % (icons.use("doc"), "あわせて読む" if current_guide else "読んでおく", items)

    return """<aside class="layout-side">
  <section class="side-card">
    <h2 class="side-head">{ic}<span>いま売れているもの</span></h2>
    <div class="side-body">
      <div class="rank-list">{rows}</div>
      <p class="side-note">楽天市場のレビュー件数が多い順です。値下がり幅とは関係ありません。</p>
    </div>
  </section>
  <section class="side-card cal-card">
    <h2 class="side-head">{ic3}<span>楽天のイベント</span></h2>
    <div class="side-body">
      <div id="calendar"><p class="cal-loading">読み込んでいます…</p></div>
    </div>
  </section>
  {guide_banner}
  {coupon}
  <section class="side-card">
    <h2 class="side-head">{ic2}<span>売場から探す</span></h2>
    <div class="side-body side-body-tight">
      <div class="side-cats">{cat_rows}</div>
    </div>
  </section>
</aside>""".format(ic=icons.use("bolt"), ic2=icons.use("grid"), ic3=icons.use("calendar"),
                   rows=rows, cat_rows=cat_rows, coupon=coupon,
                   guide_banner=guide_banner)


def render_share(p, cfg, place="top"):
    """X / Threads / LINE への共有。
    シェアされる先はSNSのタイムラインなので、
    リンクだけでは何の話か分からない。価格と割引率を文言に入れておく。"""
    url = cfg["site"]["url"].rstrip("/") + "/p/%s/" % p["id"]
    d = discount_rate(p)
    head = "【%d%%OFF】" % d if d else ""
    text = "%s%s ¥%s" % (head, p["title"], yen(p["price"]))

    q = urllib.parse.quote
    targets = [
        # Xアプリは /intent/tweet を投稿画面として直接開く。
        # 新しい /intent/post はアプリ内ブラウザで開かれることがあり、
        # アプリ内ブラウザはアプリ本体とログイン状態を共有しないため
        # 毎回ログイン画面が出てしまう。実績のある方を使う。
        ("sns-x", "X", "https://twitter.com/intent/tweet?text=%s&url=%s" % (q(text), q(url))),
        ("sns-threads", "Threads",
         "https://www.threads.net/intent/post?text=%s" % q(text + "\n" + url)),
        ("sns-line", "LINE",
         "https://social-plugins.line.me/lineit/share?url=%s&text=%s" % (q(url), q(text))),
    ]
    btns = "".join(
        '<a class="share-btn share-%s" href="%s" target="_blank" rel="noopener nofollow" '
        'aria-label="%sでシェア">%s<span>%s</span></a>'
        % (name.replace("sns-", ""), e(href), label, icons.use(name), label)
        for name, label, href in targets
    )

    if place == "top":
        return ('<div class="share share-top">'
                '<span class="share-label">%sシェア</span>%s</div>'
                % (icons.use("share"), btns))
    return ('<div class="share share-bottom">'
            '<p class="share-heading">この値段、誰かに教える</p>'
            '<div class="share-row">%s</div></div>' % btns)


MARKER_COLORS = 5


def marker_no(p):
    """キャプションに引くマーカーの色番号（1〜5）。

    商品IDから決めるので、ビルドし直しても同じ商品は同じ色になる。
    並び順で決めると、商品が増えたときに既存の色が総入れ替えになってしまう。
    """
    h = hashlib.sha1(p["id"].encode("utf-8")).hexdigest()
    return int(h[:8], 16) % MARKER_COLORS + 1


def marked(text, p):
    """マーカーを引くキャプション。線は span に対して引く。

    p ではなく span に引くのは、行ごとに線を分けるため
    （box-decoration-break: clone が効くのはインライン要素）。
    """
    return '<span class="mk mk-%d">%s</span>' % (marker_no(p), e(text))


def render_watch_btn(p, place="card"):
    """気になるリストのボタン。押した状態はJSが端末に保存する。

    サーバーを持たないので、押した数を集計することはできない。
    それらしい数字を出すのは簡単だが、それは嘘になる。
    ここは「自分のための保存」として作り、数は出さない。
    """
    label = "気になる" if place == "card" else "あとで見る"
    return ('<button class="watch-btn watch-{place}" type="button" data-watch="{id}" '
            'aria-pressed="false" aria-label="{label}に追加">'
            '{off}{on}<span class="watch-label">{label}</span></button>').format(
        place=place, id=e(p["id"]), label=label,
        off=icons.use("heart", "ic-heart-off"),
        on=icons.use("heart-on", "ic-heart-on"))


def render_card(p, cats, fetched="", km=None):
    cat = cats.get(p["category"], {})
    d = discount_rate(p)
    tags = ""
    if is_stale(p, fetched):
        tags += '<span class="tag tag-stale">%s</span>' % e(stale_label(p))
    for t in (p.get("tags") or [])[:3]:
        if t == "ウォッチ中":
            cls = "tag tag-watch"
        elif t in ("在庫わずか", "タイムセール", "本日限り"):
            cls = "tag tag-hot"
        else:
            cls = "tag"
        tags += '<span class="%s">%s</span>' % (cls, e(t))
    cap = ""
    if p.get("caption"):
        cap = '<p class="card-cap">%s</p>' % marked(p["caption"], p)
    aria = "%s %d%%OFF ¥%s" % (p["title"], d, yen(p["price"])) if d else "%s ¥%s" % (p["title"], yen(p["price"]))
    return """<article class="card">
  <a class="card-media" href="/p/{id}/" aria-label="{aria}">
    <img src="{img}" alt="{alt}" loading="lazy" width="640" height="640">
    {tag}
    {sticker}
    {burst}
  </a>
  <div class="card-body">
    {km}{cap}
    <h2 class="card-title"><a href="/p/{id}/">{title}</a></h2>
    <div class="card-tags"><a class="tag" href="/c/{cslug}/">{cicon}{clabel}</a>{tags}</div>
    <div class="card-foot">
      {watch}<span class="card-shop">{shop}</span>{posted}
      <a class="btn btn-rakuten" href="{url}" target="_blank" rel="nofollow sponsored noopener">楽天で見る{arrow}</a>
    </div>
  </div>
</article>""".format(
        id=e(p["id"]), aria=e(aria), img=e(p["image"]), alt=e(p["title"]),
        watch=render_watch_btn(p, "card"),
        posted=posted_label(p),
        tag=render_pricetag(p), sticker=render_sticker(p),
        burst=render_burst(p), cap=cap, km=render_kaimawari(p, km),
        title=e(p["title"]), cslug=e(p["category"]),
        cicon=icons.use(cat.get("icon", "tag")), clabel=e(cat.get("short", "")),
        tags=tags, shop=e(p.get("shop", "楽天市場")), url=e(p.get("affiliateUrl") or "#"),
        arrow=icons.use("arrow-right", "ic-arrow"),
    )


def render_operator(cfg):
    """運営者カード。写真は値札と同じく少しだけ傾けて、貼った紙のように見せる。"""
    op = cfg["site"].get("operator") or {}
    if not op.get("name"):
        return ""
    threads = cfg["site"].get("threads") or ""
    link = ""
    if threads:
        link = ('<a class="btn btn-threads" href="%s" target="_blank" rel="noopener me">'
                '%s<span>%s</span></a>' % (e(threads), icons.use("sns-threads"),
                                           e(op.get("threadsLabel") or "Threads")))
    return """<div class="operator">
  <img class="operator-photo" src="{avatar}" alt="{name}" width="480" height="480" loading="lazy">
  <div class="operator-head">
    <p class="operator-role">{role}</p>
    <p class="operator-name">{name}</p>
  </div>
  <p class="operator-bio">{bio}</p>
  {link}
</div>""".format(avatar=e(op.get("avatar", "")), name=e(op["name"]),
                 role=e(op.get("role", "")), bio=e(op.get("bio", "")), link=link)


def render_chipbar(cfg, active=None):
    chips = '<a class="chip" href="/"%s>%sすべて</a>' % (
        ' aria-current="true"' if active is None else "", icons.use("tag"))
    for c in cfg["categories"]:
        cur = ' aria-current="true"' if active == c["slug"] else ""
        chips += '<a class="chip" href="/c/%s/"%s>%s%s</a>' % (
            c["slug"], cur, icons.use(c["icon"]), e(c["short"]))
    return '<nav class="chipbar" aria-label="カテゴリ"><div class="chipbar-scroll">%s</div></nav>' % chips


def render_ticker(products, cats):
    """電光掲示板。流れている商品はそのまま商品ページへ飛べる。
    同じ内容を2周ぶん並べて途切れなく流す。"""
    # 終わったセールは流さない。ここは割引率の高い順なので、
    # 何もしないと「終了した半額」ほど先頭に出てしまう。
    live = [x for x in products if not sale_over(x) and not sale_soon(x)]
    ranked = sorted(live, key=lambda x: (-discount_rate(x), -(x.get("reviewCount") or 0)))[:10]
    items = []
    for p in ranked:
        d = discount_rate(p)
        label = "%d%%OFF" % d if d else "注目"
        items.append('<a class="ticker-item" href="/p/%s/">%s %s ¥%s</a>' % (
            e(p["id"]), label, e(p["title"][:24]), yen(p["price"])))
    if not items:
        items = ['<span class="ticker-item">特価をあつめています</span>']
    row = "".join(items)
    return row + row


# ---------------------------------------------------------------- page shell
def page_shell(cfg, base, *, title, desc, path, content, ogtype="website",
         chipbar="", jsonld="", bodyclass="", ogimage=None, robots="", scripts="",
         products=None, cats=None, ogtitle=None):
    site = cfg["site"]
    canonical = site["url"].rstrip("/") + path
    footer_cats = "".join(
        '<li><a href="/c/%s/">%s%s</a></li>' % (c["slug"], icons.use(c["icon"]), e(c["short"]))
        for c in cfg["categories"]
    )
    drawer_cats = "".join(
        '<li><a href="/c/%s/">%s%s</a></li>' % (c["slug"], icons.use(c["icon"]), e(c["label"]))
        for c in cfg["categories"]
    )
    return fill(base, {
        "TITLE": e(title),
        "OGTITLE": e(ogtitle or title),
        "DESC": e(desc),
        "CANONICAL": e(canonical),
        "OGTYPE": ogtype,
        "OGIMAGE": e(ogimage or (site["url"].rstrip("/") + "/assets/img/ogp.png")),
        "SITE_NAME": e(site["name"]),
        "ROBOTS": robots,
        "JSONLD": jsonld,
        "BODYCLASS": bodyclass,
        "TICKER": render_ticker(products or [], cats or {}),
        "CHIPBAR": chipbar,
        "CONTENT": content,
        "FOOTER_CATS": footer_cats,
        "DRAWER_CATS": drawer_cats,
        "YEAR": str(datetime.now(JST).year),
        "SCRIPTS": scripts,
        "IC_SEARCH": icons.use("search"),
        "IC_MENU": icons.use("menu"),
        "IC_CLOSE": icons.use("close"),
        "IC_HOME": icons.use("home"),
        "IC_GRID": icons.use("grid"),
        "IC_QUIZ": icons.use("quiz"),
        "IC_HEART": icons.use("heart"),
        "CSS_HREF": asset_url("/assets/css/style.css"),
        "JS_SRC": asset_url("/assets/js/app.js"),
        "CAL_SRC": asset_url("/assets/js/calendar.js"),
        "FOOTER_OPERATOR": footer_operator(cfg),
        "GUIDE_LINKS": "".join(
            '<li><a href="/guide/%s/">%s</a></li>' % (e(g["slug"]), e(g.get("shortTitle", g["title"])))
            for g in (cfg.get("_guides") or [])),
        "GUIDE_LINKS_FOOTER": "".join(
            '<a href="/guide/%s/">%s</a>' % (e(g["slug"]), e(g.get("shortTitle", g["title"])))
            for g in (cfg.get("_guides") or [])),
        "GUIDE_LINKS_DRAWER": "".join(
            '<li><a href="/guide/%s/">%s%s</a></li>'
            % (e(g["slug"]), icons.use("doc"), e(g.get("shortTitle", g["title"])))
            for g in (cfg.get("_guides") or [])),
        "THREADS_ITEM": threads_nav_item(cfg),
        "CATSTRIP": render_catstrip(cfg),
        "THREADS_URL": e((cfg.get("site") or {}).get("threads") or "#"),
    })


def footer_operator(cfg):
    op = cfg["site"].get("operator") or {}
    threads = cfg["site"].get("threads") or ""
    if not op.get("name"):
        return ""
    inner = ('<img src="%s" alt="" width="160" height="160" loading="lazy">'
             '<span class="fo-name">%s</span>' % (e(op.get("avatarSmall", "")), e(op["name"])))
    if threads:
        return ('<a class="footer-operator" href="%s" target="_blank" rel="noopener me">'
                '%s%s</a>' % (e(threads), inner, icons.use("sns-threads")))
    return '<div class="footer-operator">%s</div>' % inner


def threads_nav_item(cfg):
    threads = cfg["site"].get("threads") or ""
    if not threads:
        return ""
    return ('<li><a href="%s" target="_blank" rel="noopener me">%sThreads</a></li>'
            % (e(threads), icons.use("sns-threads")))


def asset_url(relpath):
    """中身のハッシュをURLに付ける。

    CSSやJSは長期キャッシュさせたいが、そのままだと修正しても
    再訪問者に古いものが出続ける（実際にCDNが7日間ヒットし続けた）。
    中身が変われば別のURLになるので、長期キャッシュと即時反映を両立できる。"""
    full = os.path.join(ROOT, relpath.lstrip("/"))
    if not os.path.exists(full):
        return relpath
    with open(full, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()[:8]
    return "%s?v=%s" % (relpath, digest)


def jsonld_block(obj):
    return '<script type="application/ld+json">%s</script>' % json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- builders
def event_lastmod(slug):
    """特集ページの最終更新日。原本（event_src/）の mtime を使い、
    原本が無ければビルド日で代用する。"""
    src = os.path.join(ROOT, "event_src", slug, "index.html")
    if os.path.isfile(src):
        return datetime.fromtimestamp(os.path.getmtime(src), JST).strftime("%Y-%m-%d")
    return datetime.now(JST).strftime("%Y-%m-%d")


def build_events():
    """特集ページの原本（event_src/）を出力（event/）へ写す。
    原本が無い特集は写さない（sitemap・トップ欄には登録だけ出るので、
    先に登録して後から本文を置く運用もできる）。"""
    for ev in EVENT_PAGES:
        src = os.path.join(ROOT, "event_src", ev["slug"], "index.html")
        if not os.path.isfile(src):
            continue
        dst = os.path.join(ROOT, "event", ev["slug"], "index.html")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)


# ================= TOP（v11デザイン・確定版の本番移植） =================
# 確定デザイン: sample-design-top11i.html（試作）。
# 試作のJS（並べ替え・ページ切り替え）は静的生成に置き換えた:
#   「新着」= 通常ページ（/ と /page/N/）、「値下がり順」= /off/ の静的ページ、
#   「特集」= トピックスへのアンカー。ページネーションは1ページ15件。
TOP_PER_PAGE = 15
WEEKDAY_JA = "月火水木金土日"
# ヘッダー帯の並び。ベビーは帯に出さない（確定デザイン）。
# 準主力のペット・季節ものも帯の枠には入れない（カテゴリ一覧・各カテゴリページから辿れる）。
STRIP_ORDER = ["kitchen", "kaden", "food", "beauty", "pc", "fashion"]

# トピックス（季節の特集）。締切の日付はここには書かない。
# events.json の kind=deadline（deadlineKey = 各エントリの key）から引くので、
# 締切が過ぎれば表示も消える。
TOPICS = [
    {"slug": "cake", "img": "/assets/img/event/cake/pick-01.jpg",
     "imgAlt": "ルタオ クリスマスケーキ2個セット",
     "badge": "クリスマス × ケーキ",
     "title": "クリスマスケーキ予約2026｜実績と締切で選ぶ10選",
     "copy": "有名パティスリーから定番の生クリーム、チョコ、少人数向けまで。締切カレンダー付きで、「いつまでに予約すればいいか」が分かります。",
     "chips": "採用10件　レビュー実績と締切で選定",
     "deadlineKey": "cake-sowari", "chip": True,
     "revFmt": "締切：早割は %s まで"},
    {"slug": "osechi", "img": "/assets/img/event/osechi/pick-06.jpg",
     "imgAlt": "博多久松のおせち「博多」",
     "badge": "年末 × おせち",
     "title": "おせち予約2027｜実績と締切で選ぶ10選",
     "copy": "レビュー5万件超の福袋おせちから、2.3万件の定番・博多久松まで。冷蔵・冷凍、段数と人数、最終受付を確認して掲載しています。",
     "chips": "採用10件　少人数・ファミリー・豪華",
     "deadlineKey": "osechi-final", "chip": False,
     "revFmt": "締切：最終受付 %s"},
]


def render_catstrip(cfg):
    """ヘッダー2段目のカテゴリ帯（単一トーン、特集だけ赤）。"""
    by = {c["slug"]: c for c in cfg["categories"]}
    out = ['<a class="v-cat-btn v-feat" href="/#topics">特集</a>']
    for slug in STRIP_ORDER:
        c = by.get(slug)
        if c:
            out.append('<a class="v-cat-btn" href="/c/%s/">%s</a>' % (slug, e(c["short"])))
    return "".join(out)


def median_off(p):
    """検証の物差しでの値下がり。(中央値, OFF%) を返す。

    取消線の価格は価格履歴（30日以上の観測）の中央値。
    試作は13件とも中央値＝curated の median_price で一致していた。
    履歴が無ければ (None, 0) を返し、呼び出し側は取消線を出さない。
    """
    f = history_facts(p)
    if not f or not f["median"]:
        return (None, 0)
    med = int(round(f["median"]))
    if med > int(p["price"]):
        return (med, int(round((med - int(p["price"])) / med * 100)))
    return (med, 0)


def _md_label(dstr):
    """'2026-11-09' → '11/9'"""
    try:
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    return "%d/%d" % (d.month, d.day)


def _fetched_label(fetched):
    try:
        d = datetime.strptime(fetched, "%Y-%m-%d")
        return "%d月%d日" % (d.month, d.day)
    except (ValueError, TypeError):
        return fetched or ""


def deadline_entries():
    """events.json から、今日以降・近い順の「締切」エントリを返す。

    締切データは events.json に一本化した（旧 config の deadlines は廃止）。
    対象は kind=deadline（特集の締切）と、kind=sale の開始日。
    旧 config の deadlines には大型セールの開始日（ブラックフライデー／
    スーパーSALE）も「締切」として載っていたので、従来の表示内容に合わせて
    sale の開始日も含める（タイトルには「開始」を付ける）。
    お買い物マラソンは頻度が高く、「逃すと困る締切」の趣旨に合わないので
    ここには含めない（カレンダー本体には従来どおり出る）。
    締切データの出どころは events.json 側の各エントリの _source に記録してある。
    終わったものは自動で外れる（過去の締切を「近い順」に出し続けない）。
    トピックスの締切参照（key）もこのエントリ経由。
    """
    if not os.path.exists(os.path.join(ROOT, "events.json")):
        return []
    doc = load("events.json")
    today = datetime.now(JST).date()
    out = []
    for ev in doc.get("events") or []:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        if kind not in ("deadline", "sale"):
            continue
        try:
            dt = datetime.strptime((ev.get("start") or "")[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if dt < today:
            continue
        title = ev.get("name", "")
        if kind == "sale":
            title += " 開始"
        label = ev.get("dateLabel") or "%d/%d（%s）" % (
            dt.month, dt.day, WEEKDAY_JA[dt.weekday()])
        out.append({"key": ev.get("key", ""), "date": dt, "label": label,
                    "title": title, "note": ev.get("note", ""),
                    "status": ev.get("status", ""), "noDays": bool(ev.get("noDays")),
                    "days": (dt - today).days})
    out.sort(key=lambda x: x["date"])
    return out


def _sd_days_text(en):
    if en["status"] == "予想":
        return "予想"
    if en["noDays"]:
        return ""
    return "あと%d日" % en["days"]


def render_top_sidebar(entries, guides=None):
    # 並びは「セールカレンダー → 締切が近い順 → 読んでおく」。
    # 特集バナーはヒーロー直下のカルーセルに一本化した（旧・キャンペーン情報）。
    # カレンダーは他ページのサイドバーと同じ仕組み（id="calendar" 枠＋
    # calendar.js ＋ events.json）。見出しも既存ページに合わせて「楽天のイベント」。
    blocks = ('<div class="v-side-block"><div class="v-side-head">楽天のイベント</div>'
              '<div class="v-cal-body"><div id="calendar">'
              '<p class="cal-loading">読み込んでいます…</p></div></div></div>')
    if entries:
        # 締切帯の廃止に伴い「全部見る」リンク（#deadline）は無くし、
        # 代わりに表示件数を 4 → 6 に増やした。全件は上のカレンダーで辿れる。
        lis = "".join(
            '<li><div class="v-sd-date">%s</div><div class="v-sd-title">%s</div>'
            '<div class="v-sd-days">%s</div></li>'
            % (e(en["label"]), e(en["title"]), e(_sd_days_text(en)))
            for en in entries[:6])
        blocks += ('<div class="v-side-block"><div class="v-side-head">締切が近い順</div>'
                   '<ul class="v-side-list">%s</ul></div>' % lis)
    if guides:
        # SEOガイドへの導線。旧 TOP にあった「読んでおく」ガイドバー
        # （gb-item）の後継としてサイドバーに置く。帯そのものは復活させない。
        # 文言はガイドデータの shortTitle（無ければ title）の範囲に限る。
        glis = "".join(
            '<li><a class="v-guide-link" href="/guide/%s/">%s</a></li>'
            % (e(g["slug"]), e(g.get("shortTitle", g["title"])))
            for g in guides)
        blocks += ('<div class="v-side-block"><div class="v-side-head">読んでおく</div>'
                   '<ul class="v-side-list">%s</ul></div>' % glis)
    return '<aside class="v-side">%s</aside>' % blocks


def render_stream_card(p, cats, badge, cat_label=None):
    """確定デザインのストリームカード。リンク先は商品ページ（/p/）。

    試作は楽天へ直リンクだったが、本番はサイト内の回遊（商品ページの
    価格推移・掲載基準の実測）を優先し、既存の /p/ 方式に合わせた。
    タイトルは商品名そのまま（CSSで3行まで）。短縮名は作らない（創作回避）。
    """
    c = cats.get(p["category"], {})
    med, off = median_off(p)
    price_row = '<span class="v-price">¥%s</span>' % yen(p["price"])
    if med and off:
        price_row += ('<span class="v-price-old">¥%s</span>'
                      '<span class="v-off">%d%%OFF</span>' % (yen(med), off))
    rev = ""
    if p.get("reviewAverage"):
        rev = ('<span class="v-rev">★★%s（%s件）</span>'
               % (e(p["reviewAverage"]), yen(p.get("reviewCount", 0))))
    ship = '・<span class="v-nw">送料無料</span>' if free_shipping(p) else ""
    note = ('<p class="v-note-line">比較元：30日の中央値・%s確認%s</p>'
            % (_md_label(p.get("lastSeen", "")), ship))
    return (
        '<a class="v-card" href="/p/{id}/">'
        '<img class="v-card-img" src="{img}" alt="{alt}" loading="lazy">'
        '<div class="v-card-body">'
        '<div class="v-cat-row"><span class="v-cat-label">{label}</span>'
        '<span class="v-note-badge">{badge}</span>'
        '<span class="v-card-date">{date}</span></div>'
        '<h3 class="v-card-title">{title}</h3>'
        '<div class="v-price-row">{price_row}</div>'
        '<div class="v-card-rule"></div>'
        '<div class="v-meta-row"><span class="v-shop">{shop}</span>{rev}</div>'
        '{note}</div></a>'
    ).format(id=e(p["id"]), img=e(p["image"]), alt=e(p["title"]),
             label=e(cat_label or c.get("short") or c.get("label") or ""),
             badge=e(badge), date=e(_md_label(p.get("lastSeen", ""))),
             title=e(p["title"]), price_row=price_row,
             shop=e(p.get("shop", "楽天市場")), rev=rev, note=note)


def select_pickup(live):
    """PICK UP の選定。最大OFFで機械的に選ばない（確定ルール）。

    1) curated.json の feed エントリに "pickup": true の明示フラグがあれば最優先。
    2) 無ければ fallback: レビュー1,000件以上の検証通過アイテムの中で
       「レビュー数 × 値下がり幅（中央値比OFF%）」のスコア上位を1件。
    戻り値: (商品, "flag" | "score")
    """
    if not live:
        return None, ""
    if os.path.exists(os.path.join(ROOT, "curated.json")):
        try:
            cur = load("curated.json")
            flagged = {en.get("id") for en in (cur.get("feed") or []) if en.get("pickup")}
        except Exception:
            flagged = set()
        for p in live:
            if p["id"] in flagged:
                return p, "flag"
    cand = [p for p in live if (p.get("reviewCount") or 0) >= 1000] or list(live)
    best = max(cand, key=lambda p: (p.get("reviewCount") or 0) * median_off(p)[1])
    return best, "score"


def pickup_note(pick, live):
    """PICK UP カード下の選定理由。試作の正直な注記の形式で、事実だけを書く。"""
    offs = sorted((median_off(p)[1] for p in live), reverse=True)
    _med, off = median_off(pick)
    rank = offs.index(off) + 1 if off in offs else len(offs)
    rc = yen(pick.get("reviewCount", 0))
    if rank <= 1:
        return ("↑ 値下がり幅も今日いちばん。レビュー%s件の実績とあわせて、"
                "今日の顔にしました。" % rc)
    return ("↑ 値下がり幅は%d番手。でもレビュー%s件の実績で、"
            "今日の顔にしました。" % (rank, rc))


def top_units(live):
    """TOP のストリームを (種類, 商品) の列にする。
    PICK UP → フィード（新着順、PICK UP を除く）。全件ユニーク（再掲なし）。"""
    ordered = sorted(live, key=feed_order, reverse=True)
    pick, _how = select_pickup(ordered)
    feed = [p for p in ordered if pick is None or p["id"] != pick["id"]]
    units = []
    if pick is not None:
        units.append(("pick", pick))
    units += [("feed", p) for p in feed]
    return ordered, pick, units


def render_unit(kind, p, cats, live):
    if kind == "pick":
        card = render_stream_card(p, cats, "本日のイチオシ")
        card = card.replace('class="v-card"', 'class="v-card v-card-pick"', 1)
        return ('<div class="v-pick-cell">%s<p class="v-pick-note">%s</p></div>'
                % (card, e(pickup_note(p, live))))
    return render_stream_card(p, cats, "検証済み")


CAROUSEL_JS = (
    '<script>(function(){'
    'var t=document.querySelector(".v-camp-track");if(!t)return;'
    'var d=document.querySelectorAll(".v-camp-dot");if(!d.length)return;'
    'function w(){var s=t.querySelector(".v-camp-slide");'
    'return s?s.getBoundingClientRect().width+10:1;}'
    'function cur(){return Math.min(d.length-1,Math.round(t.scrollLeft/w()));}'
    'var raf=0;'
    't.addEventListener("scroll",function(){if(raf)return;'
    'raf=requestAnimationFrame(function(){raf=0;var i=cur();'
    'd.forEach(function(b,j){b.classList.toggle("v-on",j===i);});});'
    '},{passive:true});'
    # 自動再生。5秒ごとに次へ送り、末尾からは先頭へ戻る。
    # 操作中（ホバー／タッチ／スクロール／ドット操作）は止め、
    # 操作が終わって6秒後に再開する。タブが非表示の間は止める。
    # prefers-reduced-motion の環境では最初から動かさない。
    # ドットが無い（特集1件）ときは上の return で終了しているので動かない。
    'var DELAY=5000,RESUME=6000,timer=null,resumeTimer=null;'
    'var mq=window.matchMedia("(prefers-reduced-motion: reduce)");'
    'function go(n){t.scrollTo({left:n*w(),behavior:"smooth"});}'
    'function step(){go((cur()+1)%d.length);}'
    'function start(){if(timer||mq.matches)return;'
    'timer=setInterval(function(){if(document.hidden)return;step();},DELAY);}'
    'function stop(){if(timer){clearInterval(timer);timer=null;}}'
    'function kick(){stop();if(resumeTimer)clearTimeout(resumeTimer);'
    'resumeTimer=setTimeout(start,RESUME);}'
    'd.forEach(function(b,j){b.addEventListener("click",function(){'
    'go(j);kick();});});'
    'var sec=t.closest(".v-camp");'
    'if(sec){sec.addEventListener("pointerenter",stop);'
    'sec.addEventListener("pointerleave",start);}'
    't.addEventListener("pointerdown",kick);'
    't.addEventListener("touchstart",kick,{passive:true});'
    't.addEventListener("wheel",kick,{passive:true});'
    'document.addEventListener("visibilitychange",function(){'
    'if(document.hidden)stop();else start();});'
    'start();'
    '})();</script>')


def render_campaign_carousel(entries):
    """キャンペーン特集カルーセル（ヒーロー直下）。特集の露出はここ1か所に
    集約する（旧・サイドバーのキャンペーン情報とストリーム内の特集カードを
    畳んだ）。スライドは TOPICS から生成し、増減に追従する。
    締切は events.json から引く。締切エントリが無い・過ぎた場合は日付まわり
    の表示ごと省く（日付の創作をしない）。id="topics" は特集タブ／カテゴリ
    帯のアンカーとして維持する。"""
    by_key = {en["key"]: en for en in entries}
    slides = []
    for t in TOPICS:
        en = by_key.get(t["deadlineKey"])
        extra = ""
        if en is not None:
            md = "%d/%d" % (en["date"].month, en["date"].day)
            if t["chip"]:
                extra = '<span class="v-topic-deadline">早割は%sまで</span>' % md
            else:
                extra = ('<span class="v-camp-dl">%s</span>'
                         % e(t["revFmt"] % md))
        slides.append(
            '<a class="v-camp-slide" href="/event/{slug}/">'
            '<img class="v-camp-img" src="{img}" alt="{alt}" loading="lazy">'
            '<span class="v-camp-cap">'
            '<span class="v-camp-row"><span class="v-note-badge">{badge}</span>'
            '{extra}</span>'
            '<span class="v-camp-title">{title}</span>'
            '</span></a>'.format(
                slug=t["slug"], img=e(t["img"]), alt=e(t["imgAlt"]),
                badge=e(t["badge"]), extra=extra, title=e(t["title"])))
    dots = ""
    if len(slides) > 1:
        dots = ('<span class="v-camp-dots">%s</span>' % "".join(
            '<button type="button" class="v-camp-dot%s" '
            'aria-label="%d枚目の特集へ"></button>'
            % (" v-on" if i == 0 else "", i + 1)
            for i in range(len(slides))))
    return ('<section class="v-camp" id="topics" aria-label="キャンペーン特集">'
            '<div class="v-camp-top">'
            '<span class="v-camp-label">キャンペーン特集</span>%s</div>'
            '<div class="v-camp-track">%s</div></section>'
            % (dots, "".join(slides)))


def render_top_hero(live_n, fetched):
    """ヒーロー。数字はすべてビルドの実データから単一集計する:
    観測数 = price_history.json のキー数、掲載数 = live 件数、最終確認 = 取得日。"""
    obs = "{:,}".format(len(price_history()))
    return (
        '<section class="v-hero"><div class="v-hero-main">'
        '<h1 class="v-hero-title"><span class="v-seg">今日の特価は、</span>'
        '<span class="v-seg"><span class="v-u">検証</span>を通った</span>'
        '<span class="v-seg">ものだけ。</span></h1>'
        '<p class="v-hero-sub">価格を観測しているのは<strong>%s品</strong>。'
        'その全部をチェックして、価格履歴・レビュー・送料まで検証できたものだけを載せました。<br>'
        '<strong>本日の掲載 %d件</strong>｜最終確認 <strong>%s</strong>'
        '<span class="v-hero-note">（観測中%s品 → 検証通過%d件）</span></p></div>'
        '</section>'
        % (obs, live_n, e(_fetched_label(fetched)), obs, live_n))


def render_notice(live):
    """速報帯: その日の検証通過アイテムから OFF 上位5件（同率はレビュー数順）。
    帯の中はティッカーで流し、各アイテムから商品ページへリンクする。
    商品名は切らずに全文を流す。ループを継ぎ目なくするため同じ並びを
    2周ぶん並べ、2周目は読み上げとタブ移動の対象から外す。"""
    top = sorted(live, key=lambda p: (median_off(p)[1], p.get("reviewCount") or 0),
                 reverse=True)[:5]
    if not top:
        return ""

    def item(p, dup=False):
        _med, off = median_off(p)
        return ('<a class="v-notice-item" href="/p/%s/"%s>'
                '<strong>%s</strong> %d%%OFF ¥%s'
                '<span class="v-notice-sep" aria-hidden="true">◆</span></a>'
                % (e(p["id"]), ' tabindex="-1" aria-hidden="true"' if dup else "",
                   e(p["title"]), off, yen(p["price"])))

    seq = "".join(item(p) for p in top)
    dup = "".join(item(p, dup=True) for p in top)
    # 流れる速さがアイテムの総量で変わらないよう、文字数から duration を出す
    # （約11px/文字 ÷ およそ55px/秒 ≒ 5文字で1秒、下限18秒・上限90秒）。
    chars = sum(len(p["title"]) + 14 for p in top)
    dur = min(90, max(18, round(chars / 5)))
    return ('<div class="v-notice"><span class="v-notice-badge">速報</span>'
            '<div class="v-notice-viewport"><div class="v-notice-track" '
            'style="animation-duration:%ds">%s%s</div></div></div>'
            % (dur, seq, dup))


def render_top_tabs(active):
    tabs = [("new", "新着", "/"), ("off", "値下がり順", "/off/"),
            ("topics", "特集", "/#topics")]
    return '<nav class="v-tabs">%s</nav>' % "".join(
        '<a class="v-tab%s" href="%s">%s</a>'
        % (" v-on" if k == active else "", href, label)
        for k, label, href in tabs)


def render_pager_v11(page, total_pages, base):
    """確定デザインのページャー（四角ボタン、現在ページ赤、前へ/次へ）。
    総ページ数が1なら出さない。"""
    if total_pages <= 1:
        return ""

    def url(n):
        return base if n == 1 else "%spage/%d/" % (base, n)

    parts = []
    if page > 1:
        parts.append('<a class="v-pager-btn" href="%s">前へ</a>' % url(page - 1))
    else:
        parts.append('<span class="v-pager-btn v-off">前へ</span>')
    for n in range(1, total_pages + 1):
        if n == page:
            parts.append('<span class="v-pager-btn v-on">%d</span>' % n)
        else:
            parts.append('<a class="v-pager-btn" href="%s">%d</a>' % (url(n), n))
    if page < total_pages:
        parts.append('<a class="v-pager-btn" href="%s">次へ</a>' % url(page + 1))
    else:
        parts.append('<span class="v-pager-btn v-off">次へ</span>')
    return '<nav class="v-pager">%s</nav>' % "".join(parts)


def _render_top(cfg, base, products, cats, *, units, live, active_tab, page,
                total_pages, pager_base, title, desc, path, robots, jsonld):
    fetched = last_fetch_date(products)
    entries = deadline_entries()
    start = (page - 1) * TOP_PER_PAGE
    chunk = units[start: start + TOP_PER_PAGE]
    cards = []
    for kind, p in chunk:
        cards.append(render_unit(kind, p, cats, live))
    stream = "".join(cards)
    content = (
        '<div class="v-page">'
        + render_top_hero(len(live), fetched)
        + (render_campaign_carousel(entries) if page == 1 else "")
        + render_notice(live)
        + '<div class="v-layout">'
        + render_top_sidebar(entries, cfg.get("_guides") or [])
        + '<div class="v-main">'
        + render_top_tabs(active_tab)
        + '<section class="v-stream" id="stream">%s</section>' % stream
        + render_pager_v11(page, total_pages, pager_base)
        + '</div></div></div>'
    )
    return page_shell(
        cfg, base, title=title,
        ogtitle="%s ── %s" % (cfg["site"]["name"], cfg["site"]["tagline"]),
        desc=desc, path=path, content=content, jsonld=jsonld,
        bodyclass="v11", robots=robots, products=products, cats=cats,
        scripts=CAROUSEL_JS if page == 1 and len(TOPICS) > 1 else "")


def build_index(cfg, base, products, cats):
    # 終わったセール・始まっていないセールは特価フィードに並べない。
    # 商品ページは残す（Xに貼ったリンクが死なないように）。
    live = [p for p in products if not sale_over(p) and not sale_soon(p)]
    _ordered, _pick, units = top_units(live)
    total_pages = max(1, -(-len(units) // TOP_PER_PAGE))
    site_url = cfg["site"]["url"].rstrip("/")

    for page in range(1, total_pages + 1):
        path = "/" if page == 1 else "/page/%d/" % page
        links = ""
        if page > 1:
            links += '<link rel="prev" href="%s">' % (
                site_url + ("/" if page == 2 else "/page/%d/" % (page - 1)))
        if page < total_pages:
            links += '<link rel="next" href="%s/page/%d/">' % (site_url, page + 1)
        jsonld = jsonld_block({
            "@context": "https://schema.org", "@type": "WebSite",
            "name": cfg["site"]["name"], "url": cfg["site"]["url"],
            "description": cfg["site"]["description"], "inLanguage": "ja",
        }) + links
        title = "%s｜%s" % (cfg["site"]["name"], cfg["site"]["tagline"])
        desc = cfg["site"]["description"]
        if page > 1:
            title = "特価フィード %dページ目｜%s" % (page, cfg["site"]["name"])
            desc = "%s（%dページ目）" % (cfg["site"]["description"], page)
        html = _render_top(
            cfg, base, products, cats, units=units, live=live,
            active_tab="new", page=page, total_pages=total_pages,
            pager_base="/", title=title, desc=desc, path=path,
            robots=('<meta name="robots" content="noindex,follow">' if page > 1 else ""),
            jsonld=jsonld)
        write("index.html" if page == 1 else "page/%d/index.html" % page, html)


def build_off_index(cfg, base, products, cats):
    """タブ「値下がり順」の静的ページ（/off/ と /off/page/N/）。

    並べ替えビューなので全ページ noindex,follow（sitemap にも入れない）。
    PICK UP は先頭に据え置き、残り（フィード全件）を OFF 順に並べ替える。
    """
    live = [p for p in products if not sale_over(p) and not sale_soon(p)]
    ordered, pick, base_units = top_units(live)
    # TOP のストリーム（PICK UP＋フィード）から PICK UP を除いたユニットを、
    # 値下がり幅の大きい順に並べ替える（試作の「値下がり順」タブと同じ対象）。
    rest_units = [(k, p) for k, p in base_units if k != "pick"]
    rest_units.sort(
        key=lambda kp: (median_off(kp[1])[1], kp[1].get("reviewCount") or 0),
        reverse=True)
    units = ([("pick", pick)] if pick is not None else []) + rest_units
    total_pages = max(1, -(-len(units) // TOP_PER_PAGE))
    site_url = cfg["site"]["url"].rstrip("/")

    for page in range(1, total_pages + 1):
        path = "/off/" if page == 1 else "/off/page/%d/" % page
        links = ""
        if page > 1:
            links += '<link rel="prev" href="%s">' % (
                site_url + ("/off/" if page == 2 else "/off/page/%d/" % (page - 1)))
        if page < total_pages:
            links += '<link rel="next" href="%s/off/page/%d/">' % (site_url, page + 1)
        title = "値下がり順の特価｜%s" % cfg["site"]["name"]
        desc = "検証を通った特価を、値下がり幅（価格履歴の中央値比）の大きい順に並べたページです。"
        if page > 1:
            title = "値下がり順の特価 %dページ目｜%s" % (page, cfg["site"]["name"])
            desc = "%s（%dページ目）" % (desc, page)
        html = _render_top(
            cfg, base, products, cats, units=units, live=live,
            active_tab="off", page=page, total_pages=total_pages,
            pager_base="/off/", title=title, desc=desc, path=path,
            robots='<meta name="robots" content="noindex,follow">',
            jsonld=links)
        write("off/index.html" if page == 1 else "off/page/%d/index.html" % page, html)


def build_categories(cfg, base, products, cats):
    # 一覧ページ
    counts = {}
    for p in products:
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    cat_cards = "".join(
        '<a class="cat-card" href="/c/%s/">%s'
        '<span class="l">%s</span><span class="c">%d件</span></a>'
        % (c["slug"], icons.use(c["icon"], "ic-xl"), e(c["short"]), counts.get(c["slug"], 0))
        for c in cfg["categories"]
    )
    content = """
<section class="page-head wrap">
  <p class="page-eyebrow">売場の地図</p>
  <h1 class="page-title">カテゴリ一覧</h1>
  <p class="page-lead">気になる売場から、値下がりしたものだけを覗けます。</p>
</section>
<section class="wrap"><div class="cat-grid">%s</div></section>
""" % cat_cards
    write("categories/index.html", page_shell(
        cfg, base, title="カテゴリ一覧｜%s" % cfg["site"]["name"],
        desc="ヤスミルの全カテゴリ。食品・家電・日用品など、売場ごとに特価をまとめています。",
        path="/categories/", content=content, chipbar=render_chipbar(cfg),
        products=products, cats=cats, bodyclass="v11",
    ))

    # 各カテゴリ
    per = cfg["feed"]["perPage"]
    fetched = last_fetch_date(products)
    site_url = cfg["site"]["url"].rstrip("/")
    km = shop_totals(products) if active_kaimawari_event() else None
    for c in cfg["categories"]:
        items = [p for p in products
                 if p["category"] == c["slug"]
                 and not sale_over(p) and not sale_soon(p)]
        items.sort(key=feed_order, reverse=True)
        best = max((discount_rate(p) for p in items), default=0)
        total_pages = max(1, -(-len(items) // per))
        cbase = "/c/%s/" % c["slug"]

        for page in range(1, total_pages + 1):
            chunk = items[(page - 1) * per: page * per]
            if chunk:
                # v4: 旧・横長カード（render_card）をやめ、TOP と同じ
                # ストリーム型カードに統一する。PC は2カラムグリッド、
                # スマホは横長行（.v-stream の既存レイアウトが担う）。
                body = '<div class="v-stream v-stream-cat">%s</div>' % "".join(
                    render_stream_card(p, cats, "検証済み") for p in chunk)
            else:
                body = ('<div class="empty">' + icons.use("tag", "ic-xxl") +
                        '<p class="empty-title">この売場はまだ空っぽです</p>'
                        '<p>値下がりを見つけ次第ここに並べます。</p></div>')

            lead = "%d件掲載%s" % (len(items), "・最大%d%%OFF" % best if best else "・値下がりを監視中")
            if page > 1:
                lead += "（%d/%dページ）" % (page, total_pages)

            content = """
<section class="page-head wrap-narrow">
  <p class="page-eyebrow">{icon}この売場</p>
  <h1 class="page-title">{label}の特価</h1>
  <p class="page-lead">{lead}</p>
</section>
<div class="layout wrap-wide">
<div class="layout-main">{body}
  {pager}
  <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。価格・在庫・送料は取得時点のものです。</p>
</div>
{sidebar}
</div>
""".format(icon=icons.use(c["icon"]), label=e(c["label"]), lead=lead, body=body,
           pager=render_pager_v11(page, total_pages, cbase),
           sidebar=render_sidebar(cfg, products, cats, active=c["slug"]))

            links = ""
            if page > 1:
                links += '<link rel="prev" href="%s%s">' % (
                    site_url, cbase if page == 2 else "%spage/%d/" % (cbase, page - 1))
            if page < total_pages:
                links += '<link rel="next" href="%s%spage/%d/">' % (site_url, cbase, page + 1)

            title = "%sの特価まとめ｜%s" % (c["label"], cfg["site"]["name"])
            if page > 1:
                title = "%sの特価 %dページ目｜%s" % (c["label"], page, cfg["site"]["name"])

            path = cbase if page == 1 else "%spage/%d/" % (cbase, page)
            out = ("c/%s/index.html" % c["slug"] if page == 1
                   else "c/%s/page/%d/index.html" % (c["slug"], page))
            write(out, page_shell(
                cfg, base, title=title,
                desc="%sの値下がり商品だけをまとめています。%s" % (c["label"], cfg["site"]["description"]),
                path=path, content=content, jsonld=links,
                chipbar=render_chipbar(cfg, active=c["slug"]),
                products=products, cats=cats, bodyclass="v11",
                robots=('<meta name="robots" content="noindex,follow">' if page > 1 else ""),
            ))


_PRICE_HISTORY = None


def price_history():
    """price_history.json（自前で観測した価格の記録）を読み込む。"""
    global _PRICE_HISTORY
    if _PRICE_HISTORY is None:
        path = os.path.join(ROOT, "price_history.json")
        _PRICE_HISTORY = load("price_history.json") if os.path.exists(path) else {}
    return _PRICE_HISTORY


def history_facts(p):
    """その商品の価格履歴から、ページに出せる事実だけを組み立てる。

    数字はすべて price_history.json の実測から取る。履歴が無い・
    観測が1件だけなら None を返し、ページ側は何も出さない。
    言えることと言えないことを分けるのが役目:
      - 中央値・最安は全観測から機械的に出せるので常に出せる
      - 「いつからこの価格か」は、履歴の最後の観測価格がいまの価格と
        一致しているときだけ言える（後ろ向きに同じ価格が続く範囲＝
        現在価格の開始日）。一致しなければ、開始日は主張しない
    """
    raw = price_history().get(p["id"]) or []
    obs = []
    for dstr, val in raw:
        try:
            d = datetime.strptime(dstr, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        obs.append((d, int(val)))
    obs.sort(key=lambda x: x[0])
    if len(obs) < 2:
        return None

    prices = [v for _d, v in obs]
    cur = int(p["price"])
    median = statistics.median(prices)
    drop_pct = (median - cur) / median * 100 if median else 0.0

    facts = {
        "obs": obs,
        "n": len(obs),
        "first": obs[0][0],
        "last": obs[-1][0],
        "median": median,
        "min_price": min(prices),
        "max_price": max(prices),
        "drop_pct": drop_pct,
        "is_lowest": cur == min(prices),
        "drop_start": None,   # 現在価格が始まった日（言えるときだけ入る）
        "prev_price": None,   # 下落直前の価格
        "days_held": None,    # 現在価格のまま観測が続いた日数（両端含む）
        "flat_since_start": False,  # 観測開始から一度も値段が変わっていない
    }

    if obs[-1][1] == cur:
        i = len(obs)
        while i > 0 and obs[i - 1][1] == cur:
            i -= 1
        facts["days_held"] = (obs[-1][0] - obs[i][0]).days + 1
        if i == 0:
            facts["flat_since_start"] = True
        else:
            facts["drop_start"] = obs[i][0]
            facts["prev_price"] = obs[i - 1][1]
    return facts


def _pct_label(x):
    """75.3 → '75.3'、50.0 → '50'。"""
    return "%g" % round(x, 1)


def render_history_chart(f):
    """価格履歴のステップグラフ（インラインSVG）。

    観測は日次のスナップショットで、価格は階段状に動く。
    点を直線で結ぶと「なだらかに下がった」ように見えて実測と違うので、
    横線→縦線のステップで描く。描画範囲は全期間。ただし観測期間が
    90日を超えるものは直近90日に絞る（それより古い動きは
    今回の値段の根拠として弱い）。
    """
    obs = f["obs"]
    if (f["last"] - f["first"]).days > 90:
        from datetime import timedelta as _td
        cut = f["last"] - _td(days=90)
        obs = [o for o in obs if o[0] >= cut] or obs[-2:]
    d0, d1 = obs[0][0], obs[-1][0]
    vals = [v for _d, v in obs]
    vmax, vmin = max(vals), min(vals)

    W, H = 660, 250
    x_l, x_r = 56.0, 652.0        # 左は価格ラベル置き場
    y_t, y_b = 34.0, 208.0        # 上は現在価格ラベル、下は日付ラベル置き場
    span_days = max((d1 - d0).days, 1)

    def X(d):
        return x_l + (x_r - x_l) * (d - d0).days / span_days

    def Y(v):
        if vmax == vmin:
            return (y_t + y_b) / 2
        return y_b - (y_b - y_t) * (v - vmin) / (vmax - vmin)

    # ステップ状のパス
    pts = [(X(d), Y(v)) for d, v in obs]
    path = ["M %.1f,%.1f" % pts[0]]
    for (px, py), (cx, cy) in zip(pts, pts[1:]):
        if cx > px:
            path.append("H %.1f" % cx)
        if cy != py:
            path.append("V %.1f" % cy)
    line = "".join(path)
    area = line + " L %.1f,%.1f L %.1f,%.1f Z" % (pts[-1][0], y_b, pts[0][0], y_b)

    out = ['<svg class="hist-chart" viewBox="0 0 %d %d" width="100%%" role="img" '
           'aria-label="価格の推移グラフ">' % (W, H)]
    # 最安と最高の水準線＋価格ラベル
    for v, anchor_y in ((vmax, Y(vmax)), (vmin, Y(vmin))):
        out.append('<line class="hist-grid" x1="%.0f" y1="%.1f" x2="%.0f" y2="%.1f"/>'
                   % (x_l, anchor_y, x_r, anchor_y))
        out.append('<text class="hist-t" x="%.0f" y="%.1f" text-anchor="end">¥%s</text>'
                   % (x_l - 6, anchor_y + 4, yen(v)))
    out.append('<path class="hist-area" d="%s"/>' % area)
    out.append('<path class="hist-line" d="%s"/>' % line)

    # 下落の開始点（言えるときだけ）
    if f["drop_start"] and f["prev_price"] and f["prev_price"] > 0:
        mx, my = X(f["drop_start"]), Y(vals[-1])
        out.append('<circle class="hist-dot" cx="%.1f" cy="%.1f" r="5"/>' % (mx, my))
        mid_y = (Y(f["prev_price"]) + my) / 2
        if mx < 240:
            out.append('<text class="hist-t" x="%.1f" y="%.1f">%d/%dに下落</text>'
                       % (mx + 10, mid_y + 4, f["drop_start"].month, f["drop_start"].day))
        else:
            out.append('<text class="hist-t" x="%.1f" y="%.1f" text-anchor="end">%d/%dに下落</text>'
                       % (mx - 10, mid_y + 4, f["drop_start"].month, f["drop_start"].day))

    # 終端: いまの観測価格
    last_v = vals[-1]
    now_txt = "いま ¥%s%s" % (yen(last_v), "（過去最安）" if last_v == f["min_price"] else "")
    out.append('<text class="hist-t-now" x="%.0f" y="%.1f" text-anchor="end">%s</text>'
               % (x_r, max(Y(last_v) - 10, 14), now_txt))
    # 始端の価格
    out.append('<text class="hist-t" x="%.1f" y="%.1f">¥%s</text>'
               % (pts[0][0] + 2, max(Y(vals[0]) - 8, 14), yen(vals[0])))
    # 日付ラベル
    out.append('<text class="hist-t" x="%.0f" y="%.0f">%02d/%02d</text>'
               % (x_l, H - 6, d0.month, d0.day))
    out.append('<text class="hist-t" x="%.0f" y="%.0f" text-anchor="end">%02d/%02d</text>'
               % (x_r, H - 6, d1.month, d1.day))
    out.append("</svg>")
    return "".join(out)


def render_history_block(p):
    """商品ページの「価格の推移」ブロック。価格ブロック＋グラフ＋事実文。

    数字の出どころは price_history.json のみ。店の言う参考価格とは
    完全に別の物として置く（ここは当サイトが観測した記録）。
    """
    f = history_facts(p)
    if not f:
        return ""
    cur = int(p["price"])

    badges = []
    if f["is_lowest"]:
        badges.append('<span class="hist-badge is-low">過去最安</span>')
    if f["drop_start"] and f["prev_price"] and f["prev_price"] > cur and f["days_held"]:
        badges.append('<span class="hist-badge is-drop">%02d/%02dから値下げ中（%d日目）</span>'
                      % (f["drop_start"].month, f["drop_start"].day, f["days_held"]))
    if free_shipping(p):
        badges.append('<span class="hist-badge is-free">送料無料</span>')
    badge_html = '<div class="hist-badges">%s</div>' % "".join(badges) if badges else ""

    ship_txt = "送料無料" if free_shipping(p) else "送料は別途かかります"
    summary = ('<p class="hist-summary">いま <b>¥%s</b>（%s）／'
               '当サイトが観測した価格の中央値 ¥%s から <b>−%s%%</b></p>'
               % (yen(cur), ship_txt, yen(f["median"]), _pct_label(f["drop_pct"])))

    fact = ""
    if f["drop_start"] and f["prev_price"] and f["prev_price"] > cur:
        ds = f["drop_start"]
        if f["days_held"] and f["days_held"] >= 2:
            fact = ('<p class="hist-fact">%d月%d日に ¥%s から ¥%s へ下落。'
                    'それから %d 日間、この価格のままです。</p>'
                    % (ds.month, ds.day, yen(f["prev_price"]), yen(cur), f["days_held"]))
        else:
            fact = ('<p class="hist-fact">%d月%d日に ¥%s から ¥%s へ下落しました。</p>'
                    % (ds.month, ds.day, yen(f["prev_price"]), yen(cur)))
    elif f["flat_since_start"]:
        fact = ('<p class="hist-fact">観測を始めた %d月%d日 から、ずっとこの価格です。</p>'
                % (f["first"].month, f["first"].day))

    note = ('<p class="hist-note">当サイトが %d月%d日〜%d月%d日 に %d 回観測した価格の記録です。</p>'
            % (f["first"].month, f["first"].day, f["last"].month, f["last"].day, f["n"]))

    return ('<h2 class="section-title">価格の推移</h2>'
            '<div class="hist">%s%s%s%s%s</div>'
            % (badge_html, summary, render_history_chart(f), fact, note))


def render_criteria_block(p):
    """商品ページの「掲載基準と、この商品の実測」。

    5ゲートの文言は掲載基準ポップアップと同一。各ゲートに添える実測値は
    products.json と価格履歴（history_facts）の実測だけから作る。
    算出できない項目は数値を出さず、文言のみにする（創作禁止）。
    """
    f = history_facts(p)
    smalls = ["", "", "", "", ""]
    if f:
        span = (f["last"] - f["first"]).days + 1
        smalls[0] = "実測：%s〜%sに%d回観測（%d日間）" % (
            f["first"].strftime("%-m月%-d日"), f["last"].strftime("%-m月%-d日"),
            f["n"], span)
        if f["is_lowest"]:
            smalls[1] = "実測：いまの価格 ¥%s は、観測期間の最安です" % yen(p["price"])
        else:
            smalls[1] = "実測：観測期間の最安は ¥%s です" % yen(f["min_price"])
        smalls[2] = "実測：中央値 ¥%s に対して −%s%%（現在 ¥%s）" % (
            yen(f["median"]), _pct_label(f["drop_pct"]), yen(p["price"]))
    if p.get("reviewAverage"):
        smalls[3] = "実測：評価 ★%s・レビュー %s件" % (
            e(p["reviewAverage"]), yen(p.get("reviewCount", 0)))
    smalls[4] = ("実測：送料無料です" if free_shipping(p)
                 else "実測：送料は別途かかります")
    gates = [
        "価格履歴を30日以上、観測している",
        "いまの価格が、観測期間で最安",
        "中央値より −20%以上 安い",
        "レビュー100件以上・評価 ★4.2以上",
        "送料無料で、「なぜお得か」を一言で言える",
    ]
    rows = "".join(
        '<div class="v-gate"><span class="v-gate-no">%d</span>'
        '<span class="v-gate-text">%s%s</span></div>'
        % (i + 1, g, ("<small>%s</small>" % smalls[i]) if smalls[i] else "")
        for i, g in enumerate(gates))
    return ('<h2 class="section-title">掲載基準と、この商品の実測</h2>'
            '<div class="v-trust">%s'
            '<p class="v-criteria-def">％OFFと比較価格（取消線）の比較元は、'
            '価格履歴30日以上の<strong>中央値</strong>です。</p></div>' % rows)


def build_products(cfg, base, products, cats):
    fetched = last_fetch_date(products)
    by_cat = {}
    for p in products:
        by_cat.setdefault(p["category"], []).append(p)

    for p in products:
        c = cats.get(p["category"], {})
        d = discount_rate(p)
        site_url = cfg["site"]["url"].rstrip("/")

        points = ""
        if p.get("points"):
            points = "<ul>%s</ul>" % "".join("<li>%s</li>" % e(x) for x in p["points"])

        rows = [("価格", '<td class="price-cell">¥%s</td>' % yen(p["price"]))]
        if d:
            # 値引きの根拠は3つある。どれなのかを混ぜない。
            # 自分で観測した値と、店が名乗った値は、別のものとして書く。
            lab = price_basis_label(p)
            if lab == "以前":
                basis = "以前の価格"
                note = "<br><small>当サイトが過去60日で観測した最高値です</small>"
            elif lab == "セール前":
                basis = "セール前の価格"
                note = ("<br><small>ショップが商品名に書いていた値で、"
                        "実売価格と一致することを確かめています。"
                        "当サイトが以前に観測した値ではありません</small>")
            else:
                basis = "通常価格"
                note = ""
            rows.append((basis, "<td>¥%s（%d%%OFF）%s</td>" % (yen(p["listPrice"]), d, note)))
        if p.get("unitNote"):
            rows.append(("単価の目安", "<td>%s</td>" % e(p["unitNote"])))
        rows.append(("カテゴリ", '<td><a class="inline-cat" href="/c/%s/">%s%s</a></td>' % (
            p["category"], icons.use(c.get("icon", "tag")), e(c.get("label", "")))))
        # 送料は必ず1行取る。無料のときだけ書いて、かかるときに黙るのは
        # 安く見せているのと同じ。金額は楽天のデータに無いので額は書かない。
        rows.append(("送料", "<td>%s</td>" % (
            "無料（楽天のデータより）" if free_shipping(p)
            else '<b class="spec-ship">別途かかります</b>'
                 ' — 商品代とは別に送料が乗ります。'
                 '金額は届け先で変わるので、楽天のページでご確認ください。')))
        rows.append(("ショップ", "<td>%s</td>" % e(p.get("shop", "楽天市場"))))
        if p.get("reviewAverage"):
            rows.append(("レビュー", "<td>%s%s（%s件）</td>" % (
                icons.use("star", "ic-star"), p["reviewAverage"],
                yen(p.get("reviewCount", 0)))))
        rows.append(("掲載日", "<td>%s</td>" % e(p.get("postedAt", ""))))
        if p.get("lastSeen"):
            rows.append(("価格の確認日", "<td>%s%s</td>" % (
                e(p["lastSeen"]),
                "" if not is_stale(p, fetched) else "<br><small>それ以降は変わっている可能性があります</small>")))
        spec = "".join("<tr><th>%s</th>%s</tr>" % (k, v) for k, v in rows)

        # 関連商品（同カテゴリ）。ページが生成される商品だけをリンクする。
        # 厳格モードでは公開対象が選定分だけなので、対象外の商品を関連に
        # 出すと、存在しないページへのリンク（死にリンク）になる。
        # 候補は products.json 全体から取り、生成されるものを優先して
        # 並べたうえで、生成されるものだけに絞る。
        _all = load("products.json")
        _all = _all["products"] if isinstance(_all, dict) else _all
        generated_ids = {x["id"] for x in products}
        rel_cands = [x for x in _all
                     if x.get("category") == p["category"] and x["id"] != p["id"]
                     and not x.get("hidden")]
        rel_cands.sort(key=lambda x: (x["id"] not in generated_ids, -discount_rate(x)))
        # 関連は TOP と同じストリームカードで出す（最大6件）。
        # 候補の絞り込みは従来どおり「生成される商品だけ」なのでリンク切れは起きない。
        rel_items = [x for x in rel_cands
                     if x["id"] in generated_ids][:6]
        rel = "".join(render_stream_card(x, cats, "検証済み") for x in rel_items)
        rel_block = ""
        if rel:
            rel_block = ('<h2 class="section-title">同じ売場のもの</h2>'
                         '<div class="v-stream v-stream-rel">%s</div>' % rel)

        cap = ""
        if p.get("caption"):
            cap = '<p class="detail-cap">%s</p>' % marked(p["caption"], p)

        # セールが終わっていたら、その事実を最初に伝える。
        # 値段が戻っているのに「いま¥1,490」と出し続けるのは、
        # このサイトが一番やってはいけないこと。
        over_notice = ""
        if sale_over(p):
            over_notice = (
                '<div class="sale-over">'
                '<p class="sale-over-head">%sこのセールは終了しました</p>'
                '<p class="sale-over-body">%s の期間限定でした。'
                'いまは値段が戻っている可能性が高いので、'
                '楽天市場で最新の価格をご確認ください。</p>'
                '</div>' % (icons.use("info"), e(sale_ends_label(p))))

        # レジの表示のように、価格の脇に根拠を小さく添える
        cta_sub = ""
        if p.get("unitNote"):
            cta_sub = '<span class="cta-sub">%s</span>' % e(p["unitNote"])
        elif d:
            cta_sub = '<span class="cta-sub cta-was">%s ¥%s</span>' % (
                price_basis_label(p), yen(p["listPrice"]))
        if not free_shipping(p):
            cta_sub += '<span class="cta-sub cta-ship">＋送料</span>'

        # 「どんな商品？」の中身。
        #
        # 手で書いた description があればそれを使う。
        # 無ければ、pitch.py で作って決まりに照らして通した商品理解を使う。
        # 以前は description しか見ていなかったので、
        # 自動で足した商品では、この項目がまるごと消えていた。
        desc_src = (p.get("description") or "").strip()
        pts = list(p.get("points") or [])
        if not desc_src:
            mk = p.get("marketing") or {}
            if (p.get("pitch_status") == "ready") and mk.get("body"):
                desc_src = mk["body"]
                if not pts:
                    pts = [x for x in (mk.get("features") or []) if x]
        desc_block = ""
        if desc_src:
            paras = "".join("<p>%s</p>" % e(x)
                            for x in desc_src.split("\n") if x.strip())
            plist = ""
            if pts:
                plist = "<ul class=\"prose-points\">%s</ul>" % "".join(
                    "<li>%s</li>" % e(x) for x in pts)
            desc_block = ('<h2 class="section-title">どんな商品？</h2>'
                          '<div class="prose prose-card">%s%s</div>' % (paras, plist))

        # 項目の並び（v4で見直し）：「どんな商品？」を仕様表より前に置く。
        # 読む人はまず「何の商品か」を知りたいのであって、表は確かめに来た
        # 人のもの。価格の検証（履歴・基準）はその後に続ける。
        # 冒頭のシェアボタンは末尾と重複するので外した（末尾だけに残す）。
        detail_off = ('<span class="detail-off">%d%%OFF</span>' % d) if d > 0 else ""
        sidebar = render_sidebar(cfg, products, cats, active=p["category"])
        content = """
<div class="layout layout-left wrap-wide">
  <div class="layout-main">
  <nav class="breadcrumb" aria-label="パンくず">
    <a href="/">ホーム</a><span class="sep">›</span>
    <a href="/c/{cslug}/">{clabel}</a><span class="sep">›</span>
    <span>{short}</span>
  </nav>

  <div class="detail-media">
    <img src="{img}" alt="{alt}" width="800" height="800">
    {detail_off}
  </div>

  <h1 class="detail-title">{title}</h1>
  {over_notice}
  {cap}
  <div class="detail-actions">{watch}</div>

  <div class="sticky-cta">
    <div class="sticky-cta-inner">
      <div class="sticky-cta-price">
        <span class="cta-label">{price_label}</span>
        <span class="cta-value"><span class="y">¥</span><span class="n">{price}</span></span>
        {cta_sub}
      </div>
      <a class="btn btn-rakuten btn-lg" href="{url}" target="_blank" rel="nofollow sponsored noopener">楽天市場で見る{arrow}</a>
    </div>
  </div>
  <p class="pop-note{stale_cls}">{freshness}</p>

  {desc_block}

  <h2 class="section-title">商品情報</h2>
  <table class="spec"><tbody>{spec}</tbody></table>

  {hist_block}

  {criteria_block}

  <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。上のリンクから購入があった場合、当サイトが紹介料を受け取ることがあります。</p>

  {share_bottom}

  {rel_block}
  </div>
  {sidebar}
</div>
""".format(cslug=e(p["category"]), clabel=e(c.get("label", "")),
           short=e(p["title"] if len(p["title"]) <= 18 else p["title"][:18] + "…"), img=e(p["image"]), alt=e(p["title"]),
           detail_off=detail_off, sidebar=sidebar, title=e(p["title"]),
           cap=cap, price=yen(p["price"]), cta_sub=cta_sub,
           price_label=e(price_label(p)),
           stale_cls=(" pop-note-stale" if is_stale(p, fetched) else ""),
           freshness=(
               "この価格は%sに見たものです。そのあと変わっているかもしれないので、"
               "楽天でいまの値段を確かめてください。" % stale_label(p).replace("の価格", "")
               if is_stale(p, fetched)
               else "値段は毎日見にいってます。買う前に楽天でも確かめてね（%s時点）" % e(p.get("lastSeen") or "")),
           url=e(p.get("affiliateUrl") or "#"),
           spec=spec, hist_block=render_history_block(p),
           criteria_block=render_criteria_block(p),
           arrow=icons.use("arrow-right", "ic-arrow"),
           desc_block=desc_block, rel_block=rel_block,
           share_top=render_share(p, cfg, "top"),
           watch=render_watch_btn(p, "detail"),
           over_notice=over_notice,
           share_bottom=render_share(p, cfg, "bottom"))

        offer = {
            "@type": "Offer",
            "price": p["price"],
            "priceCurrency": "JPY",
            "availability": "https://schema.org/InStock",
            "url": p.get("affiliateUrl") or (site_url + "/p/%s/" % p["id"]),
        }
        prod_ld = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": p["title"],
            "image": site_url + p["image"] if p["image"].startswith("/") else p["image"],
            "description": p.get("caption") or p["title"],
            "offers": offer,
        }
        if p.get("reviewAverage"):
            prod_ld["aggregateRating"] = {
                "@type": "AggregateRating",
                "ratingValue": p["reviewAverage"],
                "reviewCount": p.get("reviewCount", 1),
            }
        crumb_ld = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "ホーム", "item": site_url + "/"},
                {"@type": "ListItem", "position": 2, "name": c.get("label", ""),
                 "item": site_url + "/c/%s/" % p["category"]},
                {"@type": "ListItem", "position": 3, "name": p["title"]},
            ],
        }

        # ページのタイトル（検索結果向け）は商品名を前に置く
        title = "%s｜¥%s" % (p["title"], yen(p["price"]))
        if d:
            title = "【%d%%OFF】%s｜¥%s" % (d, p["title"], yen(p["price"]))

        # 共有カードのタイトルは価格を先頭に置く。
        # ThreadsやXのカードはタイトルを途中で切るので、
        # 末尾に価格を置くと一番大事な情報が消える。
        name = p["title"]
        if len(name) > 34:
            name = name[:34].rstrip("　 ") + "…"
        if d:
            ogtitle = "¥%s（%d%%OFF）%s" % (yen(p["price"]), d, name)
        else:
            ogtitle = "¥%s %s" % (yen(p["price"]), name)

        write("p/%s/index.html" % p["id"], page_shell(
            cfg, base,
            title="%s - %s" % (title, cfg["site"]["name"]),
            ogtitle=ogtitle,
            desc=(p.get("caption") or p["title"])[:110],
            path="/p/%s/" % p["id"], content=content, ogtype="article",
            chipbar=render_chipbar(cfg, active=p["category"]),
            jsonld=jsonld_block(prod_ld) + jsonld_block(crumb_ld),
            bodyclass="has-sticky-cta v11",
            ogimage=(site_url + p["image"]) if p["image"].startswith("/") else p["image"],
            products=products, cats=cats,
        ))


def load_guides():
    """content/guides/*.md を読み込む。新しい記事はここに .md を置くだけ。"""
    d = os.path.join(ROOT, "content", "guides")
    if not os.path.isdir(d):
        return []
    guides = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(d, name), encoding="utf-8") as f:
            meta, body = markdown.parse_frontmatter(f.read())
        if not meta.get("slug"):
            continue
        meta["body"] = body
        guides.append(meta)
    # 新しい順。ファイル名順だと、記事が増えたとき
    # たまたま名前が前に来たものが先頭を取ってしまう。
    guides.sort(key=lambda g: (g.get("updated", ""), g.get("slug", "")), reverse=True)
    return guides


def build_guides(cfg, base, products, cats, guides):
    site_url = cfg["site"]["url"].rstrip("/")
    links = load("links.json") if os.path.exists(os.path.join(ROOT, "links.json")) else {}

    def cta(key):
        """記事内のリンク。売場の案内看板として置く。
        URLが未設定なら何も出さない。埋まっていない看板を立てないため。"""
        item = links.get(key) or {}
        url = link_url(links, key)
        if not url:
            return ""
        return ("""<aside class="cta-sign">
  <p class="cta-sign-label">{ic}{label}</p>
  <p class="cta-sign-note">{note}</p>
  <a class="btn btn-sign" href="{url}" target="_blank" rel="nofollow sponsored noopener">{button}{arrow}</a>
</aside>""").format(
            ic=icons.use("arrow-right", "ic-sign"),
            label=e(item.get("label", "")), note=e(item.get("note", "")),
            url=e(url), button=e(item.get("button", "開く")),
            arrow=icons.use("arrow-right", "ic-arrow"))

    for g in guides:
        body_html, toc = markdown.render(g["body"], cta_renderer=cta)

        toc_html = "".join(
            '<li><a href="#%s">%s</a></li>' % (a, e(t)) for a, t in toc)

        content = """
<div class="wrap-narrow">
  <nav class="breadcrumb" aria-label="パンくず">
    <a href="/">ホーム</a><span class="sep">›</span>
    <span>{short}</span>
  </nav>
</div>

<article class="guide">
  <header class="guide-head wrap-narrow">
    <p class="page-eyebrow">{ic}攻略ガイド</p>
    <h1 class="guide-title">{title}</h1>
    <p class="guide-meta">{ic_cal}{updated} 更新</p>
    <p class="pop-note guide-warn">セールの条件は毎回変わります。買う前に楽天市場の公式ページで、その回の期間・上限・条件をかならず確かめてください。</p>
  </header>

  <div class="layout wrap-wide">
    <div class="layout-main">
      <details class="guide-toc-acc"><summary class="guide-toc-head">{ic_grid}目次</summary><nav aria-label="この記事の目次"><ol>{toc}</ol></nav></details>

      <div class="guide-body prose-guide">{body}</div>

      <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。記事内のリンクから購入があった場合、当サイトが紹介料を受け取ることがあります。</p>

      {share}
    </div>
    {sidebar}
  </div>
</article>
""".format(short=e(g.get("shortTitle", "攻略ガイド")), ic=icons.use("doc"),
           title=e(g["title"]), ic_cal=icons.use("calendar"),
           updated=e(g.get("updated", "")), ic_grid=icons.use("grid"),
           toc=toc_html, body=body_html,
           share=render_guide_share(g, cfg),
           sidebar=render_sidebar(cfg, products, cats, guides=guides,
                                  current_guide=g.get("slug")))

        jsonld = jsonld_block({
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": g["title"],
            "description": g.get("description", ""),
            "datePublished": g.get("updated", ""),
            "dateModified": g.get("updated", ""),
            "author": {"@type": "Organization", "name": cfg["site"]["name"]},
            "publisher": {"@type": "Organization", "name": cfg["site"]["name"]},
            "mainEntityOfPage": site_url + "/guide/%s/" % g["slug"],
        })

        write("guide/%s/index.html" % g["slug"], page_shell(
            cfg, base,
            title="%s｜%s" % (g["title"], cfg["site"]["name"]),
            ogtitle=g["title"],
            desc=g.get("description", ""),
            path="/guide/%s/" % g["slug"],
            content=content, ogtype="article", jsonld=jsonld,
            chipbar=render_chipbar(cfg), products=products, cats=cats,
            bodyclass="v11",
        ))


def render_guide_share(g, cfg):
    url = cfg["site"]["url"].rstrip("/") + "/guide/%s/" % g["slug"]
    text = g["title"]
    q = urllib.parse.quote
    targets = [
        # Xアプリは /intent/tweet を投稿画面として直接開く。
        # 新しい /intent/post はアプリ内ブラウザで開かれることがあり、
        # アプリ内ブラウザはアプリ本体とログイン状態を共有しないため
        # 毎回ログイン画面が出てしまう。実績のある方を使う。
        ("sns-x", "X", "https://twitter.com/intent/tweet?text=%s&url=%s" % (q(text), q(url))),
        ("sns-threads", "Threads",
         "https://www.threads.net/intent/post?text=%s" % q(text + "\n" + url)),
        ("sns-line", "LINE",
         "https://social-plugins.line.me/lineit/share?url=%s&text=%s" % (q(url), q(text))),
    ]
    btns = "".join(
        '<a class="share-btn share-%s" href="%s" target="_blank" rel="noopener nofollow" '
        'aria-label="%sでシェア">%s<span>%s</span></a>'
        % (n.replace("sns-", ""), e(h), l, icons.use(n), l) for n, l, h in targets)
    return ('<div class="share share-bottom">'
            '<p class="share-heading">役に立ったら、誰かに教える</p>'
            '<div class="share-row">%s</div></div>' % btns)


def build_static_pages(cfg, base, products, cats):
    site = cfg["site"]
    pages = load("pages.json")
    form_url = site.get("contactFormUrl") or ""
    if form_url:
        contact_block = (
            '<p style="margin:22px 0 6px"><a class="btn btn-rakuten btn-lg btn-block" '
            'href="%s" target="_blank" rel="noopener">%s お問い合わせフォームを開く</a></p>'
            '<p style="font-size:.78rem;color:var(--ink-52)">Googleフォームが別のタブで開きます。</p>'
            % (e(form_url), icons.use("mail"))
        )
    else:
        contact_block = (
            '<p class="affiliate-note">お問い合わせフォームは準備中です。'
            'config.json の <code>site.contactFormUrl</code> にフォームのURLを入れて '
            '<code>build.py</code> を実行すると、ここにボタンが出ます。</p>'
        )

    for slug, pg in pages.items():
        content = """
<section class="page-head wrap">
  <p class="page-eyebrow">%s</p>
  <h1 class="page-title">%s</h1>
</section>
<section class="wrap"><div class="prose prose-card">%s</div></section>
""" % (e(pg.get("eyebrow", "")), e(pg["title"]),
       pg["body"].replace("{{CONTACT_FORM}}", contact_block)
                 .replace("{{OPERATOR}}", render_operator(cfg)))
        write("%s/index.html" % slug, page_shell(
            cfg, base,
            title="%s｜%s" % (pg["title"], site["name"]),
            desc=pg["description"], path="/%s/" % slug, content=content,
            chipbar=render_chipbar(cfg), products=products, cats=cats,
            bodyclass="v11",
            robots='<meta name="robots" content="index,follow">',
        ))

    # 404
    content = """
<div class="wrap">
  <div class="empty">
    {ic}
    <p class="empty-title">ページが見つかりませんでした</p>
    <p>売り切れたか、URLが変わったようです。</p>
    <p style="margin-top:20px"><a class="btn btn-rakuten btn-lg" href="/">特価フィードへ戻る</a></p>
  </div>
</div>
""".format(ic=icons.use("tag", "ic-xxl"))
    write("404.html", page_shell(
        cfg, base, title="ページが見つかりません｜%s" % site["name"],
        desc="お探しのページは見つかりませんでした。", path="/404.html",
        content=content, chipbar=render_chipbar(cfg), products=products, cats=cats,
        bodyclass="v11",
        robots='<meta name="robots" content="noindex,follow">',
    ))


def build_feed_json(cfg, products, cats):
    """クライアント側の絞り込み・並べ替え・検索用の軽いデータ"""
    slim = []
    for p in sorted(products, key=feed_order, reverse=True):
        # 終わったセールは、絞り込みでもガチャでもクイズでも出さない
        if sale_over(p) or sale_soon(p):
            continue
        c = cats.get(p["category"], {})
        slim.append({
            "id": p["id"], "t": p["title"], "cap": p.get("caption", ""),
            "mk": marker_no(p),
            "c": p["category"], "ci": c.get("icon", ""), "cl": c.get("short", ""),
            "pr": p["price"], "lp": p.get("listPrice") or 0, "d": discount_rate(p),
            "b": price_basis_label(p),
            "rc": p.get("reviewCount") or 0, "ra": p.get("reviewAverage") or 0,
            "u": p.get("unitNote", ""), "img": p["image"], "url": p.get("affiliateUrl") or "#",
            "shop": p.get("shop", "楽天市場"), "tags": (p.get("tags") or [])[:3],
            "at": p.get("bumpedAt") or ((p.get("postedAt") or "") + "T00:00:00"),
            # 終了時刻。ビルドの間隔（最大6時間）のあいだに終わるセールを
            # 読む人の時刻で判定できるように、そのまま渡す。
            "et": (p.get("endTime") or "").strip(),
            # 最後に楽天で値段を確かめた日。投稿の鮮度を、見つけた日ではなく
            # 確かめた日で見るため（threads.py pick）。
            "ls": p.get("lastSeen") or "",
            # 投稿の「用途」の型で使う。箇条書きは事実だけを書いてある。
            "pt": (p.get("points") or [])[:3],
            # 親投稿のもと。pitch.py が作って、決まりに照らして通ったものだけ。
            # 読者向けの画面では使わないが、投稿を組み立てるのに要る。
            "mk2": ((p.get("marketing") or {}) if p.get("pitch_status") == "ready" else None),
        })
    write("assets/data/feed.json", json.dumps(slim, ensure_ascii=False, separators=(",", ":")))

    # 開始前の商品は通常のフィードから外してある（まだ買えないので）。
    # ただし投稿台では使う。セールが始まる前に告知を流したいので、
    # ここだけ別のファイルに書き出す。読者向けの画面では読み込まない。
    soon = []
    for p in sorted(products, key=lambda x: (x.get("startTime") or "")):
        if sale_over(p) or not sale_soon(p):
            continue
        c = cats.get(p["category"], {})
        soon.append({
            "id": p["id"], "t": p["title"], "cap": p.get("caption", ""),
            "c": p["category"], "ci": c.get("icon", ""), "cl": c.get("short", ""),
            "pr": p["price"], "lp": p.get("listPrice") or 0, "d": discount_rate(p),
            "b": price_basis_label(p),
            "rc": p.get("reviewCount") or 0, "ra": p.get("reviewAverage") or 0,
            "u": p.get("unitNote", ""), "img": p["image"],
            "shop": p.get("shop", "楽天市場"), "tags": (p.get("tags") or [])[:3],
            "st": (p.get("startTime") or "").strip(),
            "et": (p.get("endTime") or "").strip(),
            "sl": sale_starts_label(p),
            "pt": (p.get("points") or [])[:3],
        })
    write("assets/data/soon.json", json.dumps(soon, ensure_ascii=False, separators=(",", ":")))

    # サイドバーのカレンダー用。手で書いた events.json をそのまま配る。
    ev_path = os.path.join(ROOT, "events.json")
    if os.path.exists(ev_path):
        with open(ev_path, encoding="utf-8") as f:
            ev = json.load(f)
        links = load("links.json") if os.path.exists(
            os.path.join(ROOT, "links.json")) else {}

        def resolve(entries):
            """links の to を、実際のURLに置き換える。

            links.json のキー（entry / sale など）はアフィリエイトのリンクなので、
            外部リンクとして印を付けて渡す。ブラウザ側で
            rel="nofollow sponsored noopener" と target を付けるため。
            サイト内のパスはそのまま。"""
            out = []
            for e2 in entries or []:
                got = []
                for l in (e2.get("links") or []):
                    to = l.get("to") or ""
                    if to.startswith("/"):
                        got.append({"label": l.get("label", ""), "url": to, "ext": False})
                        continue
                    url = link_url(links, to)
                    if url:
                        got.append({"label": l.get("label", ""), "url": url, "ext": True})
                e3 = dict(e2)
                e3["links"] = got
                out.append(e3)
            return out

        write("assets/data/events.json", json.dumps(
            {"events": resolve(ev.get("events")),
             "recurring": resolve(ev.get("recurring"))},
            ensure_ascii=False, separators=(",", ":")))


def build_quiz(cfg, base, products, cats):
    """値段あてクイズのページ。

    このサイトのテーマは「安さを見抜く」なので、遊びも同じ筋にしてある。
    出題データは商品一覧と同じ feed.json をJSが引く。
    サーバー側の記録は持たない（自己最高だけ端末に残す）。
    """
    site_url = cfg["site"]["url"].rstrip("/")
    content = """
<section class="page-head wrap-narrow">
  <p class="page-eyebrow">{ic}あそぶ</p>
  <h1 class="page-title">これ、いくら？</h1>
  <p class="page-lead">写真と商品名だけ見て、値段を当ててください。全5問です。
  出題はすべて、いま実際に掲載している商品から選んでいます。</p>
</section>

<div class="layout wrap-wide">
<div class="layout-main">
  <div id="quiz" data-sprite="{sprite}" data-url="{site}">
    <div class="q-card"><p class="q-ask">読み込んでいます…</p></div>
  </div>
  <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。
  出題に使っている価格は取得時点のもので、変動します。
  購入前に楽天市場の商品ページで最新の条件をご確認ください。</p>
</div>
{sidebar}
</div>
""".format(ic=icons.use("quiz"), sprite=e(icons.sprite_href()), site=e(site_url),
           sidebar=render_sidebar(cfg, products, cats))

    write("quiz/index.html", page_shell(
        cfg, base,
        title="これ、いくら？｜値段あてクイズ",
        desc="写真と商品名だけ見て、楽天の特価商品の値段を当てるクイズです。全5問。"
             "出題はすべて、ヤスミルが実際に掲載している商品から選んでいます。",
        path="/quiz/",
        content=content,
        products=products, cats=cats, bodyclass="v11",
        scripts='<script src="%s" defer></script>' % asset_url("/assets/js/quiz.js")))


def build_kaimawari(cfg, base, products, cats):
    """買いまわりに使えるものだけを集めたページ。

    買いまわりは1ショップ税込1,000円以上で1カウント。
    そして必要なのは「別々の店」で、同じ店を何度使っても1のまま。
    だから本当に欲しいのは「安い商品の一覧」ではなく、
    「別々の店から1つずつ、なるべく安く」という組み方になる。

    送料は1,000円の判定に入らないので、送料別は入れない。
    1,000円＋送料590円は、支払い1,590円でカウントは1。
    それなら1,200円の送料無料のほうが安くて同じ1カウントになる。

    並べるのは、すでにこのサイトに載っている商品だけ。
    買いまわりのために別口で商品を集めることはしない。
    「安ければ何でも」を始めると、値下がりを載せるサイトではなくなる。
    """
    ev = active_kaimawari_event()

    ok = [p for p in products
          if not sale_over(p) and not sale_soon(p)
          and p["price"] >= KAIMAWARI_MIN and free_shipping(p)]
    ok.sort(key=lambda p: (p["price"], -(p.get("reviewCount") or 0)))

    # 店ごとに一番安いものを1つ。買いまわりは別々の店でないと数が増えない。
    picked, seen = [], set()
    for p in ok:
        sh = p.get("shop") or ""
        if sh in seen:
            continue
        seen.add(sh)
        picked.append(p)

    rows, total = "", 0
    for i, p in enumerate(picked[:10], 1):
        total += p["price"]
        cat = cats.get(p["category"], {})
        rows += (
            '<li class="kw-row">'
            '<span class="kw-no">{i}</span>'
            '<a class="kw-media" href="/p/{id}/">'
            '<img src="{img}" alt="" loading="lazy" width="120" height="120"></a>'
            '<span class="kw-body">'
            '<span class="kw-shop">{ic}{shop}</span>'
            '<a class="kw-title" href="/p/{id}/">{t}</a>'
            '<span class="kw-price"><i>¥</i>{pr}'
            '<b>ここまで¥{tot}</b></span>'
            '</span></li>'
        ).format(i=i, id=e(p["id"]), img=e(p["image"]), t=e(p["title"]),
                 ic=icons.use(cat.get("icon", "tag"), "kw-ic"),
                 shop=e(p.get("shop", "楽天市場")),
                 pr=yen(p["price"]), tot=yen(total))

    basket = ""
    if len(picked) >= 2:
        basket = """
  <div class="kw-basket">
    <p class="kw-basket-head">{ic}別々の店から、安い順に{n}件</p>
    <p class="kw-basket-note">この{n}件を買うと{n}カウントになります。
    合計は<b>¥{tot}</b>です。ただし、これは<b>いま必要でないものを
    買う話ではありません</b>。要らないものを足して増えるポイントは、
    足した金額より小さいのがふつうです。</p>
    <ol class="kw-list">{rows}</ol>
  </div>""".format(ic=icons.use("check", "ic-km"), n=len(picked[:10]),
                   tot=yen(total), rows=rows)

    cards = "".join(render_card(p, cats, "", shop_totals(products) if ev else None)
                    for p in ok[:60])

    lead = ("いま開催中の%sで使えます。" % ev["name"]) if ev else \
           "次のセールに備えて見ておけます。"

    content = """
<section class="page-head wrap-narrow">
  <p class="page-eyebrow">{ic}買いまわり</p>
  <h1 class="page-title">買いまわりに使えるもの</h1>
  <p class="page-lead">{lead}
  1ショップ税込1,000円以上で1カウントになります。
  ここに出しているのは<b>1,000円以上で送料無料</b>のものだけです。
  送料は1,000円の判定に入らないので、送料別のものは入れていません。</p>
</section>

<div class="wrap-narrow">{basket}</div>

<div class="layout wrap-wide">
<div class="layout-main">
  <div class="toolbar">
    <p class="result-count"><b>{n}</b> 件が条件を満たしています</p>
  </div>
  <div class="feed">{cards}</div>
  <div class="watch-note">
    <p class="watch-note-head">{ic2}この一覧の作り方</p>
    <ul>
      <li>1ショップ税込<b>1,000円以上</b>で1カウント。判定は商品ごとではなく<b>その店での合計</b>です。</li>
      <li><b>送料は判定に入りません。</b>1,000円＋送料590円は支払い1,590円でカウントは1。
      それなら1,200円の送料無料のほうが安くて同じ1カウントです。だから送料別は載せていません。</li>
      <li>必要なのは<b>別々の店</b>です。同じ店で何度買っても1のままなので、
      同じ店にまとめるなら送料を1回で済ませたほうが得です。</li>
      <li>ここに並ぶのは<b>もともとこのサイトに載っている商品だけ</b>です。
      買いまわりのために別口で商品を集めてはいません。</li>
      <li>買う順番はカウントに関係ありません。詳しくは
      <a href="/guide/rakuten-marathon/">お買い物マラソンの攻略法</a>にまとめています。</li>
    </ul>
  </div>
</div>
{sidebar}
</div>
""".format(ic=icons.use("check"), ic2=icons.use("info"), lead=lead,
           basket=basket, cards=cards, n=len(ok),
           sidebar=render_sidebar(cfg, products, cats))

    write("kaimawari/index.html", page_shell(
        cfg, base,
        title="買いまわりに使えるもの｜1,000円以上・送料無料｜%s" % cfg["site"]["name"],
        desc="楽天の買いまわりは1ショップ税込1,000円以上で1カウント。"
             "送料は判定に入らないので、1,000円以上かつ送料無料のものだけを集めました。"
             "別々の店から1つずつ選ぶ組み方も出しています。",
        path="/kaimawari/",
        content=content,
        products=products, cats=cats, bodyclass="v11"))


def build_watchlist(cfg, base, products, cats):
    """ウォッチリストのページ。

    会員登録は要らない。保存先はその端末のブラウザで、
    サーバーには何も送らない。そのぶん、端末を変えると引き継げないし、
    ブラウザのデータを消すと無くなる。これはページ上に明記する。
    """
    content = """
<section class="page-head wrap-narrow">
  <p class="page-eyebrow">{ic}あとで見る</p>
  <h1 class="page-title">気になるリスト</h1>
  <p class="page-lead">ハートを押した商品がここに並びます。登録も名前も要りません。</p>
</section>

<div class="layout wrap-wide">
<div class="layout-main">
  <div class="toolbar">
    <p class="result-count"><b id="watchCount">0</b> 件を保存中</p>
    <button class="btn btn-ghost btn-sm" type="button" id="watchClear" hidden>すべて外す</button>
  </div>
  <div class="feed" id="watchFeed"></div>
  <div class="watch-note">
    <p class="watch-note-head">{ic2}このリストについて</p>
    <ul>
      <li>保存先は<b>このブラウザの中だけ</b>です。サーバーには何も送っていません。</li>
      <li>そのため、別の端末やブラウザでは開けません。</li>
      <li>ブラウザの履歴やサイトデータを消すと、リストも消えます。</li>
      <li>価格は保存した時点のものではなく、<b>いまの価格</b>を表示します。</li>
      <li>掲載が終わった商品は、リストからも自動で消えます。</li>
    </ul>
  </div>
</div>
{sidebar}
</div>
""".format(ic=icons.use("heart"), ic2=icons.use("info"),
           sidebar=render_sidebar(cfg, products, cats))

    write("watch/index.html", page_shell(
        cfg, base,
        title="気になるリスト｜%s" % cfg["site"]["name"],
        desc="ハートを押して保存した商品の一覧です。会員登録は要りません。"
             "保存先はお使いのブラウザの中だけで、サーバーには送信されません。",
        path="/watch/",
        content=content,
        products=products, cats=cats, bodyclass="v11",
        robots='<meta name="robots" content="noindex,follow">'))


# セール会場やクーポンのページを紹介する投稿。商品を選ばなくていい。
# 会場とエントリーは同じ場所に着くので、投稿の型はひとつにする。
# 同じURLを別の文面で二度出しても、押す人にとっては同じページ。
POST_LINK_TEXT = {
    "sale":   ("楽天スーパーSALEの会場です。",
               "エントリーもここからできます。値引き幅の大きいものから見ていくと早いです。"),
    "coupon": ("楽天のクーポンページです。",
               "買う前に取っておくだけで値段が変わります。取り忘れが一番もったいない。"),
    "marathon": ("お買い物マラソンのエントリーはお済みですか。",
               "してもしなくても値段は同じに見えますが、"
               "していないと買いまわりのポイントが付きません。"),
    "fivezero": ("今日は5と0のつく日です。",
               "エントリーと楽天カードでの支払いが条件です。"
               "同じ買い物でも、しているかどうかで戻る量が変わります。"),
    "wonderful": ("今日はワンダフルデーです。",
               "毎月1日だけの日です。エントリーが条件で、押すだけ無料です。"),
    "ichiba": ("今日はいちばの日です。",
               "毎月18日だけの日です。エントリーが条件で、押すだけ無料です。"),
    "spu":    ("いまの自分のポイント倍率を確認できます。",
               "何倍かを知らずに買うと、いくら戻るのか分かりません。"),
    "deal":   ("楽天スーパーDEALの対象商品です。",
               "ポイントの還元率が高く設定されているものが並びます。"),
}


def link_url(links, key, _seen=None):
    """links.json のURLを引く。alias があれば、そちらを辿る。

    同じ場所に着くのに別々の短縮URLを持つと、片方だけ古くなる。
    URLは1本にして、呼び名だけを分ける。
    """
    _seen = _seen or set()
    if key in _seen:
        return ""
    _seen.add(key)
    item = links.get(key) or {}
    if item.get("alias"):
        return link_url(links, item["alias"], _seen)
    return item.get("url") or ""


def render_post_links(cfg):
    """商品以外の投稿の下書き。links.json に入っているURLを使う。"""
    links = load("links.json") if os.path.exists(os.path.join(ROOT, "links.json")) else {}
    pr = cfg["site"].get("prLabel") or ""
    ev = active_kaimawari_event()
    kind = (ev or {}).get("kind")

    # エントリー先はイベントごとに違う。使い回すと違うイベントへ送ってしまう。
    # ただし隠しはしない。何を投稿するかを選ぶのは運営者なので、
    # 全部並べたうえで、いま開催しているかどうかを添える。
    # 開催していないものを勧めても押した人のポイントは増えないが、
    # それを判断するのに必要なのは、消すことではなく書くこと。
    RUNNING = {"marathon": "marathon", "sale": "sale"}

    # 定例は日付で決まるので、こちらで数えられる。
    # 「今日は5と0のつく日です」と書いた投稿を、違う日に流さないため。
    today = datetime.now(JST)
    DAILY = {
        "fivezero": (today.day % 5 == 0, "5と0のつく日"),
        "wonderful": (today.day == 1, "ワンダフルデー"),
        "ichiba": (today.day == 18, "いちばの日"),
    }

    rows = ""
    for key, (head, body) in POST_LINK_TEXT.items():
        url = link_url(links, key)
        if not url:
            continue
        text = pr + head + "\n" + body
        full = text + "\n" + url
        intent = ("https://twitter.com/intent/tweet?text=%s&url=%s"
                  % (urllib.parse.quote(text), urllib.parse.quote(url)))
        # Threadsは本文とURLを分けて渡せないので、末尾に付けて1本にする。
        th = ("https://www.threads.net/intent/post?text=%s"
              % urllib.parse.quote(full))
        # このリンクが結びついているイベントが、いま開催しているか。
        need = RUNNING.get(key)
        state = ""
        if key in DAILY:
            live, name = DAILY[key]
            state = (('<span class="pb-state is-live">%s今日は%s</span>'
                      % (icons.use("check", "pb-state-ic"), e(name)))
                     if live else
                     ('<span class="pb-state is-off">%s今日ではありません</span>'
                      % icons.use("info", "pb-state-ic")))
        elif need:
            if kind == need:
                state = ('<span class="pb-state is-live">%s%s 開催中</span>'
                         % (icons.use("check", "pb-state-ic"), e(ev["name"])))
            else:
                state = ('<span class="pb-state is-off">%sいまは開催していません</span>'
                         % icons.use("info", "pb-state-ic"))

        rows += (
            '<div class="pb-link">'
            '<div class="pb-link-body">%s<pre class="pb-text">%s</pre></div>'
            '<div class="pb-link-btns">'
            '<a class="btn btn-rakuten" href="%s" target="_blank" rel="noopener">Xで開く</a>'
            '<a class="btn btn-threads" href="%s" target="_blank" rel="noopener">Threadsで開く</a>'
            '<button class="btn btn-ghost" type="button" data-copy>コピー</button>'
            '</div>'
            '</div>' % (state, e(full), e(intent), e(th)))
    return rows or '<p class="pb-links-note">links.json にURLが入っていません。</p>'


def build_postboard(cfg, base, products, cats):
    """Xへ流すための投稿台。運営者だけが使うページ。

    XのAPIは2026年2月から従量課金のみになり、URLを含む投稿は1件$0.20。
    1日5件で月30ドルかかるので使わない。ブラウザを自動操作して投稿するのは
    規約違反なので、それもしない。

    代わりに、文面を組み立てて「Xの投稿画面を開くリンク」を並べる。
    費用はゼロ、規約の中、手間は1件2秒。
    サイトの導線には出さない（noindex・リンクなし）。
    """
    site_url = cfg["site"]["url"].rstrip("/")
    content = """
<section class="page-head wrap-narrow">
  <p class="page-eyebrow">{ic}運営用</p>
  <h1 class="page-title">投稿台</h1>
  <p class="page-lead">未投稿は <b id="postCount">-</b> 件。
  左が直近3日の通常商品、右がセール開始前の商品です。
  「Xで開く」を押すと、本文が入った状態でXの投稿画面が開きます。あとは投稿を押すだけです。</p>
</section>

<div class="wrap-narrow">
  <div class="pb-links">
    <p class="pb-links-head">{ic3}商品以外の投稿</p>
    <p class="pb-links-note">セール会場やクーポンのページも紹介できます。
    商品を選ぶ手間がいらないので、投稿のたびに商品を探さなくて済みます。</p>
    {linkrows}
  </div>

  <div id="postboard" data-url="{site}" data-pr="{pr}">
    <p class="pb-loading">読み込んでいます…</p>
  </div>

  <div class="pb-note">
    <p class="pb-note-head">{ic2}この作りにした理由</p>
    <ul>
      <li>XのAPIは従量課金のみで、<b>URLを含む投稿は1件$0.20</b>。1日5件で月30ドルになります。</li>
      <li>ブラウザを自動で操作して投稿するのは<b>Xの規約違反</b>で、凍結の的になります。</li>
      <li>貼るのは楽天のリンクではなく<b>このサイトの商品ページ</b>です。
      アフィリエイトURLの連投は目を付けられますし、サイトに来てもらったほうが他も見てもらえます。</li>
      <li>商品ページのOGPが効くので、<b>写真つきの大きなカード</b>で表示されます。</li>
      <li>先頭の<b>【PR】</b>は、楽天のガイドラインに沿って<b>文頭</b>に置いています。
      商品提供を受けていない今の使い方では義務ではありませんが、
      下部やハッシュタグに混ぜる書き方はNG例とされているため、付けるなら先頭が正解です。
      外したいときは config.json の site.prLabel を空にしてください。</li>
    </ul>
  </div>
</div>
""".format(ic=icons.use("share"), ic2=icons.use("info"), ic3=icons.use("tag"),
           site=e(site_url), pr=e(cfg["site"].get("prLabel") or ""),
           linkrows=render_post_links(cfg))

    write("post/index.html", page_shell(
        cfg, base,
        title="投稿台｜%s" % cfg["site"]["name"],
        desc="運営用のページです。",
        path="/post/",
        content=content,
        products=products, cats=cats,
        robots='<meta name="robots" content="noindex,nofollow">',
        scripts='<script src="%s" defer></script>' % asset_url("/assets/js/post.js")))


def build_sitemap(cfg, products):
    site_url = cfg["site"]["url"].rstrip("/")
    now = datetime.now(JST).strftime("%Y-%m-%d")
    urls = [(site_url + "/", now, "1.0", "daily"),
            (site_url + "/categories/", now, "0.6", "weekly")]
    per = cfg["feed"]["perPage"]
    # TOP は v11 移植で 15件/ページ（TOP_PER_PAGE）になり、並ぶのは
    # PICK UP＋フィードのユニット列（全件ユニーク）。/page/N/ は build_index と
    # 同じユニット数から出す（単一集計でないと、実在するページが sitemap から
    # 漏れたり、無いページを載せたりする）。
    # 値下がり順（/off/）は並べ替えビューで noindex のため sitemap に入れない。
    _live = [p for p in products if not sale_over(p) and not sale_soon(p)]
    _ordered, _pick, _units = top_units(_live)
    total = max(1, -(-len(_units) // TOP_PER_PAGE))
    for n in range(2, total + 1):
        urls.append((site_url + "/page/%d/" % n, now, "0.5", "daily"))
    for c in cfg["categories"]:
        urls.append((site_url + "/c/%s/" % c["slug"], now, "0.8", "daily"))
        n_items = sum(1 for p in products if p["category"] == c["slug"])
        for n in range(2, max(1, -(-n_items // per)) + 1):
            urls.append((site_url + "/c/%s/page/%d/" % (c["slug"], n), now, "0.4", "daily"))
    for p in products:
        urls.append((site_url + "/p/%s/" % p["id"], p.get("postedAt", now), "0.7", "weekly"))
    for g in (cfg.get("_guides") or []):
        urls.append((site_url + "/guide/%s/" % g["slug"], g.get("updated", now), "0.9", "monthly"))
    for ev in EVENT_PAGES:
        urls.append((site_url + "/event/%s/" % ev["slug"],
                     event_lastmod(ev["slug"]), "0.8", "weekly"))
    urls.append((site_url + "/quiz/", now, "0.6", "weekly"))
    urls.append((site_url + "/kaimawari/", now, "0.7", "daily"))
    for slug in ("about", "contact", "privacy", "disclaimer", "terms"):
        urls.append((site_url + "/%s/" % slug, now, "0.3", "monthly"))

    body = "".join(
        "<url><loc>%s</loc><lastmod>%s</lastmod><changefreq>%s</changefreq><priority>%s</priority></url>\n"
        % (u, lm, cf, pr) for u, lm, pr, cf in urls)
    write("sitemap.xml",
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s</urlset>\n' % body)

    write("robots.txt",
          "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % site_url)


def build_rss(cfg, products):
    site = cfg["site"]
    site_url = site["url"].rstrip("/")
    live = [p for p in products if not sale_over(p) and not sale_soon(p)]
    ordered = sorted(live, key=lambda p: p.get("postedAt", ""), reverse=True)[:30]
    items = ""
    for p in ordered:
        d = discount_rate(p)
        title = "【%d%%OFF】%s ¥%s" % (d, p["title"], yen(p["price"])) if d \
            else "%s ¥%s" % (p["title"], yen(p["price"]))
        try:
            dt = datetime.strptime(p.get("postedAt", ""), "%Y-%m-%d").replace(tzinfo=JST)
        except ValueError:
            dt = datetime.now(JST)
        items += (
            "<item><title>%s</title><link>%s/p/%s/</link>"
            "<guid isPermaLink=\"true\">%s/p/%s/</guid>"
            "<description>%s</description><pubDate>%s</pubDate></item>\n"
        ) % (e(title), site_url, p["id"], site_url, p["id"],
             e(p.get("caption") or p["title"]),
             dt.strftime("%a, %d %b %Y %H:%M:%S +0900"))

    write("feed.xml",
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<rss version="2.0"><channel>\n'
          '<title>%s</title><link>%s/</link>\n'
          '<description>%s</description><language>ja</language>\n%s'
          '</channel></rss>\n' % (e(site["name"]), site_url, e(site["description"]), items))


def build_icon_sprite():
    write("assets/img/icons.svg", icons.sprite())


def ensure_placeholders(cfg, products):
    """画像が用意できていない商品のために、カテゴリ色のプレースホルダSVGを作る"""
    palette = ["#FFD31F", "#F5333F", "#00A88E", "#1A1410", "#FF8A3D", "#5B8DEF"]
    for i, c in enumerate(cfg["categories"]):
        path = "assets/img/ph-%s.svg" % c["slug"]
        if os.path.exists(os.path.join(ROOT, path)):
            continue
        col = palette[i % len(palette)]
        svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640" role="img" aria-label="{label}">
<defs><pattern id="h" width="24" height="24" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
<rect width="24" height="24" fill="#FFF1E3"/><rect width="9" height="24" fill="{col}" opacity=".16"/>
</pattern></defs>
<rect width="640" height="640" fill="url(#h)"/>
<circle cx="320" cy="292" r="120" fill="#FFFFFF" opacity=".86"/>
<g transform="translate(248 220) scale(6)" fill="none" stroke="#1A1410" stroke-width="1.5"
   stroke-linecap="round" stroke-linejoin="round" opacity=".72">{icon}</g>
<text x="320" y="470" font-size="28" text-anchor="middle" font-family="sans-serif" font-weight="bold" fill="#1A1410" opacity=".52">{label}</text>
</svg>""".format(col=col, icon=icons.inner(c["icon"]), label=c["short"])
        write(path, svg)

    # OGP（1200x630）
    ogp = "assets/img/ogp.svg"
    if not os.path.exists(os.path.join(ROOT, ogp)):
        write(ogp, """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630">
<rect width="1200" height="630" fill="#FFF1E3"/>
<rect x="0" y="0" width="1200" height="14" fill="#1A1410"/>
<g transform="rotate(-3.2 150 430)">
<rect x="82" y="376" width="330" height="150" fill="#FFD31F"/>
<text x="106" y="432" font-family="sans-serif" font-size="26" fill="#1A1410">いま</text>
<text x="106" y="500" font-family="sans-serif" font-size="76" font-weight="900" fill="#1A1410">¥1,980</text>
</g>
<circle cx="1010" cy="180" r="86" fill="#F5333F" transform="rotate(9 1010 180)"/>
<text x="1010" y="178" font-family="sans-serif" font-size="52" font-weight="900" fill="#fff" text-anchor="middle">67</text>
<text x="1010" y="216" font-family="sans-serif" font-size="24" font-weight="900" fill="#fff" text-anchor="middle">%OFF</text>
<text x="82" y="188" font-family="sans-serif" font-size="92" font-weight="900" fill="#1A1410">ヤスミル</text>
<text x="82" y="256" font-family="sans-serif" font-size="38" fill="#1A1410" opacity=".7">安いを、見る。</text>
<text x="82" y="320" font-family="sans-serif" font-size="26" fill="#1A1410" opacity=".5">二度見する安さだけを集めた特価フィード</text>
</svg>""")


def containers_with_buttons():
    """生成したHTMLを読み、内側に .btn を持つ要素のクラス名を集める。
    CSSの警告を「実際に事故る組み合わせ」だけに絞るために使う。"""
    from html.parser import HTMLParser

    found = set()

    class Scan(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []

        def handle_starttag(self, tag, attrs):
            classes = dict(attrs).get("class", "").split()
            if "btn" in classes:
                for ancestor in self.stack:
                    found.update(ancestor)
            if tag not in ("br", "img", "input", "meta", "link", "hr", "use", "source"):
                self.stack.append(classes)

        def handle_endtag(self, tag):
            if self.stack:
                self.stack.pop()

    for name in ("index.html", "guide/rakuten-super-sale/index.html",
                 "contact/index.html", "about/index.html"):
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="ignore") as f:
            try:
                Scan().feed(f.read())
            except Exception:                       # noqa: BLE001
                pass
    return found


def check_guide_toc(guides):
    """記事の目次が空になっていないかを見る。

    目次に載るのは h2、つまり記事中の「#」だけ。
    「##」から書き始めると h3 になり、目次が丸ごと空になる。
    見た目は普通に出るので、言われないと気づけない。
    """
    for g in guides:
        _body, toc = markdown.render(g["body"])
        if not toc:
            print("\n⚠ 記事「%s」の目次が空です。" % g.get("shortTitle") or g.get("slug"))
            print("   大見出しは「##」ではなく「#」で書いてください（目次に載るのは # だけ）。")


def check_expired_sales(products):
    """終わったセールが、一覧やデータに残っていないか確かめる。

    「24時間限定 半額」の商品は、時刻を過ぎると値段が戻る。
    それを載せ続けるのは、このサイトが批判している「安く見えて安くない」
    そのものになる。商品ページは残すが、一覧・ティッカー・RSS・
    ブラウザ用データからは消えていなければならない。
    """
    over = [p["id"] for p in products if sale_over(p)]
    if not over:
        return

    targets = ["index.html", "feed.xml", "assets/data/feed.json", "categories/index.html"]
    targets += ["c/%s/index.html" % c for c in
                sorted({p["category"] for p in products})]

    leaked = []
    for rel in targets:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for pid in over:
            if pid in text:
                leaked.append((rel, pid))

    print("   終了したセール %d件 を一覧から外しました" % len(over))
    if leaked:
        print("\n⚠ 終了したセールが残っています:")
        for rel, pid in leaked[:10]:
            print("   %-28s %s" % (rel, pid))


def check_featured_freshness():
    """編集部の棚（featured.json）が、腐ったまま放っておかれていないか調べる。

    featured.txt / featured.json は watch.yml では自動更新されず、
    手で `python3 fetch_rakuten.py --featured` を叩かない限り増えない。
    2026-09-08 に組んでから6日間そのままになり、載っていた12件のうち
    5件がセール終了後の値段のまま表示され続けていた（render_featured が
    sale_over を見ていなかったため）。表示側は直したが、
    「棚そのものが古い」ことにも気づけるようにしておく。
    """
    doc = load("featured.json") or {}
    items = doc.get("items") or []
    if not items:
        print("⚠ 編集部の棚が空です。featured.txt に商品URLを足して"
              " `python3 fetch_rakuten.py --featured` を実行してください。")
        return
    # 日数の古さは rules.py の rule_featured_fresh が見ている。
    # ここで見るのは別の壊れ方: 日数はまだ新しくても、載っている商品の
    # セールがもう終わっていて、表示から静かに消えている場合。
    alive = [p for p in items if not sale_over(p)]
    if len(alive) < len(items) * 0.5 or len(alive) <= 2:
        print("⚠ 編集部の棚 %d件のうち、セールが終わっていないのは %d件だけです。"
              " featured.txt を入れ替えてください。" % (len(items), len(alive)))


def check_caption_prices(products):
    """キャプションに書いた金額が、いまの価格と食い違っていないか調べる。

    キャプションは手で書くが、価格は毎日自動で動く。
    「1,290円」と書いた商品が960円に下がると、その一文は嘘になる。
    実際に2件やった。安さを売るサイトで、値段の記述が古いのは致命的。

    単価（1包53円 など）は本体価格と一致しないので、
    本体価格・セール前価格・単価の表記のどれとも合わない数字だけを挙げる。
    """
    import re as _re

    bad = []
    for p in products:
        # キャプションだけでなく、商品説明と箇条書きにも金額を書いている。
        # 見張る範囲を分けておく理由がないので、まとめて見る。
        blocks = [("キャプション", p.get("caption") or ""),
                  ("説明", p.get("description") or "")]
        blocks += [("箇条書き", x) for x in (p.get("points") or [])]
        # 商品理解も見る。Threadsの投稿はここから組み立てるので、
        # ここが古いと、間違った値段がそのまま外へ出る。
        # 実際、実売998円の商品の一文に999円と書いてあった。
        mk = p.get("marketing") or {}
        blocks += [("商品理解の入口", mk.get("hook") or ""),
                   ("商品理解の本文", mk.get("body") or "")]
        ok = {p["price"], p.get("listPrice") or 0}
        note = p.get("unitNote") or ""
        for m in _re.findall(r"([\d,]{3,9})\s*円", note):
            ok.add(int(m.replace(",", "")))

        for where, text in blocks:
            if not text:
                continue
            for m in _re.findall(r"([\d,]{3,9})\s*円", text):
                v = int(m.replace(",", ""))
                if v in ok:
                    continue
                # 単価やセット単価の言及は、本体価格と一致しなくて当然
                near = _re.search(r".{0,18}%s\s*円.{0,12}" % _re.escape(m), text)
                around = near.group(0) if near else ""
                if _re.search(r"(あたり|ぽっきり|1本|1袋|1包|1個|1枚|1食|1杯|1ヶ月|1kg|から|切ります|切る)",
                              around):
                    continue
                bad.append((p["id"], v, p["price"], where, text))

    if bad:
        print("\n⚠ 書いた金額が、いまの価格と合いません:")
        for pid, v, now, where, text in bad[:12]:
            print("   %s [%s]  文中 ¥%s / 実売 ¥%s"
                  % (pid, where, "{:,}".format(v), "{:,}".format(now)))
            print("      %s" % text.replace("\n", " ")[:60])


def check_internal_links():
    """生成したページの中の、サイト内リンクが実在するかを確かめる。

    記事は手で書くので、URLの打ち間違いが混ざる。
    実際 /guides/ と /guide/ を取り違えた（ディレクトリは guide が正しい）。
    リンク切れは読者をそこで止めてしまうし、
    公開してからでないと気づけないのが一番まずい。
    """
    import re as _re

    broken = {}
    for base, _dirs, files in os.walk(ROOT):
        # 生成物ではない場所は見ない
        rel = os.path.relpath(base, ROOT)
        if rel.split(os.sep)[0] in (".git", "content", "assets", "__pycache__", ".github"):
            continue
        for name in files:
            if not name.endswith(".html"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as f:
                html_text = f.read()
            for href in set(_re.findall(r'href="(/[^"#?]*)"', html_text)):
                target = href.strip("/")
                if not target:
                    continue
                if (os.path.exists(os.path.join(ROOT, target))
                        or os.path.exists(os.path.join(ROOT, target, "index.html"))):
                    continue
                broken.setdefault(href, set()).add(os.path.relpath(path, ROOT))

    if broken:
        print("\n⚠ サイト内リンクの行き先がありません:")
        for href, pages in sorted(broken.items()):
            where = ", ".join(sorted(pages)[:3])
            print("   %-34s ← %s" % (href, where))


def check_padding_collisions():
    """.wrap 系と併用しているクラスに padding の一括指定が無いか調べる。

    .wrap { padding-inline: 16px } に対して .hero { padding: 26px 0 6px } のような
    一括指定を後から当てると、左右の余白が 0 に潰れる。
    見た目が崩れるだけでなく、回転している要素が画面外へ出て
    横スクロールが発生する（実際に攻略ガイドで起きた）。
    CSSの読み込み順に依存する事故なので、目視ではなく機械で見張る。
    """
    css_path = os.path.join(ROOT, "assets", "css", "style.css")
    if not os.path.exists(css_path):
        return
    with open(css_path, encoding="utf-8") as f:
        css = f.read()

    # 生成物から .wrap 系と併用されているクラス名を集める
    partners = set()
    for root_dir, _dirs, files in os.walk(ROOT):
        if ".git" in root_dir:
            continue
        for name in files:
            if not name.endswith(".html"):
                continue
            with open(os.path.join(root_dir, name), encoding="utf-8", errors="ignore") as f:
                for attr in re.findall(r'class="([^"]*\bwrap[\w-]*\b[^"]*)"', f.read()):
                    for cls in attr.split():
                        if not cls.startswith("wrap") and cls != "table-wrap":
                            partners.add(cls)

    bad = []
    for cls in sorted(partners):
        for m in re.finditer(r"^\." + re.escape(cls) + r"\s*\{([^}]*)\}", css, re.M):
            if re.search(r"(^|[\s;])padding\s*:", m.group(1)):
                decl = re.search(r"(^|[\s;])(padding\s*:[^;]*)", m.group(1)).group(2)
                bad.append((cls, decl.strip()))
    if bad:
        print("\n⚠️  左右の余白が潰れます（padding の一括指定が padding-inline を上書き）:")
        for cls, decl in bad:
            print("     .%s { %s }  → padding-block を使ってください" % (cls, decl))

    # 本文中のリンクに一律で色を当てると、ボタンの文字色まで奪う。
    # 赤いボタンに緑の文字が乗る事故が実際に2回起きた。
    # ただし「ボタンを含まない入れ物」まで警告すると、うるさくて無視されるようになる。
    # 生成したHTMLを実際に読んで、その入れ物の中に .btn があるものだけを挙げる。
    holders = containers_with_buttons()
    link_rules = re.findall(r"^\.([\w-]+)\s+a(?!:not)\s*\{([^}]*)\}", css, re.M)
    risky = [("." + cls + " a", body.strip()[:34]) for cls, body in link_rules
             if re.search(r"(^|[\s;])color\s*:", body) and cls in holders]
    if risky:
        print("\n⚠️  ボタンの文字色を奪います（その入れ物の中に実際にボタンがあります）:")
        for sel, body in risky:
            print("     %s { %s }  → a:not(.btn) にしてください" % (sel, body))
        bad += risky

    return bad


def clean():
    for d in GENERATED_DIRS:
        full = os.path.join(ROOT, d)
        if os.path.isdir(full):
            shutil.rmtree(full)
    for f in GENERATED_FILES:
        full = os.path.join(ROOT, f)
        if os.path.isfile(full):
            os.remove(full)


def stale_prices(p):
    """その商品の文章に混じっている、いまの値段と合わない金額を挙げる。

    check_caption_prices と同じ見方をする。あちらは知らせるだけ、
    こちらは直すために使う。
    """
    import re as _re
    ok = {p.get("price"), p.get("listPrice") or 0}
    for m in _re.findall(r"([\d,]{3,9})\s*円", p.get("unitNote") or ""):
        ok.add(int(m.replace(",", "")))
    fields = [("caption", p.get("caption") or ""),
              ("description", p.get("description") or "")]
    fields += [("points[%d]" % i, x) for i, x in enumerate(p.get("points") or [])]
    mk = p.get("marketing") or {}
    fields += [("marketing.hook", mk.get("hook") or ""),
               ("marketing.body", mk.get("body") or "")]
    out = []
    for where, text in fields:
        for m in _re.findall(r"([\d,]{3,9})\s*円", text):
            v = int(m.replace(",", ""))
            if v in ok:
                continue
            near = _re.search(r".{0,18}%s\s*円.{0,12}" % _re.escape(m), text)
            around = near.group(0) if near else ""
            if _re.search(r"(あたり|ぽっきり|1本|1袋|1包|1個|1枚|1食|1杯|1ヶ月|1kg|から|切ります|切る)",
                          around):
                continue
            out.append((where, m, v))
    return out


def reconcile_prices(all_products):
    """書き出す前に、文章の金額をいまの値段に合わせる。合わせられなければ消す。

    2026-09-07: 半日で6件たまった。うち1件は値段が戻っているのに
    「1,740円のケースが870円」と書いたままだった。
    このサイトが批判している「安く見えて安くない」を自分でやっていた。

    それまでは check_caption_prices が知らせるだけだった。
    知らせる先は build.py の出力で、終了コードは 0。
    自動実行では誰も読まないので、直る道が「私が手で回す」しか無かった。
    半日で6件たまるのは、見落としではなく仕組みの穴。

    だから、知らせる前に直す。
      1. 「A円の○○がB円」の形なら、A=参考価格・B=実売に書き直す
      2. 参考価格が消えているなら、参考価格の部分ごと落とす
      3. どちらでもなければ、その文を出さない（間違った値段を載せるより良い）
    直せないものを載せないので、ページには必ず正しい値段だけが出る。

    ビルドは止めない。止めると古いページが残り、間違った値段が
    そのまま公開され続ける。前へ倒して直すほうが安全。
    """
    import re as _re

    def yen(n):
        return "{:,}".format(n)

    fixed, dropped = [], []
    for p in all_products:
        stale = stale_prices(p)
        if not stale:
            continue
        price = p.get("price")
        lp = p.get("listPrice") or 0
        cap = p.get("caption") or ""

        # 1・2. 「A円の○○がB円」型は、数字を入れ替えれば正しくなる。
        m = _re.match(r"^([\d,]{3,9})\s*円の(.+?)が([\d,]{3,9})\s*円", cap)
        if m:
            if lp and lp > price:
                new = "%s円の%sが%s円" % (yen(lp), m.group(2), yen(price))
            else:
                # 参考価格が消えた。値引き幅を語れないので、そこを落とす。
                new = "%sが%s円" % (m.group(2), yen(price))
            p["caption"] = new + cap[m.end():]
            # 箇条書きの「A円からB円へ」も同じ理由で古くなる。作り直せない
            # ものは落とす。ここは飾りなので、無くてもページは成り立つ。
            p["points"] = [x for x in (p.get("points") or [])
                           if not _re.search(r"[\d,]{3,9}\s*円", x)]
            if not stale_prices(p):
                fixed.append((p["id"], cap, p["caption"]))
                continue

        # 3. 直せない文は出さない。
        for where, _txt, _v in stale_prices(p):
            if where.startswith("points["):
                p["points"] = [x for x in (p.get("points") or [])
                               if not _re.search(r"[\d,]{3,9}\s*円", x)]
            elif where.startswith("marketing."):
                p.setdefault("marketing", {})[where.split(".", 1)[1]] = ""
            else:
                p[where] = ""
        dropped.append(p["id"])

    if fixed or dropped:
        print("\n金額をいまの値段に合わせました:")
        for pid, before, after in fixed:
            print("   %s" % pid)
            print("      前: %s" % before[:60])
            print("      後: %s" % after[:60])
        for pid in dropped:
            print("   %s 直せないので、その文を出しません" % pid)
    return len(fixed), len(dropped)


def main():
    cfg = load("config.json")
    data = load("products.json")
    all_products = data["products"] if isinstance(data, dict) else data
    # 書き出す前に直す。検査は「出したあとに知らせる」ので間に合わない。
    n_fixed, n_dropped = reconcile_prices(all_products)
    if n_fixed or n_dropped:
        with open(os.path.join(ROOT, "products.json"), "w", encoding="utf-8") as _f:
            json.dump(data, _f, ensure_ascii=False, indent=2)
    products = [p for p in all_products if not p.get("hidden")]
    cats = {c["slug"]: c for c in cfg["categories"]}

    unknown = {p["category"] for p in products} - set(cats)
    if unknown:
        # 厳格版 config（2026-11-09）で廃止したカテゴリ（books / car / sports）は、
        # products.json には残したまま、ビルド対象からだけ外す。
        # 以前は SystemExit で止めていたが、カテゴリ廃止が通常運用になったため、
        # 止めずに落として件数を知らせる。
        n_dropped_cat = sum(1 for p in products if p["category"] in unknown)
        products = [p for p in products if p["category"] not in unknown]
        print("config.json に無いカテゴリの商品 %d件をビルド対象から外しました: %s"
              % (n_dropped_cat, ", ".join(sorted(unknown))))

    # 厳格選定（改築 2026-11-09）: curated.json があれば、公開対象を
    # select_strict.py の掲載分だけに絞る。「掲載 = 推薦」のフィードを
    # トップ・カテゴリ・商品ページ・sitemap 等すべての生成の前提にする。
    # curated.json を消せば従来どおり全件ビルドに戻る。
    curated_path = os.path.join(ROOT, "curated.json")
    if os.path.exists(curated_path):
        curated = load("curated.json")
        keep_ids = {en["id"] for en in curated.get("feed", [])}
        n_before = len(products)
        products = [p for p in products if p["id"] in keep_ids]
        cfg["_strict"] = True
        print("厳格選定: 公開対象を %d件 → %d件に絞りました（curated.json）"
              % (n_before, len(products)))

    build_icon_sprite()
    ensure_placeholders(cfg, products)
    clean()
    base = tpl("base.html")

    guides = load_guides()
    cfg["_guides"] = guides
    build_events()
    build_index(cfg, base, products, cats)
    build_off_index(cfg, base, products, cats)
    build_guides(cfg, base, products, cats, guides)
    build_categories(cfg, base, products, cats)
    build_products(cfg, base, products, cats)
    build_static_pages(cfg, base, products, cats)
    build_quiz(cfg, base, products, cats)
    build_watchlist(cfg, base, products, cats)
    build_kaimawari(cfg, base, products, cats)
    build_postboard(cfg, base, products, cats)
    build_feed_json(cfg, products, cats)
    build_sitemap(cfg, products)
    build_rss(cfg, products)

    # 検査の出力を捕まえておく。
    # そのまま流すと、あとから出る「ビルド完了」に押し流されて、
    # tail で末尾だけ見たときに警告が画面から消える。実際に見落として、
    # 900円と書いたまま1,690円の商品を公開してしまった。
    # 出力はそのまま見せたうえで、件数を最後にもう一度出す。
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        check_padding_collisions()
        check_internal_links()
        check_caption_prices(products)
        check_expired_sales(products)
        check_featured_freshness()
        check_guide_toc(guides)
        # 決めたことを機械で守らせる。書き置きでは戻ってしまう。
        try:
            import rules as _rules
            _rules.run(quiet=True)
        except Exception as _ex:                              # noqa: BLE001
            print("⚠ 決まりの検査が動きませんでした: %s" % _ex)
    checks = buf.getvalue()
    print(checks, end="")
    warned = checks.count("⚠")

    print("✅ ビルド完了")
    print("   商品 %d件 / カテゴリ %d件" % (len(products), len(cfg["categories"])))
    print("   生成: index.html, c/, p/, categories/, 固定ページ, sitemap.xml, feed.xml")
    if warned:
        # 末尾3行だけ見ても必ず目に入る位置に出す。
        print("")
        print("⚠️  直していない警告が %d件あります。上に内容が出ています。" % warned)
        print("   反映する前に直してください。")


if __name__ == "__main__":
    main()

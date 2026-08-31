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
GENERATED_DIRS = ["p", "c", "categories", "page", "guide", "about", "contact", "privacy", "disclaimer", "terms"]
GENERATED_FILES = ["index.html", "404.html", "sitemap.xml", "feed.xml", "robots.txt",
                   "assets/data/feed.json"]


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


def feed_order_desc(p):
    o = feed_order(p)
    return (o[0], o[1], o[2])


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


def render_featured(cfg):
    """注目商品。人が選んだものを横に並べる。

    楽天の売上ランキングはAPIで取れず、アフィリエイトの注目商品ページも
    ログインの中にある。そこで「選ぶのは人、集めるのは機械」に振り分けた。
    通常のフィードとは出所が違うので、そう分かる見た目にしてある。"""
    path = os.path.join(ROOT, "featured.json")
    if not os.path.exists(path):
        return ""
    items = (load("featured.json") or {}).get("items") or []
    if not items:
        return ""

    cards = ""
    for p in items:
        tags = ""
        if p.get("freeShipping"):
            tags = '<span class="fe-tag">送料無料</span>'
        cards += (
            '<a class="fe-card" href="{url}" target="_blank" rel="nofollow sponsored noopener">'
            '<span class="fe-media"><img src="{img}" alt="" width="300" height="300" loading="lazy"></span>'
            '<span class="fe-body">'
            '<span class="fe-title">{t}</span>'
            '<span class="fe-price"><i>¥</i>{pr}</span>'
            '<span class="fe-meta">{star}{ra}<b>{rc}</b>件{tags}</span>'
            '</span></a>'
        ).format(url=e(p.get("affiliateUrl") or "#"), img=e(p.get("image", "")),
                 t=e(p.get("title", "")), pr=yen(p.get("price", 0)),
                 star=icons.use("star", "ic-star"), ra=p.get("reviewAverage") or "-",
                 rc=yen(p.get("reviewCount") or 0), tags=tags)

    return """
<section class="featured wrap-wide">
  <div class="featured-head">
    <h2 class="featured-title">{ic}編集部が選んだもの</h2>
    <p class="featured-note">売れ筋や特集から、これはと思ったものを手で選んでいます。</p>
  </div>
  <div class="featured-rail">{cards}</div>
</section>
""".format(ic=icons.use("bolt"), cards=cards)


def render_pager(page, total_pages, base):
    """ページ送り。フィードが長くなるので10件ずつに切る。
    JSに頼らず素のリンクで動かす（検索エンジンも人も同じものを辿れる）。"""
    if total_pages <= 1:
        return ""

    def href(n):
        return base if n == 1 else "%spage/%d/" % (base, n)

    prev_btn = (
        '<a class="pager-btn" href="%s" rel="prev">%s前のページ</a>' % (
            e(href(page - 1)), icons.use("arrow-right", "ic-arrow ic-flip"))
        if page > 1 else '<span class="pager-btn is-off">前のページ</span>')
    next_btn = (
        '<a class="pager-btn pager-next" href="%s" rel="next">次のページ%s</a>' % (
            e(href(page + 1)), icons.use("arrow-right", "ic-arrow"))
        if page < total_pages else '<span class="pager-btn is-off">次のページ</span>')

    return ('<nav class="pager" aria-label="ページ送り">%s'
            '<span class="pager-count"><b>%d</b><i>/</i>%d</span>%s</nav>'
            % (prev_btn, page, total_pages, next_btn))


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
        "GUIDE_LINKS_DRAWER": "".join(
            '<li><a href="/guide/%s/">%s%s</a></li>'
            % (e(g["slug"]), icons.use("doc"), e(g.get("shortTitle", g["title"])))
            for g in (cfg.get("_guides") or [])),
        "THREADS_ITEM": threads_nav_item(cfg),
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
def short_name(t, n=30):
    """行に収める長さに切る。楽天の商品名は検索語の羅列なので、
    長く出しても読めない。"""
    t = (t or "").strip()
    return t if len(t) <= n else t[:n].rstrip("…、 ") + "…"


def upcoming_event():
    """これから来るイベント。エントリーが始まっていれば、その間も返す。

    セールは始まってから動くものではない。エントリーは先に開いていて、
    エントリー前の買い物は対象外になる。
    だから「開催中かどうか」ではなく「エントリーが開いているか」で出す。
    """
    if not os.path.exists(os.path.join(ROOT, "events.json")):
        return None
    now = datetime.now(JST).replace(tzinfo=None)
    best = None
    for ev in (load("events.json") or {}).get("events", []):
        if ev.get("status") != "確定" or ev.get("kind") not in ("marathon", "sale"):
            continue
        try:
            a = datetime.strptime(ev["start"][:16], "%Y-%m-%d %H:%M")
            b = datetime.strptime((ev.get("end") or ev["start"])[:16], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        es = a
        if ev.get("entryStart"):
            try:
                es = datetime.strptime(ev["entryStart"][:16], "%Y-%m-%d %H:%M")
            except ValueError:
                es = a
        if es <= now <= b and (best is None or a < best[0]):
            best = (a, b, es, ev)
    return best


def render_event_banner():
    """セールの助走と開催を知らせる。

    件数の多い日ほど下が長くなるので、ここは高さを取らない。
    出すのは、その日に効く事実だけにする。
    """
    got = upcoming_event()
    if not got:
        return ""
    a, b, _es, ev = got
    now = datetime.now(JST).replace(tzinfo=None)
    links = load("links.json") or {}
    running = a <= now <= b

    def when(d):
        return "%d月%d日 %d:%02d" % (d.month, d.day, d.hour, d.minute)

    if running:
        head = "開催中"
        title = "%sは %s まで" % (ev["name"], when(b))
    else:
        days = (a.date() - now.date()).days
        head = ("今日 %d:%02d から" % (a.hour, a.minute) if days == 0
                else "あと%d日" % days)
        title = "%sは %s から" % (ev["name"], when(a))

    # 事実だけを並べる。書いていないことは出さない。
    facts = []
    if not running and ev.get("entryStart"):
        facts.append("エントリーはもう始まっています。押すだけ、無料")
    facts.append("買いまわりは1ショップ税込1,000円以上で1カウント")
    if ev.get("pointCap"):
        facts.append("もらえる上限は%sポイント（期間限定）" % "{:,}".format(ev["pointCap"]))

    btns = []
    for lk in (ev.get("links") or [])[:2]:
        to = lk.get("to") or ""
        url = to if to.startswith("/") else (link_url(links, to) or "")
        if not url:
            continue
        ext = ' target="_blank" rel="nofollow sponsored noopener"' if url.startswith("http") else ""
        btns.append('<a class="evb-btn" href="%s"%s>%s</a>' % (url, ext, lk.get("label", "見る")))

    return """
<section class="evbar wrap-narrow">
  <p class="evb-eyebrow">{ic}{head}</p>
  <h2 class="evb-title">{title}</h2>
  <ul class="evb-facts">{facts}</ul>
  <div class="evb-btns">{btns}</div>
</section>
""".format(ic=icons.use("bolt"), head=e(head), title=e(title),
           facts="".join("<li>%s</li>" % e(f) for f in facts),
           btns="".join(btns))


def render_soon(products, cats):
    """セールの特価をまとめる枠。ヒーローのすぐ下に置く。

    開始前のものは、その時刻まで買えないので通常のフィードには並べず、
    ここにだけ集めて、いつ買えるのかを最初に言う。

    始まったものも、イベントの最中はここに残す。
    以前は開始と同時にこの枠から消していたが、それは逆だった。
    いちばん見せたいのは「いま実際に買える特価」であって、
    始まった瞬間に221件のフィードへ紛れ込ませては、探せなくなる。
    セールが終わるか、イベントの期間が終われば、自然に消える。

    該当が無い日は、枠ごと出さない。空の箱を置いておかない。
    """
    ev = active_kaimawari_event()
    # 開催中でなくても、これから来るイベントの商品なら、その名前で出す。
    # 中身がスーパーSALEの目玉なのに「まもなく始まる特価」と名乗るのは、
    # 実態と合わないし、いちばん強い言葉を捨てている。
    coming = upcoming_event()

    def in_event(p):
        """このイベントで始まった（始まる）特価かどうか。

        イベントの開始12時間前から終了までに売り出しが始まるものを、
        そのイベントの特価とみなす。売り出しの予告は前日から出るため。
        """
        if not ev:
            return False
        st = (p.get("startTime") or "").strip()
        if not st:
            return False
        try:
            t = datetime.strptime(st[:16], "%Y-%m-%d %H:%M")
            a = datetime.strptime(ev["start"][:16], "%Y-%m-%d %H:%M")
            b = datetime.strptime((ev.get("end") or ev["start"])[:16], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            return False
        return a - timedelta(hours=12) <= t <= b

    soon = [p for p in products if not sale_over(p)
            and (sale_soon(p) or in_event(p))]
    if not soon:
        return ""

    def key(p):
        # 買えるものを先に。次に開始の早い順。
        return (1 if sale_soon(p) else 0, (p.get("startTime") or ""),
                -discount_rate(p))
    soon.sort(key=key)

    n_live = sum(1 for p in soon if not sale_soon(p))
    head = (("%s 開催中" % ev["name"]) if (ev and n_live)
            else sale_starts_label(soon[0]))

    # この一覧が、来るイベントの品ぞろえかどうか。
    # 開始48時間前から終わりまでに売り出すものを、そのイベントの品とみなす。
    ev_name = None
    if coming:
        a, b, _es, cev = coming
        n_in = 0
        for p in soon:
            st = (p.get("startTime") or "").strip()
            try:
                t = datetime.strptime(st[:16], "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if a - timedelta(hours=48) <= t <= b:
                n_in += 1
        # 半分以上がそのイベントのものなら、イベント名で出す
        if soon and n_in * 2 >= len(soon):
            ev_name = cev["name"]

    def row(p):
        """1件ぶんの行。カードだと縦を使いすぎる。

        件数が少ない日でも枠だけが大きく残っていた。
        写真・時刻・名前・値段を1行に収めて、必要な高さだけ使う。
        """
        d = discount_rate(p)
        cat = cats.get(p["category"], {})
        when = sale_starts_label(p) if sale_soon(p) else "いま買えます"
        return (
            '<a class="soon-row" href="/p/{id}/">'
            '<img class="soon-thumb" src="{img}" alt="" loading="lazy" '
            'width="56" height="56">'
            '<span class="soon-main">'
            '<span class="soon-when-in">{ic}{when}</span>'
            '<span class="soon-name">{t}</span>'
            '</span>'
            '<span class="soon-fig">'
            '<span class="soon-yen"><i>¥</i>{pr}</span>'
            '{off}{ship}'
            '</span></a>'
        ).format(
            id=e(p["id"]), img=e(p["image"]), t=e(short_name(p["title"])),
            ic=icons.use("calendar", "ic-when"), when=e(when),
            pr=yen(p["price"]),
            off=('<span class="soon-off-s">%d%%OFF</span>' % d) if d else "",
            # 送料込みは、黙っていないで言う。
            # 「¥1,000 ＋送料」は、それだけで買えない値段に見える。
            # 実際は14件中ほとんどが送料込みだった。
            # 言わなければ伝わらないし、言えば強みになる。
            ship=('<span class="soon-ship-in">送料込み</span>' if free_shipping(p)
                  else '<span class="soon-ship-s">＋送料</span>'),
            cic=icons.use(cat.get("icon", "tag")))

    # 開始時刻ごとにまとめず、1本の一覧にする。
    # 各行に時刻を書けば、見出しで分ける必要がない。
    # 見出しを挟むぶんだけ縦が伸びていた。
    soon.sort(key=lambda p: (1 if sale_soon(p) else 0, p.get("startTime") or ""))
    n_soon = sum(1 for p in soon if sale_soon(p))

    # 最初の何件かは、たたまずに必ず見せる。
    #
    # 全部をたたむと、件数が増えても見出し1行しか見えない。
    # 件数が少ないときは寂しく、多いときは中身が伝わらない。
    # 上を開いたまま、あふれたぶんだけ「ほかに◯件」に入れる。
    SHOWN = 5
    head_rows = "".join(row(p) for p in soon[:SHOWN])
    rest = soon[SHOWN:]
    more = ""
    if rest:
        more = ("""
  <details class="soon-more">
    <summary class="soon-more-sum">ほかに%d件<span class="soon-toggle" aria-hidden="true"></span></summary>
    <div class="soon-list">%s</div>
  </details>""" % (len(rest), "".join(row(p) for p in rest)))

    return """
<section class="soon wrap-narrow">
  <div class="soon-sum">
    <span class="soon-sum-main">
      <span class="soon-eyebrow">{ic}{head}</span>
      <span class="soon-title-main">{title}<b class="soon-count">{n}件</b></span>
    </span>
  </div>
  <p class="soon-note">{note}</p>
  <div class="soon-list">{rows}</div>
  {more}
  {kwlink}
</section>
""".format(ic=icons.use("bolt"), head=e(head), n=len(soon), rows=head_rows,
           more=more,
           kwlink=('<a class="soon-kw" href="/kaimawari/">%s買いまわりに使えるものを見る%s</a>'
                   % (icons.use("check", "ic-km"),
                      icons.use("arrow-right", "ic-arrow"))) if ev else "",
           title=(("%sで始まる特価" % ev_name) if (ev_name and not n_live)
                  else ("このセールの特価" if n_live else "まもなく始まる特価")),
           note=("「いま買えます」のものは、押せばその値段で買えます。"
                 "数量限定のものは早いもの勝ちです。"
                 "開始前のものは、その時刻まで買えません。"
                 if n_live else
                 "開始まではまだ買えません。時間になったら楽天のページで"
                 "値段が変わります。数量限定のものは、早いもの勝ちになります。"))


def render_gacha():
    """トップのガチャ。中身はJSが /assets/data/feed.json から引く。

    174件を上から順に眺めるのは骨が折れる。
    レバーを引いて1件出すだけの遊びだが、売場を回る入口になる。
    """
    return """
<div class="wrap-narrow"><section class="gacha">
  <p class="gacha-eyebrow">{ic}TODAY'S PICK</p>
  <h2 class="gacha-title">今日の一発、引いてみます？</h2>
  <p class="gacha-note">掲載中の特価から、1件だけ出します。</p>
  <div class="gacha-slot" id="gachaSlot"></div>
  <button class="btn-gacha" type="button" id="gachaGo">{ic2}まわす</button>
  <br><a class="gacha-more" href="/quiz/">値段あてクイズであそぶ{arrow}</a>
</section></div>
""".format(ic=icons.use("capsule"), ic2=icons.use("capsule", "ic-capsule"),
           arrow=icons.use("arrow-right", "ic-arrow"))


def build_index(cfg, base, products, cats):
    per = cfg["feed"]["perPage"]
    fetched = last_fetch_date(products)
    # 終わったセールは特価フィードに並べない。
    # 商品ページは残す（Xに貼ったリンクが死なないように）。
    live = [p for p in products if not sale_over(p) and not sale_soon(p)]
    ordered = sorted(live, key=feed_order, reverse=True)
    total_pages = max(1, -(-len(ordered) // per))
    # 買いまわりの印はイベントの最中だけ出す。
    # 何も無い日に「買いまわり対象」と書いても意味がない。
    km = shop_totals(products) if active_kaimawari_event() else None

    counts = {}
    for p in products:
        counts[p["category"]] = counts.get(p["category"], 0) + 1

    today = datetime.now(JST).strftime("%-m月%-d日")
    best = max((discount_rate(p) for p in live), default=0)
    # 値下がりを検知した商品の数。0なら「割引率」で並べ替えても何も起きないので、
    # そのボタン自体を出さない（押しても動かないボタンは壊れて見える）。
    n_off = sum(1 for p in products if discount_rate(p) >= 5)
    if best:
        third = "{ic}最大{best}%OFF".format(ic=icons.use("bolt"), best=best)
    else:
        third = "{ic}値下がりを監視中".format(ic=icons.use("bolt"))

    site_url = cfg["site"]["url"].rstrip("/")

    for page in range(1, total_pages + 1):
        chunk = ordered[(page - 1) * per: page * per]
        cards = "".join(render_card(p, cats, fetched, km) for p in chunk)
        path = "/" if page == 1 else "/page/%d/" % page

        head = """
<section class="hero wrap-narrow">
  <h1 class="hero-title">二度見する安さ、<br><span class="hl">ぜんぶここに。</span></h1>
  <p class="hero-lead">{lead}</p>
  <div class="hero-meta">
    <span>{ic_cal}{today}更新</span>
    <span>{ic_box}掲載{count}件</span>
    <span>{third}</span>
  </div>
</section>
""".format(lead=e(cfg["site"]["description"]), today=today, count=len(ordered),
           third=third, ic_cal=icons.use("calendar"), ic_box=icons.use("box"))
        if page > 1:
            head = """
<section class="page-head wrap-narrow">
  <p class="page-eyebrow">{ic}特価フィード</p>
  <h1 class="page-title">特価フィード</h1>
  <p class="page-lead">{count}件を新しい順に。いまは{page}ページ目です。</p>
</section>
""".format(ic=icons.use("bolt"), count=len(ordered), page=page)

        content = head + """
{evbar}
{soon}
{featured}
{gacha}
<div class="layout wrap-wide">
<div class="layout-main">
  <div class="toolbar">
    <p class="result-count"><b id="resultCount">{count}</b> 件の特価</p>
    <div class="sorter" role="group" aria-label="並べ替え">
      <button type="button" data-sort="new" aria-pressed="true">新着</button>
      {off_sort}
      <button type="button" data-sort="cheap" aria-pressed="false">安い順</button>
    </div>
  </div>
  <div class="pricebar" role="group" aria-label="価格で絞り込む">
    <button type="button" data-price="all" aria-pressed="true">すべて</button>
    <button type="button" data-price="0-1000" aria-pressed="false">〜1,000円</button>
    <button type="button" data-price="1000-2000" aria-pressed="false">1,000〜2,000円</button>
    <button type="button" data-price="2000-3000" aria-pressed="false">2,000〜3,000円</button>
    <button type="button" data-price="3000-" aria-pressed="false">3,000円〜</button>
  </div>
  <div class="feed" id="feed">{cards}</div>
  {pager}
  <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。価格・在庫・送料は取得時点のもので、変動します。購入前に楽天市場の商品ページで最新の条件をご確認ください。</p>
</div>
{sidebar}
</div>
""".format(count=len(products), cards=cards,
           evbar=(render_event_banner() if page == 1 else ""),
           soon=(render_soon(products, cats) if page == 1 else ""),
           featured=(render_featured(cfg) if page == 1 else ""),
           gacha=(render_gacha() if page == 1 else ""),
           off_sort=('<button type="button" data-sort="off" aria-pressed="false">割引率</button>'
                     if n_off else ''),
           pager=render_pager(page, total_pages, "/"),
           sidebar=render_sidebar(cfg, products, cats))

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

        html = page_shell(
            cfg, base, title=title,
            ogtitle="%s ── %s" % (cfg["site"]["name"], cfg["site"]["tagline"]),
            desc=desc, path=path, content=content,
            chipbar=render_chipbar(cfg), jsonld=jsonld, products=products, cats=cats,
            robots=('<meta name="robots" content="noindex,follow">' if page > 1 else ""),
        )
        write("index.html" if page == 1 else "page/%d/index.html" % page, html)


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
        products=products, cats=cats,
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
                body = '<div class="feed">%s</div>' % "".join(
                    render_card(p, cats, fetched, km) for p in chunk)
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
           pager=render_pager(page, total_pages, cbase),
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
                products=products, cats=cats,
                robots=('<meta name="robots" content="noindex,follow">' if page > 1 else ""),
            ))


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

        # 関連商品（同カテゴリの他商品）
        rel_items = [x for x in by_cat.get(p["category"], []) if x["id"] != p["id"]]
        rel_items.sort(key=lambda x: -discount_rate(x))
        rel = "".join(
            '<a class="rel" href="/p/{id}/"><div class="rel-media">'
            '<img src="{img}" alt="{alt}" loading="lazy" width="300" height="300"></div>'
            '<div class="rel-body"><p class="rel-title">{t}</p>'
            '<p class="rel-price">¥{pr}</p></div></a>'.format(
                id=e(x["id"]), img=e(x["image"]), alt=e(x["title"]),
                t=e(x["title"]), pr=yen(x["price"]))
            for x in rel_items[:cfg["feed"]["relatedCount"]]
        )
        rel_block = ""
        if rel:
            rel_block = ('<h2 class="section-title">同じ売場のもの</h2>'
                         '<div class="related">%s</div>' % rel)

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

        content = """
<div class="wrap">
  <nav class="breadcrumb" aria-label="パンくず">
    <a href="/">ホーム</a><span class="sep">›</span>
    <a href="/c/{cslug}/">{clabel}</a><span class="sep">›</span>
    <span>{short}</span>
  </nav>

  <div class="detail-media">
    <img src="{img}" alt="{alt}" width="800" height="800">
    {tag}
    {sticker}
    {burst}
  </div>

  <h1 class="detail-title">{title}</h1>
  {over_notice}
  {cap}
  <div class="detail-actions">{watch}</div>
  {share_top}

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

  <h2 class="section-title">商品情報</h2>
  <table class="spec"><tbody>{spec}</tbody></table>

  {desc_block}

  <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。上のリンクから購入があった場合、当サイトが紹介料を受け取ることがあります。</p>

  {share_bottom}

  {rel_block}
</div>
""".format(cslug=e(p["category"]), clabel=e(c.get("label", "")),
           short=e(p["title"] if len(p["title"]) <= 18 else p["title"][:18] + "…"), img=e(p["image"]), alt=e(p["title"]),
           tag=render_pricetag(p), sticker=render_sticker(p),
           burst=render_burst(p), title=e(p["title"]),
           cap=cap, price=yen(p["price"]), cta_sub=cta_sub,
           price_label=e(price_label(p)),
           stale_cls=(" pop-note-stale" if is_stale(p, fetched) else ""),
           freshness=(
               "この価格は%sに見たものです。そのあと変わっているかもしれないので、"
               "楽天でいまの値段を確かめてください。" % stale_label(p).replace("の価格", "")
               if is_stale(p, fetched)
               else "値段は毎日見にいってます。買う前に楽天でも確かめてね（%s時点）" % e(p.get("lastSeen") or "")),
           url=e(p.get("affiliateUrl") or "#"),
           spec=spec, arrow=icons.use("arrow-right", "ic-arrow"),
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
            bodyclass="has-sticky-cta",
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
      <nav class="guide-toc" aria-label="この記事の目次">
        <p class="guide-toc-head">{ic_grid}売場案内</p>
        <ol>{toc}</ol>
      </nav>

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
            robots='<meta name="robots" content="index,follow">',
        ))

    # 404
    content = """
<div class="wrap">
  <div class="empty">
    {ic}
    <p class="empty-title">この値札は見つかりませんでした</p>
    <p>売り切れたか、URLが変わったようです。</p>
    <p style="margin-top:20px"><a class="btn btn-rakuten btn-lg" href="/">特価フィードへ戻る</a></p>
  </div>
</div>
""".format(ic=icons.use("tag", "ic-xxl"))
    write("404.html", page_shell(
        cfg, base, title="ページが見つかりません｜%s" % site["name"],
        desc="お探しのページは見つかりませんでした。", path="/404.html",
        content=content, chipbar=render_chipbar(cfg), products=products, cats=cats,
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
        products=products, cats=cats,
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
        products=products, cats=cats))


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
        products=products, cats=cats,
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
    total = max(1, -(-len(products) // per))
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


def main():
    cfg = load("config.json")
    data = load("products.json")
    products = data["products"] if isinstance(data, dict) else data
    products = [p for p in products if not p.get("hidden")]
    cats = {c["slug"]: c for c in cfg["categories"]}

    unknown = {p["category"] for p in products} - set(cats)
    if unknown:
        raise SystemExit("config.json に無いカテゴリが products.json にあります: %s" % ", ".join(sorted(unknown)))

    build_icon_sprite()
    ensure_placeholders(cfg, products)
    clean()
    base = tpl("base.html")

    guides = load_guides()
    cfg["_guides"] = guides
    build_index(cfg, base, products, cats)
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

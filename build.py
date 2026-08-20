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


def discount_rate(p):
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
    return (p.get("postedAt", ""), discount_rate(p), p.get("reviewCount") or 0)


def feed_order_desc(p):
    o = feed_order(p)
    return (o[0], o[1], o[2])


def price_basis_label(p):
    """楽天APIは定価を返さないので、履歴から出した基準価格は「通常」ではなく「以前」と書く"""
    return "以前" if p.get("priceBasis") == "history" else "通常"


def render_pricetag(p, size="card"):
    d = discount_rate(p)
    sub = []
    if p.get("unitNote"):
        sub.append('<span class="pricetag-unit">%s</span>' % e(p["unitNote"]))
    if d:
        sub.append('<span class="pricetag-was">%s ¥%s</span>' % (
            price_basis_label(p), yen(p["listPrice"])))
    sub_html = '<span class="pricetag-sub">%s</span>' % "".join(sub) if sub else ""
    return (
        '<div class="pricetag">'
        '<span class="pricetag-label">いま</span>'
        '<span class="pricetag-value"><span class="pricetag-yen">¥</span>'
        '<span class="pricetag-num">%s</span></span>'
        '%s</div>'
    ) % (yen(p["price"]), sub_html)


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


def render_sidebar(cfg, products, cats, active=None, guides=None):
    """フィードの脇（PC）／下（スマホ）に出る棚。
    ヘッダーと同じ黒帯をパネルの頭に載せて、本体と地続きに見せる。
    順位マーカーは値札POPの縮小版。順位そのものに意味があるので番号を振る。"""
    fetched = last_fetch_date(products)
    fresh = [p for p in products if not is_stale(p, fetched)] or products
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

    guide_banner = ""
    gs = guides if guides is not None else (cfg.get("_guides") or [])
    if gs:
        g = gs[0]
        guide_banner = (
            '<a class="guide-banner" href="/guide/%s/">'
            '<span class="gb-eyebrow">%s読んでおく</span>'
            '<span class="gb-label">%s</span>'
            '<span class="gb-note">%s</span>'
            '<span class="gb-go">攻略ガイドを読む%s</span>'
            '</a>'
        ) % (e(g["slug"]), icons.use("doc"), e(g.get("bannerLabel", "")),
             e(g.get("bannerNote", "")), icons.use("arrow-right", "ic-arrow"))

    return """<aside class="layout-side">
  <section class="side-card">
    <h2 class="side-head">{ic}<span>いま売れているもの</span></h2>
    <div class="side-body">
      <div class="rank-list">{rows}</div>
      <p class="side-note">楽天市場のレビュー件数が多い順です。値下がり幅とは関係ありません。</p>
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
</aside>""".format(ic=icons.use("bolt"), ic2=icons.use("grid"),
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


def render_card(p, cats, fetched=""):
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
        cap = '<p class="card-cap">%s</p>' % e(p["caption"])
    aria = "%s %d%%OFF ¥%s" % (p["title"], d, yen(p["price"])) if d else "%s ¥%s" % (p["title"], yen(p["price"]))
    return """<article class="card">
  <a class="card-media" href="/p/{id}/" aria-label="{aria}">
    <img src="{img}" alt="{alt}" loading="lazy" width="640" height="640">
    {tag}
    {sticker}
    {burst}
  </a>
  <div class="card-body">
    {cap}
    <h2 class="card-title"><a href="/p/{id}/">{title}</a></h2>
    <div class="card-tags"><a class="tag" href="/c/{cslug}/">{cicon}{clabel}</a>{tags}</div>
    <div class="card-foot">
      <span class="card-shop">{shop}</span>
      <a class="btn btn-rakuten" href="{url}" target="_blank" rel="nofollow sponsored noopener">楽天で見る{arrow}</a>
    </div>
  </div>
</article>""".format(
        id=e(p["id"]), aria=e(aria), img=e(p["image"]), alt=e(p["title"]),
        tag=render_pricetag(p), sticker=render_sticker(p),
        burst=render_burst(p), cap=cap,
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
    ranked = sorted(products, key=lambda x: (-discount_rate(x), -(x.get("reviewCount") or 0)))[:10]
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
        "CSS_HREF": asset_url("/assets/css/style.css"),
        "JS_SRC": asset_url("/assets/js/app.js"),
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
def build_index(cfg, base, products, cats):
    per = cfg["feed"]["perPage"]
    fetched = last_fetch_date(products)
    ordered = sorted(products, key=feed_order, reverse=True)
    total_pages = max(1, -(-len(ordered) // per))

    counts = {}
    for p in products:
        counts[p["category"]] = counts.get(p["category"], 0) + 1

    today = datetime.now(JST).strftime("%-m月%-d日")
    best = max((discount_rate(p) for p in products), default=0)
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
        cards = "".join(render_card(p, cats, fetched) for p in chunk)
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
""".format(lead=e(cfg["site"]["description"]), today=today, count=len(products),
           third=third, ic_cal=icons.use("calendar"), ic_box=icons.use("box"))
        if page > 1:
            head = """
<section class="page-head wrap-narrow">
  <p class="page-eyebrow">{ic}特価フィード</p>
  <h1 class="page-title">特価フィード</h1>
  <p class="page-lead">{count}件を新しい順に。いまは{page}ページ目です。</p>
</section>
""".format(ic=icons.use("bolt"), count=len(products), page=page)

        content = head + """
{featured}
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
           featured=(render_featured(cfg) if page == 1 else ""),
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
    for c in cfg["categories"]:
        items = [p for p in products if p["category"] == c["slug"]]
        items.sort(key=feed_order, reverse=True)
        best = max((discount_rate(p) for p in items), default=0)
        total_pages = max(1, -(-len(items) // per))
        cbase = "/c/%s/" % c["slug"]

        for page in range(1, total_pages + 1):
            chunk = items[(page - 1) * per: page * per]
            if chunk:
                body = '<div class="feed">%s</div>' % "".join(
                    render_card(p, cats, fetched) for p in chunk)
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
            basis = "通常価格" if price_basis_label(p) == "通常" else "以前の価格"
            note = "" if basis == "通常価格" else "<br><small>当サイトが過去60日で観測した最高値です</small>"
            rows.append((basis, "<td>¥%s（%d%%OFF）%s</td>" % (yen(p["listPrice"]), d, note)))
        if p.get("unitNote"):
            rows.append(("単価の目安", "<td>%s</td>" % e(p["unitNote"])))
        rows.append(("カテゴリ", '<td><a class="inline-cat" href="/c/%s/">%s%s</a></td>' % (
            p["category"], icons.use(c.get("icon", "tag")), e(c.get("label", "")))))
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
            cap = '<p class="detail-cap">%s</p>' % e(p["caption"])

        # レジの表示のように、価格の脇に根拠を小さく添える
        cta_sub = ""
        if p.get("unitNote"):
            cta_sub = '<span class="cta-sub">%s</span>' % e(p["unitNote"])
        elif d:
            cta_sub = '<span class="cta-sub cta-was">%s ¥%s</span>' % (
                price_basis_label(p), yen(p["listPrice"]))

        desc_block = ""
        if p.get("description"):
            paras = "".join("<p>%s</p>" % e(x) for x in p["description"].split("\n") if x.strip())
            desc_block = '<h2 class="section-title">どんな商品？</h2><div class="prose prose-card">%s%s</div>' % (paras, points)

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
  {cap}
  {share_top}

  <div class="sticky-cta">
    <div class="sticky-cta-inner">
      <div class="sticky-cta-price">
        <span class="cta-label">いま</span>
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

        title = "%s｜¥%s" % (p["title"], yen(p["price"]))
        if d:
            title = "【%d%%OFF】%s｜¥%s" % (d, p["title"], yen(p["price"]))

        write("p/%s/index.html" % p["id"], page_shell(
            cfg, base,
            title="%s - %s" % (title, cfg["site"]["name"]),
            ogtitle=title,
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
    return guides


def build_guides(cfg, base, products, cats, guides):
    site_url = cfg["site"]["url"].rstrip("/")
    links = load("links.json") if os.path.exists(os.path.join(ROOT, "links.json")) else {}

    def cta(key):
        """記事内のリンク。売場の案内看板として置く。
        URLが未設定なら何も出さない。埋まっていない看板を立てないため。"""
        item = links.get(key) or {}
        if not item.get("url"):
            return ""
        return ("""<aside class="cta-sign">
  <p class="cta-sign-label">{ic}{label}</p>
  <p class="cta-sign-note">{note}</p>
  <a class="btn btn-sign" href="{url}" target="_blank" rel="nofollow sponsored noopener">{button}{arrow}</a>
</aside>""").format(
            ic=icons.use("arrow-right", "ic-sign"),
            label=e(item.get("label", "")), note=e(item.get("note", "")),
            url=e(item["url"]), button=e(item.get("button", "開く")),
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
           sidebar=render_sidebar(cfg, products, cats, guides=guides))

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
        c = cats.get(p["category"], {})
        slim.append({
            "id": p["id"], "t": p["title"], "cap": p.get("caption", ""),
            "c": p["category"], "ci": c.get("icon", ""), "cl": c.get("short", ""),
            "pr": p["price"], "lp": p.get("listPrice") or 0, "d": discount_rate(p),
            "b": price_basis_label(p),
            "rc": p.get("reviewCount") or 0,
            "u": p.get("unitNote", ""), "img": p["image"], "url": p.get("affiliateUrl") or "#",
            "shop": p.get("shop", "楽天市場"), "tags": (p.get("tags") or [])[:3],
            "at": p.get("postedAt", ""),
        })
    write("assets/data/feed.json", json.dumps(slim, ensure_ascii=False, separators=(",", ":")))


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
    ordered = sorted(products, key=lambda p: p.get("postedAt", ""), reverse=True)[:30]
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
    build_feed_json(cfg, products, cats)
    build_sitemap(cfg, products)
    build_rss(cfg, products)

    check_padding_collisions()

    print("✅ ビルド完了")
    print("   商品 %d件 / カテゴリ %d件" % (len(products), len(cfg["categories"])))
    print("   生成: index.html, c/, p/, categories/, 固定ページ, sitemap.xml, feed.xml")


if __name__ == "__main__":
    main()

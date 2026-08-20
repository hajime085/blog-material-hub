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
import html
import icons
import urllib.parse
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))

# 生成物。クリーンビルド時にこれらを消す。
GENERATED_DIRS = ["p", "c", "categories", "about", "contact", "privacy", "disclaimer", "terms"]
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
def feed_order(p):
    """フィードの既定の並び。新しい順 → 割引率が高い順 → 安い順。
    同じ日に載ったものが「高い順」に並ぶと、特価サイトとして逆になる。"""
    return (p.get("postedAt", ""), discount_rate(p), -p.get("price", 0))


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


def render_sticker(p):
    d = discount_rate(p)
    if d < 5:
        return ""
    return (
        '<div class="sticker" aria-hidden="true">'
        '<span class="sticker-num">%d</span><span class="sticker-off">%%OFF</span>'
        '</div>'
    ) % d


def render_sidebar(cfg, products, cats, active=None):
    """PCではフィードの脇に、スマホではフィードの下に出る。
    ランキングAPIが使えないので、売れ筋の根拠はレビュー件数。
    順位に意味があるので、ここでは番号を振る。"""
    ranked = sorted(products, key=lambda p: -(p.get("reviewCount") or 0))[:5]
    rows = ""
    for i, p in enumerate(ranked, 1):
        rows += (
            '<a class="rank" href="/p/{id}/">'
            '<span class="rank-no">{i}</span>'
            '<img class="rank-img" src="{img}" alt="" width="120" height="120" loading="lazy">'
            '<span class="rank-body">'
            '<span class="rank-title">{t}</span>'
            '<span class="rank-meta">★{ra} <b>{rc}</b>件のレビュー</span>'
            '<span class="rank-price">¥{pr}</span>'
            '</span></a>'
        ).format(id=e(p["id"]), i=i, img=e(p["image"]), t=e(p["title"]),
                 ra=p.get("reviewAverage") or "-", rc=yen(p.get("reviewCount") or 0),
                 pr=yen(p["price"]))

    counts = {}
    for p in products:
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    cat_rows = "".join(
        '<a class="side-cat%s" href="/c/%s/">%s<span>%s</span><b>%d</b></a>'
        % (" is-active" if active == c["slug"] else "", c["slug"],
           icons.use(c["icon"]), e(c["short"]), counts.get(c["slug"], 0))
        for c in cfg["categories"]
    )

    coupon = ""
    cp = cfg["site"].get("coupon") or {}
    if cp.get("url") and cp.get("label"):
        coupon = (
            '<section class="side-card side-coupon">'
            '<h2 class="side-title">%sいま使えるクーポン</h2>'
            '<p class="coupon-label">%s</p>'
            '<p class="coupon-note">%s</p>'
            '<a class="btn btn-rakuten btn-block" href="%s" target="_blank" '
            'rel="nofollow sponsored noopener">クーポンを見る%s</a>'
            '</section>'
        ) % (icons.use("tag"), e(cp["label"]), e(cp.get("note", "")), e(cp["url"]),
             icons.use("arrow-right", "ic-arrow"))

    return """<aside class="layout-side">
  <section class="side-card">
    <h2 class="side-title">{ic}いま売れているもの</h2>
    <div class="rank-list">{rows}</div>
    <p class="side-note">楽天市場のレビュー件数が多い順です。</p>
  </section>
  {coupon}
  <section class="side-card">
    <h2 class="side-title">{ic2}売場から探す</h2>
    <div class="side-cats">{cat_rows}</div>
  </section>
</aside>""".format(ic=icons.use("bolt"), ic2=icons.use("grid"),
                   rows=rows, cat_rows=cat_rows, coupon=coupon)


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
        ("sns-x", "X", "https://x.com/intent/post?text=%s&url=%s" % (q(text), q(url))),
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


def render_card(p, cats):
    cat = cats.get(p["category"], {})
    d = discount_rate(p)
    tags = ""
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
        tag=render_pricetag(p), sticker=render_sticker(p), cap=cap,
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
def page(cfg, base, *, title, desc, path, content, ogtype="website",
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
        "FOOTER_OPERATOR": footer_operator(cfg),
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


def jsonld_block(obj):
    return '<script type="application/ld+json">%s</script>' % json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- builders
def build_index(cfg, base, products, cats):
    per = cfg["feed"]["perPage"]
    ordered = sorted(products, key=feed_order, reverse=True)
    cards = "".join(render_card(p, cats) for p in ordered[:per])
    more = ""
    if len(ordered) > per:
        more = ('<div class="load-more">'
                '<button class="btn btn-ghost" id="loadMore">もっと見る</button></div>')

    counts = {}
    for p in products:
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    cat_cards = "".join(
        '<a class="cat-card" href="/c/%s/">%s'
        '<span class="l">%s</span><span class="c">%d件</span></a>'
        % (c["slug"], icons.use(c["icon"], "ic-xl"), e(c["short"]), counts.get(c["slug"], 0))
        for c in cfg["categories"]
    )

    today = datetime.now(JST).strftime("%-m月%-d日")
    best = max((discount_rate(p) for p in products), default=0)
    n_off = sum(1 for p in products if discount_rate(p) >= 5)
    # 値下がりを1件も検知していない日に「最大0%OFF」と出しても意味がない。
    # そのときは、いま何を見張っているのかを出す。
    if best:
        third = "{ic}最大{best}%OFF".format(ic=icons.use("bolt"), best=best)
    else:
        third = "{ic}値下がりを監視中".format(ic=icons.use("bolt"))

    content = """
<section class="hero wrap-narrow">
  <h1 class="hero-title">二度見する安さ、<br><span class="hl">ぜんぶここに。</span></h1>
  <p class="hero-lead">{lead}</p>
  <div class="hero-meta">
    <span>{ic_cal}{today}更新</span>
    <span>{ic_box}掲載{count}件</span>
    <span>{third}</span>
  </div>
</section>

<div class="layout wrap-wide">
<div class="layout-main">
  <div class="toolbar">
    <p class="result-count"><b id="resultCount">{count}</b> 件の特価</p>
    <div class="sorter" role="group" aria-label="並べ替え">
      <button type="button" data-sort="new" aria-pressed="true">新着</button>
      <button type="button" data-sort="off" aria-pressed="false">割引率</button>
      <button type="button" data-sort="cheap" aria-pressed="false">安い順</button>
    </div>
  </div>
  <div class="feed" id="feed">{cards}</div>
  {more}
  <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。価格・在庫・送料は取得時点のもので、変動します。購入前に楽天市場の商品ページで最新の条件をご確認ください。</p>
</div>
{sidebar}
</div>
""".format(lead=e(cfg["site"]["description"]), today=today, count=len(products),
           best=best, cards=cards, more=more, cat_cards=cat_cards,
           ic_cal=icons.use("calendar"), ic_box=icons.use("box"), third=third,
           sidebar=render_sidebar(cfg, products, cats))

    jsonld = jsonld_block({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": cfg["site"]["name"],
        "url": cfg["site"]["url"],
        "description": cfg["site"]["description"],
        "inLanguage": "ja",
    })

    write("index.html", page(
        cfg, base,
        title="%s｜%s" % (cfg["site"]["name"], cfg["site"]["tagline"]),
        ogtitle="%s ── %s" % (cfg["site"]["name"], cfg["site"]["tagline"]),
        desc=cfg["site"]["description"], path="/", content=content,
        chipbar=render_chipbar(cfg), jsonld=jsonld, products=products, cats=cats,
    ))


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
  <p class="page-eyebrow">CATEGORIES</p>
  <h1 class="page-title">カテゴリ一覧</h1>
  <p class="page-lead">気になる売場から、値下がりしたものだけを覗けます。</p>
</section>
<section class="wrap"><div class="cat-grid">%s</div></section>
""" % cat_cards
    write("categories/index.html", page(
        cfg, base, title="カテゴリ一覧｜%s" % cfg["site"]["name"],
        desc="ヤスミルの全カテゴリ。食品・家電・日用品など、売場ごとに特価をまとめています。",
        path="/categories/", content=content, chipbar=render_chipbar(cfg),
        products=products, cats=cats,
    ))

    # 各カテゴリ
    for c in cfg["categories"]:
        items = [p for p in products if p["category"] == c["slug"]]
        items.sort(key=feed_order, reverse=True)
        if items:
            cards = "".join(render_card(p, cats) for p in items)
            body = '<div class="feed">%s</div>' % cards
        else:
            body = ('<div class="empty">' + icons.use("tag", "ic-xxl") +
                    '<p class="empty-title">この売場はまだ空っぽです</p>'
                    '<p>値下がりを見つけ次第ここに並べます。</p></div>')
        best = max((discount_rate(p) for p in items), default=0)
        content = """
<section class="page-head wrap-narrow">
  <p class="page-eyebrow">{icon}CATEGORY</p>
  <h1 class="page-title">{label}の特価</h1>
  <p class="page-lead">{count}件掲載{bestnote}</p>
</section>
<div class="layout wrap-wide">
<div class="layout-main">{body}
  <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。価格・在庫・送料は取得時点のものです。</p>
</div>
{sidebar}
</div>
""".format(icon=icons.use(c["icon"]), label=e(c["label"]), count=len(items),
           bestnote="・最大%d%%OFF" % best if best else "・値下がりを監視中", body=body,
           sidebar=render_sidebar(cfg, products, cats, active=c["slug"]))
        write("c/%s/index.html" % c["slug"], page(
            cfg, base,
            title="%sの特価まとめ｜%s" % (c["label"], cfg["site"]["name"]),
            desc="%sの値下がり商品だけをまとめています。%s" % (c["label"], cfg["site"]["description"]),
            path="/c/%s/" % c["slug"], content=content,
            chipbar=render_chipbar(cfg, active=c["slug"]),
            products=products, cats=cats,
        ))


def build_products(cfg, base, products, cats):
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
            rows.append(("レビュー", "<td>★%s（%s件）</td>" % (
                p["reviewAverage"], p.get("reviewCount", 0))))
        rows.append(("掲載日", "<td>%s</td>" % e(p.get("postedAt", ""))))
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
  </div>

  <h1 class="detail-title">{title}</h1>
  {cap}
  {share_top}

  <div class="sticky-cta">
    <div class="sticky-cta-inner">
      <div class="sticky-cta-price"><span class="y">¥</span><span class="n">{price}</span></div>
      <a class="btn btn-rakuten btn-lg" href="{url}" target="_blank" rel="nofollow sponsored noopener">楽天市場で見る{arrow}</a>
    </div>
  </div>
  <p class="price-note">※ 価格・在庫・送料は{posted}時点のものです。変動するため、購入前に楽天市場でご確認ください。</p>

  <h2 class="section-title">商品情報</h2>
  <table class="spec"><tbody>{spec}</tbody></table>

  {desc_block}

  <p class="affiliate-note">当サイトは楽天アフィリエイトプログラムに参加しています。上のリンクから購入があった場合、当サイトが紹介料を受け取ることがあります。</p>

  {share_bottom}

  {rel_block}
</div>
""".format(cslug=e(p["category"]), clabel=e(c.get("label", "")),
           short=e(p["title"] if len(p["title"]) <= 18 else p["title"][:18] + "…"), img=e(p["image"]), alt=e(p["title"]),
           tag=render_pricetag(p), sticker=render_sticker(p), title=e(p["title"]),
           cap=cap, price=yen(p["price"]), url=e(p.get("affiliateUrl") or "#"),
           posted=e(p.get("postedAt", "")), spec=spec, arrow=icons.use("arrow-right", "ic-arrow"),
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

        write("p/%s/index.html" % p["id"], page(
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
        write("%s/index.html" % slug, page(
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
    write("404.html", page(
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
    for c in cfg["categories"]:
        urls.append((site_url + "/c/%s/" % c["slug"], now, "0.8", "daily"))
    for p in products:
        urls.append((site_url + "/p/%s/" % p["id"], p.get("postedAt", now), "0.7", "weekly"))
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

    build_index(cfg, base, products, cats)
    build_categories(cfg, base, products, cats)
    build_products(cfg, base, products, cats)
    build_static_pages(cfg, base, products, cats)
    build_feed_json(cfg, products, cats)
    build_sitemap(cfg, products)
    build_rss(cfg, products)

    print("✅ ビルド完了")
    print("   商品 %d件 / カテゴリ %d件" % (len(products), len(cfg["categories"])))
    print("   生成: index.html, c/, p/, categories/, 固定ページ, sitemap.xml, feed.xml")


if __name__ == "__main__":
    main()

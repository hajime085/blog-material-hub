#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Threadsへの自動投稿。

XのAPIは2026年2月から従量課金だけになり、URLを含む投稿は1件$0.20。
一方でThreadsのAPIは無料で、1日250投稿まで出せる。
自分のアカウントに出すだけなら、Metaの審査（App Review）も要らない。
開発モードのまま、自分をテスターにして使い続けられる。

貼るのは楽天のアフィリエイトURLではなく、このサイトのページ。
・アフィリエイトリンクは登録済みの自サイト側にあり、
  投稿そのものにはアフィリエイトリンクが含まれない形になる
・サイトに来てもらえれば、他のものも見てもらえる
・楽天はThreadsを認定SNSに入れているので、貼ること自体は問題ない

【PR】は文頭に置く。楽天のガイドラインは
「一番下に沢山のハッシュタグと合わせて#PR」をNG例として挙げており、
良い例を「PR表記が上部に位置している」と定めている。

使い方:
    python3 threads.py --dry-run     出すものと文面を見るだけ
    python3 threads.py               実際に投稿する
    python3 threads.py --limit       いまの残り投稿数を見る
"""

import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))
API = "https://graph.threads.net/v1.0"
STATE = "threads_posted.json"


def load(name, default=None):
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_dotenv():
    """.env があれば環境変数として読み込む。
    トークンをコードにもチャットにも残さないための入り口。"""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def credentials():
    load_dotenv()
    uid = os.environ.get("THREADS_USER_ID") or ""
    token = os.environ.get("THREADS_ACCESS_TOKEN") or ""
    if not uid or not token:
        sys.exit(
            "Threadsのトークンが見つかりません。\n\n"
            "  1. https://developers.facebook.com/ でアプリを作る（用途は Threads）\n"
            "  2. Threads API を追加し、自分のアカウントをテスターにする\n"
            "  3. 長期トークン（60日）と、あなたのユーザーIDを取る\n"
            "  4. .env に次の2行を書き込む\n"
            "       THREADS_USER_ID=...\n"
            "       THREADS_ACCESS_TOKEN=...\n\n"
            ".env は .gitignore に入っているのでコミットされません。\n"
            "自動実行に使うときは、GitHubのSecretsにも同じ名前で入れてください。"
        )
    return uid, token


def api(method, path, params, token):
    params = dict(params or {})
    params["access_token"] = token
    url = "%s/%s" % (API, path.lstrip("/"))
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode(), method="GET")
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("User-Agent", "yasumiru/1.0")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def yen(n):
    return "{:,}".format(int(n))


def publishing_limit(uid, token):
    """いま何件出せるか。上限は1日250件だが、無駄に近づかない。"""
    try:
        d = api("GET", "%s/threads_publishing_limit" % uid,
                {"fields": "quota_usage,config"}, token)
        row = (d.get("data") or [{}])[0]
        used = row.get("quota_usage", 0)
        total = (row.get("config") or {}).get("quota_total", 250)
        return used, total
    except Exception as ex:                               # noqa: BLE001
        print("  残り投稿数を取れませんでした: %s" % ex, file=sys.stderr)
        return None, None


# ---------------------------------------------------------------- 文面

def ends_label(et):
    """「8月27日 9:59まで」。終了時刻が分かるときだけ出す。"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", (et or "").strip())
    if not m:
        return ""
    return "%d月%d日 %s:%s まで" % (int(m.group(2)), int(m.group(3)),
                                 m.group(4).lstrip("0") or "0", m.group(5))


def short_title(t, n=34):
    """商品名を短く。楽天の商品名は検索語の羅列なので、長く出しても読めない。"""
    t = (t or "").strip()
    return t if len(t) <= n else t[:n].rstrip("…、 ") + "…"


# 締めの一言。付けるかどうかは投稿ごとに回す。
# 「使ったことある人いますか」は、こちらが使ったとは言っていないので嘘にならない。
# 反応がつくとアルゴリズムに拾われやすくなる、というのは方々で言われている。
TAILS = [
    "",
    "\n\n使ったことある人いますか？",
    "\n\n気になる人は保存しておくと、あとで見返せます。",
    "",
    "\n\nこれ、他にいいのがあれば教えてください。",
]


def hook(p, pr):
    """1行目。ここで止まってもらえないと、あとが全部無駄になる。

    値引き率から入るのはやめる。「◯%OFFです」は売り込みの形で、
    流れてくる人の指は止まらない。
    先に出すのは、値段そのものか、期限か、他の人が買った数。
    どれも手元のデータから言えることで、感想は入れない。

    使った体験は書かない。買っていない商品の体験談は作り話になる。
    【PR】を掲げているアカウントがそれをやるのは筋が通らない。
    """
    price = yen(p["pr"])
    ends = ends_label(p.get("et"))
    rc = p.get("rc") or 0
    unit = (p.get("u") or "").strip()

    cands = []

    # ① 値段そのもの。単価が分かるものは、それを添える。
    if unit:
        cands.append("%s%s円。%s。" % (pr, price, unit))
    cands.append("%sこれ、%s円です。" % (pr, price))

    # ② 期限。本物の終了時刻があるときだけ。煽りではなく事実。
    if ends:
        cands.append("%sこの値段は%s。" % (pr, ends))

    # ③ 他の人が買った数。感想ではなく記録。
    if rc >= 1000:
        cands.append("%sレビュー%s件で、星%s。" % (pr, yen(rc), p.get("ra", "")))

    # ④ 値引きの幅が大きいときは、金額の差で見せる。率より額のほうが伝わる。
    if p.get("d") and p.get("lp") and (p["lp"] - p["pr"]) >= 500:
        cands.append("%s%s円だったものが%s円になっています。"
                     % (pr, yen(p["lp"]), price))

    return cands


def compose_product(p, site, pr, reply_link=False, ev=None, seq=0):
    """商品の投稿文。

    キャプションがあればそれを先頭に置く。人が書いた文なので、いちばん読ませる。

    無ければ、手元のデータだけで組み立てる。
    キャプションを書くのは人の作業なので、そこを待っていると
    今朝見つけた79%OFFを流せるのが明日になる。
    セールの訴求は鮮度がすべてなので、待たないほうを選ぶ。

    データだけで書く場合も、書くのは事実だけ。
    値引き、送料、レビュー件数、終了時刻、買いまわりに使えるか。
    どれも楽天のデータか、そこから機械的に決まることしか使わない。
    """
    cap = (p.get("cap") or "").strip()
    title = short_title(p["t"])

    lines = []
    if cap:
        lines.append(pr + cap)
        lines.append(title)
        if p.get("d") and p.get("lp") and (yen(p["lp"]) not in cap):
            lines.append("%s円 → %s円（%d%%OFF）" % (yen(p["lp"]), yen(p["pr"]), p["d"]))
    else:
        cands = hook(p, pr)
        head = cands[seq % len(cands)]
        lines.append(head)
        lines.append(title)
        # 1行目で値段の変化を言っているなら、直後に同じことを書かない。
        # 同じ数字が2行続くと、読む気が失せる。
        if p.get("d") and p.get("lp") and yen(p["lp"]) not in head:
            lines.append("%s円 → %s円（%d%%OFF）" % (yen(p["lp"]), yen(p["pr"]), p["d"]))
        elif p.get("d") and p.get("lp"):
            lines.append("%d%%OFF です。" % p["d"])

    pts = []
    for x in (p.get("pt") or []):
        if yen(p["pr"]) in x and (yen(p["lp"]) in x if p.get("lp") else True):
            continue
        if x.strip() == "%s円" % yen(p["pr"]):
            continue
        pts.append(x)

    if not cap:
        free = "送料無料" in (p.get("tags") or [])
        pts = ["送料無料" if free else "送料は別にかかります"]
        rc = p.get("rc") or 0
        # 1行目でレビュー数を言っているなら、繰り返さない
        if rc >= 100 and yen(rc) not in lines[0]:
            pts.append("レビュー%s件・★%s" % (yen(rc), p.get("ra", "")))
        if ev and p["pr"] >= 1000 and free:
            pts.append("買いまわりに使えます（税込1,000円以上・送料無料）")
        ends = ends_label(p.get("et"))
        # 1行目で期限を言っているなら、繰り返さない
        if ends and ends not in lines[0]:
            pts.append("この値段は%s" % ends)

    if pts:
        lines.append("")
        lines += ["・" + x for x in pts[:4]]

    body = "\n".join(lines) + TAILS[seq % len(TAILS)]

    url = "%s/p/%s/" % (site, p["id"])
    if reply_link:
        return body, url
    return body + "\n\n" + url, None


def compose_guide(g, site, pr, reply_link=False):
    """攻略ガイドの案内。

    ここに【PR】は付けない。
    この投稿にアフィリエイトリンクは含まれておらず、
    リンク先は自分のサイトの記事で、特定の商品を勧めてもいない。
    仕組みの解説に広告の印を付けると、実態より広告寄りに見せることになる。
    それはそれで正確でない。

    ステマ規制の名宛人は「商品・サービスを供給する事業者」であって、
    紹介する側ではない。楽天のガイドラインが求めているのも
    「アフィリエイトリンクを掲載する投稿」へのPR表示。

    商品の投稿には付ける。あちらは値段を出して買いに誘導するので、
    実質的に広告そのものになる。
    """
    url = "%s%s" % (site, g["path"])
    body = "\n".join([g["title"], "", g["lead"]])
    if reply_link:
        return body, url
    return body + "\n\n" + url, None


def compose_page(page, site, pr, reply_link=False):
    """セールの案内。理由は compose_guide と同じで、PRは付けない。"""
    url = "%s%s" % (site, page["path"])
    body = "\n".join([page["title"], "", page["lead"]])
    if reply_link:
        return body, url
    return body + "\n\n" + url, None


# ---------------------------------------------------------------- 何を出すか

def guide_posts(site):
    """攻略ガイドの記事。商品より読み物として届きやすい。

    リード文は記事ごとに手で書く。description をそのまま使うと
    検索向けの文章になり、読ませる文にならない。
    """
    return [
        {"key": "guide:price-trick",
         "title": "その「◯%OFF」は本当か",
         "lead": "楽天で安く見えて安くない商品には、はっきりした型があります。\n"
                 "毎日800件ほど機械的に確認して弾いていると見えてきた5つを、"
                 "見分け方までまとめました。多くは商品名を読むだけで分かります。",
         "path": "/guide/price-trick/"},
        {"key": "guide:marathon",
         "title": "お買い物マラソンの攻略法",
         "lead": "買いまわりは1ショップ税込1,000円以上で1カウント。\n"
                 "「あと1店舗」に意味があるかは、その人の買い物の総額で決まります。\n"
                 "計算のしかたと、10店舗を目指さないほうがいい理由をまとめました。",
         "path": "/guide/rakuten-marathon/"},
        {"key": "guide:super-sale",
         "title": "楽天スーパーSALEの攻略法",
         "lead": "買いまわりの条件、クーポンとの併用、ポイント上限まで。\n"
                 "そのまま真似できる買い方の手順にしています。",
         "path": "/guide/rakuten-super-sale/"},
    ]


def event_posts(site, ev, n_kaimawari):
    """開催中のセール向け。イベントの外では出さない。"""
    if not ev:
        return []
    out = [{
        "key": "page:kaimawari:%s" % ev["start"][:10],
        "title": "買いまわりに使えるもの",
        "lead": "1ショップ税込1,000円以上で1カウント。送料は判定に入りません。\n"
                "1,000円＋送料590円より、1,200円の送料無料のほうが安くて同じ1カウントです。\n"
                "1,000円以上かつ送料無料のものだけ%d件集めました。"
                "別々の店から1つずつ選ぶ組み方も出しています。" % n_kaimawari,
        "path": "/kaimawari/"}]
    return out


def active_event():
    doc = load("events.json", {}) or {}
    now = datetime.now(JST).replace(tzinfo=None)
    for ev in doc.get("events", []):
        if ev.get("status") != "確定" or ev.get("kind") not in ("marathon", "sale"):
            continue
        try:
            a = datetime.strptime(ev["start"][:16], "%Y-%m-%d %H:%M")
            b = datetime.strptime((ev.get("end") or ev["start"])[:16], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        if a <= now <= b:
            return ev
    return None



# ---------------------------------------------------------------- リンクなしの投稿

def tip_posts():
    """リンクなしの知識。tips.json から読む。

    コードの中に持たないのは、書き足すのが仕事だから。
    ファイルを開いて1つ足すだけで済むようにしておく。

    書いてよいのは、自分の記事で確かめた事実だけ。
    使っていない商品の感想や、根拠のない数字は書かない。
    """
    doc = load("tips.json", {}) or {}
    rows = doc.get("tips") or []
    return [{"key": "tip:%02d" % i, "title": t.get("title", ""),
             "lead": t.get("body", ""), "cat": t.get("cat", ""),
             "to": t.get("to", "")}
            for i, t in enumerate(rows) if t.get("title")]


def _unused_tip_posts():

    return [{"key": "tip:%02d" % i, "title": t, "lead": b}
            for i, (t, b) in enumerate(TIPS)]


def schedule_post():
    """次に来るイベントの予定。リンクは貼らない。

    「予想」の日程は予想と明記する。
    発表前の日程を確定のように書くのは、
    このサイトが批判している「安く見えて安くない」と同じことになる。
    """
    doc = load("events.json", {}) or {}
    now = datetime.now(JST).replace(tzinfo=None)
    rows = []
    for ev in doc.get("events", []):
        try:
            a = datetime.strptime(ev["start"][:16], "%Y-%m-%d %H:%M")
            b = datetime.strptime((ev.get("end") or ev["start"])[:16], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        if b < now:
            continue
        rows.append((a, b, ev))
    if not rows:
        return []
    rows.sort()

    def fmt(d):
        return "%d月%d日 %02d:%02d" % (d.month, d.day, d.hour, d.minute)

    lines = []
    for a, b, ev in rows[:3]:
        mark = "" if ev.get("status") == "確定" else "（予想）"
        state = "開催中" if a <= now <= b else "あと%d日" % max(0, (a.date() - now.date()).days)
        lines.append("・%s%s\n  %s 〜 %s（%s）" % (ev["name"], mark, fmt(a), fmt(b), state))

    key = "schedule:%s" % now.strftime("%Y-%m-%d")
    return [{"key": key,
             "title": "楽天のイベント予定",
             "lead": "\n".join(lines) +
                     "\n\n予想と書いたものは、まだ楽天が発表していない日程です。"
                     "過去の傾向からの見込みなので、変わることがあります。"}]


# 返信で案内するときの一言。行き先ごとに変える。
# どこへ送るのかと噛み合っていないと、押した人が肩透かしを食う。
# 同じ文が毎回続くと機械が貼っているのが見えるので、数を用意しておく。
FOLLOW = {
    "/guide/rakuten-marathon/": [
        "この話、もう少し詳しく書いています。",
        "買いまわりの数え方を、最初からまとめています。",
        "「あと1店舗」が得かどうかの計算も載せています。",
        "買ってから後悔しないように、条件を先に確かめておきましょう。",
        "当サイトでは、こういう見落としやすい条件をまとめています。",
        "ポイントの上限や期限の話も、あわせて書いています。",
        "同じところでつまずかないよう、手順にしてあります。",
    ],
    # 分類ごとに分ける。同じ記事へ送る話でも、
    # 送料の話に「商品名を読むだけで見抜ける」と返すのは噛み合わない。
    "shipping": [
        "送料の見落としは、記事のほうにもまとめています。",
        "買ってから後悔しないように、合計で比べる癖をつけておきましょう。",
        "当サイトでは送料も楽天のデータを見て、別なら別と書いています。",
        "「送料無料」の4つの型も、あわせて解説しています。",
    ],
    "choose": [
        "商品の選び方も、あわせてまとめています。",
        "買う前に確かめる7つを、チェックリストにしてあります。",
        "当サイトでは、レビューの数と評価を条件にして選んでいます。",
    ],
    "/guide/price-trick/": [
        "同じような見せ方を、5つの型に分けてまとめました。",
        "ほかの落とし穴も、実例つきで書いています。",
        "買う前に確かめる7つを、チェックリストにしてあります。",
        "買ってから後悔しないように、先に見ておくと安全です。",
        "当サイトでは毎日800件ほど見て、こういう表記を弾いています。",
        "商品名を読むだけで見抜けるものが、ほとんどです。",
        "「安く見えて安くない」の型を、まとめて解説しています。",
    ],
    "/guide/rakuten-super-sale/": [
        "セールの買い方を、そのまま真似できる手順にしています。",
        "買いまわりの条件やクーポンの併用も書いています。",
        "次のセールで迷わないように、先に読んでおくと楽です。",
        "当サイトでは、こういう仕組みの話もまとめています。",
    ],
    "/kaimawari/": [
        "条件に合う商品を集めてあります。",
        "1,000円以上・送料無料のものだけ並べています。",
        "別々の店から1つずつ選ぶ組み方も出しています。",
        "探す手間が省けるように、こちらでまとめています。",
    ],
}
FOLLOW_DEFAULT = ["くわしくはこちらです。"]


def compose_plain(x, site="", i=0):
    """知識や予定の投稿。本文にはリンクを入れない。

    リンクは投稿したあとに、自分への返信として貼る。
    本文が読みやすいままリーチを保てるし、
    読んだ人が「もっと知りたい」と思ったときに続きがある。

    行き先が無いものは、本文だけで終える。
    案内する先が無いのに一言だけ足しても、押すところがない。
    """
    body = x["title"] + "\n\n" + x["lead"]
    to = x.get("to")
    if not to or not site:
        return body, None
    lines = FOLLOW.get(x.get("cat")) or FOLLOW.get(to) or FOLLOW_DEFAULT
    return body, lines[i % len(lines)] + "\n" + site + to


# ---------------------------------------------------------------- 選ぶ

def pick(cfg, posted, want):
    """今回出すものを選ぶ。

    リンクのある投稿とない投稿を交互に出す。
    全部にリンクが入っていると、読む側から見れば宣伝の列にしかならない。
    間に知識や予定を挟むことで、読んで終わりでいい投稿が混ざる。

    一度出したものは二度と出さない。
    ただし知識（tip）だけは、出し切ったら古いものから回す。
    種類が有限で、時間が経てば読む人も変わるため。
    """
    site = cfg["site"]["url"].rstrip("/")
    th = cfg.get("threads", {})
    # リンクを本文に入れるか、自分の投稿への返信として貼るか。
    # どちらが伸びるかは測るまで分からないので、切り替えられるようにしてある。
    placement = th.get("linkPlacement", "body")
    on = th.get("prOn") or ["product"]
    pr = (cfg["site"].get("prLabel") or "") if "product" in on else ""

    feed = load("assets/data/feed.json", []) or []
    done = set(posted.get("keys") or [])
    ev = active_event()
    n_km = sum(1 for p in feed
               if p["pr"] >= 1000 and "送料無料" in (p.get("tags") or []))

    # ---- リンクのある投稿 ----
    reply_link = placement == "reply"
    # 商品。ここが主役。
    linked = []
    # 記事とセールの案内。リンクは付くが、中身は読み物なので
    # 「商品以外」として数える。
    other = []
    for e in event_posts(site, ev, n_km):
        if e["key"] not in done:
            other.append((e["key"], compose_page(e, site, pr, reply_link)))
    for g in guide_posts(site):
        if g["key"] not in done:
            other.append((g["key"], compose_guide(g, site, pr, reply_link)))
    # 商品は新しいものだけを出す。
    #
    # 掲載中の商品は日が経つほど溜まっていく。古い順に消化していくと、
    # 何日も前に見つけた値段を、いま見つけたかのように流すことになる。
    # 値段は毎日動くので、それは古い情報を配っているのと同じ。
    #
    # 出せる新しい商品が無い日は、商品を出さない。
    # 数を埋めるために古いものを引っぱり出すくらいなら、知識を出したほうがいい。
    fresh_days = th.get("freshDays", 3)
    limit = (datetime.now(JST) - timedelta(days=fresh_days)).strftime("%Y-%m-%d")
    now_s = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

    items = []
    for p in feed:
        if ("product:" + p["id"]) in done:
            continue
        # キャプションが無くても、言えることがあるなら出す。
        # 値引きが取れているか、期限が分かっていること。
        # どちらも無い商品は、値段を並べるだけになるので出さない。
        if not p.get("cap") and not (p.get("d") or p.get("et")):
            continue
        if (p.get("at") or "")[:10] < limit:
            continue
        # 終わったセールは出さない。
        # サイトの作り直しから投稿までに終わることがあるので、ここでも見る。
        et = (p.get("et") or "").strip()
        if et and et[:16].replace("T", " ") < now_s:
            continue
        items.append(p)
    items.sort(key=lambda p: p.get("at") or "", reverse=True)

    n_done = len([k for k in (posted.get("keys") or []) if k.startswith("product:")])
    for n, p in enumerate(items):
        linked.append(("product:" + p["id"],
                       compose_product(p, site, pr, reply_link, ev, n_done + n)))

    # ---- 商品以外 ----
    # 記事・セールの案内・知識・予定をまとめて扱う。
    # 読む人から見れば、どれも「いま買うもの」ではない。
    plain = list(other)
    for x in schedule_post():
        if x["key"] not in done:
            plain.append((x["key"], compose_plain(x, site)))
    tips = tip_posts()
    fresh = [t for t in tips if t["key"] not in done]
    if not fresh:
        # 出し切ったので、いちばん前に出したものから回す
        order = {k: i for i, k in enumerate(posted.get("keys") or [])}
        fresh = sorted(tips, key=lambda t: order.get(t["key"], 0))
    else:
        # 同じ分野が続かないようにする。買いまわりの話ばかり4本続くと、
        # 読む側からは同じことを言われているようにしか見えない。
        recent = [k for k in (posted.get("keys") or []) if k.startswith("tip:")][-3:]
        cats = {t["key"]: t.get("cat", "") for t in tips}
        seen = {cats.get(k) for k in recent}
        other = [t for t in fresh if t.get("cat") not in seen]
        if other:
            fresh = other + [t for t in fresh if t not in other]
    for n, t in enumerate(fresh):
        # 一言は順番に入れ替える。同じ文が毎回続くと機械が貼っているのが見える。
        seq = len([k for k in (posted.get("keys") or []) if k.startswith("tip:")]) + n
        plain.append((t["key"], compose_plain(t, site, seq)))

    # ---- どちらを先に取るか ----
    #
    # このサイトは特価を出すサイトなので、商品が主でなければならない。
    # 知識や予定ばかり流していると、ノウハウのアカウントなのか
    # 特価のアカウントなのか分からなくなる。
    #
    # だから商品7、それ以外3の割合を保つ。
    # 直近10件を見て、商品が足りていなければ商品を取る。
    #
    # そのうえで、足りているときの選び方は時間帯で決める。
    # 夜は買う時間なので商品を、昼は読む時間なので知識を優先する。
    share = th.get("productShare", 0.7)
    recent = [x.get("kind") for x in (posted.get("log") or [])][-10:]
    have = sum(1 for k in recent if k == "product")
    short_of_product = (not recent) or (have < len(recent) * share)

    hour = datetime.now(JST).hour
    night = hour >= 19 or hour < 2

    # 商品が3つ続いたら、いったん挟む。
    # 割合を守るだけだと、足りない日に商品を6つ並べて、
    # 次の日は知識だけ、という揺れ方をする。それでは宣伝の列になる。
    last2 = [k for k in recent[-2:]]
    run = len(last2) == 2 and all(k == "product" for k in last2)

    if run:
        want_plain = True
    elif short_of_product:
        want_plain = False
    else:
        want_plain = not night

    out = []
    while len(out) < want and (linked or plain):
        pool = plain if want_plain else linked
        if not pool:
            pool = linked if want_plain else plain
        key, (body, link) = pool.pop(0)
        out.append((key, body, link))
        want_plain = not want_plain
    return out


# ---------------------------------------------------------------- 出す

def publish(uid, token, text, reply_to=None):
    """2段階。まず入れ物を作り、それから公開する。

    reply_to を渡すと、その投稿への返信として出す。
    自分の投稿への返信なので、楽天が禁じている
    「他人の投稿への返信やコメント欄への掲載」には当たらない。
    """
    params = {"media_type": "TEXT", "text": text}
    if reply_to:
        params["reply_to_id"] = reply_to
    c = api("POST", "%s/threads" % uid, params, token)
    cid = c.get("id")
    if not cid:
        raise RuntimeError("入れ物を作れませんでした: %s" % c)
    # 作ってすぐ公開すると失敗することがあるので、少し待つ
    time.sleep(3)
    r = api("POST", "%s/threads_publish" % uid, {"creation_id": cid}, token)
    return r.get("id")


def token_expiry(token):
    """トークンがいつ切れるかを見る。中身は表示しない。"""
    try:
        url = ("https://graph.threads.net/v1.0/me?fields=id&access_token=%s"
               % urllib.parse.quote(token))
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=20) as r:
            json.loads(r.read().decode("utf-8"))
        return True, ""
    except urllib.error.HTTPError as ex:                  # noqa: BLE001
        try:
            body = json.loads(ex.read().decode("utf-8"))
            msg = (body.get("error") or {}).get("message", str(ex))
        except Exception:                                 # noqa: BLE001
            msg = str(ex)
        return False, msg
    except Exception as ex:                               # noqa: BLE001
        return False, str(ex)


def refresh_token():
    """長期トークンを取り直して .env に書き戻す。

    長期トークンは60日で切れる。切れると投稿が止まる。
    取り直したトークンは画面に出さず、.env に直接書く。
    トークンを画面やログに出すと、そこから漏れる。
    """
    uid, token = credentials()
    url = ("https://graph.threads.net/refresh_access_token"
           "?grant_type=th_refresh_token&access_token=%s" % urllib.parse.quote(token))
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
    new = d.get("access_token")
    if not new:
        sys.exit("取り直せませんでした: %s" % d)
    days = int(d.get("expires_in", 0)) // 86400

    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        sys.exit(".env がありません。")
    lines = io_open_lines(path)
    out, done = [], False
    for line in lines:
        if line.strip().startswith("THREADS_ACCESS_TOKEN="):
            out.append("THREADS_ACCESS_TOKEN=%s\n" % new)
            done = True
        else:
            out.append(line)
    if not done:
        out.append("THREADS_ACCESS_TOKEN=%s\n" % new)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)

    print("✅ .env のトークンを取り直しました。あと%d日もちます。" % days)
    print("   自動実行にも使うなら、GitHubのSecretsにも入れ直してください。")
    print("   値は画面に出していません。.env を開いてコピーしてください。")


def io_open_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.readlines()


def setup():
    """トークンを受け取って .env に書く。

    ユーザーIDはトークンから引けるので、人が調べる必要はない。
    トークンは getpass で受け取る。画面にも履歴にも残さない。
    """
    import getpass

    print("Metaの「ユーザートークン生成ツール」で出したトークンを貼り付けてください。")
    print("入力中は画面に表示されません。貼り付けてEnterを押してください。\n")
    token = getpass.getpass("トークン: ").strip()
    if not token:
        sys.exit("何も入力されませんでした。")

    # トークンが誰のものかを確かめる。取り違えるとよそのアカウントに出てしまう。
    url = ("https://graph.threads.net/v1.0/me?fields=id,username&access_token=%s"
           % urllib.parse.quote(token))
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            me = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", "replace")
        sys.exit("トークンが使えませんでした。\n%s" % body[:400])
    except Exception as ex:                               # noqa: BLE001
        sys.exit("確認できませんでした: %s" % ex)

    uid, name = me.get("id"), me.get("username")
    if not uid:
        sys.exit("ユーザーIDを取れませんでした: %s" % me)
    print("\n  アカウント: @%s" % name)
    print("  ユーザーID: %s" % uid)

    ans = input("\nこのアカウントで投稿します。よろしいですか？ [y/N] ").strip().lower()
    if ans != "y":
        print("やめました。.env は変えていません。")
        return

    path = os.path.join(ROOT, ".env")
    lines = io_open_lines(path) if os.path.exists(path) else []
    keep = [l for l in lines
            if not l.strip().startswith(("THREADS_USER_ID=", "THREADS_ACCESS_TOKEN="))]
    if keep and not keep[-1].endswith("\n"):
        keep[-1] += "\n"
    keep.append("THREADS_USER_ID=%s\n" % uid)
    keep.append("THREADS_ACCESS_TOKEN=%s\n" % token)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(keep)

    print("\n✅ .env に書きました。")
    print("   次にこれで確かめてください:  python3 threads.py --check")
    print("   自動実行にも使うなら、GitHubのSecretsにも同じ2つを入れてください。")
    print("   値は画面に出していないので、.env を開いてコピーしてください。")



def report():
    """出した投稿の成績を集める。

    どの型が効いたか、リンクを本文と返信のどちらに置いたほうが良いかを、
    推測ではなく数字で見るためのもの。

    threads_manage_insights の権限が要る。
    無いときは、その旨だけ言って終わる。黙って空の表を出さない。
    """
    uid, token = credentials()
    posted = load(STATE, {}) or {}
    log = [x for x in (posted.get("log") or []) if x.get("id")]
    if not log:
        print("まだ出した記録がありません。")
        return

    rows, missing = [], 0
    for x in log:
        try:
            d = api("GET", "%s/insights" % x["id"],
                    {"metric": "views,likes,replies,reposts,quotes,shares"}, token)
        except Exception as ex:                           # noqa: BLE001
            if "insights" in str(ex).lower() or "permission" in str(ex).lower():
                print("成績を取れませんでした。")
                print("Metaのアプリに threads_manage_insights を足して、")
                print("トークンを取り直してから、もう一度実行してください。")
                print("  1. ユースケース → Threads APIにアクセス → アクセス許可と機能")
                print("  2. threads_manage_insights を「＋追加」")
                print("  3. アクセストークンを生成し直す")
                print("  4. python3 threads.py --setup")
                return
            missing += 1
            continue
        got = {}
        for m in d.get("data", []):
            got[m.get("name")] = (m.get("values") or [{}])[0].get("value", 0)
        # 返信方式で出したときは、リンクを貼るために自分で返信している。
        # それを「反応があった」と数えると、数字が水増しになる。
        if x.get("link") == "reply" and got.get("replies"):
            got["replies"] = max(0, got["replies"] - 1)
        rows.append((x, got))

    if not rows:
        print("成績を取れた投稿がありませんでした。")
        return

    now_dt = datetime.now(JST).replace(tzinfo=None)

    def age_h(x):
        try:
            return (now_dt - datetime.strptime(x["at"], "%Y-%m-%d %H:%M")).total_seconds() / 3600
        except (ValueError, KeyError):
            return 0.0

    print("=== 1件ずつ ===")
    print("  %-22s %-8s %-6s %6s %6s %6s %7s"
          % ("投稿", "型", "リンク", "表示", "いいね", "返信", "経過"))
    for x, g in rows:
        print("  %-22s %-8s %-6s %6s %6s %6s %6.1fh" % (
            x["key"][:22], x.get("kind", "?"), x.get("link", "?"),
            g.get("views", 0), g.get("likes", 0), g.get("replies", 0), age_h(x)))

    def summarize(title, keyfn):
        buckets = {}
        for x, g in rows:
            k = keyfn(x)
            b = buckets.setdefault(k, {"n": 0, "views": 0, "likes": 0,
                                       "replies": 0, "age": 0.0})
            b["n"] += 1
            b["age"] += age_h(x)
            for m in ("views", "likes", "replies"):
                b[m] += g.get(m, 0) or 0
        print("\n=== %s ===" % title)
        print("  %-10s %4s %10s %10s %10s %9s"
              % ("", "件数", "表示/件", "いいね/件", "返信/件", "平均経過"))
        for k, b in sorted(buckets.items(), key=lambda kv: -kv[1]["views"]):
            n = max(1, b["n"])
            print("  %-10s %4d %10.1f %10.1f %10.1f %8.1fh"
                  % (k, b["n"], b["views"] / n, b["likes"] / n,
                     b["replies"] / n, b["age"] / n))

    def slot_of(x):
        """出した時刻を、午前・昼・夕方・夜に振り分ける。

        記録の時刻から出すので、あとから足した項目でも過去の分を数えられる。
        """
        try:
            h = int(str(x.get("at", ""))[11:13])
        except ValueError:
            return "?"
        # 0時台を先に見る。h < 11 から先に判定すると、
        # 深夜0時の投稿が「午前」に入ってしまう。実際に入っていた。
        if h < 5:
            return "深夜"
        if h < 11:
            return "午前"
        if h < 15:
            return "昼"
        if h < 19:
            return "夕方"
        if h < 22:
            return "夜"
        return "夜遅く"

    summarize("型べつ", lambda x: x.get("kind", "?"))
    summarize("リンクの置き方べつ", lambda x: x.get("link", "?"))
    summarize("時間帯べつ", slot_of)

    if missing:
        print("\n（%d件は取れませんでした）" % missing)
    print("\n表示回数は時間とともに増えます。出した直後の投稿は不利に見えるので、")
    print("比べるときは、どちらの型も同じくらい時間が経ってから見てください。")


def main():
    args = sys.argv[1:]
    if "--report" in args:
        report()
        return
    if "--setup" in args:
        setup()
        return
    if "--refresh" in args:
        refresh_token()
        return
    if "--check" in args:
        uid, token = credentials()
        ok, msg = token_expiry(token)
        print("トークン: %s" % ("使えます" if ok else "使えません — %s" % msg))
        if ok:
            used, total = publishing_limit(uid, token)
            print("24時間の投稿数: %s / %s" % (used, total))
        return
    dry = "--dry-run" in args
    cfg = load("config.json")
    want = int(cfg.get("threads", {}).get("postsPerRun", 2))

    if "--limit" in args:
        uid, token = credentials()
        used, total = publishing_limit(uid, token)
        print("24時間の投稿数: %s / %s" % (used, total))
        return

    posted = load(STATE, {"keys": [], "log": []}) or {"keys": [], "log": []}
    picks = pick(cfg, posted, want)
    if not picks:
        print("出せるものがありません。")
        return

    print("▼ %d件を出します%s\n" % (len(picks), "（--dry-run なので出しません）" if dry else ""))
    for key, text, link in picks:
        print("── %s（%d文字）" % (key, len(text)))
        print(text)
        if link:
            print("   ↳ 返信として貼るリンク: %s" % link)
        print()

    if dry:
        return

    uid, token = credentials()
    used, total = publishing_limit(uid, token)
    if used is not None and used + len(picks) > total:
        sys.exit("24時間の上限に近いので止めます（%s/%s）" % (used, total))

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    # どの商品に人の書いた文があったかを控えておく
    feed_now = {("product:" + x["id"]): bool(x.get("cap"))
                for x in (load("assets/data/feed.json", []) or [])}
    caps = feed_now
    ok = 0
    for key, text, link in picks:
        try:
            pid = publish(uid, token, text)
            if link:
                # 本文に入れず、自分の投稿への返信としてリンクを貼る。
                # 続けて叩くと弾かれることがあるので少し待つ。
                time.sleep(5)
                publish(uid, token, link, reply_to=pid)
        except Exception as ex:                           # noqa: BLE001
            print("  × %s の投稿に失敗: %s" % (key, ex), file=sys.stderr)
            if "OAuth" in str(ex) or "190" in str(ex) or "401" in str(ex):
                print("     トークンが切れている可能性があります。"
                      "手元で python3 threads.py --refresh を実行してください。",
                      file=sys.stderr)
                break
            continue
        posted["keys"].append(key)
        kind = ("tip" if key.startswith("tip:") else
                "schedule" if key.startswith("schedule:") else
                "product" if key.startswith("product:") else
                "guide" if key.startswith("guide:") else "page")
        posted["log"].append({
            "key": key, "id": pid, "at": now, "kind": kind,
            # 人が書いた文があったかどうか。
            # データだけの投稿と読み比べたときに、差が出るのかを見るため。
            "cap": caps.get(key, None),
            # あとで比べるために、どの出し方で出したかを残す。
            # 記録が無いと、伸びた理由が本文か返信かを言えなくなる。
            "link": ("none" if kind in ("tip", "schedule")
                     else ("reply" if link else "body")),
        })
        ok += 1
        print("  ✅ %s → %s" % (key, pid))
        # 続けて出すときは間を空ける。まとめて出すとスパムに見える。
        if ok < len(picks):
            time.sleep(30)

    posted["keys"] = posted["keys"][-2000:]
    posted["log"] = posted["log"][-400:]
    save(STATE, posted)
    print("\n%d件を出しました。" % ok)


if __name__ == "__main__":
    main()

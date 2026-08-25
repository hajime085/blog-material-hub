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

def compose_product(p, site, pr, reply_link=False):
    """商品の投稿文。

    Threadsは500文字まで使えるので、Xより中身を入れられる。
    ただし書けるのは手元のデータから事実として言えることだけ。
    使っていない商品の体験談は書かない。作り話になる。

    先頭はキャプション。商品名は楽天の検索語がそのまま入っていて
    読ませる文になっていないので、頭に置かない。
    値段も繰り返さない。同じ数字が3回出ると、読む気が失せる。
    """
    cap = (p.get("cap") or "").strip()
    lines = [pr + cap]

    # 商品名は補足として置く。長いものは切る。
    title = p["t"]
    if len(title) > 46:
        title = title[:46].rstrip("…") + "…"
    lines.append(title)

    # 値段の行は、キャプションが値引きに触れていないときだけ足す。
    if p.get("d") and p.get("lp") and (yen(p["lp"]) not in cap):
        lines.append("%s円 → %s円（%d%%OFF）" % (yen(p["lp"]), yen(p["pr"]), p["d"]))

    # 箇条書きから、値段を言い直しているだけのものを落とす。
    pts = []
    for x in (p.get("pt") or []):
        if yen(p["pr"]) in x and (yen(p["lp"]) in x if p.get("lp") else True):
            continue
        if x.strip() == "%s円" % yen(p["pr"]):
            continue
        pts.append(x)
    if pts:
        lines.append("")
        lines += ["・" + x for x in pts[:3]]

    url = "%s/p/%s/" % (site, p["id"])
    if reply_link:
        # リンクは本文に入れず、あとから自分の投稿への返信として貼る。
        return "\n".join(lines), url
    lines.append("")
    lines.append(url)
    return "\n".join(lines), None


def compose_guide(g, site, pr):
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
    return "\n".join([
        g["title"],
        "",
        g["lead"],
        "",
        "%s%s" % (site, g["path"]),
    ]), None


def compose_page(page, site, pr):
    """セールの案内。理由は compose_guide と同じで、PRは付けない。"""
    return "\n".join([
        page["title"],
        "",
        page["lead"],
        "",
        "%s%s" % (site, page["path"]),
    ]), None


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

# 買いまわりと値段の見分け方の短い知識。
# 中身はすべて自分の記事に書いたことで、確かめた事実だけを置く。
# リンクを貼らないので、読んで終わりでいい投稿になる。
# 全部の投稿に商品リンクが入っていると、宣伝の列にしかならない。
TIPS = [
    ("買いまわりは1商品ではなく、1ショップの合計で数えます。",
     "税込1,000円以上で1カウント。\n"
     "500円のものを2つ同じ店で買えば、それで1カウントになります。"),
    ("送料は買いまわりの1,000円判定に入りません。",
     "商品900円＋送料300円で1,200円払っても、カウントされません。\n"
     "商品代だけで税込1,000円を超える必要があります。"),
    ("1,000円＋送料590円より、1,200円の送料無料のほうが安いです。",
     "どちらも1カウント。払う額は1,590円と1,200円。\n"
     "「1,000円ポッキリ」を狙うときほど、送料欄を見てください。"),
    ("同じ店で3回買っても、カウントは1のままです。",
     "だから同じ店のものはまとめて買ったほうが得です。送料が1回で済みます。"),
    ("買いまわりは、買う順番に関係ありません。",
     "先に買った分にも、最終的に達成した店舗数の倍率がかかります。\n"
     "「高いものを最後に」という必要はありません。"),
    ("「実質1,800円」はレジで払う額ではありません。",
     "ポイント還元を引いた計算上の値段です。\n"
     "今日払うのは値引き前の金額で、ポイントは後から付きます。"),
    ("「クーポンで1,000円」は、クーポンを取っていない人には関係ない値段です。",
     "嘘ではありませんが、そのままでは買えません。\n"
     "単価の手前に「クーポンで」「エントリーで」が付いていないか見てください。"),
    ("「3,880円→890円」の右側が、実際の価格と違うことがあります。",
     "色やサイズで値段が変わる商品だと、一番安い組み合わせの値段が書かれがちです。\n"
     "矢印の右と、表示価格が一致しているかを見てください。"),
    ("送料無料には種類があります。",
     "完全に無料、一部地域除外、条件つき、価格に込み。\n"
     "北海道・沖縄・離島にお住まいなら、送料欄を必ず開いてください。"),
    ("ポイントには獲得上限があります。",
     "上限に達したら、店舗を増やしてもポイントは増えません。\n"
     "上限はその回のキャンペーンページに書いてあります。"),
    ("買いまわりで付くのは期間限定ポイントです。",
     "有効期限が短く、使い道も楽天の中に限られます。\n"
     "現金と同じものとして計算に入れると、あとで困ります。"),
    ("「10店舗達成」を目標にしないほうがいいです。",
     "あと1店舗で増えるポイントは、買う予定の合計金額の1%ほど。\n"
     "合計3万円なら約300ポイントです。1,000円の要らないものを足すと損になります。"),
    ("割引率が書いてあるのに、いくらなのか書いていない商品があります。",
     "「77%OFF【990円〜1,390円】」のような書き方です。\n"
     "値段が幅で書かれているとき、その割引率はどれか1つのものです。"),
    ("販売開始前の商品は、ページが見えていても買えません。",
     "確実なのは、カートに入れてみることです。入らなければまだ売っていません。"),
    ("5と0のつく日は、エントリーと楽天カードでの支払いが条件です。",
     "どちらも忘れると、同じ買い物でも戻る量が変わります。"),
]


def tip_posts():
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


def compose_plain(x):
    """リンクなしの投稿。読んで終わりでいい。"""
    return x["title"] + "\n\n" + x["lead"], None


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
    linked = []
    for e in event_posts(site, ev, n_km):
        if e["key"] not in done:
            linked.append((e["key"], compose_page(e, site, pr)))
    for g in guide_posts(site):
        if g["key"] not in done:
            linked.append((g["key"], compose_guide(g, site, pr)))
    items = [p for p in feed
             if p.get("cap") and ("product:" + p["id"]) not in done]
    items.sort(key=lambda p: p.get("at") or "", reverse=True)
    reply_link = placement == "reply"
    for p in items:
        linked.append(("product:" + p["id"],
                       compose_product(p, site, pr, reply_link)))

    # ---- リンクのない投稿 ----
    plain = []
    for x in schedule_post():
        if x["key"] not in done:
            plain.append((x["key"], compose_plain(x)))
    fresh = [t for t in tip_posts() if t["key"] not in done]
    if not fresh:
        # 出し切ったので、いちばん前に出したものから回す
        order = {k: i for i, k in enumerate(posted.get("keys") or [])}
        fresh = sorted(tip_posts(), key=lambda t: order.get(t["key"], 0))
    for t in fresh:
        plain.append((t["key"], compose_plain(t)))

    # ---- どちらを先に取るか ----
    #
    # 夜は商品を出す。通販は夜のほうが買われるので、
    # 買う気のある時間帯に、買えるものを置く。
    # 楽天のセールが20時開始なのも同じ理由。
    #
    # 昼は知識や予定を出す。買う時間ではないので、
    # そこに商品を並べても流されるだけになる。
    # 読んで役に立つものを置いて、覚えてもらうほうに使う。
    #
    # 直前と同じ側が続いたときは入れ替える。
    # 夜だからと商品ばかり続けると、宣伝の列になる。
    hour = datetime.now(JST).hour
    night = hour >= 19 or hour < 2
    want_plain = not night

    last = (posted.get("log") or [])
    if last:
        prev_plain = str(last[-1].get("key", "")).startswith(("tip:", "schedule:"))
        if prev_plain == want_plain:
            want_plain = not want_plain

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
        rows.append((x, got))

    if not rows:
        print("成績を取れた投稿がありませんでした。")
        return

    print("=== 1件ずつ ===")
    print("  %-22s %-8s %-6s %6s %6s %6s" % ("投稿", "型", "リンク", "表示", "いいね", "返信"))
    for x, g in rows:
        print("  %-22s %-8s %-6s %6s %6s %6s" % (
            x["key"][:22], x.get("kind", "?"), x.get("link", "?"),
            g.get("views", 0), g.get("likes", 0), g.get("replies", 0)))

    def summarize(title, keyfn):
        buckets = {}
        for x, g in rows:
            k = keyfn(x)
            b = buckets.setdefault(k, {"n": 0, "views": 0, "likes": 0, "replies": 0})
            b["n"] += 1
            for m in ("views", "likes", "replies"):
                b[m] += g.get(m, 0) or 0
        print("\n=== %s ===" % title)
        print("  %-10s %4s %10s %10s %10s" % ("", "件数", "表示/件", "いいね/件", "返信/件"))
        for k, b in sorted(buckets.items(), key=lambda kv: -kv[1]["views"]):
            n = max(1, b["n"])
            print("  %-10s %4d %10.1f %10.1f %10.1f"
                  % (k, b["n"], b["views"] / n, b["likes"] / n, b["replies"] / n))

    def slot_of(x):
        """出した時刻を、午前・昼・夕方・夜に振り分ける。

        記録の時刻から出すので、あとから足した項目でも過去の分を数えられる。
        """
        try:
            h = int(str(x.get("at", ""))[11:13])
        except ValueError:
            return "?"
        if h < 11:
            return "午前"
        if h < 15:
            return "昼"
        if h < 19:
            return "夕方"
        if h < 22:
            return "夜"
        if h < 24:
            return "夜遅く"
        return "深夜"

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

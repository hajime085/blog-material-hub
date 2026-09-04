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


def recent_posts(uid, token, n=25):
    """アカウントに実際に載っている、直近の投稿。

    手元の記録ではなくアカウントを見る。記録は押せないことがある。
    8/29 は 14:00 に出した記録が push できず、次の回が
    「まだ出していない」と読んで同じ知識投稿をもう一度出した。
    記録が欠けても、アカウントを見ていれば気づける。
    """
    try:
        d = api("GET", "%s/threads" % uid,
                {"fields": "id,timestamp,text", "limit": n}, token)
    except Exception:                                         # noqa: BLE001
        return None
    return d.get("data") or []


def head_of(text):
    """見比べ用に、投稿の頭のほうをならして取り出す。

    1行目だけで比べると、毎回同じ見出しを使う投稿を
    「重複」と誤って弾く。「楽天のイベント予定」がそれで、
    中身は毎回違うのに1行目は必ず同じになる。
    改行をつめた先頭100字で比べる。ここまで一致していれば、
    同じ知識・同じ商品と見てよい。
    """
    t = (text or "").replace("【PR】", "")
    t = " ".join(t.split())          # 改行と連続する空白をつめる
    return t[:100]


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


# 締めの一言。付けるかどうかは投稿ごとに回す。
# 「使ったことある人いますか」は、こちらが使ったとは言っていないので嘘にならない。
# 締めの型。pitch.py の CTA_TYPES と対応している。
CTA_TEXT = {
    "experience": "これ、使ってる人います？",
    "compare":    "もっといいのあったら教えてください。",
    "empathy":    "同じこと思ってる人、いません？",
    "save":       "保存しておくと、あとで見返せます。",
    "none":       "",
}

TAILS = [
    "",
    "\n\nこれ、使ってる人います？",
    "\n\n保存しておくと、あとで見返せます。",
    "",
    "\n\nもっといいのあったら教えてください。",
    "",
]


# セールの間だけ、商品リンクを楽天へ直接つなぐ。
#
# 2026-09-04: サイトの実訪問は1日9アクセスしかなかった。
# 「投稿 → 返信のリンク → サイトの商品ページ → 楽天ボタン」の4段で、
# 楽天まで届いたのは1日1件。サイトを1枚はさむ意味が薄い。
# セール中は鮮度がすべてなので、間を抜いて直接つなぐ。
#
# 期間が過ぎたら自動でサイト経由に戻る。
# 手で戻す作りにすると、戻し忘れてそのままになる。
SALE_UNTIL = "2026-09-11 01:59"


def link_for(p, site):
    """返信に貼るリンク。セール中は楽天へ直接、それ以外はサイトの商品ページへ。"""
    url = (p.get("url") or "").strip()
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    if now <= SALE_UNTIL and url.startswith("https://hb.afl.rakuten.co.jp/"):
        # 広告であることは親の投稿の【PR】でも示しているが、
        # リンクだけを見た人にも分かるように、返信にも書く。
        return "※PR\n" + url
    return "%s/p/%s/" % (site, p["id"])


def compose_product(p, site, pr, reply_link=False, ev=None, seq=0):
    """商品の投稿文。

    キャプションがあればそれを先頭に置く。人が書いた文なので、いちばん読ませる。
    無ければ、手元のデータだけで組み立てる。
    キャプションを待っていると、今朝見つけた79%OFFを流せるのが明日になる。
    セールの訴求は鮮度がすべてなので、待たないほうを選ぶ。

    形は投稿ごとに変える。1行目だけ入れ替えても、
    組み立てが同じなら同じ投稿に見える。
    """
    mk = p.get("mk2") or None
    if mk and mk.get("hook"):
        # 事前に作って、決まりに照らして通した商品理解を使う。
        # 何を先に出すか、誰に向けて書くかは、ここで決めてある。
        # 投稿するときに考えると毎回ぶれるし、確かめる機会もない。
        lines = [pr + mk["hook"]]
        if mk.get("body"):
            lines += ["", mk["body"]]

        # 判断材料は最後に置く。値段から入ると売り込みの形になる。
        facts = []
        if p.get("d") and p.get("lp"):
            facts.append("%s円 → %s円（%d%%OFF）" % (yen(p["lp"]), yen(p["pr"]), p["d"]))
        else:
            facts.append("%s円" % yen(p["pr"]))
        facts.append("送料込み" if "送料無料" in (p.get("tags") or [])
                     else "送料は別")
        tail = "。".join(facts) + "。"
        rc = p.get("rc") or 0
        if rc >= 100:
            # 件数のまま書く。買った人数に言い換えない。
            tail += "\nレビュー%s件で、星%s。" % (yen(rc), p.get("ra", ""))
        ends = ends_label(p.get("et"))
        if ends:
            tail += "\n%sまで。" % ends.replace(" まで", "")
        lines += ["", tail]

        # 締めは商品に合わせて選んだものを使う。
        # 決めていなければ、これまでどおり順に回す。
        cta = mk.get("cta")
        if cta == "question":
            tailtext = "\n\n" + (mk.get("cta_text") or "").strip()
        elif cta in CTA_TEXT:
            t2 = CTA_TEXT[cta]
            tailtext = ("\n\n" + t2) if t2 else ""
        else:
            tailtext = TAILS[seq % len(TAILS)]

        body = "\n".join(lines) + tailtext
        url = link_for(p, site)
        if reply_link:
            return body, url
        return body + "\n\n" + url, None

    # 商品理解の無い商品は、pick() が候補から外している。
    # ここへは来ない。来たら、それは決まりが壊れている合図なので、
    # 黙って古い形で出さずに、出さないことを選ぶ。
    #
    # 以前はここに「キャプション＋商品名」と「値札型など7種」という
    # 古い道が残っていた。使わないのに残していたので、
    # pick() の条件が外れた拍子にそこへ落ち、
    # 楽天の商品名をそのまま貼った投稿が出た（2026-08-31）。
    # 使わなくなった道は残さない。残せば、いつか落ちる。
    return None, None


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
         "title": "その「◯%OFF」、本当ですか。",
         "lead": "安く見えて安くない商品には、はっきりした型があります。\n"
                 "毎日800件ほど見て弾いていると、5つに絞れました。\n\n"
                 "だいたい、商品名を読むだけで見抜けます。\n\n"
                 "引っかかったこと、ないですか？",
         "path": "/guide/price-trick/"},
        {"key": "guide:marathon",
         "title": "10店舗、目指さなくていいです。",
         "lead": "買いまわりは1ショップ税込1,000円以上で1カウント。\n"
                 "でも「あと1店舗」に意味があるかは、その人の総額しだい。\n\n"
                 "足したせいで損することも、普通にあります。\n\n"
                 "計算しないで足してませんか？",
         "path": "/guide/rakuten-marathon/"},
        {"key": "guide:super-sale",
         "title": "スーパーSALE、行き当たりばったりで買ってません？",
         "lead": "買いまわりの条件、クーポンの併用、ポイントの上限。\n"
                 "つまずくところは、だいたい決まっています。\n\n"
                 "そのまま真似できる手順にしました。",
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


def schedule_recent(posted, days=3):
    """最近この型を出したか。予定の投稿が続けざまに並ぶのを避ける。"""
    now = datetime.now(JST).replace(tzinfo=None)
    for x in reversed(posted.get("log") or []):
        if not str(x.get("key", "")).startswith("schedule:"):
            continue
        try:
            t = datetime.strptime(x["at"], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        return (now - t).total_seconds() < days * 86400
    return False


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
    guess = False
    for a, b, ev in rows[:3]:
        mark = ""
        if ev.get("status") != "確定":
            mark = "（予想）"
            guess = True
        state = ("開催中" if a <= now <= b
                 else "あと%d日" % max(0, (a.date() - now.date()).days))
        lines.append("・%s%s\n  %s 〜 %s（%s）" % (ev["name"], mark, fmt(a), fmt(b), state))

    # エントリーが先に始まるイベントは、始まるのを待つ必要がない。
    # ここを言わないと「まだ先の話」に見えて、当日まで何もしないことになる。
    entry = None
    for a, b, ev in rows[:3]:
        try:
            es = datetime.strptime(ev["entryStart"][:16], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError, TypeError):
            continue
        if es <= now < a:
            entry = ev
            break

    tail = []
    if entry:
        tail.append("%sのエントリーは、もう始まっています。" % entry["name"])
        cap = entry.get("pointCap")
        if cap:
            tail.append("押すだけ、無料、10秒。")
            tail.append("ちなみに買いまわりの上限は%s。" % ("%s,%03dポイント" % (cap // 1000, cap % 1000)
                                                if cap >= 1000 else "%dポイント" % cap))
        tail.append("")
        tail.append("開始してから慌てる人、多いんですよね。")
    if guess:
        tail.append("予想と書いたものは、まだ楽天が発表していない日程です。"
                    "過去の傾向からの見込みなので、変わることがあります。")

    # 日付ごとに鍵を作ると、日をまたいだ直後にほぼ同じ内容がもう一度出る。
    # 予定はそう頻繁に変わらないので、間隔を空ける。
    key = "schedule:%s" % now.strftime("%Y-W%W-%w")
    body = "\n".join(lines)
    if tail:
        body += "\n\n" + "\n".join(tail)
    return [{"key": key, "title": "楽天のイベント予定", "lead": body}]


# 返信で案内するときの一言。行き先ごとに変える。
# どこへ送るのかと噛み合っていないと、押した人が肩透かしを食う。
# 同じ文が毎回続くと機械が貼っているのが見えるので、数を用意しておく。
FOLLOW = {
    "/guide/rakuten-marathon/": [
        "同じ取りこぼしをしないように、数え方をまとめました。",
        "買いまわりの数え方、最初から書いてあります。",
        "「あと1店舗」が得かどうかの計算も、記事に置いてます。",
        "買ってから後悔しないように、条件は先に確かめておきましょう。",
        "こういう見落としやすいところ、ヤスミルでまとめています。",
        "ポイントの上限と期限の話も、あわせて書きました。",
        "同じところでつまずかないように、手順にしてあります。",
    ],
    # 分類ごとに分ける。同じ記事へ送る話でも、
    # 送料の話に「商品名を読むだけで見抜ける」と返すのは噛み合わない。
    "shipping": [
        "送料の見落とし、記事のほうにまとめてあります。",
        "あとで損しないように、合計で比べる癖をつけておきましょう。",
        "ヤスミルは送料も楽天のデータを見て、別なら別と書いてます。",
        "「送料無料」の4つの型、記事で全部ばらしました。",
    ],
    "choose": [
        "選び方、まとめてあります。",
        "買う前に確かめる7つ、チェックリストにしました。",
        "ヤスミルはレビューの数と評価を条件にして選んでます。",
    ],
    "/guide/price-trick/": [
        "同じ見せ方を5つの型に分けて、記事にまとめました。",
        "ほかの落とし穴も、実例つきで置いてます。",
        "買う前に確かめる7つ、チェックリストにしました。",
        "同じ思いをしないように、先に見ておくと安全です。",
        "ヤスミルは毎日800件ほど見て、こういう表記を弾いてます。",
        "商品名を読むだけで見抜けるもの、けっこう多いです。",
        "「安く見えて安くない」の型、まとめて解説しました。",
    ],
    "/guide/rakuten-super-sale/": [
        "セールの買い方、そのまま真似できる手順にしました。",
        "買いまわりの条件もクーポンの併用も、書いてあります。",
        "次のセールで迷わないように、先に読んでおくと楽です。",
        "こういう仕組みの話も、ヤスミルでまとめています。",
    ],
    "/kaimawari/": [
        "条件に合う商品、集めてあります。",
        "1,000円以上・送料無料のものだけ並べてます。",
        "別々の店から1つずつ選ぶ組み方も出してます。",
        "探す手間が省けるように、こっちでまとめました。",
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

def pick(cfg, posted, want, slot_hour=None, live_heads=None):
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
        # 商品理解のあるものだけを出す。
        #
        # 以前は、商品理解が無ければキャプションと商品名を並べる
        # 古い型に落としていた。だが楽天の商品名は検索語の羅列なので、
        # 貼るとそこで文章が途切れたようにしか見えない。
        # 「誰に・どんな悩みに・どの特徴が効くか」を先に考える、という
        # 作り直しの趣旨そのものからも外れている。
        # 出せるものが無い枠は、知識や記事で埋める。そちらのほうがましだ。
        if not p.get("mk2"):
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
    # 並べ方。
    #
    # ふだんは新しい順。値段は日々動くので、古い値引きを
    # 今見つけたかのように流さないため。
    #
    # セールの間はそうしない。セール中は「安い順に見たい」人が来る。
    # 新しい順に出すと、たまたま今朝拾った20%OFFが先に出て、
    # 昨日拾った70%OFFが後ろに回る。目玉が埋もれる。
    # 2026-09-04: 利用者から「通常の投稿じゃない。とっておきを出せ」と指摘。
    if datetime.now(JST).strftime("%Y-%m-%d %H:%M") <= SALE_UNTIL:
        items.sort(key=lambda p: (p.get("d") or 0, p.get("at") or ""), reverse=True)
    else:
        items.sort(key=lambda p: p.get("at") or "", reverse=True)

    # 売場を回す。
    #
    # 新しい順に取るだけだと、そのとき多く見つかった売場に偏る。
    # いま出せる56件のうち30件がサプリなので、放っておくと
    # オルニチン、マカ、すっぽん黒酢…と続く。
    # 流れてくる人の大半はサプリを探していないので、
    # どれだけ安くても、どれだけ文章を練っても押されない。
    #
    # 直近3件と違う売場のものを先に出す。
    # 同じものが続くと、読む人にとっては同じ投稿が並んでいるのと変わらない。
    recent_cats = []
    for k in reversed(posted.get("keys") or []):
        if not k.startswith("product:"):
            continue
        hit = next((q for q in feed if q["id"] == k[8:]), None)
        if hit:
            recent_cats.append(hit.get("c"))
        if len(recent_cats) >= 3:
            break
    if recent_cats:
        fresh_cat = [q for q in items if q.get("c") not in recent_cats]
        if fresh_cat:
            items = fresh_cat + [q for q in items if q not in fresh_cat]

    n_done = len([k for k in (posted.get("keys") or []) if k.startswith("product:")])
    for n, p in enumerate(items):
        linked.append(("product:" + p["id"],
                       compose_product(p, site, pr, reply_link, ev, n_done + n)))

    # ---- 商品以外 ----
    # 記事・セールの案内・知識・予定をまとめて扱う。
    # 読む人から見れば、どれも「いま買うもの」ではない。
    plain = list(other)
    for x in (schedule_post() if not schedule_recent(posted) else []):
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
    # 時刻ごとに、商品を出す枠か、それ以外を出す枠かを決めてある。
    # 動的に決めていたころは、足りない日に商品が偏って比率が揺れた。
    # 枠で固定すれば、1日の内訳が毎日同じになる。
    # 検証のあいだは、この安定のほうが要る。
    #
    # 夜8時以降を厚くしているのは、通販がその時間に動くから。
    # 楽天のセールが20時開始なのも同じ理由。
    # 落ちた枠をあとから埋めるときは、いまの時刻ではなく
    # 「本来その投稿が出るはずだった時刻」の枠に従う。
    # そうしないと、朝の枠を昼に埋めた瞬間に中身が昼のものに変わる。
    hour = datetime.now(JST).hour if slot_hour is None else int(slot_hour)
    slots = th.get("slots") or {}
    kind = slots.get(str(hour))
    if kind is None:
        # 枠から外れた時刻に走ったとき（手で実行した場合など）は、
        # いちばん近い枠の指定に従う。
        if slots:
            near = min(slots, key=lambda k: min(abs(int(k) - hour),
                                                24 - abs(int(k) - hour)))
            kind = slots[near]
        else:
            kind = "product"
    want_plain = (kind == "plain")

    # すでにアカウントに載っているものは、候補の段階で外す。
    #
    # 記録は押せないことがある。押せなければ「まだ出していない」と読んで
    # 同じものをまた選ぶ。出す直前に止めるだけだと、その枠は空振りになる。
    # 候補から外しておけば、次の候補が出る。枠を落とさない。
    if live_heads:
        def fresh(pair):
            _k, (body, _l) = pair
            return head_of(body) not in live_heads
        before = len(linked) + len(plain)
        linked = [x for x in linked if fresh(x)]
        plain = [x for x in plain if fresh(x)]
        gone = before - len(linked) - len(plain)
        if gone:
            print("すでに載っているものを%d件、候補から外しました。" % gone)

    # 出すものが無い枠は、もう片方で埋める。投稿を落とさない。
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
        # 落ちた枠をあとから埋めたものは印を付ける。
        # 予定と違う時刻に出ているので、時間帯の比較には使えない。
        mark = "*" if x.get("catchup") else ""
        print("  %-22s %-8s %-6s %6s %6s %6s %6.1fh %s" % (
            x["key"][:22], x.get("kind", "?"), x.get("link", "?"),
            g.get("views", 0), g.get("likes", 0), g.get("replies", 0),
            age_h(x), mark))
    if any(x.get("catchup") for x, _ in rows):
        print("  * 落ちた枠を遅れて埋めたもの。時間帯べつの比較からは外しています。")

    def median(xs):
        """中央値。表示回数はたまに大きく跳ねるので、平均だけだと1件に引きずられる。"""
        if not xs:
            return 0.0
        xs = sorted(xs)
        n = len(xs)
        return float(xs[n // 2]) if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0

    def summarize(title, keyfn, only_product=False):
        buckets = {}
        for x, g in rows:
            if only_product and x.get("kind") != "product":
                continue
            k = keyfn(x)
            if k is None:
                continue
            b = buckets.setdefault(k, {"views": [], "likes": [], "replies": [],
                                       "age": 0.0})
            b["views"].append(g.get("views", 0) or 0)
            b["likes"].append(g.get("likes", 0) or 0)
            b["replies"].append(g.get("replies", 0) or 0)
            b["age"] += age_h(x)
        if not buckets:
            return
        print("\n=== %s ===" % title)
        print("  %-14s %4s %9s %9s %8s %8s %8s"
              % ("", "件数", "表示 平均", "表示 中央", "いいね", "返信", "平均経過"))
        for k, b in sorted(buckets.items(),
                           key=lambda kv: -median(kv[1]["views"])):
            n = len(b["views"])
            print("  %-14s %4d %9.1f %9.1f %8.1f %8.1f %7.1fh"
                  % (str(k), n,
                     sum(b["views"]) / n, median(b["views"]),
                     sum(b["likes"]) / n, sum(b["replies"]) / n,
                     b["age"] / n))

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
    # 遅れて埋めたものは、予定と違う時刻に出ている。時間帯の比較から外す。
    _all = rows
    rows = [(x, g) for x, g in rows if not x.get("catchup")]
    summarize("時間帯べつ", slot_of)
    rows = _all

    # ここから下は商品の投稿だけ。知識や予定と混ぜると比べられない。
    summarize("組み立てべつ（旧 vs 新）",
              lambda x: x.get("logic_version"), only_product=True)
    summarize("入口の型べつ",
              lambda x: x.get("hook_type") or "（型なし）", only_product=True)
    summarize("締めべつ",
              lambda x: x.get("cta_type") or "（回しているもの）", only_product=True)
    summarize("売場べつ",
              lambda x: x.get("category"), only_product=True)

    if missing:
        print("\n（%d件は取れませんでした）" % missing)
    print("\n表示回数は時間とともに増えます。出した直後の投稿は不利に見えるので、")
    print("比べるときは、どちらの型も同じくらい時間が経ってから見てください。")



def run_once(cfg, posted, slot_hour=None, dry=False, late=False):
    """1件出す。slot_hour を渡すと、その枠の決まりで中身を選ぶ。

    (出した数, 理由) を返す。理由は ok / empty / blocked。
    「出すものが無い」と「出せなかった」を区別しないと、
    上限に当たっただけの枠まで消化済みにしてしまう。
    """
    want = int(cfg.get("threads", {}).get("postsPerRun", 2))

    # 候補を作る前にアカウントを見る。記録が欠けていても、
    # 実際に載っているものは候補から外せる。
    live = None
    heads = None
    if not dry:
        try:
            uid0, tok0 = credentials()
            live = recent_posts(uid0, tok0)
            if live:
                heads = {head_of(x.get("text")) for x in live}
        except SystemExit:
            pass
    picks = pick(cfg, posted, want, slot_hour, heads)
    if not picks:
        print("出せるものがありません。")
        return 0, "empty"

    print("▼ %d件を出します%s\n" % (len(picks), "（--dry-run なので出しません）" if dry else ""))
    for key, text, link in picks:
        print("── %s（%d文字）" % (key, len(text)))
        print(text)
        if link:
            print("   ↳ 返信として貼るリンク: %s" % link)
        print()

    if dry:
        return 0, "dry"

    uid, token = credentials()

    # 直前に誰かが出していないか。別の機械から重なって走っていた場合、
    # ここでしか気づけない。
    gap = int((cfg.get("threads") or {}).get("minGapMin", 10))
    if live is None:
        live = recent_posts(uid, token)
    if live:
        t0 = live[0].get("timestamp") or ""
        try:
            last = (datetime.strptime(t0[:19], "%Y-%m-%dT%H:%M:%S")
                    + timedelta(hours=9))
            since = (datetime.now(JST).replace(tzinfo=None)
                     - last).total_seconds() / 60
        except ValueError:
            since = None
        if since is not None and since < gap:
            print("%.0f分前に投稿があります（%d分は空けます）。今回は出しません。"
                  % (since, gap), file=sys.stderr)
            print("別の場所からも動いている可能性があります。", file=sys.stderr)
            return 0, "blocked"

        # 時間が空いていても、同じ中身なら出さない。
        # 間隔だけ見ていた版は、21分空いた重複を通してしまった。
        heads = {head_of(x.get("text")) for x in live}
        for key, text, _link in picks:
            h = head_of(text)
            if h and h in heads:
                print("同じ内容がすでに載っています。今回は出しません。",
                      file=sys.stderr)
                print("  %s" % h, file=sys.stderr)
                return 0, "blocked"

    used, total = publishing_limit(uid, token)
    if used is not None and used + len(picks) > total:
        print("24時間の上限に近いので止めます（%s/%s）" % (used, total),
              file=sys.stderr)
        return 0, "blocked"

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    # どの商品に人の書いた文があったかを控えておく
    _feed = load("assets/data/feed.json", []) or []
    caps = {("product:" + x["id"]): bool(x.get("cap")) for x in _feed}
    hooks = {("product:" + x["id"]): (x.get("mk2") or {}).get("hook_type")
             for x in _feed}
    ctas = {("product:" + x["id"]): (x.get("mk2") or {}).get("cta")
            for x in _feed}
    cats = {("product:" + x["id"]): x.get("c") for x in _feed}
    # どちらの組み立てで出したか。あとで旧と新を比べるため。
    # 商品理解を持っているものだけが新しいほうを通る。
    vers = {("product:" + x["id"]):
            ("marketing_v2" if x.get("mk2") else "legacy_v1") for x in _feed}
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
            # どの枠のぶんか。落ちた枠をあとから埋めたときも、
            # 本来の枠が分かるようにしておく。
            "slot": slot_hour,
            # 予定より1時間以上あとに出したものは、時間帯の比較から外せるようにする。
            "catchup": late,
            # 人が書いた文があったかどうか。
            # データだけの投稿と読み比べたときに、差が出るのかを見るため。
            "cap": caps.get(key, None),
            # どの入口の型で出したか。あとでどれが伸びたかを見るため。
            "hook_type": hooks.get(key),
            # どの締めで出したか。反応の差を見るため。
            "cta_type": ctas.get(key),
            # 売場。売場によって伸び方が違うかもしれない。
            "category": cats.get(key),
            # 旧い組み立てか、新しい組み立てか。
            "logic_version": vers.get(key,
                                      "legacy_v1" if kind == "product" else None),
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
    return ok, ("ok" if ok else "blocked")


# ------------------------------------------------- 落ちた枠を埋めながら走る
#
# GitHub の予定実行は、時刻どおりには動かない。
# 8/21〜8/28 の実行記録40回を数えたところ、
# 定刻に動いた回は 0回、遅れは平均48分・最大77分だった。
# さらに 1日の予定回数を増やすと、実行そのものが配信されなくなる。
#
#     5〜7回/日 … 配信率 100%（6日間）
#     11回/日  … 91%
#     14回/日  … 21%
#     17回/日  … 0%
#
# だから「予定した回数だけ起動する」ことに頼らない作りにする。
# 起動回数を1日4回まで減らし、そのかわり1回の起動が長く生き、
# その日の落ちた枠を見つけて埋める。
# 4回のうち1回でも動けば、その日の投稿は最後まで出る。


def slot_hours(cfg):
    return sorted(int(h) for h in ((cfg.get("threads") or {}).get("slots") or {}))


def done_slots(posted, day):
    """その日、すでに出した枠。"""
    ds = day.strftime("%Y-%m-%d")
    out = set()
    for x in posted.get("log") or []:
        at = x.get("at") or ""
        if at[:10] != ds:
            continue
        s = x.get("slot")
        if s is None:
            # 枠を記録する前の古い記録。出した時刻をその枠とみなす。
            try:
                s = int(at[11:13])
            except ValueError:
                continue
        out.add(int(s))
    return out


def next_action(hours, done, t, until, last, gap_min, max_late_h=3):
    """いま何をすべきか決める。時計に触らないので、机上で確かめられる。

    ("stop",) / ("post", 枠) / ("wait", 秒) のどれかを返す。
    """
    if t >= until:
        return ("stop",)
    # 遅れすぎた枠は捨てる。何時間も前の枠を夜にまとめて出すと、
    # 時間帯ごとの反応を比べる材料にならないうえ、まとめ出しに見える。
    due = []
    for h in hours:
        s = t.replace(hour=h, minute=0, second=0, microsecond=0)
        if s <= t and h not in done:
            if (t - s).total_seconds() <= max_late_h * 3600:
                due.append(h)
    if due:
        if last is None or (t - last).total_seconds() >= gap_min * 60:
            return ("post", due[0])
        nxt = min(until, last + timedelta(minutes=gap_min))
    else:
        nxt = until
        for h in hours:
            s = t.replace(hour=h, minute=0, second=0, microsecond=0)
            if s > t and h not in done:
                nxt = min(nxt, s)
                break
    return ("wait", max(60, min((nxt - t).total_seconds(), 300)))


def serve(cfg, until_s=None, window_h=4.0, gap_min=25, max_late_h=3,
          dry=False, push=False):
    """終わりの時刻まで生き続け、来た枠を出し、落ちた枠を埋める。"""
    hours = slot_hours(cfg)
    if not hours:
        print("枠が決まっていません。")
        return

    def now():
        return datetime.now(JST).replace(tzinfo=None)

    if until_s:
        hh, mm = (int(x) for x in until_s.split(":"))
        until = now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        if until <= now():
            until += timedelta(days=1)
    else:
        # 起動そのものが平均48分遅れるので、終わりは時刻で決めず、
        # 「動き出してから何時間」で決める。そうすれば遅れて始まっても
        # 受け持ちの枠を最後まで見届けられる。
        until = now() + timedelta(hours=window_h)

    print("いまは %s、%s まで見張ります。枠: %s"
          % (now().strftime("%H:%M"), until.strftime("%m/%d %H:%M"),
             "、".join("%d時" % h for h in hours)))

    last = None
    made = 0
    while True:
        t = now()
        posted = load(STATE, {"keys": [], "log": []}) or {"keys": [], "log": []}
        done = done_slots(posted, t.date())
        act = next_action(hours, done, t, until, last, gap_min,
                           max_late_h)

        if act[0] == "stop":
            print("%s になりました。終わります（%d件）。"
                  % (t.strftime("%H:%M"), made))
            return
        if act[0] == "wait":
            if dry:
                print("待ちに入るところなので、--dry-run はここで終わります。")
                return
            time.sleep(act[1])
            continue

        h = act[1]
        late = (t - t.replace(hour=h, minute=0)).total_seconds() > 3600
        print("\n── %d時の枠を出します（いま %s%s）"
              % (h, t.strftime("%H:%M"), "、遅れて埋めます" if late else ""))
        try:
            n, why = run_once(cfg, posted, slot_hour=h, dry=dry, late=late)
        except Exception as ex:                               # noqa: BLE001
            # ここで落ちると、その日の残りの枠が全部消える。
            # 1枠ぶんの失敗で1日を落とさない。
            print("  × %d時の枠で例外: %s" % (h, ex), file=sys.stderr)
            n, why = 0, "blocked"
        if dry:
            print("（--dry-run なので記録は残しません）")
            return
        if n:
            made += n
            last = t
            if push and not push_state():
                # 記録を送れないまま出し続けると、次の回が同じものを出す。
                # 8/29 の重複はこれで起きた。押せなかったら、そこで止める。
                print("記録を送れませんでした。ここで止めます。"
                      "出したぶんは載っていますが、記録はこの回に残りません。",
                      file=sys.stderr)
                return
        elif why == "blocked":
            # 上限やトークンで出せなかっただけ。枠は使っていない。
            # ここを消化済みにすると、直ったあとも二度と出せなくなる。
            print("  出せなかったので、この枠は残したまま少し待ちます。")
            last = t
        else:
            # 出すものが無い枠で止まると、そこから先に進めなくなる。
            # 出せなかったことを記録して次の枠へ送る。
            posted.setdefault("log", []).append({
                "key": "skip:%d" % h, "id": None,
                "at": t.strftime("%Y-%m-%d %H:%M"), "kind": "skip",
                "slot": h, "catchup": False, "link": "none",
            })
            save(STATE, posted)


LOCK = ".threads.lock"


def take_lock(what):
    """二重に走らせない。

    これまで、手元の見張り・GitHubの予定実行・手で叩いた素の実行が
    同時に走れる状態だった。どれも threads_posted.json を
    「読む→足す→書く」ので、重なると片方の記録が消える。
    記録が消えると、次の起動が同じものをもう一度出す。

    実際、8/28 17:02 に出た1件は素の実行から出たもので、
    記録は残ったが push されず、19時のコミットに紛れて入った。
    どこから出たのかを、あとから説明できない状態になっていた。
    """
    path = os.path.join(ROOT, LOCK)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:                                     # noqa: BLE001
            old = {}
        pid = old.get("pid")
        alive = False
        if pid:
            try:
                os.kill(int(pid), 0)
                alive = True
            except (OSError, ValueError):
                alive = False
        if alive:
            print("すでに走っています（%s / PID %s、%s に開始）。"
                  % (old.get("what", "?"), pid, old.get("at", "?")),
                  file=sys.stderr)
            print("重なると記録が壊れるので、こちらは何もせずに終わります。",
                  file=sys.stderr)
            return False
        print("前回の錠が残っていました（PID %s はもういません）。外して進みます。"
              % pid, file=sys.stderr)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "what": what,
                   "at": datetime.now(JST).strftime("%Y-%m-%d %H:%M")}, f)
    return True


def drop_lock():
    try:
        os.remove(os.path.join(ROOT, LOCK))
    except OSError:
        pass


def push_state():
    """出したそばから記録を送る。

    1回の起動が数時間続くので、最後にまとめて送ると、
    途中で止まったときに「出したのに記録が無い」状態になり、
    次の起動が同じものをもう一度出してしまう。
    """
    import subprocess
    cmds = [
        ["git", "add", STATE],
        ["git", "-c", "user.name=yasumiru-bot",
         "-c", "user.email=bot@users.noreply.github.com",
         "commit", "-m", "auto: Threadsへ投稿"],
    ]
    try:
        if subprocess.run(["git", "diff", "--quiet", "--", STATE]).returncode == 0:
            return True
        for c in cmds:
            subprocess.run(c, check=True)
        for _ in range(3):
            # --autostash が要る。手元で走らせると index.html などが
            # 生成し直されて汚れていることがあり、素の --rebase は
            # 「unstaged changes」で止まる。止まると記録が送られないまま
            # 残り、次の起動が同じものをもう一度出す。
            subprocess.run(["git", "pull", "--rebase", "--autostash",
                            "origin", "main"])
            if subprocess.run(["git", "push", "origin", "main"]).returncode == 0:
                return True
            time.sleep(5)
        print("  記録を送れませんでした。次の起動が同じものを出す恐れがあります。",
              file=sys.stderr)
        return False
    except Exception as ex:                                   # noqa: BLE001
        print("  記録の送信に失敗: %s" % ex, file=sys.stderr)
        return False


# ------------------------------------------------- 予定実行が届いているか

def check_duplicates():
    """アカウントに同じ投稿が並んでいないか。

    重複は表示を絞られる要因になるし、機械が回しているのも見える。
    出す前に止める仕掛けは入れたが、それが効かなかったときに
    気づけないのがいちばん困る。だから後からも数えられるようにしておく。
    """
    try:
        uid, token = credentials()
    except SystemExit:
        return
    live = recent_posts(uid, token, n=25)
    if not live:
        print("アカウントの投稿を取れませんでした。")
        return
    seen = {}
    dup = []
    for x in live:
        h = head_of(x.get("text"))
        if not h:
            continue
        if h in seen:
            dup.append((h, seen[h], x.get("timestamp", "")))
        else:
            seen[h] = x.get("timestamp", "")
    if dup:
        print("")
        print("⚠️  同じ内容が並んでいます（直近%d件のうち %d組）:" % (len(live), len(dup)))
        for h, a, b in dup:
            print("     %s" % h)
            print("       %s と %s" % (b[:16], a[:16]))
        print("   Threadsのアプリから片方を消してください。")
    else:
        print("直近%d件に重複はありません。" % len(live))


def doctor(days=7):
    """GitHub が予定実行をちゃんと配信しているかを数える。

    落ちていることに気づかないまま何日も投稿が止まる、というのが
    いちばん困る。だから配信率そのものを見られるようにしておく。
    """
    import urllib.request
    import subprocess
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True).stdout.strip()
        repo = re.sub(r"\.git$", "", re.split(r"github\.com[:/]", url)[-1])
    except Exception:                                         # noqa: BLE001
        print("リポジトリが分かりません。")
        return
    runs = []
    for p in (1, 2, 3):
        u = ("https://api.github.com/repos/%s/actions/runs?per_page=100&page=%d"
             % (repo, p))
        try:
            with urllib.request.urlopen(u, timeout=30) as r:
                runs += json.load(r).get("workflow_runs", [])
        except Exception as ex:                               # noqa: BLE001
            print("実行記録が取れません: %s" % ex)
            return
    since = datetime.now(JST).replace(tzinfo=None) - timedelta(days=days)
    rows = []
    for r in runs:
        # Cloudflare から起こすようになったので workflow_dispatch が本筋。
        # schedule だけを数えていたころの名残で、動いているのに
        # 「届いていません」と誤報していた。
        if r["event"] not in ("schedule", "workflow_dispatch"):
            continue
        t = (datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ")
             + timedelta(hours=9))
        if t < since:
            continue
        rows.append((t, r["name"], r["conclusion"], r["event"]))
    rows.sort()
    if not rows:
        print("この%d日、予定実行は一度も動いていません。" % days)
        return
    import collections
    per = collections.Counter()
    for t, name, _c, _e in rows:
        per[(t.strftime("%m/%d"), name)] += 1
    names = sorted(set(n for _, n, _c, _e in rows))
    print("予定実行が動いた回数（JST・%d日ぶん）" % days)
    print("  %-8s %s" % ("日", "  ".join("%-14s" % n[:14] for n in names)))
    for d in sorted(set(k[0] for k in per)):
        print("  %-8s %s"
              % (d, "  ".join("%-14s" % ("%d回" % per[(d, n)]) for n in names)))
    print("\n最後に動いたのは %s（%s）。"
          % (rows[-1][0].strftime("%m/%d %H:%M"),
             "Cloudflare" if rows[-1][3] == "workflow_dispatch" else "GitHubのschedule"))
    stale = [r for r in rows if r[3] == "schedule"]
    if stale and (rows[-1][0] - stale[-1][0]).total_seconds() < 0:
        print("⚠️  GitHubのscheduleが動いています。二重に起こすと記録が壊れます。")
    gap = (datetime.now(JST).replace(tzinfo=None) - rows[-1][0]).total_seconds() / 3600
    if gap > 8:
        print("⚠️  それから %.1f時間 空いています。予定実行が届いていません。" % gap)
        print("   Actions タブから手で動かすか、"
              "python3 threads.py --serve --until HH:MM を手元で走らせてください。")
    else:
        print("直近 %.1f時間以内に動いています。" % gap)
    check_duplicates()


def main():
    args = sys.argv[1:]
    if "--report" in args:
        report()
        return
    if "--doctor" in args:
        doctor()
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

    cfg = load("config.json")

    if "--limit" in args:
        uid, token = credentials()
        used, total = publishing_limit(uid, token)
        print("24時間の投稿数: %s / %s" % (used, total))
        return

    dry = "--dry-run" in args

    if "--serve" in args:
        if not dry and not take_lock("serve"):
            sys.exit(1)
        until = None
        if "--until" in args:
            until = args[args.index("--until") + 1]
        window = 4.0
        if "--hours" in args:
            window = float(args[args.index("--hours") + 1])
        th = cfg.get("threads") or {}
        try:
            serve(cfg, until, window_h=window,
                  gap_min=int(th.get("catchupGapMin", 25)),
                  max_late_h=float(th.get("maxLateHours", 3)),
                  dry=dry, push=("--push" in args))
        finally:
            if not dry:
                drop_lock()
        return

    # 素で叩いたときも枠は記録する。空のまま残すと、
    # 見張り側が「投稿した時刻」を枠とみなしてしまい、
    # 実際には出していない枠まで消化済みになる。
    if not dry and not take_lock("single"):
        sys.exit(1)
    posted = load(STATE, {"keys": [], "log": []}) or {"keys": [], "log": []}
    now_h = datetime.now(JST).hour
    try:
        n, _why = run_once(cfg, posted, slot_hour=now_h, dry=dry)
    finally:
        if not dry:
            drop_lock()
    if n and not dry:
        print("\n※ この実行は記録を送っていません（--push なし）。")
        print("   git add %s して push しないと、次の起動が同じものを出す恐れがあります。"
              % STATE)


if __name__ == "__main__":
    main()

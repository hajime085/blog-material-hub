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

def compose_product(p, site, pr):
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

    lines.append("")
    lines.append("%s/p/%s/" % (site, p["id"]))
    return "\n".join(lines)


def compose_guide(g, site, pr):
    return "\n".join([
        pr + g["title"],
        "",
        g["lead"],
        "",
        "%s%s" % (site, g["path"]),
    ])


def compose_page(page, site, pr):
    return "\n".join([
        pr + page["title"],
        "",
        page["lead"],
        "",
        "%s%s" % (site, page["path"]),
    ])


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


# ---------------------------------------------------------------- 選ぶ

def pick(cfg, posted, want):
    """今回出すものを選ぶ。

    同じ種類ばかり続けない。商品だけを毎回流すと、
    ただの値段の羅列になって読む理由が無くなる。
    記事とセールの案内を混ぜて、間を空ける。

    一度出したものは二度と出さない。
    同じ商品を何度も流すのは、読む側から見れば繰り返しでしかない。
    """
    site = cfg["site"]["url"].rstrip("/")
    pr = cfg["site"].get("prLabel") or ""
    feed = load("assets/data/feed.json", []) or []
    done = set(posted.get("keys") or [])

    ev = active_event()
    n_km = sum(1 for p in feed
               if p["pr"] >= 1000 and "送料無料" in (p.get("tags") or []))

    # 1) セールの案内。開催中だけ、期間に1回。
    pool_event = [x for x in event_posts(site, ev, n_km) if x["key"] not in done]
    # 2) 攻略ガイド。出し切ったら、いちばん前に出したものから再び回す。
    guides = guide_posts(site)
    pool_guide = [g for g in guides if g["key"] not in done]

    # 3) 商品。新しく載ったものから。キャプションの無いものは出さない。
    #    文章が無いと、値段だけの投稿になってしまう。
    items = [p for p in feed
             if p.get("cap") and ("product:" + p["id"]) not in done]
    items.sort(key=lambda p: p.get("at") or "", reverse=True)

    out = []
    if pool_event:
        e = pool_event[0]
        out.append((e["key"], compose_page(e, site, pr)))
    if len(out) < want and pool_guide:
        g = pool_guide[0]
        out.append((g["key"], compose_guide(g, site, pr)))
    for p in items:
        if len(out) >= want:
            break
        out.append(("product:" + p["id"], compose_product(p, site, pr)))
    return out


# ---------------------------------------------------------------- 出す

def publish(uid, token, text):
    """2段階。まず入れ物を作り、それから公開する。"""
    c = api("POST", "%s/threads" % uid, {"media_type": "TEXT", "text": text}, token)
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


def main():
    args = sys.argv[1:]
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
    for key, text in picks:
        print("── %s（%d文字）" % (key, len(text)))
        print(text)
        print()

    if dry:
        return

    uid, token = credentials()
    used, total = publishing_limit(uid, token)
    if used is not None and used + len(picks) > total:
        sys.exit("24時間の上限に近いので止めます（%s/%s）" % (used, total))

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    ok = 0
    for key, text in picks:
        try:
            pid = publish(uid, token, text)
        except Exception as ex:                           # noqa: BLE001
            print("  × %s の投稿に失敗: %s" % (key, ex), file=sys.stderr)
            if "OAuth" in str(ex) or "190" in str(ex) or "401" in str(ex):
                print("     トークンが切れている可能性があります。"
                      "手元で python3 threads.py --refresh を実行してください。",
                      file=sys.stderr)
                break
            continue
        posted["keys"].append(key)
        posted["log"].append({"key": key, "id": pid, "at": now})
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

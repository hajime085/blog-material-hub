#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""親投稿のもとになる「商品理解」を作って、products.json に貯める。

投稿するときに考えるのではなく、先に考えておく。
threads.py は貯めたものを組み立てるだけにする。
判断を投稿時にやると、毎回ぶれるし、確かめる機会もない。

流れ:
    商品データ → 商品理解 → pitch → 決まりに照らして自己チェック
    → products.json へ保存 → threads.py が使う

状態:
    pending      … まだ作っていない
    ready        … 作って、チェックも通った
    needs_review … 根拠が足りない、または決まりに触れている疑いがある

使い方:
    python3 pitch.py --pending           投稿の窓に入っている商品を出す（Threads用）
    python3 pitch.py --site              掲載中で未作成のものを出す（サイトの「どんな商品？」用）
    python3 pitch.py --apply out.json    作ったものを取り込む（チェック付き）
    python3 pitch.py --check             貯めたもの全部を検査し直す
    python3 pitch.py --stats             状態と入口の型の内訳
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# 入口の型（2026-11-10 の標準形）。pitch_rules.md と対応している。
#
# それまでの7種（悩み・あるある・利用シーン・発見・比較・意外性・
# 悩み＋価格）は、どれも「商品説明の言い切り」の仲間どうしで、
# 自アカウントでは差が出なかった（入口の型の中央値 7.5〜11）。
# 界隈で伸びている投稿（型カタログ T1）は、1行目が
# 事実への短い反応になっている。驚けるのは見て分かる事実だけ。
#   price … 価格の事実への第一声（現在価格・OFF を含んでよい）
#   count … レビュー件数・評価の事実への第一声
#   look  … 見た目・仕様の事実への第一声（写真で分かること）
#   scene … 利用場面への第一声（あるあるを反応の形で）
HOOK_TYPES = {
    "price": "価格への第一声",
    "count": "件数への第一声",
    "look":  "見た目・仕様への第一声",
    "scene": "場面への第一声",
}

REQUIRED = ("hook_type", "target", "problem", "hook", "body", "ask", "ask_type")

# 締めの問い（ask）の種類。締めは全投稿このどれかで統一する。
# それまでの締めローテーション（experience／compare／empathy／save／
# question／none）は廃止した。定型の文を回すのではなく、
# 商品ごとの問いを書く。種類は「何を聞くか」の分類だけに使う。
ASK_TYPES = {
    "usage":   "使っている人を聞く",
    "choice":  "選び方・派を聞く",
    "problem": "困りごとを聞く",
}

# 判定は3階層で見る。「〜そう」が付いているかどうかでは決めない。
#
#   レベル1 直接事実          … 商品データに書いてあること。そのまま使える
#   レベル2 直接導ける説明     … 物理的な比較、算術、仕様の言い換え。使える
#   レベル3 性能・効果・未来   … 禁止
#
# レベル2とレベル3の境目は「第三者が同じ結論に達するか」。
#   30枚1,360円 → 1枚45円            算術なので誰が計算しても同じ。レベル2
#   バスタオルより小さい → 場所を取らない  大きさの比較。レベル2
#   ワイヤー入り → 形が崩れにくい        持ちの話。人と条件による。レベル3
#
# レベル3の目印になる述語を並べておく。ここに当たったら見直しに回す。
LEVEL3_WORDS = [
    # 性能が続くこと
    "崩れにく", "へたりにく", "落ちにく", "壊れにく", "長持ち", "劣化しにく",
    "型崩れ", "色落ちしにく", "毛玉になりにく",
    # 体や体調
    "疲れが", "眠", "効き", "整う", "痩せ", "肌が", "髪が", "体調",
    # 未来のふるまい
    "時間が減", "手間が減", "楽になる", "続けやす", "起きにく", "迷わなくな",
    "快適になる", "便利になる", "習慣にな", "面倒がなくな", "失敗しにく",
]

# 効果効能に触れる語。サプリと化粧品では書けない。
EFFECT_WORDS = [
    "効く", "効果", "改善", "治", "痩せ", "痩身", "美白", "シミが", "シワが",
    "たるみが", "毛穴が消", "若返", "解消", "予防", "回復", "軽減", "疲れが取れ",
    "眠れる", "ぐっすり", "代謝が", "免疫", "血圧が", "血糖", "デトックス",
]
# 体型が変わる、という言い方。ファッションでは書けない。
BODY_WORDS = ["痩せて見え", "細く見え", "脚が長く", "小顔"]

# 外部評価。根拠がはっきりしないまま書けない。
AWARD_WORDS = ["MVP", "1位", "No.1", "ナンバーワン", "受賞", "ランキング", "殿堂"]

# レビュー件数を人数に言い換えたもの。
PEOPLE_RE = re.compile(r"[\d,]+\s*人が(買|購入|使)")


def load(name, default=None):
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def text_of(m):
    # 締めの問い（ask）も外に出る文なので調べる。
    return "\n".join([str(m.get("hook") or ""), str(m.get("body") or ""),
                      str(m.get("ask") or "")])


def validate(m, p):
    """決まりに照らして調べる。触れていそうなものを挙げて返す。

    機械で調べられるのはここまで。
    「その悩みが商品特徴から自然に導けるか」は人が見るしかない。
    """
    issues = []
    for k in REQUIRED:
        if not (m.get(k) or "").strip():
            issues.append("%s が空" % k)
    # 何を根拠にしたかが残っていないと、あとから確かめられない。
    if not (m.get("features") or []):
        issues.append("features が空（何を根拠にしたか残らない）")
    if m.get("hook_type") not in HOOK_TYPES:
        issues.append("入口の型が不明: %s" % m.get("hook_type"))
    # 締めの問い（ask）は必須。答えたくなる問いにする。
    # 相手を名指しし（「○○の人」）、疑問で終える。
    ask = (m.get("ask") or "").strip()
    if not ask:
        issues.append("ask が空（締めの問いは必須）")
    elif not ask.endswith(("？", "?")):
        issues.append("ask が問いで終わっていない: %s" % ask)
    if m.get("ask_type") not in ASK_TYPES:
        issues.append("問いの種類が不明: %s" % m.get("ask_type"))

    t = text_of(m)
    cat = p.get("category", "")

    if PEOPLE_RE.search(t):
        issues.append("レビュー件数を人数に言い換えている")

    for w in AWARD_WORDS:
        if w in t:
            issues.append("外部評価「%s」。根拠が商品データから分かるか確認が要る" % w)
            break

    if cat == "beauty":
        for w in EFFECT_WORDS:
            if w in t:
                issues.append("効果効能に触れる語「%s」" % w)
                break
    if cat == "fashion":
        for w in BODY_WORDS:
            if w in t:
                issues.append("体型が変わる言い方「%s」" % w)
                break

    # 値段の置き場所。
    # 事実ブロック（組み立て側が最後に付ける）以外では、本文（body）と
    # 問い（ask）に値段を書かない。二重に出すと告知の列挙になる。
    # 第一声（hook）だけは、型が price のときに価格の事実を使える。
    # その場合も、書けるのは実在の金額（現在価格・定価）か、
    # 「1杯あたり約32円」のような算術で出る単価だけ。
    body_ask = "\n".join([str(m.get("body") or ""), ask])
    if re.search(r"[\d,]{2,}\s*円", body_ask):
        issues.append("本文・問いに値段が入っている（価格は組み立て側で付ける）")
    if m.get("hook_type") != "price":
        if re.search(r"[\d,]{2,}\s*円", str(m.get("hook") or "")):
            issues.append("hook に値段が入っている（価格の第一声は型 price のときだけ）")
    else:
        allowed = {p.get("price"), p.get("listPrice")}
        for mt in re.finditer(r"([\d,]{2,})\s*円", str(m.get("hook") or "")):
            val = int(mt.group(1).replace(",", ""))
            before = (m.get("hook") or "")[max(0, mt.start() - 10):mt.start()]
            if val in allowed or "あたり" in before or "約" in before:
                continue
            issues.append("hook の金額 %s円 が商品データに無い" % mt.group(1))
            break

    # レベル3に当たる述語が入っていないか。
    # 「〜そう」が付いているかどうかでは判定しない。
    # 「選びやすそう」は仕様の言い換え（レベル2）なので通す。
    for w in LEVEL3_WORDS:
        if w in t:
            mt = re.search(r".{0,16}%s.{0,10}" % re.escape(w), t)
            issues.append("レベル3（性能・効果・未来の推測）: 「%s」"
                          % (mt.group(0).strip() if mt else w))
            break

    if len(t) > 200:
        issues.append("本文が長い（%d字）。親投稿は短く" % len(t))
    return issues


def material(p):
    """商品理解を作るための材料。ここに無いことは書けない。"""
    return {
        "id": p["id"],
        "category": p.get("category"),
        "title": p.get("title"),
        "rawTitle": p.get("rawTitle"),
        "description": p.get("description"),
        "points": p.get("points"),
        "caption": p.get("caption"),
        "price": p.get("price"),
        "listPrice": p.get("listPrice"),
        "freeShipping": "送料無料" in (p.get("tags") or []),
        "reviewCount": p.get("reviewCount"),
        "reviewAverage": p.get("reviewAverage"),
        "unitNote": p.get("unitNote"),
    }


def main():
    args = sys.argv[1:]
    doc = load("products.json") or {}
    products = doc.get("products", [])
    by_id = {p["id"]: p for p in products}

    if "--pending" in args or "--site" in args:
        n = 40
        for a in args:
            if a.isdigit():
                n = int(a)
        # 出す対象は2通りある。
        #
        #   既定（--pending）      投稿の窓に入っている商品。Threads用。
        #                          窓の外に作っても出番が来ないまま終わる。
        #                          実際、25件ぶん無駄に作ったことがある。
        #   --site                 掲載中で商品理解が無いものすべて。サイト用。
        #                          「どんな商品？」はサイトに載っている全部に要る。
        #                          窓で絞ると、そこが埋まらないまま残る。
        #
        # 商品理解はどちらにも使える。分けているのは「どれから作るか」だけ。
        from datetime import datetime, timedelta, timezone
        JST = timezone(timedelta(hours=9))
        cfg = load("config.json") or {}
        days = cfg.get("threads", {}).get("freshDays", 3)
        limit = (datetime.now(JST) - timedelta(days=days)).strftime("%Y-%m-%d")
        site_mode = "--site" in args

        feed = {x["id"]: x for x in (load("assets/data/feed.json", []) or [])}
        todo = []
        for p in products:
            if (p.get("pitch_status") or "pending") != "pending":
                continue
            if site_mode:
                # 掲載していないものは、ページも無いので後回し。
                if p.get("id") not in feed and not p.get("startTime"):
                    continue
            else:
                f = feed.get(p["id"])
                if not f:
                    continue
                if (f.get("at") or "")[:10] < limit:
                    continue
            todo.append(p)

        def off(p):
            lp = p.get("listPrice") or 0
            return round((lp - p["price"]) / lp * 100) if lp > p.get("price", 0) else 0

        if site_mode:
            # 割引の大きいものから。目立つ商品のページから埋まる。
            todo.sort(key=off, reverse=True)
        else:
            todo.sort(key=lambda p: p.get("bumpedAt") or "", reverse=True)

        out = [material(p) for p in todo[:n]]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if site_mode:
            print("\n// 掲載中で未作成 %d件（うち %d件を出しました）"
                  % (len(todo), len(out)), file=sys.stderr)
            print("// 割引の大きい順。「どんな商品？」はサイトの全商品に要ります。",
                  file=sys.stderr)
        else:
            print("\n// 窓の中で未作成 %d件（うち %d件を出しました）"
                  % (len(todo), len(out)), file=sys.stderr)
            print("// 窓は「載せてから%d日以内」。これより古いものは出番が来ないので出しません。"
                  % days, file=sys.stderr)
        return

    if "--apply" in args:
        i = args.index("--apply")
        if i + 1 >= len(args):
            sys.exit("取り込むファイルを指定してください。")
        made = load(args[i + 1])
        if not made:
            sys.exit("読み込めませんでした: %s" % args[i + 1])
        ok = ng = miss = 0
        report = []
        for m in made:
            pid = m.get("id")
            p = by_id.get(pid)
            if not p:
                miss += 1
                continue
            issues = validate(m, p)
            # 旧形式の締め（cta／cta_text）は取り込まない。
            # 残すと「組み立てが2つある」状態がデータ側に残る。
            body = {k: v for k, v in m.items()
                    if k not in ("id", "cta", "cta_text")}
            p["marketing"] = body
            p["pitch_status"] = "needs_review" if issues else "ready"
            if issues:
                ng += 1
                report.append((pid, p.get("title", "")[:28], issues))
            else:
                ok += 1
        save("products.json", doc)
        print("取り込み: ready %d件 / needs_review %d件 / 見つからず %d件" % (ok, ng, miss))
        if report:
            print("\n見直しが要るもの:")
            for pid, t, issues in report:
                print("  %s %s" % (pid, t))
                for x in issues:
                    print("      - %s" % x)
        return

    if "--check" in args:
        ok = ng = 0
        report = []
        for p in products:
            m = p.get("marketing")
            if not m:
                continue
            issues = validate(m, p)
            p["pitch_status"] = "needs_review" if issues else "ready"
            if issues:
                ng += 1
                report.append((p["id"], p.get("title", "")[:28], issues))
            else:
                ok += 1
        save("products.json", doc)
        print("検査: ready %d件 / needs_review %d件" % (ok, ng))
        for pid, t, issues in report:
            print("  %s %s" % (pid, t))
            for x in issues:
                print("      - %s" % x)
        return

    # 既定は状態の一覧
    import collections
    st = collections.Counter((p.get("pitch_status") or "pending") for p in products)
    print("商品 %d件" % len(products))
    for k in ("ready", "needs_review", "pending"):
        print("  %-13s %d件" % (k, st.get(k, 0)))
    hooks = collections.Counter(
        (p.get("marketing") or {}).get("hook_type")
        for p in products if p.get("marketing"))
    if hooks:
        print("\n入口の型:")
        for k, v in hooks.most_common():
            print("  %-14s %-8s %d件" % (k, HOOK_TYPES.get(k, "?"), v))
    cats = collections.Counter(
        p.get("category") for p in products
        if (p.get("pitch_status") or "pending") == "pending")
    if cats:
        print("\nまだ作っていない売場:")
        for k, v in cats.most_common():
            print("  %-10s %d件" % (k, v))


if __name__ == "__main__":
    main()

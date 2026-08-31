#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""決まったことを、機械で守らせる。

書き置きでは守られなかった。pitch_rules.md に書いた決まりを
自分で破ったし、直したはずの形に何度も戻した。
だから「決めたこと」はここに検査として書く。破ると警告が出る。

各項目には、なぜそれを決めたのか（どの事故から来たのか）を残す。
理由の分からない決まりは、いつか誰かに外されるため。

    python3 rules.py        いま破っているものを一覧で出す
    build.py から自動で呼ばれる
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def load(name, default=None):
    p = os.path.join(ROOT, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        if name.endswith(".json"):
            return json.load(f)
        return f.read()


# ---------------------------------------------------------------- 決まりごと

def rule_post_needs_pitch(ctx):
    """投稿は商品理解のあるものだけ。商品名をそのまま貼らない。

    2026-08-31: キャプションのあとに楽天の商品名を貼る古い型で投稿していた。
    「犬 猫 DHA/EPA 活性オメガ3オイル 100ml ふりかけ…」と切れて出た。
    作り直す前の形に戻っていた。
    """
    src = ctx["threads_py"]
    if 'if not p.get("mk2"):' not in src:
        return ["threads.py の pick() に、商品理解の無い商品を外す条件がありません"]
    return []


def rule_what_is_it(ctx):
    """「どんな商品？」は description か商品理解のどちらかで出す。

    2026-08-30: 自動で足した商品は description が空なので、
    この項目がまるごと消えていた。二度指摘を受けた。
    """
    src = ctx["build_py"]
    if 'p.get("marketing") or {}' not in src or "どんな商品？" not in src:
        return ["build.py が、商品理解を「どんな商品？」の代わりに使っていません"]
    ps = ctx["products"]
    bad = [p["id"] for p in ps
           if not (p.get("description") or "").strip()
           and not p.get("marketing")]
    if len(bad) > len(ps) * 0.3:
        return ["「どんな商品？」が出ない商品が %d件（全%d件）あります。"
                "pitch.py --site で埋めてください" % (len(bad), len(ps))]
    return []


def rule_price_checked_everywhere(ctx):
    """価格の検査は、外に出る文すべてに掛ける。

    2026-08-31: 実売998円の商品の一文に999円と書いてあり、
    検査がキャプションと箇条書きしか見ていなかったので素通りした。
    投稿は商品理解から組み立てるので、そこが検査の外にあった。
    """
    src = ctx["build_py"]
    need = ["商品理解の入口", "商品理解の本文"]
    miss = [n for n in need if n not in src]
    if miss:
        return ["価格の検査が %s を見ていません" % "、".join(miss)]
    return []


def rule_shipping_from_api(ctx):
    """送料は楽天のデータを正とする。分からないものを送料別と出さない。

    2026-08-30: 開始前の商品が postageFlag を保存しておらず、
    判定が商品名の「送料無料」の有無だけになっていた。
    16件中14件が送料込みなのに「＋送料」と出ていた。
    """
    src = ctx["fetch_py"]
    if "merge_shipping_tag" not in src:
        return ["fetch_rakuten.py に送料の統一処理がありません"]
    if src.count('"freeShipping": it.get("postageFlag") == 0') < 2:
        return ["開始前の取り込みで postageFlag を保存していない経路があります"]
    return []


def rule_discount_has_evidence(ctx):
    """載せる値引きには裏付けを持つ。元値の分からないものを安いと言わない。

    2026-08-31: 掲載191件のうち142件が割引ゼロだった。
    商品名に「OFF」とあるから拾っただけで、安い根拠が無かった。
    これはサイト自身が記事で批判している「安く見えて安くない」と同じ。
    """
    r = (ctx["config"].get("rakuten") or {})
    out = []
    if r.get("minDiscountRate", 0) < 30:
        out.append("minDiscountRate が %s。30以上にしてください"
                   % r.get("minDiscountRate"))
    if r.get("eventMinDiscountRate", 0) < 30:
        out.append("eventMinDiscountRate が %s。30以上にしてください"
                   % r.get("eventMinDiscountRate"))
    return out


def rule_one_scheduler(ctx):
    """予定実行を起こす役は1つだけ。

    2026-08-29: GitHub の schedule が復活し、Cloudflare と二重に起動した。
    回が重なって記録が壊れ、同じ知識投稿を14:00と14:21に二度出した。
    """
    out = []
    for f in ("threads.yml", "watch.yml"):
        src = load(".github/workflows/" + f, "")
        if re.search(r"^\s*schedule:", src, re.M):
            out.append(".github/workflows/%s に schedule: があります。"
                       "起こす役は Cloudflare（ops/cron-worker.js）だけです" % f)
    return out


def rule_no_emoji(ctx):
    """デザインに絵文字を使わない。アイコンはSVGで用意する。"""
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    out = []
    for f in ("index.html",):
        src = load(f, "")
        hits = set(emoji.findall(src))
        if hits:
            out.append("%s に絵文字があります: %s" % (f, " ".join(sorted(hits))[:40]))
    return out


def rule_no_rate_shown(ctx):
    """料率は読者に見せない。"""
    out = []
    for f in ("index.html",):
        src = load(f, "")
        if re.search(r"料率|affiliateRate", src):
            out.append("%s に料率らしき記述があります" % f)
    return out


def rule_affiliate_links(ctx):
    """商品リンクは楽天の公式転送（hb.afl.rakuten.co.jp）を通す。"""
    bad = [p["id"] for p in ctx["products"]
           if "hb.afl.rakuten.co.jp" not in (p.get("affiliateUrl") or "")]
    if bad:
        return ["アフィリエイトリンクでない商品が %d件（例 %s）"
                % (len(bad), bad[0])]
    return []


def rule_no_cd_dvd(ctx):
    """CD・DVD・音楽は扱わない。"""
    ng = {str(g) for g in ((ctx["config"].get("rakuten") or {}).get("ngGenres") or [])}
    if not ng:
        return ["config.json の ngGenres が空です"]
    bad = [p["id"] for p in ctx["products"] if str(p.get("genreId") or "") in ng]
    if bad:
        return ["載せない売場の商品が %d件 残っています" % len(bad)]
    return []


RULES = [
    ("投稿は商品理解のあるものだけ", rule_post_needs_pitch),
    ("「どんな商品？」が出る", rule_what_is_it),
    ("価格の検査を外に出る文すべてに掛ける", rule_price_checked_everywhere),
    ("送料は楽天のデータを正とする", rule_shipping_from_api),
    ("値引きには裏付けを持つ", rule_discount_has_evidence),
    ("予定実行を起こす役は1つ", rule_one_scheduler),
    ("絵文字を使わない", rule_no_emoji),
    ("料率を見せない", rule_no_rate_shown),
    ("商品リンクは楽天の公式転送", rule_affiliate_links),
    ("CD・DVDは扱わない", rule_no_cd_dvd),
]


def run(quiet=False):
    ctx = {
        "products": (load("products.json", {}) or {}).get("products", []),
        "config": load("config.json", {}) or {},
        "build_py": load("build.py", ""),
        "threads_py": load("threads.py", ""),
        "fetch_py": load("fetch_rakuten.py", ""),
    }
    broken = 0
    for name, fn in RULES:
        try:
            issues = fn(ctx)
        except Exception as ex:                                # noqa: BLE001
            issues = ["検査そのものが失敗しました: %s" % ex]
        if issues:
            broken += 1
            print("⚠ 決まりを破っています: %s" % name)
            for x in issues:
                print("   %s" % x)
        elif not quiet:
            print("   ✓ %s" % name)
    return broken


if __name__ == "__main__":
    n = run()
    print("")
    print("決まり %d件中 %d件を破っています。" % (len(RULES), n) if n
          else "決まり %d件、すべて守れています。" % len(RULES))
    sys.exit(1 if n else 0)

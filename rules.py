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


def _routine():
    """日次の手順の覚え書き。手元に無い環境では空。"""
    p = os.path.expanduser(
        "~/.claude/projects/-Users-furusawahatsu-Desktop-blog-material-hub"
        "/memory/daily-routine.md")
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
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
    2026-08-31: ルールを作ったのに三度目が起きた。
    「全体の3割を超えたら警告」にしていたので、
    新しく足した25件が全部欠けていても10%で素通りした。
    割合ではなく「目立つところが埋まっているか」で見る。
    """
    src = ctx["build_py"]
    if 'p.get("marketing") or {}' not in src or "どんな商品？" not in src:
        return ["build.py が、商品理解を「どんな商品？」の代わりに使っていません"]

    ps = ctx["products"]
    def missing(p):
        return (not (p.get("description") or "").strip()
                and not p.get("marketing"))

    out = []
    # 編集部の棚は、トップでいちばん目立つ。1件でも欠けたら言う。
    # 棚と商品はIDの付き方が違う（棚は f…、商品は r…）。
    # 商品コードで突き合わせる。IDで引くと全件が「無い」判定になった。
    feat = load("featured.json", {}) or {}
    by_code = {p.get("itemCode"): p for p in ps if p.get("itemCode")}
    # 棚のカードは楽天へ直接飛ぶので、それ自体に「どんな商品？」は無い。
    # 見るべきは「棚に出していて、かつサイトにも商品ページがあるもの」。
    # 2026-09-03: 棚にあるがproducts.jsonから落ちた1件を
    #             「欠けている」と鳴らした。ページが無いものは対象外。
    bad = [x.get("itemCode") for x in (feat.get("items") or [])
           if x.get("itemCode") in by_code and missing(by_code[x["itemCode"]])]
    if bad:
        out.append("編集部の棚に「どんな商品？」が無い商品が %d件あります"
                   "（%s）。pitch.py --site で作ってください"
                   % (len(bad), bad[0]))

    # 出ているものと、まだ出ていないものを分けて見る。
    #
    # 2026-08-31: 「この2日に足したもの」で数えていたので、
    # 9/3開始のスーパーSALE待ちで意図的に伏せてある12件を
    # 決まり違反として鳴らした。狼が来ないのに鳴く検査は、
    # そのうち誰も見なくなる。それが抜けを見逃す本当の原因になる。
    # 出ている商品に欠けがあるときだけ「違反」とする。
    from datetime import datetime, timedelta
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def not_yet(p):
        return (p.get("startTime") or "") > now

    def finished(p):
        e = (p.get("endTime") or "").strip()
        return bool(e) and e < now

    # 伏せた商品はサイトに出ていないので、対象から外す。
    live_bad = [p["id"] for p in ps
                if missing(p) and not p.get("hidden")
                and not not_yet(p) and not finished(p)]
    if live_bad:
        out.append("サイトに出ているのに「どんな商品？」が無い商品が %d件あります"
                   "（%s）。pitch.py --site で作ってください"
                   % (len(live_bad), live_bad[0]))

    # 開始前のものは違反ではない。ただし始まる前に書き終える必要がある。
    # 期限が近いものだけ、名前を出して知らせる。
    soon_limit = (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
    waiting = [p for p in ps
               if missing(p) and not p.get("hidden") and not_yet(p)
               and (p.get("startTime") or "") <= soon_limit]
    if waiting:
        out.append("48時間以内に始まる商品で「どんな商品？」が無いものが %d件あります"
                   "（%s 開始）。始まるまでに pitch.py --site で作ってください"
                   % (len(waiting), min(x.get("startTime") or "" for x in waiting)))
    return out


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
    for f, key in (("index.html", "index_html"),):
        src = ctx.get(key) or ""
        hits = set(emoji.findall(src))
        if hits:
            out.append("%s に絵文字があります: %s" % (f, " ".join(sorted(hits))[:40]))
    return out


def rule_no_rate_shown(ctx):
    """料率は読者に見せない。"""
    out = []
    for f, key in (("index.html", "index_html"),):
        src = ctx.get(key) or ""
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


def rule_posted_date_shown(ctx):
    """カードに掲載日を出す。値段は毎日動くので、いつの話かが要る。

    2026-08-31: 一覧を見ても、いつ載った商品なのか分からなかった。
    静的なカードとJSで描き直すカードの両方に要る。片方だけだと
    一覧を触った瞬間に消える。
    """
    out = []
    if "card-posted" not in ctx["build_py"]:
        out.append("build.py のカードに掲載日がありません")
    js = ctx.get("app_js") or ""
    # 関数があるだけでは足りない。カードの中で呼ばれているかを見る。
    # 呼び出しを外しても気づかず、一覧から掲載日が消えたことがある。
    if not re.search(r"card-foot[^;]{0,400}postedLabel\(", js, re.S):
        out.append("assets/js/app.js のカードで掲載日を呼んでいません"
                   "（一覧はJSで描き直すので、ここが無いと消えます）")
    return out


def rule_no_duplicate_post(ctx):
    """同じ内容を二度出さない。手元の記録ではなくアカウントを見る。

    2026-08-29: 同じ知識投稿を 14:00 / 14:21 / 19:00 の三度出した。
    記録が push できず、次の回が「まだ出していない」と読んだため。
    記録は壊れることがあるので、アカウントそのものを見るしかない。
    """
    src = ctx["threads_py"]
    out = []
    if "recent_posts" not in src or "head_of" not in src:
        out.append("threads.py に、アカウントの直近と見比べる仕組みがありません")
    if "live_heads" not in src:
        out.append("候補を作る段階で、すでに載っているものを外していません"
                   "（出す直前に止めるだけだと、その枠が空振りになります）")
    return out


def rule_stop_when_unpushed(ctx):
    """記録を押せなかったら、その回はそこで止める。

    2026-08-29: push に失敗したまま出し続け、次の回が同じものを出した。
    出したのに記録が無い状態が、重複の引き金になる。
    """
    if "記録を送れませんでした。ここで止めます" not in ctx["threads_py"]:
        return ["push に失敗しても投稿を続ける作りになっています"]
    return []


def rule_single_run_records_slot(ctx):
    """素で叩いた実行でも、枠を記録する。

    2026-08-29 17:02 の投稿がどこから出たか説明できなかった。
    枠が空だと、見張り側が投稿時刻を枠とみなし、
    実際には出していない枠まで消化済みになる。
    """
    src = ctx["threads_py"]
    if "slot_hour=now_h" not in src:
        return ["素の実行が枠を記録していません"]
    if "take_lock" not in src:
        return ["二重起動を止める錠がありません"]
    return []


def rule_short_serve_window(ctx):
    """1回の起動は短くする。長く居座ると回が重なる。

    2026-08-29: 1回が4時間生きる作りだったため、Cloudflare と
    GitHub の起動が重なって順番待ちになり、記録が壊れた。
    枠ごとに起こすので、長く生きる必要はない。

    2026-09-04: 上限を1.5時間にしていたので、0.75時間が素通りした。
    セールの山場を10分おきに起こしたところ、45分生きる1回目の後ろに
    2回目・3回目が並び、20:10 と 20:30 の枠が出ないまま、
    待たされた1本が20:45に流れた（しかも二度目の内容だった）。
    起動が確実に届くようになった以上、1回は「出して終わり」でよい。
    上限を0.2時間（12分）に下げる。
    """
    src = load(".github/workflows/threads.yml", "")
    m = re.search(r'H="\$\{H:-([\d.]+)\}"', src)
    if not m:
        return ["threads.yml に窓の既定値が見当たりません"]
    if float(m.group(1)) > 0.2:
        return ["1回の窓が %s時間。長すぎます。10分おきに起こすと後ろが順番待ちになります" % m.group(1)]
    return []


def rule_hide_ended_sales(ctx):
    """終わったセールは、作り直しを待たずに客側でも隠す。

    サイトの作り直しは1日4回。その間に終わったセールが残ると、
    買えないものを載せていることになる。
    """
    js = ctx.get("app_js") or ""
    # 関数の有無ではなく、一覧を絞るところで使われているかを見る。
    if not re.search(r"filter\(.{0,90}?saleOver\(", js, re.S):
        return ["assets/js/app.js が、終了したセールを一覧から外していません"]
    return []


def rule_event_heading(ctx):
    """セールの商品はイベント名で出す。

    2026-08-30: 中身がスーパーSALEの目玉なのに
    「まもなく始まる特価」と名乗っていた。実態と合わず、
    いちばん強い言葉を捨てていた。
    """
    if "で始まる特価" not in ctx["build_py"]:
        return ["build.py が、イベント名を見出しに使っていません"]
    return []


def rule_captions_filled(ctx):
    """キャプションの空きを残さない。

    値札だけのカードは、何が安いのか読む人に伝わらない。
    自動で足した商品は空で入るので、日次で埋める
    （「今日の分やって」の手順）。
    """
    # 伏せた商品はサイトに出ていないので数えない。
    ps = [p for p in ctx["products"] if not p.get("hidden")]
    empty = [p for p in ps if not (p.get("caption") or "").strip()]
    if len(empty) > max(10, len(ps) * 0.05):
        return ["キャプションが空の商品が %d件（全%d件）あります。"
                "日次で埋めてください" % (len(empty), len(ps))]
    return []


def rule_no_self_click(ctx):
    """自分のアフィリエイトリンクを、こちらから辿らない。

    a.r10.to や hb.afl.rakuten.co.jp は成果計測を通る転送で、
    叩けば自分の広告を自分で踏むことになる。楽天が禁じている不正クリック。
    2026-08-31: featured.txt に短縮URLが貼られており、
    取り込み処理がそれを辿る作りになっていた。
    """
    out = []
    if "不正クリックになります" not in ctx["fetch_py"]:
        out.append("fetch_rakuten.py が、アフィリエイトの短縮URLを辿らない作りになっていません")
    src = ctx.get("featured_txt") or ""
    live = [l for l in src.splitlines()
            if l.strip() and not l.strip().startswith("#")]
    bad = [l for l in live if "a.r10.to" in l or "hb.afl.rakuten.co.jp" in l]
    if bad:
        out.append("featured.txt にアフィリエイトのリンクが %d行あります" % len(bad))
    return out


def rule_featured_fresh(ctx):
    """編集部の棚を放置しない。

    手で選ぶ枠なので、放っておくと季節も値段も合わなくなる。
    価格と在庫は自動で追えるが、顔ぶれは人が入れ替えるしかない。
    """
    from datetime import datetime
    d = load("featured.json", {}) or {}
    at = (d.get("updatedAt") or "")[:10]
    if not at:
        return ["featured.json に更新日がありません"]
    try:
        days = (datetime.now() - datetime.strptime(at, "%Y-%m-%d")).days
    except ValueError:
        return ["featured.json の更新日が読めません: %s" % at]
    if days > 7:
        return ["編集部の棚が %d日 更新されていません（%s）。"
                "顔ぶれを見直してください" % (days, at)]
    return []


def rule_no_dead_fallback(ctx):
    """使わなくなった道は残さない。

    残しておくと、条件がひとつ外れた拍子にそこへ落ちる。
    実際、投稿の組み立てには新旧3本の道があり、
    使っていないはずの古い道に落ちて、楽天の商品名を
    そのまま貼った投稿が出た（2026-08-31）。

    新しいやり方に切り替えたら、古いほうは消す。
    「いつか使うかもしれない」で残さない。
    """
    src = ctx["threads_py"]
    out = []
    if "def shapes(" in src:
        out.append("threads.py に古い投稿の型 shapes() が残っています")
    if "forms.append" in src:
        out.append("threads.py に古い「キャプション＋商品名」の型が残っています")

    # 呼ばれていない関数も残骸。消し忘れると、いつか誰かが使う。
    import ast as _ast
    import collections as _c
    for name, code in (("threads.py", src), ("build.py", ctx["build_py"])):
        try:
            tree = _ast.parse(code)
        except SyntaxError:
            continue
        defs = [n.name for n in tree.body if isinstance(n, _ast.FunctionDef)]
        used = _c.Counter()
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Name):
                used[n.id] += 1
            elif isinstance(n, _ast.Attribute):
                used[n.attr] += 1
        dead = [d for d in defs if used[d] == 0 and d != "main"]
        if dead:
            out.append("%s に呼ばれていない関数があります: %s"
                       % (name, "、".join(dead[:4])))
    return out



def rule_keep_learning(ctx):
    """出した投稿から学ぶ手を止めない。

    2026-09-03: 69本出して、いいね0・返信0・フォロワー1人だった。
    数字は毎日取れていたのに、取るだけで何も変えていなかった。
    「今日の分やって」で枠を埋めることが目的になっていた。

    運用を無駄にしないために、台帳（learnings.json）を見張る。
    ・測っているか（4日以上ほったらかしにしない）
    ・試しが走っているか（分かったことを寝かせない）
    ・期限の来た試しに決着がついているか（やりっぱなしにしない）
    """
    from datetime import datetime, timedelta
    led = load("learnings.json", None)
    if not led:
        return ["learnings.json がありません。python3 learn.py を実行してください"]

    out = []
    today = datetime.now().strftime("%Y-%m-%d")
    limit = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
    if (led.get("lastRun") or "") < limit:
        out.append("成績を %s から測っていません。python3 learn.py を実行してください"
                   % (led.get("lastRun") or "一度も"))

    exps = led.get("experiments") or []
    running = [e for e in exps if e.get("status") == "running"]
    stale = [e for e in running if (e.get("until") or "9999") < today]
    if stale:
        out.append("試し %s は %s に期限が来ています。決着をつけてください"
                   % (stale[0]["id"], stale[0]["until"]))
    if not running:
        todo = [f for f in (led.get("findings") or [])
                if (f.get("action") or "") == "未着手"]
        if todo:
            out.append("走っている試しがありません。手つかずの気づきが %d件あります（%s）"
                       % (len(todo), todo[0]["claim"]))
        elif not exps:
            out.append("試しが一度も走っていません。learn.py の出力から一手を決めてください")
    return out



def rule_no_lookalike_pileup(ctx):
    """同じ店の似た商品を並べない。

    2026-09-05: 同じ店の同じ値段のSwitchケースが13件も載っていた。
    柄違いを13件並べても、読む人の選択肢は増えない。
    棚が埋まって見えるだけで、二度見する商品が埋もれる。
    サプリも同じ店・同じ値段で11件あった。
    店と値段が同じものは2件までにする。
    """
    import collections as _c
    ps = [p for p in ctx["products"] if not p.get("hidden")]
    g = _c.Counter((p.get("shop"), p.get("price")) for p in ps)
    over = [(k, v) for k, v in g.items() if v > 2]
    if over:
        (shop, price), n = max(over, key=lambda kv: kv[1])
        return ["同じ店・同じ値段の商品が並んでいます（%s %s円 が%d件、ほか%d組）"
                % (shop, price, n, len(over) - 1)]
    return []



def rule_daily_routine_is_one_piece(ctx):
    """日次の手順を途中で切らない。「今日の分やって」は挨拶まで含む。

    2026-09-06: 手順9（外への挨拶）をやらずに終えた。
    3回まわす話をしたときに「朝の一式（1〜8）」と書き足したせいで、
    自分の書いた覚え書きを読み違えた。
    番号で切ると、その境目で必ず落ちる。

    覚え書きに「1〜9すべて」と書いてあるかを機械で見る。
    """
    src = ctx.get("daily_routine") or ""
    if not src:
        return []          # 手元に覚え書きが無い環境では見ない
    if "1〜9すべて" not in src:
        return ["日次の手順に「1〜9すべて」の一文がありません。"
                "途中で切れる書き方に戻っています"]
    if "朝の一式（1〜8）は" in src:
        return ["日次の手順に「朝の一式（1〜8）」が残っています。"
                "この書き方で挨拶を落としました（2026-09-06）"]
    return []


def rule_network_failures_retried(ctx):
    """一過性の通信失敗を、そのまま落とさない。

    2026-09-07: 特価の見張りが10回に2回落ちていた。
    落ちる場所も時間（13〜21分）も毎回ちがい、手元では再現しなかった。
    api_get が拾っていたのは HTTPError だけで、接続が切れた・応答が
    途中で終わった・返事が JSON でなかった、という失敗は再試行されずに
    そのまま例外になっていた。1回の見張りで数百回叩くので、
    どこか1回でも起きれば全部が止まる。

    再試行の網が外れていないかを見る。
    """
    src = ctx.get("fetch_py") or ""
    if not src:
        return []
    head = src[src.find("def api_get("):]
    head = head[:head.find("\ndef ", 1)] if "\ndef " in head[1:] else head
    missing = [n for n in ("urllib.error.URLError", "TimeoutError", "OSError",
                           "json.JSONDecodeError")
               if n not in head]
    if missing:
        return ["api_get が %s を拾っていません。"
                "一過性の通信失敗で見張りが丸ごと落ちます（2026-09-07）"
                % "・".join(missing)]
    return []


def rule_unknown_flags_stop(ctx):
    """知らない指定で止まる。黙って無視しない。

    2026-09-07: 中身を見るつもりで `threads.py --dry` と打った。
    正しくは --dry-run。知らない指定は素通りし、
    下見のつもりが本当にThreadsへ投稿された。
    fetch_rakuten.py も同じ形をしていて、--wach と打てば
    見張りのつもりで巡回が走り products.json を書き換える。

    外に出る・ファイルを書き換える2本に、網が掛かっているかを見る。
    """
    bad = []
    for name, key in (("threads.py", "threads_py"),
                      ("fetch_rakuten.py", "fetch_py")):
        src = ctx.get(key) or ""
        if src and "知らない指定です" not in src:
            bad.append("%s に知らない指定を止める仕組みがありません" % name)
    return bad


def rule_warnings_reach_a_human(ctx):
    """検査の警告が、自動実行でも人の目に入るところに出る。

    2026-09-07: 価格ずれが半日で6件たまった。うち1件は値段が戻っているのに
    「1,740円のケースが870円」と書いたままだった。
    このサイトが批判している「安く見えて安くない」を自分でやっていた。

    build.py は警告を出すが終了コードは 0 で、実行の要約にも出ない。
    直る道が「私が手で回したとき」しか無かった。
    半日で6件たまるのは見落としではなく、仕組みの穴。

    最初は「合いません」を拾う段を足したが、それでは検査を足すたびに
    同じ穴が開く。件名で拾わず「直していない警告」で拾う。

    2つを見る:
      1. 見張りの手順が、警告を要約へ出しているか
      2. build.py が、直せた金額を書き出す前に直しているか
         （知らせるだけでは、読む人がいないところで嘘が公開される）
    """
    out = []
    wf = ctx.get("watch_yml") or ""
    if wf and "直していない警告" not in wf:
        out.append("見張りの手順が、検査の警告を実行の要約へ出していません。"
                   "自動実行では気づけません（2026-09-07）")

    src = ctx.get("build_py") or ""
    if "def reconcile_prices" not in src:
        out.append("build.py が、書き出す前に金額を直していません。"
                   "知らせるだけでは、読む人のいないところで嘘が公開されます"
                   "（2026-09-07）")
    # 「reconcile_prices(all_products)」で探すと def の行に当たってしまい、
    # 呼び出しを消しても鳴らない。2026-09-07、壊して確かめて気づいた。
    # 検査そのものが素通りするのが、いちばん質が悪い。呼び出しの形で探す。
    elif "= reconcile_prices(" not in src:
        out.append("reconcile_prices が定義されているのに呼ばれていません")
    return out


def rule_failed_posts_leave_a_trace(ctx):
    """出せなかった投稿が、記録に残って人の目に入る。

    2026-09-07: 10分おきの12本のうち20:41の1本が出なかった。
    起動はしていたのに、なぜ出なかったのかを調べる材料が何も無かった。
    失敗は stderr に流れるだけで、手順は緑のまま終わり、
    記録にも残らないので、たまたまか続いているのかも分からない。

    価格ずれとまったく同じ形の穴。誰も読まないところに置いても直らない。
    """
    out = []
    src = ctx["threads_py"]
    if '"failed": True' not in src:
        out.append("threads.py が、出せなかった投稿を記録に残していません"
                   "（2026-09-07）")
    if 'x.get("failed")' not in src:
        out.append("失敗した記録を「出した」と数えてしまいます。"
                   "枠が埋まったことにされ、次の起動が拾いません")
    ty = ctx.get("threads_yml") or ""
    if ty and "failed_posts.py" not in ty:
        out.append("投稿の手順が、出せなかったものを実行の要約へ出していません")
    if not (ctx.get("failed_posts_py") or ""):
        out.append("ops/failed_posts.py がありません")
    return out


def rule_blind_means_stop(ctx):
    """自分の投稿を確認できないときは、出さない。

    2026-09-07: recent_posts は通信が1回つまずくと None を返し、
    呼び出し側は「重複が無かった」と同じ扱いで先へ進んでいた。
    重複よけが、見えないところで丸ごと止まっていた。
    9/4 の重複投稿は、これが引き金だった可能性が高い。

    枠を1つ落とすより、同じ投稿を二度出すほうが痛い。
    """
    src = ctx["threads_py"]
    if 'return 0, "blind"' not in src:
        return ["自分の投稿を確認できないまま投稿しています。"
                "重複よけが働きません（2026-09-07）"]
    return []


def rule_selftest_is_wired(ctx):
    """決まりが鳴るかを確かめる仕組みが、動く場所に置いてある。

    2026-09-07: 手で壊して確かめる限り、確かめ忘れた決まりは静かに死ぬ。
    実際にこの日、鳴らなくなっていた決まりが3件見つかった。
      ・警告が人に届く（探す文字列が定義行にも当たっていた）
      ・つながらなかったらやり直す（材料を ctx から取っていなかった）
      ・終わったセールを隠す（同上）
    どれも「守れています」と出ていた。

    決まりを足したら PROBES に壊し方も書く。
    書かないと、自己診断が「壊し方を書いていない決まり」として挙げる。
    """
    out = []
    wf = ctx.get("watch_yml") or ""
    if wf and "--selftest" not in wf:
        out.append("見張りの手順が、決まりの自己診断を回していません"
                   "（2026-09-07）")
    src = ctx.get("rules_py") or ""
    if src and "def selftest(" not in src:
        out.append("rules.py に自己診断がありません")
    return out


def rule_reports_land_where_readable(ctx):
    """落ちた理由を、あとから読める場所に残す。

    2026-09-07: 見張りが落ちる理由が分からず、実行の要約へ出すようにした。
    2026-09-08: それでも分からなかった。要約は管理者権限がないと読めず、
    APIから取れる注釈は「Process completed with exit code 1」だけだった。
    報告を、読めない場所に置いていた。価格ずれのときと同じ形の間違い。

    出したつもりで、届いていない。それは出していないのと同じ。
    リポジトリの中に置けば git pull で読める。
    """
    out = []
    wf = ctx.get("watch_yml") or ""
    if wf and "record_failure.py" not in wf:
        out.append("見張りの手順が、落ちた理由をリポジトリに残していません。"
                   "実行の要約は管理者権限がないと読めません（2026-09-08）")
    if not (ctx.get("record_failure_py") or ""):
        out.append("ops/record_failure.py がありません")
    return out


def rule_server_errors_are_retried(ctx):
    """向こう側の一時的な不調で、実行全体を落とさない。

    2026-09-08: 見張りが落ち続けていた。前日に通信の切断を再試行するよう
    直したのに、翌 00:20 にまた落ちた。原因は別のところにあった。
    api_get は 429 以外の HTTP エラーをすべて SystemExit で投げる。
    SystemExit は Exception ではないので、売場ごとの受け止めも、
    再試行の網も素通りする。楽天が一度 500 を返すだけで実行全体が死ぬ。

    設定の間違い（403・400）は止まってよい。向こうの不調は待てば通る。
    """
    src = ctx.get("fetch_py") or ""
    if not src:
        return []
    head = src[src.find("def api_get("):]
    cut = head.find("\ndef ", 1)
    if cut > 0:
        head = head[:cut]
    out = []
    if "500, 502, 503, 504" not in head:
        out.append("api_get が、楽天側の一時的な不調（500系）を再試行していません。"
                   "一度返ってくるだけで見張りが丸ごと落ちます（2026-09-08）")
    # 諦めるときも、設定の間違いと同じ SystemExit で投げてはいけない。
    # SystemExit は Exception ではないので、売場ごとの受け止めを素通りする。
    if "class UpstreamError" not in src:
        out.append("楽天側の不調と、設定の間違いを分けていません。"
                   "1売場の不調で11売場ぶんが死にます（2026-09-08）")
    return out


def rule_hand_writing_is_never_overwritten(ctx):
    """自動実行が、人の書いたものを上書きしない。

    2026-09-10: 9/13開始の商品に書いたキャプションと商品理解が、
    同じ日の巡回で消えていた。開始前の商品を作るところが、
    既存を見ずに辞書をまるごと作り直していた。
    開始前の商品は始まるまで何度も拾い直されるので、
    書いても書いても次の巡回で空に戻る。

    9/5に hand_attic.json を入れたが、あれは「消してから足し直す」経路
    にしか効かない。ここは消さずに上書きしていたので素通りしていた。
    同じ「書いたものが消える」でも、道が2本あった。

    2つを見る:
      1. 上書きを防ぐ with_prev があり、実際に使われているか
      2. 守る範囲が広すぎないか（listPrice まで守ると
         参考価格が古いまま固定され、「安く見えて安くない」に戻る）
    """
    src = ctx.get("fetch_py") or ""
    if not src:
        return []
    out = []
    # 消えかたは1つではない。
    # 2026-09-10 に「上書き」を塞いだが、翌朝また消えていた。
    # 今度は「いったん落ちて、空で拾い直された」。
    # 物置に預けていたのは del を書いた2箇所だけで、
    # 取得の窓から外れて静かに落ちた商品は預けられていなかった。
    # 消えかたを1つずつ塞ぐのをやめ、保存前に全部を預けることにした。
    if "for _p in existing.values():" not in src or "for _p in result.values():" not in src:
        out.append("保存する前に、手で書いたぶんを全部は物置に預けていません。"
                   "静かに落ちた商品は戻せません（2026-09-11）")
    if "def with_prev(" not in src:
        out.append("fetch_rakuten.py に with_prev がありません。"
                   "開始前の商品を作り直すたびに、書いたものが消えます"
                   "（2026-09-10）")
    elif "with_prev(existing.get(pid)" not in src:
        out.append("with_prev が定義されているのに、使われていません")
    if "WRITTEN_BY_HAND" in src:
        i = src.find("WRITTEN_BY_HAND = (")
        head = src[i:src.find(")", i)]
        for f in ("listPrice", "tags", "unitNote"):
            if f in head:
                out.append("WRITTEN_BY_HAND に %s が入っています。"
                           "毎回calculate し直す項目を守ると、"
                           "参考価格が古いまま固定されます" % f)
                break
    return out


def rule_event_calendar_is_not_empty(ctx):
    """先の予定が切れたまま放置しない。

    2026-09-11: スーパーSALEが終わった時点で events.json の予定が空になり、
    予定の投稿（表示の中央値110、いちばん見られている型）が沈黙した。
    翌日まで誰も気づかなかった。利用者に指摘されて分かった。

    楽天が次を発表するまで日程は書けない。だから「空だと鳴る」検査にする。
    鳴ったら、発表されているか見に行って、出ていれば入れる。
    出ていなければ、投稿は「まだ発表されていません」と言う（threads.py）。
    予想で埋めない。
    """
    from datetime import datetime
    doc = load("events.json", {}) or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ahead = [e for e in doc.get("events", [])
             if ((e.get("end") or e.get("start") or "")[:16]) >= now]
    out = []
    # セールが終わった直後に予定が空なのは普通のこと。楽天がまだ出していない。
    # そこで鳴らすと、発表までの数日ずっと赤いままになる。
    # 狼が来ないのに鳴く検査は、そのうち誰も見なくなる。
    # 「終わってから1週間たっても次が無い」なら、発表を見落としている。
    if not ahead:
        ends = sorted((e.get("end") or e.get("start") or "")[:16]
                      for e in doc.get("events", []))
        last = ends[-1] if ends else ""
        gap = 99
        if last:
            from datetime import datetime as _dt
            try:
                gap = (_dt.now() - _dt.strptime(last, "%Y-%m-%d %H:%M")).days
            except ValueError:
                gap = 99
        if gap >= 7:
            out.append("前のセールが終わって%d日、これから先の予定が1つもありません。"
                       "楽天の発表を見落としていないか見てください（2026-09-11）" % gap)
    # 予定が無いときに投稿が黙らないことも見る。
    src = ctx.get("threads_py") or ""
    if src and "次の日程は、楽天からまだ発表されていません" not in src:
        out.append("予定が無いとき、予定の投稿が何も出さずに黙ります。"
                   "いちばん見られている型が止まります（2026-09-11）")
    return out


def rule_failures_are_visible_everywhere(ctx):
    """落ちたことが、どの段で落ちても私に見える。

    2026-09-12: 利用者から「GitHubがエラーを出しているが大丈夫か」と
    聞かれた。実際に3回落ちていたのに、私は「失敗の記録はありません」と
    報告していた。理由は仕組みの掛け方:
      ・--doctor は「投稿しようとして失敗した記録」しか見ない
      ・ops/failures.md は見張りの1つの段にしか掛かっていない
    落ちていたのは git を送り返す段で、どちらの網にも入らなかった。

    「見えている範囲に無い」を「起きていない」と読み替えたのが間違い。
    3つを見る:
      1. --doctor が、実行そのものの失敗を数えて出すか
      2. 両方の手順に、どの段で落ちても記録する受け皿があるか
      3. その受け皿が、最後の段に置いてあるか
         （落ちる段より前に置くと動かない）
    """
    import os
    out = []
    src = ctx.get("threads_py") or ""
    if src and 'r[2] == "failure"' not in src:
        out.append("--doctor が、予定実行そのものの失敗を見ていません"
                   "（2026-09-12）")
    here = os.path.dirname(os.path.abspath(__file__))
    for name, key in (("threads.yml", "threads_yml"), ("watch.yml", "watch_yml")):
        wf = ctx.get(key) or ""
        if not wf:
            continue
        if "どこかで落ちたら記録する" not in wf:
            out.append("%s に、どの段で落ちても記録する受け皿がありません"
                       % name)
            continue
        # 受け皿は最後になければ、落ちた段のあとに動かない。
        if wf.rstrip().rfind("- name:") > wf.rfind("どこかで落ちたら記録する"):
            out.append("%s の受け皿が最後の段ではありません。"
                       "落ちた段より前に置くと動きません" % name)
    return out


def rule_leftover_cron_does_nothing(ctx):
    """セール用に足した起動が、終わったあとに悪さをしない。

    2026-09-12: 9/11にセールが終わったのに、毎晩 21:01〜21:51 に
    投稿と見張りを6回ずつ起こしていた。
    セール用に足した cron（毎時 0,10,30,40,50分）が残っていて、
    山場の判定を外れたあとは planFor に素通りし、
    「その時間の通常の予定」をそのまま実行していた。

    git を送り返す段が毎晩ぶつかって落ちていたのは、これが原因。
    見えていたのは「落ちた」という結果だけで、
    余分に6回起きていることには気づいていなかった。

    通常の予定は自分の分（:20）でだけ動かす。
    ほかの分に来た cron は、山場でなければ用が無い。
    """
    import os
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "ops", "cron-worker.js")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        src = f.read()
    if "minute === 20 ? planFor" not in src:
        return ["cron-worker.js が、通常の予定を自分の分（:20）に限っていません。"
                "セール用に足した起動が残ると、1晩に6回ずつ実行されます"
                "（2026-09-12）"]
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
    ("カードに掲載日を出す", rule_posted_date_shown),
    ("同じ内容を二度出さない", rule_no_duplicate_post),
    ("記録を押せなければ止める", rule_stop_when_unpushed),
    ("素の実行でも枠を記録する", rule_single_run_records_slot),
    ("1回の起動は短く", rule_short_serve_window),
    ("終わったセールを隠す", rule_hide_ended_sales),
    ("セールの商品はイベント名で出す", rule_event_heading),
    ("キャプションの空きを残さない", rule_captions_filled),
    ("自分の広告を自分で踏まない", rule_no_self_click),
    ("編集部の棚を放置しない", rule_featured_fresh),
    ("使わなくなった道を残さない", rule_no_dead_fallback),
    ("同じ店の似た商品を並べない", rule_no_lookalike_pileup),
    ("日次の手順を途中で切らない", rule_daily_routine_is_one_piece),
    ("つながらなかったらやり直す", rule_network_failures_retried),
    ("知らない指定で止まる", rule_unknown_flags_stop),
    ("警告が人に届く", rule_warnings_reach_a_human),
    ("出せなかった投稿を記録に残す", rule_failed_posts_leave_a_trace),
    ("見えないときは出さない", rule_blind_means_stop),
    ("決まりが鳴るかを確かめる", rule_selftest_is_wired),
    ("落ちた理由を読める場所に残す", rule_reports_land_where_readable),
    ("向こうの不調で全部を落とさない", rule_server_errors_are_retried),
    ("書いたものを自動で消さない", rule_hand_writing_is_never_overwritten),
    ("先の予定を切らさない", rule_event_calendar_is_not_empty),
    ("落ちたことが必ず見える", rule_failures_are_visible_everywhere),
    ("残った起動が悪さをしない", rule_leftover_cron_does_nothing),
    ("学びを止めない", rule_keep_learning),
]



# ---------------------------------------------------------------- 自己診断
#
# 決まりを足すたびに、私は手で壊して「鳴るか」を確かめてきた。
# だが手で確かめる限り、確かめ忘れた決まりは静かに死ぬ。
#
# 2026-09-07: 実際に死んでいた。reconcile_prices の呼び出しを消しても
# 鳴らない決まりがあった。探していた文字列が、その関数の定義行にも
# 当たっていたため。壊れないことを確かめない検査は、検査ではない。
#
# ここでは、決まりが見ている材料をその場で書き換えて、
# ちゃんと鳴るかを機械で確かめる。ファイルには触らない。
#
# PROBES の各行は「この決まりは、この材料からこの文字列が消えたら
# （または現れたら）鳴るはずだ」という宣言。
# 商品データの壊し方。文字列の抜き差しでは作れないものはここに書く。
MUTATIONS = {
    "リンクを楽天以外にする":
        lambda ps: [dict(p, affiliateUrl="https://example.com/") for p in ps],
    "キャプションを全部空にする":
        lambda ps: [dict(p, caption="") for p in ps],
    "同じ店・同じ値段を3件作る":
        lambda ps: ps + [dict(ps[0], id="probe%d" % i, shop="probe店",
                              price=12345, hidden=False) for i in range(3)],
}


PROBES = {
    "「どんな商品？」が出る": [("build_py", "どんな商品？", "remove")],
    "送料は楽天のデータを正とする": [("fetch_py", "merge_shipping_tag", "remove")],
    "同じ内容を二度出さない": [("threads_py", "recent_posts", "remove")],
    "素の実行でも枠を記録する": [("threads_py", "take_lock", "remove")],
    "使わなくなった道を残さない": [("threads_py", "def shapes(", "add")],
    "日次の手順を途中で切らない": [("daily_routine", "1〜9すべて", "remove")],
    "知らない指定で止まる": [("threads_py", "知らない指定です", "remove"),
                             ("fetch_py", "知らない指定です", "remove")],
    "つながらなかったらやり直す": [("fetch_py", "urllib.error.URLError", "remove")],
    "警告が人に届く": [("watch_yml", "直していない警告", "remove"),
                       ("build_py", "= reconcile_prices(", "remove")],
    "出せなかった投稿を記録に残す": [("threads_py", '"failed": True', "remove"),
                                     ("threads_yml", "failed_posts.py", "remove")],
    "見えないときは出さない": [("threads_py", 'return 0, "blind"', "remove")],
    "料率を見せない": [("index_html", "料率", "add")],
    "絵文字を使わない": [("index_html", "\U0001F600", "add")],
    "終わったセールを隠す": [("app_js", "saleOver(", "remove")],
    "自分の広告を自分で踏まない": [("fetch_py", "不正クリックになります", "remove")],
    "商品リンクは楽天の公式転送": [("products", "リンクを楽天以外にする", "mutate")],
    "キャプションの空きを残さない":
        [("products", "キャプションを全部空にする", "mutate")],
    "同じ店の似た商品を並べない":
        [("products", "同じ店・同じ値段を3件作る", "mutate")],
    "決まりが鳴るかを確かめる": [("watch_yml", "--selftest", "remove"),
                                 ("rules_py", "def selftest(", "remove")],
    "落ちた理由を読める場所に残す":
        [("watch_yml", "record_failure.py", "remove"),
         ("record_failure_py", "", "empty")],
    "向こうの不調で全部を落とさない":
        [("fetch_py", "500, 502, 503, 504", "remove"),
         ("fetch_py", "class UpstreamError", "remove")],
    "書いたものを自動で消さない":
        [("fetch_py", "def with_prev(", "remove"),
         ("fetch_py", "with_prev(existing.get(pid)", "remove"),
         ("fetch_py", "for _p in existing.values():", "remove")],
    "先の予定を切らさない":
        [("threads_py", "次の日程は、楽天からまだ発表されていません", "remove")],
    "落ちたことが必ず見える":
        [("threads_py", 'r[2] == "failure"', "remove"),
         ("threads_yml", "どこかで落ちたら記録する", "remove"),
         ("watch_yml", "どこかで落ちたら記録する", "remove")],
    "学びを止めない": [],
}


def selftest():
    """決まりが本当に鳴るかを、材料を壊して確かめる。

    鳴らない決まりは、守っているのではなく、見ていないだけ。
    """
    ctx = context()
    by_name = dict(RULES)
    ng = []
    checked = 0
    for name, probes in PROBES.items():
        if not probes:
            continue
        fn = by_name.get(name)
        if fn is None:
            ng.append("%s ── そんな決まりはありません（名前が変わった？）" % name)
            continue
        for key, marker, how in probes:
            if how == "empty":
                broken = dict(ctx)
                broken[key] = ""
                checked += 1
                try:
                    issues = fn(broken)
                except Exception as ex:                        # noqa: BLE001
                    issues = ["検査そのものが失敗: %s" % ex]
                if not issues:
                    ng.append("%s ── %s を空にしても鳴りませんでした"
                              % (name, key))
                continue
            if how == "mutate":
                fn2 = MUTATIONS.get(marker)
                if fn2 is None:
                    ng.append("%s ── 壊し方 %r がありません" % (name, marker))
                    continue
                broken = dict(ctx)
                broken[key] = fn2(ctx.get(key) or [])
                checked += 1
                try:
                    issues = fn(broken)
                except Exception as ex:                        # noqa: BLE001
                    issues = ["検査そのものが失敗: %s" % ex]
                if not issues:
                    ng.append("%s ── %s を「%s」でも鳴りませんでした"
                              % (name, key, marker))
                continue
            base = ctx.get(key)
            if not isinstance(base, str) or (how == "remove" and marker not in base):
                ng.append("%s ── %s に %r が見当たりません。"
                          "決まりが何も見ていない可能性があります"
                          % (name, key, marker))
                continue
            broken = dict(ctx)
            broken[key] = (base.replace(marker, "") if how == "remove"
                           else base + "\n" + marker + "\n")
            checked += 1
            try:
                issues = fn(broken)
            except Exception as ex:                            # noqa: BLE001
                issues = ["検査そのものが失敗: %s" % ex]
            if not issues:
                ng.append("%s ── %s から %r を%sても鳴りませんでした"
                          % (name, key, marker,
                             "消し" if how == "remove" else "足し"))

    missing = [n for n, _ in RULES if n not in PROBES]
    print("自己診断: %d通りの壊し方を試しました。" % checked)
    if ng:
        print("\n⚠️  鳴らない決まりがあります:")
        for x in ng:
            print("   %s" % x)
    if missing:
        print("\n（壊し方を書いていない決まり %d件: %s）"
              % (len(missing), "、".join(missing[:6])))
    if ng:
        return 1
    print("試したものは、すべて鳴りました。")
    return 0

def context():
    """決まりが見る材料を、まとめて読む。"""
    return {
        "products": (load("products.json", {}) or {}).get("products", []),
        "config": load("config.json", {}) or {},
        "build_py": load("build.py", ""),
        "threads_py": load("threads.py", ""),
        "fetch_py": load("fetch_rakuten.py", ""),
        # 手順や覚え書きも ctx から読む。
        # 直接ファイルを開くと、決まりを壊して確かめるのに
        # 本物のファイルを書き換えるしかなくなり、自己診断が作れない。
        "watch_yml": load(".github/workflows/watch.yml", ""),
        "threads_yml": load(".github/workflows/threads.yml", ""),
        "learn_py": load("learn.py", ""),
        "failed_posts_py": load("ops/failed_posts.py", ""),
        "daily_routine": _routine(),
        "index_html": load("index.html", ""),
        "app_js": load("assets/js/app.js", ""),
        "rules_py": load("rules.py", ""),
        "record_failure_py": load("ops/record_failure.py", ""),
        "featured_txt": load("featured.txt", ""),
    }


def run(quiet=False):
    ctx = context()
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
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    n = run()
    print("")
    print("決まり %d件中 %d件を破っています。" % (len(RULES), n) if n
          else "決まり %d件、すべて守れています。" % len(RULES))
    sys.exit(1 if n else 0)

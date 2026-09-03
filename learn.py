# -*- coding: utf-8 -*-
"""日々の投稿から学ぶ。

やることは3つだけ。

  1. 測る    出した投稿の成績を取り、切り口ごとに中央値を出す
  2. 決める  走っている試しがあれば、期限が来たら勝ち負けを決める
  3. 次を出す 差がいちばん大きく、まだ試していないことを次の試しにする

学んだことは learnings.json に残す。
残さないと、次に同じことを一から考え直すことになる。
rules.py の「学びを止めない」が、この台帳が止まっていないかを見張る。

  python3 learn.py            測って、決めて、次を出す
  python3 learn.py --measure  測るだけ（台帳は書き換えない）
"""
import collections
import json
import os
import statistics as st
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
LEDGER = "learnings.json"

# 中央値を比べるとき、これより少ない件数は「まだ分からない」として扱う。
# 3件の中央値でものを決めると、次の3件でひっくり返る。
MIN_N = 8

# 「差がある」と言うための倍率。1.8倍を境にする。
GAP = 1.8


def load(name, default=None):
    p = os.path.join(ROOT, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch():
    """出した投稿とその成績を持ってくる。"""
    sys.path.insert(0, ROOT)
    import threads as T

    uid, token = T.credentials()
    log = [x for x in (load("threads_posted.json", {}) or {}).get("log", [])
           if x.get("id")]
    rows = []
    for x in log:
        try:
            d = T.api("GET", "%s/insights" % x["id"],
                      {"metric": "views,likes,replies"}, token)
        except Exception:                                  # noqa: BLE001
            continue
        g = {m.get("name"): (m.get("values") or [{}])[0].get("value", 0)
             for m in d.get("data", [])}
        # 自分で貼った返信は、反応ではない。
        # tip も記事へ誘う返信を必ず1本付けているので、同じように引く。
        if (x.get("link") == "reply" or x.get("kind") == "tip") and g.get("replies"):
            g["replies"] = max(0, g["replies"] - 1)
        rows.append((x, g))
    return rows


def followers():
    sys.path.insert(0, ROOT)
    import threads as T

    uid, token = T.credentials()
    try:
        d = T.api("GET", "%s/threads_insights" % uid,
                  {"metric": "followers_count"}, token)
        for m in d.get("data", []):
            if m.get("name") == "followers_count":
                return (m.get("total_value") or {}).get("value")
    except Exception:                                      # noqa: BLE001
        pass
    return None


def cuts(rows):
    """切り口ごとに、表示の中央値を出す。"""
    # 締め・入口・売場は商品の投稿にしか付かない。
    # 全部を混ぜて数えると、値の無い tip や schedule が
    # 「（なし）」という一群になり、どの切り口でも同じ差が出る。
    # それは「商品の投稿が弱い」を4回言い換えているだけで、
    # 台帳が同じ話で埋まる。商品どうしで比べる。
    dims = {
        "投稿の種類": (lambda x: x.get("kind") or "?", None),
        "リンクの位置": (lambda x: x.get("link") or "?", None),
        "締めの型": (lambda x: x.get("cta_type") or "（なし）", "product"),
        "入口の型": (lambda x: x.get("hook_type") or "（なし）", "product"),
        "売場": (lambda x: x.get("category") or "（なし）", "product"),
        "時刻": (lambda x: str(x.get("slot")), None),
    }
    out = {}
    for name, (f, only) in dims.items():
        b = collections.defaultdict(list)
        for x, g in rows:
            if only and x.get("kind") != only:
                continue
            b[f(x)].append(g.get("views", 0) or 0)
        out[name] = {k: {"n": len(v), "median": st.median(v),
                         "mean": round(sum(v) / len(v), 1), "max": max(v)}
                     for k, v in b.items()}
    return out


def report(rows, cut):
    print("投稿 %d件 / 表示 合計%d / いいね %d / 返信（自分の分を除く）%d"
          % (len(rows),
             sum(g.get("views", 0) for _, g in rows),
             sum(g.get("likes", 0) for _, g in rows),
             sum(g.get("replies", 0) for _, g in rows)))
    f = followers()
    if f is not None:
        print("フォロワー %d人" % f)
    print()
    for name, b in cut.items():
        rank = sorted(b.items(), key=lambda kv: -kv[1]["median"])
        line = "  ".join("%s %s(n=%d)" % (k, v["median"], v["n"])
                         for k, v in rank[:6])
        print("  %-8s %s" % (name, line))


def judged(cut, name):
    """その切り口で、件数が足りていて差の大きい2つを返す。無ければ None。"""
    b = {k: v for k, v in (cut.get(name) or {}).items() if v["n"] >= MIN_N}
    if len(b) < 2:
        return None
    rank = sorted(b.items(), key=lambda kv: -kv[1]["median"])
    hi, lo = rank[0], rank[-1]
    if lo[1]["median"] <= 0:
        lo = (lo[0], dict(lo[1], median=0.5))
    if hi[1]["median"] / lo[1]["median"] < GAP:
        return None
    return hi, lo


def main():
    args = sys.argv[1:]
    rows = fetch()
    if not rows:
        print("成績を取れた投稿がありません。")
        return
    cut = cuts(rows)
    report(rows, cut)
    if "--measure" in args:
        return

    led = load(LEDGER) or {"_readme": "投稿から学んだことの台帳。"
                                      "learn.py が書き、rules.py が止まっていないか見張る。",
                           "findings": [], "experiments": []}
    today = datetime.now().strftime("%Y-%m-%d")
    led["lastRun"] = today
    led["snapshot"] = {"date": today, "posts": len(rows),
                       "views": sum(g.get("views", 0) for _, g in rows),
                       "replies": sum(g.get("replies", 0) for _, g in rows),
                       "followers": followers(),
                       "cuts": cut}

    # ---- 分かったことを拾う ----
    known = {f["dim"] for f in led["findings"]}
    for name in cut:
        if name in known:
            continue
        j = judged(cut, name)
        if not j:
            continue
        (hk, hv), (lk, lv) = j
        led["findings"].append({
            "date": today, "dim": name,
            "claim": "%s では「%s」が「%s」より %.1f倍 見られている"
                     % (name, hk, lk, hv["median"] / max(lv["median"], 0.5)),
            "evidence": "中央値 %s(n=%d) 対 %s(n=%d)"
                        % (hv["median"], hv["n"], lv["median"], lv["n"]),
            "action": "未着手",
        })
        print("\n新しく分かったこと: %s" % led["findings"][-1]["claim"])

    # ---- 走っている試しの決着をつける ----
    for e in led["experiments"]:
        if e.get("status") != "running":
            continue
        if today < e["until"]:
            print("\n試し %s は %s まで。いま %s、目標 %s。"
                  % (e["id"], e["until"], e["metric"], e["target"]))
            continue
        now = st.median([g.get("views", 0) for x, g in rows
                         if x["at"][:10] >= e["started"]] or [0])
        e["result"] = {"median": now, "baseline": e["baseline"],
                       "target": e["target"]}
        e["status"] = "keep" if now >= e["target"] else "revert"
        e["decided"] = today
        print("\n試し %s の結果: 中央値 %s（もとは %s、目標 %s）→ %s"
              % (e["id"], now, e["baseline"], e["target"],
                 "続ける" if e["status"] == "keep" else "戻す"))

    save(LEDGER, led)
    running = [e for e in led["experiments"] if e.get("status") == "running"]
    if not running:
        print("\n走っている試しがありません。次の一手を決めてください。")
        todo = [f for f in led["findings"] if f["action"] == "未着手"]
        for f in todo:
            print("  手つかず: %s" % f["claim"])


if __name__ == "__main__":
    main()

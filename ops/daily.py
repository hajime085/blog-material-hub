#!/usr/bin/env python3
"""日次の手順1〜8を1回で回して、判断に要ることだけを短く返す。

2026-09-13: 利用者から「トークン消費を抑えたい」と言われた。
手順1〜8は、実行する回数より出力の長さでトークンを食っていた。
ビルドのログ、学習の表、検査の一覧をまるごと読んでいたが、
私が判断に使うのは「何が壊れているか」「何を書けばいいか」だけ。

使いかた:
    python3 ops/daily.py            1〜8を回して要点だけ出す
    python3 ops/daily.py --push     書き終えたあと、ビルドして push する

要点に出ないものは、壊れていないということ。
ただし「見えていない」を「起きていない」と読み替えないこと（2026-09-12）。
ここに出す項目を減らすときは、その項目が別の場所で必ず見えることを先に確かめる。
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, timeout=900):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       timeout=timeout, shell=isinstance(cmd, str))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def load(name, default=None):
    p = os.path.join(ROOT, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def section(title):
    print("\n■ " + title)


def gaps():
    """書く必要がある商品。キャプション／商品理解が無いもの。"""
    ps = (load("products.json", {}) or {}).get("products", [])
    feat = {x.get("itemCode") for x in
            ((load("featured.json", {}) or {}).get("items") or [])}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    soon = (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")

    def missing(p):
        return not p.get("marketing") and not (p.get("description") or "").strip()

    out = []
    for p in ps:
        if p.get("hidden"):
            continue
        st = p.get("startTime") or ""
        en = (p.get("endTime") or "").strip()
        need = []
        if not (p.get("caption") or "").strip():
            need.append("キャプション")
        live = not (st > now) and not (en and en < now)
        if missing(p) and (live or (st > now and st <= soon)
                           or p.get("itemCode") in feat):
            need.append("商品理解")
        if need:
            out.append((p, need))
    return out


def main():
    push = "--push" in sys.argv[1:]

    if push:
        code, log = run(["python3", "build.py"])
        warn = [l for l in log.splitlines() if l.startswith("⚠")]
        code2, log2 = run(["python3", "rules.py"])
        tail = log2.strip().splitlines()[-1] if log2.strip() else ""
        print("ビルド: " + ("警告%d件" % len(warn) if warn else "警告なし"))
        for l in warn:
            print("  " + l)
        print("決まり: " + tail)
        if warn or code2 != 0:
            print("→ 直してから、もう一度 --push してください。push していません。")
            return 1
        run("git add -A")
        # モデル名は書かない。どのモデルで回しても名乗りがずれないように。
        code, out = run('git commit -q -m "chore: 今日の分"')
        for i in range(3):
            c1, _ = run("git pull --rebase -q")
            c2, o2 = run("git push -q")
            if c2 == 0:
                print("push しました。")
                return 0
        print("push できませんでした:\n" + o2[-400:])
        return 1

    print("いま " + datetime.now().strftime("%Y-%m-%d %H:%M"))

    # 1. 取り込む
    code, out = run("git pull --rebase -q")
    if code != 0:
        section("git pull に失敗")
        print(out[-500:])

    # 2. ビルドの警告
    code, log = run(["python3", "build.py"])
    detail = []
    lines = log.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("⚠"):
            detail.append(l)
            detail += [x for x in lines[i + 1:i + 4] if x.startswith("   ")]
    m = re.search(r"商品 (\d+)件", log)
    section("ビルド（掲載 %s件）" % (m.group(1) if m else "?"))
    if detail:
        for l in detail:
            print("  " + l)
    else:
        print("  警告なし")

    # 3-4. 書くもの
    g = gaps()
    section("書くもの %d件" % len(g))
    for p, need in g[:40]:
        print("  %s [%s] %s ¥%s→¥%s ★%s/%s件 %s"
              % (p["id"], "・".join(need), p.get("category"),
                 p.get("listPrice") or "-", p.get("price"),
                 p.get("reviewAverage"), p.get("reviewCount"),
                 (p.get("startTime") or "")[:16]))
        print("     " + (p.get("rawTitle") or "")[:90])
    if len(g) > 40:
        print("  ……ほか%d件" % (len(g) - 40))

    # 6. 予定実行と、落ちた記録
    code, log = run(["python3", "threads.py", "--doctor"])
    section("予定実行")
    # 見出し付きの警告だけは、続く行ごと残す
    out_lines, grab = [], False
    for l in log.splitlines():
        if l.startswith("⚠"):
            grab = True
            out_lines.append(l)
            continue
        if grab and l.startswith("   "):
            out_lines.append(l)
            continue
        grab = False
        if ("直近" in l or "すべて成功" in l or "重複" in l
                or "失敗した記録はありません" in l):
            out_lines.append(l)
    for l in out_lines:
        print("  " + l.strip())
    fm = os.path.join(ROOT, "ops", "failures.md")
    if os.path.exists(fm):
        with open(fm, encoding="utf-8") as f:
            heads = [l for l in f if l.startswith("## ")]
        print("  ops/failures.md に %d件: %s"
              % (len(heads), " / ".join(h[3:].strip() for h in heads[:3])))

    # 7. 学習（表は出さず、数字と試しの状況だけ）
    code, log = run(["python3", "learn.py"], timeout=1500)
    section("学習")
    for l in log.splitlines():
        s = l.strip()
        if (s.startswith("投稿 ") or s.startswith("フォロワー")
                or s.startswith("試し ") or s.startswith("新しく分かったこと")
                or s.startswith("⚠") or "数字をもらってから" in s):
            print("  " + s)

    # 8. 決まりと自己診断
    code, log = run(["python3", "rules.py"])
    detail = []
    lines = log.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("⚠"):
            detail.append(l)
            detail += [x for x in lines[i + 1:i + 3]
                       if x.startswith("   ") and not x.strip().startswith("✓")]
    last = log.strip().splitlines()[-1] if log.strip() else "?"
    code2, log2 = run(["python3", "rules.py", "--selftest"])
    st = [l for l in log2.splitlines() if l.startswith("⚠") or "鳴らない" in l
          or l.startswith("   ") and "──" in l]
    section("決まり")
    print("  " + last)
    for l in detail:
        print("  " + l)
    print("  自己診断: " + ("鳴らない決まりあり" if code2 != 0 else "すべて鳴る"))
    for l in st:
        print("  " + l.strip())

    print("\n次: 書くものがあれば pitch.py --apply とキャプションを入れて、"
          "python3 ops/daily.py --push")
    return 0


if __name__ == "__main__":
    sys.exit(main())

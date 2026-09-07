#!/usr/bin/env python3
"""落ちた理由を、あとから読める場所に書き残す。

2026-09-07: 見張りが落ちる理由が分からず、実行の要約へ出すようにした。
2026-09-08: それでも分からなかった。要約は管理者権限がないと読めず、
APIから取れる注釈は「Process completed with exit code 1」だけだった。
報告を、読めない場所に置いていた。価格ずれのときと同じ形の間違い。

だからリポジトリの中に置く。git pull すれば読める。
"""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "ops", "failures.md")
KEEP = 15


def main():
    log = sys.argv[1] if len(sys.argv) > 1 else ".watch_log.txt"
    what = sys.argv[2] if len(sys.argv) > 2 else "見張り"
    p = os.path.join(ROOT, log)
    tail = "（ログが残っていません）"
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            tail = "".join(f.readlines()[-40:]).rstrip()

    when = (datetime.datetime.utcnow()
            + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    entry = "## %s JST — %s が落ちました\n\n```\n%s\n```\n" % (when, what, tail)

    old = ""
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = f.read()
    # 新しいものを上に。古いものは KEEP 件で切る。
    body = old.split("\n## ")
    head = body[0] if not body[0].startswith("## ") else ""
    rest = ["## " + x for x in body[1:]] if len(body) > 1 else []
    if old.startswith("## "):
        rest = ["## " + x for x in old.split("\n## ")][:]
        rest[0] = old.split("\n## ")[0]
        head = ""
    keep = rest[:KEEP - 1]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# 落ちた記録\n\n"
                "自動実行が落ちたときの、末尾40行。新しいものが上。\n"
                "実行の要約は管理者権限がないと読めないので、ここに置く。\n\n")
        f.write(entry + "\n")
        for x in keep:
            if x.strip() and not x.startswith("# 落ちた記録"):
                f.write(x.rstrip() + "\n\n")
    print("ops/failures.md に書きました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

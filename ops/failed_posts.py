#!/usr/bin/env python3
"""その日、出そうとして出せなかった投稿を数えて出す。

2026-09-07: 20:41 の1本が出なかった。起動はしていたのに、
なぜ出なかったのかを調べる材料が何も残っていなかった。
失敗は stderr に流れるだけで、手順は緑のまま終わる。
価格ずれと同じで、誰も読まないところに置いても直らない。

手順から呼んで、実行の要約に出す。
失敗が1件でもあれば終了コード 1 を返す（手順はそれを見て警告を出す）。
"""
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    path = os.path.join(ROOT, "threads_posted.json")
    if not os.path.exists(path):
        print("記録がありません。")
        return 0
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    bad = [x for x in doc.get("log", [])
           if x.get("failed") and (x.get("at") or "").startswith(today)]
    if not bad:
        print("出せなかった投稿はありません。")
        return 0
    print("## 出せなかった投稿 %d件" % len(bad))
    print("")
    for x in bad:
        print("- `%s` %s — %s"
              % (x.get("at"), x.get("key"), (x.get("error") or "")[:120]))
    return 1


if __name__ == "__main__":
    sys.exit(main())

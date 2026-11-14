#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Threads の「最近の投稿」を、サイト表示用に取り込む。

TOP の「Threadsの最近の投稿」欄（build.py の render_threads_section）の
材料を作る。Threads 公式にはタイムラインの埋め込みが無い（個別投稿の
埋め込みだけ）ので、API で自分の投稿を取りに行き、静的な JSON に
落としておいて、ビルド時に描く。

■ なぜ投稿（threads.py）の実行に相乗りするのか

見張り（watch.yml）には Threads の鍵が渡っていない。鍵があるのは
投稿（threads.yml）と測定（learn.yml）だけ。ワークフローを足さずに
回すため、投稿の記録を送るついで（threads.py の push_state）に
ここを呼ぶ。単体でも実行できる（手元では .env の鍵を使う）。

■ 壊さないための決まり

- 取れなければ黙って終わる（終了コード 0）。前回の JSON は残す。
  投稿も見張りも、この取り込みの失敗で止めてはいけない。
- 載せるのはトップレベルの投稿だけ。/{user}/threads はトップレベル
  だけを返す仕様だが、リンク置き場の自己リプライが万一混ざっても
  出さないよう、URL だけの投稿と空の投稿は落とす。
- 0 件しか取れなかったときも前回を残す。空の材料で欄を消すのは
  build.py 側の「古い・無いなら出さない」に任せるが、通信の一時的な
  失敗で材料を空に潰すのは、こちらの責任で避ける。
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = "threads_recent.json"
JST = timezone(timedelta(hours=9))
PROFILE_URL = "https://www.threads.com/@yasumiru_web"
LIMIT = 6        # 欄に出す件数
FETCH_N = 12     # 除外で減っても LIMIT 件そろうよう、多めに取る
FIELDS = "id,timestamp,text,permalink,media_type,username"


def _link_only(text):
    """URL だけの投稿か（リンク置き場の自己リプライの形）。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return bool(lines) and all(
        ln.startswith(("http://", "https://")) for ln in lines)


def pick_posts(data, limit=LIMIT):
    """API の応答から、欄に出せる投稿だけを新しい順に拾う。"""
    posts = []
    for it in data or []:
        text = (it.get("text") or "").strip()
        if not text or _link_only(text):
            continue
        url = (it.get("permalink") or "").strip()
        if not url:
            # 飛び先の無いカードは出さない（タップで元の投稿へ、が欄の約束）。
            continue
        posts.append({
            "id": it.get("id") or "",
            "timestamp": it.get("timestamp") or "",
            "text": text,
            "permalink": url,
            "media_type": it.get("media_type") or "TEXT",
        })
        if len(posts) >= limit:
            break
    return posts


def refresh(limit=LIMIT):
    """取り込み直す。成功したら True。失敗しても例外は出さず False。

    鍵が無い環境（見張りなど）で呼ばれても、黙って False を返す。
    """
    try:
        import threads as T
        uid, token = T.credentials()
    except SystemExit:
        # 鍵が無い。材料は作れないが、失敗ではない（呼ばれた環境の都合）。
        return False
    except Exception as ex:                              # noqa: BLE001
        print("  最近の投稿の取り込みを飛ばします: %s" % ex, file=sys.stderr)
        return False
    try:
        d = T.api("GET", "%s/threads" % uid,
                  {"fields": FIELDS, "limit": FETCH_N}, token, retry=1)
    except Exception as ex:                              # noqa: BLE001
        print("  Threadsの最近の投稿を取れませんでした: %s" % ex,
              file=sys.stderr)
        return False
    posts = pick_posts(d.get("data"), limit)
    if not posts:
        print("  最近の投稿が0件でした。前回の材料を残します。",
              file=sys.stderr)
        return False
    posts.sort(key=lambda p: p["timestamp"], reverse=True)
    obj = {
        "fetched_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "username": "yasumiru_web",
        "profile_url": PROFILE_URL,
        "posts": posts,
    }
    with open(os.path.join(ROOT, OUT), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return True


def main():
    # 呼び出し側（投稿・見張り）を止めないため、成否によらず 0 で終わる。
    if refresh():
        print("Threadsの最近の投稿を取り込みました（%s）。" % OUT)
    else:
        print("Threadsの最近の投稿は取り込めませんでした。"
              "前回の材料があれば、それが使われます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

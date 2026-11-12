#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_selection.py — 掲載台帳（ストリーム方式）の自動メンテナンス（見張り便用）

2026-11-11 に掲載モデルを「通常のサイト方式（蓄積型ストリーム）」へ移行した。
旧方式は、見張りのたびに選定を回し直して curated.json を丸ごと入れ替える
方式で、基準から外れた商品はその場でページごと消えていた。そのため
掲載が 16件 → 11件に目減りし、サイトに記事が蓄積されなかった。

新方式（このスクリプトの仕事）:
  入る門は不変。select_strict.evaluate()（選定の正本）の全ゲート
  （観測30日以上／期間最安／中央値比-20%以上／レビュー100件以上・
  ★3.8以上／送料無料／キャプション必須）を通った商品だけが、
  その日の新規掲載として台帳（listings.json）に入る。
  出ていく規則だけが違う:
    (a) 新規に全ゲート通過した商品を台帳へ追加する（掲載日＝当日）
    (b) プール（products.json）に残っている掲載中商品は、スナップ
        ショットを最新の観測値に更新する（価格は生き続ける）
    (c) 掲載から30日を超えた商品を台帳から外す（期限切れ）
    (d) 価格が戻っても落選はしない。%OFF や「検証済み」の表示を
        外すかどうかは、ビルド時に build.py が現況で判定する。

    watch.yml の順序: 価格を見に行く → 台帳を手入れする（このスクリプト）
                      → サイトを作り直す → 反映する

記録（あとから追えることが必須）:
  - ops/selection_log.md に、追加／期限切れを理由つきで追記する。
  - 自動コミットのメッセージ（.run_summary.txt）にも台帳の行を足す。
  - 見送りのときも .run_changed を yes にして、記録だけはコミットに残す。

安全弁:
  - データ異常ガードだけを残す: 商品数・価格履歴の総件数が、前回適用時の
    記録から大きく減っていたら、その回は台帳を一切触らない（取得失敗で
    履歴が欠けた状態で、追加も期限切れもさせない）。
  - 旧方式の「入れ替え量」ガード（8件以上で見送り）は廃止した。
    出ていくのが年齢だけになったので、健全な追加まで巻き添えで
    止まる害のほうが大きい。追加は全ゲート通過が条件で頭打ちになる。
  見送りを無効化して手入れを強行するときは:
    python3 apply_selection.py --force
  （データ異常ガードも含めて無効化する。記録に「強制適用」と残る）

テーマの扱い（2026-11-12 変更）:
  旧方式では evaluate() のフィード上限（10%）に加えて、台帳側でも
  「台帳＋追加分」の合計に同じ物差しを効かせ、超過する追加を落として
  いた。その結果、全ゲート通過の商品（中央値比 -50% のワイドパンツ）が
  ファッションの上限だけで何週間も見送られ続けた。
  新ルール: 全ゲートを通った商品は、テーマにかかわらず掲載する。
  ただし無制限の流入を防ぐため、1回の実行で同じテーマは最大2件まで
  追加し、超えた分は落とさずに次回の実行へ回す（次回もフィードに
  残っていれば、その回に追加される）。evaluate() 側のテーマ上限も
  同日に廃止済み（select_strict.apply_theme_cap は落とさない）。

ゼロ件の日（新規通過なし・期限切れなし）は正常として扱う（スナップ
ショットの更新だけして終わる）。掲載 = 推薦の原則として、枠を埋める
ための掲載はしない。

止め方（ロールバック）:
  - 自動化を止める: watch.yml の「選定を回す」段を消すか、その段の env に
    YASUMIRU_SELECTION_AUTO=0 を付ける。listings.json は最後の状態のまま
    固定される。
  - 旧方式に戻す: listings.json を消すと build.py は curated.json の
    従来選定に戻る（curated.json は移行後も消さずに残してある）。

使い方:
    python3 apply_selection.py [--dry-run] [--force] [--strict]
      --dry-run  何も書かず、追加／期限切れの判定と中身だけ表示する
      --force    見送り（データ異常ガード）を無効化して適用する
      --strict   異常時に終了コード 1 を返す（手動実行・テスト用。
                 既定は見張りを止めないよう 0 で終わる）
"""

import argparse
import json
import os
import sys
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))

sys.path.insert(0, str(ROOT))
import select_strict  # noqa: E402  選定の正本。ゲートはここだけに置く

# ---------------------------------------------------------------- 運用値
# 選定ゲートの基準値（★3.8・-20%・30日・100件）とは別物。
RETENTION_DAYS = 30     # 掲載日からこの日数を超えたら期限切れ
PRODUCTS_SHRINK = 0.8   # 商品数が前回適用時の8割未満: データ異常として見送る
HISTORY_SHRINK = 0.9    # 履歴総件数が前回適用時の9割未満: 同上
# 1回の実行で追加する同一テーマの上限（2026-11-12〜）。
# 全ゲート通過は落とさない代わりに、流入の速度だけを絞る。
MAX_ADD_PER_THEME_PER_RUN = 2

LISTINGS_PATH = ROOT / "listings.json"
CURATED_PATH = ROOT / "curated.json"
LOG_PATH = ROOT / "ops" / "selection_log.md"
LOG_HEADER = """# 選定の自動回し直し 記録

`apply_selection.py` が見張りのたびに追記する。追加／期限切れとその理由、
見送りとその理由を残す。
2026-11-11 から掲載モデルがストリーム方式（掲載したら30日残る）に
変わった。それ以前の記録は旧方式（選定の回し直し）のもの。

"""


def yen(n) -> str:
    return "?" if n is None else "{:,}".format(int(n))


def now_jst() -> datetime:
    return datetime.now(JST)


def today_jst():
    return now_jst().date()


def parse_date(s):
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def sanitize(product: dict) -> dict:
    """スナップショット用に、評価時の一時キー（_m / _theme 等）を除いた
    商品データの複製を返す。"""
    return {k: json.loads(json.dumps(v)) for k, v in product.items()
            if not k.startswith("_")}


# ---------------------------------------------------------------- 理由の文面

def reason_added(entry: dict, was_caption_needed: bool) -> str:
    head = "キャプションが書かれて掲載" if was_caption_needed else "新規に全ゲート通過"
    return (f"{head}（中央値比 -{entry['drop_pct_vs_median']}% / "
            f"レビュー {entry['reviewCount']:,}件 ★{entry['reviewAverage']} / "
            f"観測 {entry['obs_days']}日）")


def reason_expired(entry: dict, today) -> str:
    listed = parse_date(entry.get("listed_on"))
    age = (today - listed).days if listed else "?"
    return f"掲載から{age}日経過（{entry.get('listed_on')} 掲載、{RETENTION_DAYS}日で引退）"


# ---------------------------------------------------------------- 記録まわり

def append_log(text: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text(LOG_HEADER, encoding="utf-8")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(text)


def append_run_summary(lines: list, retitle: str | None = None) -> None:
    """自動コミットのメッセージに台帳の行を足す。見張りの外では何もしない。"""
    path = ROOT / ".run_summary.txt"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if retitle:
        first, _, rest = text.partition("\n")
        if first == "auto: 変更なし":
            text = retitle + "\n" + rest
    if not text.endswith("\n"):
        text += "\n"
    text += "\n" + "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")


def mark_changed() -> None:
    path = ROOT / ".run_changed"
    if path.exists() and path.read_text(encoding="utf-8").strip() == "no":
        path.write_text("yes", encoding="utf-8")


def item_line(mark: str, title: str, note: str) -> str:
    return f"- {mark}: {(title or '')[:60]} — {note}"


# ---------------------------------------------------------------- 台帳の読み書き

def load_listings() -> dict:
    if LISTINGS_PATH.exists():
        return json.loads(LISTINGS_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "model": "stream", "retention_days": RETENTION_DAYS,
            "listings": []}


def seed_from_curated(products_by_id: dict) -> list:
    """listings.json が無いときの初期台帳。curated.json の feed を
    「今日掲載された」ものとして取り込む（移行の正規手順では、
    git 履歴から掲載日を復元した listings.json がリポジトリに
    入っているので、ここは非常用の経路）。"""
    if not CURATED_PATH.exists():
        return []
    curated = json.loads(CURATED_PATH.read_text(encoding="utf-8"))
    today = today_jst().isoformat()
    entries = []
    for en in curated.get("feed") or []:
        p = products_by_id.get(en["id"])
        if p is not None:
            snap = sanitize(p)
        else:
            snap = {"id": en["id"], "title": en.get("title"),
                    "shop": en.get("shop"), "category": en.get("category"),
                    "price": en.get("price"), "caption": en.get("caption"),
                    "affiliateUrl": en.get("affiliateUrl"),
                    "reviewCount": en.get("reviewCount"),
                    "reviewAverage": en.get("reviewAverage"),
                    "image": "", "tags": ["送料無料"] if en.get("free_shipping") else []}
        entries.append({"id": en["id"], "listed_on": today,
                        "theme": en.get("theme"),
                        "last_seen": (snap.get("lastSeen") or today),
                        "snapshot_updated": today, "snapshot": snap})
    return entries


# ---------------------------------------------------------------- 本体

def run(force: bool, dry_run: bool) -> int:
    stamp = now_jst().strftime("%Y-%m-%d %H:%M JST")
    today = today_jst()
    products_doc = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
    products = products_doc["products"]
    products_by_id = {p["id"]: p for p in products}
    history = json.loads((ROOT / "price_history.json").read_text(encoding="utf-8"))
    history_entries = sum(len(v) for v in history.values())

    doc = load_listings()
    seeded = False
    if not doc.get("listings") and not LISTINGS_PATH.exists():
        doc["listings"] = seed_from_curated(products_by_id)
        seeded = True
    entries = {en["id"]: en for en in doc.get("listings") or []}
    old_count = len(entries)
    meta = doc.get("automation") or {}

    result, _decisions = select_strict.evaluate(products, history)
    feed = result["feed"]
    feed_by_id = {en["id"]: en for en in feed}
    old_caption_needed = {e["id"] for e in result.get("caption_needed") or []}
    # caption_needed の「前回」判定の補助: 前回 curated.json があれば
    # その caption_needed も見る（理由文の出し分けだけに使う）。
    if CURATED_PATH.exists():
        try:
            prev_curated = json.loads(CURATED_PATH.read_text(encoding="utf-8"))
            old_caption_needed |= {e["id"] for e in prev_curated.get("caption_needed") or []}
        except Exception:
            pass

    # ---- 安全弁（データ異常ガード） --------------------------------------
    holds = []
    if not force and meta:
        base_products = meta.get("products_count")
        base_history = meta.get("history_entries")
        if base_products and len(products) < base_products * PRODUCTS_SHRINK:
            holds.append(f"商品数が前回適用時 {base_products}件 → {len(products)}件に"
                         "減った（データ異常の疑い）")
        if base_history and history_entries < base_history * HISTORY_SHRINK:
            holds.append(f"価格履歴の総件数が前回適用時 {base_history:,}件 → "
                         f"{history_entries:,}件に減った（データ異常の疑い）")

    if holds:
        head = f"## {stamp} — 見送り（台帳は {old_count}件のまま）"
        body = [head, ""]
        body += [f"- 見送りの理由: {h}" for h in holds]
        body.append("")
        text = "\n".join(body)
        print("⚠ 台帳の手入れを見送りました: " + " / ".join(holds))
        if not dry_run:
            append_log(text)
            append_run_summary(
                [f"台帳: ⚠ 見送りました（{' / '.join(holds)}）"])
            mark_changed()
        return 0

    # ---- (c) 期限切れ ------------------------------------------------------
    expired = []
    for pid, en in list(entries.items()):
        listed = parse_date(en.get("listed_on"))
        if listed and (today - listed).days > RETENTION_DAYS:
            expired.append(pid)
    expired_lines = []
    for pid in expired:
        en = entries.pop(pid)
        expired_lines.append(item_line(
            "期限切れ", (en.get("snapshot") or {}).get("title"),
            reason_expired(en, today)))

    # ---- (a) 追加 ----------------------------------------------------------
    # テーマでは落とさない（2026-11-12〜）。全ゲート通過は全件が追加候補。
    # ただし1回の実行で同じテーマは MAX_ADD_PER_THEME_PER_RUN 件まで。
    # 超えた分は「見送り」ではなく次回送り（deferred）で、記録に残す。
    # 並びはフィードの順（下落率の降順インターリーブ）のまま採る。
    added = []
    deferred = []
    per_theme = Counter()
    for fe in feed:
        pid = fe["id"]
        if pid in entries:
            continue
        theme = fe.get("theme")
        if per_theme[theme] >= MAX_ADD_PER_THEME_PER_RUN:
            deferred.append(pid)
            continue
        per_theme[theme] += 1
        added.append(pid)

    added_lines = []
    for pid in added:
        fe = feed_by_id[pid]
        p = products_by_id[pid]
        entries[pid] = {
            "id": pid,
            "listed_on": today.isoformat(),
            "theme": fe.get("theme"),
            "last_seen": p.get("lastSeen") or today.isoformat(),
            "snapshot_updated": today.isoformat(),
            "snapshot": sanitize(p),
        }
        added_lines.append(item_line(
            "追加", fe["title"], reason_added(fe, pid in old_caption_needed)))
    cap_lines = [item_line("次回送り", feed_by_id[pid]["title"],
                           f"テーマ別の追加上限（1回{MAX_ADD_PER_THEME_PER_RUN}件）のため"
                           f"（テーマ: {feed_by_id[pid].get('theme')}）")
                 for pid in deferred]

    # ---- (b) スナップショット更新 ------------------------------------------
    refreshed = 0
    for pid, en in entries.items():
        if pid in added:
            continue
        p = products_by_id.get(pid)
        if p is None:
            continue  # プールに無い = 凍結。スナップショットはそのまま
        en["snapshot"] = sanitize(p)
        en["last_seen"] = p.get("lastSeen") or en.get("last_seen") or ""
        en["snapshot_updated"] = today.isoformat()
        refreshed += 1

    caption_note = (f"caption_needed: {len(result['caption_needed'])}件"
                    "（ゲート1〜5通過・キャプションを書けば掲載候補）")

    print(f"台帳: 現在 {old_count}件 → {len(entries)}件"
          f"（追加 {len(added)}件 / 次回送り {len(deferred)}件 / "
          f"期限切れ {len(expired)}件 / "
          f"スナップショット更新 {refreshed}件）")
    for line in added_lines + cap_lines + expired_lines:
        print("  " + line)

    # ---- 記録と書き出し -----------------------------------------------------
    if seeded:
        head = f"## {stamp} — 初期化（curated.json から台帳を起こした。掲載 {len(entries)}件）"
    elif added or expired:
        head = (f"## {stamp} — 適用（追加 {len(added)}件／期限切れ {len(expired)}件、"
                f"掲載 {old_count}件 → {len(entries)}件）"
                + (" ※強制適用" if force else ""))
    else:
        head = (f"## {stamp} — 更新（追加・期限切れなし。"
                f"スナップショット {refreshed}件を最新化、掲載 {len(entries)}件）")
    body = [head, ""]
    body += added_lines + cap_lines + expired_lines
    body.append(f"- スナップショット更新: {refreshed}件")
    body.append("- " + caption_note)
    body.append("")
    text = "\n".join(body)

    if dry_run:
        print("（ドライラン: ファイルは書いていません）")
        print(text)
        return 0

    doc["listings"] = [entries[pid] for pid in
                       sorted(entries, key=lambda x: (entries[x].get("listed_on") or "",
                                                      x))]
    doc["automation"] = {
        "applied_at": now_jst().isoformat(timespec="seconds"),
        "products_count": len(products),
        "history_entries": history_entries,
        "added": added,
        "expired": expired,
        "forced": bool(force),
    }
    LISTINGS_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    append_log(text)

    summary = [f"台帳: 追加 {len(added)}件／期限切れ {len(expired)}件"
               f"（掲載 {old_count}件 → {len(entries)}件）"]
    for pid in added:
        e = feed_by_id[pid]
        summary.append(f"  + {(e['title'] or '')[:38]}  ¥{yen(e['price'])}"
                       f"（中央値比 -{e['drop_pct_vs_median']}%）")
    for pid in expired:
        summary.append(f"  − 期限切れ（{RETENTION_DAYS}日経過）")
    append_run_summary(summary,
                       retitle=f"auto: 台帳のみ変更（追加{len(added)}件／期限切れ{len(expired)}件）"
                       if (added or expired) else None)
    if added or expired:
        # 追加・期限切れは「変更なし」で捨てられないようにする。
        # スナップショットだけの更新は、価格側の変更と一緒に流れる。
        mark_changed()
    print("台帳を更新しました")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ヤスミル掲載台帳（ストリーム方式）の自動メンテナンス")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    if os.environ.get("YASUMIRU_SELECTION_AUTO") == "0" and not args.force:
        print("台帳の自動メンテナンスは止められています（YASUMIRU_SELECTION_AUTO=0）。"
              "listings.json はそのままです。")
        return 0
    try:
        return run(force=args.force, dry_run=args.dry_run)
    except Exception:
        traceback.print_exc()
        print("⚠ 台帳の手入れで予期しないエラー。前回の台帳（listings.json）のままにします。")
        if not args.dry_run:
            try:
                append_log(f"## {now_jst().strftime('%Y-%m-%d %H:%M JST')} — "
                           "エラーで見送り（listings.json は変更していない）\n\n"
                           "```\n" + traceback.format_exc()[-1500:] + "\n```\n")
                append_run_summary(["台帳: ⚠ エラーで見送りました"
                                    "（ops/selection_log.md に記録）"])
                mark_changed()
            except Exception:
                pass
        return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())

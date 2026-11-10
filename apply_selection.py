#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_selection.py — 厳格選定の自動適用（見張り便用）

select_strict.evaluate()（選定の正本）を現行データに適用し、結果を
curated.json に反映する。2026-11-10 までは選定は手作業で、curated.json は
固定スナップショットだった。見張り（fetch_rakuten.py --watch）が価格を
取り直すたびに、ここで選定も回し直す。

    watch.yml の順序: 価格を見に行く → 選定を回す（このスクリプト）
                      → サイトを作り直す → 反映する

記録（あとから追えることが必須）:
  - ops/selection_log.md に、適用・見送りのたびに追記する
    （追加／落選と、その理由＝どのゲートで落ちたか、まで書く）。
  - 自動コミットのメッセージ（.run_summary.txt）にも選定の行を足す。
  - 見送りのときも .run_changed を yes にして、記録だけはコミットに残す。

安全弁（黙って大きく入れ替わらないための歯止め）:
  - データ異常ガード: 商品数・価格履歴の総件数が、前回「適用した」ときの
    記録から大きく減っていたら見送る（取得失敗で履歴が欠けた状態で
    大量落選させないため）。
  - 大量落選ガード: 落選が現在の掲載の半分以上（かつ5件以上）なら見送る。
    掲載が0件になる場合も見送る。
  - 入れ替え量: 追加+落選が 6〜7件なら適用して「要確認」を付ける。
    8件以上なら見送って、提案（追加・落選と理由）を記録に残す。
  見送った提案は、次の回のデータで毎回判定し直す（自動では適用しない）。
  furu / マル が中身を確認して適用してよいと判断したら、手で
    python3 apply_selection.py --force
  を回す（見送りを無効化して適用する。データ異常ガードも含む。
  適用したことは記録に「強制適用」と残る）。

ゼロ件の日（新規通過なし・落選なし）は正常として扱う（何もしない）。
掲載 = 推薦の原則として、枠を埋めるための掲載はしない。

止め方（ロールバック）:
  - 自動化を止める: watch.yml の「選定を回す」段を消すか、その段の env に
    YASUMIRU_SELECTION_AUTO=0 を付ける。curated.json は最後に適用された
    状態のまま固定スナップショットに戻る。
  - 選定結果を巻き戻す: curated.json を git で任意のコミットの版に戻す。
  - 厳格選定そのものを外す: curated.json を消すと build.py は従来どおり
    全件ビルドに戻る（最終手段。掲載 = 推薦 の担保が外れる）。

使い方:
    python3 apply_selection.py [--dry-run] [--force] [--strict]
      --dry-run  何も書かず、適用／見送りの判定と中身だけ表示する
      --force    見送り（安全弁）を無効化して適用する
      --strict   異常時に終了コード 1 を返す（手動実行・テスト用。
                 既定は見張りを止めないよう 0 で終わる）
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))

sys.path.insert(0, str(ROOT))
import select_strict  # noqa: E402  選定の正本。ゲートはここだけに置く

# ---------------------------------------------------------------- 安全弁の値
# 選定ゲートの基準値（★3.8・-20%・30日・100件）とは別物で、
# 「自動でどこまで入れ替えてよいか」の運用値。変えるときは記録に残すこと。
CHURN_NOTICE = 6        # 追加+落選がこれ以上: 適用するが「要確認」を付ける
CHURN_HOLD = 8          # 追加+落選がこれ以上: 見送る
MASS_DROP_MIN = 5       # 大量落選ガードの下限件数
PRODUCTS_SHRINK = 0.8   # 商品数が前回適用時の8割未満: データ異常として見送る
HISTORY_SHRINK = 0.9    # 履歴総件数が前回適用時の9割未満: 同上

LOG_PATH = ROOT / "ops" / "selection_log.md"
LOG_HEADER = """# 選定の自動回し直し 記録

`apply_selection.py` が見張りのたびに追記する。追加／落選とその理由
（どのゲートで落ちたか）、見送りとその理由を残す。
サイトの掲載はこの記録のとおりに変わっている（見送りの回は変わっていない）。

"""


def yen(n) -> str:
    return "?" if n is None else "{:,}".format(int(n))


def now_jst() -> datetime:
    return datetime.now(JST)


# ---------------------------------------------------------------- 理由の文面

def reason_added(entry: dict, was_caption_needed: bool) -> str:
    head = "キャプションが書かれて掲載" if was_caption_needed else "新規に全ゲート通過"
    return (f"{head}（中央値比 -{entry['drop_pct_vs_median']}% / "
            f"レビュー {entry['reviewCount']:,}件 ★{entry['reviewAverage']} / "
            f"観測 {entry['obs_days']}日）")


def reason_removed(pid: str, decisions: dict, product_ids: set) -> str:
    if pid not in product_ids:
        return "products.json に無くなった（取得側の掲載終了・値段戻り・保持期限切れなど）"
    d = decisions.get(pid)
    if d is None:
        return "理由を特定できなかった（要調査）"
    o = d["outcome"]
    m = (d.get("detail") or {}).get("metrics") or {}
    if o == "excluded_supplement":
        return f"サプリ・健康食品の除外対象になった（キーワード: {d['detail'].get('matched')}）"
    if o == "excluded_model_specific":
        return f"型番依存テーマの除外対象になった（キーワード: {d['detail'].get('matched')}）"
    if o == "gate1":
        return f"観測日数が {m.get('obs_days')}日で、30日に届かない"
    if o == "gate2":
        return (f"現在価格 ¥{yen(m.get('current_price'))} が履歴最安 "
                f"¥{yen(m.get('min_price'))} ではなくなった（値上がり）")
    if o == "gate3":
        drop = m.get("drop_vs_median")
        pct = "?" if drop is None else f"{drop * 100:.1f}"
        return f"中央値比の下落が {pct}% で、20%以上に届かない"
    if o == "gate4":
        return (f"レビュー {m.get('review_count')}件・評価 ★{m.get('review_average')} で、"
                "基準（100件以上・★3.8以上）に届かない")
    if o == "gate5":
        return "送料無料でなくなった"
    if o == "caption_needed":
        return "キャプションが無くなった（caption_needed 行き）"
    if o == "series_sibling":
        rep = (d["detail"].get("rep_title") or "")[:30]
        return f"シリーズ圧縮で「{rep}…」の代表に統合された"
    if o == "theme_cap":
        return f"テーマ上限のため（テーマ: {d['detail'].get('theme')}）"
    if o == "feed":
        return "選定では通過している（並び替えのみの変動のはず。要調査）"
    return o


# ---------------------------------------------------------------- 記録まわり

def append_log(text: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text(LOG_HEADER, encoding="utf-8")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(text)


def append_run_summary(lines: list, retitle: str | None = None) -> None:
    """自動コミットのメッセージに選定の行を足す。見張りの外では何もしない。"""
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


# ---------------------------------------------------------------- 本体

def run(force: bool, dry_run: bool) -> int:
    stamp = now_jst().strftime("%Y-%m-%d %H:%M JST")
    products_doc = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
    products = products_doc["products"]
    history = json.loads((ROOT / "price_history.json").read_text(encoding="utf-8"))
    history_entries = sum(len(v) for v in history.values())

    curated_path = ROOT / "curated.json"
    old = None
    if curated_path.exists():
        old = json.loads(curated_path.read_text(encoding="utf-8"))
    old_feed = (old or {}).get("feed") or []
    old_ids = [e["id"] for e in old_feed]
    old_by_id = {e["id"]: e for e in old_feed}
    old_caption_needed = {e["id"] for e in (old or {}).get("caption_needed") or []}

    result, decisions = select_strict.evaluate(products, history)
    new_feed = result["feed"]
    new_ids = [e["id"] for e in new_feed]
    new_by_id = {e["id"]: e for e in new_feed}

    product_ids = {p["id"] for p in products}
    added = [pid for pid in new_ids if pid not in old_by_id]
    removed = [pid for pid in old_ids if pid not in new_by_id]
    churn = len(added) + len(removed)

    added_lines = [item_line("追加", new_by_id[p]["title"],
                             reason_added(new_by_id[p], p in old_caption_needed))
                   for p in added]
    removed_lines = [item_line("落選", old_by_id[p]["title"],
                               reason_removed(p, decisions, product_ids))
                     for p in removed]
    caption_note = (f"caption_needed: {len(result['caption_needed'])}件"
                    "（ゲート1〜5通過・キャプションを書けば掲載候補）")

    print(f"選定: 現在 {len(old_ids)}件 → 新しい選定 {len(new_ids)}件"
          f"（追加 {len(added)}件 / 落選 {len(removed)}件）")
    for line in added_lines + removed_lines:
        print("  " + line)

    # ---- 安全弁 -------------------------------------------------------------
    holds = []
    meta = (old or {}).get("automation") or {}
    base_products = meta.get("products_count", (old or {}).get("total_products"))
    base_history = meta.get("history_entries")
    if old is not None and not force:
        if base_products and len(products) < base_products * PRODUCTS_SHRINK:
            holds.append(f"商品数が前回適用時 {base_products}件 → {len(products)}件に"
                         "減った（データ異常の疑い）")
        if base_history and history_entries < base_history * HISTORY_SHRINK:
            holds.append(f"価格履歴の総件数が前回適用時 {base_history:,}件 → "
                         f"{history_entries:,}件に減った（データ異常の疑い）")
        if len(removed) >= MASS_DROP_MIN and len(removed) * 2 >= len(old_ids):
            holds.append(f"落選が {len(removed)}件で、現在の掲載 {len(old_ids)}件の"
                         "半分以上にあたる")
        if old_ids and not new_ids:
            holds.append("新しい選定が0件になった（掲載が空になる）")
        if churn >= CHURN_HOLD:
            holds.append(f"入れ替えが合計 {churn}件で、上限 {CHURN_HOLD}件以上にあたる")

    # curated.json が無い状態は、厳格選定が外れているということ。
    # 復旧が安全方向なので、入れ替え量の見送りは効かせない（データ異常も
    # 基準が無いので判定しない）。
    restored = old is None

    if holds and not restored:
        head = f"## {stamp} — 見送り（掲載は {len(old_ids)}件のまま）"
        body = [head, ""]
        body += [f"- 見送りの理由: {h}" for h in holds]
        body.append("- 提案の中身（適用しなかった）:")
        body += ["  " + line for line in added_lines + removed_lines]
        body.append("- " + caption_note)
        body.append("")
        text = "\n".join(body)
        print("⚠ 選定を見送りました: " + " / ".join(holds))
        if not dry_run:
            append_log(text)
            append_run_summary(
                [f"選定: ⚠ 見送りました（{' / '.join(holds)}）。"
                 "提案は ops/selection_log.md にあります"])
            mark_changed()
        return 0

    if churn == 0 and meta and not restored:
        print("選定: 入れ替えなし。そのままにします。")
        return 0

    # ---- 適用 ---------------------------------------------------------------
    notice = churn >= CHURN_NOTICE
    new_curated = dict(result)
    new_curated["generated_by"] = "select_strict.py + apply_selection.py（自動選定）"
    new_curated["automation"] = {
        "applied_at": now_jst().isoformat(timespec="seconds"),
        "products_count": len(products),
        "history_entries": history_entries,
        "added": added,
        "removed": removed,
        "forced": bool(force),
    }
    if restored:
        head = f"## {stamp} — 復旧（curated.json が無かったので選定を再生成。掲載 {len(new_ids)}件）"
    elif churn == 0:
        head = f"## {stamp} — 初期化（選定の記録を開始。掲載 {len(new_ids)}件のまま）"
    else:
        head = (f"## {stamp} — 適用（追加 {len(added)}件／落選 {len(removed)}件、"
                f"掲載 {len(old_ids)}件 → {len(new_ids)}件）"
                + (" ※入れ替えが大きめ・要確認" if notice else "")
                + (" ※強制適用" if force and (added or removed) else ""))
    body = [head, ""]
    body += added_lines + removed_lines
    body.append("- " + caption_note)
    body.append("")
    text = "\n".join(body)

    if dry_run:
        print("（ドライラン: ファイルは書いていません）")
        print(text)
        return 0

    curated_path.write_text(json.dumps(new_curated, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    append_log(text)

    summary = [f"選定: 追加 {len(added)}件／落選 {len(removed)}件"
               f"（掲載 {len(old_ids)}件 → {len(new_ids)}件）"
               + (" ※要確認" if notice else "")]
    for p in added:
        e = new_by_id[p]
        summary.append(f"  + {(e['title'] or '')[:38]}  ¥{yen(e['price'])}"
                       f"（中央値比 -{e['drop_pct_vs_median']}%）")
    for p in removed:
        summary.append(f"  − {(old_by_id[p]['title'] or '')[:38]}  "
                       + reason_removed(p, decisions, product_ids))
    append_run_summary(summary,
                       retitle=f"auto: 選定のみ変更（追加{len(added)}件／落選{len(removed)}件）"
                       if churn else None)
    # curated.json を書いた以上、その差分が「変更なし」で捨てられないようにする
    mark_changed()
    print("選定を適用しました" + ("（要確認）" if notice else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ヤスミル厳格選定の自動適用")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    if os.environ.get("YASUMIRU_SELECTION_AUTO") == "0" and not args.force:
        print("選定の自動適用は止められています（YASUMIRU_SELECTION_AUTO=0）。"
              "curated.json はそのままです。")
        return 0
    try:
        return run(force=args.force, dry_run=args.dry_run)
    except Exception:
        traceback.print_exc()
        print("⚠ 選定で予期しないエラー。前回の選定（curated.json）のままにします。")
        if not args.dry_run:
            try:
                append_log(f"## {now_jst().strftime('%Y-%m-%d %H:%M JST')} — "
                           "エラーで見送り（curated.json は変更していない）\n\n"
                           "```\n" + traceback.format_exc()[-1500:] + "\n```\n")
                append_run_summary(["選定: ⚠ エラーで見送りました"
                                    "（ops/selection_log.md に記録）"])
                mark_changed()
            except Exception:
                pass
        return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())

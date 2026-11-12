#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ops/build_item_codes.py — 商品ID → 楽天 itemCode の対応表（item_codes.json）の種作り

price_history.json のキーは itemCode の SHA-1 ハッシュ（fetch_rakuten.product_id）
で、一方向なので、履歴だけが残った商品を楽天APIで引き直すには対応表が要る。

このスクリプトは、手元にある材料から対応表を作る／育てる:
  1. git 履歴の products.json（新しい順に見て、id ごとに最新の記録を採用。
     itemCode とカテゴリの両方が取れる）
  2. 現在の products.json
  3. hand_attic.json のキー（キーが itemCode そのもの。カテゴリは不明）
  4. listings.json のスナップショット（itemCode 入りのもの）

既存の item_codes.json がある場合は上書きせず、欠けている項目だけ足す
（実行時の fetch_rakuten.py が育てた対応表を、古い材料で巻き戻さないため）。

全履歴を持つクローン（furu の Mac 等）で実行すると、掘り出せる範囲が広がる。
浅いクローンでは、残っているスナップショットの範囲だけになる。

使い方:
    python3 ops/build_item_codes.py [--dry-run]
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "item_codes.json"


def product_id(item_code: str) -> str:
    return "r" + hashlib.sha1(item_code.encode("utf-8")).hexdigest()[:9]


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def from_products_doc(doc, table, source, stats):
    for p in doc.get("products") or []:
        pid, code = p.get("id"), p.get("itemCode")
        if not pid or not code:
            continue
        ent = table.setdefault(pid, {"code": code})
        if ent.get("code") != code:
            ent["code"] = code
        if p.get("category") and not ent.get("category"):
            ent["category"] = p["category"]
        stats[source] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description="item_codes.json の種作り")
    ap.add_argument("--dry-run", action="store_true", help="書かずに件数だけ出す")
    args = ap.parse_args()

    table = load_json(OUT_PATH, {})
    before = len(table)
    stats = {"git": 0, "products": 0, "attic": 0, "listings": 0}

    # 1. git 履歴の products.json（新しい順 = id ごとに最新が残る）
    try:
        commits = subprocess.run(
            ["git", "log", "--format=%H", "--all", "--", "products.json"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        ).stdout.split()
    except Exception as ex:  # noqa: BLE001
        print(f"git log を読めませんでした: {ex}", file=sys.stderr)
        commits = []
    for c in commits:
        try:
            blob = subprocess.run(
                ["git", "show", f"{c}:products.json"],
                cwd=ROOT, capture_output=True, timeout=60,
            ).stdout
            doc = json.loads(blob.decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        from_products_doc(doc, table, "git", stats)
    print(f"git 履歴: {len(commits)} スナップショットを読みました")

    # 2. 現在の products.json
    from_products_doc(load_json(ROOT / "products.json", {}), table,
                      "products", stats)

    # 3. hand_attic.json（キーが itemCode）
    attic = load_json(ROOT / "hand_attic.json", {})
    for code in attic:
        pid = product_id(code)
        ent = table.setdefault(pid, {"code": code})
        if not ent.get("code"):
            ent["code"] = code
        stats["attic"] += 1

    # 4. listings.json のスナップショット
    listings = load_json(ROOT / "listings.json", {})
    for en in listings.get("listings") or []:
        snap = en.get("snapshot") or {}
        code = snap.get("itemCode")
        if not code:
            continue
        pid = en.get("id") or product_id(code)
        ent = table.setdefault(pid, {"code": code})
        if snap.get("category") and not ent.get("category"):
            ent["category"] = snap["category"]
        stats["listings"] += 1

    with_cat = sum(1 for v in table.values() if v.get("category"))
    print(f"対応表: {before}件 → {len(table)}件"
          f"（カテゴリ付き {with_cat}件 / 材料のべ {stats}）")
    if args.dry_run:
        print("（ドライラン: item_codes.json は書いていません）")
        return 0
    OUT_PATH.write_text(json.dumps(table, ensure_ascii=False, indent=1,
                                   sort_keys=True), encoding="utf-8")
    print(f"書きました: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

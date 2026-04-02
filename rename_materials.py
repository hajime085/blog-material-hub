#!/usr/bin/env python3
"""
ソザイノ 素材一括リネームスクリプト
=====================================
assets/materials/ 以下の WebP ファイルを
{カテゴリ名}_{連番3桁}.webp にリネームし、
data.json のファイル名も自動で更新します。

使い方：
  1. このファイルをリポジトリのルート（gallery.html と同じ場所）に置く
  2. ターミナルで python3 rename_materials.py を実行
  3. プレビューを確認して「y」を押す
  4. 完了したら git add . && git commit -m "Rename materials" && git push
"""

import os
import json
import shutil
from pathlib import Path

# ===== 設定 =====
MATERIALS_DIR = Path("assets/materials")
DATA_JSON     = Path("data.json")
# ================

def main():
    print("=" * 50)
    print("ソザイノ 素材一括リネームツール")
    print("=" * 50)

    # data.json 読み込み
    if not DATA_JSON.exists():
        print(f"❌ {DATA_JSON} が見つかりません")
        print("   リポジトリのルートフォルダで実行してください")
        return

    with open(DATA_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    materials = data.get("materials", [])
    print(f"\n📦 data.json に登録されている素材: {len(materials)} 件")

    # カテゴリフォルダを取得
    if not MATERIALS_DIR.exists():
        print(f"❌ {MATERIALS_DIR} フォルダが見つかりません")
        return

    categories = sorted([
        d for d in MATERIALS_DIR.iterdir()
        if d.is_dir()
    ])

    if not categories:
        print("❌ カテゴリフォルダが見つかりません")
        return

    print(f"\n📂 検出したカテゴリフォルダ:")
    for cat in categories:
        webps = sorted(cat.glob("*.webp"))
        print(f"   {cat.name}/ : {len(webps)} 枚")

    # リネーム計画を作成
    rename_plan = []  # [(旧パス, 新パス, カテゴリ名, 連番)]

    for cat_dir in categories:
        cat_name = cat_dir.name
        webps    = sorted(cat_dir.glob("*.webp"))

        for idx, old_path in enumerate(webps, start=1):
            new_name = f"{cat_name}_{idx:03d}.webp"
            new_path = cat_dir / new_name

            if old_path.name == new_name:
                continue  # すでに正しい名前ならスキップ

            rename_plan.append((old_path, new_path, cat_name, idx))

    if not rename_plan:
        print("\n✅ すべてのファイルがすでに正しい名前です！")
        return

    # プレビュー表示
    print(f"\n📋 リネーム予定: {len(rename_plan)} 件")
    print("-" * 50)
    for old, new, _, _ in rename_plan[:10]:  # 最初の10件だけ表示
        print(f"  {old.name}  →  {new.name}")
    if len(rename_plan) > 10:
        print(f"  ... 他 {len(rename_plan) - 10} 件")
    print("-" * 50)

    # 確認
    ans = input("\n上記のリネームを実行しますか？ (y/n): ").strip().lower()
    if ans != "y":
        print("キャンセルしました")
        return

    # ===== リネーム実行 =====
    # 旧ファイル名 → 新ファイル名 のマッピングを作成
    rename_map = {}  # { (カテゴリ, 旧ファイル名): 新ファイル名 }
    for old, new, cat, _ in rename_plan:
        rename_map[(cat, old.name)] = new.name

    # 同名ファイルの競合を避けるため、一時ファイル経由でリネーム
    temp_plan = []
    for old, new, cat, idx in rename_plan:
        temp = old.parent / f"__temp_{idx:04d}__.webp"
        temp_plan.append((old, temp, new))

    # Step1: 旧名 → 一時名
    for old, temp, _ in temp_plan:
        if old.exists():
            old.rename(temp)

    # Step2: 一時名 → 新名
    renamed_count = 0
    for _, temp, new in temp_plan:
        if temp.exists():
            temp.rename(new)
            renamed_count += 1

    print(f"\n✅ {renamed_count} 件のファイルをリネームしました")

    # ===== data.json 更新 =====
    updated = 0
    not_found = []

    for m in materials:
        cat      = m.get("largeCategory", "")
        old_name = m.get("filename", "")
        key      = (cat, old_name)

        if key in rename_map:
            m["filename"] = rename_map[key]
            updated += 1
        else:
            # data.json にあるがファイルが見つからない場合
            # すでにリネーム済みの可能性もあるのでスキップ
            pass

    # data.json を保存
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ data.json を更新しました ({updated} 件のファイル名を変更)")

    if not_found:
        print(f"\n⚠ data.json にあるがフォルダで見つからなかったファイル: {len(not_found)} 件")
        for item in not_found:
            print(f"   {item}")

    print("\n" + "=" * 50)
    print("次のステップ：")
    print("  git add . && git commit -m 'Rename materials' && git push origin main")
    print("=" * 50)


if __name__ == "__main__":
    main()

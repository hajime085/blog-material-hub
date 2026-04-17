#!/usr/bin/env python3
"""
ソザイノ 孤立ファイル削除スクリプト
=====================================
data.json に記録されていない WebP ファイルを検出して削除します。

使い方：
  1. このスクリプトをリポジトリのルートに置く
  2. ターミナルで python3 cleanup_orphans.py を実行
  3. 削除予定のファイル一覧を確認して y を押す

注意：
  削除したファイルは元に戻せません。
  実行前に data.json が最新の状態か確認してください。
"""

import json
from pathlib import Path

MATERIALS_DIR = Path("assets/materials")
DATA_JSON     = Path("data.json")


def main():
    print("=" * 50)
    print("ソザイノ 孤立ファイル削除ツール")
    print("=" * 50)

    # data.json 読み込み
    if not DATA_JSON.exists():
        print(f"❌ {DATA_JSON} が見つかりません")
        return

    with open(DATA_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    materials = data.get("materials", [])

    # data.json に登録されているファイルのセットを作成
    registered = set()
    for m in materials:
        cat      = m.get("largeCategory", "")
        filename = m.get("filename", "")
        if cat and filename:
            registered.add(f"{cat}/{filename}")

    print(f"\n📦 data.json に登録されている素材: {len(registered)} 件")

    # assets/materials/ 以下の全 WebP ファイルを取得
    all_files = list(MATERIALS_DIR.rglob("*.webp"))
    print(f"📂 フォルダ内の WebP ファイル: {len(all_files)} 件")

    # 孤立ファイルを検出
    orphans = []
    for f in sorted(all_files):
        # 相対パス（例：nature/sakura001.webp）
        rel = f.relative_to(MATERIALS_DIR)
        key = str(rel).replace("\\", "/")
        if key not in registered:
            orphans.append(f)

    if not orphans:
        print("\n✅ 孤立ファイルはありません。すべて data.json に登録されています。")
        return

    # 孤立ファイル一覧を表示
    print(f"\n⚠ 孤立ファイル（data.json に未登録）: {len(orphans)} 件")
    print("-" * 50)
    for f in orphans:
        size_kb = f.stat().st_size // 1024
        print(f"  {f.relative_to(MATERIALS_DIR)}  ({size_kb}KB)")
    print("-" * 50)

    total_size = sum(f.stat().st_size for f in orphans) // 1024
    print(f"  合計サイズ: {total_size}KB ({total_size/1024:.1f}MB)")

    # 確認
    ans = input("\n上記のファイルを削除しますか？ (y/n): ").strip().lower()
    if ans != "y":
        print("キャンセルしました")
        return

    # 削除実行
    deleted = 0
    for f in orphans:
        try:
            f.unlink()
            print(f"  🗑 削除: {f.relative_to(MATERIALS_DIR)}")
            deleted += 1
        except Exception as e:
            print(f"  ❌ 削除失敗: {f.name} ({e})")

    print("\n" + "=" * 50)
    print(f"✅ {deleted} 件のファイルを削除しました")
    print("\n次のステップ：")
    print("  git add . && git commit -m 'Remove orphan files' && git push origin main")
    print("=" * 50)


if __name__ == "__main__":
    main()

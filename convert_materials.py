#!/usr/bin/env python3
"""
ソザイノ 素材一括変換・振り分けスクリプト
==========================================
1つのフォルダに放り込んだ画像を：
  ① WebPに変換（画質85%）
  ② ファイル名の先頭でカテゴリを自動判定
  ③ カテゴリ別フォルダに振り分け
  ④ 連番リネーム（例：nature_041.webp）

命名ルール（先頭文字）：
  n   → nature      自然
  f   → food        食べ物
  b   → backgrounds 背景
  h   → home        インテリア
  p   → person      人
  bl  → blog        ブログ素材
  be  → beauty      美容・リラクゼーション
  o   → outdoor     アウトドア

使い方：
  1. このスクリプトをリポジトリのルートに置く
  2. input/ フォルダを作って変換したい画像を全部入れる
  3. ターミナルで python3 convert_materials.py を実行
  4. 確認して y を押す
  5. assets/materials/ に自動で振り分けられる
"""

import os
import sys
from pathlib import Path

# Pillow チェック
try:
    from PIL import Image
except ImportError:
    print("❌ Pillowがインストールされていません。")
    print("   以下のコマンドを実行してください：")
    print("   pip3 install Pillow")
    sys.exit(1)

# ===== 設定 =====
INPUT_DIR     = Path("input")           # 変換元フォルダ
MATERIALS_DIR = Path("assets/materials") # 出力先
WEBP_QUALITY  = 87                       # WebP画質（85〜90）

# カテゴリマッピング（先頭文字 → フォルダ名）
# ※ 長いプレフィックスを先に書く（bl が b より先）
CATEGORY_MAP = [
    ("bl",  "blog"),
    ("be",  "beauty"),
    ("n",   "nature"),
    ("f",   "food"),
    ("b",   "backgrounds"),
    ("h",   "home"),
    ("p",   "person"),
    ("o",   "outdoor"),
]

# 対応する拡張子
SUPPORTED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
# ================


def detect_category(filename: str) -> str:
    """ファイル名の先頭文字からカテゴリを判定"""
    name = filename.lower()
    for prefix, category in CATEGORY_MAP:
        if name.startswith(prefix):
            return category
    return None


def get_next_number(category_dir: Path, category: str) -> int:
    """カテゴリフォルダ内の既存ファイル数から次の連番を取得"""
    existing = list(category_dir.glob(f"{category}_*.webp"))
    if not existing:
        return 1
    numbers = []
    for f in existing:
        stem = f.stem  # 例: nature_041
        parts = stem.split("_")
        if len(parts) >= 2 and parts[-1].isdigit():
            numbers.append(int(parts[-1]))
    return max(numbers) + 1 if numbers else 1


def main():
    print("=" * 55)
    print("ソザイノ 素材一括変換・振り分けツール")
    print("=" * 55)

    # input フォルダ確認
    if not INPUT_DIR.exists():
        INPUT_DIR.mkdir()
        print(f"\n📂 '{INPUT_DIR}' フォルダを作成しました。")
        print(f"   変換したい画像をこのフォルダに入れてから再実行してください。")
        return

    # 対象ファイルを取得
    files = [
        f for f in INPUT_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXT
    ]

    if not files:
        print(f"\n⚠ '{INPUT_DIR}' フォルダに画像が見つかりません。")
        print(f"  対応形式: PNG / JPG / JPEG / WebP / BMP / TIFF")
        return

    print(f"\n📂 '{INPUT_DIR}' フォルダ内の画像: {len(files)} 件")

    # カテゴリ判定
    plan = []       # (元ファイル, カテゴリ)
    unknown = []    # カテゴリ不明のファイル

    for f in sorted(files):
        cat = detect_category(f.name)
        if cat:
            plan.append((f, cat))
        else:
            unknown.append(f)

    # 不明ファイルを表示
    if unknown:
        print(f"\n⚠ カテゴリ不明（スキップ）: {len(unknown)} 件")
        for f in unknown:
            print(f"   {f.name}  ← 先頭文字が n/f/b/h/p/bl/be/o ではありません")

    if not plan:
        print("\n変換対象がありません。ファイル名の先頭を確認してください。")
        return

    # カテゴリ別にグループ化して表示
    from collections import defaultdict
    groups = defaultdict(list)
    for f, cat in plan:
        groups[cat].append(f)

    print(f"\n📋 変換予定: {len(plan)} 件")
    print("-" * 45)
    for cat, files_in_cat in sorted(groups.items()):
        cat_dir = MATERIALS_DIR / cat
        next_num = get_next_number(cat_dir, cat)
        print(f"  {cat}/ : {len(files_in_cat)} 件  （{cat}_{next_num:03d}.webp〜）")
    print(f"  画質: WebP {WEBP_QUALITY}%")
    print("-" * 45)

    # 確認
    ans = input("\n上記の変換・振り分けを実行しますか？ (y/n): ").strip().lower()
    if ans != "y":
        print("キャンセルしました")
        return

    # ===== 変換・振り分け実行 =====
    success = 0
    failed  = 0

    for cat, files_in_cat in sorted(groups.items()):
        cat_dir = MATERIALS_DIR / cat
        cat_dir.mkdir(parents=True, exist_ok=True)
        next_num = get_next_number(cat_dir, cat)

        for f in sorted(files_in_cat):
            new_name = f"{cat}_{next_num:03d}.webp"
            out_path = cat_dir / new_name

            try:
                with Image.open(f) as img:
                    # RGBAはRGBに変換（WebPはアルファ対応だが念のため）
                    if img.mode in ("RGBA", "LA"):
                        bg = Image.new("RGB", img.size, (255, 255, 255))
                        bg.paste(img, mask=img.split()[-1])
                        img = bg
                    elif img.mode != "RGB":
                        img = img.convert("RGB")

                    img.save(out_path, "WEBP", quality=WEBP_QUALITY, method=6)

                print(f"  ✅ {f.name}  →  {cat}/{new_name}")
                next_num += 1
                success += 1

            except Exception as e:
                print(f"  ❌ {f.name}  変換失敗: {e}")
                failed += 1

    # 元ファイルを processed/ フォルダに移動
    processed_dir = INPUT_DIR / "processed"
    processed_dir.mkdir(exist_ok=True)
    for f, _ in plan:
        if f.exists():
            f.rename(processed_dir / f.name)

    print("\n" + "=" * 55)
    print(f"✅ 完了: {success} 件変換  /  ❌ 失敗: {failed} 件")
    print(f"📂 元ファイルは '{INPUT_DIR}/processed/' に移動しました")
    print("\n次のステップ：")
    print("  admin.html で assets/materials/ フォルダをドロップ")
    print("  → AI生成 → data.json エクスポート → git push")
    print("=" * 55)


if __name__ == "__main__":
    main()

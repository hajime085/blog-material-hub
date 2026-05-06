#!/usr/bin/env python3
"""
ソザイノ パス緊急修正スクリプト
================================
誤って /blog-material-hub/ プレフィックスを付けてしまったリンクを
全て相対パスに戻す。

例:
  /blog-material-hub/gallery.html → gallery.html
  /blog-material-hub/             → /
"""

import os
import re
import shutil
from datetime import datetime

SOURCE_DIR = "."
BACKUP_DIR = f"_fixbackup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

TARGET_FILES = [
    "index.html",
    "gallery.html",
    "download.html",
    "about.html",
    "process.html",
    "faq.html",
    "contact.html",
    "privacy.html",
    "terms.html",
    "disclaimer.html",
]


def backup_file(filepath, backup_dir):
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    dest = os.path.join(backup_dir, os.path.basename(filepath))
    shutil.copy2(filepath, dest)
    print(f"  [backup] {filepath} -> {dest}")


def fix_paths(content):
    """
    /blog-material-hub/xxx.html → xxx.html
    /blog-material-hub/         → /
    """
    # まず /blog-material-hub/file.html を file.html に変換
    # href="/blog-material-hub/foo.html" → href="foo.html"
    content = re.sub(
        r'(href|src)="/blog-material-hub/([^"]+\.html)"',
        r'\1="\2"',
        content
    )
    # href="/blog-material-hub/" → href="/"
    content = re.sub(
        r'(href|src)="/blog-material-hub/"',
        r'\1="/"',
        content
    )
    # 念のため /blog-material-hub/data.json なども
    content = re.sub(
        r'(href|src)="/blog-material-hub/([^"]+)"',
        r'\1="\2"',
        content
    )
    return content


def process_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        original = f.read()

    new_content = fix_paths(original)

    if new_content != original:
        # 何個直したか数える
        before_count = original.count("/blog-material-hub/")
        after_count = new_content.count("/blog-material-hub/")
        fixed = before_count - after_count

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"  [fix]  {filepath}  ({fixed}箇所修正)")
        return True
    else:
        print(f"  [skip] {filepath}  (修正対象なし)")
        return False


def main():
    print("=" * 60)
    print("ソザイノ パス緊急修正")
    print("=" * 60)

    missing = [f for f in TARGET_FILES if not os.path.exists(f)]
    if missing:
        print(f"\n[ERROR] 以下のファイルが見つかりません:")
        for m in missing:
            print(f"  - {m}")
        return

    print(f"\n--- バックアップ作成 ({BACKUP_DIR}/) ---")
    for filename in TARGET_FILES:
        backup_file(filename, BACKUP_DIR)

    print(f"\n--- パス修正 ---")
    fixed = 0
    for filename in TARGET_FILES:
        if process_file(filename):
            fixed += 1

    print(f"\n" + "=" * 60)
    print(f"完了: {fixed}/{len(TARGET_FILES)} ファイルを修正")
    print(f"バックアップ: {BACKUP_DIR}/")
    print("=" * 60)
    print("\n次のステップ:")
    print("  1. git diff で確認")
    print("  2. git add . && git commit -m 'Fix: remove /blog-material-hub/ prefix from links'")
    print("  3. git push")


if __name__ == "__main__":
    main()

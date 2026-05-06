#!/usr/bin/env python3
"""
ソザイノ ナビ・フッター一括更新スクリプト
==========================================
全HTMLファイルのナビとフッターを統一パターンに置換し、
sitemap.xml に about.html / process.html を追加する。

使い方:
  python3 update_nav_footer.py

事前にバックアップ (_backup_YYYYMMDD/) を作成してから上書きする。
"""

import os
import re
import shutil
from datetime import datetime

# ====== 設定 ======
SOURCE_DIR = "."  # スクリプトを置いた場所＝blog-material-hubディレクトリ
BACKUP_DIR = f"_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# 処理対象ファイル
TARGET_FILES = [
    "index.html",
    "gallery.html",
    "download.html",
    "faq.html",
    "contact.html",
    "privacy.html",
    "terms.html",
    "disclaimer.html",
]

SITEMAP_FILE = "sitemap.xml"

# ====== ナビパターン定義 ======
# 相対パス用（index.html, gallery.html）
NAV_RELATIVE = """<nav class="nav-links" id="navMenu">
            <a href="/">ホーム</a>
            <a href="gallery.html">素材ギャラリー</a>
            <a href="about.html">ソザイノについて</a>
            <a href="process.html">素材ができるまで</a>
            <a href="faq.html">よくあるご質問</a>
            <a href="contact.html">お問い合わせ</a>
        </nav>"""

# 絶対パス用（faq.html, contact.html, privacy.html, terms.html, disclaimer.html）
NAV_ABSOLUTE = """<nav id="navMenu">
                <a href="/blog-material-hub/">ホーム</a>
                <a href="/blog-material-hub/gallery.html">素材ギャラリー</a>
                <a href="/blog-material-hub/about.html">ソザイノについて</a>
                <a href="/blog-material-hub/process.html">素材ができるまで</a>
                <a href="/blog-material-hub/faq.html">よくあるご質問</a>
                <a href="/blog-material-hub/contact.html">お問い合わせ</a>
            </nav>"""

# ====== フッターパターン定義 ======
# 相対パス用
FOOTER_LINKS_RELATIVE = """<div class="footer-links">
            <a href="about.html">ソザイノについて</a>
            <a href="process.html">素材ができるまで</a>
            <a href="privacy.html">プライバシーポリシー</a>
            <a href="terms.html">利用規約</a>
            <a href="disclaimer.html">免責事項</a>
            <a href="contact.html">お問い合わせ</a>
        </div>"""

# 絶対パス用
FOOTER_LINKS_ABSOLUTE = """<div class="footer-links">
                <a href="/blog-material-hub/about.html">ソザイノについて</a>
                <a href="/blog-material-hub/process.html">素材ができるまで</a>
                <a href="/blog-material-hub/privacy.html">プライバシーポリシー</a>
                <a href="/blog-material-hub/terms.html">利用規約</a>
                <a href="/blog-material-hub/disclaimer.html">免責事項</a>
                <a href="/blog-material-hub/contact.html">お問い合わせ</a>
            </div>"""

# ====== ファイル別の設定 ======
FILE_CONFIG = {
    "index.html":      {"nav": "relative", "footer": "relative"},
    "gallery.html":    {"nav": "relative", "footer": "relative"},
    "download.html":   {"nav": "relative", "footer": "relative"},
    "faq.html":        {"nav": "absolute", "footer": "absolute"},
    "contact.html":    {"nav": "absolute", "footer": "absolute"},
    "privacy.html":    {"nav": "absolute", "footer": "absolute"},
    "terms.html":      {"nav": "absolute", "footer": "absolute"},
    "disclaimer.html": {"nav": "absolute", "footer": "absolute"},
}


def backup_file(filepath, backup_dir):
    """ファイルを backup_dir にコピー"""
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    dest = os.path.join(backup_dir, os.path.basename(filepath))
    shutil.copy2(filepath, dest)
    print(f"  [backup] {filepath} -> {dest}")


def update_nav(content, nav_type):
    """<nav ...id="navMenu">...</nav> を置換"""
    new_nav = NAV_RELATIVE if nav_type == "relative" else NAV_ABSOLUTE
    # <nav ... id="navMenu" ... > ... </nav> を非貪欲マッチで置換
    pattern = r'<nav[^>]*id="navMenu"[^>]*>.*?</nav>'
    new_content, count = re.subn(pattern, new_nav, content, flags=re.DOTALL)
    return new_content, count


def update_footer_links(content, footer_type):
    """<div class="footer-links">...</div> を置換"""
    new_footer = FOOTER_LINKS_RELATIVE if footer_type == "relative" else FOOTER_LINKS_ABSOLUTE
    pattern = r'<div class="footer-links">.*?</div>'
    new_content, count = re.subn(pattern, new_footer, content, flags=re.DOTALL)
    return new_content, count


def fix_old_copyright(content):
    """古いcopyright表記を修正（gallery.html / download.html共通）"""
    return content.replace(
        "&copy; 2026 Blog Material Hub. All rights reserved.",
        "&copy; 2026 ソザイノ (SOZAINO). All rights reserved."
    )


def update_sitemap(filepath):
    """sitemap.xml に about.html と process.html を追加"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 既に追加済みかチェック
    already_about = "about.html" in content
    already_process = "process.html" in content

    if already_about and already_process:
        print(f"  [skip] sitemap already contains about.html and process.html")
        return False

    # </urlset> の前に挿入
    insert_block = ""
    if not already_about:
        insert_block += """  <url>
    <loc>https://sozaino.com/about.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
"""
    if not already_process:
        insert_block += """  <url>
    <loc>https://sozaino.com/process.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
"""

    new_content = content.replace("</urlset>", insert_block + "</urlset>")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"  [update] sitemap.xml に about/process を追加")
    return True


def process_file(filepath, config):
    """1ファイルを処理"""
    with open(filepath, "r", encoding="utf-8") as f:
        original = f.read()

    content = original
    nav_count = 0
    footer_count = 0

    # ナビ更新
    content, nav_count = update_nav(content, config["nav"])

    # フッター更新
    content, footer_count = update_footer_links(content, config["footer"])

    # gallery.html / download.html の copyright 修正（含まれていれば置換）
    content = fix_old_copyright(content)

    # 変更があった場合のみ書き込み
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  [update] {filepath}  nav:{nav_count}  footer:{footer_count}")
        return True
    else:
        print(f"  [skip]   {filepath}  (変更なし)")
        return False


def main():
    print("=" * 60)
    print("ソザイノ ナビ・フッター一括更新")
    print("=" * 60)

    # ファイル存在確認
    missing = [f for f in TARGET_FILES + [SITEMAP_FILE]
               if not os.path.exists(os.path.join(SOURCE_DIR, f))]
    if missing:
        print(f"\n[ERROR] 以下のファイルが見つかりません:")
        for m in missing:
            print(f"  - {m}")
        print(f"\nスクリプトを blog-material-hub ディレクトリ直下で実行してください。")
        return

    # バックアップ
    print(f"\n--- バックアップ作成 ({BACKUP_DIR}/) ---")
    for filename in TARGET_FILES + [SITEMAP_FILE]:
        backup_file(os.path.join(SOURCE_DIR, filename), BACKUP_DIR)

    # HTMLファイル処理
    print(f"\n--- ナビ・フッター更新 ---")
    updated = 0
    for filename in TARGET_FILES:
        filepath = os.path.join(SOURCE_DIR, filename)
        config = FILE_CONFIG[filename]
        if process_file(filepath, config):
            updated += 1

    # sitemap.xml 更新
    print(f"\n--- sitemap.xml 更新 ---")
    update_sitemap(os.path.join(SOURCE_DIR, SITEMAP_FILE))

    print(f"\n" + "=" * 60)
    print(f"完了: {updated}/{len(TARGET_FILES)} HTMLファイルを更新")
    print(f"バックアップ: {BACKUP_DIR}/")
    print("=" * 60)
    print("\n次のステップ:")
    print("  1. ブラウザで各ページのナビとフッターを目視確認")
    print("  2. git diff で変更内容を確認")
    print("  3. git add . && git commit -m 'Add about/process page links to nav and footer'")
    print("  4. git push")
    print("  5. Search Console でサイトマップを再送信")


if __name__ == "__main__":
    main()

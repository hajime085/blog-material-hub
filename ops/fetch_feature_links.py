#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""特集ページ（event_src/）の素の楽天リンクを、正規のアフィリエイトリンクに差し替える。

使い方（リポジトリ直下で）:

    python3 ops/fetch_feature_links.py            # 取得して差し替え
    python3 ops/fetch_feature_links.py --dry-run  # 取得だけ。ファイルは書き換えない

なぜ要るか:

    フィードの商品リンクは楽天 API が affiliateUrl を返すので、最初から
    アフィリエイトリンクになっている。一方、特集ページ（ケーキ・おせち）は
    商品ページの URL を手で貼って作ったため、素の item.rakuten.co.jp
    リンクのままだった（踏まれても報酬が入らない）。2026-11-10 に発覚。

やり方:

    fetch_rakuten.py の API 接続（認証・間隔・エラー処理）をそのまま借りて、
    商品コードで 1 件ずつ引き直し、返ってきた affiliateUrl に href を
    差し替える。認証情報は fetch_rakuten と同じ .env / config.json から
    読む（このファイルには書かない）。

    ショップのカテゴリページ（/<shop>/c/...）は「商品」ではないので
    商品 API では引けない。対象外として最後に報告だけする。
"""

import glob
import importlib.util
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HREF_RE = re.compile(r'href="(https://item\.rakuten\.co\.jp[^"]*)"')


def load_fetch_rakuten():
    path = os.path.join(ROOT, "fetch_rakuten.py")
    spec = importlib.util.spec_from_file_location("fetch_rakuten", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify(url):
    """item.rakuten.co.jp の URL を (種類, 店舗コード, 商品コード) に分ける。"""
    parts = [p for p in url.split("://", 1)[1].split("/")[1:] if p]
    if len(parts) >= 2 and parts[1] == "c":
        return ("category", parts[0], "")
    if len(parts) >= 2:
        return ("item", parts[0], parts[1])
    return ("other", parts[0] if parts else "", "")


def collect_targets():
    """event_src/ 全部の特集から、素リンクとその出現ファイルを集める。"""
    files = sorted(glob.glob(os.path.join(ROOT, "event_src", "*", "index.html")))
    targets = {}  # url -> [file, ...]
    for path in files:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for url in HREF_RE.findall(text):
            targets.setdefault(url, []).append(path)
    return files, targets


def main():
    dry_run = "--dry-run" in sys.argv
    fr = load_fetch_rakuten()
    cfg = fr.load_json("config.json", {}) or {}
    app_id, access_key, aff_id = fr.credentials(cfg)
    site_url = ((cfg.get("site") or {}).get("url") or "https://sozaino.com")

    files, targets = collect_targets()
    items, skipped = [], []
    for url in sorted(targets):
        kind, shop, code = classify(url)
        if kind == "item":
            items.append((url, shop, code))
        else:
            skipped.append((url, kind))

    print("特集ページの素リンク: %d件（商品 %d件 / 対象外 %d件）"
          % (len(targets), len(items), len(skipped)))
    if not items:
        print("差し替える商品リンクはありません。")
        return 0

    mapping = {}
    missed = []
    consecutive_fail = 0
    for i, (url, shop, code) in enumerate(items, 1):
        if i > 1:
            time.sleep(fr.REQUEST_INTERVAL)
        item_code = "%s:%s" % (shop, code)
        params = {
            "applicationId": app_id,
            "accessKey": access_key,
            "affiliateId": aff_id,
            "itemCode": item_code,
            "format": "json",
            "formatVersion": 2,
        }
        try:
            data = fr.api_get(fr.ITEM_API, params, site_url)
        except SystemExit as ex:
            missed.append((url, "APIエラー: %s" % (ex.code if ex.code else "接続失敗")))
            consecutive_fail += 1
            if consecutive_fail >= 3:
                print("APIエラーが3回続いたので中断します。認証情報を確認してください。")
                return 1
            continue
        consecutive_fail = 0
        found = None
        for it in data.get("Items", []):
            if it.get("itemCode") == item_code and it.get("affiliateUrl"):
                found = it["affiliateUrl"]
                break
        if found:
            mapping[url] = found
            print("  ○ %d/%d %s" % (i, len(items), item_code))
        else:
            missed.append((url, "商品が見つからない（販売終了の可能性）"))
            print("  × %d/%d %s（見つからない）" % (i, len(items), item_code))

    replaced_total = 0
    if mapping and not dry_run:
        for path in files:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            n = 0
            for bare, afl in mapping.items():
                needle = 'href="%s"' % bare
                cnt = text.count(needle)
                if cnt:
                    # 生成ページの流儀に合わせて & は &amp; にする
                    text = text.replace(needle, 'href="%s"' % afl.replace("&", "&amp;"))
                    n += cnt
            if n:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                print("%s: %dか所を差し替え" % (os.path.relpath(path, ROOT), n))
                replaced_total += n

    print("")
    print("== 結果 ==")
    if dry_run:
        print("（ドライラン: ファイルは書き換えていません）")
    print("取得できた商品リンク: %d/%d件、差し替えた場所: %dか所"
          % (len(mapping), len(items), replaced_total))
    for url, why in missed:
        print("  取れなかった: %s（%s）" % (url, why))
    for url, kind in skipped:
        print("  対象外（%sページ）: %s" % (kind, url))
    if not missed and not dry_run:
        print("次は: python3 build.py → git add -A → git commit → git pull → git push")
    return 0 if not missed else 1


if __name__ == "__main__":
    sys.exit(main())

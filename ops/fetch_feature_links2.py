#!/usr/bin/env python3
"""特集ページの商品リンクを楽天アフィリエイトリンクに差し替える（第2段）。

    python3 ops/fetch_feature_links2.py [--dry-run]

第1段（ops/fetch_feature_links.py）で取れなかった分専用。第1段は
「URL の末尾＝数字の商品コード」という前提で引いていたが、店によっては
末尾が店独自のカタログ番号で、API の商品コードとは別物だった
（ベルーナグルメの「絆」、海鮮かに処の「福袋おせち」など）。

第2段の探し方:

    A. 商品ページの HTML を読み、埋まっている数字の商品コード候補を
       全部拾って 1 つずつ API で確かめる（返ってきた商品 URL が
       元の URL と一致したものだけ採用する）。
    B. それでも駄目なら、ページ内の商品名（JSON-LD）からキーワードを
       作り、その店の中で名前検索して、商品 URL が一致するものを探す。
    C. カテゴリページへのリンク（2 件）は、カードの商品名で店内検索し、
       名前が一致する商品がちょうど 1 件だけ見つかった場合に限り、
       その商品ページのアフィリエイトリンクに差し替える。複数当たったら
       候補を表示して止める（推測で差し替えない）。

認証情報は fetch_rakuten.py と同じ .env / config.json から読む。
"""
import importlib.util
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENT_SRC = ROOT / "event_src"
ITEM_URL_RE = re.compile(r'href="(https://item\.rakuten\.co\.jp/[^"]+)"')
CODE_PATTERNS = [
    re.compile(r'"itemId"\s*:\s*"?(\d{6,})'),
    re.compile(r'"item_id"\s*:\s*"?(\d{6,})'),
    re.compile(r'[?&]itemId=(\d{6,})'),
]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def load_fetch_rakuten():
    spec = importlib.util.spec_from_file_location(
        "fetch_rakuten", ROOT / "fetch_rakuten.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify(url):
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) == 3 and parts[1] == "c":
        return "category", parts[0], None
    if len(parts) == 2:
        return "item", parts[0], parts[1]
    return "other", None, None


def jsonld_names(html):
    """ページの JSON-LD（ItemList）から url -> 商品名 の対応を取る。"""
    out = {}
    for m in re.finditer(r'"name"\s*:\s*"([^"]+)"\s*,\s*"url"\s*:\s*"(https://item\.rakuten\.co\.jp/[^"]+)"', html):
        out[m.group(2)] = m.group(1)
    return out


def keyword_candidates(name):
    """商品名から検索キーワード候補を作る（店内検索なので短くてよい）。"""
    if not name:
        return []
    name = re.sub(r"（[^）]*）", "", name)
    name = re.sub(r"\([^)]*\)", "", name)
    cands = []
    bracketed = re.findall(r"「([^」]+)」", name)
    if bracketed:
        cands.append(bracketed[-1])
    tokens = [t for t in re.split(r"[\s　]+", name.replace("「", " ").replace("」", " ")) if len(t) >= 2]
    tokens.sort(key=len, reverse=True)
    for t in tokens[:2]:
        if t not in cands:
            cands.append(t)
    plain = re.sub(r"[「」]", "", name).strip()
    if plain and plain not in cands:
        cands.append(plain)
    return cands


def fetch_page(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as res:
            return res.read(1500000).decode("utf-8", "replace")
    except Exception:                                     # noqa: BLE001
        return ""


def main():
    dry_run = "--dry-run" in sys.argv
    fr = load_fetch_rakuten()
    cfg = fr.load_json("config.json", {})
    creds = fr.credentials(cfg)
    if creds is None:
        return 1
    app_id, access_key, aff_id = creds
    site_url = (cfg.get("site") or {}).get("url") or "https://sozaino.com"
    ctx_params = {"applicationId": app_id, "accessKey": access_key,
                  "affiliateId": aff_id, "format": "json", "formatVersion": 2}

    files = {}
    names = {}
    bare = []
    for path in sorted(EVENT_SRC.glob("*/index.html")):
        text = path.read_text(encoding="utf-8")
        files[path] = text
        names.update(jsonld_names(text))
        for url in ITEM_URL_RE.findall(text):
            bare.append(url)
    bare = sorted(set(bare))
    items = [u for u in bare if classify(u)[0] == "item"]
    cats = [u for u in bare if classify(u)[0] == "category"]
    print("残っている素リンク: %d件（商品 %d件 / カテゴリ %d件）"
          % (len(bare), len(items), len(cats)))
    if not bare:
        print("対象はありません。")
        return 0

    def api(params):
        return fr.api_get(fr.ITEM_API, params, site_url)

    def url_matches(it, shop, slug):
        raw = urllib.parse.unquote(it.get("itemUrl") or "")
        return ("/%s/%s/" % (shop, slug)) in raw or raw.rstrip("/").endswith("/%s/%s" % (shop, slug))

    mapping = {}
    missed = []
    fail_streak = 0

    def note_fail(url, why):
        nonlocal fail_streak
        missed.append((url, why))
        fail_streak += 1
        return fail_streak >= 3

    for i, url in enumerate(items, 1):
        _, shop, slug = classify(url)
        found = None
        # --- A. ページ埋め込みの商品コード候補を全部試す
        html = fetch_page(url)
        codes = []
        for pat in CODE_PATTERNS:
            for c in pat.findall(html or ""):
                code = "%s:%s" % (shop, c)
                if code not in codes and c != slug:
                    codes.append(code)
        for code in codes[:8]:
            time.sleep(fr.REQUEST_INTERVAL)
            try:
                data = api(dict(ctx_params, itemCode=code))
            except SystemExit as ex:
                msg = str(ex).splitlines()[0] if str(ex) else "接続失敗"
                print("  × %s（APIエラー: %s）" % (code, msg))
                if note_fail(url, "APIエラー: %s" % msg):
                    print("APIエラーが3回続いたので中断します。")
                    return 1
                break
            for it in data.get("Items", []):
                if it.get("affiliateUrl") and url_matches(it, shop, slug):
                    found = it["affiliateUrl"]
                    break
            if found:
                break
        # --- B. 商品名キーワードで店内検索
        if not found:
            for kw in keyword_candidates(names.get(url)):
                time.sleep(fr.REQUEST_INTERVAL)
                try:
                    data = api(dict(ctx_params, shopCode=shop, keyword=kw, hits=30))
                except SystemExit as ex:
                    msg = str(ex).splitlines()[0] if str(ex) else "接続失敗"
                    print("  × %s 検索（APIエラー: %s）" % (kw, msg))
                    if note_fail(url, "APIエラー: %s" % msg):
                        print("APIエラーが3回続いたので中断します。")
                        return 1
                    break
                for it in data.get("Items", []):
                    if it.get("affiliateUrl") and url_matches(it, shop, slug):
                        found = it["affiliateUrl"]
                        break
                if found:
                    break
        if found:
            fail_streak = 0
            mapping[url] = found
            print("  ○ %d/%d %s" % (i, len(items), url))
        else:
            missed.append((url, "ページ内コードでも名前検索でも見つからない"))
            print("  × %d/%d %s（見つからない）" % (i, len(items), url))

    # --- C. カテゴリリンク: 名前が一致する商品が 1 件だけなら差し替える
    cat_notes = []
    for url in cats:
        _, shop, _ = classify(url)
        name = names.get(url)
        cands = keyword_candidates(name)
        adopted = None
        seen = []
        for kw in cands:
            time.sleep(fr.REQUEST_INTERVAL)
            try:
                data = api(dict(ctx_params, shopCode=shop, keyword=kw, hits=30))
            except SystemExit:
                continue
            hits = [it for it in data.get("Items", []) if it.get("affiliateUrl")]
            core = cands[0] if cands else ""
            named = [it for it in hits if core and core in (it.get("itemName") or "")]
            seen = named or hits
            if len(named) == 1:
                adopted = named[0]
                break
            if named:
                break
        if adopted:
            mapping[url] = adopted["affiliateUrl"]
            cat_notes.append("%s → 商品ページ「%s」に差し替え"
                             % (url, (adopted.get("itemName") or "")[:40]))
            print("  ○ カテゴリ %s → %s" % (url, adopted.get("itemCode")))
        else:
            missed.append((url, "カテゴリ: 候補が %d 件で決めきれない" % len(seen)))
            print("  × カテゴリ %s（候補 %d 件）" % (url, len(seen)))
            for it in seen[:5]:
                print("      候補: %s %s" % (it.get("itemCode"), (it.get("itemName") or "")[:50]))

    replaced = 0
    for path, text in files.items():
        new = text
        for old, afl in mapping.items():
            new = new.replace('href="%s"' % old, 'href="%s"' % afl.replace("&", "&amp;"))
        if new != text:
            n = sum(1 for old in mapping if ('href="%s"' % old) in text)
            print("%s: %dか所を差し替え" % (path.relative_to(ROOT), n))
            replaced += n
            if not dry_run:
                path.write_text(new, encoding="utf-8")

    print("\n== 結果（第2段） ==")
    print("取得できたリンク: %d/%d件、差し替えた場所: %dか所"
          % (len(mapping), len(bare), replaced))
    for note in cat_notes:
        print("  " + note)
    for url, why in missed:
        print("  取れなかった: %s（%s）" % (url, why))
    print("次は: python3 build.py → git add -A → git commit → git pull → git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())

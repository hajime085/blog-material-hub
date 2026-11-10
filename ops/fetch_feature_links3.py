#!/usr/bin/env python3
"""特集ページの商品リンクを楽天アフィリエイトリンクに差し替える（第3段）。

    python3 ops/fetch_feature_links3.py [--dry-run]

第2段でも取れなかった分専用（想定: saladcosmo の「ちこり村」）。
原因は、店が商品名にスペースを入れて書くこと
（「田舎の 手づくり おせち」）。第2段の長いフレーズ検索では、
間が空いた名前には一致しない。

第3段の探し方:

    A. 商品ページを大きめに読み直し（3MB）、埋まっている商品コード
       候補を全部試す（URL が一致したものだけ採用）。
    B. 名前を短い言葉に割って店内検索する（「田舎の」「手づくり」
       「ちこり村」など）。1 回の取得件数を 100 件に広げ、広い
       キーワードでは 3 ページまで見る。当たりは必ず商品 URL が
       元の URL と一致するかで確かめるので、誤採用は起きない。

認証情報は fetch_rakuten.py と同じ .env / config.json から読む。
"""
import importlib.util
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
STOPWORDS = {"おせち", "ケーキ", "予約", "早割", "送料", "無料", "冷蔵", "冷凍"}


def load_fetch_rakuten():
    spec = importlib.util.spec_from_file_location(
        "fetch_rakuten", ROOT / "fetch_rakuten.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify(url):
    parts = urllib.parse.urlparse(url).path.strip("/").split("/")
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, None


def jsonld_names(html):
    out = {}
    for m in re.finditer(r'"name"\s*:\s*"([^"]+)"\s*,\s*"url"\s*:\s*"(https://item\.rakuten\.co\.jp/[^"]+)"', html):
        out[m.group(2)] = m.group(1)
    return out


def short_keywords(name):
    """名前を短い検索語に割る。スペース入りの商品名でも当たるように。"""
    if not name:
        return []
    name = re.sub(r"（[^）]*）", "", name)
    name = re.sub(r"\([^)]*\)", "", name)
    cands = []
    for t in re.findall(r"「([^」]+)」", name):
        cands.append(t)
    tokens = [t for t in re.split(r"[\s　「」]+", name) if len(t) >= 2]
    cands.extend(tokens)
    # 長い語は先頭の特徴的な部分も候補にする（例: 田舎の手づくりおせち → 田舎の）
    for t in list(tokens):
        if len(t) >= 5:
            cands.append(t[:3])
    out = []
    for c in cands:
        if c and c not in STOPWORDS and c not in out:
            out.append(c)
    return out


def fetch_page(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as res:
            return res.read(3000000).decode("utf-8", "replace")
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
    base = {"applicationId": app_id, "accessKey": access_key,
            "affiliateId": aff_id, "format": "json", "formatVersion": 2}

    files = {}
    names = {}
    bare = []
    for path in sorted(EVENT_SRC.glob("*/index.html")):
        text = path.read_text(encoding="utf-8")
        files[path] = text
        names.update(jsonld_names(text))
        bare += ITEM_URL_RE.findall(text)
    bare = sorted({u for u in bare if classify(u)[0]})
    print("残っている素リンク（商品）: %d件" % len(bare))
    if not bare:
        print("対象はありません。")
        return 0

    def url_matches(it, shop, slug):
        raw = urllib.parse.unquote(it.get("itemUrl") or "")
        return ("/%s/%s/" % (shop, slug)) in raw

    mapping = {}
    missed = []
    for i, url in enumerate(bare, 1):
        shop, slug = classify(url)
        found = None
        html = fetch_page(url)
        codes = []
        for pat in CODE_PATTERNS:
            for c in pat.findall(html or ""):
                code = "%s:%s" % (shop, c)
                if code not in codes and c != slug:
                    codes.append(code)
        for code in codes[:10]:
            time.sleep(fr.REQUEST_INTERVAL)
            try:
                data = fr.api_get(fr.ITEM_API, dict(base, itemCode=code), site_url)
            except SystemExit:
                continue
            for it in data.get("Items", []):
                if it.get("affiliateUrl") and url_matches(it, shop, slug):
                    found = it["affiliateUrl"]
                    break
            if found:
                break
        if not found:
            for kw in short_keywords(names.get(url)):
                for page in (1, 2, 3):
                    time.sleep(fr.REQUEST_INTERVAL)
                    try:
                        data = fr.api_get(fr.ITEM_API, dict(
                            base, shopCode=shop, keyword=kw, hits=100, page=page), site_url)
                    except SystemExit:
                        data = {}
                    for it in data.get("Items", []):
                        if it.get("affiliateUrl") and url_matches(it, shop, slug):
                            found = it["affiliateUrl"]
                            break
                    if found or page >= (data.get("pageCount") or 1):
                        break
                if found:
                    break
        if found:
            mapping[url] = found
            print("  ○ %d/%d %s" % (i, len(bare), url))
        else:
            missed.append(url)
            print("  × %d/%d %s（見つからない）" % (i, len(bare), url))

    replaced = 0
    for path, text in files.items():
        new = text
        for old, afl in mapping.items():
            new = new.replace('href="%s"' % old, 'href="%s"' % afl.replace("&", "&amp;"))
        if new != text:
            replaced += sum(1 for old in mapping if ('href="%s"' % old) in text)
            print("%s: 差し替え" % path.relative_to(ROOT))
            if not dry_run:
                path.write_text(new, encoding="utf-8")
    print("\n== 結果（第3段） ==")
    print("取得できたリンク: %d/%d件、差し替えた場所: %dか所" % (len(mapping), len(bare), replaced))
    for url in missed:
        print("  取れなかった: %s" % url)
    print("次は: python3 build.py → git add -A → git commit → git pull → git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())

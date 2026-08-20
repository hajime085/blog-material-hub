#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
楽天市場APIから商品を取得して products.json を更新する。

  cp .env.example .env      # 初回だけ。.env に楽天のID・キーを書く
  python3 fetch_rakuten.py          # 値下がりした商品だけを取得（通常運転）
  python3 fetch_rakuten.py --seed   # 初回用。いま買える商品を「ウォッチ中」として掲載
  python3 fetch_rakuten.py --featured  # featured.txt のURLから「注目商品」を作る
  python3 fetch_rakuten.py food     # 特定カテゴリだけ

  そのあと  python3 build.py  でサイトを再生成する。


■ 割引率について（重要）

楽天の商品検索APIは「通常価格・定価」を返しません。返るのは現在価格だけです。
そのため当スクリプトは price_history.json に価格の観測履歴を持ち、
「過去60日で観測した最高値」を比較の基準にします。
これは定価ではなく当サイトが実際に見た価格なので、
サイト上の表記も「通常」ではなく「以前」になります（priceBasis: "history"）。

products.json の listPrice を手で書いた場合はそちらが優先され、
表記は「通常」になります（priceBasis: "manual"）。

初回は履歴が無いので、値下がりを1件も検知できません。
そのため初回だけ --seed を付けて実行します。これは値下がりを主張せず、
「ウォッチ中」のタグを付けて商品を並べ、同時に価格を記録します。
翌日以降に通常運転で回すと、値下がりを検知したものから %OFF が付いていきます。
"""

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))

# 2026年のインフラ刷新で、エンドポイントとパラメータが変わっている。
#   旧: https://app.rakuten.co.jp/services/api/...      （applicationId のみ）
#   新: https://openapi.rakuten.co.jp/ichibams/api/...  （applicationId + accessKey）
# さらに Origin / Referer を見てアクセス元を検査しており、
# アプリ設定の「許可されたWebサイト」に無いところからは 403 になる。
API_BASE = "https://openapi.rakuten.co.jp/ichibams/api"

# バージョンは新ゲートウェイで振り直されている。旧仕様の 20220601 を投げると
# 「API Configuration not found」になるので注意（存在しないバージョンと同じ扱い）。
ITEM_API_VERSION = "20260401"
ITEM_API = API_BASE + "/IchibaItem/Search/" + ITEM_API_VERSION

# ジャンル検索は、いまのところこのゲートウェイ上に見当たらない（404）。
# 商品検索のレスポンスに GenreInformation が含まれるので、実用上は困らない。
GENRE_API = API_BASE + "/IchibaGenre/Search/" + ITEM_API_VERSION

HISTORY_DAYS = 60          # 比較基準にする観測期間
REQUEST_INTERVAL = 1.1     # 楽天APIは秒間1リクエストが目安

# 取得したデータで上書きしてよいフィールド。
# ここに無いもの（caption / points / description / tags / hidden など）は
# 手で書いた内容をそのまま残す。
API_FIELDS = ("title", "rawTitle", "price", "image", "affiliateUrl", "shop",
              "reviewAverage", "reviewCount", "itemCode", "genreId")


# ------------------------------------------------------------------ helpers
def load_json(name, default=None):
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(name, obj):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_dotenv():
    """.env があれば環境変数として読み込む。
    IDをコードにもチャットにも残さないための入り口。"""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), val)


def credentials(cfg):
    load_dotenv()
    app_id = os.environ.get("RAKUTEN_APP_ID") or cfg["rakuten"].get("appId") or ""
    access_key = os.environ.get("RAKUTEN_ACCESS_KEY") or cfg["rakuten"].get("accessKey") or ""
    aff_id = os.environ.get("RAKUTEN_AFFILIATE_ID") or cfg["rakuten"].get("affiliateId") or ""
    if not app_id:
        sys.exit(
            "アプリIDが見つかりません。\n\n"
            "  1. .env.example を .env にコピーする\n"
            "       cp .env.example .env\n"
            "  2. .env を開いて、あなたのIDを書き込む\n\n"
            ".env は .gitignore に入っているのでコミットされません。\n"
            "（アプリIDの取得: https://webservice.rakuten.co.jp/app/create ）"
        )
    if not access_key:
        sys.exit(
            "アクセスキー（accessKey）が見つかりません。\n\n"
            "2026年のAPI刷新で、アプリIDに加えて accessKey が必須になりました。\n"
            "楽天ウェブサービスのアプリ管理画面で確認して、.env の\n"
            "  RAKUTEN_ACCESS_KEY=...\n"
            "に書き込んでください。"
        )
    if not aff_id:
        print("[注意] アフィリエイトIDが未設定です。リンクは通常の商品URLになります。", file=sys.stderr)
    return app_id, access_key, aff_id


def api_get(url, params, site_url):
    """新APIは Origin / Referer でアクセス元を見ている。
    アプリ設定の「許可されたWebサイト」と一致する値を必ず送る。"""
    origin = site_url.rstrip("/")
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(url + "?" + query, headers={
        "User-Agent": "yasumiru-builder/1.0",
        "Origin": origin,
        "Referer": origin + "/",
        "Accept": "application/json",
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            # 流量制限。少し待てば通るので、間隔を空けて数回試す。
            if ex.code == 429 and attempt < 3:
                wait = 5 * (attempt + 1)
                print("     （混み合っています。%d秒待ちます）" % wait)
                time.sleep(wait)
                continue
            break
    try:
        raise ex
    except urllib.error.HTTPError as ex:
        body = ""
        try:
            body = ex.read().decode("utf-8", "replace")[:400]
        except Exception:                                   # noqa: BLE001
            pass
        if ex.code == 403:
            raise SystemExit(
                "\n403 Forbidden: アクセス元が許可されていません。\n\n"
                "確認するところ:\n"
                "  1. 楽天ウェブサービスのアプリ設定「許可されたWebサイト」に\n"
                "     %s が入っているか（https:// と末尾の / は付けない）\n"
                "  2. アプリケーションタイプが「Webアプリケーション」になっているか\n"
                "  3. config.json の site.url が実際のドメインと一致しているか\n"
                "     （いまスクリプトが名乗っているのは %s です）\n\n"
                "サーバーの返答: %s"
                % (origin.replace("https://", "").replace("http://", ""), origin, body)
            )
        if ex.code == 400 and "API Configuration not found" in body:
            raise SystemExit(
                "\n400: そのAPIバージョンが存在しません。\n\n"
                "楽天はゲートウェイ刷新にあわせてAPIのバージョン番号を振り直しています。\n"
                "いまスクリプトが使っているのは %s です。\n"
                "楽天側でまた更新された場合、このエラーが出ます。\n\n"
                "対処: fetch_rakuten.py の ITEM_API_VERSION を新しい番号に変えてください。\n"
                "（アプリIDやアクセスキーが原因なら、それぞれ別のエラーが返ります）\n\n"
                "サーバーの返答: %s" % (ITEM_API_VERSION, body)
            )
        if ex.code == 400:
            raise SystemExit(
                "\n400 Bad Request: パラメータに問題があります。\n\n"
                "サーバーの返答: %s" % body
            )
        if ex.code == 403 and "Invalid Access Key" in body:
            raise SystemExit(
                "\n403: accessKey が正しくありません。\n"
                ".env の RAKUTEN_ACCESS_KEY を確認してください。\n\n"
                "サーバーの返答: %s" % body
            )
        raise SystemExit("\nHTTP %s エラー: %s\n%s" % (ex.code, ex.reason, body))


# 楽天の商品名は、店舗が付けた販促の飾りで埋まっていることが多い。
#   例: ＼送料無料／【あす楽】★店内最安値★ 商品名 まとめ買い
# そのまま並べるとカードが読めなくなるので、飾りは外して
# 意味のあるものはタグへ移す。
PROMO_WORDS = ["送料無料", "あす楽", "ポイント", "最安", "在庫処分", "セール", "SALE",
               "訳あり", "アウトレット", "まとめ買い", "限定", "クーポン", "実質", "P5倍",
               "翌日配送", "即納", "新品", "正規品", "父の日", "母の日", "お中元", "お歳暮",
               "円以上", "注文可能", "エントリー", "倍", "ゆうパケット", "宅配便", "配送",
               "楽天1位", "楽天2位", "楽天3位", "位受賞", "ランキング", "高評価", "レビュー",
               "お届け", "出荷", "翌日", "当日", "発送", "在庫あり", "日以内"]
TAGGABLE = {"送料無料": "送料無料", "あす楽": "あす楽", "まとめ買い": "まとめ買い",
            "訳あり": "訳あり", "アウトレット": "アウトレット", "在庫処分": "在庫処分"}


def clean_title(raw):
    """飾りを落として、商品名として読める形にする。

    先頭の飾りは何層にも重なっている:
      77%OFF!【期間限定：990円～1,390円！】【年間ランキング3位】 UVパーカー
    括弧を剥がすと裸の煽りが現れ、煽りを剥がすと括弧が現れる。
    そのため、変化しなくなるまで剥がし続ける。
    """
    # 括弧にも記号にも囲まれていない、先頭の売り文句
    LEAD2 = (r"(ゆうパケット[^ 　]{0,10}|[^ 　]{0,6}送料\d+円|\d+円以上で注文可能|"
             r"\d+月限定\s*[（(]?要エントリー[）)]?|要エントリー|"
             r"エントリーで[^ 　]{0,10}|最大\d+倍|全品\d+%?[オフOFF]+)")
    LEAD = (r"(\d+\s*[%％]\s*(?:OFF|オフ|off)[!！]?|店内最安値|最安値挑戦?中?|最安値|"
            r"期間限定|数量限定|タイムセール|超特価|激安|"
            r"[PpＰ]\s?\d+\s?(倍|限定)|ポイント\s?\d+倍|楽天ランキング\s?\d+位|"
            r"楽天\s?\d+位|ランキング\s?\d+位|\d+年間MVP|年間MVP|"
            r"まとめて購入がお得[♪！!]?|買うほどお得|お得♪|"
            r"[PpＰ]\s?\d+限定|\d+限定|1位|第\d+位|"
            r"本日限り|即日発送|翌日発送|新生活|大特価|お買い得|SALE|セール)")
    PRICE_PITCH = re.compile(r"\d\s*[円¥￥].{0,10}(価格|セット|購入|以上|ポッキリ|OFF|オフ|引|off)"
                             r"|(価格|ポッキリ|OFF|オフ|クーポン|セール).{0,8}\d\s*[円¥￥]"
                             r"|\d+\s*%\s*(OFF|オフ|off)|ポッキリ|提供価格"
                             r"|\d[\d,]*\s*円\s*[～〜→]")

    t = raw
    for _ in range(10):
        before = t
        t = re.sub(r"[＼\\][^／/]{0,44}[／/]", " ", t)          # ＼送料無料／ のたぐい
        t = re.sub(r"[★☆◆◇■□●○▼▲！!]+", " ", t)              # 装飾記号
        t = re.sub(r"\s+", " ", t).strip()
        t = re.sub(r"^\d\.\d{1,2}\s+(?=[^\d])", "", t)        # 先頭に紛れた評価値

        # 先頭の括弧のうち、販促・値段の話・単価のものを剥がす
        m = re.match(r"\s*[【《『\[（(]([^】》』\]）)]{1,28})[】》』\]）)]", t)
        if m:
            inner = m.group(1)
            if (any(w in inner for w in PROMO_WORDS)
                    or PRICE_PITCH.search(inner) or UNIT_RE.search(inner)):
                t = t[m.end():]

        # 裸の売り文句を剥がす
        for pat in (LEAD2, LEAD):
            m = re.match(r"\s*" + pat + r"\s*[ 　/／|｜、,・:：]*", t)
            if m and m.end() > 0:
                t = t[m.end():]

        t = re.sub(r"\s+", " ", t).strip(" 　-–—/／|｜,、・:：")
        if t == before:
            break

    # 末尾によくある店舗の符牒（@zb など）を落とす
    t = re.sub(r"\s+[@＠][A-Za-z0-9_]{1,8}$", "", t)
    if len(t) > 52:
        cut = t[:52]
        sp = cut.rfind(" ")
        t = (cut[:sp] if sp > 30 else cut).rstrip(" 　") + "…"
    return t or raw[:52]


# 「1枚あたり1,528円」のような単価は、商品名から消すのではなく
# 値札の下段（unitNote）へ移す。このサイトで一番効く情報なので。
UNIT_RE = re.compile(r"(\d+\s*(?:枚|本|個|袋|食|包|回|杯|粒|着|足|セット|kg|g|ml|L)\s*"
                     r"あたり\s*[約]?\s*[¥￥]?\d[\d,]*\s*[円¥￥])")


def unit_note_from_title(raw):
    m = UNIT_RE.search(raw)
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1))


def tags_from_title(raw):
    found = []
    for needle, tag in TAGGABLE.items():
        if needle in raw and tag not in found:
            found.append(tag)
    return found[:2]


def product_id(item_code):
    return "r" + hashlib.sha1(item_code.encode("utf-8")).hexdigest()[:9]


def big_image(urls):
    """楽天のサムネイルURLは _ex= でサイズを指定できる"""
    if not urls:
        return ""
    url = urls[0] if isinstance(urls[0], str) else urls[0].get("imageUrl", "")
    if "_ex=" in url:
        url = url.split("_ex=")[0] + "_ex=600x600"
    return url


# ------------------------------------------------------------------ history
def update_history(history, pid, price, today):
    rec = history.setdefault(pid, [])
    if rec and rec[-1][0] == today:
        rec[-1] = [today, price]
    else:
        rec.append([today, price])
    cutoff = (datetime.now(JST) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    history[pid] = [r for r in rec if r[0] >= cutoff][-HISTORY_DAYS:]
    return history[pid]


def reference_price(records, current):
    """過去に観測した最高値。現在価格より高いときだけ比較基準になる。"""
    highs = [p for _, p in records if p > current]
    return max(highs) if highs else None


# ------------------------------------------------------------------ fetching
def fetch_category(cat, app_id, access_key, aff_id, hits, site_url, ng_keyword="",
                   sort_by="-reviewCount"):
    """1カテゴリぶんの商品を取得して dict のリストで返す"""
    found = {}
    # ジャンルIDが分かっているカテゴリは、そのジャンルの中だけを見る。
    # キーワード検索はジャンルをまたいで散らばるため
    # （ベビーに大人用おむつ、ペットにゴミ箱が混ざるのはこれが原因）。
    queries = [(g, "") for g in (cat.get("genres") or [])]
    if not queries:
        queries = [("", k) for k in (cat.get("keywords") or [cat["label"]])]

    for genre_id, keyword in queries:
        params = {
            "applicationId": app_id,
            "accessKey": access_key,
            "affiliateId": aff_id,
            "keyword": keyword or None,
            "genreId": genre_id or None,
            "hits": min(hits, 30),
            "minPrice": cat.get("minPrice") or None,
            "maxPrice": cat.get("maxPrice") or None,
            "NGKeyword": ng_keyword or None,
            "sort": sort_by,
            "imageFlag": 1,
            "availability": 1,
            "format": "json",
            "formatVersion": 2,
        }
        try:
            data = api_get(ITEM_API, params, site_url)
        except SystemExit:
            raise
        except Exception as ex:                       # noqa: BLE001
            print("  × %s%s の取得に失敗: %s" % (genre_id, keyword, ex), file=sys.stderr)
            time.sleep(REQUEST_INTERVAL)
            continue

        for item in data.get("Items", []):
            code = item.get("itemCode") or ""
            if not code:
                continue
            raw_name = (item.get("itemName") or "").strip()
            found[code] = {
                "srcGenre": genre_id or keyword,
                "itemCode": code,
                "title": clean_title(raw_name),
                # 整形前の商品名。整形ルールを変えたとき、
                # APIを叩き直さずに作り直せるようにしておく。
                "rawTitle": raw_name,
                "autoTags": tags_from_title(raw_name),
                "unitNote": unit_note_from_title(raw_name),
                "price": int(item.get("itemPrice") or 0),
                "image": big_image(item.get("mediumImageUrls") or item.get("smallImageUrls")),
                "affiliateUrl": item.get("affiliateUrl") or item.get("itemUrl") or "",
                "shop": (item.get("shopName") or "").strip(),
                "reviewAverage": item.get("reviewAverage") or None,
                "reviewCount": item.get("reviewCount") or 0,
                "genreId": str(item.get("genreId") or ""),
                # postageFlag: 0=送料込み, 1=送料別
                "freeShipping": item.get("postageFlag") == 0,
            }
        print("  ・%-26s → %d件" % (
            ("ジャンル " + genre_id) if genre_id else ("「%s」" % keyword),
            len(data.get("Items", []))))
        time.sleep(REQUEST_INTERVAL)
    return list(found.values())


ITEM_URL_RE = re.compile(r"item\.rakuten\.co\.jp/([^/]+)/([^/?#]+)")


def item_code_from_url(url, ctx=None, timeout=20):
    """貼られたURLから商品コード（ショップ名:番号）を取り出す。

    商品URLの末尾はショップが決めるもので、数字のこともあれば
    英字のスラッグのこともある。APIの商品コードは常に数字なので、
    スラッグの場合はショップの商品を走査して itemUrl で照合する。

    一度解けたものは .featured_cache.json に覚えるので、次からは即座に解決する。
    """
    cache_path = os.path.join(ROOT, ".featured_cache.json")
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
        except ValueError:
            cache = {}
    if url in cache:
        return cache[url]

    def remember(code):
        if code:
            cache[url] = code
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
        return code

    # 「ショップ名:番号」の形なら、そのまま商品コードとして使う。
    # ランキングページから拾うときはこの形が確実で、余計な問い合わせもいらない。
    if re.fullmatch(r"[A-Za-z0-9_-]+:[0-9]+", url.strip()):
        return url.strip()

    target = url
    m = ITEM_URL_RE.search(target)
    if not m:
        # pc= に元のURLが入っている形
        parsed = urllib.parse.urlparse(target)
        for key in ("pc", "url", "m"):
            vals = urllib.parse.parse_qs(parsed.query).get(key)
            if vals:
                m = ITEM_URL_RE.search(urllib.parse.unquote(vals[0]))
                if m:
                    break
    if not m:
        # 短縮URLはたどってみる
        try:
            req = urllib.request.Request(target, headers={"User-Agent": "yasumiru-builder/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                m = ITEM_URL_RE.search(res.geturl())
        except Exception:                                 # noqa: BLE001
            pass
    if not m:
        return None

    shop, slug = m.group(1), m.group(2)

    # 末尾が数字なら、それがそのまま商品コードであることが多い
    if slug.isdigit():
        return remember("%s:%s" % (shop, slug))

    # そうでなければショップの商品を走査して itemUrl で照合する
    if not ctx:
        return None
    app_id, access_key, aff_id, site_url = ctx
    needle = "/%s/" % slug
    for page in range(1, 4):
        try:
            data = api_get(ITEM_API, {
                "applicationId": app_id, "accessKey": access_key, "affiliateId": aff_id,
                "shopCode": shop, "page": page, "hits": 30,
                "format": "json", "formatVersion": 2,
            }, site_url)
        except Exception:                                 # noqa: BLE001
            return None
        items = data.get("Items") or []
        for it in items:
            if needle in (it.get("itemUrl") or ""):
                return remember(it.get("itemCode"))
        if page >= (data.get("pageCount") or 1):
            break
        time.sleep(REQUEST_INTERVAL)
    return None


def build_featured(cfg, app_id, access_key, aff_id, site_url):
    """featured.txt に貼られたURLから「編集部が選んだもの」を作る。

    楽天アフィリエイトの売れ筋一覧は「実際に売れた商品」を
    発生報酬額の順に並べたもの。売れているのは事実だが、
    順位は売れた個数ではなく報酬額の順なので、高額・高料率の商品ほど上に来る。
    このサイトは安さを紹介する場所なので、リストは使うが順位は使わない。

    そのために、貼られた順番は捨てて安い順に並べ替え、
    サイトの基準（価格帯・レビュー）に合わないものは弾いて理由を伝える。
    料率は取り込まないし、どこにも表示しない。"""
    path = os.path.join(ROOT, "featured.txt")
    if not os.path.exists(path):
        print("featured.txt がありません。")
        return

    # 「## ジャンル名」で区切ると、ジャンルごとに均等に採る。
    urls, section = [], ""
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("##"):
            section = line.lstrip("#").strip()
        elif line and not line.startswith("#"):
            urls.append((line, section))
    if not urls:
        print("featured.txt にURLがありません。商品ページのURLを1行ずつ貼ってください。")
        return

    # サイトの基準は機械が一貫して適用する。
    # 売れ筋一覧は発生報酬額の順なので、高額・高料率の商品ほど上に来る。
    # そこに人の裁量が入ると基準がブレるため、ここで機械的に弾く。
    max_price = cfg["rakuten"].get("featuredMaxPrice", 5000)
    min_reviews = cfg["rakuten"].get("minReviewCount", 0)
    min_rating = cfg["rakuten"].get("minReviewAverage", 0)
    per_section = cfg["rakuten"].get("featuredPerSection", 4)
    total_max = cfg["rakuten"].get("featuredMax", 12)

    print("▼ 取り込みます（%d件）" % len(urls))
    items, seen, rejected = [], set(), []
    for url, section in urls:
        code = item_code_from_url(url, ctx=(app_id, access_key, aff_id, site_url))
        if not code:
            print("  × 商品コードを読み取れません: %s" % url[:56])
            print("      （ランキングページの「ショップ名:番号」を書くと確実です）")
            continue
        if code in seen:
            print("  ・重複のため飛ばします: %s" % code)
            continue
        seen.add(code)
        try:
            data = api_get(ITEM_API, {
                "applicationId": app_id, "accessKey": access_key, "affiliateId": aff_id,
                "itemCode": code, "format": "json", "formatVersion": 2,
            }, site_url)
        except SystemExit as ex:
            # URLの打ち間違いなど、その1件だけの問題なら飛ばして続ける。
            # 1件のせいで取り込み全体が止まると、原因も分かりにくい。
            if "itemCode is not valid" in str(ex) or "wrong_parameter" in str(ex):
                print("  × 商品コードが正しくありません: %s" % code)
                time.sleep(REQUEST_INTERVAL)
                continue
            raise
        except Exception as ex:                          # noqa: BLE001
            print("  × 取得に失敗: %s (%s)" % (code, ex))
            time.sleep(REQUEST_INTERVAL)
            continue

        got = data.get("Items") or []
        if not got:
            print("  × 見つかりません（売り切れ・掲載終了の可能性）: %s" % code)
            time.sleep(REQUEST_INTERVAL)
            continue

        it = got[0]
        raw_name = (it.get("itemName") or "").strip()
        price = int(it.get("itemPrice") or 0)
        rc = it.get("reviewCount") or 0
        ra = float(it.get("reviewAverage") or 0)

        why = None
        if price > max_price:
            why = "¥%s（上限 ¥%s）" % ("{:,}".format(price), "{:,}".format(max_price))
        elif rc < min_reviews:
            why = "レビュー%d件（%d件以上）" % (rc, min_reviews)
        elif ra < min_rating:
            why = "評価★%.1f（★%.1f以上）" % (ra, min_rating)
        if why:
            rejected.append((clean_title(raw_name), why, section))
            print("  − %-38s %s" % (clean_title(raw_name)[:38], why))
            time.sleep(REQUEST_INTERVAL)
            continue

        items.append({
            "section": section,
            "id": "f" + product_id(code)[1:],
            "itemCode": code,
            "title": clean_title(raw_name),
            "rawTitle": raw_name,
            "price": price,
            "image": big_image(it.get("mediumImageUrls") or it.get("smallImageUrls")),
            "affiliateUrl": it.get("affiliateUrl") or it.get("itemUrl") or "",
            "shop": (it.get("shopName") or "").strip(),
            "reviewAverage": it.get("reviewAverage") or None,
            "reviewCount": rc,
            "unitNote": unit_note_from_title(raw_name),
            "freeShipping": it.get("postageFlag") == 0,
            "lastSeen": datetime.now(JST).strftime("%Y-%m-%d"),
        })
        print("  ・¥%-8s %s" % ("{:,}".format(items[-1]["price"]), items[-1]["title"][:38]))
        time.sleep(REQUEST_INTERVAL)

    # 貼られた順番は捨てる。安い順に並べ替えるのが、このサイトの基準。
    items.sort(key=lambda x: x["price"])

    # ジャンルごとの上限。1つのジャンルが棚を占領しないように、
    # 安い順から順番に1件ずつ拾う。
    if per_section:
        buckets = {}
        for it in items:
            buckets.setdefault(it.get("section", ""), []).append(it)
        picked, taken = [], {k: 0 for k in buckets}
        while len(picked) < total_max and any(buckets[k] for k in buckets):
            for k in list(buckets):
                if not buckets[k] or taken[k] >= per_section or len(picked) >= total_max:
                    continue
                picked.append(buckets[k].pop(0))
                taken[k] += 1
        dropped_over = len(items) - len(picked)
        items = sorted(picked, key=lambda x: x["price"])
        if dropped_over:
            print("\n  ・上限を超えたぶんは見送りました（%d件）" % dropped_over)

    save_json("featured.json", {
        "_readme": ("featured.txt から作った「編集部が選んだもの」。"
                    "直接編集せず、featured.txt を編集してください。"
                    "並びは貼った順ではなく安い順です（売れ筋の順位はサイトの基準ではないため）。"),
        "updatedAt": datetime.now(JST).strftime("%Y-%m-%d"),
        "items": items,
    })
    print("\n✅ featured.json を更新しました（%d件）" % len(items))
    if items:
        print("   並びは安い順です。貼った順（＝売れ筋の順位）は使っていません。")
        print("   ¥%s 〜 ¥%s" % ("{:,}".format(items[0]["price"]), "{:,}".format(items[-1]["price"])))
    if rejected:
        print("\n   基準に合わず見送り: %d件" % len(rejected))
        for title, why, sec in rejected:
            print("     − %-34s %s" % (title[:34], why))
    print("\n次: python3 build.py")


def report_genres(cfg, app_id, access_key, aff_id, site_url):
    """ジャンル検索APIが使えないので、実際の検索結果から
    そのカテゴリに何のジャンルが混ざっているかを調べる。
    ここで出たIDを config.json の genreId に入れると、
    カテゴリ違いの商品が入りにくくなる。"""
    from collections import Counter
    ng = cfg["rakuten"].get("ngKeyword", "")
    for cat in cfg["categories"]:
        print("\n▼ %s (%s)" % (cat["label"], cat["slug"]))
        items = fetch_category(cat, app_id, access_key, aff_id, 30, site_url, ng)
        counter = Counter(i.get("genreId", "") for i in items if i.get("genreId"))
        samples = {}
        for i in items:
            samples.setdefault(i.get("genreId", ""), i["title"])
        for gid, n in counter.most_common(6):
            print("   %-9s %2d件  例: %s" % (gid, n, samples.get(gid, "")[:40]))


def show_genres(app_id, access_key, site_url, genre_id="0"):
    try:
        data = api_get(GENRE_API, {"applicationId": app_id, "accessKey": access_key,
                                   "genreId": genre_id, "format": "json"}, site_url)
    except SystemExit as ex:
        if "404" in str(ex):
            raise SystemExit(
                "ジャンル検索APIは、現在のゲートウェイでは提供されていないようです。\n\n"
                "config.json の keywords（検索ワード）で絞り込んでください。\n"
                "商品検索の結果には各商品の genreId が含まれるので、\n"
                "取得後の products.json から実際のジャンルIDを拾うこともできます。"
            )
        raise
    cur = data.get("current") or {}
    if cur:
        print("現在: %s (%s)" % (cur.get("genreName"), cur.get("genreId")))
    print("--- 子ジャンル ---")
    for child in data.get("children", []):
        g = child.get("child", child)
        print("%-10s %s" % (g.get("genreId"), g.get("genreName")))
    print("\n掘り下げるには: python3 fetch_rakuten.py --genres <genreId>")
    print("使いたいIDを config.json の該当カテゴリに \"genreId\": \"12345\" として足してください。")


# ------------------------------------------------------------------ main
def main():
    args = [a for a in sys.argv[1:]]
    cfg = load_json("config.json")
    app_id, access_key, aff_id = credentials(cfg)
    site_url = cfg["site"]["url"]

    if "--featured" in args:
        build_featured(cfg, app_id, access_key, aff_id, site_url)
        return

    if "--genres" in args:
        report_genres(cfg, app_id, access_key, aff_id, site_url)
        return

    seed = "--seed" in args
    args = [a for a in args if a != "--seed"]
    only = set(a for a in args if not a.startswith("-"))
    cats = [c for c in cfg["categories"] if not only or c["slug"] in only]
    if only and not cats:
        sys.exit("該当するカテゴリがありません: %s" % ", ".join(sorted(only)))

    existing_doc = load_json("products.json", {}) or {}
    existing = {p["id"]: p for p in existing_doc.get("products", [])}
    history = load_json("price_history.json", {}) or {}
    today = datetime.now(JST).strftime("%Y-%m-%d")
    min_off = cfg["rakuten"].get("minDiscountRate", 15)
    ng_keyword = cfg["rakuten"].get("ngKeyword", "")
    sort_by = cfg["rakuten"].get("sort", "-reviewCount")
    min_reviews = cfg["rakuten"].get("minReviewCount", 0)
    min_rating = cfg["rakuten"].get("minReviewAverage", 0)
    retention_days = cfg["rakuten"].get("retentionDays", 30)
    max_new = cfg["rakuten"].get("maxNewPerRun", 20)
    hits = cfg["rakuten"].get("hits", 30)

    kept, added, dropped = 0, 0, 0
    result = {}
    candidates = {}
    seed_per_category = cfg["rakuten"].get("seedPerCategory", 12)
    max_per_shop = cfg["rakuten"].get("maxPerShop", 2)

    for cat in cats:
        print("▼ %s" % cat["label"])
        for raw in fetch_category(cat, app_id, access_key, aff_id, hits, site_url,
                                  ng_keyword, sort_by):
            if not raw["price"] or not raw["title"]:
                continue
            # 実績のない商品は載せない。ランキングAPIが無い以上、
            # レビュー数と評価が「多くの人が実際に買った」ことの唯一の手がかりになる。
            if (raw.get("reviewCount") or 0) < min_reviews:
                dropped += 1
                continue
            if float(raw.get("reviewAverage") or 0) < min_rating:
                dropped += 1
                continue
            pid = product_id(raw["itemCode"])
            records = update_history(history, pid, raw["price"], today)

            prev = existing.get(pid, {})
            item = dict(prev)
            item["id"] = pid
            item["category"] = cat["slug"]
            for f in API_FIELDS:
                if f in raw:
                    item[f] = raw[f]

            # 手で書いた値があればそちらを基準にする
            if prev.get("priceBasis") == "manual" and prev.get("listPrice"):
                item["listPrice"] = prev["listPrice"]
                item["priceBasis"] = "manual"
            else:
                ref = reference_price(records, raw["price"])
                item["listPrice"] = ref
                item["priceBasis"] = "history" if ref else None

            item.setdefault("caption", "")
            item.setdefault("tags", [])
            for t in raw.get("autoTags", []):
                if t not in item["tags"]:
                    item["tags"].append(t)
            # 送料はタイトルの謳い文句ではなくAPIのフラグを正とする
            if raw.get("freeShipping") and "送料無料" not in item["tags"]:
                item["tags"].insert(0, "送料無料")
            elif not raw.get("freeShipping") and "送料無料" in item["tags"]:
                item["tags"].remove("送料無料")
            item.setdefault("points", [])
            item.setdefault("description", "")
            if raw.get("unitNote"):
                item["unitNote"] = raw["unitNote"]
            item.setdefault("unitNote", None)
            item.setdefault("postedAt", today)
            # 今日もAPIの結果に入っていた、という記録。
            # 掲載を続けるか消すかの判断と、価格の鮮度の表示に使う。
            item["lastSeen"] = today

            off = 0
            if item.get("listPrice"):
                off = round((item["listPrice"] - item["price"]) / item["listPrice"] * 100)

            if off >= min_off:
                if not prev and added >= max_new:
                    # 1回で載せる新規の上限。一気に増やさず、じわじわ増やす。
                    dropped += 1
                    continue
                # 値下がりを検知した。新着として浮上させる。
                item["postedAt"] = today
                item["tags"] = [t for t in item.get("tags", []) if t != "ウォッチ中"]
            elif seed and not prev:
                # 種まきモード。値下がりはまだ分からないので、
                # 割引の主張はせず「ウォッチ中」として置く。
                item["listPrice"] = None
                item["priceBasis"] = None
                if "ウォッチ中" not in item.get("tags", []):
                    item["tags"] = ["ウォッチ中"] + item.get("tags", [])
                candidates.setdefault(cat["slug"], []).append(
                    (raw.get("reviewCount") or 0, raw.get("shop", ""),
                     raw.get("srcGenre", ""), pid, item))
                continue
            elif not prev:
                dropped += 1
                continue          # 通常モードでは、値下がりしていない新規は載せない

            result[pid] = item
            if prev:
                kept += 1
            else:
                added += 1

    # 種まきモード: カテゴリごとに、レビュー数の多い順から一定数だけ採用する
    if seed:
        shop_total = {}
        max_total = max_per_shop * 2   # サイト全体では売場ごとの上限の2倍まで
        for slug, items in candidates.items():
            # ジャンルごとに分けてから順番に1件ずつ拾う。
            # まとめてレビュー数順に取ると、母数の大きいジャンルが
            # 売場をまるごと占領してしまう（ベビーが全部おくるみになった）。
            buckets = {}
            for entry in items:
                buckets.setdefault(entry[2], []).append(entry)
            for b in buckets.values():
                b.sort(key=lambda x: -x[0])

            per_shop, taken = {}, 0
            order = list(buckets)
            while taken < seed_per_category and any(buckets[g] for g in order):
                for g in order:
                    if taken >= seed_per_category:
                        break
                    while buckets[g]:
                        _, shop, _, pid, item = buckets[g].pop(0)
                        if per_shop.get(shop, 0) >= max_per_shop:
                            continue
                        if shop_total.get(shop, 0) >= max_total:
                            continue
                        per_shop[shop] = per_shop.get(shop, 0) + 1
                        shop_total[shop] = shop_total.get(shop, 0) + 1
                        result[pid] = item
                        taken += 1
                        added += 1
                        break
            dropped += max(0, len(items) - taken)

    # APIの結果から外れた商品の扱い。
    # レビュー数順の上位しか取らないため、売り切れていなくても
    # 順位が落ちただけで結果から外れる。すぐ消すと、Threadsに貼った
    # リンクが次々404になる。最後に見かけた日から retention_days は残す。
    cutoff = (datetime.now(JST) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    kept_stale, expired = 0, 0
    for pid, p in existing.items():
        if pid in result:
            continue
        last = p.get("lastSeen") or p.get("postedAt") or today
        if last >= cutoff:
            result[pid] = p
            kept_stale += 1
        else:
            expired += 1

    products = sorted(result.values(),
                      key=lambda p: (p.get("postedAt", ""), -p.get("price", 0)), reverse=True)

    # 中身のある products.json を空で上書きしない。
    # 値下がりが1件も無い日に、サイトが丸ごと消えるのを防ぐ。
    if not products and existing:
        save_json("price_history.json", history)
        raise SystemExit(
            "\n値下がりしている商品が1件も見つかりませんでした。\n"
            "既存の %d件 はそのまま残します（products.json は変更していません）。\n\n"
            "価格の履歴 %d件 は記録したので、明日以降の比較に使われます。\n\n"
            "まだ履歴が浅くて値下がりを検知できない場合は、\n"
            "  python3 fetch_rakuten.py --seed\n"
            "で、いま買える商品を「ウォッチ中」として掲載できます。"
            % (len(existing), len(history))
        )

    save_json("products.json", {
        "_readme": "商品データ。手で書いた caption / tags / points / description / hidden は "
                   "fetch_rakuten.py を再実行しても保持されます。",
        "updatedAt": today,
        "products": products,
    })
    save_json("price_history.json", history)

    no_caption = sum(1 for p in products if not p.get("caption"))
    print("\n✅ products.json を更新しました")
    print("   新規 %d件 / 更新 %d件 / 見送り %d件 → 合計 %d件" % (added, kept, dropped, len(products)))
    if kept_stale or expired:
        print("   掲載継続 %d件（今日はAPIの結果に無いが%d日以内）/ 掲載終了 %d件"
              % (kept_stale, retention_days, expired))
    if no_caption:
        print("   ※ ひとことキャプション未記入が %d件あります。" % no_caption)
        print("      caption を書くとカードの見え方がかなり変わります。")
    print("\n次: python3 build.py")


if __name__ == "__main__":
    main()

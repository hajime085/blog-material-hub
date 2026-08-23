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
              "reviewAverage", "reviewCount", "itemCode", "genreId",
              "startTime", "endTime")


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
    # except を抜けると例外変数は消えるので、外の変数に控えておく。
    failure = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            failure = ex
            # 流量制限。少し待てば通るので、間隔を空けて数回試す。
            if ex.code == 429 and attempt < 3:
                wait = 5 * (attempt + 1)
                print("     （混み合っています。%d秒待ちます）" % wait)
                time.sleep(wait)
                continue
            break

    if failure is not None:
        ex = failure
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

    raise SystemExit("\n楽天APIに接続できませんでした。ネットワークを確認してください。")


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
    LEAD2 = (r"(クーポン[^ 　]{0,4}で?[^ 　]{0,16}円[～〜]?|"
             r"ゆうパケット[^ 　]{0,10}|[^ 　]{0,6}送料\d+円|\d+円以上で注文可能|"
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
        # 先頭の「3,990円→2990円」。値段は価格欄に出すので商品名には要らない。
        t = re.sub(r"^\s*[\d,]{3,9}\s*円\s*[→⇒]\s*[\d,]{3,9}\s*円[！!]?\s*", "", t)

        # 先頭の括弧のうち、販促・値段の話・単価のものを剥がす。
        # 中身が販促だと分かっている場合だけ剥がすので、長さは緩めでよい。
        # 「8/21 10時〜24H限定：1枚1,290円 2枚購入クーポンで」のように長い。
        m = re.match(r"\s*[【《『\[（(]([^】》』\]）)]{1,40})[】》』\]）)]", t)
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


ARROW_RE = re.compile(
    r"([\d,]{3,9})\s*円\s*(?:→|⇒|->|=>|▶)\s*([\d,]{3,9})\s*円")


def list_price_from_title(raw_title, price):
    """タイトルに書かれた「3,790円→1,999円」から、セール前の価格を読む。

    楽天APIは定価を返さない。一方で値引きの大きい商品ほど、
    ショップ自身がタイトルに「◯円→◯円」と書いている。
    これを使えば、価格履歴が溜まるのを待たずに %OFF を出せる。

    ただしタイトルはショップが自由に書ける宣伝文で、そのままでは信用できない。
    そこで「矢印の右側が、いま実際に売られている価格と一致すること」を条件にする。
    一致していれば、左側がセール前だという主張はその場で裏が取れる。

    「77%OFF!【990円〜1,390円】」のように右側が価格帯の形式は、
    どれがセール前か決められないので採用しない。
    「50円OFFクーポン」のような割引額だけの表記も、値下げではないので読まない。
    """
    if not raw_title or not price:
        return None
    for m in ARROW_RE.finditer(raw_title):
        try:
            before = int(m.group(1).replace(",", ""))
            after = int(m.group(2).replace(",", ""))
        except ValueError:
            continue
        # 右側が実売価格と一致しなければ、別の商品や別容量の話。読まない。
        if after != int(price):
            continue
        if before <= after:
            continue
        # 桁の打ち間違いや「1円→」のような釣りを弾く。
        if before > after * 20:
            continue
        if (before - after) / before < 0.05:
            continue
        return before
    return None


# 「クーポン利用で5kgあたり2250円」のように、条件付きの単価。
# 実際に払う額と違うので、そのまま単価として出すと嘘になる。
CONDITIONAL_UNIT = re.compile(
    r"(クーポン|エントリー|まとめ買い|\d+個以上|\d+点以上|セット購入|"
    r"最大|実質|ポイント|同梱|複数購入)[^。]{0,12}$")


def unit_note_from_title(raw):
    """タイトルに書かれた単価を読む。ただし条件付きのものは読まない。

    「5kgあたり2250円」と書いてあっても、その手前に「クーポン利用で」が
    付いていれば、それはクーポンを使ったときの値段。
    実売価格の横にそのまま並べると、読者に嘘をつくことになる。
    """
    m = UNIT_RE.search(raw)
    if not m:
        return None
    # 単価の表記より前の部分に条件が書かれていないかを見る。
    if CONDITIONAL_UNIT.search(raw[:m.start()]):
        return None
    return re.sub(r"\s+", "", m.group(1))


def tags_from_title(raw):
    found = []
    for needle, tag in TAGGABLE.items():
        if needle in raw and tag not in found:
            found.append(tag)
    return found[:2]


def sale_status(raw, tolerance_hours=24):
    """いま買えるかどうか。

    在庫あり(availability=1)でも、販売期間がまだ始まっていない商品がある。
    「9月3日20時から」のようなものを今日載せても、読者は買えない。
    特価サイトで一番やってはいけないことなので、ここで弾く。

    ただし「今夜20時から数量限定」のような近い開始は、
    予告として載せる価値があるので許容する（既定は24時間先まで）。

    戻り値: (載せてよいか, 理由, 開始日時)
    """
    now = datetime.now(JST)

    def parse(v):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                d = datetime.strptime(v, fmt)
                return d if d.tzinfo else d.replace(tzinfo=JST)
            except ValueError:
                continue
        return None

    start = parse(raw.get("startTime") or "") if raw.get("startTime") else None
    end = parse(raw.get("endTime") or "") if raw.get("endTime") else None

    if end and end < now:
        return False, "販売期間が終了しています（%s まで）" % end.strftime("%-m月%-d日"), None
    if start and start > now:
        hours = (start - now).total_seconds() / 3600
        if hours > tolerance_hours:
            return False, "販売開始が%s（%.0f日先）" % (start.strftime("%-m月%-d日 %-H時"), hours / 24), start
        return True, "", start
    return True, "", None


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
    """その日の観測値を記録する。1日1件。

    同じ日に何度も見に行く場合は、高いほうを残す。
    最新で上書きすると、昼にセールが始まった時点でその日の平常価格が消え、
    「何と比べて安いのか」が分からなくなる。
    比較の基準は過去の最高値なので、その日の最高値を残すのが正しい。
    """
    rec = history.setdefault(pid, [])
    if rec and rec[-1][0] == today:
        rec[-1] = [today, max(rec[-1][1], price)]
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
                   sort_by="-reviewCount", sale_keywords=()):
    """1カテゴリぶんの商品を取得して dict のリストで返す

    レビュー数の多い順に見るだけでは、値下がりした商品はほとんど拾えない。
    実測では4日間で値段が動いた商品は追跡3,089件のうち39件（1.3%）しかなく、
    上位に並ぶのは値段の動かない定番商品ばかりだった。

    そこで「OFF」「半額」のように、値引きを謳っている商品を
    直接探しにいく検索も足す。同じ300件を見ても、
    レビュー数順では0件、「半額」で探すと10件見つかる。
    """
    found = {}
    # ジャンルIDが分かっているカテゴリは、そのジャンルの中だけを見る。
    # キーワード検索はジャンルをまたいで散らばるため
    # （ベビーに大人用おむつ、ペットにゴミ箱が混ざるのはこれが原因）。
    genres = cat.get("genres") or []
    queries = [(g, "") for g in genres]
    if not queries:
        queries = [("", k) for k in (cat.get("keywords") or [cat["label"]])]
    # 値引きを謳っている商品を探す検索。ジャンルは絞ったまま。
    for g in genres:
        for kw in sale_keywords:
            queries.append((g, kw))

    # APIは1回30件までしか返さない。hits がそれより多ければページを送る。
    # 見る母数が増えるほど、値下がりに出くわす機会も増える。
    pages = max(1, -(-hits // 30))

    for genre_id, keyword in queries:
      # 値引き検索はページを送らない。母数より、上位の鮮度のほうが効く。
      n_pages = 1 if (genre_id and keyword) else pages
      for page in range(1, n_pages + 1):
        params = {
            "applicationId": app_id,
            "accessKey": access_key,
            "affiliateId": aff_id,
            "keyword": keyword or None,
            "genreId": genre_id or None,
            "hits": min(hits, 30),
            "page": page if page > 1 else None,
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

        # そのジャンルにこれ以上ページが無ければ、次のジャンルへ。
        if page >= (data.get("pageCount") or 1):
            last_page = True
        else:
            last_page = False

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
                # 販売期間。availability=1（在庫あり）でも、
                # 販売開始前なら買えない。買えないものは載せない。
                "startTime": item.get("startTime") or "",
                "endTime": item.get("endTime") or "",
                # postageFlag: 0=送料込み, 1=送料別
                "freeShipping": item.get("postageFlag") == 0,
            }
        print("  ・%-26s → %d件%s" % (
            ("ジャンル " + genre_id) if genre_id else ("「%s」" % keyword),
            len(data.get("Items", [])),
            "" if pages == 1 else "（%d/%dページ目）" % (page, pages)))
        time.sleep(REQUEST_INTERVAL)
        if last_page:
            break
    return list(found.values())


ITEM_URL_RE = re.compile(r"item\.rakuten\.co\.jp/([^/]+)/([^/?#]+)")


def item_code_from_page(item_url, shop, timeout=20):
    """商品ページのHTMLから、数字の商品コードを読み取る。

    楽天の商品URLの末尾はショップが自由に決めるもので、
    「ex-x01028」のような英字のこともある。一方APIの商品コードは数字で、
    英字のスラッグをそのまま渡しても itemCode is not valid になる。

    商品ページには数字のほうが埋まっているので、1枚読んで取り出す。
    ショップの商品を何百件も走査するより速く、確実。
    """
    url = item_url if item_url.startswith("http") else "https://" + item_url
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read(400000).decode("utf-8", "replace")
    except Exception:                                     # noqa: BLE001
        return None
    m = re.search(r'"itemId"\s*:\s*"?(\d+)', body)
    return "%s:%s" % (shop, m.group(1)) if m else None


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

    if not ctx:
        return None
    app_id, access_key, aff_id, site_url = ctx
    needle = "/%s/" % slug

    def same_item(it):
        # itemUrl はアフィリエイトURLで返ることがあり、
        # 元の商品URLは pc= の中にURLエンコードされて入っている。
        # そのまま文字列で探すと一致しないので、必ず戻してから比べる。
        return needle in urllib.parse.unquote(it.get("itemUrl") or "")

    # 商品ページ自体に数字の商品コードが埋まっている。
    # 「ex-x01028」のような英字のスラッグでも、ページを1枚読めば
    # 「10000794」が分かるので、ショップ全体を走査せずに済む。
    code = item_code_from_page(m.group(0), shop, timeout)
    if code:
        try:
            data = api_get(ITEM_API, {
                "applicationId": app_id, "accessKey": access_key, "affiliateId": aff_id,
                "itemCode": code, "format": "json", "formatVersion": 2,
            }, site_url)
        except SystemExit:
            data = {}
        except Exception:                                 # noqa: BLE001
            data = {}
        got = data.get("Items") or []
        # 貼られたURLと同じ商品かを確かめてから採用する。
        if got and same_item(got[0]):
            return remember(code)

    # 読み取れなければ、ショップの商品を順に見て itemUrl で照合する。
    for page in range(1, 11):
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
            if same_item(it):
                return remember(it.get("itemCode"))
        if page >= (data.get("pageCount") or 1):
            break
        time.sleep(REQUEST_INTERVAL)
    return None


def category_for_section(cfg, section):
    """featured.txt の「## 見出し」を、サイトのカテゴリに対応させる。

    手で貼った商品も、フィードとカテゴリページに出したい。
    そのためにはカテゴリが要るが、商品APIが返すジャンルIDは末端の細かいIDで、
    config.json が持つ大分類のIDとは繋がらない（ジャンル検索APIは使えない）。
    そこで、貼る側が見出しで示す形にする。
    スラッグでも、カテゴリ名でも、その一部でも通る。
    """
    if not section:
        return None
    s = section.strip()
    for c in cfg["categories"]:
        if s in (c["slug"], c["label"], c.get("short")):
            return c["slug"]
    for c in cfg["categories"]:
        if s and (s in c["label"] or s in (c.get("short") or "")):
            return c["slug"]
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
            # 「## fashion   # 服・靴・バッグ・時計」のように
            # 見出しの後ろに説明を書けるようにする。
            section = line.lstrip("#").split("#")[0].strip()
        elif line and not line.startswith("#"):
            # 行の後ろに書いたメモも落とす。
            line = line.split("#")[0].strip()
            if line:
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

        ok, sale_why, _ = sale_status(it, cfg["rakuten"].get("saleStartToleranceHours", 24))
        if not ok:
            rejected.append((clean_title(raw_name), sale_why, section))
            print("  − %-38s %s" % (clean_title(raw_name)[:38], sale_why))
            time.sleep(REQUEST_INTERVAL)
            continue

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
            "genreId": str(it.get("genreId") or ""),
            "startTime": (it.get("startTime") or "").strip(),
            "endTime": (it.get("endTime") or "").strip(),
            "autoTags": tags_from_title(raw_name),
            "listPrice": list_price_from_title(raw_name, price),
            "lastSeen": datetime.now(JST).strftime("%Y-%m-%d"),
        })
        print("  ・¥%-8s %s" % ("{:,}".format(items[-1]["price"]), items[-1]["title"][:38]))
        time.sleep(REQUEST_INTERVAL)

    # 貼られた順番は捨てる。安い順に並べ替えるのが、このサイトの基準。
    items.sort(key=lambda x: x["price"])

    # 基準を通ったものは全部フィードに載せる。
    # このあとの上限は「トップの棚に何件並べるか」の話であって、
    # 載せるかどうかの話ではない。
    all_items = list(items)

    # ジャンルごとの上限。1つのジャンルが棚を占領しないように、
    # 安い順から順番に1件ずつ拾う。
    if per_section:
        buckets = {}
        for it in items:
            buckets.setdefault(it.get("section", ""), []).append(it)
        picked, taken = [], {k: 0 for k in buckets}
        # 1周して1件も採れなかったら終わり。
        # 「上限に達したジャンルに商品が残っている」状態で
        # 残数だけを見て回し続けると、無限ループになる。
        while len(picked) < total_max:
            progressed = False
            for k in list(buckets):
                if not buckets[k] or taken[k] >= per_section or len(picked) >= total_max:
                    continue
                picked.append(buckets[k].pop(0))
                taken[k] += 1
                progressed = True
            if not progressed:
                break
        dropped_over = len(items) - len(picked)
        items = sorted(picked, key=lambda x: x["price"])
        if dropped_over:
            print("\n  ・トップの棚には入りきらないぶん（%d件）は、"
                  "フィードとカテゴリにだけ載せます" % dropped_over)

    merged, no_section = merge_featured_into_products(cfg, all_items)

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
    if merged:
        print("   フィードとカテゴリにも入れました（%d件）" % merged)
    if no_section:
        print("\n   ※ 見出しからカテゴリを判定できず、棚にだけ置いた商品が %d件 あります。"
              % len(no_section))
        for title, sec in no_section:
            print("     − %-30s 見出し「%s」" % (title[:30], sec or "なし"))
        print("     featured.txt の「##」を次のどれかにすると、フィードにも流れます:")
        print("       " + " / ".join(c["slug"] for c in cfg["categories"]))
    if rejected:
        print("\n   基準に合わず見送り: %d件" % len(rejected))
        for title, why, sec in rejected:
            print("     − %-34s %s" % (title[:34], why))
    print("\n次: python3 build.py")


def merge_featured_into_products(cfg, items):
    """手で選んだ商品を、products.json にも入れる。

    これまで featured.json は独立していて、棚に出るだけだった。
    その結果、値引きの大きい商品を手で拾っても、フィードにも
    カテゴリページにも現れないという状態になっていた。

    貼ったものは「載せると決めたもの」なので、
    APIの検索結果に出てくるかどうかとは無関係に載せ続ける（pinned）。
    ただし売り切れや販売終了で消えたときは、次の --featured で落ちる。

    手で書いた caption / tags / points / description は必ず残す。
    """
    doc = load_json("products.json", {}) or {}
    existing = {p["id"]: p for p in doc.get("products", [])}
    today = datetime.now(JST).strftime("%Y-%m-%d")
    history = load_json("price_history.json", {}) or {}

    merged, no_section = 0, []
    keep_pinned = set()
    for it in items:
        slug = category_for_section(cfg, it.get("section"))
        if not slug:
            no_section.append((it["title"], it.get("section")))
            continue
        pid = product_id(it["itemCode"])
        keep_pinned.add(pid)
        prev = existing.get(pid, {})
        p = dict(prev)
        p["id"] = pid
        p["category"] = slug
        p["pinned"] = True
        for f in API_FIELDS:
            if f in it:
                p[f] = it[f]

        records = update_history(history, pid, it["price"], today)
        if prev.get("priceBasis") == "manual" and prev.get("listPrice"):
            pass
        elif it.get("listPrice"):
            p["listPrice"] = it["listPrice"]
            p["priceBasis"] = "title"
        else:
            ref = reference_price(records, it["price"])
            p["listPrice"] = ref
            p["priceBasis"] = "history" if ref else None

        p.setdefault("caption", "")
        p.setdefault("points", [])
        p.setdefault("description", "")
        p.setdefault("tags", [])
        for t in it.get("autoTags", []):
            if t not in p["tags"]:
                p["tags"].append(t)
        if it.get("freeShipping") and "送料無料" not in p["tags"]:
            p["tags"].insert(0, "送料無料")
        elif not it.get("freeShipping") and "送料無料" in p["tags"]:
            p["tags"].remove("送料無料")
        if it.get("unitNote"):
            p["unitNote"] = it["unitNote"]
        p.setdefault("unitNote", None)
        p.setdefault("postedAt", today)
        p.setdefault("bumpedAt", datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S"))
        p["lastSeen"] = today
        existing[pid] = p
        if not prev:
            merged += 1

    # featured.txt から外した商品は、留め置きも解く。
    # 解いたあとは普通の商品として、30日の保持ルールに従う。
    for pid, p in existing.items():
        if p.get("pinned") and pid not in keep_pinned:
            p.pop("pinned", None)

    products = sorted(existing.values(),
                      key=lambda p: (p.get("postedAt", ""), -p.get("price", 0)),
                      reverse=True)
    doc["products"] = products
    doc["updatedAt"] = today
    save_json("products.json", doc)
    save_json("price_history.json", history)
    return merged, no_section


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
    # 数時間おきの見張り。値段の追跡が主で、新規は控えめにしか足さない。
    # キャプションの無いカードが一気に増えるのを避けるため。
    watch = "--watch" in args
    args = [a for a in args if a not in ("--seed", "--watch")]
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
    sale_tolerance = cfg["rakuten"].get("saleStartToleranceHours", 24)
    min_reviews = cfg["rakuten"].get("minReviewCount", 0)
    min_rating = cfg["rakuten"].get("minReviewAverage", 0)
    retention_days = cfg["rakuten"].get("retentionDays", 30)
    max_new = (cfg["rakuten"].get("watchMaxNewPerRun", 3) if watch
               else cfg["rakuten"].get("maxNewPerRun", 20))
    hits = cfg["rakuten"].get("hits", 30)
    sale_keywords = cfg["rakuten"].get("saleKeywords") or []

    kept, added, dropped = 0, 0, 0
    price_drops, fresh = [], []
    pinned_stale = []
    not_on_sale = []
    excluded = set()          # 買えないと判断して外したもの。保持ルールの対象外。
    result = {}
    candidates = {}
    seed_per_category = cfg["rakuten"].get("seedPerCategory", 12)
    max_per_shop = cfg["rakuten"].get("maxPerShop", 2)

    for cat in cats:
        print("▼ %s" % cat["label"])
        for raw in fetch_category(cat, app_id, access_key, aff_id, hits, site_url,
                                  ng_keyword, sort_by, sale_keywords):
            if not raw["price"] or not raw["title"]:
                continue

            pid = product_id(raw["itemCode"])

            # いま買えるか。既存の商品にも適用する。
            # 在庫あり(availability=1)でも販売開始前の商品があり、
            # それを載せても読者は買えない。
            ok, why, start_at = sale_status(raw, sale_tolerance)
            if not ok:
                not_on_sale.append((clean_title(raw["rawTitle"]), why))
                # 「順位から落ちた」のではなく「買えないから外した」商品。
                # 30日の保持ルールで復活させてはいけない。
                excluded.add(pid)
                dropped += 1
                continue

            # 実績のない商品は載せない。ランキングAPIが無い以上、
            # レビュー数と評価が「多くの人が実際に買った」ことの唯一の手がかりになる。
            if (raw.get("reviewCount") or 0) < min_reviews:
                dropped += 1
                continue
            if float(raw.get("reviewAverage") or 0) < min_rating:
                dropped += 1
                continue
            records = update_history(history, pid, raw["price"], today)

            prev = existing.get(pid, {})
            item = dict(prev)
            item["id"] = pid
            item["category"] = cat["slug"]
            for f in API_FIELDS:
                if f in raw:
                    item[f] = raw[f]

            # 基準価格の優先順位。手で書いたもの > タイトルの表記 > 価格履歴。
            # タイトルを履歴より上に置くのは、ショップが「3,790円→1,999円」と
            # 書いている以上、それがこの商品の売りだから。履歴は当サイトが
            # 見はじめてからの最高値でしかなく、セール前を捉えられていない。
            title_ref = list_price_from_title(raw.get("rawTitle"), raw["price"])
            if prev.get("priceBasis") == "manual" and prev.get("listPrice"):
                item["listPrice"] = prev["listPrice"]
                item["priceBasis"] = "manual"
            elif title_ref:
                item["listPrice"] = title_ref
                item["priceBasis"] = "title"
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
            item.setdefault("bumpedAt", item["postedAt"] + "T00:00:00")
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
                # 記録は上限を通ったあとで。弾いたものまで数えると、
                # 実際に載った件数と履歴の記述が食い違う。
                was = prev.get("price")
                if prev and was and was > item["price"]:
                    price_drops.append((item["title"], was, item["price"], off))
                elif not prev:
                    fresh.append((item["title"], item["price"], off))

                # 新着として浮上させるのは「新しく起きたこと」だけ。
                #   ・初めて載せる商品
                #   ・値下がりしていなかった商品が、値下がりした
                #   ・すでに安かった商品が、さらに下がった
                # 安いまま据え置きの商品まで毎回浮上させると、
                # 同じ顔ぶれが上に居座り、本当の新着が押し下げられる。
                had_off = 0
                if prev.get("listPrice"):
                    had_off = round((prev["listPrice"] - prev["price"])
                                    / prev["listPrice"] * 100)
                newsworthy = (not prev) or (had_off < min_off) or (was and was > item["price"])
                if newsworthy:
                    item["postedAt"] = today
                    # 日付だけだと、同じ日に載ったものが全部同着になり、
                    # 並びが割引率の順になってしまう。時刻まで持たせる。
                    item["bumpedAt"] = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S")
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
        if pid in excluded:
            expired += 1
            continue
        if p.get("pinned"):
            # 手で選んで貼った商品。APIの検索結果に出るかどうかは関係なく載せる。
            # ここでは日付で切らない代わりに、下で1件ずつ在庫を見に行く。
            result[pid] = p
            pinned_stale.append(pid)
            continue
        last = p.get("lastSeen") or p.get("postedAt") or today
        if last >= cutoff:
            result[pid] = p
            kept_stale += 1
        else:
            expired += 1

    # 留め置きの商品は、消えていないかを1件ずつ確かめる。
    # 日付で切らないぶん、売り切れたまま残り続けるのを防ぐ。
    gone = []
    for pid in pinned_stale:
        p = result[pid]
        code = p.get("itemCode")
        if not code:
            continue
        try:
            data = api_get(ITEM_API, {
                "applicationId": app_id, "accessKey": access_key, "affiliateId": aff_id,
                "itemCode": code, "format": "json", "formatVersion": 2,
            }, site_url)
        except Exception:                                # noqa: BLE001
            time.sleep(REQUEST_INTERVAL)
            continue
        got = data.get("Items") or []
        if not got:
            gone.append((p["title"], "見つかりません（売り切れの可能性）"))
            del result[pid]
            expired += 1
            time.sleep(REQUEST_INTERVAL)
            continue
        it = got[0]
        ok, why, _ = sale_status(it, sale_tolerance)
        if not ok:
            gone.append((p["title"], why))
            del result[pid]
            expired += 1
            time.sleep(REQUEST_INTERVAL)
            continue
        price = int(it.get("itemPrice") or 0)
        if price:
            update_history(history, pid, price, today)
            p["price"] = price
        # タイトルも取り直す。「本日24時間限定」のような煽り文句は
        # 翌日には嘘になるので、留め置きのあいだ固定しておくわけにいかない。
        raw_name = (it.get("itemName") or "").strip()
        if raw_name:
            p["rawTitle"] = raw_name
            p["title"] = clean_title(raw_name)
            title_ref = list_price_from_title(raw_name, price)
            if p.get("priceBasis") != "manual":
                if title_ref:
                    p["listPrice"] = title_ref
                    p["priceBasis"] = "title"
                elif p.get("priceBasis") == "title":
                    # セール前の表記が消えた＝セールが終わった。%OFF を下ろす。
                    ref = reference_price(history.get(pid, []), price)
                    p["listPrice"] = ref
                    p["priceBasis"] = "history" if ref else None
        p["reviewCount"] = it.get("reviewCount") or p.get("reviewCount")
        p["reviewAverage"] = it.get("reviewAverage") or p.get("reviewAverage")
        p["lastSeen"] = today
        time.sleep(REQUEST_INTERVAL)

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
    if not_on_sale:
        print("   いま買えないため見送り: %d件" % len(not_on_sale))
        for t, why in not_on_sale[:5]:
            print("     − %-30s %s" % (t[:30], why))
    if gone:
        print("   留め置きから外しました: %d件" % len(gone))
        for title, why in gone:
            print("     − %-34s %s" % (title[:34], why))
    if pinned_stale:
        print("   留め置き %d件（手で選んだので保持ルールの対象外）" % (len(pinned_stale) - len(gone)))
    if kept_stale or expired:
        print("   掲載継続 %d件（今日はAPIの結果に無いが%d日以内）/ 掲載終了 %d件"
              % (kept_stale, retention_days, expired))
    if price_drops:
        print("\n   値下がりを見つけました: %d件" % len(price_drops))
        for t, was, now, off in price_drops[:8]:
            print("     ↓ %-28s ¥%s → ¥%s (%d%%OFF)"
                  % (t[:28], "{:,}".format(was), "{:,}".format(now), off))
    if no_caption:
        print("   ※ ひとことキャプション未記入が %d件あります。" % no_caption)
        print("      caption を書くとカードの見え方がかなり変わります。")

    # サイトに出している「価格の確認日」は毎日変わる。
    # その日の1回目だけは、中身に動きがなくても残す必要がある。
    first_today = (existing_doc.get("updatedAt") or "") != today
    write_run_summary(added, len(price_drops), len(products), no_caption,
                      price_drops, fresh, gone, first_today)
    print("\n次: python3 build.py")


def write_run_summary(added, drops, total, no_caption, price_drops, fresh, gone,
                      first_today=True):
    """自動実行のコミットメッセージに使う要約を書き出す。

    クラウドで動かすと実行画面を誰も見ないので、
    何が起きたのかは git の履歴に残す。あとから追えるように。
    """
    head = []
    if drops:
        head.append("値下がり%d件" % drops)
    if added:
        head.append("新規%d件" % added)
    if gone:
        head.append("掲載終了%d件" % len(gone))
    title = "auto: " + ("・".join(head) if head else "変更なし")

    body = []
    for t, was, now, off in price_drops[:10]:
        body.append("  ↓ %s  %s円 → %s円 (%d%%OFF)"
                    % (t[:38], "{:,}".format(was), "{:,}".format(now), off))
    for t, price, off in fresh[:10]:
        body.append("  + %s  %s円 (%d%%OFF)" % (t[:38], "{:,}".format(price), off))
    for t, why in gone[:10]:
        body.append("  − %s  %s" % (t[:38], why))
    body.append("")
    body.append("掲載 %d件 / キャプション未記入 %d件" % (total, no_caption))

    with open(os.path.join(ROOT, ".run_summary.txt"), "w", encoding="utf-8") as f:
        f.write(title + "\n\n" + "\n".join(body) + "\n")

    # 反映する価値があるかを、自動実行の側から判断できるようにする。
    # 中身に動きが無い回まで毎度コミットすると、
    # 「価格の確認日が1日ずれただけ」で再デプロイが走る。
    worth = bool(head) or first_today
    with open(os.path.join(ROOT, ".run_changed"), "w", encoding="utf-8") as f:
        f.write("yes" if worth else "no")


if __name__ == "__main__":
    main()

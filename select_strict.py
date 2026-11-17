#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
select_strict.py — ヤスミル (sozaino.com) 改築版の厳格選定モジュール（ドライラン用）

「掲載 = 推薦」の基準を、既存データ (products.json / price_history.json) に適用する。
既存スクリプト (fetch_rakuten.py / build.py / threads.py) には一切触れない独立モジュール。
選定パイプライン本体は evaluate() に切り出してあり、見張り便の自動適用
(apply_selection.py) も同じ evaluate() を使う（ゲートの正本はここだけ）。

使い方:
    python3 select_strict.py [--products products.json] [--history price_history.json]
                             [--out-json PATH] [--out-md PATH]

選定基準（フィード掲載は全ゲート通過が必須）:
  除外（フィード対象外・理由別に集計）:
    - サプリ・健康食品（タイトル+キャプションのキーワード判定）
    - 型番依存の消耗品テーマ（インク等。検索・関連商品でのみ扱う）
  ゲート:
    1. 価格履歴の観測日数が30日以上
    2. 現在価格が履歴の最安値と一致
    3. 現在価格が履歴中央値から20%以上下落
    4. レビュー100件以上 かつ 評価3.8以上
    5. 送料無料（products の tags に '送料無料'。送料専用フィールドはデータに存在しない）
    6. キャプション有り（これだけ落ちたものは caption_needed へ回す）
  構成ルール:
    - シリーズ圧縮: 同ショップ かつ タイトルの文字バイグラム Jaccard >= 0.42 を
      1クラスタとし、代表（レビュー最多）だけをフィードに残す
    - テーマ上限: 2026-11-12 に廃止。全ゲートを通った商品はテーマにかかわらず
      フィードに残す（旧: どのテーマも最終フィードの10%以下に落としていた）。
      流入の調整は台帳側（apply_selection.py の「1回の実行で同一テーマ最大2件」）
      で行う。apply_theme_cap() は互換のため残してあるが、もう落とさない
    - 並び: 同テーマが隣り合わないようインターリーブ（基本は下落率の降順）
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- 定数（初期値）

MIN_OBS_DAYS = 30          # ゲート1: 観測日数
MIN_DROP_VS_MEDIAN = 0.20  # ゲート3: 中央値比の下落率
MIN_REVIEWS = 100          # ゲート4
MIN_RATING = 3.8           # ゲート4
FREE_SHIPPING_TAG = "送料無料"  # ゲート5: tags 内の表記

SERIES_JACCARD = 0.42      # シリーズ判定の閾値
THEME_CAP_RATIO = 0.10     # テーマ上限（最終フィード比）

SUPPLEMENT_KEYWORDS = [
    "サプリ", "機能性表示", "栄養機能食品", "GABA", "ギャバ", "エラグ酸",
    "グルコサミン", "ルテイン", "プロテイン", "ダイエット",
    # 2026-11-17 追加: 粉末コラーゲン（健康食品）が素通りしていたため。
    # プール内で該当するのは健康食品2件のみ（化粧品等の巻き添えなし）。
    "コラーゲン",
]

# 型番依存テーマ（インクカートリッジ等）。テーマ名 'ink' として扱う
INK_KEYWORDS = ["インク", "カートリッジ", "互換", "型番"]

DEFAULT_OUT_JSON = (
    Path.home()
    / "workspace/goals/yasumiru-threads-rakuten-affiliate-improvement"
    / "files/curated-dryrun.json"
)
DEFAULT_OUT_MD = (
    Path.home()
    / "workspace/goals/yasumiru-threads-rakuten-affiliate-improvement"
    / "files/selection-dryrun-20261109.md"
)


# ---------------------------------------------------------------- 補助関数

def bigrams(text: str) -> set:
    """タイトルの文字バイグラム集合（空白除去）。"""
    t = "".join((text or "").split())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def hit_keyword(text: str, keywords) -> str | None:
    for kw in keywords:
        if kw in text:
            return kw
    return None


def theme_of(product: dict) -> str:
    text = (product.get("title") or "") + (product.get("caption") or "")
    if hit_keyword(text, INK_KEYWORDS):
        return "ink"
    return product.get("category") or "unknown"


def metrics_for(product: dict, history: dict) -> dict:
    """ゲート判定に使う指標をまとめて計算する。"""
    entries = history.get(product["id"], [])
    prices = [p for _, p in entries]
    days = len({d for d, _ in entries})
    cur = product.get("price")
    med = statistics.median(prices) if prices else None
    drop = (1 - cur / med) if (med and cur is not None) else None
    return {
        "obs_days": days,
        "obs_count": len(entries),
        "current_price": cur,
        "min_price": min(prices) if prices else None,
        "median_price": med,
        "drop_vs_median": drop,  # 0.25 = 中央値比 -25%
        "review_count": product.get("reviewCount") or 0,
        "review_average": product.get("reviewAverage") or 0,
        "free_shipping": FREE_SHIPPING_TAG in (product.get("tags") or []),
        "has_caption": bool((product.get("caption") or "").strip()),
    }


# ---------------------------------------------------------------- ポイント軸
# ポイント評価軸（2026-11-16）。価格軸とは独立の物差しで、倍率を「価格が
# 安い証拠」にはしない（絶対ルール）。判定の正本はここに置き、台帳の
# 保持判定（apply_selection）と表示（build）も同じ関数を使う。
#   期間限定（終了日時が保存されている）: 倍率5倍以上 かつ 獲得ポイント
#     目安100pt以上 かつ 品質条件（レビュー100件以上・評価3.8以上・送料無料）
#   常時（終了日時なし）: 倍率10倍以上 かつ 目安500pt以上 かつ 品質条件
# 終了日時が保存されている倍率は、API の応答から消えても終了日時が正。
# 終了日時のない倍率は、取得状態が no_rate になったら無効とみなす
# （failed = 取得失敗は前回データを保持するので、有効のまま扱う）。

POINT_MIN_RATE_LIMITED = 5     # 期間限定の下限倍率
POINT_MIN_PTS_LIMITED = 100    # 期間限定の獲得ポイント目安の下限 (pt)
POINT_MIN_RATE_PERMANENT = 10  # 常時の下限倍率
POINT_MIN_PTS_PERMANENT = 500  # 常時の獲得ポイント目安の下限 (pt)


def _parse_point_time(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def point_points(price, rate):
    """獲得ポイントの目安。付与は税抜価格に対してなので price/1.1 で割る。
    端数の扱いはショップで違うため、これは目安（表示でも目安と明記する）。"""
    if not price or not rate:
        return 0
    return int(round((price / 1.1) * rate / 100.0))


def point_state(product, now=None):
    """商品のポイント倍率の現在状態。倍率が無い・無効なら None。
    戻り値: {rate, start, end, permanent, pts}（start/end は datetime か None、
    permanent のとき end は None）。"""
    pi = product.get("pointInfo") or {}
    rate = pi.get("rate") or 1
    if rate < 2:
        return None
    now = now or datetime.now(JST)
    start = _parse_point_time(pi.get("start"))
    end = _parse_point_time(pi.get("end"))
    permanent = end is None or end.year >= 9999
    if permanent:
        if pi.get("status") == "no_rate":
            return None
        end = None
    elif end <= now:
        return None
    if start and start > now:
        return None  # 開始前の倍率はまだ載せない
    return {"rate": rate, "start": start, "end": end,
            "permanent": permanent,
            "pts": point_points(product.get("price"), rate)}


def point_qualifies(product, now=None):
    """ポイント軸の単独掲載に通るか。通れば point_state の dict を返す。"""
    st = point_state(product, now)
    if st is None:
        return None
    if (product.get("reviewCount") or 0) < MIN_REVIEWS:
        return None
    if (product.get("reviewAverage") or 0) < MIN_RATING:
        return None
    if FREE_SHIPPING_TAG not in (product.get("tags") or []):
        return None
    if st["permanent"]:
        ok = (st["rate"] >= POINT_MIN_RATE_PERMANENT
              and st["pts"] >= POINT_MIN_PTS_PERMANENT)
    else:
        ok = (st["rate"] >= POINT_MIN_RATE_LIMITED
              and st["pts"] >= POINT_MIN_PTS_LIMITED)
    return st if ok else None


POINT_DEDUPE_JACCARD = 0.30  # ポイント軸の同一商品判定（価格・倍率一致が前提）

# ポイント軸の「種類」。同じ種類の商品は同時に1件だけ載せる（2026-11-16、
# furu 指示「同じようなのはいらない」）。タイトルのキーワードで判定し、
# 上にある種類ほど優先する（自動調理ポットの説明文には「フード
# プロセッサー」という機能名が混ざるため、順序が要る）。合わなければ
# None で、種類の縛りはかからない。新しい種類が増えたらここに足す。
POINT_KINDS = [
    ("自動調理ポット", ("自動調理ポット", "豆乳メーカー", "スープメーカー")),
    ("フードプロセッサー", ("フードプロセッサー", "カプセルカッター")),
    ("ミシン", ("ミシン",)),
    ("印鑑", ("ネーム印", "浸透印", "印鑑", "はんこ")),
    ("加湿器", ("加湿器",)),
    ("電気毛布", ("電気毛布",)),
    ("モバイルバッテリー", ("モバイルバッテリー",)),
    ("イヤホン", ("イヤホン",)),
    ("掃除機", ("掃除機",)),
    ("炊飯器", ("炊飯器",)),
    ("電子レンジ", ("電子レンジ", "オーブンレンジ")),
    ("コーヒーメーカー", ("コーヒーメーカー",)),
    ("電気ケトル", ("電気ケトル",)),
    ("空気清浄機", ("空気清浄機",)),
    ("ドライヤー", ("ドライヤー",)),
    ("電動歯ブラシ", ("電動歯ブラシ",)),
    ("体重計", ("体重計", "体組成計")),
]


def point_kind(product):
    """商品の種類（ポイント軸の重なり判定用）。合わなければ None。
    表示用タイトルは途中で切れていることがあるので、元のタイトル
    （rawTitle）も合わせて見る。"""
    t = (product.get("rawTitle") or "") + (product.get("title") or "")
    for name, kws in POINT_KINDS:
        if any(k in t for k in kws):
            return name
    return None


def _point_dedupe(cands):
    """同一商品の二重掲載を防ぐ。価格と倍率が同じでタイトルが似ているものは
    レビュー最多の1件だけ残す（別ショップが出す同一商品の対策）。
    価格と倍率の一致が前提なので、閾値はシリーズ圧縮より少し緩くする。"""
    cands = sorted(cands,
                   key=lambda c: -(c["product"].get("reviewCount") or 0))
    kept, grams = [], []
    for c in cands:
        p = c["product"]
        g = bigrams(p.get("title") or "")
        dup = False
        for k, kg in zip(kept, grams):
            kp = k["product"]
            if (kp.get("price") == p.get("price")
                    and k["state"]["rate"] == c["state"]["rate"]
                    and jaccard(g, kg) >= POINT_DEDUPE_JACCARD):
                dup = True
                break
        if not dup:
            kept.append(c)
            grams.append(g)
    return kept


# ---------------------------------------------------------------- 構成ルール

def cluster_series(items: list) -> list:
    """同ショップ内でタイトル類似のものをクラスタ化し、代表+兄弟の形で返す。

    items: [{'product':..., 'metrics':...}, ...]
    戻り値: [{'rep': item, 'siblings': [item, ...]}, ...]
    """
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_shop = defaultdict(list)
    for i, it in enumerate(items):
        by_shop[it["product"].get("shop") or ""].append(i)

    grams = [bigrams(it["product"].get("title") or "") for it in items]
    for idxs in by_shop.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                ia, ib = idxs[a], idxs[b]
                if jaccard(grams[ia], grams[ib]) >= SERIES_JACCARD:
                    union(ia, ib)

    groups = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(i)

    clusters = []
    for idxs in groups.values():
        members = sorted(
            (items[i] for i in idxs),
            key=lambda it: (-it["metrics"]["review_count"],
                            -(it["metrics"]["drop_vs_median"] or 0)),
        )
        clusters.append({"rep": members[0], "siblings": members[1:]})
    return clusters


THEME_CAP_MIN_FEED = 20    # フィードがこの件数未満なら上限は適用しない（小規模時の機械的切り捨て防止）
THEME_CAP_MIN = 3          # 上限の下限（10%計算がこれ未満なら3件まで許す）


def apply_theme_cap(clusters: list) -> list:
    """テーマ上限（廃止済み・互換のために残す）。

    2026-11-12 のルール変更で、全ゲートを通った商品はテーマにかかわらず
    掲載することになった。かつては「どのテーマも最終フィードの10%以下」
    になるよう超過分を落としていたが、全ゲート通過の商品（例: 中央値比
    -50%のワイドパンツ）がテーマの椅子取りゲームだけで何週間も
    止まる事態が起きたため、ここでは落とさない。
    流入の調整は台帳側（apply_selection.py）が「1回の実行で同一テーマ
    最大2件、残りは次回」で行う。ここは並べ替えもせず、そのまま返す。"""
    return list(clusters)


def interleave(clusters: list) -> list:
    """同テーマが隣り合わないようインターリーブ。基本は下落率の降順。"""
    pool = sorted(
        clusters,
        key=lambda c: -(c["rep"]["metrics"]["drop_vs_median"] or 0),
    )
    ordered = []
    prev_theme = None
    while pool:
        pick = None
        for c in pool:
            if c["rep"]["theme"] != prev_theme:
                pick = c
                break
        if pick is None:  # 残りが全部同テーマなら降順の先頭
            pick = pool[0]
        ordered.append(pick)
        prev_theme = pick["rep"]["theme"]
        pool.remove(pick)
    return ordered


# ---------------------------------------------------------------- メイン

def evaluate(products: list, history: dict):
    """選定パイプライン本体（正本）。CLI のドライランと、見張り便の自動適用
    (apply_selection.py) の両方がここを使う。ゲートを別実装しないこと。

    戻り値: (result, decisions)
      result: 現行の出力 JSON と同形式（generated_by / total_products /
              funnel / feed / caption_needed / excluded）
      decisions: {商品id: {"outcome": ..., "detail": {...}}}
        outcome は feed / caption_needed / excluded_supplement /
        excluded_model_specific / watching / gate1..gate5 /
        series_sibling / theme_cap。
        落ちた理由をあとから説明するための記録で、result には含めない。
    """
    decisions = {}
    funnel = []  # (ステップ名, 残数)
    total = len(products)
    funnel.append(("掲載候補（現行 products.json）", total))

    # ---- 除外（フィード対象外） -------------------------------------------
    excluded = {"supplement": [], "model_specific": [], "watching": []}
    pool = []
    for p in products:
        # 観測枠（ウォッチ中）は価格履歴を積むための在籍で、選定の候補では
        # ない。値引きの裏付けが付いて通常の取り込みを通った回に
        # fetch_rakuten 側でタグが外れるので、ここで外しても
        # 昇格の邪魔にはならない（2026-11-14）。
        if "ウォッチ中" in (p.get("tags") or []):
            excluded["watching"].append({"id": p["id"], "title": p.get("title")})
            decisions[p["id"]] = {"outcome": "watching", "detail": {}}
            continue
        text = (p.get("title") or "") + (p.get("caption") or "")
        kw = hit_keyword(text, SUPPLEMENT_KEYWORDS)
        if kw:
            excluded["supplement"].append({"id": p["id"], "title": p.get("title"),
                                            "matched": kw})
            decisions[p["id"]] = {"outcome": "excluded_supplement",
                                  "detail": {"matched": kw}}
            continue
        kw = hit_keyword(text, INK_KEYWORDS)
        if kw:
            excluded["model_specific"].append({"id": p["id"], "title": p.get("title"),
                                                "matched": kw})
            decisions[p["id"]] = {"outcome": "excluded_model_specific",
                                  "detail": {"matched": kw}}
            continue
        pool.append(p)
    funnel.append((f"除外を除く（サプリ系 {len(excluded['supplement'])}件 / "
                   f"型番系 {len(excluded['model_specific'])}件 / "
                   f"観測枠 {len(excluded['watching'])}件）", len(pool)))

    # ---- ゲート ------------------------------------------------------------
    def gate(name, key_fn, outcome):
        nonlocal pool
        kept, dropped = [], []
        for p in pool:
            m = p["_m"]
            (kept if key_fn(m) else dropped).append(p)
        funnel.append((name, len(kept)))
        pool = kept
        for p in dropped:
            decisions[p["id"]] = {"outcome": outcome,
                                  "detail": {"metrics": dict(p["_m"])}}
        return dropped

    for p in pool:
        p["_m"] = metrics_for(p, history)
        p["_theme"] = theme_of(p)

    gate("ゲート1: 観測30日以上", lambda m: m["obs_days"] >= MIN_OBS_DAYS, "gate1")
    gate("ゲート2: 現在価格 = 履歴最安",
         lambda m: m["min_price"] is not None and m["current_price"] == m["min_price"],
         "gate2")
    gate("ゲート3: 中央値比 -20%以上",
         lambda m: m["drop_vs_median"] is not None
         and m["drop_vs_median"] >= MIN_DROP_VS_MEDIAN, "gate3")
    gate("ゲート4: レビュー100件以上・評価3.8以上",
         lambda m: m["review_count"] >= MIN_REVIEWS and m["review_average"] >= MIN_RATING,
         "gate4")
    gate("ゲート5: 送料無料", lambda m: m["free_shipping"], "gate5")

    # ゲート6: キャプション（これだけ落ちたものは caption_needed へ）
    caption_needed, passed = [], []
    for p in pool:
        (passed if p["_m"]["has_caption"] else caption_needed).append(p)
    funnel.append(("ゲート6: キャプション有り（無しは caption_needed へ）", len(passed)))
    for p in caption_needed:
        decisions[p["id"]] = {"outcome": "caption_needed",
                              "detail": {"metrics": dict(p["_m"])}}

    # ---- 構成ルール ---------------------------------------------------------
    items = [{"product": p, "metrics": p["_m"], "theme": p["_theme"]} for p in passed]
    clusters = cluster_series(items)
    funnel.append((f"シリーズ圧縮（同ショップ・タイトル類似 {SERIES_JACCARD} 以上）",
                   len(clusters)))
    for c in clusters:
        for s in c["siblings"]:
            decisions[s["product"]["id"]] = {
                "outcome": "series_sibling",
                "detail": {"rep_id": c["rep"]["product"]["id"],
                           "rep_title": c["rep"]["product"].get("title"),
                           "metrics": dict(s["metrics"])}}
    reps_before = {c["rep"]["product"]["id"]: c for c in clusters}
    capped = apply_theme_cap(clusters)
    reps_after = {c["rep"]["product"]["id"] for c in capped}
    for rid, c in reps_before.items():
        if rid not in reps_after:
            decisions[rid] = {"outcome": "theme_cap",
                              "detail": {"theme": c["rep"]["theme"],
                                         "metrics": dict(c["rep"]["metrics"])}}
    funnel.append(("テーマ上限（2026-11-12 廃止・全通過を残す）", len(capped)))
    feed_clusters = interleave(capped)
    for c in feed_clusters:
        decisions[c["rep"]["product"]["id"]] = {"outcome": "feed", "detail": {}}

    # ---- 出力データ ---------------------------------------------------------
    def feed_entry(c):
        p, m = c["rep"]["product"], c["rep"]["metrics"]
        return {
            "id": p["id"],
            "title": p.get("title"),
            "shop": p.get("shop"),
            "category": p.get("category"),
            "theme": c["rep"]["theme"],
            "price": m["current_price"],
            "median_price": m["median_price"],
            "min_price": m["min_price"],
            "drop_pct_vs_median": round((m["drop_vs_median"] or 0) * 100, 1),
            "obs_days": m["obs_days"],
            "reviewCount": m["review_count"],
            "reviewAverage": m["review_average"],
            "free_shipping": m["free_shipping"],
            "caption": p.get("caption"),
            "affiliateUrl": p.get("affiliateUrl"),
            "siblings": [
                {"id": s["product"]["id"], "title": s["product"].get("title"),
                 "reviewCount": s["metrics"]["review_count"],
                 "price": s["metrics"]["current_price"]}
                for s in c["siblings"]
            ],
        }

    caption_needed_out = []
    for p in caption_needed:
        m = p["_m"]
        caption_needed_out.append({
            "id": p["id"], "title": p.get("title"), "shop": p.get("shop"),
            "category": p.get("category"), "price": m["current_price"],
            "drop_pct_vs_median": round((m["drop_vs_median"] or 0) * 100, 1),
            "reviewCount": m["review_count"], "reviewAverage": m["review_average"],
        })
    caption_needed_out.sort(key=lambda x: -x["drop_pct_vs_median"])

    # ---- ポイント軸（価格軸とは独立の選定。観測枠の商品も対象） -----------
    point_cands = []
    for p in products:
        text = (p.get("title") or "") + (p.get("caption") or "")
        if hit_keyword(text, SUPPLEMENT_KEYWORDS):
            continue
        if hit_keyword(text, INK_KEYWORDS):
            continue
        st = point_qualifies(p)
        if st is not None:
            point_cands.append({"product": p, "state": st})
    point_cands = _point_dedupe(point_cands)
    # 種類の重なりを圧縮する。同じ種類は代表1件（レビュー最多、同数なら
    # ポイント目安の大きい方）だけを残す。
    by_kind, no_kind = {}, []
    for c in point_cands:
        k = point_kind(c["product"])
        if k is None:
            no_kind.append(c)
            continue
        cur = by_kind.get(k)
        key = (c["product"].get("reviewCount") or 0, c["state"]["pts"])
        if cur is None or key > ((cur["product"].get("reviewCount") or 0),
                                  cur["state"]["pts"]):
            by_kind[k] = c
    point_cands = no_kind + list(by_kind.values())
    point_feed, point_caption_needed = [], []
    for c in point_cands:
        p, st = c["product"], c["state"]
        entry = {
            "id": p["id"], "title": p.get("title"), "shop": p.get("shop"),
            "category": p.get("category"), "price": p.get("price"),
            "reviewCount": p.get("reviewCount"),
            "reviewAverage": p.get("reviewAverage"),
            "point_rate": st["rate"], "point_pts": st["pts"],
            "point_start": st["start"].isoformat() if st["start"] else "",
            "point_end": st["end"].isoformat() if st["end"] else "",
            "point_permanent": st["permanent"],
            "caption": p.get("caption"),
        }
        if (p.get("caption") or "").strip():
            point_feed.append(entry)
            if decisions.get(p["id"], {}).get("outcome") != "feed":
                decisions[p["id"]] = {"outcome": "point_feed", "detail": {}}
        else:
            point_caption_needed.append(entry)
            if p["id"] not in decisions:
                decisions[p["id"]] = {"outcome": "point_caption_needed",
                                      "detail": {}}
    funnel.append((f"ポイント軸（単独掲載 {len(point_feed)}件 / "
                   f"キャプション待ち {len(point_caption_needed)}件）",
                   len(point_feed)))

    result = {
        "generated_by": "select_strict.py (dry run)",
        "total_products": total,
        "funnel": [{"step": s, "remaining": n} for s, n in funnel],
        "feed": [feed_entry(c) for c in feed_clusters],
        "caption_needed": caption_needed_out,
        "point_feed": point_feed,
        "point_caption_needed": point_caption_needed,
        "excluded": {
            "supplement_count": len(excluded["supplement"]),
            "model_specific_count": len(excluded["model_specific"]),
            "watching_count": len(excluded["watching"]),
            "supplement": excluded["supplement"],
            "model_specific": excluded["model_specific"],
            "watching": excluded["watching"],
        },
    }
    return result, decisions


def main() -> int:
    ap = argparse.ArgumentParser(description="ヤスミル厳格選定（ドライラン）")
    here = Path(__file__).resolve().parent
    ap.add_argument("--products", default=str(here / "products.json"))
    ap.add_argument("--history", default=str(here / "price_history.json"))
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    ap.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    args = ap.parse_args()

    products = json.loads(Path(args.products).read_text(encoding="utf-8"))["products"]
    history = json.loads(Path(args.history).read_text(encoding="utf-8"))

    result, _decisions = evaluate(products, history)
    total = result["total_products"]
    funnel = [(f["step"], f["remaining"]) for f in result["funnel"]]
    excluded = result["excluded"]

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # ---- レポート -----------------------------------------------------------
    lines = []
    lines.append("# 厳格選定ドライラン結果（2026-11-09）")
    lines.append("")
    lines.append("`select_strict.py` を現行データ（products.json 343件 / price_history.json）に"
                 "適用した結果。新基準「掲載 = 推薦」の実地検証。")
    lines.append("")
    lines.append("## ファネル（各ゲート通過数）")

    prev = None
    for step, n in funnel:
        drop_note = f"（-{prev - n}）" if prev is not None else ""
        lines.append(f"- {step}: **{n}件**{drop_note}")
        prev = n
    lines.append("")
    lines.append("## 最終フィード")
    lines.append("")
    feed = result["feed"]
    lines.append(f"**{len(feed)}件**（下落率降順・同テーマ非隣接に整列済み）")
    lines.append("")
    lines.append("| # | 商品 | 価格 | 中央値比 | レビュー | 系列 |")
    lines.append("|---|---|---|---|---|---|")
    for i, e in enumerate(feed, 1):
        title = (e["title"] or "")[:40]
        sib = f"ほか{len(e['siblings'])}件" if e["siblings"] else "-"
        lines.append(
            f"| {i} | {title} | ¥{e['price']:,} | -{e['drop_pct_vs_median']}% "
            f"| {e['reviewCount']:,}件 ★{e['reviewAverage']} | {sib} |"
        )
    theme_counts = Counter(e["theme"] for e in feed)
    lines.append("")
    lines.append("テーマ内訳: " + " / ".join(f"{t} {c}件" for t, c in theme_counts.most_common()))
    lines.append("")
    lines.append("## caption_needed（ゲート1〜5通過・キャプションのみ欠落）")
    lines.append("")
    caption_needed_out = result["caption_needed"]
    lines.append(f"**{len(caption_needed_out)}件** — キャプションを書けば掲載候補に戻る。"
                 "黙って落とさず別枠で保持。")
    for e in caption_needed_out:
        lines.append(f"- {(e['title'] or '')[:40]}（¥{e['price']:,} / "
                     f"-{e['drop_pct_vs_median']}% / レビュー{e['reviewCount']:,}件）")
    lines.append("")
    lines.append("## 除外（フィード対象外）")
    lines.append("")
    lines.append(f"- サプリ・健康食品系: **{len(excluded['supplement'])}件**"
                 "（キーワード判定のため過不足あり得る）")
    lines.append(f"- 型番依存テーマ（インク等）: **{len(excluded['model_specific'])}件**"
                 "（フィードではなく検索・関連商品で扱う）")
    lines.append(f"- 観測枠（ウォッチ中）: **{len(excluded['watching'])}件**"
                 "（価格履歴を積むための在籍で、選定の候補ではない）")
    lines.append("")
    lines.append("## データ上の注意")
    lines.append("")
    lines.append("- 送料の判定: products に送料専用フィールドは無く、送料無料は "
                 "`tags` の '送料無料' で表されている（343件中240件）。ゲート5はこれを使用。")
    lines.append("- price_history.json には13,361件分の履歴があるが、現行 products.json の"
                 "商品は343件のみ（残りは過去に観測した商品）。")
    lines.append("- '現在価格' は products.json の price を使用し、履歴の最安・中央値と比較した。")
    lines.append("")

    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"total={total} feed={len(feed)} caption_needed={len(caption_needed_out)} "
          f"excluded_supplement={len(excluded['supplement'])} "
          f"excluded_model_specific={len(excluded['model_specific'])} "
          f"watching={len(excluded['watching'])}")
    for step, n in funnel:
        print(f"  {step}: {n}")
    print(f"wrote: {out_json}")
    print(f"wrote: {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

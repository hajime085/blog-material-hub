#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
select_strict.py — ヤスミル (sozaino.com) 改築版の厳格選定モジュール（ドライラン用）

「掲載 = 推薦」の基準を、既存データ (products.json / price_history.json) に適用する。
既存スクリプト (fetch_rakuten.py / build.py / threads.py) には一切触れない独立モジュール。

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
    4. レビュー100件以上 かつ 評価4.2以上
    5. 送料無料（products の tags に '送料無料'。送料専用フィールドはデータに存在しない）
    6. キャプション有り（これだけ落ちたものは caption_needed へ回す）
  構成ルール:
    - シリーズ圧縮: 同ショップ かつ タイトルの文字バイグラム Jaccard >= 0.42 を
      1クラスタとし、代表（レビュー最多）だけをフィードに残す
    - テーマ上限: どのテーマも最終フィードの10%以下（超過分はレビュー少ない順に落とす）
    - 並び: 同テーマが隣り合わないようインターリーブ（基本は下落率の降順）
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------- 定数（初期値）

MIN_OBS_DAYS = 30          # ゲート1: 観測日数
MIN_DROP_VS_MEDIAN = 0.20  # ゲート3: 中央値比の下落率
MIN_REVIEWS = 100          # ゲート4
MIN_RATING = 4.2           # ゲート4
FREE_SHIPPING_TAG = "送料無料"  # ゲート5: tags 内の表記

SERIES_JACCARD = 0.42      # シリーズ判定の閾値
THEME_CAP_RATIO = 0.10     # テーマ上限（最終フィード比）

SUPPLEMENT_KEYWORDS = [
    "サプリ", "機能性表示", "栄養機能食品", "GABA", "ギャバ", "エラグ酸",
    "グルコサミン", "ルテイン", "プロテイン", "ダイエット",
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
    """どのテーマも最終フィードの10%以下になるまで、超過テーマから
    レビュー最少の代表を落とし続ける。
    ただしフィードが THEME_CAP_MIN_FEED 未満の小規模な間は適用しない
    （通過3件すべて同テーマ→上限1件のような機械的な切り捨てを防ぐ）。"""
    reps = [c for c in clusters]
    if len(reps) < THEME_CAP_MIN_FEED:
        return reps
    while reps:
        n = len(reps)
        cap = max(THEME_CAP_MIN, int(n * THEME_CAP_RATIO))
        counts = Counter(c["rep"]["theme"] for c in reps)
        over = {t for t, c in counts.items() if c > cap}
        if not over:
            break
        # 超過テーマの中でレビュー最少のものを1つ落とす
        victim = min(
            (c for c in reps if c["rep"]["theme"] in over),
            key=lambda c: c["rep"]["metrics"]["review_count"],
        )
        reps.remove(victim)
    return reps


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

    funnel = []  # (ステップ名, 残数)
    total = len(products)
    funnel.append(("掲載候補（現行 products.json）", total))

    # ---- 除外（フィード対象外） -------------------------------------------
    excluded = {"supplement": [], "model_specific": []}
    pool = []
    for p in products:
        text = (p.get("title") or "") + (p.get("caption") or "")
        kw = hit_keyword(text, SUPPLEMENT_KEYWORDS)
        if kw:
            excluded["supplement"].append({"id": p["id"], "title": p.get("title"),
                                            "matched": kw})
            continue
        kw = hit_keyword(text, INK_KEYWORDS)
        if kw:
            excluded["model_specific"].append({"id": p["id"], "title": p.get("title"),
                                                "matched": kw})
            continue
        pool.append(p)
    n_excl = len(excluded["supplement"]) + len(excluded["model_specific"])
    funnel.append((f"除外を除く（サプリ系 {len(excluded['supplement'])}件 / "
                   f"型番系 {len(excluded['model_specific'])}件）", len(pool)))

    # ---- ゲート ------------------------------------------------------------
    def gate(name, key_fn):
        nonlocal pool
        kept, dropped = [], []
        for p in pool:
            m = p["_m"]
            (kept if key_fn(m) else dropped).append(p)
        funnel.append((name, len(kept)))
        pool = kept
        return dropped

    for p in pool:
        p["_m"] = metrics_for(p, history)
        p["_theme"] = theme_of(p)

    gate("ゲート1: 観測30日以上", lambda m: m["obs_days"] >= MIN_OBS_DAYS)
    gate("ゲート2: 現在価格 = 履歴最安",
         lambda m: m["min_price"] is not None and m["current_price"] == m["min_price"])
    gate("ゲート3: 中央値比 -20%以上",
         lambda m: m["drop_vs_median"] is not None
         and m["drop_vs_median"] >= MIN_DROP_VS_MEDIAN)
    gate("ゲート4: レビュー100件以上・評価4.2以上",
         lambda m: m["review_count"] >= MIN_REVIEWS and m["review_average"] >= MIN_RATING)
    gate("ゲート5: 送料無料", lambda m: m["free_shipping"])

    # ゲート6: キャプション（これだけ落ちたものは caption_needed へ）
    caption_needed, passed = [], []
    for p in pool:
        (passed if p["_m"]["has_caption"] else caption_needed).append(p)
    funnel.append(("ゲート6: キャプション有り（無しは caption_needed へ）", len(passed)))

    # ---- 構成ルール ---------------------------------------------------------
    items = [{"product": p, "metrics": p["_m"], "theme": p["_theme"]} for p in passed]
    clusters = cluster_series(items)
    funnel.append((f"シリーズ圧縮（同ショップ・タイトル類似 {SERIES_JACCARD} 以上）",
                   len(clusters)))
    capped = apply_theme_cap(clusters)
    funnel.append(("テーマ上限10%適用後", len(capped)))
    feed_clusters = interleave(capped)

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

    result = {
        "generated_by": "select_strict.py (dry run)",
        "total_products": total,
        "funnel": [{"step": s, "remaining": n} for s, n in funnel],
        "feed": [feed_entry(c) for c in feed_clusters],
        "caption_needed": caption_needed_out,
        "excluded": {
            "supplement_count": len(excluded["supplement"]),
            "model_specific_count": len(excluded["model_specific"]),
            "supplement": excluded["supplement"],
            "model_specific": excluded["model_specific"],
        },
    }

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
          f"excluded_model_specific={len(excluded['model_specific'])}")
    for step, n in funnel:
        print(f"  {step}: {n}")
    print(f"wrote: {out_json}")
    print(f"wrote: {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

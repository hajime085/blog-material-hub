#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""キャプションと商品説明が空の商品に、文章を書く。

  python3 write_captions.py            # 空のものを全部書く
  python3 write_captions.py --limit 5  # 5件だけ
  python3 write_captions.py --dry-run  # 書かせるだけで保存しない

■ なぜ検算が要るか

このサイトは「安く見えて安くない商品」を弾くことを売りにしている。
その文章を書く側が、データに無い仕様をでっち上げたら本末転倒になる。

そこで、書かせたあとに機械で確かめる。
  ・文中の金額が、実売価格・セール前価格・単価のどれとも一致しなければ捨てる
  ・レビュー件数や評価も、実際の値と合わなければ捨てる
  ・長すぎるもの、空のものも捨てる
捨てた商品はキャプションが空のまま残るので、後から人が書けばいい。
嘘を載せるより、空白のほうがましだという判断。
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-opus-5"


def load_dotenv():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


SYSTEM = """あなたは「ヤスミル」という楽天市場の特価まとめサイトの書き手です。
値下がりした商品だけを集めて紹介しています。

読者に渡す3つの文章を書いてください。

1. caption … カードに出す一言。20〜45字。文末は句点。
   何がいくらなのかが一目で分かること。値下がりしているなら
   「3,790円のパンツが1,990円。」のように、前後の値段を入れる。
2. description … 商品ページの「どんな商品？」。2段落、合計70〜130字。
   段落の区切りは改行1つ。1段落目はその商品が何か。
   2段落目は、買う人にとって何が違うのか。
3. points … 箇条書き3つ。各25字以内。価格・仕様・実績など事実を短く。

■ 絶対に守ること

・与えられたデータから確認できることだけを書く。
  素材、サイズ、産地、機能、対応機種などは、商品名に書かれていなければ書かない。
  「肌ざわりがいい」「味が濃い」など、確かめようのない感想も書かない。
・金額は、与えられた price / listPrice / unitNote の数字だけを使う。
  自分で計算した単価を新しく出さない。
・レビュー件数と評価は、与えられた数値をそのまま使う。
・誇張しない。「絶対」「最安」「必ず」は使わない。
・絵文字は使わない。
・「！」は多用しない。
・断定できないことは書かず、短く終わらせてよい。

■ 文体

・です・ます調。
・売り込まない。店員が横で一言添えるくらいの距離感。
・同じ言い回しを繰り返さない。

出力は必ず次のJSONだけ。前置きも説明も付けない。
{"items":[{"id":"...","caption":"...","description":"...","points":["...","...","..."]}]}
"""


def brief(p):
    """書き手に渡す材料。ここに無いことは書かせない。"""
    d = {
        "id": p["id"],
        "商品名": p.get("rawTitle") or p.get("title"),
        "price": p["price"],
        "カテゴリ": p.get("category"),
        "ショップ": p.get("shop"),
        "レビュー件数": p.get("reviewCount") or 0,
        "レビュー評価": p.get("reviewAverage"),
        "送料無料": "送料無料" in (p.get("tags") or []),
    }
    if p.get("listPrice"):
        d["listPrice"] = p["listPrice"]
    if p.get("unitNote"):
        d["unitNote"] = p["unitNote"]
    return d


NUM_RE = re.compile(r"([\d,]{3,9})\s*円")


def allowed_numbers(p):
    ok = {p["price"]}
    if p.get("listPrice"):
        ok.add(p["listPrice"])
    for m in NUM_RE.findall(p.get("unitNote") or ""):
        ok.add(int(m.replace(",", "")))
    return ok


def verify(p, item):
    """書かせた文章を検算する。1つでも引っかかれば、その商品は捨てる。"""
    cap = (item.get("caption") or "").strip()
    desc = (item.get("description") or "").strip()
    pts = [x.strip() for x in (item.get("points") or []) if x and x.strip()]

    if not cap or not desc or len(pts) < 2:
        return "文章がそろっていない"
    if not (12 <= len(cap) <= 60):
        return "キャプションの長さが %d字" % len(cap)
    if not (40 <= len(desc) <= 200):
        return "説明の長さが %d字" % len(desc)
    if any(len(x) > 34 for x in pts):
        return "箇条書きが長すぎる"

    blob = cap + "\n" + desc + "\n" + "\n".join(pts)

    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", blob):
        return "絵文字が入っている"
    if re.search(r"[가-힣Ѐ-ӿ]", blob):
        return "日本語以外の文字が入っている"
    for word in ("最安", "絶対", "必ず", "業界一"):
        if word in blob:
            return "誇張表現「%s」" % word

    # 金額の検算。単価の言及は本体価格と違って当然なので、周辺の語で除く。
    ok = allowed_numbers(p)
    for m in NUM_RE.finditer(blob):
        v = int(m.group(1).replace(",", ""))
        if v in ok:
            continue
        near = blob[max(0, m.start() - 18): m.end() + 12]
        if re.search(r"(あたり|ぽっきり|1本|1袋|1包|1個|1枚|1食|1杯|1ヶ月|1kg)", near):
            return "勝手に単価を作っている（%s円）" % m.group(1)
        return "データに無い金額（%s円）" % m.group(1)

    # レビューの数字も、実際の値と合っているか
    rc = p.get("reviewCount") or 0
    for m in re.finditer(r"レビュー\s*([\d,]+)\s*件", blob):
        if int(m.group(1).replace(",", "")) != rc:
            return "レビュー件数が違う（%s件 / 実際%d件）" % (m.group(1), rc)
    ra = p.get("reviewAverage")
    for m in re.finditer(r"★\s*([\d.]+)", blob):
        if ra is None or abs(float(m.group(1)) - float(ra)) > 0.005:
            return "評価が違う（★%s / 実際★%s）" % (m.group(1), ra)

    return None


def main():
    load_dotenv()
    args = sys.argv[1:]
    dry = "--dry-run" in args
    limit = 0
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    path = os.path.join(ROOT, "products.json")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    products = doc["products"]

    todo = [p for p in products if not (p.get("caption") or "").strip()
            or not (p.get("description") or "").strip()]
    if limit:
        todo = todo[:limit]

    if not todo:
        print("キャプションの空いている商品はありません。")
        return

    print("▼ %d件に文章を書きます（%s）" % (len(todo), MODEL))
    for p in todo:
        print("   ・¥%-7s %s" % ("{:,}".format(p["price"]), (p.get("title") or "")[:38]))

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("\nANTHROPIC_API_KEY がありません。\n"
                 ".env に書くか、環境変数で渡してください。")

    try:
        import anthropic
    except ImportError:
        sys.exit("\nanthropic が入っていません。\n  pip install anthropic")

    client = anthropic.Anthropic()
    payload = json.dumps({"items": [brief(p) for p in todo]}, ensure_ascii=False)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": payload}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()

    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        sys.exit("返答をJSONとして読めませんでした。\n" + text[:400])
    try:
        got = json.loads(m.group(0)).get("items") or []
    except ValueError as ex:
        sys.exit("JSONの解析に失敗: %s\n%s" % (ex, text[:400]))

    by_id = {p["id"]: p for p in products}
    written, rejected = 0, []
    for item in got:
        p = by_id.get(item.get("id"))
        if not p:
            continue
        why = verify(p, item)
        if why:
            rejected.append(((p.get("title") or "")[:32], why))
            continue
        p["caption"] = item["caption"].strip()
        p["description"] = item["description"].strip()
        p["points"] = [x.strip() for x in item["points"] if x.strip()][:3]
        written += 1

    print("\n✅ %d件に書きました" % written)
    for p in todo:
        if (p.get("caption") or "").strip() and p["id"] in {i.get("id") for i in got}:
            print("   %s" % p["caption"])

    if rejected:
        print("\n⚠ 検算に通らず捨てました: %d件（キャプションは空のままです）" % len(rejected))
        for title, why in rejected:
            print("   − %-32s %s" % (title, why))

    u = resp.usage
    cost = u.input_tokens / 1e6 * 5 + u.output_tokens / 1e6 * 25
    print("\n   入力 %d / 出力 %d トークン（約 %.1f円）"
          % (u.input_tokens, u.output_tokens, cost * 155))

    if dry:
        print("\n--dry-run なので保存していません。")
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

    # 自動実行は「動きが無い回は反映しない」ようにしてある。
    # 文章を書いたのなら、それは反映する価値のある変化なので上書きする。
    if written:
        with open(os.path.join(ROOT, ".run_changed"), "w", encoding="utf-8") as f:
            f.write("yes")

    print("\n次: python3 build.py")


if __name__ == "__main__":
    main()

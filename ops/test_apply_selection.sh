#!/usr/bin/env bash
# apply_selection.py の再現テスト。ネットワークも楽天APIも使わない。
#
# 実データ（products.json / price_history.json / curated.json）のコピーに
# 小さな改変を加えて、選定の自動適用の動きを確かめる:
#   A 現行データでは現行 curated を再現する（初回は記録の初期化のみ）
#   B キャプションが書かれた商品が追加され、理由が記録される
#   C 値上がりした掲載商品が、ゲート2の理由つきで落ちる
#   D 価格履歴が大きく欠けたら、データ異常として見送る（掲載は不変）
#   E 入れ替えが大きすぎたら見送る。--force では適用される
#   F 停止スイッチ（YASUMIRU_SELECTION_AUTO=0）では何もしない
#   G 適用後に build.py が通り、警告は従来の7件のまま
#
# 使い方: bash ops/test_apply_selection.sh

set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
# 作業場所は TMPDIR を尊重する（price_history.json が大きいので、
# 小さな tmpfs の環境では TMPDIR に広い場所を指定して回す）
TMP=$(mktemp -d "${TMPDIR:-/tmp}/selauto-test.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

NG=0
ok() { echo "OK  $1"; }
ng() { echo "NG  $1"; NG=$((NG + 1)); }
check() { # check <説明> <条件コマンド...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$desc"; else ng "$desc"; fi
}

# データ改変用の小さな道具
cat > "$TMP/mut.py" <<'EOS'
import json, sys
from pathlib import Path

case = Path(sys.argv[1]); mode = sys.argv[2]
p_path = case / "products.json"
doc = json.loads(p_path.read_text(encoding="utf-8"))
products = doc["products"]
by_id = {p["id"]: p for p in products}

if mode == "caption":  # caption_needed の商品にキャプションを付ける
    by_id[sys.argv[3]]["caption"] = "テスト用のキャプション。"
    p_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
elif mode == "drop_price":  # 値下げ: 現在価格を下げて履歴にも今日の分を足す
    pid = sys.argv[3]; new_price = int(sys.argv[4])
    by_id[pid]["price"] = new_price
    p_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    h_path = case / "price_history.json"
    h = json.loads(h_path.read_text(encoding="utf-8"))
    h.setdefault(pid, []).append(["2026-11-10", new_price])
    h_path.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")
elif mode == "clear_caption":  # キャプションを消す
    by_id[sys.argv[3]]["caption"] = ""
    p_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
elif mode == "price":  # 指定 id の現在価格を書き換える
    by_id[sys.argv[3]]["price"] = int(sys.argv[4])
    p_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
elif mode == "cut_history":  # 価格履歴のキーを割合ぶん消す
    h_path = case / "price_history.json"
    h = json.loads(h_path.read_text(encoding="utf-8"))
    keys = sorted(h)
    n = int(len(keys) * float(sys.argv[3]))
    for k in keys[:n]:
        del h[k]
    h_path.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")
elif mode == "feed_has":  # curated の feed に id があれば 0
    cur = json.loads((case / "curated.json").read_text(encoding="utf-8"))
    sys.exit(0 if any(e["id"] == sys.argv[3] for e in cur["feed"]) else 1)
elif mode == "feed_count":  # curated の feed 件数を出す
    cur = json.loads((case / "curated.json").read_text(encoding="utf-8"))
    print(len(cur["feed"]))
EOS

# 選定だけに必要な最小構成を用意する
setup_case() {
  local dir="$1"
  mkdir -p "$dir/ops"
  cp "$REPO_ROOT/apply_selection.py" "$REPO_ROOT/select_strict.py" "$dir/"
  cp "$REPO_ROOT/products.json" "$REPO_ROOT/price_history.json" \
     "$REPO_ROOT/curated.json" "$dir/"
  printf 'auto: 変更なし\n\n掲載 327件 / キャプション未記入 0件\n' > "$dir/.run_summary.txt"
  printf 'no' > "$dir/.run_changed"
}

run_select() { (cd "$1" && python3 apply_selection.py --strict > out.txt 2>&1); }

# ---- A: 現行データでは現行 curated を再現する -------------------------
setup_case "$TMP/a"
(cd "$TMP/a" && sha256sum curated.json > before.sha)
run_select "$TMP/a"
check "A: 初回で feed は変わらない" \
  python3 "$TMP/mut.py" "$TMP/a" feed_has r841f65348
check "A: 初回で feed は16件のまま" \
  test "$(python3 "$TMP/mut.py" "$TMP/a" feed_count)" = "16"
check "A: 記録（automation）が curated に種まきされる" \
  grep -q '"automation"' "$TMP/a/curated.json"
check "A: 記録ファイルができる" test -f "$TMP/a/ops/selection_log.md"
(cd "$TMP/a" && sha256sum curated.json > seeded.sha)
run_select "$TMP/a"
check "A: 2回目は何もしない（curated 不変）" \
  bash -c "cd '$TMP/a' && sha256sum -c seeded.sha"
check "A: 2回目は入れ替えなしと表示する" grep -q "入れ替えなし" "$TMP/a/out.txt"

# ---- B: 値下げで新規通過 / キャプション解消で追加 -------------------
# 対象は kitchen の rc390e58d2（中央値比 -19.9% でゲート3だけに落ちている）
setup_case "$TMP/b"
python3 "$TMP/mut.py" "$TMP/b" drop_price rc390e58d2 1000
run_select "$TMP/b"
check "B: 値下げした商品が feed に入る" \
  python3 "$TMP/mut.py" "$TMP/b" feed_has rc390e58d2
check "B: feed が17件になる" \
  test "$(python3 "$TMP/mut.py" "$TMP/b" feed_count)" = "17"
check "B: 追加の理由が記録される" \
  grep -q "新規に全ゲート通過" "$TMP/b/ops/selection_log.md"
check "B: コミットメッセージに選定の行が足される" \
  grep -q "選定: 追加" "$TMP/b/.run_summary.txt"
check "B: .run_changed が yes になる" \
  test "$(cat "$TMP/b/.run_changed")" = "yes"

# キャプションが無い間は caption_needed に留まり、書くと追加される
setup_case "$TMP/b2"
python3 "$TMP/mut.py" "$TMP/b2" drop_price rc390e58d2 1000
python3 "$TMP/mut.py" "$TMP/b2" clear_caption rc390e58d2
run_select "$TMP/b2"
check "B2: キャプションが無いと feed に入らない" \
  bash -c "! python3 \"$TMP/mut.py\" \"$TMP/b2\" feed_has rc390e58d2"
check "B2: caption_needed 行きになる" \
  grep -q "rc390e58d2" "$TMP/b2/curated.json"
python3 "$TMP/mut.py" "$TMP/b2" caption rc390e58d2
run_select "$TMP/b2"
check "B2: キャプションを書くと feed に入る" \
  python3 "$TMP/mut.py" "$TMP/b2" feed_has rc390e58d2
check "B2: 理由にキャプション解消と書かれる" \
  grep -q "キャプションが書かれて掲載" "$TMP/b2/ops/selection_log.md"

# ---- C: 値上がりした掲載商品がゲート2の理由で落ちる ------------------
setup_case "$TMP/c"
python3 "$TMP/mut.py" "$TMP/c" price r841f65348 980
run_select "$TMP/c"
check "C: 値上がりした商品が feed から落ちる" \
  bash -c "! python3 '$TMP/mut.py' '$TMP/c' feed_has r841f65348"
check "C: 落選理由が履歴最安で記録される" \
  grep -q "履歴最安" "$TMP/c/ops/selection_log.md"

# ---- D: 価格履歴が大きく欠けたら見送る -------------------------------
setup_case "$TMP/d"
run_select "$TMP/d"  # 基準（履歴の総件数）を種まきしておく
(cd "$TMP/d" && sha256sum curated.json > seeded.sha)
python3 "$TMP/mut.py" "$TMP/d" cut_history 0.3
run_select "$TMP/d"
check "D: 見送りになる（curated 不変）" \
  bash -c "cd '$TMP/d' && sha256sum -c seeded.sha"
check "D: 見送り理由がデータ異常（価格履歴）" \
  grep -q "価格履歴の総件数" "$TMP/d/ops/selection_log.md"

# ---- E: 入れ替えが大きすぎたら見送り / --force で適用 ----------------
setup_case "$TMP/e"
for id in r841f65348 rb482690c5 r99dc0d242 redc4f51e4 r8e972c23b r0726ed879 rc67a0d0c5 rc2e11945a; do
  python3 "$TMP/mut.py" "$TMP/e" price "$id" 99999
done
(cd "$TMP/e" && sha256sum curated.json > before.sha)
run_select "$TMP/e"
check "E: 大きな入れ替えは見送る（curated 不変）" \
  bash -c "cd '$TMP/e' && sha256sum -c before.sha"
check "E: 見送りが記録される" grep -q "見送り" "$TMP/e/ops/selection_log.md"
(cd "$TMP/e" && python3 apply_selection.py --strict --force > out_force.txt 2>&1)
feed_count_lt() { test "$(python3 "$TMP/mut.py" "$1" feed_count)" -lt "$2"; }
check "E: --force では適用される（feed が減る）" feed_count_lt "$TMP/e" 16
check "E: 強制適用と記録される" grep -q "強制適用" "$TMP/e/ops/selection_log.md"

# ---- F: 停止スイッチ ---------------------------------------------------
setup_case "$TMP/f"
python3 "$TMP/mut.py" "$TMP/f" price r841f65348 980
(cd "$TMP/f" && sha256sum curated.json > before.sha)
(cd "$TMP/f" && YASUMIRU_SELECTION_AUTO=0 python3 apply_selection.py --strict > out.txt 2>&1)
check "F: 停止中は何もしない" \
  bash -c "cd '$TMP/f' && sha256sum -c before.sha"
check "F: 停止中は記録ファイルも作らない" \
  bash -c "test ! -f '$TMP/f/ops/selection_log.md'"

# ---- G: 適用後に build.py が通る --------------------------------------
mkdir -p "$TMP/g"
(cd "$REPO_ROOT" && tar --exclude=.git -cf - .) | (cd "$TMP/g" && tar -xf -)
# B と同じ状態を g にも作る（値下げして、選定を適用）
python3 "$TMP/mut.py" "$TMP/g" drop_price rc390e58d2 1000
(cd "$TMP/g" && python3 apply_selection.py --strict > select_out.txt 2>&1)
(cd "$TMP/g" && python3 build.py > build_out.txt 2>&1)
check "G: build.py が通る" grep -q "厳格選定" "$TMP/g/build_out.txt"
check "G: 警告は従来の7件のまま" grep -q "直していない警告が 7件" "$TMP/g/build_out.txt"
check "G: 追加した商品が feed.json に出る" \
  grep -q "rc390e58d2" "$TMP/g/assets/data/feed.json"

echo
if [ "$NG" -gt 0 ]; then
  echo "NG が $NG 件あります"
  exit 1
fi
echo "すべて OK"

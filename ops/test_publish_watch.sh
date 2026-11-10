#!/usr/bin/env bash
# ops/publish_watch.sh の再現テスト。ネットワークも楽天APIも使わない。
#
# ローカルに bare リモートを作り、run #409 と同じ状況を再現する:
#   土台Aで自動コミット → リモートが「同じ生成ファイルを作り直す
#   手動 push」で前進 → 旧来の乗せ直し（rebase）は衝突する。
# 新しい publish_watch.sh が、最新 main の上に乗り直して
# 「手動の変更が残り、価格の更新が乗る」形で着地することを確かめる。
# ついでに、競合が無い通常経路と、やり直し自体が落ちたときの
# 記録（ops/failures.md）も確かめる。
#
# 使い方: bash ops/test_publish_watch.sh

set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export PUBLISH_WATCH_SLEEP=0

NG=0
ok()   { echo "OK  $1"; }
ng()   { echo "NG  $1"; NG=$((NG + 1)); }
check() { # check <説明> <条件コマンド...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$desc"; else ng "$desc"; fi
}

# ---- 土台を用意する -------------------------------------------------
# ops のスクリプトと regen.sh は、本番と同じく最初から tracked にしておく。
# （自動コミットにしか無いファイルは、乗り直しの reset で消える。
#   本番の fetch_rakuten.py / build.py / ops/* は tracked なので消えない）
git init --bare -q -b main "$TMP/remote.git"
git clone -q "$TMP/remote.git" "$TMP/seed"
cd "$TMP/seed"
git config user.name seed; git config user.email seed@example
mkdir -p assets/data p/demo ops
cp "$REPO_ROOT/ops/publish_watch.sh" ops/
cp "$REPO_ROOT/ops/record_failure.py" ops/
cat > regen.sh <<'EOS'
#!/usr/bin/env bash
# 取得（fetch_rakuten --watch）とビルド（build.py）の代わり。
# 外の世界の値段 external.txt から、データと生成物を作り直す。
set -eu
cd "$(dirname "$0")"
P=$(cat external.txt)
printf 'price=%s\n' "$P" > prices.txt
printf 'gen price=%s\n' "$P" > index.html
printf '{"price":%s}\n' "$P" > assets/data/feed.json
printf 'page price=%s\n' "$P" > p/demo/index.html
printf 'auto: 価格の見張り %s\n' "$P" > .run_summary.txt
printf 'yes\n' > .run_changed
EOS
printf '.run_summary.txt\n.run_changed\nexternal.txt\n' > .gitignore
printf 'price=1000\n'        > prices.txt
printf 'gen v1 price=1000\n' > index.html
printf '{"v":1,"price":1000}\n' > assets/data/feed.json
printf 'page v1\n'           > p/demo/index.html
printf 'サイトの手書きメモ\n' > site-note.txt
git add -A; git commit -qm "初期状態"
git push -q origin main

# watch 側クローン（土台は clone で揃う）
make_watch() { # $1=ディレクトリ名
  git clone -q "$TMP/remote.git" "$TMP/$1"
}

origin_file() { # リモート main のファイル中身
  git -C "$TMP/seed" fetch -q origin
  git -C "$TMP/seed" show "origin/main:$1"
}
file_is()  { [ "$(origin_file "$1")" = "$2" ]; }
file_has() { origin_file "$1" | grep -q "$2"; }
parent_is_manual() { [ "$(top_parent)" = "$MANUAL_SHA" ]; }
subject_is_auto900() { [ "$(top_subject)" = 'auto: 価格の見張り 900' ]; }
top_parent() {
  git -C "$TMP/seed" fetch -q origin
  git -C "$TMP/seed" rev-parse origin/main^
}
top_subject() {
  git -C "$TMP/seed" fetch -q origin
  git -C "$TMP/seed" log -1 --format=%s origin/main
}

# ---- シナリオ1: 競合 → 回復して着地する ------------------------------
make_watch watch1
cd "$TMP/watch1"
echo 900 > external.txt
bash regen.sh                      # 土台Aで取得＋ビルド済みの状態

# その最中に、手作業の push が同じ生成ファイルを作り直して前進する
git clone -q "$TMP/remote.git" "$TMP/manual"
cd "$TMP/manual"
git config user.name human; git config user.email human@example
printf 'gen v2 price=1000\n'        > index.html
printf '{"v":2,"price":1000}\n'     > assets/data/feed.json
printf '手作業で足したもの\n'       > manual.txt
printf 'サイトの手書きメモ（手動で追記）\n' >> site-note.txt
git add -A; git commit -qm "手動: 全面再生成"
git push -q origin main
MANUAL_SHA=$(git rev-parse HEAD)

cd "$TMP/watch1"
OUT=$(bash ops/publish_watch.sh "bash regen.sh" 2>&1)
RC=$?
echo "$OUT" | sed 's/^/  [watch1] /'
[ "$RC" = 0 ] && ok "シナリオ1: 終了コード0" || ng "シナリオ1: 終了コード0 (rc=$RC)"
echo "$OUT" | grep -q "衝突"     && ok "シナリオ1: 衝突を検知した" || ng "シナリオ1: 衝突を検知した"
echo "$OUT" | grep -q "乗り直し" && ok "シナリオ1: 回復経路に入った" || ng "シナリオ1: 回復経路に入った"
check "シナリオ1: 手動の追加ファイルが残っている" \
  file_is manual.txt '手作業で足したもの'
check "シナリオ1: 手動の追記が残っている" \
  file_has site-note.txt '手動で追記'
check "シナリオ1: 価格の更新が乗っている" \
  file_is index.html 'gen price=900'
check "シナリオ1: prices.txt も新しい値" \
  file_is prices.txt 'price=900'
check "シナリオ1: 自動コミットの親が手動コミット" \
  parent_is_manual
check "シナリオ1: コミットメッセージは取得の要約" \
  subject_is_auto900

# ---- シナリオ2: 競合なし（通常経路は従来どおり） ---------------------
make_watch watch2
cd "$TMP/watch2"
echo 800 > external.txt
bash regen.sh
OUT=$(bash ops/publish_watch.sh "bash regen.sh" 2>&1)
RC=$?
[ "$RC" = 0 ] && ok "シナリオ2: 終了コード0" || ng "シナリオ2: 終了コード0 (rc=$RC)"
echo "$OUT" | grep -q "乗り直し" \
  && ng "シナリオ2: 回復経路に入らない" || ok "シナリオ2: 回復経路に入らない"
check "シナリオ2: 価格の更新が乗っている" \
  file_is index.html 'gen price=800'

# ---- シナリオ3: やり直し自体が落ちたら、警告＋記録で終わる -----------
make_watch watch3
cd "$TMP/watch3"
echo 700 > external.txt
bash regen.sh
git clone -q "$TMP/remote.git" "$TMP/manual3"
cd "$TMP/manual3"
git config user.name human; git config user.email human@example
printf 'gen v3 price=800\n' > index.html
git add -A; git commit -qm "手動: また作り直し"
git push -q origin main
cd "$TMP/watch3"
OUT=$(bash ops/publish_watch.sh "false" 2>&1)
RC=$?
[ "$RC" != 0 ] && ok "シナリオ3: 終了コードが0でない" || ng "シナリオ3: 終了コードが0でない"
echo "$OUT" | grep -q "::warning::" \
  && ok "シナリオ3: 警告が出た" || ng "シナリオ3: 警告が出た"
grep -q "記録の送信（やり直し）" "$TMP/watch3/ops/failures.md" \
  && ok "シナリオ3: ops/failures.md に記録された" \
  || ng "シナリオ3: ops/failures.md に記録された"

echo
if [ "$NG" = 0 ]; then echo "全部 OK"; else echo "NG: $NG 件"; exit 1; fi

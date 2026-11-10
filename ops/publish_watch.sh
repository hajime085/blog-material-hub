#!/usr/bin/env bash
# 変化をサイトへ反映する（コミットして push する）。watch.yml の
# 「変化があれば反映する」段から呼ばれる。単体でテストできるよう、
# 段の中身をここへ切り出した（ops/test_publish_watch.sh が相手）。
#
# 2026-11-10: run #409 の失敗を受けて、回復経路を足した。
# 価格の取得は正常に終わっていたのに、反映の段で落ちた。
# 実行中に割り込んだ手作業の push（★3.8 の全面再生成）と同じ
# 生成ファイル（index.html など）を双方が作り直していたため、
# こちらの自動コミットを乗せ直す（rebase）たびに同じ場所で衝突し、
# 6回繰り返して諦めた（便の警告「記録を送れませんでした」）。
# 衝突はタイミングではなく内容で決まる。同じ土台で繰り返しても直らない。
#
# だから、乗せ直しの衝突を検知したら、古い土台の自動コミットは捨てて、
# 最新の origin/main に乗り直し、取得とビルドからやり直して送る。
# やり直しは最大2回まで。それでも送れなければ、従来どおり
# 警告＋ops/failures.md への記録で終わる（exit 1）。
# 競合が無いときの動きは、従来の段と変えていない。
#
# 使い方: ops/publish_watch.sh "やり直しのときに実行するコマンド"
#   watch.yml からは取得（fetch_rakuten.py --watch）と
#   ビルド（build.py）を渡す。テストでは中身を差し替えられる。
#
# PUBLISH_WATCH_SLEEP: リトライ間隔の倍率（既定10＝従来どおり i*10 秒）。
#   テストでは 0 にして待たない。

set -u
cd "$(dirname "$0")/.."

REDO_CMD="${1:-}"
SLEEP_BASE="${PUBLISH_WATCH_SLEEP:-10}"
MAX_REDO=2

git config user.name "yasumiru-bot"
git config user.email "bot@users.noreply.github.com"

give_up() {
  # $1=警告文 $2=記録の見出し。従来どおり、記録して失敗で終わる。
  echo "::warning::$1"
  python3 ops/record_failure.py .run_summary.txt "$2"
  exit 1
}

commit_changes() {
  # 戻り値: 0=コミットした / 1=反映するものがない（正常に終えてよい）
  if [ "$(cat .run_changed 2>/dev/null)" = "no" ]; then
    echo "見に行ったが、伝える変化はなかった。反映しない。"
    echo "（価格の確認日が変わるその日の1回目は、動きが無くても反映する）"
    git checkout -- . || true
    return 1
  fi
  if git diff --quiet && git diff --cached --quiet; then
    echo "変化なし。何もしません。"
    return 1
  fi
  git add -A
  git commit -F .run_summary.txt
  return 0
}

try_push() {
  # 従来どおり、取り込んでから送る。
  # 戻り値: 0=送れた / 1=送れなかった（押し合いなど） /
  #         2=乗せ直しが衝突した（この土台では何度やっても直らない）
  local i
  for i in 1 2 3 4 5 6; do
    if git pull --rebase --autostash origin main; then
      if git push origin main; then
        return 0
      fi
    elif [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
      git rebase --abort 2>/dev/null || true
      echo "乗せ直しが衝突しました。古い土台のままでは直りません。"
      return 2
    fi
    echo "やり直します ($i)"
    sleep $((i * SLEEP_BASE))
  done
  return 1
}

redo=0
while :; do
  commit_changes || exit 0
  rc=0
  try_push || rc=$?
  [ "$rc" = 0 ] && exit 0
  redo=$((redo + 1))
  if [ "$redo" -gt "$MAX_REDO" ] || [ -z "$REDO_CMD" ]; then
    give_up "記録を送れませんでした。次の回が取り込みます" "記録の送信"
  fi
  echo "最新の土台に乗り直して、取得からやり直します（$redo 回目）"
  if ! git fetch origin main; then
    sleep $((2 * SLEEP_BASE))
    git fetch origin main \
      || give_up "記録を送れませんでした。次の回が取り込みます" "記録の送信"
  fi
  # 古い土台の自動コミットと、その生成物を捨てる。
  # .run_changed / .run_summary.txt は .gitignore 側なので残るが、
  # このあとの取得が作り直す。
  git reset --hard origin/main
  if ! bash -c "$REDO_CMD"; then
    give_up "やり直しの取得／ビルドが落ちました。記録を送れませんでした" \
      "記録の送信（やり直し）"
  fi
done

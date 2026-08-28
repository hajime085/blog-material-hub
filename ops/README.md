# 予定実行を Cloudflare から起こす

## なぜ

GitHub Actions の `schedule` は届かないことがある。
このリポジトリで 8/21〜8/28 の実行400件を数えた結果:

- 定刻に動いた回 **0回**（遅れは平均48分・最大77分）
- 1日の予定回数を増やすほど届かなくなる
  （5〜7回/日は6日連続100%、11回で91%、14回で21%、17回で0%）
- 8/28 は 04:56 を最後に16時間以上ひとつも来なかった
- ワークフローは `active` のまま、失敗もゼロ。イベントが配信されないだけ

回数を8回/日まで戻しても直らなかったので、
「いつ動かすか」だけを GitHub の外に出す。

`workflow_dispatch`（APIで叩く実行）はただのHTTPリクエストなので取りこぼさない。
このリポジトリでの実績も 2/2 成功。

処理そのものは、いまの GitHub Actions のまま。1行も変えない。

    Cloudflare の時計 → GitHub API → いまのワークフローが動く

## 置きかた

### 1. GitHub のトークンを作る

Settings → Developer settings → Personal access tokens → **Fine-grained tokens**

| 項目 | 値 |
|---|---|
| Repository access | Only select repositories → `blog-material-hub` |
| Permissions → Actions | **Read and write** |
| 有効期限 | 好きな長さ（切れると止まるので、長めか無期限） |

ほかの権限は要らない。

### 2. Worker を作る

Cloudflare → Workers & Pages → Create → Worker

`cron-worker.js` の中身をそのまま貼って Deploy。

### 3. トークンを入れる

その Worker の Settings → Variables and Secrets →
**Secret** で追加する。

| 名前 | 値 |
|---|---|
| `GH_TOKEN` | 1. で作ったトークン |

Secret にすること。Environment Variable（平文）にはしない。

### 4. 時計を足す

Settings → Trigger Events → Cron Triggers → Add

    20 * * * *

**1本だけ。** 毎時 :20（UTC）に起きて、その時刻に用があるものだけ叩く。
どの時刻に何を叩くかは `cron-worker.js` の `PLAN` にある。

## 確かめかた

Worker の URL をブラウザで開くと、トークンの有無と予定表が出る。

叩けているかどうかは、こちらで見る:

    python3 threads.py --doctor

## 動いたら

GitHub 側の `schedule:` を外す。起こす役は1つにしておく。
二重に起きても、投稿は錠と10分ルールで止まるので事故にはならないが、
どちらが起こしたのか分からない状態にはしない。

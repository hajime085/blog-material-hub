// ヤスミルの予定実行を、GitHub の代わりにここから起こす。
//
// なぜ要るか:
//   GitHub Actions の schedule は「best effort」で、届かないことがある。
//   このリポジトリで8日ぶん数えた結果、定刻に動いた回は0回、
//   遅れは平均48分。さらに1日の予定回数を増やすほど届かなくなり、
//   8/28 は 04:56 を最後に16時間以上ひとつも来なかった。
//
//   一方 workflow_dispatch（APIで叩く実行）は、ただのHTTPリクエストなので
//   確実に届く。実績も 2/2 成功。
//   だから「いつ動かすか」だけを Cloudflare 側に移す。
//   処理そのものは、いまの GitHub Actions のまま動く。
//
// 置きかた:
//   1. GitHub で fine-grained token を作る
//        対象リポジトリ: hajime085/blog-material-hub
//        権限: Actions = Read and write（これだけでよい）
//   2. Cloudflare の Workers で新しい Worker を作り、このファイルを貼る
//   3. Settings → Variables → Secret に GH_TOKEN として 1. のトークンを入れる
//   4. Settings → Trigger Events → Cron Triggers に「20 * * * *」を1本だけ足す
//
//   Cron は1本だけでよい。毎時 :20（UTC）に起きて、
//   その時刻に用があるものだけを叩く。
//   1本で足りるので、Worker あたりの Cron 本数の上限にも当たらない。

const REPO = "hajime085/blog-material-hub";

// UTC の時 → その時刻に叩くワークフロー（複数可）
// 日本時間は +9時間。Cron は毎時 :20 に起きる。
//
// 枠ごとに1回ずつ起こす。
// 以前は1回の起動が4時間生きて複数の枠を見ていたが、
// 回が重なって順番待ちになり、押せなかった記録をもう片方が
// 読み落として同じ投稿を二度出した（8/29 14:00 と 14:21）。
// workflow_dispatch は取りこぼさないので、長く生かす必要はない。
const T = "threads.yml";
const W = "watch.yml";

const PLAN = {
  22: [T, W],   // JST  7:20  投稿(7時の枠) + 見張り
  0:  [T],      // JST  9:20  投稿(9時の枠)
  2:  [W],      // JST 11:20  見張り
  3:  [T],      // JST 12:20  投稿(12時の枠)
  5:  [T],      // JST 14:20  投稿(14時の枠)
  7:  [W],      // JST 16:20  見張り
  8:  [T],      // JST 17:20  投稿(17時の枠)
  10: [T],      // JST 19:20  投稿(19時の枠)
  11: [T],      // JST 20:20  投稿(20時の枠)
  12: [T, W],   // JST 21:20  投稿(21時の枠) + 見張り
  14: [T],      // JST 23:20  投稿(23時の枠)
  15: [T],      // JST  0:20  投稿(0時の枠)
};

async function dispatch(file, token) {
  const url =
    `https://api.github.com/repos/${REPO}/actions/workflows/${file}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      // GitHub は User-Agent が無いと 403 を返す
      "User-Agent": "yasumiru-cron",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: {} }),
  });
  // 成功は 204 No Content
  if (res.status !== 204) {
    console.log(`${file} を起こせませんでした: ${res.status} ${await res.text()}`);
    return false;
  }
  console.log(`${file} を起こしました`);
  return true;
}

export default {
  async scheduled(event, env, ctx) {
    const hour = new Date(event.scheduledTime).getUTCHours();
    const jobs = PLAN[hour];
    if (!jobs) return;                   // この時刻は用が無い
    if (!env.GH_TOKEN) {
      console.log("GH_TOKEN が入っていません");
      return;
    }
    ctx.waitUntil(
      Promise.all(jobs.map((f) => dispatch(f, env.GH_TOKEN))),
    );
  },

  // ブラウザで開くと、いまの状態と次の予定を返す。
  // 動いているかどうかを人が確かめられるようにしておく。
  async fetch(req, env) {
    const now = new Date();
    const h = now.getUTCHours();
    const rows = Object.entries(PLAN)
      .map(([k, v]) =>
        `JST ${String((Number(k) + 9) % 24).padStart(2, "0")}:20  ${v.join(" + ")}`)
      .sort();
    return new Response(
      [
        "ヤスミルの予定実行",
        `いま UTC ${h}時。トークン: ${env.GH_TOKEN ? "あり" : "なし"}`,
        "",
        ...rows,
      ].join("\n"),
      { headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  },
};

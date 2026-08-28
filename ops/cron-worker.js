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

// UTC の時 → 叩くワークフロー
// 日本時間は +9時間。
const PLAN = {
  21: { file: "threads.yml", inputs: { hours: "4" } },   // JST  6:20 投稿
  22: { file: "watch.yml" },                             // JST  7:20 見張り
  1:  { file: "threads.yml", inputs: { hours: "4" } },   // JST 10:20 投稿
  2:  { file: "watch.yml" },                             // JST 11:20 見張り
  6:  { file: "threads.yml", inputs: { hours: "4" } },   // JST 15:20 投稿
  7:  { file: "watch.yml" },                             // JST 16:20 見張り
  11: { file: "threads.yml", inputs: { hours: "4" } },   // JST 20:20 投稿
  12: { file: "watch.yml" },                             // JST 21:20 見張り
};

async function dispatch(job, token) {
  const url =
    `https://api.github.com/repos/${REPO}/actions/workflows/${job.file}/dispatches`;
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
    body: JSON.stringify({ ref: "main", inputs: job.inputs || {} }),
  });
  // 成功は 204 No Content
  if (res.status !== 204) {
    console.log(`${job.file} を起こせませんでした: ${res.status} ${await res.text()}`);
    return false;
  }
  console.log(`${job.file} を起こしました`);
  return true;
}

export default {
  async scheduled(event, env, ctx) {
    const hour = new Date(event.scheduledTime).getUTCHours();
    const job = PLAN[hour];
    if (!job) return;                    // この時刻は用が無い
    if (!env.GH_TOKEN) {
      console.log("GH_TOKEN が入っていません");
      return;
    }
    ctx.waitUntil(dispatch(job, env.GH_TOKEN));
  },

  // ブラウザで開くと、いまの状態と次の予定を返す。
  // 動いているかどうかを人が確かめられるようにしておく。
  async fetch(req, env) {
    const now = new Date();
    const h = now.getUTCHours();
    const rows = Object.entries(PLAN)
      .map(([k, v]) => `JST ${String((Number(k) + 9) % 24).padStart(2, "0")}:20  ${v.file}`)
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

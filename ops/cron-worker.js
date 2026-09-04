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

// セールの間だけ、投稿と見張りを増やす。
//
// 投稿は10本→14本。増やすぶんは全部「商品」で、夜に寄せてある。
// セール中は買う気で見ている人が多く、値段も日替わりで動くため。
//
// セール中は値段が日に何度も動き、終わる特価も出る。
// 4回では、終わったものを載せたまま何時間も置くことになる。
// 増やすのは「見に行く回数」だけで、1回に新しく載せる数は
// そのぶん減らしてある（config.json）。
// 拾う量が増えても、文章を書くのが追いつかなければ、
// 載らない商品が積み上がるだけで、読む人には何も変わらないため。
//
// 期間が過ぎたら自動でもとに戻る。
// 手で戻す作りにすると、戻し忘れてそのままになる。
const SALE_UNTIL = Date.parse("2026-09-10T16:59:59Z");  // JST 9/11 01:59
const SALE_EXTRA = {
  4:  [T],      // JST 13:20  昼の商品
  5:  [W],      // JST 14:20  昼の見張り
  9:  [T],      // JST 18:20  夕方の商品
  11: [W],      // JST 20:20  セール開始・日替わりの直後
  13: [T],      // JST 22:20  夜の商品
  15: [W],      // JST  0:20  日付が変わった直後
  16: [T],      // JST  1:20  深夜の商品
  18: [W],      // JST  3:20  夜のあいだに1回
};

function planFor(when) {
  const hour = new Date(when).getUTCHours();
  const base = PLAN[hour] || [];
  if (when > SALE_UNTIL) return base;
  const extra = (SALE_EXTRA[hour] || []).filter((f) => !base.includes(f));
  return base.concat(extra);
}

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

// トークンの期限を GitHub に聞く。
// 認証つきで叩くと、応答のヘッダに期限が入って返ってくる。
// 切れたら全部止まるので、人が覚えていなくても見えるようにしておく。
async function tokenExpiry(token) {
  try {
    const res = await fetch("https://api.github.com/rate_limit", {
      headers: {
        Authorization: `Bearer ${token}`,
        "User-Agent": "yasumiru-cron",
        Accept: "application/vnd.github+json",
      },
    });
    if (res.status === 401) return "使えません（切れたか、無効です）";
    const exp = res.headers.get("github-authentication-token-expiration");
    if (!exp) return "期限なし";
    const d = new Date(exp.replace(" UTC", "Z").replace(" ", "T"));
    if (isNaN(d)) return exp;
    const days = Math.floor((d - new Date()) / 86400000);
    const ymd = d.toISOString().slice(0, 10);
    if (days < 0) return `${ymd} に切れています`;
    if (days <= 14) return `${ymd} まで（あと${days}日 ← 作り直してください）`;
    return `${ymd} まで（あと${days}日）`;
  } catch (e) {
    return `確かめられませんでした: ${e}`;
  }
}

export default {
  async scheduled(event, env, ctx) {
    const jobs = planFor(event.scheduledTime);
    if (!jobs.length) return;            // この時刻は用が無い
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
    const exp = env.GH_TOKEN ? await tokenExpiry(env.GH_TOKEN) : "—";
    // いまの日付で見たときの、実際に動く予定を出す。
    // PLAN だけを出すと、セール中の増えたぶんが見えない。
    const hours = new Set(
      [...Object.keys(PLAN), ...Object.keys(SALE_EXTRA)].map(Number));
    const sale = now.getTime() <= SALE_UNTIL;
    const rows = [...hours]
      .map((k) => {
        const at = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(),
                            now.getUTCDate(), k, 20);
        return `JST ${String((k + 9) % 24).padStart(2, "0")}:20  ` +
               planFor(at).join(" + ");
      })
      .filter((r) => !r.endsWith("  "))
      .sort();
    return new Response(
      [
        "ヤスミルの予定実行",
        `いま UTC ${h}時。トークン: ${env.GH_TOKEN ? "あり" : "なし"}`,
        `トークンの期限: ${exp}`,
        sale ? "セール中：見張りを1日8回に増やしています（9/11 01:59まで）"
             : "通常運転：見張りは1日4回",
        "",
        ...rows,
      ].join("\n"),
      { headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  },
};

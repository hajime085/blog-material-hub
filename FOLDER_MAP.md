# フォルダ地図（blog-material-hub）

このフォルダは、そのまま **sozaino.com のサイト本体**です。
置いてあるものは大きく5種類。生成されたページ（HTMLなど）はプログラムが作るので、手で書き換えません。

## 1. プログラム（仕組み側）

| ファイル | 役割 |
|---|---|
| `build.py` | データからサイト全体のページを組み立てる |
| `fetch_rakuten.py` | 楽天から価格を取り直し、価格履歴を更新する |
| `select_strict.py` | 掲載の選定ルール（ゲート）本体 |
| `apply_selection.py` | 見張りのたびに選定を自動で回し直す（安全弁つき） |
| `threads.py` | Threads へ投稿する |
| `learn.py` | 投稿の反応を集計して学ぶ |
| `rules.py` / `pitch.py` | 投稿文のルール判定と文面づくり |
| `icons.py` / `markdown.py` | アイコン生成・文章整形の補助 |
| `serve.py` | 自分の Mac で試し見するための窓口（localhost:4321） |

## 2. データ（プログラムが読み書きする）

| ファイル | 役割 |
|---|---|
| `products.json` | 商品データの本体（説明文・投稿文もここ） |
| `price_history.json` | 自前で観測した価格の履歴 |
| `curated.json` | いま掲載している商品のリスト（選定が自動生成。手で触らない） |
| `config.json` / `config_strict.json` | 設定（投稿の枠・選定の基準値） |
| `events.json` | 楽天イベントと締切の予定（締切表示の唯一の出どころ） |
| `genres.json` / `links.json` / `pages.json` | 分類・リンク・固定ページのデータ |
| `featured.json` / `featured.txt` | 特集まわりのデータ |
| `tips.json` | お役立ち投稿の素材 |
| `learnings.json` | 学習の集計結果 |
| `threads_posted.json` | 投稿した記録 |
| `hand_attic.json` | 手動で退避した商品の置き場 |
| `research.json` | 調査メモのデータ |

## 3. 特集・記事の元データ

| 置き場 | 役割 |
|---|---|
| `event_src/` | 特集ページ（ケーキ・おせち等）の元データ。特集を直すときはここ |
| `content/` | ガイド記事などの元データ |
| `templates/` | ページのひな形 |

## 4. 公開中のページ（生成物・手で触らない）

`index.html`、`c/`、`categories/`、`p/`（商品ページ）、`event/`（特集）、`guide/`、
`off/`、`watch/`、`kaimawari/`、`post/`、`about/`、`contact/`、`privacy/`、`terms/`、`disclaimer/`、
`assets/`（画像・CSS・JS）、`feed.xml`、`sitemap.xml`、`robots.txt`、`404.html`

これらは `build.py` がデータから作ります。直したいときは元データ（2・3）を直して組み立て直します。

## 5. 設定・運用

| 置き場 | 役割 |
|---|---|
| `.github/` | 自動実行の設計図（見張り・投稿・学習）。※アプリ連携では変更できない場所 |
| `ops/` | 運用スクリプトと記録（選定の記録 `selection_log.md` など） |
| `_headers` / `_redirects` | 配布まわりの設定 |
| `.nojekyll` / `favicon.ico` / `favicon-192.png` / `apple-touch-icon.png` | サイトの備品 |
| `google0a01d8ae9f2df466.html` | Google の所有確認（消すと確認が外れる） |
| `.env.example` | 合言葉ファイルの見本（本物の `.env` はサイトに登録されない） |
| `README.md` | このリポジトリの説明 |

## ローカルだけのもの（サイトに登録されない）

- `backup/` — 保険の控え。サイトには出ていない。残すならフォルダの外に移すとすっきりする
- `.DS_Store`、`__pycache__/` — Mac と Python が自動で作るもの。登録から除外済み
- `*.patch` — 変更の指示書。適用が終わったら消してよい（登録からも除外済み。二度とサイトに混ざらない）

## 約束ごと

- ファイルの移動・名前変更はしない（URL と bot の呼び出し先が壊れる）
- 迷ったらこの地図を見る。新しい種類のファイルが増えたら、ここに1行足す

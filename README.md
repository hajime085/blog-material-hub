# ヤスミル / YASUMIRU

楽天市場で値下がりした商品だけを縦に流す特価フィード。
Threads のプロフィールから流入させることを前提に、スマホ優先で組んでいます。

- 本番: https://sozaino.com
- 静的サイト（サーバー不要）。`products.json` を更新して `build.py` を回すと全ページが再生成されます。

---

## 毎日やること

```bash
python3 fetch_rakuten.py   # 楽天APIから商品を取得 → products.json
python3 build.py           # サイト全体を再生成
```

そのあと git で push すれば公開されます。

ローカルで確認したいときは:

```bash
python3 serve.py
```

http://localhost:4321 が開きます。

---

## 最初にやる設定

### 1. 楽天のIDを `.env` に入れる

```bash
cp .env.example .env
```

`.env` を開いて、3行を埋めるだけです。

```
RAKUTEN_APP_ID=あなたのアプリID
RAKUTEN_ACCESS_KEY=あなたのアクセスキー
RAKUTEN_AFFILIATE_ID=あなたのアフィリエイトID
```

アプリIDとアクセスキーは、楽天ウェブサービスの
[アプリ一覧](https://webservice.rakuten.co.jp/app/list) で確認できます。

`.env` は `.gitignore` に入っているのでコミットされません。
`.env.example` のほうには**絶対に本物のIDを書かないでください**（こちらは追跡対象です）。

### 2. サイト情報（`config.json` の `site`）

| キー | 内容 |
|---|---|
| `contactFormUrl` | お問い合わせ用 Google フォームのURL。空にするとお問い合わせページのボタンが「準備中」表示に変わる |
| `threads` | Threads アカウントのURL。設定するとフッター・ドロワー・運営者カードにリンクが出る |
| `operator.name` | 運営者の表示名。ハンドルでも本名でも好きなものに |
| `operator.bio` | 運営者カードの紹介文 |
| `operator.avatar` | 運営者アイコン（480x480）。差し替えるときは `assets/img/operator.png` と `operator-sm.png` の両方を置き換える |

いずれも変更後に `python3 build.py` で全ページに反映されます。

---

## 楽天API（2026年の新仕様）について

2026年のインフラ刷新で、以前の仕様から次の点が変わっています。
このリポジトリのスクリプトは新仕様に対応済みです。

| | 旧 | 新 |
|---|---|---|
| エンドポイント | `app.rakuten.co.jp/services/api/` | `openapi.rakuten.co.jp/ichibams/api/` |
| 認証 | `applicationId` のみ | `applicationId` + **`accessKey`** |
| アクセス元 | 制限なし | **Origin / Referer を検査**。許可外は403 |

そのため、アプリ登録時の設定が動作に直結します。

- **アプリケーションタイプ**は「**Webアプリケーション**」
  （「バックエンドサービス」はIPアドレスの登録が必要で、自宅回線だとIPが変わるたびに再登録になる）
- **許可されたWebサイト**に `sozaino.com` と `*.sozaino.com` を登録
- `fetch_rakuten.py` は `config.json` の `site.url` を Origin / Referer として送ります。
  **ドメインを変えたときは `site.url` も必ず更新してください。**ここがずれると403になります。

403や400が出たときは、原因の候補をスクリプトが表示します。

---

## 割引率の考え方（重要）

**楽天の商品検索APIは「通常価格・定価」を返しません。** 返ってくるのは現在価格だけです。

そのため `fetch_rakuten.py` は `price_history.json` に価格の観測履歴を持ち、
**過去60日で自分が観測した最高値**を比較の基準にします。
これは定価ではないので、サイト上の表記も「通常 ¥◯◯」ではなく **「以前 ¥◯◯」** になります。

- 初回実行時は履歴がないため、値下がり判定できる商品はほぼありません。
  1日1回ほど回して履歴が溜まると拾えるようになります。
- 定価が分かっている商品は、`products.json` でその商品に
  `"listPrice": 6980, "priceBasis": "manual"` と書けば「通常」表記になります。

---

## 商品を手で足す

`products.json` の `products` 配列に足して `python3 build.py`。

```json
{
  "id": "y0099",
  "title": "商品名",
  "caption": "1本あたり48円。箱で買う以外に安くする方法がない。",
  "category": "food",
  "price": 1150,
  "listPrice": 1680,
  "priceBasis": "manual",
  "unitNote": "1本あたり約48円",
  "image": "/assets/img/ph-food.svg",
  "affiliateUrl": "楽天アフィリエイトのリンク",
  "shop": "ショップ名",
  "tags": ["送料無料", "タイムセール"],
  "points": ["箇条書きの特徴", "3つくらいが読みやすい"],
  "description": "本文。改行で段落が分かれます。",
  "postedAt": "2026-08-20"
}
```

- `id` はURLになります（`/p/y0099/`）。一度公開したら変えないこと。
- `hidden: true` を足すと、データを残したまま非公開にできます。
- `tags` に `在庫わずか` `タイムセール` `本日限り` を入れると赤いタグになります。

### caption が効きます

`caption` はカードで手書きPOP風に出る一言です。ここが埋まっているかどうかで
フィードの読み味がかなり変わります。`fetch_rakuten.py` は自動では書けないので、
実行後に未記入の件数を表示します。**安い理由を一言で**書くのがいちばん効きます。

> 「1本あたり48円。箱で買う以外に安くする方法がない。」
> 「去年の型が入れ替えで半額に。中身はほぼ変わらない。」

---

## 商品の質をどう担保しているか

楽天のランキングAPI・クーポンAPI・ジャンル検索APIは、いずれも新ゲートウェイでは
404で使えません。**使えるのは商品検索APIだけ**です。そのうえで、次の4つで質を作っています。

**1. ジャンルを固定する**（`config.json` の `categories[].genres`）
キーワード検索はジャンルをまたいで散らばります（ベビーに大人用おむつ、ペットにゴミ箱が
混ざるのはこれが原因）。ジャンルIDを指定したカテゴリは、そのジャンル内だけを検索します。
ジャンル検索APIが無いため、**IDは実際にAPIを叩いて中身を確認して同定**しました。
新しいジャンルを足すときは `python3 fetch_rakuten.py --genres` で調べられます。

**2. 価格の上限**（`minPrice` / `maxPrice`）
上限が高いと高額商品ばかり並んでお得感が消えます。APIに渡して楽天側で絞ります。

**3. 実績のない商品を弾く**（`minReviewCount` / `minReviewAverage`）
ランキングAPIが無い以上、レビュー数が「多くの人が実際に買った」ことの唯一の手がかりです。
サイドバーの「いま売れているもの」もこれを根拠にしています。

> 並べ替えには `-affiliateRate`（報酬率が高い順）も指定できますが、使っていません。
> それは「運営者が儲かる順」であって「お得な順」ではないからです。

**4. 商品名の整形**
楽天の商品名は店舗の販促文言で埋まっています。飾りを外し、「送料無料」などはタグへ、
「1本あたり48円」のような単価は値札の下段（`unitNote`）へ移します。
整形前の商品名は `rawTitle` に残るので、ルールを変えてもAPIを叩き直さずに作り直せます。

---

## クーポンについて

**楽天にクーポンAPIは存在しません。**自動取得はできません。
手で貼る枠だけ用意してあるので、`config.json` の `site.coupon` を埋めると
サイドバーにクーポンのカードが出ます。空にすると枠ごと消えます。

```json
"coupon": { "label": "全ショップ 5%OFF", "note": "8/25 23:59まで", "url": "..." }
```

---

## カテゴリを増やす・変える

`config.json` の `categories` を編集して `build.py`。

```json
{ "slug": "食品", "label": "食品・飲料・お酒", "short": "食品",
  "icon": "food", "keywords": ["取得に使う検索ワード"] }
```

- `icon` は `icons.py` に定義したアイコンID。新しいカテゴリを足すときは
  `icons.py` にもアイコンを追加してください（**絵文字は使いません**）。
- `genreId` を足すと、そのジャンルに絞って取得します。ID一覧は
  `python3 fetch_rakuten.py --genres` で確認できます。

---

## ファイル構成

```
config.json          サイト設定・カテゴリ定義
products.json        商品データ（これが元データ）
pages.json           固定ページの本文
price_history.json   価格の観測履歴（fetch_rakuten.py が管理）
icons.py             SVGアイコン定義
build.py             サイト生成
fetch_rakuten.py     楽天APIから商品取得
serve.py             ローカル確認用サーバー
templates/base.html  全ページ共通の外枠
assets/css/style.css デザイン
assets/js/app.js     検索・並べ替え・もっと見る

── ここから下は build.py の生成物。直接編集しない ──
index.html  c/  p/  categories/  about/  contact/
privacy/  disclaimer/  terms/  404.html
sitemap.xml  feed.xml  robots.txt
assets/data/feed.json  assets/img/icons.svg  assets/img/ph-*.svg
```

---

## デザインの決めごと

**コンセプト: 値札POPが主役のSNSフィード**
賑やかさを価格まわりだけに集中させ、それ以外は静かに整えています。

### 配色

| 変数 | 値 | 役割 |
|---|---|---|
| `--ground` | `#FFF1E3` | 売場の暖色地 |
| `--ink` | `#1A1410` | 文字・ヘッダー帯 |
| `--pop` | `#FFD31F` | POPカードの黄 |
| `--sale` | `#F5333F` | 価格・割引シールの赤 |
| `--fresh` | `#00A88E` | カテゴリタグ（熱を冷ます役） |

### 書体

| 用途 | 書体 | 注意 |
|---|---|---|
| 見出し（自分で書く短いコピー） | Dela Gothic One | **単一ウェイト。`font-weight:700` を当てると合成ボールドで字が潰れる** |
| 価格の数字 | Anton | 同上 |
| 本文・商品名 | Zen Kaku Gothic New | 900 まである。商品名はこちらで受ける |
| 手書きPOP風の一言 | Yusei Magic | ごく少量だけ |

Dela Gothic One は極太なので、**漢字が多い長い文字列（商品名など）には使いません**。
画数が潰れて読めなくなります。使うのは大きく出る場所だけです。

### 絵文字は使わない

OSごとに字面も色も変わり、線の太さが他のUIと揃わないためです。
アイコンはすべて `icons.py` に定義した24pxグリッドの線画SVGで、
`assets/img/icons.svg` のスプライトから `<use>` で引いています。

---

## 品質のライン

- スマホ 375px から破綻しない（横スクロールなし）
- キーボードフォーカスが常に見える
- `prefers-reduced-motion` を尊重（電光掲示板の流れが止まる）
- 商品ページに Product / BreadcrumbList の構造化データ

---

## 法務まわり

全ページのフッターと、商品ページ内に楽天アフィリエイトの表記を入れています。
`disclaimer`（免責事項）に、価格変動と販売主体が楽天市場の各店舗であることを明記しています。
文面を変える場合は `pages.json` を編集して `build.py`。

# 📦 Blog Material Hub

ブログ、SNS、Webサイト向けの高品質素材を無料配信するシステムです。手動で素材をアップロード・管理でき、自動的にギャラリーに反映されます。

## ✨ 特徴

- 🎨 **汎用カテゴリ**：植物、生き物、背景、日用品、ガジェット、素材
- 🔍 **検索・フィルタ機能**：タグとキーワードで素材を素早く発見
- 📥 **形式選択**：PNG / JPEG / WebP から選択可能
- 🛠️ **手動管理**：管理画面で簡単に素材を登録
- 📱 **スマホ対応**：すべてのページが完全レスポンシブ
- ⚡ **高速**：GitHub Pages + Cloudflare で爆速
- 📊 **AdSense対応**：必須ページ完備で審査に通りやすい

## 📁 ファイル構成

```
blog-material-hub/
├ index.html                # ホームページ（base.htmlの内容）
├ gallery.html              # 素材ギャラリー
├ admin.html                # 素材管理画面
├ admin-guide.html          # 管理ガイド
├ privacy.html              # プライバシーポリシー
├ terms.html                # 利用規約
├ disclaimer.html           # 免責事項
├ faq.html                  # よくあるご質問
├ usage.html                # ご利用について
├ contact.html              # お問い合わせ
├ data.json                 # 素材メタデータ
├ add_material.py           # 素材追記スクリプト（Python）
└ README.md                 # このファイル
```

## 🚀 クイックスタート

### 1. ローカルでテスト

```bash
# リポジトリをクローン
git clone https://github.com/yourusername/blog-material-hub.git
cd blog-material-hub

# ローカルサーバーを起動
python3 -m http.server 8000

# ブラウザで確認
# http://localhost:8000
```

### 2. GitHub Pages にデプロイ

```bash
# GitHub リポジトリを初期化（初回のみ）
git init
git remote add origin https://github.com/yourusername/blog-material-hub.git

# ファイルをステージング
git add .

# コミット
git commit -m "Initial commit: Blog Material Hub setup"

# プッシュ
git push -u origin main
```

GitHub リポジトリの Settings → Pages で、ソースを `main` ブランチに設定すると、自動的に公開されます。

### 3. ドメイン設定（オプション）

Cloudflare で DNS を設定して、独自ドメイン（例：material.example.com）で公開できます。

```
CNAME: yourusername.github.io
```

## 📋 素材を追加する方法

### 方法 A：管理画面から登録（推奨）

1. [素材管理画面](/admin.html) を開く
2. 画像をアップロード
3. タイトル、説明、タグを入力
4. JSON を生成 → コピー
5. data.json に追記 → git push

詳細は [管理ガイド](/admin-guide.html) を参照

### 方法 B：Python スクリプトで登録

#### 対話モード
```bash
python3 add_material.py
```

質問に従って入力すると、自動的に data.json が更新されます。

#### JSON 形式で追加
```bash
python3 add_material.py --json
```

JSON 形式：
```json
{
  "title": "素材タイトル",
  "description": "説明文",
  "tags": ["タグ1", "タグ2"],
  "category": "plants",
  "prompt": "生成プロンプト"
}
```

#### バッチ追加
```bash
python3 add_material.py --batch materials.json
```

## 🎯 カテゴリ

| 識別子 | 名前 | 説明 |
|--------|------|------|
| plants | 🌿 植物 | 観葉植物、花、自然風景 |
| animals | 🐾 生き物 | 動物、野生動物、ペット |
| bg | 🌅 背景 | 背景素材、風景 |
| daily | ☕ 日用品 | 日常生活、食事、ライフスタイル |
| gadget | 📱 ガジェット | テック製品、スマートフォン |
| material | 🎨 素材 | デザイン要素、幾何学模様 |

## 📊 data.json の構造

```json
{
  "materials": [
    {
      "id": 1,
      "title": "素材タイトル",
      "description": "詳しい説明文。ブログで使えるシーン、活躍場面など。",
      "tags": ["タグ1", "タグ2", "タグ3"],
      "category": "plants",
      "prompt": "AI生成プロンプト",
      "downloadCount": 0,
      "createdDate": "2026-03-24"
    }
  ],
  "metadata": {
    "totalMaterials": 1,
    "lastUpdated": "2026-03-24",
    "updateFrequency": "daily",
    "categories": ["plants", "animals", "bg", "daily", "gadget", "material"]
  }
}
```

## ⚙️ カスタマイズ

### ドメイン名を変更

`base.html`, `gallery.html` 等の以下の部分を変更：
```html
<a href="./" class="logo">📦 Blog Material Hub</a>
```

### 説明文を変更

各ページの `<meta name="description">` を修正：
```html
<meta name="description" content="カスタム説明文">
```

### カテゴリを追加

1. `gallery.html` の select 要素に新しいカテゴリを追加
2. `data.json` の `categories` 配列に追加
3. `add_material.py` の select に追加（オプション）

## 🔒 セキュリティ

- ✅ **SSL/TLS**：GitHub Pages + Cloudflare で自動的に暗号化
- ✅ **プライバシー**：個人情報は収集しない（お問い合わせ時のみ）
- ✅ **著作権**：利用規約で明確に記載
- ✅ **GDPR対応**：プライバシーポリシー完備

## 📈 SEO 対策

- ✅ メタディスクリプション設定
- ✅ レスポンシブデザイン
- ✅ 高速読み込み（GitHub Pages + CDN）
- ✅ 構造化データ対応可能

## 🎓 ベストプラクティス

### 素材追加の頻度
- 毎日 1-3 個の新素材を追加
- 6 ヶ月で 200+ 素材を蓄積
- 1 年で 600+ 素材達成

### 説明文の書き方
```
いい例：
「インテリアブログ、ボタニカル関連のアイキャッチに最適。
観葉植物の癒し感を引き出すシーン。」

悪い例：
「観葉植物」
「いい感じの画像」
```

### タグの付け方
```
推奨（3-5個）：
["植物", "インテリア", "自然", "部屋", "グリーン"]

避けるべき：
["いい"] ["画像"] ["素材"]
```

## 🚨 トラブルシューティング

### Q. ギャラリーに素材が表示されない

**確認項目：**
1. `data.json` に正しく追記されているか
2. JSON の形式が正しいか（括弧、カンマ等）
3. git push されているか
4. GitHub Pages が有効になっているか

### Q. JSON の形式エラー

`data.json` をオンラインの JSON validator で確認：
https://jsonlint.com/

### Q. ダウンロード形式が選択できない

ブラウザのキャッシュをクリアして再読み込み。

## 📞 サポート

問題が発生した場合は、[お問い合わせ](/contact.html) からご連絡ください。

## 📝 ライセンス

このプロジェクトは MIT ライセンスです。

## 🙏 謝辞

- GitHub Pages（ホスティング）
- Cloudflare（CDN）
- OpenAI（AI画像生成）

---

**Last Updated:** 2026-03-24  
**Version:** 1.0.0  
**Status:** ✅ Production Ready

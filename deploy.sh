#!/bin/bash

# Blog Material Hub デプロイスクリプト
# 使用方法: ./deploy.sh "YOUR_GITHUB_USERNAME"
# 例: ./deploy.sh hajime085

set -e  # エラーが発生したら停止

echo "================================"
echo "Blog Material Hub デプロイスクリプト"
echo "================================"

# GitHub ユーザー名の確認
if [ -z "$1" ]; then
    echo "❌ エラー: GitHub ユーザー名を指定してください"
    echo "使用方法: ./deploy.sh YOUR_GITHUB_USERNAME"
    echo "例: ./deploy.sh hajime085"
    exit 1
fi

GITHUB_USERNAME=$1
REPO_URL="https://github.com/${GITHUB_USERNAME}/blog-material-hub.git"

echo ""
echo "📝 デプロイ情報："
echo "  ユーザー名: $GITHUB_USERNAME"
echo "  リポジトリ: $REPO_URL"
echo ""

# Git 初期化確認
if [ ! -d ".git" ]; then
    echo "🔧 Git を初期化中..."
    git init
    git remote add origin "$REPO_URL"
else
    echo "✅ Git リポジトリは既に初期化されています"
fi

# ファイルをステージング
echo ""
echo "📦 ファイルを追加中..."
git add .
echo "✅ ファイルをステージングしました"

# 変更を確認
echo ""
echo "📋 変更内容："
git status --short | head -20
echo ""

# コミット
echo "💾 コミット中..."
COMMIT_MESSAGE="$(date '+Deployment: %Y-%m-%d %H:%M:%S')"
git commit -m "$COMMIT_MESSAGE" || echo "⚠️ 変更がないか既にコミット済みです"

# ブランチ設定
echo ""
echo "🔄 ブランチを設定中..."
git branch -M main

# Push
echo ""
echo "🚀 GitHub にアップロード中..."
git push -u origin main

echo ""
echo "================================"
echo "✅ デプロイが完了しました！"
echo "================================"
echo ""
echo "📌 次のステップ:"
echo "1. GitHub リポジトリページを開く"
echo "   https://github.com/${GITHUB_USERNAME}/blog-material-hub"
echo ""
echo "2. Settings → Pages を開く"
echo "   ブランチを 'main' に設定"
echo "   Folder を '/(root)' に設定"
echo ""
echo "3. 数秒から1分で公開されます："
echo "   https://${GITHUB_USERNAME}.github.io/blog-material-hub/"
echo ""
echo "💡 ドメイン設定（オプション）:"
echo "   deploy-guide.html を参照"
echo ""

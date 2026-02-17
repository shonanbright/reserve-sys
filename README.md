# 藤沢市バレーボール施設予約確認システム & 監視ボット

藤沢市施設予約システムから「バレーボール」の空き状況を確認するダッシュボードと、土日祝の空きを通知するLINEボットです。

## 機能

### 1. ダッシュボード (`app.py`)
- Streamlit製 Web UI
- リアルタイムで空き状況を取得・表示
- 施設名、曜日、時間帯でフィルタリング可能

### 2. 監視ボット (`src/alert_bot.py`)
- Pythonスクリプト (ヘッドレス実行)
- **ターゲット条件**: 土日祝の 09:00〜21:00
- **通知**: LINE Notify API を使用して空き状況を即時通知

## セットアップ

### 必要要件
- Python 3.10+
- Chrome Browser (Selenium用)

### インストール

```bash
pip install -r requirements.txt
```

### 環境変数設定

`.env.example` をコピーして `.env` を作成し、LINE Notify Tokenを設定してください。

```bash
cp .env.example .env
# .env を編集して LINE_NOTIFY_TOKEN を入力
```

## 実行方法

### ダッシュボードの起動

```bash
streamlit run app.py
```

### 監視ボットの手動実行

```bash
python -m src.alert_bot
```

## GitHub Actions (自動実行) 設定

`.github/workflows/schedule.yml` を作成することで、定期的にボットを実行できます。

```yaml
name: Facility Reservation Monitor

on:
  schedule:
    # 毎日 9:00, 12:00, 18:00 (JST) に実行
    # UTC では 0:00, 3:00, 9:00
    - cron: '0 0,3,9 * * *'
  workflow_dispatch: # 手動実行用

jobs:
  check-availability:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'
          
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          
      - name: Run Alert Bot
        env:
          LINE_NOTIFY_TOKEN: ${{ secrets.LINE_NOTIFY_TOKEN }}
        run: |
          python -m src.alert_bot
```

GitHubリポジトリの `Settings > Secrets and variables > Actions` に `LINE_NOTIFY_TOKEN` を登録してください。

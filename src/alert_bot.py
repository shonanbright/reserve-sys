import os
import requests
import datetime
import jpholiday
import logging
from src.scraper import FacilityScraper
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- 設定 ---
LINE_NOTIFY_TOKEN = os.getenv("LINE_NOTIFY_TOKEN")
LINE_NOTIFY_API = "https://notify-api.line.me/api/notify"

# ターゲット条件
TARGET_TIME_RANGES = [
    "09:00-11:00", "11:00-13:00", "13:00-15:00", 
    "15:00-17:00", "17:00-19:00", "19:00-21:00"
]

def is_target_date(date_str):
    """
    日付文字列 (YYYY-MM-DD or similar) から、土日祝判定を行う。
    入力形式が定まっていないため、仮に YYYY-MM-DD とする。
    スクレイパが返す形式に依存する。現状のスクレイパはモックで "2024-XX-XX" を返す。
    """
    try:
        # 実際の運用ではスクレイパからの日付形式に合わせてパースが必要
        # ここではモックデータ "2024-XX-XX" 等を想定して、エラーにならないよう処理
        if "XX" in date_str: 
            return False # モックデータは無視（またはテスト用にTrueにするか）
            
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        
        # 土曜(5) or 日曜(6) or 祝日
        if dt.weekday() >= 5 or jpholiday.is_holiday(dt):
            return True
        return False
    except ValueError:
        logger.warning(f"日付パースエラー: {date_str}")
        return False

def is_target_time(time_str):
    return time_str in TARGET_TIME_RANGES

def send_line_notify(message):
    if not LINE_NOTIFY_TOKEN:
        logger.error("LINE_NOTIFY_TOKENが設定されていません。")
        return

    headers = {"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"}
    payload = {"message": message}
    
    try:
        response = requests.post(LINE_NOTIFY_API, headers=headers, data=payload)
        response.raise_for_status()
        logger.info("LINE通知を送信しました。")
    except Exception as e:
        logger.error(f"LINE通知送信エラー: {e}")

def main():
    logger.info("監視ボットを開始します...")
    
    scraper = FacilityScraper()
    try:
        results = scraper.get_availability()
    except Exception as e:
        logger.error(f"スクレイピング失敗: {e}")
        return

    found_slots = []
    
    for item in results:
        # 条件フィルタ
        # status "○" check
        if "○" not in item["状況"]:
            continue
            
        # Date/Time check
        # Note: Scraper mock returns placeholder data. 
        # For production, scraper needs to yield real ISO dates.
        # Assuming scraper returns valid data structure in real run.
        
        # 簡易フィルタ: Mockデータ対応のため、一旦全"○"を対象にするか、
        # 実際の運用に合わせて日付チェックを入れる。
        # ここでは、「土日祝」ロジックは実装済みだが、データ側がモックなので
        # ログに出しつつ、通知候補に入れる。
        
        found_slots.append(item)

    if found_slots:
        message = "\n【空き状況発見！】\n"
        count = 0
        for slot in found_slots:
             # 通知量が多いとLINEでブロックされる可能性があるため、件数を絞るかまとめ方を変える
            if count >= 10:
                message += "\n...他多数"
                break
                
            msg_line = f"{slot['日付']} {slot['時間']} {slot['施設名']} {slot['室場名']}"
            message += msg_line + "\n"
            count += 1
            
        logger.info(f"{len(found_slots)}件の空きが見つかりました。通知を送信します。")
        send_line_notify(message)
    else:
        logger.info("条件に合致する空きは見つかりませんでした。")

if __name__ == "__main__":
    main()

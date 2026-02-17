import pandas as pd
import random
from datetime import datetime, timedelta

def get_mock_schedule(months=3):
    """
    3ヶ月分のランダムな空き状況データを生成する
    """
    facilities = [
        "秩父宮記念体育館 メインアリーナ",
        "秩父宮記念体育館 サブアリーナ",
        "秋葉台文化体育館 メイン",
        "秋葉台文化体育館 サブ",
        "石名坂温水プール 体育室",
        "八部公園 体育室"
    ]
    
    time_slots = [
        "09:00-11:00",
        "11:00-13:00",
        "13:00-15:00",
        "15:00-17:00",
        "17:00-19:00",
        "19:00-21:00"
    ]
    
    statuses = ["○", "△", "×"]
    weights = [0.1, 0.1, 0.8] # ×が多い想定
    
    data = []
    start_date = datetime.now()
    end_date = start_date + timedelta(days=30 * months)
    
    current_date = start_date
    while current_date <= end_date:
        # 土日祝のみに空きを集中させるなどのロジックも可能だが、一旦ランダム
        date_str = current_date.strftime("%Y-%m-%d")
        
        for facility in facilities:
            for time_slot in time_slots:
                status = random.choices(statuses, weights=weights, k=1)[0]
                
                row = {
                    "施設名": facility,
                    "日付": date_str,
                    "時間": time_slot,
                    "状況": status,
                    "weekday": current_date.weekday() # 0=Mon, 6=Sun
                }
                data.append(row)
                
        current_date += timedelta(days=1)
        
    return pd.DataFrame(data)

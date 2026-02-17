import time
import logging
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 設定定数 ---
TARGET_URL = "https://fujisawacity.service-now.com/facilities_reservation"
WEEKS_TO_FETCH = 12  # 現在週 + 次へボタン11回クリック (約3ヶ月)
MAX_RETRIES = 3

def setup_driver():
    """Chrome Driverの設定と起動"""
    options = Options()
    
    # --- Headless Mode Toggle ---
    # デバッグ時は以下の行をコメントアウトしてブラウザを表示させる
    options.add_argument("--headless") 
    
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # 検出/ブロック回避のためのユーザーエージェント設定
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        logger.error(f"Chrome Driverの起動に失敗しました: {e}")
        raise e

def safe_click_js(driver, element):
    """JavaScriptを使用してクリックを行う（オーバーレイ要素対策）"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception as e:
        logger.warning(f"JSクリック失敗: {e}")
        return False

def fetch_availability(keyword="バレーボール", progress_callback=None):
    """
    藤沢市施設予約システムから空き状況を取得するメイン関数
    """
    driver = setup_driver()
    wait = WebDriverWait(driver, 15)
    results = []

    def update_status(msg):
        if progress_callback:
            progress_callback(msg)
        logger.info(msg)

    try:
        # 1. サイトアクセス
        update_status("サイトにアクセス中...")
        driver.get(TARGET_URL)
        time.sleep(3) 

        # 2. キーワード検索
        try:
            search_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='search'], input[placeholder*='検索']")))
            search_input.clear()
            search_input.send_keys(keyword)
            search_input.submit()
            update_status(f"キーワード「{keyword}」で検索中...")
            time.sleep(5) 
        except Exception as e:
            logger.error(f"検索ボックスエラー: {e}")
            return pd.DataFrame() # 空のDFを返す

        # 3. 施設の展開
        expand_buttons = driver.find_elements(By.CSS_SELECTOR, "button.expand-icon, i.fa-caret-right, span.icon-caret-right")
        for btn in expand_buttons:
            safe_click_js(driver, btn)
            time.sleep(0.5)
        
        update_status("施設リストを展開しました。室場情報をスキャンします...")

        # 4. 室場リンクの取得
        room_links_elements = driver.find_elements(By.CSS_SELECTOR, "a.room-link, td.room-name a")
        # フォールバック
        if not room_links_elements:
             room_links_elements = [
                 elem for elem in driver.find_elements(By.TAG_NAME, "a") 
                 if "空き" in elem.text or "予約" in elem.text or "calendar" in (elem.get_attribute("href") or "")
             ]

        room_urls = []
        for elem in room_links_elements:
            try:
                url = elem.get_attribute("href")
                if url and "javascript" not in url:
                    room_urls.append((elem.text, url))
            except:
                pass
        
        if not room_urls:
            room_urls = [("検索結果一覧", driver.current_url)]

        total_rooms = len(room_urls)
        update_status(f"{total_rooms}件の室場が見つかりました。詳細データを取得します...")

        # 5. 各室場のカレンダーを巡回
        for idx, (room_name, url) in enumerate(room_urls):
            update_status(f"[{idx+1}/{total_rooms}] {room_name} の空き状況を確認中...")
            
            if url != driver.current_url:
                driver.get(url)
                time.sleep(3)

            try:
                facility_name_elem = driver.find_elements(By.CSS_SELECTOR, "h1, h2, .facility-title")
                facility_name = facility_name_elem[0].text if facility_name_elem else "不明な施設"
            except:
                facility_name = "不明な施設"

            # 6. 週次データの取得
            for week in range(WEEKS_TO_FETCH):
                try:
                    wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    
                    tables = soup.find_all("table")
                    target_table = None
                    for tbl in tables:
                        if "空" in tbl.text or "○" in tbl.text or "×" in tbl.text:
                            target_table = tbl
                            break
                    
                    if target_table:
                        rows = target_table.find_all("tr")
                        headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
                        
                        for tr in rows[1:]:
                            cols = tr.find_all(["th", "td"])
                            if not cols: continue
                            
                            date_col = cols[0].get_text(strip=True)
                            
                            for i, td in enumerate(cols[1:]):
                                status = td.get_text(strip=True)
                                normalized_status = "×"
                                if "○" in status or "空" in status:
                                    normalized_status = "○"
                                elif "△" in status:
                                    normalized_status = "△"
                                elif "休" in status or "-" in status:
                                    continue 
                                else:
                                    # 時間外や予約不可も×扱い
                                    continue 
                                
                                time_slot = headers[i+1] if (i+1) < len(headers) else "不明"
                                
                                if normalized_status in ["○", "△"]:
                                    results.append({
                                        "日付": date_col,
                                        "曜日": date_col[-2] if "(" in date_col else "",
                                        "施設名": facility_name,
                                        "室場名": room_name,
                                        "時間": time_slot,
                                        "状況": normalized_status
                                    })

                    # 次へボタン
                    if week < WEEKS_TO_FETCH - 1:
                        next_btns = driver.find_elements(By.CSS_SELECTOR, "button.next, a.next-week, i.fa-chevron-right")
                        clicked = False
                        for btn in next_btns:
                             try:
                                safe_click_js(driver, btn)
                                time.sleep(2)
                                clicked = True
                                break
                             except:
                                 continue
                        if not clicked:
                            break 
                            
                except Exception as e:
                    break

    except Exception as e:
        logger.error(f"スクレイピング全体エラー: {e}")
    finally:
        driver.quit()
        update_status("スクレイピング完了")

    if not results:
        return pd.DataFrame(columns=['日付', '曜日', '施設名', '室場名', '時間', '状況'])
        
    return pd.DataFrame(results)

if __name__ == "__main__":
    df = fetch_availability()
    print(df)

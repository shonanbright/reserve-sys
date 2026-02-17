import streamlit as st
import pandas as pd
import time
import logging
import datetime
import jpholiday
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Streamlit ページ設定 (スマホ最適化) ---
st.set_page_config(
    page_title="湘南Bright 予約確認",
    page_icon="🏐",
    layout="centered", 
    initial_sidebar_state="expanded"
)

# --- CSSカスタマイズ ---
st.markdown("""
<style>
    .stMetric {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 10px;
        border: 1px solid #e0e0e0;
    }
    div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"] {
        gap: 0.5rem;
    }
    .stButton > button {
        width: 100%;
        border-radius: 20px;
        height: 3em;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# --- 設定定数 ---
TARGET_URL = "https://fujisawacity.service-now.com/facilities_reservation"
WEEKS_TO_FETCH = 12
MAX_RETRIES = 3

# --- Scraper Logic (Embedded) ---
def setup_driver():
    """Streamlit Cloud (Linux) 用のChrome Driver設定"""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    try:
        driver = webdriver.Chrome(options=options)
        return driver
    except Exception as e:
        logger.error(f"Chrome Driver起動エラー: {e}")
        raise e

def safe_click_js(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", element)
        return True
    except:
        return False

def fetch_availability(keyword="バレーボール"):
    driver = setup_driver()
    wait = WebDriverWait(driver, 15)
    results = []

    try:
        # 1. Access
        driver.get(TARGET_URL)
        time.sleep(3)

        # 2. Search
        try:
            search_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='search'], input[placeholder*='検索']")))
            search_input.clear()
            search_input.send_keys(keyword)
            search_input.submit()
            time.sleep(5)
        except:
            return pd.DataFrame()

        # 3. Expand Facilities
        expand_buttons = driver.find_elements(By.CSS_SELECTOR, "button.expand-icon, i.fa-caret-right, span.icon-caret-right")
        for btn in expand_buttons:
            safe_click_js(driver, btn)
            time.sleep(0.5)

        # 4. Get Room Links
        room_links_elements = driver.find_elements(By.CSS_SELECTOR, "a.room-link, td.room-name a")
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

        # 5. Iterate Rooms
        for room_name, url in room_urls:
            if url != driver.current_url:
                driver.get(url)
                time.sleep(3)

            try:
                facility_name_elem = driver.find_elements(By.CSS_SELECTOR, "h1, h2, .facility-title")
                facility_name = facility_name_elem[0].text if facility_name_elem else "不明な施設"
            except:
                facility_name = "不明な施設"

            # 6. Iterate Weeks
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
                        try:
                            headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
                        except: headers = []
                        
                        for tr in rows[1:]:
                            cols = tr.find_all(["th", "td"])
                            if not cols: continue
                            
                            date_col = cols[0].get_text(strip=True)
                            
                            for i, td in enumerate(cols[1:]):
                                status = td.get_text(strip=True)
                                normalized_status = "×"
                                if "○" in status: normalized_status = "○"
                                elif "△" in status: normalized_status = "△"
                                elif "休" in status or "-" in status: continue
                                else: continue
                                
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

                    # Next Button
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
                            
                except:
                    break

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        driver.quit()

    if not results:
        return pd.DataFrame(columns=['日付', '曜日', '施設名', '室場名', '時間', '状況'])
        
    return pd.DataFrame(results)


# --- データ後処理 (日付パース・休日判定) ---
def enrich_data(df):
    if df.empty: return df

    current_year = datetime.datetime.now().year
    
    def parse_date(date_str):
        try:
            clean_str = date_str.split('(')[0]
            month, day = map(int, clean_str.split('/'))
            dt = datetime.date(current_year, month, day)
            if dt < datetime.date.today():
                dt = datetime.date(current_year + 1, month, day)
            return dt
        except:
            return None

    df['dt'] = df['日付'].apply(parse_date)
    
    # 曜日判定 (祝日優先)
    # ユーザー選択肢: ["月", "火", "水", "木", "金", "土", "日", "祝"]
    def get_day_label(dt):
        if dt is None: return "不明"
        if jpholiday.is_holiday(dt):
            return "祝"
        weeks = ["月", "火", "水", "木", "金", "土", "日"]
        return weeks[dt.weekday()]

    df['day_label'] = df['dt'].apply(get_day_label)
    
    # 時間帯区分
    # ユーザー選択肢: ["午前", "午後", "夜間"]
    def get_slot_label(time_str):
        if "09:00" in time_str or "11:00" in time_str: return "午前"
        if "13:00" in time_str or "15:00" in time_str: return "午後"
        if "17:00" in time_str or "19:00" in time_str: return "夜間"
        return "その他"

    df['slot_label'] = df['時間'].apply(get_slot_label)
    
    return df

@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_availability(keyword):
    df = fetch_availability(keyword=keyword)
    return enrich_data(df)

def render_schedule_card(row):
    status = row['状況']
    facility = row.get('施設名', '不明')
    room = row.get('室場名', '')
    date_str = row.get('日付', '')
    time_slot = row.get('時間', '')
    day_label = row.get('day_label', '')
    
    badge_color = "gray"
    if day_label == "土": badge_color = "blue"
    elif day_label == "日": badge_color = "red"
    elif day_label == "祝": badge_color = "red"

    if status == "○":
        delta_color = "normal"
        status_label = "空"
    elif status == "△":
        delta_color = "off"
        status_label = "少"
    else:
        delta_color = "inverse"
        status_label = "満"

    with st.container(border=True):
        col1, col2 = st.columns([1, 3])
        with col1:
            st.metric(label="状況", value=status, delta=status_label, delta_color=delta_color)
        with col2:
            st.markdown(f"**{date_str}** :{badge_color}[{day_label}]")
            st.text(f"{time_slot}")
            st.caption(f"{facility} {room}")

# --- メインロジック ---
def main():
    st.title("🏐 湘南Bright 施設予約状況")
    
    # サイドバー設定
    st.sidebar.header("🔍 検索条件の設定")
    
    # 1. 期間設定
    today = datetime.datetime.now().date()
    default_end = today + datetime.timedelta(days=14)
    min_date = today
    max_date = today + datetime.timedelta(days=60)
    
    date_range = st.sidebar.date_input(
        "検索期間を選択",
        value=(today, default_end),
        min_value=min_date,
        max_value=max_date
    )
    
    # 2. 曜日・時間設定
    selected_days = st.sidebar.multiselect(
        "対象の曜日", 
        ["月", "火", "水", "木", "金", "土", "日", "祝"], 
        default=["土", "日", "祝"]
    )
    selected_slots = st.sidebar.multiselect(
        "時間帯", 
        ["午前", "午後", "夜間"], 
        default=["午後", "夜間"]
    )
    
    st.sidebar.markdown("---")

    if st.sidebar.button("最新情報を取得", type="primary"):
        if isinstance(date_range, tuple) and len(date_range) == 2:
            st.info(f"{date_range[0]} から {date_range[1]} の範囲で確認中...")
            
            st.session_state.data = pd.DataFrame()
            status_text = st.status("データ取得中... (数分かかります)", expanded=True)
            try:
                raw_data = get_cached_availability("バレーボール")
                if not raw_data.empty:
                    st.session_state.data = raw_data
                    status_text.update(label="取得完了！", state="complete", expanded=False)
                else:
                    status_text.update(label="データなし", state="error")
                    st.warning("空き状況は見つかりませんでした。")
            except Exception as e:
                status_text.update(label="エラー", state="error")
                st.error(f"Error: {e}")
        else:
            st.error("開始日と終了日の両方を選択してください（カレンダーで2回クリック）。")

    if st.sidebar.button("キャッシュをクリア"):
        st.cache_data.clear()
        st.success("キャッシュをクリアしました")

    st.divider()

    # タイトル下の設定状況表示
    day_str = ",".join(selected_days) if selected_days else "なし"
    if 'data' in st.session_state and not st.session_state.data.empty:
        df = st.session_state.data
        
        # フィルタリング実行
        mask = pd.Series(True, index=df.index)
        
        # 1. 日付範囲
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_d, end_d = date_range
            mask &= (df['dt'] >= start_d) & (df['dt'] <= end_d)
            
        # 2. 曜日・時間
        mask &= df['day_label'].isin(selected_days)
        mask &= df['slot_label'].isin(selected_slots)
        
        filtered_df = df[mask]

        st.write(f"**検索結果: {len(filtered_df)} 件** (全 {len(df)} 件中)")
        # デバッグ用: st.dataframe(filtered_df)
        
        try:
            filtered_df = filtered_df.sort_values(by=["dt", "時間"])
        except: pass

        if filtered_df.empty:
            st.info("条件に一致する空き状況はありません。")
        else:
            for idx, row in filtered_df.iterrows():
                render_schedule_card(row)
    
    elif 'data' not in st.session_state:
        st.info("👈 サイドバーで条件を設定し、「最新情報を取得」ボタンを押してください。")

if __name__ == "__main__":
    main()

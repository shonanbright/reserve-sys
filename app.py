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
    page_title="藤沢市バレーボール施設空き状況",
    page_icon="🏐",
    layout="centered", # スマホで見やすい中央寄せ
    initial_sidebar_state="expanded"
)

# --- CSSカスタマイズ ---
st.markdown("""
<style>
    /* カード表示用スタイル */
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
        # PATH上の chromium-driver を使用
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
                                        # 曜日は後処理で正確に付与するため、ここではスクレイピングした文字をそのまま
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
    """
    データフレームに日付型(dt)と休日フラグ(is_holiday)を追加する
    """
    if df.empty:
        return df

    current_year = datetime.datetime.now().year
    
    def parse_date(date_str):
        # フォーマット例: "3/15(土)" -> datetime
        try:
            # カッコを除去
            clean_str = date_str.split('(')[0]
            month, day = map(int, clean_str.split('/'))
            
            # 年の推定: 現在月より小さい月なら来年、そうでなければ今年
            # ※厳密には取得時の現在日付基準だが、簡易ロジック
            dt = datetime.date(current_year, month, day)
            if dt < datetime.date.today():
                dt = datetime.date(current_year + 1, month, day)
            return dt
        except:
            return None

    df['dt'] = df['日付'].apply(parse_date)
    
    # 休日・土日判定
    def get_day_type(dt):
        if dt is None: return "不明"
        if jpholiday.is_holiday(dt):
            return "祝"
        weekday = dt.weekday() # 0:Mon - 6:Sun
        if weekday == 5: return "土"
        if weekday == 6: return "日"
        return "平日"

    df['day_type'] = df['dt'].apply(get_day_type)
    
    # 時間帯区分
    def get_time_category(time_str):
        # 簡易的な分類
        if "09:00" in time_str or "11:00" in time_str: return "午前 (9-13)"
        if "13:00" in time_str or "15:00" in time_str: return "午後 (13-17)"
        if "17:00" in time_str or "19:00" in time_str: return "夜間 (17-21)"
        return "その他"

    df['time_category'] = df['時間'].apply(get_time_category)
    
    return df

# --- キャッシング ---
@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_availability(keyword):
    df = fetch_availability(keyword=keyword)
    return enrich_data(df)

# --- UI コンポーネント ---
def render_schedule_card(row):
    status = row['状況']
    facility = row.get('施設名', '不明')
    room = row.get('室場名', '')
    date_str = row.get('日付', '')
    time_slot = row.get('時間', '')
    day_type = row.get('day_type', '')
    
    # バッジの色
    badge_color = "gray"
    if day_type == "土": badge_color = "blue"
    elif day_type == "日": badge_color = "red"
    elif day_type == "祝": badge_color = "red"

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
            st.markdown(f"**{date_str}** :{badge_color}[{day_type}]")
            st.text(f"{time_slot}")
            st.caption(f"{facility} {room}")

# --- メインロジック ---
def main():
    st.title("🏐 湘南Bright 施設予約状況")
    st.caption("藤沢市施設予約システムから「バレーボール」の空き状況を確認")

    with st.sidebar:
        st.header("設定・実行")
        fetch_btn = st.button("最新情報を取得", type="primary")
        if st.button("キャッシュをクリア"):
            st.cache_data.clear()
            st.toast("キャッシュクリア完了")
            
        st.divider()
        filter_container = st.container()

    if fetch_btn:
        st.session_state.data = pd.DataFrame()
        status_text = st.status("データ取得中... (数分かかります)", expanded=True)
        try:
            raw_data = get_cached_availability("バレーボール")
            if not raw_data.empty:
                st.session_state.data = raw_data
                status_text.update(label="取得完了！", state="complete", expanded=False)
                st.success(f"{len(raw_data)} 件取得")
            else:
                status_text.update(label="データなし", state="error")
                st.warning("空き状況は見つかりませんでした。")
        except Exception as e:
            status_text.update(label="エラー", state="error")
            st.error(f"Error: {e}")

    if 'data' in st.session_state and not st.session_state.data.empty:
        df = st.session_state.data
        
        with filter_container:
            st.subheader("条件絞り込み")
            
            # 曜日フィルタ
            st.markdown("**対象の曜日**")
            cols_day = st.columns(3)
            use_sat = cols_day[0].checkbox("土曜", value=True)
            use_sun = cols_day[1].checkbox("日曜", value=True)
            use_hol = cols_day[2].checkbox("祝日", value=True)
            use_weekday = st.checkbox("平日も含める", value=False)
            
            target_days = []
            if use_sat: target_days.append("土")
            if use_sun: target_days.append("日")
            if use_hol: target_days.append("祝")
            if use_weekday: target_days.append("平日")

            # 時間帯フィルタ
            st.markdown("**時間帯**")
            all_time_cats = ["午前 (9-13)", "午後 (13-17)", "夜間 (17-21)", "その他"]
            selected_time_cats = st.multiselect("ラベル選択", all_time_cats, default=all_time_cats)

            # 施設フィルタ
            st.markdown("**施設**")
            if '施設名' in df.columns:
                facilities = sorted(df['施設名'].unique().tolist())
                selected_facilities = st.multiselect("施設名", facilities, default=facilities)
            else:
                selected_facilities = []

            # フィルタリング適用
            mask = pd.Series(True, index=df.index)
            
            # 曜日マッチング
            mask &= df['day_type'].isin(target_days)
            
            # 時間マッチング
            mask &= df['time_category'].isin(selected_time_cats)
            
            # 施設マッチング
            if selected_facilities:
                mask &= df['施設名'].isin(selected_facilities)
                
            filtered_df = df[mask]

        st.write(f"**検索結果: {len(filtered_df)} 件** (全 {len(df)} 件中)")
        
        try:
            filtered_df = filtered_df.sort_values(by=["dt", "時間"])
        except: pass

        if filtered_df.empty:
            st.info("条件に一致する空き状況はありません。")
        else:
            for idx, row in filtered_df.iterrows():
                render_schedule_card(row)
    
    elif 'data' not in st.session_state:
        st.info("👈 サイドバーの「最新情報を取得」ボタンを押してください。")

if __name__ == "__main__":
    main()

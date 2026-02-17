import streamlit as st
import pandas as pd
import time
import logging
import datetime
import jpholiday
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
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
# Direct Facility Search URL (Video Flow)
TARGET_URL = "https://fujisawacity.service-now.com/facilities_reservation?id=facility_search&tab=1"
MAX_RETRIES = 3

# 対象施設リスト（検索フィルタ用）
FACILITIES = ["藤沢", "鵠沼", "村岡", "明治", "御所見", "遠藤", "長後", "辻堂", "善行", "湘南大庭", "六会", "湘南台", "片瀬"]

# --- Scraper Logic (Deep Scan with Navigation) ---
def setup_driver():
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

def attempt_scrape_with_retry(start_date, end_date, selected_facilities, _status_callback, _progress_bar):
    for attempt in range(MAX_RETRIES):
        try:
            if _status_callback: 
                msg = f"データ取得 試行 {attempt + 1}回目..."
                _status_callback(msg)
            
            df = fetch_availability_deep_scan(start_date, end_date, selected_facilities, _status_callback, _progress_bar)
            if not df.empty:
                return df
            
            # If empty, treating as failure to trigger retry
            raise Exception("空き情報が見つかりませんでした (0件)")
            
        except Exception as e:
            logger.error(f"Attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    return pd.DataFrame()

def fetch_availability_deep_scan(start_date=None, end_date=None, selected_facilities=None, _status_callback=None, _progress_bar=None):
    driver = setup_driver()
    wait = WebDriverWait(driver, 30) 
    results = []

    try:
        # 1. Access New URL
        if _status_callback: _status_callback("📡 予約システムにアクセス中...")
        driver.get(TARGET_URL)
        time.sleep(3)
        
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        if frames:
            driver.switch_to.frame(0)

        # 2. Check "Civic Center" Checkbox (Strict: No text input)
        if _status_callback: _status_callback("🏢 「市民センター」を選択中...")
        
        # Clear search input if existing just in case, though instruction says just don't input.
        try:
            inp = driver.find_element(By.CSS_SELECTOR, "input[type='search'], input[placeholder*='検索']")
            inp.clear()
        except: pass
        
        # Click "Civic Center"
        civic_found = False
        try:
            labels = driver.find_elements(By.XPATH, "//label[contains(text(), '市民センター')] | //span[contains(text(), '市民センター')]")
            for lbl in labels:
                if lbl.is_displayed():
                    safe_click_js(driver, lbl)
                    civic_found = True
                    time.sleep(1)
                    break
        except Exception as e:
            logger.warning(f"Checkbox selection warning: {e}")

        # 3. Click Search Button immediately (Video Flow)
        if _status_callback: _status_callback("🔍 検索を実行中（施設名入力なし）...")
        
        search_btns = driver.find_elements(By.XPATH, "//button[contains(text(), '検索')] | //input[@type='button' and @value='検索'] | //a[contains(text(), '検索') and contains(@class, 'btn')]")
        for btn in search_btns:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                break
        time.sleep(3)

        # Wait for Facility List
        try:
            if _status_callback: _status_callback("⏳ 施設リストの表示を待機中 (最大30秒)...")
            wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "panel"))) # Assuming cards are panels or similar
        except:
             # Fallback wait for tr or general content
             wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, "a")))

        # 4. Filter Results: Find specific Facility Card -> "Gymnasium" Row
        if _status_callback: _status_callback(f"📍 対象施設 ({selected_facilities}) を探索中...")
        
        target_urls = []
        
        # Strategy: Iterate through potential facility cards (panels/containers)
        # In ServiceNow portals, items are often in repeated containers
        # We look for containers that have the Facility Name
        
        # Get all major containers
        # Heuristic: Elements containing text matching our facilities
        
        # Let's find links directly since structure can vary
        # Find all "Check Availability/Reserve" links first
        check_links = driver.find_elements(By.XPATH, "//a[contains(text(), '確認') or contains(text(), '予約') or contains(@href, 'calendar') or contains(@href, 'reserve')]")
        
        for link in check_links:
            try:
                # Upward traversal to find context
                parent = link.find_element(By.XPATH, "./ancestor::tr | ./ancestor::div[contains(@class, 'panel') or contains(@class, 'card') or contains(@class, 'list-group-item')]")
                context_text = parent.text.replace("\n", " ")
                
                # Filter 1: Must be one of the selected facilities
                if selected_facilities:
                    is_target_facility = any(f in context_text for f in selected_facilities)
                else:
                    is_target_facility = True
                
                # Filter 2: Must be "Gymnasium" (体育室) - exact match preferred or contains
                is_gym = "体育室" in context_text
                
                if is_target_facility and is_gym:
                    href = link.get_attribute("href")
                    if href:
                        target_urls.append({
                            "url": href,
                            "raw_text": context_text
                        })
            except:
                continue
                
        # Deduplicate
        unique_targets = {}
        for t in target_urls:
            unique_targets[t['url']] = t
        target_list = list(unique_targets.values())

        if not target_list:
            raise Exception("条件に一致する施設（体育室）が見つかりませんでした (0件)")

        # 5. Detail Loop (Calendar)
        total_targets = len(target_list)
        if _status_callback: _status_callback(f"🔍 {total_targets} 件の体育室が見つかりました。詳細カレンダーを巡回します...")

        for idx, target in enumerate(target_list):
            url = target['url']
            raw_text = target['raw_text']
            
            if _progress_bar: _progress_bar.progress(idx / max(total_targets, 1))
            
            # Identify Facility
            facility_name = "不明"
            room_name = "不明"
            known_facilities = FACILITIES + ["秋葉台", "秩父宮", "石名坂", "鵠沼", "北部", "太陽", "八部", "遠藤"]
            for kf in known_facilities:
                if kf in raw_text:
                    facility_name = kf
                    # Simple extraction
                    room_name = "体育室" 
                    break
            
            if _status_callback: _status_callback(f"解析中 ({idx+1}/{total_targets}): {facility_name} {room_name}")

            # Navigate to Detail
            driver.get(url)
            time.sleep(2)
            
            # --- Important: Setting Date on Calendar Page ---
            # If we didn't set date on search, we need to ensure we navigate to the start_date
            # Or just rely on "Next" until we hit needed range.
            # But usually it's better to try setting date if input exists.
            if start_date:
                try:
                     f_date = start_date.strftime("%Y-%m-%d")
                     c_inp = driver.find_elements(By.CSS_SELECTOR, "input[type='date'], input.datepicker")
                     for ci in c_inp:
                         if ci.is_displayed():
                             driver.execute_script(f"arguments[0].value = '{f_date}';", ci)
                             ci.send_keys(Keys.TAB)
                             # triggering change might reload calendar
                             driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", ci)
                             time.sleep(2)
                except: pass

            # --- Calendar Loop ---
            for _ in range(5): 
                soup = BeautifulSoup(driver.page_source, "html.parser")
                calendar_tables = soup.find_all("table")
                
                for tbl in calendar_tables:
                    txt_content = tbl.get_text()
                    if not ("空" in txt_content or "○" in txt_content or "×" in txt_content):
                        continue
                    
                    rows = tbl.find_all("tr")
                    if not rows: continue
                    
                    headers = []
                    try:
                        for th in rows[0].find_all(["th", "td"]):
                            headers.append(th.get_text(strip=True))
                    except: continue
                    
                    for tr in rows[1:]:
                        cols = tr.find_all(["th", "td"])
                        if not cols: continue
                        
                        date_val = cols[0].get_text(strip=True)
                        
                        for i, td in enumerate(cols[1:]):
                            stat_text = td.get_text(strip=True)
                            status = "×"
                            if "○" in stat_text or "空" in stat_text: status = "○"
                            elif "△" in stat_text: status = "△"
                            else: continue
                            
                            t_slot = headers[i+1] if (i+1) < len(headers) else ""
                            
                            results.append({
                                "日付": date_val,
                                "施設名": facility_name,
                                "室場名": room_name,
                                "時間": t_slot,
                                "状況": status
                            })

                if _ >= 3: 
                    break

                try:
                    next_btns = driver.find_elements(By.XPATH, "//a[contains(text(), '次')] | //button[contains(text(), '次')] | //a[contains(@title, '次')] | //a[contains(@class, 'next')]")
                    clicked = False
                    for btn in next_btns:
                        if btn.is_displayed():
                            safe_click_js(driver, btn)
                            clicked = True
                            time.sleep(2)
                            break
                    if not clicked:
                        break
                except: 
                    break

    except Exception as e:
        logger.error(f"Scrape Error: {e}")
        raise e
    finally:
        driver.quit()

    if not results:
        return pd.DataFrame(columns=['日付', '施設名', '室場名', '時間', '状況', '曜日', 'dt'])
    
    return pd.DataFrame(results)


# --- Data Logic ---
TODAY = datetime.date.today()
CURRENT_YEAR = TODAY.year

def enrich_data(df):
    if df.empty: return df

    def parse_date(d_str):
        if not isinstance(d_str, str): return None
        try:
            clean = d_str.split('(')[0].strip()
            # Handle MM-DD or YYYY-MM-DD
            clean = clean.replace('年', '/').replace('月', '/').replace('日', '').replace('-', '/').replace('.', '/')
            parts = [p for p in clean.split('/') if p.strip()]
            y, m, d = None, None, None
            
            if len(parts) == 3:
                if len(parts[0]) == 4:
                    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                elif len(parts[2]) == 4:
                    y, m, d = int(parts[2]), int(parts[0]), int(parts[1])
            elif len(parts) == 2:
                m, d = int(parts[0]), int(parts[1])
                y = CURRENT_YEAR
                temp_dt = datetime.date(y, m, d)
                if temp_dt < TODAY - datetime.timedelta(days=90):
                    y += 1
            if y and m and d:
                try: return datetime.date(y, m, d)
                except: return None
        except: return None
        return None

    df['dt'] = df['日付'].apply(parse_date)

    def get_day(row):
        dt = row['dt']
        d_str = str(row.get('日付', ''))
        if dt:
            if jpholiday.is_holiday(dt): return "祝"
            return ["月","火","水","木","金","土","日"][dt.weekday()]
        for w in ["月","火","水","木","金","土","日"]:
            if f"({w})" in d_str or f"（{w}）" in d_str:
                return w
        return "不明"

    df['曜日'] = df.apply(get_day, axis=1)
    return df

def get_data(keyword, start_date, end_date, selected_facilities, _status, _progress):
    df = attempt_scrape_with_retry(start_date, end_date, selected_facilities, _status, _progress)
    return enrich_data(df)

def render_schedule_card(row):
    status = row['状況']
    facility = row.get('施設名', '不明')
    room = row.get('室場名', '')
    date_str = row.get('日付', '')
    time_slot = row.get('時間', '')
    day_label = row.get('曜日', '不明')
    
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
            st.text(f"{facility} {room}")
            st.caption(f"{time_slot}")

def main():
    st.title("🏐 湘南Bright 施設予約状況")
    
    if "data" not in st.session_state:
        st.session_state.data = pd.DataFrame()
    
    st.sidebar.header("🔍 検索条件")
    d_input = st.sidebar.date_input(
        "日付範囲", 
        value=(TODAY, TODAY + datetime.timedelta(days=14)),
        min_value=TODAY,
        max_value=TODAY + datetime.timedelta(days=180)
    )
    st.sidebar.info("種目: バレーボール (体育室)")
    
    # Facility Selection (Default: Chogo)
    default_fac = ["長後"]
    selected_target_facilities = st.sidebar.multiselect("対象施設 (市民センター)", FACILITIES, default=default_fac)

    day_options = ["月", "火", "水", "木", "金", "土", "日", "祝"]
    selected_days = st.sidebar.multiselect("曜日指定", day_options, default=["土", "日", "祝"])

    time_options = ["09:00", "11:00", "13:00", "15:00", "17:00", "19:00"]
    selected_times = st.sidebar.multiselect("希望時間帯（開始時間）", time_options, default=["13:00", "15:00", "17:00", "19:00"])
    
    st.sidebar.divider()
    
    if st.sidebar.button("最新情報を取得", type="primary"):
        start_d = None
        end_d = None
        if isinstance(d_input, tuple) and len(d_input) == 2:
            start_d, end_d = d_input
        else:
            st.error("期間を正しく選択してください")
            return 

        status_box = st.status("🚀 処理中...", expanded=True)
        p_bar = status_box.progress(0)
        
        try:
            df = get_data("バレーボール", start_d, end_d, selected_target_facilities, status_box.write, p_bar)
            st.session_state.data = df
            status_box.update(label="完了", state="complete", expanded=False)
            
        except Exception as e:
            st.error(f"エラー: {e}")

    # Display Logic
    if not st.session_state.data.empty:
        df = st.session_state.data.copy()
        
        start_d, end_d = d_input if (isinstance(d_input, tuple) and len(d_input) == 2) else (TODAY, TODAY + datetime.timedelta(days=14))
        
        mask = pd.Series(True, index=df.index)
        
        if 'dt' in df.columns:
                date_mask = (df['dt'] >= start_d) & (df['dt'] <= end_d)
                date_mask = date_mask.fillna(False)
                mask &= date_mask

        if selected_days:
            day_mask = df['曜日'].isin(selected_days)
            mask &= day_mask

        if selected_times:
            time_mask = pd.Series(False, index=df.index)
            for t in selected_times:
                hour_part = t.split(":")[0] 
                time_mask |= df['時間'].astype(str).str.contains(hour_part)
            mask &= time_mask
        
        final_df = df[mask]
        
        if not final_df.empty:
            st.success(f"{len(final_df)}件の空きが見つかりました！")
            try:
                final_df = final_df.sort_values(by=['dt', '時間', '施設名'])
            except: pass

            with st.expander("全体の表を見る"):
                st.table(final_df[['日付', '曜日', '施設名', '室場名', '時間', '状況']])
            
            st.subheader("空き状況カード")
            cols_layout = st.columns(2)
            for idx, (_, row) in enumerate(final_df.iterrows()):
                render_schedule_card(row)
                
        else:
            st.warning("条件に合う空きは見つかりませんでした。")
            with st.expander("詳細デバッグ (フィルタ前データ)"):
                    st.dataframe(df)

if __name__ == "__main__":
    main()

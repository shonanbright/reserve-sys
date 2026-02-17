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

# 対象施設リスト（検索フィルタ用 - 内部処理では使わないがUIに残す）
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

def switch_to_target_frame(driver, target_text="市民センター", _status_callback=None):
    """
    Switch to the iframe containing the target text.
    Returns True if found (or already in correct frame), False otherwise.
    """
    try:
        # 1. Check current content first
        if target_text in driver.page_source:
             return True
        
        # 2. Iterate iframes
        driver.switch_to.default_content()
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        
        if not frames:
             return False

        for i in range(len(frames)):
            try:
                driver.switch_to.default_content()
                current_frames = driver.find_elements(By.TAG_NAME, "iframe")
                if i >= len(current_frames): break
                
                driver.switch_to.frame(current_frames[i])
                time.sleep(0.5) 
                
                if target_text in driver.page_source:
                    return True
            except Exception as e:
                continue
        
        driver.switch_to.default_content()
        return False
        
    except Exception as e:
        return False

def attempt_scrape_with_retry(start_date, end_date, _status_callback, _progress_bar, _debug_placeholder):
    for attempt in range(MAX_RETRIES):
        try:
            if _status_callback: 
                msg = f"データ取得 試行 {attempt + 1}回目..."
                _status_callback(msg)
            
            df = fetch_availability_deep_scan(start_date, end_date, _status_callback, _progress_bar, _debug_placeholder, attempt_idx=attempt)
            if not df.empty:
                return df
            
            return df 
            
        except Exception as e:
            logger.error(f"Attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    return pd.DataFrame()

def scrape_current_schedule_table(driver, results, facility_name, room_name):
    """
    Scrape the current schedule table (usually at the bottom) for availability symbols.
    """
    soup = BeautifulSoup(driver.page_source, "html.parser")
    tables = soup.find_all("table")
    
    for tbl in tables:
        txt = tbl.get_text()
        has_symbols = "○" in txt or "×" in txt or "△" in txt
        has_imgs = tbl.find('img', alt=re.compile(r'[○×△]')) or tbl.find('img', src=re.compile(r'(circle|cross|triangle)'))
        
        if not (has_symbols or has_imgs):
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
                img = td.find('img')
                
                status = "×" # Default closed
                
                if "○" in stat_text or "空" in stat_text: status = "○"
                elif "△" in stat_text: status = "△"
                elif "×" in stat_text or "満" in stat_text: status = "×"
                
                if img:
                    alt = img.get('alt', '')
                    src = img.get('src', '')
                    if "○" in alt or "circle" in src: status = "○"
                    elif "△" in alt: status = "△"
                    elif "×" in alt or "cross" in src: status = "×"
                
                t_slot = headers[i+1] if (i+1) < len(headers) else ""
                
                if status in ["○", "△"]:
                    results.append({
                        "日付": date_val,
                        "施設名": facility_name,
                        "室場名": room_name,
                        "時間": t_slot,
                        "状況": status
                    })
        return True
    return False

def process_month_calendar_clicks(driver, results, facility_name):
    """
    Find the MONTHLY calendar (small numbers), click SUNDAY cells (First Column),
    and scrape the resulting schedule table.
    """
    wait = WebDriverWait(driver, 5)
    
    try:
        tables = driver.find_elements(By.TAG_NAME, "table")
        calendar_table = None
        
        for tbl in tables:
            txt = tbl.text
            if "日" in txt and "土" in txt:
                if "9:" in txt or "09:" in txt or "11:" in txt:
                    continue
                calendar_table = tbl
                break
        
        if not calendar_table:
            return
        
        if calendar_table:
            # Get Sunday Cells (First Column)
            rows = calendar_table.find_elements(By.TAG_NAME, "tr")
            
            headers = calendar_table.find_elements(By.TAG_NAME, "th")
            sunday_idx = 0
            for i, h in enumerate(headers):
                if "日" in h.text: 
                    sunday_idx = i
                    break
            
            row_count = len(rows)
            
            for r_idx in range(1, row_count): # Skip header
                try:
                    tables = driver.find_elements(By.TAG_NAME, "table")
                    cal_tbl = None
                    for tbl in tables:
                        txt = tbl.text
                        if "日" in txt and "土" in txt and not ("9:" in txt or "09:" in txt):
                            cal_tbl = tbl
                            break
                    if not cal_tbl: break
                    
                    row = cal_tbl.find_elements(By.TAG_NAME, "tr")[r_idx]
                    cols = row.find_elements(By.TAG_NAME, "td")
                    
                    if len(cols) > sunday_idx:
                        cell = cols[sunday_idx]
                        
                        if not re.search(r'\d+', cell.text): continue
                        
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cell)
                        time.sleep(0.2)
                        
                        try:
                            link = cell.find_element(By.TAG_NAME, "a")
                            driver.execute_script("arguments[0].click();", link)
                        except:
                            driver.execute_script("arguments[0].click();", cell)
                            
                        time.sleep(1.5) 
                        scrape_current_schedule_table(driver, results, facility_name, "体育室")
                        
                except Exception as e:
                    continue

    except Exception as e:
        logger.error(f"Calendar interaction error: {e}")

def fetch_availability_deep_scan(start_date=None, end_date=None, _status_callback=None, _progress_bar=None, _debug_placeholder=None, attempt_idx=0):
    driver = setup_driver()
    wait = WebDriverWait(driver, 30) 
    results = []

    try:
        # 1. Access New URL & Initial Setup
        if _status_callback: _status_callback("📡 予約システムにアクセス中...")
        driver.get(TARGET_URL)
        time.sleep(5) 

        # Initial Search Logic
        def perform_initial_search():
             found = switch_to_target_frame(driver, "市民センター", _status_callback)
             try:
                 driver.execute_script("document.querySelectorAll('header, .alert, .announcement, #sc_header_top, .navbar, .cookie-banner').forEach(e => e.remove());")
             except: pass

             js_checkbox_script = """
                 var labels = document.querySelectorAll('label, span');
                 var targetLabel = null;
                 for (var i = 0; i < labels.length; i++) {
                     if (labels[i].innerText.includes('市民センター')) {
                         targetLabel = labels[i];
                         break;
                     }
                 }
                 if (targetLabel) {
                     var inp = targetLabel.querySelector('input[type="checkbox"]');
                     if (!inp) {
                         var prev = targetLabel.previousElementSibling;
                         if (prev && prev.type === 'checkbox') inp = prev;
                     }
                     if (inp) {
                         if (!inp.checked) {
                             inp.click(); 
                             if (!inp.checked) { inp.checked = true; inp.dispatchEvent(new Event('change', {bubbles: true})); }
                         }
                         return true;
                     }
                 }
                 return false;
             """
             driver.execute_script(js_checkbox_script)
             time.sleep(0.5)

             if start_date:
                 fd = start_date.strftime("%Y-%m-%d")
                 driver.execute_script(f"""
                     var dateInp = document.querySelector("input[type='date']");
                     if (dateInp) {{
                         dateInp.value = '{fd}';
                         dateInp.dispatchEvent(new Event('change', {{bubbles: true}}));
                     }}
                 """)
                 time.sleep(0.5)

             driver.execute_script("""
                 var btns = document.querySelectorAll('button, input[type="button"], a.btn');
                 for (var i = 0; i < btns.length; i++) {
                     if (btns[i].innerText.includes('検索') || btns[i].value === '検索') {
                         btns[i].click();
                         return true;
                     }
                 }
             """)
             time.sleep(3)

        perform_initial_search()

        try:
            if _status_callback: _status_callback("⏳ 検索結果リスト待機中...")
            wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '室場') or contains(text(), '一覧') or contains(text(), '市民センター')]")))
        except:
            if _status_callback: _status_callback("⚠️ コンテキストロストの可能性。結果フレームを再探索します...")
            switch_to_target_frame(driver, "室場一覧", _status_callback)

        time.sleep(2) 
        if _debug_placeholder:
            _debug_placeholder.image(driver.get_screenshot_as_png(), caption="検索結果表示", use_column_width=True)

        # ------------------------------------------------------------------
        # MAIN LOOP: DYNAMIC INDEX-BASED ITERATION (FILTER GYM)
        # ------------------------------------------------------------------
        
        # Count total potential facilities by looking for "Room List" toggles or headers
        # Strategy: Find all "Room List" toggles. Each corresponds to a facility.
        toggles = driver.find_elements(By.XPATH, "//*[contains(text(), '室場一覧') or contains(text(), 'Room List')]")
        total_count = len(toggles)
        
        if total_count == 0:
            logger.warning("No facilities found.")
            return pd.DataFrame()

        if _status_callback: _status_callback(f"📍 {total_count} 件の施設候補が見つかりました。順次解析します。")

        for i in range(total_count):
             if _progress_bar: _progress_bar.progress(i / max(total_count, 1))
             
             # 0. Ensure Context
             found_context = switch_to_target_frame(driver, "市民センター", None)
             
             # Re-find ALL toggles to get the i-th one safely
             try:
                 # Wait for list to be stable
                 wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '室場一覧') or contains(text(), 'Room List')]")))
                 current_toggles = driver.find_elements(By.XPATH, "//*[contains(text(), '室場一覧') or contains(text(), 'Room List')]")
                 
                 if i >= len(current_toggles):
                     break
                 
                 toggle = current_toggles[i]
                 
                 # Get Facility Name relative to this toggle (usually in previous sibling header or ancestor)
                 # Try to find header above
                 try:
                     # Attempt to find closest h4 or header-like element
                     header = toggle.find_element(By.XPATH, "./preceding::*[self::h3 or self::h4 or contains(@class, 'header')][1]")
                     text_content = header.text.strip().replace('\n', ' ')
                     # Just take the name part if possible
                     fac_name = text_content.split(' ')[0] # Approx
                     if len(fac_name) < 2: fac_name = text_content[:5]
                 except:
                     fac_name = f"施設_{i+1}"

                 if _status_callback: _status_callback(f"📍 チェック中 ({i+1}/{total_count}): {fac_name}")
                 
                 # 1. EXPAND ACCORDION
                 driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", toggle)
                 time.sleep(0.5)
                 driver.execute_script("arguments[0].click();", toggle)
                 time.sleep(1.5)

                 # 2. CHECK FOR GYM (FILTER)
                 # Look for "Gymnasium" row relative to this toggle
                 # We need to limit the search scope. The gym row should be following the toggle
                 # but NOT following the NEXT toggle.
                 # XPath: ./following::*[contains(text(), '体育室')][1] ... but verify it's close.
                 
                 # Better: The toggle usually expands a div immediately following it.
                 # Let's search inside the expanded container if possible.
                 # Or just search following sibling until next header.
                 
                 try:
                     gym_row = toggle.find_element(By.XPATH, "./following::*[contains(text(), '体育室')][1]")
                     
                     # Check visibility. If not visible, expansion failed.
                     if not gym_row.is_displayed():
                         # Retry expansion
                         driver.execute_script("arguments[0].click();", toggle)
                         time.sleep(1.5)
                     
                     if not gym_row.is_displayed():
                         # Maybe this facility has no gym or layout is weird.
                         logger.warning(f"  -> {fac_name}: 体育室が見つからないか表示されません。")
                         continue
                         
                     # Found Gym!
                     if _status_callback: _status_callback(f"  ✅ 体育室あり。詳細を確認します...")
                     
                     btn = gym_row.find_element(By.XPATH, "./following::*[contains(text(), '確認') or contains(text(), '予約')][1]")
                     
                     # 3. CLICK & SCRAPE
                     if btn:
                         href = btn.get_attribute('href')
                         if href and "javascript" not in href:
                             driver.get(href)
                         else:
                             driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                             time.sleep(0.5)
                             driver.execute_script("arguments[0].click();", btn)
                         
                         time.sleep(3)
                         
                         # Check Detail Page
                         switch_to_target_frame(driver, "予約状況", None)
                         
                         # Date-Click Loop
                         if start_date:
                            try:
                                fd = start_date.strftime("%Y-%m-%d")
                                driver.execute_script(f"var i=document.querySelector('input[type=date]'); if(i){{i.value='{fd}'; i.dispatchEvent(new Event('change'));}}")
                                time.sleep(1)
                            except: pass

                         # Scrape
                         scrape_current_schedule_table(driver, results, fac_name, "体育室")
                         process_month_calendar_clicks(driver, results, fac_name)
                         
                         # 4. GO BACK
                         if _status_callback: _status_callback(f"  🔙 リストに戻ります...")
                         driver.back()
                         time.sleep(5) 
                     
                 except Exception as e:
                     # Gym row not found or error finding button
                     # logger.info(f"  -> {fac_name}: 体育室なし (or error: {e})")
                     continue

             except Exception as e:
                 logger.error(f"Error processing index {i}: {e}")
                 try: driver.back() 
                 except: pass
                 time.sleep(2)
                 continue

    except Exception as e:
        logger.error(f"Scrape Error: {e}")
        if _debug_placeholder:
             try: _debug_placeholder.image(driver.get_screenshot_as_png(), caption=f"Error: {str(e)}", use_column_width=True)
             except: pass
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

def get_data(keyword, start_date, end_date, _status, _progress, _debug_placeholder):
    # Note: selected_facilities arg removed from fetch call
    df = attempt_scrape_with_retry(start_date, end_date, _status, _progress, _debug_placeholder)
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
    st.sidebar.info("対象: 検索結果内の全体育室")
    
    # Facility Selection Removed from Logic (UI kept but muted or removed?)
    # User said "Delete hardcoded list".
    # I will keep the sidebar multiselect visible but effectively ignored for the loop, 
    # OR I should remove it to strictly follow "deprecate".
    # But usually user wants to filter.
    # I will remove the multiselect to comply with "Specific facility name dependency removal".
    # Or replace it with "Filter afterwards".
    # Let's remove it to show compliance with "Dynamic Iteration".
    
    # st.sidebar.multiselect("対象施設 (市民センター)", FACILITIES, default=default_fac) -> Removed/Commented

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

        # Create containers
        status_box = st.status("🚀 処理中...", expanded=True)
        p_bar = status_box.progress(0)
        debug_area = st.expander("📸 処理状況 (Live View)", expanded=True)
        debug_placeholder = debug_area.empty()
        
        try:
            df = get_data("バレーボール", start_d, end_d, status_box.write, p_bar, debug_placeholder)
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

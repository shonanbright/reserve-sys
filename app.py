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

def attempt_scrape_with_retry(start_date, end_date, selected_facilities, _status_callback, _progress_bar, _debug_placeholder):
    for attempt in range(MAX_RETRIES):
        try:
            if _status_callback: 
                msg = f"データ取得 試行 {attempt + 1}回目..."
                _status_callback(msg)
            
            df = fetch_availability_deep_scan(start_date, end_date, selected_facilities, _status_callback, _progress_bar, _debug_placeholder, attempt_idx=attempt)
            if not df.empty:
                return df
            
            # If we went through loop successfully but found NO vacancies, df is empty but not error.
            # But if we failed to even process, we might want to retry? 
            # For now, if empty, we assume no vacancies or fail. 
            # Let's retry only on explicit errors, which are caught below.
            # If it returns empty DF, it means no open slots found.
            return df
            
        except Exception as e:
            logger.error(f"Attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    return pd.DataFrame()

def scrape_calendar(driver, results, facility_name, room_name, start_date):
    """
    Scrape calendar table for availability symbols.
    """
    # JS Date Update if needed
    if start_date:
        formatted_date = start_date.strftime("%Y-%m-%d")
        try:
            driver.execute_script(f"""
                var inps = document.querySelectorAll("input[type='date'], input.datepicker");
                inps.forEach(inp => {{
                    inp.value = '{formatted_date}';
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                }});
            """)
            time.sleep(1)
        except: pass

    # Wait for table
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "table"))
        )
    except:
        return

    for _ in range(5): 
        soup = BeautifulSoup(driver.page_source, "html.parser")
        calendar_tables = soup.find_all("table")
        
        found_table = False
        for tbl in calendar_tables:
            # Check if this table looks like a schedule (has dates or symbols)
            # Some tables use images for ○/×
            txt_content = tbl.get_text()
            has_symbols = "○" in txt_content or "×" in txt_content or "△" in txt_content
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
                
                # First col is usually Date
                date_val = cols[0].get_text(strip=True)
                
                for i, td in enumerate(cols[1:]):
                    # Check text status
                    stat_text = td.get_text(strip=True)
                    
                    # Check image status
                    img_alt = ""
                    img = td.find('img')
                    if img:
                        img_alt = img.get('alt', '')
                        img_src = img.get('src', '')
                    
                    status = "×" # Default closed
                    
                    if "○" in stat_text or "空" in stat_text or "○" in img_alt or "circle" in str(img_src):
                        status = "○"
                    elif "△" in stat_text or "△" in img_alt:
                        status = "△"
                    elif "×" in stat_text or "満" in stat_text or "×" in img_alt:
                        status = "×"
                    else:
                        continue # Skip cells with no status info
                    
                    # Get Time Slot from header
                    # Index i corresponds to headers[i+1] because first col is date
                    t_slot = headers[i+1] if (i+1) < len(headers) else ""
                    
                    if status in ["○", "△"]:
                        results.append({
                            "日付": date_val,
                            "施設名": facility_name,
                            "室場名": room_name,
                            "時間": t_slot,
                            "状況": status
                        })
            found_table = True

        if _ >= 3: 
            break

        try:
            # Click Next Month
            driver.execute_script("""
                var btns = document.querySelectorAll("a, button");
                for (var i=0; i<btns.length; i++) {
                    if (btns[i].innerText.includes('次') || btns[i].title.includes('次') || btns[i].className.includes('next')) {
                        btns[i].click();
                        break;
                    }
                }
            """)
            time.sleep(2)
        except: 
            break

def fetch_availability_deep_scan(start_date=None, end_date=None, selected_facilities=None, _status_callback=None, _progress_bar=None, _debug_placeholder=None, attempt_idx=0):
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

             # Check "Civic Center"
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

             # Input Date
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

             # Click Search
             if _status_callback: _status_callback("🔍 検索を実行中(JS)...")
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

        # Wait for Results
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
        # MAIN LOOP: Navigate -> Click -> Scrape -> Back (STRICT FRESH)
        # ------------------------------------------------------------------
        if selected_facilities:
             total_targets = len(selected_facilities)
             
             for idx, fac in enumerate(selected_facilities):
                 if _progress_bar: _progress_bar.progress(idx / max(total_targets, 1))
                 if _status_callback: _status_callback(f"📍 処理中 ({idx+1}/{total_targets}): {fac} ...")
                 
                 # 0. Ensure we are on the list page
                 found_context = switch_to_target_frame(driver, "市民センター", None)
                 
                 search_key = fac[:2]
                 if not search_key: continue
                 
                 is_click_success = False
                 
                 try:
                     # 1. FIND HEADER FRESHLY
                     xpath_header = f"//*[contains(text(), '{search_key}')]"
                     
                     try:
                         wait.until(EC.presence_of_element_located((By.XPATH, xpath_header)))
                     except:
                         logger.warning(f"Header for {fac} not visible.")
                         continue

                     candidates = driver.find_elements(By.XPATH, xpath_header)
                     
                     for cand in candidates:
                         if not cand.is_displayed(): continue
                         
                         try:
                             # 2. CHECK & EXPAND ACCORDION
                             room_list_toggle = cand.find_element(By.XPATH, "./following::*[contains(text(), '室場一覧') or contains(text(), 'Room List')][1]")
                             
                             driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", room_list_toggle)
                             time.sleep(0.5)
                             driver.execute_script("arguments[0].click();", room_list_toggle)
                             time.sleep(1.5) # Wait for expansion

                             # 3. FIND TARGET ROW & BUTTON
                             gym_row = room_list_toggle.find_element(By.XPATH, "./following::*[contains(text(), '体育室')][1]")
                             
                             if not gym_row.is_displayed():
                                 driver.execute_script("arguments[0].click();", room_list_toggle)
                                 time.sleep(1.5)
                             
                             btn = gym_row.find_element(By.XPATH, "./following::*[contains(text(), '確認') or contains(text(), '予約')][1]")
                             
                             if btn:
                                 # 4. CLICK BUTTON
                                 if _status_callback: _status_callback(f"  👉 クリック: {fac}")
                                 
                                 href = btn.get_attribute('href')
                                 if href and "javascript" not in href:
                                     driver.get(href)
                                 else:
                                     driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                                     time.sleep(0.5)
                                     driver.execute_script("arguments[0].click();", btn)
                                 
                                 is_click_success = True
                                 break # Break candidates loop
                                 
                         except Exception as inner_e: 
                             continue
                     
                     if not is_click_success:
                         logger.warning(f"Could not find button for {fac} after checking candidates.")
                         continue

                     # 5. WAIT & SCRAPE
                     if _status_callback: _status_callback(f"  📅 カレンダー確認中: {fac}")
                     time.sleep(3) 
                     
                     found_context = switch_to_target_frame(driver, "予約状況", None)
                     
                     scrape_calendar(driver, results, fac, "体育室", start_date)
                     
                     # 6. GO BACK
                     if _status_callback: _status_callback(f"  🔙 リストに戻ります...")
                     driver.back()
                     time.sleep(5) # Wait generously for list reload

                 except Exception as e:
                     logger.error(f"Error processing {fac}: {e}")
                     try: driver.back() 
                     except: pass
                     time.sleep(3)
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

def get_data(keyword, start_date, end_date, selected_facilities, _status, _progress, _debug_placeholder):
    df = attempt_scrape_with_retry(start_date, end_date, selected_facilities, _status, _progress, _debug_placeholder)
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
        # Facility Guard
        if not selected_target_facilities:
            st.warning("対象施設を選択してください。")
            return

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
            df = get_data("バレーボール", start_d, end_d, selected_target_facilities, status_box.write, p_bar, debug_placeholder)
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

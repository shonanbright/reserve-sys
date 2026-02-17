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
TARGET_URL = "https://fujisawacity.service-now.com/facilities_reservation"
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

def attempt_scrape_with_retry(keyword, start_date, _status_callback, _progress_bar):
    for attempt in range(MAX_RETRIES):
        try:
            if _status_callback: 
                msg = f"データ取得 試行 {attempt + 1}回目..."
                _status_callback(msg)
            
            df = fetch_availability_core(keyword, start_date, _status_callback, _progress_bar)
            if not df.empty:
                return df
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
        except Exception as e:
            logger.error(f"Attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    return pd.DataFrame()

def fetch_availability_core(keyword="バレーボール", start_date=None, _status_callback=None, _progress_bar=None):
    driver = setup_driver()
    wait = WebDriverWait(driver, 30) 
    results = []

    try:
        # 1. Access & Frame
        driver.get(TARGET_URL)
        time.sleep(3)
        
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        if frames:
            driver.switch_to.frame(0)
            logger.info("Switched to iframe")

        # 2. Date Input
        if start_date:
            formatted_date = start_date.strftime("%Y-%m-%d")
            if _status_callback: _status_callback(f"📅 開始日を {formatted_date} に設定中...")
            
            inputs_to_try = driver.find_elements(By.CSS_SELECTOR, "input[type='date'], input.datepicker, input[name*='date'], input[id*='date']")
            for inp in inputs_to_try:
                try:
                    if inp.is_displayed():
                        driver.execute_script(f"arguments[0].value = '{formatted_date}';", inp)
                        inp.send_keys(Keys.TAB)
                        driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", inp)
                        time.sleep(1)
                except: pass

        # 3. Purpose Search
        if _status_callback: _status_callback(f"🏐 「{keyword}」を選択中...")
        search_done = False
        
        try:
            links = driver.find_elements(By.PARTIAL_LINK_TEXT, keyword)
            for link in links:
                if link.is_displayed():
                    safe_click_js(driver, link)
                    search_done = True
                    time.sleep(3)
                    break
        except: pass

        if not search_done:
            try:
                search_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='search'], input[placeholder*='検索'], input[name*='keyword']")))
                search_input.clear()
                search_input.send_keys(keyword)
                search_input.send_keys(Keys.ENTER)
                time.sleep(3)
            except: 
                logger.warning("Keyword search failed")

        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # 4. Scan Facilities & Availability
        if _status_callback: _status_callback("🔍 施設と空き情報を解析中...")

        soup = BeautifulSoup(driver.page_source, "html.parser")
        tables = soup.find_all("table")
        
        for tbl in tables:
            txt = tbl.get_text()
            if not ("空" in txt or "○" in txt or "×" in txt or "/" in txt):
                continue

            rows = tbl.find_all("tr")
            if not rows: continue

            headers = []
            header_row = rows[0]
            for th in header_row.find_all(["th", "td"]):
                headers.append(th.get_text(strip=True))
            
            # Row scan
            current_facility = "検索結果施設"
            
            for tr in rows[1:]:
                cols = tr.find_all(["th", "td"])
                if not cols: continue
                
                row_text = tr.get_text(separator="|", strip=True) 
                
                known_facilities = ["秋葉台", "秩父宮", "石名坂", "鵠沼", "北部", "太陽", "八部", "遠藤"]
                for kf in known_facilities:
                    if kf in row_text:
                        current_facility = kf
                        break

                col0_text = cols[0].get_text(strip=True)
                
                for i, cell in enumerate(cols[1:]):
                    status_text = cell.get_text(strip=True)
                    status = "×"
                    
                    if "○" in status_text or "空" in status_text: status = "○"
                    elif "△" in status_text: status = "△"
                    elif "休" in status_text or "-" in status_text: continue
                    else: continue
                    
                    if (i + 1) < len(headers):
                        time_slot = headers[i + 1]
                    else:
                        time_slot = f"枠{i+1}"

                    results.append({
                        "日付": col0_text,
                        "施設名": current_facility,
                        "時間": time_slot,
                        "状況": status
                    })
        
        # 5. Deep Scan fallback
        if not results:
            if _status_callback: _status_callback("📄 詳細ページを巡回中...")
            links = driver.find_elements(By.TAG_NAME, "a")
            target_urls = []
            for a in links:
                try:
                    href = a.get_attribute("href")
                    txt = a.text
                    if href and ("calendar" in href or "reference" in href):
                        target_urls.append((txt, href))
                except: pass
            
            target_urls = list(set(target_urls))
            
            for idx, (t_txt, t_url) in enumerate(target_urls):
                if _progress_bar: _progress_bar.progress(idx / max(len(target_urls), 1))
                driver.get(t_url)
                time.sleep(2)
                
                soup_sub = BeautifulSoup(driver.page_source, "html.parser")
                sub_tables = soup_sub.find_all("table")
                
                facility_name_sub = t_txt
                try:
                    h_elem = driver.find_element(By.CSS_SELECTOR, "h1, h2, .facility-name")
                    facility_name_sub = h_elem.text
                except: pass
                
                for stbl in sub_tables:
                     srows = stbl.find_all("tr")
                     if not srows: continue
                     sheaders = [th.get_text(strip=True) for th in srows[0].find_all(["th", "td"])]
                     for str_row in srows[1:]:
                         scols = str_row.find_all(["th", "td"])
                         if not scols: continue
                         date_val = scols[0].get_text(strip=True)
                         for si, scell in enumerate(scols[1:]):
                             sstat_txt = scell.get_text(strip=True)
                             sstat = "×"
                             if "○" in sstat_txt or "空" in sstat_txt: sstat = "○"
                             elif "△" in sstat_txt: sstat = "△"
                             else: continue
                             stime = sheaders[si+1] if (si+1) < len(sheaders) else ""
                             results.append({
                                 "日付": date_val,
                                 "施設名": facility_name_sub,
                                 "時間": stime,
                                 "状況": sstat
                             })

    except Exception as e:
        logger.error(f"Scrape Error: {e}")
    finally:
        driver.quit()

    if not results:
        return pd.DataFrame(columns=['日付', '施設名', '時間', '状況'])
    
    return pd.DataFrame(results)


# --- Data Logic ---
TODAY = datetime.date.today()
CURRENT_YEAR = TODAY.year

def enrich_data(df):
    if df.empty: return df

    def parse_date(d_str):
        if not isinstance(d_str, str): return None
        # Clean string: "2026-03-01", "3/1(土)"
        try:
            clean = d_str.split('(')[0].strip()
            # Replace hyphens/dots/kanji
            clean = clean.replace('年', '/').replace('月', '/').replace('日', '').replace('-', '/').replace('.', '/')
            parts = clean.split('/')
            
            if len(parts) == 2: # MM/DD
                m, d = int(parts[0]), int(parts[1])
                dt = datetime.date(CURRENT_YEAR, m, d)
                if dt < TODAY - datetime.timedelta(days=90): 
                    dt = datetime.date(CURRENT_YEAR + 1, m, d)
                return dt
            elif len(parts) == 3: # YYYY/MM/DD
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                return datetime.date(y, m, d)
        except: return None
        return None

    df['dt'] = df['日付'].apply(parse_date)
    
    def get_day(dt):
        if dt is None: return "不明"
        if jpholiday.is_holiday(dt): return "祝"
        return ["月","火","水","木","金","土","日"][dt.weekday()]

    df['曜日'] = df['dt'].apply(get_day)
    return df

@st.cache_data(ttl=600)
def get_data(keyword, start_date, _status, _progress):
    df = attempt_scrape_with_retry(keyword, start_date, _status, _progress)
    return enrich_data(df)

def render_schedule_card(row):
    status = row['状況']
    facility = row['施設名']
    date_str = row['日付']
    time_slot = row['時間']
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
            st.text(f"{facility}")
            st.caption(f"{time_slot}")

def main():
    st.title("🏐 湘南Bright 施設予約状況")
    
    st.sidebar.header("🔍 検索条件")
    d_input = st.sidebar.date_input(
        "日付範囲", 
        value=(TODAY, TODAY + datetime.timedelta(days=14)),
        min_value=TODAY,
        max_value=TODAY + datetime.timedelta(days=180)
    )
    st.sidebar.info("種目: バレーボール")

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
        
        st.session_state.data = pd.DataFrame()
        
        try:
            df = get_data("バレーボール", start_d, status_box.write, p_bar)
            status_box.update(label="完了", state="complete", expanded=False)
            
            if not df.empty:
                # Filtering
                mask = (df['dt'] >= start_d) & (df['dt'] <= end_d)
                
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

                    # Table Display
                    with st.expander("全体の表を見る"):
                        st.table(final_df[['日付', '曜日', '施設名', '時間', '状況']])
                    
                    # Card Display
                    st.subheader("空き状況カード")
                    for _, row in final_df.iterrows():
                        render_schedule_card(row)
                        
                else:
                    st.warning("条件に合う空きは見つかりませんでした。")
            else:
                st.error("データ取得に失敗しました（または空きがありません）。")
                
        except Exception as e:
            st.error(f"エラー: {e}")

if __name__ == "__main__":
    main()

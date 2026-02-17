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

# --- Scraper Logic (Deep Scan) ---
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
            
            df = fetch_availability_deep_scan(keyword, start_date, _status_callback, _progress_bar)
            if not df.empty:
                return df
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
        except Exception as e:
            logger.error(f"Attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    return pd.DataFrame()

def fetch_availability_deep_scan(keyword="バレーボール", start_date=None, _status_callback=None, _progress_bar=None):
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
        if _status_callback: _status_callback(f"🏐 「{keyword}」で施設を検索中...")
        search_done = False
        
        # リンクがあればクリック (メニュー選択)
        try:
            links = driver.find_elements(By.PARTIAL_LINK_TEXT, keyword)
            for link in links:
                if link.is_displayed():
                    safe_click_js(driver, link)
                    search_done = True
                    time.sleep(3)
                    break
        except: pass

        # 検索ボタン入力
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

        # 4. Traverse Room List (Master-Detail)
        if _status_callback: _status_callback("📋 室場リストを取得中...")
        
        # まずリストページにある「カレンダーアイコン」や「予約」ボタンのリンクを全収集
        target_urls = []
        
        # テーブル行をスキャンして、施設名/室場名とURLのペアを取得
        try:
            rows = driver.find_elements(By.CSS_SELECTOR, "tr")
            current_facility_context = "詳細不明"
            
            for row in rows:
                text = row.text
                
                # 施設名行の検知（ヘッダー等はスキップが必要だが、とりあえずヒューリスティックに）
                if "体育館" in text or "センター" in text or "公園" in text:
                    # ここが施設ヘッダーかもしれない
                    # 通常は、行の中に施設名テキストがある
                    pass 
                
                # リンク（ボタン）を探す
                links = row.find_elements(By.TAG_NAME, "a")
                for link in links:
                    href = link.get_attribute("href")
                    # カレンダー遷移っぽいURL
                    if href and ("calendar" in href or "reserve" in href or "detail" in href):
                        row_raw_text = row.text.replace("\n", " ")
                        target_urls.append({
                            "url": href,
                            "raw_text": row_raw_text # これを後でパースして施設名/室場名にする
                        })
        except: pass
        
        # 重複排除 (URLベース)
        unique_targets = {}
        for t in target_urls:
            unique_targets[t['url']] = t
        target_list = list(unique_targets.values())

        if not target_list:
            # フォールバック：ページ内の全リンクからカレンダーっぽいものを探す
             all_links = driver.find_elements(By.TAG_NAME, "a")
             for a in all_links:
                 try:
                     href = a.get_attribute("href")
                     if href and ("calendar" in href):
                         target_list.append({"url": href, "raw_text": a.text})
                 except: pass

        # 5. Loop through Detail Pages
        total_targets = len(target_list)
        if _status_callback: _status_callback(f"🔍 {total_targets} 件の室場カレンダーを巡回解析します...")

        for idx, target in enumerate(target_list):
            url = target['url']
            raw_text = target['raw_text']
            
            if _progress_bar: _progress_bar.progress(idx / max(total_targets, 1))
            
            # Extract names from raw text if possible
            # e.g. "秋葉台文化体育館 第1体育室 予約と空き状況"
            facility_name = "不明"
            room_name = "不明"
            
            known_facilities = ["秋葉台", "秩父宮", "石名坂", "鵠沼", "北部", "太陽", "八部", "遠藤"]
            for kf in known_facilities:
                if kf in raw_text:
                    facility_name = kf
                    # 室場名の推定: 施設名を除去した残りの部分
                    room_name = raw_text.replace(kf, "").replace("文化体育館", "").replace("市民センター", "").replace("体育室", "").strip()
                    if not room_name: room_name = "体育室" # default
                    break
            
            if _status_callback: _status_callback(f"解析中: {facility_name} {room_name}")

            # Navigate
            driver.get(url)
            time.sleep(2)
            
            # Scrape Calendar Table
            soup = BeautifulSoup(driver.page_source, "html.parser")
            calendar_tables = soup.find_all("table")
            
            for tbl in calendar_tables:
                # 必須要素の確認
                txt_content = tbl.get_text()
                if not ("空" in txt_content or "○" in txt_content or "×" in txt_content):
                    continue
                
                rows = tbl.find_all("tr")
                if not rows: continue
                
                # Header Parse
                headers = []
                try:
                    for th in rows[0].find_all(["th", "td"]):
                        headers.append(th.get_text(strip=True))
                except: continue
                
                # Data Parse
                for tr in rows[1:]:
                    cols = tr.find_all(["th", "td"])
                    if not cols: continue
                    
                    # Date (Column 0 usually)
                    date_val = cols[0].get_text(strip=True)
                    
                    # Time Slots
                    for i, td in enumerate(cols[1:]):
                        stat_text = td.get_text(strip=True)
                        status = "×"
                        if "○" in stat_text or "空" in stat_text: status = "○"
                        elif "△" in stat_text: status = "△"
                        else: continue
                        
                        # Time header
                        t_slot = headers[i+1] if (i+1) < len(headers) else ""
                        
                        results.append({
                            "日付": date_val,
                            "施設名": facility_name,
                            "室場名": room_name,
                            "時間": t_slot,
                            "状況": status
                        })

    except Exception as e:
        logger.error(f"Scrape Error: {e}")
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
                return datetime.date(y, m, d)
        except: return None
        return None

    df['dt'] = df['日付'].apply(parse_date)

    def get_day(row):
        dt = row['dt']
        d_str = str(row.get('日付', ''))
        
        # 1. Try from datetime object
        if dt:
            if jpholiday.is_holiday(dt): return "祝"
            return ["月","火","水","木","金","土","日"][dt.weekday()]
            
        # 2. Try from string
        for w in ["月","火","水","木","金","土","日"]:
            if f"({w})" in d_str or f"（{w}）" in d_str:
                return w
                
        return "詳細不明"

    df['曜日'] = df.apply(get_day, axis=1)
    return df

@st.cache_data(ttl=600)
def get_data(keyword, start_date, _status, _progress):
    df = attempt_scrape_with_retry(keyword, start_date, _status, _progress)
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
                # Time Filter
                mask = pd.Series(True, index=df.index)
                
                if 'dt' in df.columns:
                     date_mask = (df['dt'] >= start_d) & (df['dt'] <= end_d)
                     date_mask = date_mask.fillna(False)
                     mask &= date_mask

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
                    for _, row in final_df.iterrows():
                        render_schedule_card(row)
                        
                else:
                    st.warning("条件に合う空きは見つかりませんでした。")
                    with st.expander("詳細デバッグ (フィルタ前データ)"):
                         st.dataframe(df)
            else:
                st.error("データ取得に失敗しました（または空きがありません）。")
                
        except Exception as e:
            st.error(f"エラー: {e}")

if __name__ == "__main__":
    main()

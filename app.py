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
WEEKS_TO_FETCH_DEFAULT = 12
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
                msg = f"データ取得を試みています... (回数: {attempt + 1}/{MAX_RETRIES})"
                _status_callback(msg)
            
            df = fetch_availability_core(keyword, start_date, _status_callback, _progress_bar)
            if not df.empty:
                return df
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(3) # Retry interval
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
        # 1. Access & Frame Handling
        driver.get(TARGET_URL)
        time.sleep(5) # しっかり待つ

        # フレームがある場合はスイッチ (藤沢市はiframeを使っている箇所がある可能性がある)
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        if frames:
            driver.switch_to.frame(0) # とりあえず最初のフレームへ
            logger.info("Switched to iframe")

        # 2. Force Date Input
        if start_date:
            formatted_date = start_date.strftime("%Y-%m-%d") # HTML5 input standard
            if _status_callback: _status_callback(f"検索開始日を {formatted_date} に設定...")
            
            # 複数のフォーマットやセレクタで試行
            inputs_to_try = driver.find_elements(By.CSS_SELECTOR, "input[type='date'], input.datepicker, input[name*='date'], input[id*='date']")
            for inp in inputs_to_try:
                try:
                    if inp.is_displayed():
                        driver.execute_script(f"arguments[0].value = '{formatted_date}';", inp)
                        inp.send_keys(Keys.TAB)
                except: pass

        # 3. Keyword Search / Partial Link Text
        if _status_callback: _status_callback(f"「{keyword}」を選択/検索中...")
        
        # まずはリンクテキストでのクリックを試みる (メニュー選択式の場合)
        try:
            link = driver.find_element(By.PARTIAL_LINK_TEXT, keyword)
            safe_click_js(driver, link)
            time.sleep(3)
        except:
            # リンクがない場合は検索ボックスに入力
            try:
                search_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='search'], input[placeholder*='検索'], input[name*='keyword']")))
                search_input.clear()
                search_input.send_keys(keyword)
                search_input.send_keys(Keys.ENTER)
                time.sleep(5)
            except:
                logger.warning("Keyword search failed")

        # 4. Expand & Scan Facilities (Endo, Akibadai, etc.)
        if _status_callback: _status_callback("施設データをスキャン中...")
        
        # 全体待機
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # 施設名を含む可能性のある要素を探す
        # 検索結果がリスト形式の場合
        
        # テーブル行を走査
        # 藤沢市のシステムがテーブル形式で結果を出すと仮定して全行スキャン
        try:
            rows = driver.find_elements(By.TAG_NAME, "tr")
            for row in rows:
                text = row.text
                if keyword in text or "体育館" in text or "センター" in text:
                    # リンクがあればクリックして詳細へ行くか、その行からデータを取る
                    links = row.find_elements(By.TAG_NAME, "a")
                    for link in links:
                        # カレンダーへ飛ぶリンクなら収集対象
                        href = link.get_attribute("href")
                        if href and ("calendar" in href or "reserve" in href):
                            # ここではURL収集リストに追加するだけにするか、再帰的に処理するか
                            pass 
        except: pass

        # カレンダーページへのリンク収集 (より広範囲に)
        room_links_elements = driver.find_elements(By.CSS_SELECTOR, "a")
        room_urls = []
        for elem in room_links_elements:
            try:
                txt = elem.text
                href = elem.get_attribute("href")
                if href and "javascript" not in href and "#" not in href:
                    # 特定のキーワードが含まれるか、またはカレンダーっぽいURL
                    if "空き" in txt or "予約" in txt or "詳細" in txt or "facility" in href or "calendar" in href:
                         room_urls.append((txt, href))
            except: pass
        
        # 重複削除
        room_urls = list(set(room_urls))
        # まったくなければ現在ページを対象
        if not room_urls:
            room_urls = [("Current Page", driver.current_url)]

        # 5. Loop Rooms
        total_rooms = len(room_urls)
        
        for r_idx, (r_name, url) in enumerate(room_urls):
             # Progress
            if _progress_bar:
                _progress_bar.progress(min((r_idx / max(total_rooms, 1)), 0.9))

            if url != driver.current_url and url != "Current Page":
                driver.get(url)
                time.sleep(3)
            
            # 施設名取得
            try:
                facility_name = "不明な施設"
                titles = driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3, .title, .facility-name")
                for t in titles:
                    if t.text: 
                        facility_name = t.text
                        break
            except: pass

            if _status_callback: _status_callback(f"解析中: {facility_name}")

            # 6. Parse Table (Full Scan)
            try:
                soup = BeautifulSoup(driver.page_source, "html.parser")
                tables = soup.find_all("table")
                
                for tbl in tables:
                    # そのテーブルがカレンダーか判定
                    if not ("日" in tbl.get_text() and ("○" in tbl.get_text() or "空" in tbl.get_text() or "×" in tbl.get_text())):
                        continue
                        
                    rows = tbl.find_all("tr")
                    if not rows: continue
                    
                    # ヘッダー解析
                    headers = []
                    header_row = rows[0]
                    for th in header_row.find_all(["th", "td"]):
                        headers.append(th.get_text(strip=True))
                        
                    # データ行解析
                    for tr in rows[1:]:
                        cols = tr.find_all(["th", "td"])
                        if not cols: continue
                        
                        # 1列目は日付と仮定
                        date_val = cols[0].get_text(strip=True)
                        
                        # 2列目以降は時間枠
                        for i, col in enumerate(cols[1:]):
                            val = col.get_text(strip=True)
                            
                            # ステータス判定
                            status = "×"
                            if "○" in val or "空" in val: status = "○"
                            elif "△" in val: status = "△"
                            elif "休" in val or "-" in val: continue
                            else: continue # 対象外
                            
                            # 時間帯名
                            time_slot = headers[i+1] if (i+1) < len(headers) else f"枠{i+1}"
                            
                            if status in ["○", "△"]:
                                results.append({
                                    "日付": date_val,
                                    "曜日": "", 
                                    "施設名": facility_name,
                                    "室場名": r_name if r_name != "Current Page" else "",
                                    "時間": time_slot,
                                    "状況": status
                                })
            except Exception as e:
                logger.error(f"Table parse error: {e}")

    except Exception as e:
        logger.error(f"Global Error: {e}")
    finally:
        driver.quit()

    if not results:
        return pd.DataFrame(columns=['日付', '曜日', '施設名', '室場名', '時間', '状況'])
        
    return pd.DataFrame(results)


# --- データ後処理 (日付パース・休日判定) ---
CURRENT_YEAR = datetime.datetime.now().year
TODAY = datetime.date.today()

def enrich_data(df):
    if df.empty: return df

    def parse_date(date_str):
        if not isinstance(date_str, str): return None
        try:
            # "3/1" -> 3, 1
            # "2026/03/01" -> 2026, 3, 1
            clean = date_str.split('(')[0].strip()
            # 区切り文字統一
            clean = clean.replace('年', '/').replace('月', '/').replace('日', '').replace('-', '/')
            parts = clean.split('/')
            
            if len(parts) == 3:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                return datetime.date(y, m, d)
            elif len(parts) == 2:
                m, d = int(parts[0]), int(parts[1])
                dt = datetime.date(CURRENT_YEAR, m, d)
                if dt < TODAY - datetime.timedelta(days=60): # 過去すぎたら来年
                    dt = datetime.date(CURRENT_YEAR + 1, m, d)
                return dt
            return None
        except:
            return None

    df['dt'] = df['日付'].apply(parse_date)
    
    # 曜日判定 (祝日優先)
    def get_day_label(dt):
        if dt is None: return "不明"
        if jpholiday.is_holiday(dt):
            return "祝"
        weeks = ["月", "火", "水", "木", "金", "土", "日"]
        return weeks[dt.weekday()]

    df['day_label'] = df['dt'].apply(get_day_label)
    
    # 時間帯区分
    def get_slot_label(time_str):
        t = time_str
        if "09" in t or "11" in t or "午前" in t: return "午前"
        if "13" in t or "15" in t or "午後" in t: return "午後"
        if "17" in t or "19" in t or "夜間" in t: return "夜間"
        return "その他"

    df['slot_label'] = df['時間'].apply(get_slot_label)
    
    return df

@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_availability(keyword, start_date=None, _status_callback=None, _progress_bar=None):
    # Retry Logic Wrapper
    df = attempt_scrape_with_retry(keyword, start_date, _status_callback, _progress_bar)
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
    
    today = datetime.date.today()
    default_end = today + datetime.timedelta(days=14)
    
    date_range = st.sidebar.date_input(
        "検索期間",
        value=(today, default_end),
        min_value=today,
        max_value=today + datetime.timedelta(days=120) 
    )
    
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
        start_d = None
        
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_d, end_d = date_range
        else:
            st.error("開始日と終了日の両方を選択してください。")
            return

        st.session_state.data = pd.DataFrame()
        
        status_container = st.status("🚀 リクエストを確認中...", expanded=True)
        progress_bar = status_container.progress(0, text="ブラウザ起動中...")
        
        def update_status(msg):
            status_container.write(msg)
            
        start_time = time.time()
        
        try:
            # Scrape
            raw_data = get_cached_availability(
                "バレーボール", 
                start_date=start_d, 
                _status_callback=update_status, 
                _progress_bar=progress_bar
            )
            
            elapsed_time = time.time() - start_time
            
            if not raw_data.empty:
                st.session_state.data = raw_data
                status_container.update(label=f"取得完了！ ({elapsed_time:.1f}秒)", state="complete", expanded=False)
                st.success(f"最新データを取得しました！ (所要時間: {elapsed_time:.1f}秒)")
            else:
                status_container.update(label="データなし", state="error")
                st.warning("空き状況は見つかりませんでした（またはサイトが混雑しています）。")
                
        except Exception as e:
            status_container.update(label="エラー発生", state="error")
            st.error(f"システムエラー: {e}")

    if st.sidebar.button("キャッシュをクリア"):
        st.cache_data.clear()
        st.toast("キャッシュクリア完了")

    st.divider()

    if 'data' in st.session_state and not st.session_state.data.empty:
        df = st.session_state.data
        total_count = len(df)
        
        mask = pd.Series(True, index=df.index)
        
        # Filter: Date
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_d, end_d = date_range
            mask &= (df['dt'] >= start_d) & (df['dt'] <= end_d)
            
        # Filter: Day
        if selected_days:
            mask &= df['day_label'].isin(selected_days)
            
        # Filter: Slot
        if selected_slots:
            mask &= df['slot_label'].isin(selected_slots)
        
        filtered_df = df[mask]
        filtered_count = len(filtered_df)

        if filtered_count > 0:
            st.success(f"✨ **{filtered_count}** 件の空きが見つかりました！（全{total_count}件中）")
        else:
            st.warning(f"条件に一致する空きはありませんでした。（全{total_count}件取得）")
            with st.expander("フィルタ前のデータを確認"):
                st.dataframe(df[['日付', '曜日', '施設名', '時間', '状況']])

        try:
            filtered_df = filtered_df.sort_values(by=["dt", "時間"])
        except: pass

        for idx, row in filtered_df.iterrows():
            render_schedule_card(row)
    
    elif 'data' not in st.session_state:
        st.info("👈 サイドバー情報を確認し、「最新情報を取得」ボタンを押してください。")

if __name__ == "__main__":
    main()

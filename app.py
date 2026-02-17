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
        time.sleep(5) 

        # フレーム判定
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        if frames:
            driver.switch_to.frame(0)
            logger.info("Switched to iframe")

        # 2. Force Date Input
        if start_date:
            formatted_date = start_date.strftime("%Y-%m-%d")
            if _status_callback: _status_callback(f"検索開始日を {formatted_date} に設定...")
            
            inputs_to_try = driver.find_elements(By.CSS_SELECTOR, "input[type='date'], input.datepicker, input[name*='date'], input[id*='date']")
            for inp in inputs_to_try:
                try:
                    if inp.is_displayed():
                        driver.execute_script(f"arguments[0].value = '{formatted_date}';", inp)
                        inp.send_keys(Keys.TAB)
                except: pass

        # 3. Keyword Search
        if _status_callback: _status_callback(f"「{keyword}」で検索中...")
        
        # 検索処理
        search_success = False
        try:
            # リンクテキストクリック
            link = driver.find_element(By.PARTIAL_LINK_TEXT, keyword)
            safe_click_js(driver, link)
            search_success = True
            time.sleep(3)
        except:
            # 検索ボックス入力
            try:
                search_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='search'], input[placeholder*='検索'], input[name*='keyword']")))
                search_input.clear()
                search_input.send_keys(keyword)
                search_input.send_keys(Keys.ENTER)
                search_success = True
                time.sleep(5)
            except:
                logger.warning("Keyword search failed")

        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # 4. Parse Results (Table Scan Logic)
        if _status_callback: _status_callback("施設リストと空き状況を解析中...")
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        # 戦略A: 検索結果一覧そのものが巨大なテーブルの場合 (一括取得)
        main_tables = soup.find_all("table")
        for tbl in main_tables:
            # 有効なテーブルか判定（"施設" "年月日" などのキーワードがあるか）
            text_content = tbl.get_text()
            if not ("空" in text_content or "○" in text_content or "×" in text_content):
                continue

            rows = tbl.find_all("tr")
            if not rows: continue

            # ヘッダー解析
            headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
            
            # 各行をスキャン
            for tr in rows[1:]:
                cols = tr.find_all(["th", "td"])
                if not cols: continue
                
                # 施設名の抽出 (隠れている場合も含めて、テキストを結合)
                # 多くの場合、最初のカラムか、特定のクラスを持つ要素にある
                row_text = tr.get_text(separator="|", strip=True) # パイプ区切りで全テキスト取得
                
                # 施設名候補の抽出 (簡易ロジック: 特定のキーワードを含むか)
                candidates = ["遠藤", "秋葉台", "秩父宮", "鵠沼", "石名坂", "北部", "太陽", "八部"]
                found_facility = "検索結果一覧"
                for cand in candidates:
                    if cand in row_text:
                        found_facility = cand + "周辺施設" # 詳細がわからないので一旦これ
                        break
                
                # もし詳細リンクがあれば、それを施設名として使う
                links = tr.find_all("a")
                if links:
                    for l in links:
                        if len(l.get_text(strip=True)) > 2:
                            found_facility = l.get_text(strip=True)
                            break
                
                # 空き状況の判定
                for i, td in enumerate(cols):
                    cell_text = td.get_text(strip=True)
                    status = "×"
                    if "○" in cell_text or "空" in cell_text: status = "○"
                    elif "△" in cell_text: status = "△"
                    elif "満" in cell_text or "×" in cell_text: status = "満"
                    else: continue # 関係ないセル

                    # カラム位置から時間帯などを推測したいが、単純リストの場合は難しい
                    # ここでは「日付」カラムがある前提か、またはヘッダー対応
                    
                    # 日付情報の取得 (行の先頭にある場合が多い)
                    date_val = cols[0].get_text(strip=True)
                    
                    # 時間情報の取得 (ヘッダーがあればそれを使う)
                    time_slot = headers[i] if i < len(headers) else "時間枠不明"
                    
                    if status in ["○", "△"]:
                        results.append({
                            "日付": date_val,
                            "曜日": "",
                            "施設名": found_facility,
                            "室場名": "",
                            "時間": time_slot,
                            "状況": status
                        })

        # 戦略B: 施設ごとの詳細ページを巡回 (もし戦略Aで取れなかった場合)
        if not results:
            if _status_callback: _status_callback("詳細ページモードで再スキャン中...")
            
            # リンク収集
            room_links = []
            a_tags = driver.find_elements(By.TAG_NAME, "a")
            for a in a_tags:
                try:
                    txt = a.text
                    href = a.get_attribute("href")
                    if href and ("calendar" in href or "reserve" in href or "details" in href):
                        room_links.append((txt, href))
                except: pass
            
            room_links = list(set(room_links)) # 重複排除
            
            total_links = len(room_links)
            for idx, (txt, href) in enumerate(room_links):
                if _progress_bar: _progress_bar.progress(idx / max(total_links, 1))
                
                try:
                    driver.get(href)
                    time.sleep(2)
                    
                    # 施設名取得 (詳細ページ内のh1/h2等)
                    facility_name = txt # リンク名をデフォルトに
                    try:
                        h_tags = driver.find_elements(By.CSS_SELECTOR, "h1, h2, .facility_name")
                        if h_tags: facility_name = h_tags[0].text
                    except: pass
                    
                    if _status_callback: _status_callback(f"解析中: {facility_name}")
                    
                    # テーブル解析
                    soup_sub = BeautifulSoup(driver.page_source, "html.parser")
                    sub_tables = soup_sub.find_all("table")
                    for tbl in sub_tables:
                        # (既存のテーブル解析ロジック)
                        rows = tbl.find_all("tr")
                        if not rows: continue
                        headers_sub = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
                        
                        for tr in rows[1:]:
                            cols = tr.find_all(["th", "td"])
                            if not cols: continue
                            date_val = cols[0].get_text(strip=True)
                            
                            for i, td in enumerate(cols[1:]):
                                st_text = td.get_text(strip=True)
                                stat = "×"
                                if "○" in st_text or "空" in st_text: stat = "○"
                                elif "△" in st_text: stat = "△"
                                else: continue
                                
                                t_slot = headers_sub[i+1] if (i+1) < len(headers_sub) else ""
                                
                                if stat in ["○", "△"]:
                                    results.append({
                                        "日付": date_val,
                                        "曜日": "",
                                        "施設名": facility_name,
                                        "室場名": "",
                                        "時間": t_slot,
                                        "状況": stat
                                    })
                except Exception as e:
                    logger.error(f"Link loop error: {e}")
                    continue

    except Exception as e:
        logger.error(f"Global Error: {e}")
    finally:
        driver.quit()

    if not results:
        # デバッグ用: 失敗時はダミーを返さず空を返す (ログ等で確認)
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
            clean = date_str.split('(')[0].strip()
            clean = clean.replace('年', '/').replace('月', '/').replace('日', '').replace('-', '/')
            parts = clean.split('/')
            
            if len(parts) >= 2:
                # MM/DD 想定 (YYYYがない場合)
                if len(parts) == 2:
                    m, d = int(parts[0]), int(parts[1])
                    dt = datetime.date(CURRENT_YEAR, m, d)
                    if dt < TODAY - datetime.timedelta(days=60): # 過去すぎたら来年
                        dt = datetime.date(CURRENT_YEAR + 1, m, d)
                    return dt
                elif len(parts) == 3:
                     # YYYY/MM/DD
                     y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                     return datetime.date(y, m, d)
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
        t = str(time_str)
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
        end_d = None
        
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
                
                # デバッグ情報
                with st.expander("デバッグ: エラー診断"):
                    st.write("もし空きがあるはずなのに表示されない場合は、以下を確認してください。")
                    st.write("1. 検索期間が正しく設定されているか")
                    st.write("2. 藤沢市サイトがメンテナンス中でないか")
                    st.write("3. 「バレーボール」というキーワードでヒットする施設があるか")
                
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

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

def switch_to_target_frame(driver, target_text="市民センター", _status_callback=None):
    """
    Switch to the iframe containing the target text.
    Returns True if found (or already in correct frame), False otherwise.
    """
    try:
        # 1. Check current content first
        if target_text in driver.page_source:
             if _status_callback: _status_callback(f"✅ ターゲット要素 '{target_text}' を現在のフレームで発見")
             return True
        
        # 2. Iterate iframes
        driver.switch_to.default_content()
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        
        if not frames:
             # if _status_callback: _status_callback("⚠️ iframeが見つかりません。メインコンテンツを探索します。")
             return False

        if _status_callback: _status_callback(f"🔍 {len(frames)} 件のiframeを探索中...")
        
        for i in range(len(frames)):
            try:
                driver.switch_to.default_content()
                # Re-find to avoid stale element reference
                current_frames = driver.find_elements(By.TAG_NAME, "iframe")
                if i >= len(current_frames): break
                
                driver.switch_to.frame(current_frames[i])
                time.sleep(0.5) # Wait for frame context
                
                if target_text in driver.page_source:
                    if _status_callback: _status_callback(f"✅ iframe[{i}] 内で '{target_text}' を発見。コンテキストを固定します。")
                    return True
            except Exception as e:
                logger.warning(f"Frame check error: {e}")
                continue
        
        # If not found, revert to default
        driver.switch_to.default_content()
        return False
        
    except Exception as e:
        logger.error(f"Switch context error: {e}")
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
            
            # If empty, treating as failure to trigger retry
            raise Exception("空き情報が見つかりませんでした (0件)")
            
        except Exception as e:
            logger.error(f"Attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    return pd.DataFrame()

def fetch_availability_deep_scan(start_date=None, end_date=None, selected_facilities=None, _status_callback=None, _progress_bar=None, _debug_placeholder=None, attempt_idx=0):
    driver = setup_driver()
    wait = WebDriverWait(driver, 30) 
    results = []

    try:
        # 1. Access New URL
        if _status_callback: _status_callback("📡 予約システムにアクセス中...")
        driver.get(TARGET_URL)
        time.sleep(5) 

        # 🔵 IFRAME DETECTION & SWITCHING
        found_context = switch_to_target_frame(driver, "市民センター", _status_callback)
        if not found_context:
             if _status_callback: _status_callback("⚠️ ターゲット要素が見つかりませんでしたが、処理を続行します...")

        # HIDE BANNERS 
        try:
             driver.execute_script("document.querySelectorAll('header, .alert, .announcement, #sc_header_top, .navbar, .cookie-banner').forEach(e => e.remove());")
             time.sleep(0.5)
        except: pass

        # 2. Check "Civic Center" Checkbox using JS Logic
        if _status_callback: _status_callback("🏢 「市民センター」を選択中(JS)...")
        
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
                if (!inp && targetLabel.getAttribute('for')) {
                    inp = document.getElementById(targetLabel.getAttribute('for'));
                }
                if (inp) {
                    if (!inp.checked) {
                        inp.click(); 
                        if (!inp.checked) {
                            inp.checked = true; 
                            inp.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    }
                    return true;
                }
            }
            return false;
        """
        driver.execute_script(js_checkbox_script)
        time.sleep(1)

        # 3. Input Date using JS Logic (In current frame)
        if start_date:
            formatted_date = start_date.strftime("%Y-%m-%d")
            if _status_callback: _status_callback(f"📅 開始日を {formatted_date} に設定中(JS)...")
            
            js_date_script = f"""
                var inputs = document.querySelectorAll("input[type='date'], input.datepicker, input[type='text']");
                var dateInp = null;
                var labels = document.querySelectorAll('label, span, th, b');
                for (var i = 0; i < labels.length; i++) {{
                     if (labels[i].innerText.includes('利用日') || labels[i].innerText.includes('Date')) {{
                         var el = labels[i];
                         while (el) {{
                             el = el.nextElementSibling;
                             if (el && el.tagName === 'INPUT') {{
                                 dateInp = el; 
                                 break;
                             }}
                            if (!el) break;
                         }}
                         if (dateInp) break;
                     }}
                }}
                if (!dateInp) {{
                    dateInp = document.querySelector("input[type='date']");
                }}
                if (dateInp) {{
                    dateInp.value = '{formatted_date}';
                    dateInp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}
            """
            driver.execute_script(js_date_script)
            time.sleep(1)

        # 4. Click Search Button using JS Logic
        if _status_callback: _status_callback("🔍 検索を実行中(JS)...")
        
        js_search_script = """
            var btns = document.querySelectorAll('button, input[type="button"], a.btn');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].innerText.includes('検索') || btns[i].value === '検索') {
                    btns[i].click();
                    return true;
                }
            }
            return false;
        """
        driver.execute_script(js_search_script)
        time.sleep(3)

        # Wait for Facility List
        try:
            if _status_callback: _status_callback("⏳ 検索結果（施設リスト）の表示を待機中...")
            
            try:
                wait.until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//*[contains(text(), '室場') or contains(text(), '一覧') or contains(text(), '確認') or contains(text(), '市民センター')]")
                    )
                )
            except:
                 if _status_callback: _status_callback("⚠️ コンテキストロストの可能性。結果フレームを再探索します...")
                 switch_to_target_frame(driver, "室場一覧", _status_callback)
                 
            time.sleep(2) 

            # --- Debug Screenshot ---
            if _debug_placeholder:
                _debug_placeholder.image(driver.get_screenshot_as_png(), caption="検索結果表示確認", use_column_width=True)

        except Exception as e:
             if _debug_placeholder:
                 _debug_placeholder.image(driver.get_screenshot_as_png(), caption="エラー: 検索結果待機タイムアウト", use_column_width=True)
             if _status_callback: _status_callback("⚠️ 検索結果の表示待機中にタイムアウトしました。")
             raise Exception("Room list not found (Timeout)")

        # 5. Filter Results: SCOPED INTERACTION
        if _status_callback: _status_callback(f"📍 対象施設を捜索中 (Scoped Mode)...")
        
        target_urls = []
        found_facilities_log = []
        is_search_success = False
        
        if selected_facilities:
            # First, find ALL potential facility containers.
            # We assume a structure where facility name is in a header/label, and the "Room List" is nearby.
            # Strategy: Find the facility name element, then define that 'area' as the scope.
            
            for fac in selected_facilities:
                search_key = fac[:2]
                if not search_key: continue
                
                try:
                    # 1. SCOPE IDENTIFICATION
                    # Find facility header/label
                    # We look for something that contains the facility name.
                    # This finds ALL matches, we need to iterate to ensure we get the right one that HAS a Room List.
                    
                    if _status_callback: _status_callback(f"🔎 施設 '{fac}' (key:{search_key}) の親コンテナを特定中...")
                    
                    facility_headers = driver.find_elements(By.XPATH, f"//*[contains(text(), '{search_key}')]")
                    
                    target_container = None
                    for header in facility_headers:
                        # Heuristic: The header usually is inside a panel-heading or similar.
                        # We try to go up to a container.
                        try:
                            # Try to find a common ancestor that contains "室場"
                            # Or just work relative to the header.
                            # Let's try to find "Room List" relative to this header.
                            # axis: following
                            pass
                        except: continue

                    # Better Scoped Strategy:
                    # Iterate through match, define scope as the block between this header and the next one? 
                    # Easier: Use 'following' but limit search?
                    # No, Selenium 'following' goes to end of doc. 
                    # We need to find the container.
                    # Assumption: The layout is cards/panels.
                    # Let's try to find an ancestor 'div' or 'tr' that contains the header.
                    
                    # Implementation:
                    # Find matching header. Get its parent/ancestor.
                    # Check if that ancestor has "Room List" or "Gymnasium".
                    
                    # Let's try finding the header, then finding the NEAREST "Room List" toggle.
                    # xpath: (//header[contains(., scan_key)]/following::*[contains(., '室場一覧')])[1]
                    
                    xpath_header = f"(//*[contains(text(), '{search_key}') and (contains(text(), '市民センター') or contains(text(), '公民館'))])"
                    # If fuzzy logic is tricky, just use search_key
                    xpath_header = f"//*[contains(text(), '{search_key}')]"
                    
                    candidates = driver.find_elements(By.XPATH, xpath_header)
                    
                    for cand in candidates:
                         # Filter out tiny elements or script garbage
                         if not cand.is_displayed(): continue
                         
                         # Check if this candidate is actually a Facility Header
                         # (Check context)
                         # We'll assume the correct one will have "Room List" nearby.
                         
                         # 2. ACCORDION EXPANSION (SCOPED)
                         # Look for 'Room List' relative to this candidate
                         try:
                             # 'following::' selects everything after. We need 'descendant' or near sibling.
                             # If card structure: Header is sibling of Body.
                             # Body contains Room List.
                             
                             # Let's try to find the "Room List" button that is closest following this header.
                             room_list_toggle = cand.find_element(By.XPATH, "./following::*[contains(text(), '室場一覧') or contains(text(), 'Room List')][1]")
                             
                             # Scroll to it
                             driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", room_list_toggle)
                             time.sleep(0.5)
                             
                             # Expand
                             driver.execute_script("arguments[0].click();", room_list_toggle)
                             time.sleep(1) # Wait for animation
                             
                             # 3. FIND ROOM (SCOPED)
                             # Now look for Gymnasium specifically relative to this toggle (or the container it opened)
                             # Since we expanded it, the gymnasium row should be visible or in DOM following the toggle.
                             
                             # We find "Gymnasium" that is following the toggle, but BEFORE the next Room List?
                             # No, usually safe to just find "following::*[contains(., '体育室')][1]" relative to that toggle
                             # BUT we must be careful not to jump to next facility.
                             # We can check distance or hierarchy?
                             
                             # Let's assume the hierarchy is:
                             # Container
                             #   Header (cand)
                             #   Toggle (room_list_toggle)
                             #   Content (contains Gym)
                             
                             # So we search for Gym relative to Toggle.
                             gym_row = room_list_toggle.find_element(By.XPATH, "./following::*[contains(text(), '体育室')][1]")
                             
                             # 4. CLICK BUTTON (SCOPED)
                             # Find button inside/relative to gym_row
                             btn = gym_row.find_element(By.XPATH, "./following::*[contains(text(), '確認') or contains(text(), '予約')][1]")
                             
                             if btn:
                                 if _status_callback: _status_callback(f"🚀 SCOPED SUCCESS: '{fac}' のボタンを特定。")
                                 found_facilities_log.append(f"Scoped Click: {fac}")
                                 
                                 link_href = btn.get_attribute('href')
                                 if link_href:
                                     target_urls.append({"url": link_href, "raw_text": fac})
                                     is_search_success = True
                                     break # Done for this facility
                                 else:
                                     driver.execute_script("arguments[0].click();", btn)
                                     time.sleep(2)
                                     is_search_success = True 
                                     break
                                     
                         except Exception as inner_e:
                             # Not the right header or structure
                             continue
                             
                except Exception as e:
                    logger.warning(f"Scope search error for {fac}: {e}")
                    continue
        
        # Fallback Logic
        if not target_urls and not is_search_success:
             if _status_callback: _status_callback("⚠️ 指定施設のボタンが見つかりません(Scoped)。代替策を試行します...")
             # Just try to brute force any gym match
             try:
                 # Find ANY "Gymnasium" text then the button
                 fallback_gym = driver.find_element(By.XPATH, "(//*[contains(text(), '体育室')])[1]")
                 fallback_btn = fallback_gym.find_element(By.XPATH, "./following::*[contains(text(), '確認') or contains(text(), '予約')][1]")
                 
                 link_href = fallback_btn.get_attribute('href')
                 if link_href:
                     target_urls.append({"url": link_href, "raw_text": "Fallback Gym"})
                 else:
                     driver.execute_script("arguments[0].click();", fallback_btn)
                     pass
             except: pass

        if not target_urls and not is_search_success:
            if _status_callback: _status_callback("❌ 有効なリンクが一つも見つかりませんでした。")
            if _debug_placeholder:
                html_source = driver.execute_script("return document.body.innerHTML;")
                unique_key = f"debug_html_dump_attempt_{attempt_idx}_{int(time.time()*1000)}"
                _debug_placeholder.text_area("Debug: HTML Context Dump", html_source[:5000], height=300, key=unique_key)
            raise Exception("Brute force failed: No links found")

        # 6. Detail Loop (Calendar)
        if _debug_placeholder:
            _debug_placeholder.empty()
            _debug_placeholder.success("✅ ターゲット施設へ移動します。")

        total_targets = len(target_urls)

        for idx, target in enumerate(target_urls):
            url = target['url']
            raw_text = target['raw_text']
            
            if _progress_bar: _progress_bar.progress(idx / max(total_targets, 1))
            
            facility_name = raw_text
            room_name = "体育室"
            
            driver.get(url)
            time.sleep(2)
            
            found_context = switch_to_target_frame(driver, "予約状況", _status_callback)
            if not found_context:
                switch_to_target_frame(driver, "空", _status_callback) 

            try:
                 driver.execute_script("document.querySelectorAll('header, .alert, .announcement').forEach(e => e.remove());")
            except: pass

            if start_date:
                formatted_date = start_date.strftime("%Y-%m-%d")
                driver.execute_script(f"""
                    var inps = document.querySelectorAll("input[type='date'], input.datepicker");
                    inps.forEach(inp => {{
                        inp.value = '{formatted_date}';
                        inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }});
                """)
                time.sleep(1)

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

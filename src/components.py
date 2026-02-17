import streamlit as st

def render_schedule_card(row):
    """
    1件の予約データをスマホで見やすいカード形式で描画する
    """
    status = row['状況']
    
    # ステータスに応じた色設定
    if status == "○":
        border_color = "green"
        bg_color = "rgba(0, 128, 0, 0.1)"
        icon = "🟢"
    elif status == "△":
        border_color = "orange"
        bg_color = "rgba(255, 165, 0, 0.1)"
        icon = "BW" # Warning icon placeholder
        icon = "🟡"
    else:
        border_color = "gray"
        bg_color = "rgba(128, 128, 128, 0.1)"
        icon = "🔴"

    # コンテナでカード風表示
    # Streamlit 1.31+ の st.container(border=True) を使用
    with st.container(border=True):
        col1, col2 = st.columns([1, 4])
        
        with col1:
            st.markdown(f"<div style='text-align: center; font-size: 2em; line-height: 1.5;'>{status}</div>", unsafe_allow_html=True)
        
        with col2:
            st.caption(f"{row['日付']} ({get_weekday_ja(row['weekday'])})")
            st.markdown(f"**{row['時間']}**")
            st.text(row['施設名'])

def get_weekday_ja(weekday_num):
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    try:
        return weekdays[weekday_num]
    except:
        return ""

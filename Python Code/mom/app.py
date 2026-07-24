from datetime import datetime, timedelta
import io
import os
import random
import pandas as pd
import plotly.express as px
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
import streamlit as st

st.set_page_config(
    page_title='血液透析脫水監控系統', layout='wide'
)

# 注入自訂 CSS
st.markdown("""
<style>
[data-testid="stSidebar"] div.stButton > button {
    background-color: #2B6CB0 !important; 
    color: white !important;
    border: none !important;
}
[data-testid="stSidebar"] div.stButton > button:hover {
    background-color: #2C5282 !important;
    color: white !important;
}
div.stButton > button[kind="primary"] {
    background-color: #059669 !important;
    border-color: #059669 !important;
    color: white !important;
}
div.stButton > button[kind="primary"]:hover {
    background-color: #047857 !important;
    border-color: #047857 !important;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 繁體中文字型載入函式 (正確支援 Windows .ttc 集合字型)
# ==========================================
@st.cache_resource
def register_pdf_font():
    font_configs = [
        ("C:/Windows/Fonts/msjh.ttc", 0),   # 微軟正黑體 (Subfont Index 0)
        ("C:/Windows/Fonts/msjh.ttf", None),
        ("C:/Windows/Fonts/simsun.ttc", 0), # 新細明體
        ("C:/Windows/Fonts/arial.ttf", None)
    ]
    for font_path, sub_idx in font_configs:
        if os.path.exists(font_path):
            try:
                if sub_idx is not None:
                    pdfmetrics.registerFont(TTFont('NotoSansTC', font_path, subfontIndex=sub_idx))
                else:
                    pdfmetrics.registerFont(TTFont('NotoSansTC', font_path))
                return True
            except Exception:
                continue
    return False

has_cjk_font = register_pdf_font()

# ==========================================
# 核心計算引擎 (必須放在資料初始化後、報表生成前)
# ==========================================
def update_calculations(df, tol_mode, tol_val, includes_rinsing):
    if df.empty:
        return df
    
    numeric_cols = ['乾體重', '洗前體重', '洗後體重', '進食重量', '機器UF值', '沖水總重']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).round(2)

    weekday_map = {0: '週一', 1: '週二', 2: '週三', 3: '週四', 4: '週五', 5: '週六', 6: '週日'}
    df['日期_dt'] = pd.to_datetime(df['日期'], errors='coerce')
    df['週期'] = df['日期_dt'].dt.weekday.map(weekday_map).fillna('週一')
    df = df.drop(columns=['日期_dt'], errors='ignore')
    df['週期'] = df['週期'].astype(str)

    df['預期脫水'] = (df['洗前體重'] - df['乾體重']).round(2)
    df['實際脫水'] = (df['洗前體重'] - df['洗後體重'] + df['進食重量']).round(2)

    if not includes_rinsing:
        df['誤差值'] = (df['實際脫水'] - (df['預期脫水'] + df['沖水總重'])).round(2)
    else:
        df['誤差值'] = (df['實際脫水'] - df['預期脫水']).round(2)

    statuses = []
    for _, row in df.iterrows():
        target_uf = row['機器UF值'] 
        error_val = row['誤差值']
        is_abnormal = False
        
        if tol_mode == '嚴格模式 (零容忍：只要有誤差即異常)':
            if abs(error_val) > 0.0:
                is_abnormal = True
        elif tol_mode == 'IEC 60601-2-16 標準 (Max: 50g 或 1%)':
            threshold_kg = max(0.05, abs(target_uf) * 0.01)
            if abs(error_val) > threshold_kg: is_abnormal = True
        elif tol_mode == '固定重量 (g)':
            if abs(error_val * 1000) > tol_val: is_abnormal = True
        else: 
            threshold_kg = abs(target_uf) * (tol_val / 100)
            if abs(error_val) > threshold_kg: is_abnormal = True
            
        if is_abnormal:
            status = '🔴 異常 (多洗)' if error_val > 0 else '🔵 異常 (少洗)'
        else:
            status = '🟢 正常'
        statuses.append(status)
        
    df['狀態'] = statuses
    return df

# 1. 初始化 DataFrame
if 'data' not in st.session_state:
    st.session_state.data = pd.DataFrame(
        columns=[
            '日期', '週期', '班別', '乾體重', '洗前體重', '洗後體重', '進食重量',
            '機器UF值', '沖水總重', '預期脫水', '實際脫水', '誤差值', '狀態'
        ]
    )
    st.session_state.data['週期'] = st.session_state.data['週期'].astype(str)

# ==========================================
# 側邊欄設定 (提前獲取設定值以便計算)
# ==========================================
st.sidebar.header('⚙️ 系統全域設定')

tolerance_mode = st.sidebar.radio(
    '容許誤差判定模式', 
    [
        '嚴格模式 (零容忍：只要有誤差即異常)',
        'IEC 60601-2-16 標準 (Max: 50g 或 1%)', 
        '固定重量 (g)', 
        '預期脫水量的百分比 (%)'
    ]
)

tolerance_val = None
if tolerance_mode == '固定重量 (g)':
    tolerance_val = st.sidebar.slider(
        '自訂容許誤差值 (g)', min_value=50, max_value=500, value=150, step=50
    )
elif tolerance_mode == '預期脫水量的百分比 (%)':
    tolerance_val = st.sidebar.slider(
        '自訂容許誤差比例 (%)', min_value=0.5, max_value=5.0, value=1.0, step=0.5
    )

st.sidebar.markdown('---')
st.sidebar.header('🎛️ 臨床參數邏輯設定')
machine_uf_includes_rinsing = st.sidebar.checkbox(
    '機器 UF 值已包含沖水總重', 
    value=True,
    help='勾選：機器設定值已納入回血/沖水總量。\n不勾選：機器未設定沖水，系統將自動把沖水總重計入水分滯留與誤差修正。'
)

# 立即執行計算，確保資料狀態正確
st.session_state.data = update_calculations(st.session_state.data, tolerance_mode, tolerance_val, machine_uf_includes_rinsing)
abnormal_count = len(st.session_state.data[st.session_state.data['狀態'].str.contains('異常', na=False)])

# ==========================================
# PDF 報告生成函式 (移除 Emoji 並使用正確字型)
# ==========================================
def generate_pdf_report(df):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4,
        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    story = []
    
    styles = getSampleStyleSheet()
    font_name = 'NotoSansTC' if has_cjk_font else 'Helvetica'
    
    title_style = ParagraphStyle(
        'ReportTitle',
        parent=styles['Heading1'],
        fontName=font_name,
        fontSize=16,
        leading=20,
        alignment=1,
        textColor=colors.HexColor('#1A202C')
    )
    
    normal_style = ParagraphStyle(
        'ReportNormal',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#2D3748')
    )
    
    story.append(Paragraph("<b>血液透析脫水監控與 IEC 60601-2-16 檢核報告</b>", title_style))
    story.append(Spacer(1, 15))
    
    summary_text = f"<b>產出時間：</b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &nbsp;&nbsp;|&nbsp;&nbsp; <b>總記錄場次：</b>{len(df)} 筆"
    story.append(Paragraph(summary_text, normal_style))
    story.append(Spacer(1, 15))
    
    table_data = [['日期', '週期', '班別', '乾體重', '洗前', '洗後', '機器UF', '誤差值', '狀態']]
    for _, row in df.iterrows():
        clean_status = str(row['狀態']).replace('🔴', '').replace('🔵', '').replace('🟢', '').strip()
        table_data.append([
            str(row['日期']),
            str(row['週期']),
            str(row['班別']),
            str(row['乾體重']),
            str(row['洗前體重']),
            str(row['洗後體重']),
            str(row['機器UF值']),
            str(row['誤差值']),
            clean_status
        ])
        
    t = Table(table_data, colWidths=[65, 45, 45, 45, 45, 45, 50, 50, 75])
    
    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E2E8F0')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1A202C')),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
    ]
    
    for idx, row in df.iterrows():
        row_idx = idx + 1 
        status = str(row['狀態'])
        if '多洗' in status:
            t_style.append(('TEXTCOLOR', (8, row_idx), (8, row_idx), colors.HexColor('#DC2626')))
            t_style.append(('BACKGROUND', (8, row_idx), (8, row_idx), colors.HexColor('#FEE2E2')))
        elif '少洗' in status:
            t_style.append(('TEXTCOLOR', (8, row_idx), (8, row_idx), colors.HexColor('#7C3AED')))
            t_style.append(('BACKGROUND', (8, row_idx), (8, row_idx), colors.HexColor('#EDE9FE')))
        elif '正常' in status:
            t_style.append(('TEXTCOLOR', (8, row_idx), (8, row_idx), colors.HexColor('#059669')))
            t_style.append(('BACKGROUND', (8, row_idx), (8, row_idx), colors.HexColor('#D1FAE5')))
            
    t.setStyle(TableStyle(t_style))
    story.append(t)
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# ==========================================
# 精準控制的驗證資料生成器函式
# ==========================================
def generate_verified_test_data(total_count, over_count, under_count):
    normal_count = max(0, total_count - over_count - under_count)
    
    types = ['over'] * over_count + ['under'] * under_count + ['normal'] * normal_count
    random.shuffle(types)
    
    pattern = random.choice(['135', '246'])
    allowed_weekdays = [0, 2, 4] if pattern == '135' else [1, 3, 5]
    
    data = []
    current_date_dt = datetime.now() - timedelta(days=total_count * 2)
    while current_date_dt.weekday() not in allowed_weekdays:
        current_date_dt += timedelta(days=1)
        
    weekday_map = {0: '週一', 1: '週二', 2: '週三', 3: '週四', 4: '週五', 5: '週六', 6: '週日'}
    
    for i, t in enumerate(types):
        if i > 0:
            current_date_dt += timedelta(days=1)
            while current_date_dt.weekday() not in allowed_weekdays:
                current_date_dt += timedelta(days=1)
                
        current_date = current_date_dt.strftime('%Y-%m-%d')
        cycle = weekday_map[current_date_dt.weekday()]
        shift = random.choice(['早班', '中班', '晚班'])
        
        dry_wt = float(random.randint(55, 70))
        pre_wt = round(dry_wt + random.uniform(1.5, 3.5), 2)
        expected_uf = round(pre_wt - dry_wt, 2)
        machine_uf = expected_uf
        rinsing_wt = random.choice([0.3, 0.6])
        food_wt = random.choice([0.0, 0.0, 0.2, 0.3, 0.5])
        
        if t == 'over':
            target_actual_uf = round(expected_uf + random.uniform(0.08, 0.25), 2)
        elif t == 'under':
            target_actual_uf = round(expected_uf - random.uniform(0.08, 0.25), 2)
        else:
            target_actual_uf = round(expected_uf + random.uniform(-0.02, 0.02), 2)
            
        post_wt = round(pre_wt + food_wt - target_actual_uf, 2)
        
        data.append({
            '日期': current_date,
            '週期': cycle,
            '班別': shift,
            '乾體重': dry_wt,
            '洗前體重': pre_wt,
            '洗後體重': post_wt,
            '進食重量': food_wt,
            '機器UF值': machine_uf,
            '沖水總重': rinsing_wt,
            '預期脫水': 0.0,
            '實際脫水': 0.0,
            '誤差值': 0.0,
            '狀態': ''
        })
    df_res = pd.DataFrame(data)
    df_res['週期'] = df_res['週期'].astype(str)
    return df_res

# ==========================================
# 💾 長期資料與 PDF / JSON 報表管理
# ==========================================
st.sidebar.markdown('---')
st.sidebar.header('💾 長期資料與報表管理')

# PDF 下載按鈕
if not st.session_state.data.empty:
    pdf_bytes = generate_pdf_report(st.session_state.data)
    st.sidebar.download_button(
        label='📄 下載正式 PDF 檢核報告',
        data=pdf_bytes,
        file_name=f'dialysis_uf_report_{datetime.now().strftime("%Y%m%d")}.pdf',
        mime='application/pdf',
        use_container_width=True
    )

# 匯出 JSON 按鈕
if not st.session_state.data.empty:
    json_data = st.session_state.data.to_json(orient='records', force_ascii=False)
    st.sidebar.download_button(
        label='📥 匯出完整紀錄 (JSON)',
        data=json_data,
        file_name=f'dialysis_uf_records_{datetime.now().strftime("%Y%m%d")}.json',
        mime='application/json',
        use_container_width=True
    )

# 匯入 JSON 檔案上傳
uploaded_json = st.sidebar.file_uploader('📤 匯入紀錄 (JSON)', type=['json'])
if uploaded_json is not None:
    try:
        imported_df = pd.read_json(uploaded_json)
        required_cols = ['日期', '週期', '班別', '乾體重', '洗前體重', '洗後體重', '進食重量', '機器UF值', '沖水總重']
        if all(col in imported_df.columns for col in required_cols):
            st.session_state.data = imported_df
            st.sidebar.success('JSON 歷史紀錄已成功載入！')
            st.rerun()
        else:
            st.sidebar.error('JSON 檔案格式或欄位不符，請確認來源。')
    except Exception as e:
        st.sidebar.error(f'讀取 JSON 發生錯誤：{e}')

# ==========================================
# 🧪 自我驗證資料生成器控制項
# ==========================================
st.sidebar.markdown('---')
enable_validation_mode = st.sidebar.checkbox('🧪 啟用自我驗證資料產生器', value=False)

if enable_validation_mode:
    total_records = st.sidebar.number_input('總測試筆數', min_value=1, max_value=50, value=10, step=1)
    over_records = st.sidebar.number_input('多洗筆數 (偏高)', min_value=0, max_value=total_records, value=min(7, total_records), step=1)
    max_under = max(0, total_records - over_records)
    under_records = st.sidebar.number_input('少洗筆數 (偏低)', min_value=0, max_value=max_under, value=min(3, max_under), step=1)
    
    if st.sidebar.button('🎲 一鍵生成驗證數據', use_container_width=True):
        st.session_state.data = generate_verified_test_data(total_records, over_records, under_records)
        st.toast(f'已成功生成 {total_records} 筆規律週期驗證資料！', icon='🎲')
        st.rerun()

# ==========================================
# 0. 主標題與右上角動態警示標籤
# ==========================================
col_title, col_badge = st.columns([4, 1]) 
with col_title:
    st.title('🩸 血液透析脫水監控系統')
with col_badge:
    if abnormal_count > 0:
        st.markdown(
            f"""
            <div style="
                background-color: #FEE2E2; 
                color: #B91C1C; 
                padding: 8px 16px; 
                border-radius: 20px; 
                font-weight: bold; 
                text-align: center;
                margin-top: 25px;
                border: 1px solid #F87171;
                box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            ">
                🚨 警示：累積 {abnormal_count} 筆異常
            </div>
            """, 
            unsafe_allow_html=True
        )

st.markdown('---')

# ==========================================
# 1. 定義彈出式視窗 (Dialog) - 完整表單新增
# ==========================================
@st.dialog("📝 新增洗腎紀錄 (表單模式)")
def add_record_dialog():
    st.markdown("##### 📅 基本資訊")
    col1, col2 = st.columns(2)
    with col1:
        date_input = st.date_input('日期')
        shift_input = st.selectbox('班別', ['早班', '中班', '晚班'])
    with col2:
        weekday_map = {0: '週一', 1: '週二', 2: '週三', 3: '週四', 4: '週五', 5: '週六', 6: '週日'}
        calculated_cycle = weekday_map[date_input.weekday()]
        st.text_input('週期 (系統自動判定)', value=calculated_cycle, disabled=True)
        
        dry_wt = st.number_input('乾體重 (kg)', min_value=30.0, max_value=150.0, value=60.0, step=1.0, format="%.2f")

    st.write("") 
    
    st.markdown("##### ⚖️ 透析與體重數據")
    col3, col4 = st.columns(2)
    with col3:
        pre_wt = st.number_input('洗前體重 (kg)', min_value=30.0, max_value=150.0, value=float(dry_wt + 1.0), step=0.1, format="%.2f")
        post_wt = st.number_input('洗後體重 (kg)', min_value=30.0, max_value=150.0, value=float(dry_wt), step=0.1, format="%.2f")
        food_wt = st.number_input('進食重量 (kg)', min_value=0.0, max_value=2.0, value=0.0, step=0.1, format="%.2f")
    with col4:
        machine_uf = st.number_input('機器UF值 (L/kg)', min_value=0.0, max_value=10.0, value=5.0, step=0.1, format="%.2f")
        rinsing_wt = st.number_input('沖水/回血總重影響 (kg)', min_value=0.0, max_value=2.0, value=0.3, step=0.05, format="%.2f")

    st.write("") 
    st.info("💡 提示：儲存後，系統會自動依照日期與標準進行計算。")

    if st.button('儲存紀錄', type='primary', use_container_width=True):
        new_row = {
            '日期': str(date_input),
            '週期': calculated_cycle,
            '班別': shift_input,
            '乾體重': round(dry_wt, 2),
            '洗前體重': round(pre_wt, 2),
            '洗後體重': round(post_wt, 2),
            '進食重量': round(food_wt, 2),
            '機器UF值': round(machine_uf, 2),
            '沖水總重': round(rinsing_wt, 2),
            '預期脫水': 0.0,
            '實際脫水': 0.0,
            '誤差值': 0.0,
            '狀態': ''
        }
        st.session_state.data = pd.concat(
            [st.session_state.data, pd.DataFrame([new_row])], ignore_index=True
        )
        st.session_state.show_success_toast = True
        st.rerun() 

st.sidebar.markdown('---')
st.sidebar.header('📝 資料登錄')

if st.sidebar.button('➕ 點此開啟表單新增', use_container_width=True):
    add_record_dialog()

if st.session_state.pop('show_success_toast', False):
    st.toast('紀錄已成功新增！', icon='✅')

# TFDA 醫材規範與 IEC 標準說明與連結
st.sidebar.markdown('---')
with st.sidebar.expander('📖 TFDA 醫材規範與 IEC 標準說明', expanded=False):
    st.markdown("""
    **衛福部食藥署 (TFDA)** 在審查血液透析機時，要求設備符合 **IEC 60601-2-16** 標準，以確保病患安全。
    
    * **精準度規範 (UF Control)**：
      業界與標準最常採用的脫水誤差容許門檻為 **±50 cc 或 機器設定UF值的 ±1%**。
    * **相關標準參考**：
      可參閱 [IEC 60601-2-16 標準資訊](https://webstore.iec.ch/publication/2565) 了解詳細規範。
    """)

# ==========================================
# 欄位更名與順序對應對照表
# ==========================================
RENAME_MAP = {
    '機器UF值': '💧 UF | 機器UF值',
    '預期脫水': '💧 UF | 預期脫水',
    '沖水總重': '💧 UF | 沖水總重',
    '進食重量': '進食重量'
}
REVERSE_MAP = {v: k for k, v in RENAME_MAP.items()}

# ==========================================
# 3. 主畫面數據分析面板
# ==========================================
st.subheader('📊 數據分析檢視面板')

if st.session_state.data.empty:
    st.info('目前尚無資料，請從左側點擊「開啟表單新增」或啟用自我驗證資料產生器來建立測試數據。')
else:
    df = st.session_state.data.copy()

    view_option = st.radio(
        '選擇顯示視圖',
        ['全部', '異常數據', '資料分布圖'],
        horizontal=True,
    )

    column_order_list = [
        '日期', '週期', '班別', '乾體重', '洗前體重', '洗後體重', 
        RENAME_MAP['進食重量'],
        RENAME_MAP['機器UF值'], 
        RENAME_MAP['預期脫水'], 
        RENAME_MAP['沖水總重'], 
        '實際脫水', '誤差值', '狀態'
    ]

    if view_option == '全部':
        st.markdown('### 完整歷史紀錄')
        
        display_df = df.rename(columns=RENAME_MAP)[column_order_list].copy()
        display_df.insert(0, '項次', range(1, len(display_df) + 1))
        display_df['🗑️ 點選刪除'] = False
        
        st.info("💡 **提示**：請直接在下方表格的最右側勾選要刪除的場次，然後點擊「確認刪除已勾選項目」。")
        
        edited_df = st.data_editor(
            display_df,
            use_container_width=True,
            hide_index=True,
            disabled=[col for col in display_df.columns if col != '🗑️ 點選刪除'],
            key="main_data_editor"
        )
        
        col_btn1, col_btn2 = st.columns([8, 2])
        with col_btn2:
            if st.button("確認刪除已勾選項目", type="primary", use_container_width=True):
                if edited_df['🗑️ 點選刪除'].any():
                    keep_mask = ~edited_df['🗑️ 點選刪除']
                    cleaned_df = edited_df[keep_mask]
                    st.session_state.data = cleaned_df.drop(columns=['項次', '🗑️ 點選刪除'], errors='ignore').rename(columns=REVERSE_MAP)
                    st.session_state.data['週期'] = st.session_state.data['週期'].astype(str)
                    st.success("已成功刪除所選紀錄！")
                    st.rerun()
                else:
                    st.warning("請先在右側勾選要刪除的資料列！")

        st.write("")
        fig = px.line(
            df, 
            x='日期',
            y=['預期脫水', '實際脫水'],
            markers=True,
            title='預期脫水量 vs 實際脫水量趨勢圖',
        )
        fig.update_traces(selector=dict(name='預期脫水'), line=dict(color='#F59E0B', width=3))
        fig.update_traces(selector=dict(name='實際脫水'), line=dict(color='#38BDF8', width=3))
        st.plotly_chart(fig, use_container_width=True)

    elif view_option == '異常數據':
        st.markdown('### ⚠️ 超出容許誤差標準的異常紀錄')
        abnormal_df = df[df['狀態'].str.contains('異常')]
        if abnormal_df.empty:
            st.success('目前的紀錄中沒有發現異常數據的場次。')
        else:
            disp_abn_df = abnormal_df.rename(columns=RENAME_MAP)[column_order_list].copy()
            disp_abn_df.insert(0, '項次', range(1, len(disp_abn_df) + 1))
            disp_abn_df['🗑️ 點選刪除'] = False
            
            edited_abn_df = st.data_editor(
                disp_abn_df,
                use_container_width=True,
                hide_index=True,
                disabled=[col for col in disp_abn_df.columns if col != '🗑️ 點選刪除'],
                key="abnormal_data_editor"
            )
            
            col_abn_btn1, col_abn_btn2 = st.columns([8, 2])
            with col_abn_btn2:
                if st.button("確認刪除勾選的異常紀錄", type="primary", use_container_width=True):
                    if edited_abn_df['🗑️ 點選刪除'].any():
                        checked_indices = edited_abn_df[edited_abn_df['🗑️ 點選刪除']].index
                        original_indices_to_drop = abnormal_df.iloc[checked_indices].index
                        st.session_state.data = df.drop(index=original_indices_to_drop).reset_index(drop=True)
                        st.success("已成功刪除所選異常紀錄！")
                        st.rerun()
                    else:
                        st.warning("請先在右側勾選要刪除的資料列！")

            st.write("")
            fig_abnormal = px.bar(
                abnormal_df,
                x='日期',
                y='誤差值',
                color='狀態',
                title='異常場次誤差值分佈',
                text='誤差值',
                color_discrete_map={
                    '🔴 異常 (多洗)': '#DC2626',
                    '🔵 異常 (少洗)': '#7C3AED'
                }
            )
            st.plotly_chart(fig_abnormal, use_container_width=True)

    elif view_option == '資料分布圖':
        st.markdown('### 📊 脫水狀態資料分布圖與偏差分析')

        col1, col2 = st.columns(2)
        
        over_wash = len(df[df['狀態'].str.contains('多洗')])
        under_wash = len(df[df['狀態'].str.contains('少洗')])
        normal_count = len(df[df['狀態'].str.contains('正常')])

        with col1:
            st.metric(label='總記錄場次', value=len(df))
            st.metric(label='多洗次數 (偏高)', value=over_wash)
        with col2:
            st.metric(label='少洗次數 (偏低)', value=under_wash)
            st.metric(label='正常範圍場次', value=normal_count)

        fig_pie = px.pie(
            names=['多洗', '少洗', '正常'],
            values=[over_wash, under_wash, normal_count],
            title='脫水狀態資料分布比例',
            color_discrete_sequence=['#DC2626', '#7C3AED', '#059669'],
        )
        st.plotly_chart(fig_pie, use_container_width=True)

# ==========================================
# 4. 欄位定義與規範說明 (頁腳 Footer)
# ==========================================
st.markdown('---')
st.markdown('### 📖 欄位定義與系統規範說明')

col_def1, col_def2 = st.columns(2)
with col_def1:
    st.markdown("""
    ##### 📌 基礎體重數據
    * **乾體重 (Dry Weight)**：醫生評估設定目標體重。
    * **洗前體重**：本次透析療程前測量的實際體重。
    * **洗後體重**：本次透析療程結束後測量的實際體重。
    * **進食重量**：療程中病患額外攝取之食物或水分重量。
    """)
with col_def2:
    st.markdown("""
    ##### 📌 UF 脫水與運算邏輯
    * **機器UF值 (Total UF)**：透析機面板上設定的總脫水量。
    * **沖水總重**：療程中額外進入病患體內的總液體重量 (如生理食鹽水沖洗、回血等)。
    * **預期脫水**：由體重反推的理論需求脫水量 (`洗前體重` - `乾體重`)。
    * **實際脫水**：病患實際被抽走的水量 (`洗前體重` - `洗後體重` + `進食重量`)。
    * **誤差值**：實際脫水與預期脫水量的落差。當場次超出容許誤差範圍時，系統將標示為**異常數據**。
    """)
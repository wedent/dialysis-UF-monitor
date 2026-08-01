from datetime import datetime, timedelta
import io
import os
import random
import pandas as pd
import plotly.express as px
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.piecharts import Pie
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
# 繁體中文字型載入函式
# ==========================================
@st.cache_resource
def register_pdf_font():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    cloud_font_path = os.path.join(current_dir, 'NotoSansTC-Regular.ttf')

    font_configs = [
        (cloud_font_path, None),            
        ("C:/Windows/Fonts/msjh.ttc", 0),   
        ("C:/Windows/Fonts/msjh.ttf", None),
        ("C:/Windows/Fonts/simsun.ttc", 0), 
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
# 核心計算引擎
# ==========================================
def update_calculations(df, tol_mode, tol_val, includes_rinsing, iec_time_hr=4.0, iec_base_err=50):
    if df.empty:
        return df
    
    numeric_cols = ['乾體重', '洗前體重', '洗後體重', '進食重量', '機器UF值', '沖水總重', '輪椅重量']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).round(2)
        else:
            df[col] = 0.0

    if '含輪椅' not in df.columns:
        df['含輪椅'] = False

    weekday_map = {0: '週一', 1: '週二', 2: '週三', 3: '週四', 4: '週五', 5: '週六', 6: '週日'}
    df['日期_dt'] = pd.to_datetime(df['日期'], errors='coerce')
    df['週期'] = df['日期_dt'].dt.weekday.map(weekday_map).fillna('週一')
    df = df.drop(columns=['日期_dt'], errors='ignore')
    df['週期'] = df['週期'].astype(str)

    df['預期脫水'] = (df['洗前體重'] - df['乾體重']).round(2)
    
    effective_post_wt = df.apply(
        lambda row: row['洗後體重'] - row['輪椅重量'] if row.get('含輪椅', False) else row['洗後體重'],
        axis=1
    )
    
    df['實際脫水'] = (df['洗前體重'] - (effective_post_wt - df['進食重量'])).round(2)

    # 機器淨脫水量 (Baseline) = 機器設定的總UF - 沖水總重
    machine_net_uf = (df['機器UF值'] - df['沖水總重']).round(2)
    
    # 誤差值 = 實際脫水 - 機器的淨脫水目標
    df['誤差值'] = (df['實際脫水'] - machine_net_uf).round(2)

    statuses = []
    for _, row in df.iterrows():
        target_uf = row['機器UF值'] 
        error_val = row['誤差值']
        is_abnormal = False
        
        if tol_mode == '嚴格模式 (零容忍：只要有誤差即異常)':
            if abs(error_val) > 0.0:
                is_abnormal = True
        elif tol_mode == 'IEC 60601-2-16 標準 (時間基準 或 1%)':
            abs_limit_kg = (iec_base_err * iec_time_hr) / 1000.0
            threshold_kg = max(abs_limit_kg, abs(target_uf) * 0.01)
            if abs(error_val) > threshold_kg: is_abnormal = True
        elif tol_mode == '固定重量 (kg)':
            if abs(error_val) > tol_val: is_abnormal = True
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

# 初始化 Session State 變數
if 'data' not in st.session_state:
    st.session_state.data = pd.DataFrame(
        columns=[
            '日期', '週期', '班別', '乾體重', '洗前體重', '洗後體重', '進食重量',
            '機器UF值', '沖水總重', '含輪椅', '輪椅重量', '預期脫水', '實際脫水', '誤差值', '狀態'
        ]
    )
    st.session_state.data['週期'] = st.session_state.data['週期'].astype(str)
    st.session_state.data['含輪椅'] = False
    st.session_state.data['輪椅重量'] = 0.0

if 'active_view_tab' not in st.session_state:
    st.session_state.active_view_tab = '📋 全部紀錄'

# ==========================================
# 側邊欄設定
# ==========================================
st.sidebar.header('⚙️ 系統全域設定')

tolerance_mode = st.sidebar.radio(
    '容許誤差判定模式', 
    [
        '嚴格模式 (零容忍：只要有誤差即異常)',
        'IEC 60601-2-16 標準 (時間基準 或 1%)', 
        '固定重量 (kg)', 
        '預期脫水量的百分比 (%)'
    ]
)

tolerance_val = None
iec_time_hr = 4.0
iec_base_err = 50

if tolerance_mode == 'IEC 60601-2-16 標準 (時間基準 或 1%)':
    st.sidebar.markdown('**➤ IEC 參數設定**')
    iec_time_hr = st.sidebar.number_input('預設透析時間 (小時)', min_value=1.0, max_value=8.0, value=4.0, step=0.5, help="透析時間會直接影響總容許誤差值")
    iec_base_err = st.sidebar.number_input('每小時容許誤差 (g/hr)', min_value=10, max_value=100, value=50, step=10, help="例如：洗 4 小時 × 50g/hr = 200g 的絕對容許誤差")
elif tolerance_mode == '固定重量 (kg)':
    tolerance_val = st.sidebar.slider(
        '自訂容許誤差值 (kg)', min_value=0.1, max_value=2.0, value=0.2, step=0.1, format="%.1f"
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

st.session_state.data = update_calculations(st.session_state.data, tolerance_mode, tolerance_val, machine_uf_includes_rinsing, iec_time_hr, iec_base_err)
abnormal_count = len(st.session_state.data[st.session_state.data['狀態'].str.contains('異常', na=False)])

# ==========================================
# PDF 報告生成函式
# ==========================================
def generate_pdf_report(df):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=landscape(A4), 
        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    story = []
    
    styles = getSampleStyleSheet()
    font_name = 'NotoSansTC' if has_cjk_font else 'Helvetica'
    
    title_style = ParagraphStyle(
        'ReportTitle',
        parent=styles['Heading1'],
        fontName=font_name,
        fontSize=18,
        leading=22,
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
    story.append(Spacer(1, 10))
    
    summary_text = f"<b>產出時間：</b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &nbsp;&nbsp;|&nbsp;&nbsp; <b>總記錄場次：</b>{len(df)} 筆"
    story.append(Paragraph(summary_text, normal_style))
    story.append(Spacer(1, 10))

    over_count = len(df[df['狀態'].str.contains('多洗')])
    under_count = len(df[df['狀態'].str.contains('少洗')])
    normal_count = len(df[df['狀態'].str.contains('正常')])
    
    total = len(df) if len(df) > 0 else 1
    over_pct = (over_count / total) * 100
    under_pct = (under_count / total) * 100
    normal_pct = (normal_count / total) * 100

    dist_data = [
        ['分佈狀態', '場次統計', '佔比'],
        ['🟢 正常範圍', f'{normal_count} 筆', f'{normal_pct:.1f}%'],
        ['🔴 多洗 (偏高)', f'{over_count} 筆', f'{over_pct:.1f}%'],
        ['🔵 少洗 (偏低)', f'{under_count} 筆', f'{under_pct:.1f}%']
    ]
    dist_table = Table(dist_data, colWidths=[130, 100, 100])
    dist_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1A202C')),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))

    d = Drawing(120, 120)
    pc = Pie()
    pc.x = 10
    pc.y = 10
    pc.width = 100
    pc.height = 100
    pc.data = [
        normal_count if normal_count > 0 else 0.001,
        over_count if over_count > 0 else 0.001,
        under_count if under_count > 0 else 0.001
    ]
    pc.slices[0].fillColor = colors.HexColor('#059669') 
    pc.slices[1].fillColor = colors.HexColor('#DC2626') 
    pc.slices[2].fillColor = colors.HexColor('#7C3AED') 
    pc.labels = ['', '', ''] 
    d.add(pc)

    summary_layout = Table([[dist_table, d]], colWidths=[380, 140])
    summary_layout.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (1,0), (1,0), 'CENTER')
    ]))
    
    story.append(summary_layout)
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("<b>詳細歷史紀錄與檢核清單 (包含完整運算參數)</b>", normal_style))
    story.append(Spacer(1, 5))

    table_data = [['日期', '班別', '乾體重', '洗前', '洗後(秤)', '輪椅重', '進食', '預期脫水', '機器UF', '沖水', '實際脫水', '誤差值', '狀態']]
    for _, row in df.iterrows():
        clean_status = str(row['狀態']).replace('🔴', '').replace('🔵', '').replace('🟢', '').strip()
        wheelchair_str = f"{row.get('輪椅重量', 0.0)}" if row.get('含輪椅', False) else "0.0"
        
        table_data.append([
            str(row['日期']),
            str(row['班別']),
            str(row['乾體重']),
            str(row['洗前體重']),
            str(row['洗後體重']),
            wheelchair_str,
            str(row.get('進食重量', 0.0)),
            str(row.get('預期脫水', 0.0)),
            str(row['機器UF值']),
            str(row.get('沖水總重', 0.0)),
            str(row.get('實際脫水', 0.0)),
            str(row['誤差值']),
            clean_status
        ])
        
    t = Table(table_data, colWidths=[65, 35, 45, 45, 50, 45, 40, 55, 50, 40, 55, 45, 75])
    
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
            t_style.append(('TEXTCOLOR', (12, row_idx), (12, row_idx), colors.HexColor('#DC2626')))
            t_style.append(('BACKGROUND', (12, row_idx), (12, row_idx), colors.HexColor('#FEE2E2')))
        elif '少洗' in status:
            t_style.append(('TEXTCOLOR', (12, row_idx), (12, row_idx), colors.HexColor('#7C3AED')))
            t_style.append(('BACKGROUND', (12, row_idx), (12, row_idx), colors.HexColor('#EDE9FE')))
        elif '正常' in status:
            t_style.append(('TEXTCOLOR', (12, row_idx), (12, row_idx), colors.HexColor('#059669')))
            t_style.append(('BACKGROUND', (12, row_idx), (12, row_idx), colors.HexColor('#D1FAE5')))
            
    t.setStyle(TableStyle(t_style))
    story.append(t)
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# ==========================================
# 驗證資料生成器
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
        
        dry_wt = float(random.randint(65, 80)) 
        pre_wt = round(dry_wt + random.uniform(1.5, 3.5), 2)
        expected_uf = round(pre_wt - dry_wt, 2)
        
        rinsing_wt = random.choice([0.3, 0.6])
        food_wt = random.choice([0.0, 0.0, 0.2, 0.3, 0.5])
        
        machine_uf = round(expected_uf + rinsing_wt, 2)
        machine_net_uf = round(machine_uf - rinsing_wt, 2)
        
        if t == 'over':
            error_val = round(random.uniform(0.08, 0.25), 2)
        elif t == 'under':
            error_val = round(-random.uniform(0.08, 0.25), 2)
        else:
            error_val = round(random.uniform(-0.02, 0.02), 2)
            
        actual_dehydration = round(machine_net_uf + error_val, 2)
        post_wt = round(pre_wt - actual_dehydration + food_wt, 2)
        
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
            '含輪椅': False,
            '輪椅重量': 0.0,
            '預期脫水': 0.0,
            '實際脫水': 0.0,
            '誤差值': 0.0,
            '狀態': ''
        })
    df_res = pd.DataFrame(data)
    df_res['週期'] = df_res['週期'].astype(str)
    return df_res

# ==========================================
# 側邊欄：長期資料與報表管理
# ==========================================
st.sidebar.markdown('---')
st.sidebar.header('💾 長期資料與報表管理')

if not st.session_state.data.empty:
    pdf_bytes = generate_pdf_report(st.session_state.data)
    st.sidebar.download_button(
        label='📄 下載正式 PDF 檢核報告 (含圓餅圖與明細)',
        data=pdf_bytes,
        file_name=f'dialysis_uf_report_{datetime.now().strftime("%Y%m%d")}.pdf',
        mime='application/pdf',
        use_container_width=True
    )

if not st.session_state.data.empty:
    json_data = st.session_state.data.to_json(orient='records', force_ascii=False)
    st.sidebar.download_button(
        label='📥 匯出完整紀錄 (JSON)',
        data=json_data,
        file_name=f'dialysis_uf_records_{datetime.now().strftime("%Y%m%d")}.json',
        mime='application/json',
        use_container_width=True
    )

uploaded_json = st.sidebar.file_uploader('📤 匯入紀錄 (JSON)', type=['json'], key='json_file_uploader')
if uploaded_json is not None:
    # 避免重複讀取造成無限重載
    if st.session_state.get('last_uploaded_json_name') != uploaded_json.name:
        try:
            imported_df = pd.read_json(uploaded_json)
            required_cols = ['日期', '週期', '班別', '乾體重', '洗前體重', '洗後體重', '進食重量', '機器UF值', '沖水總重']
            if all(col in imported_df.columns for col in required_cols):
                st.session_state.data = imported_df
                if '含輪椅' not in st.session_state.data.columns:
                    st.session_state.data['含輪椅'] = False
                if '輪椅重量' not in st.session_state.data.columns:
                    st.session_state.data['輪椅重量'] = 0.0
                st.session_state.last_uploaded_json_name = uploaded_json.name
                st.session_state.active_view_tab = '📋 全部紀錄' # 匯入成功後，自動切換到全部紀錄！
                st.sidebar.success('JSON 歷史紀錄已成功載入！')
                st.rerun()
            else:
                st.sidebar.error('JSON 檔案格式或欄位不符，請確認來源。')
        except Exception as e:
            st.sidebar.error(f'讀取 JSON 發生錯誤：{e}')

st.sidebar.markdown('---')
enable_validation_mode = st.sidebar.checkbox('🧪 啟用自我驗證資料產生器', value=False)

if enable_validation_mode:
    total_records = st.sidebar.number_input('總測試筆數', min_value=1, max_value=50, value=10, step=1)
    over_records = st.sidebar.number_input('多洗筆數 (偏高)', min_value=0, max_value=total_records, value=min(7, total_records), step=1)
    max_under = max(0, total_records - over_records)
    under_records = st.sidebar.number_input('少洗筆數 (偏低)', min_value=0, max_value=max_under, value=min(3, max_under), step=1)
    
    if st.sidebar.button('🎲 一鍵生成驗證數據', use_container_width=True):
        st.session_state.data = generate_verified_test_data(total_records, over_records, under_records)
        st.session_state.active_view_tab = '📋 全部紀錄' # 生成數據後，自動切換到全部紀錄！
        st.toast(f'已成功生成 {total_records} 筆規律週期驗證資料！', icon='🎲')
        st.rerun()

# ==========================================
# TFDA 與 IEC 規範說明區塊
# ==========================================
st.sidebar.markdown('---')
with st.sidebar.expander('📖 TFDA 醫材規範與 IEC 標準說明', expanded=False):
    st.markdown("""
    **衛福部食藥署 (TFDA)** 在審查血液透析機時，要求設備符合 **IEC 60601-2-16** 標準，以確保病患安全。
    
    * **精準度規範 (UF Control)**：
      業界與標準最常採用的脫水誤差容許門檻為 **每小時 ±50 cc × 透析時間 或 機器設定UF值的 ±1% (兩者取其大)**。
    * **相關標準參考**：
      1. 國際標準：[IEC 60601-2-16 標準資訊](https://webstore.iec.ch/publication/2565)
      2. TFDA 仿單查詢：[醫療器材許可證資料庫查詢系統](https://lmspiq.fda.gov.tw/web/) *(可於此查詢各廠牌透析機之原廠技術規格與精準度宣告)*
    """)

RENAME_MAP = {
    '機器UF值': '💧 UF | 機器UF值',
    '預期脫水': '💧 UF | 預期脫水',
    '沖水總重': '💧 UF | 沖水總重',
    '進食重量': '進食重量',
    '洗後體重': '洗後(秤)',
    '含輪椅': '🦽 含輪椅',
    '輪椅重量': '輪椅重(kg)'
}
REVERSE_MAP = {v: k for k, v in RENAME_MAP.items()}
EDITABLE_INTERNAL_COLS = ['洗前體重', '洗後體重', '含輪椅', '輪椅重量', '進食重量', '機器UF值', '沖水總重']

# ==========================================
# 主標題與警示
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

if st.session_state.pop('show_success_toast', False):
    st.toast('紀錄已成功新增！', icon='✅')

# ==========================================
# 主畫面整合式視圖 (預設跳轉邏輯優化)
# ==========================================
tab_options = ['📋 全部紀錄', '📝 新增紀錄', '⚠️ 異常數據', '📊 資料分布圖']

# 確保 active_view_tab 在合法選項內
if st.session_state.get('active_view_tab') not in tab_options:
    st.session_state.active_view_tab = '📋 全部紀錄'

current_index = tab_options.index(st.session_state.active_view_tab)

view_option = st.radio(
    '操作與檢視面板',
    tab_options,
    index=current_index,
    horizontal=True,
    key='main_radio_selection'
)

# 使用者點擊切換時，同步更新至 session_state
st.session_state.active_view_tab = view_option

st.write("") 

df = st.session_state.data.copy()

if view_option == '📋 全部紀錄':
    if df.empty:
        st.info('目前尚無資料，請切換至「📝 新增紀錄」填寫表單，或從左側匯入 JSON / 生成測試資料。')
    else:
        st.markdown('### 📋 完整歷史紀錄')
        
        column_order_list = [
            '日期', '週期', '班別', '乾體重', '洗前體重', 
            RENAME_MAP['洗後體重'], 
            RENAME_MAP['含輪椅'],
            RENAME_MAP['輪椅重量'],
            RENAME_MAP['進食重量'],
            RENAME_MAP['機器UF值'], 
            RENAME_MAP['預期脫水'], 
            RENAME_MAP['沖水總重'], 
            '實際脫水', '誤差值', '狀態'
        ]
        
        display_df = df.rename(columns=RENAME_MAP)[column_order_list].copy()
        display_df.insert(0, '項次', range(1, len(display_df) + 1))
        display_df['🗑️ 點選刪除'] = False
        
        st.info("💡 **提示**：請直接在下方表格的最右側雙擊數值進行修改，修改後系統會「自動重新計算脫水狀態」！")
        
        edited_df = st.data_editor(
            display_df,
            use_container_width=True,
            hide_index=True,
            disabled=[col for col in display_df.columns if col not in ['洗前體重', RENAME_MAP['洗後體重'], RENAME_MAP['輪椅重量'], RENAME_MAP['進食重量'], RENAME_MAP['機器UF值'], RENAME_MAP['沖水總重'], '🗑️ 點選刪除', RENAME_MAP['含輪椅']]],
            key="main_data_editor"
        )
        
        # --- 自動連動計算邏輯 (全部視圖) ---
        edited_internal = edited_df.rename(columns=REVERSE_MAP)
        is_modified = False
        for col in EDITABLE_INTERNAL_COLS:
            if col == '含輪椅':
                if not edited_internal[col].equals(df[col]):
                    is_modified = True
                    break
            else:
                if not pd.to_numeric(edited_internal[col], errors='coerce').round(2).equals(
                       pd.to_numeric(df[col], errors='coerce').round(2)):
                    is_modified = True
                    break
                    
        if is_modified:
            for col in EDITABLE_INTERNAL_COLS:
                st.session_state.data[col] = edited_internal[col]
            st.rerun()
        # -------------------------------
        
        col_btn1, col_btn2 = st.columns([8, 2])
        with col_btn2:
            if st.button("確認刪除已勾選項目", type="primary", use_container_width=True):
                if edited_df['🗑️ 點選刪除'].any():
                    keep_mask = ~edited_df['🗑️ 點選刪除']
                    st.session_state.data = st.session_state.data[keep_mask].reset_index(drop=True)
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

elif view_option == '📝 新增紀錄':
    st.markdown("### 📝 新增洗腎紀錄")
    st.markdown("##### 📅 基本資訊")
    col1, col2 = st.columns(2)
    with col1:
        date_input = st.date_input('日期')
        shift_input = st.selectbox('班別', ['早班', '中班', '晚班'])
    with col2:
        weekday_map = {0: '週一', 1: '週二', 2: '週三', 3: '週四', 4: '週五', 5: '週六', 6: '週日'}
        calculated_cycle = weekday_map[date_input.weekday()]
        st.text_input('週期 (系統自動判定)', value=calculated_cycle, disabled=True)
        
        dry_wt = st.number_input('乾體重 (kg)', min_value=30.0, max_value=150.0, value=73.0, step=0.1, format="%.2f")

    st.write("") 
    st.markdown("##### ⚖️ 透析與體重數據")
    col3, col4 = st.columns(2)
    with col3:
        pre_wt = st.number_input('洗前體重 (kg)', min_value=30.0, max_value=150.0, value=float(dry_wt + 1.0), step=0.1, format="%.2f")
        post_wt = st.number_input('洗後秤重讀數 (kg)', min_value=30.0, max_value=150.0, value=float(dry_wt), step=0.1, format="%.2f", help="若連輪椅一起量測，請填寫磅秤顯示的總重量")
        food_wt = st.number_input('進食重量 (kg)', min_value=0.0, max_value=2.0, value=0.0, step=0.1, format="%.2f", help="透析中進食或補充水分之總重量")
    with col4:
        machine_uf = st.number_input('機器UF值 (L/kg)', min_value=0.0, max_value=10.0, value=5.0, step=0.1, format="%.2f")
        rinsing_wt = st.number_input('沖水/回血總重影響 (kg)', min_value=0.0, max_value=2.0, value=0.3, step=0.05, format="%.2f")

    st.write("")
    st.markdown("##### 🦽 輪椅重量校正 (選填)")
    include_wheelchair = st.checkbox('洗後體重包含輪椅重量', value=False, help='勾選後，系統會自動從洗後秤重讀數中扣除輪椅重量以計算真實脫水')
    wheelchair_wt = 0.0
    if include_wheelchair:
        wheelchair_wt = st.number_input('輪椅重量 (kg)', min_value=0.0, max_value=30.0, value=11.9, step=0.1, format="%.2f")

    st.write("") 
    st.info("💡 提示：儲存後，系統會自動跳轉至「📋 全部紀錄」展示新增的算式與結果。")

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
            '含輪椅': include_wheelchair,
            '輪椅重量': round(wheelchair_wt, 2),
            '預期脫水': 0.0,
            '實際脫水': 0.0,
            '誤差值': 0.0,
            '狀態': ''
        }
        st.session_state.data = pd.concat(
            [st.session_state.data, pd.DataFrame([new_row])], ignore_index=True
        )
        st.session_state.show_success_toast = True
        st.session_state.active_view_tab = '📋 全部紀錄' # 儲存成功後自動跳轉全部紀錄！
        st.rerun() 

elif view_option == '⚠️ 異常數據':
    if df.empty:
        st.info('目前尚無資料，請先新增紀錄或匯入資料。')
    else:
        st.markdown('### ⚠️ 超出容許誤差標準的異常紀錄')
        abnormal_df = df[df['狀態'].str.contains('異常')]
        if abnormal_df.empty:
            st.success('目前的紀錄中沒有發現異常數據的場次。')
        else:
            column_order_list = [
                '日期', '週期', '班別', '乾體重', '洗前體重', 
                RENAME_MAP['洗後體重'], 
                RENAME_MAP['含輪椅'],
                RENAME_MAP['輪椅重量'],
                RENAME_MAP['進食重量'],
                RENAME_MAP['機器UF值'], 
                RENAME_MAP['預期脫水'], 
                RENAME_MAP['沖水總重'], 
                '實際脫水', '誤差值', '狀態'
            ]
            
            disp_abn_df = abnormal_df.rename(columns=RENAME_MAP)[column_order_list].copy().reset_index(drop=True)
            disp_abn_df.insert(0, '項次', range(1, len(disp_abn_df) + 1))
            disp_abn_df['🗑️ 點選刪除'] = False
            
            st.info("💡 **提示**：直接在表格雙擊修改，修改後若數值落回正常範圍，該筆資料將會自動從異常清單中消失！")
            
            edited_abn_df = st.data_editor(
                disp_abn_df,
                use_container_width=True,
                hide_index=True,
                disabled=[col for col in disp_abn_df.columns if col not in ['洗前體重', RENAME_MAP['洗後體重'], RENAME_MAP['輪椅重量'], RENAME_MAP['進食重量'], RENAME_MAP['機器UF值'], RENAME_MAP['沖水總重'], '🗑️ 點選刪除', RENAME_MAP['含輪椅']]],
                key="abnormal_data_editor"
            )
            
            # --- 自動連動計算邏輯 (異常視圖) ---
            edited_internal_abn = edited_abn_df.rename(columns=REVERSE_MAP)
            abn_original_reset = abnormal_df.reset_index(drop=True)
            
            is_modified_abn = False
            for col in EDITABLE_INTERNAL_COLS:
                if col == '含輪椅':
                    if not edited_internal_abn[col].equals(abn_original_reset[col]):
                        is_modified_abn = True
                        break
                else:
                    if not pd.to_numeric(edited_internal_abn[col], errors='coerce').round(2).equals(
                           pd.to_numeric(abn_original_reset[col], errors='coerce').round(2)):
                        is_modified_abn = True
                        break
                        
            if is_modified_abn:
                original_indices = abnormal_df.index
                for i, orig_idx in enumerate(original_indices):
                    for col in EDITABLE_INTERNAL_COLS:
                        st.session_state.data.at[orig_idx, col] = edited_internal_abn.at[i, col]
                st.rerun()
            # -------------------------------
            
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

elif view_option == '📊 資料分布圖':
    if df.empty:
        st.info('目前尚無資料，請先新增紀錄或匯入資料。')
    else:
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

st.markdown('---')
st.markdown('### 📖 欄位定義與系統規範說明')

col_def1, col_def2 = st.columns(2)
with col_def1:
    st.markdown("""
    ##### 📌 基礎體重數據
    * **乾體重 (Dry Weight)**：醫生評估設定目標體重。
    * **洗前體重**：本次透析療程前測量的實際體重。
    * **洗後(秤)**：本次透析療程結束後磅秤上的實際讀數（若連輪椅測量會包含輪椅重）。
    * **輪椅重 / 含輪椅**：當洗後體重包含輪椅時，系統自動扣除以計算真實淨體重。
    * **進食重量**：療程中病患額外攝取之食物或水分重量。
    """)
with col_def2:
    st.markdown("""
    ##### 📌 UF 脫水與運算邏輯
    * **機器UF值 (Total UF)**：透析機面板上設定的總脫水量。
    * **沖水總重**：療程中額外進入病患體內的總液體重量 (如生理食鹽水沖洗、回血等)。
    * **預期脫水**：由體重反推的理論需求脫水量 (`洗前體重` - `乾體重`)。代表病人應該脫多少水。
    * **實際脫水**：病患實際被抽走的水量 (`洗前體重` - (`洗後秤重讀數` - `輪椅重量` - `進食重量`))。
    * **機器淨脫水**：機器實際對病患造成的淨脫水量 (`機器UF值` - `沖水總重`)。
    * **誤差值**：用來驗證機器準確度 (`實際脫水` - `機器淨脫水`)。當落差超出容許範圍時，標示為異常。
    """)
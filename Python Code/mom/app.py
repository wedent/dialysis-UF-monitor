import os
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 這裡填入你剛剛放在專案資料夾裡的字型檔名
# (例如: "NotoSansTC-Regular.ttf" 或者是你複製過來的微軟正黑體檔名)
font_filename = "NotoSansTC-Regular.ttf" 

if os.path.exists(font_filename):
    try:
        # 根據副檔名自動判斷註冊方式
        if font_filename.lower().endswith('.ttc'):
            pdfmetrics.registerFont(TTFont("CustomCJK", font_filename, subfontIndex=0))
        else:
            pdfmetrics.registerFont(TTFont("CustomCJK", font_filename))
        chinese_font_name = "CustomCJK"
    except Exception as e:
        chinese_font_name = "Helvetica"
else:
    # 如果雲端沒找到，本機 Windows 測試時的備用路徑
    win_font_path = "C:/Windows/Fonts/msjh.ttc"
    if os.path.exists(win_font_path):
        pdfmetrics.registerFont(TTFont("CustomCJK", win_font_path, subfontIndex=0))
        chinese_font_name = "CustomCJK"
    else:
        chinese_font_name = "Helvetica"

# 之後在製作 PDF 的 ParagraphStyle 或 TableStyle 時，字型名稱請統一使用 chinese_font_name
# 例如: styles.add(ParagraphStyle('MyStyle', fontName=chinese_font_name, ...))
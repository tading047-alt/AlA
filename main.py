# -*- coding: utf-8 -*-
"""تحليل البيانات وإرسال النتائج إلى Telegram (مع fpdf2)"""

import pandas as pd
import sqlite3
import os
import requests
from datetime import datetime
from fpdf import FPDF  # fpdf2 تستخدم نفس الاسم FPDF
import json

print("="*60)
print("📊 مشروع تحليل البيانات والإرسال إلى Telegram")
print("="*60)

# ============================================
# بيانات Telegram (أدخل بياناتك هنا)
# ============================================

TELEGRAM_BOT_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"  # ضع توكن البوت هنا
TELEGRAM_CHAT_ID = "5067771509"      # ضع معرف المحادثة هنا

# ============================================
# 1. تحديد مسار المجلد
# ============================================

folder_path = os.path.dirname(os.path.abspath(__file__))
if folder_path == "":
    folder_path = os.getcwd()

print(f"\n📁 مجلد العمل: {folder_path}")

# ============================================
# 2. إنشاء الملفات التجريبية
# ============================================

def create_sample_files(path):
    """إنشاء الملفات التجريبية الثلاثة"""
    
    db_file = os.path.join(path, 'sales.db')
    csv_file = os.path.join(path, 'sales_q2.csv')
    excel_file = os.path.join(path, 'sales_q3.xlsx')
    
    # إنشاء قاعدة البيانات
    if not os.path.exists(db_file):
        print("\n📝 إنشاء ملف قاعدة البيانات...")
        conn = sqlite3.connect(db_file)
        q1_data = pd.DataFrame({
            'id': [1, 2, 3, 4, 5],
            'product_name': ['لابتوب', 'ماوس', 'لوحة مفاتيح', 'شاشة', 'طابعة'],
            'quantity': [5, 20, 15, 8, 3],
            'price': [2500, 50, 150, 800, 600],
            'sale_date': ['2024-01-15', '2024-01-20', '2024-02-10', '2024-02-25', '2024-03-05'],
            'region': ['الرياض', 'جدة', 'الدمام', 'الرياض', 'جدة']
        })
        q1_data.to_sql('sales', conn, if_exists='replace', index=False)
        conn.close()
    
    # إنشاء ملف CSV
    if not os.path.exists(csv_file):
        print("📝 إنشاء ملف CSV...")
        q2_data = pd.DataFrame({
            'id': [6, 7, 8, 9, 10],
            'product_name': ['لابتوب', 'سماعة', 'ماوس', 'كاميرا', 'شاحن'],
            'quantity': [7, 25, 30, 4, 40],
            'price': [2400, 120, 45, 1500, 80],
            'sale_date': ['2024-04-12', '2024-04-18', '2024-05-05', '2024-05-20', '2024-06-15'],
            'region': ['الرياض', 'الدمام', 'جدة', 'الرياض', 'الخبر']
        })
        q2_data.to_csv(csv_file, index=False, encoding='utf-8-sig')
    
    # إنشاء ملف Excel
    if not os.path.exists(excel_file):
        print("📝 إنشاء ملف Excel...")
        q3_data = pd.DataFrame({
            'id': [11, 12, 13, 14, 15],
            'product_name': ['لابتوب', 'سماعة', 'طابعة', 'ماوس', 'لوحة مفاتيح'],
            'quantity': [6, 35, 5, 45, 20],
            'price': [2450, 110, 580, 48, 140],
            'sale_date': ['2024-07-10', '2024-07-25', '2024-08-15', '2024-08-30', '2024-09-05'],
            'region': ['جدة', 'الرياض', 'الدمام', 'الخبر', 'الرياض']
        })
        q3_data.to_excel(excel_file, index=False)
    
    print("✅ تم إنشاء الملفات التجريبية")
    return db_file, csv_file, excel_file

db_path, csv_path, excel_path = create_sample_files(folder_path)

# ============================================
# 3. تحميل البيانات وتحليلها
# ============================================
print("\n🔄 جاري تحميل وتحليل البيانات...")

conn = sqlite3.connect(db_path)
df_q1 = pd.read_sql_query("SELECT *, 'Q1' as quarter FROM sales;", conn)
conn.close()

df_q2 = pd.read_csv(csv_path, encoding='utf-8-sig')
df_q2['quarter'] = 'Q2'

df_q3 = pd.read_excel(excel_path, engine='openpyxl')
df_q3['quarter'] = 'Q3'

df_all = pd.concat([df_q1, df_q2, df_q3], ignore_index=True)
df_all['total_revenue'] = df_all['quantity'] * df_all['price']
df_all['sale_date'] = pd.to_datetime(df_all['sale_date'])

# إحصائيات
total_revenue = df_all['total_revenue'].sum()
total_quantity = df_all['quantity'].sum()
avg_price = df_all['price'].mean()
total_transactions = len(df_all)

top_products = df_all.groupby('product_name')['total_revenue'].sum().sort_values(ascending=False).head(5)
revenue_by_region = df_all.groupby('region')['total_revenue'].sum().sort_values(ascending=False)
quarterly = df_all.groupby('quarter')['total_revenue'].sum()

print(f"\n✅ إجمالي الإيرادات: {total_revenue:,.2f} ريال")
print(f"✅ إجمالي الكميات: {total_quantity:,} وحدة")

# ============================================
# 4. إنشاء التقرير النصي
# ============================================

def create_report_text():
    report = f"""
╔══════════════════════════════════════════════════════════════════╗
║                      📊 تقرير تحليل المبيعات                      ║
║                    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                    ║
╚══════════════════════════════════════════════════════════════════╝

📈 ملخص عام:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• إجمالي الإيرادات:          {total_revenue:,.2f} ريال
• إجمالي الكميات المباعة:    {total_quantity:,} وحدة
• متوسط سعر المنتج:          {avg_price:.2f} ريال
• عدد المعاملات:             {total_transactions} عملية

🏆 أفضل 5 منتجات:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    for i, (product, revenue) in enumerate(top_products.items(), 1):
        report += f"{i}. {product:<15} {revenue:>15,.2f} ريال\n"
    
    report += """
💰 الإيرادات حسب المنطقة:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    for region, revenue in revenue_by_region.items():
        report += f"• {region:<10} {revenue:>20,.2f} ريال\n"
    
    report += """
📅 الإيرادات حسب الربع:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    for quarter, revenue in quarterly.items():
        report += f"• الربع {quarter}          {revenue:>20,.2f} ريال\n"
    
    report += "\n✨ تم إنشاء هذا التقرير تلقائياً\n"
    return report

report_text = create_report_text()
print("\n" + report_text)

# ============================================
# 5. إنشاء ملف PDF (باستخدام fpdf2)
# ============================================

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, 'Sales Report - تقرير المبيعات', 0, 1, 'C')
        self.ln(10)
    
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

def create_pdf_report(text, output_path):
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)
    
    for line in text.split('\n'):
        # تنظيف النص من الرموز الخاصة
        clean_line = line.encode('utf-8', 'ignore').decode('utf-8')
        try:
            pdf.cell(0, 6, clean_line, 0, 1)
        except:
            pdf.cell(0, 6, " ", 0, 1)
    
    pdf.output(output_path)
    print(f"✅ تم إنشاء PDF: {output_path}")
    return output_path

pdf_file = os.path.join(folder_path, 'sales_report.pdf')
create_pdf_report(report_text, pdf_file)

# حفظ التقرير النصي
txt_file = os.path.join(folder_path, 'analysis_report.txt')
with open(txt_file, 'w', encoding='utf-8') as f:
    f.write(report_text)

# ============================================
# 6. إرسال إلى Telegram
# ============================================

def send_telegram_message(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except:
        return False

def send_telegram_document(bot_token, chat_id, file_path, caption=""):
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with open(file_path, 'rb') as file:
        files = {'document': file}
        data = {'chat_id': chat_id, 'caption': caption}
        try:
            response = requests.post(url, files=files, data=data)
            return response.status_code == 200
        except:
            return False

print("\n" + "="*60)
print("📤 جاري الإرسال إلى Telegram...")
print("="*60)

# إدخال التوكن
if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    TELEGRAM_BOT_TOKEN = input("أدخل توكن البوت: ").strip()

if TELEGRAM_CHAT_ID == "YOUR_CHAT_ID_HERE":
    TELEGRAM_CHAT_ID = input("أدخل معرف المحادثة (Chat ID): ").strip()

# إرسال الرسالة
if send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, report_text):
    print("✅ تم إرسال الرسالة النصية")
else:
    print("❌ فشل إرسال الرسالة")

# إرسال ملف PDF
if send_telegram_document(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, pdf_file, "📊 تقرير المبيعات"):
    print("✅ تم إرسال ملف PDF")
else:
    print("❌ فشل إرسال PDF")

print("\n🎉 اكتمل!")

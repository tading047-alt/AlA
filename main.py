# -*- coding: utf-8 -*-
"""تحليل البيانات وإرسال النتائج إلى Telegram (رسالة + PDF)"""

import pandas as pd
import sqlite3
import os
import requests
from datetime import datetime
from fpdf import FPDF
import json

print("="*60)
print("📊 مشروع تحليل البيانات والإرسال إلى Telegram")
print("="*60)

# ============================================
# بيانات Telegram (أدخل بياناتك هنا)
# ============================================

TELEGRAM_BOT_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"  # ⚠️ ضع توكن البوت هنا
TELEGRAM_CHAT_ID = "5067771509"      # ⚠️ ضع معرف المحادثة هنا

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
    
    files_created = []
    
    # إنشاء قاعدة البيانات
    if not os.path.exists(db_file):
        print("\n📝 إنشاء ملف قاعدة البيانات (الربع الأول)...")
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
        files_created.append('sales.db')
    
    # إنشاء ملف CSV
    if not os.path.exists(csv_file):
        print("📝 إنشاء ملف CSV (الربع الثاني)...")
        q2_data = pd.DataFrame({
            'id': [6, 7, 8, 9, 10],
            'product_name': ['لابتوب', 'سماعة', 'ماوس', 'كاميرا', 'شاحن'],
            'quantity': [7, 25, 30, 4, 40],
            'price': [2400, 120, 45, 1500, 80],
            'sale_date': ['2024-04-12', '2024-04-18', '2024-05-05', '2024-05-20', '2024-06-15'],
            'region': ['الرياض', 'الدمام', 'جدة', 'الرياض', 'الخبر']
        })
        q2_data.to_csv(csv_file, index=False, encoding='utf-8-sig')
        files_created.append('sales_q2.csv')
    
    # إنشاء ملف Excel
    if not os.path.exists(excel_file):
        print("📝 إنشاء ملف Excel (الربع الثالث)...")
        q3_data = pd.DataFrame({
            'id': [11, 12, 13, 14, 15],
            'product_name': ['لابتوب', 'سماعة', 'طابعة', 'ماوس', 'لوحة مفاتيح'],
            'quantity': [6, 35, 5, 45, 20],
            'price': [2450, 110, 580, 48, 140],
            'sale_date': ['2024-07-10', '2024-07-25', '2024-08-15', '2024-08-30', '2024-09-05'],
            'region': ['جدة', 'الرياض', 'الدمام', 'الخبر', 'الرياض']
        })
        q3_data.to_excel(excel_file, index=False)
        files_created.append('sales_q3.xlsx')
    
    if files_created:
        print(f"\n✅ تم إنشاء الملفات: {', '.join(files_created)}")
    
    return db_file, csv_file, excel_file

db_path, csv_path, excel_path = create_sample_files(folder_path)

# ============================================
# 3. تحميل البيانات من المصادر الثلاثة
# ============================================
print("\n" + "="*60)
print("🔄 جاري تحميل البيانات من المصادر الثلاثة...")
print("="*60)

# تحميل من قاعدة البيانات
conn = sqlite3.connect(db_path)
df_q1 = pd.read_sql_query("SELECT *, 'Q1' as quarter FROM sales;", conn)
conn.close()
print(f"\n✅ قاعدة البيانات (الربع الأول): {len(df_q1)} صف")

# تحميل من CSV
df_q2 = pd.read_csv(csv_path, encoding='utf-8-sig')
df_q2['quarter'] = 'Q2'
print(f"✅ ملف CSV (الربع الثاني): {len(df_q2)} صف")

# تحميل من Excel
df_q3 = pd.read_excel(excel_path, engine='openpyxl')
df_q3['quarter'] = 'Q3'
print(f"✅ ملف Excel (الربع الثالث): {len(df_q3)} صف")

# ============================================
# 4. دمج وتحليل البيانات
# ============================================
print("\n" + "="*60)
print("🔗 جاري دمج وتحليل البيانات...")
print("="*60)

# دمج جميع البيانات
df_all = pd.concat([df_q1, df_q2, df_q3], ignore_index=True)

# حساب الإيرادات
df_all['total_revenue'] = df_all['quantity'] * df_all['price']
df_all['sale_date'] = pd.to_datetime(df_all['sale_date'])

# إحصائيات عامة
total_revenue = df_all['total_revenue'].sum()
total_quantity = df_all['quantity'].sum()
avg_price = df_all['price'].mean()
total_transactions = len(df_all)

print(f"\n📊 إجمالي الإيرادات: {total_revenue:,.2f} ريال")
print(f"📦 إجمالي الكميات المباعة: {total_quantity:,} وحدة")
print(f"💰 متوسط سعر المنتج: {avg_price:.2f} ريال")
print(f"📋 عدد المعاملات: {total_transactions} عملية")

# أفضل المنتجات
top_products = df_all.groupby('product_name')['total_revenue'].sum().sort_values(ascending=False).head(5)

print("\n🏆 أفضل 5 منتجات من حيث الإيرادات:")
for i, (product, revenue) in enumerate(top_products.items(), 1):
    print(f"   {i}. {product}: {revenue:,.2f} ريال")

# الإيرادات حسب المنطقة
revenue_by_region = df_all.groupby('region')['total_revenue'].sum().sort_values(ascending=False)

print("\n💰 الإيرادات حسب المنطقة:")
for region, revenue in revenue_by_region.items():
    print(f"   • {region}: {revenue:,.2f} ريال")

# ============================================
# 5. إنشاء التقرير النصي
# ============================================
print("\n" + "="*60)
print("📝 جاري إنشاء التقرير...")
print("="*60)

def create_report_text():
    """إنشاء تقرير منسق للإرسال"""
    
    quarterly = df_all.groupby('quarter')['total_revenue'].sum()
    
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

🏆 أفضل 5 منتجات مبيعاً:
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
    
    report += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✨ تم إنشاء هذا التقرير تلقائياً بواسطة نظام التحليل الذكي
🤖 تم الإرسال من بوت Telegram
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    return report

# ============================================
# 6. إنشاء ملف PDF
# ============================================

class PDF(FPDF):
    def header(self):
        # عنوان في رأس كل صفحة
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, 'تقرير تحليل المبيعات', 0, 1, 'C')
        self.ln(10)
    
    def footer(self):
        # رقم الصفحة في تذييل كل صفحة
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'صفحة {self.page_no()}', 0, 0, 'C')

def create_pdf_report(report_text, output_path):
    """تحويل التقرير النصي إلى ملف PDF"""
    
    pdf = PDF()
    pdf.add_page()
    
    # إعداد الخط
    pdf.set_font("Arial", size=10)
    
    # تقسيم النص إلى سطور وإضافته
    for line in report_text.split('\n'):
        # ترميز النص العربي
        try:
            pdf.cell(0, 6, line.encode('latin-1', errors='ignore').decode('latin-1'), 0, 1)
        except:
            pdf.cell(0, 6, line, 0, 1)
    
    # حفظ ملف PDF
    pdf.output(output_path)
    print(f"✅ تم إنشاء ملف PDF: {output_path}")
    return output_path

# إنشاء التقرير
report_text = create_report_text()
print("\n" + report_text)

# إنشاء ملف PDF
pdf_file = os.path.join(folder_path, 'sales_report.pdf')
create_pdf_report(report_text, pdf_file)

# حفظ التقرير النصي أيضاً
txt_file = os.path.join(folder_path, 'analysis_report.txt')
with open(txt_file, 'w', encoding='utf-8') as f:
    f.write(report_text)
print(f"💾 تم حفظ التقرير النصي في: {txt_file}")

# حفظ البيانات المدمجة
merged_file = os.path.join(folder_path, 'merged_data.csv')
df_all.to_csv(merged_file, index=False, encoding='utf-8-sig')
print(f"💾 تم حفظ البيانات المدمجة في: {merged_file}")

# ============================================
# 7. إرسال إلى Telegram
# ============================================

def send_telegram_message(bot_token, chat_id, message):
    """إرسال رسالة نصية إلى Telegram"""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML'
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("✅ تم إرسال الرسالة النصية إلى Telegram")
            return True
        else:
            print(f"❌ فشل إرسال الرسالة: {response.text}")
            return False
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False

def send_telegram_document(bot_token, chat_id, file_path, caption=""):
    """إرسال ملف (PDF) إلى Telegram"""
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    
    with open(file_path, 'rb') as file:
        files = {'document': file}
        data = {'chat_id': chat_id, 'caption': caption}
        
        try:
            response = requests.post(url, files=files, data=data)
            if response.status_code == 200:
                print(f"✅ تم إرسال ملف PDF إلى Telegram")
                return True
            else:
                print(f"❌ فشل إرسال الملف: {response.text}")
                return False
        except Exception as e:
            print(f"❌ خطأ: {e}")
            return False

def get_chat_id(bot_token):
    """الحصول على معرف المحادثة (Chat ID) - طريقة مساعدة"""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if data['ok'] and data['result']:
                chat_id = data['result'][0]['message']['chat']['id']
                print(f"\n💡 تم العثور على Chat ID: {chat_id}")
                print("   يمكنك استخدام هذا المعرف في الكود")
                return chat_id
    except:
        pass
    return None

# ============================================
# 8. تنفيذ الإرسال إلى Telegram
# ============================================

print("\n" + "="*60)
print("📤 جاري الإرسال إلى Telegram...")
print("="*60)

# التحقق من وجود التوكن
if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    print("\n⚠️ لم تقم بإدخال توكن البوت!")
    print("   يرجى إدخال التوكن الآن:")
    TELEGRAM_BOT_TOKEN = input("أدخل توكن البوت: ").strip()
    
    # محاولة الحصول على Chat ID تلقائياً
    print("\n📡 جاري محاولة الحصول على Chat ID...")
    print("   تأكد من إرسال رسالة إلى البوت أولاً")
    chat_id = get_chat_id(TELEGRAM_BOT_TOKEN)
    if chat_id:
        TELEGRAM_CHAT_ID = chat_id

if TELEGRAM_CHAT_ID == "YOUR_CHAT_ID_HERE":
    TELEGRAM_CHAT_ID = input("أدخل معرف المحادثة (Chat ID): ").strip()

# إرسال رسالة نصية أولاً
print("\n📨 جاري إرسال الرسالة النصية...")
send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, report_text)

# إرسال ملف PDF
print("\n📎 جاري إرسال ملف PDF...")
send_telegram_document(
    TELEGRAM_BOT_TOKEN, 
    TELEGRAM_CHAT_ID, 
    pdf_file, 
    caption="📊 تقرير تحليل المبيعات - ملف PDF"
)

# إرسال ملف البيانات المدمجة (اختياري)
send_extra = input("\nهل تريد إرسال ملف البيانات المدمجة (CSV) أيضاً؟ (نعم/لا): ").strip().lower()
if send_extra in ['نعم', 'yes', 'y']:
    send_telegram_document(
        TELEGRAM_BOT_TOKEN, 
        TELEGRAM_CHAT_ID, 
        merged_file, 
        caption="📋 البيانات المدمجة (CSV)"
    )

# ============================================
# 9. عرض الملفات الناتجة
# ============================================
print("\n" + "="*60)
print("📂 الملفات التي تم إنشاؤها:")
print("="*60)

files_list = []
for file in os.listdir(folder_path):
    if file.endswith(('.db', '.csv', '.xlsx', '.txt', '.pdf')):
        file_path = os.path.join(folder_path, file)
        size = os.path.getsize(file_path)
        files_list.append(f"   📄 {file} ({size} bytes)")

if files_list:
    for f in files_list:
        print(f)

print("\n" + "="*60)
print("🎉 اكتمل التحليل والإرسال إلى Telegram!")
print("="*60)

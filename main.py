# -*- coding: utf-8 -*-
"""تحليل البيانات وإرسال النتائج"""

import pandas as pd
import sqlite3
import os
from google.colab import drive
import yagmail
import pywhatkit as kit
import requests
from datetime import datetime

print("="*60)
print("📊 مشروع تحليل البيانات والإرسال")
print("="*60)

# ============================================
# بياناتك
# ============================================

SENDER_EMAIL = "mouldi204@gmail.com"
APP_PASSWORD = "elabed0022"
RECEIVER_EMAIL = "mouldi204@gmail.com"
WHATSAPP_NUMBER = "+21629311722"

# ============================================
# 1. تحميل Google Drive
# ============================================
print("\n📁 جاري تحميل Google Drive...")
drive.mount('/content/drive')
print("✅ تم تحميل Google Drive بنجاح!")

# ============================================
# 2. تحديد مسار المجلد
# ============================================
folder_path = '/content/drive/MyDrive/sales_data/'
os.makedirs(folder_path, exist_ok=True)

# ============================================
# 3. إنشاء البيانات
# ============================================
def load_or_create_data(path):
    """تحميل البيانات من Drive أو إنشاؤها"""
    
    db_file = os.path.join(path, 'sales.db')
    csv_file = os.path.join(path, 'sales_q2.csv')
    excel_file = os.path.join(path, 'sales_q3.xlsx')
    
    # إنشاء الملفات إذا لم تكن موجودة
    if not os.path.exists(db_file):
        print("📝 إنشاء ملفات تجريبية...")
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
        
        q2_data = pd.DataFrame({
            'id': [6, 7, 8, 9, 10],
            'product_name': ['لابتوب', 'سماعة', 'ماوس', 'كاميرا', 'شاحن'],
            'quantity': [7, 25, 30, 4, 40],
            'price': [2400, 120, 45, 1500, 80],
            'sale_date': ['2024-04-12', '2024-04-18', '2024-05-05', '2024-05-20', '2024-06-15'],
            'region': ['الرياض', 'الدمام', 'جدة', 'الرياض', 'الخبر']
        })
        q2_data.to_csv(csv_file, index=False)
        
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

db_path, csv_path, excel_path = load_or_create_data(folder_path)

# ============================================
# 4. تحميل البيانات وتحليلها
# ============================================
print("\n🔄 جاري تحميل وتحليل البيانات...")

conn = sqlite3.connect(db_path)
df_q1 = pd.read_sql_query("SELECT *, 'Q1' as quarter FROM sales;", conn)
conn.close()

df_q2 = pd.read_csv(csv_path)
df_q2['quarter'] = 'Q2'

df_q3 = pd.read_excel(excel_path, engine='openpyxl')
df_q3['quarter'] = 'Q3'

df_all = pd.concat([df_q1, df_q2, df_q3], ignore_index=True)
df_all['total_revenue'] = df_all['quantity'] * df_all['price']
df_all['sale_date'] = pd.to_datetime(df_all['sale_date'])

# ============================================
# 5. إنشاء نص الرسالة
# ============================================
def create_message_text():
    """إنشاء نص الرسالة مع ملخص التحليل"""
    
    total_revenue = df_all['total_revenue'].sum()
    total_quantity = df_all['quantity'].sum()
    avg_price = df_all['price'].mean()
    total_transactions = len(df_all)
    
    top_products = df_all.groupby('product_name')['total_revenue'].sum().sort_values(ascending=False).head(3)
    revenue_by_region = df_all.groupby('region')['total_revenue'].sum().sort_values(ascending=False)
    quarterly = df_all.groupby('quarter')['total_revenue'].sum()
    
    message = f"""
╔══════════════════════════════════════════════════════╗
║           📊 تقرير تحليل المبيعات                    ║
║              {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}              ║
╚══════════════════════════════════════════════════════╝

📈 ملخص عام:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• إجمالي الإيرادات: {total_revenue:,.2f} ريال
• إجمالي الكميات المباعة: {total_quantity:,} وحدة
• متوسط سعر المنتج: {avg_price:.2f} ريال
• عدد المعاملات: {total_transactions} عملية

🏆 أفضل 3 منتجات:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    for i, (product, revenue) in enumerate(top_products.items(), 1):
        message += f"{i}. {product}: {revenue:,.2f} ريال\n"
    
    message += "\n💰 الإيرادات حسب المنطقة:\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for region, revenue in revenue_by_region.items():
        message += f"• {region}: {revenue:,.2f} ريال\n"
    
    message += "\n📅 الإيرادات حسب الربع:\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for quarter, revenue in quarterly.items():
        message += f"• الربع {quarter}: {revenue:,.2f} ريال\n"
    
    message += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✨ تم إنشاء هذا التقرير تلقائياً
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    return message

message_text = create_message_text()

print("\n" + "="*60)
print("📝 نص التقرير:")
print("="*60)
print(message_text)

# ============================================
# 6. إرسال التقارير
# ============================================
print("\n" + "="*60)
print("📤 جاري إرسال التقارير...")
print("="*60)

# إرسال إلى البريد
try:
    print("\n📧 جاري إرسال البريد...")
    yag = yagmail.SMTP(user=SENDER_EMAIL, password=APP_PASSWORD)
    yag.send(to=RECEIVER_EMAIL, subject="تقرير المبيعات", contents=message_text)
    print("✅ تم إرسال البريد")
except Exception as e:
    print(f"❌ خطأ في البريد: {e}")

# إرسال إلى واتساب
try:
    print("\n💬 جاري إرسال واتساب...")
    kit.sendwhatmsg_instantly(phone_no=WHATSAPP_NUMBER, message=message_text, wait_time=20)
    print("✅ تم فتح واتساب ويب")
except Exception as e:
    print(f"❌ خطأ في واتساب: {e}")

# حفظ نسخة
with open('analysis_report.txt', 'w', encoding='utf-8') as f:
    f.write(message_text)
print("\n💾 تم حفظ التقرير في: analysis_report.txt")

print("\n" + "="*60)
print("🎉 اكتمل التحليل!")
print("="*60)

# -*- coding: utf-8 -*-
"""تحليل البيانات وكتابة النتائج في ملف Excel ورفعها إلى GitHub"""

import pandas as pd
import sqlite3
import os
import base64
import json
import requests
from datetime import datetime
import tempfile

print("="*60)
print("📊 تحليل البيانات وإنشاء ملف resultat.xlsx")
print("="*60)

# ============================================
# إعدادات GitHub (أدخل بياناتك هنا)
# ============================================

# ⚠️ أدخل بيانات GitHub الخاصة بك:
GITHUB_TOKEN = "ghp_your_github_token_here"  # توكن GitHub
REPO_OWNER = "username"                       # اسم المستخدم
REPO_NAME = "repository-name"                 # اسم المستودع
BRANCH = "main"                               # اسم الفرع

# ============================================
# 1. قراءة البيانات من المصادر الثلاثة
# ============================================

def load_data():
    """تحميل البيانات من المصادر الثلاثة"""
    
    # بيانات قاعدة البيانات (ربع أول)
    print("\n📁 جاري تحميل البيانات...")
    
    # إنشاء قاعدة بيانات مؤقتة للاختبار
    conn = sqlite3.connect(':memory:')
    q1_data = pd.DataFrame({
        'id': [1, 2, 3, 4, 5],
        'product_name': ['لابتوب', 'ماوس', 'لوحة مفاتيح', 'شاشة', 'طابعة'],
        'quantity': [5, 20, 15, 8, 3],
        'price': [2500, 50, 150, 800, 600],
        'sale_date': ['2024-01-15', '2024-01-20', '2024-02-10', '2024-02-25', '2024-03-05'],
        'region': ['الرياض', 'جدة', 'الدمام', 'الرياض', 'جدة']
    })
    q1_data.to_sql('sales', conn, if_exists='replace', index=False)
    df_q1 = pd.read_sql_query("SELECT *, 'Q1' as quarter FROM sales;", conn)
    conn.close()
    
    # بيانات CSV (ربع ثاني)
    q2_data = pd.DataFrame({
        'id': [6, 7, 8, 9, 10],
        'product_name': ['لابتوب', 'سماعة', 'ماوس', 'كاميرا', 'شاحن'],
        'quantity': [7, 25, 30, 4, 40],
        'price': [2400, 120, 45, 1500, 80],
        'sale_date': ['2024-04-12', '2024-04-18', '2024-05-05', '2024-05-20', '2024-06-15'],
        'region': ['الرياض', 'الدمام', 'جدة', 'الرياض', 'الخبر']
    })
    df_q2 = q2_data.copy()
    df_q2['quarter'] = 'Q2'
    
    # بيانات Excel (ربع ثالث)
    q3_data = pd.DataFrame({
        'id': [11, 12, 13, 14, 15],
        'product_name': ['لابتوب', 'سماعة', 'طابعة', 'ماوس', 'لوحة مفاتيح'],
        'quantity': [6, 35, 5, 45, 20],
        'price': [2450, 110, 580, 48, 140],
        'sale_date': ['2024-07-10', '2024-07-25', '2024-08-15', '2024-08-30', '2024-09-05'],
        'region': ['جدة', 'الرياض', 'الدمام', 'الخبر', 'الرياض']
    })
    df_q3 = q3_data.copy()
    df_q3['quarter'] = 'Q3'
    
    # دمج جميع البيانات
    df_all = pd.concat([df_q1, df_q2, df_q3], ignore_index=True)
    df_all['total_revenue'] = df_all['quantity'] * df_all['price']
    df_all['sale_date'] = pd.to_datetime(df_all['sale_date'])
    
    print(f"✅ تم تحميل {len(df_all)} سجل")
    return df_all

# ============================================
# 2. إنشاء ملف Excel بالنتائج
# ============================================

def create_excel_report(df, output_path):
    """إنشاء ملف Excel متعدد الأوراق"""
    
    # حساب الإحصائيات
    total_revenue = df['total_revenue'].sum()
    total_quantity = df['quantity'].sum()
    avg_price = df['price'].mean()
    total_transactions = len(df)
    
    # أفضل المنتجات
    top_products = df.groupby('product_name').agg({
        'total_revenue': 'sum',
        'quantity': 'sum'
    }).sort_values('total_revenue', ascending=False).head(10)
    
    # الإيرادات حسب المنطقة
    revenue_by_region = df.groupby('region')['total_revenue'].sum().sort_values(ascending=False)
    
    # الإيرادات حسب الربع
    quarterly = df.groupby('quarter')['total_revenue'].sum()
    
    # المبيعات الشهرية
    df['month'] = df['sale_date'].dt.strftime('%Y-%m')
    monthly_sales = df.groupby('month')['total_revenue'].sum()
    
    # إحصائيات إضافية
    stats = pd.DataFrame({
        'المؤشر': ['إجمالي الإيرادات', 'إجمالي الكميات', 'متوسط السعر', 'عدد المعاملات', 'عدد المنتجات', 'تاريخ التقرير'],
        'القيمة': [
            f"{total_revenue:,.2f} ريال",
            f"{total_quantity:,}",
            f"{avg_price:.2f} ريال",
            f"{total_transactions:,}",
            f"{df['product_name'].nunique()}",
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ]
    })
    
    # إنشاء ملف Excel بأوراق متعددة
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # ورقة 1: البيانات الخام
        df.to_excel(writer, sheet_name='البيانات_الخام', index=False)
        
        # ورقة 2: أفضل المنتجات
        top_products.to_excel(writer, sheet_name='أفضل_المنتجات')
        
        # ورقة 3: الإيرادات حسب المنطقة
        revenue_by_region.to_excel(writer, sheet_name='الإيرادات_حسب_المنطقة')
        
        # ورقة 4: الإيرادات حسب الربع
        quarterly.to_excel(writer, sheet_name='الإيرادات_حسب_الربع')
        
        # ورقة 5: المبيعات الشهرية
        monthly_sales.to_excel(writer, sheet_name='المبيعات_الشهرية')
        
        # ورقة 6: إحصائيات عامة
        stats.to_excel(writer, sheet_name='إحصائيات_عامة', index=False)
    
    print(f"✅ تم إنشاء ملف Excel: {output_path}")
    return output_path

# ============================================
# 3. رفع الملف إلى GitHub
# ============================================

def upload_to_github(file_path, github_path, token, owner, repo, branch="main", commit_message=None):
    """رفع ملف إلى GitHub"""
    
    if commit_message is None:
        commit_message = f"إضافة {os.path.basename(file_path)} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    # قراءة محتوى الملف وتشفيره بـ Base64
    with open(file_path, 'rb') as file:
        content = base64.b64encode(file.read()).decode('utf-8')
    
    # إعداد الطلب
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{github_path}"
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    # التحقق من وجود الملف مسبقاً
    try:
        response = requests.get(url, headers=headers)
        sha = response.json().get('sha') if response.status_code == 200 else None
    except:
        sha = None
    
    # إعداد البيانات للإرسال
    data = {
        'message': commit_message,
        'content': content,
        'branch': branch
    }
    if sha:
        data['sha'] = sha
    
    # رفع الملف
    response = requests.put(url, headers=headers, json=data)
    
    if response.status_code in [200, 201]:
        print(f"✅ تم رفع الملف إلى GitHub: {github_path}")
        print(f"   🔗 رابط الملف: https://github.com/{owner}/{repo}/blob/{branch}/{github_path}")
        return True, response.json()
    else:
        print(f"❌ فشل رفع الملف: {response.status_code}")
        print(f"   {response.text}")
        return False, None

def get_github_folder_contents(token, owner, repo, folder_path, branch="main"):
    """الحصول على محتويات مجلد في GitHub"""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{folder_path}"
    headers = {'Authorization': f'token {token}'}
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    return []

def delete_github_file(file_path, token, owner, repo, branch="main", commit_message=None):
    """حذف ملف من GitHub"""
    if commit_message is None:
        commit_message = f"حذف {os.path.basename(file_path)}"
    
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    # الحصول على sha للملف
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"❌ الملف غير موجود: {file_path}")
        return False
    
    sha = response.json().get('sha')
    
    # حذف الملف
    data = {
        'message': commit_message,
        'sha': sha,
        'branch': branch
    }
    
    response = requests.delete(url, headers=headers, json=data)
    if response.status_code == 200:
        print(f"✅ تم حذف الملف من GitHub: {file_path}")
        return True
    else:
        print(f"❌ فشل حذف الملف: {response.status_code}")
        return False

# ============================================
# 4. إنشاء مجلد output في GitHub إن لم يكن موجوداً
# ============================================

def ensure_folder_exists(token, owner, repo, folder_path, branch="main"):
    """التأكد من وجود المجلد في GitHub (بإنشاء ملف .gitkeep)"""
    
    # التحقق من وجود المجلد
    contents = get_github_folder_contents(token, owner, repo, folder_path, branch)
    
    if not contents:
        # إنشاء ملف .gitkeep لإنشاء المجلد
        gitkeep_path = f"{folder_path}/.gitkeep"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.gitkeep', delete=False) as f:
            f.write("# هذا الملف يضمن وجود المجلد في GitHub")
            temp_file = f.name
        
        with open(temp_file, 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')
        
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{gitkeep_path}"
        headers = {'Authorization': f'token {token}'}
        data = {
            'message': f'إنشاء مجلد {folder_path}',
            'content': content,
            'branch': branch
        }
        
        response = requests.put(url, headers=headers, json=data)
        os.unlink(temp_file)
        
        if response.status_code in [200, 201]:
            print(f"✅ تم إنشاء مجلد {folder_path} على GitHub")
            return True
        else:
            print(f"⚠️ لا يمكن إنشاء المجلد: {response.status_code}")
            return False
    
    print(f"✅ مجلد {folder_path} موجود مسبقاً")
    return True

# ============================================
# 5. التنفيذ الرئيسي
# ============================================

def main():
    # تحميل البيانات
    df = load_data()
    
    # إنشاء ملف Excel مؤقت
    temp_dir = tempfile.gettempdir()
    output_file = os.path.join(temp_dir, 'resultat.xlsx')
    
    # إنشاء التقرير
    create_excel_report(df, output_file)
    
    # عرض ملخص النتائج
    print("\n" + "="*60)
    print("📊 ملخص النتائج:")
    print("="*60)
    
    total_revenue = df['total_revenue'].sum()
    total_quantity = df['quantity'].sum()
    print(f"💰 إجمالي الإيرادات: {total_revenue:,.2f} ريال")
    print(f"📦 إجمالي الكميات: {total_quantity:,} وحدة")
    print(f"🏆 أفضل منتج: {df.groupby('product_name')['total_revenue'].sum().idxmax()}")
    print(f"📍 أكثر منطقة: {df.groupby('region')['total_revenue'].sum().idxmax()}")
    
    # رفع إلى GitHub
    print("\n" + "="*60)
    print("📤 جاري رفع الملف إلى GitHub...")
    print("="*60)
    
    # التحقق من إعدادات GitHub
    if GITHUB_TOKEN == "ghp_your_github_token_here":
        print("\n⚠️ لم تقم بإدخال بيانات GitHub!")
        GITHUB_TOKEN = input("أدخل GitHub Token: ").strip()
        REPO_OWNER = input("أدخل اسم المستخدم (Owner): ").strip()
        REPO_NAME = input("أدخل اسم المستودع (Repository): ").strip()
    
    # إنشاء مجلد output
    folder_success = ensure_folder_exists(GITHUB_TOKEN, REPO_OWNER, REPO_NAME, "output")
    
    # رفع الملف
    github_path = "output/resultat.xlsx"
    success, result = upload_to_github(
        output_file,
        github_path,
        GITHUB_TOKEN,
        REPO_OWNER,
        REPO_NAME
    )
    
    if success:
        print("\n" + "="*60)
        print("🎉 اكتمل بنجاح!")
        print("="*60)
        print(f"\n📁 رابط المجلد: https://github.com/{REPO_OWNER}/{REPO_NAME}/tree/main/output")
        print(f"📄 رابط الملف: https://github.com/{REPO_OWNER}/{REPO_NAME}/blob/main/output/resultat.xlsx")
    else:
        print("\n❌ فشل الرفع إلى GitHub")
        print("\n💡 تأكد من:")
        print("   1. التوكن صحيح ولديه صلاحيات 'repo'")
        print("   2. اسم المستخدم والمستودع صحيحين")
        print("   3. لديك اتصال بالإنترنت")
    
    # تنظيف الملف المؤقت
    os.unlink(output_file)

if __name__ == "__main__":
    main()

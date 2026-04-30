import pandas as pd
import os
import random
from datetime import datetime, timedelta

def run_automation():
    print("🚀 بدء معالجة البيانات وتحديث ملف resultat.xlsx...")
    
    # التأكد من وجود مجلد المخرجات
    output_dir = 'output'
    os.makedirs(output_dir, exist_ok=True)

    # --- 1. توليد بيانات تجريبية ---
    products = ['Laptop Pro', 'Smartphone X', 'Tablet Air', 'Monitor 4K', 'Headset']
    data = []
    start_date = datetime(2026, 1, 1)
    
    for i in range(100):
        qty = random.randint(1, 30)
        price = random.randint(50, 1500)
        sale_date = start_date + timedelta(days=random.randint(0, 60))
        
        data.append({
            'Date': sale_date.strftime('%Y-%m-%d'),
            'Product': random.choice(products),
            'Quantity': qty,
            'Price': price,
            'Total': qty * price
        })
    
    df = pd.DataFrame(data)

    # --- 2. تصدير النتائج إلى ملف محدد الاسم ---
    # تم تعديل المسار ليصبح resultat.xlsx داخل مجلد output
    file_path = os.path.join(output_dir, 'resultat.xlsx')
    
    # حفظ الملف
    df.to_excel(file_path, index=False)

    print("-" * 40)
    print(f"✅ تم بنجاح معالجة {len(df)} سجل.")
    print(f"📂 الملف جاهز الآن في: {file_path}")
    print("-" * 40)

if __name__ == "__main__":
    run_automation()

import pandas as pd
import os

def run_task():
    print("🚀 بدء عملية توليد البيانات وتسجيلها...")
    
    # التأكد من وجود مجلد المخرجات
    output_dir = 'output'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"✅ تم إنشاء مجلد: {output_dir}")

    # 1. توليد معطيات (بيانات مبيعات تجريبية)
    data = {
        'ID': [101, 102, 103, 104, 105],
        'Product': ['Laptop', 'Smartphone', 'Tablet', 'Monitor', 'Keyboard'],
        'Quantity': [5, 12, 8, 10, 25],
        'Price_USD': [1200, 800, 450, 300, 50]
    }
    
    df = pd.DataFrame(data)
    
    # 2. إضافة عملية حسابية (إجمالي المبيعات)
    df['Total_Value'] = df['Quantity'] * df['Price_USD']
    
    # 3. تحديد مسار ملف الإكسيل وحفظه
    file_name = 'generated_data.xlsx'
    file_path = os.path.join(output_dir, file_name)
    
    # حفظ الملف بصيغة xlsx
    df.to_excel(file_path, index=False)
    
    print(f"📊 تم تسجيل {len(df)} سجل في الملف: {file_path}")
    print("✅ اكتملت المهمة بنجاح!")

if __name__ == "__main__":
    run_task()

import pandas as pd
import os
import random
from datetime import datetime, timedelta

def run_pro_automation():
    print("🚀 بدء محرك تحليل البيانات المتقدم (نسخة 2026)...")
    
    output_dir = 'output'
    os.makedirs(output_dir, exist_ok=True)

    # --- 1. توليد بيانات ذكية مع تواريخ ---
    products = ['Laptop Pro', 'Smartphone X', 'Tablet Air', 'Monitor 4K', 'Headset']
    data = []
    
    start_date = datetime(2026, 1, 1)
    
    for i in range(150): # زيادة العدد لـ 150 سجل
        qty = random.randint(1, 30)
        price = random.randint(50, 2000)
        sale_date = start_date + timedelta(days=random.randint(0, 90))
        
        data.append({
            'Date': sale_date.strftime('%Y-%m-%d'),
            'Product': random.choice(products),
            'Quantity': qty,
            'Unit_Price': price,
            'Total_Sales': qty * price
        })
    
    df = pd.DataFrame(data)

    # --- 2. ميزة جديدة: تصنيف الأداء (Performance Logic) ---
    # إذا كانت المبيعات أكبر من 10,000 $ يعتبر الأداء ممتاز
    def classify_performance(sales):
        if sales > 15000: return '🌟 Excellent'
        elif sales > 5000: return '✅ Good'
        else: return '⚠️ Low'

    # تجميع البيانات حسب المنتج
    summary = df.groupby('Product')['Total_Sales'].sum().reset_index()
    summary['Performance'] = summary['Total_Sales'].apply(classify_performance)

    # --- 3. تصدير التقارير بصيغ متعددة ---
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    
    # حفظ التقرير التفصيلي
    excel_file = f"{output_dir}/detailed_report_{timestamp}.xlsx"
    df.to_excel(excel_file, index=False)
    
    # حفظ ملخص الأداء
    summary_file = f"{output_dir}/performance_summary.csv"
    summary.to_csv(summary_file, index=False)

    print("-" * 45)
    print(f"✅ تم تحليل {len(df)} عملية بيع.")
    print(f"📊 ملخص الأداء تم طباعته في: {summary_file}")
    print(f"📑 التقرير التفصيلي جاهز للتحميل: {excel_file}")
    print("-" * 45)
    print(summary.to_string(index=False))

if __name__ == "__main__":
    run_pro_automation()

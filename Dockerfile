# استخدام نسخة خفيفة من بايثون 3.10
FROM python:3.10-slim

# تحديد مجلد العمل داخل الحاوية
WORKDIR /app

# تثبيت المكتبات اللازمة مباشرة (Pandas ومحرك الإكسيل)
RUN pip install --no-cache-dir pandas openpyxl

# نسخ ملف الكود فقط (لأننا نولد البيانات داخلياً)
COPY main.py .

# إنشاء مجلد المخرجات داخل الحاوية
RUN mkdir -p output

# تشغيل الكود بمجرد بدء الحاوية
CMD ["python", "main.py"]

# Dockerfile صحيح
FROM python:3.9-slim

# تثبيت dependencies النظام
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# تعيين مجلد العمل
WORKDIR /app

# نسخ requirements.txt أولاً (للاستفادة من cache)
COPY requirements.txt .

# تثبيت المكتبات
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي الملفات
COPY . .

# تشغيل التطبيق
CMD ["python", "main.py"]

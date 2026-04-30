FROM python:3.9-slim

WORKDIR /app

# تثبيت المكتبات المطلوبة
RUN pip install --no-cache-dir pandas openpyxl yagmail pywhatkit requests

# نسخ الكود
COPY main.py .
COPY requirements.txt .

# تشغيل الكود
CMD ["python", "main.py"]

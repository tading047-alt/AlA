#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بوت تجريبي مبسط - للتأكد من عمل تليجرام
"""

import asyncio
import httpx
from datetime import datetime

TOKEN = "8738851163:AAEe7YI7p05xSxsRSruu34taIaUk47aHCQY"
CHAT_ID = "5067771509"

async def send_test():
    # أولاً: جلب التحديثات لمعرفة Chat ID الصحيح
    print("جلب التحديثات...")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates")
        data = response.json()
        
        if data.get("ok"):
            updates = data.get("result", [])
            print(f"وجدت {len(updates)} تحديث")
            
            correct_chat_id = None
            for update in updates:
                if "message" in update:
                    chat = update["message"]["chat"]
                    correct_chat_id = str(chat["id"])
                    print(f"Chat ID الصحيح: {correct_chat_id} (نوع: {chat['type']})")
                    break
            
            # استخدام Chat ID الصحيح
            use_chat_id = correct_chat_id or CHAT_ID
            print(f"\nاستخدام Chat ID: {use_chat_id}")
            
            # إرسال رسالة
            msg = f"✅ *تم الاتصال بنجاح!*\n🕐 {datetime.now().strftime('%H:%M:%S')}"
            
            send_response = await client.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": use_chat_id, "text": msg, "parse_mode": "Markdown"}
            )
            
            send_data = send_response.json()
            if send_data.get("ok"):
                print("✅✅✅ تم إرسال الرسالة بنجاح!")
                print("تحقق من تليجرام الآن!")
            else:
                print(f"فشل الإرسال: {send_data}")
        else:
            print(f"فشل جلب التحديثات: {data}")

if __name__ == "__main__":
    asyncio.run(send_test())

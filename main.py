import asyncio
import ccxt.async_support as ccxt_async
import ccxt
import pandas as pd
import json
import os
import csv
import threading
import httpx
from flask import Flask, send_file
from datetime import datetime

# =========================================================
# 1. الإعدادات والروابط
# =========================================================
TELEGRAM_TOKEN = "8439548325:AAHOBBHy7EwcX3J5neIaf6iJuSjyGJCuZ68"
PUBLIC_CHAT_ID = "-1003692815602"
BOT_TAG = "#trading_100"
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")

LOG_TRADES = "paper_trading_results.csv"
STATE_FILE = "bot_state.json" # لضمان عدم ضياع الرصيد والصفقات
FEE_RATE = 0.002 

# =========================================================
# 2. إدارة الذاكرة الدائمة (Persistence Logic)
# =========================================================
def save_state(balance, positions):
    """حفظ حالة البوت في ملف صلب"""
    state = {
        "current_balance": balance,
        "active_positions": positions
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def load_state():
    """استعادة الحالة عند إعادة التشغيل"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            return state.get("current_balance", 1000.0), state.get("active_positions", {})
    return 1000.0, {}

# تحميل البيانات عند بدء تشغيل الكود
current_balance, active_positions = load_state()

# =========================================================
# 3. محرك التداول المطور بالتعافي الذاتي
# =========================================================
async def trading_engine():
    global current_balance, active_positions
    ex_async = ccxt_async.gateio({'enableRateLimit': True})
    ex_sync = ccxt.gateio({'enableRateLimit': True})
    
    await send_tg(f"🛡️ **نظام التعافي الذاتي نشط** {BOT_TAG}\n💰 الرصيد الحالي: {current_balance:.2f}$\n📦 صفقات مستعادة: {len(active_positions)}")

    while True:
        try:
            btc = await ex_async.fetch_ticker('BTC/USDT')
            btc_p = btc['percentage']
            
            # --- إدارة الخروج ---
            to_remove = []
            state_changed = False
            
            for sym, trade in active_positions.items():
                t = await ex_async.fetch_ticker(sym)
                cp = t['last']
                gross_pft = ((cp - trade['entry']) / trade['entry']) * 100
                
                exit_now, reason = False, ""
                if cp >= trade['tp']: exit_now, reason = True, "🎯 Target"
                elif cp <= trade['sl']: exit_now, reason = True, "🛑 Stop"
                
                if exit_now:
                    fees = trade['allocated_amount'] * FEE_RATE
                    net_profit = (trade['allocated_amount'] * (gross_pft / 100)) - fees
                    current_balance += net_profit
                    
                    log_to_csv({
                        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
                        'sym': sym, 'net_pft': round((net_profit/trade['allocated_amount'])*100, 2),
                        'balance': round(current_balance, 2), 'reason': reason
                    }, LOG_TRADES)
                    
                    await send_tg(f"📉 **إغلاق صفقة** {BOT_TAG}\n🪙 {sym}\n💵 الصافي: {net_profit:.2f}$\n💰 الرصيد: {current_balance:.2f}$")
                    to_remove.append(sym)
                    state_changed = True
            
            for s in to_remove: del active_positions[s]

            # --- المسح والدخول ---
            if btc_p > -1.2 and len(active_positions) < 3:
                tickers = await ex_async.fetch_tickers()
                valid = [s for s, t in tickers.items() if s.endswith('/USDT') and t['quoteVolume'] > 350000]
                
                for sym in valid[:10]:
                    if sym in active_positions: continue
                    # تطبيق فلاتر RSI والسكور هنا...
                    p = (await ex_async.fetch_ticker(sym))['last']
                    allocated = current_balance * 0.15 
                    
                    active_positions[sym] = {
                        'entry': p, 'sl': p * 0.96, 'tp': p * 1.07, 
                        'allocated_amount': allocated
                    }
                    state_changed = True
                    await send_tg(f"🚀 **دخول جديد** {BOT_TAG}\n🪙 {sym}\n💰 المبلغ: {allocated:.2f}$")

            # حفظ الحالة إذا حدث أي تغيير
            if state_changed:
                save_state(current_balance, active_positions)

            await asyncio.sleep(45)
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(20)

# =========================================================
# 4. وظائف الدعم
# =========================================================
async def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        try: await client.post(url, json={"chat_id": PUBLIC_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except: pass

def log_to_csv(data, filename):
    exists = os.path.isfile(filename)
    with open(filename, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if not exists: writer.writeheader()
        writer.writerow(data)

app = Flask(__name__)
@app.route('/')
def home(): return f"🟢 {BOT_TAG} Online. Balance: {current_balance:.2f}$", 200

@app.route('/download')
def download(): return send_file(LOG_TRADES, as_attachment=True) if os.path.exists(LOG_TRADES) else ("Empty", 404)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    asyncio.run(trading_engine())

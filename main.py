import asyncio
import ccxt.async_support as ccxt_async
import ccxt
import pandas as pd
import numpy as np
import os
import csv
import json
import threading
import httpx
from flask import Flask, send_file
from datetime import datetime, timedelta

# =========================================================
# 1. إعدادات الهوية والاتصال
# =========================================================
TELEGRAM_TOKEN = "8439548325:AAHOBBHy7EwcX3J5neIaf6iJuSjyGJCuZ68"
PUBLIC_CHAT_ID = "-1003692815602"
BOT_TAG = "#trading_100"
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")

# ملفات البيانات (المختبر السيادي)
LOG_REAL = "real_trading_results.csv"       # سجل الأرباح الحقيقية
LOG_SHADOW = "shadow_trading_results.csv"   # سجل صفقات المختبر (>85 سكور)
LOG_ANALYSIS = "post_exit_analysis.csv"     # تحليل جودة الخروج بعد ساعتين
STATE_FILE = "bot_state.json"               # حفظ الرصيد والصفقات من الضياع

INITIAL_BALANCE = 1000.0
FEE_RATE = 0.002 # عمولة البيع والشراء الإجمالية 0.2%

# الذاكرة النشطة
active_positions = {}
virtual_tracker = {}

# =========================================================
# 2. وظائف إدارة البيانات والتحليل
# =========================================================
def save_state(balance, positions):
    with open(STATE_FILE, 'w') as f:
        json.dump({"balance": balance, "positions": positions}, f)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            return data.get("balance", 1000.0), data.get("positions", {})
    return 1000.0, {}

def log_to_csv(data, filename):
    exists = os.path.isfile(filename)
    with open(filename, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if not exists: writer.writeheader()
        writer.writerow(data)

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

# =========================================================
# 3. نظام رادار "ما بعد الخروج" (Post-Exit Tracker)
# =========================================================
async def analyze_after_2h(sym, exit_price, result_type):
    """يراقب السعر بعد ساعتين من الإغلاق لتقييم القرار"""
    await asyncio.sleep(7200) # انتظار ساعتين
    try:
        ex = ccxt.gateio()
        ticker = ex.fetch_ticker(sym)
        price_2h = ticker['last']
        drift = ((price_2h - exit_price) / exit_price) * 100
        
        evaluation = "✅ خروج مثالي" if drift < 0 else "⚠️ خروج مبكر"
        
        log_to_csv({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'symbol': sym,
            'exit_price': exit_price,
            'price_after_2h': price_2h,
            'drift_pct': round(drift, 2),
            'evaluation': evaluation,
            'trade_result': result_type
        }, LOG_ANALYSIS)
    except: pass

# =========================================================
# 4. محرك التداول والمختبر الذكي
# =========================================================
async def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        try: await client.post(url, json={"chat_id": PUBLIC_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except: pass

async def main_engine():
    global current_balance, active_positions, virtual_tracker
    current_balance, active_positions = load_state()
    
    ex_async = ccxt_async.gateio({'enableRateLimit': True})
    ex_sync = ccxt.gateio({'enableRateLimit': True})
    
    await send_tg(f"🚀 **انطلاق نظام التداول والمختبر السيادي**\n💰 الرصيد الحالي: {current_balance:.2f}$\n📊 وضع الرصد: نشط لجميع العملات > 85")

    while True:
        try:
            # تحديث حالة البيتكوين
            btc = await ex_async.fetch_ticker('BTC/USDT')
            btc_p = btc['percentage']
            
            # --- إدارة الصفقات الحالية (حقيقية + افتراضية) ---
            combined_trades = {**active_positions, **virtual_tracker}
            for sym, trade in list(combined_trades.items()):
                t = await ex_async.fetch_ticker(sym)
                cp = t['last']
                pft_raw = ((cp - trade['entry']) / trade['entry']) * 100
                
                done, rsn = False, ""
                if cp >= trade['tp']: done, rsn = True, "TP"
                elif cp <= trade['sl']: done, rsn = True, "SL"
                
                if done:
                    if sym in active_positions:
                        # تسوية مالية حقيقية
                        fees = trade['allocated'] * FEE_RATE
                        net_profit = (trade['allocated'] * (pft_raw / 100)) - fees
                        current_balance += net_profit
                        log_to_csv({'date': datetime.now(), 'sym': sym, 'net_pft': round(pft_raw, 2), 'balance': round(current_balance, 2), 'type': 'REAL'}, LOG_REAL)
                        del active_positions[sym]
                    else:
                        # تسوية في سجل المختبر
                        log_to_csv({'date': datetime.now(), 'sym': sym, 'pft': round(pft_raw, 2), 'score': trade['score'], 'type': 'SHADOW'}, LOG_SHADOW)
                        del virtual_tracker[sym]
                    
                    # بدء تحليل ما بعد الخروج
                    asyncio.create_task(analyze_after_2h(sym, cp, rsn))
                    await send_tg(f"🏁 **إغلاق صفقة ({rsn})** {BOT_TAG}\n🪙 {sym}\n📊 الربح: {pft_raw:.2f}%\n💵 الرصيد: {current_balance:.2f}$")

            # --- المسح الذكي واختيار النخبة ---
            tickers = await ex_async.fetch_tickers()
            valid_symbols = [s for s, t in tickers.items() if s.endswith('/USDT') and t['quoteVolume'] > 350000]
            
            for sym in valid_symbols[:30]:
                if sym in active_positions or sym in virtual_tracker: continue
                try:
                    ohlcv = ex_sync.fetch_ohlcv(sym, '5m', limit=30)
                    df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
                    rsi = calculate_rsi(df['c']).iloc[-1]
                    c1, c2 = df.iloc[-1], df.iloc[-2]
                    rvol = c1['v'] / (df['v'].iloc[-10:].mean() + 1e-9)
                    
                    # معادلة السكور الثلاثية
                    score = (40 if c1['c'] > c2['h'] else 0) + (30 if rvol > 1.8 else 0) + (30 if 50 < rsi < 70 else 0)
                    
                    if score >= 85:
                        trade_data = {
                            'entry': c1['c'], 'tp': c1['c']*1.06, 'sl': c1['c']*0.965, 
                            'score': score, 'allocated': current_balance * 0.12
                        }
                        
                        # دخول حقيقي إذا كان السكور > 90 والبيتكوين آمن
                        if score >= 90 and len(active_positions) < 3 and btc_p > -1.0:
                            active_positions[sym] = trade_data
                            await send_tg(f"🚀 **دخول حقيقي** {BOT_TAG}\n🪙 {sym}\n📊 السكور: {score}\n💰 المستثمر: {trade_data['allocated']:.2f}$")
                        else:
                            # دخول للمختبر التحليلي فقط
                            virtual_tracker[sym] = trade_data
                            await send_tg(f"🧪 **رصد للمختبر** {BOT_TAG}\n🪙 {sym}\n📊 السكور: {score}")
                except: continue

            save_state(current_balance, active_positions)
            await asyncio.sleep(55)
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(20)

# =========================================================
# 5. واجهة التحكم والتحميل (Flask)
# =========================================================
app = Flask(__name__)

@app.route('/')
def home():
    return f"🟢 {BOT_TAG} Active | Balance: {current_balance:.2f}$ | Real: {len(active_positions)} | Shadow: {len(virtual_tracker)}", 200

@app.route('/download_real')
def dr(): return send_file(LOG_REAL, as_attachment=True) if os.path.exists(LOG_REAL) else ("No Data", 404)

@app.route('/download_shadow')
def ds(): return send_file(LOG_SHADOW, as_attachment=True) if os.path.exists(LOG_SHADOW) else ("No Data", 404)

@app.route('/download_analysis')
def da(): return send_file(LOG_ANALYSIS, as_attachment=True) if os.path.exists(LOG_ANALYSIS) else ("No Data", 404)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    asyncio.run(main_engine())

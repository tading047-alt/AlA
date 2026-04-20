import asyncio
import ccxt.async_support as ccxt_async
import ccxt
import pandas as pd
import numpy as np
import os
import csv
from datetime import datetime

# =========================================================
# 1. الإعدادات والرموز التعريفية
# =========================================================
TELEGRAM_TOKEN = "8439548325:AAHOBBHy7EwcX3J5neIaf6iJuSjyGJCuZ68"
PUBLIC_CHAT_ID = "-1003692815602"
BOT_TAG = "#trading_100"  # الوسم المميز للاشعارات

LOG_TRADES = "paper_trading_results.csv"
LOG_EXPLOSIONS = "imminent_explosions.csv"

active_positions = {}

# =========================================================
# 2. وظائف التحليل والسكور
# =========================================================
def calculate_hierarchy_score(df):
    c1, c2 = df.iloc[-1], df.iloc[-2]
    ma20 = df['close'].rolling(20).mean()
    std = df['close'].rolling(20).std()
    bw = ((std * 4) / ma20).iloc[-1]
    rvol = c1['volume'] / (df['volume'].iloc[-21:-1].mean() + 0.000001)

    score = 0
    if c1['close'] > c2['high']: score += 40
    if bw < 0.023: score += 30
    if rvol > 1.3: score += 30

    efficiency = abs(c1['close'] - c1['open']) / (c1['high'] - c1['low'] + 0.000001)
    velocity = sum(1 for i in range(1, 4) if df['close'].iloc[-i] > df['close'].iloc[-i-1])
    recent_high = df['high'].iloc[-50:].max()
    expected_move = round(((recent_high - c1['close']) / c1['close']) * 100, 2)

    return {
        'main': score, 'eff': efficiency, 'vel': velocity, 
        'exp': max(expected_move, round(rvol * 2.5, 2))
    }

async def check_exceptional_exit(symbol, trade, current_price, btc_change):
    profit = ((current_price - trade['entry']) / trade['entry']) * 100
    minutes_passed = (datetime.now() - trade['start_time']).total_seconds() / 60
    if profit > trade.get('max_profit', 0): trade['max_profit'] = profit

    if minutes_passed > 45 and profit < 0.5: return True, "الزمن الميت (45د)"
    if trade.get('max_profit', 0) >= 2.0 and profit < (trade['max_profit'] * 0.5): return True, "تراجع الزخم (50%)"
    if btc_change < -0.8: return True, "انهيار البيتكوين اللحظي"
    return False, None

# =========================================================
# 3. المحرك وإدارة الإشعارات
# =========================================================
async def main_engine():
    ex_async = ccxt_async.gateio({'enableRateLimit': True})
    ex_sync = ccxt.gateio({'enableRateLimit': True})
    
    print(f"💎 {BOT_TAG} يعمل الآن على سيرفر أمستردام بتوقيت تونس...")

    while True:
        try:
            btc_ticker = await ex_async.fetch_ticker('BTC/USDT')
            btc_change = btc_ticker['percentage']
            
            # --- إدارة الخروج ---
            to_remove = []
            for symbol, trade in active_positions.items():
                ticker = await ex_async.fetch_ticker(symbol)
                curr_p = ticker['last']
                should_exc, r_exc = await check_exceptional_exit(symbol, trade, curr_p, btc_change)
                profit = ((curr_p - trade['entry']) / trade['entry']) * 100
                
                exit_now, reason = False, ""
                if should_exc: exit_now, reason = True, f"خروج استثنائي: {r_exc}"
                elif curr_p <= trade['sl']: exit_now, reason = True, "وقف الخسارة (SL)"
                elif curr_p >= trade['tp']: exit_now, reason = True, "الهدف (TP)"

                if exit_now:
                    msg = (f"📉 **إغلاق صفقة** {BOT_TAG}\n"
                           f"🪙 العملة: {symbol}\n"
                           f"📊 النتيجة: {reason}\n"
                           f"💰 الربح/الخسارة: {profit:.2f}%")
                    print(msg) # هنا تضع كود إرسال التليجرام
                    log_to_csv({'time': datetime.now(), 'sym': symbol, 'profit': round(profit, 2), 'reason': reason}, LOG_TRADES)
                    to_remove.append(symbol)

            for s in to_remove: del active_positions[s]

            # --- المسح والدخول ---
            if btc_change > -1.0:
                tickers = await ex_async.fetch_tickers()
                candidates = [s for s, t in tickers.items() if s.endswith('/USDT') and t['quoteVolume'] > 200000]
                
                results = []
                for cand in candidates[:50]:
                    try:
                        ohlcv = ex_sync.fetch_ohlcv(cand, '5m', limit=60)
                        df = pd.DataFrame(ohlcv, columns=['time','open','high','low','close','volume'])
                        m = calculate_hierarchy_score(df)
                        if m['main'] >= 90:
                            results.append({'sym': cand, 'p': df['close'].iloc[-1], 'metrics': m, 'df': df})
                    except: continue

                results.sort(key=lambda x: (x['metrics']['main'], x['metrics']['eff']), reverse=True)
                
                for sig in results[:3]:
                    if sig['sym'] not in active_positions:
                        # حساب ATR للوقف
                        tr = pd.concat([sig['df']['high']-sig['df']['low'], abs(sig['df']['high']-sig['df']['close'].shift())], axis=1).max(axis=1)
                        atr = tr.rolling(14).mean().iloc[-1]
                        sl, tp = sig['p'] - (atr * 1.6), sig['p'] + (atr * 3)

                        msg = (f"🚀 **إشارة دخول جديدة** {BOT_TAG}\n"
                               f"🪙 العملة: {sig['sym']}\n"
                               f"📊 السكور: {sig['metrics']['main']}\n"
                               f"📈 الانفجار المتوقع: +{sig['metrics']['exp']}%\n"
                               f"🛡️ الوقف: {sl:.6f}")
                        print(msg)
                        
                        active_positions[sig['sym']] = {
                            'entry': sig['p'], 'sl': sl, 'tp': tp,
                            'start_time': datetime.now(), 'max_profit': 0
                        }

            await asyncio.sleep(10 if (datetime.now().hour == 0 and datetime.now().minute >= 45) else 30)

        except Exception as e:
            print(f"⚠️ خطأ: {e}")
            await asyncio.sleep(10)

def log_to_csv(data, filename):
    exists = os.path.isfile(filename)
    with open(filename, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if not exists: writer.writeheader()
        writer.writerow(data)

if __name__ == "__main__":
    asyncio.run(main_engine())

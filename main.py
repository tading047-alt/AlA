#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🚂 نظام ركوب القطار - النسخة النهائية (أميرة النجاح)
إعدادات محسنة + حماية من أخطاء البيانات + إدارة أرباح ذكية
"""

import asyncio
import threading
from flask import Flask, render_template_string
import ccxt.async_support as ccxt_async
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os

# =========================================================
# ⚡ الإعدادات الذهبية (قابلة للتعديل حسب رغبتك)
# =========================================================
TOTAL_CAPITAL = 1000.0          # رأس المال الكلي
CAPITAL_PER_TRADE = 30.0        # حجم الصفقة الواحدة (30 دولار)
MAX_CONCURRENT_TRADES = 15      # أقصى عدد صفقات مفتوحة في وقت واحد
SCAN_INTERVAL = 25              # فحص السوق كل 25 ثانية
TARGET_PROFIT = 8.0             # جني الأرباح الكلي عند 8%
TRAILING_STOP_TRIGGER = 2.5     # حماية الربح: إذا صعد السعر 2.5% لا نسمح بالخسارة
STOP_LOSS_INITIAL = -3.5        # وقف خسارة أولي صارم

# =========================================================
# الهياكل البيانية
# =========================================================
class TradeInfo:
    def __init__(self, symbol, entry_price, capital):
        self.symbol = symbol
        self.entry_price = entry_price
        self.capital = capital
        self.highest_price = entry_price
        self.trailing_stop = entry_price * (1 + STOP_LOSS_INITIAL/100)
        self.entry_time = datetime.now()

# =========================================================
# 🔍 محرك التحليل والاقتناص
# =========================================================
class SmartDetector:
    async def get_active_symbols(self, exchange):
        try:
            tickers = await exchange.fetch_tickers()
            promising = []
            for symbol, t in tickers.items():
                # 🛡️ الحماية من القيم الفارغة (None) لمنع توقف الكود
                price = t.get('last')
                volume = t.get('quoteVolume')
                percentage = t.get('percentage')

                if price is None or volume is None or percentage is None:
                    continue

                # تصفية العملات: USDT فقط + سيولة > 10,000 + صعود معقول
                if symbol.endswith('/USDT') and volume > 10000 and -2 < percentage < 15:
                    promising.append({
                        'symbol': symbol, 
                        'price': price, 
                        'vol': volume, 
                        'change': percentage
                    })
            
            # ترتيب حسب السيولة الأعلى
            promising.sort(key=lambda x: x['vol'], reverse=True)
            return promising
        except Exception as e:
            print(f"⚠️ خطأ في جلب البيانات: {e}")
            return []

    async def analyze_momentum(self, exchange, symbol_info):
        try:
            ohlcv = await exchange.fetch_ohlcv(symbol_info['symbol'], '5m', limit=15)
            if len(ohlcv) < 10: return False
            
            closes = np.array([x[4] for x in ohlcv])
            volumes = np.array([x[5] for x in ohlcv])
            
            # شرط الدخول: حجم تداول الشمعة الأخيرة أكبر من المتوسط + صعود سعري
            vol_avg = np.mean(volumes[:-1])
            if volumes[-1] > vol_avg * 1.5 and closes[-1] > closes[-2]:
                return True
        except:
            return False
        return False

# =========================================================
# 🚂 مدير الصفقات والعمليات
# =========================================================
class TradeManager:
    def __init__(self):
        self.active_trades = {}
        self.available_balance = TOTAL_CAPITAL
        self.total_completed_trades = 0
        self.wins = 0

    async def monitor_and_exit(self, exchange):
        for symbol, trade in list(self.active_trades.items()):
            try:
                ticker = await exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                if current_price is None: continue

                pnl = (current_price - trade.entry_price) / trade.entry_price * 100
                
                # تحديث الـ Trailing Stop (رفع الوقف مع صعود السعر)
                if current_price > trade.highest_price:
                    trade.highest_price = current_price
                    if pnl > TRAILING_STOP_TRIGGER:
                        # رفع الوقف ليكون تحت السعر الحالي بـ 2% (حماية الربح)
                        trade.trailing_stop = max(trade.trailing_stop, current_price * 0.98)

                # شروط الخروج
                exit_now = False
                reason = ""
                
                if pnl >= TARGET_PROFIT:
                    exit_now, reason = True, "🎯 الهدف المحقق"
                elif current_price <= trade.trailing_stop:
                    exit_now, reason = True, "🛡️ الوقف المتحرك/الخسارة"

                if exit_now:
                    profit_usd = trade.capital * (pnl/100)
                    self.available_balance += (trade.capital + profit_usd)
                    self.total_completed_trades += 1
                    if pnl > 0: self.wins += 1
                    
                    print(f"\n🏁 إغلاق صفقة {symbol}:")
                    print(f"💵 الربح: {pnl:.2f}% | السبب: {reason} | الرصيد المتاح: {self.available_balance:.2f}$")
                    del self.active_trades[symbol]
            except Exception as e:
                print(f"⚠️ خطأ في مراقبة {symbol}: {e}")

# =========================================================
# 🌐 واجهة التحكم البسيطة (Flask)
# =========================================================
app = Flask(__name__)
manager = TradeManager()

@app.route('/')
def index():
    return render_template_string('''
    <html><head><meta charset="utf-8"><meta http-equiv="refresh" content="10"><title>بوت أميرة النجاح</title>
    <style>body{background:#121212;color:white;font-family:sans-serif;text-align:center}
    .card{background:#1e1e1e;padding:20px;margin:10px;border-radius:10px;display:inline-block;min-width:200px}
    .profit{color:#00ff00} .loss{color:#ff4444} table{margin:auto;border-collapse:collapse;width:80%}
    th,td{padding:10px;border:1px solid #333}</style></head><body>
    <h1>🚂 بوت أميرة النجاح (النسخة النهائية)</h1>
    <div class="card"><h3>رأس المال المتاح</h3><h2>{{balance|round(2)}}$</h2></div>
    <div class="card"><h3>صفقات نشطة</h3><h2>{{active_count}}</h2></div>
    <div class="card"><h3>نسبة النجاح</h3><h2>{{win_rate|round(1)}}%</h2></div>
    <hr><h3>📈 الصفقات المفتوحة الآن</h3>
    <table><tr><th>الرمز</th><th>سعر الدخول</th><th>الربح الحالي</th></tr>
    {% for s, t in trades.items() %}
    <tr><td>{{s}}</td><td>{{t.entry_price}}</td><td class="profit">جاري المراقبة...</td></tr>
    {% endfor %}</table></body></html>''', 
    balance=manager.available_balance, active_count=len(manager.active_trades),
    win_rate=(manager.wins/manager.total_completed_trades*100) if manager.total_completed_trades>0 else 0,
    trades=manager.active_trades)

# =========================================================
# 🚀 التشغيل الرئيسي
# =========================================================
async def main_loop():
    # تفعيل Rate Limit لاحترام قوانين المنصة وتجنب الحظر
    exchange = ccxt_async.gateio({'enableRateLimit': True})
    detector = SmartDetector()
    
    print("💎 تم تشغيل المحرك بنجاح. البوت يبحث عن صفقات الآن...")
    
    try:
        while True:
            # 1. مراقبة وإغلاق الصفقات
            await manager.monitor_and_exit(exchange)
            
            # 2. البحث عن صفقات جديدة إذا توفر مكان
            if len(manager.active_trades) < MAX_CONCURRENT_TRADES and manager.available_balance >= CAPITAL_PER_TRADE:
                symbols = await detector.get_active_symbols(exchange)
                for info in symbols[:40]: # فحص أفضل 40 عملة نشطة
                    if info['symbol'] in manager.active_trades: continue
                    
                    is_ready = await detector.analyze_momentum(exchange, info)
                    if is_ready:
                        # دخول الصفقة
                        manager.available_balance -= CAPITAL_PER_TRADE
                        manager.active_trades[info['symbol']] = TradeInfo(info['symbol'], info['price'], CAPITAL_PER_TRADE)
                        print(f"🚀 صفقة جديدة: {info['symbol']} @ {info['price']}")
                        
                        if len(manager.active_trades) >= MAX_CONCURRENT_TRADES: break
            
            await asyncio.sleep(SCAN_INTERVAL)
    finally:
        await exchange.close()

def run_flask():
    app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    # تشغيل واجهة الويب في الخلفية
    threading.Thread(target=run_flask, daemon=True).start()
    # تشغيل البوت
    asyncio.run(main_loop())

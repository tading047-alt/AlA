#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🚂 نظام ركوب القطار - نسخة "أميرة النجاح" المحدثة
إعدادات مثالية للنمو المستمر واقتناص الصفقات السريعة
"""

import asyncio
import threading
from flask import Flask, jsonify, render_template_string
import sqlite3
import ccxt.async_support as ccxt_async
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os
import csv
from collections import deque
from dataclasses import dataclass, field, asdict

# =========================================================
# ⚡ الإعدادات الذهبية (الأمثل لنمو المحفظة)
# =========================================================
TOTAL_CAPITAL = 1000.0          # رأس المال
MAX_TRADES_PER_DAY = 150       # عدد صفقات كبير للتحليل والربح
CAPITAL_PER_TRADE = 30.0        # توزيع المخاطر (3% من المحفظة لكل صفقة)
MAX_CONCURRENT_TRADES = 15      # فتح 15 صفقة في نفس الوقت كحد أقصى
SCAN_INTERVAL = 20              # فحص سريع كل 20 ثانية
MIN_CONFIDENCE = 25             # اقتناص الفرص المتوسطة والقوية

# =========================================================
# 🎯 معايير الربح والخسارة (الأهداف السريعة)
# =========================================================
TARGET_PROFIT = 8.0             # جني أرباح كلي عند 8%
TRAILING_STOP_TRIGGER = 2.5     # تفعيل حماية الأرباح بعد صعود 2.5%
STOP_LOSS_INITIAL = -3.5        # وقف خسارة صارم لحماية رأس المال

# إعدادات تليجرام (تأكد من صحتها)
TELEGRAM_TOKEN = "8439548325:8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"
TELEGRAM_CHAT_ID = "5067771509"

# =========================================================
# الهياكل البيانية
# =========================================================
@dataclass
class StationSignal:
    symbol: str
    pattern_type: str
    confidence: float
    entry_price: float
    reasons: list
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class TradeInfo:
    symbol: str
    entry_price: float
    capital: float
    stage: int
    highest_price: float
    trailing_stop: float
    entry_time: datetime = field(default_factory=datetime.now)

# =========================================================
# 🔍 كاشف الانفجارات السعرية (المحرك الرئيسي)
# =========================================================
class SmartDetector:
    async def get_active_symbols(self, exchange):
        tickers = await exchange.fetch_tickers()
        # نركز على العملات التي لديها سيولة محترمة وتذبذب إيجابي
        return [
            {'symbol': s, 'price': t['last'], 'vol': t['quoteVolume'], 'change': t['percentage']}
            for s, t in tickers.items() 
            if s.endswith('/USDT') and t['quoteVolume'] > 10000 and -2 < t['percentage'] < 15
        ]

    async def analyze_symbol(self, exchange, info):
        try:
            ohlcv = await exchange.fetch_ohlcv(info['symbol'], '5m', limit=20)
            data = np.array(ohlcv)
            closes = data[:, 4]
            volumes = data[:, 5]
            
            # مؤشر الزخم السريع (الحجم + السعر)
            vol_spike = volumes[-1] > np.mean(volumes[:-1]) * 1.5
            price_move = (closes[-1] - closes[-2]) / closes[-2] * 100
            
            if vol_spike and price_move > 0.5:
                return StationSignal(
                    symbol=info['symbol'],
                    pattern_type="🚀 انفجار زخم",
                    confidence=70 if price_move > 1 else 40,
                    entry_price=info['price'],
                    reasons=[f"حجم {vol_spike}", f"صعود {price_move:.2f}%"]
                )
        except: return None
        return None

# =========================================================
# 🚂 مدير الصفقات (إدارة الأرباح)
# =========================================================
class TradeManager:
    def __init__(self):
        self.active_trades = {}
        self.balance = TOTAL_CAPITAL
        self.daily_wins = 0

    async def execute_trade(self, signal):
        if signal.symbol in self.active_trades or self.balance < CAPITAL_PER_TRADE:
            return
        
        self.balance -= CAPITAL_PER_TRADE
        self.active_trades[signal.symbol] = TradeInfo(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            capital=CAPITAL_PER_TRADE,
            stage=1,
            highest_price=signal.entry_price,
            trailing_stop=signal.entry_price * (1 + STOP_LOSS_INITIAL/100)
        )
        print(f"✅ تم دخول صفقة: {signal.symbol} بسعر {signal.entry_price}")

    async def monitor_trades(self, exchange):
        for symbol, trade in list(self.active_trades.items()):
            try:
                ticker = await exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                pnl = (current_price - trade.entry_price) / trade.entry_price * 100
                
                # تحديث أعلى سعر وصل له لتفعيل الوقف المتحرك
                if current_price > trade.highest_price:
                    trade.highest_price = current_price
                    # إذا ربحنا أكثر من 2.5%، اجعل وقف الخسارة عند سعر الدخول (ضمان عدم الخسارة)
                    if pnl > TRAILING_STOP_TRIGGER:
                        trade.trailing_stop = max(trade.trailing_stop, current_price * 0.98)

                # شروط الخروج
                exit_reason = ""
                if pnl >= TARGET_PROFIT: exit_reason = "🎯 تم تحقيق الهدف"
                elif current_price <= trade.trailing_stop: exit_reason = "🛡️ تفعيل وقف الخسارة/المتحرك"

                if exit_reason:
                    final_pnl_usd = trade.capital * (1 + pnl/100)
                    self.balance += final_pnl_usd
                    if pnl > 0: self.daily_wins += 1
                    print(f"🏁 خروج من {symbol} | ربح: {pnl:.2f}% | السبب: {exit_reason}")
                    del self.active_trades[symbol]
            except: pass

# =========================================================
# 🔄 المحرك والتشغيل
# =========================================================
async def run_bot():
    exchange = ccxt_async.gateio({'enableRateLimit': True})
    detector = SmartDetector()
    manager = TradeManager()
    
    print("🚀 بوت 'أميرة النجاح' بدأ العمل...")
    
    try:
        while True:
            # 1. مراقبة الصفقات المفتوحة أولاً
            await manager.monitor_trades(exchange)
            
            # 2. البحث عن فرص جديدة
            if len(manager.active_trades) < MAX_CONCURRENT_TRADES:
                symbols = await detector.get_active_symbols(exchange)
                # فحص أعلى 50 عملة سيولة لتوفير الوقت
                for info in symbols[:50]:
                    signal = await detector.analyze_symbol(exchange, info)
                    if signal and signal.confidence >= MIN_CONFIDENCE:
                        await manager.execute_trade(signal)
                        if len(manager.active_trades) >= MAX_CONCURRENT_TRADES: break
            
            await asyncio.sleep(SCAN_INTERVAL)
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(run_bot())

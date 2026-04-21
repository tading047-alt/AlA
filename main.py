import asyncio
import threading
from flask import Flask, render_template_string, send_file
import sqlite3
import ccxt.async_support as ccxt_async
import pandas as pd
import numpy as np
import json
import os
import httpx
import csv
from datetime import datetime
from dataclasses import dataclass, field, asdict

# =========================================================
# ⚙️ الإعدادات النهائية (Configuration)
# =========================================================
TELEGRAM_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"
TELEGRAM_CHAT_ID = "5067771509"

LOG_DIR = "/tmp/trading_logs"
DB_FILE = os.path.join(LOG_DIR, "empire_final_v8.db")
REAL_CSV = os.path.join(LOG_DIR, "trading_history.csv")
MISSED_CSV = os.path.join(LOG_DIR, "missed_trades.csv")
os.makedirs(LOG_DIR, exist_ok=True)

INITIAL_BALANCE = 1000.0
MAX_CONCURRENT_TRADES = 5  # 5 صفقات × 20% = 100% استغلال
SCAN_INTERVAL = 10 

@dataclass
class TrainSignal:
    symbol: str
    entry_price: float
    strategy_name: str
    timeframe_confirmed: bool

@dataclass
class TradeInfo:
    symbol: str
    signal: TrainSignal
    entry_price: float
    invested: float
    highest_price: float
    stop_loss: float
    is_virtual: bool = False
    is_secured: bool = False
    entry_time: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

# =========================================================
# 🚀 المحرك الإمبراطوري المتكامل (The Master Engine)
# =========================================================
class ImperialMasterEngine:
    def __init__(self):
        self.active_trades = {}
        self.virtual_trades = {}
        self.balance = INITIAL_BALANCE
        self._init_storage()
        self._load_state()

    def _init_storage(self):
        conn = sqlite3.connect(DB_FILE)
        conn.execute("CREATE TABLE IF NOT EXISTS active_trades (symbol TEXT PRIMARY KEY, data TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value REAL)")
        if not conn.execute("SELECT value FROM config WHERE key='balance'").fetchone():
            conn.execute("INSERT INTO config VALUES ('balance', ?)", (INITIAL_BALANCE,))
        conn.commit()
        conn.close()
        for path in [REAL_CSV, MISSED_CSV]:
            if not os.path.exists(path):
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Time', 'Symbol', 'Strategy', 'Entry', 'Exit', 'PNL%', 'Status'])

    def _save_state(self):
        conn = sqlite3.connect(DB_FILE)
        conn.execute("DELETE FROM active_trades")
        for sym, t in self.active_trades.items():
            conn.execute("INSERT INTO active_trades VALUES (?, ?)", (sym, json.dumps(asdict(t))))
        conn.execute("UPDATE config SET value = ? WHERE key = 'balance'", (self.balance,))
        conn.commit()
        conn.close()

    def _load_state(self):
        try:
            conn = sqlite3.connect(DB_FILE)
            self.balance = conn.execute("SELECT value FROM config WHERE key='balance'").fetchone()[0]
            rows = conn.execute("SELECT data FROM active_trades").fetchall()
            for r in rows:
                d = json.loads(r[0])
                sig = TrainSignal(**d.pop('signal'))
                self.active_trades[d['symbol']] = TradeInfo(**d, signal=sig)
            conn.close()
        except: pass

    async def send_tg(self, msg: str):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except: pass

    async def analyze(self, ex, symbol):
        try:
            # 1. تحليل الفريم الكبير (15 دقيقة) - تحديد الاتجاه
            ohlcv_15 = await ex.fetch_ohlcv(symbol, timeframe='15m', limit=50)
            df_15 = pd.DataFrame(ohlcv_15, columns=['t','o','h','l','c','v'])
            ema_50_15 = df_15['c'].ewm(span=50).mean().iloc[-1]
            if df_15['c'].iloc[-1] < ema_50_15: return None # تجاهل لو الاتجاه هابط

            # 2. تحليل فريم الدخول (5 دقائق) - استراتيجية BB + RSI + Volume
            ohlcv_5 = await ex.fetch_ohlcv(symbol, timeframe='5m', limit=50)
            df_5 = pd.DataFrame(ohlcv_5, columns=['t','o','h','l','c','v'])
            
            # بولنجر
            sma = df_5['c'].rolling(20).mean()
            std = df_5['c'].rolling(20).std()
            upper_bb = (sma + 2*std).iloc[-1]
            
            # RSI
            delta = df_5['c'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rsi = 100 - (100 / (1 + gain/(loss + 1e-9)))
            
            last_c = df_5['c'].iloc[-1]
            vol_avg = df_5['v'].rolling(10).mean().iloc[-2]

            # الشروط المدمجة
            is_breakout = last_c > upper_bb
            is_momentum = 55 < rsi.iloc[-1] < 75
            is_volume = df_5['v'].iloc[-1] > vol_avg * 2

            if is_breakout and is_momentum and is_volume:
                return TrainSignal(symbol=symbol, entry_price=last_c, strategy_name="نظام الامتياز (15m/5m)", timeframe_confirmed=True)
            return None
        except: return None

# =========================================================
# 🌐 واجهة الويب والتحميل
# =========================================================
app = Flask(__name__)
engine = ImperialMasterEngine()

@app.route('/')
def dashboard():
    equity = engine.balance + sum([t.invested for t in engine.active_trades.values()])
    html = """
    <html dir="rtl"><head><meta charset="UTF-8"><title>Empire V8 Final</title>
    <style>
        body { background: #020617; color: white; font-family: sans-serif; padding: 20px; }
        .grid { display: flex; gap: 15px; justify-content: center; margin-bottom: 20px; }
        .

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
DB_FILE = os.path.join(LOG_DIR, "empire_final_v8_1.db")
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
            if df_15['c'].iloc[-1] < ema_50_15: return None 

            # 2. تحليل فريم الدخول (5 دقائق) - استراتيجية BB + RSI + Volume
            ohlcv_5 = await ex.fetch_ohlcv(symbol, timeframe='5m', limit=50)
            df_5 = pd.DataFrame(ohlcv_5, columns=['t','o','h','l','c','v'])
            
            sma = df_5['c'].rolling(20).mean()
            std = df_5['c'].rolling(20).std()
            upper_bb = (sma + 2*std).iloc[-1]
            
            delta = df_5['c'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rsi = 100 - (100 / (1 + gain/(loss + 1e-9)))
            
            last_c = df_5['c'].iloc[-1]
            vol_avg = df_5['v'].rolling(10).mean().iloc[-2]

            is_breakout = last_c > upper_bb
            is_momentum = 55 < rsi.iloc[-1] < 75
            is_volume = df_5['v'].iloc[-1] > vol_avg * 2

            if is_breakout and is_momentum and is_volume:
                return TrainSignal(symbol=symbol, entry_price=last_c, strategy_name="نظام الامتياز (15m/5m)", timeframe_confirmed=True)
            return None
        except: return None

# =========================================================
# 🌐 واجهة الويب والتحميل (المصححة)
# =========================================================
app = Flask(__name__)
engine = ImperialMasterEngine()

@app.route('/')
def dashboard():
    active_inv = sum([t.invested for t in engine.active_trades.values()])
    equity = engine.balance + active_inv
    v_count = len(engine.virtual_trades)
    
    html_template = """
    <html dir="rtl"><head><meta charset="UTF-8"><title>Empire V8 Final</title>
    <style>
        body { background: #020617; color: white; font-family: sans-serif; padding: 20px; }
        .grid { display: flex; gap: 15px; justify-content: center; margin-bottom: 20px; }
        .card { background: #1e293b; padding: 20px; border-radius: 10px; border-bottom: 4px solid #38bdf8; min-width: 200px; text-align: center; }
        .btn { background: #38bdf8; color: #020617; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; margin: 10px; display: inline-block; }
        table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 10px; overflow: hidden; margin-top: 20px; }
        th, td { padding: 12px; border-bottom: 1px solid #334155; text-align: center; }
        .profit { color: #4ade80; font-weight: bold; } .virtual { color: #fbbf24; }
    </style></head><body>
        <h1 style="color:#38bdf8; text-align:center;">🛡️ المحرك الإمبراطوري النهائي (V8.1)</h1>
        <div class="grid">
            <div class="card"><h3>إجمالي المحفظة</h3><p class="profit" style="font-size:1.5rem;">{{ "%.2f"|format(equity) }} USDT</p></div>
            <div class="card"><h3>السيولة المتاحة</h3><p>{{ "%.2f"|format(balance) }}</p></div>
            <div class="card"><h3>الفرص المراقبة</h3><p class="virtual">{{ v_count }}</p></div>
        </div>
        <div style="text-align:center;">
            <a href="/dl_real" class="btn">📥 تحميل سجل الأرباح</a>
            <a href="/dl_missed" class="btn" style="background:#fbbf24;">📥 تحميل سجل التحليل</a>
        </div>
        
        <h2>🟢 صفقات حقيقية ({{ active|length }}/5)</h2>
        <table>
            <tr style="background:#334155;"><th>العملة</th><th>المبلغ المستثمر</th><th>الربح العائم</th><th>الحالة</th></tr>
            {% for s, t in active.items() %}
            <tr><td><b>{{ s }}</b></td><td>{{ "%.2f"|format(t.invested) }}</td>
            <td class="profit">{{ "%.2f"|format(((t.highest_price - t.entry_price)/t.entry_price)*100) }}%</td>
            <td>{{ "🛡️ مؤمنة" if t.is_secured else "⚡ نشطة" }}</td></tr>
            {% endfor %}
        </table>

        <h2 style="color:#fbbf24;">🟡 الفرص الضائعة (تحت المراقبة)</h2>
        <table>
            <tr style="background:#334155;"><th>العملة</th><th>سعر الاكتشاف</th><th>الربح الافتراضي</th><th>الاستراتيجية</th></tr>
            {% for s, t in virtual.items() %}
            <tr><td>{{ s }}</td><td>{{ t.entry_price }}</td><td class="virtual">{{ "%.2f"|format(((t.highest_price - t.entry_price)/t.entry_price)*100) }}%</td><td>{{ t.signal.strategy_name }}</td></tr>
            {% endfor %}
        </table>
    </body></html>
    """
    return render_template_string(html_template, balance=engine.balance, equity=equity, active=engine.active_trades, virtual=engine.virtual_trades, v_count=v_count)

@app.route('/dl_real')
def dl_r(): return send_file(REAL_CSV, as_attachment=True)

@app.route('/dl_missed')
def dl_m(): return send_file(MISSED_CSV, as_attachment=True)

# =========================================================
# 🔄 محرك المعالجة والتلجرام
# =========================================================
async def main_loop():
    ex = ccxt_async.gateio({'enableRateLimit': True})
    markets = await ex.fetch_markets()
    symbols = [m['symbol'] for m in markets if m['symbol'].endswith('/USDT') and m['active']]
    
    await engine.send_tg("🚀 *تم إطلاق النسخة V8.1 المصححة*\n- نظام الفريمات المزدوجة مفعل\n- تتبع الفرص الضائعة مفعل\n- إدارة المحفظة 20% مفعله")

    while True:
        combined = {**engine.active_trades, **engine.virtual_trades}
        for sym, trade in list(combined.items()):
            try:
                t_data = await ex.fetch_ticker(sym); curr = t_data['last']
                pnl = (curr - trade.entry_price) / trade.entry_price * 100
                if curr > trade.highest_price: trade.highest_price = curr
                
                if not trade.is_virtual and pnl >= 1.5 and not trade.is_secured:
                    trade.stop_loss = trade.entry_price; trade.is_secured = True
                    await engine.send_tg(f"🛡️ *تأمين صفقة {sym}*\nنقطة الدخول أصبحت هي الوقف.")

                exit_r = ""
                if pnl <= -2.5: exit_r = "Stop Loss"
                elif pnl >= 6.0: exit_r = "Target Hit"

                if exit_r:
                    path = MISSED_CSV if trade.is_virtual else REAL_CSV
                    with open(path, 'a', newline='') as f:
                        writer = csv.writer(f); writer.writerow([datetime.now(), sym, trade.signal.strategy_name, trade.entry_price, curr, f"{pnl:.2f}", exit_r])
                    
                    if trade.is_virtual: del engine.virtual_trades[sym]
                    else:
                        engine.balance += trade.invested * (1 + pnl/100)
                        del engine.active_trades[sym]; engine._save_state()
                        await engine.send_tg(f"🏁 *إغلاق حقيقي {sym}*\nالنتيجة: `{pnl:.2f}%`\nالسبب: `{exit_r}`")
            except: pass

        import random
        batch = random.sample(symbols, min(len(symbols), 35))
        tasks = [engine.analyze(ex, s) for s in batch]
        results = await asyncio.gather(*tasks)
        
        for sig in results:
            if sig and sig.symbol not in engine.active_trades and sig.symbol not in engine.virtual_trades:
                if len(engine.active_trades) < MAX_CONCURRENT_TRADES:
                    equity = engine.balance + sum([t.invested for t in engine.active_trades.values()])
                    invest = equity * 0.20
                    if engine.balance >=

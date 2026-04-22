import asyncio
import threading
from flask import Flask, render_template_string
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
# ⚙️ الإعدادات العامة
# =========================================================
TELEGRAM_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"
TELEGRAM_CHAT_ID = "5067771509"

LOG_DIR = "/tmp/trading_logs"
DB_FILE = os.path.join(LOG_DIR, "empire_v19.db")
REAL_CSV = os.path.join(LOG_DIR, "real_trades.csv")
MISSED_CSV = os.path.join(LOG_DIR, "missed_trades.csv")
os.makedirs(LOG_DIR, exist_ok=True)

MAX_CONCURRENT_TRADES = 10 
TRADE_AMOUNT = 100.0
TOTAL_SYMBOLS_TO_SCAN = 1000

@dataclass
class TrainSignal:
    symbol: str
    entry_price: float
    votes: int
    strategies: list
    expected_pump: float
    time_found: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))

@dataclass
class TradeInfo:
    symbol: str
    signal: TrainSignal
    entry_price: float
    invested: float
    highest_price: float
    stop_loss: float
    take_profit: float
    entry_time: str = field(default_factory=lambda: datetime.now().isoformat())
    is_virtual: bool = False

# =========================================================
# 🚀 المحرك الرئيسي (V19 Engine)
# =========================================================
class EmpireEngineV19:
    def __init__(self):
        self.active_trades = {}
        self.missed_trades = []
        self.balance = 2000.0
        self.stats = {"scanned": 0, "status": "Initializing", "db_status": "🔴", "api_status": "🔴"}
        self._init_storage()

    def _init_storage(self):
        conn = sqlite3.connect(DB_FILE)
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value REAL)")
        if not conn.execute("SELECT value FROM config WHERE key='balance'").fetchone():
            conn.execute("INSERT INTO config VALUES ('balance', 2000.0)")
        conn.commit(); conn.close()
        self.stats["db_status"] = "🟢"
        
        for f in [REAL_CSV, MISSED_CSV]:
            if not os.path.exists(f):
                with open(f, 'w', newline='') as csvfile:
                    csv.writer(csvfile).writerow(['Time', 'Symbol', 'Entry', 'Exit', 'PNL%'])

    async def analyze(self, ex, symbol):
        try:
            ohlcv = await ex.fetch_ohlcv(symbol, timeframe='5m', limit=50)
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            
            # المؤشرات
            sma = df['c'].rolling(20).mean(); std = df['c'].rolling(20).std()
            upper_bb = sma + (2 * std); lower_bb = sma - (2 * std)
            bw = (upper_bb - lower_bb) / sma
            rsi = 100 - (100 / (1 + (df['c'].diff().clip(lower=0).rolling(14).mean() / df['c'].diff().clip(upper=0).abs().rolling(14).mean())))

            votes = []
            if bw.iloc[-1] < bw.rolling(30).min().iloc[-2] * 1.1: votes.append("Squeeze") # ضيق البولنجر
            if df['c'].iloc[-1] > sma.iloc[-1]: votes.append("Uptrend") # ترند صاعد
            if df['v'].iloc[-1] > df['v'].rolling(20).mean().iloc[-2] * 2: votes.append("Volume") # سيولة
            if rsi.iloc[-1] > 50: votes.append("Momentum")
            if df['c'].iloc[-1] > upper_bb.iloc[-1]: votes.append("Breakout")

            if len(votes) >= 3:
                return TrainSignal(symbol, df['c'].iloc[-1], len(votes), votes, round(bw.iloc[-1]*100, 2))
            return None
        except: return None

# =========================================================
# 🌐 واجهة الويب (Professional Dashboard)
# =========================================================
app = Flask(__name__)
engine = EmpireEngineV19()

@app.template_filter('duration')
def duration_filter(iso_time):
    diff = datetime.now() - datetime.fromisoformat(iso_time)
    return f"{diff.seconds // 60} دقيقة"

@app.route('/')
def dashboard():
    curr_prices = {s: t.highest_price for s, t in engine.active_trades.items()}
    html = """
    <html dir="rtl"><head><meta charset="UTF-8"><title>Empire V19 Dashboard</title>
    <style>
        body { background: #020617; color: white; font-family: sans-serif; padding: 20px; }
        .status-bar { display: flex; justify-content: space-around; background: #1e293b; padding: 15px; border-radius: 10px; margin-bottom: 20px; border-top: 4px solid #38bdf8; }
        .grid { display: grid; grid-template-columns: 1fr; gap: 20px; }
        .card { background: #0f172a; padding: 15px; border-radius: 10px; border: 1px solid #334155; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th { background: #1e293b; color: #38bdf8; padding: 10px; font-size: 0.8rem; }
        td { padding: 10px; border-bottom: 1px solid #1e293b; text-align: center; }
        .pnl-pos { color: #4ade80; } .pnl-neg { color: #f87171; }
        .badge { background: #38bdf8; color: #020617; padding: 2px 6px; border-radius: 4px; font-weight: bold; }
    </style></head><body>
        <h2>💎 إمبراطورية التداول V19</h2>
        <div class="status-bar">
            <div>حالة المنصة: {{ stats.api_status }}</div>
            <div>قاعدة البيانات: {{ stats.db_status }}</div>
            <div>تم مسحه: {{ stats.scanned }} عملة</div>
            <div>رصيد المحفظة: {{ "%.2f"|format(balance) }} $</div>
        </div>

        <div class="grid">
            <div class="card">
                <h3>🟢 صفقات مفتوحة ({{ active|length }}/10)</h3>
                <table>
                    <tr><th>العملة</th><th>الدخول</th><th>الهدف</th><th>الوقف</th><th>المدة</th><th>الربح العائم</th></tr>
                    {% for s, t in active.items() %}
                    <tr>
                        <td><b>{{ s }}</b> <span class="badge">{{ t.signal.votes }}/5</span></td>
                        <td>{{ t.entry_price }}</td>
                        <td style="color:#4ade80">{{ "%.6f"|format(t.take_profit) }}</td>
                        <td style="color:#f87171">{{ "%.6f"|format(t.stop_loss) }}</td>
                        <td>{{ t.entry_time|duration }}</td>
                        <td class="pnl-pos"> {{ "%.2f"|format(((prices[s]-t.entry_price)/t.entry_price)*100) }}% </td>
                    </tr>
                    {% endfor %}
                </table>
            </div>

            <div class="card" style="border-top: 3px solid #fbbf24;">
                <h3>🟡 صفقات ضائعة (تم رصدها ولم يتم دخولها)</h3>
                <table>
                    <tr><th>الوقت</th><th>العملة</th><th>قوة الإشارة</th><th>الاستراتيجيات</th><th>الصعود المتوقع</th></tr>
                    {% for m in missed %}
                    <tr>
                        <td>{{ m.time_found }}</td>
                        <td>{{ m.symbol }}</td>
                        <td><span class="badge">{{ m.votes }}/5</span></td>
                        <td style="font-size: 0.7rem;">{{ m.strategies|join(', ') }}</td>
                        <td style="color:#fbbf24">+{{ m.expected_pump }}%</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>
    </body></html>
    """
    return render_template_string(html, active=engine.active_trades, missed=engine.missed_trades[-10:], balance=engine.balance, stats=engine.stats, prices=curr_prices)

# =========================================================
# 🔄 حلقة التشغيل والمسح (Turbo Sync)
# =========================================================
async def main_loop():
    ex = ccxt_async.gateio({'enableRateLimit': True})
    engine.stats["api_status"] = "🟢"
    markets = await ex.fetch_markets()
    symbols = [m['symbol'] for m in markets if m['symbol'].endswith('/USDT') and m['active']]

    while True:
        try:
            # 1. إدارة الصفقات
            for sym, trade in list(engine.active_trades.items()):
                t_data = await ex.fetch_ticker(sym); curr = t_data['last']
                trade.highest_price = curr
                pnl = (curr - trade.entry_price) / trade.entry_price * 100
                
                if pnl <= -2.5 or pnl >= 6.0:
                    engine.balance += trade.invested * (1 + pnl/100)
                    with open(REAL_CSV, 'a') as f: csv.writer(f).writerow([datetime.now(), sym, trade.entry_price, curr, f"{pnl:.2f}"])
                    del engine.active_trades[sym]

            # 2. مسح السوق (Turbo Scan)
            engine.stats["scanned"] = 0
            import random
            for batch in range(0, 1000, 50):
                tasks = [engine.analyze(ex, s) for s in random.sample(symbols, 50)]
                results = await asyncio.gather(*tasks)
                for sig in results:
                    if sig:
                        if sig.symbol not in engine.active_trades and len(engine.active_trades) < MAX_CONCURRENT_TRADES:
                            engine.active_trades[sig.symbol] = TradeInfo(sig.symbol, sig, sig.entry_price, TRADE_AMOUNT, sig.entry_price, sig.entry_price*0.97, sig.entry_price*1.06)
                            engine.balance -= TRADE_AMOUNT
                            await send_tg(f"🚀 *تم دخول صفقة:* {sig.symbol}\nقوة الإشارة: `{sig.votes}/5`")
                        else:
                            engine.missed_trades.append(sig)
                            with open(MISSED_CSV, 'a') as f: csv.writer(f).writerow([datetime.now(), sig.symbol, sig.entry_price, "MISSED", sig.votes])
                engine.stats["scanned"] += 50
                await asyncio.sleep(0.1)

        except: pass
        await asyncio.sleep(10)

async def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client: await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080), daemon=True).start()
    asyncio.run(main_loop())

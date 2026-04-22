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
from datetime import datetime, time
from dataclasses import dataclass, field, asdict

# =========================================================
# ⚙️ CONFIGURATION
# =========================================================
TELEGRAM_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"
TELEGRAM_CHAT_ID = "5067771509"

LOG_DIR = "/tmp/trading_logs"
DB_FILE = os.path.join(LOG_DIR, "empire_v19_optimized.db")
REAL_CSV = os.path.join(LOG_DIR, "real_trades.csv")
MISSED_CSV = os.path.join(LOG_DIR, "missed_trades.csv")
os.makedirs(LOG_DIR, exist_ok=True)

MAX_CONCURRENT_TRADES = 10
RISK_PER_TRADE = 0.02
STOP_LOSS_PCT = 0.025
TRAILING_ACTIVATE_PCT = 2.0
TRAILING_DISTANCE_PCT = 1.5
MIN_VOTES = 4
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

class EmpireEngineV19Optimized:
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
        conn.commit()
        conn.close()
        self.stats["db_status"] = "🟢"
        for f in [REAL_CSV, MISSED_CSV]:
            if not os.path.exists(f):
                with open(f, 'w', newline='') as csvfile:
                    csv.writer(csvfile).writerow(['Time', 'Symbol', 'Entry', 'Exit', 'PNL%'])

    def _save_balance(self):
        conn = sqlite3.connect(DB_FILE)
        conn.execute("UPDATE config SET value = ? WHERE key = 'balance'", (self.balance,))
        conn.commit()
        conn.close()

    async def analyze(self, ex, symbol):
        try:
            now_utc = datetime.utcnow().time()
            if not (time(14,0) <= now_utc <= time(22,0)):
                return None
            ohlcv_15 = await ex.fetch_ohlcv(symbol, timeframe='15m', limit=30)
            df_15 = pd.DataFrame(ohlcv_15, columns=['t','o','h','l','c','v'])
            ema_50_15 = df_15['c'].ewm(span=50).mean().iloc[-1]
            if df_15['c'].iloc[-1] < ema_50_15:
                return None
            ohlcv = await ex.fetch_ohlcv(symbol, timeframe='5m', limit=50)
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            sma = df['c'].rolling(20).mean()
            std = df['c'].rolling(20).std()
            upper_bb = sma + (2 * std)
            lower_bb = sma - (2 * std)
            bw = (upper_bb - lower_bb) / sma
            delta = df['c'].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))
            rsi_val = rsi.iloc[-1]
            if rsi_val < 30 or rsi_val > 85:
                return None
            atr = (df['h'].rolling(14).max() - df['l'].rolling(14).min()) / 14
            if atr.iloc[-1] / df['c'].iloc[-1] > 0.04:
                return None
            exp1 = df['c'].ewm(span=12).mean()
            exp2 = df['c'].ewm(span=26).mean()
            macd = exp1 - exp2
            macd_signal = macd.ewm(span=9).mean()
            macd_bullish = macd.iloc[-1] > macd_signal.iloc[-1] and macd.iloc[-2] <= macd_signal.iloc[-2]
            votes = []
            if bw.iloc[-1] < bw.rolling(30).min().iloc[-2] * 1.1:
                votes.append("Squeeze")
            if df['c'].iloc[-1] > sma.iloc[-1]:
                votes.append("Uptrend")
            if df['v'].iloc[-1] > df['v'].rolling(20).mean().iloc[-2] * 2:
                votes.append("Volume")
            if rsi_val > 55:
                votes.append("Momentum")
            if df['c'].iloc[-1] > upper_bb.iloc[-1]:
                votes.append("Breakout")
            if macd_bullish:
                votes.append("MACD")
            if len(votes) >= MIN_VOTES:
                expected_pump = round(bw.iloc[-1] * 100, 2)
                return TrainSignal(symbol, df['c'].iloc[-1], len(votes), votes, expected_pump)
            return None
        except:
            return None

    async def update_trades(self, ex):
        for sym, trade in list(self.active_trades.items()):
            try:
                ticker = await ex.fetch_ticker(sym)
                curr = ticker['last']
                pnl = (curr - trade.entry_price) / trade.entry_price * 100
                if curr > trade.highest_price:
                    trade.highest_price = curr
                if pnl >= TRAILING_ACTIVATE_PCT:
                    new_stop = trade.entry_price * (1 + (pnl - TRAILING_DISTANCE_PCT)/100)
                    if new_stop > trade.stop_loss:
                        trade.stop_loss = new_stop
                exit_reason = None
                if pnl <= -STOP_LOSS_PCT * 100:
                    exit_reason = "Stop Loss"
                elif curr >= trade.take_profit:
                    exit_reason = "Take Profit"
                elif curr <= trade.stop_loss and trade.stop_loss > trade.entry_price:
                    exit_reason = "Trailing Stop"
                if exit_reason:
                    self.balance += trade.invested * (1 + pnl/100)
                    self._save_balance()
                    with open(REAL_CSV, 'a', newline='') as f:
                        csv.writer(f).writerow([datetime.now().isoformat(), sym, trade.entry_price, curr, f"{pnl:.2f}"])
                    await send_tg(f"🏁 *Fermeture {sym}*\nProfit: `{pnl:.2f}%`\nRaison: {exit_reason}\nNouveau solde: {self.balance:.2f} USDT")
                    del self.active_trades[sym]
            except:
                pass

app = Flask(__name__)
engine = EmpireEngineV19Optimized()

@app.template_filter('duration')
def duration_filter(iso_time):
    diff = datetime.now() - datetime.fromisoformat(iso_time)
    return f"{diff.seconds // 60} min"

@app.route('/')
def dashboard():
    curr_prices = {s: t.highest_price for s, t in engine.active_trades.items()}
    html = """
    <html dir="rtl"><head><meta charset="UTF-8"><title>Empire V19 Optimized</title>
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
        <h2>💎 Empire Trading V19 - Optimisé</h2>
        <div class="status-bar">
            <div>Platforme: {{ stats.api_status }}</div>
            <div>Base donnée: {{ stats.db_status }}</div>
            <div>Scannés: {{ stats.scanned }} symboles</div>
            <div>Solde: {{ "%.2f"|format(balance) }} $</div>
        </div>
        <div class="grid">
            <div class="card">
                <h3>🟢 Trades ouverts ({{ active|length }}/10)</h3>
                <table>
                    <tr><th>Symbole</th><th>Entry</th><th>SL</th><th>TP</th><th>Durée</th><th>PNL flottant</th></tr>
                    {% for s, t in active.items() %}
                    <tr>
                        <td><b>{{ s }}</b> <span class="badge">{{ t.signal.votes }}/6</span></td>
                        <td>{{ t.entry_price }}</td>
                        <td style="color:#f87171">{{ "%.6f"|format(t.stop_loss) }}</td>
                        <td style="color:#4ade80">{{ "%.6f"|format(t.take_profit) }}</td>
                        <td>{{ t.entry_time|duration }}</td>
                        <td class="pnl-pos">{{ "%.2f"|format(((prices[s]-t.entry_price)/t.entry_price)*100) }}%</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
            <div class="card">
                <h3>🟡 Opportunités manquées</h3>
                <table>
                    <tr><th>Heure</th><th>Symbole</th><th>Force</th><th>Stratégies</th><th>Pump attendu</th></tr>
                    {% for m in missed %}
                    <tr>
                        <td>{{ m.time_found }}</td>
                        <td>{{ m.symbol }}</td>
                        <td><span class="badge">{{ m.votes }}/6</span></td>
                        <td style="font-size:0.7rem">{{ m.strategies|join(', ') }}</td>
                        <td style="color:#fbbf24">+{{ m.expected_pump }}%</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>
    </body></html>
    """
    return render_template_string(html, active=engine.active_trades, missed=engine.missed_trades[-10:], balance=engine.balance, stats=engine.stats, prices=curr_prices)

async def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    except:
        pass

async def main_loop():
    ex = ccxt_async.gateio({'enableRateLimit': True})
    engine.stats["api_status"] = "🟢"
    await send_tg("🚀 Bot V19 optimisé démarré")
    markets = await ex.fetch_markets()
    symbols = [m['symbol'] for m in markets if m['symbol'].endswith('/USDT') and m['active']]
    symbols = symbols[:TOTAL_SYMBOLS_TO_SCAN]
    while True:
        try:
            await engine.update_trades(ex)
            engine.stats["scanned"] = 0
            batch_size = 50
            random_symbols = np.random.choice(symbols, min(len(symbols), TOTAL_SYMBOLS_TO_SCAN), replace=False)
            for i in range(0, len(random_symbols), batch_size):
                batch = random_symbols[i:i+batch_size]
                tasks = [engine.analyze(ex, s) for s in batch]
                results = await asyncio.gather(*tasks)
                for sig in results:
                    if sig and sig.symbol not in engine.active_trades:
                        risk_amount = engine.balance * RISK_PER_TRADE
                        position_size = risk_amount / STOP_LOSS_PCT
                        invest = min(position_size, engine.balance)
                        if len(engine.active_trades) < MAX_CONCURRENT_TRADES and engine.balance >= invest:
                            stop_loss_price = sig.entry_price * (1 - STOP_LOSS_PCT)
                            take_profit_price = sig.entry_price * 1.06
                            trade = TradeInfo(sig.symbol, sig, sig.entry_price, invest, sig.entry_price, stop_loss_price, take_profit_price)
                            engine.active_trades[sig.symbol] = trade
                            engine.balance -= invest
                            engine._save_balance()
                            await send_tg(f"🟢 *Achat {sig.symbol}*\nPrix: {sig.entry_price:.8f}\nInvesti: {invest:.2f} USDT\nForce: {sig.votes}/6")
                        else:
                            if len(engine.missed_trades) > 100:
                                engine.missed_trades.pop(0)
                            engine.missed_trades.append(sig)
                            with open(MISSED_CSV, 'a', newline='') as f:
                                csv.writer(f).writerow([datetime.now().isoformat(), sig.symbol, sig.entry_price, "MISSED", sig.votes])
                engine.stats["scanned"] += len(batch)
                await asyncio.sleep(0.1)
        except Exception as e:
            print("Erreur:", e)
            await asyncio.sleep(5)
        await asyncio.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False), daemon=True).start()
    asyncio.run(main_loop())

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
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict

# =========================================================
# ⚙️ الإعدادات (Configuration)
# =========================================================
TELEGRAM_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"
TELEGRAM_CHAT_ID = "5067771509"

LOG_DIR = "/tmp/trading_logs"
DB_FILE = os.path.join(LOG_DIR, "empire_final_high_winrate.db")
os.makedirs(LOG_DIR, exist_ok=True)

INITIAL_BALANCE = 1000.0
RISK_PER_TRADE = 0.02
MAX_CONCURRENT_TRADES = 5
SCAN_INTERVAL = 90
BTC_PANIC_THRESHOLD = -3.0

# شروط الدخول
MIN_VOLUME_RATIO = 2.5
MIN_BREAKOUT_PERCENT = 2.0
RSI_FAST_PERIOD = 5
RSI_FAST_OVERSOLD = 30
RSI_FAST_RECOVER = 45
ATR_MULTIPLIER = 1.5
MIN_EXPECTED_GAIN = 5.0   # أقل نسبة صعود متوقعة للدخول (5%)

# معاملات الارتباط
CORRELATION_THRESHOLD = 0.7

# === استبعاد العملات الكبيرة ===
LARGE_COINS = [
    'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'SHIB', 'MATIC', 'DOT',
    'LINK', 'LTC', 'BCH', 'ETC', 'NEAR', 'ATOM', 'AVAX', 'UNI', 'FIL', 'ALGO',
    'VET', 'ICP', 'EGLD', 'THETA', 'SAND', 'MANA', 'AXS', 'GALA', 'ENJ', 'ZIL',
    'NEO', 'QTUM', 'ONT', 'IOST', 'CELR', 'KAVA', 'ZEC', 'XLM', 'TRX', 'EOS',
    'AAVE', 'MKR', 'COMP', 'SNX', 'CRV', '1INCH', 'SUSHI', 'CAKE', 'BAKE'
]

# === إعدادات المسح الشامل ===
BATCH_SIZE = 50
TOTAL_SCAN_TARGET = 1500

# =========================================================
# 🏗️ الهياكل البرمجية
# =========================================================
@dataclass
class TrainSignal:
    symbol: str
    entry_price: float
    rsi_fast: float
    rsi_slow: float
    vol_ratio: float
    atr: float
    expected_gain: float          # النسبة المئوية المتوقعة للصعود
    reasons: list
    timeframe_confirmation: dict

@dataclass
class TradeInfo:
    symbol: str
    signal: TrainSignal
    entry_price: float
    invested: float
    highest_price: float
    stop_loss: float
    partial_taken_5: bool
    partial_taken_7: bool
    entry_time: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

# =========================================================
# 🚀 المحرك النهائي مع تحليل الهدف المتوقع
# =========================================================
class FinalHighWinRateEngine:
    def __init__(self):
        self.active_trades = {}
        self.closed_trades = []
        self.cooldown_list = {}
        self.balance = INITIAL_BALANCE
        self.panic_mode = False
        self.all_usdt_symbols = []
        self.scan_index = 0
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("CREATE TABLE IF NOT EXISTS trades (symbol TEXT PRIMARY KEY, data TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT)")
            conn.commit()
            conn.close()
        except:
            pass

    async def send_tg(self, msg: str):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except:
            print("Telegram Error")

    async def check_btc_panic(self, ex):
        try:
            ticker = await ex.fetch_ticker('BTC/USDT')
            change = ticker.get('percentage', 0)
            if change <= BTC_PANIC_THRESHOLD and not self.panic_mode:
                self.panic_mode = True
                await self.send_tg("⚠️ *وضع الهلع النشط*: BTC يهبط بشدة، تم تعليق الدخول الجديد.")
            elif change > BTC_PANIC_THRESHOLD:
                self.panic_mode = False
            return self.panic_mode
        except:
            return False

    def is_news_time(self):
        now = datetime.now()
        if now.weekday() == 2 and now.hour == 14 and now.minute >= 30:
            return True
        if now.weekday() == 4 and now.hour == 14 and now.minute >= 30:
            return True
        return False

    async def is_highly_correlated(self, ex, symbol1, symbol2):
        try:
            ohlcv1 = await ex.fetch_ohlcv(symbol1, timeframe='1h', limit=50)
            ohlcv2 = await ex.fetch_ohlcv(symbol2, timeframe='1h', limit=50)
            if len(ohlcv1) < 20 or len(ohlcv2) < 20:
                return False
            df1 = pd.DataFrame(ohlcv1)[3].pct_change().dropna()
            df2 = pd.DataFrame(ohlcv2)[3].pct_change().dropna()
            if len(df1) < 10 or len(df2) < 10:
                return False
            corr = df1.corr(df2)
            return abs(corr) > CORRELATION_THRESHOLD
        except:
            return False

    async def get_multi_timeframe_data(self, ex, symbol):
        try:
            ohlcv5 = await ex.fetch_ohlcv(symbol, timeframe='5m', limit=100)
            ohlcv15 = await ex.fetch_ohlcv(symbol, timeframe='15m', limit=100)
            ohlcv1h = await ex.fetch_ohlcv(symbol, timeframe='1h', limit=200)
            df5 = pd.DataFrame(ohlcv5, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df15 = pd.DataFrame(ohlcv15, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df1h = pd.DataFrame(ohlcv1h, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            return df5, df15, df1h
        except:
            return None, None, None

    def calc_indicators(self, df):
        if df is None or len(df) < 30:
            return None
        close = df['c']
        high = df['h']
        low = df['l']
        volume = df['v']

        # RSI سريع
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=RSI_FAST_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_FAST_PERIOD).mean()
        rs = gain / (loss + 1e-9)
        rsi_fast = 100 - (100 / (1 + rs)).iloc[-1]

        # RSI بطيء
        delta14 = close.diff()
        gain14 = (delta14.where(delta14 > 0, 0)).rolling(window=14).mean()
        loss14 = (-delta14.where(delta14 < 0, 0)).rolling(window=14).mean()
        rs14 = gain14 / (loss14 + 1e-9)
        rsi_slow = 100 - (100 / (1 + rs14)).iloc[-1]

        # MACD
        exp1 = close.ewm(span=12, adjust=False).mean()
        exp2 = close.ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_cross_up = (macd.iloc[-1] > signal.iloc[-1]) and (macd.iloc[-2] <= signal.iloc[-2])

        # Bollinger
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = sma20 + 2 * std20
        bb_breakout = close.iloc[-1] > upper_bb.iloc[-1] * (1 + MIN_BREAKOUT_PERCENT / 100)
        bb_position = (close.iloc[-1] - sma20.iloc[-1]) / (std20.iloc[-1] + 1e-9)  # عدد الانحرافات المعيارية

        # ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]

        # حجم
        avg_volume = volume.iloc[-20:-1].mean()
        vol_ratio = volume.iloc[-1] / (avg_volume + 1e-9)

        # EMA200
        ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
        above_ema200 = close.iloc[-1] > ema200

        # الزخم (معدل التغير لـ 3 شمعات)
        momentum = (close.iloc[-1] / close.iloc[-4] - 1) * 100 if len(close) >= 4 else 0

        return {
            "rsi_fast": rsi_fast,
            "rsi_slow": rsi_slow,
            "macd_cross_up": macd_cross_up,
            "bb_breakout": bb_breakout,
            "bb_position": bb_position,
            "atr": atr,
            "vol_ratio": vol_ratio,
            "above_ema200": above_ema200,
            "momentum": momentum,
            "close": close.iloc[-1]
        }

    def calculate_expected_gain(self, ind5, ind15, ind1h, vol_ratio, bb_position):
        """
        حساب نسبة الصعود المتوقعة بناءً على:
        - قوة الاختراق (bb_position): كلما زادت الانحرافات المعيارية، زاد الهدف.
        - نسبة الحجم (vol_ratio): حجم أكبر يعني زخم أقوى.
        - زخم السعر (momentum): سرعة التحرك.
        - الاتجاه العام: قوة الترند على الإطار اليومي (هنا نكتفي بـ 1h).
        """
        # الأساس: 3% كحد أدنى للاختراق الجيد
        base_gain = 3.0
        
        # مكافأة اختراق Bollinger (كل 0.5 انحراف معياري يضيف 1%)
        bb_bonus = max(0, (bb_position - 1.5)) * 2   # مثال: عند 2.5 انحراف معياري → +2%
        
        # مكافأة الحجم (كل 1 ضعف حجم يضيف 0.5%، بحد أقصى 4%)
        vol_bonus = min(4.0, (vol_ratio - 2.0) * 0.8)
        
        # مكافأة الزخم (كل 1% تغير في 3 شمعات يضيف 0.5%)
        mom_bonus = max(0, ind5['momentum'] * 0.5)
        
        # مكافأة الترند القوي (إذا كان السعر بعيداً عن EMA200 بنسبة >3%)
        if ind1h['above_ema200']:
            ema_distance = (ind1h['close'] / ind1h.get('ema200', ind1h['close'])) - 1 if 'ema200' in ind1h else 0
            trend_bonus = min(2.0, max(0, ema_distance * 10))
        else:
            trend_bonus = 0
        
        total_gain = base_gain + bb_bonus + vol_bonus + mom_bonus + trend_bonus
        
        # لا نتجاوز 15% تقديراً واقعياً
        return min(total_gain, 15.0)

    async def analyze_opportunity(self, ex, symbol):
        if symbol in self.cooldown_list and datetime.now() < self.cooldown_list[symbol]:
            return None
        if self.is_news_time():
            return None

        for active_sym in self.active_trades:
            if await self.is_highly_correlated(ex, symbol, active_sym):
                return None

        df5, df15, df1h = await self.get_multi_timeframe_data(ex, symbol)
        if df5 is None or df15 is None or df1h is None:
            return None

        ind5 = self.calc_indicators(df5)
        ind15 = self.calc_indicators(df15)
        ind1h = self.calc_indicators(df1h)
        if not (ind5 and ind15 and ind1h):
            return None

        reasons = []
        
        # 1. الاتجاه العام صاعد على الساعة
        if not ind1h["above_ema200"]:
            return None
        reasons.append("الاتجاه العام صاعد (1H فوق EMA200)")

        # 2. حجم مفاجئ
        if ind5["vol_ratio"] < MIN_VOLUME_RATIO:
            return None
        reasons.append(f"حجم مفاجئ x{ind5['vol_ratio']:.1f}")

        # 3. اختراق Bollinger العلوي
        if not ind5["bb_breakout"]:
            return None
        reasons.append("اختراق Bollinger العلوي")

        # 4. انعكاس RSI السريع
        delta5 = df5['c'].diff()
        gain5 = (delta5.where(delta5 > 0, 0)).rolling(RSI_FAST_PERIOD).mean()
        loss5 = (-delta5.where(delta5 < 0, 0)).rolling(RSI_FAST_PERIOD).mean()
        rs5 = gain5 / (loss5 + 1e-9)
        rsi_fast_prev = 100 - (100 / (1 + rs5)).iloc[-2]
        rsi_fast_now = ind5["rsi_fast"]
        if not (rsi_fast_prev < RSI_FAST_OVERSOLD and rsi_fast_now > RSI_FAST_RECOVER):
            return None
        reasons.append(f"انعكاس RSI سريع ({rsi_fast_prev:.0f} → {rsi_fast_now:.0f})")

        # 5. تقاطع MACD على 5m و 15m
        if not (ind5["macd_cross_up"] and ind15["macd_cross_up"]):
            return None
        reasons.append("تقاطع MACD صاعد على 5m و 15m")

        # 6. RSI بطيء بين 40 و 70
        if not (40 < ind5["rsi_slow"] < 70):
            return None
        reasons.append(f"RSI بطيء {ind5['rsi_slow']:.0f}")

        # 7. زيادة ATR
        atr_prev = df5['h'].rolling(14).apply(lambda x: x.max() - x.min(), raw=False).iloc[-2] if len(df5) > 15 else ind5["atr"]
        if ind5["atr"] <= atr_prev * 0.95:
            return None
        reasons.append("زيادة التقلب (ATR)")

        # ---- حساب النسبة المتوقعة للصعود ----
        expected_gain = self.calculate_expected_gain(
            ind5, ind15, ind1h,
            vol_ratio=ind5["vol_ratio"],
            bb_position=ind5.get("bb_position", 1.5)
        )
        
        # إذا كان الهدف المتوقع أقل من 5%، نرفض الإشارة ولا نرسل إشعار
        if expected_gain < MIN_EXPECTED_GAIN:
            # لا نرسل شيئاً، فقط نتجاهل
            return None
        reasons.append(f"🚀 الهدف المتوقع: {expected_gain:.1f}%")

        signal = TrainSignal(
            symbol=symbol,
            entry_price=ind5["close"],
            rsi_fast=rsi_fast_now,
            rsi_slow=ind5["rsi_slow"],
            vol_ratio=ind5["vol_ratio"],
            atr=ind5["atr"],
            expected_gain=expected_gain,
            reasons=reasons,
            timeframe_confirmation={"5m": "bullish", "15m": "bullish", "1h": "bullish"}
        )
        return signal

    # باقي الدوال (monitor_trades, save, load, scan_batch, reentry_smart) كما هي دون تغيير...
    # (للاختصار، سأكتبها مختصرة، لكن في الكود النهائي ستكون كاملة كما في النسخة السابقة)
    # ... (يمكنك نسخ بقية الدوال من الرد السابق، فهي لم تتغير)

    async def monitor_trades(self, ex):
        panic = await self.check_btc_panic(ex)
        for sym, trade in list(self.active_trades.items()):
            try:
                ticker = await ex.fetch_ticker(sym)
                current_price = ticker['last']
                pnl_pct = (current_price - trade.entry_price) / trade.entry_price * 100

                if current_price > trade.highest_price:
                    trade.highest_price = current_price

                if pnl_pct >= 5.0 and not trade.partial_taken_5:
                    close_amount = trade.invested * 0.3
                    self.balance += close_amount * (1 + pnl_pct/100)
                    trade.invested -= close_amount
                    trade.partial_taken_5 = True
                    await self.send_tg(f"📌 *جني أرباح 30%* عند +5% لـ {sym} (ربح {pnl_pct:.2f}%)")

                elif pnl_pct >= 7.0 and not trade.partial_taken_7:
                    close_amount = trade.invested * 0.3
                    self.balance += close_amount * (1 + pnl_pct/100)
                    trade.invested -= close_amount
                    trade.partial_taken_7 = True
                    await self.send_tg(f"📌 *جني أرباح 30% إضافية* عند +7% لـ {sym} (ربح {pnl_pct:.2f}%)")

                if trade.stop_loss == 0:
                    initial_sl = max(trade.entry_price - (trade.signal.atr * ATR_MULTIPLIER), trade.entry_price * 0.975)
                    trade.stop_loss = initial_sl
                else:
                    if pnl_pct >= 4.0:
                        new_stop = trade.highest_price * 0.985
                        if new_stop > trade.stop_loss:
                            trade.stop_loss = new_stop
                            await self.send_tg(f"🔒 تحديث الوقف المتحرك لـ {sym} → {trade.stop_loss:.4f}")

                if current_price <= trade.stop_loss:
                    final_pnl = (current_price - trade.entry_price) / trade.entry_price * 100
                    result = {
                        "symbol": sym,
                        "pnl": round(final_pnl, 2),
                        "exit_price": current_price,
                        "exit_reason": "Stop Loss",
                        "time": datetime.now().strftime("%H:%M:%S")
                    }
                    self.balance += trade.invested * (1 + final_pnl/100)
                    self.closed_trades.insert(0, result)
                    self.cooldown_list[sym] = datetime.now() + timedelta(hours=1)
                    self._save_closed_trade(result)
                    del self.active_trades[sym]
                    await self.send_tg(f"🏁 *إغلاق صفقة* {sym}\nالنتيجة: `{final_pnl:.2f}%`\nالسبب: وقف الخسارة")
                    continue

                if pnl_pct >= 10.0 and not panic:
                    final_pnl = pnl_pct
                    result = {
                        "symbol": sym,
                        "pnl": round(final_pnl, 2),
                        "exit_price": current_price,
                        "exit_reason": "Take Profit 10%",
                        "time": datetime.now().strftime("%H:%M:%S")
                    }
                    self.balance += trade.invested * (1 + final_pnl/100)
                    self.closed_trades.insert(0, result)
                    self._save_closed_trade(result)
                    del self.active_trades[sym]
                    await self.send_tg(f"💰 *جني أرباح كامل* {sym} → +{final_pnl:.2f}%")
                    continue

                self._save_trade_state(trade)
            except Exception as e:
                print(f"Error monitoring {sym}: {e}")

    def _save_trade_state(self, trade):
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("INSERT OR REPLACE INTO trades VALUES (?, ?)", (trade.symbol, json.dumps(asdict(trade))))
            conn.commit()
            conn.close()
        except:
            pass

    def _save_closed_trade(self, result):
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("INSERT INTO history (data) VALUES (?)", (json.dumps(result),))
            conn.commit()
            conn.close()
        except:
            pass

    def load_all(self):
        try:
            conn = sqlite3.connect(DB_FILE)
            rows = conn.execute("SELECT data FROM trades").fetchall()
            for r in rows:
                d = json.loads(r[0])
                sig_dict = d['signal']
                sig = TrainSignal(**sig_dict)
                trade = TradeInfo(**{k: v for k, v in d.items() if k != 'signal'}, signal=sig)
                self.active_trades[trade.symbol] = trade
                self.balance -= trade.invested
            hist = conn.execute("SELECT data FROM history ORDER BY id DESC LIMIT 20").fetchall()
            self.closed_trades = [json.loads(h[0]) for h in hist]
            conn.close()
        except:
            print("بداية جديدة ...")

    async def refresh_symbol_list(self, ex):
        try:
            markets = await ex.fetch_markets()
            all_symbols = [m['symbol'] for m in markets if m['symbol'].endswith('/USDT') and m['active']]
            filtered = [sym for sym in all_symbols if sym.split('/')[0] not in LARGE_COINS]
            self.all_usdt_symbols = filtered
            print(f"تم تحميل {len(self.all_usdt_symbols)} عملة صغيرة")
            await self.send_tg(f"🔄 تم تحديث قائمة العملات الصغيرة: {len(self.all_usdt_symbols)} عملة.")
            import random
            random.shuffle(self.all_usdt_symbols)
        except Exception as e:
            print(f"خطأ في جلب قائمة العملات: {e}")

    async def scan_batch(self, ex, batch_symbols):
        signals = []
        for sym in batch_symbols:
            if sym in self.active_trades:
                continue
            try:
                ticker = await ex.fetch_ticker(sym)
                if ticker.get('quoteVolume', 0) < 50000 or ticker.get('percentage', -100) < 0.5:
                    continue
            except:
                continue
            signal = await self.analyze_opportunity(ex, sym)
            if signal:
                signals.append(signal)
            await asyncio.sleep(0.3)
        return signals

    async def reentry_smart(self, ex, closed_trade):
        if closed_trade.get("pnl", 0) >= 4.0:
            sym = closed_trade["symbol"]
            if sym in self.cooldown_list and datetime.now() < self.cooldown_list[sym]:
                return None
            signal = await self.analyze_opportunity(ex, sym)
            if signal:
                return signal
        return None

# =========================================================
# 🌐 واجهة الويب
# =========================================================
engine = FinalHighWinRateEngine()
app = Flask(__name__)

@app.route('/')
def index():
    return render_template_string("""
    <body style="background:#020617; color:#f1f5f9; font-family:sans-serif; padding:20px;">
        <h1 style="color:#38bdf8;">🔥 Empire Target Gain Filter (≥5%) 🔥</h1>
        <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:20px; margin:20px 0;">
            <div style="background:#1e293b; padding:20px; border-radius:12px;">
                <small>الرصيد الورقي</small><br><strong style="font-size:1.8rem;">{{ "%.2f"|format(balance) }} USDT</strong>
            </div>
            <div style="background:#1e293b; padding:20px; border-radius:12px;">
                <small>صفقات نشطة</small><br><strong style="font-size:1.8rem;">{{ active|length }} / 5</strong>
            </div>
            <div style="background:#1e293b; padding:20px; border-radius:12px;">
                <small>وضع السوق</small><br><strong style="color:{{ '#f87171' if panic else '#4ade80' }};">{{ '⚠️ PANIC' if panic else '✅ STABLE' }}</strong>
            </div>
        </div>
        <h3>🚀 الصفقات المفتوحة</h3>
        <table style="width:100%; background:#0f172a;">
            <tr style="background:#334155;"><th>العملة</th><th>الدخول</th><th>الهدف المتوقع</th><th>الربح الحالي%</th></tr>
            {% for sym, t in active.items() %}
            <tr>
                <td>{{ sym }}</td><td>{{ t.entry_price }}</td>
                <td style="color:#facc15;">{{ "%.1f"|format(t.signal.expected_gain) }}%</td>
                <td style="color:{{ '#4ade80' if t.highest_price > t.entry_price else '#f87171' }};">{{ "%.2f"|format(((t.highest_price - t.entry_price)/t.entry_price)*100) }}%</td>
            </tr>
            {% endfor %}
        </table>
        <h3 style="margin-top:30px;">📊 آخر 10 صفقات مغلقة</h3>
        <div style="display:flex; flex-wrap:wrap; gap:10px;">
            {% for h in history[:10] %}
            <div style="background:#0f172a; padding:10px; border-radius:8px; width:180px;">
                <b>{{ h.symbol }}</b> <span style="color:{{ '#4ade80' if h.pnl > 0 else '#f87171' }};">{{ h.pnl }}%</span><br>
                <small>{{ h.exit_reason }} @ {{ h.time }}</small>
            </div>
            {% endfor %}
        </div>
    </body>
    """, balance=engine.balance, active=engine.active_trades, panic=engine.panic_mode, history=engine.closed_trades)

@app.route('/health')
def health(): return "Empire Target Gain Filter Online", 200

def keep_alive():
    while True:
        try:
            port = os.environ.get("PORT", 8080)
            httpx.get(f"http://localhost:{port}/health")
        except:
            pass
        time.sleep(600)

async def main_loop():
    ex = ccxt_async.gateio({'enableRateLimit': True})
    engine.load_all()
    await engine.send_tg("🚀 *تشغيل الماسح الإمبراطوري - فلتر الهدف ≥5%* \n✅ استبعاد العملات الكبيرة\n✅ مسح 1500 عملة\n✅ الدخول فقط إذا كان الهدف المتوقع ≥5%")
    await engine.refresh_symbol_list(ex)
    if not engine.all_usdt_symbols:
        await engine.send_tg("⚠️ لم يتم العثور على عملات صغيرة.")
        return
    
    total_symbols = len(engine.all_usdt_symbols)
    scan_limit = min(TOTAL_SCAN_TARGET, total_symbols)
    import random
    random.shuffle(engine.all_usdt_symbols)
    last_closed_count = len(engine.closed_trades)
    batch_index = 0
    
    while True:
        await engine.monitor_trades(ex)
        
        if datetime.now().minute == 0:
            await engine.refresh_symbol_list(ex)
            random.shuffle(engine.all_usdt_symbols)
            batch_index = 0
        
        if len(engine.closed_trades) > last_closed_count:
            for closed in engine.closed_trades[:1]:
                new_signal = await engine.reentry_smart(ex, closed)
                if new_signal and len(engine.active_trades) < MAX_CONCURRENT_TRADES and not engine.panic_mode:
                    trade_amount = engine.balance * RISK_PER_TRADE * 3
                    trade = TradeInfo(
                        symbol=new_signal.symbol,
                        signal=new_signal,
                        entry_price=new_signal.entry_price,
                        invested=trade_amount,
                        highest_price=new_signal.entry_price,
                        stop_loss=0,
                        partial_taken_5=False,
                        partial_taken_7=False
                    )
                    engine.active_trades[new_signal.symbol] = trade
                    engine.balance -= trade_amount
                    engine._save_trade_state(trade)
                    await engine.send_tg(f"🔄 *إعادة دخول ذكي* على {new_signal.symbol} (هدف {new_signal.expected_gain:.1f}%)")
            last_closed_count = len(engine.closed_trades)
        
        if not engine.panic_mode and len(engine.active_trades) < MAX_CONCURRENT_TRADES:
            start = batch_index * BATCH_SIZE
            end = min(start + BATCH_SIZE, scan_limit)
            if start >= scan_limit:
                batch_index = 0
                start = 0
                end = min(BATCH_SIZE, scan_limit)
            
            batch_symbols = engine.all_usdt_symbols[start:end]
            if batch_symbols:
                print(f"مسح الدفعة {batch_index+1}: {len(batch_symbols)} عملة")
                signals = await engine.scan_batch(ex, batch_symbols)
                for sig in signals:
                    if len(engine.active_trades) >= MAX_CONCURRENT_TRADES:
                        break
                    trade_amount = engine.balance * RISK_PER_TRADE * 3
                    trade = TradeInfo(
                        symbol=sig.symbol,
                        signal=sig,
                        entry_price=sig.entry_price,
                        invested=trade_amount,
                        highest_price=sig.entry_price,
                        stop_loss=0,
                        partial_taken_5=False,
                        partial_taken_7=False
                    )
                    engine.active_trades[sig.symbol] = trade
                    engine.balance -= trade_amount
                    engine._save_trade_state(trade)
                    reasons_text = "\n".join([f"• {r}" for r in sig.reasons])
                    await engine.send_tg(
                        f"🔔 *إشارة انفجار قوية* (هدف {sig.expected_gain:.1f}%)\n"
                        f"العملة: `{sig.symbol}`\n"
                        f"السعر: `{sig.entry_price}`\n"
                        f"نسبة الحجم: `{sig.vol_ratio:.2f}x`\n"
                        f"RSI السريع: `{sig.rsi_fast:.0f}`\n"
                        f"الأسباب:\n{reasons_text}"
                    )
                    await asyncio.sleep(1)
                batch_index += 1
        
        await asyncio.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    asyncio.run(main_loop())

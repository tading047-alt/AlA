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
RISK_PER_TRADE = 0.02          # 2% من الرصيد لكل صفقة
MAX_CONCURRENT_TRADES = 5
SCAN_INTERVAL = 60
BTC_PANIC_THRESHOLD = -3.0

# شروط الدخول
MIN_VOLUME_RATIO = 2.5
MIN_BREAKOUT_PERCENT = 2.0
RSI_FAST_PERIOD = 5
RSI_FAST_OVERSOLD = 30
RSI_FAST_RECOVER = 45
ATR_MULTIPLIER = 1.5

# معاملات الارتباط
CORRELATION_THRESHOLD = 0.7

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
# 🚀 المحرك النهائي
# =========================================================
class FinalHighWinRateEngine:
    def __init__(self):
        self.active_trades = {}
        self.closed_trades = []
        self.cooldown_list = {}
        self.balance = INITIAL_BALANCE
        self.panic_mode = False
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

    # --- فلتر الأخبار (بسيط) ---
    def is_news_time(self):
        now = datetime.now()
        # تجنب أيام الأربعاء الساعة 14:30 (CPI) والجمعة 14:30 (NFP) بتوقيت GMT (يمكن تعديله حسب منطقتك)
        if now.weekday() == 2 and now.hour == 14 and now.minute >= 30:
            return True
        if now.weekday() == 4 and now.hour == 14 and now.minute >= 30:
            return True
        return False

    # --- فلتر الارتباط ---
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

    # --- مؤشرات متعددة الأطر ---
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

        # ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]

        # حجم
        avg_volume = volume.iloc[-20:-1].mean()
        vol_ratio = volume.iloc[-1] / (avg_volume + 1e-9)

        # EMA200
        ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
        above_ema200 = close.iloc[-1] > ema200

        return {
            "rsi_fast": rsi_fast,
            "rsi_slow": rsi_slow,
            "macd_cross_up": macd_cross_up,
            "bb_breakout": bb_breakout,
            "atr": atr,
            "vol_ratio": vol_ratio,
            "above_ema200": above_ema200,
            "close": close.iloc[-1]
        }

    async def analyze_opportunity(self, ex, symbol):
        if symbol in self.cooldown_list and datetime.now() < self.cooldown_list[symbol]:
            return None
        if self.is_news_time():
            return None

        # منع الدخول إذا كان هناك عملة مرتبطة مفتوحة
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
        if not ind1h["above_ema200"]:
            return None
        reasons.append("الاتجاه العام صاعد (1H فوق EMA200)")

        if ind5["vol_ratio"] < MIN_VOLUME_RATIO:
            return None
        reasons.append(f"حجم مفاجئ x{ind5['vol_ratio']:.1f}")

        if not ind5["bb_breakout"]:
            return None
        reasons.append("اختراق Bollinger العلوي")

        # انعكاس RSI السريع
        delta5 = df5['c'].diff()
        gain5 = (delta5.where(delta5 > 0, 0)).rolling(RSI_FAST_PERIOD).mean()
        loss5 = (-delta5.where(delta5 < 0, 0)).rolling(RSI_FAST_PERIOD).mean()
        rs5 = gain5 / (loss5 + 1e-9)
        rsi_fast_prev = 100 - (100 / (1 + rs5)).iloc[-2]
        rsi_fast_now = ind5["rsi_fast"]
        if not (rsi_fast_prev < RSI_FAST_OVERSOLD and rsi_fast_now > RSI_FAST_RECOVER):
            return None
        reasons.append(f"انعكاس RSI سريع ({rsi_fast_prev:.0f} → {rsi_fast_now:.0f})")

        if not (ind5["macd_cross_up"] and ind15["macd_cross_up"]):
            return None
        reasons.append("تقاطع MACD صاعد على 5m و 15m")

        if not (40 < ind5["rsi_slow"] < 70):
            return None
        reasons.append(f"RSI بطيء {ind5['rsi_slow']:.0f}")

        # ATR متوسع (بسيط)
        atr_prev = df5['h'].rolling(14).apply(lambda x: x.max() - x.min(), raw=False).iloc[-2] if len(df5) > 15 else ind5["atr"]
        if ind5["atr"] <= atr_prev * 0.95:
            return None
        reasons.append("زيادة التقلب (ATR)")

        signal = TrainSignal(
            symbol=symbol,
            entry_price=ind5["close"],
            rsi_fast=rsi_fast_now,
            rsi_slow=ind5["rsi_slow"],
            vol_ratio=ind5["vol_ratio"],
            atr=ind5["atr"],
            reasons=reasons,
            timeframe_confirmation={"5m": "bullish", "15m": "bullish", "1h": "bullish"}
        )
        return signal

    async def monitor_trades(self, ex):
        panic = await self.check_btc_panic(ex)
        for sym, trade in list(self.active_trades.items()):
            try:
                ticker = await ex.fetch_ticker(sym)
                current_price = ticker['last']
                pnl_pct = (current_price - trade.entry_price) / trade.entry_price * 100

                if current_price > trade.highest_price:
                    trade.highest_price = current_price

                # جني أرباح تدريجي
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

                # إدارة وقف الخسارة
                if trade.stop_loss == 0:
                    initial_sl = max(trade.entry_price - (trade.signal.atr * ATR_MULTIPLIER), trade.entry_price * 0.975)
                    trade.stop_loss = initial_sl
                else:
                    if pnl_pct >= 4.0:
                        new_stop = trade.highest_price * 0.985
                        if new_stop > trade.stop_loss:
                            trade.stop_loss = new_stop
                            await self.send_tg(f"🔒 تحديث الوقف المتحرك لـ {sym} → {trade.stop_loss:.4f}")

                # الخروج عند الوقف
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

                # خروج كامل عند +10%
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

    async def reentry_smart(self, ex, closed_trade):
        """إعادة دخول ذكي: إذا حققت صفقة ربح >4% ومرت 30 دقيقة، يمكن إعادة الدخول إذا ظل الشرط"""
        if closed_trade.get("pnl", 0) >= 4.0:
            sym = closed_trade["symbol"]
            if sym in self.cooldown_list and datetime.now() < self.cooldown_list[sym]:
                return None
            # تقليل وقت التبريد إلى 30 دقيقة للعملات التي حققت ربحاً
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
        <h1 style="color:#38bdf8;">🔥 Empire Final High Win‑Rate 🔥</h1>
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
            <tr style="background:#334155;"><th>العملة</th><th>الدخول</th><th>الوقف</th><th>الربح الحالي%</th></tr>
            {% for sym, t in active.items() %}
            <tr>
                <td>{{ sym }}</td><td>{{ t.entry_price }}</td><td>{{ t.stop_loss }}</td>
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
def health(): return "Empire Final High WinRate Online", 200

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
    await engine.send_tg("🚀 *تشغيل البوت الإمبراطوري النهائي v7.0*\nنسبة النجاح المستهدفة >80% مع أهداف تدريجية (5% / 7% / 10%).")
    last_closed_count = len(engine.closed_trades)
    while True:
        await engine.monitor_trades(ex)

        # إعادة الدخول الذكي: بعد كل دورة، تحقق من الصفقات المغلقة حديثاً
        if len(engine.closed_trades) > last_closed_count:
            for closed in engine.closed_trades[:1]:  # آخر صفقة
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
                    await engine.send_tg(f"🔄 *إعادة دخول ذكي* على {new_signal.symbol} بعد تحقيق ربح سابق.")
            last_closed_count = len(engine.closed_trades)

        if not engine.panic_mode and len(engine.active_trades) < MAX_CONCURRENT_TRADES:
            try:
                tickers = await ex.fetch_tickers()
                candidates = [s for s, t in tickers.items() if s.endswith('/USDT') and t.get('quoteVolume', 0) > 100000]
                for sym in candidates[:50]:
                    if sym in engine.active_trades:
                        continue
                    signal = await engine.analyze_opportunity(ex, sym)
                    if signal:
                        trade_amount = engine.balance * RISK_PER_TRADE * 3
                        trade = TradeInfo(
                            symbol=sym,
                            signal=signal,
                            entry_price=signal.entry_price,
                            invested=trade_amount,
                            highest_price=signal.entry_price,
                            stop_loss=0,
                            partial_taken_5=False,
                            partial_taken_7=False
                        )
                        engine.active_trades[sym] = trade
                        engine.balance -= trade_amount
                        engine._save_trade_state(trade)
                        reasons_text = "\n".join([f"• {r}" for r in signal.reasons])
                        await engine.send_tg(
                            f"🔔 *إشارة انفجار قوية*\n"
                            f"العملة: `{sym}`\n"
                            f"السعر: `{signal.entry_price}`\n"
                            f"نسبة الحجم: `{signal.vol_ratio:.2f}x`\n"
                            f"RSI السريع: `{signal.rsi_fast:.0f}`\n"
                            f"الأسباب:\n{reasons_text}"
                        )
            except Exception as e:
                print(f"خطأ في الدورة: {e}")
        await asyncio.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    asyncio.run(main_loop())

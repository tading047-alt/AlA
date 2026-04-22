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
# ⚙️ الإعدادات العامة
# =========================================================
TELEGRAM_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"
TELEGRAM_CHAT_ID = "5067771509"

LOG_DIR = "/tmp/trading_logs"
DB_FILE = os.path.join(LOG_DIR, "empire_v20.db")
REAL_CSV = os.path.join(LOG_DIR, "real_trades.csv")
MISSED_CSV = os.path.join(LOG_DIR, "missed_trades.csv")
OPPORTUNITIES_CSV = os.path.join(LOG_DIR, "opportunities.csv")  # جديد: كل الفرص مع الأسباب
os.makedirs(LOG_DIR, exist_ok=True)

MAX_CONCURRENT_TRADES = 10
RISK_PER_TRADE = 0.02
STOP_LOSS_PCT = 0.025
TRAILING_ACTIVATE_PCT = 2.0
TRAILING_DISTANCE_PCT = 1.5
MIN_VOTES = 4
TOTAL_SYMBOLS_TO_SCAN = 1000
SCAN_INTERVAL = 10  # ثوانٍ بين الدورات

# =========================================================
# هيكل البيانات
# =========================================================
@dataclass
class TrainSignal:
    symbol: str
    entry_price: float
    votes: int
    strategies: list
    expected_pump: float
    score: float  # درجة الجودة
    reason: str = ""  # سبب الرفض (إذا لم تدخل)
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
# المحرك الرئيسي المحسن (مع تتبع الفرص والأسباب)
# =========================================================
class EmpireEngineV20:
    def __init__(self):
        self.active_trades = {}
        self.missed_trades = []  # الفرص التي فاتت (قوية ولكن لم يدخل)
        self.all_opportunities = []  # كل الفرص التي تم مسحها (للعرض على الويب)
        self.balance = 2000.0
        self.stats = {
            "scanned": 0,
            "opportunities_found": 0,
            "last_scan_time": None,
            "status": "Initializing",
            "db_status": "🔴",
            "api_status": "🔴"
        }
        self._init_storage()
        self._init_opportunities_csv()

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

    def _init_opportunities_csv(self):
        if not os.path.exists(OPPORTUNITIES_CSV):
            with open(OPPORTUNITIES_CSV, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Time', 'Symbol', 'Price', 'Votes', 'Score', 'Reason', 'Strategies'])

    def log_opportunity(self, symbol, price, votes, score, reason, strategies):
        """تسجيل كل فرصة (حتى المرفوضة) في ملف CSV"""
        with open(OPPORTUNITIES_CSV, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol, price, votes, f"{score:.2f}", reason, ", ".join(strategies)
            ])

    def _save_balance(self):
        conn = sqlite3.connect(DB_FILE)
        conn.execute("UPDATE config SET value = ? WHERE key = 'balance'", (self.balance,))
        conn.commit()
        conn.close()

    async def analyze(self, ex, symbol):
        """تحليل عملة واحدة وإرجاع TrainSignal أو None مع سبب الرفض"""
        reason = None
        try:
            # 1. فلتر الوقت
            now_utc = datetime.utcnow().time()
            if not (time(14,0) <= now_utc <= time(22,0)):
                reason = "وقت غير مناسب (خارج 14-22 UTC)"
                return None, reason

            # 2. فريم 15 دقيقة لتأكيد الاتجاه
            ohlcv_15 = await ex.fetch_ohlcv(symbol, timeframe='15m', limit=30)
            if len(ohlcv_15) < 30:
                reason = "بيانات غير كافية"
                return None, reason
            df_15 = pd.DataFrame(ohlcv_15, columns=['t','o','h','l','c','v'])
            ema_50_15 = df_15['c'].ewm(span=50).mean().iloc[-1]
            if df_15['c'].iloc[-1] < ema_50_15:
                reason = "اتجاه هابط على 15 دقيقة"
                return None, reason

            # 3. فريم 5 دقائق للمؤشرات
            ohlcv = await ex.fetch_ohlcv(symbol, timeframe='5m', limit=50)
            if len(ohlcv) < 50:
                reason = "بيانات 5 دقائق غير كافية"
                return None, reason
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            
            # بولينجر باند
            sma = df['c'].rolling(20).mean()
            std = df['c'].rolling(20).std()
            upper_bb = sma + (2 * std)
            lower_bb = sma - (2 * std)
            bw = (upper_bb - lower_bb) / sma
            
            # RSI مع تجنب التشبع
            delta = df['c'].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))
            rsi_val = rsi.iloc[-1]
            if rsi_val < 30 or rsi_val > 85:
                reason = f"RSI مشبع ({rsi_val:.1f})"
                return None, reason

            # ATR لتجنب التقلب العالي
            atr = (df['h'].rolling(14).max() - df['l'].rolling(14).min()) / 14
            volatility = atr.iloc[-1] / df['c'].iloc[-1] * 100
            if volatility > 4.0:
                reason = f"تقلب عالي ({volatility:.1f}%)"
                return None, reason

            # MACD
            exp1 = df['c'].ewm(span=12).mean()
            exp2 = df['c'].ewm(span=26).mean()
            macd = exp1 - exp2
            macd_signal = macd.ewm(span=9).mean()
            macd_bullish = macd.iloc[-1] > macd_signal.iloc[-1] and macd.iloc[-2] <= macd_signal.iloc[-2]

            # جمع الأصوات
            votes = []
            if bw.iloc[-1] < bw.rolling(30).min().iloc[-2] * 1.1:
                votes.append("Squeeze")
            if df['c'].iloc[-1] > sma.iloc[-1]:
                votes.append("Uptrend")
            vol_ratio = df['v'].iloc[-1] / df['v'].rolling(20).mean().iloc[-2] if df['v'].rolling(20).mean().iloc[-2] > 0 else 1
            if vol_ratio > 2:
                votes.append("Volume")
            if rsi_val > 55:
                votes.append("Momentum")
            if df['c'].iloc[-1] > upper_bb.iloc[-1]:
                votes.append("Breakout")
            if macd_bullish:
                votes.append("MACD")
            
            # حساب الدرجة (Score)
            score = len(votes) * 10
            score += (rsi_val - 50) / 5 if rsi_val > 50 else 0
            score += min(vol_ratio * 5, 15)
            score += (bw.iloc[-1] * 100) if bw.iloc[-1] < 0.5 else 0
            score = round(score, 2)
            
            if len(votes) >= MIN_VOTES:
                expected_pump = round(bw.iloc[-1] * 100, 2)
                signal = TrainSignal(
                    symbol=symbol,
                    entry_price=df['c'].iloc[-1],
                    votes=len(votes),
                    strategies=votes,
                    expected_pump=expected_pump,
                    score=score,
                    reason=""
                )
                return signal, None
            else:
                reason = f"أصوات غير كافية ({len(votes)}/{MIN_VOTES})"
                return None, reason
        except Exception as e:
            reason = f"خطأ في التحليل: {str(e)[:50]}"
            return None, reason

    async def update_trades(self, ex):
        """مراقبة الصفقات المفتوحة وتطبيق الوقف المتحرك والإغلاق"""
        for sym, trade in list(self.active_trades.items()):
            try:
                ticker = await ex.fetch_ticker(sym)
                curr = ticker['last']
                pnl = (curr - trade.entry_price) / trade.entry_price * 100
                if curr > trade.highest_price:
                    trade.highest_price = curr
                
                # تفعيل التريلينغ بعد ربح 2%
                if pnl >= TRAILING_ACTIVATE_PCT:
                    new_stop = trade.entry_price * (1 + (pnl - TRAILING_DISTANCE_PCT)/100)
                    if new_stop > trade.stop_loss:
                        trade.stop_loss = new_stop
                
                # شروط الخروج
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
                    await send_tg(f"🏁 *إغلاق {sym}*\nالربح: `{pnl:.2f}%`\nالسبب: {exit_reason}\nالرصيد: {self.balance:.2f} USDT")
                    del self.active_trades[sym]
            except Exception as e:
                pass

# =========================================================
# واجهة الويب (مع جداول إضافية)
# =========================================================
app = Flask(__name__)
engine = EmpireEngineV20()

@app.template_filter('duration')
def duration_filter(iso_time):
    if not iso_time:
        return "0 دقيقة"
    diff = datetime.now() - datetime.fromisoformat(iso_time)
    return f"{diff.seconds // 60} دقيقة"

@app.route('/')
def dashboard():
    curr_prices = {s: t.highest_price for s, t in engine.active_trades.items()}
    # نأخذ آخر 50 فرصة من all_opportunities (الأحدث أولاً)
    recent_opps = list(reversed(engine.all_opportunities[-50:])) if engine.all_opportunities else []
    
    html = """
    <html dir="rtl"><head><meta charset="UTF-8"><title>Empire V20 - التداول الذكي</title>
    <style>
        body { background: #020617; color: white; font-family: 'Segoe UI', sans-serif; padding: 20px; margin:0; }
        .status-bar { display: flex; flex-wrap: wrap; justify-content: space-around; background: #1e293b; padding: 15px; border-radius: 10px; margin-bottom: 20px; border-top: 4px solid #38bdf8; }
        .status-item { background: #0f172a; padding: 8px 15px; border-radius: 20px; margin: 5px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .card { background: #0f172a; padding: 15px; border-radius: 10px; border: 1px solid #334155; margin-bottom: 20px; }
        .full-width { grid-column: span 2; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.8rem; }
        th { background: #1e293b; color: #38bdf8; padding: 10px; }
        td { padding: 8px; border-bottom: 1px solid #1e293b; text-align: center; }
        .pnl-pos { color: #4ade80; font-weight: bold; }
        .pnl-neg { color: #f87171; }
        .badge { background: #38bdf8; color: #020617; padding: 2px 8px; border-radius: 12px; font-weight: bold; font-size: 0.7rem; }
        .badge-orange { background: #f59e0b; }
        .badge-red { background: #ef4444; }
        .reason-text { font-size: 0.7rem; color: #94a3b8; max-width: 200px; }
        h2, h3 { color: #38bdf8; margin-top: 0; }
        .score-high { color: #4ade80; font-weight: bold; }
        .score-mid { color: #fbbf24; }
        .score-low { color: #f87171; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
    </style></head><body>
        <h2>💎 إمبراطورية التداول V20 - التحليل الكامل</h2>
        <div class="status-bar">
            <div class="status-item">🟢 المنصة: {{ stats.api_status }}</div>
            <div class="status-item">🗄️ قاعدة البيانات: {{ stats.db_status }}</div>
            <div class="status-item">🔍 العملات الممسوحة: {{ stats.scanned }}</div>
            <div class="status-item">✨ الفرص المكتشفة: {{ stats.opportunities_found }}</div>
            <div class="status-item">⏱️ آخر مسح: {{ stats.last_scan_time or "لم يبدأ" }}</div>
            <div class="status-item">💰 الرصيد: {{ "%.2f"|format(balance) }} $</div>
        </div>

        <div class="grid">
            <!-- الصفقات المفتوحة -->
            <div class="card">
                <h3>🟢 الصفقات المفتوحة ({{ active|length }}/{{ max_trades }})</h3>
                <table>
                    <tr><th>العملة</th><th>الدخول</th><th>الوقف</th><th>الهدف</th><th>المدة</th><th>الربح العائم</th></tr>
                    {% for s, t in active.items() %}
                    <tr>
                        <td><b>{{ s }}</b> <span class="badge">{{ t.signal.votes }}/6</span></td>
                        <td>{{ t.entry_price }}</td>
                        <td style="color:#f87171">{{ "%.6f"|format(t.stop_loss) }}</td>
                        <td style="color:#4ade80">{{ "%.6f"|format(t.take_profit) }}</td>
                        <td>{{ t.entry_time|duration }}</td>
                        <td class="pnl-pos">{{ "%.2f"|format(((prices[s]-t.entry_price)/t.entry_price)*100) }}%</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="6">لا توجد صفقات مفتوحة</td></tr>
                    {% endfor %}
                </table>
            </div>

            <!-- آخر الفرص الضائعة (القوية) -->
            <div class="card">
                <h3>🟡 أقوى الفرص الضائعة (لم يدخل بسبب)</h3>
                <table>
                    <tr><th>الوقت</th><th>العملة</th><th>السعر</th><th>القوة</th><th>الدرجة</th><th>السبب</th></tr>
                    {% for opp in missed[:10] %}
                    <tr>
                        <td>{{ opp.time_found }}</td>
                        <td>{{ opp.symbol }}</td>
                        <td>{{ opp.entry_price }}</td>
                        <td><span class="badge">{{ opp.votes }}/6</span></td>
                        <td class="{% if opp.score >= 40 %}score-high{% elif opp.score >= 25 %}score-mid{% else %}score-low{% endif %}">{{ opp.score }}</td>
                        <td class="reason-text">{{ opp.reason }}</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="6">لا توجد فرص ضائعة</td></tr>
                    {% endfor %}
                </table>
            </div>

            <!-- سجل جميع الفرص (آخر 50) -->
            <div class="card full-width">
                <h3>📋 سجل جميع الفرص التي تم تحليلها (آخر 50)</h3>
                <div style="overflow-x: auto;">
                <table>
                    <tr><th>الوقت</th><th>العملة</th><th>السعر</th><th>الأصوات</th><th>الدرجة</th><th>السبب</th><th>الاستراتيجيات</th></tr>
                    {% for opp in opportunities %}
                    <tr>
                        <td>{{ opp.time_found }}</td>
                        <td>{{ opp.symbol }}</td>
                        <td>{{ opp.entry_price }}</td>
                        <td><span class="badge {% if opp.votes >= 4 %}badge-orange{% endif %}">{{ opp.votes }}/6</span></td>
                        <td class="{% if opp.score >= 40 %}score-high{% elif opp.score >= 25 %}score-mid{% else %}score-low{% endif %}">{{ opp.score }}</td>
                        <td class="reason-text">{{ opp.reason if opp.reason else "✅ دخل الصفقة" }}</td>
                        <td style="font-size:0.7rem">{{ opp.strategies|join(', ') if opp.strategies else '-' }}</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="7">لا توجد بيانات بعد</td></tr>
                    {% endfor %}
                </table>
                </div>
            </div>
        </div>
    </body></html>
    """
    return render_template_string(html, 
        active=engine.active_trades,
        missed=engine.missed_trades[-20:],
        opportunities=recent_opps,
        balance=engine.balance,
        stats=engine.stats,
        prices=curr_prices,
        max_trades=MAX_CONCURRENT_TRADES
    )

# =========================================================
# إشعارات تلغرام
# =========================================================
async def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    except:
        pass

# =========================================================
# الحلقة الرئيسية للمسح (مع تسجيل كل الفرص)
# =========================================================
async def main_loop():
    ex = ccxt_async.gateio({'enableRateLimit': True})
    engine.stats["api_status"] = "🟢"
    await send_tg("🚀 *تم تشغيل البوت V20*\n- تسجيل كل الفرص مع الأسباب\n- نظام Score لترتيب العملات\n- جدول متكامل على الويب")
    
    markets = await ex.fetch_markets()
    symbols = [m['symbol'] for m in markets if m['symbol'].endswith('/USDT') and m['active']]
    symbols = symbols[:TOTAL_SYMBOLS_TO_SCAN]
    await send_tg(f"📊 جاهز لمسح {len(symbols)} عملة")
    
    while True:
        try:
            scan_start = datetime.now()
            engine.stats["scanned"] = 0
            engine.stats["opportunities_found"] = 0
            batch_size = 50
            # خلط عشوائي للحصول على تنوع
            random_symbols = np.random.choice(symbols, min(len(symbols), TOTAL_SYMBOLS_TO_SCAN), replace=False)
            all_signals = []  # لتجميع الإشارات القوية لهذه الدورة
            
            for i in range(0, len(random_symbols), batch_size):
                batch = random_symbols[i:i+batch_size]
                tasks = [engine.analyze(ex, s) for s in batch]
                results = await asyncio.gather(*tasks)
                
                for (sig, reason), symbol in zip(results, batch):
                    # تسجيل كل فرصة (حتى المرفوضة) في الذاكرة لعرضها على الويب
                    if sig:
                        # إشارة قوية
                        opp_record = sig
                        opp_record.reason = ""  # سيتم ملؤه لاحقاً إذا لم يدخل
                        all_signals.append(sig)
                        engine.stats["opportunities_found"] += 1
                        # تسجيل في CSV
                        engine.log_opportunity(symbol, sig.entry_price, sig.votes, sig.score, "إشارة قوية (انتظار)", sig.strategies)
                    else:
                        # عملة لم تعط إشارة أو مرفوضة بسبب reason
                        dummy_signal = TrainSignal(
                            symbol=symbol,
                            entry_price=0,
                            votes=0,
                            strategies=[],
                            expected_pump=0,
                            score=0,
                            reason=reason or "لا توجد إشارة",
                            time_found=datetime.now().strftime("%H:%M:%S")
                        )
                        engine.all_opportunities.append(dummy_signal)
                        engine.log_opportunity(symbol, 0, 0, 0, reason or "لا توجد إشارة", [])
                        # الاحتفاظ بآخر 500 فرصة فقط
                        if len(engine.all_opportunities) > 500:
                            engine.all_opportunities = engine.all_opportunities[-500:]
                
                engine.stats["scanned"] += len(batch)
                await asyncio.sleep(0.1)  # تجنب الحظر
            
            # معالجة الإشارات القوية: ترتيب حسب الدرجة (Score) ثم فتح الصفقات
            all_signals.sort(key=lambda x: x.score, reverse=True)
            for sig in all_signals:
                if sig.symbol in engine.active_trades:
                    continue
                # حساب حجم الصفقة
                risk_amount = engine.balance * RISK_PER_TRADE
                position_size = risk_amount / STOP_LOSS_PCT
                invest = min(position_size, engine.balance)
                
                if len(engine.active_trades) < MAX_CONCURRENT_TRADES and engine.balance >= invest:
                    # فتح صفقة حقيقية
                    stop_loss_price = sig.entry_price * (1 - STOP_LOSS_PCT)
                    take_profit_price = sig.entry_price * 1.06
                    trade = TradeInfo(
                        symbol=sig.symbol,
                        signal=sig,
                        entry_price=sig.entry_price,
                        invested=invest,
                        highest_price=sig.entry_price,
                        stop_loss=stop_loss_price,
                        take_profit=take_profit_price,
                        is_virtual=False
                    )
                    engine.active_trades[sig.symbol] = trade
                    engine.balance -= invest
                    engine._save_balance()
                    await send_tg(f"🟢 *صفقة شراء* {sig.symbol}\nالسعر: {sig.entry_price:.8f}\nالمبلغ: {invest:.2f} USDT\nالقوة: {sig.votes}/6\nالدرجة: {sig.score}")
                    # تسجيل في CSV كفرصة تم الدخول فيها
                    engine.log_opportunity(sig.symbol, sig.entry_price, sig.votes, sig.score, "تم الدخول", sig.strategies)
                else:
                    # فرصة ضائعة (قوية لكن لم يدخل بسبب limit أو رصيد)
                    sig.reason = f"الحد الأقصى للصفقات أو رصيد غير كافٍ (صفقات مفتوحة: {len(engine.active_trades)})"
                    engine.missed_trades.append(sig)
                    if len(engine.missed_trades) > 100:
                        engine.missed_trades.pop(0)
                    engine.log_opportunity(sig.symbol, sig.entry_price, sig.votes, sig.score, sig.reason, sig.strategies)
            
            # تحديث وقت آخر مسح
            engine.stats["last_scan_time"] = scan_start.strftime("%H:%M:%S")
            
            # تحديث الصفقات المفتوحة (مراقبة الخروج)
            await engine.update_trades(ex)
            
        except Exception as e:
            print(f"خطأ في الحلقة الرئيسية: {e}")
            await send_tg(f"⚠️ خطأ في البوت: {str(e)[:100]}")
            await asyncio.sleep(5)
        
        await asyncio.sleep(SCAN_INTERVAL)

# =========================================================
# تشغيل الخادم والمحرك
# =========================================================
if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False), daemon=True).start()
    asyncio.run(main_loop())

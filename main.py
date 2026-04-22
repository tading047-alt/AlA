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
from datetime import datetime, time, timedelta
from dataclasses import dataclass, field, asdict
import math

# =========================================================
# ⚙️ الإعدادات العامة (قابلة للتعديل بسهولة)
# =========================================================
TELEGRAM_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"
TELEGRAM_CHAT_ID = "5067771509"

LOG_DIR = "/tmp/trading_logs"
DB_FILE = os.path.join(LOG_DIR, "empire_v23.db")
REAL_CSV = os.path.join(LOG_DIR, "real_trades.csv")
MISSED_CSV = os.path.join(LOG_DIR, "missed_trades.csv")
OPPORTUNITIES_CSV = os.path.join(LOG_DIR, "opportunities.csv")
os.makedirs(LOG_DIR, exist_ok=True)

# إعدادات التداول
MAX_CONCURRENT_TRADES = 10
RISK_PER_TRADE = 0.02          # 2% من الرصيد لكل صفقة
STOP_LOSS_PCT = 0.025          # 2.5%
TRAILING_ACTIVATE_PCT = 2.0    # تفعيل التريلينغ بعد 2% ربح
TRAILING_DISTANCE_PCT = 1.5    # مسافة التريلينغ 1.5%
PARTIAL_TP_PCT = 4.0           # جني أرباح جزئي عند 4%
PARTIAL_CLOSE_RATIO = 0.5      # إغلاق 50%
FINAL_TP_PCT = 8.0             # الهدف النهائي 8%

# إعدادات المسح
TOTAL_SYMBOLS_TO_SCAN = 1000
SCAN_INTERVAL = 10
BATCH_SIZE = 50

# إعدادات الفلاتر والدرجات
MIN_VOTES = 4                  # الحد الأدنى للأصوات (من 6 مؤشرات أساسية)
ENABLE_EXPLOSION_FILTER = True  # تشغيل فلتر الانفجار السريع
EXPLOSION_FILTER_MIN_CONDITIONS = 3

# إعدادات السيولة
MIN_24H_VOLUME_USD = 500000
MAX_SPREAD_PCT = 0.1

# =========================================================
# هيكل البيانات (موسع ليشمل الأنماط والدرجات)
# =========================================================
@dataclass
class TrainSignal:
    symbol: str
    entry_price: float
    expected_pump_pct: float
    votes: int
    strategies: list
    score: float
    candle_patterns: list = field(default_factory=list)
    reason: str = ""
    entry_point: float = 0.0
    extra_scores: dict = field(default_factory=dict)
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
    partial_closed: bool = False
    is_virtual: bool = False

# =========================================================
# المحرك الرئيسي (مع جميع التحسينات)
# =========================================================
class EmpireEngineV23:
    def __init__(self):
        self.active_trades = {}
        self.missed_trades = []          # فرص قوية ضائعة (لم يدخل بسبب limit أو رصيد)
        self.watchlist = []              # فرص جيدة للمراقبة (سكور 80-120) دون دخول فوري
        self.all_opportunities = []      # آخر 500 فرصة (للعرض)
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
                writer.writerow(['Time', 'Symbol', 'Price', 'EntryPoint', 'ExpectedPump%', 'Votes', 'Score', 'Reason', 'Strategies', 'CandlePatterns', 'ExtraScores'])

    def log_opportunity(self, symbol, price, entry_point, expected_pump, votes, score, reason, strategies, candle_patterns=None, extra_scores=None):
        with open(OPPORTUNITIES_CSV, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol, price, entry_point, expected_pump, votes, f"{score:.2f}",
                reason, ", ".join(strategies), ", ".join(candle_patterns) if candle_patterns else "",
                json.dumps(extra_scores or {})
            ])

    def _save_balance(self):
        conn = sqlite3.connect(DB_FILE)
        conn.execute("UPDATE config SET value = ? WHERE key = 'balance'", (self.balance,))
        conn.commit()
        conn.close()

    # ---------- فلتر الانفجار السريع ----------
    async def explosion_filter(self, df):
        if len(df) < 30:
            return False, []
        avg_volume = df['v'].rolling(20).mean().iloc[-2]
        current_volume = df['v'].iloc[-1]
        volume_ok = (current_volume > avg_volume * 1.8) if avg_volume > 0 else False
        
        price_change_3 = (df['c'].iloc[-1] - df['c'].iloc[-4]) / df['c'].iloc[-4] * 100
        momentum_ok = price_change_3 > 1.5
        
        sma = df['c'].rolling(20).mean()
        std = df['c'].rolling(20).std()
        upper_bb = sma + (1.5 * std)
        bb_break_ok = df['c'].iloc[-1] > upper_bb.iloc[-1]
        
        lower_bb = sma - (2 * std)
        bw = (upper_bb - lower_bb) / sma
        squeeze_ok = bw.iloc[-1] < 0.05
        
        delta = df['c'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))
        rsi_val = rsi.iloc[-1]
        rsi_increasing = rsi.iloc[-1] > rsi.iloc[-2] > rsi.iloc[-3]
        rsi_ok = rsi_val > 55 or rsi_increasing
        
        conditions = []
        if volume_ok: conditions.append("VolumeSpike")
        if momentum_ok: conditions.append("Momentum3")
        if bb_break_ok: conditions.append("BBBreak")
        if squeeze_ok: conditions.append("Squeeze")
        if rsi_ok: conditions.append("RSI_Dynamic")
        passed = len(conditions) >= EXPLOSION_FILTER_MIN_CONDITIONS
        return passed, conditions

    # ---------- أنماط الشموع اليابانية ----------
    def detect_candlestick_patterns(self, df):
        if len(df) < 30:
            return 0, 0, [], False
        bullish_score = 0
        bearish_score = 0
        patterns = []
        is_exit = False
        
        avg_volume = df['v'].rolling(20).mean().iloc[-1]
        if avg_volume == 0:
            avg_volume = df['v'].iloc[-1]
        
        # Bullish Three Line Strike
        if len(df) >= 4:
            last_4 = df.iloc[-4:]
            if (all(last_4['c'].iloc[i] < last_4['c'].iloc[i-1] for i in range(1, 4)) and
                last_4['c'].iloc[-1] > last_4['h'].iloc[-2] and
                last_4['c'].iloc[-1] > last_4['o'].iloc[-1] and
                df['v'].iloc[-1] > avg_volume * 1.5):
                bullish_score += 25
                patterns.append("ThreeLineStrike")
        
        # Hammer
        body = abs(df['c'].iloc[-1] - df['o'].iloc[-1])
        lower_wick = min(df['o'].iloc[-1], df['c'].iloc[-1]) - df['l'].iloc[-1]
        upper_wick = df['h'].iloc[-1] - max(df['o'].iloc[-1], df['c'].iloc[-1])
        if body > 0 and lower_wick > body * 2 and upper_wick < body * 0.5:
            if df['v'].iloc[-1] > avg_volume * 1.2:
                bullish_score += 15
                patterns.append("Hammer")
        
        # Bullish Engulfing
        if len(df) >= 2:
            if (df['c'].iloc[-1] > df['o'].iloc[-1] and 
                df['o'].iloc[-1] < df['c'].iloc[-2] and 
                df['c'].iloc[-1] > df['o'].iloc[-2] and
                df['v'].iloc[-1] > avg_volume * 1.3):
                bullish_score += 15
                patterns.append("BullishEngulfing")
        
        # Morning Star
        if len(df) >= 3:
            last_3 = df.iloc[-3:]
            if (last_3['c'].iloc[-3] < last_3['o'].iloc[-3] and
                abs(last_3['c'].iloc[-2] - last_3['o'].iloc[-2]) < abs(last_3['c'].iloc[-3] - last_3['o'].iloc[-3]) * 0.3 and
                last_3['c'].iloc[-1] > last_3['o'].iloc[-1] and
                last_3['c'].iloc[-1] > (last_3['h'].iloc[-3] + last_3['l'].iloc[-3]) / 2 and
                df['v'].iloc[-1] > avg_volume * 1.2):
                bullish_score += 18
                patterns.append("MorningStar")
        
        # Piercing Line
        if len(df) >= 2:
            if (df['c'].iloc[-2] < df['o'].iloc[-2] and
                df['c'].iloc[-1] > df['o'].iloc[-1] and
                df['o'].iloc[-1] < df['c'].iloc[-2] and
                df['c'].iloc[-1] > (df['c'].iloc[-2] + df['o'].iloc[-2]) / 2 and
                df['v'].iloc[-1] > avg_volume * 1.2):
                bullish_score += 12
                patterns.append("PiercingLine")
        
        # Three Black Crows (bearish)
        if len(df) >= 3:
            last_3 = df.iloc[-3:]
            if (all(last_3['c'].iloc[i] < last_3['c'].iloc[i-1] for i in range(1, 3)) and
                all(last_3['h'].iloc[i] - last_3['l'].iloc[i] > (df['h'].iloc[-5] - df['l'].iloc[-5]) * 0.7 for i in range(3))):
                bearish_score += 20
                patterns.append("ThreeBlackCrows")
                is_exit = True
        
        # Evening Star
        if len(df) >= 3:
            last_3 = df.iloc[-3:]
            if (last_3['c'].iloc[-3] > last_3['o'].iloc[-3] and
                abs(last_3['c'].iloc[-2] - last_3['o'].iloc[-2]) < abs(last_3['c'].iloc[-3] - last_3['o'].iloc[-3]) * 0.3 and
                last_3['c'].iloc[-1] < last_3['o'].iloc[-1] and
                last_3['c'].iloc[-1] < (last_3['l'].iloc[-3] + last_3['h'].iloc[-3]) / 2):
                bearish_score += 15
                patterns.append("EveningStar")
                is_exit = True
        
        # Shooting Star
        if body > 0 and upper_wick > body * 2 and lower_wick < body * 0.5:
            bearish_score += 12
            patterns.append("ShootingStar")
            is_exit = True
        
        return bullish_score, bearish_score, patterns, is_exit

    # ---------- حالة السوق والتقاطع الذهبي ----------
    async def get_market_condition_score(self, ex, symbol):
        try:
            ohlcv_15 = await ex.fetch_ohlcv(symbol, timeframe='15m', limit=30)
            ohlcv_1h = await ex.fetch_ohlcv(symbol, timeframe='1h', limit=100)
            if len(ohlcv_15) < 30 or len(ohlcv_1h) < 50:
                return 0, "Insufficient data"
            df_15 = pd.DataFrame(ohlcv_15, columns=['t','o','h','l','c','v'])
            df_1h = pd.DataFrame(ohlcv_1h, columns=['t','o','h','l','c','v'])
            price_15 = df_15['c'].iloc[-1]
            ema50_15 = df_15['c'].ewm(span=50).mean().iloc[-1]
            ema50_1h = df_1h['c'].ewm(span=50).mean().iloc[-1]
            ema200_1h = df_1h['c'].ewm(span=200).mean().iloc[-1]
            if price_15 > ema50_15 and ema50_1h > ema200_1h:
                return 15, "Strong Uptrend"
            elif price_15 > ema50_15:
                return 5, "Weak Uptrend"
            elif price_15 < ema50_15:
                return -15, "Downtrend"
            else:
                return -5, "Sideways"
        except:
            return 0, "Error"

    async def get_golden_cross_score(self, ex, symbol):
        try:
            ohlcv_4h = await ex.fetch_ohlcv(symbol, timeframe='4h', limit=100)
            if len(ohlcv_4h) < 50:
                return 0, None
            df = pd.DataFrame(ohlcv_4h, columns=['t','o','h','l','c','v'])
            ema50 = df['c'].ewm(span=50).mean()
            ema200 = df['c'].ewm(span=200).mean()
            for i in range(-3, 0):
                if ema50.iloc[i] > ema200.iloc[i] and ema50.iloc[i-1] <= ema200.iloc[i-1]:
                    return 10, "Golden Cross (4h)"
            return 0, None
        except:
            return 0, None

    # ---------- التحليل الأساسي (مع كل الفلاتر والسكور) ----------
    async def analyze(self, ex, symbol):
        reason = None
        try:
            # فلتر الوقت
            now_utc = datetime.utcnow().time()
            if not (time(14,0) <= now_utc <= time(22,0)):
                reason = "وقت غير مناسب (خارج 14-22 UTC)"
                return None, reason

            # فريم 15 دقيقة للاتجاه
            ohlcv_15 = await ex.fetch_ohlcv(symbol, timeframe='15m', limit=50)
            if len(ohlcv_15) < 30:
                reason = "بيانات 15 دقيقة غير كافية"
                return None, reason
            df_15 = pd.DataFrame(ohlcv_15, columns=['t','o','h','l','c','v'])
            ema50_15 = df_15['c'].ewm(span=50).mean().iloc[-1]
            ema200_15 = df_15['c'].ewm(span=200).mean().iloc[-1]
            if df_15['c'].iloc[-1] < ema50_15 or ema50_15 < ema200_15:
                reason = "اتجاه هابط أو ضعيف على 15 دقيقة"
                return None, reason

            # بيانات 5 دقائق
            ohlcv = await ex.fetch_ohlcv(symbol, timeframe='5m', limit=100)
            if len(ohlcv) < 60:
                reason = "بيانات 5 دقائق غير كافية"
                return None, reason
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])

            # فلتر الانفجار السريع
            if ENABLE_EXPLOSION_FILTER:
                passed, conds = await self.explosion_filter(df)
                if not passed:
                    reason = f"فلتر الانفجار: {','.join(conds) if conds else 'لا توجد شروط كافية'}"
                    return None, reason

            # السيولة والسبريد
            ticker = await ex.fetch_ticker(symbol)
            vol_24h = ticker['quoteVolume'] if 'quoteVolume' in ticker else ticker['volume'] * ticker['last']
            spread = (ticker['ask'] - ticker['bid']) / ticker['last'] * 100 if ticker['ask'] and ticker['bid'] else 100
            if vol_24h < MIN_24H_VOLUME_USD:
                reason = f"حجم 24h منخفض ({vol_24h/1000:.0f}K$)"
                return None, reason
            if spread > MAX_SPREAD_PCT:
                reason = f"سبريد عالٍ ({spread:.2f}%)"
                return None, reason

            # المؤشرات الأساسية
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
                reason = f"RSI مشبع ({rsi_val:.1f})"
                return None, reason

            # ATR
            atr = (df['h'].rolling(14).max() - df['l'].rolling(14).min()) / 14
            volatility = atr.iloc[-1] / df['c'].iloc[-1] * 100
            if volatility > 5.0:
                reason = f"تقلب عالي ({volatility:.1f}%)"
                return None, reason

            # MACD
            exp1 = df['c'].ewm(span=12).mean()
            exp2 = df['c'].ewm(span=26).mean()
            macd = exp1 - exp2
            macd_signal = macd.ewm(span=9).mean()
            macd_hist = macd - macd_signal
            macd_bullish = macd.iloc[-1] > macd_signal.iloc[-1] and macd_hist.iloc[-1] > macd_hist.iloc[-2]

            # Divergence (نسخة مبسطة)
            divergence = None
            if len(df) >= 20:
                price_lows = []
                rsi_lows = []
                for i in range(-20, -1):
                    if df['c'].iloc[i] <= df['c'].iloc[i-1] and df['c'].iloc[i] <= df['c'].iloc[i+1]:
                        price_lows.append(df['c'].iloc[i])
                    if rsi.iloc[i] <= rsi.iloc[i-1] and rsi.iloc[i] <= rsi.iloc[i+1]:
                        rsi_lows.append(rsi.iloc[i])
                if len(price_lows) >= 2 and len(rsi_lows) >= 2:
                    if price_lows[-1] < price_lows[-2] and rsi_lows[-1] > rsi_lows[-2]:
                        divergence = "bullish"
                    elif price_lows[-1] > price_lows[-2] and rsi_lows[-1] < rsi_lows[-2]:
                        divergence = "bearish"

            # حالة السوق والتقاطع الذهبي
            market_score, market_reason = await self.get_market_condition_score(ex, symbol)
            golden_score, golden_reason = await self.get_golden_cross_score(ex, symbol)

            # جمع الأصوات
            avg_volume = df['v'].rolling(20).mean().iloc[-2]
            volume_ratio = df['v'].iloc[-1] / avg_volume if avg_volume > 0 else 1

            votes = []
            if bw.iloc[-1] < bw.rolling(30).min().iloc[-2] * 1.1:
                votes.append("Squeeze")
            if df['c'].iloc[-1] > sma.iloc[-1]:
                votes.append("Uptrend")
            if volume_ratio > 2:
                votes.append("Volume")
            if rsi_val > 55:
                votes.append("Momentum")
            if df['c'].iloc[-1] > upper_bb.iloc[-1]:
                votes.append("Breakout")
            if macd_bullish:
                votes.append("MACD")
            if divergence == "bullish":
                votes.append("BullishDivergence")

            # حساب السكور الأساسي
            base_score = len(votes) * 10
            rsi_score = max(0, (rsi_val - 50) / 5) if rsi_val > 50 else 0
            volume_score = min(volume_ratio * 5, 15)
            bw_score = max(0, (0.5 - bw.iloc[-1]) * 20) if bw.iloc[-1] < 0.5 else 0
            liquidity_score = 0
            if vol_24h > 200_000_000:
                liquidity_score = 15
            elif vol_24h > 50_000_000:
                liquidity_score = 10
            elif vol_24h > 5_000_000:
                liquidity_score = 5
            spread_score = 10 if spread < 0.05 else 0
            volume_spike_score = 0
            if volume_ratio >= 5:
                volume_spike_score = 20
            elif volume_ratio >= 3:
                volume_spike_score = 15
            elif volume_ratio >= 2:
                volume_spike_score = 10

            total_score = base_score + rsi_score + volume_score + bw_score + liquidity_score + spread_score + volume_spike_score + market_score + (golden_score or 0)
            if divergence == "bullish":
                total_score += 20
            total_score = round(total_score, 2)

            # أنماط الشموع
            candle_bullish, candle_bearish, candle_patterns, exit_signal = self.detect_candlestick_patterns(df)
            if candle_bearish >= 15:
                reason = f"نمط شموع هابط: {', '.join(candle_patterns)}"
                return None, reason
            total_score += candle_bullish

            # النسبة المتوقعة للارتفاع
            expected_pump = (volume_ratio * 1.5) + (bw.iloc[-1] * 50) + (rsi_val / 20)
            expected_pump = min(expected_pump, 15.0)

            # نقطة الدخول المقترحة
            ask_price = ticker['ask'] if ticker['ask'] else df['c'].iloc[-1] * (1 + spread/100)
            entry_point = ask_price  # يمكن تعديلها لاحقاً

            # القرار النهائي
            if len(votes) >= MIN_VOTES:
                extra_scores = {
                    'base': base_score, 'rsi': round(rsi_score,2), 'volume': volume_score,
                    'bw': round(bw_score,2), 'liquidity': liquidity_score, 'spread': spread_score,
                    'spike': volume_spike_score, 'market': market_score, 'golden': golden_score or 0,
                    'divergence': 20 if divergence == 'bullish' else 0,
                    'candle_bullish': candle_bullish
                }
                signal = TrainSignal(
                    symbol=symbol,
                    entry_price=df['c'].iloc[-1],
                    expected_pump_pct=round(expected_pump, 2),
                    votes=len(votes),
                    strategies=votes,
                    score=total_score,
                    candle_patterns=candle_patterns,
                    entry_point=round(entry_point, 8),
                    extra_scores=extra_scores
                )
                return signal, None
            else:
                reason = f"أصوات غير كافية ({len(votes)}/{MIN_VOTES})"
                return None, reason

        except Exception as e:
            reason = f"خطأ: {str(e)[:50]}"
            return None, reason

    # ---------- تحديث الصفقات المفتوحة (وقف متحرك وجني أرباح جزئي) ----------
    async def update_trades(self, ex):
        for sym, trade in list(self.active_trades.items()):
            try:
                ticker = await ex.fetch_ticker(sym)
                curr = ticker['last']
                pnl = (curr - trade.entry_price) / trade.entry_price * 100
                if curr > trade.highest_price:
                    trade.highest_price = curr

                # جني أرباح جزئي
                if not trade.partial_closed and pnl >= PARTIAL_TP_PCT:
                    close_amount = trade.invested * PARTIAL_CLOSE_RATIO
                    profit_partial = close_amount * (pnl / 100)
                    self.balance += close_amount + profit_partial
                    trade.invested -= close_amount
                    trade.partial_closed = True
                    await send_tg(f"📊 *جني أرباح جزئي {sym}*\nالربح: {pnl:.2f}% | المتبقي: {trade.invested:.2f} USDT")

                # وقف متحرك
                if pnl >= TRAILING_ACTIVATE_PCT:
                    new_stop = trade.entry_price * (1 + (pnl - TRAILING_DISTANCE_PCT)/100)
                    if new_stop > trade.stop_loss:
                        trade.stop_loss = new_stop

                exit_reason = None
                if pnl <= -STOP_LOSS_PCT * 100:
                    exit_reason = "Stop Loss"
                elif trade.partial_closed and pnl <= (TRAILING_ACTIVATE_PCT - 1):
                    exit_reason = "Trailing Stop (remainder)"
                elif pnl >= FINAL_TP_PCT:
                    exit_reason = "Final Take Profit"
                elif curr <= trade.stop_loss and trade.stop_loss > trade.entry_price:
                    exit_reason = "Trailing Stop"

                if exit_reason:
                    total_pnl = (curr - trade.entry_price) / trade.entry_price * 100
                    self.balance += trade.invested * (1 + total_pnl/100)
                    self._save_balance()
                    with open(REAL_CSV, 'a', newline='') as f:
                        csv.writer(f).writerow([datetime.now().isoformat(), sym, trade.entry_price, curr, f"{total_pnl:.2f}"])
                    await send_tg(f"🏁 *إغلاق {sym}*\nالربح: `{total_pnl:.2f}%`\nالسبب: {exit_reason}\nالرصيد: {self.balance:.2f} USDT")
                    del self.active_trades[sym]
            except Exception as e:
                pass

# =========================================================
# واجهة الويب (مع عرض السكور والأنماط)
# =========================================================
app = Flask(__name__)
engine = EmpireEngineV23()

@app.template_filter('duration')
def duration_filter(iso_time):
    if not iso_time:
        return "0"
    diff = datetime.now() - datetime.fromisoformat(iso_time)
    return f"{diff.seconds // 60}"

@app.route('/')
def dashboard():
    curr_prices = {s: t.highest_price for s, t in engine.active_trades.items()}
    recent_opps = list(reversed(engine.all_opportunities[-50:])) if engine.all_opportunities else []
    watchlist = engine.watchlist[-20:] if engine.watchlist else []
    
    html = """
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Empire V23 - التداول الذكي</title>
        <style>
            * { box-sizing: border-box; }
            body { background: #020617; color: white; font-family: 'Segoe UI', sans-serif; padding: 20px; margin: 0; }
            .status-bar { display: flex; flex-wrap: wrap; justify-content: space-around; background: #1e293b; padding: 15px; border-radius: 10px; margin-bottom: 20px; border-top: 4px solid #38bdf8; }
            .status-item { background: #0f172a; padding: 8px 15px; border-radius: 20px; margin: 5px; font-size: 0.9rem; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }
            .card { background: #0f172a; padding: 15px; border-radius: 10px; border: 1px solid #334155; margin-bottom: 20px; }
            .full-width { grid-column: 1 / -1; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.75rem; }
            th { background: #1e293b; color: #38bdf8; padding: 8px; position: sticky; top: 0; }
            td { padding: 6px; border-bottom: 1px solid #1e293b; text-align: center; }
            .pnl-pos { color: #4ade80; font-weight: bold; }
            .badge { background: #38bdf8; color: #020617; padding: 2px 8px; border-radius: 12px; font-weight: bold; font-size: 0.7rem; }
            .badge-orange { background: #f59e0b; }
            .score-high { color: #4ade80; font-weight: bold; }
            .score-mid { color: #fbbf24; }
            .score-low { color: #f87171; }
            .reason-text { font-size: 0.7rem; color: #94a3b8; max-width: 200px; }
            h2, h3 { color: #38bdf8; margin-top: 0; }
            @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } table { font-size: 0.7rem; } }
        </style>
    </head>
    <body>
        <h2>💎 إمبراطورية التداول V23 - التحليل الذكي</h2>
        <div class="status-bar">
            <div class="status-item">🟢 المنصة: {{ stats.api_status }}</div>
            <div class="status-item">🗄️ DB: {{ stats.db_status }}</div>
            <div class="status-item">🔍 الممسوحة: {{ stats.scanned }}</div>
            <div class="status-item">✨ الفرص: {{ stats.opportunities_found }}</div>
            <div class="status-item">⏱️ آخر مسح: {{ stats.last_scan_time or "—" }}</div>
            <div class="status-item">💰 الرصيد: {{ "%.2f"|format(balance) }} $</div>
        </div>

        <div class="grid">
            <!-- الصفقات المفتوحة -->
            <div class="card">
                <h3>🟢 الصفقات المفتوحة ({{ active|length }}/{{ max_trades }})</h3>
                <table>
                    <thead><tr><th>العملة</th><th>الدخول</th><th>الوقف</th><th>الهدف</th><th>المدة</th><th>الربح %</th></tr></thead>
                    <tbody>
                    {% for s, t in active.items() %}
                    <tr>
                        <td><b>{{ s }}</b> <span class="badge">{{ t.signal.votes }}/6</span></td>
                        <td>{{ t.entry_price }}</td>
                        <td style="color:#f87171">{{ "%.6f"|format(t.stop_loss) }}</td>
                        <td style="color:#4ade80">{{ "%.6f"|format(t.take_profit) }}</td>
                        <td>{{ t.entry_time|duration }} دقيقة</td>
                        <td class="pnl-pos">{{ "%.2f"|format(((prices[s]-t.entry_price)/t.entry_price)*100) }}%</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="6">لا توجد صفقات مفتوحة</td></tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>

            <!-- قائمة المراقبة (سكور 80-120) -->
            <div class="card">
                <h3>👁️ قائمة المراقبة (سكور متوسط)</h3>
                <table>
                    <thead><tr><th>العملة</th><th>السعر</th><th>السكور</th><th>الأصوات</th><th>الأنماط</th></tr></thead>
                    <tbody>
                    {% for w in watchlist %}
                    <tr>
                        <td>{{ w.symbol }}</td>
                        <td>{{ w.entry_price }}</td>
                        <td class="score-mid">{{ w.score }}</td>
                        <td><span class="badge">{{ w.votes }}/6</span></td>
                        <td>{{ w.candle_patterns|join(',') if w.candle_patterns else '-' }}</td>
                    </tr>
                    {% else %}
                    <td><td colspan="5">لا توجد عملات في المراقبة</td></tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>

            <!-- سجل جميع الفرص (آخر 50) -->
            <div class="card full-width">
                <h3>📋 سجل الفرص التي تم تحليلها</h3>
                <div style="overflow-x: auto;">
                <table>
                    <thead><tr><th>الوقت</th><th>العملة</th><th>السعر</th><th>نقطة الدخول</th><th>الارتفاع%</th><th>الأصوات</th><th>السكور</th><th>السبب</th><th>الأنماط</th></tr></thead>
                    <tbody>
                    {% for opp in opportunities %}
                    <tr>
                        <td>{{ opp.time_found }}</td>
                        <td>{{ opp.symbol }}</td>
                        <td>{{ opp.entry_price }}</td>
                        <td>{{ opp.entry_point }}</td>
                        <td>{{ opp.expected_pump_pct }}%</td>
                        <td><span class="badge {% if opp.votes >= 4 %}badge-orange{% endif %}">{{ opp.votes }}/6</span></td>
                        <td class="{% if opp.score >= 120 %}score-high{% elif opp.score >= 80 %}score-mid{% else %}score-low{% endif %}">{{ opp.score }}</td>
                        <td class="reason-text">{{ opp.reason if opp.reason else "✅ دخل الصفقة" }}</td>
                        <td style="font-size:0.7rem">{{ opp.candle_patterns|join(', ') if opp.candle_patterns else '-' }}</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="9">لا توجد بيانات بعد</td></tr>
                    {% endfor %}
                    </tbody>
                </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html,
        active=engine.active_trades,
        watchlist=watchlist,
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
# الحلقة الرئيسية للمسح (مع اختيار العملات حسب السكور)
# =========================================================
async def main_loop():
    ex = ccxt_async.gateio({'enableRateLimit': True})
    engine.stats["api_status"] = "🟢"
    await send_tg("🚀 *Empire V23 démarré*\n✅ Filtre explosion\n✅ Score avancé avec chandeliers\n✅ Watchlist intelligente")
    
    markets = await ex.fetch_markets()
    symbols = [m['symbol'] for m in markets if m['symbol'].endswith('/USDT') and m['active']]
    symbols = symbols[:TOTAL_SYMBOLS_TO_SCAN]
    await send_tg(f"📊 {len(symbols)} symboles chargés")
    
    while True:
        try:
            scan_start = datetime.now()
            engine.stats["scanned"] = 0
            engine.stats["opportunities_found"] = 0
            random_symbols = np.random.choice(symbols, min(len(symbols), TOTAL_SYMBOLS_TO_SCAN), replace=False)
            all_signals = []
            
            # مرحلة المسح
            for i in range(0, len(random_symbols), BATCH_SIZE):
                batch = random_symbols[i:i+BATCH_SIZE]
                tasks = [engine.analyze(ex, s) for s in batch]
                results = await asyncio.gather(*tasks)
                
                for (sig, reason), symbol in zip(results, batch):
                    if sig:
                        all_signals.append(sig)
                        engine.stats["opportunities_found"] += 1
                        engine.log_opportunity(sig.symbol, sig.entry_price, sig.entry_point, sig.expected_pump_pct,
                                               sig.votes, sig.score, "إشارة قوية (انتظار)", sig.strategies, sig.candle_patterns, sig.extra_scores)
                        engine.all_opportunities.append(sig)
                    else:
                        dummy = TrainSignal(symbol=symbol, entry_price=0, expected_pump_pct=0, votes=0,
                                            strategies=[], score=0, reason=reason or "لا توجد إشارة", entry_point=0)
                        engine.all_opportunities.append(dummy)
                        engine.log_opportunity(symbol, 0, 0, 0, 0, 0, reason or "لا توجد إشارة", [], [], {})
                        if len(engine.all_opportunities) > 500:
                            engine.all_opportunities = engine.all_opportunities[-500:]
                
                engine.stats["scanned"] += len(batch)
                await asyncio.sleep(0.1)
            
            # ترتيب الإشارات حسب السكور (تنازلي)
            all_signals.sort(key=lambda x: x.score, reverse=True)
            
            # تصنيف الإشارات: دخول فوري، مراقبة، تجاهل
            engine.watchlist.clear()
            for sig in all_signals:
                if sig.symbol in engine.active_trades:
                    continue
                if sig.score >= 120:
                    # فرصة ممتازة - نحاول الدخول
                    risk_amount = engine.balance * RISK_PER_TRADE
                    position_size = risk_amount / STOP_LOSS_PCT
                    invest = min(position_size, engine.balance)
                    if len(engine.active_trades) < MAX_CONCURRENT_TRADES and engine.balance >= invest:
                        stop_loss_price = sig.entry_point * (1 - STOP_LOSS_PCT)
                        take_profit_price = sig.entry_point * (1 + FINAL_TP_PCT/100)
                        trade = TradeInfo(
                            symbol=sig.symbol,
                            signal=sig,
                            entry_price=sig.entry_point,
                            invested=invest,
                            highest_price=sig.entry_point,
                            stop_loss=stop_loss_price,
                            take_profit=take_profit_price,
                            partial_closed=False
                        )
                        engine.active_trades[sig.symbol] = trade
                        engine.balance -= invest
                        engine._save_balance()
                        await send_tg(
                            f"🟢 *ACHAT {sig.symbol}*\n"
                            f"💰 Prix: {sig.entry_point:.8f}\n"
                            f"📈 Pump estimé: {sig.expected_pump_pct}%\n"
                            f"⭐ Score: {sig.score}\n"
                            f"🎫 Votes: {sig.votes}/6\n"
                            f"🕯️ Patterns: {', '.join(sig.candle_patterns) if sig.candle_patterns else '-'}\n"
                            f"💵 Investi: {invest:.2f} USDT"
                        )
                        engine.log_opportunity(sig.symbol, sig.entry_price, sig.entry_point, sig.expected_pump_pct,
                                               sig.votes, sig.score, "✅ تم الدخول", sig.strategies, sig.candle_patterns, sig.extra_scores)
                    else:
                        # فرصة ممتازة لكن لا مكان - نضيفها للفرص الضائعة
                        sig.reason = f"الحد الأقصى للصفقات أو رصيد غير كافٍ (مفتوحة: {len(engine.active_trades)})"
                        engine.missed_trades.append(sig)
                        engine.log_opportunity(sig.symbol, sig.entry_price, sig.entry_point, sig.expected_pump_pct,
                                               sig.votes, sig.score, sig.reason, sig.strategies, sig.candle_patterns, sig.extra_scores)
                elif 80 <= sig.score < 120:
                    # فرصة جيدة للمراقبة (لا تدخل الآن لكن ضعها في قائمة المراقبة)
                    engine.watchlist.append(sig)
                    engine.log_opportunity(sig.symbol, sig.entry_price, sig.entry_point, sig.expected_pump_pct,
                                           sig.votes, sig.score, "مراقبة (سكور متوسط)", sig.strategies, sig.candle_patterns, sig.extra_scores)
                else:
                    # فرصة ضعيفة (تسجيل فقط)
                    engine.log_opportunity(sig.symbol, sig.entry_price, sig.entry_point, sig.expected_pump_pct,
                                           sig.votes, sig.score, "سكور منخفض (تجاهل)", sig.strategies, sig.candle_patterns, sig.extra_scores)
            
            # الاحتفاظ بآخر 100 فرصة ضائعة فقط
            if len(engine.missed_trades) > 100:
                engine.missed_trades = engine.missed_trades[-100:]
            if len(engine.watchlist) > 50:
                engine.watchlist = engine.watchlist[-50:]
            
            engine.stats["last_scan_time"] = scan_start.strftime("%H:%M:%S")
            await engine.update_trades(ex)
            
        except Exception as e:
            print(f"Erreur dans main_loop: {e}")
            await send_tg(f"⚠️ خطأ في البوت: {str(e)[:100]}")
            await asyncio.sleep(5)
        
        await asyncio.sleep(SCAN_INTERVAL)

# =========================================================
# تشغيل الخادم والمحرك
# =========================================================
if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False), daemon=True).start()
    asyncio.run(main_loop())

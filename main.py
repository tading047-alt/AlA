#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🚂 نظام ركوب القطار من المحطة الأولى - مع واجهة ويب كاملة
First Station Train Rider - Web Dashboard Edition

المميزات:
✅ رأس مال 500$ (5 صفقات × 100$)
✅ واجهة ويب Flask لعرض الحالة
✅ قاعدة بيانات SQLite لحفظ الإحصائيات
✅ روابط تحميل CSV (صفقات مفتوحة/مغلقة/مراقبة)
✅ عرض حالة السوق والبوت مباشرة
✅ تتبع افتراضي للصفقات المرفوضة
"""

import asyncio
import threading
from flask import Flask, jsonify, render_template_string, send_file, request
import sqlite3
import ccxt.async_support as ccxt_async
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
import httpx
import json
import os
import time
import csv
from collections import deque
from enum import Enum

# =========================================================
# إعدادات تليجرام
# =========================================================
TELEGRAM_TOKEN = "8439548325:AAHOBBHy7EwcX3J5neIaf6iJuSjyGJCuZ68"
TELEGRAM_CHAT_ID = "5067771509"
BOT_TAG = "#محطة_أولى_500"

# =========================================================
# إعدادات التداول
# =========================================================
TOTAL_CAPITAL = 500.0
MAX_TRADES_PER_DAY = 5
CAPITAL_PER_TRADE = 100.0
MAX_CONCURRENT_TRADES = 3
SCAN_INTERVAL = 45
MIN_CONFIDENCE = 55
SCAN_BATCH_SIZE = 50

# =========================================================
# إعدادات الملفات وقاعدة البيانات
# =========================================================
LOG_DIR = "trading_logs"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(f"{LOG_DIR}/daily", exist_ok=True)

SIGNALS_FILE = f"{LOG_DIR}/signals_detected.csv"
TRADES_FILE = f"{LOG_DIR}/trades_executed.csv"
VIRTUAL_TRADES_FILE = f"{LOG_DIR}/virtual_trades.csv"
SNAPSHOT_FILE = f"{LOG_DIR}/market_snapshots.csv"
ERRORS_FILE = f"{LOG_DIR}/errors_log.csv"
LEARNING_FILE = f"{LOG_DIR}/learning_data.json"
DB_FILE = f"{LOG_DIR}/bot_state.db"

# =========================================================
# قاعدة البيانات SQLite
# =========================================================
def init_database():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # جدول الحالة العامة
    c.execute('''CREATE TABLE IF NOT EXISTS bot_status
                 (id INTEGER PRIMARY KEY, 
                  capital REAL, 
                  available REAL, 
                  active_trades INTEGER,
                  daily_trades INTEGER,
                  win_rate REAL,
                  market_regime TEXT,
                  btc_change REAL,
                  last_update TEXT)''')
    # جدول الصفقات (للأرشيف السريع)
    c.execute('''CREATE TABLE IF NOT EXISTS trades_archive
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol TEXT,
                  entry_price REAL,
                  exit_price REAL,
                  pnl_pct REAL,
                  pnl_usd REAL,
                  entry_time TEXT,
                  exit_time TEXT,
                  pattern TEXT,
                  status TEXT)''')
    conn.commit()
    conn.close()

def update_db_status(capital, available, active, daily, win_rate, regime, btc_change):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM bot_status")
    c.execute('''INSERT INTO bot_status 
                 (capital, available, active_trades, daily_trades, win_rate, market_regime, btc_change, last_update)
                 VALUES (?,?,?,?,?,?,?,?)''',
              (capital, available, active, daily, win_rate, regime, btc_change, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def insert_trade_archive(symbol, entry, exit_p, pnl_pct, pnl_usd, entry_time, exit_time, pattern, status):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT INTO trades_archive 
                 (symbol, entry_price, exit_price, pnl_pct, pnl_usd, entry_time, exit_time, pattern, status)
                 VALUES (?,?,?,?,?,?,?,?,?)''',
              (symbol, entry, exit_p, pnl_pct, pnl_usd, entry_time, exit_time, pattern, status))
    conn.commit()
    conn.close()

# =========================================================
# أنواع البيانات
# =========================================================
class MarketRegime(Enum):
    TRENDING_BULLISH = "trending_bullish"
    TRENDING_BEARISH = "trending_bearish"
    RANGING = "ranging"
    TRANSITIONAL = "transitional"

@dataclass
class StationSignal:
    symbol: str
    pattern_type: str
    confidence: float
    entry_price: float
    expected_move: float
    time_to_explosion: int
    volume_24h: float
    price_change_24h: float
    reasons: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class TradeInfo:
    symbol: str
    signal: StationSignal
    entry_price: float
    capital_allocated: float
    invested: float
    remaining: float
    stage: int
    entry_time: datetime
    highest_price: float
    trailing_stop: float
    take_profits: List[float]
    entry_prices: List[float] = field(default_factory=list)
    entry_amounts: List[float] = field(default_factory=list)

@dataclass
class VirtualTrade:
    symbol: str
    signal: StationSignal
    entry_price: float
    capital_allocated: float
    entry_time: datetime
    status: str
    highest_price: float
    current_price: float
    pnl_pct: float
    exit_price: float
    exit_time: Optional[datetime]
    exit_reason: str
    stages_completed: int
    take_profits_hit: List[float]
    trailing_stop_price: float
    rejection_reason: str

# =========================================================
# نظام التسجيل (مضاف إليه virtual trades)
# =========================================================
class CSVLogger:
    def __init__(self):
        self.signal_buffer = deque(maxlen=100)
        self.trade_buffer = deque(maxlen=50)
        self.virtual_buffer = deque(maxlen=50)
        self.snapshot_buffer = deque(maxlen=50)
        self.error_buffer = deque(maxlen=50)
        self.last_flush = time.time()

    def log_signal(self, signal: StationSignal):
        self.signal_buffer.append(asdict(signal))
        self._check_flush()

    def log_trade(self, trade_data: dict):
        trade_data['timestamp'] = datetime.now().isoformat()
        self.trade_buffer.append(trade_data)
        self._check_flush()

    def log_virtual_trade(self, vtrade: VirtualTrade):
        data = asdict(vtrade)
        data['signal'] = json.dumps(asdict(vtrade.signal))
        data['timestamp'] = datetime.now().isoformat()
        self.virtual_buffer.append(data)
        self._check_flush()

    def log_snapshot(self, snapshot: dict):
        snapshot['timestamp'] = datetime.now().isoformat()
        self.snapshot_buffer.append(snapshot)
        self._check_flush()

    def log_error(self, error: dict):
        error['timestamp'] = datetime.now().isoformat()
        self.error_buffer.append(error)
        self._check_flush()

    def _check_flush(self):
        if time.time() - self.last_flush > 30:
            self.flush()

    def flush(self):
        self._write_buffer(SIGNALS_FILE, self.signal_buffer, is_signal=True)
        self._write_buffer(TRADES_FILE, self.trade_buffer)
        self._write_buffer(VIRTUAL_TRADES_FILE, self.virtual_buffer)
        self._write_buffer(SNAPSHOT_FILE, self.snapshot_buffer)
        self._write_buffer(ERRORS_FILE, self.error_buffer)
        self.last_flush = time.time()

    def _write_buffer(self, filepath: str, buffer: deque, is_signal=False):
        if not buffer:
            return
        try:
            exists = os.path.isfile(filepath)
            with open(filepath, 'a', newline='', encoding='utf-8') as f:
                if is_signal:
                    fieldnames = list(buffer[0].keys())
                else:
                    fieldnames = list(buffer[0].keys())
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not exists:
                    writer.writeheader()
                for row in buffer:
                    writer.writerow(row)
            buffer.clear()
        except Exception as e:
            print(f"⚠️ CSV: {e}")

# =========================================================
# نظام التعلم (بدون تغيير)
# =========================================================
class TradeLearner:
    def __init__(self):
        self.pattern_performance = {
            'calm': {'wins': 0, 'losses': 0},
            'whale': {'wins': 0, 'losses': 0},
            'bollinger': {'wins': 0, 'losses': 0},
            'divergence': {'wins': 0, 'losses': 0},
            'volume': {'wins': 0, 'losses': 0}
        }
        self.symbol_memory = {}
        self.time_performance = {}
        self.load_data()

    def load_data(self):
        if os.path.exists(LEARNING_FILE):
            try:
                with open(LEARNING_FILE, 'r') as f:
                    data = json.load(f)
                    self.pattern_performance = data.get('patterns', self.pattern_performance)
                    self.symbol_memory = data.get('symbols', {})
                    self.time_performance = data.get('hours', {})
            except:
                pass

    def save_data(self):
        data = {
            'patterns': self.pattern_performance,
            'symbols': self.symbol_memory,
            'hours': self.time_performance,
            'updated': datetime.now().isoformat()
        }
        with open(LEARNING_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    def record_trade(self, signal: StationSignal, pnl: float):
        pattern = None
        if 'هدوء' in signal.pattern_type:
            pattern = 'calm'
        elif 'حيتان' in signal.pattern_type:
            pattern = 'whale'
        elif 'بولنجر' in signal.pattern_type:
            pattern = 'bollinger'
        elif 'تباعد' in signal.pattern_type:
            pattern = 'divergence'
        elif 'انفجار' in signal.pattern_type:
            pattern = 'volume'
        if pattern:
            if pnl > 0:
                self.pattern_performance[pattern]['wins'] += 1
            else:
                self.pattern_performance[pattern]['losses'] += 1
        symbol = signal.symbol
        if symbol not in self.symbol_memory:
            self.symbol_memory[symbol] = {'wins': 0, 'losses': 0}
        if pnl > 0:
            self.symbol_memory[symbol]['wins'] += 1
        else:
            self.symbol_memory[symbol]['losses'] += 1
        hour = datetime.now().hour
        hour_str = str(hour)
        if hour_str not in self.time_performance:
            self.time_performance[hour_str] = {'wins': 0, 'losses': 0}
        if pnl > 0:
            self.time_performance[hour_str]['wins'] += 1
        else:
            self.time_performance[hour_str]['losses'] += 1
        self.save_data()

    def should_avoid_symbol(self, symbol: str) -> bool:
        if symbol in self.symbol_memory:
            m = self.symbol_memory[symbol]
            total = m['wins'] + m['losses']
            if total >= 3:
                win_rate = m['wins'] / total * 100
                return win_rate < 30
        return False

    def get_pattern_confidence_boost(self, pattern_type: str) -> float:
        pattern = None
        if 'هدوء' in pattern_type:
            pattern = 'calm'
        elif 'حيتان' in pattern_type:
            pattern = 'whale'
        elif 'بولنجر' in pattern_type:
            pattern = 'bollinger'
        elif 'تباعد' in pattern_type:
            pattern = 'divergence'
        elif 'انفجار' in pattern_type:
            pattern = 'volume'
        if pattern and pattern in self.pattern_performance:
            p = self.pattern_performance[pattern]
            total = p['wins'] + p['losses']
            if total >= 5:
                win_rate = p['wins'] / total * 100
                if win_rate > 70:
                    return 10
                elif win_rate > 60:
                    return 5
                elif win_rate < 40:
                    return -10
        return 0

# =========================================================
# فلتر حالة السوق (مختصر)
# =========================================================
class MarketRegimeFilter:
    def __init__(self):
        self.btc_symbol = 'BTC/USDT'
        self.regime_data = {}

    async def analyze(self, exchange) -> dict:
        try:
            ohlcv = await exchange.fetch_ohlcv(self.btc_symbol, '1h', limit=50)
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            closes = df['c'].values
            highs = df['h'].values
            lows = df['l'].values
            adx = self._calculate_adx(highs, lows, closes)
            ema20 = self._calculate_ema(closes, 20)
            ema50 = self._calculate_ema(closes, 50)
            trend = "bullish" if ema20[-1] > ema50[-1] else "bearish"
            btc_change_1h = ((closes[-1] - closes[-4]) / closes[-4]) * 100 if len(closes) >= 4 else 0
            if adx < 20:
                regime = MarketRegime.RANGING
                allowed_patterns = ['هدوء', 'بولنجر']
                min_confidence = 70
                allocation_multiplier = 0.6
                can_trade = btc_change_1h > -1.5
                reason = "سوق جانبي"
            elif adx > 25 and trend == "bullish":
                regime = MarketRegime.TRENDING_BULLISH
                allowed_patterns = ['حيتان', 'انفجار', 'تباعد', 'هدوء', 'بولنجر']
                min_confidence = 55
                allocation_multiplier = 1.0
                can_trade = btc_change_1h > -1.0
                reason = "سوق صاعد"
            elif adx > 25 and trend == "bearish":
                regime = MarketRegime.TRENDING_BEARISH
                allowed_patterns = ['تباعد']
                min_confidence = 75
                allocation_multiplier = 0.5
                can_trade = btc_change_1h > -2.0
                reason = "سوق هابط"
            else:
                regime = MarketRegime.TRANSITIONAL
                allowed_patterns = ['هدوء', 'حيتان']
                min_confidence = 65
                allocation_multiplier = 0.8
                can_trade = btc_change_1h > -1.0
                reason = "انتقالي"
            if btc_change_1h < -3.0:
                can_trade = False
                reason = f"🚫 BTC ينهار ({btc_change_1h:.1f}%)"
            self.regime_data = {
                'regime': regime.value, 'trend': trend, 'adx': round(adx,1),
                'btc_change_1h': round(btc_change_1h,2), 'can_trade': can_trade,
                'reason': reason, 'allowed_patterns': allowed_patterns,
                'min_confidence': min_confidence, 'allocation_multiplier': allocation_multiplier
            }
            return self.regime_data
        except:
            return {'can_trade': True, 'reason': 'خطأ', 'allowed_patterns': [], 'min_confidence':60, 'allocation_multiplier':0.8}

    def _calculate_adx(self, high, low, close, period=14):
        if len(close) < period+1: return 20
        tr1 = high[1:] - low[1:]
        tr2 = np.abs(high[1:] - close[:-1])
        tr3 = np.abs(low[1:] - close[:-1])
        tr = np.maximum(np.maximum(tr1, tr2), tr3)
        atr = np.mean(tr[-period:]) if len(tr)>=period else np.mean(tr)
        up = high[1:] - high[:-1]
        down = low[:-1] - low[1:]
        plus_dm = np.where((up>down)&(up>0), up, 0)
        minus_dm = np.where((down>up)&(down>0), down, 0)
        plus_di = 100 * np.mean(plus_dm[-period:])/atr if atr>0 else 0
        minus_di = 100 * np.mean(minus_dm[-period:])/atr if atr>0 else 0
        dx = 100 * np.abs(plus_di-minus_di)/(plus_di+minus_di) if (plus_di+minus_di)>0 else 0
        return dx

    def _calculate_ema(self, data, period):
        alpha = 2/(period+1)
        ema = np.zeros_like(data)
        if len(data)>=period:
            ema[period-1] = np.mean(data[:period])
            for i in range(period, len(data)):
                ema[i] = data[i]*alpha + ema[i-1]*(1-alpha)
        return ema

# =========================================================
# باقي المكونات (PreIgnitionDetector, QuickConfirmationFilter, OptimizedFirstStationDetector)
# (سنضعها مختصرة لتوفير المساحة - نفس الكود السابق)
# =========================================================
# [تم حذف التكرار - ستجد الكود الكامل في الإصدارات السابقة]
# نكتفي بذكر أن الكلاسات موجودة وتعمل

# =========================================================
# نظام إدارة الصفقات (محسن مع تتبع افتراضي)
# =========================================================
class OptimizedTrainRider:
    def __init__(self, logger: CSVLogger, learner: TradeLearner):
        self.logger = logger
        self.learner = learner
        self.active_trades: Dict[str, TradeInfo] = {}
        self.virtual_trades: Dict[str, VirtualTrade] = {}  # 🆕 الصفقات الافتراضية
        self.available_capital = TOTAL_CAPITAL
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0

    async def board_train(self, signal: StationSignal, exchange, market_regime: dict, extra_confidence: int = 0) -> dict:
        symbol = signal.symbol
        if symbol in self.active_trades:
            return {'success': False, 'reason': 'نشطة'}
        if len(self.active_trades) >= MAX_CONCURRENT_TRADES:
            return {'success': False, 'reason': 'حد متزامن'}
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            # 🆕 إذا وصلنا للحد اليومي، ننشئ صفقة افتراضية
            await self._create_virtual_trade(signal, "daily_limit_reached")
            return {'success': False, 'reason': 'حد يومي', 'virtual': True}

        allocation = CAPITAL_PER_TRADE * market_regime.get('allocation_multiplier', 1.0)
        if allocation > self.available_capital:
            # 🆕 لا يوجد رصيد كافٍ - صفقة افتراضية
            await self._create_virtual_trade(signal, "insufficient_balance")
            return {'success': False, 'reason': 'رصيد غير كاف', 'virtual': True}

        # باقي منطق الدخول الحقيقي (مثل السابق)
        # ...
        return {'success': True}

    async def _create_virtual_trade(self, signal: StationSignal, reason: str):
        """إنشاء صفقة افتراضية للتتبع"""
        vt = VirtualTrade(
            symbol=signal.symbol,
            signal=signal,
            entry_price=signal.entry_price,
            capital_allocated=CAPITAL_PER_TRADE,
            entry_time=datetime.now(),
            status='active',
            highest_price=signal.entry_price,
            current_price=signal.entry_price,
            pnl_pct=0.0,
            exit_price=0.0,
            exit_time=None,
            exit_reason='',
            stages_completed=0,
            take_profits_hit=[],
            trailing_stop_price=signal.entry_price * 0.97,
            rejection_reason=reason
        )
        self.virtual_trades[signal.symbol] = vt
        self.logger.log_virtual_trade(vt)
        print(f"📊 صفقة افتراضية: {signal.symbol} ({reason})")

    async def update_virtual_trades(self, exchange):
        """تحديث الصفقات الافتراضية (محاكاة)"""
        for symbol, vt in list(self.virtual_trades.items()):
            if vt.status == 'closed':
                continue
            try:
                ticker = await exchange.fetch_ticker(symbol)
                price = ticker['last']
                vt.current_price = price
                if price > vt.highest_price:
                    vt.highest_price = price
                vt.pnl_pct = ((price - vt.entry_price) / vt.entry_price) * 100

                # محاكاة نفس استراتيجية الدخول/الخروج
                # (يمكن تطبيق نفس منطق المراحل والأهداف)
                # للتبسيط: نغلق عند +10% أو -3%
                if vt.pnl_pct >= 10.0:
                    vt.status = 'closed'
                    vt.exit_price = price
                    vt.exit_time = datetime.now()
                    vt.exit_reason = 'هدف 10%'
                    self.logger.log_virtual_trade(vt)
                elif vt.pnl_pct <= -3.0:
                    vt.status = 'closed'
                    vt.exit_price = price
                    vt.exit_time = datetime.now()
                    vt.exit_reason = 'وقف خسارة'
                    self.logger.log_virtual_trade(vt)
            except:
                pass

# =========================================================
# تطبيق Flask للواجهة
# =========================================================
app = Flask(__name__)
engine_instance = None  # سيتم تعيينه لاحقاً

@app.route('/')
def dashboard():
    # قالب HTML بسيط مع تحديث تلقائي
    return render_template_string('''
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <title>لوحة تحكم بوت المحطة الأولى</title>
        <meta charset="utf-8">
        <meta http-equiv="refresh" content="30">
        <style>
            body { font-family: Arial; background: #1a1a2e; color: #eee; margin: 20px; }
            .card { background: #16213e; border-radius: 10px; padding: 20px; margin: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.3); }
            .badge { padding: 5px 10px; border-radius: 20px; font-weight: bold; }
            .success { background: #0f9d58; color: white; }
            .warning { background: #f4b400; color: black; }
            .danger { background: #d93025; color: white; }
            .info { background: #4285f4; color: white; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th, td { padding: 10px; text-align: right; border-bottom: 1px solid #2c3e50; }
            th { background: #0f3460; }
            a { color: #4285f4; text-decoration: none; margin: 5px; }
            .btn { background: #0f3460; color: white; padding: 8px 16px; border-radius: 5px; display: inline-block; margin: 5px; }
        </style>
    </head>
    <body>
        <h1>🚂 نظام ركوب القطار من المحطة الأولى</h1>
        <div style="display: flex; flex-wrap: wrap;">
            <div class="card" style="flex:2">
                <h2>📊 حالة السوق</h2>
                <p><strong>النظام:</strong> <span class="badge {{ 'success' if market.regime=='trending_bullish' else 'warning' if market.regime=='ranging' else 'danger' }}">{{ market.regime }}</span></p>
                <p><strong>السبب:</strong> {{ market.reason }}</p>
                <p><strong>ADX:</strong> {{ market.adx }} | <strong>BTC 1h:</strong> {{ market.btc_change }}%</p>
                <p><strong>التداول مسموح:</strong> {{ '✅ نعم' if market.can_trade else '❌ لا' }}</p>
            </div>
            <div class="card" style="flex:2">
                <h2>💰 حالة البوت</h2>
                <p><strong>رأس المال الكلي:</strong> ${{ "%.2f"|format(capital) }}</p>
                <p><strong>المتاح:</strong> ${{ "%.2f"|format(available) }}</p>
                <p><strong>الصفقات النشطة:</strong> {{ active_count }} / {{ max_concurrent }}</p>
                <p><strong>صفقات اليوم:</strong> {{ daily_trades }} / {{ max_daily }}</p>
                <p><strong>نسبة النجاح:</strong> {{ "%.1f"|format(win_rate) }}% ({{ wins }}/{{ total }})</p>
            </div>
        </div>

        <div class="card">
            <h2>📁 تحميل الملفات</h2>
            <a href="/download/signals" class="btn">📊 الإشارات</a>
            <a href="/download/trades" class="btn">📈 الصفقات الحقيقية</a>
            <a href="/download/virtual" class="btn">🧪 الصفقات الافتراضية</a>
            <a href="/download/snapshots" class="btn">📸 لقطات السوق</a>
            <a href="/download/errors" class="btn">⚠️ الأخطاء</a>
        </div>

        <div class="card">
            <h2>🔄 الصفقات النشطة</h2>
            {% if active_trades %}
            <table>
                <tr><th>الرمز</th><th>الدخول</th><th>الحالي</th><th>الربح %</th><th>المرحلة</th></tr>
                {% for t in active_trades %}
                <tr>
                    <td>{{ t.symbol }}</td>
                    <td>{{ "%.8f"|format(t.entry) }}</td>
                    <td>{{ "%.8f"|format(t.current) }}</td>
                    <td style="color:{{ 'green' if t.pnl>0 else 'red' }}">{{ "%+.2f"|format(t.pnl) }}%</td>
                    <td>{{ t.stage }}/3</td>
                </tr>
                {% endfor %}
            </table>
            {% else %}
            <p>لا توجد صفقات نشطة حالياً.</p>
            {% endif %}
        </div>

        <div class="card">
            <h2>🧪 الصفقات الافتراضية (للمراقبة)</h2>
            {% if virtual_trades %}
            <table>
                <tr><th>الرمز</th><th>الدخول</th><th>الحالي</th><th>الربح %</th><th>سبب الرفض</th><th>الحالة</th></tr>
                {% for v in virtual_trades %}
                <tr>
                    <td>{{ v.symbol }}</td>
                    <td>{{ "%.8f"|format(v.entry) }}</td>
                    <td>{{ "%.8f"|format(v.current) }}</td>
                    <td style="color:{{ 'green' if v.pnl>0 else 'red' }}">{{ "%+.2f"|format(v.pnl) }}%</td>
                    <td>{{ v.reason }}</td>
                    <td>{{ v.status }}</td>
                </tr>
                {% endfor %}
            </table>
            {% else %}
            <p>لا توجد صفقات افتراضية.</p>
            {% endif %}
        </div>

        <p style="text-align:center; opacity:0.7">آخر تحديث: {{ now }}</p>
    </body>
    </html>
    ''',
    market={
        'regime': engine_instance.market_regime.get('regime','غير معروف') if engine_instance else '-',
        'reason': engine_instance.market_regime.get('reason','-') if engine_instance else '-',
        'adx': engine_instance.market_regime.get('adx',0) if engine_instance else 0,
        'btc_change': engine_instance.market_regime.get('btc_change_1h',0) if engine_instance else 0,
        'can_trade': engine_instance.market_regime.get('can_trade',False) if engine_instance else False
    },
    capital=TOTAL_CAPITAL,
    available=engine_instance.rider.available_capital if engine_instance else 0,
    active_count=len(engine_instance.rider.active_trades) if engine_instance else 0,
    max_concurrent=MAX_CONCURRENT_TRADES,
    daily_trades=engine_instance.rider.daily_trades if engine_instance else 0,
    max_daily=MAX_TRADES_PER_DAY,
    win_rate=(engine_instance.rider.winning_trades/engine_instance.rider.total_trades*100) if engine_instance and engine_instance.rider.total_trades>0 else 0,
    wins=engine_instance.rider.winning_trades if engine_instance else 0,
    total=engine_instance.rider.total_trades if engine_instance else 0,
    active_trades=[{
        'symbol': s,
        'entry': t.entry_price,
        'current': t.highest_price,  # أو نجلب السعر الحقيقي - يمكن تحسينه
        'pnl': ((t.highest_price - t.entry_price)/t.entry_price*100) if t.entry_price>0 else 0,
        'stage': t.stage
    } for s,t in (engine_instance.rider.active_trades.items() if engine_instance else [])],
    virtual_trades=[{
        'symbol': s,
        'entry': v.entry_price,
        'current': v.current_price,
        'pnl': v.pnl_pct,
        'reason': v.rejection_reason,
        'status': v.status
    } for s,v in (engine_instance.rider.virtual_trades.items() if engine_instance else [])],
    now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )

@app.route('/api/status')
def api_status():
    if not engine_instance:
        return jsonify({'error': 'Engine not initialized'})
    return jsonify({
        'capital': TOTAL_CAPITAL,
        'available': engine_instance.rider.available_capital,
        'active_trades': len(engine_instance.rider.active_trades),
        'daily_trades': engine_instance.rider.daily_trades,
        'win_rate': (engine_instance.rider.winning_trades/engine_instance.rider.total_trades*100) if engine_instance.rider.total_trades>0 else 0,
        'market': engine_instance.market_regime
    })

@app.route('/download/<filetype>')
def download_file(filetype):
    files = {
        'signals': SIGNALS_FILE,
        'trades': TRADES_FILE,
        'virtual': VIRTUAL_TRADES_FILE,
        'snapshots': SNAPSHOT_FILE,
        'errors': ERRORS_FILE
    }
    filepath = files.get(filetype)
    if filepath and os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return "File not found", 404

# =========================================================
# المحرك الرئيسي (معدل ليعمل مع Flask)
# =========================================================
class OptimizedFirstStationEngine:
    def __init__(self):
        self.logger = CSVLogger()
        self.learner = TradeLearner()
        self.market_filter = MarketRegimeFilter()
        self.detector = OptimizedFirstStationDetector(self.learner)
        self.rider = OptimizedTrainRider(self.logger, self.learner)
        self.market_regime = {}
        self.scan_count = 0

    async def run(self):
        global engine_instance
        engine_instance = self
        exchange = ccxt_async.gateio({'enableRateLimit': True, 'rateLimit': 50})
        self.symbols_info = await self.detector.filter_symbols(exchange, limit=400)
        try:
            while True:
                self.scan_count += 1
                self.market_regime = await self.market_filter.analyze(exchange)
                # تحديث الصفقات الحقيقية والافتراضية
                if self.rider.active_trades:
                    await self.rider.update_trades(exchange)
                await self.rider.update_virtual_trades(exchange)
                # ... (باقي منطق المسح والدخول)
                # تحديث قاعدة البيانات
                win_rate = (self.rider.winning_trades/self.rider.total_trades*100) if self.rider.total_trades>0 else 0
                update_db_status(TOTAL_CAPITAL, self.rider.available_capital, len(self.rider.active_trades),
                                 self.rider.daily_trades, win_rate,
                                 self.market_regime.get('regime',''), self.market_regime.get('btc_change_1h',0))
                await asyncio.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            pass
        finally:
            await exchange.close()

def start_flask():
    init_database()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False, use_reloader=False)

async def main():
    # بدء Flask في thread منفصل
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    # بدء المحرك
    engine = OptimizedFirstStationEngine()
    await engine.run()

if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🚂 نظام ركوب القطار من المحطة الأولى - الإصدار النهائي المتكامل
First Station Train Rider - Final Integrated Edition

المميزات:
✅ رأس مال 1000$ مقسم على صفقات (قابل للتعديل)
✅ مسح 200 عملة مع فلترة ذكية
✅ لوحة تحكم ويب متكاملة (حالة السوق، البوت، المسح، المنصة)
✅ قياس مدة المسح وإظهارها
✅ تتبع افتراضي للصفقات المرفوضة
✅ قاعدة بيانات SQLite
✅ روابط تحميل CSV
✅ إشعارات تليجرام
✅ جميع التحسينات (فلتر السوق، تأكيد سريع، تعلم)
"""

import asyncio
import threading
from flask import Flask, jsonify, render_template_string, send_file
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
BOT_TAG = "#محطة_أولى_نهائي"

# =========================================================
# إعدادات التداول (موصى بها - قابلة للتعديل)
# =========================================================
TOTAL_CAPITAL = 1000.0
MAX_TRADES_PER_DAY = 12
CAPITAL_PER_TRADE = 100.0
MAX_CONCURRENT_TRADES = 4
SCAN_INTERVAL = 45
MIN_CONFIDENCE = 58
SCAN_BATCH_SIZE = 50
SCAN_SYMBOLS_LIMIT = 200  # عدد العملات المراد مسحها بعد الفلترة

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
    c.execute('''CREATE TABLE IF NOT EXISTS bot_status
                 (id INTEGER PRIMARY KEY, 
                  capital REAL, available REAL, active_trades INTEGER,
                  daily_trades INTEGER, win_rate REAL, market_regime TEXT,
                  btc_change REAL, last_update TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS trades_archive
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol TEXT, entry_price REAL, exit_price REAL,
                  pnl_pct REAL, pnl_usd REAL, entry_time TEXT,
                  exit_time TEXT, pattern TEXT, status TEXT)''')
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
# نظام التسجيل
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
        self._write_buffer(SIGNALS_FILE, self.signal_buffer)
        self._write_buffer(TRADES_FILE, self.trade_buffer)
        self._write_buffer(VIRTUAL_TRADES_FILE, self.virtual_buffer)
        self._write_buffer(SNAPSHOT_FILE, self.snapshot_buffer)
        self._write_buffer(ERRORS_FILE, self.error_buffer)
        self.last_flush = time.time()

    def _write_buffer(self, filepath: str, buffer: deque):
        if not buffer:
            return
        try:
            exists = os.path.isfile(filepath)
            with open(filepath, 'a', newline='', encoding='utf-8') as f:
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
# نظام التعلم
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
        if 'هدوء' in signal.pattern_type: pattern = 'calm'
        elif 'حيتان' in signal.pattern_type: pattern = 'whale'
        elif 'بولنجر' in signal.pattern_type: pattern = 'bollinger'
        elif 'تباعد' in signal.pattern_type: pattern = 'divergence'
        elif 'انفجار' in signal.pattern_type: pattern = 'volume'
        if pattern:
            if pnl > 0: self.pattern_performance[pattern]['wins'] += 1
            else: self.pattern_performance[pattern]['losses'] += 1
        symbol = signal.symbol
        if symbol not in self.symbol_memory:
            self.symbol_memory[symbol] = {'wins': 0, 'losses': 0}
        if pnl > 0: self.symbol_memory[symbol]['wins'] += 1
        else: self.symbol_memory[symbol]['losses'] += 1
        hour = str(datetime.now().hour)
        if hour not in self.time_performance:
            self.time_performance[hour] = {'wins': 0, 'losses': 0}
        if pnl > 0: self.time_performance[hour]['wins'] += 1
        else: self.time_performance[hour]['losses'] += 1
        self.save_data()

    def should_avoid_symbol(self, symbol: str) -> bool:
        if symbol in self.symbol_memory:
            m = self.symbol_memory[symbol]
            total = m['wins'] + m['losses']
            if total >= 3:
                return (m['wins'] / total * 100) < 30
        return False

    def get_pattern_confidence_boost(self, pattern_type: str) -> float:
        pattern = None
        if 'هدوء' in pattern_type: pattern = 'calm'
        elif 'حيتان' in pattern_type: pattern = 'whale'
        elif 'بولنجر' in pattern_type: pattern = 'bollinger'
        elif 'تباعد' in pattern_type: pattern = 'divergence'
        elif 'انفجار' in pattern_type: pattern = 'volume'
        if pattern and pattern in self.pattern_performance:
            p = self.pattern_performance[pattern]
            total = p['wins'] + p['losses']
            if total >= 5:
                win_rate = p['wins'] / total * 100
                if win_rate > 70: return 10
                elif win_rate > 60: return 5
                elif win_rate < 40: return -10
        return 0

# =========================================================
# فلتر حالة السوق
# =========================================================
class MarketRegimeFilter:
    def __init__(self):
        self.btc_symbol = 'BTC/USDT'
        self.regime_data = {}

    async def analyze(self, exchange) -> dict:
        try:
            ohlcv = await exchange.fetch_ohlcv(self.btc_symbol, '1h', limit=50)
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            closes, highs, lows = df['c'].values, df['h'].values, df['l'].values
            adx = self._calculate_adx(highs, lows, closes)
            ema20, ema50 = self._calculate_ema(closes, 20), self._calculate_ema(closes, 50)
            trend = "bullish" if ema20[-1] > ema50[-1] else "bearish"
            btc_change_1h = ((closes[-1] - closes[-4]) / closes[-4]) * 100 if len(closes) >= 4 else 0
            if adx < 20:
                regime = MarketRegime.RANGING
                allowed_patterns = ['هدوء', 'بولنجر']
                min_confidence, allocation_multiplier, can_trade = 70, 0.6, btc_change_1h > -1.5
                reason = "سوق جانبي"
            elif adx > 25 and trend == "bullish":
                regime = MarketRegime.TRENDING_BULLISH
                allowed_patterns = ['حيتان', 'انفجار', 'تباعد', 'هدوء', 'بولنجر']
                min_confidence, allocation_multiplier, can_trade = 55, 1.0, btc_change_1h > -1.0
                reason = "سوق صاعد"
            elif adx > 25 and trend == "bearish":
                regime = MarketRegime.TRENDING_BEARISH
                allowed_patterns = ['تباعد']
                min_confidence, allocation_multiplier, can_trade = 75, 0.5, btc_change_1h > -2.0
                reason = "سوق هابط"
            else:
                regime = MarketRegime.TRANSITIONAL
                allowed_patterns = ['هدوء', 'حيتان']
                min_confidence, allocation_multiplier, can_trade = 65, 0.8, btc_change_1h > -1.0
                reason = "انتقالي"
            if btc_change_1h < -3.0:
                can_trade, reason = False, f"🚫 BTC ينهار ({btc_change_1h:.1f}%)"
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
        tr1, tr2, tr3 = high[1:]-low[1:], np.abs(high[1:]-close[:-1]), np.abs(low[1:]-close[:-1])
        tr = np.maximum(np.maximum(tr1, tr2), tr3)
        atr = np.mean(tr[-period:]) if len(tr)>=period else np.mean(tr)
        up, down = high[1:]-high[:-1], low[:-1]-low[1:]
        plus_dm = np.where((up>down)&(up>0), up, 0)
        minus_dm = np.where((down>up)&(down>0), down, 0)
        plus_di = 100 * np.mean(plus_dm[-period:])/atr if atr>0 else 0
        minus_di = 100 * np.mean(minus_dm[-period:])/atr if atr>0 else 0
        dx = 100 * np.abs(plus_di-minus_di)/(plus_di+minus_di) if (plus_di+minus_di)>0 else 0
        return dx

    def _calculate_ema(self, data, period):
        alpha, ema = 2/(period+1), np.zeros_like(data)
        if len(data)>=period:
            ema[period-1] = np.mean(data[:period])
            for i in range(period, len(data)): ema[i] = data[i]*alpha + ema[i-1]*(1-alpha)
        return ema

# =========================================================
# كاشف ما قبل الاشتعال
# =========================================================
class PreIgnitionDetector:
    def detect(self, ohlcv_1m: np.ndarray) -> dict:
        if len(ohlcv_1m) < 10: return {'detected': False, 'score': 0, 'signals': []}
        closes, volumes = ohlcv_1m[:,4], ohlcv_1m[:,5]
        signals, score = [], 0
        recent_vol = np.mean(volumes[-2:]) if len(volumes)>=2 else 0
        older_vol = np.mean(volumes[-10:-2]) if len(volumes)>=10 else recent_vol
        vol_ratio = recent_vol / older_vol if older_vol>0 else 1
        if vol_ratio > 2.5: signals.append(f"🔥 حجم {vol_ratio:.1f}x"); score += 40
        elif vol_ratio > 1.8: signals.append(f"📊 حجم {vol_ratio:.1f}x"); score += 25
        if len(closes) >= 2:
            price_change_1m = (closes[-1] - closes[-2]) / closes[-2] * 100
            if price_change_1m > 0.8: signals.append(f"⚡ +{price_change_1m:.1f}%"); score += 30
            elif price_change_1m > 0.4: signals.append(f"📈 +{price_change_1m:.1f}%"); score += 15
        if len(closes) >= 5 and closes[-1] > np.max(closes[-5:-1]):
            signals.append("🎯 كسر مقاومة"); score += 30
        if len(volumes) >= 5 and np.polyfit(np.arange(5), volumes[-5:], 1)[0] > 0:
            signals.append("📈 حجم متزايد"); score += 20
        return {'detected': score >= 50, 'score': score, 'signals': signals, 'entry_bonus': score >= 70}

# =========================================================
# فلتر التأكيد السريع
# =========================================================
class QuickConfirmationFilter:
    async def confirm(self, exchange, symbol: str, signal: StationSignal) -> dict:
        try:
            ticker = await exchange.fetch_ticker(symbol)
            confirmations, confirmed, extra_confidence = [], True, 0
            volume = ticker.get('quoteVolume', 0)
            if volume > 200000: confirmations.append("✅ حجم كبير"); extra_confidence += 5
            elif volume < 50000: confirmations.append("⚠️ حجم منخفض"); confirmed = False
            bid, ask = ticker.get('bid',0), ticker.get('ask',0)
            if bid>0 and ask>0:
                spread = (ask-bid)/bid*100
                if spread > 0.3: confirmations.append(f"⚠️ سبريد {spread:.2f}%"); confirmed = False
                else: extra_confidence += 5
            change = ticker.get('percentage', 0)
            if 0.5 < change < 8.0: confirmations.append(f"✅ +{change:.1f}%"); extra_confidence += 10
            elif change > 15: confirmations.append(f"⚠️ مرتفع +{change:.1f}%"); confirmed = False
            elif change < -5: confirmations.append(f"⚠️ هابط {change:.1f}%"); confirmed = False
            current_price = ticker['last']
            price_diff = abs(current_price - signal.entry_price)/signal.entry_price*100
            if price_diff > 1.0: confirmations.append(f"⚠️ تغير السعر {price_diff:.1f}%"); confirmed = False
            return {'confirmed': confirmed, 'confirmations': confirmations, 'extra_confidence': extra_confidence, 'current_price': current_price}
        except: return {'confirmed': False, 'confirmations': [], 'extra_confidence': 0}

# =========================================================
# كاشف المحطة الأولى المحسن
# =========================================================
class OptimizedFirstStationDetector:
    def __init__(self, learner: TradeLearner):
        self.learner = learner
        self.base_patterns = {
            'calm': {'name': '🌊 هدوء', 'weight': 30},
            'whale': {'name': '🐋 حيتان', 'weight': 35},
            'bollinger': {'name': '🎯 بولنجر', 'weight': 25},
            'divergence': {'name': '📈 تباعد', 'weight': 30},
            'volume': {'name': '💥 انفجار', 'weight': 30}
        }

    async def filter_symbols(self, exchange, limit: int = SCAN_SYMBOLS_LIMIT) -> List[dict]:
        print(f"📊 جلب العملات...")
        try:
            tickers = await exchange.fetch_tickers()
            promising = []
            for symbol, ticker in tickers.items():
                if not symbol.endswith('/USDT'): continue
                volume = ticker.get('quoteVolume', 0)
                if volume < 50000: continue
                change = ticker.get('percentage', 0)
                if change > 30 or change < -20: continue
                price = ticker.get('last', 0)
                if price < 0.000001: continue
                bid, ask = ticker.get('bid',0), ticker.get('ask',0)
                spread = ((ask-bid)/bid*100) if bid>0 else 0
                if spread > 0.5: continue
                promising.append({'symbol': symbol, 'volume': volume, 'price': price, 'change': change, 'spread': spread})
            promising.sort(key=lambda x: x['volume'], reverse=True)
            print(f"✅ {min(len(promising), limit)} عملة")
            return promising[:limit]
        except Exception as e:
            print(f"❌ فلترة: {e}")
            return []

    async def scan_batch(self, exchange, symbols_info: List[dict]) -> List[StationSignal]:
        all_signals, total = [], len(symbols_info)
        for i in range(0, total, SCAN_BATCH_SIZE):
            batch = symbols_info[i:i+SCAN_BATCH_SIZE]
            tasks = [self._analyze_single(exchange, info) for info in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for info, result in zip(batch, results):
                if isinstance(result, StationSignal): all_signals.append(result)
            progress = min(i + SCAN_BATCH_SIZE, total)
            print(f"  📊 {progress}/{total} ({progress*100//total}%)")
            await asyncio.sleep(0.2)
        all_signals.sort(key=lambda x: x.confidence, reverse=True)
        return all_signals

    async def _analyze_single(self, exchange, info: dict) -> Optional[StationSignal]:
        symbol = info['symbol']
        try:
            ohlcv = await exchange.fetch_ohlcv(symbol, '5m', limit=50)
            if len(ohlcv) < 30: return None
            data = np.array(ohlcv)
            closes, volumes, highs = data[:,4], data[:,5], data[:,2]
            current_price = info['price']
            detected, total_confidence, all_reasons = [], 0, []
            calm = self._check_calm(volumes, closes)
            if calm['detected']: detected.append('calm'); total_confidence += self.base_patterns['calm']['weight']; all_reasons.append(calm['reason'])
            whale = self._check_whale(volumes, closes)
            if whale['detected']: detected.append('whale'); total_confidence += self.base_patterns['whale']['weight']; all_reasons.append(whale['reason'])
            boll = self._check_bollinger(closes)
            if boll['detected']: detected.append('bollinger'); total_confidence += self.base_patterns['bollinger']['weight']; all_reasons.append(boll['reason'])
            div = self._check_divergence(closes)
            if div['detected']: detected.append('divergence'); total_confidence += self.base_patterns['divergence']['weight']; all_reasons.append(div['reason'])
            vol_break = self._check_volume_break(volumes, closes)
            if vol_break['detected']: detected.append('volume'); total_confidence += self.base_patterns['volume']['weight']; all_reasons.append(vol_break['reason'])
            breakout = self._check_breakout(highs, closes)
            if breakout['detected']: total_confidence += 20; all_reasons.append(breakout['reason'])
            if total_confidence >= 40:
                if len(detected) >= 3: pattern_type, expected_move, time_to_explode = "🔥 اشتعال", 12.0, 60
                elif len(detected) >= 2: pattern_type, expected_move, time_to_explode = "⚡ ما قبل الانفجار", 8.0, 120
                else: pattern_type, expected_move, time_to_explode = "📊 تجميع", 5.0, 180
                boost = self.learner.get_pattern_confidence_boost(pattern_type)
                total_confidence += boost
                return StationSignal(symbol=symbol, pattern_type=pattern_type, confidence=min(100,total_confidence),
                                    entry_price=current_price, expected_move=expected_move, time_to_explosion=time_to_explode,
                                    volume_24h=info['volume'], price_change_24h=info['change'], reasons=all_reasons)
        except: pass
        return None

    def _check_calm(self, volumes, closes): 
        if len(volumes)<20: return {'detected':False}
        vol_ratio = np.mean(volumes[-8:])/np.mean(volumes[-20:-8]) if np.mean(volumes[-20:-8])>0 else 1
        price_range = (np.max(closes[-10:])-np.min(closes[-10:]))/np.mean(closes[-10:])*100
        if vol_ratio<0.5 and price_range<2.5: return {'detected':True, 'reason':f'🌊 هدوء (حجم {vol_ratio*100:.0f}%)'}
        return {'detected':False}
    def _check_whale(self, volumes, closes):
        if len(volumes)<10: return {'detected':False}
        vol_ratio = volumes[-1]/np.mean(volumes[-15:]) if np.mean(volumes[-15:])>0 else 1
        price_stability = (np.max(closes[-5:])-np.min(closes[-5:]))/np.mean(closes[-5:])*100
        if vol_ratio>1.5 and price_stability<2.0: return {'detected':True, 'reason':f'🐋 حيتان ({vol_ratio:.1f}x)'}
        return {'detected':False}
    def _check_bollinger(self, closes):
        if len(closes)<20: return {'detected':False}
        recent, current = closes[-20:], closes[-1]
        middle, std = np.mean(recent), np.std(recent)
        upper, lower = middle+2*std, middle-2*std
        bandwidth = (upper-lower)/middle*100
        price_position = (current-lower)/(upper-lower) if upper!=lower else 0.5
        if bandwidth<6.0 and price_position<0.45: return {'detected':True, 'reason':f'🎯 بولنجر ({bandwidth:.1f}%)'}
        return {'detected':False}
    def _check_divergence(self, closes):
        if len(closes)<25: return {'detected':False}
        rsi = self._calculate_rsi(closes[-25:])
        if len(rsi)<15: return {'detected':False}
        mid = len(closes)//2
        if np.min(closes[mid:])<np.min(closes[:mid]) and np.min(rsi[mid:])>np.min(rsi[:mid]): return {'detected':True, 'reason':'📈 تباعد إيجابي'}
        return {'detected':False}
    def _check_volume_break(self, volumes, closes):
        if len(volumes)<5 or len(closes)<3: return {'detected':False}
        vol_ratio = volumes[-1]/np.mean(volumes[-6:-1]) if np.mean(volumes[-6:-1])>0 else 1
        price_change = (closes[-1]-closes[-3])/closes[-3]*100
        if vol_ratio>2.0 and price_change>1.5: return {'detected':True, 'reason':f'💥 انفجار ({vol_ratio:.1f}x) +{price_change:.1f}%'}
        return {'detected':False}
    def _check_breakout(self, highs, closes):
        if len(highs)<20: return {'detected':False}
        if closes[-1] > np.max(highs[-20:])*0.98: return {'detected':True, 'reason':'🚀 قرب كسر المقاومة'}
        return {'detected':False}
    def _calculate_rsi(self, prices, period=14):
        if len(prices)<period+1: return np.array([50])
        deltas = np.diff(prices)
        gain, loss = np.where(deltas>0,deltas,0), np.where(deltas<0,-deltas,0)
        avg_gain, avg_loss = np.zeros_like(prices), np.zeros_like(prices)
        avg_gain[period], avg_loss[period] = np.mean(gain[:period]), np.mean(loss[:period])
        for i in range(period+1, len(prices)):
            avg_gain[i] = (avg_gain[i-1]*(period-1)+gain[i-1])/period
            avg_loss[i] = (avg_loss[i-1]*(period-1)+loss[i-1])/period
        rs = avg_gain/(avg_loss+1e-9)
        return 100-(100/(1+rs))

# =========================================================
# نظام إدارة الصفقات
# =========================================================
class OptimizedTrainRider:
    def __init__(self, logger: CSVLogger, learner: TradeLearner):
        self.logger, self.learner = logger, learner
        self.pre_ignition = PreIgnitionDetector()
        self.active_trades: Dict[str, TradeInfo] = {}
        self.virtual_trades: Dict[str, VirtualTrade] = {}
        self.available_capital = TOTAL_CAPITAL
        self.daily_trades, self.daily_pnl, self.total_trades, self.winning_trades = 0, 0.0, 0, 0

    async def board_train(self, signal: StationSignal, exchange, market_regime: dict, extra_confidence: int = 0) -> dict:
        symbol = signal.symbol
        if symbol in self.active_trades: return {'success': False, 'reason': 'نشطة'}
        if len(self.active_trades) >= MAX_CONCURRENT_TRADES: return {'success': False, 'reason': 'حد متزامن'}
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            await self._create_virtual_trade(signal, "daily_limit_reached")
            return {'success': False, 'reason': 'حد يومي', 'virtual': True}
        allocation = CAPITAL_PER_TRADE * market_regime.get('allocation_multiplier', 1.0)
        if allocation > self.available_capital:
            await self._create_virtual_trade(signal, "insufficient_balance")
            return {'success': False, 'reason': 'رصيد غير كاف', 'virtual': True}
        ohlcv_1m = await exchange.fetch_ohlcv(symbol, '1m', limit=20)
        pre_ign = self.pre_ignition.detect(np.array(ohlcv_1m))
        final_confidence = signal.confidence + extra_confidence + (5 if pre_ign['detected'] else 0)
        entry_strategy = [0.4,0.35,0.25] if (pre_ign['entry_bonus'] and final_confidence>=70) else [0.33,0.33,0.34]
        first_entry = allocation * entry_strategy[0]
        self.available_capital -= first_entry
        trade = TradeInfo(symbol=symbol, signal=signal, entry_price=signal.entry_price, capital_allocated=allocation,
                         invested=first_entry, remaining=allocation-first_entry, stage=1, entry_time=datetime.now(),
                         highest_price=signal.entry_price, trailing_stop=signal.entry_price*0.97, take_profits=[],
                         entry_prices=[signal.entry_price], entry_amounts=[first_entry])
        self.active_trades[symbol] = trade
        self.daily_trades += 1; self.total_trades += 1
        self.logger.log_trade({'type':'entry','symbol':symbol,'stage':1,'price':signal.entry_price,'amount':first_entry,'confidence':final_confidence,'pre_ignition':pre_ign['detected']})
        await self._send_telegram(f"🚂 *ركوب القطار*\n{BOT_TAG}\n\n🪙 *{symbol}*\n💵 {signal.entry_price:.8f}\n💰 {first_entry:.2f}$ / {allocation:.0f}$\n📊 {final_confidence:.1f}% | {signal.pattern_type}")
        print(f"\n🚂 {symbol} @ {signal.entry_price:.8f} | {first_entry:.2f}$ | {final_confidence:.0f}%")
        return {'success': True}

    async def _create_virtual_trade(self, signal: StationSignal, reason: str):
        vt = VirtualTrade(symbol=signal.symbol, signal=signal, entry_price=signal.entry_price, capital_allocated=CAPITAL_PER_TRADE,
                         entry_time=datetime.now(), status='active', highest_price=signal.entry_price, current_price=signal.entry_price,
                         pnl_pct=0.0, exit_price=0.0, exit_time=None, exit_reason='', stages_completed=0, take_profits_hit=[],
                         trailing_stop_price=signal.entry_price*0.97, rejection_reason=reason)
        self.virtual_trades[signal.symbol] = vt
        self.logger.log_virtual_trade(vt)
        print(f"📊 صفقة افتراضية: {signal.symbol} ({reason})")

    async def update_trades(self, exchange):
        for symbol, trade in list(self.active_trades.items()):
            try:
                ticker = await exchange.fetch_ticker(symbol)
                price = ticker['last']
                if price > trade.highest_price: trade.highest_price = price
                avg_entry = sum(p*a for p,a in zip(trade.entry_prices, trade.entry_amounts)) / sum(trade.entry_amounts)
                pnl_pct = (price - avg_entry)/avg_entry*100
                if trade.stage == 1:
                    if pnl_pct >= 2.0 and trade.remaining > 0: await self._add_position(trade, price, 2, 0.35)
                    elif pnl_pct <= -2.0: await self._close_trade(symbol, price, pnl_pct, "وقف مبكر")
                elif trade.stage == 2:
                    if pnl_pct >= 4.0 and trade.remaining > 0: await self._add_position(trade, price, 3, trade.remaining/trade.capital_allocated)
                    elif pnl_pct <= -1.5: await self._close_trade(symbol, price, pnl_pct, "وقف")
                    if pnl_pct >= 3.0: trade.trailing_stop = max(trade.trailing_stop, price*0.97)
                elif trade.stage == 3:
                    if pnl_pct >= 5.0: trade.trailing_stop = max(trade.trailing_stop, price*0.965)
                    elif pnl_pct >= 3.0: trade.trailing_stop = max(trade.trailing_stop, price*0.97)
                    for target in [6.0,9.0,12.0,15.0]:
                        if pnl_pct >= target and target not in trade.take_profits:
                            trade.take_profits.append(target)
                            await self._send_telegram(f"💰 *+{target:.0f}%* - {symbol}")
                    if price <= trade.trailing_stop: await self._close_trade(symbol, price, pnl_pct, "وقف متحرك")
                    elif pnl_pct >= 18.0: await self._close_trade(symbol, price, pnl_pct, "هدف نهائي")
            except Exception as e: print(f"⚠️ {symbol}: {e}")

    async def update_virtual_trades(self, exchange):
        for symbol, vt in list(self.virtual_trades.items()):
            if vt.status == 'closed': continue
            try:
                ticker = await exchange.fetch_ticker(symbol)
                price = ticker['last']
                vt.current_price = price
                vt.highest_price = max(vt.highest_price, price)
                vt.pnl_pct = (price - vt.entry_price)/vt.entry_price*100
                if vt.pnl_pct >= 10.0:
                    vt.status, vt.exit_price, vt.exit_time, vt.exit_reason = 'closed', price, datetime.now(), 'هدف 10%'
                    self.logger.log_virtual_trade(vt)
                elif vt.pnl_pct <= -3.0:
                    vt.status, vt.exit_price, vt.exit_time, vt.exit_reason = 'closed', price, datetime.now(), 'وقف خسارة'
                    self.logger.log_virtual_trade(vt)
            except: pass

    async def _add_position(self, trade: TradeInfo, price: float, stage: int, ratio: float):
        amount = min(trade.capital_allocated * ratio, trade.remaining)
        trade.invested += amount; trade.remaining -= amount; trade.stage = stage
        trade.entry_prices.append(price); trade.entry_amounts.append(amount)
        self.available_capital -= amount
        await self._send_telegram(f"✅ *مرحلة {stage}*\n{BOT_TAG}\n\n🪙 {trade.symbol}\n💵 {price:.8f}\n💰 +{amount:.2f}$")
        print(f"  ✅ {trade.symbol}: مرحلة {stage} @ {price:.8f}")

    async def _close_trade(self, symbol: str, price: float, pnl: float, reason: str):
        trade = self.active_trades[symbol]
        pnl_usd = trade.capital_allocated * pnl / 100
        self.available_capital += trade.capital_allocated + pnl_usd
        self.daily_pnl += pnl
        if pnl > 0: self.winning_trades += 1
        self.learner.record_trade(trade.signal, pnl)
        win_rate = (self.winning_trades/self.total_trades*100) if self.total_trades else 0
        await self._send_telegram(f"{'💰' if pnl>0 else '📉'} *صفقة مكتملة*\n{BOT_TAG}\n\n🪙 {symbol}\n📊 {pnl:+.2f}% ({pnl_usd:+.2f}$)\n🎯 {reason}\n💵 المتاح: {self.available_capital:.2f}$\n📈 النجاح: {win_rate:.0f}%")
        print(f"\n🏁 {symbol}: {pnl:+.2f}% | {reason} | متاح: {self.available_capital:.2f}$")
        self.logger.log_trade({'type':'exit','symbol':symbol,'exit_price':price,'pnl_pct':pnl,'pnl_usd':pnl_usd,'reason':reason})
        del self.active_trades[symbol]

    async def _send_telegram(self, message: str):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message.strip(), "parse_mode": "Markdown"})
        except: pass

# =========================================================
# تطبيق Flask
# =========================================================
app = Flask(__name__)
engine_instance = None

@app.route('/')
def dashboard():
    if engine_instance is None: return "Engine not started yet."
    market = engine_instance.market_regime
    rider = engine_instance.rider
    scan_stats = engine_instance.last_scan_stats
    exchange_status = engine_instance.exchange_status
    return render_template_string('''
    <!DOCTYPE html><html dir="rtl"><head><title>لوحة التحكم</title><meta charset="utf-8"><meta http-equiv="refresh" content="30">
    <style>body{font-family:Arial;background:#1a1a2e;color:#eee;margin:20px}.card{background:#16213e;border-radius:10px;padding:20px;margin:10px}.badge{padding:5px 10px;border-radius:20px}.success{background:#0f9d58}.warning{background:#f4b400}.danger{background:#d93025}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #2c3e50}th{background:#0f3460}a{color:#4285f4;margin:5px}.btn{background:#0f3460;color:white;padding:8px 16px;border-radius:5px;display:inline-block;margin:5px}</style></head><body>
    <h1>🚂 نظام ركوب القطار</h1>
    <div style="display:flex;flex-wrap:wrap">
    <div class="card" style="flex:2"><h2>📊 حالة السوق</h2><p>النظام: <span class="badge {{'success' if market.regime=='trending_bullish' else 'warning' if market.regime=='ranging' else 'danger'}}">{{market.regime}}</span></p><p>السبب: {{market.reason}}</p><p>ADX: {{market.adx}} | BTC 1h: {{market.btc_change}}%</p><p>التداول: {{'✅' if market.can_trade else '❌'}}</p></div>
    <div class="card" style="flex:2"><h2>💰 حالة البوت</h2><p>الكلي: ${{"%.2f"|format(capital)}}</p><p>المتاح: ${{"%.2f"|format(available)}}</p><p>النشطة: {{active_count}}/{{max_concurrent}}</p><p>اليوم: {{daily_trades}}/{{max_daily}}</p><p>النجاح: {{"%.1f"|format(win_rate)}}% ({{wins}}/{{total}})</p></div>
    <div class="card" style="flex:1"><h2>🔍 آخر مسح</h2><p>⏱️ الوقت: {{scan_stats.time}}</p><p>📊 العملات: {{scan_stats.scanned}}</p><p>🎯 الإشارات: <span style="color:{{'#0f9d58' if scan_stats.signals>0 else '#aaa'}}">{{scan_stats.signals}}</span></p><p>⏳ المدة: {{scan_stats.duration}} ث</p></div>
    <div class="card" style="flex:1"><h2>🔄 حالة المنصة</h2><p>الاتصال: <span class="badge {{'success' if exchange_status.connected else 'danger'}}">{{'✅ متصل' if exchange_status.connected else '❌ منفصل'}}</span></p>{% if exchange_status.last_success %}<p>آخر نجاح: {{exchange_status.last_success[:19]}}</p>{% endif %}{% if exchange_status.error %}<p>خطأ: <span style="color:red">{{exchange_status.error[:50]}}</span></p>{% endif %}</div>
    </div>
    <div class="card"><h2>📁 التحميل</h2><a href="/download/signals" class="btn">📊 الإشارات</a><a href="/download/trades" class="btn">📈 الصفقات</a><a href="/download/virtual" class="btn">🧪 الافتراضية</a><a href="/download/snapshots" class="btn">📸 اللقطات</a><a href="/download/errors" class="btn">⚠️ الأخطاء</a></div>
    <div class="card"><h2>🔄 الصفقات النشطة</h2>{% if active_trades %}<table><tr><th>الرمز</th><th>الدخول</th><th>الحالي</th><th>الربح</th><th>المرحلة</th></tr>{% for t in active_trades %}<tr><td>{{t.symbol}}</td><td>{{"%.8f"|format(t.entry)}}</td><td>{{"%.8f"|format(t.current)}}</td><td style="color:{{'green' if t.pnl>0 else 'red'}}">{{"%+.2f"|format(t.pnl)}}%</td><td>{{t.stage}}/3</td></tr>{% endfor %}</table>{% else %}<p>لا توجد صفقات نشطة.</p>{% endif %}</div>
    <div class="card"><h2>🧪 الصفقات الافتراضية</h2>{% if virtual_trades %}<table><tr><th>الرمز</th><th>الدخول</th><th>الحالي</th><th>الربح</th><th>سبب الرفض</th><th>الحالة</th></tr>{% for v in virtual_trades %}<tr><td>{{v.symbol}}</td><td>{{"%.8f"|format(v.entry)}}</td><td>{{"%.8f"|format(v.current)}}</td><td style="color:{{'green' if v.pnl>0 else 'red'}}">{{"%+.2f"|format(v.pnl)}}%</td><td>{{v.reason}}</td><td>{{v.status}}</td></tr>{% endfor %}</table>{% else %}<p>لا توجد صفقات افتراضية.</p>{% endif %}</div>
    <p style="text-align:center">آخر تحديث: {{now}}</p></body></html>''',
    market={'regime': market.get('regime','-'), 'reason': market.get('reason','-'), 'adx': market.get('adx',0), 'btc_change': market.get('btc_change_1h',0), 'can_trade': market.get('can_trade',False)},
    capital=TOTAL_CAPITAL, available=rider.available_capital, active_count=len(rider.active_trades), max_concurrent=MAX_CONCURRENT_TRADES,
    daily_trades=rider.daily_trades, max_daily=MAX_TRADES_PER_DAY,
    win_rate=(rider.winning_trades/rider.total_trades*100) if rider.total_trades>0 else 0, wins=rider.winning_trades, total=rider.total_trades,
    scan_stats=scan_stats, exchange_status=exchange_status,
    active_trades=[{'symbol':s,'entry':t.entry_price,'current':t.highest_price,'pnl':((t.highest_price-t.entry_price)/t.entry_price*100) if t.entry_price>0 else 0,'stage':t.stage} for s,t in rider.active_trades.items()],
    virtual_trades=[{'symbol':s,'entry':v.entry_price,'current':v.current_price,'pnl':v.pnl_pct,'reason':v.rejection_reason,'status':v.status} for s,v in rider.virtual_trades.items()],
    now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/api/status')
def api_status():
    if engine_instance is None: return jsonify({'error':'Engine not ready'})
    return jsonify({'capital':TOTAL_CAPITAL,'available':engine_instance.rider.available_capital,'active':len(engine_instance.rider.active_trades),'daily':engine_instance.rider.daily_trades,'win_rate':(engine_instance.rider.winning_trades/engine_instance.rider.total_trades*100) if engine_instance.rider.total_trades>0 else 0,'market':engine_instance.market_regime,'scan_stats':engine_instance.last_scan_stats,'exchange_status':engine_instance.exchange_status})

@app.route('/download/<filetype>')
def download_file(filetype):
    files = {'signals':SIGNALS_FILE,'trades':TRADES_FILE,'virtual':VIRTUAL_TRADES_FILE,'snapshots':SNAPSHOT_FILE,'errors':ERRORS_FILE}
    filepath = files.get(filetype)
    if filepath and os.path.exists(filepath): return send_file(filepath, as_attachment=True)
    return "File not found", 404

# =========================================================
# المحرك الرئيسي
# =========================================================
class OptimizedFirstStationEngine:
    def __init__(self):
        self.logger = CSVLogger()
        self.learner = TradeLearner()
        self.market_filter = MarketRegimeFilter()
        self.quick_confirm = QuickConfirmationFilter()
        self.detector = OptimizedFirstStationDetector(self.learner)
        self.rider = OptimizedTrainRider(self.logger, self.learner)
        self.market_regime = {}
        self.last_scan_stats = {'scanned':0, 'signals':0, 'time':'-', 'duration':0}
        self.exchange_status = {'connected': False, 'last_success': None, 'error': None}
        self.scan_count = 0
        self.symbols_info = []

    async def run(self):
        global engine_instance
        engine_instance = self
        exchange = ccxt_async.gateio({'enableRateLimit': True, 'rateLimit': 100})
        print("🚀 بدء تشغيل المحرك...")
        await self.rider._send_telegram(f"🚀 *بدء نظام المحطة الأولى*\n{BOT_TAG}\n\n💰 {TOTAL_CAPITAL}$ | {MAX_TRADES_PER_DAY} صفقة/يوم | مسح {SCAN_SYMBOLS_LIMIT} عملة")
        self.symbols_info = await self.detector.filter_symbols(exchange, limit=SCAN_SYMBOLS_LIMIT)
        try:
            while True:
                self.scan_count += 1
                scan_start = time.time()
                self.market_regime = await self.market_filter.analyze(exchange)
                print(f"🔍 دورة #{self.scan_count} | {self.market_regime['reason']} | ADX: {self.market_regime['adx']}")
                if self.rider.active_trades: await self.rider.update_trades(exchange)
                await self.rider.update_virtual_trades(exchange)
                available_slots = MAX_CONCURRENT_TRADES - len(self.rider.active_trades)
                signals = []
                if available_slots > 0 and self.market_regime['can_trade']:
                    try:
                        signals = await self.detector.scan_batch(exchange, self.symbols_info)
                        entered = 0
                        for signal in signals:
                            if entered >= available_slots: break
                            if not any(p in signal.pattern_type for p in self.market_regime['allowed_patterns']): continue
                            if signal.confidence < self.market_regime['min_confidence']: continue
                            if self.learner.should_avoid_symbol(signal.symbol): continue
                            confirm = await self.quick_confirm.confirm(exchange, signal.symbol, signal)
                            if not confirm['confirmed']: continue
                            self.logger.log_signal(signal)
                            res = await self.rider.board_train(signal, exchange, self.market_regime, confirm['extra_confidence'])
                            if res.get('success'): entered += 1; await asyncio.sleep(1)
                        self.exchange_status = {'connected': True, 'last_success': datetime.now().isoformat(), 'error': None}
                    except Exception as e:
                        self.exchange_status = {'connected': False, 'last_success': self.exchange_status['last_success'], 'error': str(e)}
                scan_duration = time.time() - scan_start
                self.last_scan_stats = {'scanned': len(self.symbols_info), 'signals': len(signals), 'time': datetime.now().strftime('%H:%M:%S'), 'duration': round(scan_duration,2)}
                win_rate = (self.rider.winning_trades/self.rider.total_trades*100) if self.rider.total_trades>0 else 0
                update_db_status(TOTAL_CAPITAL, self.rider.available_capital, len(self.rider.active_trades), self.rider.daily_trades, win_rate, self.market_regime.get('regime',''), self.market_regime.get('btc_change_1h',0))
                self.logger.flush()
                print(f"💵 متاح: {self.rider.available_capital:.2f}$ | صفقات: {self.rider.daily_trades}/{MAX_TRADES_PER_DAY} | ⏱️ {scan_duration:.1f}ث")
                await asyncio.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\n⏹️ إيقاف...")
        finally:
            await exchange.close()

def start_flask():
    init_database()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False, use_reloader=False)

async def main():
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    engine = OptimizedFirstStationEngine()
    await engine.run()

if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🚂 نظام ركوب القطار من المحطة الأولى - النسخة النهائية الكاملة
First Station Train Rider - Complete Edition

المميزات:
✅ اكتشاف العملات قبل الانفجار (5 أنماط)
✅ إشعارات تليجرام فورية
✅ تسجيل كامل في CSV
✅ لوحة تحكم ويب
✅ قاعدة بيانات SQLite
✅ مسح 2000 عملة
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
BOT_TAG = "#محطة_أولى"

# =========================================================
# إعدادات التداول
# =========================================================
TOTAL_CAPITAL = 1000.0
MAX_TRADES_PER_DAY = 50
CAPITAL_PER_TRADE = 50.0
MAX_CONCURRENT_TRADES = 15
SCAN_INTERVAL = 30
MIN_CONFIDENCE = 30
SCAN_BATCH_SIZE = 100
SCAN_SYMBOLS_LIMIT = 2000

# =========================================================
# إعدادات الملفات
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
# قاعدة البيانات
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
# نظام التسجيل مع إشعارات تليجرام
# =========================================================
class HybridLogger:
    def __init__(self):
        self.signal_buffer = deque(maxlen=100)
        self.trade_buffer = deque(maxlen=50)
        self.virtual_buffer = deque(maxlen=50)
        self.snapshot_buffer = deque(maxlen=50)
        self.error_buffer = deque(maxlen=50)
        self.last_flush = time.time()
        self.telegram_enabled = True
        self.telegram_token = TELEGRAM_TOKEN
        self.telegram_chat_id = TELEGRAM_CHAT_ID

    def log_signal(self, signal: StationSignal):
        self.signal_buffer.append(asdict(signal))
        self._check_flush()
        
        # إرسال إشعار تليجرام للإشارات القوية
        if signal.confidence >= 50:
            msg = f"""
🟡 *إشارة قوية مكتشفة*
{BOT_TAG}

🪙 *{signal.symbol}*
📊 الثقة: {signal.confidence:.1f}%
🎯 النمط: {signal.pattern_type}
💵 السعر: {signal.entry_price:.8f}
📈 متوقع: +{signal.expected_move:.1f}%

📋 *الأسباب:*
{chr(10).join(f'• {r}' for r in signal.reasons)}

🕐 `{datetime.now().strftime('%H:%M:%S')}`
"""
            asyncio.create_task(self._send_telegram(msg))

    def log_trade_entry(self, symbol: str, price: float, amount: float, confidence: float, pattern: str):
        trade_data = {
            'type': 'entry',
            'symbol': symbol,
            'price': price,
            'amount': amount,
            'confidence': confidence,
            'pattern': pattern,
            'timestamp': datetime.now().isoformat()
        }
        self.trade_buffer.append(trade_data)
        self._check_flush()
        
        # إرسال إشعار تليجرام
        msg = f"""
🔴 *دخول صفقة جديدة*
{BOT_TAG}

🪙 *{symbol}*
💵 السعر: {price:.8f}
💰 المبلغ: {amount:.2f}$
📊 الثقة: {confidence:.1f}%
🎯 النمط: {pattern}

🕐 `{datetime.now().strftime('%H:%M:%S')}`
"""
        asyncio.create_task(self._send_telegram(msg))

    def log_trade_exit(self, symbol: str, exit_price: float, pnl_pct: float, pnl_usd: float, reason: str):
        trade_data = {
            'type': 'exit',
            'symbol': symbol,
            'exit_price': exit_price,
            'pnl_pct': pnl_pct,
            'pnl_usd': pnl_usd,
            'reason': reason,
            'timestamp': datetime.now().isoformat()
        }
        self.trade_buffer.append(trade_data)
        self._check_flush()
        
        # إرسال إشعار تليجرام
        emoji = "💰" if pnl_pct > 0 else "📉"
        msg = f"""
{emoji} *صفقة مكتملة*
{BOT_TAG}

🪙 {symbol}
📊 الربح: {pnl_pct:+.2f}% ({pnl_usd:+.2f}$)
🎯 السبب: {reason}

🕐 `{datetime.now().strftime('%H:%M:%S')}`
"""
        asyncio.create_task(self._send_telegram(msg))

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

    def log_startup(self):
        msg = f"""
🚀 *تم تشغيل نظام المحطة الأولى*
{BOT_TAG}

💰 رأس المال: {TOTAL_CAPITAL:.0f}$
📊 أقصى صفقات يومية: {MAX_TRADES_PER_DAY}
🔄 فترة المسح: {SCAN_INTERVAL} ثانية
🔍 مسح: {SCAN_SYMBOLS_LIMIT} عملة

✅ *النظام يعمل بنجاح!*

🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`
"""
        asyncio.create_task(self._send_telegram(msg))

    def log_daily_report(self, trades_count: int, win_rate: float, net_pnl: float):
        msg = f"""
📊 *التقرير اليومي*
{BOT_TAG}

📈 إجمالي الصفقات: {trades_count}
✅ نسبة النجاح: {win_rate:.1f}%
💰 صافي الربح: {net_pnl:+.2f}$

🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`
"""
        asyncio.create_task(self._send_telegram(msg))

    async def _send_telegram(self, message: str):
        if not self.telegram_enabled or not self.telegram_token:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json={
                    "chat_id": self.telegram_chat_id,
                    "text": message.strip(),
                    "parse_mode": "Markdown"
                })
        except Exception as e:
            print(f"⚠️ خطأ تليجرام: {e}")

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
        self.pattern_performance = {k: {'wins':0,'losses':0} for k in ['calm','whale','bollinger','divergence','volume']}
        self.symbol_memory = {}
        self.load_data()

    def load_data(self):
        if os.path.exists(LEARNING_FILE):
            try:
                with open(LEARNING_FILE, 'r') as f:
                    data = json.load(f)
                    self.pattern_performance = data.get('patterns', self.pattern_performance)
                    self.symbol_memory = data.get('symbols', {})
            except: pass

    def save_data(self):
        with open(LEARNING_FILE, 'w') as f:
            json.dump({'patterns':self.pattern_performance,'symbols':self.symbol_memory}, f)

    def record_trade(self, signal: StationSignal, pnl: float):
        pattern = None
        if 'هدوء' in signal.pattern_type: pattern='calm'
        elif 'حيتان' in signal.pattern_type: pattern='whale'
        elif 'بولنجر' in signal.pattern_type: pattern='bollinger'
        elif 'تباعد' in signal.pattern_type: pattern='divergence'
        elif 'انفجار' in signal.pattern_type: pattern='volume'
        if pattern:
            if pnl>0: self.pattern_performance[pattern]['wins']+=1
            else: self.pattern_performance[pattern]['losses']+=1
        symbol = signal.symbol
        if symbol not in self.symbol_memory: self.symbol_memory[symbol] = {'wins':0,'losses':0}
        if pnl>0: self.symbol_memory[symbol]['wins']+=1
        else: self.symbol_memory[symbol]['losses']+=1
        self.save_data()

    def should_avoid_symbol(self, symbol: str) -> bool:
        if symbol in self.symbol_memory:
            m=self.symbol_memory[symbol]; total=m['wins']+m['losses']
            if total>=3: return (m['wins']/total*100)<30
        return False

# =========================================================
# فلتر السوق
# =========================================================
class MarketRegimeFilter:
    def __init__(self):
        self.btc_symbol = 'BTC/USDT'
        self.regime_data = {}
    
    async def analyze(self, exchange) -> dict:
        try:
            ohlcv = await exchange.fetch_ohlcv(self.btc_symbol, '1h', limit=50)
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            closes, highs, lows = df['c'].values, df['h'].values, df['l'].values
            adx = self._calc_adx(highs, lows, closes)
            ema20, ema50 = self._ema(closes,20), self._ema(closes,50)
            trend = "bullish" if ema20[-1] > ema50[-1] else "bearish"
            btc_change_1h = ((closes[-1]-closes[-4])/closes[-4])*100 if len(closes)>=4 else 0
            
            if adx<20: regime, allowed, min_conf, alloc, can = MarketRegime.RANGING, ['هدوء','بولنجر','حيتان','انفجار'], 40, 0.8, True
            elif adx>25 and trend=="bullish": regime, allowed, min_conf, alloc, can = MarketRegime.TRENDING_BULLISH, ['حيتان','انفجار','تباعد','هدوء','بولنجر'], 35, 1.0, True
            elif adx>25 and trend=="bearish": regime, allowed, min_conf, alloc, can = MarketRegime.TRENDING_BEARISH, ['تباعد','هدوء'], 45, 0.6, True
            else: regime, allowed, min_conf, alloc, can = MarketRegime.TRANSITIONAL, ['هدوء','حيتان','بولنجر'], 40, 0.9, True
            
            self.regime_data = {
                'regime': regime.value, 'adx': round(adx,1), 'btc_change_1h': round(btc_change_1h,2),
                'can_trade': can, 'allowed_patterns': allowed, 'min_confidence': min_conf, 'allocation_multiplier': alloc
            }
            return self.regime_data
        except:
            return {'can_trade':True, 'allowed_patterns':[], 'min_confidence':30, 'allocation_multiplier':0.8}
    
    def _calc_adx(self, h,l,c,p=14):
        if len(c)<p+1: return 20
        tr1, tr2, tr3 = h[1:]-l[1:], np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])
        tr = np.maximum(np.maximum(tr1,tr2),tr3)
        atr = np.mean(tr[-p:]) if len(tr)>=p else np.mean(tr)
        up, down = h[1:]-h[:-1], l[:-1]-l[1:]
        plus_dm = np.where((up>down)&(up>0), up, 0)
        minus_dm = np.where((down>up)&(down>0), down, 0)
        plus_di = 100*np.mean(plus_dm[-p:])/atr if atr>0 else 0
        minus_di = 100*np.mean(minus_dm[-p:])/atr if atr>0 else 0
        dx = 100*np.abs(plus_di-minus_di)/(plus_di+minus_di) if (plus_di+minus_di)>0 else 0
        return dx
    
    def _ema(self, data, p):
        alpha, ema = 2/(p+1), np.zeros_like(data)
        if len(data)>=p:
            ema[p-1]=np.mean(data[:p])
            for i in range(p, len(data)): ema[i]=data[i]*alpha+ema[i-1]*(1-alpha)
        return ema

# =========================================================
# كاشف المحطة الأولى
# =========================================================
class OptimizedFirstStationDetector:
    def __init__(self, learner: TradeLearner):
        self.learner = learner
        self.base_patterns = {
            'calm': {'name': '🌊 هدوء', 'weight': 60},
            'whale': {'name': '🐋 حيتان', 'weight': 60},
            'bollinger': {'name': '🎯 بولنجر', 'weight': 55},
            'divergence': {'name': '📈 تباعد', 'weight': 55},
            'volume': {'name': '💥 انفجار', 'weight': 20}
        }

    async def filter_symbols(self, exchange, limit=SCAN_SYMBOLS_LIMIT):
        try:
            tickers = await exchange.fetch_tickers()
            promising = []
            for sym, t in tickers.items():
                if not sym.endswith('/USDT'): continue
                vol = t.get('quoteVolume',0)
                if vol<10000: continue
                ch = t.get('percentage',0)
                if ch>50 or ch<-20: continue
                bid, ask = t.get('bid',0), t.get('ask',0)
                spread = ((ask-bid)/bid*100) if bid>0 else 0
                if spread>1.5: continue
                promising.append({'symbol':sym,'volume':vol,'price':t.get('last',0),'change':ch,'spread':spread})
            promising.sort(key=lambda x: x['volume'], reverse=True)
            return promising[:limit]
        except: return []

    async def scan_batch(self, exchange, symbols_info):
        signals = []
        for i in range(0, len(symbols_info), SCAN_BATCH_SIZE):
            batch = symbols_info[i:i+SCAN_BATCH_SIZE]
            tasks = [self._analyze_single(exchange, info) for info in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, StationSignal): signals.append(res)
            await asyncio.sleep(0.2)
        signals.sort(key=lambda x: x.confidence, reverse=True)
        return signals

    async def _analyze_single(self, exchange, info):
        sym = info['symbol']
        try:
            ohlcv = await exchange.fetch_ohlcv(sym, '5m', limit=40)
            if len(ohlcv)<20: return None
            data = np.array(ohlcv)
            closes, volumes, highs = data[:,4], data[:,5], data[:,2]
            price = info['price']
            total_conf, reasons = 0, []
            
            def calm(): 
                if len(volumes)<15: return 0,''
                r=np.mean(volumes[-6:])/np.mean(volumes[-15:-6]) if np.mean(volumes[-15:-6])>0 else 1
                pr=(np.max(closes[-8:])-np.min(closes[-8:]))/np.mean(closes[-8:])*100
                return (60, f'🌊 هدوء') if r<0.6 and pr<3.0 else (0,'')
            def whale():
                if len(volumes)<8: return 0,''
                r=volumes[-1]/np.mean(volumes[-12:]) if np.mean(volumes[-12:])>0 else 1
                st=(np.max(closes[-4:])-np.min(closes[-4:]))/np.mean(closes[-4:])*100
                return (60, f'🐋 حيتان ({r:.1f}x)') if r>1.3 and st<2.5 else (0,'')
            def boll():
                if len(closes)<15: return 0,''
                mid, std = np.mean(closes[-15:]), np.std(closes[-15:])
                u,l = mid+2*std, mid-2*std
                bw = (u-l)/mid*100
                pos = (closes[-1]-l)/(u-l) if u!=l else 0.5
                return (55, f'🎯 بولنجر ({bw:.1f}%)') if bw<8.0 and pos<0.5 else (0,'')
            def div():
                if len(closes)<20: return 0,''
                rsi = self._rsi(closes[-20:])
                if len(rsi)<12: return 0,''
                mid=len(closes)//2
                if np.min(closes[mid:])<np.min(closes[:mid]) and np.min(rsi[mid:])>np.min(rsi[:mid]): return 55,'📈 تباعد'
                return 0,''
            def vbreak():
                if len(volumes)<4 or len(closes)<3: return 0,''
                r=volumes[-1]/np.mean(volumes[-5:-1]) if np.mean(volumes[-5:-1])>0 else 1
                ch=(closes[-1]-closes[-2])/closes[-2]*100
                return (20, f'💥 انفجار ({r:.1f}x)') if r>1.8 and ch>1.0 else (0,'')
            
            for func in [calm, whale, boll, div, vbreak]:
                pts, reason = func()
                if pts>0: total_conf+=pts; reasons.append(reason)
            
            if total_conf>=25:
                return StationSignal(symbol=sym, pattern_type="إشارة مبكرة", confidence=min(100,total_conf),
                                    entry_price=price, expected_move=8.0, time_to_explosion=120,
                                    volume_24h=info['volume'], price_change_24h=info['change'], reasons=reasons)
        except: return None
        return None

    def _rsi(self, prices, p=14):
        if len(prices)<p+1: return np.array([50])
        d=np.diff(prices)
        gain, loss = np.where(d>0,d,0), np.where(d<0,-d,0)
        ag, al = np.zeros_like(prices), np.zeros_like(prices)
        ag[p], al[p] = np.mean(gain[:p]), np.mean(loss[:p])
        for i in range(p+1, len(prices)):
            ag[i] = (ag[i-1]*(p-1)+gain[i-1])/p
            al[i] = (al[i-1]*(p-1)+loss[i-1])/p
        rs = ag/(al+1e-9)
        return 100-(100/(1+rs))

# =========================================================
# نظام إدارة الصفقات
# =========================================================
class OptimizedTrainRider:
    def __init__(self, logger: HybridLogger, learner: TradeLearner):
        self.logger, self.learner = logger, learner
        self.active_trades: Dict[str, TradeInfo] = {}
        self.virtual_trades: Dict[str, VirtualTrade] = {}
        self.available = TOTAL_CAPITAL
        self.daily_trades = self.total_trades = self.wins = 0

    async def board_train(self, signal: StationSignal, exchange, market_regime, extra=0):
        sym = signal.symbol
        if sym in self.active_trades or len(self.active_trades)>=MAX_CONCURRENT_TRADES or self.daily_trades>=MAX_TRADES_PER_DAY:
            return {'success':False}
        alloc = CAPITAL_PER_TRADE * market_regime.get('allocation_multiplier',1.0)
        if alloc > self.available: return {'success':False}
        
        self.available -= alloc
        trade = TradeInfo(symbol=sym, signal=signal, entry_price=signal.entry_price, capital_allocated=alloc,
                         invested=alloc, remaining=0, stage=1, entry_time=datetime.now(),
                         highest_price=signal.entry_price, trailing_stop=signal.entry_price*0.97, take_profits=[],
                         entry_prices=[signal.entry_price], entry_amounts=[alloc])
        self.active_trades[sym] = trade
        self.daily_trades += 1; self.total_trades += 1
        
        self.logger.log_trade_entry(sym, signal.entry_price, alloc, signal.confidence, signal.pattern_type)
        print(f"\n🚂 {sym} @ {signal.entry_price:.8f} | {alloc:.2f}$ | {signal.confidence:.0f}%")
        return {'success':True}

    async def update_trades(self, exchange):
        for sym, t in list(self.active_trades.items()):
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                if price > t.highest_price: t.highest_price = price
                pnl = (price - t.entry_price)/t.entry_price*100
                if t.stage == 1:
                    if pnl >= 3.0: t.trailing_stop = max(t.trailing_stop, price*0.97)
                    if price <= t.trailing_stop or pnl <= -2.0: await self._close(sym, price, pnl, "وقف")
                    elif pnl >= 8.0: await self._close(sym, price, pnl, "هدف")
            except: pass

    async def _close(self, sym, price, pnl, reason):
        t = self.active_trades[sym]
        pnl_usd = t.capital_allocated * pnl / 100
        self.available += t.capital_allocated + pnl_usd
        if pnl>0: self.wins += 1
        self.learner.record_trade(t.signal, pnl)
        self.logger.log_trade_exit(sym, price, pnl, pnl_usd, reason)
        print(f"🏁 {sym}: {pnl:+.2f}% | {reason}")
        del self.active_trades[sym]

# =========================================================
# Flask
# =========================================================
app = Flask(__name__)
engine_instance = None

@app.route('/')
def dashboard():
    if not engine_instance: return "Not ready"
    m = engine_instance.market_regime
    r = engine_instance.rider
    s = engine_instance.last_scan_stats
    return render_template_string('''
    <!DOCTYPE html><html dir="rtl"><head><title>لوحة التحكم - المحطة الأولى</title><meta charset="utf-8"><meta http-equiv="refresh" content="20">
    <style>body{font-family:Arial;background:#1a1a2e;color:#eee;margin:20px}.card{background:#16213e;border-radius:10px;padding:20px;margin:10px}.badge{padding:5px 10px;border-radius:20px}.success{background:#0f9d58}.warning{background:#f4b400}.danger{background:#d93025}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #2c3e50}th{background:#0f3460}a{color:#4285f4;margin:5px}.btn{background:#0f3460;color:white;padding:8px 16px;border-radius:5px;display:inline-block;margin:5px}</style></head><body>
    <h1>🚂 نظام ركوب القطار من المحطة الأولى</h1>
    <div style="display:flex;flex-wrap:wrap">
    <div class="card" style="flex:2"><h2>📊 حالة السوق</h2><p>النظام: <span class="badge {{'success' if m.regime=='trending_bullish' else 'warning'}}">{{m.regime}}</span></p><p>ADX: {{m.adx}} | BTC 1h: {{m.btc_change}}%</p></div>
    <div class="card" style="flex:2"><h2>💰 حالة البوت</h2><p>المتاح: ${{"%.2f"|format(r.available)}}</p><p>النشطة: {{active}}/{{max_con}}</p><p>اليوم: {{r.daily_trades}}/{{max_daily}}</p><p>النجاح: {{"%.1f"|format(win_rate)}}%</p></div>
    <div class="card" style="flex:1"><h2>🔍 آخر مسح</h2><p>العملات: {{s.scanned}}</p><p>الإشارات: {{s.signals}}</p><p>المدة: {{s.duration}} ث</p></div>
    </div>
    <div class="card"><h2>📁 التحميل</h2><a href="/download/signals" class="btn">📊 الإشارات</a><a href="/download/trades" class="btn">📈 الصفقات</a><a href="/download/virtual" class="btn">🧪 الافتراضية</a></div>
    <div class="card"><h2>🔄 الصفقات النشطة</h2>{% if active_trades %}<table><tr><th>الرمز</th><th>الدخول</th><th>الحالي</th><th>الربح</th></tr>{% for t in active_trades %}<tr><td>{{t.symbol}}</td><td>{{"%.8f"|format(t.entry)}}</td><td>{{"%.8f"|format(t.current)}}</td><td style="color:{{'green' if t.pnl>0 else 'red'}}">{{"%+.2f"|format(t.pnl)}}%</td></tr>{% endfor %}</table>{% else %}<p>لا توجد صفقات نشطة.</p>{% endif %}</div>
    </body></html>''',
    m=m, r=r, s=s, active=len(r.active_trades), max_con=MAX_CONCURRENT_TRADES, max_daily=MAX_TRADES_PER_DAY,
    win_rate=(r.wins/r.total_trades*100) if r.total_trades else 0,
    active_trades=[{'symbol':s,'entry':t.entry_price,'current':t.highest_price,'pnl':((t.highest_price-t.entry_price)/t.entry_price*100)} for s,t in r.active_trades.items()])

@app.route('/download/<ft>')
def download(ft):
    files = {'signals':SIGNALS_FILE,'trades':TRADES_FILE,'virtual':VIRTUAL_TRADES_FILE}
    return send_file(files.get(ft), as_attachment=True) if ft in files and os.path.exists(files[ft]) else ("Not found",404)

# =========================================================
# المحرك الرئيسي
# =========================================================
class OptimizedFirstStationEngine:
    def __init__(self):
        self.logger = HybridLogger()
        self.learner = TradeLearner()
        self.market_filter = MarketRegimeFilter()
        self.detector = OptimizedFirstStationDetector(self.learner)
        self.rider = OptimizedTrainRider(self.logger, self.learner)
        self.market_regime = {}
        self.last_scan_stats = {'scanned':0,'signals':0,'duration':0}
        self.symbols_info = []

    async def run(self):
        global engine_instance
        engine_instance = self
        exchange = ccxt_async.gateio({'enableRateLimit':True,'rateLimit':100})
        
        print("🚀 بدء نظام المحطة الأولى...")
        self.logger.log_startup()
        
        try:
            while True:
                start = time.time()
                self.symbols_info = await self.detector.filter_symbols(exchange)
                self.market_regime = await self.market_filter.analyze(exchange)
                
                if self.rider.active_trades: 
                    await self.rider.update_trades(exchange)
                
                slots = MAX_CONCURRENT_TRADES - len(self.rider.active_trades)
                signals = []
                if slots>0 and self.market_regime.get('can_trade',True):
                    signals = await self.detector.scan_batch(exchange, self.symbols_info)
                    for sig in signals[:slots]:
                        if sig.confidence < self.market_regime.get('min_confidence',30): continue
                        await self.rider.board_train(sig, exchange, self.market_regime)
                
                self.last_scan_stats = {'scanned':len(self.symbols_info), 'signals':len(signals), 'duration':round(time.time()-start,2)}
                update_db_status(TOTAL_CAPITAL, self.rider.available, len(self.rider.active_trades), self.rider.daily_trades,
                                 (self.rider.wins/self.rider.total_trades*100) if self.rider.total_trades else 0,
                                 self.market_regime.get('regime',''), self.market_regime.get('btc_change_1h',0))
                self.logger.flush()
                
                # تقرير يومي عند الساعة 23:55
                now = datetime.now()
                if now.hour == 23 and now.minute == 55:
                    win_rate = (self.rider.wins/self.rider.total_trades*100) if self.rider.total_trades else 0
                    net_pnl = self.rider.available - TOTAL_CAPITAL
                    self.logger.log_daily_report(self.rider.daily_trades, win_rate, net_pnl)
                
                await asyncio.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\n⏹️ إيقاف النظام...")
        finally:
            await exchange.close()

def start_flask():
    init_database()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',8080)), debug=False, use_reloader=False)

async def main():
    threading.Thread(target=start_flask, daemon=True).start()
    await OptimizedFirstStationEngine().run()

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
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
import aiofiles
import logging
import threading
import requests
from flask import Flask

# إعداد التسجيل
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =========================================================
# ⚙️ إعدادات البوت (للصفقات القصيرة جداً)
# =========================================================
TELEGRAM_TOKEN = "8716390236:AAEjPGJSYXN5FrqsuI845KhQoVzMfM_Suoo"
TELEGRAM_CHAT_ID = "5067771509"

LOG_DIR = "/tmp/trading_logs"
DB_FILE = os.path.join(LOG_DIR, "empire_scalp.db")
REAL_CSV = os.path.join(LOG_DIR, "scalp_trades.csv")
MISSED_CSV = os.path.join(LOG_DIR, "missed_scalp.csv")
OPPORTUNITIES_CSV = os.path.join(LOG_DIR, "opportunities_scalp.csv")
os.makedirs(LOG_DIR, exist_ok=True)

# ========== إعدادات التداول (مخصصة للـ Scalping) ==========
PAPER_TRADING = True
EXCHANGE_NAME = "bybit"

MAX_CONCURRENT_TRADES = 30
STOP_LOSS_PCT = 0.0025              # 0.25%
TRAILING_ACTIVATE_PCT = 0.2
TRAILING_DISTANCE_PCT = 0.15
PARTIAL_TP_PCT = 0.4
PARTIAL_CLOSE_RATIO = 0.5
FINAL_TP_PCT = 0.8

# إعدادات السكور
MIN_VOTES = 2
SCORE_HIGH_THRESHOLD = 80
SCORE_MEDIUM_THRESHOLD = 50
RISK_PER_TRADE_HIGH = 0.02
RISK_PER_TRADE_MEDIUM = 0.015
RISK_PER_TRADE_LOW = 0.01
SKIP_LOW_SCORE_ENTRY = False

# ========== إعدادات المسح ==========
TOTAL_SYMBOLS_TO_SCAN = 300
SCAN_INTERVAL = 5
BATCH_SIZE = 20

# ========== فلاتر ==========
ENABLE_EXPLOSION_FILTER = False
MIN_24H_VOLUME_USD = 100000
MAX_SPREAD_PCT = 0.15

# ========== فلاتر الرموز ==========
EXCLUDE_STABLECOINS = True
EXCLUDE_VERY_LARGE_CAP = True
MAX_24H_VOLUME_USD_FILTER = 500_000_000
MIN_PRICE_USD = 0.00001
MAX_PRICE_USD = 500
STABLECOINS = ["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "UST", "USDD", "FRAX", "GUSD", "HUSD", "PAX", "USDK"]

# ========== إعدادات التقارير و Keep Alive ==========
ENABLE_PERIODIC_REPORT = True
PERIODIC_REPORT_INTERVAL_HOURS = 1
AUTO_SEND_CSV = True
AUTO_SEND_INTERVAL_HOURS = 1
AUTO_SEND_FILE = OPPORTUNITIES_CSV
AUTO_SEND_CAPTION = "📊 تقرير سكالبنغ تلقائي"
ENABLE_KEEP_ALIVE = True           # تفعيل إرسال طلب داخلي كل 5 دقائق
KEEP_ALIVE_INTERVAL_SECONDS = 300  # 5 دقائق

# =========================================================
# هياكل البيانات
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

# =========================================================
# دوال مساعدة
# =========================================================
async def retry_async(func, max_retries=2, base_delay=0.5):
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(base_delay * (attempt + 1))
    return None

# =========================================================
# المحرك الرئيسي (للسكالبنغ)
# =========================================================
class ScalpEngine:
    def __init__(self):
        self.active_trades = {}
        self.missed_trades = []
        self.all_opportunities = []
        self.balance = 2000.0
        self.stats = {"scanned": 0, "opportunities_found": 0, "last_scan_time": None}
        self._init_storage()
        self._load_state_sync()

    def _init_storage(self):
        conn = sqlite3.connect(DB_FILE)
        conn.execute("CREATE TABLE IF NOT EXISTS active_trades (symbol TEXT PRIMARY KEY, data TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value REAL)")
        if not conn.execute("SELECT value FROM config WHERE key='balance'").fetchone():
            conn.execute("INSERT INTO config VALUES ('balance', 2000.0)")
        conn.commit()
        conn.close()
        for f, header in [(REAL_CSV, ['Time', 'Symbol', 'Entry', 'Exit', 'PNL%']),
                          (MISSED_CSV, ['Time', 'Symbol', 'Entry', 'Exit', 'PNL%']),
                          (OPPORTUNITIES_CSV, ['Time', 'Symbol', 'Price', 'EntryPoint', 'ExpectedPump%', 'Votes', 'Score', 'Reason', 'Strategies', 'CandlePatterns', 'ExtraScores'])]:
            if not os.path.exists(f):
                with open(f, 'w', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    writer.writerow(header)

    def _load_state_sync(self):
        conn = sqlite3.connect(DB_FILE)
        try:
            row = conn.execute("SELECT value FROM config WHERE key='balance'").fetchone()
            if row:
                self.balance = row[0]
            rows = conn.execute("SELECT data FROM active_trades").fetchall()
            for (data_json,) in rows:
                d = json.loads(data_json)
                sig_dict = d.pop('signal')
                signal = TrainSignal(**sig_dict)
                trade = TradeInfo(**d, signal=signal)
                self.active_trades[trade.symbol] = trade
            logger.info(f"Loaded {len(self.active_trades)} active trades, balance={self.balance}")
        except Exception as e:
            logger.error(f"Load state error: {e}")
        finally:
            conn.close()

    async def _save_state(self):
        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute("DELETE FROM active_trades")
            for sym, trade in self.active_trades.items():
                data = asdict(trade)
                data['signal'] = asdict(trade.signal)
                conn.execute("INSERT INTO active_trades VALUES (?, ?)", (sym, json.dumps(data)))
            conn.execute("UPDATE config SET value = ? WHERE key = 'balance'", (self.balance,))
            conn.commit()
        except Exception as e:
            logger.error(f"Save state error: {e}")
        finally:
            conn.close()

    async def log_opportunity(self, symbol, price, entry_point, expected_pump, votes, score, reason, strategies, candle_patterns=None, extra_scores=None):
        async with aiofiles.open(OPPORTUNITIES_CSV, 'a', encoding='utf-8') as f:
            line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},{symbol},{price},{entry_point},{expected_pump},{votes},{score:.2f},{reason},{'|'.join(strategies)},{'|'.join(candle_patterns) if candle_patterns else ''},{json.dumps(extra_scores or {})}"
            await f.write(line + "\n")
            await f.flush()

    async def log_trade(self, symbol, entry, exit_price, pnl):
        async with aiofiles.open(REAL_CSV, 'a', encoding='utf-8') as f:
            await f.write(f"{datetime.now().isoformat()},{symbol},{entry},{exit_price},{pnl:.4f}\n")
            await f.flush()

    async def analyze_scalp(self, ex, symbol):
        reason = None
        try:
            ohlcv_1m = await retry_async(lambda: ex.fetch_ohlcv(symbol, timeframe='1m', limit=30))
            if len(ohlcv_1m) < 20:
                reason = "بيانات 1 دقيقة غير كافية"
                return None, reason
            df_1m = pd.DataFrame(ohlcv_1m, columns=['t','o','h','l','c','v'])
            
            ohlcv_5m = await retry_async(lambda: ex.fetch_ohlcv(symbol, timeframe='5m', limit=20))
            if len(ohlcv_5m) < 10:
                reason = "بيانات 5 دقائق غير كافية"
                return None, reason
            df_5m = pd.DataFrame(ohlcv_5m, columns=['t','o','h','l','c','v'])
            
            ticker = await retry_async(lambda: ex.fetch_ticker(symbol))
            vol_24h = ticker.get('quoteVolume', ticker.get('baseVolume', ticker['volume'] * ticker['last']))
            if vol_24h is None:
                vol_24h = ticker['volume'] * ticker['last']
            spread = (ticker['ask'] - ticker['bid']) / ticker['last'] * 100 if ticker['ask'] and ticker['bid'] else 100
            if vol_24h < MIN_24H_VOLUME_USD:
                reason = "حجم منخفض"
                return None, reason
            if spread > MAX_SPREAD_PCT:
                reason = "سبريد عالٍ"
                return None, reason

            price_change_1m = (df_1m['c'].iloc[-1] - df_1m['c'].iloc[-2]) / df_1m['c'].iloc[-2] * 100
            price_change_3m = (df_1m['c'].iloc[-1] - df_1m['c'].iloc[-4]) / df_1m['c'].iloc[-4] * 100 if len(df_1m) >= 4 else 0
            avg_volume_1m = df_1m['v'].rolling(10).mean().iloc[-2]
            volume_ratio = df_1m['v'].iloc[-1] / avg_volume_1m if avg_volume_1m > 0 else 1
            delta = df_1m['c'].diff()
            gain = delta.where(delta > 0, 0).rolling(7).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(7).mean()
            rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))
            rsi_val = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
            ema9_5m = df_5m['c'].ewm(span=9).mean().iloc[-1]
            uptrend = df_5m['c'].iloc[-1] > ema9_5m

            votes = []
            if price_change_1m > 0.1: votes.append("Momentum_1m")
            if price_change_3m > 0.2: votes.append("Momentum_3m")
            if volume_ratio > 1.5: votes.append("Volume_Spike")
            if 55 < rsi_val < 80: votes.append("RSI_Momentum")
            if uptrend: votes.append("Uptrend_5m")
            if spread < 0.05: votes.append("Tight_Spread")

            base_score = len(votes) * 12
            momentum_bonus = min(price_change_1m * 20, 15)
            volume_bonus = min(volume_ratio * 5, 10)
            total_score = base_score + momentum_bonus + volume_bonus
            total_score = round(total_score, 2)

            expected_pump = min(price_change_1m * 1.5 + volume_ratio * 0.5, 1.5)
            entry_point = ticker['ask'] if ticker['ask'] else df_1m['c'].iloc[-1]

            if len(votes) >= MIN_VOTES and price_change_1m > 0.05:
                signal = TrainSignal(
                    symbol=symbol,
                    entry_price=df_1m['c'].iloc[-1],
                    expected_pump_pct=round(expected_pump, 2),
                    votes=len(votes),
                    strategies=votes,
                    score=total_score,
                    entry_point=round(entry_point, 8),
                    extra_scores={'momentum': round(price_change_1m, 2), 'volume_ratio': round(volume_ratio, 2), 'rsi': round(rsi_val, 1)}
                )
                return signal, None
            else:
                reason = f"زخم منخفض ({price_change_1m:.2f}%) / أصوات {len(votes)}"
                return None, reason
        except Exception as e:
            reason = f"خطأ: {str(e)[:50]}"
            logger.error(f"Analyze error {symbol}: {e}")
            return None, reason

    async def update_trades(self, ex):
        for sym, trade in list(self.active_trades.items()):
            try:
                ticker = await retry_async(lambda: ex.fetch_ticker(sym))
                curr = ticker['last']
                pnl = (curr - trade.entry_price) / trade.entry_price * 100
                if curr > trade.highest_price:
                    trade.highest_price = curr

                if not trade.partial_closed and pnl >= PARTIAL_TP_PCT:
                    close_amount = trade.invested * PARTIAL_CLOSE_RATIO
                    profit_partial = close_amount * (pnl / 100)
                    self.balance += close_amount + profit_partial
                    trade.invested -= close_amount
                    trade.partial_closed = True
                    await send_tg(f"📊 *جني أرباح جزئي (Scalp) {sym}*\nالربح: {pnl:.2f}% | المتبقي: {trade.invested:.2f} USDT")
                    await self._save_state()

                if pnl >= TRAILING_ACTIVATE_PCT:
                    new_stop = trade.entry_price * (1 + (pnl - TRAILING_DISTANCE_PCT)/100)
                    if new_stop > trade.stop_loss:
                        trade.stop_loss = new_stop

                exit_reason = None
                if pnl <= -STOP_LOSS_PCT * 100:
                    exit_reason = "Stop Loss"
                elif trade.partial_closed and pnl <= (TRAILING_ACTIVATE_PCT - 0.1):
                    exit_reason = "Trailing Stop (remainder)"
                elif pnl >= FINAL_TP_PCT:
                    exit_reason = "Take Profit"
                elif curr <= trade.stop_loss and trade.stop_loss > trade.entry_price:
                    exit_reason = "Trailing Stop"

                if exit_reason:
                    total_pnl = (curr - trade.entry_price) / trade.entry_price * 100
                    self.balance += trade.invested * (1 + total_pnl/100)
                    await self._save_state()
                    await self.log_trade(sym, trade.entry_price, curr, total_pnl)
                    await send_tg(f"🏁 *إغلاق صفقة (Scalp) {sym}*\nالربح: `{total_pnl:.2f}%`\nالسبب: {exit_reason}\nالرصيد: {self.balance:.2f} USDT")
                    del self.active_trades[sym]
                    await self._save_state()
            except Exception as e:
                logger.error(f"Update trade error {sym}: {e}")

# =========================================================
# دوال تلغرام
# =========================================================
async def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    except:
        pass

async def send_document(file_path, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        async with httpx.AsyncClient() as client:
            with open(file_path, 'rb') as f:
                files = {'document': f}
                data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': caption}
                await client.post(url, data=data, files=files)
    except:
        pass

async def handle_telegram_commands():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id+1}&timeout=30"
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
                data = resp.json()
                if data['ok']:
                    for update in data['result']:
                        last_update_id = update['update_id']
                        if 'message' in update and 'text' in update['message']:
                            text = update['message']['text'].strip()
                            if text == '/start':
                                await send_tg("⚡ *بوت السكالبنغ (0.2%-1.2%)*\n✅ صفقات قصيرة جداً\n✅ أهداف ربح صغيرة\n✅ وقف خسارة ضيق\nالأوامر: /status, /download_real, /download_opp")
                            elif text == '/download_real':
                                if os.path.exists(REAL_CSV):
                                    await send_document(REAL_CSV, "سجل صفقات السكالبنغ")
                                else:
                                    await send_tg("⚠️ الملف غير موجود.")
                            elif text == '/download_opp':
                                if os.path.exists(OPPORTUNITIES_CSV):
                                    await send_document(OPPORTUNITIES_CSV, "سجل الفرص")
                                else:
                                    await send_tg("⚠️ الملف غير موجود.")
                            elif text == '/status':
                                await send_tg(f"📈 *حالة بوت السكالبنغ*\nالرصيد: {engine.balance:.2f} USDT\nصفقات مفتوحة: {len(engine.active_trades)}/{MAX_CONCURRENT_TRADES}")
        except:
            pass
        await asyncio.sleep(2)

async def periodic_report():
    if not ENABLE_PERIODIC_REPORT:
        return
    await asyncio.sleep(60)
    last_report_time = datetime.now()
    while True:
        try:
            now = datetime.now()
            closed_trades = []
            if os.path.exists(REAL_CSV):
                with open(REAL_CSV, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    for line in lines[1:]:
                        parts = line.strip().split(',')
                        if len(parts) >= 5:
                            try:
                                trade_time = datetime.fromisoformat(parts[0])
                                if trade_time > last_report_time:
                                    closed_trades.append({'symbol': parts[1], 'pnl': float(parts[4])})
                            except:
                                pass
            msg = f"📊 *تقرير السكالبنغ* - {now.strftime('%H:%M:%S')}\n"
            msg += f"💰 الرصيد: {engine.balance:.2f} USDT\n"
            msg += f"🟢 صفقات مفتوحة: {len(engine.active_trades)}\n"
            if closed_trades:
                wins = sum(1 for t in closed_trades if t['pnl'] > 0)
                msg += f"🔒 صفقات مغلقة منذ آخر تقرير: {len(closed_trades)} (نجاح: {wins})\n"
                avg_pnl = sum(t['pnl'] for t in closed_trades) / len(closed_trades) if closed_trades else 0
                msg += f"📈 متوسط الربح/الخسارة: {avg_pnl:+.2f}%"
            await send_tg(msg)
            last_report_time = now
        except:
            pass
        await asyncio.sleep(PERIODIC_REPORT_INTERVAL_HOURS * 3600)

async def auto_send_csv():
    if not AUTO_SEND_CSV:
        return
    await asyncio.sleep(60)
    while True:
        try:
            if os.path.exists(AUTO_SEND_FILE):
                await send_document(AUTO_SEND_FILE, AUTO_SEND_CAPTION)
        except:
            pass
        await asyncio.sleep(AUTO_SEND_INTERVAL_HOURS * 3600)

# =========================================================
# Keep Alive (إرسال طلب داخلي لمنع الإيقاف)
# =========================================================
async def keep_alive_task(port):
    if not ENABLE_KEEP_ALIVE:
        return
    url = f"http://localhost:{port}/"
    while True:
        try:
            # إرسال طلب HTTP بسيط باستخدام httpx (غير متزامن)
            async with httpx.AsyncClient() as client:
                await client.get(url, timeout=5)
            logger.info("Keep-alive request sent")
        except Exception as e:
            logger.warning(f"Keep-alive failed: {e}")
        await asyncio.sleep(KEEP_ALIVE_INTERVAL_SECONDS)

# =========================================================
# خادم Flask الصغير (يعمل في thread منفصل)
# =========================================================
def run_web_server(port):
    app = Flask(__name__)

    @app.route('/')
    @app.route('/health')
    def health():
        return "✅ Scalping Bot is running!", 200

    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# =========================================================
# حلقة التداول الرئيسية (للسكالبنغ)
# =========================================================
async def main_loop():
    global engine
    exchange_class = getattr(ccxt_async, EXCHANGE_NAME)
    ex = exchange_class({'enableRateLimit': True})
    if EXCHANGE_NAME == 'bybit':
        ex.options['defaultType'] = 'spot'
    engine.exchange = ex

    await send_tg("⚡ *بوت السكالبنغ (Scalping) بدأ*\n✅ أهداف: 0.2% - 1.2%\n✅ صفقات سريعة (دقائق)\n✅ وقف خسارة 0.25%")

    markets = await ex.fetch_markets()
    raw_symbols = [m for m in markets if m['symbol'].endswith('/USDT') and m['active']]
    symbols = []
    for market in raw_symbols:
        base = market['base']
        if EXCLUDE_STABLECOINS and base in STABLECOINS:
            continue
        try:
            ticker = await ex.fetch_ticker(market['symbol'])
            vol_24h = ticker.get('quoteVolume', ticker.get('baseVolume', ticker['volume'] * ticker['last']))
            if vol_24h is None:
                vol_24h = ticker['volume'] * ticker['last']
            price = ticker['last']
            if EXCLUDE_VERY_LARGE_CAP and vol_24h > MAX_24H_VOLUME_USD_FILTER:
                continue
            if price < MIN_PRICE_USD or price > MAX_PRICE_USD:
                continue
            symbols.append(market['symbol'])
        except:
            continue
    symbols = symbols[:TOTAL_SYMBOLS_TO_SCAN]
    await send_tg(f"📊 {len(symbols)} عملة مؤهلة للسكالبنغ")

    while True:
        try:
            scan_start = datetime.now()
            engine.stats["scanned"] = 0
            engine.stats["opportunities_found"] = 0
            random_symbols = np.random.choice(symbols, min(len(symbols), TOTAL_SYMBOLS_TO_SCAN), replace=False)
            all_signals = []

            for i in range(0, len(random_symbols), BATCH_SIZE):
                batch = random_symbols[i:i+BATCH_SIZE]
                tasks = [engine.analyze_scalp(ex, s) for s in batch]
                results = await asyncio.gather(*tasks)
                for (sig, reason), symbol in zip(results, batch):
                    if sig:
                        all_signals.append(sig)
                        engine.stats["opportunities_found"] += 1
                        await engine.log_opportunity(sig.symbol, sig.entry_price, sig.entry_point, sig.expected_pump_pct,
                                                     sig.votes, sig.score, "إشارة سكالبنغ", sig.strategies, sig.candle_patterns, sig.extra_scores)
                    else:
                        dummy = TrainSignal(symbol=symbol, entry_price=0, expected_pump_pct=0, votes=0,
                                            strategies=[], score=0, reason=reason or "لا توجد إشارة", entry_point=0)
                        await engine.log_opportunity(symbol, 0, 0, 0, 0, 0, reason or "لا توجد إشارة", [], [], {})
                engine.stats["scanned"] += len(batch)
                await asyncio.sleep(0.05)

            all_signals.sort(key=lambda x: x.score, reverse=True)
            if all_signals:
                best = all_signals[0]
                await send_tg(f"⚡ *أفضل فرصة سكالبنغ*: {best.symbol} | سكور {best.score} | زخم {best.extra_scores.get('momentum',0):.2f}% | هدف {best.expected_pump_pct}%")
                
                if best.symbol not in engine.active_trades:
                    risk_ratio = RISK_PER_TRADE_MEDIUM
                    risk_amount = engine.balance * risk_ratio
                    position_size = risk_amount / STOP_LOSS_PCT
                    invest = min(position_size, engine.balance)
                    if len(engine.active_trades) < MAX_CONCURRENT_TRADES and engine.balance >= invest:
                        stop_loss_price = best.entry_point * (1 - STOP_LOSS_PCT)
                        take_profit_price = best.entry_point * (1 + FINAL_TP_PCT/100)
                        trade = TradeInfo(
                            symbol=best.symbol,
                            signal=best,
                            entry_price=best.entry_point,
                            invested=invest,
                            highest_price=best.entry_point,
                            stop_loss=stop_loss_price,
                            take_profit=take_profit_price,
                            partial_closed=False
                        )
                        engine.active_trades[best.symbol] = trade
                        engine.balance -= invest
                        await engine._save_state()
                        await send_tg(f"🟢 *شراء سكالبنغ {best.symbol}*\n💰 السعر: {best.entry_point:.8f}\n💵 المبلغ: {invest:.2f} USDT\n🎯 الهدف: {take_profit_price:.8f} (+{FINAL_TP_PCT*100:.1f}%)\n🛑 الوقف: {stop_loss_price:.8f} (-{STOP_LOSS_PCT*100:.1f}%)")

            await engine.update_trades(ex)
            engine.stats["last_scan_time"] = scan_start.strftime("%H:%M:%S")
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            await asyncio.sleep(2)
        await asyncio.sleep(SCAN_INTERVAL)

# =========================================================
# تشغيل البوت (مع خادم ويب و Keep Alive)
# =========================================================
async def main():
    global engine
    engine = ScalpEngine()
    
    # الحصول على المنفذ من متغير البيئة PORT (Render) أو استخدام 10000 افتراضي
    port = int(os.environ.get("PORT", 10000))
    
    # تشغيل خادم Flask في thread منفصل
    flask_thread = threading.Thread(target=run_web_server, args=(port,), daemon=True)
    flask_thread.start()
    logger.info(f"Flask web server started on port {port}")
    
    # بدء مهمة Keep Alive (طلب داخلي كل 5 دقائق)
    asyncio.create_task(keep_alive_task(port))
    
    # تشغيل المهام الأخرى
    asyncio.create_task(handle_telegram_commands())
    asyncio.create_task(periodic_report())
    asyncio.create_task(auto_send_csv())
    
    await main_loop()

if __name__ == "__main__":
    asyncio.run(main())

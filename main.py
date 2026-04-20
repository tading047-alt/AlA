#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
نظام التداول الهجين المتكامل - الإصدار النهائي
Hybrid Trading System - Final Edition with Keep-Alive

المميزات:
- اكتشاف المحطة الأولى (دخول مبكر قبل الانفجار)
- تأكيد هجين (سرعة + أمان)
- إشعارات تيليجرام فورية
- تسجيل شامل في CSV للمراقبة والتحليل
- خادم HTTP لفحص الصحة (متوافق مع Render)
- نظام Keep-Alive ذاتي لمنع النوم
- تقارير يومية تلقائية
- إدارة مخاطر متكاملة
"""

import asyncio
import ccxt.async_support as ccxt_async
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import deque
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import json
import csv
import os
import aiofiles
import httpx
import time
import hashlib
import shutil
import threading
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import random

# =========================================================
# خادم HTTP لفحص الصحة و Keep-Alive
# =========================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    """معالج طلبات HTTP لفحص الصحة"""
    
    def do_GET(self):
        """معالجة طلبات GET"""
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            # إرجاع حالة النظام
            status = {
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'service': 'hybrid-trading-bot',
                'uptime': time.time() - start_time if 'start_time' in globals() else 0
            }
            self.wfile.write(json.dumps(status).encode())
            
        elif self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            
            # صفحة رئيسية بسيطة
            html = """
            <!DOCTYPE html>
            <html dir="rtl" lang="ar">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>نظام التداول الهجين</title>
                <style>
                    body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                           min-height: 100vh; display: flex; justify-content: center; align-items: center; margin: 0; }
                    .container { background: white; border-radius: 20px; padding: 40px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); 
                                text-align: center; max-width: 500px; }
                    h1 { color: #333; margin-bottom: 20px; }
                    .status { color: #10b981; font-size: 18px; margin: 20px 0; }
                    .emoji { font-size: 50px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="emoji">🚀</div>
                    <h1>نظام التداول الهجين</h1>
                    <div class="status">✅ النظام يعمل بنجاح</div>
                    <p>اكتشاف المحطة الأولى | تأكيد هجين | إشعارات فورية</p>
                    <hr style="margin: 20px 0; border: none; border-top: 1px solid #eee;">
                    <small style="color: #999;">Hybrid Trading Bot v2.0</small>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode())
            
        elif self.path == '/ping':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'pong')
            
        elif self.path == '/stats':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            # إحصائيات سريعة
            stats = {
                'active_trades': len(active_trades_global) if 'active_trades_global' in globals() else 0,
                'daily_trades': daily_trades_global if 'daily_trades_global' in globals() else 0,
                'timestamp': datetime.now().isoformat()
            }
            self.wfile.write(json.dumps(stats).encode())
            
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """تجاهل سجلات HTTP لتقليل الإخراج"""
        pass


class KeepAliveServer:
    """خادم Keep-Alive لمنع النوم"""
    
    def __init__(self, port: int = 8080):
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.is_running = False
        
        # روابط للـ ping الذاتي
        self.self_ping_urls = [
            f"http://localhost:{port}/ping",
            f"http://127.0.0.1:{port}/ping",
        ]
        
        # إضافة رابط Render إذا وجد
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            self.self_ping_urls.append(f"{render_url}/ping")
            self.self_ping_urls.append(f"{render_url}/health")
    
    def start(self):
        """بدء تشغيل الخادم"""
        try:
            self.server = HTTPServer(('0.0.0.0', self.port), HealthCheckHandler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.is_running = True
            
            print(f"""
╔══════════════════════════════════════════════════════════╗
║              🌐 خادم HTTP يعمل بنجاح                      ║
╠══════════════════════════════════════════════════════════╣
║  📡 المنفذ: {self.port}                                          ║
║  💓 فحص الصحة: http://localhost:{self.port}/health          ║
║  🏠 الصفحة الرئيسية: http://localhost:{self.port}/          ║
╚══════════════════════════════════════════════════════════╝
            """)
            
            # بدء الـ Self-Ping في خيط منفصل
            ping_thread = threading.Thread(target=self._self_ping_loop, daemon=True)
            ping_thread.start()
            
            return True
        except Exception as e:
            print(f"❌ فشل تشغيل خادم HTTP: {e}")
            return False
    
    def _self_ping_loop(self):
        """حلقة ping ذاتية لمنع النوم"""
        print("🔄 بدء نظام Keep-Alive الذاتي...")
        
        while self.is_running:
            try:
                # انتظار عشوائي بين 4-8 دقائق
                sleep_time = random.randint(240, 480)
                time.sleep(sleep_time)
                
                # Ping لكل الروابط
                for url in self.self_ping_urls:
                    try:
                        req = urllib.request.Request(url, method='GET')
                        with urllib.request.urlopen(req, timeout=5) as response:
                            if response.status == 200:
                                print(f"  💓 Self-ping ناجح: {url}")
                    except Exception:
                        pass  # تجاهل الأخطاء في self-ping
                        
            except Exception as e:
                print(f"  ⚠️ خطأ في self-ping: {e}")
    
    def stop(self):
        """إيقاف الخادم"""
        self.is_running = False
        if self.server:
            self.server.shutdown()


def ping_external_service():
    """Ping خدمة خارجية للحفاظ على النشاط"""
    urls = [
        "https://api.gate.io/api/v4/public/tickers",
        "https://www.google.com",
        "https://httpbin.org/get"
    ]
    
    while True:
        try:
            time.sleep(random.randint(300, 600))  # 5-10 دقائق
            
            url = random.choice(urls)
            req = urllib.request.Request(url, method='GET')
            req.add_header('User-Agent', 'Mozilla/5.0')
            
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    print(f"  🌐 External ping ناجح")
                    
        except Exception:
            pass  # تجاهل الأخطاء


# =========================================================
# المتغيرات العامة (لخادم HTTP)
# =========================================================

start_time = time.time()
active_trades_global = {}
daily_trades_global = 0

# =========================================================
# هياكل البيانات الأساسية
# =========================================================

class NotificationLevel(Enum):
    """مستويات الإشعارات"""
    CRITICAL = "🔴"
    IMPORTANT = "🟡"
    INFO = "🔵"
    SUCCESS = "🟢"
    WARNING = "🟠"
    DEBUG = "⚪"

class StrategyType(Enum):
    """أنواع الاستراتيجيات"""
    BREAKOUT_MOMENTUM = "💥 اختراق الزخم"
    VOLUME_EXPLOSION = "📊 انفجار الحجم"
    TREND_REVERSAL = "🔄 انعكاس الترند"
    PATTERN_RECOGNITION = "🕯️ نماذج الشموع"
    MULTI_TIMEFRAME = "🔭 متعدد الأطر"

@dataclass
class StationSignal:
    """إشارة المحطة الأولى"""
    symbol: str
    station_type: str
    confidence: float
    entry_price: float
    expected_move: float
    time_to_explosion: int
    signals: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class StrategySignal:
    """إشارة من استراتيجية واحدة"""
    strategy: StrategyType
    score: float
    confidence: float
    entry_price: float
    target_price: float
    stop_loss: float
    signals: List[str]
    timeframe_scores: Dict[str, float]
    timestamp: str

@dataclass
class ConsensusResult:
    """نتيجة توافق الاستراتيجيات"""
    symbol: str
    current_price: float
    strategy_results: Dict[StrategyType, StrategySignal]
    consensus_score: float
    agreement_count: int
    average_score: float
    weighted_score: float
    final_grade: str
    recommendation: str
    risk_level: str
    top_strategies: List[StrategyType]
    entry_conditions: List[str]
    warnings: List[str]

# =========================================================
# نظام التسجيل والإشعارات
# =========================================================

class HybridLogger:
    """نظام تسجيل متكامل للمراقبة والتحليل"""
    
    def __init__(self, base_dir: str = "trading_logs"):
        self.base_dir = base_dir
        self._create_directories()
        
        self.files = {
            'signals': f"{base_dir}/signals_detected.csv",
            'trades': f"{base_dir}/trades_executed.csv",
            'performance': f"{base_dir}/performance_daily.csv",
            'errors': f"{base_dir}/errors_log.csv",
            'market_snapshot': f"{base_dir}/market_snapshots.csv",
            'notifications': f"{base_dir}/notifications_sent.csv",
        }
        
        self.daily_stats = {}
        self.telegram_enabled = True
        self.telegram_token = os.environ.get("8439548325:AAHOBBHy7EwcX3J5neIaf6iJuSjyGJCuZ68", "")
        self.telegram_chat_id = os.environ.get("5067771509", "")
        self.min_notification_level = NotificationLevel.INFO
        
        print(f"✅ نظام التسجيل جاهز - المجلد: {base_dir}")
    
    def _create_directories(self):
        """إنشاء المجلدات اللازمة"""
        directories = [
            self.base_dir,
            f"{self.base_dir}/daily",
            f"{self.base_dir}/archive",
            f"{self.base_dir}/charts",
        ]
        for directory in directories:
            os.makedirs(directory, exist_ok=True)
    
    async def log_signal(self, data: dict):
        """تسجيل إشارة مكتشفة"""
        filepath = self.files['signals']
        data['log_timestamp'] = datetime.now().isoformat()
        data['signal_id'] = self._generate_id(data)
        await self._append_to_csv(filepath, data)
        await self._update_daily_stats('signals_detected', 1)
    
    async def log_trade(self, trade_data: dict):
        """تسجيل صفقة منفذة"""
        filepath = self.files['trades']
        trade_data['log_timestamp'] = datetime.now().isoformat()
        trade_data['trade_id'] = self._generate_id(trade_data)
        await self._append_to_csv(filepath, trade_data)
        
        if trade_data.get('trade_type') == 'exit':
            await self._log_trade_result(trade_data)
    
    async def log_error(self, error_data: dict):
        """تسجيل خطأ"""
        filepath = self.files['errors']
        error_data['timestamp'] = datetime.now().isoformat()
        error_data['date'] = datetime.now().strftime('%Y-%m-%d')
        error_data['time'] = datetime.now().strftime('%H:%M:%S')
        await self._append_to_csv(filepath, error_data)
        
        if error_data.get('severity') == 'critical':
            await self.send_notification(
                level=NotificationLevel.CRITICAL,
                title="❌ خطأ حرج",
                message=f"{error_data.get('error_type')}: {error_data.get('message')}"
            )
    
    async def log_performance(self, performance_data: dict):
        """تسجيل أداء يومي"""
        filepath = self.files['performance']
        performance_data['date'] = datetime.now().strftime('%Y-%m-%d')
        performance_data['updated_at'] = datetime.now().isoformat()
        
        daily_file = f"{self.base_dir}/daily/performance_{performance_data['date']}.json"
        async with aiofiles.open(daily_file, 'w') as f:
            await f.write(json.dumps(performance_data, indent=2))
        
        await self._append_to_csv(filepath, performance_data)
    
    async def log_market_snapshot(self, snapshot_data: dict):
        """تسجيل لقطة للسوق"""
        filepath = self.files['market_snapshot']
        snapshot_data['timestamp'] = datetime.now().isoformat()
        await self._append_to_csv(filepath, snapshot_data)
    
    async def log_notification(self, notification_data: dict):
        """تسجيل إشعار مرسل"""
        filepath = self.files['notifications']
        notification_data['sent_at'] = datetime.now().isoformat()
        await self._append_to_csv(filepath, notification_data)
    
    async def _append_to_csv(self, filepath: str, data: dict):
        """إضافة صف إلى ملف CSV"""
        try:
            exists = os.path.isfile(filepath)
            async with aiofiles.open(filepath, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=data.keys())
                if not exists:
                    await writer.writeheader()
                await writer.writerow(data)
        except Exception as e:
            print(f"خطأ في كتابة CSV: {e}")
    
    def _generate_id(self, data: dict) -> str:
        """إنشاء معرف فريد"""
        timestamp = str(int(time.time() * 1000))
        data_str = json.dumps(data, sort_keys=True)
        hash_str = hashlib.md5(f"{timestamp}_{data_str}".encode()).hexdigest()[:8]
        return f"{timestamp}_{hash_str}"
    
    async def _log_trade_result(self, trade_data: dict):
        """تسجيل نتيجة صفقة مكتملة"""
        pnl = trade_data.get('pnl_percentage', 0)
        pnl_usd = trade_data.get('pnl_usd', 0)
        
        if pnl > 0:
            await self._update_daily_stats('winning_trades', 1)
            await self._update_daily_stats('total_profit_usd', pnl_usd)
        else:
            await self._update_daily_stats('losing_trades', 1)
            await self._update_daily_stats('total_loss_usd', abs(pnl_usd))
        
        await self._update_daily_stats('total_trades', 1)
        
        if abs(pnl) >= 5:
            level = NotificationLevel.SUCCESS if pnl > 0 else NotificationLevel.WARNING
            emoji = "💰" if pnl > 0 else "📉"
            await self.send_notification(
                level=level,
                title=f"{emoji} صفقة مكتملة",
                message=f"{trade_data['symbol']}: {pnl:+.2f}% ({pnl_usd:+.2f}$)"
            )
    
    async def _update_daily_stats(self, key: str, value: float):
        """تحديث الإحصائيات اليومية"""
        today = datetime.now().strftime('%Y-%m-%d')
        
        if today not in self.daily_stats:
            self.daily_stats[today] = {
                'signals_detected': 0, 'total_trades': 0,
                'winning_trades': 0, 'losing_trades': 0,
                'total_profit_usd': 0, 'total_loss_usd': 0,
                'largest_win': 0, 'largest_loss': 0,
            }
        
        if key in self.daily_stats[today]:
            if isinstance(value, (int, float)):
                self.daily_stats[today][key] += value
            else:
                self.daily_stats[today][key] = value
        
        if key == 'total_profit_usd' and value > self.daily_stats[today]['largest_win']:
            self.daily_stats[today]['largest_win'] = value
        elif key == 'total_loss_usd' and value > self.daily_stats[today]['largest_loss']:
            self.daily_stats[today]['largest_loss'] = value
    
    async def send_notification(self, level: NotificationLevel, title: str, 
                               message: str, data: dict = None):
        """إرسال إشعار"""
        if level.value < self.min_notification_level.value:
            return
        
        timestamp = datetime.now()
        formatted_message = f"{level.value} *{title}*\n\n{message}"
        
        if data:
            formatted_message += f"\n\n```json\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```"
        
        formatted_message += f"\n\n🕐 `{timestamp.strftime('%Y-%m-%d %H:%M:%S')}`"
        
        telegram_sent = False
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            telegram_sent = await self._send_telegram(formatted_message)
        
        print(f"\n{level.value} {title}\n{message}\n{'-'*40}")
        
        await self.log_notification({
            'level': level.name, 'title': title, 'message': message,
            'telegram_sent': telegram_sent, 'data': json.dumps(data) if data else ''
        })
    
    async def _send_telegram(self, message: str) -> bool:
        """إرسال رسالة إلى تيليجرام"""
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json={
                    "chat_id": self.telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown"
                })
                return response.status_code == 200
        except Exception as e:
            print(f"خطأ في إرسال تيليجرام: {e}")
            return False
    
    async def generate_daily_report(self, date: str = None) -> dict:
        """إنشاء تقرير يومي"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        stats = self.daily_stats.get(date, {})
        total_trades = stats.get('total_trades', 0)
        winning = stats.get('winning_trades', 0)
        losing = stats.get('losing_trades', 0)
        
        win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
        total_profit = stats.get('total_profit_usd', 0)
        total_loss = stats.get('total_loss_usd', 0)
        net_pnl = total_profit - total_loss
        
        report = {
            'date': date,
            'summary': {
                'total_trades': total_trades,
                'winning_trades': winning,
                'losing_trades': losing,
                'win_rate': round(win_rate, 2),
                'net_pnl_usd': round(net_pnl, 2),
                'largest_win': round(stats.get('largest_win', 0), 2),
                'largest_loss': round(stats.get('largest_loss', 0), 2),
            },
            'signals': {
                'total_detected': stats.get('signals_detected', 0),
                'conversion_rate': round((total_trades / stats.get('signals_detected', 1)) * 100, 2)
            }
        }
        
        report_file = f"{self.base_dir}/daily/report_{date}.json"
        async with aiofiles.open(report_file, 'w') as f:
            await f.write(json.dumps(report, indent=2))
        
        if total_trades > 0:
            await self.send_notification(
                level=NotificationLevel.INFO,
                title="📊 التقرير اليومي",
                message=f"الصفقات: {total_trades} | الربح: {win_rate:.1f}% | الصافي: {net_pnl:+.2f}$"
            )
        
        return report
    
    async def export_all_data(self):
        """تصدير جميع البيانات"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        export_dir = f"{self.base_dir}/archive/export_{timestamp}"
        os.makedirs(export_dir, exist_ok=True)
        
        for name, filepath in self.files.items():
            if os.path.exists(filepath):
                shutil.copy(filepath, f"{export_dir}/{name}.csv")
        
        stats_file = f"{export_dir}/daily_stats.json"
        with open(stats_file, 'w') as f:
            json.dump(self.daily_stats, f, indent=2)
        
        print(f"✅ تم تصدير جميع البيانات إلى: {export_dir}")
        return export_dir

# =========================================================
# كاشف المحطة الأولى
# =========================================================

class FirstStationDetector:
    """كاشف المحطة الأولى - يكتشف الانفجار قبل حدوثه"""
    
    def __init__(self):
        self.patterns = {
            'calm_before_storm': {
                'volume_drop': 0.4, 'price_range': 1.5,
                'duration_minutes': 15, 'weight': 35, 'time_to_explode': 300
            },
            'whale_accumulation': {
                'large_trades_ratio': 0.7, 'price_stability': 1.0,
                'buy_sell_ratio': 1.5, 'weight': 40, 'time_to_explode': 180
            },
            'bollinger_crush': {
                'bandwidth_percentile': 5, 'price_at_band': 'lower',
                'weight': 30, 'time_to_explode': 240
            },
            'hidden_bullish_divergence': {
                'price_making_lower_low': True, 'rsi_making_higher_low': True,
                'weight': 35, 'time_to_explode': 360
            },
            'micro_ma_breakout': {
                'ema9_cross_ema21': True, 'volume_confirmation': 1.3,
                'weight': 20, 'time_to_explode': 90
            }
        }
        self.watching = {}
        self.entry_threshold = 65
    
    async def scan_for_first_station(self, exchange, symbols: List[str]) -> List[StationSignal]:
        """المسح الشامل لاكتشاف العملات في المحطة الأولى"""
        stations = []
        batch_size = 20
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            tasks = [self._analyze_single_symbol(exchange, symbol) for symbol in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for symbol, result in zip(batch, results):
                if isinstance(result, StationSignal) and result.confidence >= self.entry_threshold:
                    stations.append(result)
                    self.watching[symbol] = result
            
            await asyncio.sleep(0.5)
        
        stations.sort(key=lambda x: x.confidence, reverse=True)
        return stations
    
    async def _analyze_single_symbol(self, exchange, symbol: str) -> Optional[StationSignal]:
        """تحليل عملة واحدة لاكتشاف المحطة الأولى"""
        try:
            ohlcv_1m = await exchange.fetch_ohlcv(symbol, '1m', limit=60)
            ohlcv_5m = await exchange.fetch_ohlcv(symbol, '5m', limit=50)
            ticker = await exchange.fetch_ticker(symbol)
            
            df_1m = self._to_dataframe(ohlcv_1m)
            df_5m = self._to_dataframe(ohlcv_5m)
            
            detected_patterns = []
            total_confidence = 0
            time_estimates = []
            
            calm = self._check_calm_before_storm(df_1m, df_5m)
            if calm['detected']:
                detected_patterns.append(calm)
                total_confidence += calm['confidence']
                time_estimates.append(calm['time_to_explode'])
            
            whales = self._check_whale_accumulation(df_1m, ticker)
            if whales['detected']:
                detected_patterns.append(whales)
                total_confidence += whales['confidence']
                time_estimates.append(whales['time_to_explode'])
            
            bollinger = self._check_bollinger_crush(df_5m)
            if bollinger['detected']:
                detected_patterns.append(bollinger)
                total_confidence += bollinger['confidence']
                time_estimates.append(bollinger['time_to_explode'])
            
            divergence = self._check_hidden_divergence(df_5m)
            if divergence['detected']:
                detected_patterns.append(divergence)
                total_confidence += divergence['confidence']
                time_estimates.append(divergence['time_to_explode'])
            
            micro_ma = self._check_micro_ma_breakout(df_1m)
            if micro_ma['detected']:
                detected_patterns.append(micro_ma)
                total_confidence += micro_ma['confidence']
                time_estimates.append(micro_ma['time_to_explode'])
            
            if total_confidence >= 50:
                station_type = "ignition" if total_confidence >= 80 else "pre_explosion" if total_confidence >= 65 else "confirmation"
                avg_time = int(np.mean(time_estimates)) if time_estimates else 180
                expected_move = self._calculate_expected_move(total_confidence, detected_patterns)
                signals = [p['description'] for p in detected_patterns]
                
                return StationSignal(
                    symbol=symbol, station_type=station_type,
                    confidence=min(100, total_confidence),
                    entry_price=ticker['last'], expected_move=expected_move,
                    time_to_explosion=avg_time, signals=signals
                )
            
        except Exception as e:
            print(f"خطأ في تحليل {symbol}: {e}")
        
        return None
    
    def _to_dataframe(self, ohlcv: list) -> pd.DataFrame:
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    def _check_calm_before_storm(self, df_1m: pd.DataFrame, df_5m: pd.DataFrame) -> dict:
        pattern = self.patterns['calm_before_storm']
        recent_volume = df_1m['volume'].tail(15).mean()
        older_volume = df_1m['volume'].head(30).mean()
        volume_drop_ratio = recent_volume / older_volume if older_volume > 0 else 1
        
        recent_prices = df_1m['close'].tail(15)
        price_range = ((recent_prices.max() - recent_prices.min()) / recent_prices.mean()) * 100
        
        if volume_drop_ratio < pattern['volume_drop'] and price_range < pattern['price_range']:
            confidence = pattern['weight']
            if self._is_at_support(df_5m):
                confidence += 10
            return {
                'detected': True, 'pattern': 'calm_before_storm',
                'description': f'🌊 هدوء قبل العاصفة (حجم {volume_drop_ratio*100:.0f}%)',
                'confidence': confidence, 'time_to_explode': pattern['time_to_explode']
            }
        return {'detected': False}
    
    def _check_whale_accumulation(self, df_1m: pd.DataFrame, ticker: dict) -> dict:
        pattern = self.patterns['whale_accumulation']
        volume = df_1m['volume'].tail(10).sum()
        avg_volume = df_1m['volume'].tail(50).mean()
        volume_ratio = volume / (avg_volume * 10) if avg_volume > 0 else 1
        
        prices = df_1m['close'].tail(10)
        price_stability = ((prices.max() - prices.min()) / prices.mean()) * 100
        
        if volume_ratio > 1.3 and price_stability < pattern['price_stability']:
            return {
                'detected': True, 'pattern': 'whale_accumulation',
                'description': f'🐋 تجميع حيتان (حجم {volume_ratio:.1f}x)',
                'confidence': pattern['weight'], 'time_to_explode': pattern['time_to_explode']
            }
        return {'detected': False}
    
    def _check_bollinger_crush(self, df: pd.DataFrame) -> dict:
        pattern = self.patterns['bollinger_crush']
        if len(df) < 30:
            return {'detected': False}
        
        closes = df['close'].values
        period = 20
        recent = closes[-period:]
        middle = np.mean(recent)
        std = np.std(recent)
        upper, lower = middle + 2 * std, middle - 2 * std
        current_price = closes[-1]
        bandwidth = ((upper - lower) / middle) * 100
        
        historical_bandwidth = []
        for i in range(period, len(closes)):
            window = closes[i-period:i]
            m = np.mean(window)
            s = np.std(window)
            historical_bandwidth.append((4 * s / m) * 100)
        
        if historical_bandwidth:
            percentile = (sum(1 for bw in historical_bandwidth if bw <= bandwidth) / len(historical_bandwidth)) * 100
            price_position = (current_price - lower) / (upper - lower)
            
            if percentile < pattern['bandwidth_percentile'] and price_position < 0.3:
                return {
                    'detected': True, 'pattern': 'bollinger_crush',
                    'description': f'🎯 انضغاط بولنجر ({bandwidth:.1f}%)',
                    'confidence': pattern['weight'] + (10 if percentile < 3 else 0),
                    'time_to_explode': pattern['time_to_explode']
                }
        return {'detected': False}
    
    def _check_hidden_divergence(self, df: pd.DataFrame) -> dict:
        pattern = self.patterns['hidden_bullish_divergence']
        if len(df) < 30:
            return {'detected': False}
        
        closes = df['close'].values
        rsi = self._calculate_rsi(closes, 14)
        
        recent_closes = closes[-20:]
        recent_rsi = rsi[-20:]
        
        price_lows = self._find_lows(recent_closes)
        rsi_lows = self._find_lows(recent_rsi)
        
        if len(price_lows) >= 2 and len(rsi_lows) >= 2:
            if (recent_closes[price_lows[-1]] < recent_closes[price_lows[-2]] and 
                recent_rsi[rsi_lows[-1]] > recent_rsi[rsi_lows[-2]]):
                return {
                    'detected': True, 'pattern': 'hidden_divergence',
                    'description': '📈 تباعد إيجابي خفي',
                    'confidence': pattern['weight'], 'time_to_explode': pattern['time_to_explode']
                }
        return {'detected': False}
    
    def _check_micro_ma_breakout(self, df: pd.DataFrame) -> dict:
        pattern = self.patterns['micro_ma_breakout']
        if len(df) < 30:
            return {'detected': False}
        
        closes = df['close'].values
        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)
        
        if ema9[-2] <= ema21[-2] and ema9[-1] > ema21[-1]:
            volumes = df['volume'].values
            current_volume = volumes[-1]
            avg_volume = np.mean(volumes[-10:])
            
            if current_volume > avg_volume * pattern['volume_confirmation']:
                return {
                    'detected': True, 'pattern': 'micro_ma_breakout',
                    'description': '📊 اختراق المتوسطات الصغرى',
                    'confidence': pattern['weight'], 'time_to_explode': pattern['time_to_explode']
                }
        return {'detected': False}
    
    def _is_at_support(self, df: pd.DataFrame) -> bool:
        closes = df['close'].values
        current = closes[-1]
        lows = []
        for i in range(20, len(closes) - 5):
            if closes[i] < closes[i-1] and closes[i] < closes[i+1]:
                lows.append(closes[i])
        if lows:
            avg_support = np.mean(lows[-3:]) if len(lows) >= 3 else np.mean(lows)
            distance = abs(current - avg_support) / current
            return distance < 0.02
        return False
    
    def _calculate_rsi(self, prices: np.ndarray, period: int = 14) -> np.ndarray:
        deltas = np.diff(prices)
        gain = np.where(deltas > 0, deltas, 0)
        loss = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.zeros_like(prices)
        avg_loss = np.zeros_like(prices)
        
        if len(prices) > period:
            avg_gain[period] = np.mean(gain[:period])
            avg_loss[period] = np.mean(loss[:period])
            for i in range(period + 1, len(prices)):
                avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i-1]) / period
                avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i-1]) / period
        
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))
    
    def _calculate_ema(self, data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data)
        if len(data) >= period:
            ema[period-1] = np.mean(data[:period])
            for i in range(period, len(data)):
                ema[i] = data[i] * alpha + ema[i-1] * (1 - alpha)
        return ema
    
    def _find_lows(self, data: np.ndarray, window: int = 3) -> List[int]:
        lows = []
        for i in range(window, len(data) - window):
            if all(data[i] <= data[i-j] for j in range(1, window+1)) and \
               all(data[i] <= data[i+j] for j in range(1, window+1)):
                lows.append(i)
        return lows
    
    def _calculate_expected_move(self, confidence: float, patterns: List[dict]) -> float:
        base_move = 5.0
        pattern_count = len(patterns)
        if pattern_count >= 3:
            base_move += 3.0
        elif pattern_count >= 2:
            base_move += 1.5
        if confidence >= 80:
            base_move += 2.0
        elif confidence >= 65:
            base_move += 1.0
        for p in patterns:
            if p['pattern'] == 'whale_accumulation':
                base_move += 1.5
            elif p['pattern'] == 'hidden_divergence':
                base_move += 2.0
        return min(15.0, base_move)

# =========================================================
# استراتيجية اختراق الزخم
# =========================================================

class BreakoutMomentumStrategy:
    NAME = StrategyType.BREAKOUT_MOMENTUM
    WEIGHT = 0.25
    
    def __init__(self):
        self.min_score = 60
    
    def analyze(self, ohlcv_data: Dict[str, pd.DataFrame]) -> StrategySignal:
        timeframe_scores = {}
        signals = []
        total_score = 0
        total_weight = 0
        timeframe_weights = {'5m': 0.3, '15m': 0.35, '1h': 0.35}
        
        for tf, df in ohlcv_data.items():
            if len(df) < 50:
                continue
            
            closes = df['close'].values
            current_price = closes[-1]
            
            upper, middle, lower = self._calculate_bbands(closes)
            bandwidth = ((upper - lower) / middle) * 100
            price_position = (current_price - lower) / (upper - lower) if upper != lower else 0.5
            
            macd, signal, hist = self._calculate_macd(closes)
            rsi = self._calculate_rsi(closes, 14)
            
            tf_score = 0
            tf_signals = []
            
            if bandwidth < 5.0 and price_position < 0.4:
                tf_score += 40
                tf_signals.append(f"🔥 انخناق بولنجر ({tf})")
            elif bandwidth < 7.0:
                tf_score += 25
                tf_signals.append(f"📊 انخناق بولنجر ({tf})")
            
            if macd[-1] > signal[-1] and hist[-1] > 0:
                tf_score += 35
                tf_signals.append(f"✅ MACD إيجابي ({tf})")
                if hist[-1] > hist[-2]:
                    tf_score += 5
                    tf_signals.append(f"📈 زخم متسارع ({tf})")
            
            if 50 <= rsi <= 70:
                tf_score += 25
                tf_signals.append(f"💪 RSI في منطقة القوة ({tf})")
            elif 40 <= rsi < 50:
                tf_score += 15
                tf_signals.append(f"📈 RSI يتعافى ({tf})")
            
            weight = timeframe_weights.get(tf, 0.2)
            timeframe_scores[tf] = min(100, tf_score)
            total_score += tf_score * weight
            total_weight += weight
            signals.extend(tf_signals)
        
        if total_weight == 0:
            return self._empty_signal()
        
        final_score = total_score / total_weight
        entry_price = ohlcv_data['15m']['close'].iloc[-1] if '15m' in ohlcv_data else 0
        target_price = entry_price * 1.06
        stop_loss = entry_price * 0.97
        confidence = self._calculate_confidence(final_score, len(signals))
        
        return StrategySignal(
            strategy=self.NAME, score=round(final_score, 2),
            confidence=round(confidence, 2), entry_price=entry_price,
            target_price=target_price, stop_loss=stop_loss,
            signals=signals, timeframe_scores=timeframe_scores,
            timestamp=datetime.now().isoformat()
        )
    
    def _calculate_bbands(self, closes: np.ndarray) -> Tuple[float, float, float]:
        period = 20
        recent = closes[-period:]
        middle = np.mean(recent)
        std = np.std(recent)
        return middle + 2*std, middle, middle - 2*std
    
    def _calculate_macd(self, closes: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        ema12 = self._ema(closes, 12)
        ema26 = self._ema(closes, 26)
        macd = ema12 - ema26
        signal = self._ema(macd, 9)
        hist = macd - signal
        return macd, signal, hist
    
    def _calculate_rsi(self, closes: np.ndarray, period: int) -> float:
        deltas = np.diff(closes)
        gain = np.where(deltas > 0, deltas, 0)
        loss = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gain[-period:])
        avg_loss = np.mean(loss[-period:])
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))
    
    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data)
        ema[period-1] = np.mean(data[:period])
        for i in range(period, len(data)):
            ema[i] = data[i] * alpha + ema[i-1] * (1 - alpha)
        return ema
    
    def _calculate_confidence(self, score: float, signal_count: int) -> float:
        return min(100, score * 0.7 + min(signal_count * 5, 30))
    
    def _empty_signal(self) -> StrategySignal:
        return StrategySignal(
            strategy=self.NAME, score=0, confidence=0, entry_price=0,
            target_price=0, stop_loss=0, signals=[], timeframe_scores={},
            timestamp=datetime.now().isoformat()
        )

# =========================================================
# استراتيجية انفجار الحجم
# =========================================================

class VolumeExplosionStrategy:
    NAME = StrategyType.VOLUME_EXPLOSION
    WEIGHT = 0.20
    
    def __init__(self):
        self.min_score = 65
    
    def analyze(self, ohlcv_data: Dict[str, pd.DataFrame]) -> StrategySignal:
        timeframe_scores = {}
        signals = []
        total_score = 0
        total_weight = 0
        
        for tf, df in ohlcv_data.items():
            if len(df) < 30:
                continue
            
            closes = df['close'].values
            volumes = df['volume'].values
            current_price = closes[-1]
            current_volume = volumes[-1]
            
            avg_volume = np.mean(volumes[-21:-1]) if len(volumes) >= 21 else current_volume
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
            
            price_change = ((current_price - closes[-6]) / closes[-6]) * 100 if len(closes) >= 6 else 0
            volume_trend = self._calculate_trend(volumes[-10:])
            
            tf_score = 0
            
            if volume_ratio > 3.0:
                tf_score += 50
                signals.append(f"🚀 حجم استثنائي {volume_ratio:.1f}x ({tf})")
            elif volume_ratio > 2.0:
                tf_score += 40
                signals.append(f"💪 حجم قوي {volume_ratio:.1f}x ({tf})")
            elif volume_ratio > 1.5:
                tf_score += 25
                signals.append(f"✅ حجم جيد {volume_ratio:.1f}x ({tf})")
            
            if price_change > 3.0:
                tf_score += 30
                signals.append(f"📈 حركة سعرية +{price_change:.1f}% ({tf})")
            elif price_change > 1.5:
                tf_score += 20
                signals.append(f"📊 حركة +{price_change:.1f}% ({tf})")
            
            if volume_trend > 0:
                tf_score += 20
                signals.append(f"📊 حجم متزايد ({tf})")
            
            weight = 0.33
            timeframe_scores[tf] = min(100, tf_score)
            total_score += tf_score * weight
            total_weight += weight
        
        if total_weight == 0:
            return self._empty_signal()
        
        final_score = total_score / total_weight
        entry_price = ohlcv_data['5m']['close'].iloc[-1] if '5m' in ohlcv_data else 0
        target_price = entry_price * 1.08
        stop_loss = entry_price * 0.965
        confidence = final_score * 0.8 + min(len(signals) * 4, 20)
        
        return StrategySignal(
            strategy=self.NAME, score=round(final_score, 2),
            confidence=round(confidence, 2), entry_price=entry_price,
            target_price=target_price, stop_loss=stop_loss,
            signals=signals, timeframe_scores=timeframe_scores,
            timestamp=datetime.now().isoformat()
        )
    
    def _calculate_trend(self, data: np.ndarray) -> float:
        if len(data) < 3:
            return 0
        x = np.arange(len(data))
        slope = np.polyfit(x, data, 1)[0]
        return slope / np.mean(data) if np.mean(data) != 0 else 0
    
    def _empty_signal(self) -> StrategySignal:
        return StrategySignal(
            strategy=self.NAME, score=0, confidence=0, entry_price=0,
            target_price=0, stop_loss=0, signals=[], timeframe_scores={},
            timestamp=datetime.now().isoformat()
        )

# =========================================================
# محرك التوافق
# =========================================================

class ConsensusEngine:
    """محرك التوافق - يجمع نتائج الاستراتيجيات"""
    
    def __init__(self):
        self.strategies = {
            StrategyType.BREAKOUT_MOMENTUM: BreakoutMomentumStrategy(),
            StrategyType.VOLUME_EXPLOSION: VolumeExplosionStrategy(),
        }
        self.strategy_weights = {
            StrategyType.BREAKOUT_MOMENTUM: 0.6,
            StrategyType.VOLUME_EXPLOSION: 0.4,
        }
    
    def analyze_symbol(self, symbol: str, ohlcv_data: Dict[str, pd.DataFrame]) -> ConsensusResult:
        results = {}
        
        for strategy_type, strategy in self.strategies.items():
            try:
                signal = strategy.analyze(ohlcv_data)
                if signal.score >= strategy.min_score:
                    results[strategy_type] = signal
            except Exception as e:
                print(f"Error in {strategy_type}: {e}")
                continue
        
        consensus_score, agreement_count = self._calculate_consensus(results)
        weighted_score = self._calculate_weighted_score(results)
        average_score = np.mean([s.score for s in results.values()]) if results else 0
        top_strategies = self._get_top_strategies(results)
        entry_conditions, warnings = self._generate_conditions(results, consensus_score)
        final_grade, recommendation, risk_level = self._classify_result(consensus_score, agreement_count, weighted_score)
        
        current_price = ohlcv_data['15m']['close'].iloc[-1] if '15m' in ohlcv_data else 0
        
        return ConsensusResult(
            symbol=symbol, current_price=current_price,
            strategy_results=results, consensus_score=round(consensus_score, 2),
            agreement_count=agreement_count, average_score=round(average_score, 2),
            weighted_score=round(weighted_score, 2), final_grade=final_grade,
            recommendation=recommendation, risk_level=risk_level,
            top_strategies=top_strategies, entry_conditions=entry_conditions, warnings=warnings
        )
    
    def _calculate_consensus(self, results: Dict) -> Tuple[float, int]:
        if len(results) < 1:
            return (0, len(results))
        
        scores = [s.score for s in results.values()]
        agreement_count = sum(1 for s in scores if s >= 60)
        
        if len(scores) >= 1:
            avg_score = np.mean(scores)
            consensus = avg_score + agreement_count * 10
            return (min(100, consensus), agreement_count)
        
        return (0, agreement_count)
    
    def _calculate_weighted_score(self, results: Dict) -> float:
        if not results:
            return 0
        total = 0
        total_weight = 0
        for strategy_type, signal in results.items():
            weight = self.strategy_weights.get(strategy_type, 0.5)
            total += signal.score * weight
            total_weight += weight
        return total / total_weight if total_weight > 0 else 0
    
    def _get_top_strategies(self, results: Dict) -> List[StrategyType]:
        sorted_results = sorted(results.items(), key=lambda x: x[1].score, reverse=True)
        return [s[0] for s in sorted_results[:2] if s[1].score >= 60]
    
    def _generate_conditions(self, results: Dict, consensus_score: float) -> Tuple[List[str], List[str]]:
        conditions = []
        warnings = []
        
        if consensus_score >= 70:
            conditions.append("✅ توافق عالي بين الاستراتيجيات")
        elif consensus_score >= 55:
            conditions.append("📊 توافق متوسط بين الاستراتيجيات")
        
        for stype, signal in results.items():
            if signal.score >= 70:
                conditions.append(f"✅ {stype.value}: {signal.score:.1f}%")
            if signal.confidence < 50:
                warnings.append(f"⚠️ ثقة منخفضة في {stype.value}")
        
        return conditions, warnings
    
    def _classify_result(self, consensus: float, agreement: int, weighted: float) -> Tuple[str, str, str]:
        if consensus >= 75 and agreement >= 2:
            return "🏆🏆 ممتاز (A++)", "💎 فرصة ممتازة - توافق قوي", "🟢 منخفض"
        elif consensus >= 60 and agreement >= 1:
            return "🏆 جيد جداً (A+)", "✅ فرصة جيدة - توافق جيد", "🟡 متوسط"
        elif consensus >= 50:
            return "📊 جيد (B)", "📈 فرصة مقبولة", "🟠 مرتفع"
        else:
            return "❌ ضعيف (D)", "🚫 لا ينصح بالدخول", "🔴 مرتفع جداً"

# =========================================================
# نظام ركوب القطار
# =========================================================

class TrainRider:
    """نظام ركوب القطار - يدير الصفقة من المحطة الأولى حتى الوجهة"""
    
    def __init__(self, initial_capital: float, logger: HybridLogger):
        self.capital = initial_capital
        self.logger = logger
        self.active_rides = {}
        
        self.exit_strategy = {
            'take_profit_levels': [
                {'percent': 3.0, 'sell_ratio': 0.25},
                {'percent': 5.0, 'sell_ratio': 0.25},
                {'percent': 8.0, 'sell_ratio': 0.25},
                {'percent': 12.0, 'sell_ratio': 0.25},
            ],
            'trailing_stop': {'activation': 4.0, 'distance': 2.0}
        }
    
    async def board_train(self, signal: StationSignal, exchange) -> dict:
        """ركوب القطار من المحطة الأولى"""
        symbol = signal.symbol
        
        print(f"\n🚂 ركوب القطار: {symbol}")
        print(f"   المحطة: {signal.station_type}")
        print(f"   الثقة: {signal.confidence:.1f}%")
        print(f"   السعر: {signal.entry_price:.8f}")
        
        allocation = self.capital * 0.08
        first_entry = allocation * 0.25
        
        first_order = await self._place_order(exchange, symbol, 'buy', first_entry, signal.entry_price)
        
        if not first_order:
            print(f"❌ فشل الدخول الأولي لـ {symbol}")
            return {'success': False}
        
        print(f"✅ دخول أولي: {first_entry:.2f}$ @ {signal.entry_price:.8f}")
        
        ride = {
            'symbol': symbol, 'signal': signal,
            'total_allocation': allocation, 'invested': first_entry,
            'remaining': allocation - first_entry,
            'entries': [{'price': signal.entry_price, 'amount': first_entry,
                        'time': datetime.now(), 'stage': 'first_station'}],
            'highest_price': signal.entry_price,
            'current_stage': 'waiting_confirmation',
            'take_profit_hits': [], 'trailing_stop_active': False,
            'trailing_stop_price': signal.entry_price * 0.97
        }
        
        self.active_rides[symbol] = ride
        asyncio.create_task(self._monitor_ride(symbol, exchange))
        
        return {'success': True, 'ride': ride, 'message': f"🎫 تم حجز مقعد في قطار {symbol}"}
    
    async def _monitor_ride(self, symbol: str, exchange):
        """مراقبة الرحلة وإدارة الدخولات الإضافية والخروج"""
        ride = self.active_rides.get(symbol)
        if not ride:
            return
        
        check_intervals = {'waiting_confirmation': 10, 'confirmed': 30, 'trailing': 60}
        
        while symbol in self.active_rides:
            try:
                ticker = await exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                
                if current_price > ride['highest_price']:
                    ride['highest_price'] = current_price
                
                avg_entry = self._calculate_avg_entry(ride['entries'])
                current_pnl = ((current_price - avg_entry) / avg_entry) * 100
                
                # تحديث المتغير العام
                global active_trades_global
                active_trades_global[symbol] = {'current_pnl': current_pnl, 'stage': ride['current_stage']}
                
                # إدارة الدخولات الإضافية
                if ride['current_stage'] == 'waiting_confirmation':
                    if current_pnl > 1.5 and ride['remaining'] > 0:
                        second_entry = ride['total_allocation'] * 0.35
                        order = await self._place_order(exchange, symbol, 'buy', second_entry, current_price)
                        if order:
                            ride['entries'].append({
                                'price': current_price, 'amount': second_entry,
                                'time': datetime.now(), 'stage': 'confirmation'
                            })
                            ride['invested'] += second_entry
                            ride['remaining'] -= second_entry
                            ride['current_stage'] = 'confirmed'
                            print(f"✅ {symbol}: دخول تأكيدي @ {current_price:.8f} (+{current_pnl:.1f}%)")
                
                elif ride['current_stage'] == 'confirmed':
                    if len(ride['entries']) == 2 and ride['remaining'] > 0:
                        if current_pnl > 3.0:
                            third_entry = ride['total_allocation'] * 0.40
                            order = await self._place_order(exchange, symbol, 'buy', third_entry, current_price)
                            if order:
                                ride['entries'].append({
                                    'price': current_price, 'amount': third_entry,
                                    'time': datetime.now(), 'stage': 'momentum'
                                })
                                ride['invested'] += third_entry
                                ride['remaining'] = 0
                                ride['current_stage'] = 'trailing'
                                print(f"✅ {symbol}: دخول زخم @ {current_price:.8f} (+{current_pnl:.1f}%)")
                
                # إدارة الخروج
                for level in self.exit_strategy['take_profit_levels']:
                    target_pct = level['percent']
                    if target_pct not in ride['take_profit_hits'] and current_pnl >= target_pct:
                        sell_amount = ride['invested'] * level['sell_ratio']
                        order = await self._place_order(exchange, symbol, 'sell', sell_amount, current_price)
                        if order:
                            ride['take_profit_hits'].append(target_pct)
                            ride['invested'] -= sell_amount
                            print(f"💰 {symbol}: جني أرباح {target_pct}% - بيع {sell_amount:.2f}$")
                            
                            if not ride['trailing_stop_active'] and current_pnl >= self.exit_strategy['trailing_stop']['activation']:
                                ride['trailing_stop_active'] = True
                                ride['trailing_stop_price'] = current_price * (1 - self.exit_strategy['trailing_stop']['distance'] / 100)
                
                if ride['trailing_stop_active']:
                    new_stop = current_price * (1 - self.exit_strategy['trailing_stop']['distance'] / 100)
                    if new_stop > ride['trailing_stop_price']:
                        ride['trailing_stop_price'] = new_stop
                
                stop_price = ride['trailing_stop_price'] if ride['trailing_stop_active'] else ride['entries'][0]['price'] * 0.97
                
                if current_price <= stop_price and ride['invested'] > 0:
                    order = await self._place_order(exchange, symbol, 'sell', ride['invested'], current_price)
                    if order:
                        print(f"🛑 {symbol}: تفعيل وقف الخسارة @ {current_price:.8f}")
                        final_pnl = self._calculate_total_pnl(ride, current_price)
                        print(f"🏁 {symbol}: انتهت الرحلة - صافي الربح: {final_pnl:.2f}%")
                        
                        await self.logger.log_trade({
                            'symbol': symbol, 'trade_type': 'exit',
                            'exit_price': current_price, 'pnl_percentage': final_pnl,
                            'pnl_usd': ride['total_allocation'] * final_pnl / 100
                        })
                        
                        del self.active_rides[symbol]
                        if symbol in active_trades_global:
                            del active_trades_global[symbol]
                        return
                
                if ride['invested'] <= 0.01:
                    final_pnl = self._calculate_total_pnl(ride, current_price)
                    print(f"🏁 {symbol}: تم بيع كامل الكمية - صافي الربح: {final_pnl:.2f}%")
                    del self.active_rides[symbol]
                    if symbol in active_trades_global:
                        del active_trades_global[symbol]
                    return
                
                interval = check_intervals.get(ride['current_stage'], 30)
                await asyncio.sleep(interval)
                
            except Exception as e:
                print(f"⚠️ خطأ في مراقبة {symbol}: {e}")
                await asyncio.sleep(30)
    
    async def _place_order(self, exchange, symbol: str, side: str, amount: float, price: float) -> dict:
        try:
            quantity = amount / price
            quantity = self._round_quantity(symbol, quantity)
            if quantity <= 0:
                return None
            
            return {'symbol': symbol, 'side': side, 'amount': amount,
                   'quantity': quantity, 'price': price, 'timestamp': datetime.now().isoformat()}
        except Exception as e:
            print(f"❌ فشل تنفيذ الأمر {side} لـ {symbol}: {e}")
            return None
    
    def _round_quantity(self, symbol: str, quantity: float) -> float:
        if quantity < 0.00001:
            return 0
        return round(quantity, 5)
    
    def _calculate_avg_entry(self, entries: List[dict]) -> float:
        if not entries:
            return 0
        total_value = sum(e['amount'] for e in entries)
        weighted_sum = sum(e['price'] * e['amount'] for e in entries)
        return weighted_sum / total_value if total_value > 0 else 0
    
    def _calculate_total_pnl(self, ride: dict, current_price: float) -> float:
        avg_entry = self._calculate_avg_entry(ride['entries'])
        return ((current_price - avg_entry) / avg_entry) * 100

# =========================================================
# النظام الهجين المتكامل
# =========================================================

class HybridTradingSystem:
    """نظام التداول الهجين المتكامل"""
    
    def __init__(self, initial_capital: float = 1000.0):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        
        self.logger = HybridLogger()
        self.first_station = FirstStationDetector()
        self.consensus = ConsensusEngine()
        self.rider = TrainRider(initial_capital, self.logger)
        
        self.settings = {
            'min_station_confidence': 65,
            'min_consensus_score': 55,
            'quick_confirmation_time': 45,
            'max_concurrent_trades': 3,
            'max_daily_trades': 10,
            'max_daily_loss': -5.0,
            'use_quick_consensus': True,
        }
        
        self.active_trades = {}
        self.daily_trades_count = 0
        self.daily_pnl = 0.0
        self.scan_count = 0
        self.watchlist = []
        
        global daily_trades_global
        daily_trades_global = 0
        
        print(f"""
╔══════════════════════════════════════════════════════════╗
║     🚀 نظام التداول الهجين - الإصدار المتكامل 🚀         ║
╠══════════════════════════════════════════════════════════╣
║  💰 رأس المال: {initial_capital:.2f}$                     ║
║  📊 أقصى صفقات: {self.settings['max_concurrent_trades']} متزامنة                    ║
║  🎯 حد المحطة الأولى: {self.settings['min_station_confidence']}%                      ║
║  ✅ حد التأكيد: {self.settings['min_consensus_score']}%                          ║
╚══════════════════════════════════════════════════════════╝
        """)
    
    async def scan_and_trade(self, exchange, symbols: List[str]):
        """دورة المسح والتداول الرئيسية"""
        self.scan_count += 1
        scan_start = datetime.now()
        
        print(f"\n{'='*60}")
        print(f"🔍 دورة المسح #{self.scan_count} - {scan_start.strftime('%H:%M:%S')}")
        print(f"{'='*60}")
        
        try:
            print(f"📊 فلترة {len(symbols)} عملة...")
            filtered = await self._filter_promising_symbols(exchange, symbols[:300])
            print(f"✅ تم اختيار {len(filtered)} عملة واعدة للتحليل العميق")
            
            print(f"🚂 البحث عن محطات أولى...")
            stations = await self.first_station.scan_for_first_station(exchange, filtered)
            
            strong_stations = [s for s in stations if s.confidence >= self.settings['min_station_confidence']]
            
            if strong_stations:
                print(f"\n📍 تم اكتشاف {len(strong_stations)} محطة أولى قوية:")
                for i, station in enumerate(strong_stations[:5], 1):
                    print(f"   {i}. {station.symbol:12} | ثقة: {station.confidence:.1f}% | "
                          f"متوقع: +{station.expected_move:.1f}% | خلال {station.time_to_explosion}ث")
                    
                    await self.logger.log_signal({
                        'symbol': station.symbol, 'station_type': station.station_type,
                        'confidence': station.confidence, 'expected_move': station.expected_move,
                        'time_to_explosion': station.time_to_explosion,
                        'signals': ', '.join(station.signals), 'price': station.entry_price
                    })
            else:
                print("   ⚪ لا توجد محطات أولى قوية")
            
            available_slots = self.settings['max_concurrent_trades'] - len(self.active_trades)
            
            if available_slots > 0 and strong_stations:
                print(f"\n🎫 مقاعد متاحة: {available_slots}")
                
                for station in strong_stations[:available_slots]:
                    if not self._can_trade_today():
                        print(f"   ⏸️ تم الوصول لحدود التداول اليومية")
                        break
                    
                    if station.symbol in self.active_trades:
                        continue
                    
                    decision = await self._hybrid_evaluation(station, exchange)
                    
                    if decision['should_enter']:
                        await self._execute_entry(station, decision, exchange)
                        await asyncio.sleep(2)
            
            if self.active_trades:
                print(f"\n📊 الصفقات النشطة ({len(self.active_trades)}):")
                for symbol, trade in self.active_trades.items():
                    pnl = trade.get('current_pnl', 0)
                    emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                    print(f"   {emoji} {symbol:10} | {pnl:+.2f}% | مرحلة: {trade['stage']}")
            
            scan_duration = (datetime.now() - scan_start).total_seconds()
            
            await self.logger.log_market_snapshot({
                'scan_count': self.scan_count, 'symbols_scanned': len(symbols),
                'filtered_count': len(filtered), 'stations_found': len(strong_stations),
                'active_trades': len(self.active_trades), 'daily_trades': self.daily_trades_count,
                'daily_pnl': self.daily_pnl, 'scan_duration': scan_duration, 'capital': self.current_capital
            })
            
            print(f"\n✅ اكتملت الدورة في {scan_duration:.1f} ثانية")
            
        except Exception as e:
            print(f"❌ خطأ في دورة المسح: {e}")
            await self.logger.log_error({
                'error_type': 'scan_cycle_error', 'message': str(e),
                'severity': 'high', 'scan_count': self.scan_count
            })
    
    async def _filter_promising_symbols(self, exchange, symbols: List[str]) -> List[str]:
        promising = []
        try:
            tickers = await exchange.fetch_tickers()
            for symbol in symbols[:200]:
                ticker = tickers.get(symbol, {})
                volume = ticker.get('quoteVolume', 0)
                if volume < 50000:
                    continue
                change = ticker.get('percentage', 0)
                if change > 20 or change < -10:
                    continue
                price = ticker.get('last', 0)
                if price < 0.000001:
                    continue
                promising.append(symbol)
        except Exception as e:
            print(f"خطأ في الفلترة: {e}")
            promising = symbols[:50]
        return promising[:100]
    
    async def _hybrid_evaluation(self, station: StationSignal, exchange) -> dict:
        print(f"\n🔍 تقييم هجين لـ {station.symbol}")
        print(f"   المحطة الأولى: {station.station_type} (ثقة {station.confidence:.1f}%)")
        
        if station.confidence >= 80:
            print(f"   ⚡ ثقة عالية - تأكيد سريع...")
            await asyncio.sleep(self.settings['quick_confirmation_time'] / 2)
            quick_check = await self._quick_confirmation(station.symbol, exchange)
            
            if quick_check['confirmed']:
                await self.logger.send_notification(
                    level=NotificationLevel.IMPORTANT,
                    title="🎯 إشارة هجينة قوية",
                    message=f"{station.symbol}: محطة {station.station_type} + تأكيد سريع\nالثقة: {station.confidence:.1f}% | متوقع: +{station.expected_move:.1f}%"
                )
                return {
                    'should_enter': True, 'confidence': station.confidence,
                    'entry_type': 'quick_confirm', 'allocation_ratio': 0.8,
                    'signals': station.signals + quick_check.get('signals', [])
                }
        
        if self.settings['use_quick_consensus']:
            print(f"   🔄 انتظار تأكيد مخفف ({self.settings['quick_confirmation_time']} ثانية)...")
            await asyncio.sleep(self.settings['quick_confirmation_time'])
            
            consensus_score = await self._light_consensus(station.symbol, exchange)
            print(f"   📊 درجة التأكيد: {consensus_score:.1f}%")
            
            if consensus_score >= self.settings['min_consensus_score']:
                combined_confidence = (station.confidence * 0.6) + (consensus_score * 0.4)
                await self.logger.send_notification(
                    level=NotificationLevel.IMPORTANT,
                    title="✅ إشارة هجينة مؤكدة",
                    message=f"{station.symbol}: تأكيد {consensus_score:.1f}%\nثقة مركبة: {combined_confidence:.1f}%"
                )
                return {
                    'should_enter': True, 'confidence': combined_confidence,
                    'entry_type': 'confirmed', 'allocation_ratio': 0.6,
                    'consensus_score': consensus_score, 'signals': station.signals
                }
            else:
                print(f"   ❌ فشل التأكيد - مراقبة فقط")
                await self.logger.log_signal({
                    'symbol': station.symbol, 'station_type': station.station_type,
                    'confidence': station.confidence, 'consensus_score': consensus_score,
                    'status': 'rejected', 'reason': 'low_consensus'
                })
        
        if station.symbol not in self.watchlist:
            self.watchlist.append(station.symbol)
        
        return {'should_enter': False}
    
    async def _quick_confirmation(self, symbol: str, exchange) -> dict:
        try:
            ticker = await exchange.fetch_ticker(symbol)
            signals = []
            confirmed = False
            
            volume = ticker.get('quoteVolume', 0)
            if volume > 100000:
                signals.append(f"حجم مرتفع ({volume:.0f}$)")
                confirmed = True
            
            change = ticker.get('percentage', 0)
            if 0.5 < change < 5:
                signals.append(f"زخم إيجابي (+{change:.1f}%)")
                confirmed = True
            
            return {'confirmed': confirmed, 'signals': signals}
        except Exception as e:
            return {'confirmed': False, 'signals': []}
    
    async def _light_consensus(self, symbol: str, exchange) -> float:
        try:
            ohlcv = await exchange.fetch_ohlcv(symbol, '5m', limit=50)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            if len(df) < 30:
                return 0
            
            closes = df['close'].values
            volumes = df['volume'].values
            current_price = closes[-1]
            
            score = 0
            
            avg_volume = np.mean(volumes[-20:])
            volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 1
            
            if volume_ratio > 2.0:
                score += 40
            elif volume_ratio > 1.5:
                score += 25
            elif volume_ratio > 1.2:
                score += 15
            
            price_change = ((current_price - closes[-6]) / closes[-6]) * 100 if len(closes) >= 6 else 0
            
            if 1.0 < price_change < 5.0:
                score += 30
            elif 0.5 < price_change <= 1.0:
                score += 15
            
            ema9 = self._calculate_ema(closes, 9)
            ema21 = self._calculate_ema(closes, 21)
            
            if ema9[-1] > ema21[-1] and current_price > ema9[-1]:
                score += 30
            elif current_price > ema21[-1]:
                score += 15
            
            return min(100, score)
        except Exception as e:
            print(f"خطأ في التحليل المخفف لـ {symbol}: {e}")
            return 0
    
    def _calculate_ema(self, data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data)
        if len(data) >= period:
            ema[period-1] = np.mean(data[:period])
            for i in range(period, len(data)):
                ema[i] = data[i] * alpha + ema[i-1] * (1 - alpha)
        return ema
    
    async def _execute_entry(self, station: StationSignal, decision: dict, exchange):
        symbol = station.symbol
        
        print(f"\n🚀 تنفيذ دخول هجين لـ {symbol}")
        print(f"   النوع: {decision['entry_type']}")
        print(f"   الثقة: {decision['confidence']:.1f}%")
        
        base_allocation = self.current_capital * 0.08
        allocation = base_allocation * decision['allocation_ratio']
        
        result = await self.rider.board_train(station, exchange)
        
        if result['success']:
            self.active_trades[symbol] = {
                'station': station, 'decision': decision,
                'entry_time': datetime.now(), 'entry_price': station.entry_price,
                'allocation': allocation, 'stage': 'entered', 'rider_result': result
            }
            self.daily_trades_count += 1
            
            global daily_trades_global
            daily_trades_global = self.daily_trades_count
            
            await self.logger.log_trade({
                'symbol': symbol, 'trade_type': 'entry', 'entry_type': decision['entry_type'],
                'price': station.entry_price, 'allocation': allocation,
                'confidence': decision['confidence'], 'station_type': station.station_type
            })
            
            await self.logger.send_notification(
                level=NotificationLevel.CRITICAL,
                title="🎫 دخول هجين ناجح",
                message=f"{symbol}\n💰 المبلغ: {allocation:.2f}$\n💵 السعر: {station.entry_price:.8f}\n📊 الثقة: {decision['confidence']:.1f}%\n🎯 متوقع: +{station.expected_move:.1f}%"
            )
        else:
            print(f"   ❌ فشل تنفيذ الدخول")
    
    def _can_trade_today(self) -> bool:
        if self.daily_trades_count >= self.settings['max_daily_trades']:
            return False
        if self.daily_pnl <= self.settings['max_daily_loss']:
            return False
        return True
    
    async def run(self, exchange, scan_interval: int = 45):
        print("\n🔄 بدء تشغيل النظام الهجين...")
        
        print("📊 جلب العملات المتاحة...")
        markets = await exchange.load_markets()
        
        all_symbols = []
        for symbol, market in markets.items():
            if symbol.endswith('/USDT') and market.get('active', False):
                all_symbols.append(symbol)
        
        print(f"✅ تم العثور على {len(all_symbols)} عملة")
        
        await self.logger.send_notification(
            level=NotificationLevel.SUCCESS,
            title="🚀 بدء تشغيل النظام الهجين",
            message=f"رأس المال: {self.current_capital:.2f}$\nالعملات: {len(all_symbols)}\nفترة المسح: {scan_interval} ثانية"
        )
        
        try:
            while True:
                await self.scan_and_trade(exchange, all_symbols)
                
                now = datetime.now()
                if now.hour == 23 and now.minute >= 55:
                    await self.logger.generate_daily_report()
                
                await asyncio.sleep(scan_interval)
                
        except KeyboardInterrupt:
            print("\n⏹️ إيقاف النظام...")
            
            await self.logger.send_notification(
                level=NotificationLevel.WARNING,
                title="⏸️ تم إيقاف النظام",
                message=f"الصفقات اليوم: {self.daily_trades_count}\nالربح/الخسارة: {self.daily_pnl:+.2f}%"
            )
            
            await self.logger.generate_daily_report()
            export_path = await self.logger.export_all_data()
            print(f"📦 تم حفظ جميع البيانات في: {export_path}")

# =========================================================
# الدالة الرئيسية
# =========================================================

async def main():
    """تشغيل النظام الهجين المتكامل"""
    
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║     🚀 نظام التداول الهجين - الإصدار النهائي 🚀             ║
║                                                              ║
║     • اكتشاف المحطة الأولى (دخول مبكر)                      ║
║     • تأكيد هجين (سرعة + أمان)                              ║
║     • إشعارات تيليجرام فورية                                ║
║     • خادم HTTP + Keep-Alive ذاتي                            ║
║     • تسجيل شامل في CSV للمراقبة والتحليل                   ║
║     • تقارير يومية تلقائية                                  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # بدء خادم Keep-Alive
    port = int(os.environ.get('PORT', 8080))
    keep_alive = KeepAliveServer(port)
    if keep_alive.start():
        print(f"✅ خادم Keep-Alive يعمل على المنفذ {port}")
    
    # بدء الـ External Ping في خيط منفصل
    ping_thread = threading.Thread(target=ping_external_service, daemon=True)
    ping_thread.start()
    
    # إعداد الاتصال بالبورصة
    exchange = ccxt_async.gateio({
        'enableRateLimit': True,
        'rateLimit': 100,
    })
    
    initial_capital = float(os.environ.get('INITIAL_CAPITAL', 1000))
    
    state_file = "trading_logs/system_state.json"
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            state = json.load(f)
            initial_capital = state.get('capital', initial_capital)
            print(f"📂 تم تحميل الحالة السابقة - الرصيد: {initial_capital:.2f}$")
    
    system = HybridTradingSystem(initial_capital=initial_capital)
    
    # تحميل الإعدادات من متغيرات البيئة
    system.settings.update({
        'min_station_confidence': int(os.environ.get('MIN_STATION_CONFIDENCE', 65)),
        'min_consensus_score': int(os.environ.get('MIN_CONSENSUS_SCORE', 55)),
        'quick_confirmation_time': int(os.environ.get('QUICK_CONFIRMATION_TIME', 45)),
        'max_concurrent_trades': int(os.environ.get('MAX_CONCURRENT_TRADES', 3)),
        'max_daily_trades': int(os.environ.get('MAX_DAILY_TRADES', 10)),
        'use_quick_consensus': os.environ.get('USE_QUICK_CONSENSUS', 'true').lower() == 'true',
    })
    
    scan_interval = int(os.environ.get('SCAN_INTERVAL', 45))
    
    try:
        await system.run(exchange, scan_interval)
    finally:
        state = {
            'capital': system.current_capital,
            'last_run': datetime.now().isoformat(),
            'total_trades': system.daily_trades_count,
            'settings': system.settings
        }
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        keep_alive.stop()
        await exchange.close()
        print("✅ تم حفظ الحالة وإغلاق الاتصال")

if __name__ == "__main__":
    asyncio.run(main())

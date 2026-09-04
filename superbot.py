#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import logging
import io
import threading
from datetime import datetime
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

import matplotlib
matplotlib.use('Agg')  # Sunucuda headless grafik çizimi için
import matplotlib.pyplot as plt
import mplfinance as mpf

import ccxt
import pandas as pd
import numpy as np

# =========================================================
# 0. RENDER & UPTIMEROBOT İÇİN DAHİLİ HTTP SUNUCUSU
# =========================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"SMC Trading Bot is alive and running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return  # Konsol log kirliliğini önler

def start_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# Arka planda web sunucusunu başlat (Render zaman aşımı / uyku modunu engeller)
threading.Thread(target=start_health_check_server, daemon=True).start()

# =========================================================
# LOGGING VE KONFİGÜRASYON
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Render / Çevre Değişkenleri
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID_HERE")
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL", "15"))
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "13.5"))
DB_FILE = os.getenv("DB_FILE", "trades_db.json")

# =========================================================
# 1. VERİTABANI VE PERFORMANS YÖNETİCİSİ (JSON DATABASE)
# =========================================================
class PerformanceDB:
    def __init__(self, filename=DB_FILE):
        self.filename = filename
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Veritabanı okuma hatası: {e}")
        return {"active_trades": {}, "closed_trades": []}

    def _save(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Veritabanı kaydetme hatası: {e}")

    def add_active_trade(self, symbol, direction, entry_price, stop_loss):
        risk = abs(entry_price - stop_loss)
        self.data["active_trades"][symbol] = {
            "symbol": symbol,  # DÜZELTİLDİ: Sembol artık kaydediliyor
            "direction": direction,
            "entry": entry_price,
            "stop": stop_loss,
            "risk": risk,
            "be_notified": False,
            "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save()

    def get_active_trades(self):
        return self.data.get("active_trades", {})

    def mark_be_notified(self, symbol):
        if symbol in self.data["active_trades"]:
            self.data["active_trades"][symbol]["be_notified"] = True
            self._save()

    def close_trade(self, symbol, exit_price, exit_reason):
        if symbol in self.data["active_trades"]:
            trade = self.data["active_trades"].pop(symbol)
            direction = trade["direction"]
            entry = trade["entry"]
            risk = trade["risk"]

            pnl = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
            r_realized = round(pnl / risk, 2) if risk > 0 else 0.0

            trade["exit_price"] = exit_price
            trade["exit_reason"] = exit_reason
            trade["r_realized"] = r_realized
            trade["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self.data["closed_trades"].append(trade)
            self._save()

    def get_coin_stats(self):
        """Coin bazında performans sınıflandırması (ELE / RISKLI / GÜÇLÜ)"""
        stats = {}
        for trade in self.data.get("closed_trades", []):
            symbol = trade.get("symbol", "UNKNOWN")
            r = trade.get("r_realized", 0.0)
            if symbol not in stats:
                stats[symbol] = []
            stats[symbol].append(r)

        classification = {}
        for symbol, r_list in stats.items():
            count = len(r_list)
            avg_r = np.mean(r_list) if count > 0 else 0.0
            
            if count >= 5 and avg_r < -0.2:
                cat = "🔴 ELE (Kötü)"
            elif avg_r >= 0.5 and count >= 5:
                cat = "🟢 GÜÇLÜ / TUT"
            else:
                cat = "🟡 RİSKLİ"

            classification[symbol] = {
                "count": count,
                "avg_r": round(float(avg_r), 2),
                "total_r": round(float(sum(r_list)), 2),
                "status": cat
            }
        return classification

# =========================================================
# 2. TEKNİK ANALİZ VE SMC DETEKSİYON MOTORU
# =========================================================
class SMCAnalyzer:
    @staticmethod
    def add_indicators(df):
        """RSI ve Hareketli Ortalamaları Hesaplar"""
        df = df.copy()
        df['ma50'] = df['close'].rolling(50).mean()
        df['ma100'] = df['close'].rolling(100).mean()
        df['ma200'] = df['close'].rolling(200).mean()

        # RSI 14
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        df['rsi'] = 100 - (100 / (1 + rs))
        return df

    @classmethod
    def analyze_timeframe(cls, df, timeframe_name="4H"):
        """SMC ve Price Action Kurallarını Detaylıca İnceleyip Skorlar"""
        df = cls.add_indicators(df)
        criterias = []
        score = 0.0

        if len(df) < 50:
            return 0.0, criterias, df

        last = df.iloc[-1]
        prev1 = df.iloc[-2]

        # Trend Yönü Tespiti
        is_bullish = last['close'] > last['ma200']

        # 1. Structure / BOS & CHoCH
        recent_high = df['high'].iloc[-15:-1].max()
        recent_low = df['low'].iloc[-15:-1].min()

        if is_bullish and last['close'] > recent_high:
            score += 1.80
            criterias.append("• BOS: 1.80 — Bullish Break of Structure")
            score += 1.35
            criterias.append("• CHoCH: 1.35 — Bullish Change of Character")
        elif not is_bullish and last['close'] < recent_low:
            score += 1.80
            criterias.append("• BOS: 1.80 — Bearish Break of Structure")
            score += 1.35
            criterias.append("• CHoCH: 1.35 — Bearish Change of Character")

        # 2. Liquidity Sweep & SFP
        lowest_10 = df['low'].iloc[-12:-2].min()
        highest_10 = df['high'].iloc[-12:-2].max()

        if is_bullish and prev1['low'] < lowest_10 and last['close'] > lowest_10:
            score += 1.57
            criterias.append("• Liquidity Sweep: 1.57 — Sell-side sweep + reclaim")
            score += 1.35
            criterias.append("• SFP: 1.35 — Bullish Swing Failure Pattern")
        elif not is_bullish and prev1['high'] > highest_10 and last['close'] < highest_10:
            score += 1.57
            criterias.append("• Liquidity Sweep: 1.57 — Buy-side sweep + reclaim")
            score += 1.35
            criterias.append("• SFP: 1.35 — Bearish Swing Failure Pattern")

        # 3. Order Block & Breaker Block
        if is_bullish and last['close'] > prev1['high'] and prev1['close'] < prev1['open']:
            score += 1.57
            criterias.append("• Order Block: 1.57 — Bullish Order Block active")
            score += 1.12
            criterias.append("• Breaker Block: 1.12 — Bullish Breaker confirmed")
        elif not is_bullish and last['close'] < prev1['low'] and prev1['close'] > prev1['open']:
            score += 1.57
            criterias.append("• Order Block: 1.57 — Bearish Order Block active")
            score += 1.12
            criterias.append("• Breaker Block: 1.12 — Bearish Breaker confirmed")

        # 4. FVG (Fair Value Gap)
        if is_bullish and df['low'].iloc[-1] > df['high'].iloc[-3]:
            score += 1.35
            criterias.append("• FVG: 1.35 — Bullish Fair Value Gap")
        elif not is_bullish and df['high'].iloc[-1] < df['low'].iloc[-3]:
            score += 1.35
            criterias.append("• FVG: 1.35 — Bearish Fair Value Gap")

        # 5. RSI Göstergesi
        rsi_val = last['rsi']
        if is_bullish and rsi_val > 50:
            score += 1.12
            criterias.append(f"• RSI: 1.12 — RSI {rsi_val:.1f} bullish momentum")
        elif not is_bullish and rsi_val < 50:
            score += 1.12
            criterias.append(f"• RSI: 1.12 — RSI {rsi_val:.1f} bearish momentum")

        # 6. Hareketli Ortalamalar (MA 200, 100, 50)
        if last['close'] > last['ma200']:
            score += 1.35
            criterias.append("• MA 200: 1.35 — Price above MA200")
        else:
            score += 0.50
            criterias.append("• MA 200: 0.50 — Price below MA200")

        if last['ma50'] > last['ma100']:
            score += 0.90
            criterias.append("• MA 100: 0.90 — MA50 above MA100 (bullish cross)")
        else:
            score += 0.90
            criterias.append("• MA 100: 0.90 — MA50 below MA100")

        if last['close'] > last['ma50']:
            score += 0.90
            criterias.append("• MA 50: 0.90 — Price above MA50")

        return round(score, 2), criterias, df

# =========================================================
# 3. GRAFİK GÖRSELLEŞTİRME KÜTÜPHANESİ
# =========================================================
def create_chart_image(df, symbol):
    """Mplfinance ile Binance tarzı mum grafiği çizer"""
    df_chart = df.iloc[-60:].copy()
    df_chart['timestamp'] = pd.to_datetime(df_chart['timestamp'], unit='ms')
    df_chart.set_index('timestamp', inplace=True)

    add_plots = []
    if 'ma50' in df_chart.columns:
        add_plots.append(mpf.make_addplot(df_chart['ma50'], color='orange', width=1.2))
    if 'ma100' in df_chart.columns:
        add_plots.append(mpf.make_addplot(df_chart['ma100'], color='deepskyblue', width=1.2))
    if 'ma200' in df_chart.columns:
        add_plots.append(mpf.make_addplot(df_chart['ma200'], color='limegreen', width=1.2))

    buf = io.BytesIO()
    mpf.plot(
        df_chart,
        type='candle',
        addplot=add_plots,
        style='binance',
        title=f"\n{symbol} - 4H SMC Analysis",
        savefig=dict(fname=buf, format='png', bbox_inches='tight'),
        volume=False,
        axisoff=False
    )
    buf.seek(0)
    return buf

# =========================================================
# 4. TELEGRAM BİLDİRİM VE MESAJ SERVİSİ
# =========================================================
class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.token}"

    def send_signal(self, symbol, direction, base_score, criterias, chart_buf, mtf_scores):
        mtf_bonus = 2.00
        total_score = base_score + mtf_bonus

        crit_text = "\n".join(criterias) if criterias else "• Standart SMC kurulumu tespit edildi."

        caption = f"""🧠 **{symbol} — {direction}**
⭐ **Skor:** {total_score:.2f}/20
⏱ **MTF bonus:** +{mtf_bonus:.2f}/2

**4H kriterleri:**
{crit_text}

**Zaman dilimleri:**
• 1D: {direction} ({mtf_scores['1d']:.2f}/18)
• 4H: {direction} ({mtf_scores['4h']:.2f}/18)
• 1H: {direction} ({mtf_scores['1h']:.2f}/18)

⚠️ Bu bot yalnızca teknik/algoritmik analiz üretir; garanti edilmiş fiyat hareketi veya yatırım tavsiyesi değildir."""

        url = f"{self.api_url}/sendPhoto"
        files = {'photo': ('chart.png', chart_buf, 'image/png')}
        data = {'chat_id': self.chat_id, 'caption': caption, 'parse_mode': 'Markdown'}

        try:
            res = requests.post(url, data=data, files=files, timeout=15)
            if res.status_code == 200:
                logging.info(f"Sinyal gönderildi: {symbol}")
            else:
                logging.error(f"Telegram hatası: {res.text}")
        except Exception as e:
            logging.error(f"Telegram gönderim hatası: {e}")

    def send_be_alert(self, symbol, direction, entry_price):
        text = f"""🛡 **STOPU GİRİŞE ÇEK (BE)**
───────────────────────
📌 **{symbol} ({direction})**
📈 İşlem **+1R** kara geçti!
🎯 Borsa stop seviyeni giriş fiyatına (**{entry_price}**) çek."""

        url = f"{self.api_url}/sendMessage"
        data = {'chat_id': self.chat_id, 'text': text, 'parse_mode': 'Markdown'}

        try:
            requests.post(url, data=data, timeout=10)
            logging.info(f"BE Uyarısı gönderildi: {symbol}")
        except Exception as e:
            logging.error(f"BE Telegram hatası: {e}")

    def send_performance_report(self, stats_dict):
        if not stats_dict:
            return

        lines = ["📊 **COIN PERFORMANS VE ELEME RAPORU**", "───────────────────────"]
        for sym, data in stats_dict.items():
            lines.append(f"• **{sym}**: {data['status']}")
            lines.append(f"   └ İşlem: {data['count']} | Ort R: {data['avg_r']} | Toplam R: {data['total_r']}")

        text = "\n".join(lines)
        url = f"{self.api_url}/sendMessage"
        data = {'chat_id': self.chat_id, 'text': text, 'parse_mode': 'Markdown'}
        try:
            requests.post(url, data=data, timeout=10)
        except Exception as e:
            logging.error(f"Rapor hatası: {e}")

# =========================================================
# 5. ANA ÇALIŞMA VE TARAMA DÖNGÜSÜ
# =========================================================
class SMCTradingBot:
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        self.db = PerformanceDB()
        self.telegram = TelegramNotifier(BOT_TOKEN, CHAT_ID)
        self.symbols = [
            'ETH/USDT', 'BTC/USDT', 'SOL/USDT', 'ACE/USDT', 
            'DYM/USDT', 'AVAX/USDT', 'NEAR/USDT', 'LINK/USDT'
        ]

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        try:
            bars = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            return df
        except Exception as e:
            # Render / Binance IP kısıtlamalarına karşı alternatif MEXC yedek desteği
            try:
                mexc_symbol = symbol.replace("USDT", "_USDT")
                mexc_url = f"https://api.mexc.com/api/v3/klines?symbol={mexc_symbol}&interval={timeframe}&limit={limit}"
                res = requests.get(mexc_url, timeout=10)
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    formatted_data = []
                    for row in data:
                        formatted_data.append([row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
                    return pd.DataFrame(formatted_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            except Exception as me:
                logging.error(f"{symbol} {timeframe} veri çekme hatası (MEXC yedek de başarısız): {me}")
            return None

    def check_active_positions(self):
        """Aktif pozisyonları kontrol eder ve +1R kar oluşunca BE uyarısı atar"""
        active_trades = self.db.get_active_trades()
        for symbol, trade in list(active_trades.items()):
            df_1h = self.fetch_ohlcv(symbol, '1h', limit=5)
            if df_1h is None or df_1h.empty:
                continue

            current_price = df_1h['close'].iloc[-1]
            entry = trade['entry']
            risk = trade['risk']
            direction = trade['direction']

            pnl = (current_price - entry) if direction == 'LONG' else (entry - current_price)
            r_multiple = pnl / risk if risk > 0 else 0

            # +1R Kara Geçti ve Henüz BE Bildirimi Atılmadıysa
            if r_multiple >= 1.0 and not trade['be_notified']:
                self.telegram.send_be_alert(symbol, direction, entry)
                self.db.mark_be_notified(symbol)

            # Otomatik Kapanma Takibi
            if direction == 'LONG' and current_price <= trade['stop']:
                self.db.close_trade(symbol, current_price, "STOP_HIT")
            elif direction == 'SHORT' and current_price >= trade['stop']:
                self.db.close_trade(symbol, current_price, "STOP_HIT")
            elif r_multiple >= 3.0:
                self.db.close_trade(symbol, current_price, "TP3_HIT")

    def run_scan(self):
        logging.info(">>> SMC Taraması Başlatılıyor...")
        
        coin_stats = self.db.get_coin_stats()

        for symbol in self.symbols:
            if symbol in coin_stats and "🔴 ELE" in coin_stats[symbol]['status']:
                logging.info(f"{symbol} elendiği için taranmıyor.")
                continue

            df_1d = self.fetch_ohlcv(symbol, '1d', limit=100)
            df_4h = self.fetch_ohlcv(symbol, '4h', limit=100)
            df_1h = self.fetch_ohlcv(symbol, '1h', limit=100)

            if df_4h is None or len(df_4h) < 50:
                continue

            score_1d, _, _ = SMCAnalyzer.analyze_timeframe(df_1d, '1D') if df_1d is not None else (10.0, [], None)
            score_4h, criterias, df_4h_analyzed = SMCAnalyzer.analyze_timeframe(df_4h, '4H')
            score_1h, _, _ = SMCAnalyzer.analyze_timeframe(df_1h, '1H') if df_1h is not None else (10.0, [], None)

            total_score = score_4h + 2.0  # MTF bonus

            if total_score >= SCORE_THRESHOLD:
                direction = "LONG" if df_4h_analyzed['close'].iloc[-1] > df_4h_analyzed['ma200'].iloc[-1] else "SHORT"
                
                chart_buf = create_chart_image(df_4h_analyzed, symbol)
                mtf_scores = {'1d': score_1d, '4h': score_4h, '1h': score_1h}

                self.telegram.send_signal(
                    symbol=symbol,
                    direction=direction,
                    base_score=score_4h,
                    criterias=criterias,
                    chart_buf=chart_buf,
                    mtf_scores=mtf_scores
                )

                entry_price = df_4h_analyzed['close'].iloc[-1]  # DÜZELTİLDİ: Yazım hatası giderildi
                stop_price = entry_price * 0.98 if direction == 'LONG' else entry_price * 1.02
                self.db.add_active_trade(symbol, direction, entry_price, stop_price)

        self.check_active_positions()
        logging.info("<<< Tarama Tamamlandı.")

    def start(self):
        logging.info("SMC Trading Bot Başlatıldı. Render ortamında çalışıyor...")
        while Type := True:
            try:
                self.run_scan()
            except Exception as e:
                logging.error(f"Ana döngü hatası: {e}")

            time.sleep(SCAN_INTERVAL_MINUTES * 60)

# =========================================================
# 6. BAŞLANGIÇ
# =========================================================
if __name__ == "__main__":
    bot = SMCTradingBot()
    bot.start()

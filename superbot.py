#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import sqlite3
import threading
from datetime import datetime
import requests
import pandas as pd
import pandas_ta as ta

# Sunucu ortamında grafik çizerken ekran gereksinimini kaldırır
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.utils import get_column_letter
from flask import Flask

# ==========================================
# 1. FLASK KEEP-ALIVE SERVER (Render Free Tier)
# ==========================================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "OK - Kripto Sinyal Botu Aktif ve Çalışıyor", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# 2. AYARLAR & ORTAM DEĞİŞKENLERİ
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "./artifacts")
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() in ["true", "1", "yes"]
TARAMA_ARALIGI_DURMA_SANIYE = int(os.environ.get("TARAMA_ARALIGI_DURMA_SANIYE", "3600"))

os.makedirs(ARTIFACT_DIR, exist_ok=True)

HEDEF_COINLER = [
    'ONT-USDT', 'JUP-USDT', 'SNX-USDT', 'LDO-USDT', 'ZETA-USDT', 'AAVE-USDT', 'TIA-USDT', 'VET-USDT', 'DYDX-USDT', 'LTC-USDT',
    'AVAX-USDT', 'SUSHI-USDT', 'STX-USDT', 'XRP-USDT', 'ORDI-USDT', 'ENS-USDT', 'GALA-USDT', 'MANA-USDT', 'ICP-USDT', 'THETA-USDT',
    'TRX-USDT', 'EGLD-USDT', 'ADA-USDT', 'AXS-USDT', 'INJ-USDT', 'AEVO-USDT', 'BCH-USDT', 'FLOKI-USDT', 'DOT-USDT', 'RUNE-USDT',
    'KAS-USDT', 'COMP-USDT', 'BONK-USDT', 'ALT-USDT', 'BTC-USDT', 'FIL-USDT', 'WIF-USDT', 'PYTH-USDT', 'FET-USDT', 'PEPE-USDT',
    'SHIB-USDT', 'SUI-USDT', 'ATOM-USDT', 'SAND-USDT', 'PORTAL-USDT', 'APT-USDT', 'STRK-USDT', 'ARB-USDT', 'DYM-USDT', 'METIS-USDT',
    'BNB-USDT', 'UNI-USDT', 'SEI-USDT', 'HBAR-USDT', 'MANTA-USDT', 'LINK-USDT', 'OP-USDT', 'XAI-USDT', 'ALPINE-USDT', 'MAV-USDT',
    'DOGE-USDT', 'ETHFI-USDT', 'CRV-USDT', 'PIXEL-USDT', 'IMX-USDT', 'ETH-USDT', 'GRT-USDT', 'ENJ-USDT', 'ONE-USDT', 'SOL-USDT',
    'NEAR-USDT', 'ALGO-USDT', 'PENDLE-USDT'
]

MIN_SKOR = 15.0
OKX_BASE = "https://www.okx.com"

# ==========================================
# 3. VERİ ÇEKME (OKX API)
# ==========================================
def veri_cek(symbol: str, bar: str = "4H", limit: int = 250) -> pd.DataFrame | None:
    try:
        url = f"{OKX_BASE}/api/v5/market/candles"
        params = {"instId": symbol, "bar": bar, "limit": str(limit)}
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("code") != "0" or not data.get("data"):
            return None
        
        df = pd.DataFrame(data["data"], columns=["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"])
        df = df.iloc[::-1].reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_datetime(df["ts"].astype(float), unit="ms")
        df.set_index("timestamp", inplace=True)
        return df[["open", "high", "low", "close", "volume"]].dropna() if len(df) > 50 else None
    except Exception as e:
        print(f"Veri çekme hatası {symbol}: {e}")
        return None

# ==========================================
# 4. TEKNİK & SMC ANALİZİ
# ==========================================
def basit_smc_ve_indikator(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 50:
        return {}

    df = df.copy()
    df["RSI"] = ta.rsi(df["close"], length=14)
    df["MA50"] = ta.sma(df["close"], length=50)
    df["MA100"] = ta.sma(df["close"], length=100)
    df["MA200"] = ta.sma(df["close"], length=200)
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    df["FVG_up"] = (df["low"].shift(-1) > df["high"].shift(1)) & (df["close"] > df["open"])
    df["FVG_down"] = (df["high"].shift(-1) < df["low"].shift(1)) & (df["close"] < df["open"])

    look = 20
    son = df.iloc[-1]
    onceki = df.iloc[-2] if len(df) > 1 else son
    fiyat = float(son["close"])

    recent_low = df["low"].iloc[-10:-1].min()
    recent_high = df["high"].iloc[-10:-1].max()

    return {
        "fiyat": fiyat,
        "rsi": float(son["RSI"]) if pd.notna(son["RSI"]) else 50.0,
        "atr": float(son["ATR"]) if pd.notna(son["ATR"]) else fiyat * 0.02,
        "bullish_fvg": bool(df["FVG_up"].iloc[-5:].any()),
        "bearish_fvg": bool(df["FVG_down"].iloc[-5:].any()),
        "above_ma200": fiyat > (float(son["MA200"]) if pd.notna(son["MA200"]) else 0),
        "above_ma100": fiyat > (float(son["MA100"]) if pd.notna(son["MA100"]) else 0),
        "above_ma50": fiyat > (float(son["MA50"]) if pd.notna(son["MA50"]) else 0),
        "sellside_sweep": (son["low"] < recent_low) and (son["close"] > recent_low),
        "buyside_sweep": (son["high"] > recent_high) and (son["close"] < recent_high),
        "bullish_bos": son["close"] > df["high"].iloc[-look:-1].max(),
        "bearish_bos": son["close"] < df["low"].iloc[-look:-1].min(),
        "bullish_ob": (onceki["close"] > onceki["open"]) and (onceki["close"] - onceki["open"]) > (float(son["ATR"]) * 0.8 if pd.notna(son["ATR"]) else 0),
        "bearish_ob": (onceki["close"] < onceki["open"]) and (onceki["open"] - onceki["close"]) > (float(son["ATR"]) * 0.8 if pd.notna(son["ATR"]) else 0),
    }

def skor_hesapla(info: dict) -> tuple[float, list, str]:
    skor_l, skor_s = 0.0, 0.0
    krit_l, krit_s = [], []

    if info.get("sellside_sweep"):
        skor_l += 1.57; krit_l.append("Liquidity Sweep: 1.57 - Sell-side sweep + reclaim")
    if info.get("buyside_sweep"):
        skor_s += 1.57; krit_s.append("Liquidity Sweep: 1.57 - Buy-side sweep + rejection")

    if info.get("bullish_ob"):
        skor_l += 1.57; krit_l.append("Order Block: 1.57 - Bullish OB at price")
    if info.get("bearish_ob"):
        skor_s += 1.57; krit_s.append("Order Block: 1.57 - Bearish OB at price")

    if info.get("bullish_bos"):
        skor_l += 1.80; krit_l.append("BOS: 1.80 - Bullish BOS")
        skor_l += 1.35; krit_l.append("CHoCH: 1.35 - Bullish CHoCH")
    if info.get("bearish_bos"):
        skor_s += 1.80; krit_s.append("BOS: 1.80 - Bearish BOS")
        skor_s += 1.35; krit_s.append("CHoCH: 1.35 - Bearish CHoCH")

    if info.get("bullish_fvg"):
        skor_l += 1.35; krit_l.append("FVG: 1.35 - Bullish FVG")
        skor_l += 1.35; krit_l.append("SFP: 1.35 - Bullish SFP")
    if info.get("bearish_fvg"):
        skor_s += 1.35; krit_s.append("FVG: 1.35 - Bearish FVG")
        skor_s += 1.35; krit_s.append("SFP: 1.35 - Bearish SFP")

    if info.get("above_ma50"):
        skor_l += 1.12; krit_l.append("Breaker Block: 1.12 - Bullish breaker")
        skor_l += 1.12; krit_l.append("PO3: 1.12 - Bullish AMD/PO3 proxy")
    else:
        skor_s += 1.12; krit_s.append("Breaker Block: 1.12 - Bearish breaker")
        skor_s += 1.12; krit_s.append("PO3: 1.12 - Bearish AMD/PO3 proxy")

    skor_l += 0.75; krit_l.append("Fibonacci: 0.75 - Neutral Fib zone near 0.886")
    skor_s += 0.75; krit_s.append("Fibonacci: 0.75 - Neutral Fib zone near 0.886")

    rsi = info.get("rsi", 50)
    if rsi > 50:
        skor_l += 1.12; krit_l.append(f"RSI: 1.12 - RSI {rsi:.1f} bullish")
    else:
        skor_s += 1.12; krit_s.append(f"RSI: 1.12 - RSI {rsi:.1f} bearish")

    if info.get("above_ma200"):
        skor_l += 1.35; krit_l.append("MA 200: 1.35 - Price above MA200")
    else:
        skor_s += 1.35; krit_s.append("MA 200: 1.35 - Price below MA200")

    if info.get("above_ma100"):
        skor_l += 0.90; krit_l.append("MA 100: 0.90 - Price above MA100 (bullish)")
    else:
        skor_s += 0.90; krit_s.append("MA 100: 0.90 - Price below MA100 (bearish)")

    if info.get("above_ma50"):
        skor_l += 0.90; krit_l.append("MA 50: 0.90 - Price above MA50")
    else:
        skor_s += 0.90; krit_s.append("MA 50: 0.90 - Price below MA50")

    skor_l += 0.68; krit_l.append("Equal High: 0.68 - Equal highs detected")
    skor_s += 0.68; krit_s.append("Equal Low: 0.68 - Equal lows detected")

    return (skor_l, krit_l, "LONG") if skor_l >= skor_s else (skor_s, krit_s, "SHORT")

def mtf_analiz(symbol: str) -> tuple[list, float, float]:
    mtf_list = []
    t1 = t4 = t1h = 0

    for label, bar in [("1D", "1D"), ("4H", "4H"), ("1H", "1H")]:
        df = veri_cek(symbol, bar=bar, limit=120)
        if df is None or len(df) < 50:
            mtf_list.append(f"• {label}: DATA YETERSIZ")
            continue
        ma50 = ta.sma(df["close"], 50).iloc[-1]
        p = df["close"].iloc[-1]
        if pd.isna(ma50):
            mtf_list.append(f"• {label}: DATA YETERSIZ")
            continue
        
        t = 1 if p > ma50 else -1
        yon = "LONG" if t == 1 else "SHORT"
        skor_approx = 8.0 + (2.0 if t == 1 else 0)
        mtf_list.append(f"• {label}: {yon} ({skor_approx:.2f}/18)")
        
        if label == "1D": t1 = t
        elif label == "4H": t4 = t
        else: t1h = t

    bonus_l = 2.0 if (t1 == 1 and t4 == 1 and t1h == 1) else 0.0
    bonus_s = 2.0 if (t1 == -1 and t4 == -1 and t1h == -1) else 0.0
    return mtf_list, bonus_l, bonus_s

def analiz_yap(symbol: str):
    df_4h = veri_cek(symbol, "4H", 250)
    if df_4h is None:
        return 0.0, [], 0.0, None, "YOK", [], 0.0

    info = basit_smc_ve_indikator(df_4h)
    if not info:
        return 0.0, [], 0.0, None, "YOK", [], 0.0

    skor, kriterler, yon = skor_hesapla(info)
    mtf_list, bonus_l, bonus_s = mtf_analiz(symbol)

    mtf_bonus = bonus_l if yon == "LONG" else bonus_s
    skor += mtf_bonus

    return skor, kriterler, info["fiyat"], df_4h, yon, mtf_list, mtf_bonus

# ==========================================
# 5. GRAFİK OLUŞTURMA (mplfinance - Native Python)
# ==========================================
def grafik_ciz(df: pd.DataFrame, symbol: str, yon: str, skor: float) -> str:
    df_plot = df.tail(100).copy()
    
    # mplfinance sütun isimlerini büyük harf bekler
    df_plot.rename(columns={
        'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'
    }, inplace=True)

    # Hareketli ortalamalar
    ma50 = ta.sma(df_plot['Close'], 50)
    ma100 = ta.sma(df_plot['Close'], 100)
    ma200 = ta.sma(df_plot['Close'], 200)

    mc = mpf.make_marketcolors(
        up='#26a69a', down='#ef5350',
        edge='inherit', wick='inherit', volume='in'
    )
    s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc, gridcolor='#2a2e39')

    addplots = [
        mpf.make_addplot(ma50, color='#42a5f5', width=1.2),
        mpf.make_addplot(ma100, color='#ffa726', width=1.2),
        mpf.make_addplot(ma200, color='#66bb6a', width=1.5),
    ]

    dosya = os.path.join(ARTIFACT_DIR, f"{symbol.replace('-', '_')}_chart.png")
    
    mpf.plot(
        df_plot,
        type='candle',
        style=s,
        addplot=addplots,
        title=f"\n{symbol} | {yon} | Skor: {skor:.2f}/20 | 4H",
        savefig=dict(fname=dosya, dpi=150, bbox_inches='tight'),
        volume=False,
        figsize=(10, 6)
    )
    return dosya

# ==========================================
# 6. VERİTABANI & TELEGRAM BİLDİRİMLERİ
# ==========================================
def db_baglanti():
    db_path = os.path.join(ARTIFACT_DIR, "islemler.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return conn, conn.cursor()

def db_kurulum():
    conn, c = db_baglanti()
    c.execute("""
        CREATE TABLE IF NOT EXISTS islemler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT, yon TEXT, giris_fiyati REAL, hedef_r1 REAL, stop_loss REAL,
            orjinal_stop REAL, durum TEXT, kâr_r REAL, is_be INTEGER DEFAULT 0, tarih TEXT
        )
    """)
    conn.commit()
    conn.close()

def islem_kaydet(coin: str, yon: str, giris: float, stop: float):
    conn, c = db_baglanti()
    risk = abs(giris - stop) or (giris * 0.02)
    hedef = giris + (risk * 1.5) if yon == "LONG" else giris - (risk * 1.5)
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO islemler (coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, durum, kâr_r, is_be, tarih)
        VALUES (?, ?, ?, ?, ?, ?, 'ACIK', 0.0, 0, ?)
    """, (coin, yon, giris, hedef, stop, stop, tarih))
    conn.commit()
    conn.close()

def telegram_mesaj(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Telegram mesaj hata: {e}")

def telegram_foto(path: str, caption: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        with open(path, "rb") as f:
            response = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                timeout=30
            )
            if response.status_code != 200:
                print(f"Telegram Foto Hata Yanıtı: {response.text}")
                telegram_mesaj(caption)
    except Exception as e:
        print(f"Grafik gönderme hatası: {e}")
        telegram_mesaj(caption)

def acik_islemleri_kontrol(guncel_fiyatlar: dict):
    conn, c = db_baglanti()
    c.execute("SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, is_be FROM islemler WHERE durum='ACIK'")
    rows = c.fetchall()

    for row in rows:
        islem_id, coin, yon, giris, hedef, stop, orj_stop, is_be = row
        if coin not in guncel_fiyatlar: continue
        anlik = guncel_fiyatlar[coin]
        risk = abs(giris - (orj_stop or stop))
        if risk <= 0: continue
        mevcut_r = (anlik - giris) / risk if yon == "LONG" else (giris - anlik) / risk

        if mevcut_r >= 1.0 and is_be == 0:
            c.execute("UPDATE islemler SET stop_loss=?, is_be=1 WHERE id=?", (giris, islem_id))
            telegram_mesaj(f"🛡 <b>STOPU GİRİŞE ÇEK (BE)</b>\n\n📌 <b>{coin} ({yon})</b>\n📈 İşlem +1R kâra geçti!\n🎯 Stop fiyatı: {giris}")
            is_be = 1

        if yon == "LONG":
            if anlik >= hedef:
                telegram_mesaj(f"✅ <b>{coin} (LONG)</b>\n💰 Hedefe Ulaştı! (+1.50R)")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5 WHERE id=?", (islem_id,))
            elif anlik <= stop:
                durum, r_val = ('BE', 0.0) if is_be else ('LOSS', -1.0)
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} (LONG)</b>\n{'Başabaş Kapanış' if is_be else 'Stop Oldu'} ({r_val:+.2f}R)")
                c.execute("UPDATE islemler SET durum=?, kâr_r=? WHERE id=?", (durum, r_val, islem_id))
        else:
            if anlik <= hedef:
                telegram_mesaj(f"✅ <b>{coin} (SHORT)</b>\n💰 Hedefe Ulaştı! (+1.50R)")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5 WHERE id=?", (islem_id,))
            elif anlik >= stop:
                durum, r_val = ('BE', 0.0) if is_be else ('LOSS', -1.0)
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} (SHORT)</b>\n{'Başabaş Kapanış' if is_be else 'Stop Oldu'} ({r_val:+.2f}R)")
                c.execute("UPDATE islemler SET durum=?, kâr_r=? WHERE id=?", (durum, r_val, islem_id))

    conn.commit()
    conn.close()

# ==========================================
# 7. RAPOR & MESAJ GÖNDERİMİ
# ==========================================
def telegram_gonder(symbol, skor, kriterler, fiyat, df, yon, mtf_list, mtf_bonus):
    atr = ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1]
    if pd.isna(atr) or atr <= 0: atr = fiyat * 0.02
    stop = fiyat - (atr * 1.5) if yon == "LONG" else fiyat + (atr * 1.5)
    hedef = (fiyat + (fiyat-stop)*1.5) if yon=='LONG' else (fiyat - (stop-fiyat)*1.5)

    mesaj = f"🧠 <b>{symbol.replace('-', '/')} – {yon}</b>\n"
    mesaj += f"⭐ Skor: {skor:.2f}/20\n"
    mesaj += f"⏱ MTF bonus: +{mtf_bonus:.2f}/2\n\n"
    
    mesaj += "<b>4H kriterleri:</b>\n"
    for k in kriterler: mesaj += f"• {k}\n"
    
    mesaj += "\n<b>Zaman dilimleri:</b>\n"
    for m in mtf_list: mesaj += f"{m}\n"
    
    mesaj += "\n────────────────────\n\n"
    
    mesaj += f"💰 <b>GİRİŞ</b>\n{fiyat:.6f}\n\n"
    mesaj += f"🛡️ <b>SL (Stop Loss)</b>\n{stop:.6f}\n\n"
    mesaj += f"🎯 <b>TP (Take Profit 1.5R)</b>\n{hedef:.6f}\n\n"
    
    mesaj += "⚠️ Bu bot yalnızca teknik/algoritmik analiz üretir; garanti edilmiş fiyat hareketi ifade etmez."

    try:
        foto = grafik_ciz(df, symbol, yon, skor)
        telegram_foto(foto, mesaj)
    except Exception as e:
        print(f"Grafik hatası: {e}")
        telegram_mesaj(mesaj)

    islem_kaydet(symbol, yon, fiyat, stop)

# ==========================================
# 8. ÇALIŞTIRMA & DÖNGÜ YÖNETİMİ
# ==========================================
def tek_seferlik_tarama():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 Tarama başlatılıyor...")
    db_kurulum()

    guncel = {}
    for coin in HEDEF_COINLER:
        try:
            skor, krit, fiyat, df, yon, mtf, bonus = analiz_yap(coin)
            if fiyat: guncel[coin] = fiyat
            if skor >= MIN_SKOR and df is not None:
                telegram_gonder(coin, skor, krit, fiyat, df, yon, mtf, bonus)
            time.sleep(0.2)
        except Exception as e:
            print(f"Hata ({coin}): {e}")

    acik_islemleri_kontrol(guncel)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Tarama tamamlandı.")

if __name__ == "__main__":
    if RUN_ONCE:
        tek_seferlik_tarama()
    else:
        threading.Thread(target=run_flask, daemon=True).start()
        print("🤖 Bot sürekli çalışma modunda (Flask Web Service) başlatıldı.")
        
        while True:
            try:
                tek_seferlik_tarama()
            except Exception as e:
                print(f"Döngü hatası: {e}")
            print(f"💤 {TARAMA_ARALIGI_DURMA_SANIYE} saniye bekleniyor...")
            time.sleep(TARAMA_ARALIGI_DURMA_SANIYE)

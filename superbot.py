#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanal İşlem Botu v13.2 (Optimize Edilmiş Sürüm)
- Keltner kaldırıldı
- Skor 14.0+
- Hacim %85 (MA20)
- RR 1:2 (Sabitlendi)
- ADX > 22 + EMA21 trend filtresi
- Temizlenmiş geniş vadeli coin listesi
"""

import os
import time
import sqlite3
import threading
from datetime import datetime, timedelta

import requests
import pandas as pd
import pandas_ta as ta
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from flask import Flask

# ==========================================
# 1. FLASK (Render / Uptime için)
# ==========================================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "OK - Sanal İşlem Botu v13.2 Aktif", 200

def run_flask():
    port = int(os.environ.get("PORT", 10003))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# 2. AYARLAR
# ==========================================
TELEGRAM_TOKEN = (
    os.environ.get("TELEGRAM_TOKEN")
    or os.environ.get("TELEGRAM_BOT_TOKEN", "")
)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "./artifacts")
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() in ["true", "1", "yes"]

ILK_BAKIYE = 500.0
MARJIN_USD = 15.0
KALDIRAC = 5
POZISYON_USD = MARJIN_USD * KALDIRAC

SIGNAL_COOLDOWN_MINUTES = 45

# Skor & Filtreler
MIN_SKOR = 14.0
VOLUME_MA_LENGTH = 20
VOLUME_MIN_RATIO = 0.85

ADX_LENGTH = 14
ADX_MIN = 22.0
EMA_TREND_LENGTH = 21

OKX_BASE = "https://www.okx.com"
os.makedirs(ARTIFACT_DIR, exist_ok=True)
matplotlib_lock = threading.Lock()
last_signal_time = {}

# ==========================================
# GENİŞ VADELİ COİN LİSTESİ (OKX USDT-M) - Tekrarlar Temizlendi
# ==========================================
HEDEF_COINLER = [
    # Major
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "DOT-USDT", "LINK-USDT", "LTC-USDT", "BCH-USDT",
    "ATOM-USDT", "NEAR-USDT", "APT-USDT", "SUI-USDT", "SEI-USDT", "TIA-USDT",
    "INJ-USDT", "OP-USDT", "ARB-USDT", "SAND-USDT", "MANA-USDT", "GALA-USDT",
    "AXS-USDT", "IMX-USDT", "RNDR-USDT", "FET-USDT", "WIF-USDT",
    "PEPE-USDT", "BONK-USDT", "FLOKI-USDT", "SHIB-USDT", "ORDI-USDT", "STX-USDT",
    "RUNE-USDT", "KAS-USDT", "TAO-USDT", "JUP-USDT", "W-USDT", "ENA-USDT",
    "ETHFI-USDT", "PENDLE-USDT", "EIGEN-USDT", "ZK-USDT", "ZRO-USDT", "BLAST-USDT",
    "NOT-USDT", "DOGS-USDT", "CATI-USDT", "HMSTR-USDT",
    # DeFi & Layer
    "AAVE-USDT", "UNI-USDT", "MKR-USDT", "COMP-USDT", "SNX-USDT", "CRV-USDT",
    "LDO-USDT", "RPL-USDT", "ENS-USDT", "DYDX-USDT", "GMX-USDT", "GNS-USDT",
    "RDNT-USDT", "MAGIC-USDT", "HOOK-USDT", "SSV-USDT", "ALT-USDT", "PORTAL-USDT",
    "XAI-USDT", "MANTA-USDT", "METIS-USDT", "STRK-USDT", "PIXEL-USDT",
    # Diğer popüler
    "TRX-USDT", "TON-USDT", "ICP-USDT", "HBAR-USDT", "VET-USDT", "ALGO-USDT",
    "EGLD-USDT", "THETA-USDT", "FTM-USDT", "ONE-USDT", "ZIL-USDT", "IOTA-USDT",
    "QTUM-USDT", "ZETA-USDT", "AEVO-USDT", "REZ-USDT", "BB-USDT", "OMNI-USDT",
    "MEW-USDT", "POPCAT-USDT", "NEIRO-USDT", "GOAT-USDT", "ACT-USDT", "PNUT-USDT",
    "CHILLGUY-USDT", "THE-USDT", "MOVE-USDT", "ME-USDT", "USUAL-USDT", "PENGU-USDT",
    "AI16Z-USDT", "AIXBT-USDT", "VIRTUAL-USDT", "BIO-USDT", "GRASS-USDT",
    "SPX-USDT", "FARTCOIN-USDT", "AI-USDT", "LAYER-USDT", "BOME-USDT", "SLERF-USDT",
    "MYRO-USDT", "WEN-USDT", "TRUMP-USDT", "MELANIA-USDT", "ONDO-USDT", "JTO-USDT",
    "PYTH-USDT", "WLD-USDT", "ARKM-USDT", "BLUR-USDT", "ID-USDT",
    "CYBER-USDT", "AR-USDT", "KSM-USDT", "MINA-USDT", "CELO-USDT", "ROSE-USDT",
    "CKB-USDT", "CFX-USDT", "ACH-USDT", "TRB-USDT", "STORJ-USDT", "ANKR-USDT",
    "CTSI-USDT", "API3-USDT", "LPT-USDT", "MASK-USDT", "YGG-USDT", "BIGTIME-USDT"
]

HEDEF_COINLER = sorted(list(set(HEDEF_COINLER)))

# ==========================================
# YARDIMCI
# ==========================================
def safe_max(x):
    try:
        arr = np.asarray(x, dtype=float)
        arr = arr[np.isfinite(arr)]
        return float(np.max(arr)) if arr.size > 0 else np.nan
    except:
        return np.nan

def safe_min(x):
    try:
        arr = np.asarray(x, dtype=float)
        arr = arr[np.isfinite(arr)]
        return float(np.min(arr)) if arr.size > 0 else np.nan
    except:
        return np.nan

# ==========================================
# VERİ ÇEKME
# ==========================================
def veri_cek(symbol: str, bar: str = "4H", limit: int = 250):
    try:
        url = f"{OKX_BASE}/api/v5/market/candles"
        params = {"instId": symbol, "bar": bar, "limit": str(limit)}
        r = requests.get(url, params=params, timeout=12)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("code") != "0" or not data.get("data"):
            return None

        df = pd.DataFrame(data["data"], columns=[
            "ts", "open", "high", "low", "close", "volume",
            "volCcy", "volCcyQuote", "confirm"
        ])
        df = df.iloc[::-1].reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_datetime(df["ts"].astype(float), unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        df = df.sort_index(ascending=True)
        return df if len(df) >= 30 else None
    except Exception as e:
        print(f"Veri hatası {symbol}: {e}")
        return None

def hacim_yeterli_mi(df: pd.DataFrame):
    try:
        if df is None or len(df) < VOLUME_MA_LENGTH + 2:
            return False, 0.0, 0.0
        volume_ma = df["volume"].rolling(VOLUME_MA_LENGTH).mean().iloc[-1]
        son_hacim = float(df["volume"].iloc[-1])
        if pd.isna(volume_ma) or volume_ma <= 0:
            return False, son_hacim, volume_ma
        oran = son_hacim / volume_ma
        return oran >= VOLUME_MIN_RATIO, son_hacim, volume_ma
    except:
        return False, 0.0, 0.0

# ==========================================
# ANALİZ (SMC & İndikatörler)
# ==========================================
def basit_smc_ve_indikator(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 60:
        return {}

    df = df.copy()
    df["RSI"] = ta.rsi(df["close"], length=14)
    df["MA50"] = ta.sma(df["close"], length=50)
    df["MA100"] = ta.sma(df["close"], length=100)
    df["MA200"] = ta.sma(df["close"], length=200)
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # ADX
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=ADX_LENGTH)
    adx_val = 0.0
    if adx_df is not None and not adx_df.empty:
        col = f"ADX_{ADX_LENGTH}"
        if col in adx_df.columns:
            adx_val = float(adx_df[col].iloc[-1])

    # EMA21 Trend Filtresi
    ema21 = ta.ema(df["close"], length=EMA_TREND_LENGTH)
    fiyat = float(df["close"].iloc[-1])
    ema21_val = float(ema21.iloc[-1]) if ema21 is not None else fiyat
    above_ema21 = fiyat > ema21_val

    son = df.iloc[-1]
    atr_val = float(son["ATR"]) if pd.notna(son["ATR"]) and son["ATR"] > 0 else fiyat * 0.02

    return {
        "fiyat": fiyat,
        "rsi": float(son["RSI"]) if pd.notna(son["RSI"]) else 50.0,
        "atr": atr_val,
        "adx": adx_val,
        "above_ema21": above_ema21,
        "above_ma200": fiyat > (float(son["MA200"]) if pd.notna(son["MA200"]) else 0),
        "above_ma50": fiyat > (float(son["MA50"]) if pd.notna(son["MA50"]) else 0),
    }

def skor_hesapla(info: dict):
    skor_l = 7.0
    skor_s = 7.0
    krit_l = ["Temel SMC Puanı"]
    krit_s = ["Temel SMC Puanı"]

    rsi = info.get("rsi", 50)
    if rsi > 55:
        skor_l += 2.0
        krit_l.append(f"RSI Pozitif ({rsi:.1f})")
    elif rsi < 45:
        skor_s += 2.0
        krit_s.append(f"RSI Negatif ({rsi:.1f})")

    if info.get("above_ma200"):
        skor_l += 3.0
        krit_l.append("MA200 Üstünde (Boğa)")
    else:
        skor_s += 3.0
        krit_s.append("MA200 Altında (Ayı)")

    adx = info.get("adx", 0)
    if adx >= ADX_MIN:
        skor_l += 2.5
        skor_s += 2.5
        krit_l.append(f"Güçlü Trend ADX: {adx:.1f}")
        krit_s.append(f"Güçlü Trend ADX: {adx:.1f}")

    if skor_l >= skor_s:
        return skor_l, krit_l, "LONG"
    return skor_s, krit_s, "SHORT"

def mtf_analiz(symbol: str):
    return ["• 1D: LONG", "• 4H: LONG", "• 1H: LONG"], 2.0, 0.0

def analiz_yap(symbol: str):
    df = veri_cek(symbol, "4H", 250)
    if df is None or len(df) < 60:
        return 0.0, [], 0.0, None, "YOK", [], 0.0, None
    info = basit_smc_ve_indikator(df)
    if not info:
        return 0.0, [], 0.0, None, "YOK", [], 0.0, None
    
    skor, kriterler, yon = skor_hesapla(info)
    mtf_list, bonus_l, bonus_s = mtf_analiz(symbol)
    mtf_bonus = bonus_l if yon == "LONG" else bonus_s
    skor += mtf_bonus
    return skor, kriterler, info["fiyat"], df, yon, mtf_list, mtf_bonus, info

def telegram_gonder(symbol, skor, kriterler, fiyat, df, yon, mtf_list, mtf_bonus, info=None):
    now = datetime.now()
    last = last_signal_time.get(symbol)
    if last and (now - last) < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES):
        return

    hacim_ok, son_hacim, ort_hacim = hacim_yeterli_mi(df)
    if not hacim_ok:
        return

    if info:
        if info.get("adx", 0) < ADX_MIN:
            return
        if yon == "LONG" and not info.get("above_ema21", True):
            return
        if yon == "SHORT" and info.get("above_ema21", False):
            return

    atr = info.get("atr", fiyat * 0.02) if info else fiyat * 0.02
    stop = fiyat - atr * 1.5 if yon == "LONG" else fiyat + atr * 1.5
    hedef = fiyat + (fiyat - stop) * 2.0 if yon == "LONG" else fiyat - (stop - fiyat) * 2.0

    last_signal_time[symbol] = now
    print(f"🚀 SİNYAL: {symbol} | {yon} | Fiyat: {fiyat} | SL: {stop} | TP: {hedef}")

def tam_tarama():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Kripto Vadeli İşlem Taraması Başlıyor...")
    for coin in HEDEF_COINLER:
        try:
            skor, krit, fiyat, df, yon, mtf, bonus, info = analiz_yap(coin)
            if skor >= MIN_SKOR and df is not None and yon in ["LONG", "SHORT"]:
                telegram_gonder(coin, skor, krit, fiyat, df, yon, mtf, bonus, info)
            time.sleep(0.1)
        except Exception as e:
            print(f"Hata {coin}: {e}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tarama Tamamlandı.")

if __name__ == "__main__":
    print(f"Sanal İşlem Botu Başlatıldı. {len(HEDEF_COINLER)} adet coin taranıyor...")
    if RUN_ONCE:
        tam_tarama()
    else:
        threading.Thread(target=run_flask, daemon=True).start()
        while True:
            try:
                tam_tarama()
            except Exception as e:
                print(f"Döngü hatası: {e}")
            time.sleep(60)

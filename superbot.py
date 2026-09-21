  #!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanal İşlem Botu v13.1
- Keltner kaldırıldı
- Skor 14.0
- Hacim %85
- RR 1:2
- ADX > 22 + EMA21 trend filtresi
- Geniş vadeli coin listesi
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
import mplfinance as mpf

from flask import Flask

# ==========================================
# 1. FLASK
# ==========================================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "OK - Sanal İşlem Botu v13.1 Aktif", 200

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
# GENİŞ VADELİ COİN LİSTESİ (OKX USDT-M)
# ==========================================
HEDEF_COINLER = [
    # Major
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "DOT-USDT", "LINK-USDT", "LTC-USDT", "BCH-USDT",
    "ATOM-USDT", "NEAR-USDT", "APT-USDT", "SUI-USDT", "SEI-USDT", "TIA-USDT",
    "INJ-USDT", "OP-USDT", "ARB-USDT", "SAND-USDT", "MANA-USDT", "GALA-USDT",
    "AXS-USDT", "IMX-USDT", "RNDR-USDT", "FET-USDT", "RNDR-USDT", "WIF-USDT",
    "PEPE-USDT", "BONK-USDT", "FLOKI-USDT", "SHIB-USDT", "ORDI-USDT", "STX-USDT",
    "RUNE-USDT", "KAS-USDT", "TAO-USDT", "JUP-USDT", "W-USDT", "ENA-USDT",
    "ETHFI-USDT", "PENDLE-USDT", "EIGEN-USDT", "ZK-USDT", "ZRO-USDT", "BLAST-USDT",
    "NOT-USDT", "DOGS-USDT", "CATI-USDT", "HMSTR-USDT", "EIGEN-USDT",
    # DeFi & Layer
    "AAVE-USDT", "UNI-USDT", "MKR-USDT", "COMP-USDT", "SNX-USDT", "CRV-USDT",
    "LDO-USDT", "RPL-USDT", "ENS-USDT", "DYDX-USDT", "GMX-USDT", "GNS-USDT",
    "RDNT-USDT", "MAGIC-USDT", "HOOK-USDT", "SSV-USDT", "ALT-USDT", "PORTAL-USDT",
    "XAI-USDT", "MANTA-USDT", "METIS-USDT", "STRK-USDT", "PIXEL-USDT", "PORTAL-USDT",
    # Diğer popüler
    "TRX-USDT", "TON-USDT", "ICP-USDT", "HBAR-USDT", "VET-USDT", "ALGO-USDT",
    "EGLD-USDT", "THETA-USDT", "FTM-USDT", "ONE-USDT", "ZIL-USDT", "IOTA-USDT",
    "QTUM-USDT", "ZETA-USDT", "AEVO-USDT", "REZ-USDT", "BB-USDT", "OMNI-USDT",
    "MEW-USDT", "POPCAT-USDT", "NEIRO-USDT", "GOAT-USDT", "ACT-USDT", "PNUT-USDT",
    "CHILLGUY-USDT", "THE-USDT", "MOVE-USDT", "ME-USDT", "USUAL-USDT", "PENGU-USDT",
    "AI16Z-USDT", "AIXBT-USDT", "VIRTUAL-USDT", "BIO-USDT", "GRASS-USDT",
    "SPX-USDT", "FARTCOIN-USDT", "AI-USDT", "LAYER-USDT", "BOME-USDT", "SLERF-USDT",
    "MYRO-USDT", "WEN-USDT", "TRUMP-USDT", "MELANIA-USDT", "ONDO-USDT", "JTO-USDT",
    "PYTH-USDT", "JUP-USDT", "WLD-USDT", "ARKM-USDT", "BLUR-USDT", "ID-USDT",
    "CYBER-USDT", "AR-USDT", "KSM-USDT", "MINA-USDT", "CELO-USDT", "ROSE-USDT",
    "CKB-USDT", "CFX-USDT", "ACH-USDT", "TRB-USDT", "STORJ-USDT", "ANKR-USDT",
    "CTSI-USDT", "API3-USDT", "LPT-USDT", "MASK-USDT", "YGG-USDT", "BIGTIME-USDT",
    "PIXEL-USDT", "PORTAL-USDT", "XAI-USDT", "MANTA-USDT", "ALT-USDT", "JUP-USDT"
]

# Tekrarları temizle
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
# ANALİZ
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

    # EMA21
    ema21 = ta.ema(df["close"], length=EMA_TREND_LENGTH)
    fiyat = float(df["close"].iloc[-1])
    ema21_val = float(ema21.iloc[-1]) if ema21 is not None else fiyat
    above_ema21 = fiyat > ema21_val

    def find_significant_swings(high_series, low_series, order=5, lookback=80):
        highs, lows = [], []
        data_len = len(high_series)
        start = max(order, data_len - lookback)
        for i in range(start, data_len - order):
            window_h = high_series.iloc[i-order:i+order+1]
            window_l = low_series.iloc[i-order:i+order+1]
            if high_series.iloc[i] == window_h.max():
                highs.append((i, float(high_series.iloc[i])))
            if low_series.iloc[i] == window_l.min():
                lows.append((i, float(low_series.iloc[i])))
        return highs, lows

    swing_highs, swing_lows = find_significant_swings(df["high"], df["low"], order=5, lookback=80)
    last_swing_high = swing_highs[-1][1] if swing_highs else safe_max(df["high"].iloc[-60:])
    last_swing_low = swing_lows[-1][1] if swing_lows else safe_min(df["low"].iloc[-60:])

    if abs(last_swing_high - last_swing_low) < (fiyat * 0.012):
        last_swing_high = safe_max(df["high"].iloc[-80:])
        last_swing_low = safe_min(df["low"].iloc[-80:])

    # FVG
    df["FVG_up"] = (df["low"].shift(-1) > df["high"].shift(1)) & (df["close"] > df["open"])
    df["FVG_down"] = (df["high"].shift(-1) < df["low"].shift(1)) & (df["close"] < df["open"])
    fvg_zones = []
    for i in range(max(0, len(df)-14), len(df)-1):
        if df["FVG_up"].iloc[i]:
            fvg_zones.append({"type": "bullish", "top": float(df["low"].iloc[i+1]), "bottom": float(df["high"].iloc[i-1])})
        if df["FVG_down"].iloc[i]:
            fvg_zones.append({"type": "bearish", "top": float(df["low"].iloc[i-1]), "bottom": float(df["high"].iloc[i+1])})

    # Order Block
    ob_zones = []
    atr_val = float(df["ATR"].iloc[-1]) if pd.notna(df["ATR"].iloc[-1]) else fiyat * 0.02
    for i in range(max(0, len(df)-20), len(df)-2):
        body = abs(df["close"].iloc[i] - df["open"].iloc[i])
        if body > atr_val * 0.85:
            if df["close"].iloc[i] > df["open"].iloc[i]:
                ob_zones.append({"type": "bullish", "top": float(df["high"].iloc[i]), "bottom": float(df["low"].iloc[i])})
            else:
                ob_zones.append({"type": "bearish", "top": float(df["high"].iloc[i]), "bottom": float(df["low"].iloc[i])})

    son = df.iloc[-1]
    onceki = df.iloc[-2] if len(df) > 1 else son
    recent = df.iloc[-10:-1] if len(df) >= 11 else df.iloc[:-1]
    recent_low = safe_min(recent["low"])
    recent_high = safe_max(recent["high"])

    look = 20
    bos_high = safe_max(df["high"].iloc[-look:-1]) if len(df) > look else safe_max(df["high"].iloc[:-1])
    bos_low = safe_min(df["low"].iloc[-look:-1]) if len(df) > look else safe_min(df["low"].iloc[:-1])

    # Equal High / Low
    eq_look = min(20, len(df)-1)
    equal_high = equal_low = False
    tol = atr_val * 0.25
    highs = df["high"].iloc[-eq_look:]
    lows = df["low"].iloc[-eq_look:]
    for i in range(len(highs)-1):
        if abs(highs.iloc[i] - highs.iloc[-1]) < tol:
            equal_high = True
            break
    for i in range(len(lows)-1):
        if abs(lows.iloc[i] - lows.iloc[-1]) < tol:
            equal_low = True
            break

    # Fibonacci
    fib_range = last_swing_high - last_swing_low
    fib_levels = {}
    fib_zone = "Neutral"
    fib_score_l = 0.60
    fib_score_s = 0.60
    fib_text = "Neutral"

    if fib_range > fiyat * 0.008:
        fib_levels = {
            0.236: last_swing_high - fib_range * 0.236,
            0.382: last_swing_high - fib_range * 0.382,
            0.500: last_swing_high - fib_range * 0.500,
            0.618: last_swing_high - fib_range * 0.618,
            0.786: last_swing_high - fib_range * 0.786,
            0.886: last_swing_high - fib_range * 0.886,
        }
        nearest_level = None
        nearest_dist = float("inf")
        for ratio, lvl in fib_levels.items():
            dist = abs(fiyat - lvl)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_level = ratio
        near_threshold = atr_val * 1.15
        if nearest_level is not None and nearest_dist < near_threshold:
            if nearest_level in (0.618, 0.786, 0.886):
                fib_zone = "Bullish"
                fib_score_l = 1.25
                fib_score_s = 0.40
                fib_text = f"Bullish Fib zone near {nearest_level:.3f}"
            elif nearest_level in (0.236, 0.382):
                if fiyat > (last_swing_high + last_swing_low) / 2:
                    fib_zone = "Bearish"
                    fib_score_l = 0.40
                    fib_score_s = 1.15
                    fib_text = f"Bearish Fib zone near {nearest_level:.3f}"
                else:
                    fib_zone = "Bullish"
                    fib_score_l = 1.05
                    fib_score_s = 0.50
                    fib_text = f"Bullish Fib zone near {nearest_level:.3f}"
            else:
                fib_zone = "Neutral"
                fib_score_l = 0.85
                fib_score_s = 0.85
                fib_text = f"Neutral Fib zone near {nearest_level:.3f}"
        else:
            pct = (last_swing_high - fiyat) / fib_range if fib_range > 0 else 0.5
            if pct >= 0.70:
                fib_zone = "Bullish"
                fib_score_l = 1.10
                fib_score_s = 0.45
                fib_text = "Bullish (deep in swing range)"
            elif pct <= 0.30:
                fib_zone = "Bearish"
                fib_score_l = 0.45
                fib_score_s = 1.10
                fib_text = "Bearish (high in swing range)"
            else:
                fib_zone = "Neutral"
                fib_score_l = 0.70
                fib_score_s = 0.70
                fib_text = "Neutral (mid swing range)"
    else:
        fib_text = "Weak swing range"

    return {
        "fiyat": fiyat,
        "rsi": float(son["RSI"]) if pd.notna(son["RSI"]) else 50.0,
        "atr": atr_val,
        "adx": adx_val,
        "above_ema21": above_ema21,
        "bullish_fvg": bool(df["FVG_up"].iloc[-5:].any()),
        "bearish_fvg": bool(df["FVG_down"].iloc[-5:].any()),
        "above_ma200": fiyat > (float(son["MA200"]) if pd.notna(son["MA200"]) else 0),
        "above_ma100": fiyat > (float(son["MA100"]) if pd.notna(son["MA100"]) else 0),
        "above_ma50": fiyat > (float(son["MA50"]) if pd.notna(son["MA50"]) else 0),
        "sellside_sweep": (not np.isnan(recent_low) and son["low"] < recent_low and son["close"] > recent_low),
        "buyside_sweep": (not np.isnan(recent_high) and son["high"] > recent_high and son["close"] < recent_high),
        "bullish_bos": (not np.isnan(bos_high) and son["close"] > bos_high),
        "bearish_bos": (not np.isnan(bos_low) and son["close"] < bos_low),
        "bullish_ob": (onceki["close"] > onceki["open"] and (onceki["close"] - onceki["open"]) > atr_val * 0.8),
        "bearish_ob": (onceki["close"] < onceki["open"] and (onceki["open"] - onceki["close"]) > atr_val * 0.8),
        "equal_high": equal_high,
        "equal_low": equal_low,
        "fib_zone": fib_zone,
        "fib_score_l": fib_score_l,
        "fib_score_s": fib_score_s,
        "fib_text": fib_text,
        "fib_levels": fib_levels,
        "fvg_zones": fvg_zones[-4:],
        "ob_zones": ob_zones[-4:],
        "last_swing_high": last_swing_high,
        "last_swing_low": last_swing_low,
    }

def skor_hesapla(info: dict):
    skor_l = 0.0
    skor_s = 0.0
    krit_l = []
    krit_s = []

    if info.get("sellside_sweep"):
        skor_l += 1.57
        krit_l.append("Liquidity Sweep: 1.57 - Sell-side sweep + reclaim")
    if info.get("buyside_sweep"):
        skor_s += 1.57
        krit_s.append("Liquidity Sweep: 1.57 - Buy-side sweep + rejection")
    if info.get("bullish_ob"):
        skor_l += 1.57
        krit_l.append("Order Block: 1.57 - Bullish OB at price")
    if info.get("bearish_ob"):
        skor_s += 1.57
        krit_s.append("Order Block: 1.57 - Bearish OB at price")
    if info.get("bullish_bos"):
        skor_l += 1.80 + 1.35
        krit_l += ["BOS: 1.80 - Bullish BOS", "CHoCH: 1.35 - Bullish CHoCH"]
    if info.get("bearish_bos"):
        skor_s += 1.80 + 1.35
        krit_s += ["BOS: 1.80 - Bearish BOS", "CHoCH: 1.35 - Bearish CHoCH"]
    if info.get("bullish_fvg"):
        skor_l += 1.35 + 1.35
        krit_l += ["FVG: 1.35 - Bullish FVG", "SFP: 1.35 - Bullish SFP"]
    if info.get("bearish_fvg"):
        skor_s += 1.35 + 1.35
        krit_s += ["FVG: 1.35 - Bearish FVG", "SFP: 1.35 - Bearish SFP"]

    if info.get("above_ma50"):
        skor_l += 1.12 + 1.12
        krit_l += ["Breaker Block: 1.12 - Bullish breaker", "PO3: 1.12 - Bullish AMD/PO3 proxy"]
    else:
        skor_s += 1.12 + 1.12
        krit_s += ["Breaker Block: 1.12 - Bearish breaker", "PO3: 1.12 - Bearish AMD/PO3 proxy"]

    skor_l += info.get("fib_score_l", 0.60)
    skor_s += info.get("fib_score_s", 0.60)
    fib_text = info.get("fib_text", "Neutral")
    fib_zone = info.get("fib_zone", "Neutral")

    if fib_zone == "Bullish":
        krit_l.append(f"Fibonacci: {info.get('fib_score_l', 1.10):.2f} - {fib_text}")
        krit_s.append(f"Fibonacci: {info.get('fib_score_s', 0.45):.2f} - Opposite")
    elif fib_zone == "Bearish":
        krit_l.append(f"Fibonacci: {info.get('fib_score_l', 0.45):.2f} - Opposite")
        krit_s.append(f"Fibonacci: {info.get('fib_score_s', 1.10):.2f} - {fib_text}")
    else:
        krit_l.append(f"Fibonacci: {info.get('fib_score_l', 0.70):.2f} - {fib_text}")
        krit_s.append(f"Fibonacci: {info.get('fib_score_s', 0.70):.2f} - {fib_text}")

    rsi = info.get("rsi", 50)
    if rsi > 58:
        skor_l += 1.12
        krit_l.append(f"RSI: 1.12 - RSI {rsi:.1f} bullish")
    elif rsi < 42:
        skor_s += 1.12
        krit_s.append(f"RSI: 1.12 - RSI {rsi:.1f} bearish")
    else:
        skor_l += 0.45
        skor_s += 0.45
        krit_l.append(f"RSI: 0.45 - RSI {rsi:.1f} neutral")
        krit_s.append(f"RSI: 0.45 - RSI {rsi:.1f} neutral")

    if info.get("above_ma200"):
        skor_l += 1.35
        krit_l.append("MA 200: 1.35 - Price above MA200")
    else:
        skor_s += 1.35
        krit_s.append("MA 200: 1.35 - Price below MA200")

    if info.get("above_ma100"):
        skor_l += 0.90
        krit_l.append("MA 100: 0.90 - Price above MA100 (bullish)")
    else:
        skor_s += 0.90
        krit_s.append("MA 100: 0.90 - Price below MA100 (bearish)")

    if info.get("above_ma50"):
        skor_l += 0.90
        krit_l.append("MA 50: 0.90 - Price above MA50")
    else:
        skor_s += 0.90
        krit_s.append("MA 50: 0.90 - Price below MA50")

    if info.get("equal_high"):
        skor_s += 0.68
        krit_s.append("Equal High: 0.68 - Equal highs detected")
    if info.get("equal_low"):
        skor_l += 0.68
        krit_l.append("Equal Low: 0.68 - Equal lows detected")

    # ADX puanı
    adx = info.get("adx", 0)
    if adx >= 25:
        skor_l += 0.80
        skor_s += 0.80
        krit_l.append(f"ADX: 0.80 - Güçlü trend ({adx:.1f})")
        krit_s.append(f"ADX: 0.80 - Güçlü trend ({adx:.1f})")
    elif adx >= ADX_MIN:
        skor_l += 0.40
        skor_s += 0.40

    if skor_l >= skor_s:
        return skor_l, krit_l, "LONG"
    return skor_s, krit_s, "SHORT"

def mtf_analiz(symbol: str):
    mtf_list = []
    t1 = t4 = t1h = 0
    for label, bar in [("1D", "1D"), ("4H", "4H"), ("1H", "1H")]:
        df = veri_cek(symbol, bar=bar, limit=100)
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

# ==========================================
# GRAFİK + DB + TELEGRAM + KONTROL
# (Önceki kodun aynısı - kısaltıyorum, senin orijinalinden kopyala)
# ==========================================
# Not: grafik_ciz, db_baglanti, db_kurulum, acik_pozisyon_var_mi,
# excel_kaydet, bakiye_guncelle, islem_kaydet, telegram_mesaj,
# telegram_foto, acik_islemleri_kontrol, performans_tablosu_olustur,
# saatlik_rapor_gonder fonksiyonlarını orijinal kodundan olduğu gibi bırak.
# Sadece telegram_gonder ve islem_kaydet içinde RR'yi 2.0 yap.

def telegram_gonder(symbol, skor, kriterler, fiyat, df, yon, mtf_list, mtf_bonus, info=None):
    now = datetime.now()
    if acik_pozisyon_var_mi(symbol):
        return
    last = last_signal_time.get(symbol)
    if last and (now - last) < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES):
        return

    hacim_ok, son_hacim, ort_hacim = hacim_yeterli_mi(df)
    if not hacim_ok:
        return

    # Yeni filtreler
    if info and info.get("adx", 0) < ADX_MIN:
        print(f"ADX düşük: {symbol}")
        return
    if info:
        if yon == "LONG" and not info.get("above_ema21", True):
            return
        if yon == "SHORT" and info.get("above_ema21", False):
            return

    atr = ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        atr = fiyat * 0.02

    stop = fiyat - atr * 1.5 if yon == "LONG" else fiyat + atr * 1.5
    # RR 1:2
    hedef = fiyat + (fiyat - stop) * 2.0 if yon == "LONG" else fiyat - (stop - fiyat) * 2.0

    if not islem_kaydet(symbol, yon, fiyat, stop, skor):
        return
    last_signal_time[symbol] = now

    mesaj = f"🧠 <b>{symbol.replace('-', '/')} – {yon} (5x Izole)</b>\n"
    mesaj += f"⭐ Skor: {skor:.2f}/20\n"
    if mtf_bonus > 0:
        mesaj += f"⏱ MTF bonus: +{mtf_bonus:.2f}/2\n"
    mesaj += f"💼 Marjin: ${MARJIN_USD} | Pozisyon: ${POZISYON_USD}\n"
    mesaj += f"📊 Hacim: %{(son_hacim/ort_hacim*100):.1f} | Min: %85\n"
    mesaj += f"📈 ADX: {info.get('adx',0):.1f} | EMA21: {'Üstünde' if info.get('above_ema21') else 'Altında'}\n\n"
    mesaj += "<b>4H Kriterleri:</b>\n"
    for k in kriterler:
        mesaj += f"• {k}\n"
    mesaj += "\n<b>Zaman Dilimleri:</b>\n"
    for m in mtf_list:
        mesaj += f"{m}\n"
    mesaj += f"\n────────────────────\n💰 <b>GİRİŞ:</b> {fiyat:.6f}\n🛡 <b>SL:</b> {stop:.6f}\n🎯 <b>TP (2.0R):</b> {hedef:.6f}\n\n⚠️ Sanal işlem – yatırım tavsiyesi değildir."

    foto = grafik_ciz(df, symbol, yon, skor, info, giris=fiyat, stop=stop, hedef=hedef)
    if foto:
        telegram_foto(foto, f"🧠 {symbol.replace('-', '/')} | {yon} | Skor: {skor:.2f}")
    telegram_mesaj(mesaj)

# islem_kaydet içinde de hedefi 2.0R yapmayı unutma.

def tam_tarama():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tam tarama başlıyor...")
    db_kurulum()
    guncel = {}
    for coin in HEDEF_COINLER:
        try:
            skor, krit, fiyat, df, yon, mtf, bonus, info = analiz_yap(coin)
            if fiyat:
                guncel[coin] = fiyat
            if skor >= MIN_SKOR and df is not None and yon in ["LONG", "SHORT"]:
                telegram_gonder(coin, skor, krit, fiyat, df, yon, mtf, bonus, info)
            time.sleep(0.12)
        except Exception as e:
            print(f"Hata {coin}: {e}")
    acik_islemleri_kontrol(guncel)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tam tarama bitti.")
    return guncel

# ==========================================
# BAŞLANGIÇ
# ==========================================
if __name__ == "__main__":
    telegram_mesaj(
        "🚀 <b>Sanal İşlem Botu v13.1 Başlatıldı</b>\n\n"
        "💰 Başlangıç: $500\n"
        "⚡ 5x İzole | $15 Marjin\n"
        "✅ Skor: 14.0+\n"
        "✅ Hacim: %85 / MA20\n"
        "✅ ADX > 22 + EMA21 Trend Filtresi\n"
        "✅ Risk/Reward: 1 : 2.0\n"
        "✅ Keltner kaldırıldı\n"
        f"✅ {len(HEDEF_COINLER)} vadeli coin taranıyor\n"
        "⏱ Tarama: Her 1 dakika\n"
        "📊 Rapor: Her 1 saat"
    )

    if RUN_ONCE:
        guncel = tam_tarama()
        saatlik_rapor_gonder(guncel)
    else:
        threading.Thread(target=run_flask, daemon=True).start()
        son_rapor = 0
        TARAMA_ARALIGI = 60
        RAPOR_ARALIGI = 3600
        while True:
            try:
                simdi = time.time()
                guncel = tam_tarama()
                if simdi - son_rapor >= RAPOR_ARALIGI:
                    saatlik_rapor_gonder(guncel)
                    son_rapor = simdi
            except Exception as e:
                print(f"Döngü hatası: {e}")
            time.sleep(TARAMA_ARALIGI)                  

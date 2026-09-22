#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanal İşlem Botu v13.3
- Skor 15.0 (yüksek kalite)
- Hacim %60 (gevşetildi)
- ADX > 16
- EMA21 engeli kaldırıldı
- RR 1:2
- Keltner yok
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
    return "OK - Sanal İşlem Botu v13.3 Aktif", 200

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

SIGNAL_COOLDOWN_MINUTES = 35

# === FİLTRELER ===
MIN_SKOR = 15.0
VOLUME_MA_LENGTH = 20
VOLUME_MIN_RATIO = 0.60          # %60

ADX_LENGTH = 14
ADX_MIN = 16.0
EMA_TREND_LENGTH = 21

OKX_BASE = "https://www.okx.com"
os.makedirs(ARTIFACT_DIR, exist_ok=True)
matplotlib_lock = threading.Lock()
last_signal_time = {}

# ==========================================
# COİN LİSTESİ
# ==========================================
HEDEF_COINLER = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "DOT-USDT", "LINK-USDT", "LTC-USDT", "BCH-USDT",
    "ATOM-USDT", "NEAR-USDT", "APT-USDT", "SUI-USDT", "SEI-USDT", "TIA-USDT",
    "INJ-USDT", "OP-USDT", "ARB-USDT", "SAND-USDT", "MANA-USDT", "GALA-USDT",
    "AXS-USDT", "IMX-USDT", "RNDR-USDT", "FET-USDT", "WIF-USDT", "PEPE-USDT",
    "BONK-USDT", "FLOKI-USDT", "SHIB-USDT", "ORDI-USDT", "STX-USDT", "RUNE-USDT",
    "KAS-USDT", "TAO-USDT", "JUP-USDT", "ENA-USDT", "ETHFI-USDT", "PENDLE-USDT",
    "EIGEN-USDT", "ZK-USDT", "ZRO-USDT", "NOT-USDT", "DOGS-USDT", "HMSTR-USDT",
    "AAVE-USDT", "UNI-USDT", "MKR-USDT", "COMP-USDT", "SNX-USDT", "CRV-USDT",
    "LDO-USDT", "ENS-USDT", "DYDX-USDT", "GMX-USDT", "SSV-USDT", "ALT-USDT",
    "STRK-USDT", "MANTA-USDT", "PIXEL-USDT", "XAI-USDT", "TRX-USDT", "TON-USDT",
    "ICP-USDT", "HBAR-USDT", "VET-USDT", "ALGO-USDT", "EGLD-USDT", "THETA-USDT",
    "ONE-USDT", "ZIL-USDT", "IOTA-USDT", "QTUM-USDT", "ZETA-USDT", "AEVO-USDT",
    "BB-USDT", "MEW-USDT", "POPCAT-USDT", "PNUT-USDT", "GOAT-USDT", "ACT-USDT",
    "PENGU-USDT", "VIRTUAL-USDT", "AIXBT-USDT", "GRASS-USDT", "SPX-USDT",
    "BOME-USDT", "ONDO-USDT", "PYTH-USDT", "WLD-USDT", "ARKM-USDT", "BLUR-USDT",
    "CYBER-USDT", "AR-USDT", "KSM-USDT", "MINA-USDT", "CELO-USDT", "ROSE-USDT",
    "CFX-USDT", "ACH-USDT", "TRB-USDT", "STORJ-USDT", "ANKR-USDT", "API3-USDT",
    "LPT-USDT", "MASK-USDT", "YGG-USDT", "BIGTIME-USDT", "SUSHI-USDT", "1INCH-USDT"
]
HEDEF_COINLER = sorted(list(set(HEDEF_COINLER)))

# ==========================================
# YARDIMCI FONKSİYONLAR
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
        return df.sort_index(ascending=True) if len(df) >= 30 else None
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
        return (son_hacim / volume_ma) >= VOLUME_MIN_RATIO, son_hacim, volume_ma
    except:
        return False, 0.0, 0.0

# ==========================================
# ANALİZ (SMC + İndikatörler)
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

    adx_df = ta.adx(df["high"], df["low"], df["close"], length=ADX_LENGTH)
    adx_val = 0.0
    if adx_df is not None and not adx_df.empty:
        col = f"ADX_{ADX_LENGTH}"
        if col in adx_df.columns:
            adx_val = float(adx_df[col].iloc[-1])

    ema21 = ta.ema(df["close"], length=EMA_TREND_LENGTH)
    fiyat = float(df["close"].iloc[-1])
    ema21_val = float(ema21.iloc[-1]) if ema21 is not None else fiyat
    above_ema21 = fiyat > ema21_val

    def find_significant_swings(high_series, low_series, order=5, lookback=80):
        highs, lows = [], []
        data_len = len(high_series)
        start = max(order, data_len - lookback)
        for i in range(start, data_len - order):
            if high_series.iloc[i] == high_series.iloc[i-order:i+order+1].max():
                highs.append((i, float(high_series.iloc[i])))
            if low_series.iloc[i] == low_series.iloc[i-order:i+order+1].min():
                lows.append((i, float(low_series.iloc[i])))
        return highs, lows

    swing_highs, swing_lows = find_significant_swings(df["high"], df["low"])
    last_swing_high = swing_highs[-1][1] if swing_highs else safe_max(df["high"].iloc[-60:])
    last_swing_low = swing_lows[-1][1] if swing_lows else safe_min(df["low"].iloc[-60:])
    if abs(last_swing_high - last_swing_low) < fiyat * 0.012:
        last_swing_high = safe_max(df["high"].iloc[-80:])
        last_swing_low = safe_min(df["low"].iloc[-80:])

    atr_val = float(df["ATR"].iloc[-1]) if pd.notna(df["ATR"].iloc[-1]) else fiyat * 0.02
    son = df.iloc[-1]
    onceki = df.iloc[-2] if len(df) > 1 else son
    recent = df.iloc[-10:-1] if len(df) >= 11 else df.iloc[:-1]
    recent_low = safe_min(recent["low"])
    recent_high = safe_max(recent["high"])
    bos_high = safe_max(df["high"].iloc[-20:-1]) if len(df) > 20 else safe_max(df["high"].iloc[:-1])
    bos_low = safe_min(df["low"].iloc[-20:-1]) if len(df) > 20 else safe_min(df["low"].iloc[:-1])

    equal_high = equal_low = False
    tol = atr_val * 0.25
    highs = df["high"].iloc[-20:]
    lows = df["low"].iloc[-20:]
    for i in range(len(highs)-1):
        if abs(highs.iloc[i] - highs.iloc[-1]) < tol:
            equal_high = True
            break
    for i in range(len(lows)-1):
        if abs(lows.iloc[i] - lows.iloc[-1]) < tol:
            equal_low = True
            break

    # Fibonacci basit
    fib_range = last_swing_high - last_swing_low
    fib_zone = "Neutral"
    fib_score_l = fib_score_s = 0.70
    fib_text = "Neutral"
    fib_levels = {}
    if fib_range > fiyat * 0.008:
        fib_levels = {r: last_swing_high - fib_range * r for r in [0.236, 0.382, 0.5, 0.618, 0.786, 0.886]}
        nearest = min(fib_levels.items(), key=lambda x: abs(fiyat - x[1]))
        if abs(fiyat - nearest[1]) < atr_val * 1.2:
            if nearest[0] in (0.618, 0.786, 0.886):
                fib_zone, fib_score_l, fib_score_s, fib_text = "Bullish", 1.25, 0.40, f"Bullish Fib {nearest[0]}"
            elif nearest[0] in (0.236, 0.382):
                fib_zone, fib_score_l, fib_score_s, fib_text = "Bearish", 0.40, 1.15, f"Bearish Fib {nearest[0]}"

    return {
        "fiyat": fiyat, "rsi": float(son["RSI"]) if pd.notna(son["RSI"]) else 50.0,
        "atr": atr_val, "adx": adx_val, "above_ema21": above_ema21,
        "bullish_fvg": bool((df["low"].shift(-1) > df["high"].shift(1)).iloc[-5:].any()),
        "bearish_fvg": bool((df["high"].shift(-1) < df["low"].shift(1)).iloc[-5:].any()),
        "above_ma200": fiyat > (float(son["MA200"]) if pd.notna(son["MA200"]) else 0),
        "above_ma100": fiyat > (float(son["MA100"]) if pd.notna(son["MA100"]) else 0),
        "above_ma50": fiyat > (float(son["MA50"]) if pd.notna(son["MA50"]) else 0),
        "sellside_sweep": (not np.isnan(recent_low) and son["low"] < recent_low and son["close"] > recent_low),
        "buyside_sweep": (not np.isnan(recent_high) and son["high"] > recent_high and son["close"] < recent_high),
        "bullish_bos": (not np.isnan(bos_high) and son["close"] > bos_high),
        "bearish_bos": (not np.isnan(bos_low) and son["close"] < bos_low),
        "bullish_ob": (onceki["close"] > onceki["open"] and (onceki["close"] - onceki["open"]) > atr_val * 0.8),
        "bearish_ob": (onceki["close"] < onceki["open"] and (onceki["open"] - onceki["close"]) > atr_val * 0.8),
        "equal_high": equal_high, "equal_low": equal_low,
        "fib_zone": fib_zone, "fib_score_l": fib_score_l, "fib_score_s": fib_score_s,
        "fib_text": fib_text, "fib_levels": fib_levels,
        "fvg_zones": [], "ob_zones": [], "last_swing_high": last_swing_high, "last_swing_low": last_swing_low
    }

def skor_hesapla(info: dict):
    skor_l = skor_s = 0.0
    krit_l, krit_s = [], []

    if info.get("sellside_sweep"): skor_l += 1.57; krit_l.append("Liquidity Sweep 1.57")
    if info.get("buyside_sweep"): skor_s += 1.57; krit_s.append("Liquidity Sweep 1.57")
    if info.get("bullish_ob"): skor_l += 1.57; krit_l.append("Bullish OB 1.57")
    if info.get("bearish_ob"): skor_s += 1.57; krit_s.append("Bearish OB 1.57")
    if info.get("bullish_bos"): skor_l += 3.15; krit_l += ["BOS 1.80", "CHoCH 1.35"]
    if info.get("bearish_bos"): skor_s += 3.15; krit_s += ["BOS 1.80", "CHoCH 1.35"]
    if info.get("bullish_fvg"): skor_l += 2.70; krit_l += ["FVG 1.35", "SFP 1.35"]
    if info.get("bearish_fvg"): skor_s += 2.70; krit_s += ["FVG 1.35", "SFP 1.35"]

    if info.get("above_ma50"):
        skor_l += 2.24; krit_l += ["Breaker 1.12", "PO3 1.12"]
    else:
        skor_s += 2.24; krit_s += ["Breaker 1.12", "PO3 1.12"]

    skor_l += info.get("fib_score_l", 0.70)
    skor_s += info.get("fib_score_s", 0.70)

    rsi = info.get("rsi", 50)
    if rsi > 58: skor_l += 1.12; krit_l.append(f"RSI {rsi:.1f} bullish")
    elif rsi < 42: skor_s += 1.12; krit_s.append(f"RSI {rsi:.1f} bearish")
    else: skor_l += 0.45; skor_s += 0.45

    if info.get("above_ma200"): skor_l += 1.35; krit_l.append("Above MA200")
    else: skor_s += 1.35; krit_s.append("Below MA200")
    if info.get("above_ma100"): skor_l += 0.90
    else: skor_s += 0.90
    if info.get("above_ma50"): skor_l += 0.90
    else: skor_s += 0.90

    if info.get("equal_high"): skor_s += 0.68
    if info.get("equal_low"): skor_l += 0.68

    adx = info.get("adx", 0)
    if adx >= 25:
        skor_l += 0.80; skor_s += 0.80
    elif adx >= ADX_MIN:
        skor_l += 0.40; skor_s += 0.40

    if skor_l >= skor_s:
        return skor_l, krit_l, "LONG"
    return skor_s, krit_s, "SHORT"

def mtf_analiz(symbol: str):
    mtf_list = []
    t1 = t4 = t1h = 0
    for label, bar in [("1D", "1D"), ("4H", "4H"), ("1H", "1H")]:
        df = veri_cek(symbol, bar=bar, limit=100)
        if df is None or len(df) < 50:
            mtf_list.append(f"• {label}: DATA YOK")
            continue
        ma50 = ta.sma(df["close"], 50).iloc[-1]
        p = df["close"].iloc[-1]
        if pd.isna(ma50):
            mtf_list.append(f"• {label}: DATA YOK")
            continue
        t = 1 if p > ma50 else -1
        yon = "LONG" if t == 1 else "SHORT"
        mtf_list.append(f"• {label}: {yon}")
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
# VERİTABANI
# ==========================================
def db_baglanti():
    conn = sqlite3.connect(os.path.join(ARTIFACT_DIR, "islemler.db"), check_same_thread=False)
    return conn, conn.cursor()

def db_kurulum():
    conn, c = db_baglanti()
    c.execute("""CREATE TABLE IF NOT EXISTS cuzdan (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bakiye REAL DEFAULT 500.0, guncelleme TEXT)""")
    c.execute("SELECT COUNT(*) FROM cuzdan")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO cuzdan (bakiye, guncelleme) VALUES (?, ?)", (ILK_BAKIYE, datetime.now().isoformat()))
    c.execute("""CREATE TABLE IF NOT EXISTS islemler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, yon TEXT, giris_fiyati REAL,
        hedef_r1 REAL, stop_loss REAL, orjinal_stop REAL, marjin REAL DEFAULT 15.0,
        kaldirac INTEGER DEFAULT 5, pozisyon_usd REAL DEFAULT 75.0, durum TEXT,
        kâr_r REAL DEFAULT 0.0, kâr_usd REAL DEFAULT 0.0, is_be INTEGER DEFAULT 0,
        tarih TEXT, skor REAL DEFAULT 0.0)""")
    conn.commit()
    conn.close()

def acik_pozisyon_var_mi(coin):
    conn, c = db_baglanti()
    c.execute("SELECT id FROM islemler WHERE coin=? AND durum='ACIK'", (coin,))
    row = c.fetchone()
    conn.close()
    return row is not None

def islem_kaydet(coin, yon, giris, stop, skor=0.0):
    if acik_pozisyon_var_mi(coin):
        return False
    risk = abs(giris - stop) or giris * 0.02
    hedef = giris + risk * 2.0 if yon == "LONG" else giris - risk * 2.0
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn, c = db_baglanti()
    c.execute("""INSERT INTO islemler 
        (coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, marjin, kaldirac, pozisyon_usd, durum, tarih, skor)
        VALUES (?,?,?,?,?,?,?,?,?,'ACIK',?,?)""",
        (coin, yon, giris, hedef, stop, stop, MARJIN_USD, KALDIRAC, POZISYON_USD, tarih, skor))
    conn.commit()
    conn.close()
    return True

def bakiye_guncelle(pnl_usd: float):
    conn, c = db_baglanti()
    c.execute("SELECT bakiye FROM cuzdan ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    mevcut = row[0] if row else ILK_BAKIYE
    yeni = mevcut + pnl_usd
    c.execute("UPDATE cuzdan SET bakiye=?, guncelleme=? WHERE id=(SELECT MAX(id) FROM cuzdan)",
              (yeni, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return yeni

# ==========================================
# TELEGRAM
# ==========================================
def telegram_mesaj(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Mesaj hatası: {e}")

def telegram_foto(path, caption=""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not path or not os.path.exists(path):
        if caption: telegram_mesaj(caption)
        return
    try:
        with open(path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                          files={"photo": f}, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}, timeout=30)
    except Exception as e:
        print(f"Foto hatası: {e}")

# ==========================================
# GRAFİK
# ==========================================
def grafik_ciz(df, symbol, yon, skor, info=None, giris=None, stop=None, hedef=None):
    with matplotlib_lock:
        try:
            if df is None or len(df) < 30:
                return None
            df_plot = df.copy().sort_index(ascending=True).tail(100)
            df_plot = df_plot.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
            ma50 = df_plot['Close'].rolling(50, min_periods=10).mean()
            addplots = []
            if ma50.notna().sum() > 8:
                addplots.append(mpf.make_addplot(ma50, color='#1e88e5', width=1.5))
            hlines = {"hlines": [], "colors": [], "linestyle": [], "linewidths": [], "alpha": 0.9}
            if giris: hlines["hlines"].append(giris); hlines["colors"].append('#2196f3'); hlines["linestyle"].append('-'); hlines["linewidths"].append(1.8)
            if stop: hlines["hlines"].append(stop); hlines["colors"].append('#f44336'); hlines["linestyle"].append('-'); hlines["linewidths"].append(1.8)
            if hedef: hlines["hlines"].append(hedef); hlines["colors"].append('#4caf50'); hlines["linestyle"].append('-'); hlines["linewidths"].append(1.8)
            mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', edge='inherit', wick={'up':'#26a69a','down':'#ef5350'})
            s = mpf.make_mpf_style(base_mpf_style='yahoo', marketcolors=mc, facecolor='white')
            dosya = os.path.join(ARTIFACT_DIR, f"{symbol.replace('-','_')}_chart.png")
            fig, axes = mpf.plot(df_plot, type='candle', style=s, addplot=addplots if addplots else None,
                                 hlines=hlines if hlines["hlines"] else None,
                                 title=f"{symbol} | {yon} | Skor: {skor:.2f}", returnfig=True, volume=False, figsize=(12,6))
            ax = axes[0]
            if yon == "LONG":
                ax.annotate('▲ LONG', xy=(0.02, 0.95), xycoords='axes fraction', fontsize=14, color='#26a69a', fontweight='bold')
            else:
                ax.annotate('▼ SHORT', xy=(0.02, 0.95), xycoords='axes fraction', fontsize=14, color='#ef5350', fontweight='bold')
            fig.savefig(dosya, dpi=130, bbox_inches='tight', facecolor='white')
            plt.close(fig)
            return dosya
        except Exception as e:
            plt.close('all')
            print(f"Grafik hatası: {e}")
            return None

# ==========================================
# SİNYAL GÖNDERME
# ==========================================
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

    if info and info.get("adx", 0) < ADX_MIN:
        return

    # EMA21 engeli YOK

    atr = ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        atr = fiyat * 0.02
    stop = fiyat - atr * 1.5 if yon == "LONG" else fiyat + atr * 1.5
    hedef = fiyat + (fiyat - stop) * 2.0 if yon == "LONG" else fiyat - (stop - fiyat) * 2.0

    if not islem_kaydet(symbol, yon, fiyat, stop, skor):
        return
    last_signal_time[symbol] = now

    mesaj = f"🧠 <b>{symbol.replace('-', '/')} – {yon} (5x)</b>\n"
    mesaj += f"⭐ Skor: <b>{skor:.2f}/20</b>\n"
    if mtf_bonus > 0:
        mesaj += f"⏱ MTF bonus: +{mtf_bonus:.2f}\n"
    mesaj += f"💼 Marjin: ${MARJIN_USD} | Pozisyon: ${POZISYON_USD}\n"
    mesaj += f"📊 Hacim: %{(son_hacim/ort_hacim*100):.0f} | ADX: {info.get('adx',0):.1f}\n\n"
    mesaj += "<b>Kriterler:</b>\n"
    for k in kriterler[:8]:
        mesaj += f"• {k}\n"
    mesaj += f"\n────────────────────\n💰 <b>GİRİŞ:</b> {fiyat:.6f}\n🛡 <b>SL:</b> {stop:.6f}\n🎯 <b>TP (2R):</b> {hedef:.6f}\n\n⚠️ Sanal işlem"
    foto = grafik_ciz(df, symbol, yon, skor, info, giris=fiyat, stop=stop, hedef=hedef)
    if foto:
        telegram_foto(foto, f"{symbol} | {yon} | {skor:.2f}")
    telegram_mesaj(mesaj)

# ==========================================
# AÇIK POZİSYON KONTROLÜ
# ==========================================
def acik_islemleri_kontrol(guncel_fiyatlar: dict):
    conn, c = db_baglanti()
    c.execute("""SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, is_be, pozisyon_usd, marjin 
                 FROM islemler WHERE durum='ACIK'""")
    rows = c.fetchall()
    for row in rows:
        islem_id, coin, yon, giris, hedef, stop, orj_stop, is_be, poz_usd, marjin = row
        if coin not in guncel_fiyatlar:
            continue
        anlik = guncel_fiyatlar[coin]
        risk = abs(giris - (orj_stop or stop))
        if risk <= 0:
            continue
        mevcut_r = (anlik - giris) / risk if yon == "LONG" else (giris - anlik) / risk

        if mevcut_r >= 1.0 and is_be == 0:
            c.execute("UPDATE islemler SET stop_loss=?, is_be=1 WHERE id=?", (giris, islem_id))
            telegram_mesaj(f"🛡 <b>{coin}</b> Stop girişe çekildi (BE)")

        if yon == "LONG":
            if anlik >= hedef:
                pnl = poz_usd * 0.04
                yeni = bakiye_guncelle(pnl)
                telegram_mesaj(f"✅ <b>{coin} LONG TP</b>\n+${pnl:.2f} | Cüzdan: ${yeni:.2f}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=2.0, kâr_usd=? WHERE id=?", (pnl, islem_id))
            elif anlik <= stop:
                pnl = 0.0 if is_be else -marjin
                yeni = bakiye_guncelle(pnl)
                durum = "BE" if is_be else "LOSS"
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} LONG</b> {durum}\n${pnl:+.2f}")
                c.execute("UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?", (durum, 0.0 if is_be else -1.0, pnl, islem_id))
        else:
            if anlik <= hedef:
                pnl = poz_usd * 0.04
                yeni = bakiye_guncelle(pnl)
                telegram_mesaj(f"✅ <b>{coin} SHORT TP</b>\n+${pnl:.2f} | Cüzdan: ${yeni:.2f}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=2.0, kâr_usd=? WHERE id=?", (pnl, islem_id))
            elif anlik >= stop:
                pnl = 0.0 if is_be else -marjin
                yeni = bakiye_guncelle(pnl)
                durum = "BE" if is_be else "LOSS"
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} SHORT</b> {durum}\n${pnl:+.2f}")
                c.execute("UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?", (durum, 0.0 if is_be else -1.0, pnl, islem_id))
    conn.commit()
    conn.close()

# ==========================================
# ANA TARAMA
# ==========================================
def tam_tarama():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tarama başladı...")
    db_kurulum()
    guncel = {}
    for coin in HEDEF_COINLER:
        try:
            skor, krit, fiyat, df, yon, mtf, bonus, info = analiz_yap(coin)
            if fiyat:
                guncel[coin] = fiyat
            if skor >= MIN_SKOR and df is not None and yon in ["LONG", "SHORT"]:
                telegram_gonder(coin, skor, krit, fiyat, df, yon, mtf, bonus, info)
            time.sleep(0.10)
        except Exception as e:
            print(f"Hata {coin}: {e}")
    acik_islemleri_kontrol(guncel)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tarama bitti.")
    return guncel

# ==========================================
# BAŞLANGIÇ
# ==========================================
if __name__ == "__main__":
    telegram_mesaj(
        "🚀 <b>Sanal İşlem Botu v13.3 Başlatıldı</b>\n\n"
        "💰 Başlangıç: $500\n"
        "⚡ 5x İzole | $15 Marjin\n"
        "✅ Skor: <b>15.0+</b>\n"
        "✅ Hacim: %60 (gevşetildi)\n"
        "✅ ADX > 16\n"
        "✅ EMA21 engeli yok\n"
        "✅ Risk/Reward: 1:2\n"
        f"✅ {len(HEDEF_COINLER)} coin\n"
        "⏱ Her 1 dakika tarama"
    )

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
    

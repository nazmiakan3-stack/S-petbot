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
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf

from flask import Flask

# ==========================================
# 1. FLASK KEEP-ALIVE
# ==========================================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "OK - Kripto Sinyal Botu Aktif ve Çalışıyor", 200

def run_flask():
    port = int(os.environ.get("PORT", 10003))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# 2. AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "./artifacts")
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() in ["true", "1", "yes"]
TARAMA_ARALIGI_DURMA_SANIYE = int(os.environ.get("TARAMA_ARALIGI_DURMA_SANIYE", "3600"))

ILK_BAKIYE = 500.0
MARJIN_USD = 15.0
KALDIRAC = 5
POZISYON_USD = MARJIN_USD * KALDIRAC

os.makedirs(ARTIFACT_DIR, exist_ok=True)
matplotlib_lock = threading.Lock()

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
# GÜVENLİ MİN / MAX
# ==========================================
def safe_max(series_or_array):
    try:
        arr = np.asarray(series_or_array, dtype=float)
        arr = arr[np.isfinite(arr)]
        return float(np.max(arr)) if arr.size > 0 else np.nan
    except Exception:
        return np.nan

def safe_min(series_or_array):
    try:
        arr = np.asarray(series_or_array, dtype=float)
        arr = arr[np.isfinite(arr)]
        return float(np.min(arr)) if arr.size > 0 else np.nan
    except Exception:
        return np.nan

# ==========================================
# 3. VERİ ÇEKME
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
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        df = df.sort_index(ascending=True)  # Eski → Yeni (ters olmasın)
        return df if len(df) >= 60 else None
    except Exception as e:
        print(f"Veri çekme hatası {symbol}: {e}")
        return None

# ==========================================
# 4. TEKNİK & SMC ANALİZİ
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

    def find_swings(series, order=4):
        highs, lows = [], []
        for i in range(order, len(series) - order):
            window = series.iloc[i - order:i + order + 1]
            if series.iloc[i] >= window.max():
                highs.append((series.index[i], float(series.iloc[i])))
            if series.iloc[i] <= window.min():
                lows.append((series.index[i], float(series.iloc[i])))
        return highs, lows

    swing_highs, _ = find_swings(df["high"], order=4)
    _, swing_lows = find_swings(df["low"], order=4)

    last_swing_high = swing_highs[-1][1] if swing_highs else safe_max(df["high"].iloc[-40:])
    last_swing_low = swing_lows[-1][1] if swing_lows else safe_min(df["low"].iloc[-40:])

    df["FVG_up"] = (df["low"].shift(-1) > df["high"].shift(1)) & (df["close"] > df["open"])
    df["FVG_down"] = (df["high"].shift(-1) < df["low"].shift(1)) & (df["close"] < df["open"])

    fvg_zones = []
    for i in range(max(0, len(df) - 12), len(df) - 1):
        if df["FVG_up"].iloc[i]:
            fvg_zones.append({"type": "bullish", "top": float(df["low"].iloc[i + 1]), "bottom": float(df["high"].iloc[i - 1])})
        if df["FVG_down"].iloc[i]:
            fvg_zones.append({"type": "bearish", "top": float(df["low"].iloc[i - 1]), "bottom": float(df["high"].iloc[i + 1])})

    ob_zones = []
    atr_val = float(df["ATR"].iloc[-1]) if pd.notna(df["ATR"].iloc[-1]) else float(df["close"].iloc[-1]) * 0.02
    for i in range(max(0, len(df) - 18), len(df) - 2):
        body = abs(df["close"].iloc[i] - df["open"].iloc[i])
        if body > atr_val * 0.85:
            if df["close"].iloc[i] > df["open"].iloc[i]:
                ob_zones.append({"type": "bullish", "top": float(df["high"].iloc[i]), "bottom": float(df["low"].iloc[i])})
            else:
                ob_zones.append({"type": "bearish", "top": float(df["high"].iloc[i]), "bottom": float(df["low"].iloc[i])})

    son = df.iloc[-1]
    onceki = df.iloc[-2] if len(df) > 1 else son
    fiyat = float(son["close"])

    recent_slice = df.iloc[-10:-1] if len(df) >= 11 else df.iloc[:-1]
    recent_low = safe_min(recent_slice["low"])
    recent_high = safe_max(recent_slice["high"])

    look = 20
    bos_high = safe_max(df["high"].iloc[-look:-1]) if len(df) > look else safe_max(df["high"].iloc[:-1])
    bos_low = safe_min(df["low"].iloc[-look:-1]) if len(df) > look else safe_min(df["low"].iloc[:-1])

    eq_look = min(20, len(df) - 1)
    equal_high = equal_low = False
    tol = atr_val * 0.25
    highs = df["high"].iloc[-eq_look:]
    lows = df["low"].iloc[-eq_look:]
    for i in range(len(highs) - 1):
        if abs(highs.iloc[i] - highs.iloc[-1]) < tol:
            equal_high = True
            break
    for i in range(len(lows) - 1):
        if abs(lows.iloc[i] - lows.iloc[-1]) < tol:
            equal_low = True
            break

    fib_range = last_swing_high - last_swing_low if last_swing_high > last_swing_low else 0
    fib_levels = {}
    if fib_range > 0:
        fib_levels = {
            0.618: last_swing_high - fib_range * 0.618,
            0.786: last_swing_high - fib_range * 0.786,
            0.886: last_swing_high - fib_range * 0.886,
        }

    fib_zone = "Neutral"
    fib_score_l = 0.75
    fib_score_s = 0.75
    if fib_levels:
        near = any(abs(fiyat - lvl) < atr_val * 0.7 for lvl in fib_levels.values())
        if near:
            mid = (last_swing_high + last_swing_low) / 2
            if fiyat < mid:
                fib_zone = "Bullish"
                fib_score_l, fib_score_s = 1.12, 0.50
            else:
                fib_zone = "Bearish"
                fib_score_l, fib_score_s = 0.50, 1.12

    return {
        "fiyat": fiyat,
        "rsi": float(son["RSI"]) if pd.notna(son["RSI"]) else 50.0,
        "atr": atr_val,
        "bullish_fvg": bool(df["FVG_up"].iloc[-5:].any()),
        "bearish_fvg": bool(df["FVG_down"].iloc[-5:].any()),
        "above_ma200": fiyat > (float(son["MA200"]) if pd.notna(son["MA200"]) else 0),
        "above_ma100": fiyat > (float(son["MA100"]) if pd.notna(son["MA100"]) else 0),
        "above_ma50": fiyat > (float(son["MA50"]) if pd.notna(son["MA50"]) else 0),
        "sellside_sweep": (not np.isnan(recent_low)) and (son["low"] < recent_low) and (son["close"] > recent_low),
        "buyside_sweep": (not np.isnan(recent_high)) and (son["high"] > recent_high) and (son["close"] < recent_high),
        "bullish_bos": (not np.isnan(bos_high)) and (son["close"] > bos_high),
        "bearish_bos": (not np.isnan(bos_low)) and (son["close"] < bos_low),
        "bullish_ob": (onceki["close"] > onceki["open"]) and ((onceki["close"] - onceki["open"]) > atr_val * 0.8),
        "bearish_ob": (onceki["close"] < onceki["open"]) and ((onceki["open"] - onceki["close"]) > atr_val * 0.8),
        "equal_high": equal_high,
        "equal_low": equal_low,
        "fib_zone": fib_zone,
        "fib_score_l": fib_score_l,
        "fib_score_s": fib_score_s,
        "fib_levels": fib_levels,
        "fvg_zones": fvg_zones[-4:],
        "ob_zones": ob_zones[-4:],
        "last_swing_high": last_swing_high,
        "last_swing_low": last_swing_low,
    }

def skor_hesapla(info: dict) -> tuple[float, list, str]:
    skor_l, skor_s = 0.0, 0.0
    krit_l, krit_s = [], []

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
        skor_l += 1.80
        krit_l.append("BOS: 1.80 - Bullish BOS")
        skor_l += 1.35
        krit_l.append("CHoCH: 1.35 - Bullish CHoCH")
    if info.get("bearish_bos"):
        skor_s += 1.80
        krit_s.append("BOS: 1.80 - Bearish BOS")
        skor_s += 1.35
        krit_s.append("CHoCH: 1.35 - Bearish CHoCH")

    if info.get("bullish_fvg"):
        skor_l += 1.35
        krit_l.append("FVG: 1.35 - Bullish FVG")
        skor_l += 1.35
        krit_l.append("SFP: 1.35 - Bullish SFP")
    if info.get("bearish_fvg"):
        skor_s += 1.35
        krit_s.append("FVG: 1.35 - Bearish FVG")
        skor_s += 1.35
        krit_s.append("SFP: 1.35 - Bearish SFP")

    if info.get("above_ma50"):
        skor_l += 1.12
        krit_l.append("Breaker Block: 1.12 - Bullish breaker")
        skor_l += 1.12
        krit_l.append("PO3: 1.12 - Bullish AMD/PO3 proxy")
    else:
        skor_s += 1.12
        krit_s.append("Breaker Block: 1.12 - Bearish breaker")
        skor_s += 1.12
        krit_s.append("PO3: 1.12 - Bearish AMD/PO3 proxy")

    fib_zone = info.get("fib_zone", "Neutral")
    skor_l += info.get("fib_score_l", 0.75)
    skor_s += info.get("fib_score_s", 0.75)
    if fib_zone == "Bullish":
        krit_l.append("Fibonacci: 1.12 - Bullish Fib zone near 0.618/0.786/0.886")
        krit_s.append("Fibonacci: 0.50 - Neutral/Bearish Fib")
    elif fib_zone == "Bearish":
        krit_l.append("Fibonacci: 0.50 - Neutral/Bullish Fib")
        krit_s.append("Fibonacci: 1.12 - Bearish Fib zone near 0.618/0.786/0.886")
    else:
        krit_l.append("Fibonacci: 0.75 - Neutral Fib zone near 0.886")
        krit_s.append("Fibonacci: 0.75 - Neutral Fib zone near 0.886")

    rsi = info.get("rsi", 50)
    if rsi > 55:
        skor_l += 1.12
        krit_l.append(f"RSI: 1.12 - RSI {rsi:.1f} bullish")
    elif rsi < 45:
        skor_s += 1.12
        krit_s.append(f"RSI: 1.12 - RSI {rsi:.1f} bearish")
    else:
        skor_l += 0.39
        skor_s += 0.39
        krit_l.append(f"RSI: 0.39 - RSI {rsi:.1f} neutral")
        krit_s.append(f"RSI: 0.39 - RSI {rsi:.1f} neutral")

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

        if label == "1D":
            t1 = t
        elif label == "4H":
            t4 = t
        else:
            t1h = t

    bonus_l = 2.0 if (t1 == 1 and t4 == 1 and t1h == 1) else 0.0
    bonus_s = 2.0 if (t1 == -1 and t4 == -1 and t1h == -1) else 0.0
    return mtf_list, bonus_l, bonus_s

def analiz_yap(symbol: str):
    df_4h = veri_cek(symbol, "4H", 250)
    if df_4h is None or len(df_4h) < 60:
        return 0.0, [], 0.0, None, "YOK", [], 0.0, None

    info = basit_smc_ve_indikator(df_4h)
    if not info:
        return 0.0, [], 0.0, None, "YOK", [], 0.0, None

    skor, kriterler, yon = skor_hesapla(info)
    mtf_list, bonus_l, bonus_s = mtf_analiz(symbol)

    mtf_bonus = bonus_l if yon == "LONG" else bonus_s
    skor += mtf_bonus

    return skor, kriterler, info["fiyat"], df_4h, yon, mtf_list, mtf_bonus, info

# ==========================================
# 5. GRAFİK (BEYAZ TEMA + TERS OLMAYAN)
# ==========================================
def grafik_ciz(df: pd.DataFrame, symbol: str, yon: str, skor: float, info: dict = None) -> str | None:
    with matplotlib_lock:
        try:
            if df is None or len(df) < 30:
                return None

            # Zamanı kesinlikle eski → yeni yap
            df_plot = df.copy().sort_index(ascending=True)
            df_plot = df_plot.tail(min(120, len(df_plot)))

            if len(df_plot) < 20:
                return None

            df_plot = df_plot.rename(columns={
                'open': 'Open', 'high': 'High', 'low': 'Low',
                'close': 'Close', 'volume': 'Volume'
            })

            ma50 = df_plot['Close'].rolling(window=50, min_periods=10).mean()
            ma100 = df_plot['Close'].rolling(window=100, min_periods=20).mean()
            ma200 = df_plot['Close'].rolling(window=200, min_periods=30).mean()

            addplots = []
            if ma50.notna().sum() > 8:
                addplots.append(mpf.make_addplot(ma50, color='#1e88e5', width=1.4))
            if ma100.notna().sum() > 8:
                addplots.append(mpf.make_addplot(ma100, color='#fb8c00', width=1.4))
            if ma200.notna().sum() > 8:
                addplots.append(mpf.make_addplot(ma200, color='#43a047', width=1.6))

            hlines = dict(hlines=[], colors=[], linestyle='--', linewidths=1.0, alpha=0.75)
            if info and info.get("fib_levels"):
                for price in info["fib_levels"].values():
                    hlines["hlines"].append(price)
                    hlines["colors"].append('#5c6bc0')

            mc = mpf.make_marketcolors(
                up='#26a69a',
                down='#ef5350',
                edge='inherit',
                wick={'up': '#26a69a', 'down': '#ef5350'},
                volume='in'
            )

            s = mpf.make_mpf_style(
                base_mpf_style='yahoo',
                marketcolors=mc,
                facecolor='white',
                edgecolor='#e0e0e0',
                figcolor='white',
                gridcolor='#f0f0f0',
                gridstyle='-',
                y_on_right=False,
                rc={
                    'axes.labelcolor': '#333333',
                    'xtick.color': '#555555',
                    'ytick.color': '#555555',
                    'axes.titlesize': 11,
                    'axes.titleweight': 'bold'
                }
            )

            dosya = os.path.join(ARTIFACT_DIR, f"{symbol.replace('-', '_')}_chart.png")

            fig, axes = mpf.plot(
                df_plot,
                type='candle',
                style=s,
                addplot=addplots if addplots else None,
                hlines=hlines if hlines["hlines"] else None,
                title=f"{symbol.replace('-', '/')} | {yon} | Skor: {skor:.2f}/20 | 4H",
                returnfig=True,
                volume=False,
                figsize=(12, 6.5),
                tight_layout=True,
                datetime_format='%m-%d',
                xrotation=0,
                scale_padding=0.04
            )

            ax = axes[0]

            if info:
                for z in info.get("fvg_zones", []):
                    color = '#bbdefb90' if z["type"] == "bullish" else '#ffcdd290'
                    ax.axhspan(z["bottom"], z["top"], facecolor=color, edgecolor=None, zorder=0)

                for z in info.get("ob_zones", []):
                    color = '#90caf990' if z["type"] == "bullish" else '#ef9a9a90'
                    ax.axhspan(z["bottom"], z["top"], facecolor=color, edgecolor='#ffffff40', linewidth=0.5, zorder=1)

            ax.set_ylabel("Price", fontsize=10, color='#333333')
            ax.tick_params(colors='#555555')

            fig.savefig(dosya, dpi=160, bbox_inches='tight', facecolor='white', edgecolor='none')
            plt.close(fig)
            return dosya

        except Exception as e:
            plt.close('all')
            print(f"Grafik çizim hatası ({symbol}): {e}")
            return None

# ==========================================
# 6. VERİTABANI & TELEGRAM
# ==========================================
def db_baglanti():
    db_path = os.path.join(ARTIFACT_DIR, "islemler.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return conn, conn.cursor()

def db_kurulum():
    conn, c = db_baglanti()
    c.execute("""
        CREATE TABLE IF NOT EXISTS cuzdan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bakiye REAL DEFAULT 500.0
        )
    """)
    c.execute("SELECT COUNT(*) FROM cuzdan")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO cuzdan (bakiye) VALUES (?)", (ILK_BAKIYE,))

    c.execute("""
        CREATE TABLE IF NOT EXISTS islemler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT, yon TEXT, giris_fiyati REAL, hedef_r1 REAL, stop_loss REAL,
            orjinal_stop REAL, marjin REAL DEFAULT 15.0, kaldirac INTEGER DEFAULT 5,
            pozisyon_usd REAL DEFAULT 75.0, durum TEXT, kâr_r REAL, kâr_usd REAL DEFAULT 0.0,
            is_be INTEGER DEFAULT 0, tarih TEXT
        )
    """)
    conn.commit()
    conn.close()

def bakiye_guncelle(pnl_usd: float):
    conn, c = db_baglanti()
    c.execute("SELECT bakiye FROM cuzdan ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    mevcut = row[0] if row else ILK_BAKIYE
    yeni_bakiye = mevcut + pnl_usd
    c.execute("UPDATE cuzdan SET bakiye=? WHERE id=(SELECT MAX(id) FROM cuzdan)", (yeni_bakiye,))
    conn.commit()
    conn.close()
    return yeni_bakiye

def islem_kaydet(coin: str, yon: str, giris: float, stop: float):
    conn, c = db_baglanti()
    c.execute("SELECT id FROM islemler WHERE coin=? AND durum='ACIK'", (coin,))
    if c.fetchone():
        conn.close()
        return

    risk = abs(giris - stop) or (giris * 0.02)
    hedef = giris + (risk * 1.5) if yon == "LONG" else giris - (risk * 1.5)
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        INSERT INTO islemler (coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, marjin, kaldirac, pozisyon_usd, durum, kâr_r, kâr_usd, is_be, tarih)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACIK', 0.0, 0.0, 0, ?)
    """, (coin, yon, giris, hedef, stop, stop, MARJIN_USD, KALDIRAC, POZISYON_USD, tarih))
    conn.commit()
    conn.close()

def telegram_mesaj(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Token veya Chat ID eksik!")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram mesaj hatası: {e}")

def telegram_foto(path: str, caption: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if not path or not os.path.exists(path):
        telegram_mesaj(caption)
        return
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                timeout=30
            )
    except Exception as e:
        print(f"Grafik gönderme hatası: {e}")
        telegram_mesaj(caption)

def acik_islemleri_kontrol(guncel_fiyatlar: dict):
    conn, c = db_baglanti()
    c.execute("SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, is_be, pozisyon_usd, marjin FROM islemler WHERE durum='ACIK'")
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
            telegram_mesaj(f"🛡 <b>STOPU GİRİŞE ÇEK (BE)</b>\n\n📌 <b>{coin} ({yon} 5x)</b>\n📈 +1R kâra ulaşıldı!\n🎯 Yeni Stop: {giris:.6f}")
            is_be = 1

        if yon == "LONG":
            if anlik >= hedef:
                pnl_usd = (poz_usd * 0.03)
                yeni_b = bakiye_guncelle(pnl_usd)
                telegram_mesaj(f"✅ <b>{coin} (LONG 5x)</b>\n💰 Hedef TP1 (+1.50R) Ulaşıldı!\n💵 Kâr: +${pnl_usd:.2f}\n🏦 Cüzdan: ${yeni_b:.2f}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5, kâr_usd=? WHERE id=?", (pnl_usd, islem_id))
            elif anlik <= stop:
                durum, r_val = ('BE', 0.0) if is_be else ('LOSS', -1.0)
                pnl_usd = 0.0 if is_be else -marjin
                yeni_b = bakiye_guncelle(pnl_usd)
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} (LONG 5x)</b>\n{'Başabaş Kapanış (0R)' if is_be else 'Stop Oldu (-1R)'}\n💵 PnL: ${pnl_usd:+.2f}\n🏦 Cüzdan: ${yeni_b:.2f}")
                c.execute("UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?", (durum, r_val, pnl_usd, islem_id))
        else:
            if anlik <= hedef:
                pnl_usd = (poz_usd * 0.03)
                yeni_b = bakiye_guncelle(pnl_usd)
                telegram_mesaj(f"✅ <b>{coin} (SHORT 5x)</b>\n💰 Hedef TP1 (+1.50R) Ulaşıldı!\n💵 Kâr: +${pnl_usd:.2f}\n🏦 Cüzdan: ${yeni_b:.2f}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5, kâr_usd=? WHERE id=?", (pnl_usd, islem_id))
            elif anlik >= stop:
                durum, r_val = ('BE', 0.0) if is_be else ('LOSS', -1.0)
                pnl_usd = 0.0 if is_be else -marjin
                yeni_b = bakiye_guncelle(pnl_usd)
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} (SHORT 5x)</b>\n{'Başabaş Kapanış (0R)' if is_be else 'Stop Oldu (-1R)'}\n💵 PnL: ${pnl_usd:+.2f}\n🏦 Cüzdan: ${yeni_b:.2f}")
                c.execute("UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?", (durum, r_val, pnl_usd, islem_id))

    conn.commit()
    conn.close()

def saatlik_rapor_gonder(guncel_fiyatlar: dict):
    conn, c = db_baglanti()
    c.execute("SELECT bakiye FROM cuzdan ORDER BY id DESC LIMIT 1")
    row_bakiye = c.fetchone()
    bakiye = row_bakiye[0] if row_bakiye else 500.0

    c.execute("SELECT coin, yon, giris_fiyati, pozisyon_usd FROM islemler WHERE durum='ACIK'")
    acik_islemler = c.fetchall()

    unrealized_pnl = 0.0
    acik_detay = ""

    for coin, yon, giris, poz_usd in acik_islemler:
        anlik = guncel_fiyatlar.get(coin, giris)
        pnl = poz_usd * ((anlik - giris) / giris) if yon == "LONG" else poz_usd * ((giris - anlik) / giris)
        unrealized_pnl += pnl
        yuzde = ((anlik - giris) / giris * 100) if yon == "LONG" else ((giris - anlik) / giris * 100)
        acik_detay += f"• <b>{coin}</b> ({yon} 5x): ${pnl:+.2f} (%{yuzde * 5:+.2f})\n"

    c.execute("SELECT COUNT(*), SUM(kâr_usd) FROM islemler WHERE durum!='ACIK'")
    kapanan_count, toplam_kapanan_pnl = c.fetchone()
    toplam_kapanan_pnl = toplam_kapanan_pnl or 0.0

    c.execute("SELECT COUNT(*) FROM islemler WHERE durum='WIN'")
    win_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM islemler WHERE durum='LOSS'")
    loss_count = c.fetchone()[0]

    toplam_varlik = bakiye + unrealized_pnl

    rapor = "📊 <b>SAATLİK PERFORMANS VE CÜZDAN RAPORU</b>\n"
    rapor += "────────────────────\n"
    rapor += f"💵 <b>Kullanılabilir Bakiye:</b> ${bakiye:.2f}\n"
    rapor += f"📈 <b>Açık Pozisyon PnL:</b> ${unrealized_pnl:+.2f}\n"
    rapor += f"💎 <b>Toplam Varlık:</b> ${toplam_varlik:.2f}\n\n"
    rapor += f"🔓 <b>Açık Pozisyonlar ({len(acik_islemler)}):</b>\n"
    rapor += (acik_detay if acik_detay else "• Şu an açık pozisyon yok.\n") + "\n"
    rapor += f"📜 <b>Kapanan İşlemler:</b> {kapanan_count} Adet\n"
    rapor += f"✅ Win: {win_count} | ❌ Loss: {loss_count}\n"
    rapor += f"💰 Realize Edilen PnL: ${toplam_kapanan_pnl:+.2f}\n"
    rapor += "────────────────────"

    telegram_mesaj(rapor)
    conn.close()

def telegram_gonder(symbol, skor, kriterler, fiyat, df, yon, mtf_list, mtf_bonus, info=None):
    atr = ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        atr = fiyat * 0.02

    stop = fiyat - (atr * 1.5) if yon == "LONG" else fiyat + (atr * 1.5)
    hedef = (fiyat + (fiyat - stop) * 1.5) if yon == 'LONG' else (fiyat - (stop - fiyat) * 1.5)

    mesaj = f"🧠 <b>{symbol.replace('-', '/')} – {yon} (5x Izole)</b>\n"
    mesaj += f"⭐ Skor: {skor:.2f}/20\n"
    if mtf_bonus > 0:
        mesaj += f"⏱ MTF bonus: +{mtf_bonus:.2f}/2\n"
    mesaj += f"💼 Marjin: ${MARJIN_USD} | Pozisyon: ${POZISYON_USD}\n\n"

    mesaj += "<b>4H Kriterleri:</b>\n"
    for k in kriterler:
        mesaj += f"• {k}\n"

    mesaj += "\n<b>Zaman Dilimleri:</b>\n"
    for m in mtf_list:
        mesaj += f"{m}\n"

    mesaj += "\n────────────────────\n\n"
    mesaj += f"💰 <b>GİRİŞ:</b> {fiyat:.6f}\n"
    mesaj += f"🛡️ <b>SL:</b> {stop:.6f}\n"
    mesaj += f"🎯 <b>TP (1.5R):</b> {hedef:.6f}\n\n"
    mesaj += "⚠️ Algoritmik sinyaldir, yatırım tavsiyesi değildir."

    try:
        foto = grafik_ciz(df, symbol, yon, skor, info)
        kisa_baslik = f"🧠 <b>{symbol.replace('-', '/')} – {yon} 5x</b> | Skor: {skor:.2f}/20"
        if foto:
            telegram_foto(foto, kisa_baslik)
        telegram_mesaj(mesaj)
    except Exception as e:
        print(f"Gönderim hatası: {e}")
        telegram_mesaj(mesaj)

    islem_kaydet(symbol, yon, fiyat, stop)

# ==========================================
# 7. ÇALIŞTIRMA
# ==========================================
def tek_seferlik_tarama():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 Tarama başlatılıyor...")
    db_kurulum()

    guncel = {}
    for coin in HEDEF_COINLER:
        try:
            skor, krit, fiyat, df, yon, mtf, bonus, info = analiz_yap(coin)
            if fiyat:
                guncel[coin] = fiyat
            if skor >= MIN_SKOR and df is not None and yon in ["LONG", "SHORT"]:
                telegram_gonder(coin, skor, krit, fiyat, df, yon, mtf, bonus, info)
            time.sleep(0.25)
        except Exception as e:
            print(f"Hata ({coin}): {e}")

    acik_islemleri_kontrol(guncel)
    saatlik_rapor_gonder(guncel)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Tarama ve saatlik rapor tamamlandı.")

if __name__ == "__main__":
    telegram_mesaj("🚀 <b>SMC & MTF Bot Başlatıldı!</b>\n\n💰 Cüzdan: $500\n⚡ Kaldıraç: 5x Izole ($15 Marjin)\n⏱ Tarama aralığı: 1 Saat")

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

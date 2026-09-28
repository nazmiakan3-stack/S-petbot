#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanal İşlem Botu v14.0
- 20 coin
- Skor >= 15.0
- Bollinger: üst banda dokun/aş → SHORT | alt banda dokun/geç → LONG
- ATR SL = 1x | TP = 3x  (RR 1:3)
- Saatlik rapor + pozisyon açılışında grafik dosya adı
- Grafik: BB, EMA21, MA50, Fib, Vol+MA14, RSI, MACD, StochRSI+MA, KDJ
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
    return "OK - Sanal İşlem Botu v14.0 Aktif", 200

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
HOURLY_REPORT_MINUTES = 60

MIN_SKOR = 15.0
VOLUME_MA_LENGTH = 20
VOLUME_MIN_RATIO = 0.60

ADX_LENGTH = 14
ADX_MIN = 16.0
EMA_TREND_LENGTH = 21

BB_LENGTH = 20
BB_STD = 2.0
ATR_SL_MULT = 1.0   # SL = 1x ATR
ATR_TP_MULT = 3.0   # TP = 3x ATR  → RR 1:3

OKX_BASE = "https://www.okx.com"
os.makedirs(ARTIFACT_DIR, exist_ok=True)
matplotlib_lock = threading.Lock()
last_signal_time = {}
last_hourly_report = None

# ==========================================
# COİN LİSTESİ (20)
# ==========================================
HEDEF_COINLER = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT",
    "LTC-USDT", "NEAR-USDT", "APT-USDT", "SUI-USDT", "ARB-USDT",
    "OP-USDT", "INJ-USDT", "AAVE-USDT", "UNI-USDT", "TON-USDT",
]

# ==========================================
# YARDIMCI
# ==========================================
def safe_max(x):
    try:
        arr = np.asarray(x, dtype=float)
        arr = arr[np.isfinite(arr)]
        return float(np.max(arr)) if arr.size > 0 else np.nan
    except Exception:
        return np.nan

def safe_min(x):
    try:
        arr = np.asarray(x, dtype=float)
        arr = arr[np.isfinite(arr)]
        return float(np.min(arr)) if arr.size > 0 else np.nan
    except Exception:
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
    except Exception:
        return False, 0.0, 0.0

# ==========================================
# ANALİZ (SMC + BB + indikatörler)
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

    # Bollinger Bands
    bb = ta.bbands(df["close"], length=BB_LENGTH, std=BB_STD)
    bb_upper = bb_mid = bb_lower = None
    if bb is not None and not bb.empty:
        # pandas_ta kolon sırası: BBL, BBM, BBU, BBB, BBP
        cols = list(bb.columns)
        for c in cols:
            if c.startswith("BBU"):
                bb_upper = bb[c]
            elif c.startswith("BBM"):
                bb_mid = bb[c]
            elif c.startswith("BBL"):
                bb_lower = bb[c]

    adx_df = ta.adx(df["high"], df["low"], df["close"], length=ADX_LENGTH)
    adx_val = 0.0
    if adx_df is not None and not adx_df.empty:
        col = f"ADX_{ADX_LENGTH}"
        if col in adx_df.columns:
            adx_val = float(adx_df[col].iloc[-1])

    ema21 = ta.ema(df["close"], length=EMA_TREND_LENGTH)
    fiyat = float(df["close"].iloc[-1])
    high_son = float(df["high"].iloc[-1])
    low_son = float(df["low"].iloc[-1])
    ema21_val = float(ema21.iloc[-1]) if ema21 is not None else fiyat
    above_ema21 = fiyat > ema21_val

    upper_val = float(bb_upper.iloc[-1]) if bb_upper is not None and pd.notna(bb_upper.iloc[-1]) else None
    lower_val = float(bb_lower.iloc[-1]) if bb_lower is not None and pd.notna(bb_lower.iloc[-1]) else None
    mid_val = float(bb_mid.iloc[-1]) if bb_mid is not None and pd.notna(bb_mid.iloc[-1]) else None

    # Üst banda dokunma / aşma → SHORT
    # Alt banda dokunma / geçme → LONG
    bb_touch_upper = False
    bb_touch_lower = False
    if upper_val is not None:
        bb_touch_upper = (high_son >= upper_val) or (fiyat >= upper_val)
    if lower_val is not None:
        bb_touch_lower = (low_son <= lower_val) or (fiyat <= lower_val)

    def find_significant_swings(high_series, low_series, order=5, lookback=80):
        highs, lows = [], []
        data_len = len(high_series)
        start = max(order, data_len - lookback)
        for i in range(start, data_len - order):
            if high_series.iloc[i] == high_series.iloc[i - order:i + order + 1].max():
                highs.append((i, float(high_series.iloc[i])))
            if low_series.iloc[i] == low_series.iloc[i - order:i + order + 1].min():
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
    for i in range(len(highs) - 1):
        if abs(highs.iloc[i] - highs.iloc[-1]) < tol:
            equal_high = True
            break
    for i in range(len(lows) - 1):
        if abs(lows.iloc[i] - lows.iloc[-1]) < tol:
            equal_low = True
            break

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
        "bb_upper": upper_val, "bb_lower": lower_val, "bb_mid": mid_val,
        "bb_touch_upper": bb_touch_upper, "bb_touch_lower": bb_touch_lower,
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
        "last_swing_high": last_swing_high, "last_swing_low": last_swing_low,
    }

def skor_hesapla(info: dict):
    skor_l = skor_s = 0.0
    krit_l, krit_s = [], []

    # Bollinger ağırlıklı
    if info.get("bb_touch_lower"):
        skor_l += 4.50
        krit_l.append("BB Alt Band 4.50")
    if info.get("bb_touch_upper"):
        skor_s += 4.50
        krit_s.append("BB Üst Band 4.50")

    if info.get("sellside_sweep"):
        skor_l += 1.57; krit_l.append("Liquidity Sweep 1.57")
    if info.get("buyside_sweep"):
        skor_s += 1.57; krit_s.append("Liquidity Sweep 1.57")
    if info.get("bullish_ob"):
        skor_l += 1.57; krit_l.append("Bullish OB 1.57")
    if info.get("bearish_ob"):
        skor_s += 1.57; krit_s.append("Bearish OB 1.57")
    if info.get("bullish_bos"):
        skor_l += 3.15; krit_l += ["BOS 1.80", "CHoCH 1.35"]
    if info.get("bearish_bos"):
        skor_s += 3.15; krit_s += ["BOS 1.80", "CHoCH 1.35"]
    if info.get("bullish_fvg"):
        skor_l += 2.70; krit_l += ["FVG 1.35", "SFP 1.35"]
    if info.get("bearish_fvg"):
        skor_s += 2.70; krit_s += ["FVG 1.35", "SFP 1.35"]

    if info.get("above_ma50"):
        skor_l += 2.24; krit_l += ["Breaker 1.12", "PO3 1.12"]
    else:
        skor_s += 2.24; krit_s += ["Breaker 1.12", "PO3 1.12"]

    skor_l += info.get("fib_score_l", 0.70)
    skor_s += info.get("fib_score_s", 0.70)

    rsi = info.get("rsi", 50)
    if rsi > 58:
        skor_l += 1.12; krit_l.append(f"RSI {rsi:.1f} bullish")
    elif rsi < 42:
        skor_s += 1.12; krit_s.append(f"RSI {rsi:.1f} bearish")
    else:
        skor_l += 0.45; skor_s += 0.45

    if info.get("above_ma200"):
        skor_l += 1.35; krit_l.append("Above MA200")
    else:
        skor_s += 1.35; krit_s.append("Below MA200")
    if info.get("above_ma100"):
        skor_l += 0.90
    else:
        skor_s += 0.90
    if info.get("above_ma50"):
        skor_l += 0.90
    else:
        skor_s += 0.90

    if info.get("equal_high"):
        skor_s += 0.68
    if info.get("equal_low"):
        skor_l += 0.68

    adx = info.get("adx", 0)
    if adx >= 25:
        skor_l += 0.80; skor_s += 0.80
    elif adx >= ADX_MIN:
        skor_l += 0.40; skor_s += 0.40

    # Yön: BB sinyali öncelikli
    if info.get("bb_touch_upper") and not info.get("bb_touch_lower"):
        return skor_s, krit_s, "SHORT"
    if info.get("bb_touch_lower") and not info.get("bb_touch_upper"):
        return skor_l, krit_l, "LONG"
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
        c.execute("INSERT INTO cuzdan (bakiye, guncelleme) VALUES (?, ?)",
                  (ILK_BAKIYE, datetime.now().isoformat()))
    c.execute("""CREATE TABLE IF NOT EXISTS islemler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, yon TEXT, giris_fiyati REAL,
        hedef_r1 REAL, stop_loss REAL, orjinal_stop REAL, marjin REAL DEFAULT 15.0,
        kaldirac INTEGER DEFAULT 5, pozisyon_usd REAL DEFAULT 75.0, durum TEXT,
        kâr_r REAL DEFAULT 0.0, kâr_usd REAL DEFAULT 0.0, is_be INTEGER DEFAULT 0,
        tarih TEXT, skor REAL DEFAULT 0.0, grafik_dosya TEXT DEFAULT '')""")
    # Eski DB'ye kolon ekle
    try:
        c.execute("ALTER TABLE islemler ADD COLUMN grafik_dosya TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()

def acik_pozisyon_var_mi(coin):
    conn, c = db_baglanti()
    c.execute("SELECT id FROM islemler WHERE coin=? AND durum='ACIK'", (coin,))
    row = c.fetchone()
    conn.close()
    return row is not None

def islem_kaydet(coin, yon, giris, stop, hedef, skor=0.0, grafik_dosya=""):
    if acik_pozisyon_var_mi(coin):
        return False
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn, c = db_baglanti()
    c.execute("""INSERT INTO islemler
        (coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, marjin, kaldirac,
         pozisyon_usd, durum, tarih, skor, grafik_dosya)
        VALUES (?,?,?,?,?,?,?,?,?,'ACIK',?,?,?)""",
              (coin, yon, giris, hedef, stop, stop, MARJIN_USD, KALDIRAC,
               POZISYON_USD, tarih, skor, grafik_dosya or ""))
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

def acik_pozisyonlari_listele():
    conn, c = db_baglanti()
    c.execute("""SELECT coin, yon, giris_fiyati, stop_loss, hedef_r1, tarih, skor, grafik_dosya
                 FROM islemler WHERE durum='ACIK' ORDER BY id DESC""")
    rows = c.fetchall()
    conn.close()
    return rows

def bakiye_oku():
    conn, c = db_baglanti()
    c.execute("SELECT bakiye FROM cuzdan ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else ILK_BAKIYE

# ==========================================
# TELEGRAM
# ==========================================
def telegram_mesaj(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"Mesaj hatası: {e}")

def telegram_foto(path, caption=""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not path or not os.path.exists(path):
        if caption:
            telegram_mesaj(caption)
        return
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                timeout=30,
            )
    except Exception as e:
        print(f"Foto hatası: {e}")

# ==========================================
# SAATLİK RAPOR (dosya adları dahil)
# ==========================================
def saatlik_rapor_gonder():
    global last_hourly_report
    now = datetime.now()
    if last_hourly_report and (now - last_hourly_report) < timedelta(minutes=HOURLY_REPORT_MINUTES):
        return
    last_hourly_report = now

    pozisyonlar = acik_pozisyonlari_listele()
    bakiye = bakiye_oku()
    mesaj = f"📋 <b>Saatlik Rapor</b> — {now.strftime('%Y-%m-%d %H:%M')}\n"
    mesaj += f"💰 Cüzdan: <b>${bakiye:.2f}</b>\n"
    mesaj += f"📂 ARTIFACT_DIR: <code>{ARTIFACT_DIR}</code>\n\n"

    if not pozisyonlar:
        mesaj += "Açık pozisyon yok.\n"
    else:
        mesaj += f"<b>Açık pozisyonlar ({len(pozisyonlar)}):</b>\n"
        for coin, yon, giris, stop, hedef, tarih, skor, grafik in pozisyonlar:
            dosya_adi = grafik if grafik else f"{coin.replace('-', '_')}_chart.png"
            mesaj += (
                f"\n• <b>{coin}</b> {yon}\n"
                f"  Giriş: {giris:.6f} | SL: {stop:.6f} | TP: {hedef:.6f}\n"
                f"  Skor: {skor:.1f} | Açılış: {tarih}\n"
                f"  📎 Dosya: <code>{dosya_adi}</code>\n"
            )
    telegram_mesaj(mesaj)

# ==========================================
# GRAFİK
# ==========================================
def grafik_ciz(df, symbol, yon, skor, info=None, giris=None, stop=None, hedef=None):
    with matplotlib_lock:
        try:
            if df is None or len(df) < 50:
                return None

            df_plot = df.copy().sort_index(ascending=True).tail(120)
            df_plot = df_plot.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })

            close = df_plot["Close"]
            high = df_plot["High"]
            low = df_plot["Low"]
            volume = df_plot["Volume"]

            ema21 = ta.ema(close, length=21)
            ma50 = ta.sma(close, length=50)
            vol_ma14 = volume.rolling(14).mean()
            rsi = ta.rsi(close, length=14)

            bb = ta.bbands(close, length=BB_LENGTH, std=BB_STD)
            bb_u = bb_m = bb_l = None
            if bb is not None:
                for c in bb.columns:
                    if c.startswith("BBU"):
                        bb_u = bb[c]
                    elif c.startswith("BBM"):
                        bb_m = bb[c]
                    elif c.startswith("BBL"):
                        bb_l = bb[c]

            macd_df = ta.macd(close, fast=12, slow=26, signal=9)
            macd_line = macd_df.iloc[:, 0] if macd_df is not None else None
            macd_hist = macd_df.iloc[:, 1] if macd_df is not None else None
            macd_signal = macd_df.iloc[:, 2] if macd_df is not None else None

            stochrsi_df = ta.stochrsi(close, length=14, rsi_length=14, k=3, d=3)
            stochrsi_k = stochrsi_df.iloc[:, 0] if stochrsi_df is not None else None
            stochrsi_d = stochrsi_df.iloc[:, 1] if stochrsi_df is not None else None
            stochrsi_ma = stochrsi_k.rolling(5).mean() if stochrsi_k is not None else None

            kdj = ta.kdj(high, low, close, length=9, signal=3)
            kdj_k = kdj.iloc[:, 0] if kdj is not None else None
            kdj_d = kdj.iloc[:, 1] if kdj is not None else None
            kdj_j = kdj.iloc[:, 2] if kdj is not None else None

            fib_levels = (info or {}).get("fib_levels") or {}
            addplots = []

            if ema21 is not None and ema21.notna().sum() > 5:
                addplots.append(mpf.make_addplot(ema21, color="#ff9800", width=1.3, panel=0))
            if ma50 is not None and ma50.notna().sum() > 5:
                addplots.append(mpf.make_addplot(ma50, color="#1e88e5", width=1.5, panel=0))
            if bb_u is not None:
                addplots.append(mpf.make_addplot(bb_u, color="#e91e63", width=1.0, panel=0))
            if bb_m is not None:
                addplots.append(mpf.make_addplot(bb_m, color="#9e9e9e", width=0.8, panel=0))
            if bb_l is not None:
                addplots.append(mpf.make_addplot(bb_l, color="#4caf50", width=1.0, panel=0))

            addplots.append(mpf.make_addplot(volume, type="bar", color="#90caf9", panel=1, ylabel="Vol"))
            if vol_ma14 is not None and vol_ma14.notna().sum() > 3:
                addplots.append(mpf.make_addplot(vol_ma14, color="#e91e63", width=1.2, panel=1))

            if rsi is not None and rsi.notna().sum() > 5:
                addplots.append(mpf.make_addplot(rsi, color="#9c27b0", width=1.2, panel=2, ylabel="RSI"))
                addplots.append(mpf.make_addplot(pd.Series(70, index=df_plot.index), color="red", width=0.7, linestyle="--", panel=2))
                addplots.append(mpf.make_addplot(pd.Series(30, index=df_plot.index), color="green", width=0.7, linestyle="--", panel=2))

            if macd_line is not None:
                addplots.append(mpf.make_addplot(macd_line, color="#2196f3", width=1.1, panel=3, ylabel="MACD"))
            if macd_signal is not None:
                addplots.append(mpf.make_addplot(macd_signal, color="#ff5722", width=1.1, panel=3))
            if macd_hist is not None:
                colors = ["#26a69a" if v >= 0 else "#ef5350" for v in macd_hist.fillna(0)]
                addplots.append(mpf.make_addplot(macd_hist, type="bar", color=colors, panel=3))

            if stochrsi_k is not None:
                addplots.append(mpf.make_addplot(stochrsi_k, color="#00bcd4", width=1.1, panel=4, ylabel="StochRSI"))
            if stochrsi_d is not None:
                addplots.append(mpf.make_addplot(stochrsi_d, color="#ff9800", width=1.0, panel=4))
            if stochrsi_ma is not None:
                addplots.append(mpf.make_addplot(stochrsi_ma, color="#e91e63", width=1.0, panel=4))
            addplots.append(mpf.make_addplot(pd.Series(80, index=df_plot.index), color="red", width=0.6, linestyle="--", panel=4))
            addplots.append(mpf.make_addplot(pd.Series(20, index=df_plot.index), color="green", width=0.6, linestyle="--", panel=4))

            if kdj_k is not None:
                addplots.append(mpf.make_addplot(kdj_k, color="#2196f3", width=1.1, panel=5, ylabel="KDJ"))
            if kdj_d is not None:
                addplots.append(mpf.make_addplot(kdj_d, color="#ff9800", width=1.0, panel=5))
            if kdj_j is not None:
                addplots.append(mpf.make_addplot(kdj_j, color="#9c27b0", width=1.0, panel=5))
            addplots.append(mpf.make_addplot(pd.Series(80, index=df_plot.index), color="red", width=0.6, linestyle="--", panel=5))
            addplots.append(mpf.make_addplot(pd.Series(20, index=df_plot.index), color="green", width=0.6, linestyle="--", panel=5))

            hlines = {"hlines": [], "colors": [], "linestyle": [], "linewidths": [], "alpha": 0.85}
            if giris is not None:
                hlines["hlines"].append(giris); hlines["colors"].append("#2196f3")
                hlines["linestyle"].append("-"); hlines["linewidths"].append(1.6)
            if stop is not None:
                hlines["hlines"].append(stop); hlines["colors"].append("#f44336")
                hlines["linestyle"].append("-"); hlines["linewidths"].append(1.6)
            if hedef is not None:
                hlines["hlines"].append(hedef); hlines["colors"].append("#4caf50")
                hlines["linestyle"].append("-"); hlines["linewidths"].append(1.6)

            fib_colors = {
                0.236: "#9e9e9e", 0.382: "#ff9800", 0.5: "#2196f3",
                0.618: "#4caf50", 0.786: "#e91e63", 0.886: "#9c27b0",
            }
            for ratio, level in fib_levels.items():
                if level and np.isfinite(level):
                    hlines["hlines"].append(level)
                    hlines["colors"].append(fib_colors.get(ratio, "#757575"))
                    hlines["linestyle"].append("--")
                    hlines["linewidths"].append(0.9)

            mc = mpf.make_marketcolors(
                up="#26a69a", down="#ef5350", edge="inherit",
                wick={"up": "#26a69a", "down": "#ef5350"}, volume="in",
            )
            s = mpf.make_mpf_style(base_mpf_style="yahoo", marketcolors=mc, facecolor="white", gridstyle=":", y_on_right=False)
            panel_ratios = (0, 6, 1.2, 1.2, 1.2, 1.2, 1.2)

            dosya_adi = f"{symbol.replace('-', '_')}_chart.png"
            dosya = os.path.join(ARTIFACT_DIR, dosya_adi)
            fig, axes = mpf.plot(
                df_plot, type="candle", style=s,
                addplot=addplots if addplots else None,
                hlines=hlines if hlines["hlines"] else None,
                title=f"{symbol} | {yon} | Skor: {skor:.2f} | BB",
                returnfig=True, volume=False, figsize=(14, 12),
                panel_ratios=panel_ratios, tight_layout=True,
            )
            ax = axes[0]
            if yon == "LONG":
                ax.annotate("▲ LONG", xy=(0.02, 0.96), xycoords="axes fraction", fontsize=13, color="#26a69a", fontweight="bold")
            else:
                ax.annotate("▼ SHORT", xy=(0.02, 0.96), xycoords="axes fraction", fontsize=13, color="#ef5350", fontweight="bold")
            ax.annotate("BB | EMA21 | MA50 | Fib | Vol+MA14 | RSI | MACD | StochRSI | KDJ",
                        xy=(0.5, 1.02), xycoords="axes fraction", fontsize=8, ha="center", color="#555555")
            fig.savefig(dosya, dpi=120, bbox_inches="tight", facecolor="white")
            plt.close(fig)
            return dosya
        except Exception as e:
            plt.close("all")
            print(f"Grafik hatası: {e}")
            return None

# ==========================================
# SİNYAL
# ==========================================
def telegram_gonder(symbol, skor, kriterler, fiyat, df, yon, mtf_list, mtf_bonus, info=None):
    now = datetime.now()
    if acik_pozisyon_var_mi(symbol):
        return
    last = last_signal_time.get(symbol)
    if last and (now - last) < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES):
        return

    # Bollinger zorunlu: LONG için alt band, SHORT için üst band
    if yon == "LONG" and not (info and info.get("bb_touch_lower")):
        return
    if yon == "SHORT" and not (info and info.get("bb_touch_upper")):
        return

    hacim_ok, son_hacim, ort_hacim = hacim_yeterli_mi(df)
    if not hacim_ok:
        return
    if info and info.get("adx", 0) < ADX_MIN:
        return

    atr = info.get("atr") if info else None
    if atr is None or pd.isna(atr) or atr <= 0:
        atr = ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        atr = fiyat * 0.02

    # SL = 1x ATR, TP = 3x ATR
    if yon == "LONG":
        stop = fiyat - atr * ATR_SL_MULT
        hedef = fiyat + atr * ATR_TP_MULT
    else:
        stop = fiyat + atr * ATR_SL_MULT
        hedef = fiyat - atr * ATR_TP_MULT

    dosya_adi = f"{symbol.replace('-', '_')}_chart.png"
    foto = grafik_ciz(df, symbol, yon, skor, info, giris=fiyat, stop=stop, hedef=hedef)
    if foto:
        dosya_adi = os.path.basename(foto)

    if not islem_kaydet(symbol, yon, fiyat, stop, hedef, skor, grafik_dosya=dosya_adi):
        return
    last_signal_time[symbol] = now

    bb_txt = ""
    if info:
        if yon == "SHORT" and info.get("bb_upper"):
            bb_txt = f"BB Üst: {info['bb_upper']:.6f}"
        elif yon == "LONG" and info.get("bb_lower"):
            bb_txt = f"BB Alt: {info['bb_lower']:.6f}"

    mesaj = f"🧠 <b>{symbol.replace('-', '/')} – {yon} (5x)</b>\n"
    mesaj += f"⭐ Skor: <b>{skor:.2f}</b> | Min: {MIN_SKOR}\n"
    if mtf_bonus > 0:
        mesaj += f"⏱ MTF bonus: +{mtf_bonus:.2f}\n"
    mesaj += f"📐 Bollinger: {bb_txt}\n"
    mesaj += f"💼 Marjin: ${MARJIN_USD} | Pozisyon: ${POZISYON_USD}\n"
    mesaj += f"📊 Hacim: %{(son_hacim / ort_hacim * 100):.0f} | ADX: {info.get('adx', 0):.1f} | ATR: {atr:.6f}\n\n"
    mesaj += "<b>Kriterler:</b>\n"
    for k in kriterler[:8]:
        mesaj += f"• {k}\n"
    mesaj += (
        f"\n────────────────────\n"
        f"💰 <b>GİRİŞ:</b> {fiyat:.6f}\n"
        f"🛡 <b>SL (1×ATR):</b> {stop:.6f}\n"
        f"🎯 <b>TP (3×ATR):</b> {hedef:.6f}\n"
        f"📎 <b>Dosya:</b> <code>{dosya_adi}</code>\n\n"
        f"⚠️ Sanal işlem | RR 1:3"
    )
    if foto:
        telegram_foto(foto, f"{symbol} | {yon} | {skor:.2f} | {dosya_adi}")
    telegram_mesaj(mesaj)

# ==========================================
# AÇIK POZİSYON KONTROLÜ
# ==========================================
def acik_islemleri_kontrol(guncel_fiyatlar: dict):
    conn, c = db_baglanti()
    c.execute("""SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop,
                        is_be, pozisyon_usd, marjin, grafik_dosya
                 FROM islemler WHERE durum='ACIK'""")
    rows = c.fetchall()
    for row in rows:
        islem_id, coin, yon, giris, hedef, stop, orj_stop, is_be, poz_usd, marjin, grafik = row
        if coin not in guncel_fiyatlar:
            continue
        anlik = guncel_fiyatlar[coin]
        risk = abs(giris - (orj_stop or stop))
        if risk <= 0:
            continue
        mevcut_r = (anlik - giris) / risk if yon == "LONG" else (giris - anlik) / risk
        dosya_notu = f"\n📎 {grafik}" if grafik else ""

        if mevcut_r >= 1.0 and is_be == 0:
            c.execute("UPDATE islemler SET stop_loss=?, is_be=1 WHERE id=?", (giris, islem_id))
            telegram_mesaj(f"🛡 <b>{coin}</b> Stop girişe çekildi (BE){dosya_notu}")

        if yon == "LONG":
            if anlik >= hedef:
                pnl = marjin * ATR_TP_MULT  # 3R
                yeni = bakiye_guncelle(pnl)
                telegram_mesaj(f"✅ <b>{coin} LONG TP 3R</b>\n+${pnl:.2f} | Cüzdan: ${yeni:.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=3.0, kâr_usd=? WHERE id=?", (pnl, islem_id))
            elif anlik <= stop:
                pnl = 0.0 if is_be else -marjin
                bakiye_guncelle(pnl)
                durum = "BE" if is_be else "LOSS"
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} LONG</b> {durum}\n${pnl:+.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?",
                          (durum, 0.0 if is_be else -1.0, pnl, islem_id))
        else:
            if anlik <= hedef:
                pnl = marjin * ATR_TP_MULT
                yeni = bakiye_guncelle(pnl)
                telegram_mesaj(f"✅ <b>{coin} SHORT TP 3R</b>\n+${pnl:.2f} | Cüzdan: ${yeni:.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=3.0, kâr_usd=? WHERE id=?", (pnl, islem_id))
            elif anlik >= stop:
                pnl = 0.0 if is_be else -marjin
                bakiye_guncelle(pnl)
                durum = "BE" if is_be else "LOSS"
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} SHORT</b> {durum}\n${pnl:+.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?",
                          (durum, 0.0 if is_be else -1.0, pnl, islem_id))
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
            # Skor + BB yön zorunlu
            if skor >= MIN_SKOR and df is not None and yon in ("LONG", "SHORT"):
                if info and (info.get("bb_touch_upper") or info.get("bb_touch_lower")):
                    telegram_gonder(coin, skor, krit, fiyat, df, yon, mtf, bonus, info)
            time.sleep(0.12)
        except Exception as e:
            print(f"Hata {coin}: {e}")
    acik_islemleri_kontrol(guncel)
    try:
        saatlik_rapor_gonder()
    except Exception as e:
        print(f"Rapor hatası: {e}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tarama bitti.")
    return guncel

# ==========================================
# BAŞLANGIÇ
# ==========================================
if __name__ == "__main__":
    telegram_mesaj(
        "🚀 <b>Sanal İşlem Botu v14.0 Başlatıldı</b>\n\n"
        "💰 Başlangıç: $500\n"
        "⚡ 5x İzole | $15 Marjin\n"
        f"✅ Skor: <b>{MIN_SKOR}+</b>\n"
        "✅ Bollinger: Üst→SHORT | Alt→LONG\n"
        f"✅ SL: {ATR_SL_MULT}×ATR | TP: {ATR_TP_MULT}×ATR (RR 1:3)\n"
        f"✅ {len(HEDEF_COINLER)} coin\n"
        "📋 Saatlik rapor + grafik dosya adı\n"
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanal Islem Botu v15.1
- Ana zaman dilimi: 1H (4H kaldirildi)
- Bollinger KALDIRILDI
- SuperTrend (10, 3) al/sat + RSI kosulu (yeterli giris sarti)
  LONG : SuperTrend BUY ve RSI < 70
  SHORT: SuperTrend SELL ve RSI > 30
- Skor sadece bilgi; giris icin zorunlu degil
- ATR SL = 1x | TP = 3x (RR 1:3)
- Saatlik rapor + PnL + grafik dosya adi
- Hacim >= %35 | ADX filtresi kaldirildi (giriste)
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
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf

from flask import Flask

DOSYA_ADI = os.path.basename(__file__)

app = Flask(__name__)

@app.route("/")
def health_check():
    return f"OK - Sanal Islem Botu v15.1 | {DOSYA_ADI}", 200

def run_flask():
    port = int(os.environ.get("PORT", 10003))
    app.run(host="0.0.0.0", port=port)

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

MIN_SKOR = 0.0  # giris icin skor zorunlu degil; ST+RSI yeterli
VOLUME_MA_LENGTH = 20
VOLUME_MIN_RATIO = 0.35

ADX_LENGTH = 14
ADX_MIN = 0.0  # giriste ADX engeli yok
EMA_TREND_LENGTH = 21

# SuperTrend — TradingView klasik: length=10, multiplier=3
ST_LENGTH = 10
ST_MULT = 3.0

# RSI filtresi (ST sinyali ile birlikte yeterli)
RSI_LONG_MAX = 70.0   # LONG: RSI asiri alimda olmasin
RSI_SHORT_MIN = 30.0  # SHORT: RSI asiri satimda olmasin

ATR_SL_MULT = 1.0
ATR_TP_MULT = 3.0

OKX_BASE = "https://www.okx.com"
os.makedirs(ARTIFACT_DIR, exist_ok=True)
matplotlib_lock = threading.Lock()
last_signal_time = {}
last_hourly_report = None

HEDEF_COINLER = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT",
    "LTC-USDT", "NEAR-USDT", "APT-USDT", "SUI-USDT", "ARB-USDT",
    "OP-USDT", "INJ-USDT", "AAVE-USDT", "UNI-USDT", "TON-USDT",
    "XAG-USDT-SWAP",  # Gumus (grafikteki pair)
]


def acilis_mesaji_goster():
    print("=" * 60)
    print("SANAL ISLEM BOTU v15.1 — SuperTrend + RSI | 1H")
    print(f"Dosya : {DOSYA_ADI}")
    print(f"TF    : 1H")
    print(f"ST    : length={ST_LENGTH} mult={ST_MULT}")
    print(f"RSI   : LONG < {RSI_LONG_MAX} | SHORT > {RSI_SHORT_MIN}")
    print("Giris : ST BUY/SELL + RSI (skor/ADX zorunlu degil)")
    print("=" * 60)


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


def veri_cek(symbol: str, bar: str = "1H", limit: int = 250):
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
            "volCcy", "volCcyQuote", "confirm",
        ])
        df = df.iloc[::-1].reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_datetime(df["ts"].astype(float), unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        return df.sort_index(ascending=True) if len(df) >= 30 else None
    except Exception as e:
        print(f"Veri hatasi {symbol}: {e}")
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


def supertrend_hesapla(df: pd.DataFrame):
    st = ta.supertrend(df["high"], df["low"], df["close"], length=ST_LENGTH, multiplier=ST_MULT)
    if st is None or st.empty:
        return None, 0.0, False, False
    line_col = dir_col = None
    for c in st.columns:
        cs = str(c)
        if cs.startswith("SUPERTd"):
            dir_col = c
        elif cs.startswith("SUPERT_") and "SUPERTd" not in cs and "SUPERTl" not in cs and "SUPERTs" not in cs:
            line_col = c
    if line_col is None or dir_col is None:
        if len(st.columns) >= 2:
            line_col = st.columns[0]
            dir_col = st.columns[1]
        else:
            return None, 0.0, False, False

    st_line = st[line_col]
    st_dir = st[dir_col]
    d_now = float(st_dir.iloc[-1]) if pd.notna(st_dir.iloc[-1]) else 0.0
    d_prev = float(st_dir.iloc[-2]) if len(st_dir) > 1 and pd.notna(st_dir.iloc[-2]) else d_now

    st_buy = (d_prev < 0 and d_now > 0)
    st_sell = (d_prev > 0 and d_now < 0)

    if not st_buy and not st_sell and len(df) >= 2:
        line_now = float(st_line.iloc[-1]) if pd.notna(st_line.iloc[-1]) else None
        line_prev = float(st_line.iloc[-2]) if pd.notna(st_line.iloc[-2]) else line_now
        if line_now is not None and line_prev is not None:
            c0, c1 = float(df["close"].iloc[-2]), float(df["close"].iloc[-1])
            if c0 < line_prev and c1 >= line_now:
                st_buy = True
            if c0 > line_prev and c1 <= line_now:
                st_sell = True

    return st_line, d_now, st_buy, st_sell


def basit_smc_ve_indikator(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 60:
        return {}
    df = df.copy()
    df["RSI"] = ta.rsi(df["close"], length=14)
    df["MA50"] = ta.sma(df["close"], length=50)
    df["MA100"] = ta.sma(df["close"], length=100)
    df["MA200"] = ta.sma(df["close"], length=200)
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    st_line, st_dir, st_buy, st_sell = supertrend_hesapla(df)

    adx_df = ta.adx(df["high"], df["low"], df["close"], length=ADX_LENGTH)
    adx_val = 0.0
    if adx_df is not None and not adx_df.empty:
        col = f"ADX_{ADX_LENGTH}"
        if col in adx_df.columns:
            adx_val = float(adx_df[col].iloc[-1])

    fiyat = float(df["close"].iloc[-1])
    rsi_val = float(df["RSI"].iloc[-1]) if pd.notna(df["RSI"].iloc[-1]) else 50.0
    atr_val = float(df["ATR"].iloc[-1]) if pd.notna(df["ATR"].iloc[-1]) else fiyat * 0.02
    son = df.iloc[-1]
    onceki = df.iloc[-2] if len(df) > 1 else son
    recent = df.iloc[-10:-1] if len(df) >= 11 else df.iloc[:-1]
    recent_low = safe_min(recent["low"])
    recent_high = safe_max(recent["high"])
    bos_high = safe_max(df["high"].iloc[-20:-1]) if len(df) > 20 else safe_max(df["high"].iloc[:-1])
    bos_low = safe_min(df["low"].iloc[-20:-1]) if len(df) > 20 else safe_min(df["low"].iloc[:-1])

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

    fib_score_l = fib_score_s = 0.70
    fib_levels = {}
    fib_range = last_swing_high - last_swing_low
    if fib_range > fiyat * 0.008:
        fib_levels = {r: last_swing_high - fib_range * r for r in [0.236, 0.382, 0.5, 0.618, 0.786, 0.886]}
        nearest = min(fib_levels.items(), key=lambda x: abs(fiyat - x[1]))
        if abs(fiyat - nearest[1]) < atr_val * 1.2:
            if nearest[0] in (0.618, 0.786, 0.886):
                fib_score_l, fib_score_s = 1.25, 0.40
            elif nearest[0] in (0.236, 0.382):
                fib_score_l, fib_score_s = 0.40, 1.15

    st_line_val = None
    if st_line is not None and pd.notna(st_line.iloc[-1]):
        st_line_val = float(st_line.iloc[-1])

    return {
        "fiyat": fiyat,
        "rsi": rsi_val,
        "atr": atr_val,
        "adx": adx_val,
        "st_line": st_line_val,
        "st_dir": st_dir,
        "st_buy": bool(st_buy),
        "st_sell": bool(st_sell),
        "rsi_ok_long": rsi_val < RSI_LONG_MAX,
        "rsi_ok_short": rsi_val > RSI_SHORT_MIN,
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
        "equal_high": equal_high,
        "equal_low": equal_low,
        "fib_score_l": fib_score_l,
        "fib_score_s": fib_score_s,
        "fib_levels": fib_levels,
        "last_swing_high": last_swing_high,
        "last_swing_low": last_swing_low,
    }


def skor_hesapla(info: dict):
    skor_l = skor_s = 0.0
    krit_l, krit_s = [], []

    if info.get("st_buy"):
        skor_l += 5.0
        krit_l.append("SuperTrend BUY 5.0")
    if info.get("st_sell"):
        skor_s += 5.0
        krit_s.append("SuperTrend SELL 5.0")

    if info.get("st_dir") == 1 or info.get("st_dir") == 1.0:
        skor_l += 1.5
        krit_l.append("ST trend UP 1.5")
    elif info.get("st_dir") == -1 or info.get("st_dir") == -1.0:
        skor_s += 1.5
        krit_s.append("ST trend DOWN 1.5")

    if info.get("rsi_ok_long") and info.get("st_buy"):
        skor_l += 2.0
        krit_l.append(f"RSI {info.get('rsi', 0):.1f}<{RSI_LONG_MAX} OK")
    if info.get("rsi_ok_short") and info.get("st_sell"):
        skor_s += 2.0
        krit_s.append(f"RSI {info.get('rsi', 0):.1f}>{RSI_SHORT_MIN} OK")

    if info.get("sellside_sweep"):
        skor_l += 1.57
        krit_l.append("Liquidity Sweep 1.57")
    if info.get("buyside_sweep"):
        skor_s += 1.57
        krit_s.append("Liquidity Sweep 1.57")
    if info.get("bullish_ob"):
        skor_l += 1.57
        krit_l.append("Bullish OB 1.57")
    if info.get("bearish_ob"):
        skor_s += 1.57
        krit_s.append("Bearish OB 1.57")
    if info.get("bullish_bos"):
        skor_l += 3.15
        krit_l += ["BOS 1.80", "CHoCH 1.35"]
    if info.get("bearish_bos"):
        skor_s += 3.15
        krit_s += ["BOS 1.80", "CHoCH 1.35"]
    if info.get("bullish_fvg"):
        skor_l += 2.70
        krit_l += ["FVG 1.35", "SFP 1.35"]
    if info.get("bearish_fvg"):
        skor_s += 2.70
        krit_s += ["FVG 1.35", "SFP 1.35"]

    if info.get("above_ma50"):
        skor_l += 2.24
        krit_l += ["Breaker 1.12", "PO3 1.12"]
    else:
        skor_s += 2.24
        krit_s += ["Breaker 1.12", "PO3 1.12"]

    skor_l += info.get("fib_score_l", 0.70)
    skor_s += info.get("fib_score_s", 0.70)

    rsi = info.get("rsi", 50)
    if rsi > 58:
        skor_l += 1.12
        krit_l.append(f"RSI {rsi:.1f} bullish")
    elif rsi < 42:
        skor_s += 1.12
        krit_s.append(f"RSI {rsi:.1f} bearish")
    else:
        skor_l += 0.45
        skor_s += 0.45

    if info.get("above_ma200"):
        skor_l += 1.35
        krit_l.append("Above MA200")
    else:
        skor_s += 1.35
        krit_s.append("Below MA200")
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
        skor_l += 0.80
        skor_s += 0.80
    elif adx >= ADX_MIN:
        skor_l += 0.40
        skor_s += 0.40

    if info.get("st_buy") and not info.get("st_sell"):
        return skor_l, krit_l, "LONG"
    if info.get("st_sell") and not info.get("st_buy"):
        return skor_s, krit_s, "SHORT"
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
        mtf_list.append(f"• {label}: {'LONG' if t == 1 else 'SHORT'}")
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
    df = veri_cek(symbol, "1H", 250)
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
    c.execute("""SELECT coin, yon, giris_fiyati, stop_loss, hedef_r1, tarih, skor, grafik_dosya, marjin, kaldirac
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
        print(f"Mesaj hatasi: {e}")


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
        print(f"Foto hatasi: {e}")


def saatlik_rapor_gonder(guncel_fiyatlar: dict):
    global last_hourly_report
    now = datetime.now()
    if last_hourly_report and (now - last_hourly_report) < timedelta(minutes=HOURLY_REPORT_MINUTES):
        return
    last_hourly_report = now

    pozisyonlar = acik_pozisyonlari_listele()
    bakiye = bakiye_oku()
    mesaj = f"SAATLIK PnL RAPORU\n"
    mesaj += f"Zaman: {now.strftime('%Y-%m-%d %H:%M')}\n"
    mesaj += f"Dosya: {DOSYA_ADI}\n"
    mesaj += f"Cuzdan: ${bakiye:.2f}\n\n"

    if not pozisyonlar:
        mesaj += "Acik pozisyon yok.\n"
    else:
        mesaj += f"Acik ({len(pozisyonlar)}):\n"
        toplam = 0.0
        for row in pozisyonlar:
            coin, yon, giris, stop, hedef, tarih, skor, grafik, marjin, kaldirac = row
            anlik = guncel_fiyatlar.get(coin, giris)
            if yon == "LONG":
                pnl_yuzde = ((anlik - giris) / giris) * 100 * kaldirac
            else:
                pnl_yuzde = ((giris - anlik) / giris) * 100 * kaldirac
            pnl_usd = (marjin * kaldirac) * (pnl_yuzde / 100)
            toplam += pnl_usd
            mesaj += (
                f"\n• {coin} {yon}\n"
                f"  Giris {giris:.4f} | Anlik {anlik:.4f}\n"
                f"  PnL: ${pnl_usd:+.2f} (%{pnl_yuzde:+.2f})\n"
                f"  Dosya: {grafik or 'Yok'}\n"
            )
        mesaj += f"\nToplam acik PnL: ${toplam:+.2f}\n"
    telegram_mesaj(mesaj)


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

            st = ta.supertrend(high, low, close, length=ST_LENGTH, multiplier=ST_MULT)
            st_line = None
            if st is not None:
                for c in st.columns:
                    cs = str(c)
                    if cs.startswith("SUPERT_") and "SUPERTd" not in cs and "SUPERTl" not in cs and "SUPERTs" not in cs:
                        st_line = st[c]
                        break
                if st_line is None and len(st.columns):
                    st_line = st.iloc[:, 0]

            macd_df = ta.macd(close, fast=12, slow=26, signal=9)
            macd_line = macd_df.iloc[:, 0] if macd_df is not None else None
            macd_hist = macd_df.iloc[:, 1] if macd_df is not None else None
            macd_signal = macd_df.iloc[:, 2] if macd_df is not None else None

            stochrsi_df = ta.stochrsi(close, length=14, rsi_length=14, k=3, d=3)
            stochrsi_k = stochrsi_df.iloc[:, 0] if stochrsi_df is not None else None
            stochrsi_d = stochrsi_df.iloc[:, 1] if stochrsi_df is not None else None

            kdj = ta.kdj(high, low, close, length=9, signal=3)
            kdj_k = kdj.iloc[:, 0] if kdj is not None else None
            kdj_d = kdj.iloc[:, 1] if kdj is not None else None
            kdj_j = kdj.iloc[:, 2] if kdj is not None else None

            addplots = []
            if ema21 is not None:
                addplots.append(mpf.make_addplot(ema21, color="#ff9800", width=1.2, panel=0))
            if ma50 is not None:
                addplots.append(mpf.make_addplot(ma50, color="#1e88e5", width=1.4, panel=0))
            if st_line is not None:
                addplots.append(mpf.make_addplot(st_line, color="#26a69a", width=1.6, panel=0))

            addplots.append(mpf.make_addplot(volume, type="bar", color="#90caf9", panel=1, ylabel="Vol"))
            if vol_ma14 is not None:
                addplots.append(mpf.make_addplot(vol_ma14, color="#e91e63", width=1.1, panel=1))

            if rsi is not None:
                addplots.append(mpf.make_addplot(rsi, color="#9c27b0", width=1.2, panel=2, ylabel="RSI"))
                addplots.append(mpf.make_addplot(pd.Series(70, index=df_plot.index), color="red", width=0.7, linestyle="--", panel=2))
                addplots.append(mpf.make_addplot(pd.Series(30, index=df_plot.index), color="green", width=0.7, linestyle="--", panel=2))

            if macd_line is not None:
                addplots.append(mpf.make_addplot(macd_line, color="#2196f3", width=1.0, panel=3, ylabel="MACD"))
            if macd_signal is not None:
                addplots.append(mpf.make_addplot(macd_signal, color="#ff5722", width=1.0, panel=3))
            if macd_hist is not None:
                colors = ["#26a69a" if v >= 0 else "#ef5350" for v in macd_hist.fillna(0)]
                addplots.append(mpf.make_addplot(macd_hist, type="bar", color=colors, panel=3))

            if stochrsi_k is not None:
                addplots.append(mpf.make_addplot(stochrsi_k, color="#00bcd4", width=1.0, panel=4, ylabel="StochRSI"))
            if stochrsi_d is not None:
                addplots.append(mpf.make_addplot(stochrsi_d, color="#ff9800", width=1.0, panel=4))

            if kdj_k is not None:
                addplots.append(mpf.make_addplot(kdj_k, color="#2196f3", width=1.0, panel=5, ylabel="KDJ"))
            if kdj_d is not None:
                addplots.append(mpf.make_addplot(kdj_d, color="#ff9800", width=1.0, panel=5))
            if kdj_j is not None:
                addplots.append(mpf.make_addplot(kdj_j, color="#9c27b0", width=1.0, panel=5))

            hlines = {"hlines": [], "colors": [], "linestyle": [], "linewidths": [], "alpha": 0.85}
            if giris is not None:
                hlines["hlines"].append(giris)
                hlines["colors"].append("#2196f3")
                hlines["linestyle"].append("-")
                hlines["linewidths"].append(1.6)
            if stop is not None:
                hlines["hlines"].append(stop)
                hlines["colors"].append("#f44336")
                hlines["linestyle"].append("-")
                hlines["linewidths"].append(1.6)
            if hedef is not None:
                hlines["hlines"].append(hedef)
                hlines["colors"].append("#4caf50")
                hlines["linestyle"].append("-")
                hlines["linewidths"].append(1.6)

            dosya_adi = f"{symbol.replace('-', '_')}_chart.png"
            dosya = os.path.join(ARTIFACT_DIR, dosya_adi)
            mc = mpf.make_marketcolors(
                up="#26a69a", down="#ef5350", edge="inherit",
                wick={"up": "#26a69a", "down": "#ef5350"}, volume="in",
            )
            s = mpf.make_mpf_style(base_mpf_style="yahoo", marketcolors=mc, facecolor="white", gridstyle=":")
            fig, axes = mpf.plot(
                df_plot, type="candle", style=s, addplot=addplots,
                hlines=hlines if hlines["hlines"] else None,
                title=f"{symbol} | {yon} | Skor {skor:.1f} | ST+RSI | {DOSYA_ADI}",
                returnfig=True, volume=False, figsize=(14, 12),
                panel_ratios=(0, 6, 1.2, 1.2, 1.2, 1.2, 1.2), tight_layout=True,
            )
            ax = axes[0]
            ax.annotate(
                f"{'LONG' if yon == 'LONG' else 'SHORT'}",
                xy=(0.02, 0.96), xycoords="axes fraction", fontsize=13,
                color="#26a69a" if yon == "LONG" else "#ef5350", fontweight="bold",
            )
            fig.savefig(dosya, dpi=120, bbox_inches="tight", facecolor="white")
            plt.close(fig)
            return dosya
        except Exception as e:
            plt.close("all")
            print(f"Grafik hatasi: {e}")
            return None


def telegram_gonder(symbol, skor, kriterler, fiyat, df, yon, mtf_list, mtf_bonus, info=None):
    now = datetime.now()
    if acik_pozisyon_var_mi(symbol):
        return
    last = last_signal_time.get(symbol)
    if last and (now - last) < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES):
        return

    if yon == "LONG":
        if not (info and info.get("st_buy") and info.get("rsi_ok_long")):
            return
    elif yon == "SHORT":
        if not (info and info.get("st_sell") and info.get("rsi_ok_short")):
            return
    else:
        return

    hacim_ok, son_hacim, ort_hacim = hacim_yeterli_mi(df)
    if not hacim_ok:
        return
    # ADX ve skor giriste zorunlu degil — ST + RSI yeterli

    atr = info.get("atr") if info else None
    if not atr or atr <= 0:
        atr = fiyat * 0.02

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

    mesaj = (
        f"<b>{symbol.replace('-', '/')} – {yon} (5x)</b>\n"
        f"Dosya: <code>{DOSYA_ADI}</code>\n"
        f"Skor: <b>{skor:.2f}</b>\n"
        f"SuperTrend + RSI\n"
        f"RSI: {info.get('rsi', 0):.1f} | ADX: {info.get('adx', 0):.1f}\n"
        f"Marjin ${MARJIN_USD} | Poz ${POZISYON_USD}\n\n"
        f"GIRIS: {fiyat:.6f}\n"
        f"SL (1xATR): {stop:.6f}\n"
        f"TP (3xATR): {hedef:.6f}\n"
        f"Grafik: <code>{dosya_adi}</code>\n"
        f"Sanal | RR 1:3"
    )
    if foto:
        telegram_foto(foto, mesaj)
    else:
        telegram_mesaj(mesaj)


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
        dosya_notu = f"\nDosya: {DOSYA_ADI}" + (f" | {grafik}" if grafik else "")

        if mevcut_r >= 1.0 and is_be == 0:
            c.execute("UPDATE islemler SET stop_loss=?, is_be=1 WHERE id=?", (giris, islem_id))
            telegram_mesaj(f"{coin} BE (stop girise){dosya_notu}")

        if yon == "LONG":
            if anlik >= hedef:
                pnl = marjin * ATR_TP_MULT
                yeni = bakiye_guncelle(pnl)
                telegram_mesaj(f"{coin} LONG TP 3R +${pnl:.2f} | Cuzdan ${yeni:.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=3.0, kâr_usd=? WHERE id=?", (pnl, islem_id))
            elif anlik <= stop:
                pnl = 0.0 if is_be else -marjin
                bakiye_guncelle(pnl)
                durum = "BE" if is_be else "LOSS"
                telegram_mesaj(f"{coin} LONG {durum} ${pnl:+.2f}{dosya_notu}")
                c.execute(
                    "UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?",
                    (durum, 0.0 if is_be else -1.0, pnl, islem_id),
                )
        else:
            if anlik <= hedef:
                pnl = marjin * ATR_TP_MULT
                yeni = bakiye_guncelle(pnl)
                telegram_mesaj(f"{coin} SHORT TP 3R +${pnl:.2f} | Cuzdan ${yeni:.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=3.0, kâr_usd=? WHERE id=?", (pnl, islem_id))
            elif anlik >= stop:
                pnl = 0.0 if is_be else -marjin
                bakiye_guncelle(pnl)
                durum = "BE" if is_be else "LOSS"
                telegram_mesaj(f"{coin} SHORT {durum} ${pnl:+.2f}{dosya_notu}")
                c.execute(
                    "UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?",
                    (durum, 0.0 if is_be else -1.0, pnl, islem_id),
                )
    conn.commit()
    conn.close()


def tam_tarama():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tarama ST+RSI | {DOSYA_ADI}")
    db_kurulum()
    guncel = {}
    for coin in HEDEF_COINLER:
        try:
            skor, krit, fiyat, df, yon, mtf, bonus, info = analiz_yap(coin)
            if fiyat:
                guncel[coin] = fiyat
            # Giris: sadece SuperTrend flip + RSI (skor zorunlu degil)
            if df is not None and yon in ("LONG", "SHORT") and info:
                if (yon == "LONG" and info.get("st_buy") and info.get("rsi_ok_long")) or (
                    yon == "SHORT" and info.get("st_sell") and info.get("rsi_ok_short")
                ):
                    telegram_gonder(coin, skor, krit, fiyat, df, yon, mtf, bonus, info)
            time.sleep(0.12)
        except Exception as e:
            print(f"Hata {coin}: {e}")
    acik_islemleri_kontrol(guncel)
    try:
        saatlik_rapor_gonder(guncel)
    except Exception as e:
        print(f"Rapor hatasi: {e}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tarama bitti.")
    return guncel


if __name__ == "__main__":
    acilis_mesaji_goster()
    telegram_mesaj(
        f"<b>Sanal Islem Botu v15.1</b>\n\n"
        f"Dosya: <code>{DOSYA_ADI}</code>\n"
        f"Zaman dilimi: <b>1H</b>\n"
        f"Bollinger kaldirildi\n"
        f"SuperTrend ({ST_LENGTH}, {ST_MULT}) BUY/SELL\n"
        f"RSI: LONG &lt;{RSI_LONG_MAX} | SHORT &gt;{RSI_SHORT_MIN}\n"
        f"Giris: ST + RSI yeterli (skor/ADX zorunlu degil)\n"
        f"SL 1xATR / TP 3xATR\n"
        f"{len(HEDEF_COINLER)} coin (XAG dahil)"
    )

    if RUN_ONCE:
        tam_tarama()
    else:
        threading.Thread(target=run_flask, daemon=True).start()
        while True:
            try:
                tam_tarama()
            except Exception as e:
                print(f"Dongu hatasi: {e}")
            time.sleep(60)

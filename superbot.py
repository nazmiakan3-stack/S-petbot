#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vadeli Sanal Bot v16.3 (OKX USDT-SWAP)
- 50 likit vadeli coin (USDT-SWAP)
- Cuzdan: $1000 | Marjin $15 IZOLE | SL risk $10 | TP $30 (3R)
- Giris: SuperTrend flip VEYA ST cizgisine pullback + RSI
- SL/TP: SuperTrend cizgisine gore (Fib SADECE referans, islem icin kullanilmaz)
  LONG  SL = ST_line altinda | TP = giris + 3 * risk mesafesi
  SHORT SL = ST_line ustunde | TP = giris - 3 * risk mesafesi
  ST yoksa ATR yedek
- Grafik: yesil/kirmizi SuperTrend bolgeleri + AL/SAT oklar + Giris/SL/TP + Fib
- TF: 1H
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
    return f"OK - Vadeli Bot v16.0 | {DOSYA_ADI}", 200

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

# === VADELI / IZOLE AYARLAR ===
ILK_BAKIYE = 1000.0
MARJIN_USD = 15.0          # izole marjin (pozisyon basina kilit)
RISK_USD = 10.0            # SL'de kayip (1R)
TP_USD = 30.0              # TP kar (3R)
KALDIRAC = 5
POZISYON_USD = MARJIN_USD * KALDIRAC  # 75 USDT notional

SIGNAL_COOLDOWN_MINUTES = 12
HOURLY_REPORT_MINUTES = 60

VOLUME_MA_LENGTH = 20
VOLUME_MIN_RATIO = 0.15        # hacim esigi dusuk — tepe/dip kacmasin

ST_LENGTH = 10
ST_MULT = 3.0
RSI_LONG_MAX = 70.0
RSI_SHORT_MIN = 30.0
ST_TOUCH_ATR_FRAC = 0.40

# StochRSI: dipler (asiri satim) / tepeler (asiri alim)
STOCH_RSI_LEN = 14
STOCH_RSI_OVERSOLD = 20.0      # asiri satim → LONG icin (dip)
STOCH_RSI_OVERBOUGHT = 80.0    # asiri alim → SHORT icin (tepe)
MIN_SKOR = 15.0                 # giris icin minimum skor

ATR_SL_MULT = 1.0
ATR_TP_MULT = 3.0

OKX_BASE = "https://www.okx.com"
os.makedirs(ARTIFACT_DIR, exist_ok=True)
matplotlib_lock = threading.Lock()
last_signal_time = {}
last_hourly_report = None

# Top 50 OKX USDT-SWAP (vadeli)
HEDEF_COINLER = [
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "BNB-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP", "ADA-USDT-SWAP", "AVAX-USDT-SWAP", "LINK-USDT-SWAP", "DOT-USDT-SWAP",
    "LTC-USDT-SWAP", "NEAR-USDT-SWAP", "APT-USDT-SWAP", "SUI-USDT-SWAP", "ARB-USDT-SWAP",
    "OP-USDT-SWAP", "INJ-USDT-SWAP", "AAVE-USDT-SWAP", "UNI-USDT-SWAP", "BCH-USDT-SWAP",
    "TRX-USDT-SWAP", "FIL-USDT-SWAP", "ATOM-USDT-SWAP", "ICP-USDT-SWAP", "ETC-USDT-SWAP",
    "WLD-USDT-SWAP", "PEPE-USDT-SWAP", "WIF-USDT-SWAP", "BONK-USDT-SWAP", "ORDI-USDT-SWAP",
    "TIA-USDT-SWAP", "SEI-USDT-SWAP", "JUP-USDT-SWAP", "ENA-USDT-SWAP", "PENDLE-USDT-SWAP",
    "ONDO-USDT-SWAP", "SAND-USDT-SWAP", "MANA-USDT-SWAP", "GALA-USDT-SWAP", "IMX-USDT-SWAP",
    "RENDER-USDT-SWAP", "FET-USDT-SWAP", "TAO-USDT-SWAP", "STX-USDT-SWAP", "CFX-USDT-SWAP",
    "HBAR-USDT-SWAP", "ALGO-USDT-SWAP", "SATS-USDT-SWAP", "SHIB-USDT-SWAP", "PUMP-USDT-SWAP",
]


def acilis_mesaji_goster():
    print("=" * 60)
    print("VADELI SANAL BOT v16.0 — SuperTrend + RSI | 1H SWAP")
    print(f"Dosya   : {DOSYA_ADI}")
    print(f"Cuzdan  : ${ILK_BAKIYE:.0f}")
    print(f"Marjin  : ${MARJIN_USD:.0f} IZOLE | Risk SL ${RISK_USD:.0f} | TP ${TP_USD:.0f}")
    print(f"Kaldirac: {KALDIRAC}x | Pozisyon ~${POZISYON_USD:.0f}")
    print(f"Coin    : {len(HEDEF_COINLER)} USDT-SWAP")
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
            return False, son_hacim, float(volume_ma) if volume_ma == volume_ma else 0.0
        return (son_hacim / volume_ma) >= VOLUME_MIN_RATIO, son_hacim, float(volume_ma)
    except Exception:
        return False, 0.0, 0.0


def supertrend_hesapla(df: pd.DataFrame):
    """Donus: st_line, d_now, st_buy_flip, st_sell_flip
    Flip, ST kirilim, yerel tepe/dip + ST temas (grafikteki oklarla uyumlu).
    """
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
            line_col, dir_col = st.columns[0], st.columns[1]
        else:
            return None, 0.0, False, False

    st_line = st[line_col]
    st_dir = st[dir_col]
    d_now = float(st_dir.iloc[-1]) if pd.notna(st_dir.iloc[-1]) else 0.0
    st_buy = False
    st_sell = False
    n = len(df)
    if n < 5:
        return st_line, d_now, False, False

    # Son 4 mum tarama
    for i in range(max(1, n - 4), n):
        d0 = float(st_dir.iloc[i - 1]) if pd.notna(st_dir.iloc[i - 1]) else 0.0
        d1 = float(st_dir.iloc[i]) if pd.notna(st_dir.iloc[i]) else 0.0
        if d0 <= 0 and d1 > 0:
            st_buy = True
        if d0 >= 0 and d1 < 0:
            st_sell = True

        ln = float(st_line.iloc[i]) if pd.notna(st_line.iloc[i]) else None
        lp = float(st_line.iloc[i - 1]) if pd.notna(st_line.iloc[i - 1]) else ln
        c0 = float(df["close"].iloc[i - 1])
        c1 = float(df["close"].iloc[i])
        o1 = float(df["open"].iloc[i])
        lo1 = float(df["low"].iloc[i])
        hi1 = float(df["high"].iloc[i])
        if ln is not None and lp is not None:
            if c0 < lp and c1 >= ln:
                st_buy = True
            if c0 > lp and c1 <= ln:
                st_sell = True
            # ST temas + yesil/kirmizi mum
            if d1 > 0 and lo1 <= ln * 1.002 and c1 >= ln and c1 >= o1:
                st_buy = True
            if d1 < 0 and hi1 >= ln * 0.998 and c1 <= ln and c1 <= o1:
                st_sell = True

    # Yerel dip/tepe (son bar merkez, 3 mum kanat)
    if n >= 7 and pd.notna(st_line.iloc[-1]):
        i = n - 1
        lv = float(st_line.iloc[-1])
        lo = df["low"].values
        hi = df["high"].values
        c1 = float(df["close"].iloc[-1])
        o1 = float(df["open"].iloc[-1])
        w0 = max(0, i - 3)
        if lo[i] <= np.min(lo[w0:i + 1]) * 1.0001 and c1 >= lv:
            st_buy = True
        if hi[i] >= np.max(hi[w0:i + 1]) * 0.9999 and c1 <= lv:
            st_sell = True

    return st_line, d_now, st_buy, st_sell


def fib_sl_tp(yon, fiyat, atr, st_line, fib_levels, swing_hi, swing_lo):
    """SL = SuperTrend (ana). TP = 3R.
    Fib sadece ST/3R'ye cok yakinsa (0.25 ATR) hizalanir — grafik ile uyumlu.
    """
    buf = atr * 0.15
    min_risk = atr * 0.4
    snap = atr * 0.25

    if yon == "LONG":
        stop = (float(st_line) - buf) if (st_line is not None and st_line < fiyat) else (fiyat - atr * ATR_SL_MULT)
        if fiyat - stop < min_risk:
            stop = fiyat - min_risk
        # Fib destegi ST'ye cok yakinsa SL'yi Fib'e cek
        for v in (fib_levels or {}).values():
            if v is not None and v < fiyat and abs(v - stop) <= snap:
                if fiyat - v >= min_risk:
                    stop = float(v)
                    break
        risk = max(fiyat - stop, min_risk)
        hedef = fiyat + risk * ATR_TP_MULT
        # Fib direnci 3R'ye yakinsa TP'yi Fib'e cek (min 2R)
        for v in sorted((fib_levels or {}).values()):
            if v is not None and v > fiyat and abs(v - hedef) <= snap * 1.5:
                if v - fiyat >= risk * 2.0:
                    hedef = float(v)
                    break
        return stop, hedef, "ST+3R"

    else:
        stop = (float(st_line) + buf) if (st_line is not None and st_line > fiyat) else (fiyat + atr * ATR_SL_MULT)
        if stop - fiyat < min_risk:
            stop = fiyat + min_risk
        for v in sorted((fib_levels or {}).values()):
            if v is not None and v > fiyat and abs(v - stop) <= snap:
                if v - fiyat >= min_risk:
                    stop = float(v)
                    break
        risk = max(stop - fiyat, min_risk)
        hedef = fiyat - risk * ATR_TP_MULT
        for v in sorted((fib_levels or {}).values(), reverse=True):
            if v is not None and v < fiyat and abs(v - hedef) <= snap * 1.5:
                if fiyat - v >= risk * 2.0:
                    hedef = float(v)
                    break
        return stop, hedef, "ST+3R"


def basit_analiz(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 60:
        return {}
    df = df.copy()
    df["RSI"] = ta.rsi(df["close"], length=14)
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["MA50"] = ta.sma(df["close"], length=50)

    st_line, st_dir, st_buy_flip, st_sell_flip = supertrend_hesapla(df)
    fiyat = float(df["close"].iloc[-1])
    low_son = float(df["low"].iloc[-1])
    high_son = float(df["high"].iloc[-1])
    rsi_val = float(df["RSI"].iloc[-1]) if pd.notna(df["RSI"].iloc[-1]) else 50.0
    atr_val = float(df["ATR"].iloc[-1]) if pd.notna(df["ATR"].iloc[-1]) else fiyat * 0.02

    st_line_val = float(st_line.iloc[-1]) if st_line is not None and pd.notna(st_line.iloc[-1]) else None

    st_pullback_long = False
    st_pullback_short = False
    if st_line_val is not None and atr_val > 0:
        tol = atr_val * ST_TOUCH_ATR_FRAC
        if st_dir > 0 and fiyat >= st_line_val:
            if low_son <= st_line_val + tol:
                st_pullback_long = True
        if st_dir < 0 and fiyat <= st_line_val:
            if high_son >= st_line_val - tol:
                st_pullback_short = True

    # StochRSI (tepe / dip)
    stoch_k = stoch_d = 50.0
    stoch_oversold = stoch_overbought = False
    stoch_cross_up = stoch_cross_down = False
    try:
        sr = ta.stochrsi(df["close"], length=STOCH_RSI_LEN, rsi_length=STOCH_RSI_LEN, k=3, d=3)
        if sr is not None and not sr.empty:
            # kolonlar: STOCHRSIk_... STOCHRSId_...
            k_col = d_col = None
            for c in sr.columns:
                cs = str(c).lower()
                if "stochrsik" in cs or cs.endswith("k"):
                    if k_col is None:
                        k_col = c
                if "stochrsid" in cs or (cs.endswith("d") and "stoch" in cs):
                    d_col = c
            if k_col is None:
                k_col = sr.columns[0]
            if d_col is None and len(sr.columns) > 1:
                d_col = sr.columns[1]
            stoch_k = float(sr[k_col].iloc[-1]) if pd.notna(sr[k_col].iloc[-1]) else 50.0
            stoch_d = float(sr[d_col].iloc[-1]) if d_col and pd.notna(sr[d_col].iloc[-1]) else stoch_k
            k_prev = float(sr[k_col].iloc[-2]) if len(sr) > 1 and pd.notna(sr[k_col].iloc[-2]) else stoch_k
            d_prev = float(sr[d_col].iloc[-2]) if d_col and len(sr) > 1 and pd.notna(sr[d_col].iloc[-2]) else stoch_d
            # pandas_ta stochrsi bazen 0-1 araliginda — 0-100'e cevir
            if stoch_k <= 1.5 and stoch_d <= 1.5:
                stoch_k *= 100.0
                stoch_d *= 100.0
                k_prev *= 100.0
                d_prev *= 100.0
            stoch_oversold = stoch_k <= STOCH_RSI_OVERSOLD
            stoch_overbought = stoch_k >= STOCH_RSI_OVERBOUGHT
            stoch_cross_up = (k_prev <= d_prev and stoch_k > stoch_d and stoch_k < 40)
            stoch_cross_down = (k_prev >= d_prev and stoch_k < stoch_d and stoch_k > 60)
    except Exception as e:
        print(f"StochRSI: {e}")

    st_buy = bool(st_buy_flip or st_pullback_long)
    st_sell = bool(st_sell_flip or st_pullback_short)

    # ANA KURAL (tepe/dip):
    # LONG  = SuperTrend AL  + StochRSI asiri satim (dip) veya alttan kesişim
    # SHORT = SuperTrend SAT + StochRSI asiri alim (tepe) veya ustten kesişim
    long_ok = st_buy and (stoch_oversold or stoch_cross_up)
    short_ok = st_sell and (stoch_overbought or stoch_cross_down)

    sinyal_tipi = ""
    if long_ok:
        sinyal_tipi = "ST_AL+StochDIP"
    elif short_ok:
        sinyal_tipi = "ST_SAT+StochTEPE"
    elif st_buy_flip:
        sinyal_tipi = "FLIP"
    elif st_pullback_long or st_pullback_short:
        sinyal_tipi = "PULLBACK"

    look = min(80, len(df))
    hi = float(df["high"].iloc[-look:].max())
    lo = float(df["low"].iloc[-look:].min())
    fib_levels = {}
    rng = hi - lo
    if rng > fiyat * 0.005:
        for r in (0.236, 0.382, 0.5, 0.618, 0.786, 0.886):
            fib_levels[r] = hi - rng * r

    return {
        "fiyat": fiyat,
        "rsi": rsi_val,
        "atr": atr_val,
        "st_line": st_line_val,
        "st_dir": st_dir,
        "st_buy": st_buy,
        "st_sell": st_sell,
        "st_buy_flip": bool(st_buy_flip),
        "st_sell_flip": bool(st_sell_flip),
        "st_pullback_long": st_pullback_long,
        "st_pullback_short": st_pullback_short,
        "long_ok": long_ok,
        "short_ok": short_ok,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "stoch_oversold": stoch_oversold,
        "stoch_overbought": stoch_overbought,
        "sinyal_tipi": sinyal_tipi,
        "rsi_ok_long": rsi_val < RSI_LONG_MAX,
        "rsi_ok_short": rsi_val > RSI_SHORT_MIN,
        "fib_levels": fib_levels,
        "swing_hi": hi,
        "swing_lo": lo,
        "above_ma50": fiyat > (float(df["MA50"].iloc[-1]) if pd.notna(df["MA50"].iloc[-1]) else 0),
    }


def analiz_yap(symbol: str):
    df = veri_cek(symbol, "1H", 250)
    if df is None or len(df) < 60:
        return 0.0, None, "YOK", None, None
    info = basit_analiz(df)
    if not info:
        return 0.0, None, "YOK", None, None

    # Yon: SADECE ST + StochRSI uyumu
    if info.get("long_ok"):
        yon = "LONG"
    elif info.get("short_ok"):
        yon = "SHORT"
    else:
        yon = "YOK"

    skor = 0.0
    if info.get("st_buy_flip") or info.get("st_sell_flip"):
        skor += 8.0
    if info.get("st_pullback_long") or info.get("st_pullback_short"):
        skor += 5.0
    if info.get("stoch_oversold") or info.get("stoch_overbought"):
        skor += 6.0
    if info.get("long_ok") or info.get("short_ok"):
        skor += 4.0  # cift onay bonusu → toplam kolayca >= 15
    if info.get("rsi_ok_long") and yon == "LONG":
        skor += 1.5
    if info.get("rsi_ok_short") and yon == "SHORT":
        skor += 1.5
    return skor, info["fiyat"], yon, info, df


_db_lock = threading.Lock()


def db_baglanti():
    path = os.path.join(ARTIFACT_DIR, "islemler.db")
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn, conn.cursor()


def cuzdan_tek_satir_yap(c, reset=False):
    """Cuzdan tablosunu tek satira indir; reset=True ise $ILK_BAKIYE."""
    c.execute("SELECT id, bakiye FROM cuzdan ORDER BY id ASC")
    rows = c.fetchall()
    if not rows:
        c.execute("INSERT INTO cuzdan (bakiye, guncelleme) VALUES (?, ?)",
                  (ILK_BAKIYE, datetime.now().isoformat()))
        return ILK_BAKIYE
    bakiye = float(rows[-1][1]) if rows[-1][1] is not None else ILK_BAKIYE
    if reset:
        bakiye = ILK_BAKIYE
    # tek satir birak
    c.execute("DELETE FROM cuzdan")
    c.execute("INSERT INTO cuzdan (id, bakiye, guncelleme) VALUES (1, ?, ?)",
              (bakiye, datetime.now().isoformat()))
    return bakiye


def db_kurulum():
    with _db_lock:
        conn, c = db_baglanti()
        c.execute("""CREATE TABLE IF NOT EXISTS cuzdan (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bakiye REAL DEFAULT 1000.0, guncelleme TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS islemler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT, yon TEXT, giris_fiyati REAL,
            hedef_r1 REAL, stop_loss REAL, orjinal_stop REAL,
            marjin REAL DEFAULT 15.0, kaldirac INTEGER DEFAULT 5,
            pozisyon_usd REAL DEFAULT 75.0, risk_usd REAL DEFAULT 10.0,
            durum TEXT, kâr_r REAL DEFAULT 0.0, kâr_usd REAL DEFAULT 0.0,
            is_be INTEGER DEFAULT 0, tarih TEXT, skor REAL DEFAULT 0.0,
            grafik_dosya TEXT DEFAULT '', mod TEXT DEFAULT 'IZOLE')""")
        for col, typ in [
            ("risk_usd", "REAL DEFAULT 10.0"),
            ("grafik_dosya", "TEXT DEFAULT ''"),
            ("mod", "TEXT DEFAULT 'IZOLE'"),
        ]:
            try:
                c.execute(f"ALTER TABLE islemler ADD COLUMN {col} {typ}")
            except Exception:
                pass

        reset = os.environ.get("RESET_WALLET", "").lower() in ("1", "true", "yes")
        bakiye = cuzdan_tek_satir_yap(c, reset=reset)

        # Ayni coinde birden fazla ACIK varsa eskileri kapat
        c.execute("""
            UPDATE islemler SET durum='IPTAL'
            WHERE durum='ACIK' AND id NOT IN (
                SELECT MAX(id) FROM islemler WHERE durum='ACIK' GROUP BY coin
            )
        """)
        conn.commit()
        conn.close()
        if reset:
            print(f"[DB] Cuzdan sifirlandi: ${ILK_BAKIYE:.2f}")
        else:
            print(f"[DB] Cuzdan: ${bakiye:.2f} (tek satir)")


def acik_pozisyon_var_mi(coin):
    with _db_lock:
        conn, c = db_baglanti()
        c.execute("SELECT id FROM islemler WHERE coin=? AND durum='ACIK' LIMIT 1", (coin,))
        row = c.fetchone()
        conn.close()
        return row is not None


def islem_kaydet(coin, yon, giris, stop, hedef, skor=0.0, grafik_dosya=""):
    with _db_lock:
        conn, c = db_baglanti()
        c.execute("SELECT id FROM islemler WHERE coin=? AND durum='ACIK' LIMIT 1", (coin,))
        if c.fetchone():
            conn.close()
            return False
        tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""INSERT INTO islemler
            (coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop,
             marjin, kaldirac, pozisyon_usd, risk_usd, durum, tarih, skor, grafik_dosya, mod)
            VALUES (?,?,?,?,?,?,?,?,?,?,'ACIK',?,?,?,'IZOLE')""",
                  (coin, yon, giris, hedef, stop, stop,
                   MARJIN_USD, KALDIRAC, POZISYON_USD, RISK_USD,
                   tarih, skor, grafik_dosya or ""))
        conn.commit()
        conn.close()
        return True


def bakiye_guncelle(pnl_usd: float):
    with _db_lock:
        conn, c = db_baglanti()
        c.execute("SELECT bakiye FROM cuzdan WHERE id=1")
        row = c.fetchone()
        if not row:
            cuzdan_tek_satir_yap(c, reset=False)
            c.execute("SELECT bakiye FROM cuzdan WHERE id=1")
            row = c.fetchone()
        mevcut = float(row[0]) if row else ILK_BAKIYE
        yeni = mevcut + float(pnl_usd)
        c.execute("UPDATE cuzdan SET bakiye=?, guncelleme=? WHERE id=1",
                  (yeni, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return yeni


def acik_pozisyonlari_listele():
    with _db_lock:
        conn, c = db_baglanti()
        c.execute("""SELECT coin, yon, giris_fiyati, stop_loss, hedef_r1, tarih, skor,
                            grafik_dosya, marjin, kaldirac, risk_usd
                     FROM islemler WHERE durum='ACIK' ORDER BY id DESC""")
        rows = c.fetchall()
        conn.close()
        return rows


def bakiye_oku():
    with _db_lock:
        conn, c = db_baglanti()
        c.execute("SELECT bakiye FROM cuzdan WHERE id=1")
        row = c.fetchone()
        if not row:
            cuzdan_tek_satir_yap(c, reset=False)
            conn.commit()
            c.execute("SELECT bakiye FROM cuzdan WHERE id=1")
            row = c.fetchone()
        conn.close()
        return float(row[0]) if row else ILK_BAKIYE


def telegram_mesaj(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG] Token/ChatID yok — mesaj atlanadi")
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
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        if caption:
            print("[TG] Token yok:", caption[:120])
        return
    if not path or not os.path.exists(path):
        if caption:
            telegram_mesaj(caption)
        return
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000], "parse_mode": "HTML"},
                timeout=30,
            )
    except Exception as e:
        print(f"Foto hatasi: {e}")
        if caption:
            telegram_mesaj(caption)


def saatlik_rapor_gonder(guncel_fiyatlar: dict):
    global last_hourly_report
    now = datetime.now()
    if last_hourly_report and (now - last_hourly_report) < timedelta(minutes=HOURLY_REPORT_MINUTES):
        return
    last_hourly_report = now

    pozisyonlar = acik_pozisyonlari_listele()
    bakiye = bakiye_oku()
    kar_zarar = bakiye - ILK_BAKIYE
    kz_emoji = "🟢 KÂR" if kar_zarar >= 0 else "🔴 ZARAR"
    mesaj = (
        f"📋 <b>VADELİ SAATLİK RAPOR</b>\n"
        f"🕒 {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"📂 Dosya: <code>{DOSYA_ADI}</code>\n"
        f"💰 Cüzdan: <b>${bakiye:.2f}</b> / ${ILK_BAKIYE:.0f}\n"
        f"{kz_emoji}: <b>${kar_zarar:+.2f}</b> "
        f"(%{(kar_zarar / ILK_BAKIYE * 100):+.2f})\n"
        f"🔒 Marjin ${MARJIN_USD:.0f} İZOLE | Risk ${RISK_USD:.0f}\n"
        f"📌 Strateji: ST AL/SAT + StochRSI dip/tepe | Skor≥{MIN_SKOR:.0f}\n\n"
    )
    if not pozisyonlar:
        mesaj += "Açık pozisyon yok.\n"
    else:
        mesaj += f"<b>Açık ({len(pozisyonlar)}):</b>\n"
        toplam = 0.0
        for row in pozisyonlar:
            coin, yon, giris, stop, hedef, tarih, skor, grafik, marjin, kaldirac, risk = row
            anlik = guncel_fiyatlar.get(coin, giris)
            risk_px = abs(giris - stop) or giris * 0.01
            if yon == "LONG":
                r_mult = (anlik - giris) / risk_px
            else:
                r_mult = (giris - anlik) / risk_px
            pnl_usd = r_mult * RISK_USD
            toplam += pnl_usd
            durum = "KÂR" if pnl_usd >= 0 else "ZARAR"
            emoji = "🟢" if pnl_usd >= 0 else "🔴"
            mesaj += (
                f"\n• <b>{coin}</b> {yon}\n"
                f"  Giriş {giris:.6f} | Anlık {anlik:.6f}\n"
                f"  {emoji} <b>{durum}</b>: ${pnl_usd:+.2f} ({r_mult:+.2f}R)\n"
                f"  📎 <code>{grafik or 'Yok'}</code>\n"
            )
        td = "KÂR" if toplam >= 0 else "ZARAR"
        mesaj += (
            f"\n────────────────\n"
            f"📊 Açık pozisyonlar toplam <b>{td}</b>: <b>${toplam:+.2f}</b>\n"
            f"💼 Cüzdan net <b>{kz_emoji}</b>: <b>${kar_zarar:+.2f}</b>\n"
        )
    telegram_mesaj(mesaj)


def grafik_ciz(df, symbol, yon, skor, info=None, giris=None, stop=None, hedef=None):
    """Mum + ST + RSI + Giris/SL/TP + Fib. Hata olursa detay loglar."""
    with matplotlib_lock:
        try:
            if df is None or len(df) < 50:
                print("Grafik: df yetersiz")
                return None

            df_plot = df.copy().sort_index(ascending=True).tail(100)
            df_plot = df_plot.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            # NaN temizle
            df_plot = df_plot.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df_plot) < 40:
                print("Grafik: dropna sonrasi yetersiz")
                return None

            close = df_plot["Close"]
            high = df_plot["High"]
            low = df_plot["Low"]

            addplots = []

            # EMA / MA
            try:
                ema21 = ta.ema(close, length=21)
                if ema21 is not None and ema21.notna().sum() > 5:
                    addplots.append(mpf.make_addplot(ema21, color="#ff9800", width=1.2, panel=0))
            except Exception:
                pass
            try:
                ma50 = ta.sma(close, length=50)
                if ma50 is not None and ma50.notna().sum() > 5:
                    addplots.append(mpf.make_addplot(ma50, color="#1e88e5", width=1.3, panel=0))
            except Exception:
                pass

            # SuperTrend: yesil/kirmizi cizgi + tepe/dip AL-SAT oklar (TV benzeri)
            try:
                st = ta.supertrend(high, low, close, length=ST_LENGTH, multiplier=ST_MULT)
                st_line = None
                st_dir = None
                if st is not None and not st.empty:
                    for c in st.columns:
                        cs = str(c)
                        if cs.startswith("SUPERTd"):
                            st_dir = st[c]
                        elif cs.startswith("SUPERT_") and "SUPERTd" not in cs and "SUPERTl" not in cs and "SUPERTs" not in cs:
                            st_line = st[c]
                    if st_line is None:
                        st_line = st.iloc[:, 0]
                    if st_dir is None and len(st.columns) >= 2:
                        st_dir = st.iloc[:, 1]
                    st_line = st_line.reindex(df_plot.index)
                    if st_dir is not None:
                        st_dir = st_dir.reindex(df_plot.index)

                    if st_line is not None and st_dir is not None and st_line.notna().sum() > 5:
                        st_up = st_line.where(st_dir > 0)
                        st_dn = st_line.where(st_dir < 0)
                        if st_up.notna().sum() > 0:
                            addplots.append(mpf.make_addplot(
                                st_up, color="#00c853", width=2.4, panel=0))
                        if st_dn.notna().sum() > 0:
                            addplots.append(mpf.make_addplot(
                                st_dn, color="#ff1744", width=2.4, panel=0))
                    elif st_line is not None and st_line.notna().sum() > 5:
                        addplots.append(mpf.make_addplot(
                            st_line, color="#26a69a", width=2.0, panel=0))

                    # --- AL / SAT: SADECE gercek SuperTrend yon flip (profesyonel, az ok) ---
                    buy_marks = pd.Series(np.nan, index=df_plot.index)
                    sell_marks = pd.Series(np.nan, index=df_plot.index)
                    if st_dir is not None:
                        d_vals = st_dir.ffill().fillna(0).values
                        lows = low.values.astype(float)
                        highs = high.values.astype(float)
                        for i in range(1, len(d_vals)):
                            prev_d, cur_d = float(d_vals[i - 1]), float(d_vals[i])
                            # 1 = bullish, -1 = bearish — sadece renk degisimi
                            if prev_d < 0 and cur_d > 0:
                                buy_marks.iloc[i] = lows[i] * 0.992
                            elif prev_d > 0 and cur_d < 0:
                                sell_marks.iloc[i] = highs[i] * 1.008

                        if buy_marks.notna().sum() > 0:
                            addplots.append(mpf.make_addplot(
                                buy_marks, type="scatter", markersize=110,
                                marker="^", color="#00c853", panel=0,
                            ))
                        if sell_marks.notna().sum() > 0:
                            addplots.append(mpf.make_addplot(
                                sell_marks, type="scatter", markersize=110,
                                marker="v", color="#ff1744", panel=0,
                            ))
            except Exception as e:
                print(f"ST addplot: {e}")

            # RSI panel 1
            try:
                rsi = ta.rsi(close, length=14)
                if rsi is not None and rsi.notna().sum() > 5:
                    addplots.append(mpf.make_addplot(rsi, color="#9c27b0", width=1.2, panel=1, ylabel="RSI"))
                    addplots.append(mpf.make_addplot(
                        pd.Series(70.0, index=df_plot.index), color="red", width=0.6, linestyle="--", panel=1))
                    addplots.append(mpf.make_addplot(
                        pd.Series(30.0, index=df_plot.index), color="green", width=0.6, linestyle="--", panel=1))
            except Exception as e:
                print(f"RSI addplot: {e}")

            # Giris / SL / TP + Fib (sadece sonlu degerler)
            h_vals, h_cols, h_styles, h_widths = [], [], [], []

            def _hl(val, color, style="-", width=1.6):
                if val is None:
                    return
                try:
                    v = float(val)
                except Exception:
                    return
                if not np.isfinite(v):
                    return
                h_vals.append(v)
                h_cols.append(color)
                h_styles.append(style)
                h_widths.append(width)

            # Giris / SL / TP kalin (asıl seviyeler)
            _hl(giris, "#1e88e5", "-", 2.4)
            _hl(stop, "#e53935", "-", 2.4)
            _hl(hedef, "#43a047", "-", 2.4)

            # Fib sadece ana seviyeler (0.382 / 0.5 / 0.618) — kalabalik yok
            fib_levels = (info or {}).get("fib_levels") or {}
            fib_colors = {0.382: "#ffb74d", 0.5: "#90caf9", 0.618: "#a5d6a7"}
            for ratio in (0.382, 0.5, 0.618):
                if ratio in fib_levels:
                    _hl(fib_levels[ratio], fib_colors[ratio], "--", 0.8)

            hlines = None
            if h_vals:
                hlines = {
                    "hlines": h_vals,
                    "colors": h_cols,
                    "linestyle": h_styles,
                    "linewidths": h_widths,
                    "alpha": 0.9,
                }

            dosya_adi = f"{symbol.replace('-', '_')}_chart.png"
            dosya = os.path.join(ARTIFACT_DIR, dosya_adi)
            os.makedirs(ARTIFACT_DIR, exist_ok=True)

            mc = mpf.make_marketcolors(
                up="#26a69a", down="#ef5350", edge="inherit",
                wick={"up": "#26a69a", "down": "#ef5350"},
            )
            style = mpf.make_mpf_style(
                base_mpf_style="yahoo", marketcolors=mc,
                facecolor="white", gridstyle=":",
            )

            # panel 0 = fiyat, panel 1 = RSI  → 2 panel
            kwargs = dict(
                type="candle",
                style=style,
                title=f"{symbol} | {yon} | SuperTrend flip | Giriş/SL/TP",
                returnfig=True,
                volume=False,
                figsize=(12, 8),
                panel_ratios=(3.5, 1.2),
                tight_layout=True,
            )
            if addplots:
                kwargs["addplot"] = addplots
            if hlines:
                kwargs["hlines"] = hlines

            fig, axes = mpf.plot(df_plot, **kwargs)
            ax = axes[0] if isinstance(axes, (list, np.ndarray)) else axes

            ax.annotate(
                f"{'▲ LONG' if yon == 'LONG' else '▼ SHORT'} | ST flip okları | SL=ST · TP=3R · Fib 38/50/62",
                xy=(0.01, 0.97), xycoords="axes fraction", fontsize=8,
                color="#26a69a" if yon == "LONG" else "#ef5350", fontweight="bold",
            )

            # Sag etiketler
            x_right = len(df_plot) - 1

            def _etiket(y, text, color):
                if y is None:
                    return
                try:
                    y = float(y)
                except Exception:
                    return
                if not np.isfinite(y):
                    return
                ax.annotate(
                    text, xy=(x_right, y), xytext=(6, 0),
                    textcoords="offset points", fontsize=8, color=color,
                    fontweight="bold", va="center",
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.9),
                )

            if giris is not None:
                _etiket(giris, f"GİRİŞ {float(giris):.4f}", "#2196f3")
            if stop is not None:
                _etiket(stop, f"SL {float(stop):.4f}", "#f44336")
            if hedef is not None:
                _etiket(hedef, f"TP {float(hedef):.4f}", "#4caf50")
            for ratio, level in fib_levels.items():
                _etiket(level, f"Fib{ratio}", fib_colors.get(ratio, "#757575"))

            fig.savefig(dosya, dpi=110, bbox_inches="tight", facecolor="white")
            plt.close(fig)
            if os.path.exists(dosya) and os.path.getsize(dosya) > 1000:
                return dosya
            print(f"Grafik dosya bos/kucuk: {dosya}")
            return None
        except Exception as e:
            plt.close("all")
            import traceback
            print(f"Grafik hatasi: {e}")
            traceback.print_exc()
            return None


def test_grafik_gonder():
    """Sinyal olmasa bile BTC ile ornek grafik Telegram'a gider."""
    try:
        symbol = "BTC-USDT-SWAP"
        df = veri_cek(symbol, "1H", 250)
        if df is None or len(df) < 60:
            # Spot fallback
            df = veri_cek("BTC-USDT", "1H", 250)
            symbol = "BTC-USDT" if df is not None else symbol
        if df is None or len(df) < 60:
            telegram_mesaj("⚠️ Test grafik: mum verisi alinamadi (OKX)")
            return
        info = basit_analiz(df) or {}
        fiyat = float(info.get("fiyat") or df["close"].iloc[-1])
        atr = float(info.get("atr") or fiyat * 0.01)
        st_line = info.get("st_line")
        buf = atr * 0.15
        if st_line is not None and st_line < fiyat:
            stop = float(st_line) - buf
        else:
            stop = fiyat - atr * ATR_SL_MULT
        risk_px = max(fiyat - stop, atr * 0.3)
        hedef = fiyat + risk_px * ATR_TP_MULT
        foto = grafik_ciz(df, symbol, "LONG", 0.0, info, giris=fiyat, stop=stop, hedef=hedef)
        if foto and os.path.exists(foto):
            telegram_foto(
                foto,
                f"🧪 <b>TEST GRAFİK</b> (sinyal değil)\n"
                f"📂 <code>{DOSYA_ADI}</code>\n"
                f"🪙 {symbol} | 1H\n"
                f"💰 GİRİŞ: {fiyat:.4f}\n"
                f"🛡 SL (SuperTrend): {stop:.4f}\n"
                f"🎯 TP (3R): {hedef:.4f}\n"
                f"🟢 ST yeşil=AL · 🔴 kırmızı=SAT · ▲▼ flip okları\n"
                f"ℹ️ Fib sadece referans (SL/TP Fib değil)\n"
                f"📎 <code>{os.path.basename(foto)}</code>",
            )
            print(f"Test grafik gonderildi: {foto}")
        else:
            telegram_mesaj(
                "⚠️ Test grafik cizilemedi.\n"
                "screen -r S_petbot ile 'Grafik hatasi' satirina bak."
            )
    except Exception as e:
        print(f"Test grafik hatasi: {e}")
        telegram_mesaj(f"⚠️ Test grafik hatasi: <code>{e}</code>")


def telegram_gonder(symbol, skor, fiyat, df, yon, info):
    now = datetime.now()
    if acik_pozisyon_var_mi(symbol):
        return
    last = last_signal_time.get(symbol)
    if last and (now - last) < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES):
        return

    # Zorunlu: ST AL/SAT + StochRSI dip/tepe + skor >= 15
    if yon == "LONG":
        if not (info and info.get("long_ok")):
            return
    elif yon == "SHORT":
        if not (info and info.get("short_ok")):
            return
    else:
        return

    if skor < MIN_SKOR:
        return

    hacim_ok, son_hacim, ort_hacim = hacim_yeterli_mi(df)
    if not hacim_ok:
        return

    atr = info.get("atr") or fiyat * 0.02
    if atr <= 0:
        atr = fiyat * 0.02
    st_line = info.get("st_line")
    fib_levels = info.get("fib_levels") or {}
    swing_hi = info.get("swing_hi")
    swing_lo = info.get("swing_lo")

    stop, hedef, sltp_mod = fib_sl_tp(
        yon, fiyat, atr, st_line, fib_levels, swing_hi, swing_lo
    )

    dosya_adi = f"{symbol.replace('-', '_')}_chart.png"
    foto = grafik_ciz(df, symbol, yon, skor, info, giris=fiyat, stop=stop, hedef=hedef)
    if foto:
        dosya_adi = os.path.basename(foto)

    if not islem_kaydet(symbol, yon, fiyat, stop, hedef, skor, grafik_dosya=dosya_adi):
        return
    last_signal_time[symbol] = now

    tip = info.get("sinyal_tipi") or "ST+StochRSI"
    st_txt = f"{st_line:.6f}" if st_line else "yok"
    sk = info.get("stoch_k", 0)
    sd = info.get("stoch_d", 0)
    mesaj = (
        f"⚡ <b>{symbol}</b> — <b>{yon}</b> (VADELİ İZOLE)\n"
        f"📂 Dosya: <code>{DOSYA_ADI}</code>\n"
        f"📌 Sinyal: <b>{tip}</b>\n"
        f"⭐ Skor: <b>{skor:.1f}</b> (≥{MIN_SKOR:.0f})\n"
        f"📐 SuperTrend: {st_txt}\n"
        f"📊 StochRSI: K={sk:.1f} D={sd:.1f} "
        f"({'DIP/asiri satim' if info.get('stoch_oversold') else 'TEPE/asiri alim' if info.get('stoch_overbought') else 'nötr'})\n"
        f"🔒 Marjin ${MARJIN_USD:.0f} | Risk ${RISK_USD:.0f} | TP ${TP_USD:.0f}\n"
        f"📈 Hacim: %{(son_hacim / ort_hacim * 100) if ort_hacim else 0:.0f}\n\n"
        f"💰 <b>GİRİŞ:</b> {fiyat:.6f}\n"
        f"🛡 <b>SL (SuperTrend):</b> {stop:.6f}\n"
        f"🎯 <b>TP (3R):</b> {hedef:.6f}\n"
        f"📎 Grafik: <code>{dosya_adi}</code>"
    )
    if foto:
        telegram_foto(foto, mesaj)
    else:
        telegram_mesaj(mesaj)


def acik_islemleri_kontrol(guncel_fiyatlar: dict):
    """TP/SL/BE kontrol — her olay bir kez, cuzdan tek satir."""
    bildirimler = []  # (mesaj, pnl) — pnl None ise sadece bilgi

    with _db_lock:
        conn, c = db_baglanti()
        c.execute("""SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop,
                            is_be, marjin, grafik_dosya
                     FROM islemler WHERE durum='ACIK'""")
        rows = c.fetchall()
        for row in rows:
            islem_id, coin, yon, giris, hedef, stop, orj_stop, is_be, marjin, grafik = row
            if coin not in guncel_fiyatlar:
                continue
            anlik = float(guncel_fiyatlar[coin])
            giris = float(giris)
            stop = float(stop)
            hedef = float(hedef)
            orj = float(orj_stop or stop)
            risk_px = abs(giris - orj) or (giris * 0.01)
            mevcut_r = (anlik - giris) / risk_px if yon == "LONG" else (giris - anlik) / risk_px
            dosya_notu = f"\n📎 {grafik or DOSYA_ADI}\n📂 {DOSYA_ADI}"
            is_be = int(is_be or 0)

            # +1R → BE stop (yalnizca 1 kez)
            if mevcut_r >= 1.0 and is_be == 0:
                be_stop = giris * (0.9995 if yon == "LONG" else 1.0005)
                c.execute(
                    "UPDATE islemler SET stop_loss=?, is_be=1 WHERE id=? AND is_be=0 AND durum='ACIK'",
                    (be_stop, islem_id),
                )
                if c.rowcount > 0:
                    is_be = 1
                    stop = be_stop
                    bildirimler.append((
                        f"🛡 <b>{coin}</b> BE aktif (stop girişe)\nStop: {be_stop:.6f}{dosya_notu}",
                        None,
                    ))

            durum = None
            pnl = 0.0
            if yon == "LONG":
                if anlik >= hedef:
                    pnl, durum = TP_USD, "WIN"
                elif anlik <= stop:
                    pnl = 0.0 if is_be == 1 else -RISK_USD
                    durum = "BE" if is_be == 1 else "LOSS"
            else:
                if anlik <= hedef:
                    pnl, durum = TP_USD, "WIN"
                elif anlik >= stop:
                    pnl = 0.0 if is_be == 1 else -RISK_USD
                    durum = "BE" if is_be == 1 else "LOSS"

            if durum is None:
                continue

            c.execute(
                "UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=? AND durum='ACIK'",
                (durum, 3.0 if durum == "WIN" else (0.0 if durum == "BE" else -1.0), pnl, islem_id),
            )
            if c.rowcount == 0:
                continue

            if durum == "WIN":
                emoji = "✅"
            elif durum == "BE":
                emoji = "🛡"
            else:
                emoji = "❌"
            bildirimler.append((
                f"{emoji} <b>{coin} {yon}</b> {durum}\n${pnl:+.2f}{{{{CUZDAN}}}}{dosya_notu}",
                pnl,
            ))

        conn.commit()
        conn.close()

    for mesaj, pnl in bildirimler:
        if pnl is not None:
            yeni = bakiye_guncelle(pnl)
            mesaj = mesaj.replace("{{CUZDAN}}", f" | Cüzdan ${yeni:.2f}")
        telegram_mesaj(mesaj)


def tam_tarama():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Vadeli tarama | {DOSYA_ADI}")
    db_kurulum()
    guncel = {}
    n_flip = n_pull = n_sinyal = 0
    for coin in HEDEF_COINLER:
        try:
            result = analiz_yap(coin)
            if len(result) == 5:
                skor, fiyat, yon, info, df = result
            else:
                continue
            if fiyat:
                guncel[coin] = fiyat
            if info:
                if info.get("st_buy_flip") or info.get("st_sell_flip"):
                    n_flip += 1
                if info.get("st_pullback_long") or info.get("st_pullback_short"):
                    n_pull += 1
            if df is not None and yon in ("LONG", "SHORT") and info and skor >= MIN_SKOR:
                if (yon == "LONG" and info.get("long_ok")) or (yon == "SHORT" and info.get("short_ok")):
                    n_sinyal += 1
                    telegram_gonder(coin, skor, fiyat, df, yon, info)
            time.sleep(0.10)
        except Exception as e:
            print(f"Hata {coin}: {e}")
    acik_islemleri_kontrol(guncel)
    try:
        saatlik_rapor_gonder(guncel)
    except Exception as e:
        print(f"Rapor hatasi: {e}")
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Bitti | "
        f"flip={n_flip} pullback={n_pull} aday={n_sinyal} | Cuzdan=${bakiye_oku():.2f}"
    )
    return guncel


if __name__ == "__main__":
    acilis_mesaji_goster()
    db_kurulum()
    telegram_mesaj(
        f"🚀 <b>Vadeli Sanal Bot v16.7</b>\n\n"
        f"📂 Dosya: <code>{DOSYA_ADI}</code>\n"
        f"💰 Cüzdan: <b>${bakiye_oku():.2f}</b> (başlangıç ${ILK_BAKIYE:.0f})\n"
        f"🔒 Marjin ${MARJIN_USD:.0f} İZOLE | Risk ${RISK_USD:.0f}\n"
        f"📌 LONG = ST AL + StochRSI dip | SHORT = ST SAT + StochRSI tepe\n"
        f"⭐ Min skor {MIN_SKOR:.0f} | BE spam düzeltildi | tek cüzdan\n"
        f"📋 {len(HEDEF_COINLER)} SWAP | TF 1H"
    )

    # Sinyal beklemeden ornek grafik (Fib + Giris/SL/TP)
    try:
        test_grafik_gonder()
    except Exception as e:
        print(f"Test grafik atlandi: {e}")

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

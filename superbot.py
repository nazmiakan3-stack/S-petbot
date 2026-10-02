#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vadeli Sanal Bot v16.1 (OKX USDT-SWAP)
- 50 likit vadeli coin (USDT-SWAP)
- Cuzdan: $1000
- Her pozisyon IZOLE marjin: $15
- SL kaybi: $10 (risk) | TP: $30 (3R)  — RR 1:3
- SuperTrend (10,3) + RSI giris
  LONG : ST BUY + RSI < 70
  SHORT: ST SELL + RSI > 30
- TF: 1H
- Grafik: Giris/SL/TP etiketli + Fibonacci seviyeleri
- Baslangicta test grafiği (sinyal olmasa da) Telegram'a gider
- Saatlik PnL raporu
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

SIGNAL_COOLDOWN_MINUTES = 35
HOURLY_REPORT_MINUTES = 60

VOLUME_MA_LENGTH = 20
VOLUME_MIN_RATIO = 0.35

ST_LENGTH = 10
ST_MULT = 3.0
RSI_LONG_MAX = 70.0
RSI_SHORT_MIN = 30.0

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


def basit_analiz(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 60:
        return {}
    df = df.copy()
    df["RSI"] = ta.rsi(df["close"], length=14)
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["MA50"] = ta.sma(df["close"], length=50)

    st_line, st_dir, st_buy, st_sell = supertrend_hesapla(df)
    fiyat = float(df["close"].iloc[-1])
    rsi_val = float(df["RSI"].iloc[-1]) if pd.notna(df["RSI"].iloc[-1]) else 50.0
    atr_val = float(df["ATR"].iloc[-1]) if pd.notna(df["ATR"].iloc[-1]) else fiyat * 0.02

    # Fibonacci (son swing)
    look = min(80, len(df))
    hi = float(df["high"].iloc[-look:].max())
    lo = float(df["low"].iloc[-look:].min())
    fib_levels = {}
    rng = hi - lo
    if rng > fiyat * 0.005:
        for r in (0.236, 0.382, 0.5, 0.618, 0.786, 0.886):
            fib_levels[r] = hi - rng * r

    st_line_val = float(st_line.iloc[-1]) if st_line is not None and pd.notna(st_line.iloc[-1]) else None

    return {
        "fiyat": fiyat,
        "rsi": rsi_val,
        "atr": atr_val,
        "st_line": st_line_val,
        "st_dir": st_dir,
        "st_buy": bool(st_buy),
        "st_sell": bool(st_sell),
        "rsi_ok_long": rsi_val < RSI_LONG_MAX,
        "rsi_ok_short": rsi_val > RSI_SHORT_MIN,
        "fib_levels": fib_levels,
        "above_ma50": fiyat > (float(df["MA50"].iloc[-1]) if pd.notna(df["MA50"].iloc[-1]) else 0),
    }


def analiz_yap(symbol: str):
    df = veri_cek(symbol, "1H", 250)
    if df is None or len(df) < 60:
        return 0.0, None, "YOK", None, None
    info = basit_analiz(df)
    if not info:
        return 0.0, None, "YOK", None, None
    if info.get("st_buy") and not info.get("st_sell"):
        yon = "LONG"
    elif info.get("st_sell") and not info.get("st_buy"):
        yon = "SHORT"
    else:
        yon = "YOK"
    skor = 0.0
    if info.get("st_buy"):
        skor += 7.0
    if info.get("st_sell"):
        skor += 7.0
    if info.get("rsi_ok_long") and yon == "LONG":
        skor += 2.0
    if info.get("rsi_ok_short") and yon == "SHORT":
        skor += 2.0
    return skor, info["fiyat"], yon, info, df


def db_baglanti():
    conn = sqlite3.connect(os.path.join(ARTIFACT_DIR, "islemler.db"), check_same_thread=False)
    return conn, conn.cursor()


def db_kurulum():
    conn, c = db_baglanti()
    c.execute("""CREATE TABLE IF NOT EXISTS cuzdan (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bakiye REAL DEFAULT 1000.0, guncelleme TEXT)""")
    c.execute("SELECT COUNT(*) FROM cuzdan")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO cuzdan (bakiye, guncelleme) VALUES (?, ?)",
                  (ILK_BAKIYE, datetime.now().isoformat()))
    c.execute("""CREATE TABLE IF NOT EXISTS islemler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin TEXT, yon TEXT, giris_fiyati REAL,
        hedef_r1 REAL, stop_loss REAL, orjinal_stop REAL,
        marjin REAL DEFAULT 15.0, kaldirac INTEGER DEFAULT 5,
        pozisyon_usd REAL DEFAULT 75.0, risk_usd REAL DEFAULT 10.0,
        durum TEXT, kâr_r REAL DEFAULT 0.0, kâr_usd REAL DEFAULT 0.0,
        is_be INTEGER DEFAULT 0, tarih TEXT, skor REAL DEFAULT 0.0,
        grafik_dosya TEXT DEFAULT '', mod TEXT DEFAULT 'IZOLE')""")
    try:
        c.execute("ALTER TABLE islemler ADD COLUMN risk_usd REAL DEFAULT 10.0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE islemler ADD COLUMN grafik_dosya TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE islemler ADD COLUMN mod TEXT DEFAULT 'IZOLE'")
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
    c.execute("""SELECT coin, yon, giris_fiyati, stop_loss, hedef_r1, tarih, skor,
                        grafik_dosya, marjin, kaldirac, risk_usd
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
    mesaj = (
        f"📋 <b>VADELİ SAATLİK PnL</b>\n"
        f"🕒 {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"📂 <code>{DOSYA_ADI}</code>\n"
        f"💰 Cüzdan: <b>${bakiye:.2f}</b> / ${ILK_BAKIYE:.0f}\n"
        f"🔒 Marjin/poz: ${MARJIN_USD:.0f} İZOLE | Risk ${RISK_USD:.0f}\n\n"
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
            emoji = "🟢" if pnl_usd >= 0 else "🔴"
            mesaj += (
                f"\n• <b>{coin}</b> {yon} İZOLE\n"
                f"  Giriş {giris:.6f} | Anlık {anlik:.6f}\n"
                f"  {emoji} PnL: <b>${pnl_usd:+.2f}</b> ({r_mult:+.2f}R)\n"
                f"  📎 <code>{grafik or 'Yok'}</code>\n"
            )
        mesaj += f"\n────────────────\n📊 Toplam açık PnL: <b>${toplam:+.2f}</b>\n"
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

            # SuperTrend cizgisi + AL / SAT isaretleri (TV gibi)
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
                    if st_line is not None and st_line.notna().sum() > 5:
                        addplots.append(mpf.make_addplot(st_line, color="#26a69a", width=1.6, panel=0))

                    # AL / SAT noktalari: yon degisimi
                    if st_dir is not None and st_dir.notna().sum() > 5:
                        buy_marks = pd.Series(np.nan, index=df_plot.index)
                        sell_marks = pd.Series(np.nan, index=df_plot.index)
                        d = st_dir.fillna(0).values
                        lows = low.values
                        highs = high.values
                        for i in range(1, len(d)):
                            prev, cur = d[i - 1], d[i]
                            # SUPERTd: 1 = bullish (AL), -1 = bearish (SAT)
                            if prev < 0 and cur > 0:
                                buy_marks.iloc[i] = lows[i] * 0.997  # mum altina
                            elif prev > 0 and cur < 0:
                                sell_marks.iloc[i] = highs[i] * 1.003  # mum ustune
                        if buy_marks.notna().sum() > 0:
                            addplots.append(mpf.make_addplot(
                                buy_marks, type="scatter", markersize=80,
                                marker="^", color="#00c853", panel=0,
                            ))
                        if sell_marks.notna().sum() > 0:
                            addplots.append(mpf.make_addplot(
                                sell_marks, type="scatter", markersize=80,
                                marker="v", color="#d50000", panel=0,
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

            _hl(giris, "#2196f3", "-", 2.0)
            _hl(stop, "#f44336", "-", 2.0)
            _hl(hedef, "#4caf50", "-", 2.0)

            fib_levels = (info or {}).get("fib_levels") or {}
            fib_colors = {
                0.236: "#9e9e9e", 0.382: "#ff9800", 0.5: "#42a5f5",
                0.618: "#66bb6a", 0.786: "#e91e63", 0.886: "#9c27b0",
            }
            for ratio, level in fib_levels.items():
                _hl(level, fib_colors.get(ratio, "#757575"), "--", 0.9)

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
                title=f"{symbol} | {yon} | ST+RSI | Giriş/SL/TP/Fib",
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
                f"{'▲ LONG' if yon == 'LONG' else '▼ SHORT'}  |  ▲ AL  ▼ SAT (SuperTrend)",
                xy=(0.02, 0.96), xycoords="axes fraction", fontsize=10,
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
        stop = fiyat - atr * ATR_SL_MULT
        hedef = fiyat + atr * ATR_TP_MULT
        foto = grafik_ciz(df, symbol, "LONG", 0.0, info, giris=fiyat, stop=stop, hedef=hedef)
        if foto and os.path.exists(foto):
            telegram_foto(
                foto,
                f"🧪 <b>TEST GRAFİK</b> (sinyal değil)\n"
                f"📂 <code>{DOSYA_ADI}</code>\n"
                f"🪙 {symbol} | 1H\n"
                f"💰 GİRİŞ: {fiyat:.4f}\n"
                f"🛡 SL: {stop:.4f}\n"
                f"🎯 TP: {hedef:.4f}\n"
                f"📐 Fib + ST + RSI\n"
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

    atr = info.get("atr") or fiyat * 0.02
    if atr <= 0:
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
        f"⚡ <b>{symbol}</b> — <b>{yon}</b> (VADELİ İZOLE)\n"
        f"📂 <code>{DOSYA_ADI}</code>\n"
        f"⭐ Skor: {skor:.1f} | RSI: {info.get('rsi', 0):.1f}\n"
        f"🔒 Marjin: <b>${MARJIN_USD:.0f}</b> | Risk SL: <b>${RISK_USD:.0f}</b> | TP: <b>${TP_USD:.0f}</b>\n"
        f"📊 {KALDIRAC}x | Notional ~${POZISYON_USD:.0f}\n"
        f"📈 Hacim: %{(son_hacim / ort_hacim * 100) if ort_hacim else 0:.0f}\n\n"
        f"💰 <b>GİRİŞ:</b> {fiyat:.6f}\n"
        f"🛡 <b>SL (1×ATR):</b> {stop:.6f}\n"
        f"🎯 <b>TP (3×ATR):</b> {hedef:.6f}\n"
        f"📎 Grafik: <code>{dosya_adi}</code>\n"
        f"⚠️ Sanal vadeli | RR 1:3"
    )
    if foto:
        telegram_foto(foto, mesaj)
    else:
        telegram_mesaj(mesaj)


def acik_islemleri_kontrol(guncel_fiyatlar: dict):
    conn, c = db_baglanti()
    c.execute("""SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop,
                        is_be, marjin, grafik_dosya
                 FROM islemler WHERE durum='ACIK'""")
    rows = c.fetchall()
    for row in rows:
        islem_id, coin, yon, giris, hedef, stop, orj_stop, is_be, marjin, grafik = row
        if coin not in guncel_fiyatlar:
            continue
        anlik = guncel_fiyatlar[coin]
        risk_px = abs(giris - (orj_stop or stop))
        if risk_px <= 0:
            continue
        mevcut_r = (anlik - giris) / risk_px if yon == "LONG" else (giris - anlik) / risk_px
        dosya_notu = f"\n📎 {grafik or DOSYA_ADI}"

        if mevcut_r >= 1.0 and is_be == 0:
            c.execute("UPDATE islemler SET stop_loss=?, is_be=1 WHERE id=?", (giris, islem_id))
            telegram_mesaj(f"🛡 <b>{coin}</b> BE (stop girişe){dosya_notu}")

        if yon == "LONG":
            if anlik >= hedef:
                pnl = TP_USD
                yeni = bakiye_guncelle(pnl)
                telegram_mesaj(f"✅ <b>{coin} LONG TP 3R</b>\n+${pnl:.2f} | Cüzdan ${yeni:.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=3.0, kâr_usd=? WHERE id=?", (pnl, islem_id))
            elif anlik <= stop:
                pnl = 0.0 if is_be else -RISK_USD
                yeni = bakiye_guncelle(pnl)
                durum = "BE" if is_be else "LOSS"
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} LONG</b> {durum}\n${pnl:+.2f} | Cüzdan ${yeni:.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?",
                          (durum, 0.0 if is_be else -1.0, pnl, islem_id))
        else:
            if anlik <= hedef:
                pnl = TP_USD
                yeni = bakiye_guncelle(pnl)
                telegram_mesaj(f"✅ <b>{coin} SHORT TP 3R</b>\n+${pnl:.2f} | Cüzdan ${yeni:.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=3.0, kâr_usd=? WHERE id=?", (pnl, islem_id))
            elif anlik >= stop:
                pnl = 0.0 if is_be else -RISK_USD
                yeni = bakiye_guncelle(pnl)
                durum = "BE" if is_be else "LOSS"
                telegram_mesaj(f"{'🛡' if is_be else '❌'} <b>{coin} SHORT</b> {durum}\n${pnl:+.2f} | Cüzdan ${yeni:.2f}{dosya_notu}")
                c.execute("UPDATE islemler SET durum=?, kâr_r=?, kâr_usd=? WHERE id=?",
                          (durum, 0.0 if is_be else -1.0, pnl, islem_id))
    conn.commit()
    conn.close()


def tam_tarama():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Vadeli tarama | {DOSYA_ADI}")
    db_kurulum()
    guncel = {}
    for coin in HEDEF_COINLER:
        try:
            result = analiz_yap(coin)
            if len(result) == 5:
                skor, fiyat, yon, info, df = result
            else:
                continue
            if fiyat:
                guncel[coin] = fiyat
            if df is not None and yon in ("LONG", "SHORT") and info:
                if (yon == "LONG" and info.get("st_buy") and info.get("rsi_ok_long")) or (
                    yon == "SHORT" and info.get("st_sell") and info.get("rsi_ok_short")
                ):
                    telegram_gonder(coin, skor, fiyat, df, yon, info)
            time.sleep(0.10)
        except Exception as e:
            print(f"Hata {coin}: {e}")
    acik_islemleri_kontrol(guncel)
    try:
        saatlik_rapor_gonder(guncel)
    except Exception as e:
        print(f"Rapor hatasi: {e}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tarama bitti. Cuzdan=${bakiye_oku():.2f}")
    return guncel


if __name__ == "__main__":
    acilis_mesaji_goster()
    telegram_mesaj(
        f"🚀 <b>Vadeli Sanal Bot v16.1</b>\n\n"
        f"📂 <code>{DOSYA_ADI}</code>\n"
        f"💰 Cüzdan: <b>${ILK_BAKIYE:.0f}</b>\n"
        f"🔒 Marjin: <b>${MARJIN_USD:.0f} İZOLE</b>\n"
        f"🛡 SL risk: <b>${RISK_USD:.0f}</b> | 🎯 TP: <b>${TP_USD:.0f}</b> (1:3)\n"
        f"⚡ {KALDIRAC}x | TF: 1H | SuperTrend+RSI\n"
        f"📋 {len(HEDEF_COINLER)} USDT-SWAP coin\n"
        f"📊 Grafik: <b>Giriş / SL / TP + Fib etiketli</b>\n"
        f"🧪 Baslangicta test grafiği gonderilir"
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

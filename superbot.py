#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gelişmiş Kripto Sinyal Botu - Production Sürümü
(Render Web Service / Flask Keep-Alive & GitHub Actions Uyumlu)
"""

import os
import sys
import time
import sqlite3
import threading
from datetime import datetime
import requests
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.utils import get_column_letter
from flask import Flask

# ==========================================
# 1. FLASK KEEP-ALIVE SERVER (Render Free Tier Uyumlu)
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

    skor_l += 0.75
    krit_l.append("Fibonacci: 0.75 - Neutral Fib zone near 0.886")
    skor_s += 0.75
    krit_s.append("Fibonacci: 0.75 - Neutral Fib zone near 0.886")

    rsi = info.get("rsi", 50)
    if rsi > 50:
        skor_l += 1.12
        krit_l.append(f"RSI: 1.12 - RSI {rsi:.1f} bullish")
    else:
        skor_s += 1.12
        krit_s.append(f"RSI: 1.12 - RSI {rsi:.1f} bearish")

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

    skor_l += 0.68
    krit_l.append("Equal High: 0.68 - Equal highs detected")
    skor_s += 0.68
    krit_s.append("Equal Low: 0.68 - Equal lows detected")

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
# 5. GRAFİK OLUŞTURMA
# ==========================================
def grafik_ciz(df: pd.DataFrame, symbol: str, yon: str, skor: float) -> str:
    df_plot = df.tail(120).copy()
    df_plot["MA50"] = ta.sma(df_plot["close"], 50)
    df_plot["MA100"] = ta.sma(df_plot["close"], 100)
    df_plot["MA200"] = ta.sma(df_plot["close"], 200)

    fig = make_subplots(rows=1, cols=1)
    fig.add_trace(go.Candlestick(
        x=df_plot.index, open=df_plot["open"], high=df_plot["high"],
        low=df_plot["low"], close=df_plot["close"], name="Fiyat",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350"
    ))
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["MA50"], line=dict(color="#42a5f5", width=1.5), name="MA50"))
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["MA100"], line=dict(color="#ffa726", width=1.5), name="MA100"))
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["MA200"], line=dict(color="#66bb6a", width=2), name="MA200"))

    fig.update_layout(
        title=f"{symbol} | {yon} | Skor {skor:.2f}/20 | 4H",
        yaxis_title="Fiyat", xaxis_rangeslider_visible=False,
        template="plotly_dark", height=500, margin=dict(l=40, r=40, t=50, b=40)
    )

    dosya = os.path.join(ARTIFACT_DIR, f"{symbol.replace('-', '_')}_chart.png")
    fig.write_image(dosya, scale=2)
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
        print(f"\n[TELEGRAM MESAJ]\n{text}\n")
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
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                          files={"photo": f}, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}, timeout=30)
    except Exception as e:
        print(f"Telegram foto hata: {e}")

def telegram_belge_gonder(dosya_yolu: str, caption: str = ""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"\n[TELEGRAM BELGE] {dosya_yolu}\n")
        return
    try:
        with open(dosya_yolu, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                files={"document": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                timeout=30
            )
    except Exception as e:
        print(f"Telegram belge gönderme hatası: {e}")

def acik_islemleri_kontrol(guncel_fiyatlar: dict):
    conn, c = db_baglanti()
    c.execute("SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, is_be FROM islemler WHERE durum='ACIK'")
    rows = c.fetchall()
    guncelleme = False

    for row in rows:
        islem_id, coin, yon, giris, hedef, stop, orj_stop, is_be = row
        if coin not in guncel_fiyatlar:
            continue
        anlik = guncel_fiyatlar[coin]
        risk = abs(giris - (orj_stop or stop))
        if risk <= 0:
            continue
        mevcut_r = (anlik - giris) / risk if yon == "LONG" else (giris - anlik) / risk

        # +1R olunca stop'u girişe çek (Break-Even)
        if mevcut_r >= 1.0 and is_be == 0:
            c.execute("UPDATE islemler SET stop_loss=?, is_be=1 WHERE id=?", (giris, islem_id))
            telegram_mesaj(
                f"🛡 <b>STOPU GİRİŞE ÇEK (BE)</b>\n\n"
                f"📌 <b>{coin} ({yon})</b>\n"
                f"📈 İşlem +1R kâra geçti!\n"
                f"🎯 Borsa stop seviyesini giriş fiyatına ({giris}) çek."
            )
            is_be = 1
            stop = giris  # Yerel stop değişkenini de güncelle ki sonraki kontrol doğru olsun
            guncelleme = True

        if yon == "LONG":
            if anlik >= hedef:
                telegram_mesaj(f"✅ <b>{coin} (LONG)</b>\n💰 Hedefe Ulaştı! (+1.50R)")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5 WHERE id=?", (islem_id,))
                guncelleme = True
            elif anlik <= stop:
                durum, r_val = ('BE', 0.0) if is_be else ('LOSS', -1.0)
                emoji = '🛡' if is_be else '❌'
                mesaj_durum = 'Başabaş Kapanış' if is_be else 'Stop Oldu'
                telegram_mesaj(f"{emoji} <b>{coin} (LONG)</b>\n{mesaj_durum} ({r_val:+.2f}R)")
                c.execute("UPDATE islemler SET durum=?, kâr_r=? WHERE id=?", (durum, r_val, islem_id))
                guncelleme = True
        else:  # SHORT
            if anlik <= hedef:
                telegram_mesaj(f"✅ <b>{coin} (SHORT)</b>\n💰 Hedefe Ulaştı! (+1.50R)")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5 WHERE id=?", (islem_id,))
                guncelleme = True
            elif anlik >= stop:
                durum, r_val = ('BE', 0.0) if is_be else ('LOSS', -1.0)
                emoji = '🛡' if is_be else '❌'
                mesaj_durum = 'Başabaş Kapanış' if is_be else 'Stop Oldu'
                telegram_mesaj(f"{emoji} <b>{coin} (SHORT)</b>\n{mesaj_durum} ({r_val:+.2f}R)")
                c.execute("UPDATE islemler SET durum=?, kâr_r=? WHERE id=?", (durum, r_val, islem_id))
                guncelleme = True

    conn.commit()
    conn.close()
    if guncelleme:
        excel_raporu_olustur()

# ==========================================
# 7. EXCEL & SIYAH DASHBOARD RAPORLARI
# ==========================================
def siyah_dashboard_olustur():
    conn = sqlite3.connect(os.path.join(ARTIFACT_DIR, "islemler.db"))
    df = pd.read_sql_query("SELECT * FROM islemler WHERE durum != 'ACIK'", conn)
    conn.close()

    if df.empty:
        return None

    df['tarih_dt'] = pd.to_datetime(df['tarih'], errors='coerce')
    df['ay'] = df['tarih_dt'].dt.strftime('%Y-%m')
    
    aylik = df.groupby('ay')['kâr_r'].sum().reset_index().sort_values('ay', ascending=True)

    toplam_r = df['kâr_r'].sum()
    ort_r = aylik['kâr_r'].mean() if not aylik.empty else 0.0
    en_yuksek_ay = aylik['kâr_r'].max() if not aylik.empty else 0.0
    son_ay_r = aylik['kâr_r'].iloc[-1] if not aylik.empty else 0.0

    toplam_pozitif_r = aylik[aylik['kâr_r'] > 0]['kâr_r'].sum()
    aylik['katki_yuzde'] = aylik['kâr_r'].apply(
        lambda x: f"{(x / toplam_pozitif_r * 100):.2f}%" if (toplam_pozitif_r > 0 and x > 0) else "0.00%"
    )

    aylar = aylik['ay'].tolist()
    getiriler = [f"+{r:.2f} R" if r >= 0 else f"{r:.2f} R" for r in aylik['kâr_r']]
    katkilar = aylik['katki_yuzde'].tolist()
    durumlar = ["🟢 Başarılı" if r > 0 else ("🔴 Zarar" if r < 0 else "⚪ Nötr") for r in aylik['kâr_r']]

    fig = go.Figure()

    fig.add_trace(go.Table(
        header=dict(
            values=["<b>DÖNEM / AY</b>", "<b>AYLIK GETİRİ (R)</b>", "<b>KATKI PAYI (%)</b>", "<b>DURUM</b>"],
            fill_color='#1f2937', align='center', font=dict(color='white', size=13, family="Arial")
        ),
        cells=dict(
            values=[aylar, getiriler, katkilar, durumlar],
            fill_color='#111827', align='center', font=dict(color=['#9ca3af', '#10b981', '#3b82f6', 'white'], size=12, family="Arial"), height=30
        )
    ))

    baslik_metni = (
        f"<b>KRİPTO SİNYAL BOTU PERFORMANS ÖZETİ</b><br>"
        f"<span style='font-size:12px; color:#9ca3af;'>Aylık Algoritmik Ticaret Raporu</span><br><br>"
        f"<span style='color:#10b981;'><b>TOPLAM KAZANÇ:</b> +{toplam_r:.1f} R</span> &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>ORTALAMA:</b> +{ort_r:.1f} R &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>EN YÜKSEK AY:</b> +{en_yuksek_ay:.1f} R &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>SON AY:</b> +{son_ay_r:.1f} R"
    )

    fig.update_layout(
        title=dict(text=baslik_metni, x=0.5, y=0.92, xanchor='center', font=dict(size=15, color="white")),
        paper_bgcolor="#0b0f19", plot_bgcolor="#0b0f19", width=800, height=500, margin=dict(l=20, r=20, t=110, b=20)
    )

    dosya_yolu = os.path.join(ARTIFACT_DIR, "dashboard_ozet.png")
    fig.write_image(dosya_yolu, scale=2)
    return dosya_yolu

def excel_raporu_olustur():
    conn, _ = db_baglanti()
    df = pd.read_sql_query("SELECT * FROM islemler", conn)
    conn.close()

    dosya = os.path.join(ARTIFACT_DIR, "Performans_Raporu.xlsx")
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Coin Performansı"
    ws2 = wb.create_sheet("Ozet")

    if df.empty:
        return dosya

    ozet = df.groupby("coin").agg(
        Toplam_Islem=("id", "count"),
        WIN=("durum", lambda x: (x == "WIN").sum()),
        LOSS=("durum", lambda x: (x == "LOSS").sum()),
        BE=("durum", lambda x: (x == "BE").sum()),
        Toplam_R=("kâr_r", "sum")
    ).reset_index()

    ozet["Kapanan_Net"] = ozet["WIN"] + ozet["LOSS"]
    ozet["Win Rate (%)"] = ozet.apply(lambda r: f"{(r['WIN'] / r['Kapanan_Net'] * 100):.1f}%" if r["Kapanan_Net"] > 0 else "0.0%", axis=1)
    ozet["Ort. R / İşlem"] = ozet.apply(lambda r: round(r["Toplam_R"] / r["Toplam_Islem"], 3) if r["Toplam_Islem"] > 0 else 0, axis=1)

    def degerlendirme(r):
        ort = r["Ort. R / İşlem"]
        n = r["Toplam_Islem"]
        # Ekran görüntüsündeki mantığa daha yakın
        if n >= 5 and ort < -0.2:
            return "ELE - kötü performans"
        if ort < 0 or n < 5:
            return "Riskli - takip et" if n >= 5 else "Riskli - az örneklem"
        if ort >= 0.5 and n >= 5:
            return "Güçlü - tut"
        if ort >= 0.5 and n < 5:
            return "Güçlü - tut (az örneklem)"
        return "Normal"

    ozet["Değerlendirme"] = ozet.apply(degerlendirme, axis=1)
    ozet = ozet[["coin", "Toplam_Islem", "WIN", "LOSS", "BE", "Win Rate (%)", "Toplam_R", "Ort. R / İşlem", "Değerlendirme"]].sort_values("Ort. R / İşlem", ascending=False)

    header_fill = PatternFill("solid", fgColor="3B5998")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))
    fills = {
        "ELE": PatternFill("solid", fgColor="F5CBA7"),       # Turuncu
        "Riskli": PatternFill("solid", fgColor="F9E79F"),    # Sarı
        "Güçlü": PatternFill("solid", fgColor="A9DFBF"),     # Yeşil
        "Normal": PatternFill("solid", fgColor="FFFFFF"),
    }

    for r_idx, row in enumerate(dataframe_to_rows(ozet, index=False, header=True), 1):
        for c_idx, value in enumerate(row, 1):
            cell = ws1.cell(row=r_idx, column=c_idx, value=value)
            cell.border = thin
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if r_idx == 1:
                cell.font = header_font
                cell.fill = header_fill
            else:
                deg = str(ozet.iloc[r_idx - 2]["Değerlendirme"])
                for key, fill in fills.items():
                    if key in deg:
                        cell.fill = fill
                        break

    for col in ws1.columns:
        ws1.column_dimensions[get_column_letter(col[0].column)].width = 18

    toplam_islem = int(ozet["Toplam_Islem"].sum())
    toplam_win = int(ozet["WIN"].sum())
    toplam_loss = int(ozet["LOSS"].sum())
    toplam_be = int(ozet["BE"].sum())
    genel_wr = f"{(toplam_win / (toplam_win + toplam_loss) * 100):.1f}%" if (toplam_win + toplam_loss) > 0 else "0.0%"
    ele_sayisi = int(ozet["Değerlendirme"].str.contains("ELE").sum())

    ozet_veriler = [
        ("Sinyal Botu Performans Özeti", ""),
        ("Toplam İşlem Sayısı", toplam_islem),
        ("Toplam Coin Sayısı", len(ozet)),
        ("Toplam WIN", toplam_win),
        ("Toplam LOSS", toplam_loss),
        ("Toplam BE", toplam_be),
        ("Genel Win Rate", genel_wr),
        ("Toplam R (kümülatif)", round(ozet["Toplam_R"].sum(), 2)),
        ("ELE önerilen coin sayısı (az örneklem hariç)", ele_sayisi),
    ]

    for r_idx, (k, v) in enumerate(ozet_veriler, 1):
        ws2.cell(row=r_idx, column=1, value=k).font = Font(bold=True) if r_idx > 1 else Font(bold=True, size=14)
        ws2.cell(row=r_idx, column=2, value=v)

    # Renk kodu açıklaması
    ws2.cell(row=len(ozet_veriler) + 2, column=1, value="Renk Kodu Açıklaması").font = Font(bold=True, size=12)
    aciklamalar = [
        ("ELE - kötü performans", "min 5 işlem, ortalama R < -0.2 (net zarar ettiriyor)"),
        ("Riskli / az örneklem (sarı)", "ortalama R negatif; ya da <5 işlem ile belirsiz sonuç"),
        ("Güçlü - tut", "ortalama R >= 0.5, yeterli örneklem (>=5 işlem)"),
        ("Güçlü ama az örneklem (açık yeşil)", "ortalama R >= 0.5 ama <5 işlem — umut verici, daha fazla veri topla"),
    ]
    for i, (baslik, aciklama) in enumerate(aciklamalar, start=len(ozet_veriler) + 3):
        ws2.cell(row=i, column=1, value=baslik)
        ws2.cell(row=i, column=2, value=aciklama)

    ws2.column_dimensions["A"].width = 45
    ws2.column_dimensions["B"].width = 55
    wb.save(dosya)
    return dosya

def performans_ozeti_metin() -> str:
    conn = sqlite3.connect(os.path.join(ARTIFACT_DIR, "islemler.db"))
    df = pd.read_sql_query("SELECT * FROM islemler WHERE durum != 'ACIK'", conn)
    conn.close()
    if df.empty: return "Henüz kapanmış işlem bulunmuyor."

    toplam_r = df["kâr_r"].sum()
    return f"📊 <b>Kripto Sinyal Botu Performans Özeti</b>\n\n🟢 <b>TOPLAM KAZANÇ</b>: +{toplam_r:.1f} R\n🎯 <b>KAPANAN İŞLEM</b>: {len(df)} Adet"

def telegram_gonder(symbol, skor, kriterler, fiyat, df, yon, mtf_list, mtf_bonus):
    mesaj = f"🧠 <b>{symbol} – {yon}</b>\n⭐ Skor: {skor:.2f}/20\n⏱ MTF bonus: +{mtf_bonus:.2f}/2\n\n<b>4H kriterleri:</b>\n"
    for k in kriterler:
        mesaj += f"• {k}\n"
    mesaj += "\n<b>Zaman dilimleri:</b>\n"
    for m in mtf_list:
        mesaj += f"{m}\n"

    atr = ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        atr = fiyat * 0.02
    stop = fiyat - (atr * 1.5) if yon == "LONG" else fiyat + (atr * 1.5)
    hedef = (fiyat + (fiyat - stop) * 1.5) if yon == "LONG" else (fiyat - (stop - fiyat) * 1.5)

    mesaj += f"\n💰 Giriş: {fiyat:.6f}\n🛡️ Stop: {stop:.6f}\n🎯 Hedef (1.5R): {hedef:.6f}"
    mesaj += "\n\n⚠️ Bu bot yalnızca teknik/algoritmik analiz üretir; garanti edilmiş fiyat hareketi veya yatırım tavsiyesi değildir."

    try:
        foto = grafik_ciz(df, symbol, yon, skor)
        telegram_foto(foto, mesaj)
    except Exception:
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

    excel_dosyasi = excel_raporu_olustur()
    ozet_metni = performans_ozeti_metin()
    
    telegram_mesaj(ozet_metni)

    dashboard_img = siyah_dashboard_olustur()
    if dashboard_img and os.path.exists(dashboard_img):
        telegram_foto(dashboard_img, "📊 <b>Kripto Sinyal Botu - Görsel Performans Özeti</b>")

    if excel_dosyasi and os.path.exists(excel_dosyasi):
        telegram_belge_gonder(excel_dosyasi, "📊 Güncel Kripto Performans Raporu (Excel)")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Tarama ve raporlama tamamlandı.")

if __name__ == "__main__":
    if RUN_ONCE:
        tek_seferlik_tarama()
    else:
        # Flask Web Sunucusunu Arka Planda Başlat (Render Free Web Service Uyumlu)
        threading.Thread(target=run_flask, daemon=True).start()
        print("🤖 Bot sürekli çalışma modunda (Flask Web Service) başlatıldı.")
        
        while True:
            try:
                tek_seferlik_tarama()
            except Exception as e:
                print(f"Döngü hatası: {e}")
            print(f"💤 {TARAMA_ARALIGI_DURMA_SANIYE} saniye bekleniyor...")
            time.sleep(TARAMA_ARALIGI_DURMA_SANIYE)

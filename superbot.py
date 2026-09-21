#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanal İşlem Botu v13.0
------------------------------------------------------------
v12.5 üzerine eklenenler:

1) Skor filtresi DEĞİŞMEDİ:
   MIN_SKOR = 15.0

2) Hacim filtresi gevşetildi:
   Hacim >= Son 20 mum ortalama hacminin %70'i
   Eski amaçlanan yüksek hacim filtresine göre daha gevşek.

3) ETH/USDT 1H Keltner Channel eklendi:
   EMA = 46
   ATR = 81
   Multiplier = 2.5

4) Keltner dönüş sinyali:
   - Alt bandın altında kapanış -> sonraki kapanmış mum
     tekrar alt bandın üstüne çıkarsa LONG
   - Üst bandın üstünde kapanış -> sonraki kapanmış mum
     tekrar üst bandın altına inerse SHORT

5) Sadece kapanmış 1H mumlar kullanılır.

6) Aynı Keltner mumundan tekrar tekrar sinyal üretilmez.

7) ETH Keltner sinyali SMC skorundan bağımsız ek sinyal
   olarak çalışabilir.

8) Telegram'da Keltner üst/orta/alt bantları gösterilir.

9) Mevcut SQLite / Excel / Telegram / Flask / sanal
   pozisyon sistemi korunmuştur.
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
    return "OK - Sanal İşlem Botu v13.0 Aktif", 200


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

RUN_ONCE = os.environ.get(
    "RUN_ONCE", "false"
).lower() in ["true", "1", "yes"]

ILK_BAKIYE = 500.0
MARJIN_USD = 15.0
KALDIRAC = 5
POZISYON_USD = MARJIN_USD * KALDIRAC

# Aynı coin'e tekrar sinyal süresi
SIGNAL_COOLDOWN_MINUTES = 45

# ==========================================
# SKOR
# ==========================================
# DOKUNMADIK
MIN_SKOR = 15.0


# ==========================================
# HACİM FİLTRESİ
# ==========================================
# Daha önce yüksek hacim şartı kullanılacak şekilde
# tasarlanan sistem burada %70'e gevşetildi.
#
# Örnek:
# Son mum hacmi >= son 20 mum ortalamasının %70'i
#
# Böylece %90'a göre daha fazla sinyal geçebilir.
VOLUME_MA_LENGTH = 20
VOLUME_MIN_RATIO = 0.70


# ==========================================
# KELTNER CHANNEL
# ==========================================

KC_SYMBOL = "ETH-USDT"
KC_BAR = "1H"

KC_EMA_LENGTH = 46
KC_ATR_LENGTH = 81
KC_MULTIPLIER = 2.5

# Son işlenen Keltner sinyal mumları
last_kc_signal_candle = {}


# ==========================================
# DİĞER
# ==========================================

OKX_BASE = "https://www.okx.com"

os.makedirs(ARTIFACT_DIR, exist_ok=True)

matplotlib_lock = threading.Lock()

# Son normal SMC sinyal zamanları
last_signal_time = {}


# ==========================================
# HEDEF COINLER
# ==========================================

HEDEF_COINLER = [
    'ONT-USDT', 'JUP-USDT', 'SNX-USDT', 'LDO-USDT', 'ZETA-USDT',
    'AAVE-USDT', 'TIA-USDT', 'VET-USDT', 'DYDX-USDT', 'LTC-USDT',
    'AVAX-USDT', 'SUSHI-USDT', 'STX-USDT', 'XRP-USDT', 'ORDI-USDT',
    'ENS-USDT', 'GALA-USDT', 'MANA-USDT', 'ICP-USDT', 'THETA-USDT',
    'TRX-USDT', 'EGLD-USDT', 'ADA-USDT', 'AXS-USDT', 'INJ-USDT',
    'AEVO-USDT', 'BCH-USDT', 'FLOKI-USDT', 'DOT-USDT', 'RUNE-USDT',
    'KAS-USDT', 'COMP-USDT', 'BONK-USDT', 'ALT-USDT', 'BTC-USDT',
    'FIL-USDT', 'WIF-USDT', 'PYTH-USDT', 'FET-USDT', 'PEPE-USDT',
    'SHIB-USDT', 'SUI-USDT', 'ATOM-USDT', 'SAND-USDT', 'PORTAL-USDT',
    'APT-USDT', 'STRK-USDT', 'ARB-USDT', 'DYM-USDT', 'METIS-USDT',
    'BNB-USDT', 'UNI-USDT', 'SEI-USDT', 'HBAR-USDT', 'MANTA-USDT',
    'LINK-USDT', 'OP-USDT', 'XAI-USDT', 'ALPINE-USDT', 'MAV-USDT',
    'DOGE-USDT', 'ETHFI-USDT', 'CRV-USDT', 'PIXEL-USDT', 'IMX-USDT',
    'ETH-USDT', 'GRT-USDT', 'ENJ-USDT', 'ONE-USDT', 'SOL-USDT',
    'NEAR-USDT', 'ALGO-USDT', 'PENDLE-USDT'
]


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
# 3. VERİ ÇEKME
# ==========================================

def veri_cek(symbol: str, bar: str = "4H", limit: int = 250):
    try:
        url = f"{OKX_BASE}/api/v5/market/candles"

        params = {
            "instId": symbol,
            "bar": bar,
            "limit": str(limit)
        }

        r = requests.get(
            url,
            params=params,
            timeout=12
        )

        if r.status_code != 200:
            return None

        data = r.json()

        if data.get("code") != "0" or not data.get("data"):
            return None

        df = pd.DataFrame(
            data["data"],
            columns=[
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "volCcy",
                "volCcyQuote",
                "confirm"
            ]
        )

        df = df.iloc[::-1].reset_index(drop=True)

        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df["timestamp"] = pd.to_datetime(
            df["ts"].astype(float),
            unit="ms"
        )

        df.set_index("timestamp", inplace=True)

        df = df[
            [
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        ].dropna()

        df = df.sort_index(ascending=True)

        return df if len(df) >= 30 else None

    except Exception as e:
        print(f"Veri hatası {symbol}: {e}")
        return None


# ==========================================
# 3.1 HACİM FİLTRESİ
# ==========================================

def hacim_yeterli_mi(df: pd.DataFrame):
    """
    Son mum hacmi, son 20 mum ortalamasının
    en az %70'i olmalı.

    Böylece hacim filtresi önceki daha sert
    yapıya göre gevşetilmiş olur.
    """

    try:
        if df is None or len(df) < VOLUME_MA_LENGTH + 2:
            return False, 0.0, 0.0

        volume_ma = (
            df["volume"]
            .rolling(VOLUME_MA_LENGTH)
            .mean()
            .iloc[-1]
        )

        son_hacim = float(df["volume"].iloc[-1])

        if pd.isna(volume_ma) or volume_ma <= 0:
            return False, son_hacim, volume_ma

        oran = son_hacim / volume_ma

        return (
            oran >= VOLUME_MIN_RATIO,
            son_hacim,
            volume_ma
        )

    except Exception:
        return False, 0.0, 0.0


# ==========================================
# 3.2 KELTNER CHANNEL
# ==========================================

def keltner_eth_1h():
    """
    ETH-USDT 1H Keltner Channel.

    EMA 46
    ATR 81
    Multiplier 2.5

    Sinyal:

    LONG:
        Önceki kapanış < önceki alt band
        VE
        Son kapanış > son alt band

    SHORT:
        Önceki kapanış > önceki üst band
        VE
        Son kapanış < son üst band

    Sadece kapanmış mumlar kullanılır.
    """

    try:

        df = veri_cek(
            KC_SYMBOL,
            KC_BAR,
            200
        )

        if df is None:
            return None

        if len(df) < KC_ATR_LENGTH + 5:
            return None

        # OKX son mum genellikle devam eden mumdur.
        # Keltner sinyalini yalnızca kapanmış mumlarla
        # değerlendirmek için son mumu çıkartıyoruz.
        kapali = df.iloc[:-1].copy()

        if len(kapali) < KC_ATR_LENGTH + 3:
            return None

        kapali["KC_MID"] = ta.ema(
            kapali["close"],
            length=KC_EMA_LENGTH
        )

        kapali["KC_ATR"] = ta.atr(
            kapali["high"],
            kapali["low"],
            kapali["close"],
            length=KC_ATR_LENGTH
        )

        kapali["KC_UPPER"] = (
            kapali["KC_MID"]
            + KC_MULTIPLIER * kapali["KC_ATR"]
        )

        kapali["KC_LOWER"] = (
            kapali["KC_MID"]
            - KC_MULTIPLIER * kapali["KC_ATR"]
        )

        kapali = kapali.dropna(
            subset=[
                "KC_MID",
                "KC_ATR",
                "KC_UPPER",
                "KC_LOWER"
            ]
        )

        if len(kapali) < 3:
            return None

        onceki = kapali.iloc[-2]
        son = kapali.iloc[-1]

        sinyal = None

        # ======================================
        # LONG
        # Alt bandın altındaydı,
        # tekrar alt bandın üstüne çıktı.
        # ======================================

        if (
            float(onceki["close"]) < float(onceki["KC_LOWER"])
            and
            float(son["close"]) > float(son["KC_LOWER"])
        ):
            sinyal = "LONG"

        # ======================================
        # SHORT
        # Üst bandın üstündeydi,
        # tekrar üst bandın altına indi.
        # ======================================

        elif (
            float(onceki["close"]) > float(onceki["KC_UPPER"])
            and
            float(son["close"]) < float(son["KC_UPPER"])
        ):
            sinyal = "SHORT"

        fiyat = float(son["close"])
        upper = float(son["KC_UPPER"])
        middle = float(son["KC_MID"])
        lower = float(son["KC_LOWER"])
        atr = float(son["KC_ATR"])

        # ======================================
        # DURUM
        # ======================================

        if fiyat > upper:
            durum = "ÜST BAND ÜZERİNDE"
        elif fiyat < lower:
            durum = "ALT BAND ALTINDA"
        elif fiyat >= middle:
            durum = "KANAL İÇİ / ÜST YARISI"
        else:
            durum = "KANAL İÇİ / ALT YARISI"

        if sinyal == "LONG":
            durum = "ALT BANDDAN GERİ DÖNÜŞ → LONG"
        elif sinyal == "SHORT":
            durum = "ÜST BANDDAN GERİ DÖNÜŞ → SHORT"

        candle_time = kapali.index[-1]

        return {
            "symbol": KC_SYMBOL,
            "bar": KC_BAR,
            "signal": sinyal,
            "price": fiyat,
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "atr": atr,
            "status": durum,
            "candle_time": candle_time,
            "data": kapali
        }

    except Exception as e:
        print(f"Keltner hatası: {e}")
        return None


# ==========================================
# 4. ANALİZ
# ==========================================

def basit_smc_ve_indikator(df: pd.DataFrame) -> dict:

    if df is None or len(df) < 60:
        return {}

    df = df.copy()

    df["RSI"] = ta.rsi(
        df["close"],
        length=14
    )

    df["MA50"] = ta.sma(
        df["close"],
        length=50
    )

    df["MA100"] = ta.sma(
        df["close"],
        length=100
    )

    df["MA200"] = ta.sma(
        df["close"],
        length=200
    )

    df["ATR"] = ta.atr(
        df["high"],
        df["low"],
        df["close"],
        length=14
    )

    def find_significant_swings(
        high_series,
        low_series,
        order=5,
        lookback=80
    ):
        highs, lows = [], []

        data_len = len(high_series)

        start = max(
            order,
            data_len - lookback
        )

        for i in range(
            start,
            data_len - order
        ):

            window_h = high_series.iloc[
                i-order:i+order+1
            ]

            window_l = low_series.iloc[
                i-order:i+order+1
            ]

            if high_series.iloc[i] == window_h.max():
                highs.append(
                    (
                        i,
                        float(high_series.iloc[i])
                    )
                )

            if low_series.iloc[i] == window_l.min():
                lows.append(
                    (
                        i,
                        float(low_series.iloc[i])
                    )
                )

        return highs, lows

    swing_highs, swing_lows = find_significant_swings(
        df["high"],
        df["low"],
        order=5,
        lookback=80
    )

    last_swing_high = (
        swing_highs[-1][1]
        if swing_highs
        else safe_max(df["high"].iloc[-60:])
    )

    last_swing_low = (
        swing_lows[-1][1]
        if swing_lows
        else safe_min(df["low"].iloc[-60:])
    )

    if abs(last_swing_high - last_swing_low) < (
        df["close"].iloc[-1] * 0.012
    ):

        last_swing_high = safe_max(
            df["high"].iloc[-80:]
        )

        last_swing_low = safe_min(
            df["low"].iloc[-80:]
        )

    # ======================================
    # FVG
    # ======================================

    df["FVG_up"] = (
        (df["low"].shift(-1) > df["high"].shift(1))
        &
        (df["close"] > df["open"])
    )

    df["FVG_down"] = (
        (df["high"].shift(-1) < df["low"].shift(1))
        &
        (df["close"] < df["open"])
    )

    fvg_zones = []

    for i in range(
        max(0, len(df)-14),
        len(df)-1
    ):

        if df["FVG_up"].iloc[i]:

            fvg_zones.append({
                "type": "bullish",
                "top": float(
                    df["low"].iloc[i+1]
                ),
                "bottom": float(
                    df["high"].iloc[i-1]
                )
            })

        if df["FVG_down"].iloc[i]:

            fvg_zones.append({
                "type": "bearish",
                "top": float(
                    df["low"].iloc[i-1]
                ),
                "bottom": float(
                    df["high"].iloc[i+1]
                )
            })

    # ======================================
    # ORDER BLOCK
    # ======================================

    ob_zones = []

    atr_val = (
        float(df["ATR"].iloc[-1])
        if pd.notna(df["ATR"].iloc[-1])
        else float(df["close"].iloc[-1]) * 0.02
    )

    for i in range(
        max(0, len(df)-20),
        len(df)-2
    ):

        body = abs(
            df["close"].iloc[i]
            -
            df["open"].iloc[i]
        )

        if body > atr_val * 0.85:

            if df["close"].iloc[i] > df["open"].iloc[i]:

                ob_zones.append({
                    "type": "bullish",
                    "top": float(
                        df["high"].iloc[i]
                    ),
                    "bottom": float(
                        df["low"].iloc[i]
                    )
                })

            else:

                ob_zones.append({
                    "type": "bearish",
                    "top": float(
                        df["high"].iloc[i]
                    ),
                    "bottom": float(
                        df["low"].iloc[i]
                    )
                })

    son = df.iloc[-1]

    onceki = (
        df.iloc[-2]
        if len(df) > 1
        else son
    )

    fiyat = float(son["close"])

    recent = (
        df.iloc[-10:-1]
        if len(df) >= 11
        else df.iloc[:-1]
    )

    recent_low = safe_min(
        recent["low"]
    )

    recent_high = safe_max(
        recent["high"]
    )

    look = 20

    bos_high = (
        safe_max(df["high"].iloc[-look:-1])
        if len(df) > look
        else safe_max(df["high"].iloc[:-1])
    )

    bos_low = (
        safe_min(df["low"].iloc[-look:-1])
        if len(df) > look
        else safe_min(df["low"].iloc[:-1])
    )

    # ======================================
    # Equal High / Low
    # ======================================

    eq_look = min(
        20,
        len(df)-1
    )

    equal_high = False
    equal_low = False

    tol = atr_val * 0.25

    highs = df["high"].iloc[-eq_look:]
    lows = df["low"].iloc[-eq_look:]

    for i in range(len(highs)-1):

        if abs(
            highs.iloc[i]
            -
            highs.iloc[-1]
        ) < tol:

            equal_high = True
            break

    for i in range(len(lows)-1):

        if abs(
            lows.iloc[i]
            -
            lows.iloc[-1]
        ) < tol:

            equal_low = True
            break

    # ======================================
    # Fibonacci
    # ======================================

    fib_range = (
        last_swing_high
        -
        last_swing_low
    )

    fib_levels = {}

    fib_zone = "Neutral"

    fib_score_l = 0.60
    fib_score_s = 0.60

    fib_text = "Neutral"

    if fib_range > fiyat * 0.008:

        fib_levels = {
            0.236:
                last_swing_high
                - fib_range * 0.236,

            0.382:
                last_swing_high
                - fib_range * 0.382,

            0.500:
                last_swing_high
                - fib_range * 0.500,

            0.618:
                last_swing_high
                - fib_range * 0.618,

            0.786:
                last_swing_high
                - fib_range * 0.786,

            0.886:
                last_swing_high
                - fib_range * 0.886,
        }

        nearest_level = None
        nearest_dist = float("inf")

        for ratio, lvl in fib_levels.items():

            dist = abs(
                fiyat - lvl
            )

            if dist < nearest_dist:

                nearest_dist = dist
                nearest_level = ratio

        near_threshold = atr_val * 1.15

        if (
            nearest_level is not None
            and
            nearest_dist < near_threshold
        ):

            if nearest_level in (
                0.618,
                0.786,
                0.886
            ):

                fib_zone = "Bullish"
                fib_score_l = 1.25
                fib_score_s = 0.40

                fib_text = (
                    f"Bullish Fib zone "
                    f"near {nearest_level:.3f}"
                )

            elif nearest_level in (
                0.236,
                0.382
            ):

                if fiyat > (
                    last_swing_high
                    +
                    last_swing_low
                ) / 2:

                    fib_zone = "Bearish"
                    fib_score_l = 0.40
                    fib_score_s = 1.15

                    fib_text = (
                        f"Bearish Fib zone "
                        f"near {nearest_level:.3f}"
                    )

                else:

                    fib_zone = "Bullish"
                    fib_score_l = 1.05
                    fib_score_s = 0.50

                    fib_text = (
                        f"Bullish Fib zone "
                        f"near {nearest_level:.3f}"
                    )

            else:

                fib_zone = "Neutral"
                fib_score_l = 0.85
                fib_score_s = 0.85

                fib_text = (
                    "Neutral Fib zone "
                    f"near {nearest_level:.3f}"
                )

        else:

            pct = (
                (last_swing_high - fiyat)
                /
                fib_range
                if fib_range > 0
                else 0.5
            )

            if pct >= 0.70:

                fib_zone = "Bullish"
                fib_score_l = 1.10
                fib_score_s = 0.45

                fib_text = (
                    "Bullish "
                    "(deep in swing range)"
                )

            elif pct <= 0.30:

                fib_zone = "Bearish"
                fib_score_l = 0.45
                fib_score_s = 1.10

                fib_text = (
                    "Bearish "
                    "(high in swing range)"
                )

            else:

                fib_zone = "Neutral"
                fib_score_l = 0.70
                fib_score_s = 0.70

                fib_text = (
                    "Neutral "
                    "(mid swing range)"
                )

    else:

        fib_text = "Weak swing range"

    return {

        "fiyat": fiyat,

        "rsi":
            float(son["RSI"])
            if pd.notna(son["RSI"])
            else 50.0,

        "atr": atr_val,

        "bullish_fvg":
            bool(
                df["FVG_up"]
                .iloc[-5:]
                .any()
            ),

        "bearish_fvg":
            bool(
                df["FVG_down"]
                .iloc[-5:]
                .any()
            ),

        "above_ma200":
            fiyat >
            (
                float(son["MA200"])
                if pd.notna(son["MA200"])
                else 0
            ),

        "above_ma100":
            fiyat >
            (
                float(son["MA100"])
                if pd.notna(son["MA100"])
                else 0
            ),

        "above_ma50":
            fiyat >
            (
                float(son["MA50"])
                if pd.notna(son["MA50"])
                else 0
            ),

        "sellside_sweep":
            (
                not np.isnan(recent_low)
                and
                son["low"] < recent_low
                and
                son["close"] > recent_low
            ),

        "buyside_sweep":
            (
                not np.isnan(recent_high)
                and
                son["high"] > recent_high
                and
                son["close"] < recent_high
            ),

        "bullish_bos":
            (
                not np.isnan(bos_high)
                and
                son["close"] > bos_high
            ),

        "bearish_bos":
            (
                not np.isnan(bos_low)
                and
                son["close"] < bos_low
            ),

        "bullish_ob":
            (
                onceki["close"] > onceki["open"]
                and
                (
                    onceki["close"]
                    -
                    onceki["open"]
                ) > atr_val * 0.8
            ),

        "bearish_ob":
            (
                onceki["close"] < onceki["open"]
                and
                (
                    onceki["open"]
                    -
                    onceki["close"]
                ) > atr_val * 0.8
            ),

        "equal_high": equal_high,
        "equal_low": equal_low,

        "fib_zone": fib_zone,

        "fib_score_l":
            fib_score_l,

        "fib_score_s":
            fib_score_s,

        "fib_text":
            fib_text,

        "fib_levels":
            fib_levels,

        "fvg_zones":
            fvg_zones[-4:],

        "ob_zones":
            ob_zones[-4:],

        "last_swing_high":
            last_swing_high,

        "last_swing_low":
            last_swing_low,
    }


# ==========================================
# 4.1 SKOR
# ==========================================

def skor_hesapla(info: dict):

    skor_l = 0.0
    skor_s = 0.0

    krit_l = []
    krit_s = []

    if info.get("sellside_sweep"):

        skor_l += 1.57

        krit_l.append(
            "Liquidity Sweep: 1.57 - "
            "Sell-side sweep + reclaim"
        )

    if info.get("buyside_sweep"):

        skor_s += 1.57

        krit_s.append(
            "Liquidity Sweep: 1.57 - "
            "Buy-side sweep + rejection"
        )

    if info.get("bullish_ob"):

        skor_l += 1.57

        krit_l.append(
            "Order Block: 1.57 - "
            "Bullish OB at price"
        )

    if info.get("bearish_ob"):

        skor_s += 1.57

        krit_s.append(
            "Order Block: 1.57 - "
            "Bearish OB at price"
        )

    if info.get("bullish_bos"):

        skor_l += 1.80 + 1.35

        krit_l += [
            "BOS: 1.80 - Bullish BOS",
            "CHoCH: 1.35 - Bullish CHoCH"
        ]

    if info.get("bearish_bos"):

        skor_s += 1.80 + 1.35

        krit_s += [
            "BOS: 1.80 - Bearish BOS",
            "CHoCH: 1.35 - Bearish CHoCH"
        ]

    if info.get("bullish_fvg"):

        skor_l += 1.35 + 1.35

        krit_l += [
            "FVG: 1.35 - Bullish FVG",
            "SFP: 1.35 - Bullish SFP"
        ]

    if info.get("bearish_fvg"):

        skor_s += 1.35 + 1.35

        krit_s += [
            "FVG: 1.35 - Bearish FVG",
            "SFP: 1.35 - Bearish SFP"
        ]

    if info.get("above_ma50"):

        skor_l += 1.12 + 1.12

        krit_l += [
            "Breaker Block: 1.12 - "
            "Bullish breaker",

            "PO3: 1.12 - "
            "Bullish AMD/PO3 proxy"
        ]

    else:

        skor_s += 1.12 + 1.12

        krit_s += [
            "Breaker Block: 1.12 - "
            "Bearish breaker",

            "PO3: 1.12 - "
            "Bearish AMD/PO3 proxy"
        ]

    skor_l += info.get(
        "fib_score_l",
        0.60
    )

    skor_s += info.get(
        "fib_score_s",
        0.60
    )

    fib_text = info.get(
        "fib_text",
        "Neutral"
    )

    fib_zone = info.get(
        "fib_zone",
        "Neutral"
    )

    if fib_zone == "Bullish":

        krit_l.append(
            f"Fibonacci: "
            f"{info.get('fib_score_l', 1.10):.2f}"
            f" - {fib_text}"
        )

        krit_s.append(
            f"Fibonacci: "
            f"{info.get('fib_score_s', 0.45):.2f}"
            f" - Opposite"
        )

    elif fib_zone == "Bearish":

        krit_l.append(
            f"Fibonacci: "
            f"{info.get('fib_score_l', 0.45):.2f}"
            f" - Opposite"
        )

        krit_s.append(
            f"Fibonacci: "
            f"{info.get('fib_score_s', 1.10):.2f}"
            f" - {fib_text}"
        )

    else:

        krit_l.append(
            f"Fibonacci: "
            f"{info.get('fib_score_l', 0.70):.2f}"
            f" - {fib_text}"
        )

        krit_s.append(
            f"Fibonacci: "
            f"{info.get('fib_score_s', 0.70):.2f}"
            f" - {fib_text}"
        )

    rsi = info.get(
        "rsi",
        50
    )

    if rsi > 58:

        skor_l += 1.12

        krit_l.append(
            f"RSI: 1.12 - "
            f"RSI {rsi:.1f} bullish"
        )

    elif rsi < 42:

        skor_s += 1.12

        krit_s.append(
            f"RSI: 1.12 - "
            f"RSI {rsi:.1f} bearish"
        )

    else:

        skor_l += 0.45
        skor_s += 0.45

        krit_l.append(
            f"RSI: 0.45 - "
            f"RSI {rsi:.1f} neutral"
        )

        krit_s.append(
            f"RSI: 0.45 - "
            f"RSI {rsi:.1f} neutral"
        )

    if info.get("above_ma200"):

        skor_l += 1.35

        krit_l.append(
            "MA 200: 1.35 - "
            "Price above MA200"
        )

    else:

        skor_s += 1.35

        krit_s.append(
            "MA 200: 1.35 - "
            "Price below MA200"
        )

    if info.get("above_ma100"):

        skor_l += 0.90

        krit_l.append(
            "MA 100: 0.90 - "
            "Price above MA100 (bullish)"
        )

    else:

        skor_s += 0.90

        krit_s.append(
            "MA 100: 0.90 - "
            "Price below MA100 (bearish)"
        )

    if info.get("above_ma50"):

        skor_l += 0.90

        krit_l.append(
            "MA 50: 0.90 - "
            "Price above MA50"
        )

    else:

        skor_s += 0.90

        krit_s.append(
            "MA 50: 0.90 - "
            "Price below MA50"
        )

    if info.get("equal_high"):

        skor_s += 0.68

        krit_s.append(
            "Equal High: 0.68 - "
            "Equal highs detected"
        )

    if info.get("equal_low"):

        skor_l += 0.68

        krit_l.append(
            "Equal Low: 0.68 - "
            "Equal lows detected"
        )

    if skor_l >= skor_s:

        return (
            skor_l,
            krit_l,
            "LONG"
        )

    return (
        skor_s,
        krit_s,
        "SHORT"
    )


# ==========================================
# 4.2 MTF
# ==========================================

def mtf_analiz(symbol: str):

    mtf_list = []

    t1 = 0
    t4 = 0
    t1h = 0

    for label, bar in [
        ("1D", "1D"),
        ("4H", "4H"),
        ("1H", "1H")
    ]:

        df = veri_cek(
            symbol,
            bar=bar,
            limit=100
        )

        if df is None or len(df) < 50:

            mtf_list.append(
                f"• {label}: DATA YETERSIZ"
            )

            continue

        ma50 = ta.sma(
            df["close"],
            50
        ).iloc[-1]

        p = df["close"].iloc[-1]

        if pd.isna(ma50):

            mtf_list.append(
                f"• {label}: DATA YETERSIZ"
            )

            continue

        t = 1 if p > ma50 else -1

        yon = (
            "LONG"
            if t == 1
            else
            "SHORT"
        )

        skor_approx = (
            8.0
            +
            (2.0 if t == 1 else 0)
        )

        mtf_list.append(
            f"• {label}: {yon} "
            f"({skor_approx:.2f}/18)"
        )

        if label == "1D":
            t1 = t

        elif label == "4H":
            t4 = t

        else:
            t1h = t

    bonus_l = (
        2.0
        if (
            t1 == 1
            and t4 == 1
            and t1h == 1
        )
        else 0.0
    )

    bonus_s = (
        2.0
        if (
            t1 == -1
            and t4 == -1
            and t1h == -1
        )
        else 0.0
    )

    return (
        mtf_list,
        bonus_l,
        bonus_s
    )


# ==========================================
# 4.3 ANA ANALİZ
# ==========================================

def analiz_yap(symbol: str):

    df = veri_cek(
        symbol,
        "4H",
        250
    )

    if df is None or len(df) < 60:

        return (
            0.0,
            [],
            0.0,
            None,
            "YOK",
            [],
            0.0,
            None
        )

    info = basit_smc_ve_indikator(df)

    if not info:

        return (
            0.0,
            [],
            0.0,
            None,
            "YOK",
            [],
            0.0,
            None
        )

    skor, kriterler, yon = skor_hesapla(info)

    mtf_list, bonus_l, bonus_s = mtf_analiz(
        symbol
    )

    mtf_bonus = (
        bonus_l
        if yon == "LONG"
        else bonus_s
    )

    skor += mtf_bonus

    return (
        skor,
        kriterler,
        info["fiyat"],
        df,
        yon,
        mtf_list,
        mtf_bonus,
        info
    )


# ==========================================
# 5. GRAFİK
# ==========================================

def grafik_ciz(
    df,
    symbol,
    yon,
    skor,
    info=None,
    giris=None,
    stop=None,
    hedef=None
):

    with matplotlib_lock:

        try:

            if df is None or len(df) < 30:
                return None

            df_plot = (
                df.copy()
                .sort_index(ascending=True)
                .tail(100)
            )

            if len(df_plot) < 20:
                return None

            df_plot = df_plot.rename(
                columns={
                    'open': 'Open',
                    'high': 'High',
                    'low': 'Low',
                    'close': 'Close',
                    'volume': 'Volume'
                }
            )

            ma50 = df_plot[
                'Close'
            ].rolling(
                50,
                min_periods=10
            ).mean()

            ma100 = df_plot[
                'Close'
            ].rolling(
                100,
                min_periods=20
            ).mean()

            ma200 = df_plot[
                'Close'
            ].rolling(
                200,
                min_periods=30
            ).mean()

            addplots = []

            if ma50.notna().sum() > 8:

                addplots.append(
                    mpf.make_addplot(
                        ma50,
                        color='#1e88e5',
                        width=1.5
                    )
                )

            if ma100.notna().sum() > 8:

                addplots.append(
                    mpf.make_addplot(
                        ma100,
                        color='#fb8c00',
                        width=1.5
                    )
                )

            if ma200.notna().sum() > 8:

                addplots.append(
                    mpf.make_addplot(
                        ma200,
                        color='#43a047',
                        width=1.7
                    )
                )

            hlines = {
                "hlines": [],
                "colors": [],
                "linestyle": [],
                "linewidths": [],
                "alpha": 0.9
            }

            if info and info.get("fib_levels"):

                for price in info[
                    "fib_levels"
                ].values():

                    hlines[
                        "hlines"
                    ].append(price)

                    hlines[
                        "colors"
                    ].append('#7e57c2')

                    hlines[
                        "linestyle"
                    ].append('--')

                    hlines[
                        "linewidths"
                    ].append(1.0)

            if giris is not None:

                hlines[
                    "hlines"
                ].append(giris)

                hlines[
                    "colors"
                ].append('#2196f3')

                hlines[
                    "linestyle"
                ].append('-')

                hlines[
                    "linewidths"
                ].append(1.8)

            if stop is not None:

                hlines[
                    "hlines"
                ].append(stop)

                hlines[
                    "colors"
                ].append('#f44336')

                hlines[
                    "linestyle"
                ].append('-')

                hlines[
                    "linewidths"
                ].append(1.8)

            if hedef is not None:

                hlines[
                    "hlines"
                ].append(hedef)

                hlines[
                    "colors"
                ].append('#4caf50')

                hlines[
                    "linestyle"
                ].append('-')

                hlines[
                    "linewidths"
                ].append(1.8)

            mc = mpf.make_marketcolors(
                up='#26a69a',
                down='#ef5350',
                edge='inherit',
                wick={
                    'up': '#26a69a',
                    'down': '#ef5350'
                }
            )

            s = mpf.make_mpf_style(
                base_mpf_style='yahoo',
                marketcolors=mc,
                facecolor='white',
                edgecolor='#e0e0e0',
                figcolor='white',
                gridcolor='#f5f5f5',
                y_on_right=False,
                rc={
                    'axes.labelcolor': '#333',
                    'xtick.color': '#555',
                    'ytick.color': '#555',
                    'axes.titlesize': 12,
                    'axes.titleweight': 'bold'
                }
            )

            dosya = os.path.join(
                ARTIFACT_DIR,
                f"{symbol.replace('-', '_')}_chart.png"
            )

            fig, axes = mpf.plot(
                df_plot,
                type='candle',
                style=s,
                addplot=(
                    addplots
                    if addplots
                    else None
                ),
                hlines=(
                    hlines
                    if hlines["hlines"]
                    else None
                ),
                title=(
                    f"{symbol.replace('-', '/')}"
                    f" | {yon}"
                    f" | Skor: {skor:.2f}/20"
                    f" | 4H"
                ),
                returnfig=True,
                volume=False,
                figsize=(13, 7),
                tight_layout=True,
                datetime_format='%d/%m %H:%M',
                xrotation=15,
                scale_padding={
                    'left': 0.05,
                    'right': 0.28,
                    'top': 0.08,
                    'bottom': 0.15
                }
            )

            ax = axes[0]

            if info:

                for z in info.get(
                    "fvg_zones",
                    []
                ):

                    color = (
                        '#bbdefb80'
                        if z["type"] == "bullish"
                        else
                        '#ffcdd280'
                    )

                    ax.axhspan(
                        z["bottom"],
                        z["top"],
                        facecolor=color,
                        zorder=0
                    )

                for z in info.get(
                    "ob_zones",
                    []
                ):

                    color = (
                        '#90caf980'
                        if z["type"] == "bullish"
                        else
                        '#ef9a9a80'
                    )

                    ax.axhspan(
                        z["bottom"],
                        z["top"],
                        facecolor=color,
                        edgecolor='#ffffff40',
                        linewidth=0.6,
                        zorder=1
                    )

            if giris is not None:

                ax.annotate(
                    f'ENTRY {giris:.5f}',
                    xy=(1.01, giris),
                    xycoords=(
                        'axes fraction',
                        'data'
                    ),
                    fontsize=9,
                    color='#2196f3',
                    fontweight='bold',
                    va='center'
                )

            if stop is not None:

                ax.annotate(
                    f'SL {stop:.5f}',
                    xy=(1.01, stop),
                    xycoords=(
                        'axes fraction',
                        'data'
                    ),
                    fontsize=9,
                    color='#f44336',
                    fontweight='bold',
                    va='center'
                )

            if hedef is not None:

                ax.annotate(
                    f'TP {hedef:.5f}',
                    xy=(1.01, hedef),
                    xycoords=(
                        'axes fraction',
                        'data'
                    ),
                    fontsize=9,
                    color='#4caf50',
                    fontweight='bold',
                    va='center'
                )

            if yon == "LONG":

                ax.annotate(
                    '▲ LONG',
                    xy=(0.02, 0.95),
                    xycoords='axes fraction',
                    fontsize=14,
                    color='#26a69a',
                    fontweight='bold'
                )

            else:

                ax.annotate(
                    '▼ SHORT',
                    xy=(0.02, 0.95),
                    xycoords='axes fraction',
                    fontsize=14,
                    color='#ef5350',
                    fontweight='bold'
                )

            fig.savefig(
                dosya,
                dpi=150,
                bbox_inches='tight',
                facecolor='white'
            )

            plt.close(fig)

            return dosya

        except Exception as e:

            plt.close('all')

            print(
                f"Grafik hatası ({symbol}): {e}"
            )

            return None


# ==========================================
# 6. VERİTABANI + EXCEL
# ==========================================

def db_baglanti():

    conn = sqlite3.connect(
        os.path.join(
            ARTIFACT_DIR,
            "islemler.db"
        ),
        check_same_thread=False
    )

    return conn, conn.cursor()


def db_kurulum():

    conn, c = db_baglanti()

    c.execute("""
        CREATE TABLE IF NOT EXISTS cuzdan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bakiye REAL DEFAULT 500.0,
            guncelleme TEXT
        )
    """)

    c.execute(
        "SELECT COUNT(*) FROM cuzdan"
    )

    if c.fetchone()[0] == 0:

        c.execute(
            """
            INSERT INTO cuzdan
            (bakiye, guncelleme)
            VALUES (?, ?)
            """,
            (
                ILK_BAKIYE,
                datetime.now().isoformat()
            )
        )

    c.execute(
        "PRAGMA table_info(islemler)"
    )

    kolonlar = [
        row[1]
        for row in c.fetchall()
    ]

    if (
        not kolonlar
        or
        "pozisyon_usd" not in kolonlar
    ):

        c.execute(
            "DROP TABLE IF EXISTS islemler"
        )

        c.execute("""
            CREATE TABLE islemler (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT,
                yon TEXT,
                giris_fiyati REAL,
                hedef_r1 REAL,
                stop_loss REAL,
                orjinal_stop REAL,
                marjin REAL DEFAULT 15.0,
                kaldirac INTEGER DEFAULT 5,
                pozisyon_usd REAL DEFAULT 75.0,
                durum TEXT,
                kâr_r REAL DEFAULT 0.0,
                kâr_usd REAL DEFAULT 0.0,
                is_be INTEGER DEFAULT 0,
                tarih TEXT,
                skor REAL DEFAULT 0.0
            )
        """)

    conn.commit()
    conn.close()


def acik_pozisyon_var_mi(coin):

    conn, c = db_baglanti()

    c.execute(
        """
        SELECT id
        FROM islemler
        WHERE coin=?
        AND durum='ACIK'
        """,
        (coin,)
    )

    row = c.fetchone()

    conn.close()

    return row is not None


def excel_kaydet():

    try:

        conn, c = db_baglanti()

        df_islem = pd.read_sql_query(
            """
            SELECT *
            FROM islemler
            ORDER BY id DESC
            """,
            conn
        )

        c.execute(
            """
            SELECT bakiye, guncelleme
            FROM cuzdan
            ORDER BY id DESC
            LIMIT 1
            """
        )

        row = c.fetchone()

        bakiye = (
            row[0]
            if row
            else ILK_BAKIYE
        )

        df_acik = pd.read_sql_query(
            """
            SELECT *
            FROM islemler
            WHERE durum='ACIK'
            """,
            conn
        )

        df_kapali = pd.read_sql_query(
            """
            SELECT *
            FROM islemler
            WHERE durum != 'ACIK'
            """,
            conn
        )

        conn.close()

        excel_path = os.path.join(
            ARTIFACT_DIR,
            "bot_kayitlari.xlsx"
        )

        with pd.ExcelWriter(
            excel_path,
            engine="openpyxl"
        ) as writer:

            ozet_data = {

                "Metrik": [
                    "Bakiye (USD)",
                    "Açık Pozisyon Sayısı",
                    "Toplam Kapanmış İşlem",
                    "WIN",
                    "LOSS",
                    "BE",
                    "Son Güncelleme"
                ],

                "Değer": [

                    round(
                        bakiye,
                        2
                    ),

                    len(df_acik),

                    len(df_kapali),

                    (
                        len(
                            df_kapali[
                                df_kapali["durum"]
                                == "WIN"
                            ]
                        )
                        if len(df_kapali)
                        else 0
                    ),

                    (
                        len(
                            df_kapali[
                                df_kapali["durum"]
                                == "LOSS"
                            ]
                        )
                        if len(df_kapali)
                        else 0
                    ),

                    (
                        len(
                            df_kapali[
                                df_kapali["durum"]
                                == "BE"
                            ]
                        )
                        if len(df_kapali)
                        else 0
                    ),

                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                ]
            }

            pd.DataFrame(
                ozet_data
            ).to_excel(
                writer,
                sheet_name="Cüzdan_Özet",
                index=False
            )

            if not df_acik.empty:

                df_acik.to_excel(
                    writer,
                    sheet_name="Açık_Pozisyonlar",
                    index=False
                )

            if not df_kapali.empty:

                df_kapali.to_excel(
                    writer,
                    sheet_name="Kapanmış_İşlemler",
                    index=False
                )

            if not df_islem.empty:

                df_islem.to_excel(
                    writer,
                    sheet_name="Tüm_İşlemler",
                    index=False
                )

        print(
            f"Excel kaydedildi: {excel_path}"
        )

        return excel_path

    except Exception as e:

        print(
            f"Excel kayıt hatası: {e}"
        )

        return None


def bakiye_guncelle(pnl_usd: float):

    conn, c = db_baglanti()

    c.execute(
        """
        SELECT bakiye
        FROM cuzdan
        ORDER BY id DESC
        LIMIT 1
        """
    )

    row = c.fetchone()

    mevcut = (
        row[0]
        if row
        else ILK_BAKIYE
    )

    yeni = mevcut + pnl_usd

    c.execute(
        """
        UPDATE cuzdan
        SET bakiye=?,
            guncelleme=?
        WHERE id=(
            SELECT MAX(id)
            FROM cuzdan
        )
        """,
        (
            yeni,
            datetime.now().isoformat()
        )
    )

    conn.commit()
    conn.close()

    excel_kaydet()

    return yeni


def islem_kaydet(
    coin,
    yon,
    giris,
    stop,
    skor=0.0
):

    if acik_pozisyon_var_mi(coin):
        return False

    risk = (
        abs(giris - stop)
        or
        giris * 0.02
    )

    hedef = (
        giris + risk * 1.5
        if yon == "LONG"
        else
        giris - risk * 1.5
    )

    tarih = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    conn, c = db_baglanti()

    c.execute(
        """
        INSERT INTO islemler
        (
            coin,
            yon,
            giris_fiyati,
            hedef_r1,
            stop_loss,
            orjinal_stop,
            marjin,
            kaldirac,
            pozisyon_usd,
            durum,
            kâr_r,
            kâr_usd,
            is_be,
            tarih,
            skor
        )
        VALUES
        (
            ?,?,?,?,?,?,?,?,?, 
            'ACIK',
            0,
            0,
            0,
            ?,
            ?
        )
        """,
        (
            coin,
            yon,
            giris,
            hedef,
            stop,
            stop,
            MARJIN_USD,
            KALDIRAC,
            POZISYON_USD,
            tarih,
            skor
        )
    )

    conn.commit()
    conn.close()

    excel_kaydet()

    return True


# ==========================================
# TELEGRAM
# ==========================================

def telegram_mesaj(text):

    if (
        not TELEGRAM_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):
        return

    try:

        requests.post(
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_TOKEN}/sendMessage",

            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            },

            timeout=10
        )

    except Exception as e:

        print(
            f"Mesaj hatası: {e}"
        )


def telegram_foto(
    path,
    caption=""
):

    if (
        not TELEGRAM_TOKEN
        or
        not TELEGRAM_CHAT_ID
        or
        not path
        or
        not os.path.exists(path)
    ):

        if caption:
            telegram_mesaj(caption)

        return

    try:

        with open(
            path,
            "rb"
        ) as f:

            requests.post(
                f"https://api.telegram.org/bot"
                f"{TELEGRAM_TOKEN}/sendPhoto",

                files={
                    "photo": f
                },

                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,

                    "caption":
                        caption,

                    "parse_mode":
                        "HTML"
                },

                timeout=30
            )

    except Exception as e:

        print(
            f"Foto hatası: {e}"
        )


# ==========================================
# 7. AÇIK İŞLEM KONTROL
# ==========================================

def acik_islemleri_kontrol(
    guncel_fiyatlar: dict
):

    conn, c = db_baglanti()

    c.execute(
        """
        SELECT
            id,
            coin,
            yon,
            giris_fiyati,
            hedef_r1,
            stop_loss,
            orjinal_stop,
            is_be,
            pozisyon_usd,
            marjin
        FROM islemler
        WHERE durum='ACIK'
        """
    )

    rows = c.fetchall()

    for row in rows:

        (
            islem_id,
            coin,
            yon,
            giris,
            hedef,
            stop,
            orj_stop,
            is_be,
            poz_usd,
            marjin
        ) = row

        if coin not in guncel_fiyatlar:
            continue

        anlik = guncel_fiyatlar[
            coin
        ]

        risk = abs(
            giris -
            (orj_stop or stop)
        )

        if risk <= 0:
            continue

        mevcut_r = (
            (anlik - giris) / risk
            if yon == "LONG"
            else
            (giris - anlik) / risk
        )

        # ==================================
        # BREAK EVEN
        # ==================================

        if (
            mevcut_r >= 1.0
            and
            is_be == 0
        ):

            c.execute(
                """
                UPDATE islemler
                SET stop_loss=?,
                    is_be=1
                WHERE id=?
                """,
                (
                    giris,
                    islem_id
                )
            )

            telegram_mesaj(
                f"🛡 <b>STOPU GİRİŞE ÇEK (BE)</b>\n\n"
                f"📌 <b>{coin} ({yon})</b>\n"
                f"📈 +1R kâra ulaşıldı!\n"
                f"🎯 Yeni Stop: {giris:.6f}"
            )

            is_be = 1

        # ==================================
        # LONG
        # ==================================

        if yon == "LONG":

            if anlik >= hedef:

                pnl_usd = (
                    poz_usd * 0.03
                )

                yeni_b = bakiye_guncelle(
                    pnl_usd
                )

                telegram_mesaj(
                    f"✅ <b>{coin} LONG</b>\n"
                    f"💰 TP1 (+1.5R)\n"
                    f"💵 +${pnl_usd:.2f}\n"
                    f"🏦 Cüzdan: ${yeni_b:.2f}"
                )

                c.execute(
                    """
                    UPDATE islemler
                    SET durum='WIN',
                        kâr_r=1.5,
                        kâr_usd=?
                    WHERE id=?
                    """,
                    (
                        pnl_usd,
                        islem_id
                    )
                )

            elif anlik <= stop:

                durum = (
                    'BE'
                    if is_be
                    else
                    'LOSS'
                )

                r_val = (
                    0.0
                    if is_be
                    else
                    -1.0
                )

                pnl_usd = (
                    0.0
                    if is_be
                    else
                    -marjin
                )

                yeni_b = bakiye_guncelle(
                    pnl_usd
                )

                emoji = (
                    "🛡"
                    if is_be
                    else
                    "❌"
                )

                telegram_mesaj(
                    f"{emoji} <b>{coin} LONG</b>\n"
                    f"{'BE' if is_be else 'Stop'}\n"
                    f"💵 ${pnl_usd:+.2f}\n"
                    f"🏦 ${yeni_b:.2f}"
                )

                c.execute(
                    """
                    UPDATE islemler
                    SET durum=?,
                        kâr_r=?,
                        kâr_usd=?
                    WHERE id=?
                    """,
                    (
                        durum,
                        r_val,
                        pnl_usd,
                        islem_id
                    )
                )

        # ==================================
        # SHORT
        # ==================================

        else:

            if anlik <= hedef:

                pnl_usd = (
                    poz_usd * 0.03
                )

                yeni_b = bakiye_guncelle(
                    pnl_usd
                )

                telegram_mesaj(
                    f"✅ <b>{coin} SHORT</b>\n"
                    f"💰 TP1 (+1.5R)\n"
                    f"💵 +${pnl_usd:.2f}\n"
                    f"🏦 Cüzdan: ${yeni_b:.2f}"
                )

                c.execute(
                    """
                    UPDATE islemler
                    SET durum='WIN',
                        kâr_r=1.5,
                        kâr_usd=?
                    WHERE id=?
                    """,
                    (
                        pnl_usd,
                        islem_id
                    )
                )

            elif anlik >= stop:

                durum = (
                    'BE'
                    if is_be
                    else
                    'LOSS'
                )

                r_val = (
                    0.0
                    if is_be
                    else
                    -1.0
                )

                pnl_usd = (
                    0.0
                    if is_be
                    else
                    -marjin
                )

                yeni_b = bakiye_guncelle(
                    pnl_usd
                )

                emoji = (
                    "🛡"
                    if is_be
                    else
                    "❌"
                )

                telegram_mesaj(
                    f"{emoji} <b>{coin} SHORT</b>\n"
                    f"{'BE' if is_be else 'Stop'}\n"
                    f"💵 ${pnl_usd:+.2f}\n"
                    f"🏦 ${yeni_b:.2f}"
                )

                c.execute(
                    """
                    UPDATE islemler
                    SET durum=?,
                        kâr_r=?,
                        kâr_usd=?
                    WHERE id=?
                    """,
                    (
                        durum,
                        r_val,
                        pnl_usd,
                        islem_id
                    )
                )

    conn.commit()
    conn.close()

    excel_kaydet()


# ==========================================
# 8. PERFORMANS
# ==========================================

def performans_tablosu_olustur():

    conn, c = db_baglanti()

    c.execute(
        """
        SELECT coin, durum, kâr_r
        FROM islemler
        WHERE durum != 'ACIK'
        """
    )

    rows = c.fetchall()

    conn.close()

    if not rows:
        return None, None

    stats = {}

    for coin, durum, r in rows:

        if coin not in stats:

            stats[coin] = {
                "WIN": 0,
                "LOSS": 0,
                "BE": 0,
                "toplam_r": 0.0,
                "islem": 0
            }

        stats[coin]["islem"] += 1

        if durum == "WIN":

            stats[coin]["WIN"] += 1

        elif durum == "LOSS":

            stats[coin]["LOSS"] += 1

        else:

            stats[coin]["BE"] += 1

        stats[coin]["toplam_r"] += (
            r or 0.0
        )

    data = []

    for coin, s in stats.items():

        wr = (
            s["WIN"]
            /
            s["islem"]
            *
            100
        ) if s["islem"] else 0

        ort_r = (
            s["toplam_r"]
            /
            s["islem"]
        ) if s["islem"] else 0

        if s["islem"] < 5:

            deger = "Az örneklem"

        elif ort_r < -0.2:

            deger = "ELE - kötü"

        elif ort_r < 0:

            deger = "Riskli"

        elif ort_r >= 0.5:

            deger = "Güçlü - tut"

        else:

            deger = "Normal"

        data.append({
            "Coin":
                coin.replace(
                    "-USDT",
                    ""
                ),

            "İşlem":
                s["islem"],

            "WIN":
                s["WIN"],

            "LOSS":
                s["LOSS"],

            "BE":
                s["BE"],

            "Win%":
                f"{wr:.1f}%",

            "Toplam R":
                f"{s['toplam_r']:.2f}",

            "Ort R":
                f"{ort_r:.3f}",

            "Değerlendirme":
                deger
        })

    df = pd.DataFrame(
        data
    ).sort_values(
        "Ort R",
        ascending=False
    )

    fig, ax = plt.subplots(
        figsize=(
            14,
            max(
                6,
                len(df) * 0.35
            )
        )
    )

    ax.axis('off')

    tbl = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        cellLoc='center',
        loc='center',
        colColours=[
            '#1565c0'
        ] * len(df.columns)
    )

    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1.2, 1.4)

    for (
        row,
        col
    ), cell in tbl.get_celld().items():

        if row == 0:

            cell.set_text_props(
                color='white',
                weight='bold'
            )

        else:

            val = df.iloc[
                row - 1
            ]["Değerlendirme"]

            if "ELE" in val:

                cell.set_facecolor(
                    '#ffcdd2'
                )

            elif (
                "Riskli" in val
                or
                "Az" in val
            ):

                cell.set_facecolor(
                    '#fff9c4'
                )

            elif "Güçlü" in val:

                cell.set_facecolor(
                    '#c8e6c9'
                )

            else:

                cell.set_facecolor(
                    '#e3f2fd'
                )

    plt.title(
        "Coin Performans Tablosu (Sanal)",
        fontsize=14,
        pad=20
    )

    dosya = os.path.join(
        ARTIFACT_DIR,
        "performans_tablosu.png"
    )

    plt.savefig(
        dosya,
        dpi=130,
        bbox_inches='tight',
        facecolor='white'
    )

    plt.close()

    toplam_islem = len(rows)

    toplam_win = sum(
        1
        for _, d, _ in rows
        if d == "WIN"
    )

    toplam_loss = sum(
        1
        for _, d, _ in rows
        if d == "LOSS"
    )

    toplam_be = sum(
        1
        for _, d, _ in rows
        if d == "BE"
    )

    genel_wr = (
        toplam_win
        /
        toplam_islem
        *
        100
    ) if toplam_islem else 0

    toplam_r = sum(
        r or 0
        for _, _, r in rows
    )

    ozet = f"""
📊 <b>SİNYAL BOTU PERFORMANS ÖZETİ</b>
────────────────────
Toplam İşlem: {toplam_islem}
Toplam Coin: {len(stats)}
WIN: {toplam_win} | LOSS: {toplam_loss} | BE: {toplam_be}
Genel Win Rate: %{genel_wr:.1f}
Toplam R: {toplam_r:.2f}
────────────────────
"""

    return dosya, ozet


# ==========================================
# 8.1 SAATLİK RAPOR
# ==========================================

def saatlik_rapor_gonder(
    guncel_fiyatlar: dict
):

    conn, c = db_baglanti()

    c.execute(
        """
        SELECT bakiye
        FROM cuzdan
        ORDER BY id DESC
        LIMIT 1
        """
    )

    row = c.fetchone()

    bakiye = (
        row[0]
        if row
        else ILK_BAKIYE
    )

    c.execute(
        """
        SELECT
            coin,
            yon,
            giris_fiyati,
            pozisyon_usd,
            stop_loss,
            hedef_r1
        FROM islemler
        WHERE durum='ACIK'
        """
    )

    aciklar = c.fetchall()

    conn.close()

    unrealized = 0.0
    detay = ""

    for (
        coin,
        yon,
        giris,
        poz,
        stop,
        hedef
    ) in aciklar:

        anlik = guncel_fiyatlar.get(
            coin,
            giris
        )

        pnl = (
            poz
            *
            (
                (anlik - giris)
                /
                giris
            )
            if yon == "LONG"
            else
            poz
            *
            (
                (giris - anlik)
                /
                giris
            )
        )

        unrealized += pnl

        yuzde = (
            (anlik - giris)
            /
            giris
            *
            100
            if yon == "LONG"
            else
            (giris - anlik)
            /
            giris
            *
            100
        )

        detay += (
            f"• <b>{coin}</b> "
            f"({yon}): "
            f"${pnl:+.2f} "
            f"(%{yuzde:+.1f})\n"
        )

    # ======================================
    # KELTNER DURUMU
    # ======================================

    kc = keltner_eth_1h()

    kc_text = ""

    if kc:

        kc_signal = (
            kc["signal"]
            if kc["signal"]
            else "SİNYAL YOK"
        )

        kc_text = (
            "\n📡 <b>ETH/USDT KELTNER 1H</b>\n"
            "────────────────────\n"
            f"💰 Fiyat: {kc['price']:.6f}\n"
            f"🔺 Üst: {kc['upper']:.6f}\n"
            f"〰️ Orta EMA46: {kc['middle']:.6f}\n"
            f"🔻 Alt: {kc['lower']:.6f}\n"
            f"📏 ATR81: {kc['atr']:.6f}\n"
            f"📌 Durum: <b>{kc['status']}</b>\n"
            f"🎯 Sinyal: <b>{kc_signal}</b>\n"
        )

    rapor = f"""
📊 <b>SAATLİK CÜZDAN RAPORU</b>
────────────────────
💵 Kullanılabilir: ${bakiye:.2f}
📈 Açık PnL: ${unrealized:+.2f}
💎 Toplam Varlık: ${bakiye + unrealized:.2f}

🔓 Açık Pozisyonlar ({len(aciklar)}):
{detay if detay else "• Yok"}
{kc_text}
────────────────────
📁 Excel kayıtları güncellendi.
"""

    telegram_mesaj(
        rapor
    )

    excel_kaydet()

    tablo_path, ozet = (
        performans_tablosu_olustur()
    )

    if ozet:
        telegram_mesaj(
            ozet
        )

    if tablo_path:

        telegram_foto(
            tablo_path,
            "📋 Coin Performans Tablosu"
        )


# ==========================================
# 9. NORMAL SİNYAL GÖNDER
# ==========================================

def telegram_gonder(
    symbol,
    skor,
    kriterler,
    fiyat,
    df,
    yon,
    mtf_list,
    mtf_bonus,
    info=None
):

    now = datetime.now()

    # ======================================
    # AÇIK POZİSYON
    # ======================================

    if acik_pozisyon_var_mi(symbol):

        print(
            f"Atlandı: {symbol} "
            f"için zaten açık pozisyon."
        )

        return

    # ======================================
    # COOLDOWN
    # ======================================

    last = last_signal_time.get(
        symbol
    )

    if (
        last
        and
        (
            now - last
        )
        <
        timedelta(
            minutes=
            SIGNAL_COOLDOWN_MINUTES
        )
    ):

        print(
            f"Cooldown: {symbol}"
        )

        return

    # ======================================
    # HACİM
    # ======================================

    hacim_ok, son_hacim, ort_hacim = (
        hacim_yeterli_mi(df)
    )

    if not hacim_ok:

        oran = (
            son_hacim / ort_hacim * 100
            if ort_hacim > 0
            else 0
        )

        print(
            f"Hacim yetersiz: "
            f"{symbol} "
            f"(%{oran:.1f} / gereken %{VOLUME_MIN_RATIO*100:.0f})"
        )

        return

    # ======================================
    # ATR / STOP
    # ======================================

    atr = ta.atr(
        df["high"],
        df["low"],
        df["close"],
        14
    ).iloc[-1]

    if (
        pd.isna(atr)
        or
        atr <= 0
    ):

        atr = fiyat * 0.02

    stop = (
        fiyat - atr * 1.5
        if yon == "LONG"
        else
        fiyat + atr * 1.5
    )

    hedef = (
        fiyat + (fiyat - stop) * 1.5
        if yon == "LONG"
        else
        fiyat - (stop - fiyat) * 1.5
    )

    # ======================================
    # DB'YE ÖNCE KAYDET
    # ======================================

    kaydedildi = islem_kaydet(
        symbol,
        yon,
        fiyat,
        stop,
        skor
    )

    if not kaydedildi:
        return

    last_signal_time[
        symbol
    ] = now

    # ======================================
    # TELEGRAM
    # ======================================

    mesaj = (
        f"🧠 <b>"
        f"{symbol.replace('-', '/')}"
        f" – {yon} "
        f"(5x Izole)</b>\n"
    )

    mesaj += (
        f"⭐ Skor: "
        f"{skor:.2f}/20\n"
    )

    if mtf_bonus > 0:

        mesaj += (
            f"⏱ MTF bonus: "
            f"+{mtf_bonus:.2f}/2\n"
        )

    mesaj += (
        f"💼 Marjin: ${MARJIN_USD} "
        f"| Pozisyon: ${POZISYON_USD}\n"
        f"📊 Hacim: "
        f"%{(son_hacim / ort_hacim * 100):.1f} "
        f"| Min: %{VOLUME_MIN_RATIO*100:.0f}\n\n"
    )

    mesaj += (
        "<b>4H Kriterleri:</b>\n"
    )

    for k in kriterler:

        mesaj += (
            f"• {k}\n"
        )

    mesaj += (
        "\n<b>Zaman Dilimleri:</b>\n"
    )

    for m in mtf_list:

        mesaj += (
            f"{m}\n"
        )

    mesaj += (
        f"\n────────────────────\n"
        f"💰 <b>GİRİŞ:</b> "
        f"{fiyat:.6f}\n"
        f"🛡 <b>SL:</b> "
        f"{stop:.6f}\n"
        f"🎯 <b>TP (1.5R):</b> "
        f"{hedef:.6f}\n\n"
        f"⚠️ Sanal işlem – "
        f"yatırım tavsiyesi değildir."
    )

    foto = grafik_ciz(
        df,
        symbol,
        yon,
        skor,
        info,
        giris=fiyat,
        stop=stop,
        hedef=hedef
    )

    if foto:

        telegram_foto(
            foto,
            (
                f"🧠 "
                f"{symbol.replace('-', '/')}"
                f" | {yon}"
                f" | Skor: {skor:.2f}"
            )
        )

    telegram_mesaj(
        mesaj
    )


# ==========================================
# 9.1 KELTNER SİNYAL GÖNDER
# ==========================================

def telegram_gonder_keltner(
    kc
):

    if not kc:
        return

    symbol = KC_SYMBOL
    yon = kc["signal"]

    if yon not in [
        "LONG",
        "SHORT"
    ]:
        return

    # ======================================
    # AYNI 1H MUMU TEKRAR İŞLEME
    # ======================================

    candle_time = kc[
        "candle_time"
    ]

    candle_key = (
        candle_time.isoformat()
        +
        "_"
        +
        yon
    )

    if (
        last_kc_signal_candle.get(
            symbol
        )
        ==
        candle_key
    ):

        print(
            f"Keltner aynı mum "
            f"tekrarlandı: {symbol}"
        )

        return

    # ======================================
    # AÇIK POZİSYON
    # ======================================

    if acik_pozisyon_var_mi(
        symbol
    ):

        print(
            f"Keltner atlandı: "
            f"{symbol} açık pozisyon."
        )

        return

    # ======================================
    # COOLDOWN
    # ======================================

    now = datetime.now()

    last = last_signal_time.get(
        symbol
    )

    if (
        last
        and
        (
            now - last
        )
        <
        timedelta(
            minutes=
            SIGNAL_COOLDOWN_MINUTES
        )
    ):

        print(
            f"Keltner cooldown: "
            f"{symbol}"
        )

        return

    fiyat = kc["price"]
    atr = kc["atr"]

    if atr <= 0:
        atr = fiyat * 0.02

    # Keltner sinyalinde 1H ATR kullanıyoruz.
    stop = (
        fiyat - atr * 1.5
        if yon == "LONG"
        else
        fiyat + atr * 1.5
    )

    hedef = (
        fiyat + (fiyat - stop) * 1.5
        if yon == "LONG"
        else
        fiyat - (stop - fiyat) * 1.5
    )

    # ======================================
    # DB'YE KAYDET
    # Keltner sinyali SMC skoru olmadığı
    # için skor alanına 0 yazılır.
    # ======================================

    kaydedildi = islem_kaydet(
        symbol,
        yon,
        fiyat,
        stop,
        0.0
    )

    if not kaydedildi:
        return

    # Önce sinyal mumunu kilitle
    last_kc_signal_candle[
        symbol
    ] = candle_key

    last_signal_time[
        symbol
    ] = now

    # ======================================
    # TELEGRAM
    # ======================================

    yon_emoji = (
        "🟢"
        if yon == "LONG"
        else
        "🔴"
    )

    mesaj = f"""
{yon_emoji} <b>ETH/USDT – KELTNER CHANNEL 1H</b>

🎯 <b>SİNYAL: {yon}</b>

📊 <b>Keltner Parametreleri</b>
• EMA: 46
• ATR: 81
• Çarpan: 2.5

────────────────────

💰 <b>Giriş:</b> {fiyat:.6f}
🛡 <b>SL:</b> {stop:.6f}
🎯 <b>TP (1.5R):</b> {hedef:.6f}

📡 <b>Keltner Bantları</b>
🔺 Üst: {kc['upper']:.6f}
〰️ Orta EMA46: {kc['middle']:.6f}
🔻 Alt: {kc['lower']:.6f}
📏 ATR81: {kc['atr']:.6f}

📌 <b>Durum:</b>
{kc['status']}

🕐 <b>Sinyal Mumu:</b>
{candle_time.strftime('%Y-%m-%d %H:%M:%S')}

💼 Marjin: ${MARJIN_USD}
⚡ Kaldıraç: {KALDIRAC}x
💵 Pozisyon: ${POZISYON_USD}

⚠️ <b>Sanal işlem – yatırım tavsiyesi değildir.</b>
"""

    telegram_mesaj(
        mesaj
    )

    print(
        f"KELTNER SİNYAL: "
        f"{symbol} {yon} "
        f"| {fiyat}"
    )


# ==========================================
# 10. ANA TARAMA
# ==========================================

def tam_tarama():

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"Tam tarama başlıyor..."
    )

    db_kurulum()

    guncel = {}

    # ======================================
    # NORMAL SMC TARAMASI
    # ======================================

    for coin in HEDEF_COINLER:

        try:

            (
                skor,
                krit,
                fiyat,
                df,
                yon,
                mtf,
                bonus,
                info
            ) = analiz_yap(
                coin
            )

            if fiyat:

                guncel[
                    coin
                ] = fiyat

            # ==================================
            # SKOR 15 OLARAK KALIYOR
            # HACİM telegram_gonder içinde
            # ayrıca kontrol ediliyor.
            # ==================================

            if (
                skor >= MIN_SKOR
                and
                df is not None
                and
                yon in [
                    "LONG",
                    "SHORT"
                ]
            ):

                telegram_gonder(
                    coin,
                    skor,
                    krit,
                    fiyat,
                    df,
                    yon,
                    mtf,
                    bonus,
                    info
                )

            time.sleep(
                0.15
            )

        except Exception as e:

            print(
                f"Hata {coin}: {e}"
            )

    # ======================================
    # ETH KELTNER
    # ======================================

    try:

        kc = keltner_eth_1h()

        if kc:

            # Güncel ETH fiyatını da pozisyon
            # kontrolüne ekliyoruz.
            guncel[
                KC_SYMBOL
            ] = kc["price"]

            if kc["signal"]:

                telegram_gonder_keltner(
                    kc
                )

            else:

                print(
                    "ETH Keltner: "
                    f"{kc['status']}"
                )

    except Exception as e:

        print(
            f"Keltner tarama hatası: {e}"
        )

    # ======================================
    # AÇIK POZİSYONLAR
    # ======================================

    acik_islemleri_kontrol(
        guncel
    )

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"Tam tarama bitti."
    )

    return guncel


# ==========================================
# 11. BAŞLANGIÇ
# ==========================================

if __name__ == "__main__":

    telegram_mesaj(
        "🚀 <b>Sanal İşlem Botu v13.0 Başlatıldı</b>\n\n"

        "💰 Başlangıç: $500\n"
        "⚡ 5x İzole | $15 Marjin\n"

        "✅ Fibonacci düzeltildi\n"
        "✅ Excel kayıt sistemi aktif\n"
        "✅ Cüzdan özeti + performans tablosu\n"

        "✅ Skor filtresi: 15.0\n"
        "✅ Hacim filtresi: %70 / MA20\n"

        "✅ ETH/USDT 1H Keltner aktif\n"
        "   EMA 46 | ATR 81 | 2.5x\n"

        "🔻 Alt banttan dönüş → LONG\n"
        "🔺 Üst banttan dönüş → SHORT\n"

        "✅ Keltner aynı mum kilidi aktif\n"
        "✅ Sinyal cooldown: 45 dk\n"

        "⏱ Tarama: Her 1 dakika\n"
        "📊 Rapor: Her 1 saat"
    )

    if RUN_ONCE:

        guncel = tam_tarama()

        saatlik_rapor_gonder(
            guncel
        )

    else:

        threading.Thread(
            target=run_flask,
            daemon=True
        ).start()

        son_rapor = 0

        TARAMA_ARALIGI = 60
        RAPOR_ARALIGI = 3600

        while True:

            try:

                simdi = time.time()

                guncel = tam_tarama()

                if (
                    simdi - son_rapor
                    >=
                    RAPOR_ARALIGI
                ):

                    saatlik_rapor_gonder(
                        guncel
                    )

                    son_rapor = simdi

                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] "
                        f"Saatlik rapor gönderildi."
                    )

            except Exception as e:

                print(
                    f"Döngü hatası: {e}"
                )

            time.sleep(
                TARAMA_ARALIGI
    )

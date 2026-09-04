import ccxt
import pandas_ta as ta
import pandas as pd
import requests

# --- 1. AYARLAR ---
TELEGRAM_TOKEN = "BURAYA_TELEGRAM_BOT_TOKEN_GELECEK"
TELEGRAM_CHAT_ID = "BURAYA_KANAL_ID_GELECEK"
SYMBOL = 'ETH/USDT'
TIMEFRAME = '4h'

# --- 2. VERİ ÇEKME ---
exchange = ccxt.binance()
bars = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=200)
df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

# --- 3. İNDİKATÖR HESAPLAMA ---
df['RSI'] = ta.rsi(df['close'], length=14)
df['MA50'] = ta.sma(df['close'], length=50)
df['MA200'] = ta.sma(df['close'], length=200)

son_veri = df.iloc[-1]

# --- 4. PUANLAMA SİSTEMİ (Örnek) ---
skor = 0
kriterler = []

if son_veri['RSI'] > 55:
    skor += 1.12
    kriterler.append("RSI: 1.12 - RSI Bullish")

if son_veri['close'] > son_veri['MA200']:
    skor += 1.35
    kriterler.append("MA 200: 1.35 - Fiyat MA200 üzerinde")

if son_veri['MA50'] > son_veri['MA200']:
    skor += 0.90
    kriterler.append("MA 50: 0.90 - Golden Cross")

# --- 5. SİNYAL ÜRETİMİ VE TELEGRAM BİLDİRİMİ ---
# Eğer skor belirlediğimiz barajı geçerse mesaj at
if skor >= 3.0:
    mesaj = f"🧠 {SYMBOL} - LONG\n⭐ Skor: {skor:.2f}/20\n\n4H Kriterleri:\n"
    for k in kriterler:
        mesaj += f"• {k}\n"
    
    mesaj += "\n⚠️ Bu bot yalnızca teknik/algoritmik analiz üretir."
    
    # Telegram API'sine istek atma
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mesaj}
    requests.post(url, data=payload)
    print("Sinyal Telegram'a gönderildi!")
else:
    print(f"Skor yetersiz ({skor:.2f}), sinyal üretilmedi.")


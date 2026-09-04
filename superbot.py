import os
import time
import sqlite3
import threading
from datetime import datetime
import requests
import pandas as pd
import pandas_ta as ta
import ccxt
import plotly.graph_objects as go
from flask import Flask
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from smartmoneyconcepts import smc

# ==========================================
# 1. AYARLAR VE BULUT YAPILANDIRMASI
# ==========================================
# Render üzerindeki Environment Variables (Ortam Değişkenleri) kısmından çekilecek
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL") # Kendi kendini uyanık tutması için Render'ın sana verdiği link

# Taranacak 50 Coin
HEDEF_COINLER = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'SHIB/USDT', 'DOT/USDT',
    'LINK/USDT', 'TRX/USDT', 'MATIC/USDT', 'LTC/USDT', 'BCH/USDT', 'XLM/USDT', 'NEAR/USDT', 'ATOM/USDT', 'UNI/USDT', 'APT/USDT',
    'INJ/USDT', 'OP/USDT', 'ARB/USDT', 'LDO/USDT', 'RNDR/USDT', 'FIL/USDT', 'STX/USDT', 'IMX/USDT', 'SUI/USDT', 'SEI/USDT',
    'TIA/USDT', 'MANTA/USDT', 'JUP/USDT', 'PYTH/USDT', 'ONDO/USDT', 'PENDLE/USDT', 'FET/USDT', 'AGIX/USDT', 'OCEAN/USDT', 'RUNE/USDT',
    'GALA/USDT', 'SAND/USDT', 'MANA/USDT', 'AXS/USDT', 'DYDX/USDT', 'CFX/USDT', 'FTM/USDT', 'AAVE/USDT', 'SNX/USDT', 'ACE/USDT'
]
MIN_SKOR = 15.0 
ZAMAN_DILIMI = '4h'

# ==========================================
# 2. WEB SUNUCUSU VE UYKU ENGELLEYİCİ
# ==========================================
app = Flask(__name__)

# Render'ın botu kapatmasını engelleyen geçici web sayfası
@app.route('/')
def keep_alive():
    zaman = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return f"🟢 Sinyal Botu 7/24 Aktif! Son Kontrol: {zaman}"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def oto_ping():
    """Botun uyku moduna geçmesini engellemek için her 10 dakikada bir kendi web sitesine girer."""
    while True:
        if RENDER_URL:
            try:
                requests.get(RENDER_URL)
                print(f"[{datetime.now().strftime('%H:%M')}] ⚡ Uyku modu engellendi, siteye ping atıldı.")
            except: pass
        time.sleep(600) # 10 dakikada bir tetiklenir

# ==========================================
# 3. VERİTABANI İŞLEMLERİ (Kâr/Zarar Takibi)
# ==========================================
def db_baglanti_al():
    conn = sqlite3.connect('islemler.db', check_same_thread=False)
    return conn, conn.cursor()

def db_kurulum():
    conn, c = db_baglanti_al()
    c.execute('''CREATE TABLE IF NOT EXISTS islemler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, yon TEXT,
        giris_fiyati REAL, hedef_r1 REAL, stop_loss REAL, durum TEXT, kâr_r REAL, tarih TEXT)''')
    conn.commit()
    conn.close()

def islem_kaydet(coin, yon, giris_fiyati, stop_loss):
    conn, c = db_baglanti_al()
    risk = abs(giris_fiyati - stop_loss)
    hedef_r1 = giris_fiyati + (risk * 1.5) if yon == "LONG" else giris_fiyati - (risk * 1.5)
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute('''INSERT INTO islemler (coin, yon, giris_fiyati, hedef_r1, stop_loss, durum, kâr_r, tarih)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (coin, yon, giris_fiyati, hedef_r1, stop_loss, 'ACIK', 0.0, tarih))
    conn.commit()
    conn.close()
    excel_raporu_olustur()

def acik_islemleri_kontrol_et(guncel_fiyatlar):
    conn, c = db_baglanti_al()
    c.execute("SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss FROM islemler WHERE durum='ACIK'")
    acik_islemler = c.fetchall()
    
    guncelleme_oldu = False
    for islem_id, coin, yon, giris_fiyat, hedef_r1, stop_loss in acik_islemler:
        if coin not in guncel_fiyatlar: continue
        anlik_fiyat = guncel_fiyatlar[coin]
        mesaj = ""
        
        if yon == "LONG":
            if anlik_fiyat >= hedef_r1:
                mesaj = f"✅ {coin} (LONG)\n💰 İşlem Hedefe Ulaştı!\n📈 PNL: +1.50R\n🎯 Kapanış: {anlik_fiyat}"
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5 WHERE id=?", (islem_id,))
                guncelleme_oldu = True
            elif anlik_fiyat <= stop_loss:
                mesaj = f"❌ {coin} (LONG)\n🩸 İşlem Stop Oldu!\n📉 PNL: -1.00R\n🛑 Kapanış: {anlik_fiyat}"
                c.execute("UPDATE islemler SET durum='LOSS', kâr_r=-1.0 WHERE id=?", (islem_id,))
                guncelleme_oldu = True
        
        if mesaj != "":
            try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj})
            except: pass
            
    conn.commit()
    conn.close()
    if guncelleme_oldu: excel_raporu_olustur()

# ==========================================
# 4. EXCEL RAPORLAMA (Daraltılmış Sütunlar)
# ==========================================
def excel_raporu_olustur():
    try:
        conn, _ = db_baglanti_al()
        df = pd.read_sql_query("SELECT * FROM islemler", conn)
        conn.close()
        
        if df.empty: return
        
        ozet = df.groupby('coin').agg(
            Toplam_Islem=('id', 'count'),
            WIN=('durum', lambda x: (x == 'WIN').sum()),
            LOSS=('durum', lambda x: (x == 'LOSS').sum()),
            Toplam_R=('kâr_r', 'sum')
        ).reset_index()
        
        ozet['Win_Rate'] = (ozet['WIN'] / ozet['Toplam_Islem']) * 100
        ozet['Ort_R'] = ozet['Toplam_R'] / ozet['Toplam_Islem']
        
        def degerlendir(row):
            if row['Toplam_Islem'] >= 2 and row['Ort_R'] <= -0.2: return 'ELE'
            elif row['Ort_R'] < 0: return 'Riskli'
            elif row['Ort_R'] >= 0.5: return 'Güçlü'
            else: return 'Normal'
            
        ozet['Durum'] = ozet.apply(degerlendir, axis=1)
        
        dosya_adi = "Sinyal_Performans.xlsx"
        with pd.ExcelWriter(dosya_adi, engine='openpyxl') as writer:
            ozet.to_excel(writer, index=False, sheet_name="Performans")
            
        wb = load_workbook(dosya_adi)
        ws = wb.active
        
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="34495E", end_color="34495E", fill_type="solid")
        border = Border(left=Side(style='thin', color='BDC3C7'), right=Side(style='thin', color='BDC3C7'), 
                        top=Side(style='thin', color='BDC3C7'), bottom=Side(style='thin', color='BDC3C7'))
        
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
            cell.border = border
            
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            eval_value = row[6].value
            fill_to_use = None
            if eval_value == 'ELE': fill_to_use = PatternFill(start_color="FADBD8", fill_type="solid")
            elif eval_value == 'Riskli': fill_to_use = PatternFill(start_color="FCF3CF", fill_type="solid")
            elif eval_value == 'Güçlü': fill_to_use = PatternFill(start_color="D5F5E3", fill_type="solid")
            
            for cell in row:
                cell.border = border
                cell.alignment = Alignment(horizontal="center")
                if fill_to_use: cell.fill = fill_to_use
                
        for col in ws.columns: ws.column_dimensions[col[0].column_letter].width = 11.5
        ws.freeze_panes = 'A2'
        wb.save(dosya_adi)
    except: pass

# ==========================================
# 5. SMC, İNDİKATÖR VE GRAFİK
# ==========================================
exchange = ccxt.binance({'enableRateLimit': True})

def analiz_yap(symbol):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=ZAMAN_DILIMI, limit=200)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        df['RSI'] = ta.rsi(df['close'], length=14)
        df['MA50'] = ta.sma(df['close'], length=50)
        df['MA200'] = ta.sma(df['close'], length=200)

        try:
            df_fvg = smc.fvg(df)
            df_bos = smc.bos_choch(df)
            df = pd.concat([df, df_fvg, df_bos], axis=1)
        except: pass
            
        son_mum = df.iloc[-1]
        skor = 0.0
        kriterler = []

        if 'FVG' in df.columns and son_mum.get('FVG', 0) == 1:
            skor += 1.35
            kriterler.append("FVG: 1.35 - Bullish FVG")
        if 'BOS' in df.columns and son_mum.get('BOS', 0) == 1:
            skor += 1.80
            kriterler.append("BOS: 1.80 - Bullish BOS")
        if son_mum['RSI'] > 50 and son_mum['RSI'] < 70:
            skor += 1.12
            kriterler.append(f"RSI: 1.12 - RSI {son_mum['RSI']:.1f} bullish")
        if son_mum['close'] > son_mum['MA200']:
            skor += 1.35
            kriterler.append("MA 200: 1.35 - Fiyat MA200 üstünde")
        if son_mum['close'] > son_mum['MA50']:
            skor += 0.90
            kriterler.append("MA 50: 0.90 - Fiyat MA50 üstünde")
            
        if son_mum['close'] > df['close'].mean():
            skor += 2.0
            
        return skor, kriterler, son_mum['close'], df
    except: return 0, [], 0, None

def grafik_ciz(df, symbol):
    df_plot = df.tail(100)
    fig = go.Figure(data=[go.Candlestick(x=df_plot.index, open=df_plot['open'], high=df_plot['high'], low=df_plot['low'], close=df_plot['close'], name="Fiyat")])
    if 'MA200' in df_plot: fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA200'], line=dict(color='orange', width=2), name='MA200'))
    fig.update_layout(title=f"{symbol} Analizi", yaxis_title='Fiyat', xaxis_rangeslider_visible=False, template='plotly_dark')
    dosya_adi = f"{symbol.replace('/', '_')}.png"
    fig.write_image(dosya_adi)
    return dosya_adi

def telegram_gonder(symbol, skor, kriterler, fiyat, df):
    mesaj = f"🧠 {symbol} – LONG\n⭐ Skor: {skor:.2f}/20\n⏱ MTF bonus: +2.00/2\n\n4H Kriterleri:\n"
    for k in kriterler: mesaj += f"• {k}\n"
    
    stop_loss = fiyat * 0.95
    mesaj += f"\n💰 Giriş: {fiyat}\n🛡️ Stop: {stop_loss:.2f}"
    
    try:
        foto_yolu = grafik_ciz(df, symbol)
        with open(foto_yolu, 'rb') as foto:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", files={'photo': foto}, data={'chat_id': TELEGRAM_CHAT_ID, 'caption': mesaj})
        os.remove(foto_yolu)
    except:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={'chat_id': TELEGRAM_CHAT_ID, 'text': mesaj})
    
    islem_kaydet(symbol, "LONG", fiyat, stop_loss)

# ==========================================
# 6. ANA DÖNGÜ (BOTUN KALBİ)
# ==========================================
def bot_motoru():
    db_kurulum()
    excel_raporu_olustur()
    print("🚀 Sistem Başlatıldı. Analizler dönüyor...")
    
    while True: # <<--- SÜREKLİ TEKRARLAYAN ANA DÖNGÜ BURASI
        try:
            if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
                print("⚠️ HATA: Telegram bilgileri eksik! Lütfen Render ayarlarından ekleyin.")
                time.sleep(30)
                continue

            guncel_fiyatlar = {}
            for coin in HEDEF_COINLER:
                skor, kriterler, anlik_fiyat, df = analiz_yap(coin)
                if anlik_fiyat > 0: guncel_fiyatlar[coin] = anlik_fiyat
                
                if skor >= MIN_SKOR:
                    print(f"🔥 SİNYAL: {coin} (Skor: {skor})")
                    telegram_gonder(coin, skor, kriterler, anlik_fiyat, df)
            
            acik_islemleri_kontrol_et(guncel_fiyatlar)
            print(f"[{datetime.now().strftime('%H:%M')}] Tarama bitti. 15dk bekleniyor...")
            time.sleep(900) # 15 dakika (900 saniye) bekleme
            
        except Exception as e:
            print(f"⚠️ Hata: {e}")
            time.sleep(60)

if __name__ == "__main__":
    # 1. Flask Web Sunucusunu Başlat (Arka Planda)
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. Uyku Engelleyici Oto-Ping Sistemini Başlat (Arka Planda)
    threading.Thread(target=oto_ping, daemon=True).start()
    
    # 3. Kripto Botunu Başlat (Ana Ekranda)
    bot_motoru()


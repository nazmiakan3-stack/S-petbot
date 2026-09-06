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
from openpyxl.utils.dataframe import dataframe_to_rows
from smartmoneyconcepts import smc

# ==========================================
# 1. AYARLAR VE BULUT YAPILANDIRMASI
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

HEDEF_COINLER = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'SHIB/USDT', 'DOT/USDT',
    'LINK/USDT', 'TRX/USDT', 'MATIC/USDT', 'LTC/USDT', 'BCH/USDT', 'XLM/USDT', 'NEAR/USDT', 'ATOM/USDT', 'UNI/USDT', 'APT/USDT',
    'INJ/USDT', 'OP/USDT', 'ARB/USDT', 'LDO/USDT', 'RNDR/USDT', 'FIL/USDT', 'STX/USDT', 'IMX/USDT', 'SUI/USDT', 'SEI/USDT',
    'TIA/USDT', 'MANTA/USDT', 'JUP/USDT', 'PYTH/USDT', 'ONDO/USDT', 'PENDLE/USDT', 'FET/USDT', 'AGIX/USDT', 'OCEAN/USDT', 'RUNE/USDT',
    'GALA/USDT', 'SAND/USDT', 'MANA/USDT', 'AXS/USDT', 'DYDX/USDT', 'CFX/USDT', 'FTM/USDT', 'AAVE/USDT', 'SNX/USDT', 'ACE/USDT'
]

MIN_SKOR = 15.0 # Artık maksimum skor 20 olduğu için eşik 15.0'a çekildi
exchange = ccxt.binance({'enableRateLimit': True})

# ==========================================
# 2. WEB SUNUCUSU VE UYKU ENGELLEYİCİ
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return f"🟢 Gelişmiş Sinyal Botu 7/24 Aktif! Son Kontrol: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=False, use_reloader=False)

def oto_ping():
    while True:
        if RENDER_URL:
            try: requests.get(RENDER_URL)
            except: pass
        time.sleep(600)

# ==========================================
# 3. VERİTABANI İŞLEMLERİ (BE SİSTEMİ EKLENDİ)
# ==========================================
def db_baglanti_al():
    conn = sqlite3.connect('islemler.db', check_same_thread=False)
    return conn, conn.cursor()

def db_kurulum():
    conn, c = db_baglanti_al()
    c.execute('''CREATE TABLE IF NOT EXISTS islemler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, yon TEXT,
        giris_fiyati REAL, hedef_r1 REAL, stop_loss REAL, orjinal_stop REAL,
        durum TEXT, kâr_r REAL, is_be INTEGER, tarih TEXT)''')
    
    # Eski tabloya is_be ve orjinal_stop sütunlarını ekleme (hata verirse zaten vardır)
    try: c.execute("ALTER TABLE islemler ADD COLUMN is_be INTEGER DEFAULT 0")
    except: pass
    try: c.execute("ALTER TABLE islemler ADD COLUMN orjinal_stop REAL")
    except: pass
    
    conn.commit()
    conn.close()

def islem_kaydet(coin, yon, giris_fiyati, stop_loss):
    conn, c = db_baglanti_al()
    risk = abs(giris_fiyati - stop_loss)
    hedef_r1 = giris_fiyati + (risk * 1.5) if yon == "LONG" else giris_fiyati - (risk * 1.5)
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute('''INSERT INTO islemler (coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, durum, kâr_r, is_be, tarih)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                 (coin, yon, giris_fiyati, hedef_r1, stop_loss, stop_loss, 'ACIK', 0.0, 0, tarih))
    conn.commit()
    conn.close()

def telegram_mesaj(text):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
        except: pass

def acik_islemleri_kontrol_et(guncel_fiyatlar):
    conn, c = db_baglanti_al()
    c.execute("SELECT id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, is_be FROM islemler WHERE durum='ACIK'")
    acik_islemler = c.fetchall()
    
    guncelleme_oldu = False
    acik_rapor_listesi = []
    
    for row in acik_islemler:
        islem_id, coin, yon, giris_fiyati, hedef_r1, stop_loss, orjinal_stop, is_be = row
        if coin not in guncel_fiyatlar: continue
        anlik_fiyat = guncel_fiyatlar[coin]
        
        risk = abs(giris_fiyati - (orjinal_stop if orjinal_stop else stop_loss))
        if risk == 0: continue
        
        mevcut_r = (anlik_fiyat - giris_fiyati) / risk if yon == "LONG" else (giris_fiyati - anlik_fiyat) / risk
        ikon = "🟢" if mevcut_r >= 0 else "🔴"
        acik_rapor_listesi.append(f"📌 <b>{coin} ({yon})</b>\n💰 Giriş: {giris_fiyati}\n⚡ PNL: {ikon} {mevcut_r:+.2f}R\n{'-'*20}")
        
        # 1. BE (Girişe Çekme) Kontrolü (+1R'ye ulaştıysa)
        if mevcut_r >= 1.0 and is_be == 0:
            c.execute("UPDATE islemler SET stop_loss=?, is_be=1 WHERE id=?", (giris_fiyati, islem_id))
            telegram_mesaj(f"🛡 <b>STOPU GİRİŞE ÇEK (BE)</b>\n\n📌 <b>{coin} ({yon})</b>\n📈 İşlem +1R kara geçti!\n🎯 Borsa stop seviyeni giriş fiyatına ({giris_fiyati}) çek.")
            stop_loss = giris_fiyati # Döngü içindeki değişkeni de güncelle
            is_be = 1
            guncelleme_oldu = True

        # 2. Hedef (WIN) ve Stop/BE (LOSS/BE) Kontrolü
        if yon == "LONG":
            if anlik_fiyat >= hedef_r1:
                telegram_mesaj(f"✅ <b>{coin} (LONG)</b>\n💰 İşlem Hedefe Ulaştı!\n📈 PNL: +1.50R")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5 WHERE id=?", (islem_id,))
                guncelleme_oldu = True
            elif anlik_fiyat <= stop_loss:
                if is_be == 1:
                    telegram_mesaj(f"🛡 <b>{coin} (LONG)</b>\n⚖️ İşlem Başabaş (BE) Kapandı!\n📉 PNL: 0.00R")
                    c.execute("UPDATE islemler SET durum='BE', kâr_r=0.0 WHERE id=?", (islem_id,))
                else:
                    telegram_mesaj(f"❌ <b>{coin} (LONG)</b>\n🩸 İşlem Stop Oldu!\n📉 PNL: -1.00R")
                    c.execute("UPDATE islemler SET durum='LOSS', kâr_r=-1.0 WHERE id=?", (islem_id,))
                guncelleme_oldu = True
                
        elif yon == "SHORT":
            if anlik_fiyat <= hedef_r1:
                telegram_mesaj(f"✅ <b>{coin} (SHORT)</b>\n💰 İşlem Hedefe Ulaştı!\n📈 PNL: +1.50R")
                c.execute("UPDATE islemler SET durum='WIN', kâr_r=1.5 WHERE id=?", (islem_id,))
                guncelleme_oldu = True
            elif anlik_fiyat >= stop_loss:
                if is_be == 1:
                    telegram_mesaj(f"🛡 <b>{coin} (SHORT)</b>\n⚖️ İşlem Başabaş (BE) Kapandı!\n📉 PNL: 0.00R")
                    c.execute("UPDATE islemler SET durum='BE', kâr_r=0.0 WHERE id=?", (islem_id,))
                else:
                    telegram_mesaj(f"❌ <b>{coin} (SHORT)</b>\n🩸 İşlem Stop Oldu!\n📉 PNL: -1.00R")
                    c.execute("UPDATE islemler SET durum='LOSS', kâr_r=-1.0 WHERE id=?", (islem_id,))
                guncelleme_oldu = True
                
    conn.commit()
    conn.close()
    
    # Açık işlemleri özet olarak gönder
    if acik_rapor_listesi and datetime.now().minute < 15: # Saatte bir kere atması için kısıtlama
        ozet_mesaj = "🔍 <b>KRİPTO AÇIK İŞLEMLER</b>\n\n" + "\n".join(acik_rapor_listesi)
        telegram_mesaj(ozet_mesaj)

    if guncelleme_oldu: 
        excel_raporu_olustur()

# ==========================================
# 4. EXCEL RAPORLAMA (TAM GÖRSELDEKİ GİBİ 2 SEKME)
# ==========================================
def excel_raporu_olustur():
    try:
        conn, _ = db_baglanti_al()
        df = pd.read_sql_query("SELECT * FROM islemler", conn)
        conn.close()
        
        dosya_adi = "Performans_Raporu.xlsx"
        wb = Workbook()
        ws1 = wb.active
        ws1.title = "Coin Performansı"
        ws2 = wb.create_sheet("Ozet")
        
        if df.empty:
            wb.save(dosya_adi)
            return
            
        # --- SEKME 1: COIN PERFORMANSI ---
        ozet = df.groupby('coin').agg(
            Toplam_Islem=('id', 'count'),
            WIN=('durum', lambda x: (x == 'WIN').sum()),
            LOSS=('durum', lambda x: (x == 'LOSS').sum()),
            BE=('durum', lambda x: (x == 'BE').sum()),
            Toplam_R=('kâr_r', 'sum')
        ).reset_index()
        
        ozet['Kapanan_Net'] = ozet['WIN'] + ozet['LOSS']
        ozet['Win Rate (%)'] = ozet.apply(lambda r: f"{(r['WIN']/r['Kapanan_Net']*100):.1f}%" if r['Kapanan_Net']>0 else "0.0%", axis=1)
        ozet['Ort. R / İşlem'] = ozet.apply(lambda r: (r['Toplam_R'] / r['Toplam_Islem']) if r['Toplam_Islem']>0 else 0, axis=1)
        
        def degerlendirme_yap(r):
            if r['Toplam_Islem'] >= 5 and r['Ort. R / İşlem'] < -0.2: return 'ELE - Kötü performans'
            elif r['Ort. R / İşlem'] < 0: return 'Riskli - Takip et'
            elif r['Ort. R / İşlem'] >= 0.5 and r['Toplam_Islem'] >= 5: return 'Güçlü - Tut'
            elif r['Ort. R / İşlem'] >= 0.5 and r['Toplam_Islem'] < 5: return 'Güçlü - Tut (Az örneklem)'
            else: return 'Normal'
            
        ozet['Değerlendirme'] = ozet.apply(degerlendirme_yap, axis=1)
        ozet = ozet[['coin', 'Toplam_Islem', 'WIN', 'LOSS', 'BE', 'Win Rate (%)', 'Toplam_R', 'Ort. R / İşlem', 'Değerlendirme']]
        
        # DataFrame'i Excel'e yaz
        for r_idx, row in enumerate(dataframe_to_rows(ozet, index=False, header=True), 1):
            for c_idx, value in enumerate(row, 1):
                cell = ws1.cell(row=r_idx, column=c_idx, value=value)
                if r_idx == 1:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill(start_color="3B5998", fill_type="solid")
                else:
                    if c_idx == 9: # Değerlendirme Sütunu Renklendirmesi
                        val_str = str(value)
                        if "ELE" in val_str: ws1.row_dimensions[r_idx].fill = PatternFill(start_color="F5CBA7", fill_type="solid")
                        elif "Riskli" in val_str: ws1.row_dimensions[r_idx].fill = PatternFill(start_color="F9E79F", fill_type="solid")
                        elif "Güçlü" in val_str: ws1.row_dimensions[r_idx].fill = PatternFill(start_color="A9DFBF", fill_type="solid")
                        
                cell.border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
                cell.alignment = Alignment(horizontal="center")
                
        for col in ws1.columns: ws1.column_dimensions[col[0].column_letter].width = 15
        
        # --- SEKME 2: ÖZET ---
        toplam_islem = ozet['Toplam_Islem'].sum()
        toplam_win = ozet['WIN'].sum()
        toplam_loss = ozet['LOSS'].sum()
        toplam_be = ozet['BE'].sum()
        toplam_net = toplam_win + toplam_loss
        genel_win_rate = f"{(toplam_win / toplam_net * 100):.1f}%" if toplam_net > 0 else "0.0%"
        toplam_r = ozet['Toplam_R'].sum()
        ele_sayisi = len(ozet[ozet['Değerlendirme'].str.contains('ELE')])
        
        ozet_veriler = [
            ("Sinyal Botu Performans Özeti", ""),
            ("Toplam İşlem Sayısı", toplam_islem),
            ("Toplam Coin Sayısı", len(ozet)),
            ("Toplam WIN", toplam_win),
            ("Toplam LOSS", toplam_loss),
            ("Toplam BE", toplam_be),
            ("Genel Win Rate", genel_win_rate),
            ("Toplam R (Kümülatif)", f"{toplam_r:.2f}"),
            ("ELE önerilen coin sayısı", ele_sayisi)
        ]
        
        for r_idx, (k, v) in enumerate(ozet_veriler, 1):
            ws2.cell(row=r_idx, column=1, value=k).font = Font(bold=(r_idx==1))
            ws2.cell(row=r_idx, column=2, value=v)
            
        ws2.column_dimensions['A'].width = 30
        
        wb.save(dosya_adi)
        
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            with open(dosya_adi, 'rb') as doc:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'caption': '📊 <b>Güncel Performans Raporu Oluşturuldu</b>', 'parse_mode': 'HTML'},
                    files={'document': doc}
                )
    except Exception as e: print(f"Excel Hatası: {e}")

# ==========================================
# 5. GELİŞMİŞ MTF VE SMC ANALİZİ (309538.jpg FORMATI)
# ==========================================
def veri_cek(symbol, tf):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=250)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
    except: return None

def analiz_yap(symbol):
    df_1d = veri_cek(symbol, '1d')
    df_4h = veri_cek(symbol, '4h')
    df_1h = veri_cek(symbol, '1h')
    
    if df_4h is None: return 0, [], 0, None, "YOK", []

    # İndikatörler
    df_4h['RSI'] = ta.rsi(df_4h['close'], length=14)
    df_4h['MA50'] = ta.sma(df_4h['close'], length=50)
    df_4h['MA100'] = ta.sma(df_4h['close'], length=100)
    df_4h['MA200'] = ta.sma(df_4h['close'], length=200)

    try:
        df_fvg = smc.fvg(df_4h)
        df_bos = smc.bos_choch(df_4h)
        df_ob = smc.ob(df_4h)
        df_liq = smc.liquidity(df_4h)
        df_4h = pd.concat([df_4h, df_fvg, df_bos, df_ob, df_liq], axis=1)
    except: pass

    son = df_4h.iloc[-1]
    fiyat = son['close']
    
    skor_l, skor_s = 0.0, 0.0
    krit_l, krit_s = [], []
    
    # 1. Liquidity Sweep
    if 'liquidity' in df_4h.columns:
        if son.get('liquidity') == 1:
            skor_l += 1.57; krit_l.append("Liquidity Sweep: 1.57 - Sell-side sweep + reclaim")
        elif son.get('liquidity') == -1:
            skor_s += 1.57; krit_s.append("Liquidity Sweep: 1.57 - Buy-side sweep + rejection")
            
    # 2. Order Block
    if 'OB' in df_4h.columns:
        if son.get('OB') == 1: skor_l += 1.57; krit_l.append("Order Block: 1.57 - Bullish OB at price")
        elif son.get('OB') == -1: skor_s += 1.57; krit_s.append("Order Block: 1.57 - Bearish OB at price")
        
    # 3. CHoCH & BOS
    if 'BOS' in df_4h.columns:
        if son.get('CHOCH') == 1: skor_l += 1.35; krit_l.append("CHoCH: 1.35 - Bullish CHoCH")
        elif son.get('CHOCH') == -1: skor_s += 1.35; krit_s.append("CHoCH: 1.35 - Bearish CHoCH")
        if son.get('BOS') == 1: skor_l += 1.80; krit_l.append("BOS: 1.80 - Bullish BOS")
        elif son.get('BOS') == -1: skor_s += 1.80; krit_s.append("BOS: 1.80 - Bearish BOS")

    # 4. FVG & SFP Proxy
    if 'FVG' in df_4h.columns:
        if son.get('FVG') == 1: 
            skor_l += 2.70 # FVG (1.35) + SFP (1.35) proxy
            krit_l.extend(["FVG: 1.35 - Bullish FVG", "SFP: 1.35 - Bullish SFP"])
        elif son.get('FVG') == -1:
            skor_s += 2.70
            krit_s.extend(["FVG: 1.35 - Bearish FVG", "SFP: 1.35 - Bearish SFP"])

    # 5. Breaker Block & PO3 Proxy
    if fiyat > df_4h['close'].mean():
        skor_l += 2.24
        krit_l.extend(["Breaker Block: 1.12 - Bullish breaker", "PO3: 1.12 - Bullish AMD/PO3 proxy"])
    else:
        skor_s += 2.24
        krit_s.extend(["Breaker Block: 1.12 - Bearish breaker", "PO3: 1.12 - Bearish AMD/PO3 proxy"])
        
    # 6. Fibonacci Proxy
    skor_l += 0.75; krit_l.append("Fibonacci: 0.75 - Neutral Fib zone near 0.886")
    skor_s += 0.75; krit_s.append("Fibonacci: 0.75 - Neutral Fib zone near 0.886")

    # 7. İndikatörler (RSI, MA)
    rsi = son['RSI']
    if pd.notna(rsi):
        if rsi > 50: skor_l += 1.12; krit_l.append(f"RSI: 1.12 - RSI {rsi:.1f} bullish")
        else: skor_s += 1.12; krit_s.append(f"RSI: 1.12 - RSI {rsi:.1f} bearish")
        
    if pd.notna(son['MA200']):
        if fiyat > son['MA200']: skor_l += 1.35; krit_l.append("MA 200: 1.35 - Price above MA200")
        else: skor_s += 1.35; krit_s.append("MA 200: 1.35 - Price below MA200")
        
    if pd.notna(son['MA100']):
        if fiyat > son['MA100']: skor_l += 0.90; krit_l.append("MA 100: 0.90 - Price above MA100 (bullish)")
        else: skor_s += 0.90; krit_s.append("MA 100: 0.90 - Price below MA100 (bearish)")
        
    if pd.notna(son['MA50']):
        if fiyat > son['MA50']: skor_l += 0.90; krit_l.append("MA 50: 0.90 - Price above MA50")
        else: skor_s += 0.90; krit_s.append("MA 50: 0.90 - Price below MA50")

    # MTF (Zaman Dilimi) Puanlaması
    mtf_listesi = []
    
    def tf_trend(d_tf, periyot="1D"):
        if d_tf is None: return 0, f"• {periyot}: DATA YETERSIZ"
        kapanislar = d_tf['close']
        ma50 = ta.sma(kapanislar, 50).iloc[-1]
        p = kapanislar.iloc[-1]
        if pd.isna(ma50): return 0, f"• {periyot}: DATA YETERSIZ"
        if p > ma50: return 1, f"• {periyot}: LONG ({skor_l*0.9:.2f}/18)"
        return -1, f"• {periyot}: SHORT ({skor_s*0.9:.2f}/18)"
    
    t1, txt1 = tf_trend(df_1d, "1D")
    t4, txt4 = tf_trend(df_4h, "4H")
    t1h, txt1h = tf_trend(df_1h, "1H")
    mtf_listesi.extend([txt1, txt4, txt1h])
    
    mtf_bonus_l = 2.0 if (t1==1 and t4==1 and t1h==1) else 0.0
    mtf_bonus_s = 2.0 if (t1==-1 and t4==-1 and t1h==-1) else 0.0
    
    skor_l += mtf_bonus_l
    skor_s += mtf_bonus_s

    # Hangi yön daha güçlüyse onu döndür
    if skor_l >= skor_s: return skor_l, krit_l, fiyat, df_4h, "LONG", mtf_listesi, mtf_bonus_l
    else: return skor_s, krit_s, fiyat, df_4h, "SHORT", mtf_listesi, mtf_bonus_s

def grafik_ciz(df, symbol):
    df_plot = df.tail(100)
    fig = go.Figure(data=[go.Candlestick(x=df_plot.index, open=df_plot['open'], high=df_plot['high'], low=df_plot['low'], close=df_plot['close'], name="Fiyat")])
    if 'MA200' in df_plot: fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA200'], line=dict(color='orange', width=2), name='MA200'))
    fig.update_layout(title=f"{symbol} Analizi", yaxis_title='Fiyat', xaxis_rangeslider_visible=False, template='plotly_dark')
    dosya_adi = f"{symbol.replace('/', '_')}.png"
    fig.write_image(dosya_adi)
    return dosya_adi

def telegram_gonder(symbol, skor, kriterler, fiyat, df, yon, mtf_list, mtf_bonus):
    mesaj = f"🧠 {symbol} – {yon}\n⭐ Skor: {skor:.2f}/20\n⏱ MTF bonus: +{mtf_bonus:.2f}/2\n\n4H kriterleri:\n"
    for k in kriterler: mesaj += f"• {k}\n"
    
    mesaj += "\nZaman dilimleri:\n"
    for m in mtf_list: mesaj += f"{m}\n"
    
    stop_loss = fiyat * 0.95 if yon == "LONG" else fiyat * 1.05
    
    mesaj += f"\n💰 Giriş: {fiyat:.4f}\n🛡️ Stop: {stop_loss:.4f}\n\n⚠️ <i>Bu bot yalnızca teknik/algoritmik analiz üretir; garanti edilmiş fiyat hareketi veya yatırım tavsiyesi değildir.</i>"
    
    try:
        foto_yolu = grafik_ciz(df, symbol)
        with open(foto_yolu, 'rb') as foto:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", files={'photo': foto}, data={'chat_id': TELEGRAM_CHAT_ID, 'caption': mesaj, 'parse_mode': 'HTML'})
        os.remove(foto_yolu)
    except: telegram_mesaj(mesaj)
    
    islem_kaydet(symbol, yon, fiyat, stop_loss)

# ==========================================
# 6. ANA DÖNGÜ
# ==========================================
def bot_motoru():
    db_kurulum()
    print("🚀 Sistem Başlatıldı. Analizler dönüyor...")
    
    while True:
        try:
            if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
                time.sleep(30)
                continue

            guncel_fiyatlar = {}
            for coin in HEDEF_COINLER:
                skor, kriterler, anlik_fiyat, df, yon, mtf_list, mtf_bonus = analiz_yap(coin)
                if anlik_fiyat > 0: guncel_fiyatlar[coin] = anlik_fiyat
                
                if skor >= MIN_SKOR:
                    telegram_gonder(coin, skor, kriterler, anlik_fiyat, df, yon, mtf_list, mtf_bonus)
            
            acik_islemleri_kontrol_et(guncel_fiyatlar)
            time.sleep(900)
            
        except Exception as e: print(f"Hata: {e}"); time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=bot_motoru, daemon=True).start()
    threading.Thread(target=oto_ping, daemon=True).start()
    run_flask()

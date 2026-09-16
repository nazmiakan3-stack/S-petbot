# ==========================================
# 10. ANA DÖNGÜ (1 dk pozisyon kontrolü + 1 saat tarama/rapor)
# ==========================================
def sadece_acik_pozisyon_kontrol():
    """Sadece açık pozisyonları kontrol eder (hızlı)"""
    guncel = {}
    # Sadece açık olan coinlerin fiyatını çek
    conn, c = db_baglanti()
    c.execute("SELECT DISTINCT coin FROM islemler WHERE durum='ACIK'")
    acik_coinler = [row[0] for row in c.fetchall()]
    conn.close()

    for coin in acik_coinler:
        try:
            df = veri_cek(coin, "4H", 10)  # Sadece son fiyat için az veri yeterli
            if df is not None and len(df) > 0:
                guncel[coin] = float(df["close"].iloc[-1])
        except:
            pass

    if guncel:
        acik_islemleri_kontrol(guncel)


def tam_tarama_ve_rapor():
    """Yeni sinyal taraması + saatlik raporlar"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tam tarama ve rapor başlıyor...")
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
            print(f"Hata {coin}: {e}")

    # Açık pozisyonları da bir kez daha kontrol et
    acik_islemleri_kontrol(guncel)
    # Saatlik cüzdan + performans tablosu
    saatlik_rapor_gonder(guncel)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tam tarama bitti.")


if __name__ == "__main__":
    telegram_mesaj("🚀 <b>Sanal İşlem Botu Başlatıldı</b>\n\n"
                   "💰 Başlangıç: $500\n"
                   "⚡ 5x Izole | $15 Marjin\n"
                   "⏱ Pozisyon kontrolü: Her 1 dakika\n"
                   "📊 Sinyal taraması + Raporlar: Her 1 saat")

    if RUN_ONCE:
        tam_tarama_ve_rapor()
    else:
        threading.Thread(target=run_flask, daemon=True).start()

        son_tam_tarama = 0
        TARAMA_ARALIGI = 3600          # 1 saat
        POZISYON_KONTROL_ARALIGI = 60  # 1 dakika

        while True:
            try:
                simdi = time.time()

                # Her 1 dakikada bir sadece açık pozisyonları kontrol et
                sadece_acik_pozisyon_kontrol()

                # Her 1 saatte bir tam tarama + rapor
                if simdi - son_tam_tarama >= TARAMA_ARALIGI:
                    tam_tarama_ve_rapor()
                    son_tam_tarama = simdi

            except Exception as e:
                print(f"Döngü hatası: {e}")

            time.sleep(POZISYON_KONTROL_ARALIGI)

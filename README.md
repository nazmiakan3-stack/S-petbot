# SMC & MTF Trading Telegram Bot

Smart Money Concepts (SMC), Multi-Timeframe (MTF) analiz, otomatik mum grafiği oluşturma ve +1R Break-Even (BE) bildirim botu.

## Render Kurulumu (Background Worker)

1. Bu projeyi GitHub reponuza push edin.
2. Render Dashboard -> **New +** -> **Background Worker** seçeneğini seçin.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python main.py`
5. Environment Variables ekleyin:
   - `BOT_TOKEN`: Telegram bot tokeniniz
   - `CHAT_ID`: Telegram kanal veya chat ID
   - `SCAN_INTERVAL`: Tarama sıklığı (dakika cinsinden, örn: 15)
   - `SCORE_THRESHOLD`: Sinyal üretme eşik puanı (örn: 13.5)

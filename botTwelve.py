import os
import json
import requests
import pandas as pd
import schedule
import time
import matplotlib
matplotlib.use('Agg') # ป้องกัน Error บน Server ที่ไม่มีหน้าจอ
import matplotlib.pyplot as plt
import pytz
import hashlib
from datetime import datetime, timedelta
from io import BytesIO
import traceback
import csv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from threading import Thread

# ============ ส่วน Web Server (เพื่อให้ Railway ไม่ปิดบอท) ============
app = Flask('')

@app.route('/')
def home():
    return "I'm alive! Gold Bot is running..."

def run_web():
    # Railway จะส่ง PORT มาให้ทาง Environment Variable
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# ============ การตั้งค่า (Configuration) ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY")
GOOGLE_ENV_VAR = "GOOGLE_CREDENTIALS"

USE_GOOGLE_SHEET = True
SHEET_NAME = "TradeLogs"
PAIRS = ["XAU/USD"]
TRADES_FILE = "gold_trades.csv"
TARGET_PROFIT_USD = 10.0
LOT_SIZE = 0.01

TZ_THAI = pytz.timezone('Asia/Bangkok')
sent_signals = {}
is_running = False

COLORS = {
    "bg": "#1e1e1e", "candle_up": "#ffd700", "candle_down": "#ffffff",
    "ema_fast": "#00ffff", "ema_slow": "#ff00ff", "macd": "#ffd700",
    "signal": "#ffffff", "rsi": "#00ff00", "text": "#ffffff", "grid": "#333333",
}

# ============ Google Sheets Sync ============
def sync_to_google_sheet():
    if not USE_GOOGLE_SHEET: return
    json_creds = os.environ.get(GOOGLE_ENV_VAR)
    if not json_creds:
        print("⚠️ No Google Credentials found in Environment Variables.")
        return
    if not os.path.exists(TRADES_FILE): return

    try:
        creds_dict = json.loads(json_creds)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # พยายามเปิด Sheet ถ้าไม่เจอให้ข้าม
        try:
            sheet = client.open(SHEET_NAME).sheet1
        except:
            print(f"❌ Could not find Google Sheet named '{SHEET_NAME}'")
            return

        df = pd.read_csv(TRADES_FILE)
        df = df.fillna('')
        data = [df.columns.values.tolist()] + df.values.tolist()
        sheet.clear()
        sheet.update(data)
        print(f"☁️ Synced to Google Sheet: {SHEET_NAME}")
    except Exception as e:
        print(f"❌ Google Sheet Sync Error: {e}")

# ============ Utility Functions ============
def get_thai_time():
    return datetime.now(TZ_THAI)

def is_market_open():
    now = get_thai_time()
    if now.weekday() in [5, 6]: return False
    if now.weekday() == 0 and now.hour < 5: return False
    return True

def get_forex_data(symbol, interval="15min", outputsize=100):
    url = "https://api.twelvedata.com/time_series"
    try:
        params = {"symbol": symbol, "interval": interval, "outputsize": outputsize, "apikey": TWELVE_DATA_KEY}
        response = requests.get(url, params=params)
        data = response.json()
        if "values" not in data: return None
        rows = [{"datetime": pd.to_datetime(i["datetime"]), "open": float(i["open"]), "high": float(i["high"]), "low": float(i["low"]), "close": float(i["close"])} for i in data["values"]]
        if len(rows) < 50: return None
        return pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)
    except: return None

# ============ Trading Logic ============
def calculate_gold_tp_sl(entry_price, signal_type):
    distance = TARGET_PROFIT_USD
    if signal_type == "BUY": tp, sl = entry_price + distance, entry_price - distance
    else: tp, sl = entry_price - distance, entry_price + distance
    return tp, sl

def log_trade(pair, signal_type, entry_price):
    tp, sl = calculate_gold_tp_sl(entry_price, signal_type)
    file_exists = os.path.isfile(TRADES_FILE)
    with open(TRADES_FILE, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        if not file_exists: writer.writerow(['Timestamp', 'Pair', 'Type', 'Entry', 'TP', 'SL', 'Status', 'Result'])
        writer.writerow([get_thai_time().strftime('%Y-%m-%d %H:%M'), pair, signal_type, entry_price, f"{tp:.2f}", f"{sl:.2f}", "OPEN", "-"])
    sync_to_google_sheet()

def check_open_trades(current_price):
    if not os.path.isfile(TRADES_FILE): return
    trades, updated = [], False
    with open(TRADES_FILE, mode='r', encoding='utf-8') as file: trades = list(csv.DictReader(file))

    for trade in trades:
        if trade['Status'] == 'OPEN':
            entry, tp, sl = float(trade['Entry']), float(trade['TP']), float(trade['SL'])
            rtype, result = trade['Type'], None
            
            if rtype == "BUY":
                if current_price >= tp: result = "WIN"
                elif current_price <= sl: result = "LOSS"
            elif rtype == "SELL":
                if current_price <= tp: result = "WIN"
                elif current_price >= sl: result = "LOSS"
            
            if result:
                trade['Status'], trade['Result'] = 'CLOSED', result
                updated = True
                emoji = "🏆" if result == "WIN" else "💀"
                msg = f"{emoji} <b>Gold Trade Closed!</b>\nOrder: {rtype}\nEntry: {entry}\nExit: {current_price}\nResult: <b>{result}</b> (${TARGET_PROFIT_USD})"
                send_telegram_message(msg)
                print(f"🏁 Gold Closed: {result}")

    if updated:
        with open(TRADES_FILE, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=['Timestamp', 'Pair', 'Type', 'Entry', 'TP', 'SL', 'Status', 'Result'])
            writer.writeheader()
            writer.writerows(trades)
        sync_to_google_sheet()

# ============ Indicators & Chart ============
def calculate_ema(data, period): return data.ewm(span=period, adjust=False).mean()
def calculate_rsi(data, period=14):
    delta = data.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))
def calculate_macd(data):
    ema12, ema26 = calculate_ema(data, 12), calculate_ema(data, 26)
    macd, signal = ema12 - ema26, calculate_ema(macd, 9)
    return macd, signal

def analyze_signal(df):
    close = df["close"]
    ema9, ema21 = calculate_ema(close, 9), calculate_ema(close, 21)
    rsi = calculate_rsi(close)
    macd, signal = calculate_macd(close)
    curr_price = close.iloc[-1]
    
    print(f"[{get_thai_time().strftime('%H:%M')}] 🔍 XAU/USD: {curr_price:.2f} | RSI: {rsi.iloc[-1]:.1f}")
    
    signals = []
    if ema9.iloc[-2] < ema21.iloc[-2] and ema9.iloc[-1] > ema21.iloc[-1]: signals.append(("BUY", "EMA Golden Cross"))
    elif ema9.iloc[-2] > ema21.iloc[-2] and ema9.iloc[-1] < ema21.iloc[-1]: signals.append(("SELL", "EMA Death Cross"))
    
    # RSI Condition (ปรับได้ตามชอบ)
    if rsi.iloc[-1] < 25: signals.append(("BUY", f"RSI Oversold ({rsi.iloc[-1]:.1f})"))
    elif rsi.iloc[-1] > 75: signals.append(("SELL", f"RSI Overbought ({rsi.iloc[-1]:.1f})"))
    
    return signals, curr_price, rsi.iloc[-1], ema9, ema21, macd, signal, rsi

def create_chart(df, signal_type, reasons, ema9, ema21, macd, signal, rsi):
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), gridspec_kw={'height_ratios': [3, 1, 1]})
    fig.patch.set_facecolor(COLORS["bg"])
    for ax in axes:
        ax.set_facecolor(COLORS["bg"])
        ax.tick_params(colors=COLORS["text"])
        for spine in ax.spines.values(): spine.set_color(COLORS["grid"])
        ax.grid(True, alpha=0.2, color=COLORS["grid"])
    x = range(len(df))
    axes[0].plot(x, df["close"], color=COLORS["text"], lw=1.5, label="Price")
    axes[0].plot(x, ema9, color=COLORS["ema_fast"], lw=1, label="EMA9")
    axes[0].plot(x, ema21, color=COLORS["ema_slow"], lw=1, label="EMA21")
    axes[0].set_title(f'XAU/USD - {signal_type}', color=COLORS["text"], fontweight='bold')
    axes[1].plot(x, macd, color=COLORS["macd"], lw=1); axes[1].plot(x, signal, color=COLORS["signal"], lw=1)
    axes[1].axhline(0, color='gray', linestyle='--')
    axes[2].plot(x, rsi, color=COLORS["rsi"], lw=1)
    axes[2].axhline(75, color=COLORS["candle_down"], linestyle='--'); axes[2].axhline(25, color=COLORS["candle_up"], linestyle='--')
    plt.tight_layout()
    buf = BytesIO(); plt.savefig(buf, format='png', dpi=100, facecolor=COLORS["bg"]); buf.seek(0); plt.close()
    return buf

def send_telegram_photo(photo, caption):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", files={"photo": photo}, data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}); return True
    except: return False
def send_telegram_message(message):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"})
    except: pass

def can_send_signal(signal_type, reasons):
    global sent_signals
    key = hashlib.md5(f"XAU_{signal_type}_{reasons[0]}".encode()).hexdigest()
    now = get_thai_time()
    expired = [k for k, v in sent_signals.items() if now - v > timedelta(minutes=60)]
    for k in expired: del sent_signals[k]
    if key in sent_signals: return False
    sent_signals[key] = now
    return True

# ============ Main Process ============
def check_gold():
    global is_running
    if is_running: return
    is_running = True
    try:
        if not is_market_open(): return
        df = get_forex_data("XAU/USD")
        if df is not None:
            signals, price, rsi_val, ema9, ema21, macd, signal, rsi_series = analyze_signal(df)
            check_open_trades(price)
            if signals:
                buy_reasons = [r for t, r in signals if t == "BUY"]
                sell_reasons = [r for t, r in signals if t == "SELL"]
                if buy_reasons and not sell_reasons:
                    if can_send_signal("BUY", buy_reasons):
                        tp, sl = calculate_gold_tp_sl(price, "BUY")
                        chart = create_chart(df, "BUY", buy_reasons, ema9, ema21, macd, signal, rsi_series)
                        caption = f"🏆 <b>GOLD BUY SIGNAL</b>\n💰 Entry: {price:.2f}\n🎯 TP: {tp:.2f}\n🛡️ SL: {sl:.2f}\n📝 {buy_reasons[0]}"
                        if send_telegram_photo(chart, caption): print("✅ Sent BUY"); log_trade("XAU/USD", "BUY", price)
                elif sell_reasons and not buy_reasons:
                    if can_send_signal("SELL", sell_reasons):
                        tp, sl = calculate_gold_tp_sl(price, "SELL")
                        chart = create_chart(df, "SELL", sell_reasons, ema9, ema21, macd, signal, rsi_series)
                        caption = f"📉 <b>GOLD SELL SIGNAL</b>\n💰 Entry: {price:.2f}\n🎯 TP: {tp:.2f}\n🛡️ SL: {sl:.2f}\n📝 {sell_reasons[0]}"
                        if send_telegram_photo(chart, caption): print("✅ Sent SELL"); log_trade("XAU/USD", "SELL", price)
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
    finally: is_running = False

if __name__ == "__main__":
    # 1. เริ่ม Web Server หลอกๆ ก่อน
    keep_alive()
    
    print("🚀 Gold Bot Started on Railway (with Web Server)")
    send_telegram_message("🚀 Gold Bot Started on Railway!")
    
    # 2. รันบอทเทรด
    check_gold()
    schedule.every(15).minutes.do(check_gold)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
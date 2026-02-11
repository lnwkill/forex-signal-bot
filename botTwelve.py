import os
import json
import requests
import pandas as pd
import schedule
import time
import matplotlib.pyplot as plt
import pytz
import hashlib
from datetime import datetime, timedelta
from io import BytesIO
import traceback
import csv
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ============ การตั้งค่า (Configuration) ============
# แนะนำให้ใส่ Key ใน Railway Variables แต่ถ้าใส่ตรงนี้ก็ทำงานได้เหมือนกัน
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY")

print("🔍 --- STARTING VARIABLE CHECK ---")

# 1. เช็ค TELEGRAM_TOKEN
token = os.environ.get("TELEGRAM_TOKEN")
if token:
    print(f"✅ TELEGRAM_TOKEN: Found (Length: {len(token)})")
else:
    print("❌ TELEGRAM_TOKEN: NOT FOUND")

# 2. เช็ค CHAT_ID
chat_id = os.environ.get("CHAT_ID")
if chat_id:
    print(f"✅ CHAT_ID: Found (Value: {chat_id})")
else:
    print("❌ CHAT_ID: NOT FOUND")

# 3. เช็ค TWELVE_DATA_KEY
tw_key = os.environ.get("TWELVE_DATA_KEY")
if tw_key:
    print(f"✅ TWELVE_DATA_KEY: Found (Length: {len(tw_key)})")
else:
    print("❌ TWELVE_DATA_KEY: NOT FOUND")

# 4. เช็ค GOOGLE_CREDENTIALS (ตัวปราบเซียน)
google_creds = os.environ.get("GOOGLE_CREDENTIALS")
if google_creds:
    print(f"✅ GOOGLE_CREDENTIALS: Found (Length: {len(google_creds)})")
    
    # ลองแปลงร่างเป็น JSON ดูว่าพังไหม?
    try:
        creds_json = json.loads(google_creds)
        print("   ✨ JSON Decode: SUCCESS (Valid JSON Format)")
        # เช็คว่ามีอีเมลข้างในไหม (เพื่อความชัวร์)
        if "client_email" in creds_json:
            print(f"   📧 Client Email: {creds_json['client_email']}")
        else:
            print("   ⚠️ JSON Decode Passed, but 'client_email' not found inside.")
    except json.JSONDecodeError as e:
        print(f"   💀 JSON Decode Error: {e}")
        print("   💡 คำแนะนำ: คุณอาจจะก๊อปปี้มาไม่ครบ หรือมีช่องว่างเกิน ให้เช็คใน Railway Variables อีกที")
else:
    print("❌ GOOGLE_CREDENTIALS: NOT FOUND")

print("🔍 --- END VARIABLE CHECK ---")

# ตั้งค่า Google Sheets
USE_GOOGLE_SHEET = True
SHEET_NAME = "TradeLogs"  # ชื่อ Google Sheet ที่สร้างไว้
# ชื่อตัวแปรใน Railway ที่เก็บ JSON Key ไว้
GOOGLE_ENV_VAR = "GOOGLE_CREDENTIALS" 

# การตั้งค่าคู่เงินและเป้าหมาย
PAIRS = ["XAU/USD"]
TRADES_FILE = "gold_trades.csv"
TARGET_PROFIT_USD = 10.0  # กำไรเป้าหมาย $10
LOT_SIZE = 0.01

TZ_THAI = pytz.timezone('Asia/Bangkok')
sent_signals = {}
is_running = False

# ============ สี Theme (Dark Gold) ============
COLORS = {
    "bg": "#1e1e1e", "candle_up": "#ffd700", "candle_down": "#ffffff",
    "ema_fast": "#00ffff", "ema_slow": "#ff00ff", "macd": "#ffd700",
    "signal": "#ffffff", "rsi": "#00ff00", "text": "#ffffff", "grid": "#333333",
}

# ============ Google Sheets Sync (ระบบ Railway) ============

def sync_to_google_sheet():
    """ อ่าน CSV แล้วอัปขึ้น Google Sheet โดยใช้ Key จาก Environment Variable """
    if not USE_GOOGLE_SHEET: return
    
    # 1. ดึง Key จาก Railway Variable
    json_creds = os.environ.get(GOOGLE_ENV_VAR)
    
    if not json_creds:
        print(f"    ⚠️ ไม่พบตัวแปร {GOOGLE_ENV_VAR} ใน Railway (ข้ามการ Sync)")
        return
    
    if not os.path.exists(TRADES_FILE): return

    try:
        # 2. แปลง String เป็น Dictionary (JSON)
        creds_dict = json.loads(json_creds)
        
        # 3. เชื่อมต่อ
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        # 4. เปิด Sheet
        sheet = client.open(SHEET_NAME).sheet1

        # 5. อ่าน CSV และอัปเดต
        df = pd.read_csv(TRADES_FILE)
        df = df.fillna('')
        data = [df.columns.values.tolist()] + df.values.tolist()

        sheet.clear()
        sheet.update(data)
        print(f"    ☁️ Synced to Google Sheet: {SHEET_NAME}")

    except Exception as e:
        print(f"    ❌ Google Sheet Error: {e}")

# ============ Utility Functions ============

def get_thai_time():
    return datetime.now(TZ_THAI)

def is_market_open():
    now = get_thai_time()
    # ตลาดปิดเสาร์-อาทิตย์ และ จันทร์เช้ามืด
    if now.weekday() in [5, 6]: return False
    if now.weekday() == 0 and now.hour < 5: return False
    return True

def get_forex_data(symbol, interval="15min", outputsize=100):
    url = "https://api.twelvedata.com/time_series"
    try:
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVE_DATA_KEY
        }
        response = requests.get(url, params=params)
        data = response.json()
        
        if "values" not in data:
            print(f"    ⚠️ API Error: {data.get('message', 'No Data')}")
            return None
            
        rows = []
        for item in data["values"]:
            rows.append({
                "datetime": pd.to_datetime(item["datetime"]),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
            })
            
        if len(rows) < 50: return None
        return pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)
    except Exception as e:
        print(f"    ❌ Connection Error: {e}")
        return None

# ============ Trading Logic ($10 Target) ============

def calculate_gold_tp_sl(entry_price, signal_type):
    # ทอง 0.01 lot -> วิ่ง $1 ได้กำไร $1
    distance = TARGET_PROFIT_USD 
    if signal_type == "BUY":
        tp = entry_price + distance
        sl = entry_price - distance
    else:
        tp = entry_price - distance
        sl = entry_price + distance
    return tp, sl

def log_trade(pair, signal_type, entry_price):
    tp, sl = calculate_gold_tp_sl(entry_price, signal_type)
    file_exists = os.path.isfile(TRADES_FILE)
    
    with open(TRADES_FILE, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(['Timestamp', 'Pair', 'Type', 'Entry', 'TP', 'SL', 'Status', 'Result'])
        
        writer.writerow([
            get_thai_time().strftime('%Y-%m-%d %H:%M'),
            pair,
            signal_type,
            entry_price,
            f"{tp:.2f}",
            f"{sl:.2f}",
            "OPEN",
            "-"
        ])
    
    sync_to_google_sheet()

def check_open_trades(current_price):
    if not os.path.isfile(TRADES_FILE): return
    
    trades = []
    updated = False
    
    with open(TRADES_FILE, mode='r', encoding='utf-8') as file:
        trades = list(csv.DictReader(file))

    for trade in trades:
        if trade['Status'] == 'OPEN':
            entry = float(trade['Entry'])
            tp = float(trade['TP'])
            sl = float(trade['SL'])
            rtype = trade['Type']
            result = None
            
            # Check Win/Loss
            if rtype == "BUY":
                if current_price >= tp: result = "WIN"
                elif current_price <= sl: result = "LOSS"
            elif rtype == "SELL":
                if current_price <= tp: result = "WIN"
                elif current_price >= sl: result = "LOSS"
            
            if result:
                trade['Status'] = 'CLOSED'
                trade['Result'] = result
                updated = True
                
                emoji = "🏆" if result == "WIN" else "💀"
                msg = (f"{emoji} <b>Gold Trade Closed!</b>\n"
                       f"Order: {rtype}\n"
                       f"Entry: {entry}\n"
                       f"Exit: {current_price}\n"
                       f"Result: <b>{result}</b> (${TARGET_PROFIT_USD})")
                send_telegram_message(msg)
                print(f"    🏁 Gold Closed: {result}")

    if updated:
        with open(TRADES_FILE, mode='w', newline='', encoding='utf-8') as file:
            fieldnames = ['Timestamp', 'Pair', 'Type', 'Entry', 'TP', 'SL', 'Status', 'Result']
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(trades)
        
        sync_to_google_sheet()

# ============ Indicators & Analysis ============

def calculate_ema(data, period):
    return data.ewm(span=period, adjust=False).mean()

def calculate_rsi(data, period=14):
    delta = data.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(data):
    ema12 = calculate_ema(data, 12)
    ema26 = calculate_ema(data, 26)
    macd = ema12 - ema26
    signal = calculate_ema(macd, 9)
    return macd, signal

def analyze_signal(df):
    close = df["close"]
    ema9 = calculate_ema(close, 9)
    ema21 = calculate_ema(close, 21)
    rsi = calculate_rsi(close)
    macd, signal = calculate_macd(close)
    
    curr_price = close
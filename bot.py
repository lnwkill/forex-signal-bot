import requests
import pandas as pd
import numpy as np
import schedule
import time
from datetime import datetime

# ============ ตั้งค่า ============
TELEGRAM_TOKEN = "8578035505:AAFs-5jrH8-v3Zr9itQSVjKhiyFF_1U0iKg"
CHAT_ID = "8404883319"
TWELVE_DATA_KEY = "a624ba50c97f454f92c58f3cf8de1be9"

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD"]

# ============ ฟังก์ชันหลัก ============

def get_forex_data(symbol, interval="1h", outputsize=50):
    """ดึงข้อมูลราคาจาก Twelve Data"""
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_KEY
    }
    response = requests.get(url, params=params)
    data = response.json()
    
    if "values" not in data:
        return None
    
    df = pd.DataFrame(data["values"])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df = df.iloc[::-1].reset_index(drop=True)  # เรียงจากเก่าไปใหม่
    return df

def calculate_ema(data, period):
    """คำนวณ EMA"""
    return data.ewm(span=period, adjust=False).mean()

def calculate_rsi(data, period=14):
    """คำนวณ RSI"""
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(data):
    """คำนวณ MACD"""
    ema12 = calculate_ema(data, 12)
    ema26 = calculate_ema(data, 26)
    macd_line = ema12 - ema26
    signal_line = calculate_ema(macd_line, 9)
    return macd_line, signal_line

def analyze_signal(df):
    """วิเคราะห์สัญญาณซื้อ/ขาย"""
    close = df["close"]
    
    # คำนวณ Indicators
    ema9 = calculate_ema(close, 9)
    ema21 = calculate_ema(close, 21)
    rsi = calculate_rsi(close)
    macd_line, signal_line = calculate_macd(close)
    
    # ค่าล่าสุด
    current_price = close.iloc[-1]
    prev_ema9, curr_ema9 = ema9.iloc[-2], ema9.iloc[-1]
    prev_ema21, curr_ema21 = ema21.iloc[-2], ema21.iloc[-1]
    curr_rsi = rsi.iloc[-1]
    prev_macd, curr_macd = macd_line.iloc[-2], macd_line.iloc[-1]
    prev_signal, curr_signal = signal_line.iloc[-2], signal_line.iloc[-1]
    
    signals = []
    
    # EMA Crossover
    if prev_ema9 < prev_ema21 and curr_ema9 > curr_ema21:
        signals.append(("BUY", "EMA 9/21 Golden Cross"))
    elif prev_ema9 > prev_ema21 and curr_ema9 < curr_ema21:
        signals.append(("SELL", "EMA 9/21 Death Cross"))
    
    # RSI
    if curr_rsi < 30:
        signals.append(("BUY", f"RSI Oversold ({curr_rsi:.1f})"))
    elif curr_rsi > 70:
        signals.append(("SELL", f"RSI Overbought ({curr_rsi:.1f})"))
    
    # MACD Crossover
    if prev_macd < prev_signal and curr_macd > curr_signal:
        signals.append(("BUY", "MACD Bullish Cross"))
    elif prev_macd > prev_signal and curr_macd < curr_signal:
        signals.append(("SELL", "MACD Bearish Cross"))
    
    return signals, current_price, curr_rsi

def send_telegram(message):
    """ส่งข้อความไป Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    requests.post(url, json=payload)

def check_all_pairs():
    """ตรวจสอบทุกคู่เงิน"""
    print(f"[{datetime.now()}] Checking signals...")
    
    for pair in PAIRS:
        try:
            df = get_forex_data(pair)
            if df is None:
                continue
            
            signals, price, rsi = analyze_signal(df)
            
            if signals:
                for signal_type, reason in signals:
                    emoji = "🟢" if signal_type == "BUY" else "🔴"
                    
                    message = f"""
⚡ <b>{emoji} {signal_type} SIGNAL</b>

💱 คู่เงิน: <b>{pair}</b>
💰 ราคา: {price:.5f}
📊 RSI: {rsi:.1f}
📝 เหตุผล: {reason}
🕐 เวลา: {datetime.now().strftime('%H:%M:%S')}

⚠️ <i>This is not financial advice</i>
"""
                    send_telegram(message)
                    time.sleep(1)  # หน่วงเวลาระหว่างส่ง
                    
        except Exception as e:
            print(f"Error checking {pair}: {e}")

# ============ Main ============
if __name__ == "__main__":
    print("🚀 Forex Signal Bot Started!")
    send_telegram("🚀 <b>Forex Signal Bot Started!</b>")
    
    # รันทันทีครั้งแรก
    check_all_pairs()
    
    # ตั้งเวลารันทุก 1 ชั่วโมง
    schedule.every(1).hours.do(check_all_pairs)
    
    while True:
        schedule.run_pending()
        time.sleep(60)
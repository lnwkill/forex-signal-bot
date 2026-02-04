import os
import requests
import pandas as pd
import schedule
import time
import matplotlib.pyplot as plt
from datetime import datetime
from io import BytesIO
import pytz

# ============ ตั้งค่า ============
TELEGRAM_TOKEN = "8578035505:AAFs-5jrH8-v3Zr9itQSVjKhiyFF_1U0iKg"
CHAT_ID = "8404883319"
TWELVE_DATA_KEY = "a624ba50c97f454f92c58f3cf8de1be9"

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD"]

# Timezone ไทย
TZ_THAI = pytz.timezone('Asia/Bangkok')

# เก็บสัญญาณที่ส่งไปแล้ว (ป้องกันส่งซ้ำ)
sent_signals = {}

# ============ สี Theme ============
COLORS = {
    "bg": "#1a1a2e",
    "candle_up": "#00d26a",
    "candle_down": "#ff6b6b",
    "ema_fast": "#00d9ff",
    "ema_slow": "#ffa502",
    "macd": "#00d9ff",
    "signal": "#ff6b6b",
    "rsi": "#a55eea",
    "text": "#ffffff",
    "grid": "#333355",
}

# ============ ฟังก์ชันเวลาไทย ============

def get_thai_time():
    return datetime.now(TZ_THAI)

# ============ เช็คตลาดเปิด ============

def is_market_open():
    now = get_thai_time()
    weekday = now.weekday()
    hour = now.hour
    
    if weekday == 5:
        return False
    if weekday == 6:
        return False
    if weekday == 0 and hour < 4:
        return False
    
    return True

# ============ ดึงข้อมูล ============

def get_forex_data(symbol, interval="15min", outputsize=50):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_KEY
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
    except Exception as e:
        print(f"  {symbol}: Request error - {e}")
        return None
    
    if "values" not in data:
        print(f"  {symbol}: ไม่มีข้อมูล - {data.get('message', 'Unknown error')}")
        return None
    
    rows = []
    for item in data["values"]:
        try:
            rows.append({
                "datetime": pd.to_datetime(item["datetime"]),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
            })
        except (ValueError, KeyError, TypeError):
            continue
    
    if len(rows) < 20:
        print(f"  {symbol}: ข้อมูลไม่พอ ({len(rows)} rows)")
        return None
    
    df = pd.DataFrame(rows)
    df = df.iloc[::-1].reset_index(drop=True)
    return df

# ============ Indicators ============

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
    macd_line = ema12 - ema26
    signal_line = calculate_ema(macd_line, 9)
    return macd_line, signal_line

def analyze_signal(df):
    close = df["close"]
    
    ema9 = calculate_ema(close, 9)
    ema21 = calculate_ema(close, 21)
    rsi = calculate_rsi(close)
    macd_line, signal_line = calculate_macd(close)
    
    current_price = close.iloc[-1]
    curr_rsi = rsi.iloc[-1]
    
    signals = []
    
    # EMA Crossover (เช็คเฉพาะจุด cross)
    if ema9.iloc[-2] < ema21.iloc[-2] and ema9.iloc[-1] > ema21.iloc[-1]:
        signals.append(("BUY", "EMA 9/21 Golden Cross"))
    elif ema9.iloc[-2] > ema21.iloc[-2] and ema9.iloc[-1] < ema21.iloc[-1]:
        signals.append(("SELL", "EMA 9/21 Death Cross"))
    
    # RSI (เช็คเฉพาะจุดที่เพิ่งเข้าโซน)
    if rsi.iloc[-2] >= 30 and curr_rsi < 30:
        signals.append(("BUY", f"RSI Oversold ({curr_rsi:.1f})"))
    elif rsi.iloc[-2] <= 70 and curr_rsi > 70:
        signals.append(("SELL", f"RSI Overbought ({curr_rsi:.1f})"))
    
    # MACD Crossover
    if macd_line.iloc[-2] < signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        signals.append(("BUY", "MACD Bullish Cross"))
    elif macd_line.iloc[-2] > signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        signals.append(("SELL", "MACD Bearish Cross"))
    
    return signals, current_price, curr_rsi, ema9, ema21, macd_line, signal_line, rsi

# ============ สร้างกราฟ ============

def create_chart(df, pair, signal_type, reasons, ema9, ema21, macd_line, signal_line, rsi):
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1, 1]})
    fig.patch.set_facecolor(COLORS["bg"])
    
    for ax in axes:
        ax.set_facecolor(COLORS["bg"])
        ax.tick_params(colors=COLORS["text"])
        for spine in ax.spines.values():
            spine.set_color(COLORS["grid"])
        ax.grid(True, alpha=0.3, color=COLORS["grid"])
    
    x = range(len(df))
    
    # กราฟราคา + EMA
    ax1 = axes[0]
    ax1.plot(x, df["close"].values, label="Price", color=COLORS["text"], linewidth=1.5)
    ax1.plot(x, ema9.values, label="EMA 9", color=COLORS["ema_fast"], linewidth=1)
    ax1.plot(x, ema21.values, label="EMA 21", color=COLORS["ema_slow"], linewidth=1)
    
    # Candlestick
    for i in range(len(df)):
        color = COLORS["candle_up"] if df["close"].iloc[i] >= df["open"].iloc[i] else COLORS["candle_down"]
        ax1.plot([i, i], [df["low"].iloc[i], df["high"].iloc[i]], color=color, linewidth=1)
        ax1.plot([i, i], [df["open"].iloc[i], df["close"].iloc[i]], color=color, linewidth=3)
    
    # จุดสัญญาณ
    marker_color = COLORS["candle_up"] if signal_type == "BUY" else COLORS["candle_down"]
    marker = "^" if signal_type == "BUY" else "v"
    ax1.scatter(len(df)-1, df["close"].iloc[-1], color=marker_color, s=300, marker=marker, zorder=5, edgecolors='white')
    
    ax1.set_ylabel("Price", color=COLORS["text"])
    ax1.legend(loc="upper left", facecolor=COLORS["bg"], labelcolor=COLORS["text"])
    
    emoji = "🟢" if signal_type == "BUY" else "🔴"
    ax1.set_title(f'{emoji} {pair} - {signal_type}', color=COLORS["text"], fontsize=14, fontweight='bold')
    
    # MACD
    ax2 = axes[1]
    ax2.plot(x, macd_line.values, label="MACD", color=COLORS["macd"], linewidth=1)
    ax2.plot(x, signal_line.values, label="Signal", color=COLORS["signal"], linewidth=1)
    macd_hist = (macd_line - signal_line).values
    colors = [COLORS["candle_up"] if v >= 0 else COLORS["candle_down"] for v in macd_hist]
    ax2.bar(x, macd_hist, color=colors, alpha=0.5)
    ax2.axhline(y=0, color='gray', linestyle='--', linewidth=0.5)
    ax2.set_ylabel("MACD", color=COLORS["text"])
    ax2.legend(loc="upper left", facecolor=COLORS["bg"], labelcolor=COLORS["text"])
    
    # RSI
    ax3 = axes[2]
    ax3.plot(x, rsi.values, label="RSI", color=COLORS["rsi"], linewidth=1.5)
    ax3.axhline(y=70, color=COLORS["candle_down"], linestyle='--', linewidth=0.5)
    ax3.axhline(y=30, color=COLORS["candle_up"], linestyle='--', linewidth=0.5)
    ax3.fill_between(x, 30, 70, alpha=0.1, color='gray')
    ax3.set_ylabel("RSI", color=COLORS["text"])
    ax3.set_ylim(0, 100)
    ax3.legend(loc="upper left", facecolor=COLORS["bg"], labelcolor=COLORS["text"])
    
    plt.tight_layout()
    
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=100, facecolor=COLORS["bg"])
    buf.seek(0)
    plt.close()
    
    return buf

# ============ Telegram ============

def send_telegram_photo(photo, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": photo}
    data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    requests.post(url, files=files, data=data)

# ============ เช็คสัญญาณซ้ำ ============

def is_duplicate_signal(pair, signal_type, reasons):
    """เช็คว่าสัญญาณนี้ส่งไปแล้วหรือยัง"""
    global sent_signals
    
    key = f"{pair}_{signal_type}"
    reason_key = "_".join(sorted(reasons))
    current_hour = get_thai_time().strftime('%Y-%m-%d-%H')
    
    # ถ้าส่งไปแล้วในชั่วโมงเดียวกัน = ซ้ำ
    if key in sent_signals:
        if sent_signals[key]["hour"] == current_hour and sent_signals[key]["reasons"] == reason_key:
            return True
    
    # บันทึกว่าส่งแล้ว
    sent_signals[key] = {
        "hour": current_hour,
        "reasons": reason_key
    }
    return False

# ============ Main Loop ============

def check_all_pairs():
    now = get_thai_time()
    
    if not is_market_open():
        print(f"[{now.strftime('%H:%M')}] ตลาดปิด - ข้าม")
        return
    
    print(f"[{now.strftime('%H:%M')}] Checking signals...")
    
    for pair in PAIRS:
        try:
            df = get_forex_data(pair)
            if df is None:
                continue
            
            signals, price, rsi_val, ema9, ema21, macd_line, signal_line, rsi_series = analyze_signal(df)
            
            if not signals:
                print(f"  {pair}: ไม่มีสัญญาณ")
                continue
            
            # รวมสัญญาณที่เป็น BUY และ SELL แยกกัน
            buy_reasons = [reason for sig_type, reason in signals if sig_type == "BUY"]
            sell_reasons = [reason for sig_type, reason in signals if sig_type == "SELL"]
            
            # ส่ง BUY (ถ้ามี)
            if buy_reasons:
                if not is_duplicate_signal(pair, "BUY", buy_reasons):
                    emoji = "🟢"
                    reasons_text = "\n• ".join(buy_reasons)
                    
                    chart = create_chart(df, pair, "BUY", buy_reasons, ema9, ema21, macd_line, signal_line, rsi_series)
                    
                    caption = f"""
⚡ <b>{emoji} BUY SIGNAL</b>

💱 คู่เงิน: <b>{pair}</b>
💰 ราคา: {price:.5f}
📊 RSI: {rsi_val:.1f}

📝 เหตุผล:
- {reasons_text}

🕐 เวลา: {get_thai_time().strftime('%H:%M')}

⚠️ <i>This is not financial advice</i>
"""
                    send_telegram_photo(chart, caption)
                    print(f"  {pair}: ส่งสัญญาณ BUY")
                    time.sleep(2)
                else:
                    print(f"  {pair}: BUY ส่งไปแล้ว (ข้าม)")
            
            # ส่ง SELL (ถ้ามี)
            if sell_reasons:
                if not is_duplicate_signal(pair, "SELL", sell_reasons):
                    emoji = "🔴"
                    reasons_text = "\n• ".join(sell_reasons)
                    
                    chart = create_chart(df, pair, "SELL", sell_reasons, ema9, ema21, macd_line, signal_line, rsi_series)
                    
                    caption = f"""
⚡ <b>{emoji} SELL SIGNAL</b>

💱 คู่เงิน: <b>{pair}</b>
💰 ราคา: {price:.5f}
📊 RSI: {rsi_val:.1f}

📝 เหตุผล:
- {reasons_text}

🕐 เวลา: {get_thai_time().strftime('%H:%M')}

⚠️ <i>This is not financial advice</i>
"""
                    send_telegram_photo(chart, caption)
                    print(f"  {pair}: ส่งสัญญาณ SELL")
                    time.sleep(2)
                else:
                    print(f"  {pair}: SELL ส่งไปแล้ว (ข้าม)")
                
        except Exception as e:
            print(f"Error {pair}: {e}")

# ============ Main ============

if __name__ == "__main__":
    print(f"🚀 Forex Signal Bot Started!")
    print(f"📅 Thai Time: {get_thai_time().strftime('%Y-%m-%d %H:%M')}")
    print(f"⏰ Check every 20 minutes")
    print(f"💱 Pairs: {', '.join(PAIRS)}")
    print("-" * 40)
    
    check_all_pairs()
    
    schedule.every(20).minutes.do(check_all_pairs)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
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

TZ_THAI = pytz.timezone('Asia/Bangkok')

# เก็บสัญญาณที่ส่งไปแล้ว (ป้องกันส่งซ้ำ)
sent_signals = {}

# Lock ป้องกันรันซ้อน
is_running = False

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

def analyze_signal(df, pair):
    close = df["close"]
    
    ema9 = calculate_ema(close, 9)
    ema21 = calculate_ema(close, 21)
    rsi = calculate_rsi(close)
    macd_line, signal_line = calculate_macd(close)
    
    current_price = close.iloc[-1]
    curr_ema9 = ema9.iloc[-1]
    curr_ema21 = ema21.iloc[-1]
    curr_rsi = rsi.iloc[-1]
    curr_macd = macd_line.iloc[-1]
    curr_signal = signal_line.iloc[-1]
    
    # แสดง Debug ทุกค่า
    print(f"  {pair}:")
    print(f"    Price: {current_price:.5f}")
    print(f"    EMA9: {curr_ema9:.5f}, EMA21: {curr_ema21:.5f}, Diff: {(curr_ema9-curr_ema21):.5f}")
    print(f"    RSI: {curr_rsi:.1f}")
    print(f"    MACD: {curr_macd:.5f}, Signal: {curr_signal:.5f}, Diff: {(curr_macd-curr_signal):.5f}")
    
    signals = []
    
    # ===== EMA Crossover =====
    if ema9.iloc[-2] < ema21.iloc[-2] and curr_ema9 > curr_ema21:
        signals.append(("BUY", "EMA 9/21 Golden Cross"))
    elif ema9.iloc[-2] > ema21.iloc[-2] and curr_ema9 < curr_ema21:
        signals.append(("SELL", "EMA 9/21 Death Cross"))
    
    # ===== EMA Trend (เพิ่มใหม่ - ผ่อนปรน) =====
    # ถ้า EMA9 > EMA21 อยู่แล้ว และ ราคาอยู่เหนือทั้งสอง = Bullish
    elif curr_ema9 > curr_ema21 and current_price > curr_ema9:
        signals.append(("BUY", "Price above EMA9 > EMA21 (Bullish)"))
    # ถ้า EMA9 < EMA21 อยู่แล้ว และ ราคาอยู่ใต้ทั้งสอง = Bearish
    elif curr_ema9 < curr_ema21 and current_price < curr_ema9:
        signals.append(("SELL", "Price below EMA9 < EMA21 (Bearish)"))
    
    # ===== RSI =====
    if curr_rsi < 30:
        signals.append(("BUY", f"RSI Oversold ({curr_rsi:.1f})"))
    elif curr_rsi > 70:
        signals.append(("SELL", f"RSI Overbought ({curr_rsi:.1f})"))
    
    # ===== MACD Crossover =====
    if macd_line.iloc[-2] < signal_line.iloc[-2] and curr_macd > curr_signal:
        signals.append(("BUY", "MACD Bullish Cross"))
    elif macd_line.iloc[-2] > signal_line.iloc[-2] and curr_macd < curr_signal:
        signals.append(("SELL", "MACD Bearish Cross"))
    
    # ===== MACD Momentum (เพิ่มใหม่ - ผ่อนปรน) =====
    macd_diff = curr_macd - curr_signal
    if macd_diff > 0 and curr_macd > 0:
        signals.append(("BUY", f"MACD Bullish Momentum"))
    elif macd_diff < 0 and curr_macd < 0:
        signals.append(("SELL", f"MACD Bearish Momentum"))
    
    print(f"    Signals: {len(signals)}")
    
    return signals, current_price, curr_rsi, ema9, ema21, macd_line, signal_line, rsi

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
    
    ax1 = axes[0]
    ax1.plot(x, df["close"].values, label="Price", color=COLORS["text"], linewidth=1.5)
    ax1.plot(x, ema9.values, label="EMA 9", color=COLORS["ema_fast"], linewidth=1)
    ax1.plot(x, ema21.values, label="EMA 21", color=COLORS["ema_slow"], linewidth=1)
    
    for i in range(len(df)):
        color = COLORS["candle_up"] if df["close"].iloc[i] >= df["open"].iloc[i] else COLORS["candle_down"]
        ax1.plot([i, i], [df["low"].iloc[i], df["high"].iloc[i]], color=color, linewidth=1)
        ax1.plot([i, i], [df["open"].iloc[i], df["close"].iloc[i]], color=color, linewidth=3)
    
    marker_color = COLORS["candle_up"] if signal_type == "BUY" else COLORS["candle_down"]
    marker = "^" if signal_type == "BUY" else "v"
    ax1.scatter(len(df)-1, df["close"].iloc[-1], color=marker_color, s=300, marker=marker, zorder=5, edgecolors='white')
    
    ax1.set_ylabel("Price", color=COLORS["text"])
    ax1.legend(loc="upper left", facecolor=COLORS["bg"], labelcolor=COLORS["text"])
    
    emoji = "🟢" if signal_type == "BUY" else "🔴"
    ax1.set_title(f'{emoji} {pair} - {signal_type}', color=COLORS["text"], fontsize=14, fontweight='bold')
    
    ax2 = axes[1]
    ax2.plot(x, macd_line.values, label="MACD", color=COLORS["macd"], linewidth=1)
    ax2.plot(x, signal_line.values, label="Signal", color=COLORS["signal"], linewidth=1)
    macd_hist = (macd_line - signal_line).values
    colors_hist = [COLORS["candle_up"] if v >= 0 else COLORS["candle_down"] for v in macd_hist]
    ax2.bar(x, macd_hist, color=colors_hist, alpha=0.5)
    ax2.axhline(y=0, color='gray', linestyle='--', linewidth=0.5)
    ax2.set_ylabel("MACD", color=COLORS["text"])
    ax2.legend(loc="upper left", facecolor=COLORS["bg"], labelcolor=COLORS["text"])
    
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

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    response = requests.post(url, data=data)
    return response.ok

def send_telegram_photo(photo, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": photo}
    data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    response = requests.post(url, files=files, data=data)
    return response.ok

def get_signal_key(pair, signal_type, reasons):
    reasons_str = "|".join(sorted(reasons))
    raw = f"{pair}_{signal_type}_{reasons_str}"
    return hashlib.md5(raw.encode()).hexdigest()

def can_send_signal(pair, signal_type, reasons):
    global sent_signals
    
    key = get_signal_key(pair, signal_type, reasons)
    now = get_thai_time()
    
    expired_keys = []
    for k, v in sent_signals.items():
        if now - v > timedelta(minutes=60):
            expired_keys.append(k)
    for k in expired_keys:
        del sent_signals[k]
    
    if key in sent_signals:
        return False
    
    sent_signals[key] = now
    return True

def check_all_pairs():
    global is_running
    
    if is_running:
        print("Already running, skip...")
        return
    
    is_running = True
    now = get_thai_time()
    
    try:
        if not is_market_open():
            print(f"[{now.strftime('%H:%M')}] ตลาดปิด - ข้าม")
            return
        
        print(f"\n[{now.strftime('%H:%M')}] Checking signals...")
        print("=" * 50)
        
        for pair in PAIRS:
            try:
                df = get_forex_data(pair)
                if df is None:
                    continue
                
                signals, price, rsi_val, ema9, ema21, macd_line, signal_line, rsi_series = analyze_signal(df, pair)
                
                if not signals:
                    continue
                
                buy_reasons = [reason for sig_type, reason in signals if sig_type == "BUY"]
                sell_reasons = [reason for sig_type, reason in signals if sig_type == "SELL"]
                
                if buy_reasons and can_send_signal(pair, "BUY", buy_reasons):
                    reasons_text = "\n• ".join(buy_reasons)
                    chart = create_chart(df, pair, "BUY", buy_reasons, ema9, ema21, macd_line, signal_line, rsi_series)
                    
                    caption = f"""⚡ <b>🟢 BUY SIGNAL</b>

💱 คู่เงิน: <b>{pair}</b>
💰 ราคา: {price:.5f}
📊 RSI: {rsi_val:.1f}

📝 เหตุผล:
- {reasons_text}

🕐 เวลา: {get_thai_time().strftime('%H:%M')}

⚠️ <i>This is not financial advice</i>"""
                    
                    if send_telegram_photo(chart, caption):
                        print(f"    ✅ ส่ง BUY สำเร็จ")
                    time.sleep(2)
                
                if sell_reasons and can_send_signal(pair, "SELL", sell_reasons):
                    reasons_text = "\n• ".join(sell_reasons)
                    chart = create_chart(df, pair, "SELL", sell_reasons, ema9, ema21, macd_line, signal_line, rsi_series)
                    
                    caption = f"""⚡ <b>🔴 SELL SIGNAL</b>

💱 คู่เงิน: <b>{pair}</b>
💰 ราคา: {price:.5f}
📊 RSI: {rsi_val:.1f}

📝 เหตุผล:
- {reasons_text}

🕐 เวลา: {get_thai_time().strftime('%H:%M')}

⚠️ <i>This is not financial advice</i>"""
                    
                    if send_telegram_photo(chart, caption):
                        print(f"    ✅ ส่ง SELL สำเร็จ")
                    time.sleep(2)
                    
            except Exception as e:
                print(f"Error {pair}: {e}")
        
        print("=" * 50)
    
    finally:
        is_running = False

if __name__ == "__main__":
    print(f"🚀 Forex Signal Bot Started!")
    print(f"📅 Thai Time: {get_thai_time().strftime('%Y-%m-%d %H:%M')}")
    print(f"🏪 Market Open: {is_market_open()}")
    print(f"⏰ Check every 20 minutes")
    print(f"💱 Pairs: {', '.join(PAIRS)}")
    
    send_telegram_message(f"🚀 Bot Started!\n📅 {get_thai_time().strftime('%Y-%m-%d %H:%M')}")
    
    check_all_pairs()
    
    schedule.every(20).minutes.do(check_all_pairs)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
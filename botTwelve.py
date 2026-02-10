import os
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

# ============ ตั้งค่า ============
TELEGRAM_TOKEN = "8578035505:AAFs-5jrH8-v3Zr9itQSVjKhiyFF_1U0iKg"
CHAT_ID = "8404883319"
TWELVE_DATA_KEY = "a624ba50c97f454f92c58f3cf8de1be9"

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD"]

TZ_THAI = pytz.timezone('Asia/Bangkok')

# เก็บสัญญาณที่ส่งไปแล้ว {key: timestamp}
sent_signals = {}
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

# ============ ฟังก์ชัน Utility ============

def get_thai_time():
    return datetime.now(TZ_THAI)

def is_market_open():
    now = get_thai_time()
    weekday = now.weekday()
    hour = now.hour
    
    if weekday == 5: return False # เสาร์
    if weekday == 6: return False # อาทิตย์
    if weekday == 0 and hour < 4: return False # จันทร์เช้าตรู่
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
        print(f"  ❌ {symbol}: Request error - {e}")
        return None
    
    if "values" not in data:
        print(f"  ❌ {symbol}: No Data - {data.get('message', 'Unknown error')}")
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
        except:
            continue
    
    if len(rows) < 30:
        print(f"  ⚠️ {symbol}: Not enough data ({len(rows)} rows)")
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

# ============ Analysis ============

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
    
    print(f"  🔍 {pair}: Price: {current_price:.4f} | RSI: {curr_rsi:.1f}")
    
    signals = []
    
    # EMA
    if ema9.iloc[-2] < ema21.iloc[-2] and curr_ema9 > curr_ema21:
        signals.append(("BUY", "EMA 9/21 Golden Cross"))
    elif ema9.iloc[-2] > ema21.iloc[-2] and curr_ema9 < curr_ema21:
        signals.append(("SELL", "EMA 9/21 Death Cross"))
    elif curr_ema9 > curr_ema21 and current_price > curr_ema9:
        signals.append(("BUY", "Trend Bullish (Price > EMA9 > EMA21)"))
    elif curr_ema9 < curr_ema21 and current_price < curr_ema9:
        signals.append(("SELL", "Trend Bearish (Price < EMA9 < EMA21)"))
    
    # RSI
    if curr_rsi < 30: signals.append(("BUY", f"RSI Oversold ({curr_rsi:.1f})"))
    elif curr_rsi > 70: signals.append(("SELL", f"RSI Overbought ({curr_rsi:.1f})"))
    
    # MACD
    if macd_line.iloc[-2] < signal_line.iloc[-2] and curr_macd > curr_signal:
        signals.append(("BUY", "MACD Bullish Cross"))
    elif macd_line.iloc[-2] > signal_line.iloc[-2] and curr_macd < curr_signal:
        signals.append(("SELL", "MACD Bearish Cross"))
    
    macd_diff = curr_macd - curr_signal
    if macd_diff > 0 and curr_macd > 0: signals.append(("BUY", f"MACD Bullish Momentum"))
    elif macd_diff < 0 and curr_macd < 0: signals.append(("SELL", f"MACD Bearish Momentum"))
    
    return signals, current_price, curr_rsi, ema9, ema21, macd_line, signal_line, rsi

# ============ Chart Generation (Fixed Fonts) ============

def create_chart(df, pair, signal_type, reasons, ema9, ema21, macd_line, signal_line, rsi):
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1, 1]})
    fig.patch.set_facecolor(COLORS["bg"])
    
    for ax in axes:
        ax.set_facecolor(COLORS["bg"])
        ax.tick_params(colors=COLORS["text"])
        for spine in ax.spines.values(): spine.set_color(COLORS["grid"])
        ax.grid(True, alpha=0.3, color=COLORS["grid"])
    
    x = range(len(df))
    
    # Plot 1: Price
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
    
    # --- แก้ไขตรงนี้: เอา Emoji ออกจาก Title เพื่อแก้ Error ---
    ax1.set_title(f'{pair} - {signal_type}', color=COLORS["text"], fontsize=14, fontweight='bold')
    
    # Plot 2: MACD
    ax2 = axes[1]
    ax2.plot(x, macd_line.values, label="MACD", color=COLORS["macd"], linewidth=1)
    ax2.plot(x, signal_line.values, label="Signal", color=COLORS["signal"], linewidth=1)
    macd_hist = (macd_line - signal_line).values
    colors_hist = [COLORS["candle_up"] if v >= 0 else COLORS["candle_down"] for v in macd_hist]
    ax2.bar(x, macd_hist, color=colors_hist, alpha=0.5)
    ax2.axhline(y=0, color='gray', linestyle='--', linewidth=0.5)
    
    # Plot 3: RSI
    ax3 = axes[2]
    ax3.plot(x, rsi.values, label="RSI", color=COLORS["rsi"], linewidth=1.5)
    ax3.axhline(y=70, color=COLORS["candle_down"], linestyle='--', linewidth=0.5)
    ax3.axhline(y=30, color=COLORS["candle_up"], linestyle='--', linewidth=0.5)
    ax3.fill_between(x, 30, 70, alpha=0.1, color='gray')
    ax3.set_ylim(0, 100)
    
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=100, facecolor=COLORS["bg"])
    buf.seek(0)
    plt.close()
    return buf

# ============ Telegram & Main Logic ============

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, data=data)
    except: pass

def send_telegram_photo(photo, caption):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        files = {"photo": photo}
        data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        requests.post(url, files=files, data=data)
        return True
    except Exception as e:
        print(f"    ❌ Telegram Error: {e}")
        return False

def get_signal_key(pair, signal_type, reasons):
    reasons_str = "|".join(sorted(reasons))
    raw = f"{pair}_{signal_type}_{reasons_str}"
    return hashlib.md5(raw.encode()).hexdigest()

def can_send_signal(pair, signal_type, reasons):
    global sent_signals
    key = get_signal_key(pair, signal_type, reasons)
    now = get_thai_time()
    
    expired = [k for k, v in sent_signals.items() if now - v > timedelta(minutes=60)]
    for k in expired: del sent_signals[k]
    
    if key in sent_signals: return False
    
    sent_signals[key] = now
    return True

def check_all_pairs():
    global is_running
    if is_running: return
    is_running = True
    now = get_thai_time()
    
    try:
        if not is_market_open():
            print(f"[{now.strftime('%H:%M')}] 💤 Market Closed")
            return
        
        print(f"\n[{now.strftime('%H:%M')}] 🔄 Checking Signals...")
        print("-" * 50)
        
        for pair in PAIRS:
            try:
                df = get_forex_data(pair)
                if df is None: continue
                
                signals, price, rsi_val, ema9, ema21, macd_line, signal_line, rsi_series = analyze_signal(df, pair)
                if not signals: continue
                
                buy_reasons = [r for t, r in signals if t == "BUY"]
                sell_reasons = [r for t, r in signals if t == "SELL"]
                
                # ถ้าเจอทั้ง BUY และ SELL ข้ามเลย
                if buy_reasons and sell_reasons:
                    print(f"    ⚠️ {pair}: Conflicting Signals -> Skipped")
                    continue
                
                if buy_reasons and can_send_signal(pair, "BUY", buy_reasons):
                    chart = create_chart(df, pair, "BUY", buy_reasons, ema9, ema21, macd_line, signal_line, rsi_series)
                    caption = f"⚡ <b>🟢 BUY SIGNAL</b>\n\n💱 <b>{pair}</b>\n💰 {price:.5f}\n📊 RSI: {rsi_val:.1f}\n\n📝 เหตุผล:\n• " + "\n• ".join(buy_reasons) + f"\n\n🕐 {now.strftime('%H:%M')}"
                    if send_telegram_photo(chart, caption): print(f"    ✅ {pair}: Sent BUY")
                    time.sleep(2)
                
                elif sell_reasons and can_send_signal(pair, "SELL", sell_reasons):
                    chart = create_chart(df, pair, "SELL", sell_reasons, ema9, ema21, macd_line, signal_line, rsi_series)
                    caption = f"⚡ <b>🔴 SELL SIGNAL</b>\n\n💱 <b>{pair}</b>\n💰 {price:.5f}\n📊 RSI: {rsi_val:.1f}\n\n📝 เหตุผล:\n• " + "\n• ".join(sell_reasons) + f"\n\n🕐 {now.strftime('%H:%M')}"
                    if send_telegram_photo(chart, caption): print(f"    ✅ {pair}: Sent SELL")
                    time.sleep(2)

            except Exception as e:
                print(f"    ❌ Error {pair}: {e}")
                traceback.print_exc()

    finally:
        is_running = False

if __name__ == "__main__":
    print(f"🚀 Bot Started at {get_thai_time().strftime('%H:%M')}")
    send_telegram_message("🚀 Bot Started!")
    
    check_all_pairs()
    schedule.every(20).minutes.do(check_all_pairs)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
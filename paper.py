import os
import yfinance as yf
import pandas as pd
import time
import json
from datetime import datetime
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I am alive"

def run():
    # Get the PORT from Render (or use 8080 if testing on your laptop)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
    
# ==========================================
# CONFIGURATION
# ==========================================
SYMBOL = "GC=F"             # Symbol for Gold Futures (Real-time). Use "XAUUSD=X" for Spot.
TIMEFRAME_INTERVAL = "1h"   # 1-Hour Candles
REFRESH_RATE = 60           # Check market every 60 seconds

# Strategy Settings
DONCHIAN_PERIOD = 20
EMA_PERIOD = 200
RISK_PER_TRADE = 0.01       # Risk 1% of capital
INITIAL_CAPITAL = 10000     
STATE_FILE = "bot_state.json"

# ==========================================
# TRADING ENGINE
# ==========================================
class PaperTrader:
    def __init__(self):
        self.load_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                self.state = json.load(f)
            print(f"Loaded Wallet: ${self.state['balance']:.2f}")
        else:
            self.state = {
                "balance": INITIAL_CAPITAL,
                "position": None, # None, 'LONG', 'SHORT'
                "entry_price": 0,
                "lots": 0,
                "trade_history": []
            }
            print(f"Created New Wallet: ${INITIAL_CAPITAL:.2f}")

    def save_state(self):
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=4)

    def get_data(self):
        try:
            # Fetch enough data for 200 EMA
            df = yf.download(SYMBOL, period="1mo", interval=TIMEFRAME_INTERVAL, progress=False)
            if len(df) < EMA_PERIOD:
                print("Not enough data yet...")
                return None
            
            # Flatten MultiIndex columns if present (Fix for new yfinance versions)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Calculate Indicators
            # Shift(1) because we trade breakouts of the PREVIOUS high
            df['Upper'] = df['High'].rolling(window=DONCHIAN_PERIOD).max().shift(1)
            df['Lower'] = df['Low'].rolling(window=DONCHIAN_PERIOD).min().shift(1)
            df['Mid'] = (df['Upper'] + df['Lower']) / 2
            df['EMA'] = df['Close'].ewm(span=EMA_PERIOD, adjust=False).mean()
            
            return df.iloc[-1] # Return just the latest candle
        except Exception as e:
            print(f"Data Error: {e}")
            return None

    def calculate_position_size(self, entry, stop_loss):
        risk_amt = self.state['balance'] * RISK_PER_TRADE
        dist = abs(entry - stop_loss)
        if dist == 0: dist = 1.0
        # Assuming 1 Lot = 100 oz (Standard Gold Contract)
        contract_size = 100
        lots = risk_amt / (dist * contract_size)
        return round(max(0.01, lots), 2)

    def execute_trade(self, action, price, lots=0, pnl=0):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if action == "OPEN_LONG":
            self.state['position'] = 'LONG'
            self.state['entry_price'] = price
            self.state['lots'] = lots
            print(f"[{timestamp}] 🟢 OPEN LONG @ {price:.2f} (Lots: {lots})")
            
        elif action == "OPEN_SHORT":
            self.state['position'] = 'SHORT'
            self.state['entry_price'] = price
            self.state['lots'] = lots
            print(f"[{timestamp}] 🔴 OPEN SHORT @ {price:.2f} (Lots: {lots})")
            
        elif action == "CLOSE":
            self.state['balance'] += pnl
            self.state['position'] = None
            self.state['entry_price'] = 0
            self.state['lots'] = 0
            
            record = f"[{timestamp}] ✖ CLOSE TRADE @ {price:.2f} | PnL: ${pnl:.2f}"
            self.state['trade_history'].append(record)
            print(record)
            print(f"New Balance: ${self.state['balance']:.2f}")

        self.save_state()

    def run(self):
        print(f"--- Live Paper Trader Started ({SYMBOL}) ---")
        print("Press Ctrl+C to stop. Wallet saved automatically.")
        
        while True:
            try:
                # 1. Fetch Live Data
                latest = self.get_data()
                if latest is None:
                    time.sleep(10)
                    continue

                price = latest['Close']
                upper = latest['Upper']
                lower = latest['Lower']
                mid = latest['Mid']
                ema = latest['EMA']
                
                pos = self.state['position']
                entry = self.state['entry_price']
                lots = self.state['lots']
                contract_size = 100

                # Print Status Bar
                status_symbol = "🟩" if pos == "LONG" else "🟥" if pos == "SHORT" else "⬜"
                print(f"\r{status_symbol} Price: {price:.2f} | Ch: {lower:.1f}-{upper:.1f} | EMA: {ema:.1f} | Bal: ${self.state['balance']:.0f} ", end="")

                # 2. EXIT LOGIC
                if pos == "LONG":
                    # Exit if Price < Middle Band
                    if price < mid:
                        pnl = (mid - entry) * lots * contract_size
                        print("\nExit Signal Detected (Price crossed Mid Band)")
                        self.execute_trade("CLOSE", mid, pnl=pnl)
                        
                elif pos == "SHORT":
                    # Exit if Price > Middle Band
                    if price > mid:
                        pnl = (entry - mid) * lots * contract_size
                        print("\nExit Signal Detected (Price crossed Mid Band)")
                        self.execute_trade("CLOSE", mid, pnl=pnl)

                # 3. ENTRY LOGIC (Only if no position)
                elif pos is None:
                    # Long: Breakout Up + Above EMA
                    if price > upper and price > ema:
                        print("\nEntry Signal Detected (Breakout UP)")
                        size = self.calculate_position_size(price, mid)
                        self.execute_trade("OPEN_LONG", price, lots=size)
                        
                    # Short: Breakout Down + Below EMA
                    elif price < lower and price < ema:
                        print("\nEntry Signal Detected (Breakout DOWN)")
                        size = self.calculate_position_size(price, mid)
                        self.execute_trade("OPEN_SHORT", price, lots=size)

                time.sleep(REFRESH_RATE)

            except KeyboardInterrupt:
                print("\nBot stopped.")
                break
            except Exception as e:
                print(f"\nError: {e}")
                time.sleep(10)

if __name__ == "__main__":
    bot = PaperTrader()
    bot.run()

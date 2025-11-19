import yfinance as yf
import pandas as pd
import time
import os
import json
import requests
from datetime import datetime
from flask import Flask
from threading import Thread

# ==========================================
# CONFIGURATION
# ==========================================
SYMBOL = "GC=F"
TIMEFRAME_INTERVAL = "1h"
REFRESH_RATE = 60

# --- JSONBIN SECRETS (PASTE YOURS HERE) ---
# (Keep your existing keys if you have them)
BIN_ID = "691def67ae596e708f63486e" 
API_KEY = "$2a$10$tz1bke1XrPrRJE7GaR8dcuJtn1YL9D36xex2gqlbWF7LNZQQZ1VOO"

# --- STRATEGY SETTINGS (ASYMMETRIC DONCHIAN) ---
ENTRY_PERIOD = 20        # Lookback for Entering Trades
EXIT_PERIOD = 10         # Lookback for Exiting Trades (Tighter trailing stop)
EMA_PERIOD = 200         # Trend Filter
RISK_PER_TRADE = 0.0075  # 0.75% Risk per trade

# ==========================================
# WEB SERVER (PING HACK)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "I am alive"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# TRADING ENGINE
# ==========================================
class PaperTrader:
    def __init__(self):
        self.headers = {
            'Content-Type': 'application/json',
            'X-Master-Key': API_KEY
        }
        self.url = f"https://api.jsonbin.io/v3/b/{BIN_ID}"
        self.load_state()

    def load_state(self):
        try:
            print("Fetching Wallet from Cloud...")
            response = requests.get(self.url, headers=self.headers)
            
            if response.status_code == 200:
                self.state = response.json()['record']
                print(f"Loaded Wallet: ${self.state['balance']:.2f}")
            else:
                print(f"Error loading bin: {response.text}")
                self.state = {"balance": 10000, "position": None, "entry_price": 0, "lots": 0, "trade_history": []}
        except Exception as e:
            print(f"Connection Error: {e}")
            self.state = {"balance": 10000, "position": None, "entry_price": 0, "lots": 0, "trade_history": []}

    def save_state(self):
        try:
            response = requests.put(self.url, json=self.state, headers=self.headers)
            if response.status_code != 200:
                print(f" [Cloud Save Failed]: {response.text}")
        except Exception as e:
            print(f"Save Error: {e}")

    def get_data(self):
        try:
            # Download enough data for 200 EMA
            df = yf.download(
                SYMBOL, 
                period="1mo", 
                interval=TIMEFRAME_INTERVAL, 
                progress=False, 
                auto_adjust=False,
                multi_level_index=False
            )
            
            if len(df) < EMA_PERIOD: return None
            
            # --- CALCULATE INDICATORS ---
            
            # 1. Entry Channel (20 Period)
            df['Entry_Upper'] = df['High'].rolling(window=ENTRY_PERIOD).max().shift(1)
            df['Entry_Lower'] = df['Low'].rolling(window=ENTRY_PERIOD).min().shift(1)
            
            # 2. Exit Channel (10 Period)
            df['Exit_Upper'] = df['High'].rolling(window=EXIT_PERIOD).max().shift(1)
            df['Exit_Lower'] = df['Low'].rolling(window=EXIT_PERIOD).min().shift(1)
            
            # 3. Stop Line (Middle of Exit Channel)
            df['Stop_Line'] = (df['Exit_Upper'] + df['Exit_Lower']) / 2
            
            # 4. Trend Filter
            df['EMA'] = df['Close'].ewm(span=EMA_PERIOD, adjust=False).mean()
            
            return df.iloc[-1]
        except Exception as e:
            print(f"Data Error: {e}")
            return None

    def calculate_position_size(self, entry, stop_loss):
        risk_amt = self.state['balance'] * RISK_PER_TRADE
        dist = abs(entry - stop_loss)
        if dist < 0.5: dist = 0.5
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
            record = f"[{timestamp}] ✖ CLOSE @ {price:.2f} | PnL: ${pnl:.2f}"
            self.state['trade_history'].append(record)
            print(record)

        self.save_state()

    def run(self):
        print(f"--- Asymmetric Donchian Bot Started ({SYMBOL}) ---")
        
        while True:
            try:
                latest = self.get_data()
                if latest is None:
                    time.sleep(10)
                    continue

                # Live Market Data
                price = latest['Close']
                
                # Strategy Levels
                entry_upper = latest['Entry_Upper']
                entry_lower = latest['Entry_Lower']
                stop_line = latest['Stop_Line']
                ema = latest['EMA']
                
                pos = self.state['position']
                entry_p = self.state['entry_price']
                lots = self.state['lots']
                contract_size = 100

                # Status Log
                status_symbol = "🟩" if pos == "LONG" else "🟥" if pos == "SHORT" else "⬜"
                print(f"{status_symbol} Price: {price:.2f} | Entry: {entry_lower:.1f}-{entry_upper:.1f} | Stop: {stop_line:.1f} | Bal: ${self.state['balance']:.0f}", flush=True)

                # --- TRADING LOGIC ---

                # 1. CHECK EXITS (Based on STOP LINE)
                if pos == "LONG":
                    # Exit Long if Price crosses BELOW the Stop Line
                    if price < stop_line:
                        pnl = (stop_line - entry_p) * lots * contract_size
                        self.execute_trade("CLOSE", stop_line, pnl=pnl)
                        
                elif pos == "SHORT":
                    # Exit Short if Price crosses ABOVE the Stop Line
                    if price > stop_line:
                        pnl = (entry_p - stop_line) * lots * contract_size
                        self.execute_trade("CLOSE", stop_line, pnl=pnl)

                # 2. CHECK ENTRIES (Based on ENTRY CHANNEL + EMA)
                elif pos is None:
                    # Long Entry: Breakout Upper AND Above EMA
                    if price > entry_upper and price > ema:
                        size = self.calculate_position_size(price, stop_line)
                        self.execute_trade("OPEN_LONG", price, lots=size)
                        
                    # Short Entry: Breakout Lower AND Below EMA
                    elif price < entry_lower and price < ema:
                        size = self.calculate_position_size(price, stop_line)
                        self.execute_trade("OPEN_SHORT", price, lots=size)

                time.sleep(REFRESH_RATE)

            except Exception as e:
                print(f"Error: {e}")
                time.sleep(10)

if __name__ == "__main__":
    keep_alive()
    bot = PaperTrader()
    bot.run()

import streamlit as st
import threading
import time
import datetime as dt
import yfinance as yf
import pandas as pd
from math import isnan

# CONFIG
DEFAULT_TICKERS = "AAPL,MSFT,TSLA,AMZN,GOOGL,META,NVDA,NFLX"
CHECK_INTERVAL_SECONDS = 15 * 60  # 15 minutes
THRESHOLD_PE_LE_25 = 5.0  # 5% drop threshold
THRESHOLD_PE_GT_25_OR_NEG = 7.5  # 7.5% drop threshold
HISTORY_DAYS = 11  # window to safely find "7 days ago" trading close
MAX_HISTORY_ENTRIES = 100
YF_INFO_RETRIES = 2
YF_RETRY_BACKOFF_SECONDS = 2

# HELPERS
def parse_tickers(text):
    """Parse comma-separated ticker string into list"""
    return [t.strip().upper() for t in text.split(",") if t.strip()]

def safe_get_info(ticker):
    """Get ticker info with retries for rate limiting"""
    for i in range(YF_INFO_RETRIES):
        try:
            return yf.Ticker(ticker).info or {}
        except Exception as e:
            if i < YF_INFO_RETRIES - 1 and "Too Many Requests" in str(e):
                time.sleep(YF_RETRY_BACKOFF_SECONDS * (2 ** i))
                continue
            return {}

def batch_fetch_histories(tickers):
    """Fetch historical data for all tickers in one API call"""
    if not tickers:
        return None
    try:
        df = yf.download(
            tickers,
            period=f"{HISTORY_DAYS}d",
            interval="1d",
            progress=False,
            group_by="ticker",
            threads=True,
        )
        return df
    except Exception:
        return None

def get_prices_from_hist(hist_df, tickers):
    """Extract current and 7-day-ago prices from historical data"""
    prices = {}
    prior_prices = {}
    now = dt.datetime.utcnow().date()
    target_date = now - dt.timedelta(days=7)
    
    if hist_df is None:
        for tk in tickers:
            prices[tk] = None
            prior_prices[tk] = None
        return prices, prior_prices

    multi = isinstance(hist_df.columns, pd.MultiIndex)
    
    for tk in tickers:
        try:
            if multi and len(tickers) > 1:
                df = hist_df[tk].dropna(subset=["Close"])
            else:
                df = hist_df.dropna(subset=["Close"])
                
            if df.empty:
                prices[tk] = None
                prior_prices[tk] = None
                continue
                
            # Get current price (latest close)
            last_close = float(df["Close"].iloc[-1])
            prices[tk] = last_close

            # Get price from 7 days ago
            df_dates = pd.to_datetime(df.index).normalize()
            eligible = df_dates[df_dates <= pd.to_datetime(target_date).normalize()]
            
            if not eligible.empty:
                chosen = eligible.max()
                row = df.loc[str(chosen.date())]
                prior_prices[tk] = float(row["Close"])
            else:
                # Fallback to oldest available data
                prior_prices[tk] = float(df["Close"].iloc[0])
                
        except Exception:
            prices[tk] = None
            prior_prices[tk] = None
            
    return prices, prior_prices

def evaluate_batch(tickers, hist_df, last_good):
    """Evaluate all tickers for drop alerts"""
    prices, prior_prices = get_prices_from_hist(hist_df, tickers)
    results

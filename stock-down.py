import streamlit as st
import threading
import time
import datetime as dt
import yfinance as yf
import pandas as pd
import requests
from math import isnan

# CONFIG
DEFAULT_TICKERS = "AAPL,MSFT,TSLA,AMZN"
CHECK_INTERVAL_SECONDS = 30 * 60  # 30 minutes
THRESHOLD_PE_LE_25 = 5.0
THRESHOLD_PE_GT_25_OR_NEG = 7.5
HISTORY_DAYS = 11  # window to safely find "7 days ago" trading close
MAX_HISTORY_ENTRIES = 200
YF_INFO_RETRIES = 2
YF_RETRY_BACKOFF_SECONDS = 2

# HELPERS
def parse_tickers(text):
    return [t.strip().upper() for t in text.split(",") if t.strip()]

def safe_get_info(ticker):
    # minimal retries for .info (can hit rate limits). Return dict or {}
    for i in range(YF_INFO_RETRIES):
        try:
            return yf.Ticker(ticker).info or {}
        except Exception as e:
            if i < YF_INFO_RETRIES - 1 and "Too Many Requests" in str(e):
                time.sleep(YF_RETRY_BACKOFF_SECONDS * (2 ** i))
                continue
            return {}

def batch_fetch_histories(tickers):
    # Use yf.download to fetch daily closes for all tickers in one request.
    if not tickers:
        return None
    try:
        # period covers HISTORY_DAYS days: ensures we can find a price ~7 days ago
        df = yf.download(tickers, period=f"{HISTORY_DAYS}d", interval="1d",
                         progress=False, group_by="ticker", threads=True)
        return df
    except Exception:
        return None

def get_prices_from_hist(hist_df, tickers):
    # hist_df may be MultiIndex if multiple tickers, or single ticker DataFrame
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
            if multi:
                df = hist_df[tk].dropna(subset=["Close"])
            else:
                df = hist_df.dropna(subset=["Close"])
            if df.empty:
                prices[tk] = None
                prior_prices[tk] = None
                continue
            # current price -> last available close in history
            last_close = float(df["Close"].iloc[-1])
            prices[tk] = last_close

            # find last available close on or before target_date
            df_dates = pd.to_datetime(df.index).normalize()
            eligible = df_dates[df_dates <= pd.to_datetime(target_date).normalize()]
            if not eligible.empty:
                chosen = eligible.max()
                # locate row by chosen date
                row = df.loc[str(chosen.date())]
                prior_prices[tk] = float(row["Close"])
            else:
                # fallback to earliest available in df
                prior_prices[tk] = float(df["Close"].iloc[0])
        except Exception:
            prices[tk] = None
            prior_prices[tk] = None
    return prices, prior_prices

def evaluate_batch(tickers, hist_df, last_good):
    prices, prior_prices = get_prices_from_hist(hist_df, tickers)
    results = []
    for tk in tickers:
        current = prices.get(tk)
        prior = prior_prices.get(tk)
        pe = None
        pe_info = {}
        # attempt to get PE only if we have a price and no strong reason not to
        try:
            info = safe_get_info(tk)
            pe_val = info.get("trailingPE")
            if pe_val is not None and isinstance(pe_val, (int,float)) and not isnan(pe_val):
                pe = float(pe_val)
        except Exception:
            pe = None

        if current is None:
            # if history failed for this ticker, use last known good values if present
            lg = last_good.get(tk, {})
            current = lg.get("current_price")
            prior = lg.get("prior_price")
            note = "used last-good data" if current is not None else "no data"
            results.append({"ticker": tk, "error": f"missing current price ({note})"})
            continue

        if prior is None:
            results.append({"ticker": tk, "error": "no prior price found"})
            continue

        drop_pct = (prior - current) / prior * 100.0 if prior != 0 else 0.0
        if (pe is not None) and (pe <= 25):
            threshold = THRESHOLD_PE_LE_25
        else:
            threshold = THRESHOLD_PE_GT_25_OR_NEG

        alert = drop_pct >= threshold
        row = {
            "ticker": tk,
            "prior_price": prior,
            "current_price": current,
            "drop_pct": drop_pct,
           

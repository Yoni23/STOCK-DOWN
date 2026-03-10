import streamlit as st
import threading
import time
import datetime as dt
import yfinance as yf
import pandas as pd
import requests
from math import isnan

# --- CONFIG default ---
DEFAULT_TICKERS = "AAPL,MSFT,TSLA,AMZN"
CHECK_INTERVAL_SECONDS = 15 * 60  # 15 minutes
THRESHOLD_PE_LE_25 = 5.0
THRESHOLD_PE_GT_25_OR_NEG = 7.5

# --- helper functions ---
def parse_tickers(text):
    return [t.strip().upper() for t in text.split(",") if t.strip()]

def get_ticker_info(ticker):
    tk = yf.Ticker(ticker)
    info = tk.info
    current_price = info.get("regularMarketPrice") or info.get("previousClose")
    pe = info.get("trailingPE")
    if current_price is None:
        hist = tk.history(period="2d", interval="1d")
        if not hist.empty:
            current_price = float(hist["Close"].iloc[-1])
    if pe is None or (isinstance(pe, float) and isnan(pe)):
        pe = None
    return float(current_price), pe

def get_price_7_days_ago(ticker):
    tk = yf.Ticker(ticker)
    end = dt.datetime.utcnow().date()
    start = end - dt.timedelta(days=10)
    hist = tk.history(start=start.isoformat(), end=(end + dt.timedelta(days=1)).isoformat(), interval="1d")
    if hist.empty:
        return None
    target = pd.to_datetime(end - dt.timedelta(days=7)).normalize()
    hist_dates = pd.to_datetime(hist.index).normalize()
    eligible = hist_dates[hist_dates <= target]
    if eligible.empty:
        chosen_idx = hist_dates[0]
    else:
        chosen_idx = eligible.max()
    price = float(hist.loc[str(chosen_idx.date())]["Close"])
    return price

def evaluate_ticker(ticker):
    try:
        current_price, pe = get_ticker_info(ticker)
    except Exception as e:
        return {"ticker": ticker, "error": f"current price/PE failed: {e}"}
    prior_price = get_price_7_days_ago(ticker)
    if prior_price is None:
        return {"ticker": ticker, "error": "no prior price found"}
    drop_pct = (prior_price - current_price) / prior_price * 100.0
    if (pe is not None) and (pe <= 25):
        threshold = THRESHOLD_PE_LE_25
    else:
        threshold = THRESHOLD_PE_GT_25_OR_NEG
    alert = drop_pct >= threshold
    return {
        "ticker": ticker,
        "prior_price": prior_price,
        "current_price": current_price,
        "drop_pct": drop_pct,
        "pe": pe,
        "threshold": threshold,
        "alert": alert,
        "time": dt.datetime.utcnow().isoformat() + "Z",
    }

# --- background worker ---
def worker_loop(get_tickers_fn, interval_seconds):
    while True:
        if not st.session_state.get("running", False):
            time.sleep(1)
            continue
        tickers = get_tickers_fn()
        now = dt.datetime.utcnow()
        results = []
        for t in tickers:
            res = evaluate_ticker(t)
            results.append(res)
        # append to alerts/history
        history = st.session_state.get("history", [])
        history.insert(0, {"time": now.isoformat() + "Z", "results": results})
        st.session_state["history"] = history[:200]  # cap length
        st.session_state["last_run"] = now.isoformat() + "Z"
        # sleep interval but check running flag every second so stop is responsive
        slept = 0
        while slept < interval_seconds:
            if not st.session_state.get("running", False):
                break
            time.sleep(1)
            slept += 1

# --- Streamlit UI ---
if "running" not in st.session_state:
    st.session_state["running"] = False
if "history" not in st.session_state:
    st.session_state["history"] = []
if "last_run" not in st.session_state:
    st.session_state["last_run"] = None

st.title("Stock 7-Day Drop Monitor")

col1, col2 = st.columns([2,1])
with col1:
    tickers_text = st.text_input("Tickers (comma separated)", value=DEFAULT_TICKERS, key="tickers_text")
with col2:
    st.write("Controls")
    if st.button("Start"):
        st.session_state["running"] = True
    if st.button("Stop"):
        st.session_state["running"] = False
    if st.button("Run now"):
        # trigger immediate one-off run by toggling a short-run flag
        tickers_now = parse_tickers(st.session_state["tickers_text"])
        results = []
        for t in tickers_now:
            results.append(evaluate_ticker(t))
        now = dt.datetime.utcnow()
        history = st.session_state.get("history", [])
        history.insert(0, {"time": now.isoformat() + "Z", "results": results})
        st.session_state["history"] = history[:200]
        st.session_state["last_run"] = now.isoformat() + "Z"

st.write(f"Running: {st.session_state['running']}  —  Last run (UTC): {st.session_state['last_run']}")

# start background thread (one only)
if "worker_thread_started" not in st.session_state:
    def get_tickers():
        return parse_tickers(st.session_state["tickers_text"])
    t = threading.Thread(target=worker_loop, args=(get_tickers, CHECK_INTERVAL_SECONDS), daemon=True)
    t.start()
    st.session_state["worker_thread_started"] = True

# show most recent results
if st.session_state["history"]:
    latest = st.session_state["history"][0]
    st.subheader(f"Latest run: {latest['time']}")
    df_rows = []
    alerts = []
    for r in latest["results"]:
        if "error" in r:
            df_rows.append([r["ticker"], "-", "-", "-", "-", "-", r["error"]])
            continue
        df_rows.append([
            r["ticker"],
            f"{r['prior_price']:.2f}",
            f"{r['current_price']:.2f}",
            f"{r['drop_pct']:.2f}%",
            f"{r['pe']:.2f}" if r["pe"] is not None else "None",
            f"{r['threshold']:.2f}%",
            "ALERT" if r["alert"] else ""
        ])
        if r["alert"]:
            alerts.append(f"{r['ticker']}: down {r['drop_pct']:.2f}% (threshold {r['threshold']}%)")
    st.table(pd.DataFrame(df_rows, columns=["Ticker","7d Price","Now","Drop","PE","Thresh","Alert"]))
    if alerts:
        st.error("Alerts:\n" + "\n".join(alerts))
else:
    st.info("No runs yet. Click 'Run now' or Start to begin periodic checks.")

# show history toggle
if st.checkbox("Show history (last runs)"):
    for item in st.session_state["history"][:20]:
        st.write(f"Run: {item['time']}")
        for r in item["results"]:
            if "error" in r:
                st.write(f"  {r['ticker']}: ERROR: {r['error']}")
            else:
                line = f"  {r['ticker']}: prior {r['prior_price']:.2f} now {r['current_price']:.2f} drop {r['drop_pct']:.2f}% pe {r['pe']}"
                if r["alert"]:
                    st.write(line + "  ALERT")
                else:
                    st.write(line)

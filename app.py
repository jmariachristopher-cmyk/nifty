import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, date, time
from dotenv import load_dotenv
from engine import StrategyConfig, run_backtest, evaluate_latest_signal
from angel_client import AngelOneClient, get_nifty_atm_pair, fetch_option_candles, fetch_nifty_spot_candles

load_dotenv()
st.set_page_config(page_title="NIFTY Premium Duel V1", layout="wide")
st.title("NIFTY Premium Duel V1")
st.caption("Angel One SmartAPI • Paper trading • 1-minute option-premium price action • No OI")

with st.sidebar:
    st.header("Strategy settings")
    risk_pct = st.number_input("Risk per trade (%)", 0.1, 2.0, 0.5, 0.1)
    max_trades = st.number_input("Maximum trades/day", 1, 2, 2)
    st.caption("V1 defaults are fixed to the agreed rules. Change only for controlled experiments.")
    st.divider()
    st.header("Angel One credentials")
    api_key = st.text_input("API key", type="password")
    client_code = st.text_input("Client code")
    pin = st.text_input("PIN / MPIN", type="password")
    totp = st.text_input("TOTP secret", type="password")
    login = st.button("Connect to Angel One", use_container_width=True)

if "angel" not in st.session_state:
    st.session_state.angel = None
if login:
    try:
        client = AngelOneClient(api_key, client_code, pin, totp)
        client.login()
        st.session_state.angel = client
        st.success("Angel One session created.")
    except Exception as e:
        st.session_state.angel = None
        st.error(f"Angel One connection failed: {e}")

tabs = st.tabs(["Live paper scanner", "Historical backtest", "Rules & limitations"])

with tabs[0]:
    st.subheader("Live paper scanner")
    st.warning("This is a paper-trading tool. It does not place orders. The live scanner requests completed 1-minute candles from Angel One; refresh manually after a candle closes.")
    if st.session_state.angel is None:
        st.info("Enter Angel One credentials in the sidebar and connect. For reliable results, first verify your SmartAPI account has NFO historical-data access.")
    else:
        if st.button("Fetch latest ATM CE/PE candles", type="primary"):
            try:
                client = st.session_state.angel
                spot = fetch_nifty_spot_candles(client, minutes=90)
                spot_px = float(spot.iloc[-1]["close"])
                pair = get_nifty_atm_pair(client, spot_px, date.today())
                st.session_state.pair = pair
                st.session_state.spot = spot
                ce = fetch_option_candles(client, pair["ce_token"], minutes=90)
                pe = fetch_option_candles(client, pair["pe_token"], minutes=90)
                st.session_state.ce = ce
                st.session_state.pe = pe
                st.session_state.pair_data = pair
            except Exception as e:
                st.error(f"Data fetch failed: {e}")
        if "pair_data" in st.session_state:
            p = st.session_state.pair_data
            a,b,c = st.columns(3)
            a.metric("NIFTY spot", f'{st.session_state.spot.iloc[-1]["close"]:.2f}')
            b.metric("ATM strike", str(p["strike"]))
            c.metric("Expiry", str(p["expiry"]))
            st.write(f'**CE:** {p["ce_symbol"]}  ·  **PE:** {p["pe_symbol"]}')
            ce, pe = st.session_state.ce, st.session_state.pe
            sig = evaluate_latest_signal(ce, pe)
            x,y,z = st.columns(3)
            x.metric("CE 5m change", f'{sig.get("ce_change", 0):.2f}%')
            y.metric("PE 5m change", f'{sig.get("pe_change", 0):.2f}%')
            z.metric("Premium Duel Score", f'{sig.get("pds", 0):.2f} pp')
            st.subheader("Decision")
            st.info(sig["signal"] + " — " + sig["reason"])
            st.caption("Signal is advisory only. The backtest engine uses next-candle entry and historical spread/slippage assumptions; this live view does not simulate broker queue priority.")
            st.write("Latest CE candles", ce.tail(10))
            st.write("Latest PE candles", pe.tail(10))

with tabs[1]:
    st.subheader("Historical backtest")
    st.markdown("Upload two CSV files containing synchronized 1-minute option candles. Required columns: `timestamp,open,high,low,close,volume`. Use the same ATM strike and expiry for both files.")
    ce_file = st.file_uploader("ATM CE 1-minute CSV", type=["csv"], key="ce_csv")
    pe_file = st.file_uploader("ATM PE 1-minute CSV", type=["csv"], key="pe_csv")
    if ce_file and pe_file:
        ce_df = pd.read_csv(ce_file)
        pe_df = pd.read_csv(pe_file)
        cfg = StrategyConfig(risk_pct=float(risk_pct)/100, max_trades_per_day=int(max_trades))
        try:
            result = run_backtest(ce_df, pe_df, cfg)
            trades = result["trades"]
            m = result["metrics"]
            cols = st.columns(5)
            cols[0].metric("Trades", m["trades"])
            cols[1].metric("Win rate", f'{m["win_rate"]:.1f}%')
            cols[2].metric("Profit factor", f'{m["profit_factor"]:.2f}')
            cols[3].metric("Net P&L", f'₹{m["net_pnl"]:.2f}')
            cols[4].metric("Max drawdown", f'₹{m["max_drawdown"]:.2f}')
            if not trades.empty:
                st.dataframe(trades, use_container_width=True)
                st.download_button("Download trade log CSV", trades.to_csv(index=False), "premium_duel_trades.csv", "text/csv")
            else:
                st.info("No qualifying trades in this sample.")
            st.caption("Backtest results are only as reliable as the uploaded data, synchronized timestamps, expiry/strike continuity, and realistic transaction-cost assumptions.")
        except Exception as e:
            st.error(f"Backtest error: {e}")
    else:
        st.info("Upload both CE and PE files to run a backtest.")

with tabs[2]:
    st.subheader("V1 rules")
    st.markdown("""
- NIFTY ATM CE and PE, same strike and expiry; strike selected at 9:20 AM and held fixed for the session.
- Signal window: 9:25–14:30 IST; forced square-off at 15:15 IST.
- CE: CE 5-bar close change >= +4%; PE 5-bar close change <= 0%; PDS >= +6 percentage points; CE close above prior five highs; CE volume >= 1.2x prior 20-bar average.
- PE: PE 5-bar close change >= +4%; CE 5-bar close change <= 0%; PDS <= -6 percentage points; PE close above prior five highs; PE volume >= 1.2x prior 20-bar average.
- Spread filter: <=1.5% of midpoint when bid/ask data exists. CSV backtests without bid/ask use a conservative configurable slippage allowance instead.
- Entry: next 1-minute candle open plus slippage. Skip if entry is >2% above signal close.
- Stop: one tick below breakout candle low; reject if stop distance >10% of entry premium.
- Exit: 50% at 1.5R; move remainder stop to entry after T1; remainder at 2R, stop, or 15:15.
- Maximum two entries per day; one active trade at a time. No re-entry on the same direction/contract after an exit.
- No OI, OI change, PCR, or option-chain positioning is read.
    """)
    st.warning("The 1-minute history endpoint may not provide a complete, synchronized 12-month option history. Download/retain contract-specific data as it is available. Do not assume missing candles or expired contracts are continuous.")

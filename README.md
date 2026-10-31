# NIFTY Premium Duel V1

Paper-trading and historical backtest starter project for NIFTY ATM CE/PE premium comparison using Angel One SmartAPI. No OI, OI change, PCR, or option-chain positioning is used.

## Important status

This is a research/paper-trading implementation, not a profit guarantee or a broker-certified trading system. Angel One API permissions, endpoint availability, scrip-master schema, expiry naming, and historical-data retention can change. Verify the current SmartAPI documentation and test with a small date range before relying on results. The live tab fetches historical one-minute candles on demand; it is not a tick-by-tick streaming execution engine and does not submit orders.

## Windows installation

1. Install Python 3.11 or 3.12.
2. Extract the ZIP.
3. Open Command Prompt in the extracted folder.
4. Create and activate an environment:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

5. Run:

```bat
streamlit run app.py
```

6. Enter API key, client code, PIN/MPIN and TOTP secret in the sidebar. Credentials are held in Streamlit session state only and are not written to a file by this app.

Do not share your API key, PIN, TOTP secret, QR code, or session tokens.

## Live paper scanner

- Connect using Angel One SmartAPI.
- Click **Fetch latest ATM CE/PE candles** after a completed minute.
- The app selects the nearest ATM strike based on NIFTY spot and the nearest expiry, then fetches NFO one-minute candles for both contracts.
- It displays the current premium-duel signal. No live order is placed.

Note: Angel One historical candle API may return limited history and may not include a fully closed current candle. The scanner is manual-refresh in V1. Confirm the timestamps and contract tokens before interpreting a signal.

## Backtesting

Upload synchronized CE and PE CSV files with columns:

`timestamp,open,high,low,close,volume`

Both files must represent the same NIFTY strike, expiry, and session, with timestamps in the same timezone. Do not concatenate different option contracts into one continuous series. Include only completed one-minute candles.

The engine:
- Inner-joins CE and PE by timestamp.
- Uses 5-bar premium percentage changes.
- Requires the premium divergence thresholds and 5-candle breakout.
- Requires current volume >= 1.2 times the previous 20 completed candles' average.
- Enters at next candle open with configurable slippage.
- Uses structural stop below the signal candle low, rejects stops wider than 10% of entry.
- Takes 50% at 1.5R, moves remaining stop to entry, exits remainder at 2R or stop.
- Forces exit at 15:15.
- Allows maximum two trades/day and blocks another entry in the same direction that day.
- Estimates brokerage as a configurable flat charge per order. This is not a full Indian statutory fee calculator; add actual brokerage, STT, exchange transaction charges, GST, SEBI charges, stamp duty and slippage for the selected broker/segment before evaluating profitability.

### Known V1 simplifications to review before research conclusions

1. The CSV backtest uses premium OHLCV but standard Angel historical candles do not provide historical bid/ask. Therefore the 1.5% spread filter cannot be reconstructed from ordinary OHLCV. Use quote/tick archives to validate spread or treat the slippage assumption conservatively.
2. The sizing engine reports units, not lot-rounded contracts. Round down to the valid lot size before any live use.
3. A single candle can touch both stop and target. The engine assumes the stop happens first (conservative).
4. The current strategy has no underlying spot directional filter. The earlier design discussion mentioned spot confirmation, but the agreed premium-only rule set above intentionally excludes it.
5. For a valid 12-month study, acquire and preserve each historical expiry's own CE/PE contract candles; ATM strike selection and expiry must be reconstructed for each session. Do not use today's ATM contract for past dates.
6. Expiry-day, non-expiry-day, trending and sideways results should be reported separately.
7. Test on data not used to tune thresholds. Do not optimize thresholds on the same sample used to report performance.

## Exact V1 signal thresholds

CE:
- CE 5-bar close change >= +4%
- PE 5-bar close change <= 0%
- PDS = CE change - PE change >= +6 percentage points
- CE close > highest high of previous five completed CE candles
- CE volume >= 1.2 * average volume of previous 20 completed CE candles

PE:
- PE 5-bar close change >= +4%
- CE 5-bar close change <= 0%
- PDS <= -6 percentage points
- PE close > highest high of previous five completed PE candles
- PE volume >= 1.2 * average volume of previous 20 completed PE candles

The entry is next bar open. Skip if next open is more than 2% above the signal close. Initial stop is one tick below signal candle low. Reject if risk exceeds 10% of entry premium. T1=1.5R for half quantity; after T1 move remaining stop to entry; T2=2R. Time exit at 15:15.

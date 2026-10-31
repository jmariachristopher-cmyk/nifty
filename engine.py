from dataclasses import dataclass
import pandas as pd
import numpy as np

@dataclass
class StrategyConfig:
    risk_pct: float = 0.005
    max_trades_per_day: int = 2
    ce_min_change: float = 4.0
    pe_min_change: float = 4.0
    pds_threshold: float = 6.0
    volume_multiple: float = 1.2
    spread_limit_pct: float = 1.5
    max_stop_pct: float = 10.0
    max_chase_pct: float = 2.0
    tick_size: float = 0.05
    slippage_pct: float = 0.10
    brokerage_per_order: float = 20.0
    other_cost_per_order: float = 0.0
    start_time: str = "09:25"
    last_entry_time: str = "14:30"
    squareoff_time: str = "15:15"

def _prepare(df):
    d = df.copy()
    required = {"timestamp","open","high","low","close","volume"}
    missing = required - set(d.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    for c in ["open","high","low","close","volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["timestamp","open","high","low","close","volume"])
    d = d.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return d

def _features(ce, pe, cfg):
    c = _prepare(ce).rename(columns={x:f"ce_{x}" for x in ["open","high","low","close","volume"]})
    p = _prepare(pe).rename(columns={x:f"pe_{x}" for x in ["open","high","low","close","volume"]})
    d = pd.merge(c, p, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)
    # Only fully closed bars; all rolling references exclude the current candle.
    d["ce_change"] = (d.ce_close / d.ce_close.shift(5) - 1) * 100
    d["pe_change"] = (d.pe_close / d.pe_close.shift(5) - 1) * 100
    d["pds"] = d.ce_change - d.pe_change
    d["ce_prior_high"] = d.ce_high.shift(1).rolling(5).max()
    d["pe_prior_high"] = d.pe_high.shift(1).rolling(5).max()
    d["ce_vol_avg"] = d.ce_volume.shift(1).rolling(20).mean()
    d["pe_vol_avg"] = d.pe_volume.shift(1).rolling(20).mean()
    d["ce_signal"] = (
        (d.ce_change >= cfg.ce_min_change) &
        (d.pe_change <= 0) &
        (d.pds >= cfg.pds_threshold) &
        (d.ce_close > d.ce_prior_high) &
        (d.ce_volume >= cfg.volume_multiple * d.ce_vol_avg)
    )
    d["pe_signal"] = (
        (d.pe_change >= cfg.pe_min_change) &
        (d.ce_change <= 0) &
        (d.pds <= -cfg.pds_threshold) &
        (d.pe_close > d.pe_prior_high) &
        (d.pe_volume >= cfg.volume_multiple * d.pe_vol_avg)
    )
    return d

def evaluate_latest_signal(ce, pe, cfg=None):
    cfg = cfg or StrategyConfig()
    d = _features(ce, pe, cfg)
    if d.empty:
        return {"signal":"NO TRADE","reason":"No synchronized candles."}
    r = d.iloc[-1]
    if pd.isna(r.ce_change) or pd.isna(r.pe_change):
        return {"signal":"NO TRADE","reason":"Insufficient lookback candles."}
    if bool(r.ce_signal) and not bool(r.pe_signal):
        sig, reason = "CE WATCH", "CE premium strength and breakout conditions met. Confirm next-candle execution filters."
    elif bool(r.pe_signal) and not bool(r.ce_signal):
        sig, reason = "PE WATCH", "PE premium strength and breakout conditions met. Confirm next-candle execution filters."
    else:
        sig, reason = "NO TRADE", "Premium divergence and breakout conditions are not simultaneously satisfied."
    return {"signal":sig,"reason":reason,"ce_change":float(r.ce_change),"pe_change":float(r.pe_change),"pds":float(r.pds)}

def _time_str(ts):
    return ts.strftime("%H:%M")

def run_backtest(ce, pe, cfg=None, capital=200000):
    cfg = cfg or StrategyConfig()
    d = _features(ce, pe, cfg)
    trades = []
    daily_count = {}
    blocked_contract_direction = set()
    i = 0
    while i < len(d)-1:
        r = d.iloc[i]
        ts = r.timestamp
        day = ts.date()
        if not (cfg.start_time <= _time_str(ts) <= cfg.last_entry_time):
            i += 1; continue
        if daily_count.get(day,0) >= cfg.max_trades_per_day:
            i += 1; continue
        if not (bool(r.ce_signal) ^ bool(r.pe_signal)):
            i += 1; continue
        side = "CE" if bool(r.ce_signal) else "PE"
        direction_key = (day, side)
        if direction_key in blocked_contract_direction:
            i += 1; continue
        nxt = d.iloc[i+1]
        entry_raw = float(nxt[f"{side.lower()}_open"])
        signal_close = float(r[f"{side.lower()}_close"])
        # Skip gaps/chasing >2% from the signal candle close.
        if entry_raw > signal_close * (1 + cfg.max_chase_pct/100):
            i += 1; continue
        entry = entry_raw * (1 + cfg.slippage_pct/100)
        signal_low = float(r[f"{side.lower()}_low"])
        stop = signal_low - cfg.tick_size
        risk = entry - stop
        if risk <= 0 or risk / entry * 100 > cfg.max_stop_pct:
            i += 1; continue
        # Spread is not present in standard Angel historical candles. Apply slippage both ways.
        risk_cash = capital * cfg.risk_pct
        lot_qty = max(1, int(risk_cash // risk))
        # Keep sizing as units in backtest; user must round to actual lot size before live use.
        qty = lot_qty
        t1 = entry + 1.5*risk
        t2 = entry + 2.0*risk
        remaining = qty
        realized = 0.0
        t1_hit = False
        exit_reason = None
        exit_time = None
        exit_price = None
        j = i+1
        while j < len(d):
            bar = d.iloc[j]
            bts = bar.timestamp
            if _time_str(bts) >= cfg.squareoff_time:
                px = float(bar[f"{side.lower()}_close"])
                exit_price = px * (1 - cfg.slippage_pct/100)
                realized += remaining * (exit_price-entry)
                exit_reason="TIME_EXIT"; exit_time=bts
                break
            lo = float(bar[f"{side.lower()}_low"])
            hi = float(bar[f"{side.lower()}_high"])
            # Conservative same-bar ordering: stop takes priority over target.
            active_stop = entry if t1_hit else stop
            if lo <= active_stop:
                px = active_stop * (1 - cfg.slippage_pct/100)
                realized += remaining * (px-entry)
                exit_reason="BREAKEVEN" if t1_hit else "STOP"
                exit_time=bts; exit_price=px
                break
            if not t1_hit and hi >= t1:
                half = max(1, qty//2)
                half = min(half, remaining)
                px = t1 * (1 - cfg.slippage_pct/100)
                realized += half * (px-entry)
                remaining -= half
                t1_hit=True
                if remaining == 0:
                    exit_reason="T1_FULL"; exit_time=bts; exit_price=px
                    break
            if t1_hit and hi >= t2:
                px = t2 * (1 - cfg.slippage_pct/100)
                realized += remaining * (px-entry)
                remaining=0
                exit_reason="T2"; exit_time=bts; exit_price=px
                break
            j += 1
        if exit_reason is None:
            last = d.iloc[-1]
            px = float(last[f"{side.lower()}_close"]) * (1-cfg.slippage_pct/100)
            realized += remaining*(px-entry)
            exit_reason="END_OF_DATA"; exit_time=last.timestamp; exit_price=px
        # Simplified round-trip charges: entry + exit; partial exit incurs an additional exit order.
        orders = 3 if t1_hit else 2
        costs = orders*(cfg.brokerage_per_order+cfg.other_cost_per_order)
        net = realized-costs
        trades.append({
            "date":str(day),"side":side,"signal_time":str(ts),"entry_time":str(nxt.timestamp),
            "entry":round(entry,2),"stop":round(stop,2),"t1":round(t1,2),"t2":round(t2,2),
            "quantity_units":qty,"exit_time":str(exit_time),"exit_reason":exit_reason,
            "gross_pnl":round(realized,2),"estimated_costs":round(costs,2),"net_pnl":round(net,2)
        })
        daily_count[day]=daily_count.get(day,0)+1
        blocked_contract_direction.add(direction_key)
        i=max(i+1,j+1)
    tdf=pd.DataFrame(trades)
    if tdf.empty:
        metrics={"trades":0,"win_rate":0.0,"profit_factor":0.0,"net_pnl":0.0,"max_drawdown":0.0}
    else:
        pnl=tdf.net_pnl.astype(float)
        wins=pnl[pnl>0].sum()
        losses=abs(pnl[pnl<0].sum())
        equity=pnl.cumsum()
        dd=(equity-equity.cummax()).min() if len(equity) else 0
        metrics={"trades":len(tdf),"win_rate":float((pnl>0).mean()*100),
                 "profit_factor":float(wins/losses) if losses>0 else float("inf") if wins>0 else 0.0,
                 "net_pnl":float(pnl.sum()),"max_drawdown":float(abs(dd))}
    return {"trades":tdf,"metrics":metrics,"candles":d}

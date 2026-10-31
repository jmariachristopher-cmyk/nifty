import json, pyotp, pandas as pd
from datetime import datetime, timedelta, date
from SmartApi import SmartConnect

SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

class AngelOneClient:
    def __init__(self, api_key, client_code, pin, totp_secret):
        self.api_key=api_key.strip()
        self.client_code=client_code.strip()
        self.pin=pin.strip()
        self.totp_secret=totp_secret.strip()
        self.smart=None
    def login(self):
        if not all([self.api_key,self.client_code,self.pin,self.totp_secret]):
            raise ValueError("Enter API key, client code, PIN and TOTP secret.")
        self.smart=SmartConnect(api_key=self.api_key)
        otp=pyotp.TOTP(self.totp_secret).now()
        result=self.smart.generateSession(self.client_code,self.pin,otp)
        if not result or not result.get("status"):
            raise RuntimeError(f"Login rejected: {result}")
        return result
    def candles(self, token, exchange, interval="ONE_MINUTE", minutes=120):
        end=datetime.now()
        start=end-timedelta(minutes=minutes+30)
        params={"exchange":exchange,"symboltoken":str(token),"interval":interval,
                "fromdate":start.strftime("%Y-%m-%d %H:%M"),
                "todate":end.strftime("%Y-%m-%d %H:%M")}
        response=self.smart.getCandleData(params)
        if not response or not response.get("status") or not response.get("data"):
            raise RuntimeError(f"Historical candle request failed: {response}")
        df=pd.DataFrame(response["data"],columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"]=pd.to_datetime(df["timestamp"])
        return df

def _master():
    import requests
    r=requests.get(SCRIP_MASTER_URL,timeout=30)
    r.raise_for_status()
    return pd.DataFrame(r.json())

def get_nifty_atm_pair(client, spot_price, session_date=None):
    master=_master()
    required={"name","instrumenttype","symbol","token","expiry","strike","exch_seg"}
    if not required.issubset(master.columns):
        raise RuntimeError("Angel One scrip master schema changed; expected option contract columns not found.")
    opts=master[(master.name=="NIFTY") & (master.instrumenttype=="OPTIDX") & (master.exch_seg=="NFO")].copy()
    opts["expiry_dt"]=pd.to_datetime(opts.expiry,format="%d%b%Y",errors="coerce")
    today=pd.Timestamp(session_date or date.today()).normalize()
    opts=opts[opts.expiry_dt>=today]
    if opts.empty:
        raise RuntimeError("No current/future NIFTY option contracts found in scrip master.")
    expiry=opts.expiry_dt.min()
    opts=opts[opts.expiry_dt==expiry]
    opts["strike_num"]=pd.to_numeric(opts.strike,errors="coerce")/100
    strike=round(float(spot_price)/50)*50
    pair=opts[opts.strike_num==strike]
    ce=pair[pair.symbol.str.endswith("CE")]
    pe=pair[pair.symbol.str.endswith("PE")]
    if ce.empty or pe.empty:
        raise RuntimeError(f"Could not find both CE and PE for ATM strike {strike}, expiry {expiry.date()}.")
    return {"strike":strike,"expiry":expiry.date(),"ce_symbol":ce.iloc[0].symbol,
            "pe_symbol":pe.iloc[0].symbol,"ce_token":str(ce.iloc[0].token),"pe_token":str(pe.iloc[0].token)}

def fetch_option_candles(client, token, minutes=120):
    return client.candles(token,"NFO","ONE_MINUTE",minutes)

def fetch_nifty_spot_candles(client, minutes=120):
    # NIFTY 50 index token on NSE; Angel One commonly identifies it as 99926000.
    return client.candles("99926000","NSE","ONE_MINUTE",minutes)

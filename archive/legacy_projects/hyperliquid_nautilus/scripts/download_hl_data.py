import os
import requests
import pandas as pd
from datetime import datetime, timedelta
import time

DAYS = 30
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
MAX_COINS = 30

def get_top_coins(limit: int = 30) -> list[str]:
    print("Fetching top coins by 24h volume...")
    url = "https://api.hyperliquid.xyz/info"
    response = requests.post(url, json={"type": "metaAndAssetCtxs"})
    if response.status_code == 200:
        data = response.json()
        meta, ctxs = data[0], data[1]
        
        # Combine coin name with its 24h volume
        coins = []
        for asset, ctx in zip(meta["universe"], ctxs):
            name = asset["name"]
            volume = float(ctx["dayNtlVlm"])
            coins.append((name, volume))
            
        # Sort by volume descending
        coins.sort(key=lambda x: x[1], reverse=True)
        top_coins = [c[0] for c in coins[:limit]]
        print(f"Top {limit} coins: {top_coins}")
        return top_coins
    return ["BTC", "ETH", "SOL"]

def download_historical_data(coin: str, start_time: int, end_time: int) -> pd.DataFrame:
    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": "1m",
            "startTime": start_time,
            "endTime": end_time
        }
    }
    
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        data = response.json()
        if not data:
            return pd.DataFrame()
            
        df = pd.DataFrame(data)
        
        mapped = pd.DataFrame()
        mapped["instrument_id"] = df["s"] + "-USD-PERP.HYPERLIQUID"
        mapped["ts_event"] = pd.to_datetime(df["T"], unit="ms")
        mapped["ts_init"] = pd.to_datetime(df["t"], unit="ms")
        mapped["open"] = df["o"].astype(float)
        mapped["high"] = df["h"].astype(float)
        mapped["low"] = df["l"].astype(float)
        mapped["close"] = df["c"].astype(float)
        mapped["volume"] = df["v"].astype(float)
        
        return mapped
    else:
        print(f"Error fetching data: {response.text}")
        return pd.DataFrame()

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=DAYS)
    
    end_ts = int(end_dt.timestamp() * 1000)
    start_ts = int(start_dt.timestamp() * 1000)
    
    chunk_ms = 3 * 24 * 60 * 60 * 1000
    
    coins = get_top_coins(MAX_COINS)
    
    for coin in coins:
        print(f"Downloading {DAYS} days of 1m data for {coin}...")
        
        current_start = start_ts
        all_dfs = []
        
        while current_start < end_ts:
            current_end = min(current_start + chunk_ms, end_ts)
            
            df = download_historical_data(coin, current_start, current_end)
            if not df.empty:
                all_dfs.append(df)
                
            print(f"Fetched {len(df)} candles for {coin} from {datetime.fromtimestamp(current_start/1000)}")
            current_start = current_end + 1
            time.sleep(0.5)
            
        if all_dfs:
            final_df = pd.concat(all_dfs, ignore_index=True)
            final_df.drop_duplicates(subset=["ts_init"], inplace=True)
            final_df.sort_values("ts_init", inplace=True)
            
            output_file = os.path.join(DATA_DIR, f"{coin}_1m.csv")
            final_df.to_csv(output_file, index=False)
            print(f"Saved {len(final_df)} candles to {output_file}")
            
if __name__ == "__main__":
    main()

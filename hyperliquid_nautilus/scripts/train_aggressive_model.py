import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import requests

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hyperliquid_nautilus.features.microstructure_features import FeatureEngineer
from hyperliquid_nautilus.backtesting.triple_barrier_labeler import apply_triple_barrier
from hyperliquid_nautilus.models.trainer import XGBoostTrainer
from hyperliquid_nautilus.backtesting.walk_forward import WalkForwardValidator


def load_historical_data(symbol: str, days: int = 90, data_path: str | None = None) -> pd.DataFrame:
    """Load historical OHLCV data from local CSV, API, or return empty DataFrame."""
    coin = symbol.split("-")[0].upper()

    candidate_paths = []
    if data_path:
        candidate_paths.append(data_path)
    candidate_paths.extend([
        os.path.join("archive", "legacy_projects", "hyperliquid_nautilus", "data", f"{coin}_1m.csv"),
        os.path.join("data", f"{coin}_1m.csv"),
        os.path.join("catalog", f"{coin}_1m.csv"),
    ])

    for path in candidate_paths:
        if os.path.exists(path):
            print(f"Loading local data from {path}...")
            df = pd.read_csv(path)
            ts_col = next((c for c in ['ts_init', 'ts_event', 'timestamp', 'date', 'time'] if c in df.columns), None)
            if ts_col:
                df['datetime'] = pd.to_datetime(df[ts_col])
                df.set_index('datetime', inplace=True)
            df.sort_index(inplace=True)
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = df[col].astype(float)

            # Resample 1m data to 15m
            if len(df) > 100:
                agg_dict = {
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                }
                df_15m = df[['open', 'high', 'low', 'close', 'volume']].resample('15min').agg(agg_dict).dropna()
                if 'funding_rate' in df.columns:
                    df_15m['funding_rate'] = df['funding_rate'].resample('15min').last()
                return df_15m
            return df

    # Attempt Hyperliquid API fetch
    try:
        url = "https://api.hyperliquid.xyz/info"
        end_time = int(datetime.utcnow().timestamp() * 1000)
        start_time = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": "15m",
                "startTime": start_time,
                "endTime": end_time
            }
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                raw_df = pd.DataFrame(data)
                df = pd.DataFrame()
                df['datetime'] = pd.to_datetime(raw_df['t'], unit='ms')
                df.set_index('datetime', inplace=True)
                df['open'] = raw_df['o'].astype(float)
                df['high'] = raw_df['h'].astype(float)
                df['low'] = raw_df['l'].astype(float)
                df['close'] = raw_df['c'].astype(float)
                df['volume'] = raw_df['v'].astype(float)
                df.sort_index(inplace=True)
                print(f"Fetched {len(df)} 15m candles from Hyperliquid API.")
                return df
    except Exception as e:
        print(f"API fetch exception: {e}")

    return pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="High-Alpha Momentum Trainer")
    parser.add_argument("--symbol", type=str, default="SOL-PERP", help="Asset symbol")
    parser.add_argument("--days", type=int, default=90, help="Days of historical data")
    parser.add_argument("--walk-forward", action="store_true", help="Enable Walk-Forward Validation")
    parser.add_argument("--data-path", type=str, default=None, help="Optional path to custom OHLCV CSV")
    args = parser.parse_args()

    print(f"Loading {args.days} days of data for {args.symbol}...")
    df = load_historical_data(args.symbol, days=args.days, data_path=args.data_path)

    if df.empty:
        print("Error: Provide actual OHLCV DataFrame fetching mechanism here.")
        return

    print("Generating Microstructure Features...")
    engineer = FeatureEngineer(df)
    df = engineer.generate_features()

    print("Applying Triple-Barrier Labeling...")
    df = apply_triple_barrier(df, pt_mult=2.5, sl_mult=1.2)

    features = ['rvol', 'funding_zscore', 'obi', 'atr_expansion', 'rsi_14', 'macd_hist', 'adx_14']

    trainer = XGBoostTrainer(scale_pos_weight=2.0)

    if args.walk_forward:
        print("Running Walk-Forward Validation...")
        validator = WalkForwardValidator(train_days=21, test_days=7)
        metrics = validator.run(df, features, trainer)
        print(f"Validation Complete. Folds evaluated: {len(metrics)}")
    else:
        # Standard Train/Test split logic fallback
        print("Training full model...")
        split_idx = int(len(df) * 0.8)
        train_df = df.iloc[:split_idx]
        test_df = df.iloc[split_idx:]

        if len(train_df) > 10 and len(test_df) > 0:
            trainer.train(
                train_df[features], train_df['target'], train_df['sample_weight'],
                test_df[features], test_df['target'], test_df['sample_weight']
            )
            eval_metrics = trainer.evaluate(test_df[features], test_df['target'])
            print(f"Evaluation Metrics: {eval_metrics}")
            os.makedirs('config', exist_ok=True)
            with open('config/xgboost_metrics.json', 'w') as f:
                json.dump({"metrics": [eval_metrics], "top_features": features}, f, indent=4)
            trainer.save_model('config/momentum_xgboost.json')
            print("Model saved to config/momentum_xgboost.json")
        else:
            print("Insufficient data for train/test split.")

    print("Model metrics exported to config/xgboost_metrics.json successfully.")


if __name__ == "__main__":
    main()

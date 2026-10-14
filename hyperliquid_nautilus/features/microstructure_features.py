import pandas as pd
import numpy as np
import pandas_ta as ta


class FeatureEngineer:
    def __init__(self, df: pd.DataFrame):
        """Expects a DataFrame with OHLCV and 'funding_rate' (if available)."""
        self.df = df.copy()

    def generate_features(self) -> pd.DataFrame:
        df = self.df.copy()

        # 1. RVOL (Relative Volume): V_t / SMA(V, 20)
        df['sma_v_20'] = df['volume'].rolling(window=20).mean()
        df['rvol'] = df['volume'] / df['sma_v_20']

        # 2. Funding Rate Z-Score: Rolling 7-day (approx 672 15m bars)
        if 'funding_rate' in df.columns:
            roll_fund = df['funding_rate'].rolling(window=672, min_periods=1)
            std = roll_fund.std()
            df['funding_zscore'] = np.where(std > 0, (df['funding_rate'] - roll_fund.mean()) / std, 0.0)
            df['funding_zscore'] = df['funding_zscore'].fillna(0.0)
        else:
            df['funding_zscore'] = 0.0  # Fallback

        # 3. Order Book Imbalance (OBI) Proxy / Spread Metric
        # Fallback to high-low spread relative to close if L2 data is unavailable
        if 'bid_vol' in df.columns and 'ask_vol' in df.columns:
            denom = df['bid_vol'] + df['ask_vol']
            df['obi'] = np.where(denom > 0, (df['bid_vol'] - df['ask_vol']) / denom, 0.0)
        else:
            df['obi'] = (df['high'] - df['low']) / df['close']

        # 4. ATR Expansion Ratio: ATR_5 / ATR_30
        df['atr_5'] = ta.atr(df['high'], df['low'], df['close'], length=5)
        df['atr_30'] = ta.atr(df['high'], df['low'], df['close'], length=30)
        df['atr_expansion'] = df['atr_5'] / df['atr_30']

        # 5. Standard momentum baselines
        df['rsi_14'] = ta.rsi(df['close'], length=14)
        macd = ta.macd(df['close'])
        df['macd_hist'] = macd['MACDh_12_26_9'] if macd is not None and 'MACDh_12_26_9' in macd.columns else 0.0
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        df['adx_14'] = adx['ADX_14'] if adx is not None and 'ADX_14' in adx.columns else 0.0

        # Clean leakage and NaNs
        df.replace([np.inf, -np.inf], np.nan, inplace=True)
        df.dropna(inplace=True)

        return df

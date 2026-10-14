import pandas as pd
import numpy as np
import pandas_ta as ta


def apply_triple_barrier(df: pd.DataFrame, pt_mult: float = 2.5, sl_mult: float = 1.2, max_hold: int = 12) -> pd.DataFrame:
    """
    Applies Triple-Barrier labeling for Long Breakout entries.
    """
    df = df.copy()
    df['atr_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    df.dropna(subset=['atr_14'], inplace=True)

    n = len(df)
    labels = np.zeros(n, dtype=int)
    weights = np.ones(n, dtype=float)

    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    atrs = df['atr_14'].values

    for i in range(n - max_hold):
        entry_price = closes[i]
        upper_barrier = entry_price + (pt_mult * atrs[i])
        lower_barrier = entry_price - (sl_mult * atrs[i])

        for j in range(1, max_hold + 1):
            curr_idx = i + j
            if lows[curr_idx] <= lower_barrier:
                labels[i] = -1
                weights[i] = abs(entry_price - lower_barrier) / entry_price
                break
            elif highs[curr_idx] >= upper_barrier:
                labels[i] = 1
                weights[i] = abs(upper_barrier - entry_price) / entry_price
                break
    # Normalize and scale weights by min_ret so hessians allow deeper tree splits
    min_ret = 0.0025
    scaled_weights = np.clip(weights / min_ret, 0.5, 10.0)

    df['target'] = labels
    df['sample_weight'] = scaled_weights

    # Filter out neutral/timeout labels (0) or remap them based on strategy preference
    df = df[df['target'] != 0].copy()

    # Remap -1 to 0 for standard binary classification (XGBoost expects 0/1)
    df['target'] = df['target'].map({1: 1, -1: 0}).astype(int)
    return df

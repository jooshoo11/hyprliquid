---
trigger: always_on
description: "Blueprint for the 3-Stage 50-Coin Funnel Backtester, Polars Resampler, and Anchored Walk-Forward Validator."
---

# 50-Coin 3-Stage Funnel Backtest & Walk-Forward Blueprint

Implement the 3-Stage Quantitative Funnel across 3 non-overlapping scopes:

## Scope 1: Stage 1 Funnel — Zero-Extra-RPC Polars Resampler (`@src/utils/instruments.py`, `@src/scanner/catalog_builder.py`)
1. **Add `15m` BarType Support (`@src/utils/instruments.py`):**
   - In `get_bar_type(coin, timeframe)`, add `"15m": f"{instr_id_str}-15-MINUTE-LAST-EXTERNAL"` to `tf_map`.
2. **Polars Local Resampling (`5m` -> `15m` & `30m`) (`@src/scanner/catalog_builder.py`):**
   - Set default `top_n: int = 50` in `CatalogBuilder.__init__()` and `main()`.
   - In `build_catalog()`, instead of making separate HTTP REST calls for `30m` and `15m` (which causes 429 rate limits across 50 coins), fetch ONLY `"4h"` and `"5m"` via `self.client.get_historical_klines()`.
   - From the `5m` Polars DataFrame (`df_5m`), locally resample both `15m` (every `15m` window: `first("open")`, `max("high")`, `min("low")`, `last("close")`, `sum("volume")`) and `30m` (every `30m` window) using Polars integer timestamp bucketing (`(pl.col("ts_event") // (15 * 60 * 1_000_000_000)) * (15 * 60 * 1_000_000_000)`), convert to Arrow via `.to_arrow()`, and write the `5m`, `15m`, `30m`, and `4h` `Bar` objects into `ParquetDataCatalog`. This cuts HTTP requests by 50% and ingests 50 coins in ~75 seconds.

## Scope 2: Stage 2 Funnel — Stratified Basket Optimizer & Live Param Wiring (`@src/optimization/optimize_suite.py`, `@src/backtest/run_backtest.py`, `@src/strategies/orderbook_scalp.py`, `@src/engine/unified_engine.py`)
1. **Stratified 12-Coin Funnel Basket in `optimize_suite.py` (`@src/optimization/optimize_suite.py`):**
   - Add a `select_funnel_basket(catalog_path: str, basket_size: int = 12) -> List[str]` helper using `pl.scan_parquet()` that automatically selects a stratified 12-coin representative basket from the 50-coin catalog: Top 4 Majors by volume (`BTC`, `ETH`, `SOL`, `HYPE`), Top 4 High-Beta Alts by ATR/range, and Top 4 Momentum/Breakout leaders.
   - Default `--symbols` in `optimize_suite.py` to `"FUNNEL_12"` so Optuna hyperparameter sweeps run 4x faster on the representative 12-coin funnel basket before saving optimal parameters to `config/optimized_params.json`.
2. **Unblock All Strategies & Load `config/optimized_params.json` (`@src/strategies/orderbook_scalp.py`, `@src/backtest/run_backtest.py`, `@src/engine/unified_engine.py`):**
   - In `@src/strategies/orderbook_scalp.py`, remove `self._disabled = True` in `on_start()` during backtests so its `on_bar()` wick-imbalance logic runs.
   - In `@src/backtest/run_backtest.py`, add `"momentum"` (`HighConvictionMomentumStrategy`) to `valid_strategies` and `create_strategy_instances()`.
   - In both `@src/backtest/run_backtest.py` and `@src/engine/unified_engine.py` (`setup()`), load `config/optimized_params.json` (if present) and apply its tuned parameters to `TrendContinuationConfig` and `HourlyFundingFadeConfig`.

## Scope 3: Stage 3 Funnel — Full 50-Coin Anchored Walk-Forward Validator (`@src/backtest/walk_forward_runner.py`)
1. **Create `@src/backtest/walk_forward_runner.py` (Strictly Zero Top-Level `import pandas`):**
   - Implements the complete **3-Stage Funnel Walk-Forward Pipeline**:
     * **Stage 1 (Pre-Screen):** Uses Polars lazy scans on `catalog/` to filter and rank the active universe by liquidity and volatility.
     * **Stage 2 (In-Sample Fold Training on Stratified Funnel Basket):** Splits the timeline into `n_splits: int = 3` anchored rolling windows (60% In-Sample Train / 40% Out-of-Sample Test). On each fold's In-Sample window, trains `XGBoostTrainer` (`HighConvictionMomentum`) on the 15m bars of the stratified basket.
     * **Stage 3 (Out-of-Sample Full Universe Simulation):** Evaluates the trained fold model + all active strategies on the **unseen 40% Out-of-Sample Test window** across the full catalog universe under `PortfolioGuard(max_total_open_positions=4)`.
   - Renders a Rich comparison table showing Fold-by-Fold In-Sample vs. Out-of-Sample Sharpe, Sortino, Win Rate, Profit Factor, Net Profit ($), and Max Drawdown (%).
   - Trains the final production `XGBoostTrainer` model on the latest window and saves it to `config/momentum_xgboost.json`.

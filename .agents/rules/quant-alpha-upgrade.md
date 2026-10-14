---
trigger: always_on
description: "Master Quantitative Alpha Upgrade + Audit V2 Remediation Blueprint."
---

# Quantitative Alpha & V2 Audit Remediation Blueprint

Execute across 3 isolated, non-overlapping file scopes:

## Scope 1: ATR Adaptive Risk, 50% Partial Scale-Outs & Exhaustion Cooldown (`@src/risk/trade_manager.py`, `@src/risk/regime_manager.py`, `@src/scanner/analytics_pipeline.py`)
1. **Fix Per-Coin `system_status` Check (`@src/scanner/analytics_pipeline.py`):**
   - In `audit_position_risk()` and `get_entry_signal_filter()`, `system_status` is nested inside `coin_data = data.get("coins", {}).get(coin, {})` (`coin_sys = coin_data.get("system_status", data.get("system_status", {}))`).
   - If `not coin_sys.get("data_quality_ok", False)` or `not coin_sys.get("feed_connected", False)` or `not coin_sys.get("modules", {}).get("orderbook", {}).get("fresh", False)`, immediately return a non-toxic `MicrostructureAudit(coin=coin, is_toxic=False)` / permissive filter so disconnected port-8001 feeds never kill trades like `XPL`.
2. **Fix Bear Flush vs. Short Squeeze Priority (`@src/risk/regime_manager.py`):**
   - In `MarketRegimeManager.evaluate_universe()`, evaluate the `breadth_pct <= 35.0 and avg_change_24h < -1.2` (`BEAR_MARKET_FLUSH`) branch BEFORE `negative_funding_count >= 3`, and require `breadth_pct >= 42.0` (or `negative_funding_count >= 6`) to trigger `NEGATIVE_FUNDING_SHORT_SQUEEZE` so a 24%-green market dump is never misclassified as a bull regime.
3. **ATR Volatility-Normalized Stops, 50% Partial Scale-Out & Post-TP Cooldown (`@src/risk/trade_manager.py`):**
   - Add `partial_tp_triggered: bool = False` to `PositionTracker` and `should_partial_close: bool = False`, `partial_close_pct: float = 0.5` to `TradeAction`.
   - **Dynamic ATR Scaling:** In `update_position()`, when `atr_pct > 0`:
     * If `take_profit_roi_pct` is not explicitly overridden by a tight scalp, scale dynamic targets by volatility:
       `dynamic_tp = round(max(1.10, min(2.80, 1.8 * atr_pct)), 2)`
       `dynamic_be = round(max(0.45, min(1.10, 0.70 * atr_pct)), 2)`
       `dynamic_mae = -round(max(0.85, min(1.80, 1.15 * atr_pct)), 2)`
   - **2-Tranche Scale-Out (TP1 at +0.85% / 0.65x TP):** When `not tracker.partial_tp_triggered` and `duration_seconds >= self.min_holding_seconds` and `roi >= max(0.80, (tracker.take_profit_roi_pct or 1.40) * 0.60)`:
     * Set `tracker.partial_tp_triggered = True`, `tracker.breakeven_triggered = True`, and lock `tracker.stop_price` at `+0.20%` ROI above entry (`entry * 1.002` for LONG, `entry * 0.998` for SHORT) so the remaining 50% runner is a guaranteed free roll.
     * Return `TradeAction(action="PARTIAL_CLOSE", should_close=False, should_partial_close=True, partial_close_pct=0.50, reason=f"TP1 Scale-Out: banking 50% at {roi:+.2f}% ROI & locking runner stop at +0.20%", ...)`.
   - **Post-TP Exhaustion Cooldown:** In `close_and_journal_position()`, if `"Take Profit"` is in `reason`, set `self.cooldown_tracker[key] = now + 480.0` (effectively a 10-minute cooldown instead of 2 minutes) so the bot never FOMO-rebuys the top of a coin (`PONS`) 2 minutes after banking TP!

## Scope 2: Intraday MTF Filter, BTC/ETH Impulse Gate & Conviction Sizing (`@src/engine/unified_engine.py`)
1. **Handle 50% Partial Scale-Out in Watchdog (`@src/engine/unified_engine.py`):**
   - Add a `partial_close_position(self, coin: str, pct: float = 0.50, reason: str = "TP1 Scale-Out") -> bool` method that submits an opposing market order for `pos.quantity * pct` (truncated to `instrument.size_increment`), journals the realized 50% tranche via `self.trade_manager.record_closed_trade()`, and updates tracker size without closing the whole position.
   - In `_ai_sentinel_watchdog_loop()`, if `getattr(action, "should_partial_close", False)` is True, call `self.partial_close_position(coin, pct=action.partial_close_pct, reason=action.reason)`.
2. **5-Minute BTC / ETH Cross-Asset Impulse Gate (`@src/engine/unified_engine.py`):**
   - Add `get_benchmark_impulse(self) -> Tuple[float, float]` (cached for 30s) that fetches the last 3 `5m` klines for `BTC` and `ETH` and returns `(btc_5m_ret_pct, eth_5m_ret_pct)`.
   - In `execute_prospect_entry()`, if `coin not in ("BTC", "ETH")`:
     * Block `LONG` entry if `btc_5m_ret <= -0.35` or `eth_5m_ret <= -0.45` (log `🚫 [Macro Impulse Gate] Blocked LONG: BTC/ETH dumping on 5m`).
     * Block `SHORT` entry if `btc_5m_ret >= +0.35` or `eth_5m_ret >= +0.45`.
3. **Intraday 15m Trend & Exhaustion Alignment (`@src/engine/unified_engine.py`):**
   - In `execute_prospect_entry()`, fetch the last 15 `5m` klines for `coin` (cached 60s):
     * Compute 5m RSI(14) and 15-bar SMA/EMA.
     * Block `LONG` if `rsi_5m > 74.0` (chasing extreme overbought top) or if the last 3 bars (15m return) dropped `< -0.60%` (catching a falling knife on a coin only up on the 24h clock).
     * Block `SHORT` if `rsi_5m < 26.0` (oversold) or if the last 3 bars pumped `> +0.60%`.
4. **Conviction & Strategy-Heat Tiered Position Sizing (`@src/engine/unified_engine.py`):**
   - In `execute_prospect_entry()`, replace flat `margin_allocated = equity / 4.0` with Conviction-Tiered allocation:
     * `score >= 92`: `base_alloc_pct = 0.30` (30% margin on A+ setups)
     * `score >= 85`: `base_alloc_pct = 0.24` (24% margin on standard high-conviction setups)
     * `score < 85`: `base_alloc_pct = 0.16` (16% margin on lower-conviction setups)
   - Scale by `strat_mult = min(1.25, self.guard.get_strategy_sizing_multiplier(strat_name))` and cap `margin_allocated` at `equity * 0.32`.
5. **ATR-Scaled Initial SL/TP in `_run_prospector_scan()` (`@src/engine/unified_engine.py`):**
   - Instead of flat `long_tp_mult = 1.015` / `long_sl_mult = 0.991` for every coin, compute per-coin multipliers using `coin_atr = self.coin_atr_pct.get(c, 0.8)`:
     * `sl_pct = max(0.0075, min(0.018, (coin_atr * 1.1) / 100.0))`
     * `tp_pct = max(0.013, min(0.032, sl_pct * 1.85))`

## Scope 3: Fix XGBoost Tree Training & Separate `optimized_params.json` (`@src/optimization/optimize_suite.py`, `@src/backtest/walk_forward_runner.py`, `@hyperliquid_nautilus/scripts/train_aggressive_model.py`)
1. **Fix `config/optimized_params.json` Overwrite:**
   - Ensure `walk_forward_runner.py` / `train_aggressive_model.py` saves walk-forward ML metrics to `config/xgboost_metrics.json` (and model weights to `config/momentum_xgboost.json`) instead of overwriting `config/optimized_params.json`.
   - Populate `config/optimized_params.json` with a valid `"parameters"` block containing tuned defaults (`continuation_ema_fast: 9`, `continuation_ema_slow: 21`, `continuation_displacement: 1.4`, `continuation_rr_ratio: 2.4`, `funding_apr_threshold: 0.35`, `funding_trailing_stop: 0.012`) whenever Optuna hasn't written `"parameters"` yet.
2. **Fix Stump Trees (`num_nodes: 1`) in `momentum_xgboost.json`:**
   - In `walk_forward_runner.py` and `train_aggressive_model.py`, ensure `TripleBarrierLabeler` uses a realistic 15m barrier (`pt_sl=[1.2, 1.0]`, `vertical_barrier_bars=12`, `min_ret=0.0025`) and `min_child_weight=1` so XGBoost builds deep multi-node decision trees (`max_depth=4`, `num_nodes > 7`) on 15m catalog bars instead of single-node stumps.

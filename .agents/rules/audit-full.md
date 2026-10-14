---
trigger: always_on
description: "Master End-to-End System Audit and Remediation Reference for the Hyperliquid Unified Autonomous Trading Platform."
---

# Hyperliquid Autonomous Trading System — Master Audit Reference

This document serves as the single source of truth for repository cleanup, risk logic corrections, strategy repairs, and execution engine hardening across the entire workspace[cite: 1, 2, 3].

---

## 1. Repository Hygiene & Architectural Pruning

### 1.1 Redundant Vendor Trees & Corrupted Archives
- **Duplicate Nested Vendor Repositories:** Delete `@vendor/chainstack_bot/hyperliquid-trading-bot-main/` and `@vendor/hyperliquid_monitor/hyperliquid-monitor-main/`[cite: 1]. These are exact duplicates of their parent directories on disk[cite: 1].
- **Unpacked Zip Archives:** Delete `@archive/vendor_archives/` containing `chainstack.zip`, `monitor.zip`, and `smc.zip` (the latter is an invalid file containing HTTP 404 text)[cite: 1].
- **Duplicate MCP Server Folders:** Consolidate root `@hyperliquid-info-mcp/` into `@mcp_servers/hyperliquid-info-mcp/`[cite: 1]. Verify environment and config paths point to `@mcp_servers/`[cite: 1].
- **Patch Script Graveyard:** Archive or clean up the 47 legacy regex/AST hotfix scripts in `@archive/patches/` to avoid sub-agent indexing noise[cite: 1].

### 1.2 Competing Sub-Engines
The workspace contains three competing execution architectures[cite: 1]:
1. **Primary Autonomous Daemon:** `@src/engine/unified_engine.py`, `@src/execution/`, `@src/strategies/`, `@src/risk/`[cite: 1, 2, 3].
2. **Nautilus Research Sub-project:** `@hyperliquid_nautilus/`[cite: 1, 2, 3].
3. **Macro Sub-project:** `@vtx_macro/`[cite: 1].
Ensure `@src/` remains the canonical runtime path, treating `@hyperliquid_nautilus/` strictly as an offline feature-training library[cite: 1, 2, 3].

---

## 2. Core Engine & Risk Management Logic (Part 1)

### 2.1 Strategy Registration Hijack & Empty Instruments Maps
- **Files:** `@src/engine/unified_engine.py` (`setup()`, `execute_prospect_entry()`, `_ai_sentinel_watchdog_loop()`, `_ai_prospector_loop()`)[cite: 2]
- **Issue:** In `setup()`, only `self.momentum_strat` is added to `self.node.trader`[cite: 2]. Five other strategies (`continuation_strat`, `funding_strat`, `scalp_strat`, `vwap_strat`, `absorber_strat`) are instantiated but left unregistered[cite: 2].
- **Failure Modes:**
  1. `execute_prospect_entry()` checks `if self.momentum_strat:` first, forcing 100% of prospect setups onto `HighConvictionMomentumStrategy` regardless of `strat_hint`[cite: 2].
  2. Because unregistered strategies never execute `on_start()`, their `instruments_map` dictionaries remain empty (`{}`)[cite: 2].
  3. `_ai_sentinel_watchdog_loop()` proactive scalper queries `self.scalp_strat.instruments_map`, which fails silently every 120 seconds[cite: 2].
  4. `_ai_prospector_loop()` startup check waits on `len(self.continuation_strat.instruments_map) > 0`, which always hits the timeout[cite: 2].
- **Remediation:** Register all intended active strategies in `self.node.trader.add_strategy()` or remove the bypass logic in `execute_prospect_entry()` to properly route orders[cite: 2].

### 2.2 Paper Balance Desync & Feedback Loop
- **Files:** `@src/engine/unified_engine.py`, `@src/execution/node_app.py`, `@src/execution/node_runner.py`[cite: 2]
- **Issue:** `UnifiedEngine` restores starting capital from `_paper_starting_balance` ($200) + `_paper_realized_pnl`[cite: 2]. During execution, Nautilus's `SandboxExecutionClient` internally updates balance upon fills, while `close_position()` manually calculates cumulative PnL from `TradeManager`[cite: 2].
- **Failure Mode:** `_sync_bridge_state_loop()` writes `realized_pnl = state["cash_balance"] - self._paper_starting_balance` every 5 seconds, conflicting with `TradeManager.net_realized_pnl`[cite: 2]. Furthermore, `node_app.py` and `node_runner.py` hardcode a $100 starting equity base (`cash_balance - 100.0`), corrupting `bridge/paper_state.json` on restart[cite: 2].
- **Remediation:** Standardize on a single source of truth for cash balance and realized PnL across all daemons, using `$200.0` as the consistent paper base[cite: 2].

### 2.3 Sentinel Target Overwrite in Watchdog Loop
- **Files:** `@src/engine/unified_engine.py` (`_ai_sentinel_watchdog_loop()`), `@src/risk/trade_manager.py`[cite: 2]
- **Issue:** `UnifiedEngine.__init__` configures `TradeManager` with `take_profit_roi_pct=1.40` and `breakeven_roi_pct=0.60` (unleveraged price move)[cite: 2].
- **Failure Mode:** Every 10 seconds, `_ai_sentinel_watchdog_loop()` overrides `take_profit_roi_pct` to `2.0%`–`3.5%` and `breakeven_roi_pct` to `0.65%`–`1.0%` based on macro regime[cite: 2]. At 15x leverage, this sets the profit target to +30% to +52.5% ROE, while the hard stop remains `-0.90%`, breaking the intended tight scalping reward-to-risk ratio[cite: 2].
- **Remediation:** Remove the hardcoded override in `_ai_sentinel_watchdog_loop()` or scale the regime adjustments proportionately around the base 1.40% / 0.60% parameters[cite: 2].

### 2.4 Approvals Counter Leak in `PortfolioGuard`
- **Files:** `@src/risk/portfolio_guard.py` (`can_open_position()`, `register_order_submitted()`, `sync_open_positions()`)[cite: 2]
- **Issue:** `can_open_position()` increments `self._pending_approvals += 1`, which is only decremented by `register_order_submitted()`[cite: 2].
- **Failure Mode:** If an order fails validation, throws an exception during creation, or is blocked before submission, `_pending_approvals` is never decremented[cite: 2]. Once leaked to 3, `can_open_position()` permanently rejects all new trades[cite: 2].
- **Remediation:** Add `self._pending_approvals = 0` inside `PortfolioGuard.sync_open_positions()`[cite: 2].

### 2.5 Post-Only (`Alo`) Book Crossing & Premature Registration
- **Files:** `@src/engine/unified_engine.py` (`execute_prospect_entry()`), `@src/risk/trade_manager.py`[cite: 2]
- **Issue:** Maker limit prices are calculated using `mark_px -/+ (tick_size * 0.5)`[cite: 2]. When the mark price leads the best bid/ask, buy orders cross the ask, causing Hyperliquid to immediately reject the order due to `post_only=True`[cite: 2].
- **Failure Mode:** `trade_manager.register_position()` is called immediately upon order submission instead of fill[cite: 2]. If the order sits unfilled or is rejected, the position tracker records an active trade that is pruned 10 seconds later as an orphan[cite: 2].
- **Remediation:** Anchor post-only buys to `best_bid` (or `best_ask - tick_size`) and register positions with `TradeManager` only upon fill verification[cite: 2].

### 2.6 Prospector Priority Key Typo
- **Files:** `@src/engine/unified_engine.py` (`_run_prospector_scan()`)[cite: 2]
- **Issue:** In the prospect priority sort, the key is queried via `x.get("side", "")`[cite: 2]. All prospect records store direction under `"bias"` (`"LONG"` / `"SHORT"`)[cite: 2].
- **Failure Mode:** `is_bull_match` is always 0, preventing the bull-regime priority sort from prioritizing long setups over short setups[cite: 2].
- **Remediation:** Change `x.get("side", "")` to `x.get("bias", "")` in `prospect_priority_key()`[cite: 2].

### 2.7 Strategy Allocation Cap Scale Mismatch & Duplicate Definition
- **Files:** `@src/risk/portfolio_guard.py`[cite: 2]
- **Issue:** `UnifiedEngine` passes `max_strategy_equity_pct=50.0`, which `PortfolioGuard` evaluates as a 50x multiplier rather than a percentage[cite: 2].
- **Failure Mode:** Strategy equity limits are completely bypassed until `MarketRegimeManager` resets the cap[cite: 2]. Furthermore, `set_strategy_allocation_cap()` is defined twice in `portfolio_guard.py`[cite: 2].
- **Remediation:** Use normalized floats (`0.40` for 40%) across all constructors and remove the duplicate method definition[cite: 2].

---

## 3. Strategies, Scanners & Cockpit Telemetry (Part 2)

### 3.1 Delta-Neutral Carry Arbitrage Exit Scale Inversion
- **Files:** `@src/strategies/funding_arbitrage.py` (`check_active_pairs_exit()`), `@src/scanner/arbitrage_scanner.py`[cite: 3]
- **Issue:** `_get_coin_funding_apr()` returns the float APR directly (e.g. `0.60` for 60% APR)[cite: 3].
- **Failure Mode:** `check_active_pairs_exit()` calculates `cur_net_carry_apr_pct = (short_apr - long_apr) * 100.0`, scaling the APR up by an extra factor of 100 (e.g. 18,000% APR)[cite: 3]. When rates fluctuate, this scale mismatch triggers premature exits on the next bar[cite: 3].
- **Remediation:** Unify APR representations between `ArbitrageScanner` and `DeltaNeutralCarryStrategy` so raw APRs and percentages are not cross-multiplied[cite: 3].

### 3.2 Position Over-Sizing in Microstructure Scalper
- **Files:** `@src/strategies/orderbook_scalp.py` (`_execute_scalp()`), `@src/strategies/vwap_momentum.py` (`_enter_momentum()`)[cite: 3]
- **Issue:** Sizing calculations allocate `(equity / max_positions) * max_lev` without respecting the configured `risk_per_trade_pct = 0.0075`[cite: 3].
- **Failure Mode:** On BTC (40x leverage), this creates a $1,333 position on a $100 account for a 3-tick scalp[cite: 2, 3]. It also triggers an immediate rejection by `PortfolioGuard` because `can_open_position()` enforces a 15x leverage cap[cite: 2, 3].
- **Remediation:** Constrain position size using `risk_per_trade_pct` and clamp `max_lev = min(max_lev, 15.0)` prior to computing target notional[cite: 3].

### 3.3 ZeroDivisionError on Marubozu Candles
- **Files:** `@src/strategies/orderbook_scalp.py` (`on_bar()`)[cite: 3]
- **Issue:** In the synthetic bar simulation path, `upper_wick / lower_wick` and `lower_wick / upper_wick` are computed directly[cite: 3].
- **Failure Mode:** On candles with no lower wick (`lower_wick == 0`), this throws a `ZeroDivisionError`, crashing the worker thread[cite: 3].
- **Remediation:** Guard calculations using `max(lower_wick, 1e-6)` and `max(upper_wick, 1e-6)`[cite: 3].

### 3.4 Invalid Parameter in Nautilus Bracket Order Factory
- **Files:** `@src/strategies/liquidation_absorber.py` (`_execute_absorption()`)[cite: 3]
- **Issue:** `self.order_factory.bracket()` is called with `entry_post_only=self.absorber_config.post_only`[cite: 3].
- **Failure Mode:** `entry_post_only` is not a valid parameter on `order_factory.bracket()` in NautilusTrader, raising a `TypeError` and causing all absorption orders to fail silently inside the exception handler[cite: 3].
- **Remediation:** Remove `entry_post_only` from `order_factory.bracket()` and configure post-only execution via execution client options or standalone limit orders[cite: 3].

### 3.5 Disk I/O Saturation in `AnalyticsPipeline`
- **Files:** `@src/scanner/analytics_pipeline.py` (`on_message()`, `_persist_bridge()`)[cite: 3]
- **Issue:** `_persist_bridge()` writes `bridge/analytics_microstructure.json` via atomic `.tmp` replace directly on every incoming WebSocket message[cite: 3].
- **Failure Mode:** During fast orderbook updates or trade sweeps across multiple coins, disk writes occur up to 20 times per second, degrading performance[cite: 3].
- **Remediation:** Add a 1.0-second throttling timer to `_persist_bridge()` so disk writes occur at most once per second[cite: 3].

### 3.6 REST Polling Backoff in Web Cockpit
- **Files:** `@src/web/server.py`, `@src/web/index.html`[cite: 3]
- **Issue:** When the WebSocket connection drops, the web frontend falls back to polling `/api/state` without backoff[cite: 3].
- **Failure Mode:** Frequent unbacked polling triggers HTTP 429 rate limits from Hyperliquid's REST endpoints[cite: 3].
- **Remediation:** Implement exponential backoff (2s → 4s → 8s) for client-side state polling when the WebSocket is disconnected[cite: 3].

---

## 4. Remediation Checklist for Agent Execution

- [ ] **Phase 1: Filesystem Cleanup**
  - Delete `@vendor/chainstack_bot/hyperliquid-trading-bot-main/`[cite: 1]
  - Delete `@vendor/hyperliquid_monitor/hyperliquid-monitor-main/`[cite: 1]
  - Delete `@archive/vendor_archives/`[cite: 1]
  - Remove duplicate root `@hyperliquid-info-mcp/`[cite: 1]

- [ ] **Phase 2: Core Engine & Risk Patches**
  - Fix strategy registration in `@src/engine/unified_engine.py`[cite: 2]
  - Reset `self._pending_approvals = 0` in `@src/risk/portfolio_guard.py`[cite: 2]
  - Remove hardcoded TP/breakeven overrides in `_ai_sentinel_watchdog_loop()`[cite: 2]
  - Standardize paper trading starting equity across `@src/engine/unified_engine.py`, `@src/execution/node_app.py`, and `@src/tui/data_provider.py`[cite: 2, 3]
  - Correct `"side"` to `"bias"` in `@src/engine/unified_engine.py`[cite: 2]

- [ ] **Phase 3: Strategy & Scanner Hardening**
  - Fix net carry scale calculation in `@src/strategies/funding_arbitrage.py`[cite: 3]
  - Correct position sizing and leverage bounds in `@src/strategies/orderbook_scalp.py`[cite: 3]
  - Guard wick division against zero in `@src/strategies/orderbook_scalp.py`[cite: 3]
  - Remove `entry_post_only` keyword from `@src/strategies/liquidation_absorber.py`[cite: 3]
  - Throttle disk writes in `@src/scanner/analytics_pipeline.py`[cite: 3]
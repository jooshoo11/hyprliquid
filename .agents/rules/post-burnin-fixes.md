---
trigger: always_on
description: "Phase 2 Post-Burn-In Remediation & AI Studio Removal Blueprint (Zero-Local-LLM, Pure Groq + Polars Stack)."
---

# Post-Burn-In Audit & Groq Stack Migration Blueprint

Execute these exact surgical fixes across 3 isolated file scopes:

## Scope A: Core Engine & Dynamic Instrument Cache (`@src/engine/unified_engine.py`, `@run.py`)
1. **Top 50 Cache Coverage:** In `@src/engine/unified_engine.py` (`__init__`) and `@run.py`, change default `top_n: int = 50` so all 50 perpetuals scanned by `_run_prospector_scan()` are loaded into Nautilus `target_ids` on startup.
2. **Dynamic Instrument Fallback:** In `UnifiedEngine.execute_prospect_entry()`, if `instrument` is still `None` after checking `self.node.cache`, dynamically construct it via `get_hyperliquid_perp(coin)` from `@src/utils/instruments.py`, call `self.node.cache.add_instrument(instrument)`, and store it in `strat_actor.instruments_map[instr_key] = instrument` so no prospect ever fails with `Instrument not found in cache`.
3. **Eliminate Zombie Open Orders in Paper Mode:**
   - In `execute_prospect_entry()`, when `self.paper` is `True`, submit `strat_actor.order_factory.market(instrument_id=instrument.id, order_side=side, quantity=quantity)` directly instead of submitting a `post_only=True` limit order and cancelling it 0.8s later (which leaves uncancelled zombie orders in `SandboxExecutionClient` and blocks future trades with `Resting open orders already exist`).
   - In `execute_prospect_entry()`, change the open-order guard so it only blocks if there is a fresh open order (< 90s old): ignore stale zombie orders in `self.node.cache.orders_open(instrument_id=instrument.id)` older than 90 seconds.
   - In `_ai_sentinel_watchdog_loop()`, track cancelled orphan order IDs in a `self._cancelled_orphan_ids: set[str]` set so the watchdog only logs and cancels each orphan order ID once instead of spamming the log every 10 seconds.
4. **Throttle Arbitrage Scanner & Update Log Label:**
   - In `_run_prospector_scan()`, throttle `self.arbitrage_scanner.scan_and_save()` to run at most once every 300 seconds (`self._last_arb_scan_ts`) to prevent Hyperliquid public RPC 429 rate limits.
   - Change the log string `[AI Studio Prospector] Enriching prospects with Google Search grounding...` to `[Groq LPU Prospector] Auditing top prospects via Groq LPU...`.

## Scope B: Scanner & Cloud Groq Migration (`@src/utils/llm_client.py`, `@src/scanner/ai_studio_prospector.py`, `@src/scanner/analytics_pipeline.py`)
1. **Gate Disconnected Analytics Feed (`@src/scanner/analytics_pipeline.py`):**
   - At the top of `audit_position_risk()` and `get_entry_signal_filter()`, inspect `sys_status = data.get("system_status", {})`.
   - If `not sys_status.get("data_quality_ok", False)` or `not sys_status.get("feed_connected", False)` or `not sys_status.get("modules", {}).get("orderbook", {}).get("fresh", False)`, immediately return `audit` (`is_toxic=False`) / `(True, "Analytics feed offline - permissive default")`.
2. **Remove Google AI Studio (`@src/utils/llm_client.py`):**
   - Remove `google-genai` / `GEMINI_API_KEY` initialization. Do NOT add any local LLM.
   - In `query_groq_json()`, set `models_to_try = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-20b"]`.
   - Route `query_gemini_json()` directly to `self.query_groq_json(prompt=prompt, system_prompt=system_prompt or "Respond strictly in valid JSON.", temperature=temperature, retries=retries)` and make `is_gemini_ready()` return `self.is_groq_ready()` so all existing unit tests pass without AI Studio.
3. **5-Minute Throttled Groq Prospector (`@src/scanner/ai_studio_prospector.py`):**
   - Add a class/module-level cache `_LAST_ENHANCE_TS = 0.0` and `_CACHED_VERDICTS = {}`.
   - If `time.time() - _LAST_ENHANCE_TS < 300.0`, reuse `_CACHED_VERDICTS` or return `raw_prospects` immediately without making an API call (caps usage to 288 req/day vs Groq's 14,400 RPD limit).
   - Remove Google Search grounding prompt instructions; instruct Groq to audit the quantitative candidate metrics (`funding_apr`, `change_24h`, `volume_24h_usd`, `strategy`) and return the JSON array. On any error or rate limit, silently return `raw_prospects`.

## Scope C: UI & Strategy Zero-Pandas Cleanup (`@src/strategies/high_conviction_momentum.py`, `@src/tui/data_provider.py`, `@src/web/index.html`, `@tests/test_llm_integrations.py`)
1. **Zero-Pandas Fix (`@src/strategies/high_conviction_momentum.py`):**
   - Remove top-level `import pandas as pd` on line 18 (so `test_zero_pandas_in_src` passes).
   - Import `polars as pl` at top level and use `pl.DataFrame(self.bars).to_pandas().set_index("datetime")` inside `InstrumentBarHistory.to_dataframe()`.
2. **UI Badge Cleanup (`@src/tui/data_provider.py`, `@src/web/index.html`):**
   - Replace `[AI STUDIO]` / `GEMINI 3 FLASH (GROUNDED)` labels with `[GROQ LPU]` / `QUANT + GROQ LPU`.
3. **Test Compatibility (`@tests/test_llm_integrations.py`):**
   - Ensure any test asserting on `UnifiedLLMClient` or `AIStudioProspector` passes with the Groq routing.
## Scope D: Overnight Hardening & Edge-Case Immunity (`@src/engine/unified_engine.py`, `@src/risk/trade_manager.py`, `@src/risk/portfolio_guard.py`, `@run.py`)
1. **Guaranteed Sandbox Market Fills (`@src/engine/unified_engine.py`):**
   - In `execute_prospect_entry()`, when `self.paper` is `True`, before submitting the `OrderType.MARKET` order, ensure the Nautilus cache has a fresh `QuoteTick` for `instrument.id` (if `self.node.cache.quote_tick(instrument.id)` is `None`, construct a `QuoteTick` using `best_bid` / `best_ask` or `mark_px` and `instrument.make_price()` / `instrument.make_qty()` and add it via `self.node.cache.add_quote_tick(tick)` or data engine) so `SandboxExecutionClient` fills the market order instantaneously on the first attempt.
2. **Widen Swing Breathing Room (`@src/engine/unified_engine.py`):**
   - In `UnifiedEngine.__init__()`, update `TradeManager` args: set `stagnant_hours=1.5` (was `0.35`) and `fast_dud_minutes=25.0` (was `10.0`) so 15m/30m strategies are not prematurely cut after 1 candle.
3. **Ignore Disconnected-Dashboard Kills in Strategy Lockouts (`@src/risk/portfolio_guard.py`):**
   - In `update_dynamic_allocations()`, filter out any historical trade in `closed_trades` where `"Dashboard Microstructure Toxic Risk"` is in `tr.get("reason", "")` so pre-patch false kills from port 8001 never trigger a 1-hour strategy circuit breaker lockout.
4. **Deduplicate Missed Opportunity Logging (`@src/risk/trade_manager.py`):**
   - In `log_missed_opportunity()`, track `self._last_missed_log_ts: Dict[str, float] = {}` keyed by `f"{coin_clean}_{strategy_clean}"`. If the same coin + strategy was logged within the last `600.0` seconds (10 minutes), return the record without appending to `missed_opportunities.md` or `decision_journal.jsonl` to prevent 15,000-line disk bloat.
5. **Single-Instance PID Guard (`@run.py`):**
   - Write `os.getpid()` to `bridge/engine.pid` on startup (and kill any existing PID in `bridge/engine.pid` before starting) so two instances of `UnifiedEngine` can never run concurrently and fight over `bridge/paper_state.json`.

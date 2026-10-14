---
trigger: always_on
description: "Institutional 100vh 3-Column Pro Terminal Redesign & Telemetry Upgrade for the Web Cockpit (src/web/index.html + src/web/server.py)."
---

# Web Cockpit Pro Terminal Redesign Blueprint

Redesign ONLY the Web Cockpit (`@src/web/index.html` and `@src/web/server.py`) into a high-density, single-viewport (`100vh`) institutional trading terminal while wiring all `trade_manager` and `strategy_allocations` telemetry from `UnifiedEngine.get_state()`. Do NOT touch any files in `src/tui/`.

## Scope 1: Backend FastAPI Scan Endpoint (`@src/web/server.py`)
1. **Add `POST /api/scan` Endpoint:**
   - Trigger an immediate prospector scan (`engine._force_prospector_scan = True` or append `{"action": "TRIGGER_SCAN"}` to `bridge/ai_commands.json`) and return `{"status": "SUCCESS", "message": "Immediate 50-coin Prospector scan triggered"}`.

## Scope 2: Web Cockpit Single-Viewport Pro Terminal Redesign (`@src/web/index.html`)
Redesign `@src/web/index.html` into a sleek, zero-page-scroll (`h-screen overflow-hidden flex flex-col bg-[#060911] text-slate-100`) 3-Column Pro Terminal layout (preserve all existing DOM IDs and JS modal/audio/WebSocket helpers so nothing breaks):

1. **Compact Top Telemetry Ribbon (`h-12 border-b border-slate-800/90 px-4 flex items-center justify-between shrink-0`):**
   - Consolidate into a single horizontal bar:
     * **Left:** Logo (`HYPERLIQUID [AI SENTINEL]`), Mode badge (`PAPER EMULATED`), and Subsystem status pills (`#ws-indicator`, `#ws-status`, `#subsystem-groq-text`, `#badge-guard`).
     * **Center KPIs:** Total Equity (`#metric-equity` + `#metric-roi`), Cash (`#metric-cash`), Unrealized PnL (`#metric-pnl` + `#metric-pos-count` + `#metric-notional`), Win Rate (`#quant-win-rate` + `#analytics-trade-count` + `#analytics-win-loss`), Profit Factor (`#quant-profit-factor`), Sharpe (`#quant-sharpe-ratio`), Expectancy (`#quant-expectancy`), Net Realized PnL (`#quant-total-net-pnl` + `#quant-gross-sub`), Fees/Carry (`#header-fees`, `#header-carry`), and the live Equity Sparkline (`#equity-sparkline` + `#header-sparkline-equity`).
     * **Right Controls:** `#btn-audio-toggle`, `PnL Report` (`openDailyPnlModal()`), `Missed Setups` (`openMissedSetupsModal()`), `+ New Trade` (`openManualOrderModal()`), `#btn-reset-breaker`, and `#btn-flatten-all` (`openFlattenModal()`).

2. **Main 3-Column Pro Grid (`flex-1 grid grid-cols-12 gap-2.5 p-2.5 overflow-hidden min-h-0`):**
   - **Left Column (`col-span-3 flex flex-col gap-2.5 overflow-hidden min-h-0`):**
     a) **Compact Market Regime Card:** Shows `#regime-badge`, Breadth (`#regime-breadth`), Avg 24h (`#regime-avg-chg`), Avg Carry (`#regime-funding`), `#regime-sentiment`, and `#regime-tweaks-list`.
     b) **Strategy Heat & Allocation Matrix (`data.strategy_allocations`):** New compact grid card (`#strategy-heat-grid`) rendering each strategy's live Tier badge (`HOT 🔥`, `WARM ⚡`, `NORMAL ⚖️`, `COLD ❄️`, `LOCKED 🛑`), sizing Multiplier (`1.6x`), Win Rate %, Profit Factor, and Rolling Net PnL.
     c) **AI Prospector Top 10 Radar (`#prospects-list`, `flex-1 overflow-y-auto min-h-0`):**
        - Wire `triggerProspectScan()` to `await fetch('/api/scan', {method: 'POST'})` before showing toast.
        - Render active `anti_churn_status` cooldown pills (`⏳ COIN: Xs`) in `#prospects-quick-tags`.
        - Make clicking any prospect card call `selectSymbol(coin)` so the chart and L2 widget immediately load that coin.

   - **Center Column (`col-span-6 flex flex-col gap-2.5 overflow-hidden min-h-0`):**
     a) **TradingView Chart Deck (`h-[54%] flex flex-col bg-[#0f172a] border border-slate-800 rounded-xl p-3`):**
        - Keep symbol pills (`#chart-symbol-pills`), `#input-custom-symbol`, `#chart-funding-pill`, EMA/VWAP toggles (`#btn-toggle-ema`, `#btn-toggle-vwap`), timeframe buttons (`1m`–`1d`), legend (`#chart-active-symbol`, `#chart-active-price`, `#legend-ema20`, `#legend-ema50`, `#legend-vwap`, `#chart-pos-tag`), and `#tv-chart-container` (`flex-1 w-full`).
        - Upgrade `updateChartPositionLine(coin)` to cross-reference `(latestTradeManager.positions || [])` and draw **3 live horizontal price lines** on `candleSeries` (cleaning up previous lines first):
          1. `entryPriceLine`: Entry price (dashed `#10b981` for LONG, `#f43f5e` for SHORT).
          2. `stopLossPriceLine`: From `tracker.stop_price` (solid `#f59e0b` if `tracker.breakeven_triggered` or `tracker.trailing_stop_triggered` else `#f43f5e`, titled `🛡️ BE LOCKED` / `🎯 TRAIL STOP` / `SL`).
          3. `takeProfitPriceLine`: Computed from `tracker.take_profit_roi_pct` (`entry * (1 + tp/100)` for LONG, `entry * (1 - tp/100)` for SHORT, dashed `#10b981`, titled `🎯 TP TARGET`).
     b) **Tabbed Execution Blotter (`h-[46%] flex flex-col bg-[#0f172a] border border-slate-800 rounded-xl p-3 overflow-hidden min-h-0`):**
        - Tabs for **Active Positions (`#tab-btn-active`)**, **Closed History (`#tab-btn-closed`)**, and **Funding Arbitrage (`#tab-btn-arb`)**.
        - Upgrade Active Positions table (`#positions-table-body`) to merge `(data.trade_manager || {}).positions` and display:
          * `COIN` (clickable -> `selectSymbol(coin)`)
          * `STRATEGY` badge
          * `SIDE` & `SIZE`
          * `ENTRY → MARK`
          * `UNREALIZED P&L`
          * `ROI% (Peak)` (shows current ROI% + `Peak: +X.XX%` from `peak_roi_pct`)
          * `STOP / RATCHET` (shows `$stop_price` + badge: `🛡️ BE LOCKED`, `🎯 TRAILING`, or `⏳ Xs min hold` from `anti_churn_label`)
          * `HOLD` (formatted from `duration_seconds`, e.g. `12m 30s`)
          * `ACTION` (`[✕ Close]` button calling `closeTrade(coin)`)

   - **Right Column (`col-span-3 flex flex-col gap-2.5 overflow-hidden min-h-0`):**
     a) **Live L2 Orderbook Depth & Skew Card:** Shows `#l2-coin-name`, `#l2-bids-depth`, `#l2-asks-depth`, `#l2-bid-bar`, `#l2-ask-bar`, and `#l2-skew-label`.
     b) **10s AI Sentinel & Watchdog Stream (`#sentinel-stream-container`, `flex-1 overflow-y-auto min-h-0`):** Category filter pills (`ALL`, `[GROQ LPU]`, `[AUDIT]`, `[REGIME]`, `[RISK]`, `[MISSED]`) and live streaming thoughts.

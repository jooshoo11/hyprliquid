# Hyperliquid Autonomous AI Trading System & WezTerm Cockpit

Institutional-grade, autonomous multi-strategy algorithmic trading daemon and terminal cockpit built for the Hyperliquid L1 perpetuals exchange.

Powered by:
- **Nautilus Trader** event-driven execution core (SMC Trend Continuation, Hourly Funding Fade, Orderbook Scalper, VWAP/OI Momentum, Liquidation Absorber).
- **Sub-Second Groq LPU Sentinel** & **Google AI Studio Grounded Prospector** multi-agent risk guardians.
- **WezTerm Autonomous Cockpit**: High-performance dark terminal dashboard replacing the browser web cockpit, featuring real-time telemetry, L2 depth microstructure ladders, multi-agent thought streams, and 1-click execution.

---

## ⚡ Quick Start

### 1. Launch in Dedicated WezTerm Window
Launch the autonomous engine and dedicated WezTerm Cockpit window with a single command:
```bash
./launch_wezterm.sh
```
Or via `run.py`:
```bash
python run.py --wezterm
```

### 2. Launch Inline in Current Terminal
If already running inside WezTerm, tmux, or any terminal emulator:
```bash
python run.py
```
*(Runs by default in Paper Trading mode on live Mainnet data with the interactive cockpit)*.

### 3. Headless Daemon Mode
Run the engine in headless daemon mode in the background:
```bash
python run.py --headless
```
And open the WezTerm Cockpit at any time in another window:
```bash
./launch_wezterm.sh
```

### 4. Optional Legacy Web Cockpit
If you still need the FastAPI + WebSockets web server at `http://localhost:8000`:
```bash
python run.py --web
```

---

## 🖥️ WezTerm Cockpit Architecture

The WezTerm Cockpit (`wezterm.lua` + `src/tui/`) is configured with an institutional cyberpunk dark palette (`#080c14` background, `#38bdf8` cyan accents, `#10b981` emerald greens, and `#f43f5e` crimson alerts) and provides 5 interactive tabs:

### Tab `[1]` Active Positions
- **Real-Time Mark-to-Market**: Live mark prices, position sizes, unrealized P&L ($ and %), ROI %, and liquidation prices.
- **Circuit Guard Status**: Displays PortfolioGuard 20% drawdown circuit breaker and active exposure metrics.
- **Quick Actions**:
  - `[C]` Close selected position.
  - `[F]` 🚨 **EMERGENCY FLATTEN ALL** (Immediate market close of all open trades).
  - `[N]` Manual order entry dialog (Long/Short, size, SL, TP).
  - `[R]` Reset circuit breaker.

### Tab `[2]` AI Radar & Market Prospects
- **Deep LLM-Ranked Opportunities**: Top perpetual candidates ranked every 45s by liquidity, volume, carry, and microstructure momentum.
- **Conviction Scores & Targets**: 0–100 conviction score, suggested entry, take profit, stop loss, 24h volume, and funding APR.
- **AI Intelligence Dossier**: Selected coin inspector displaying Google AI Studio news catalyst verdicts, token unlock audits, and technical rationales.
- **Shortcuts**: `[Enter]` to execute selected prospect; `[S]` to trigger immediate market scan.

### Tab `[3]` Sentinel Thoughts Stream
- **Real-Time Multi-Agent Feed**: Live auto-scrolling telemetry from:
  - `[GROQ LPU]`: Sub-second LPU risk guardian auditing orderbook liquidity walls and margin safety.
  - `[AI STUDIO]`: Gemini 3 Flash catalyst assessments.
  - `[WATCHDOG]`: 10s position health checks and toxic order book spread alerts.
  - `[REGIME]`: Dynamic market regime classifications (BULL_MOMENTUM, RANGE_BOUND, etc.).
- Filter between agents via buttons or key shortcuts.

### Tab `[4]` Performance & Daily P&L
- **Executive Risk Cards**: Sharpe Ratio, Profit Factor, Win Rate %, Expectancy ($), Total Trades, Max Drawdown %, Avg Win / Avg Loss.
- **Daily P&L Report Viewer**: Real-time rendering of generated daily markdown reports.
- **Retrospective Decision Journal**: Audit trail of filtered trades and missed setups.

### Tab `[5]` L2 Depth & Orderbook
- **Orderbook Ladder**: Top 10 Asks (red) and Top 10 Bids (green) with real-time horizontal cumulative volume depth bars.
- **Microstructure Telemetry**: Mid price, bid-ask spread in bps, total bid/ask volume, and orderbook imbalance ratio.
- Quick coin switcher (`BTC`, `ETH`, `SOL`, `HYPE`, or custom ticker).

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Description |
| :--- | :--- |
| `1` | Switch to **Active Positions** tab |
| `2` | Switch to **AI Radar & Prospects** tab |
| `3` | Switch to **Sentinel Stream** tab |
| `4` | Switch to **Performance & P&L** tab |
| `5` | Switch to **L2 Depth & Orderbook** tab |
| `N` | Open **New Trade** order entry dialog |
| `C` | Close currently selected position |
| `F` | 🚨 **EMERGENCY FLATTEN ALL** open positions |
| `R` | Reset PortfolioGuard circuit breaker |
| `S` | Force immediate on-demand market prospector scan |
| `Q` | Cleanly exit Cockpit and stop autonomous engine |

### WezTerm Window Multiplexing Shortcuts
| Shortcut | Action |
| :--- | :--- |
| `Alt + V` | Split pane horizontally |
| `Alt + S` | Split pane vertically |
| `Alt + H / J / K / L` | Navigate between split panes |
| `Alt + Z` | Toggle pane zoom (full screen pane) |
| `Alt + W` | Close current pane |
| `F11` | Toggle fullscreen window |

---

## 📁 Repository Structure

```
├── wezterm.lua                  # Primary WezTerm configuration and dark cockpit theme
├── launch_wezterm.sh            # One-click WezTerm Cockpit launcher
├── run.py                       # Unified system entry point (Engine + WezTerm Cockpit)
├── src/
│   ├── tui/                     # WezTerm Terminal Cockpit package
│   │   ├── app.py               # Master Textual cockpit application
│   │   ├── data_provider.py     # Telemetry & command execution bus (in-memory + bridge)
│   │   └── widgets/             # Cockpit UI components
│   │       ├── header_bar.py    # Top status bar & sparkline metrics
│   │       ├── positions_view.py# Positions table & flatten controls
│   │       ├── prospects_view.py# AI Radar & catalyst inspector
│   │       ├── sentinel_view.py # Multi-agent thoughts log stream
│   │       ├── analytics_view.py# Risk cards, daily P&L & journal
│   │       ├── orderbook_view.py# L2 depth ladder & imbalance bars
│   │       └── modals.py        # Order entry, flatten & reset dialogs
│   ├── engine/                  # Unified autonomous trading engine
│   ├── strategies/              # Algorithmic trading strategies
│   ├── scanner/                 # MCP client & Polars market discovery
│   ├── risk/                    # PortfolioGuard & TradeManager
│   └── monitoring/              # Groq LPU sentinel daemon
├── bridge/                      # Shared telemetry JSON bridges
├── reports/                     # Trading reports and decision journals
└── tests/                       # Complete pytest suite (132 tests)
```

---

## 🧪 Testing

Run the automated test suite:
```bash
pytest tests/
```
Or test only the WezTerm cockpit components:
```bash
pytest tests/test_wezterm_dashboard.py
```
"""
Hyperliquid Multi-Strategy Portfolio Backtest Runner.
Loads the 20-perpetual Parquet Data Catalog, executes:
  - TrendContinuationSMC
  - HourlyFundingFade
  - OrderBookImbalance
  - VwapOiMomentum
Enforces PortfolioGuard risk rules:
  - Max 25% margin equity allocation per individual strategy
  - Max 4 simultaneous positions across the node
  - Anti-collision direction filtering
  - 2% 24h drawdown circuit breaker
Evaluates Hyperliquid maker/taker fee models (-0.01% / 0.035%),
and renders an interactive Rich performance comparison matrix across strategies.
Strictly zero pandas dependencies — powered entirely by Polars and PyArrow.
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import argparse
import math
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple

import polars as pl
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models.fee import MakerTakerFeeModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.strategies.continuation import TrendContinuationSMC, TrendContinuationConfig
from src.strategies.funding_fade import HourlyFundingFade, HourlyFundingFadeConfig
from src.strategies.orderbook_scalp import OrderBookImbalance, OrderBookImbalanceConfig
from src.strategies.vwap_momentum import VwapOiMomentum, VwapOiMomentumConfig
from src.risk.portfolio_guard import PortfolioGuard

console = Console()


def _parse_money_val(val: Any) -> float:
    """Safely extract float from Money objects or strings like '61.87 USD'."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    parts = s.split()
    if parts:
        try:
            return float(parts[0].replace(",", ""))
        except ValueError:
            pass
    return 0.0


def compute_performance_metrics(
    initial_capital: float,
    account_report: Optional[Any],
    fills_report: Optional[Any],
    positions_report: Optional[Any],
) -> Dict[str, Any]:
    """
    Calculate portfolio statistics using Polars: Return, Drawdown, Win Rate, Sharpe, and Sortino.
    Strictly zero pandas dependencies.
    """
    final_equity = initial_capital
    max_drawdown_pct = 0.0
    max_drawdown_usd = 0.0
    daily_returns: List[float] = []

    if account_report is not None:
        if isinstance(account_report, pl.DataFrame):
            acc_df = account_report
        elif hasattr(account_report, "to_dict"):
            acc_df = pl.DataFrame(account_report.to_dict(orient="list"))
        else:
            try:
                acc_df = pl.DataFrame(account_report)
            except Exception:
                acc_df = pl.DataFrame()

        if not acc_df.is_empty() and "total" in acc_df.columns:
            raw_vals = [_parse_money_val(v) for v in acc_df["total"].to_list()]
            eq_df = pl.DataFrame({"equity": raw_vals}).filter(pl.col("equity") > 0)
            if not eq_df.is_empty():
                final_equity = float(eq_df["equity"][-1])
                cum_df = eq_df.with_columns([
                    pl.col("equity").cum_max().alias("cummax"),
                ]).with_columns([
                    (pl.col("cummax") - pl.col("equity")).alias("drawdowns"),
                    ((pl.col("cummax") - pl.col("equity")) / pl.col("cummax")).alias("drawdown_pcts"),
                    pl.col("equity").pct_change().alias("pct_changes"),
                ])
                max_drawdown_usd = float(cum_df["drawdowns"].max() or 0.0)
                max_drawdown_pct = float(cum_df["drawdown_pcts"].max() or 0.0) * 100.0

                daily_returns = [
                    float(r) for r in cum_df["pct_changes"].drop_nulls().to_list()
                    if not math.isnan(r) and not math.isinf(r)
                ]

    net_profit = final_equity - initial_capital
    return_pct = (net_profit / initial_capital) * 100.0 if initial_capital > 0 else 0.0

    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    win_rate = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    profit_factor = 0.0

    if positions_report is not None:
        if isinstance(positions_report, pl.DataFrame):
            pos_df = positions_report
        elif hasattr(positions_report, "to_dict"):
            pos_df = pl.DataFrame(positions_report.to_dict(orient="list"), strict=False)
        else:
            try:
                pos_df = pl.DataFrame(positions_report)
            except Exception:
                pos_df = pl.DataFrame()

        if not pos_df.is_empty() and "realized_pnl" in pos_df.columns:
            total_trades = len(pos_df)
            pnls = [_parse_money_val(v) for v in pos_df["realized_pnl"].to_list()]
            for realized_pnl in pnls:
                if realized_pnl > 0:
                    winning_trades += 1
                    gross_profit += realized_pnl
                elif realized_pnl < 0:
                    losing_trades += 1
                    gross_loss += abs(realized_pnl)

        win_rate = (winning_trades / total_trades) * 100.0 if total_trades > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    elif fills_report is not None:
        total_trades = len(fills_report) // 2

    # Sharpe Ratio
    ANNUALISATION_FACTOR = math.sqrt(365 * 24 * 60 / 5)  # 5-minute bars, crypto
    if daily_returns and len(daily_returns) > 1:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
        sharpe_ratio = (mean_r / std_r) * ANNUALISATION_FACTOR if std_r > 0 else 0.0

        # Sortino Ratio (downside deviation)
        neg_returns = [r for r in daily_returns if r < 0]
        if len(neg_returns) > 1:
            downside_std = math.sqrt(sum(r ** 2 for r in neg_returns) / (len(neg_returns) - 1))
            sortino_ratio = (mean_r / downside_std) * ANNUALISATION_FACTOR if downside_std > 0 else 0.0
        else:
            sortino_ratio = sharpe_ratio
    else:
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        if total_trades > 0 and (gross_profit > 0 or gross_loss > 0):
            avg_return = net_profit / total_trades
            std_approx = (gross_profit + gross_loss) / total_trades
            sharpe_ratio = (avg_return / std_approx) * ANNUALISATION_FACTOR if std_approx > 0 else 0.0
            sortino_ratio = (avg_return / (gross_loss / total_trades)) * ANNUALISATION_FACTOR if gross_loss > 0 else sharpe_ratio

    total_fills = len(fills_report) if fills_report is not None else 0

    return {
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "net_profit": net_profit,
        "return_pct": return_pct,
        "total_fills": total_fills,
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "max_drawdown_usd": max_drawdown_usd,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
    }


def create_strategy_instances(
    strategy_names: List[str],
    portfolio_guard: Optional[PortfolioGuard],
    risk_pct: float = 0.01,
    rr_ratio: float = 2.5,
) -> List[Any]:
    """Instantiate requested strategies with configured risk and portfolio guard."""
    instances = []
    for name in strategy_names:
        if name == "continuation":
            c = TrendContinuationConfig(
                venue="HYPERLIQUID",
                risk_per_trade_pct=risk_pct,
                reward_to_risk_ratio=rr_ratio,
                max_active_positions=4,
            )
            instances.append(TrendContinuationSMC(config=c, portfolio_guard=portfolio_guard))
        elif name == "funding_fade":
            f = HourlyFundingFadeConfig(
                venue="HYPERLIQUID",
                risk_per_trade_pct=min(risk_pct, 0.0075),
                max_active_positions=3,
            )
            instances.append(HourlyFundingFade(config=f, portfolio_guard=portfolio_guard))
        elif name == "orderbook":
            o = OrderBookImbalanceConfig(
                venue="HYPERLIQUID",
                risk_per_trade_pct=min(risk_pct, 0.005),
                max_active_positions=3,
            )
            instances.append(OrderBookImbalance(config=o, portfolio_guard=portfolio_guard))
        elif name == "vwap":
            v = VwapOiMomentumConfig(
                venue="HYPERLIQUID",
                risk_per_trade_pct=risk_pct,
                max_active_positions=4,
            )
            instances.append(VwapOiMomentum(config=v, portfolio_guard=portfolio_guard))
    return instances


def run_single_simulation(
    catalog: ParquetDataCatalog,
    instruments: List[Any],
    sorted_bars: List[Bar],
    strategy_instances: List[Any],
    initial_capital: float = 10_000.0,
    trader_id: str = "HL-BACKTEST-001",
) -> Dict[str, Any]:
    """Execute simulation for a specific strategy set in an isolated Nautilus BacktestEngine."""
    engine_config = BacktestEngineConfig(
        trader_id=trader_id,
        logging=LoggingConfig(log_level="ERROR"),
    )
    engine = BacktestEngine(config=engine_config)

    venue = Venue("HYPERLIQUID")
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(initial_capital, USD)],
        fee_model=MakerTakerFeeModel(),
    )

    for instrument in instruments:
        engine.add_instrument(instrument)

    engine.add_data(sorted_bars)

    for strat in strategy_instances:
        engine.add_strategy(strat)

    engine.run()

    account_report = engine.trader.generate_account_report(venue)
    fills_report = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()

    metrics = compute_performance_metrics(
        initial_capital=initial_capital,
        account_report=account_report,
        fills_report=fills_report,
        positions_report=positions_report,
    )
    engine.dispose()
    return metrics


def run_isolated_worker(s_name, catalog_path, initial_capital, symbols, risk_pct, rr_ratio):
    catalog = ParquetDataCatalog(catalog_path)
    all_instruments = catalog.instruments()
    if symbols:
        instruments = [i for i in all_instruments if any(s.upper() in str(i.id) for s in symbols)]
    else:
        instruments = all_instruments
    raw_bars = catalog.bars(instrument_ids=[str(i.id) for i in instruments])
    sorted_bars = sorted(raw_bars, key=lambda b: (b.ts_event, str(b.bar_type)))
    
    iso_guard = PortfolioGuard()
    strats = create_strategy_instances([s_name], iso_guard, risk_pct, rr_ratio)
    metrics = run_single_simulation(catalog, instruments, sorted_bars, strats, initial_capital, f"HL-{s_name.upper()}")
    return s_name, metrics

def print_comparison_table(results: Dict[str, Dict[str, Any]], instrument_count: int, bar_count: int) -> None:
    """Renders a Rich performance comparison table across strategies."""
    console.print()
    header_text = Text(
        f"HYPERLIQUID MULTI-STRATEGY QUANTITATIVE NODE — PERFORMANCE MATRIX\n"
        f"Instruments Simulated: {instrument_count} | Event Stream Bars: {bar_count:,}",
        style="bold cyan",
        justify="center",
    )
    console.print(Panel(header_text, border_style="cyan"))

    table = Table(title="Strategy Comparison Summary", header_style="bold magenta", expand=True)
    table.add_column("Strategy / Engine", style="bold white", justify="left")
    table.add_column("Net Profit ($)", justify="right")
    table.add_column("Return (%)", justify="right")
    table.add_column("Sharpe", justify="right", style="cyan")
    table.add_column("Sortino", justify="right", style="green")
    table.add_column("Max DD (%)", justify="right", style="red")
    table.add_column("Win Rate", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Profit Factor", justify="right")

    for strat_key, m in results.items():
        ret_color = "green" if m["net_profit"] >= 0 else "red"
        sign = "+" if m["net_profit"] >= 0 else ""
        pnl_str = f"[{ret_color}]{sign}${m['net_profit']:,.2f}[/{ret_color}]"
        ret_str = f"[{ret_color}]{sign}{m['return_pct']:.2f}%[/{ret_color}]"
        dd_str = f"-{m['max_drawdown_pct']:.2f}%"
        win_str = f"{m['win_rate']:.1f}%"
        pf_str = f"{m['profit_factor']:.2f}" if m["profit_factor"] < 900 else "∞"
        trades_str = f"{m['total_trades']} ({m['winning_trades']}W/{m['losing_trades']}L)"

        table.add_row(
            strat_key.upper(),
            pnl_str,
            ret_str,
            f"{m['sharpe_ratio']:.2f}",
            f"{m['sortino_ratio']:.2f}",
            dd_str,
            win_str,
            trades_str,
            pf_str,
        )

    console.print(table)
    console.print(
        "[dim]Exchange Model: Hyperliquid DEX (-0.01% Maker Rebate / 0.035% Taker Fee) | "
        "Risk Engine: PortfolioGuard (Max 25% Margin / Max 4 Positions / 2% DD Circuit Breaker)[/dim]\n"
    )


def run_portfolio_backtest(
    strategy_mode: str = "all",
    catalog_path: str = "catalog",
    initial_capital: float = 10_000.0,
    symbols: Optional[List[str]] = None,
    risk_pct: float = 0.01,
    rr_ratio: float = 2.5,
) -> Dict[str, Dict[str, Any]]:
    """
    Run backtest simulation on top perpetuals loaded from Parquet catalog.
    Supports individual strategies or comprehensive multi-strategy comparison.
    """
    console.print(f"[bold cyan]🚀 Initializing NautilusTrader Backtest Engine on '{catalog_path}'...[/bold cyan]")
    catalog = ParquetDataCatalog(catalog_path)

    all_instruments = catalog.instruments()
    if symbols:
        instruments = [i for i in all_instruments if any(s.upper() in str(i.id) for s in symbols)]
    else:
        instruments = all_instruments

    if not instruments:
        raise ValueError(f"No instruments found in catalog at '{catalog_path}'. Run catalog_builder first.")

    console.print(f"[green]Loaded {len(instruments)} perpetual instruments from catalog.[/green]")

    instrument_ids = [str(i.id) for i in instruments]
    console.print(f"[yellow]Loading multi-timeframe bar data for {len(instrument_ids)} instruments...[/yellow]")
    raw_bars = catalog.bars(instrument_ids=instrument_ids)
    sorted_bars = sorted(raw_bars, key=lambda b: (b.ts_event, str(b.bar_type)))
    console.print(f"[green]Added {len(sorted_bars):,} bars into event simulation stream.[/green]")

    results: Dict[str, Dict[str, Any]] = {}
    guard = PortfolioGuard()

    valid_strategies = ["continuation", "funding_fade", "orderbook", "vwap"]

    if strategy_mode in valid_strategies:
        console.print(f"[bold yellow]⚡ Running isolated backtest for: {strategy_mode.upper()}...[/bold yellow]")
        strats = create_strategy_instances([strategy_mode], guard, risk_pct, rr_ratio)
        metrics = run_single_simulation(catalog, instruments, sorted_bars, strats, initial_capital, f"HL-{strategy_mode.upper()}")
        results[strategy_mode] = metrics
    elif strategy_mode == "all":
        import concurrent.futures
        import os
        # 1. Run each strategy in isolation for baseline in parallel
        console.print("[bold yellow]⚡ Running isolated strategies in parallel processes...[/bold yellow]")
        with concurrent.futures.ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as executor:
            futures = [
                executor.submit(run_isolated_worker, s, catalog_path, initial_capital, symbols, risk_pct, rr_ratio)
                for s in valid_strategies
            ]
            for fut in concurrent.futures.as_completed(futures):
                s_name, metrics = fut.result()
                results[s_name] = metrics
                console.print(f"[dim]Finished isolated: {s_name.upper()}[/dim]")

        # 2. Run combined portfolio with PortfolioGuard
        console.print(f"[bold yellow]⚡ Running unified multi-strategy portfolio under PortfolioGuard...[/bold yellow]")
        shared_guard = PortfolioGuard(max_strategy_equity_pct=0.25, max_total_open_positions=4, max_daily_drawdown_pct=0.02)
        combined_strats = create_strategy_instances(valid_strategies, shared_guard, risk_pct, rr_ratio)
        combined_metrics = run_single_simulation(catalog, instruments, sorted_bars, combined_strats, initial_capital, "HL-MULTI-STRAT")
        results["combined_portfolio"] = combined_metrics
    else:
        raise ValueError(f"Unknown strategy mode '{strategy_mode}'. Choose from: {valid_strategies + ['all']}")

    print_comparison_table(results, len(instruments), len(sorted_bars))
    return results


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid Multi-Strategy Portfolio Backtest")
    parser.add_argument("--strategy", type=str, default="all", choices=["continuation", "funding_fade", "orderbook", "vwap", "all"],
                        help="Strategy to run (continuation, funding_fade, orderbook, vwap, or all)")
    parser.add_argument("--catalog-dir", type=str, default="catalog", help="Path to Parquet catalog")
    parser.add_argument("--capital", type=float, default=10000.0, help="Initial USD margin equity")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols filter e.g. BTC,ETH,SOL")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="Risk percentage per trade")
    parser.add_argument("--rr-ratio", type=float, default=2.5, help="Reward-to-risk ratio for take profit")
    args = parser.parse_args()

    symbols_list = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    run_portfolio_backtest(
        strategy_mode=args.strategy,
        catalog_path=args.catalog_dir,
        initial_capital=args.capital,
        symbols=symbols_list,
        risk_pct=args.risk_pct,
        rr_ratio=args.rr_ratio,
    )


if __name__ == "__main__":
    main()

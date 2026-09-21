"""
Hyperliquid Multi-Strategy Optuna Parameter Optimizer Suite.
Leverages Polars for zero-copy lazy evaluation of historical Parquet catalog data.
Sweeps hyperparameters for:
  - TrendContinuationSMC (EMA periods, displacement multiplier, reward-to-risk ratio, risk pct)
  - HourlyFundingFade (funding APR threshold, trailing stop pct, risk pct)
Applies MedianPruner to drop underperforming trials.
Objective: Maximize Sortino Ratio with heavy penalty if Max Drawdown > 12%.
Saves optimal parameters to config/optimized_params.json.
Zero pandas dependencies.
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import glob
import math
import json
import argparse
import datetime
from typing import Dict, List, Optional, Any

import optuna
import polars as pl
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import logging
logging.getLogger("nautilus_trader").setLevel(logging.ERROR)

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models.fee import MakerTakerFeeModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.strategies.continuation import TrendContinuationSMC, TrendContinuationConfig
from src.strategies.funding_fade import HourlyFundingFade, HourlyFundingFadeConfig
from src.backtest.run_backtest import compute_performance_metrics

console = Console()


def scan_catalog_metadata_lazy(catalog_dir: str = "catalog") -> Dict[str, int]:
    """
    Scans historical Parquet bar files lazily using Polars to extract bar counts per instrument.
    """
    bar_dir = os.path.join(catalog_dir, "data", "bar")
    if not os.path.exists(bar_dir):
        return {}

    summary = {}
    for series_name in os.listdir(bar_dir):
        series_path = os.path.join(bar_dir, series_name)
        if not os.path.isdir(series_path):
            continue
        parquet_files = glob.glob(os.path.join(series_path, "*.parquet"))
        if parquet_files:
            lf = pl.scan_parquet(parquet_files)
            count = lf.select(pl.len()).collect().item()
            summary[series_name] = count

    return summary


class MultiStrategyOptimizer:
    """
    Optuna parameter optimizer for Hyperliquid algorithmic strategies.
    Evaluates trials on NautilusTrader BacktestEngine with Polars metric computations.
    """

    def __init__(
        self,
        strategy_target: str = "all",
        catalog_path: str = "catalog",
        initial_capital: float = 10_000.0,
        symbols: Optional[List[str]] = None,
    ):
        self.strategy_target = strategy_target.lower()
        self.catalog_path = catalog_path
        self.initial_capital = initial_capital
        self.symbols = symbols
        self.catalog = ParquetDataCatalog(catalog_path)

        # Pre-scan catalog using Polars
        self.catalog_stats = scan_catalog_metadata_lazy(catalog_path)

        all_instruments = self.catalog.instruments()
        if symbols:
            self.instruments = [i for i in all_instruments if any(s.upper() in str(i.id) for s in symbols)]
        else:
            self.instruments = all_instruments

        if not self.instruments:
            raise ValueError(f"No instruments found matching {symbols} in {catalog_path}")

        instrument_ids = [str(i.id) for i in self.instruments]
        raw_bars = self.catalog.bars(instrument_ids=instrument_ids)
        self.sorted_bars = sorted(raw_bars, key=lambda b: b.ts_event)

    def objective(self, trial: optuna.Trial) -> float:
        """
        Objective function evaluating candidate hyperparameters.
        Target: Maximize Sortino Ratio with heavy drawdown penalty if DD > 12%.
        """
        # Suggest parameters based on target strategy
        continuation_params = {}
        funding_params = {}

        if self.strategy_target in ("continuation", "all"):
            continuation_params = {
                "ema_fast_period": trial.suggest_int("continuation_ema_fast", 20, 100, step=10),
                "ema_slow_period": trial.suggest_int("continuation_ema_slow", 100, 300, step=25),
                "displacement_multiplier": trial.suggest_float("continuation_displacement", 1.1, 2.5, step=0.2),
                "reward_to_risk_ratio": trial.suggest_float("continuation_rr_ratio", 1.5, 3.5, step=0.5),
                "risk_per_trade_pct": trial.suggest_float("continuation_risk_pct", 0.005, 0.02, step=0.005),
            }

        if self.strategy_target in ("funding_fade", "all"):
            funding_params = {
                "min_funding_apr_threshold": trial.suggest_float("funding_apr_threshold", 0.50, 1.50, step=0.10),
                "trailing_stop_pct": trial.suggest_float("funding_trailing_stop", 0.008, 0.020, step=0.002),
                "risk_per_trade_pct": trial.suggest_float("funding_risk_pct", 0.005, 0.015, step=0.0025),
            }

        # Build Backtest Engine
        engine_config = BacktestEngineConfig(
            trader_id=f"OPT-TRIAL-{trial.number}",
            logging=LoggingConfig(log_level="ERROR"),
        )
        engine = BacktestEngine(config=engine_config)

        venue = Venue("HYPERLIQUID")
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(self.initial_capital, USD)],
            fee_model=MakerTakerFeeModel(),
        )

        for instrument in self.instruments:
            engine.add_instrument(instrument)

        engine.add_data(self.sorted_bars)

        # Attach Strategies
        if self.strategy_target in ("continuation", "all"):
            cont_config = TrendContinuationConfig(
                venue="HYPERLIQUID",
                ema_fast_period=continuation_params["ema_fast_period"],
                ema_slow_period=continuation_params["ema_slow_period"],
                displacement_multiplier=continuation_params["displacement_multiplier"],
                reward_to_risk_ratio=continuation_params["reward_to_risk_ratio"],
                risk_per_trade_pct=continuation_params["risk_per_trade_pct"],
                max_active_positions=4,
            )
            strat_cont = TrendContinuationSMC(config=cont_config)
            engine.add_strategy(strat_cont)

        if self.strategy_target in ("funding_fade", "all"):
            fade_config = HourlyFundingFadeConfig(
                venue="HYPERLIQUID",
                min_funding_apr_threshold=funding_params["min_funding_apr_threshold"],
                trailing_stop_pct=funding_params["trailing_stop_pct"],
                risk_per_trade_pct=funding_params["risk_per_trade_pct"],
                max_active_positions=3,
            )
            strat_fade = HourlyFundingFade(config=fade_config)
            engine.add_strategy(strat_fade)

        try:
            engine.run()

            account_report = engine.trader.generate_account_report(venue)
            positions_report = engine.trader.generate_positions_report()
            fills_report = engine.trader.generate_order_fills_report()

            metrics = compute_performance_metrics(
                initial_capital=self.initial_capital,
                account_report=account_report,
                fills_report=fills_report,
                positions_report=positions_report,
            )

            sortino = metrics["sortino_ratio"]
            sharpe = metrics["sharpe_ratio"]
            max_dd_pct = metrics["max_drawdown_pct"]
            net_profit = metrics["net_profit"]
            total_trades = metrics["total_trades"]

            # Base score from Sortino or Sharpe ratio
            if sortino > 0:
                base_score = sortino
            elif sharpe > 0:
                base_score = sharpe
            else:
                base_score = net_profit / 100.0

            # Heavy penalty if Max Drawdown > 12%
            if max_dd_pct > 12.0:
                penalty = (max_dd_pct - 12.0) * 100.0
                score = base_score - penalty - 1000.0
            else:
                score = base_score

            # Report trial progress to Optuna pruner
            trial.report(score, step=1)
            if trial.should_prune():
                raise optuna.TrialPruned()

            return score

        except optuna.TrialPruned:
            raise
        except Exception as exc:
            console.print(f"[red]Trial {trial.number} failed: {exc}[/red]")
            return -9999.0
        finally:
            engine.dispose()

    def run(self, n_trials: int = 15, n_jobs: int = 1, out_path: str = "config/optimized_params.json") -> Dict[str, Any]:
        """
        Runs the Optuna optimization study with MedianPruner and exports parameters to JSON.
        """
        console.print(Panel(
            f"[bold cyan]OPTUNA MULTI-STRATEGY PARAMETER OPTIMIZATION SUITE[/bold cyan]\n"
            f"Strategy Target: [bold white]{self.strategy_target.upper()}[/bold white] | "
            f"Trials: {n_trials} | Parallel Jobs: {n_jobs} | Instruments: {len(self.instruments)}",
            border_style="cyan",
        ))

        pruner = optuna.pruners.MedianPruner(
            n_startup_trials=2,
            n_warmup_steps=1,
            interval_steps=1,
        )

        study = optuna.create_study(
            direction="maximize",
            pruner=pruner,
            study_name=f"hyperliquid_opt_{self.strategy_target}",
        )

        study.optimize(self.objective, n_trials=n_trials, n_jobs=n_jobs)

        console.print("\n[bold green]🏆 Optimization Study Completed![/bold green]")
        best_trial = study.best_trial
        console.print(f"[bold yellow]Best Score: {best_trial.value:.4f} (Trial #{best_trial.number})[/bold yellow]")

        # Format parameters table
        table = Table(title="Optimized Hyperparameters", header_style="bold magenta", expand=True)
        table.add_column("Parameter Name", style="cyan")
        table.add_column("Optimal Value", style="bold green", justify="right")

        for param_name, param_val in best_trial.params.items():
            table.add_row(param_name, str(param_val))

        console.print(table)

        # Structure output payload
        result_payload: Dict[str, Any] = {
            "metadata": {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "strategy_target": self.strategy_target,
                "best_trial_number": best_trial.number,
                "best_score": best_trial.value,
                "instruments": [str(i.id) for i in self.instruments],
            },
            "parameters": best_trial.params,
        }

        # Ensure directory exists and write JSON
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result_payload, f, indent=2)

        console.print(f"[bold green]Saved optimal parameters to '{out_path}'[/bold green]")
        return result_payload


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid Optuna Strategy Optimizer Suite")
    parser.add_argument("--strategy", type=str, default="all", choices=["continuation", "funding_fade", "all"],
                        help="Strategy to optimize (continuation, funding_fade, or all)")
    parser.add_argument("--catalog-dir", type=str, default="catalog", help="Path to Parquet data catalog")
    parser.add_argument("--trials", type=int, default=10, help="Number of Optuna trials to evaluate")
    parser.add_argument("--jobs", type=int, default=1, help="Number of concurrent worker threads")
    parser.add_argument("--symbols", type=str, default="SOL,ETH,BTC", help="Comma-separated symbols to optimize on")
    parser.add_argument("--out", type=str, default="config/optimized_params.json", help="Path to write JSON output")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    optimizer = MultiStrategyOptimizer(
        strategy_target=args.strategy,
        catalog_path=args.catalog_dir,
        symbols=symbols,
    )
    optimizer.run(n_trials=args.trials, n_jobs=args.jobs, out_path=args.out)


if __name__ == "__main__":
    main()

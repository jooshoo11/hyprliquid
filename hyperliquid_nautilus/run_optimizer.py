import optuna
import logging
import os
from run_backtest import run_vwap_backtest

logging.getLogger("nautilus_trader").setLevel(logging.ERROR)

def objective(trial):
    params = {
        "default_sl_pct": trial.suggest_float("default_sl_pct", 0.005, 0.02, step=0.001),
        "default_tp_pct": trial.suggest_float("default_tp_pct", 0.010, 0.05, step=0.001),
        "imbalance_threshold": trial.suggest_float("imbalance_threshold", 0.55, 0.80, step=0.01)
    }
    
    try:
        engine = run_vwap_backtest(params)
        
        from nautilus_trader.model.identifiers import Venue
        report = engine.trader.generate_account_report(Venue("HYPERLIQUID"))
        if report is not None and not report.empty:
            final_equity = float(report["total"].iloc[-1])
            net_profit = final_equity - 100.0
            return net_profit
        else:
            return 0.0
    except Exception as e:
        print(f"Trial failed: {e}")
        return 0.0

def main():
    print("Starting Multi-Core Optuna Optimizer...")
    
    study = optuna.create_study(direction="maximize")
    
    # n_jobs=4 runs parallel workers based on CPU core count
    study.optimize(objective, n_trials=4, n_jobs=4)
    
    print("\nOptimization Finished.")
    print("Best Trial:")
    trial = study.best_trial
    
    print(f"  Net Profit: {trial.value:.2f}")
    print("  Best Parameters:")
    for key, value in trial.params.items():
        print(f"    {key}: {value}")

if __name__ == "__main__":
    main()

import re

path = "src/backtest/run_backtest.py"
with open(path, "r") as f:
    code = f.read()

worker_func = """
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

def print_comparison_table
"""

code = code.replace("def print_comparison_table", worker_func.strip() + "\n\ndef print_comparison_table")

original_all_block = """
        # 1. Run each strategy in isolation for baseline
        for s_name in valid_strategies:
            console.print(f"[dim]Simulating isolated: {s_name.upper()}...[/dim]")
            iso_guard = PortfolioGuard()
            strats = create_strategy_instances([s_name], iso_guard, risk_pct, rr_ratio)
            metrics = run_single_simulation(catalog, instruments, sorted_bars, strats, initial_capital, f"HL-{s_name.upper()}")
            results[s_name] = metrics
"""

new_all_block = """
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
"""

code = code.replace(original_all_block.strip(), new_all_block.strip())

with open(path, "w") as f:
    f.write(code)

print("patched")

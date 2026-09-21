import os
import argparse
import pandas as pd
import vectorbt as vbt
from rich.console import Console
from rich.table import Table
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.data import BarType

console = Console()

def load_catalog_data(catalog_path: str = "catalog") -> pd.DataFrame:
    console.print(f"Loading 4H bars from Nautilus Catalog at '{catalog_path}'...")
    catalog = ParquetDataCatalog(catalog_path)
    instruments = catalog.instruments()
    
    data = []
    for inst in instruments:
        bar_type = BarType.from_str(f"{inst.id}-4-HOUR-LAST-EXTERNAL")
        try:
            bars = catalog.bars([bar_type])
            for b in bars:
                data.append({
                    "coin": inst.id.symbol.value.replace("-USD-PERP", ""),
                    "ts": pd.to_datetime(b.ts_event, unit='ns'),
                    "close": b.close.as_double()
                })
        except Exception:
            continue
            
    if not data:
        raise ValueError("No 4H bar data found in catalog.")
        
    df = pd.DataFrame(data)
    price_df = df.pivot(index='ts', columns='coin', values='close')
    price_df = price_df.ffill().dropna()
    return price_df

def run_screener(price_df: pd.DataFrame, ema_window: int = 50):
    console.print(f"Running VectorBT EMA({ema_window}) macro trend filter across {price_df.shape[1]} coins...")
    
    fast_ma = vbt.MA.run(price_df, ema_window, short_name='fast')
    
    entries = price_df.vbt.crossed_above(fast_ma.ma)
    exits = price_df.vbt.crossed_below(fast_ma.ma)
    
    portfolio = vbt.Portfolio.from_signals(
        price_df,
        entries,
        exits,
        freq='4h',
        init_cash=1000,
        fees=0.00035 
    )
    
    stats = []
    for coin in price_df.columns:
        pf = portfolio.loc[:, coin]
        wr_series = pf.trades.win_rate()
        sr_series = pf.sharpe_ratio()
        tr_series = pf.total_return()
        cnt_series = pf.trades.count()
        
        win_rate = float(wr_series.iloc[0]) if not pd.isna(wr_series.iloc[0]) else 0.0
        sharpe = float(sr_series.iloc[0]) if not pd.isna(sr_series.iloc[0]) else 0.0
        total_return = float(tr_series.iloc[0]) * 100.0
        trades_cnt = int(cnt_series.iloc[0])
        
        stats.append({
            "Coin": coin,
            "Total Return (%)": f"{total_return:.2f}%",
            "Win Rate (%)": f"{win_rate * 100:.2f}%",
            "Sharpe Ratio": f"{sharpe:.2f}",
            "Trades": trades_cnt
        })
        
    stats.sort(key=lambda x: float(x["Sharpe Ratio"]), reverse=True)
    
    table = Table(title=f"VectorBT 4H EMA({ema_window}) Trend Filter Baseline", header_style="bold magenta")
    table.add_column("Coin", style="cyan")
    table.add_column("Total Return (%)", justify="right", style="green")
    table.add_column("Win Rate (%)", justify="right", style="yellow")
    table.add_column("Sharpe Ratio", justify="right", style="bold white")
    table.add_column("Trades", justify="right", style="dim")
    
    for stat in stats:
        table.add_row(stat["Coin"], stat["Total Return (%)"], stat["Win Rate (%)"], stat["Sharpe Ratio"], str(stat["Trades"]))
        
    console.print(table)
    
    ov_sharpe = float(portfolio.sharpe_ratio()) if isinstance(portfolio.sharpe_ratio(), float) else float(portfolio.sharpe_ratio().iloc[0]) if not portfolio.sharpe_ratio().empty else 0.0
    ov_win_rate = float(portfolio.trades.win_rate()) if isinstance(portfolio.trades.win_rate(), float) else float(portfolio.trades.win_rate().iloc[0]) if not portfolio.trades.win_rate().empty else 0.0
    print(f"\nOverall Portfolio Sharpe Ratio: {ov_sharpe:.2f}")
    print(f"Overall Win Rate: {ov_win_rate * 100:.2f}%")
    
    return portfolio

if __name__ == "__main__":
    price_df = load_catalog_data("catalog")
    run_screener(price_df, 50)

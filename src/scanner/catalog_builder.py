"""
Dynamic Scanner & Historical Parquet Data Catalog Ingestion using Polars.
Scans Hyperliquid perp universe, ranks the top 20 perpetuals by 24h volume & volatility,
downloads historical 4H, 30M, and 5M candles, processes candle features and displacement
via Polars expressions, and writes them into Nautilus ParquetDataCatalog using df.to_arrow().
"""

import os
import sys
import time
import requests
import pyarrow.parquet as pq
import argparse
from decimal import Decimal
from typing import List, Dict, Any, Optional

import polars as pl
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.utils.instruments import get_hyperliquid_perp, get_bar_type
from src.scanner.mcp_client import HyperliquidInfoClient, compute_candle_features

console = Console()


def process_candles_with_polars(candles: List[Dict[str, Any]]) -> pl.DataFrame:
    """
    Transforms raw candle records into a Polars DataFrame with displacement,
    True Range, and normalized float64 types, sorted chronologically.

    Parameters
    ----------
    candles : list[dict]
        Raw candle records from Hyperliquid ('T'/'t', 'o', 'h', 'l', 'c', 'v').

    Returns
    -------
    pl.DataFrame
        Polars DataFrame with enforced float64 types, sorted chronologically by ts_event.
    """
    if not candles:
        return pl.DataFrame()

    df = pl.DataFrame({
        "ts_event": [int(c.get("T") or c.get("t") or 0) * 1_000_000 for c in candles],
        "open": [float(c["o"]) for c in candles],
        "high": [float(c["h"]) for c in candles],
        "low": [float(c["l"]) for c in candles],
        "close": [float(c["c"]) for c in candles],
        "volume": [float(c["v"]) for c in candles],
    }, schema={
        "ts_event": pl.Int64,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
    }).unique(subset=["ts_event"]).sort("ts_event")

    # Polars expressions for displacement candles (close - open)
    df = df.with_columns([
        (pl.col("close") - pl.col("open")).alias("displacement"),
        (pl.col("close") - pl.col("open")).abs().alias("abs_displacement"),
        pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - pl.col("close").shift(1)).abs(),
            (pl.col("low") - pl.col("close").shift(1)).abs(),
        ).fill_null(pl.col("high") - pl.col("low")).alias("tr"),
    ])

    return df


class CatalogBuilder:
    """
    Scans the Hyperliquid universe and ingests multi-timeframe candle data
    into a NautilusTrader ParquetDataCatalog using Polars and Arrow.
    """

    def __init__(
        self,
        catalog_path: str = "catalog",
        top_n: int = 30,
        network: str = "mainnet",
        lookback_days_4h: int = 45,
        lookback_days_30m: int = 45,
        lookback_days_5m: int = 30,
    ):
        self.catalog_path = catalog_path
        self.top_n = top_n
        self.network = network
        self.lookback_days_4h = lookback_days_4h
        self.lookback_days_30m = lookback_days_30m
        self.lookback_days_5m = lookback_days_5m
        self.client = HyperliquidInfoClient(network=network)

    def scan_universe(self) -> List[Dict[str, Any]]:
        """
        Query top perpetuals ranked by 24h volume and volatility using Polars.
        """
        console.print(
            f"[bold cyan]🔍 Scanning Hyperliquid ({self.network}) for Top {self.top_n} Perpetuals via Polars Engine...[/bold cyan]"
        )
        top_markets = self.client.get_top_perpetuals(top_n=self.top_n)

        # Render Rich Table
        table = Table(
            title=f"Hyperliquid Top {len(top_markets)} Perpetuals (Ranked by Volume & Volatility)",
            header_style="bold magenta",
        )
        table.add_column("Rank", justify="center", style="bold yellow")
        table.add_column("Symbol", justify="left", style="bold green")
        table.add_column("24h Volume (USD)", justify="right", style="cyan")
        table.add_column("Ann. Volatility", justify="right", style="bold red")
        table.add_column("Oracle Price", justify="right", style="white")
        table.add_column("Size Decimals", justify="center", style="dim")

        for idx, market in enumerate(top_markets, start=1):
            table.add_row(
                str(idx),
                market["name"],
                f"${market['volume_24h']:,.0f}",
                f"{market.get('volatility', 0.0):.2%}",
                f"${market['oracle_price']:,.4f}",
                str(market["szDecimals"]),
            )

        console.print(table)
        return top_markets

    def fetch_funding_history(self, coin: str, start_time_ms: int, end_time_ms: int) -> pl.DataFrame:
        url = "https://api.hyperliquid.xyz/info"
        payload = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_time_ms
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not data:
                return pl.DataFrame()
            
            df = pl.DataFrame(data)
            
            if "fundingRate" in df.columns:
                df = df.with_columns([
                    pl.col("fundingRate").cast(pl.Float64),
                    pl.col("premium").cast(pl.Float64),
                    pl.col("time").cast(pl.Int64)
                ])
                df = df.filter(pl.col("time") <= end_time_ms)
            if "coin" not in df.columns:
                df = df.with_columns(pl.lit(coin).alias("coin"))
            return df
        except Exception as exc:
            console.print(f"[bold red]Warning: Failed fetching funding history for {coin}: {exc}[/bold red]")
            return pl.DataFrame()

    def build_funding_catalog(self, top_markets: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Download historical funding history for top instruments and write
        partitioned Parquet files into catalog/funding/ with deduplication.
        """
        if top_markets is None:
            top_markets = self.scan_universe()

        funding_dir = os.path.join(self.catalog_path, "funding")
        os.makedirs(funding_dir, exist_ok=True)

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (self.lookback_days_4h * 24 * 3600 * 1000)

        console.print(
            f"\n[bold green]📦 Ingesting Funding History for {len(top_markets)} instruments into '{funding_dir}'...[/bold green]"
        )

        for idx, market in enumerate(top_markets, start=1):
            coin = market["name"]
            console.print(f"[{idx}/{len(top_markets)}] Fetching funding history for {coin}...")
            funding_df = self.fetch_funding_history(coin, start_ms, now_ms)
            if not funding_df.is_empty():
                arrow_table = funding_df.to_arrow()
                pq.write_to_dataset(
                    arrow_table,
                    root_path=funding_dir,
                    partition_cols=["coin"],
                    existing_data_behavior="delete_matching",
                )
            time.sleep(0.05)

        console.print("[bold green]✅ Funding catalog ingestion complete![/bold green]")

    def build_catalog(self, top_markets: Optional[List[Dict[str, Any]]] = None) -> ParquetDataCatalog:
        """
        Download historical 5M, 30M, and 4H bars for top instruments,
        process displacement features with Polars, convert via df.to_arrow(),
        and write to Nautilus ParquetDataCatalog.
        """
        if top_markets is None:
            top_markets = self.scan_universe()

        os.makedirs(self.catalog_path, exist_ok=True)
        catalog = ParquetDataCatalog(self.catalog_path)

        funding_dir = os.path.join(self.catalog_path, "funding")
        os.makedirs(funding_dir, exist_ok=True)

        now_ms = int(time.time() * 1000)
        start_4h_ms = now_ms - (self.lookback_days_4h * 24 * 3600 * 1000)
        start_30m_ms = now_ms - (self.lookback_days_30m * 24 * 3600 * 1000)
        start_5m_ms = now_ms - (self.lookback_days_5m * 24 * 3600 * 1000)

        console.print(
            f"\n[bold green]📦 Ingesting Multi-Timeframe Bars into Parquet Data Catalog at '{self.catalog_path}'...[/bold green]"
        )

        total_bars_ingested = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"[yellow]Downloading and cataloging {len(top_markets)} instruments...",
                total=len(top_markets),
            )

            for market in top_markets:
                coin = market["name"]
                sz_decimals = market["szDecimals"]

                instrument = get_hyperliquid_perp(coin=coin, sz_decimals=sz_decimals)
                # Persist instrument definition
                catalog.write_data([instrument])

                console.print(f"Fetching funding history for {coin}...")
                funding_df = self.fetch_funding_history(coin, start_4h_ms, now_ms)
                if not funding_df.is_empty():
                    arrow_table = funding_df.to_arrow()
                    pq.write_to_dataset(
                        arrow_table,
                        root_path=funding_dir,
                        partition_cols=["coin"],
                        existing_data_behavior="delete_matching",
                    )

                instrument_bars: List[Bar] = []

                # Timeframes to ingest: (interval_str, nautilus_tf, start_ms)
                timeframe_configs = [
                    ("4h", "4h", start_4h_ms),
                    ("30m", "30m", start_30m_ms),
                    ("5m", "5m", start_5m_ms),
                ]

                for hl_interval, tf_key, start_ms in timeframe_configs:
                    bar_type = get_bar_type(coin, tf_key)
                    try:
                        candles = self.client.get_historical_klines(
                            coin=coin,
                            interval=hl_interval,
                            start_time_ms=start_ms,
                            end_time_ms=now_ms,
                        )
                        if not candles:
                            continue

                        # 1. Calculate features and displacement with Polars expressions
                        df = process_candles_with_polars(candles)

                        # 2. Use df.to_arrow() when interfacing directly with NautilusTrader's Parquet catalog
                        arrow_table = df.to_arrow()
                        pydict = arrow_table.to_pydict()

                        ts_events = pydict["ts_event"]
                        opens = pydict["open"]
                        highs = pydict["high"]
                        lows = pydict["low"]
                        closes = pydict["close"]
                        volumes = pydict["volume"]

                        for i in range(arrow_table.num_rows):
                            ts_event = int(ts_events[i])
                            bar = Bar(
                                bar_type=bar_type,
                                open=instrument.make_price(Decimal(str(opens[i]))),
                                high=instrument.make_price(Decimal(str(highs[i]))),
                                low=instrument.make_price(Decimal(str(lows[i]))),
                                close=instrument.make_price(Decimal(str(closes[i]))),
                                volume=instrument.make_qty(Decimal(str(volumes[i]))),
                                ts_event=ts_event,
                                ts_init=ts_event,
                            )
                            instrument_bars.append(bar)
                    except Exception as exc:
                        console.print(
                            f"[bold red]Warning: Failed fetching {hl_interval} for {coin}: {exc}[/bold red]"
                        )

                if instrument_bars:
                    catalog.write_data(instrument_bars)
                    total_bars_ingested += len(instrument_bars)

                progress.update(task, advance=1)
                # Gentle rate-limit pause between symbols
                time.sleep(0.05)

        console.print(
            f"[bold green]✅ Catalog ingestion complete! Total bars written: {total_bars_ingested:,}[/bold green]"
        )
        return catalog


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid Top-30 Catalog Builder with Polars Engine")
    parser.add_argument("--catalog-dir", type=str, default="catalog", help="Directory path for Parquet catalog")
    parser.add_argument("--top-n", type=int, default=30, help="Number of top perpetuals to ingest")
    parser.add_argument("--network", type=str, default="mainnet", choices=["mainnet", "testnet"], help="Hyperliquid network")
    parser.add_argument("--days-4h", type=int, default=45, help="Days lookback for 4H bars")
    parser.add_argument("--days-30m", type=int, default=20, help="Days lookback for 30M bars")
    parser.add_argument("--days-5m", type=int, default=7, help="Days lookback for 5M bars")
    parser.add_argument("--funding-only", action="store_true", help="Only ingest funding history without downloading full bar history")
    args = parser.parse_args()

    builder = CatalogBuilder(
        catalog_path=args.catalog_dir,
        top_n=args.top_n,
        network=args.network,
        lookback_days_4h=args.days_4h,
        lookback_days_30m=args.days_30m,
        lookback_days_5m=args.days_5m,
    )
    if args.funding_only:
        builder.build_funding_catalog()
    else:
        builder.build_catalog()


if __name__ == "__main__":
    main()

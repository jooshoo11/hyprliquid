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
        top_n: int = 50,
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

                # Ingest 4H bars directly from REST API
                bar_type_4h = get_bar_type(coin, "4h")
                try:
                    candles_4h = self.client.get_historical_klines(
                        coin=coin,
                        interval="4h",
                        start_time_ms=start_4h_ms,
                        end_time_ms=now_ms,
                    )
                    if candles_4h:
                        df_4h = process_candles_with_polars(candles_4h)
                        arrow_4h = df_4h.to_arrow()
                        pydict_4h = arrow_4h.to_pydict()
                        for i in range(arrow_4h.num_rows):
                            ts_event = int(pydict_4h["ts_event"][i])
                            bar = Bar(
                                bar_type=bar_type_4h,
                                open=instrument.make_price(Decimal(str(pydict_4h["open"][i]))),
                                high=instrument.make_price(Decimal(str(pydict_4h["high"][i]))),
                                low=instrument.make_price(Decimal(str(pydict_4h["low"][i]))),
                                close=instrument.make_price(Decimal(str(pydict_4h["close"][i]))),
                                volume=instrument.make_qty(Decimal(str(pydict_4h["volume"][i]))),
                                ts_event=ts_event,
                                ts_init=ts_event,
                            )
                            instrument_bars.append(bar)
                except Exception as exc:
                    console.print(f"[bold red]Warning: Failed fetching 4h for {coin}: {exc}[/bold red]")

                # Ingest 5M bars directly from REST API, then resample 15M and 30M locally
                bar_type_5m = get_bar_type(coin, "5m")
                bar_type_15m = get_bar_type(coin, "15m")
                bar_type_30m = get_bar_type(coin, "30m")

                try:
                    candles_5m = self.client.get_historical_klines(
                        coin=coin,
                        interval="5m",
                        start_time_ms=start_5m_ms,
                        end_time_ms=now_ms,
                    )
                    if candles_5m:
                        df_5m = process_candles_with_polars(candles_5m)
                        if not df_5m.is_empty():
                            # 1. 5M Bars
                            arrow_5m = df_5m.to_arrow()
                            pydict_5m = arrow_5m.to_pydict()
                            for i in range(arrow_5m.num_rows):
                                ts_event = int(pydict_5m["ts_event"][i])
                                bar = Bar(
                                    bar_type=bar_type_5m,
                                    open=instrument.make_price(Decimal(str(pydict_5m["open"][i]))),
                                    high=instrument.make_price(Decimal(str(pydict_5m["high"][i]))),
                                    low=instrument.make_price(Decimal(str(pydict_5m["low"][i]))),
                                    close=instrument.make_price(Decimal(str(pydict_5m["close"][i]))),
                                    volume=instrument.make_qty(Decimal(str(pydict_5m["volume"][i]))),
                                    ts_event=ts_event,
                                    ts_init=ts_event,
                                )
                                instrument_bars.append(bar)

                            # 2. Local Polars Resample: 5m -> 15m
                            # 15 mins in nanoseconds = 15 * 60 * 1_000_000_000
                            ns_15m = 15 * 60 * 1_000_000_000
                            df_15m = (
                                df_5m.with_columns(
                                    ((pl.col("ts_event") // ns_15m) * ns_15m).alias("bucket")
                                )
                                .group_by("bucket")
                                .agg([
                                    pl.first("open").alias("open"),
                                    pl.max("high").alias("high"),
                                    pl.min("low").alias("low"),
                                    pl.last("close").alias("close"),
                                    pl.sum("volume").alias("volume"),
                                ])
                                .sort("bucket")
                            )
                            arrow_15m = df_15m.to_arrow()
                            pydict_15m = arrow_15m.to_pydict()
                            for i in range(arrow_15m.num_rows):
                                ts_event = int(pydict_15m["bucket"][i])
                                bar = Bar(
                                    bar_type=bar_type_15m,
                                    open=instrument.make_price(Decimal(str(pydict_15m["open"][i]))),
                                    high=instrument.make_price(Decimal(str(pydict_15m["high"][i]))),
                                    low=instrument.make_price(Decimal(str(pydict_15m["low"][i]))),
                                    close=instrument.make_price(Decimal(str(pydict_15m["close"][i]))),
                                    volume=instrument.make_qty(Decimal(str(pydict_15m["volume"][i]))),
                                    ts_event=ts_event,
                                    ts_init=ts_event,
                                )
                                instrument_bars.append(bar)

                            # 3. Local Polars Resample: 5m -> 30m
                            # 30 mins in nanoseconds = 30 * 60 * 1_000_000_000
                            ns_30m = 30 * 60 * 1_000_000_000
                            df_30m = (
                                df_5m.with_columns(
                                    ((pl.col("ts_event") // ns_30m) * ns_30m).alias("bucket")
                                )
                                .group_by("bucket")
                                .agg([
                                    pl.first("open").alias("open"),
                                    pl.max("high").alias("high"),
                                    pl.min("low").alias("low"),
                                    pl.last("close").alias("close"),
                                    pl.sum("volume").alias("volume"),
                                ])
                                .sort("bucket")
                            )
                            arrow_30m = df_30m.to_arrow()
                            pydict_30m = arrow_30m.to_pydict()
                            for i in range(arrow_30m.num_rows):
                                ts_event = int(pydict_30m["bucket"][i])
                                bar = Bar(
                                    bar_type=bar_type_30m,
                                    open=instrument.make_price(Decimal(str(pydict_30m["open"][i]))),
                                    high=instrument.make_price(Decimal(str(pydict_30m["high"][i]))),
                                    low=instrument.make_price(Decimal(str(pydict_30m["low"][i]))),
                                    close=instrument.make_price(Decimal(str(pydict_30m["close"][i]))),
                                    volume=instrument.make_qty(Decimal(str(pydict_30m["volume"][i]))),
                                    ts_event=ts_event,
                                    ts_init=ts_event,
                                )
                                instrument_bars.append(bar)
                except Exception as exc:
                    console.print(f"[bold red]Warning: Failed fetching/resampling 5m for {coin}: {exc}[/bold red]")

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
    parser.add_argument("--top-n", type=int, default=50, help="Number of top perpetuals to ingest")
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

"""
Unit tests for Polars Dynamic Scanner and Parquet Catalog Ingestion Pipeline.
Verifies:
  - Zero-pandas compliance across data transformations.
  - Polars LazyFrame ATR and volatility computations.
  - Schema normalization: float64 and int64 conversions.
  - Timestamp conversion to nanoseconds.
  - Arrow conversion via df.to_arrow().
"""

import polars as pl
import pyarrow as pa
import pytest

from src.scanner.mcp_client import compute_candle_features


def test_compute_candle_features_polars():
    # Synthetic candle data with 20 bars
    raw_candles = []
    base_px = 100.0
    for i in range(20):
        raw_candles.append({
            "t": 1_700_000_000_000 + (i * 300_000),  # millisecond timestamps
            "T": 1_700_000_000_000 + ((i + 1) * 300_000),
            "s": "SOL",
            "i": "5m",
            "o": str(base_px + i),
            "h": str(base_px + i + 2.0),
            "l": str(base_px + i - 1.0),
            "c": str(base_px + i + 1.5),
            "v": "500.0",
            "n": 100,
        })

    df = compute_candle_features(raw_candles)

    # 1. Type and schema assertions
    assert isinstance(df, pl.DataFrame)
    assert "atr_14" in df.columns
    assert "tr" in df.columns
    assert "displacement" in df.columns
    assert "abs_displacement" in df.columns

    # 2. Check strict numeric dtypes
    assert df["open"].dtype == pl.Float64
    assert df["high"].dtype == pl.Float64
    assert df["low"].dtype == pl.Float64
    assert df["close"].dtype == pl.Float64
    assert df["volume"].dtype == pl.Float64

    # 3. Add nanosecond timestamps as done during catalog ingestion
    df_with_ns = df.with_columns([
        (pl.col("timestamp") * 1_000_000).cast(pl.Int64).alias("time_ns")
    ])
    assert df_with_ns["time_ns"].dtype == pl.Int64
    first_ts = df_with_ns["time_ns"][0]
    assert first_ts == 1_700_000_000_000 * 1_000_000

    # 4. Check PyArrow conversion via df.to_arrow()
    arrow_table = df_with_ns.to_arrow()
    assert isinstance(arrow_table, pa.Table)
    assert arrow_table.num_rows == len(df)


def test_zero_pandas_in_src():
    """Enforces zero pandas dependencies inside src/ custom modules."""
    import subprocess
    cmd = ["grep", "-rn", "import pandas", "src/"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode != 0, f"Found forbidden 'import pandas' in src/:\n{res.stdout}"

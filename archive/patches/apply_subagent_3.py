import os

base_dir = "/home/jooshoo/Desktop/hyprliquid"
cb_path = os.path.join(base_dir, "src/scanner/catalog_builder.py")

with open(cb_path, "r") as f:
    cb_code = f.read()

# Add imports
if "import requests" not in cb_code:
    cb_code = cb_code.replace("import time\n", "import time\nimport requests\nimport pyarrow.parquet as pq\n")

# Add fetch_funding_history method
new_method = """    def fetch_funding_history(self, coin: str, start_time_ms: int, end_time_ms: int) -> pl.DataFrame:
        url = "https://api.hyperliquid.xyz/info"
        payload = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_time_ms
        }
        try:
            response = requests.post(url, json=payload)
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
            return df
        except Exception as exc:
            console.print(f"[bold red]Warning: Failed fetching funding history for {coin}: {exc}[/bold red]")
            return pl.DataFrame()

    def build_catalog"""

cb_code = cb_code.replace("    def build_catalog", new_method)

# Add funding_dir creation
os_makedir = "os.makedirs(self.catalog_path, exist_ok=True)\n        catalog = ParquetDataCatalog(self.catalog_path)"
new_os_makedir = os_makedir + "\n\n        funding_dir = os.path.join(self.catalog_path, \"funding\")\n        os.makedirs(funding_dir, exist_ok=True)"
cb_code = cb_code.replace(os_makedir, new_os_makedir)

# Add funding ingestion loop
old_instrument_loop = """                instrument_bars: List[Bar] = []

                # Timeframes to ingest"""

new_instrument_loop = """                console.print(f"Fetching funding history for {coin}...")
                funding_df = self.fetch_funding_history(coin, start_4h_ms, now_ms)
                if not funding_df.is_empty():
                    arrow_table = funding_df.to_arrow()
                    pq.write_to_dataset(
                        arrow_table,
                        root_path=funding_dir,
                        partition_cols=["coin"]
                    )

                instrument_bars: List[Bar] = []

                # Timeframes to ingest"""

cb_code = cb_code.replace(old_instrument_loop, new_instrument_loop)

with open(cb_path, "w") as f:
    f.write(cb_code)

print("Applied Subagent 3 changes to catalog_builder.py")

from nautilus_trader.persistence.catalog import ParquetDataCatalog
catalog = ParquetDataCatalog("catalog")
instruments = catalog.instruments()
print(f"Found {len(instruments)} instruments")

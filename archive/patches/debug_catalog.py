from nautilus_trader.persistence.catalog import ParquetDataCatalog
catalog = ParquetDataCatalog("catalog")
all_instruments = catalog.instruments()
symbols = ["HYPE", "WLD"]
instruments = [i for i in all_instruments if any(s.upper() in str(i.id) for s in symbols)]
print([str(i.id) for i in instruments])
print(len(instruments))

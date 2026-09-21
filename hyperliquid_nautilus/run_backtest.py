import os
import pandas as pd
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import Money
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.enums import AssetClass, PriceType, OmsType, AccountType

from config.scanner_config import UniverseScannerConfig
from strategies.vwap_scanner import VwapScannerStrategy

def get_hyperliquid_perp(coin: str):
    return CurrencyPair(
        instrument_id=InstrumentId(Symbol(f"{coin}-USD-PERP"), Venue("HYPERLIQUID")),
        raw_symbol=Symbol(f"{coin}-USD-PERP"),
        quote_currency=USD,
        base_currency=USD,
        price_precision=4,
        price_increment=Price(1e-4, 4),
        multiplier=Quantity(1, 0),
        size_precision=4,
        size_increment=Quantity(1e-4, 4),
        ts_event=0,
        ts_init=0,
    )

def create_backtest_engine():
    engine_config = BacktestEngineConfig(
        trader_id="BACKTEST-001",
        logging=LoggingConfig(log_level="ERROR"),
    )
    engine = BacktestEngine(config=engine_config)
    
    venue = Venue("HYPERLIQUID")
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(100.0, USD)]
    )
    return engine

def load_data(engine: BacktestEngine, data_dir: str):
    print("Loading CSV data into Backtest Engine...")
    
    for filename in os.listdir(data_dir):
        if not filename.endswith("_1m.csv"):
            continue
            
        coin = filename.split("_")[0]
        instr_id_str = f"{coin}-USD-PERP.HYPERLIQUID"
        
        instrument = get_hyperliquid_perp(coin)
        engine.add_instrument(instrument)
        
        df = pd.read_csv(os.path.join(data_dir, filename))
        
        bars = []
        bar_type = BarType.from_str(f"{instr_id_str}-1-MINUTE-LAST-EXTERNAL")
        
        for _, row in df.iterrows():
            ts_init = dt_to_unix_nanos(pd.to_datetime(row["ts_init"]))
            ts_event = dt_to_unix_nanos(pd.to_datetime(row["ts_event"]))
            
            bar = Bar(
                bar_type=bar_type,
                open=instrument.make_price(Decimal(str(row["open"]))),
                high=instrument.make_price(Decimal(str(row["high"]))),
                low=instrument.make_price(Decimal(str(row["low"]))),
                close=instrument.make_price(Decimal(str(row["close"]))),
                volume=instrument.make_qty(Decimal(str(row["volume"]))),
                ts_event=ts_event,
                ts_init=ts_init,
            )
            bars.append(bar)
            
        engine.add_data(bars)
        print(f"Loaded {len(bars)} bars for {instr_id_str}")

def run_vwap_backtest(params=None):
    if params is None:
        params = {
            "default_sl_pct": 0.0075,
            "default_tp_pct": 0.025,
            "imbalance_threshold": 0.70
        }
        
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    engine = create_backtest_engine()
    
    load_data(engine, data_dir)
    
    strategy_config = UniverseScannerConfig(
        target_leverage=20.0,
        min_order_notional=10.0,
        max_daily_drawdown_pct=0.08,
        max_concurrent_positions=1,
        default_sl_pct=params["default_sl_pct"],
        default_tp_pct=params["default_tp_pct"],
        imbalance_threshold=params["imbalance_threshold"]
    )
    
    strategy = VwapScannerStrategy(config=strategy_config)
    engine.add_strategy(strategy)
    
    engine.run()
    
    return engine

def main():
    print("Starting Backtest...")
    engine = run_vwap_backtest()
    
    print("Backtest Completed.")
    print("Account Report:")
    report = engine.trader.generate_account_report(Venue("HYPERLIQUID"))
    print(report)
    print("Columns:", report.columns)
    print("Total Type:", type(report["total"].iloc[-1]))
    print("Total Value:", report["total"].iloc[-1])
    print("Orders Count:", len(engine.trader.generate_orders_report()))

if __name__ == "__main__":
    main()

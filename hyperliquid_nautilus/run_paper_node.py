import logging

from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.common.config import OrderEmulatorConfig, InstrumentProviderConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.adapters.hyperliquid.config import HyperliquidDataClientConfig
from nautilus_trader.adapters.hyperliquid.factories import HyperliquidLiveDataClientFactory

from config.scanner_config import UniverseScannerConfig
from strategies.vwap_scanner import VwapScannerStrategy

def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("HyperliquidNode")
    logger.info("Starting NautilusTrader Paper Node for Hyperliquid...")
    
    hl_data_config = HyperliquidDataClientConfig(
        instrument_provider=InstrumentProviderConfig(load_all=True)
    )
    
    # Configure node with emulator for keyless paper trading
    config = TradingNodeConfig(
        data_clients={"HYPERLIQUID_DATA": hl_data_config},
        emulator=OrderEmulatorConfig()
    )
    
    node = TradingNode(config=config)
    
    # Register factories
    node.add_data_client_factory("HYPERLIQUID_DATA", HyperliquidLiveDataClientFactory)
    
    # Build the internal engines, cache, and message bus
    node.build()
    # Configure and add the strategy
    strategy_config = UniverseScannerConfig(
        target_leverage=20.0,
        min_order_notional=10.0,
        max_daily_drawdown_pct=0.08,
        max_concurrent_positions=1,
        default_sl_pct=0.0075,
        default_tp_pct=0.025
    )
    strategy = VwapScannerStrategy(config=strategy_config)
    node.trader.add_strategy(strategy)
    
    try:
        # Run blocks the main thread
        logger.info("Node built successfully. Running...")
        node.run()
    except KeyboardInterrupt:
        logger.info("Shutting down node...")
        node.stop()

if __name__ == "__main__":
    main()

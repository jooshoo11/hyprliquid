from nautilus_trader.config import StrategyConfig

class UniverseScannerConfig(StrategyConfig):
    """
    Configuration for the Dynamic All-Coin Universe Scanner.
    """
    target_leverage: float = 20.0
    max_daily_drawdown_pct: float = 0.08
    default_sl_pct: float = 0.0075
    default_tp_pct: float = 0.025
    imbalance_threshold: float = 0.70
    max_concurrent_positions: int = 1
    min_order_notional: float = 10.0

from nautilus_trader.config import StrategyConfig
from pydantic import Field

class BaseBotConfig(StrategyConfig):
    """
    Universal configuration chassis for any quantitative strategy.
    """
    instrument_ids: list[str] = Field(default=["BTC-PERP", "ETH-PERP"], description="Instruments to trade")
    target_leverage: float = Field(default=20.0, description="Target leverage for positions")
    min_order_notional: float = Field(default=10.0, description="Minimum order size in USD notional")
    max_daily_drawdown_pct: float = Field(default=0.08, description="Maximum daily drawdown percentage (e.g., 0.08 for 8%)")
    default_sl_pct: float = Field(default=0.01, description="Default stop-loss percentage from entry (e.g., 0.01 for 1%)")
    default_tp_pct: float = Field(default=0.025, description="Default take-profit percentage from entry (e.g., 0.025 for 2.5%)")

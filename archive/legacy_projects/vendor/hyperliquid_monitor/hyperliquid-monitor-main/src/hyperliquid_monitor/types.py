from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Literal, Callable

TradeType = Literal["FILL", "ORDER_PLACED", "ORDER_CANCELLED"]
TradeSide = Literal["BUY", "SELL"]

@dataclass
class Trade:
    timestamp: datetime
    address: str
    coin: str
    side: TradeSide
    size: float
    price: float
    trade_type: TradeType
    direction: Optional[str] = None
    tx_hash: Optional[str] = None
    fee: Optional[float] = None
    fee_token: Optional[str] = None
    start_position: Optional[float] = None
    closed_pnl: Optional[float] = None
    order_id: Optional[int] = None

    def __post_init__(self):
        """Validate trade data after initialization"""
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {self.side}. Must be 'BUY' or 'SELL'")
        
        if self.trade_type not in ("FILL", "ORDER_PLACED", "ORDER_CANCELLED"):
            raise ValueError(
                f"Invalid trade_type: {self.trade_type}. "
                "Must be 'FILL', 'ORDER_PLACED', or 'ORDER_CANCELLED'"
            )

TradeCallback = Callable[[Trade], None]
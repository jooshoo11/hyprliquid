from datetime import datetime
import pytest
from hyperliquid_monitor.types import Trade

def test_trade_creation():
    trade = Trade(
        timestamp=datetime(2023, 11, 8, 15, 30),
        address="0x123...",
        coin="ETH",
        side="BUY",
        size=0.5,
        price=1850.5,
        trade_type="FILL"
    )
    
    assert trade.timestamp == datetime(2023, 11, 8, 15, 30)
    assert trade.address == "0x123..."
    assert trade.coin == "ETH"
    assert trade.side == "BUY"
    assert trade.size == 0.5
    assert trade.price == 1850.5
    assert trade.trade_type == "FILL"

def test_trade_optional_fields():
    trade = Trade(
        timestamp=datetime(2023, 11, 8, 15, 30),
        address="0x123...",
        coin="ETH",
        side="BUY",
        size=0.5,
        price=1850.5,
        trade_type="FILL",
        direction="Open Long",
        tx_hash="0x123...",
        fee=1.5,
        fee_token="USDC",
        start_position=-10.5,
        closed_pnl=100.25,
        order_id=12345
    )
    
    assert trade.direction == "Open Long"
    assert trade.tx_hash == "0x123..."
    assert trade.fee == 1.5
    assert trade.fee_token == "USDC"
    assert trade.start_position == -10.5
    assert trade.closed_pnl == 100.25
    assert trade.order_id == 12345

def test_trade_invalid_side():
    with pytest.raises(ValueError, match="Invalid side"):
        Trade(
            timestamp=datetime(2023, 11, 8, 15, 30),
            address="0x123...",
            coin="ETH",
            side="INVALID",  # Should raise ValueError
            size=0.5,
            price=1850.5,
            trade_type="FILL"
        )

def test_trade_invalid_trade_type():
    with pytest.raises(ValueError, match="Invalid trade_type"):
        Trade(
            timestamp=datetime(2023, 11, 8, 15, 30),
            address="0x123...",
            coin="ETH",
            side="BUY",
            size=0.5,
            price=1850.5,
            trade_type="INVALID"  # Should raise ValueError
        )
import os
import tempfile
from datetime import datetime
from typing import Dict
import pytest
from hyperliquid_monitor.types import Trade

@pytest.fixture
def temp_db_path(tmp_path):
    """Create a temporary database path"""
    db_path = tmp_path / "test.db"
    return str(db_path)

@pytest.fixture
def sample_fill_data() -> Dict:
    """Sample fill data for testing"""
    return {
        "coin": "ETH",
        "px": "1850.5",
        "sz": "0.5",
        "side": "A",
        "time": 1699457400000,  # 2023-11-08 15:30:00
        "startPosition": "-10.5",
        "dir": "Open Long",
        "closedPnl": "100.25",
        "hash": "0x123...",
        "oid": 12345,
        "crossed": False,
        "fee": "1.5",
        "tid": 67890,
        "feeToken": "USDC"
    }

@pytest.fixture
def sample_order_data() -> Dict:
    """Sample order data for testing"""
    return {
        "coin": "BTC",
        "time": 1699457400000,  # 2023-11-08 15:30:00
        "placed": {
            "px": "35000.5",
            "sz": "0.1",
            "side": "B",
            "oid": 54321
        }
    }

@pytest.fixture
def sample_trade() -> Trade:
    """Sample trade object for testing"""
    return Trade(
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
        closed_pnl=100.25
    )
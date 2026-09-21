import pytest
from unittest.mock import Mock, call
from freezegun import freeze_time
from datetime import datetime

from hyperliquid_monitor.monitor import HyperliquidMonitor
from hyperliquid_monitor.types import Trade

def test_monitor_initialization():
    addresses = ["0x123..."]
    monitor = HyperliquidMonitor(addresses)
    assert monitor.addresses == addresses
    assert monitor.db is None
    assert monitor.callback is None
    assert monitor.silent is False

def test_monitor_with_db(temp_db_path):
    addresses = ["0x123..."]
    monitor = HyperliquidMonitor(addresses, db_path=temp_db_path)
    assert monitor.db is not None
    assert monitor.db.db_path == temp_db_path
    assert monitor.silent is False

def test_silent_mode_requires_db():
    addresses = ["0x123..."]
    with pytest.raises(ValueError, match="Silent mode requires a database path"):
        HyperliquidMonitor(addresses, silent=True)

def test_silent_mode_initialization(temp_db_path):
    addresses = ["0x123..."]
    callback = Mock()
    monitor = HyperliquidMonitor(
        addresses, 
        db_path=temp_db_path, 
        callback=callback,
        silent=True
    )
    assert monitor.db is not None
    assert monitor.callback is None  # Callback should be None in silent mode
    assert monitor.silent is True

@freeze_time("2023-11-08 15:30:00")
def test_process_fill(sample_fill_data):
    monitor = HyperliquidMonitor(["0x123..."])
    address = "0x123..."
    
    trade = monitor._process_fill(sample_fill_data, address)
    
    assert isinstance(trade, Trade)
    assert trade.address == address
    assert trade.coin == "ETH"
    assert trade.side == "BUY"  # 'A' should be converted to 'BUY'
    assert trade.size == 0.5
    assert trade.price == 1850.5
    assert trade.trade_type == "FILL"
    assert trade.direction == "Open Long"
    assert abs(trade.closed_pnl - 100.25) < 0.001  # Float comparison

@freeze_time("2023-11-08 15:30:00")
def test_process_order_update(sample_order_data):
    monitor = HyperliquidMonitor(["0x123..."])
    address = "0x123..."
    
    trades = monitor._process_order_update(sample_order_data, address)
    
    assert len(trades) == 1
    trade = trades[0]
    assert isinstance(trade, Trade)
    assert trade.address == address
    assert trade.coin == "BTC"
    assert trade.side == "SELL"  # 'B' should be converted to 'SELL'
    assert trade.size == 0.1
    assert abs(trade.price - 35000.5) < 0.001
    assert trade.trade_type == "ORDER_PLACED"
    assert trade.order_id == 54321

def test_callback_execution(sample_fill_data):
    mock_callback = Mock()
    monitor = HyperliquidMonitor(["0x123..."], callback=mock_callback)
    
    handler = monitor.create_event_handler("0x123...")
    event = {"data": {"fills": [sample_fill_data]}}
    
    handler(event)
    
    assert mock_callback.call_count == 1
    call_args = mock_callback.call_args[0][0]
    assert isinstance(call_args, Trade)
    assert call_args.coin == "ETH"
    assert abs(call_args.size - 0.5) < 0.001

def test_silent_mode_callback_suppression(sample_fill_data, temp_db_path):
    mock_callback = Mock()
    monitor = HyperliquidMonitor(
        ["0x123..."], 
        db_path=temp_db_path,
        callback=mock_callback,
        silent=True
    )
    
    handler = monitor.create_event_handler("0x123...")
    event = {"data": {"fills": [sample_fill_data]}}
    
    handler(event)
    
    # Callback should not be called in silent mode
    assert mock_callback.call_count == 0

def test_stop_monitor():
    monitor = HyperliquidMonitor(["0x123..."])
    assert not monitor._stop_event.is_set()
    
    monitor.stop()
    assert monitor._stop_event.is_set()
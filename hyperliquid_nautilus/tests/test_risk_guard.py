import pytest
from decimal import Decimal
from config.scanner_config import UniverseScannerConfig
from risk.position_guard import PositionGuard

def test_position_size():
    config = UniverseScannerConfig(target_leverage=20.0, max_concurrent_positions=1)
    guard = PositionGuard(config)
    
    size = guard.calculate_position_size(Decimal("100"), Decimal("50000"), active_position_count=0)
    assert size == Decimal("0.04")

def test_multi_slot_sizing():
    config = UniverseScannerConfig(target_leverage=20.0, max_concurrent_positions=4)
    guard = PositionGuard(config)
    
    size = guard.calculate_position_size(Decimal("100"), Decimal("50000"), active_position_count=0)
    assert size == Decimal("0.01")
    
    size_full = guard.calculate_position_size(Decimal("100"), Decimal("50000"), active_position_count=4)
    assert size_full == Decimal("0")

def test_daily_drawdown_halt():
    config = UniverseScannerConfig(max_daily_drawdown_pct=0.08)
    guard = PositionGuard(config)
    
    guard.update_equity(Decimal("100"))
    guard.update_equity(Decimal("93"))
    assert not guard.is_trading_halted
    
    guard.update_equity(Decimal("91"))
    assert guard.is_trading_halted
    
    size = guard.calculate_position_size(Decimal("91"), Decimal("50000"), active_position_count=0)
    assert size == Decimal("0")
    
def test_stop_loss_clamp():
    config = UniverseScannerConfig(default_sl_pct=0.0075)
    guard = PositionGuard(config)
    
    entry = Decimal("10000")
    sl = guard.clamp_stop_loss(entry, "SELL")
    assert sl == Decimal("9925")
    
    config_aggressive = UniverseScannerConfig(default_sl_pct=0.10)
    guard2 = PositionGuard(config_aggressive)
    sl2 = guard2.clamp_stop_loss(entry, "SELL")
    assert sl2 == Decimal("9550")

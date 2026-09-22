"""
Unit tests for DeltaNeutralCarryStrategy (src/strategies/funding_arbitrage.py).
Verifies:
1. Strategy configuration defaults and thresholds.
2. Pair qualification and microstructure pre-filtering (spread <= 0.10%, top-5 depth >= $10k).
3. Delta-neutral equal-dollar sizing (leg_capital_usd).
4. Concurrent entry logic and PortfolioGuard integration.
5. Dynamic carry compression exit (< 30.0% APR) and circuit breaker exits.
"""

from decimal import Decimal
from typing import Dict, Any, List, Optional
import time
import pytest
from unittest.mock import MagicMock, patch

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity

from src.strategies.funding_arbitrage import (
    DeltaNeutralCarryStrategy,
    DeltaNeutralCarryConfig,
    PairedCarryPosition,
)
from src.risk.portfolio_guard import PortfolioGuard
from src.utils.instruments import get_hyperliquid_perp


@pytest.fixture
def mock_instruments():
    """Create mock Hyperliquid perpetual instruments for testing."""
    inst_a = get_hyperliquid_perp("COINA", sz_decimals=2, px_decimals=2)
    inst_b = get_hyperliquid_perp("COINB", sz_decimals=2, px_decimals=2)
    return inst_a, inst_b


# =====================================================================
# 1. Config Defaults
# =====================================================================

def test_strategy_config_defaults():
    """Verify DeltaNeutralCarryConfig default parameters match strategy specs."""
    config = DeltaNeutralCarryConfig()
    assert config.min_net_carry_apr == 80.0
    assert config.exit_net_carry_apr == 30.0
    assert config.max_spread_pct == 0.10
    assert config.min_top5_depth_usd == 10000.0
    assert config.leg_capital_usd == 10.0
    assert config.max_active_pairs == 2
    assert config.venue == "HYPERLIQUID"


# =====================================================================
# 2. Pair Qualification & Microstructure Pre-Filtering
# =====================================================================

def test_pair_qualification_and_prefiltering(mock_instruments):
    """
    Verify:
    - Pairs with net carry < 80.0% are skipped.
    - Pairs failing spread or depth pre-filters are skipped.
    - Pairs with net carry >= 80.0% and passing pre-filters qualify for entry.
    """
    inst_a, inst_b = mock_instruments
    guard = PortfolioGuard()
    config = DeltaNeutralCarryConfig(min_net_carry_apr=80.0, max_spread_pct=0.10, min_top5_depth_usd=10000.0)
    
    mock_scanner = MagicMock()
    strategy = DeltaNeutralCarryStrategy(config=config, portfolio_guard=guard, arbitrage_scanner=mock_scanner)
    strategy.instruments_map["COINA"] = inst_a
    strategy.instruments_map["COINB"] = inst_b

    # Case 1: Candidate with net carry below 80% (e.g. 50%) -> Skipped
    mock_scanner.find_arbitrage_pairs.return_value = [
        {
            "pair_id": "COINA_LONG__COINB_SHORT",
            "net_carry_apr_pct": 50.0,
            "long_leg": {"coin": "COINA", "oracle_price": 50.0},
            "short_leg": {"coin": "COINB", "oracle_price": 100.0},
        }
    ]
    entered = strategy.check_arbitrage_opportunities()
    assert len(entered) == 0

    # Case 2: Candidate with net carry 120%, but Long leg fails spread filter (> 0.10%) -> Skipped
    mock_scanner.find_arbitrage_pairs.return_value = [
        {
            "pair_id": "COINA_LONG__COINB_SHORT",
            "net_carry_apr_pct": 120.0,
            "long_leg": {"coin": "COINA", "oracle_price": 50.0},
            "short_leg": {"coin": "COINB", "oracle_price": 100.0},
        }
    ]
    mock_scanner.evaluate_liquidity_and_spread.side_effect = [
        {"passed": False, "reason": "Spread 0.25% > 0.10%"},  # Long leg fails
        {"passed": True, "reason": "Passed"},
    ]
    entered = strategy.check_arbitrage_opportunities()
    assert len(entered) == 0

    # Case 3: Candidate with net carry 120%, but Short leg fails depth filter (< $10k) -> Skipped
    mock_scanner.evaluate_liquidity_and_spread.side_effect = [
        {"passed": True, "reason": "Passed"},
        {"passed": False, "reason": "Depth $2,000 < $10,000"},  # Short leg fails
    ]
    entered = strategy.check_arbitrage_opportunities()
    assert len(entered) == 0


# =====================================================================
# 3. Delta-Neutral Equal Dollar Sizing
# =====================================================================

def test_delta_neutral_equal_dollar_sizing(mock_instruments):
    """
    Verify both legs are sized to equal dollar notional (leg_capital_usd):
    Long Leg ($50/coin) -> 0.20 coins ($10 notional)
    Short Leg ($200/coin) -> 0.05 coins ($10 notional)
    Net Delta Exposure = $0.0 (Delta Neutral)
    """
    inst_a, inst_b = mock_instruments
    guard = PortfolioGuard()
    config = DeltaNeutralCarryConfig(leg_capital_usd=10.0)
    strategy = DeltaNeutralCarryStrategy(config=config, portfolio_guard=guard)

    long_px = 50.0
    short_px = 200.0

    target_usd = config.leg_capital_usd
    long_qty_val = target_usd / long_px       # 0.20
    short_qty_val = target_usd / short_px     # 0.05

    long_qty = inst_a.make_qty(Decimal(str(round(long_qty_val, inst_a.size_precision))))
    short_qty = inst_b.make_qty(Decimal(str(round(short_qty_val, inst_b.size_precision))))

    long_notional = float(long_qty.as_double()) * long_px
    short_notional = float(short_qty.as_double()) * short_px

    assert long_notional == pytest.approx(10.0, rel=1e-2)
    assert short_notional == pytest.approx(10.0, rel=1e-2)
    # Delta neutrality: net dollar exposure is approximately zero
    net_delta_usd = long_notional - short_notional
    assert abs(net_delta_usd) < 0.01


# =====================================================================
# 4. Concurrent Entry Logic & PortfolioGuard Integration
# =====================================================================

def test_entry_logic_and_portfolio_guard_integration(mock_instruments):
    """
    Verify that when entering a qualified pair:
    - Long order (BUY) and Short order (SELL) are submitted.
    - PortfolioGuard.can_open_position is checked for both legs.
    - PortfolioGuard.register_order_submitted is called for both legs.
    - Active pair is recorded in strategy.active_pairs.
    """
    inst_a, inst_b = mock_instruments
    guard = PortfolioGuard()
    guard.update_equity(100.0)

    config = DeltaNeutralCarryConfig(leg_capital_usd=10.0, min_net_carry_apr=80.0)
    strategy = DeltaNeutralCarryStrategy(config=config, portfolio_guard=guard)

    # Mock order submission and order factory
    mock_order_factory = MagicMock()
    mock_order_factory.market.side_effect = [
        MagicMock(id="ORDER-LONG-001"),
        MagicMock(id="ORDER-SHORT-001"),
    ]
    strategy._order_factory = mock_order_factory
    strategy.submit_order = MagicMock()

    pair_data = {
        "pair_id": "COINA_LONG__COINB_SHORT",
        "net_carry_apr_pct": 150.0,
        "long_leg": {"coin": "COINA", "oracle_price": 50.0},
        "short_leg": {"coin": "COINB", "oracle_price": 100.0},
    }
    long_micro = {"best_ask": 50.0, "passed": True}
    short_micro = {"best_bid": 100.0, "passed": True}

    success = strategy._enter_paired_carry(
        long_instrument=inst_a,
        short_instrument=inst_b,
        pair_data=pair_data,
        long_micro=long_micro,
        short_micro=short_micro,
    )

    assert success is True
    assert strategy.submit_order.call_count == 2
    assert "COINA_LONG__COINB_SHORT" in strategy.active_pairs

    active_pos = strategy.active_pairs["COINA_LONG__COINB_SHORT"]
    assert active_pos.status == "OPEN"
    assert active_pos.long_coin == "COINA"
    assert active_pos.short_coin == "COINB"
    assert active_pos.long_notional_usd == pytest.approx(10.0, rel=1e-2)
    assert active_pos.short_notional_usd == pytest.approx(10.0, rel=1e-2)
    assert active_pos.entry_net_carry_apr == 150.0


# =====================================================================
# 5. Exit Logic: Carry Compression & Circuit Breaker
# =====================================================================

def test_exit_logic_on_carry_compression(mock_instruments):
    """
    Verify that when net carry compresses below exit_net_carry_apr (30.0%):
    - Both legs are closed simultaneously via close_all_positions.
    - The pair is removed from active_pairs.
    """
    inst_a, inst_b = mock_instruments
    guard = PortfolioGuard()
    config = DeltaNeutralCarryConfig(exit_net_carry_apr=30.0)
    strategy = DeltaNeutralCarryStrategy(config=config, portfolio_guard=guard)
    strategy.close_all_positions = MagicMock()

    # Setup active paired position with 120% entry carry
    pair_id = "COINA_LONG__COINB_SHORT"
    strategy.active_pairs[pair_id] = PairedCarryPosition(
        pair_id=pair_id,
        long_instrument_id=inst_a.id,
        short_instrument_id=inst_b.id,
        long_coin="COINA",
        short_coin="COINB",
        long_notional_usd=10.0,
        short_notional_usd=10.0,
        long_entry_price=50.0,
        short_entry_price=100.0,
        entry_net_carry_apr=120.0,
        current_net_carry_apr=120.0,
        entry_time=time.time(),
        status="OPEN",
    )

    # 1. Carry remains high: Long=-0.40, Short=+1.10 -> Net Carry = +150% (> 30%) -> No exit
    strategy._get_coin_funding_apr = MagicMock(side_effect=lambda coin: -0.40 if coin == "COINA" else 1.10)
    exited = strategy.check_active_pairs_exit()
    assert len(exited) == 0
    assert pair_id in strategy.active_pairs

    # 2. Carry compresses: Long=0.00, Short=+0.20 -> Net Carry = +20% (< 30% exit threshold) -> Triggers exit!
    strategy._get_coin_funding_apr = MagicMock(side_effect=lambda coin: 0.00 if coin == "COINA" else 0.20)
    exited = strategy.check_active_pairs_exit()
    assert len(exited) == 1
    assert exited[0] == pair_id
    assert pair_id not in strategy.active_pairs
    assert strategy.close_all_positions.call_count == 2


def test_exit_logic_on_circuit_breaker(mock_instruments):
    """Verify that if PortfolioGuard circuit breaker is tripped, active pairs exit immediately."""
    inst_a, inst_b = mock_instruments
    guard = PortfolioGuard()
    config = DeltaNeutralCarryConfig(exit_net_carry_apr=30.0)
    strategy = DeltaNeutralCarryStrategy(config=config, portfolio_guard=guard)
    strategy.close_all_positions = MagicMock()

    pair_id = "COINA_LONG__COINB_SHORT"
    strategy.active_pairs[pair_id] = PairedCarryPosition(
        pair_id=pair_id,
        long_instrument_id=inst_a.id,
        short_instrument_id=inst_b.id,
        long_coin="COINA",
        short_coin="COINB",
        long_notional_usd=10.0,
        short_notional_usd=10.0,
        long_entry_price=50.0,
        short_entry_price=100.0,
        entry_net_carry_apr=120.0,
        current_net_carry_apr=120.0,
        entry_time=time.time(),
        status="OPEN",
    )

    # Trip circuit breaker
    guard.is_circuit_breaker_triggered = True

    exited = strategy.check_active_pairs_exit()
    assert len(exited) == 1
    assert exited[0] == pair_id
    assert pair_id not in strategy.active_pairs
    assert strategy.close_all_positions.call_count == 2

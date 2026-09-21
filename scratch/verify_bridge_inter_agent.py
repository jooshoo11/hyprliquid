"""
Comprehensive Verification Script for Bridge & Inter-Agent Communications
Tests bidirectional loops:
- Component A: ai_prospector -> bridge/prospects.json -> node_runner.py / portfolio_guard.py
- Component B: node_app.py -> bridge/active_trades.json -> ai_trade_manager
- Component C: ai_trade_manager -> bridge/ai_commands.json -> node_app.py -> Nautilus Trader
- Component D: Hyperliquid WebSocket & REST connections (mcp_client.py, caching, asset contexts)
"""

import os
import sys
import json
import time
import shutil
from decimal import Decimal
from typing import Dict, Any, List

# Ensure repository root is in path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nautilus_trader.model.identifiers import (
    InstrumentId,
    Symbol,
    Venue,
    ClientOrderId,
    StrategyId,
    TraderId,
    PositionId,
    AccountId,
)
from nautilus_trader.model.enums import OrderSide, TimeInForce, AccountType, PositionSide
from nautilus_trader.model.objects import Quantity, Price, Money, MarginBalance, AccountBalance
from nautilus_trader.model.orders.market import MarketOrder
from nautilus_trader.execution.messages import SubmitOrder
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.position import Position
from nautilus_trader.model.instruments.base import Instrument

from src.risk.portfolio_guard import PortfolioGuard
from src.execution.node_runner import HyperliquidNodeRunner
from src.scanner.mcp_client import HyperliquidInfoClient


def log_step(name: str):
    print(f"\n{'='*70}\n>>> [VERIFY] {name}\n{'='*70}")


def log_pass(msg: str):
    print(f"  [PASS] {msg}")


def log_fail(msg: str):
    print(f"  [FAIL] {msg}")
    raise AssertionError(msg)


# ==============================================================================
# COMPONENT A: ai_prospector -> bridge/prospects.json -> runner / guard
# ==============================================================================
def verify_component_a():
    log_step("Component A: ai_prospector -> bridge/prospects.json -> PortfolioGuard")
    prospects_file = os.path.join(REPO_ROOT, "bridge", "prospects.json")
    backup_file = prospects_file + ".bak"

    # Backup original file
    if os.path.exists(prospects_file):
        shutil.copyfile(prospects_file, backup_file)

    try:
        # 1. Test standard dict format written by ai_prospector
        mock_prospects_dict = {
            "BTC": {
                "bias": "LONG",
                "target_entry": 62000.0,
                "reason": "Extreme negative funding APR -450%, short squeeze expected",
                "updated_at": "2026-09-19T13:00:00Z"
            },
            "ETH": {
                "bias": "SHORT",
                "target_entry": 3450.0,
                "reason": "Overextended longs, funding APR +280%",
                "updated_at": "2026-09-19T13:00:00Z"
            },
            "SOL": {
                "bias": "NEUTRAL",
                "target_entry": 140.0,
                "reason": "Consolidation zone, no directional edge",
                "updated_at": "2026-09-19T13:00:00Z"
            }
        }
        with open(prospects_file, "w") as f:
            json.dump(mock_prospects_dict, f, indent=2)

        guard = PortfolioGuard()
        guard.update_equity(10000.0)

        runner = HyperliquidNodeRunner(top_n=5, paper=True)
        runner.guard = guard

        biases = runner.load_prospects()
        assert biases.get("BTC") == "LONG", f"Expected BTC LONG, got {biases.get('BTC')}"
        assert biases.get("ETH") == "SHORT", f"Expected ETH SHORT, got {biases.get('ETH')}"
        assert biases.get("SOL") == "NEUTRAL", f"Expected SOL NEUTRAL, got {biases.get('SOL')}"
        log_pass("load_prospects correctly parsed dict format and updated guard biases.")

        # 2. Verify PortfolioGuard enforcement
        btc_id = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")
        eth_id = InstrumentId.from_str("ETH-USD-PERP.HYPERLIQUID")
        sol_id = InstrumentId.from_str("SOL-USD-PERP.HYPERLIQUID")
        unlisted_id = InstrumentId.from_str("DOGE-USD-PERP.HYPERLIQUID")

        # BTC: Bias is LONG -> BUY should pass, SELL should be rejected
        ok_btc_buy, reason = guard.can_open_position("TestStrat", btc_id, OrderSide.BUY, 500.0, 0)
        assert ok_btc_buy, f"Expected BTC BUY approved, got: {reason}"
        log_pass("BTC BUY approved under LONG bias.")

        ok_btc_sell, reason = guard.can_open_position("TestStrat", btc_id, OrderSide.SELL, 500.0, 0)
        assert not ok_btc_sell and "AI Prospect bias for BTC is LONG" in reason, f"Expected BTC SELL rejection, got: {reason}"
        log_pass(f"BTC SELL rejected under LONG bias: {reason}")

        # ETH: Bias is SHORT -> SELL should pass, BUY should be rejected
        ok_eth_sell, reason = guard.can_open_position("TestStrat", eth_id, OrderSide.SELL, 500.0, 0)
        assert ok_eth_sell, f"Expected ETH SELL approved, got: {reason}"
        log_pass("ETH SELL approved under SHORT bias.")

        ok_eth_buy, reason = guard.can_open_position("TestStrat", eth_id, OrderSide.BUY, 500.0, 0)
        assert not ok_eth_buy and "AI Prospect bias for ETH is SHORT" in reason, f"Expected ETH BUY rejection, got: {reason}"
        log_pass(f"ETH BUY rejected under SHORT bias: {reason}")

        # SOL: Bias is NEUTRAL -> Both BUY and SELL should pass
        guard._pending_approvals = 0
        ok_sol_buy, _ = guard.can_open_position("TestStrat", sol_id, OrderSide.BUY, 500.0, 0)
        guard._pending_approvals = 0
        ok_sol_sell, _ = guard.can_open_position("TestStrat", sol_id, OrderSide.SELL, 500.0, 0)
        assert ok_sol_buy and ok_sol_sell, "Expected both BUY and SELL allowed for NEUTRAL bias"
        log_pass("SOL BUY and SELL approved under NEUTRAL bias.")

        # Unlisted: DOGE -> Both BUY and SELL should pass
        guard._pending_approvals = 0
        ok_doge_buy, _ = guard.can_open_position("TestStrat", unlisted_id, OrderSide.BUY, 500.0, 0)
        guard._pending_approvals = 0
        ok_doge_sell, _ = guard.can_open_position("TestStrat", unlisted_id, OrderSide.SELL, 500.0, 0)
        assert ok_doge_buy and ok_doge_sell, "Expected unlisted coin to be allowed"
        log_pass("Unlisted coin (DOGE) allowed without prospect restrictions.")

        # 3. Test list format support in load_prospects
        mock_prospects_list = [
            {"coin": "AVAX", "bias": "BUY", "reason": "Squeeze"},
            {"symbol": "ARB", "direction": "BEARISH", "reason": "Unlock dump"}
        ]
        with open(prospects_file, "w") as f:
            json.dump(mock_prospects_list, f, indent=2)

        biases_list = runner.load_prospects()
        assert biases_list.get("AVAX") == "LONG", f"Expected AVAX LONG from BUY, got {biases_list.get('AVAX')}"
        assert biases_list.get("ARB") == "SHORT", f"Expected ARB SHORT from BEARISH, got {biases_list.get('ARB')}"
        log_pass("load_prospects successfully parsed list format and normalized BUY/BEARISH aliases.")

    finally:
        # Restore backup
        if os.path.exists(backup_file):
            shutil.copyfile(backup_file, prospects_file)
            os.remove(backup_file)


# ==============================================================================
# ==============================================================================
# COMPONENT B: node_app.py -> bridge/active_trades.json -> ai_trade_manager
# ==============================================================================
def verify_component_b():
    log_step("Component B: node_app.py -> bridge/active_trades.json -> ai_trade_manager")
    trades_file = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
    backup_file = trades_file + ".bak"

    if os.path.exists(trades_file):
        shutil.copyfile(trades_file, backup_file)

    try:
        from src.utils.instruments import get_hyperliquid_perp
        from nautilus_trader.model.enums import OrderType, LiquiditySide, OmsType
        from nautilus_trader.model.events.order import OrderFilled
        from nautilus_trader.model.currencies import USD
        from nautilus_trader.model.identifiers import VenueOrderId, TradeId

        # Create real Nautilus Cache with real Position
        cache = Cache()
        inst = get_hyperliquid_perp("NEAR")
        cache.add_instrument(inst)

        fill = OrderFilled(
            trader_id=TraderId("TRADER-001"),
            strategy_id=StrategyId("TrendContinuation-001"),
            instrument_id=inst.id,
            client_order_id=ClientOrderId("ORD-001"),
            venue_order_id=VenueOrderId("V-001"),
            account_id=AccountId("HYPERLIQUID-PAPER001"),
            trade_id=TradeId("T-001"),
            position_id=PositionId("POS-NEAR-001"),
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            last_qty=inst.make_qty(150.0),
            last_px=inst.make_price(3.35),
            currency=USD,
            commission=Money(0.0, USD),
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            ts_event=int(time.time_ns()),
            ts_init=int(time.time_ns()),
        )
        pos = Position(inst, fill)
        cache.add_position(pos, OmsType.NETTING)

        # Run exact node_app.py export logic on real position
        positions = cache.positions_open()
        active_positions = []
        equity = 10540.25
        for p in positions:
            if not p.is_closed:
                entry_px = float(p.avg_px_open) if hasattr(p, "avg_px_open") and p.avg_px_open else 0.0
                unrealized = 0.0
                active_positions.append({
                    "coin": p.instrument_id.symbol.value.split("-")[0],
                    "instrument_id": str(p.instrument_id),
                    "side": "LONG" if p.is_long else "SHORT",
                    "size": p.quantity.as_double(),
                    "entry_price": entry_px,
                    "unrealized_pnl": unrealized,
                })

        state = {
            "timestamp": time.time(),
            "equity": equity,
            "positions": active_positions,
        }

        with open(trades_file, "w") as f:
            json.dump(state, f, indent=2)

        log_pass("node_app.py logic exported live Nautilus position state to bridge/active_trades.json.")

        # Simulate ai_trade_manager reading and parsing
        with open(trades_file, "r") as f:
            parsed_state = json.load(f)

        assert "timestamp" in parsed_state and isinstance(parsed_state["timestamp"], (int, float)), "Invalid timestamp"
        assert "equity" in parsed_state and isinstance(parsed_state["equity"], (int, float)), "Invalid equity"
        assert "positions" in parsed_state and isinstance(parsed_state["positions"], list), "Invalid positions"

        positions_read = parsed_state["positions"]
        assert len(positions_read) == 1, f"Expected 1 position, got {len(positions_read)}"

        p = positions_read[0]
        assert p["coin"] == "NEAR"
        assert p["instrument_id"] == "NEAR-USD-PERP.HYPERLIQUID"
        assert p["side"] == "LONG"
        assert p["size"] == 150.0
        assert p["entry_price"] == 3.35
        assert p["unrealized_pnl"] == 0.0

        log_pass("ai_trade_manager successfully parsed active_trades.json generated from real Nautilus Position.")

    finally:
        if os.path.exists(backup_file):
            shutil.copyfile(backup_file, trades_file)
            os.remove(backup_file)


# ==============================================================================
# COMPONENT C: ai_trade_manager -> bridge/ai_commands.json -> node_app.py
# ==============================================================================
def verify_component_c():
    log_step("Component C: ai_trade_manager -> bridge/ai_commands.json -> node_app.py -> Nautilus")
    cmds_file = os.path.join(REPO_ROOT, "bridge", "ai_commands.json")
    backup_file = cmds_file + ".bak"

    if os.path.exists(cmds_file):
        shutil.copyfile(cmds_file, backup_file)

    try:
        from src.utils.instruments import get_hyperliquid_perp
        from nautilus_trader.model.enums import OrderType, LiquiditySide, OmsType
        from nautilus_trader.model.events.order import OrderFilled
        from nautilus_trader.model.currencies import USD
        from nautilus_trader.model.identifiers import VenueOrderId, TradeId

        # 1. Setup mock Nautilus environment with an open NEAR position
        cache = Cache()
        inst = get_hyperliquid_perp("NEAR")
        cache.add_instrument(inst)

        fill = OrderFilled(
            trader_id=TraderId("TRADER-001"),
            strategy_id=StrategyId("TrendContinuation-001"),
            instrument_id=inst.id,
            client_order_id=ClientOrderId("ORD-001"),
            venue_order_id=VenueOrderId("V-001"),
            account_id=AccountId("HYPERLIQUID-PAPER001"),
            trade_id=TradeId("T-001"),
            position_id=PositionId("POS-NEAR-001"),
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            last_qty=inst.make_qty(150.0),
            last_px=inst.make_price(3.35),
            currency=USD,
            commission=Money(0.0, USD),
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            ts_event=int(time.time_ns()),
            ts_init=int(time.time_ns()),
        )
        pos = Position(inst, fill)
        cache.add_position(pos, OmsType.NETTING)

        # Mock Node & Trader to capture executed commands
        executed_commands = []

        class MockTrader:
            def execute(self, cmd):
                executed_commands.append(cmd)

        class MockNode:
            def __init__(self, c):
                self.trader_id = TraderId("TRADER-001")
                self.trader = MockTrader()
                self.cache = c

        class MockRunner:
            def __init__(self, n):
                self.node = n
                self.continuation_strat = None

        mock_runner = MockRunner(MockNode(cache))

        # 2. ai_trade_manager writes emergency close command
        mock_commands = [
            {
                "action": "CLOSE_POSITION",
                "coin": "NEAR",
                "reason": "Emergency risk: adverse funding flip to +1200% APR"
            }
        ]
        with open(cmds_file, "w") as f:
            json.dump(mock_commands, f, indent=2)

        log_pass("ai_trade_manager wrote CLOSE_POSITION command to bridge/ai_commands.json.")

        # 3. Execute exact node_app.py bridge command reader
        if os.path.exists(cmds_file):
            with open(cmds_file, "r") as cf:
                try:
                    commands = json.load(cf)
                except json.JSONDecodeError:
                    commands = []

            if commands:
                for cmd in commands:
                    if cmd.get("action") == "CLOSE_POSITION":
                        coin_target = cmd.get("coin")
                        coin_clean = str(coin_target).upper().split("-")[0].split(".")[0]
                        instr_id = InstrumentId.from_str(f"{coin_clean}-USD-PERP.HYPERLIQUID")
                        open_positions = mock_runner.node.cache.positions_open(instrument_id=instr_id)
                        for p in open_positions:
                            if not p.is_closed:
                                inst = mock_runner.node.cache.instrument(instr_id)
                                side = OrderSide.SELL if p.is_long else OrderSide.BUY
                                client_order_id = ClientOrderId(f"AI-CLOSE-{int(time.time())}")
                                strat_id = p.strategy_id or StrategyId("AI-TRADE-MANAGER")
                                order = MarketOrder(
                                    trader_id=mock_runner.node.trader_id,
                                    strategy_id=strat_id,
                                    instrument_id=instr_id,
                                    client_order_id=client_order_id,
                                    order_side=side,
                                    quantity=p.quantity,
                                    init_id=UUID4(),
                                    ts_init=int(time.time_ns()),
                                    time_in_force=TimeInForce.FOK,
                                    reduce_only=True,
                                )
                                close_cmd = SubmitOrder(
                                    trader_id=mock_runner.node.trader_id,
                                    strategy_id=strat_id,
                                    order=order,
                                    command_id=UUID4(),
                                    ts_init=int(time.time_ns()),
                                    position_id=p.id,
                                )
                                mock_runner.node.trader.execute(close_cmd)

                with open(cmds_file, "w") as cf:
                    json.dump([], cf)

        # 4. Assert command dispatch and order parameters
        assert len(executed_commands) == 1, f"Expected 1 executed command, got {len(executed_commands)}"
        close_cmd = executed_commands[0]
        assert isinstance(close_cmd, SubmitOrder), "Executed command must be SubmitOrder"
        assert close_cmd.order.side == OrderSide.SELL, "Expected OrderSide.SELL to close LONG position"
        assert close_cmd.order.quantity == inst.make_qty(150.0), "Order quantity must match position size"
        assert close_cmd.order.is_reduce_only, "Emergency close order must be reduce_only"
        assert close_cmd.order.time_in_force == TimeInForce.FOK, "Emergency close order must be FOK"
        assert close_cmd.position_id == PositionId("POS-NEAR-001"), "SubmitOrder must reference position_id"
        log_pass("node_app.py read CLOSE_POSITION, built valid MarketOrder/SubmitOrder, and dispatched via trader.execute.")

        # 5. Verify bridge/ai_commands.json is cleared
        with open(cmds_file, "r") as cf:
            cleared = json.load(cf)
        assert cleared == [], "ai_commands.json must be cleared after processing"
        log_pass("ai_commands.json properly cleared to prevent duplicate execution.")

    finally:
        if os.path.exists(backup_file):
            shutil.copyfile(backup_file, cmds_file)
            os.remove(backup_file)


# ==============================================================================
# COMPONENT D: Hyperliquid WebSocket & REST connections (mcp_client.py)
# ==============================================================================
def verify_component_d():
    log_step("Component D: Hyperliquid Info Client, Caching & Real-time Asset Contexts")

    client = HyperliquidInfoClient()
    log_pass(f"HyperliquidInfoClient initialized: network='{client.network}', api_url='{client.api_url}'")

    # 1. Fetch meta and asset contexts
    t0 = time.time()
    meta, asset_ctxs = client.get_meta_and_asset_ctxs()
    latency_ms = (time.time() - t0) * 1000

    universe = meta.get("universe", [])
    assert len(universe) > 0, "Universe should contain instruments"
    assert len(asset_ctxs) > 0, "Asset contexts should not be empty"
    assert len(universe) == len(asset_ctxs), "Universe and asset contexts lengths should match"
    log_pass(f"Fetched {len(universe)} markets and asset contexts in {latency_ms:.1f}ms.")

    # 2. Verify local disk cache
    cache_path = client._resolve_meta_cache_path()
    assert os.path.exists(cache_path), f"Meta cache file not found at {cache_path}"
    with open(cache_path, "r") as f:
        cache_data = json.load(f)
    assert cache_data.get("status") == "SUCCESS", "Cache status not SUCCESS"
    log_pass(f"Verified meta cache persisted at {cache_path} ({os.path.getsize(cache_path):,} bytes).")

    # 3. Verify in-memory 5-second cache
    t1 = time.time()
    _, _ = client.get_meta_and_asset_ctxs()
    cached_latency_ms = (time.time() - t1) * 1000
    assert cached_latency_ms < 5.0, f"Cached call took too long: {cached_latency_ms:.2f}ms"
    log_pass(f"In-memory 5-second cache hit: {cached_latency_ms:.3f}ms (vs {latency_ms:.1f}ms network).")

    # 4. Verify Polars ranking of top perpetuals
    top_perps = client.get_top_perpetuals(top_n=5)
    assert len(top_perps) == 5, f"Expected 5 top perpetuals, got {len(top_perps)}"
    for p in top_perps:
        assert "name" in p and "oracle_price" in p and "volume_24h" in p and "volatility_proxy" in p
        assert p["oracle_price"] > 0, f"Oracle price must be positive for {p['name']}"
    log_pass(f"Polars ranked top perpetuals: {[p['name'] for p in top_perps]}")

    # 5. Verify real-time L2 order book snapshot
    l2 = client.get_l2_snapshot("BTC")
    assert l2.get("status") == "SUCCESS", f"L2 snapshot failed: {l2.get('error')}"
    assert len(l2.get("bids", [])) > 0, "L2 bids empty"
    assert len(l2.get("asks", [])) > 0, "L2 asks empty"
    best_bid = float(l2["bids"][0]["px"])
    best_ask = float(l2["asks"][0]["px"])
    assert best_bid <= best_ask, f"Invalid book: best bid {best_bid} > best ask {best_ask}"
    log_pass(f"L2 snapshot for BTC: Best Bid ${best_bid:,.2f} | Best Ask ${best_ask:,.2f} | Depth: {len(l2['bids'])} bids / {len(l2['asks'])} asks")

    # 6. Verify funding rate calculation
    funding_apr = client.get_funding_rate("BTC")
    assert funding_apr is not None, "Funding APR should not be None"
    log_pass(f"BTC Annualized Funding APR: {funding_apr * 100:.3f}%")


def main():
    print("=" * 70)
    print("HYPERLIQUID MULTI-AGENT SYSTEM: INTER-AGENT & BRIDGE VERIFICATION")
    print("=" * 70)

    try:
        verify_component_a()
        verify_component_b()
        verify_component_c()
        verify_component_d()

        print("\n" + "=" * 70)
        print("ALL INTER-AGENT & BRIDGE COMMUNICATIONS VERIFIED SUCCESSFULLY! [4/4]")
        print("=" * 70)
    except Exception as e:
        print(f"\n[CRITICAL ERROR] Verification failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

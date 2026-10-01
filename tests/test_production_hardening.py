import time
from unittest.mock import patch, MagicMock
from src.utils.instruments import to_hl_price
from src.risk.portfolio_guard import PortfolioGuard
from src.risk.trade_manager import TradeManager
from src.execution.agent_wallet import HyperliquidAgentWallet


def test_to_hl_price_rules():
    # Max 5 sig figs
    assert to_hl_price(84123.456) == 84123.0
    assert to_hl_price(2715.75) == 2715.8
    assert to_hl_price(134.567) == 134.57
    assert to_hl_price(5.46723) == 5.4672
    assert to_hl_price(0.274567) == 0.27457
    assert to_hl_price(0.0000123456) == 0.000012346


def test_portfolio_guard_rolling_4h_circuit_breaker():
    guard = PortfolioGuard(max_daily_drawdown_pct=0.20)
    guard.update_equity(100.0)
    assert guard.daily_high_water_mark == 100.0
    assert not guard.is_circuit_breaker_triggered

    # Trip circuit breaker: 25% drawdown
    guard.update_equity(75.0)
    assert guard.is_circuit_breaker_triggered

    # Test manual reset
    guard.reset_circuit_breaker()
    assert not guard.is_circuit_breaker_triggered
    assert guard.daily_high_water_mark == 75.0

    # Test rolling 4-hour reset (simulate 4.1 hours elapsed)
    guard.update_equity(50.0)  # Drawdown from 75 to 50 trips it again
    assert guard.is_circuit_breaker_triggered

    guard.day_start_timestamp_ms = int((time.time() - (4.1 * 3600)) * 1000)
    guard.update_equity(50.0)
    assert not guard.is_circuit_breaker_triggered
    assert guard.daily_high_water_mark == 50.0


def test_trade_manager_scalp_configuration():
    tm = TradeManager(
        min_holding_seconds=45.0,
        reentry_cooldown_seconds=120.0,
        take_profit_roi_pct=1.40,
        mae_roi_pct=-0.80,
        fast_dud_minutes=10.0,
        stagnant_hours=0.5,
        tiered_profit_lock=True,
    )
    assert tm.take_profit_roi_pct == 1.40
    assert tm.mae_roi_pct == -0.80
    assert tm.fast_dud_minutes == 10.0
    assert tm.reentry_cooldown_seconds == 120.0
    assert tm.breakeven_stop_roi_pct >= 0.10
    assert tm.tiered_profit_lock is True


def test_trade_manager_telegram_alert_dispatch():
    tm = TradeManager()
    trade_record = {
        "coin": "SOL",
        "side": "LONG",
        "roi": 1.45,
        "net_pnl": 3.25,
        "strategy": "OrderBookImbalance",
        "reason": "Take profit hit +1.40%",
        "duration": "0h 4m 12s",
    }

    with patch("os.getenv") as mock_env, patch("requests.post") as mock_post:
        mock_env.side_effect = lambda k, default=None: {
            "TELEGRAM_BOT_TOKEN": "test_token",
            "TELEGRAM_CHAT_ID": "test_chat",
        }.get(k, default)

        tm._send_trade_alert(trade_record)
        time.sleep(0.1)  # Allow background thread to dispatch

        assert mock_post.called
        call_args = mock_post.call_args
        assert "api.telegram.org" in call_args[0][0]
        assert "SOL" in call_args[1]["json"]["text"]


def test_agent_wallet_interface():
    wallet = HyperliquidAgentWallet(
        main_address="0x1111111111111111111111111111111111111111",
        agent_key="0x2222222222222222222222222222222222222222222222222222222222222222",
        network="testnet",
    )
    status = wallet.get_status()
    assert status["configured"] is True
    assert status["network"] == "testnet"
    assert "agent_address" in status


def test_portfolio_guard_leveraged_notional_scaling():
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.enums import OrderSide
    guard = PortfolioGuard(max_strategy_equity_pct=0.40)
    guard.update_equity(100.0)
    inst_sol = InstrumentId(Symbol("SOL-USD-PERP"), Venue("HYPERLIQUID"))

    # Unleveraged 1x call with $500 notional exceeds $40 cap
    can_open_unlev, reason = guard.can_open_position(
        strategy_name="OrderBookImbalance",
        instrument_id=inst_sol,
        side=OrderSide.BUY,
        proposed_notional_usd=500.0,
        current_open_positions_count=0,
    )
    assert can_open_unlev is False
    assert "exceeds" in reason

    # Leveraged 15x call scales $40 equity allocation cap to $600 notional capacity
    can_open_lev, _ = guard.can_open_position(
        strategy_name="OrderBookImbalance",
        instrument_id=inst_sol,
        side=OrderSide.BUY,
        proposed_notional_usd=500.0,
        current_open_positions_count=0,
        leverage=15.0,
    )
    assert can_open_lev is True


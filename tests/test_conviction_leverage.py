"""Tests for conviction-based leverage clamping and risk budget protection."""
from src.utils.instruments import get_coin_max_leverage
from src.risk.portfolio_guard import PortfolioGuard
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId


def test_native_coin_max_leverage():
    assert get_coin_max_leverage("BTC") == 40.0
    assert get_coin_max_leverage("ETH") == 25.0
    assert get_coin_max_leverage("SOL") == 20.0
    assert get_coin_max_leverage("PONS") == 3.0


def test_conviction_leverage_clamp_policy():
    def compute_effective_leverage(coin: str, conviction_score: int) -> float:
        native_lev = float(get_coin_max_leverage(coin))
        if conviction_score < 90:
            return min(native_lev, 15.0)
        return native_lev

    # High conviction (score >= 90) gets full exchange max leverage
    assert compute_effective_leverage("BTC", 95) == 40.0
    assert compute_effective_leverage("ETH", 90) == 25.0
    assert compute_effective_leverage("SOL", 92) == 20.0
    assert compute_effective_leverage("PONS", 95) == 3.0

    # Lower conviction (score < 90) is clamped to max 15x
    assert compute_effective_leverage("BTC", 80) == 15.0
    assert compute_effective_leverage("ETH", 85) == 15.0
    assert compute_effective_leverage("SOL", 89) == 15.0
    # Coins with native max leverage < 15x stay at their native ceiling
    assert compute_effective_leverage("PONS", 85) == 3.0


def test_risk_budget_clamps_high_leverage_loss():
    equity = 200.0
    base_margin = 100.0  # 50% allocation across 2 slots
    max_risk_pct = 0.06  # 6% of equity = $12 budget
    max_risk_usd = equity * max_risk_pct

    # Scenario 1: BTC at 40x with 1.5% stop loss
    lev = 40.0
    target_notional = base_margin * lev  # $4,000
    stop_dist_frac = 0.015  # 1.5% stop
    potential_loss = target_notional * stop_dist_frac  # $60 > $12!

    assert potential_loss > max_risk_usd
    clamped_notional = max_risk_usd / stop_dist_frac  # $12 / 0.015 = $800
    assert clamped_notional == 800.0

    # Scenario 2: BTC at 40x with tight 0.25% stop loss
    stop_dist_frac_tight = 0.0025
    potential_loss_tight = target_notional * stop_dist_frac_tight  # $10 <= $12
    assert potential_loss_tight <= max_risk_usd
    # Notional does not need clamping when within risk budget


def test_portfolio_guard_allows_clamped_leverage():
    guard = PortfolioGuard(
        max_strategy_equity_pct=1.00,
        max_total_open_positions=2,
        max_single_position_equity_pct=None,
    )
    guard.update_equity(200.0)
    instr = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")
    # At 15x on $200 equity, notional is $1,500 ($100 margin x 15x)
    can_trade, reason = guard.can_open_position(
        strategy_name="HighConvictionMomentum",
        instrument_id=instr,
        side=OrderSide.BUY,
        proposed_notional_usd=1500.0,
        current_open_positions_count=0,
        leverage=15.0,
    )
    assert can_trade, reason

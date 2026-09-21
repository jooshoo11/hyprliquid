from decimal import Decimal
import logging

class PositionGuard:
    def __init__(self, config):
        self.config = config
        self.max_daily_drawdown = Decimal(str(config.max_daily_drawdown_pct))
        self.target_leverage = Decimal(str(config.target_leverage))
        
        # State tracking
        self.start_of_day_equity: Decimal | None = None
        self.consecutive_losses = 0
        self.is_trading_halted = False
        self.logger = logging.getLogger("PositionGuard")
    
    def update_equity(self, current_equity: Decimal) -> None:
        """Called periodically to check drawdown."""
        if self.start_of_day_equity is None:
            self.start_of_day_equity = current_equity
            
        drawdown = (self.start_of_day_equity - current_equity) / self.start_of_day_equity
        if drawdown >= self.max_daily_drawdown:
            if not self.is_trading_halted:
                self.logger.warning(f"Daily drawdown limit reached ({drawdown*100:.2f}%). Trading halted.")
            self.is_trading_halted = True
            
    def calculate_position_size(self, available_equity: Decimal, price: Decimal, active_position_count: int = 0) -> Decimal:
        """Calculate isolated position sizing based on available equity, target leverage, and concurrent slots."""
        if self.is_trading_halted:
            return Decimal("0")
            
        max_pos = getattr(self.config, "max_concurrent_positions", 1)
        if active_position_count >= max_pos:
            return Decimal("0")
            
        notional_target = (available_equity / Decimal(str(max_pos))) * self.target_leverage
        if notional_target < Decimal(str(self.config.min_order_notional)):
            return Decimal("0")
            
        qty = notional_target / price
        return qty

    def clamp_stop_loss(self, entry_price: Decimal, side: str, desired_sl_pct: Decimal | None = None) -> Decimal:
        """
        Calculate and clamp stop-loss to ensure it triggers before exchange liquidation.
        Exchange liquidation with 20x leverage occurs at approximately 5% adverse movement.
        """
        sl_pct = desired_sl_pct if desired_sl_pct is not None else Decimal(str(self.config.default_sl_pct))
        
        # Clamp SL to maximum 4.5% to avoid liquidation at 5% (assuming 20x leverage)
        clamped_sl_pct = min(sl_pct, Decimal("0.045"))
        
        if side.upper() == "SELL":
            return entry_price * (Decimal("1") - clamped_sl_pct)
        else:
            return entry_price * (Decimal("1") + clamped_sl_pct)

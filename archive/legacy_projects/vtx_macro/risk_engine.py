"""
VTX Macro Risk Engine
Validates trade parameters and risk bounds before routing orders to Hyperliquid perps.
"""

import json
import os
from typing import Dict, Any, Tuple

class RiskEngine:
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "vtx_config.json")
        
        self.config = self._load_config(config_path)

    def _load_config(self, path: str) -> Dict[str, Any]:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return {
            "max_leverage": 5,
            "max_position_size_usd": 1000.0,
            "max_slippage_pct": 0.01,
            "stop_loss_required": True,
            "max_daily_drawdown_usd": 200.0,
            "allowed_symbols": ["BTC", "ETH", "SOL", "HYPE", "AVAX"]
        }

    def validate_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        leverage: int,
        stop_loss: float = None,
        take_profit: float = None
    ) -> Tuple[bool, str]:
        """
        Validates order parameters against strict VTX risk rules.
        Returns (is_valid: bool, reason: str).
        """
        side = side.lower()
        if side not in ["buy", "sell", "long", "short"]:
            return False, f"Invalid order side '{side}'. Must be buy/sell or long/short."

        # Check allowed symbol
        symbol_upper = symbol.upper()
        allowed = self.config.get("allowed_symbols", [])
        if allowed and symbol_upper not in allowed:
            return False, f"Symbol '{symbol}' is not in allowed VTX risk symbols: {allowed}"

        # Check leverage
        max_lev = self.config.get("max_leverage", 5)
        if leverage > max_lev:
            return False, f"Requested leverage {leverage}x exceeds maximum allowed leverage of {max_lev}x."

        # Check order USD size
        notional_usd = size * price
        max_usd = self.config.get("max_position_size_usd", 1000.0)
        if notional_usd > max_usd:
            return False, f"Order value ${notional_usd:.2f} exceeds maximum position size of ${max_usd:.2f}."

        # Check stop loss requirement
        sl_required = self.config.get("stop_loss_required", True)
        if sl_required and (stop_loss is None or stop_loss <= 0):
            return False, "Stop loss is strictly required by VTX Macro risk policy."

        # Validate stop loss direction
        if stop_loss is not None and stop_loss > 0:
            if side in ["buy", "long"] and stop_loss >= price:
                return False, f"Long stop loss ({stop_loss}) must be lower than entry price ({price})."
            if side in ["sell", "short"] and stop_loss <= price:
                return False, f"Short stop loss ({stop_loss}) must be higher than entry price ({price})."

        # Validate take profit direction
        if take_profit is not None and take_profit > 0:
            if side in ["buy", "long"] and take_profit <= price:
                return False, f"Long take profit ({take_profit}) must be higher than entry price ({price})."
            if side in ["sell", "short"] and take_profit >= price:
                return False, f"Short take profit ({take_profit}) must be lower than entry price ({price})."

        return True, "Order risk parameters validated successfully."

if __name__ == "__main__":
    engine = RiskEngine()
    print("Testing RiskEngine validation...")
    valid, reason = engine.validate_order("BTC", "buy", 0.01, 90000, leverage=3, stop_loss=88000, take_profit=95000)
    print(f"Test 1 (Valid Order): {valid} -> {reason}")
    
    valid, reason = engine.validate_order("BTC", "buy", 0.1, 90000, leverage=10, stop_loss=88000)
    print(f"Test 2 (High Leverage): {valid} -> {reason}")

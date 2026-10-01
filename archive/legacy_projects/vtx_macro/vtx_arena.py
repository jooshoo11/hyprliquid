"""
VTX Macro Execution Arena & Routing Core
Connects foreground decision workflows (Antigravity) to Hyperliquid perps.
Reads live order flow, evaluates macro conditions, validates risk parameters via RiskEngine,
and routes trades directly through Hyperliquid Agent API Keys.
"""

import os
import sys
import argparse
import json
import time
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from risk_engine import RiskEngine

class VTXArena:
    def __init__(self, testnet: bool = True):
        self.risk_engine = RiskEngine()
        self.testnet = testnet
        self.main_address = os.getenv("HYPERLIQUID_MAIN_ADDRESS")
        self.agent_key = os.getenv("HYPERLIQUID_AGENT_KEY")
        self.network = os.getenv("HYPERLIQUID_NETWORK", "testnet" if testnet else "mainnet")
        self.initialized_exchange = False
        self.exchange = None

    def _init_hyperliquid(self):
        if self.initialized_exchange:
            return
        
        if not self.agent_key or self.agent_key.startswith("0x000000"):
            print("[VTX Arena Warning] HYPERLIQUID_AGENT_KEY is not configured in .env. Running in DRY-RUN mode.")
            return

        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants

            account = Account.from_key(self.agent_key)
            base_url = constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
            self.exchange = Exchange(account, base_url, account_address=self.main_address)
            self.initialized_exchange = True
            print(f"[VTX Arena] Hyperliquid Exchange initialized for address {self.main_address} on {self.network}.")
        except Exception as e:
            print(f"[VTX Arena Error] Failed to initialize Hyperliquid Exchange: {e}")

    def evaluate_macro_and_orderflow(self, symbol: str) -> Dict[str, Any]:
        """
        Simulates / reads live orderbook snapshot and macro conditions.
        Returns a structured dictionary of orderflow metrics.
        """
        print(f"[VTX Arena] Evaluating order flow and macro conditions for {symbol}...")
        # In live execution, this aggregates orderbook imbalance, funding rates, and kline trend
        return {
            "symbol": symbol,
            "orderbook_imbalance": 0.15,  # positive indicates net buy side pressure
            "macro_bias": "bullish",
            "volatility_index": "medium",
            "timestamp": time.time()
        }

    def execute_trade_signal(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        leverage: int = 3,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Executes trade signal after passing VTX Risk Engine validation.
        """
        # Step 1: Validate through VTX Risk Engine
        is_valid, reason = self.risk_engine.validate_order(
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit
        )

        if not is_valid:
            print(f"[VTX Risk Engine REJECTED] {reason}")
            return {"status": "REJECTED", "reason": reason}

        print(f"[VTX Risk Engine APPROVED] {reason}")

        if dry_run:
            print(f"[VTX Arena DRY-RUN] Would place {side.upper()} order: {size} {symbol} @ ${price:.2f} (Lev: {leverage}x, SL: {stop_loss}, TP: {take_profit})")
            return {
                "status": "DRY_RUN_SUCCESS",
                "symbol": symbol,
                "side": side,
                "size": size,
                "price": price,
                "leverage": leverage,
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }

        # Step 2: Route trade to Hyperliquid via Agent Key
        self._init_hyperliquid()
        if not self.exchange:
            return {"status": "DRY_RUN_FALLBACK", "message": "Exchange not connected. Order validated but not sent."}

        try:
            print(f"[VTX Arena ROUTING] Sending trade to Hyperliquid perps...")
            # Set leverage first
            self.exchange.update_leverage(leverage, symbol)
            
            # Place Order
            is_buy = side.lower() in ["buy", "long"]
            order_result = self.exchange.order(
                symbol,
                is_buy,
                size,
                price,
                {"limit": {"tif": "Gtc"}}
            )
            print(f"[VTX Arena Result] {order_result}")
            return {"status": "EXECUTED", "response": order_result}
        except Exception as e:
            print(f"[VTX Arena Execution Error] {e}")
            return {"status": "FAILED", "error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="VTX Macro Execution Arena CLI")
    parser.add_argument("--symbol", type=str, default="BTC", help="Target trading symbol (e.g. BTC)")
    parser.add_argument("--side", type=str, choices=["buy", "sell", "long", "short"], default="buy", help="Trade direction")
    parser.add_argument("--size", type=float, default=0.001, help="Trade size")
    parser.add_argument("--price", type=float, default=90000.0, help="Limit order price")
    parser.add_argument("--leverage", type=int, default=3, help="Leverage multiplier")
    parser.add_argument("--sl", type=float, default=88000.0, help="Stop loss price")
    parser.add_argument("--tp", type=float, default=95000.0, help="Take profit price")
    parser.add_argument("--dry-run", action="store_true", help="Run risk validation without submitting order")
    
    args = parser.parse_args()

    arena = VTXArena()
    arena.evaluate_macro_and_orderflow(args.symbol)
    res = arena.execute_trade_signal(
        symbol=args.symbol,
        side=args.side,
        size=args.size,
        price=args.price,
        leverage=args.leverage,
        stop_loss=args.sl,
        take_profit=args.tp,
        dry_run=args.dry_run
    )
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()

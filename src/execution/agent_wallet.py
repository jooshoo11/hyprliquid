"""
Native Hyperliquid Agent Wallet & Execution Engine (src/execution/agent_wallet.py)
Powered by official hyperliquid-dex/hyperliquid-python-sdk.

Provides:
- Non-custodial EIP-712 order signing via delegated agent API keys (cannot withdraw funds)
- Account state, margin summary, and open order queries
- Seamless fallback between live exchange connectivity and paper simulation
"""

import os
from typing import Dict, Any, List, Optional, Tuple

try:
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    import eth_account
    from eth_account.signers.local import LocalAccount
    HL_SDK_AVAILABLE = True
except ImportError:
    Info = None
    Exchange = None
    constants = None
    eth_account = None
    LocalAccount = None
    HL_SDK_AVAILABLE = False


class HyperliquidAgentWallet:
    """
    Manager for Hyperliquid Agent API keys, supporting non-custodial delegated order execution.
    """

    def __init__(
        self,
        main_address: Optional[str] = None,
        agent_key: Optional[str] = None,
        network: str = "testnet",
        base_url: Optional[str] = None,
    ):
        self.main_address = main_address or os.getenv("HYPERLIQUID_MAIN_ADDRESS")
        self.agent_key = agent_key or os.getenv("HYPERLIQUID_AGENT_KEY")
        self.network = (network or os.getenv("HYPERLIQUID_NETWORK", "testnet")).lower()

        # Check if credentials are placeholder zeros
        self.is_configured = bool(
            self.main_address
            and self.agent_key
            and not self.main_address.startswith("0x0000000000000000")
            and not self.agent_key.startswith("0x0000000000000000")
        )

        self.base_url = base_url or (
            constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
        ) if constants else None

        self.info: Optional[Info] = None
        self.exchange: Optional[Exchange] = None
        self.wallet: Optional[LocalAccount] = None

        if HL_SDK_AVAILABLE and self.base_url:
            try:
                self.info = Info(self.base_url, skip_ws=True)
            except Exception as e:
                print(f"[Agent Wallet] Info initialization notice: {e}")

        if HL_SDK_AVAILABLE and self.is_configured and self.base_url:
            try:
                self.wallet = eth_account.Account.from_key(self.agent_key)
                self.exchange = Exchange(
                    wallet=self.wallet,
                    base_url=self.base_url,
                    account_address=self.main_address,
                )
            except Exception as e:
                print(f"[Agent Wallet] Exchange signer initialization notice: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Diagnostic summary of Hyperliquid SDK connection."""
        return {
            "sdk_installed": HL_SDK_AVAILABLE,
            "configured": self.is_configured,
            "network": self.network,
            "base_url": self.base_url,
            "agent_address": self.wallet.address if self.wallet else None,
            "main_address": self.main_address,
            "mode": "LIVE_AGENT_KEY" if (self.exchange and self.is_configured) else "PAPER_LOCAL_EMULATION",
        }

    def fetch_user_state(self) -> Optional[Dict[str, Any]]:
        """Fetch live margin balance and active positions from exchange."""
        if not self.info or not self.main_address or not self.is_configured:
            return None
        try:
            return self.info.user_state(self.main_address)
        except Exception as e:
            print(f"[Agent Wallet] fetch_user_state error: {e}")
            return None

    def fetch_open_orders(self) -> List[Dict[str, Any]]:
        """Fetch open orders for the account from exchange."""
        if not self.info or not self.main_address or not self.is_configured:
            return []
        try:
            orders = self.info.open_orders(self.main_address)
            return orders if isinstance(orders, list) else []
        except Exception as e:
            print(f"[Agent Wallet] fetch_open_orders error: {e}")
            return []

    def place_order(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        limit_px: float,
        order_type: Dict[str, Any] = None,
        reduce_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute an order using the non-custodial agent key.
        """
        if not self.exchange or not self.is_configured:
            return {"status": "MOCK_PAPER_FILLED", "coin": coin, "is_buy": is_buy, "size": size, "limit_px": limit_px}

        order_type = order_type or {"limit": {"tif": "Gtc"}}
        try:
            res = self.exchange.order(
                name=coin.upper(),
                is_buy=is_buy,
                sz=size,
                limit_px=limit_px,
                order_type=order_type,
                reduce_only=reduce_only,
            )
            return res
        except Exception as e:
            print(f"[Agent Wallet] Order placement error on {coin}: {e}")
            return None

    def cancel_order(self, coin: str, order_id: int) -> Optional[Dict[str, Any]]:
        """Cancel an open order via the exchange client."""
        if not self.exchange or not self.is_configured:
            return {"status": "MOCK_CANCELLED", "coin": coin, "order_id": order_id}

        try:
            return self.exchange.cancel(coin.upper(), order_id)
        except Exception as e:
            print(f"[Agent Wallet] Cancel error on {coin} order {order_id}: {e}")
            return None

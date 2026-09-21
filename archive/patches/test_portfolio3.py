import asyncio
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.objects import MarginAccount, AccountState
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.currencies import USD

node = TradingNode(config=TradingNodeConfig())
node.portfolio.update_account(AccountState(
    account_id=AccountId("HYPERLIQUID-MARGIN"),
    base_currency=USD,
    balances=[],
))
account = node.portfolio.account(AccountId("HYPERLIQUID-MARGIN"))
print(dir(account))

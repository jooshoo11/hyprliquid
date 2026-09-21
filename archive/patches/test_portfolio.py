import asyncio
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode

node = TradingNode(config=TradingNodeConfig())
print(dir(node.trader.portfolio))

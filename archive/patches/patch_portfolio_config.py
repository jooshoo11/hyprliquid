import re
with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

# Make sure PortfolioConfig is imported
if "PortfolioConfig" not in code:
    code = code.replace("from nautilus_trader.config import TradingNodeConfig", "from nautilus_trader.config import TradingNodeConfig, PortfolioConfig")

# Add portfolio=PortfolioConfig() to TradingNodeConfig
old_node = """        self.node = TradingNode(
            config=TradingNodeConfig(
                trader_id=self.trader_id,
                data_clients={"HYPERLIQUID_DATA": HyperliquidDataClientConfig(environment="mainnet")},
            )
        )"""
new_node = """        self.node = TradingNode(
            config=TradingNodeConfig(
                trader_id=self.trader_id,
                data_clients={"HYPERLIQUID_DATA": HyperliquidDataClientConfig(environment="mainnet")},
                portfolio=PortfolioConfig(),
            )
        )"""

code = code.replace(old_node, new_node)

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("patched PortfolioConfig!")

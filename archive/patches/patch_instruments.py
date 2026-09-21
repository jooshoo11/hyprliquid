import re

with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

old_config = """
        env = HyperliquidEnvironment.MAINNET if self.paper else HyperliquidEnvironment.TESTNET
        data_cfg = HyperliquidDataClientConfig(
            environment=env,
            instrument_provider=InstrumentProviderConfig(load_all=False),
        )
        
        exec_clients = {}
        if not self.paper:
            exec_cfg = HyperliquidExecClientConfig(
                environment=env,
                account_address=self.wallet_address,
                private_key=self._private_key,
                instrument_provider=InstrumentProviderConfig(load_all=False),
            )
"""

new_config = """
        env = HyperliquidEnvironment.MAINNET if self.paper else HyperliquidEnvironment.TESTNET
        
        target_ids = frozenset([InstrumentId(Symbol(f"{coin}-USD-PERP"), Venue("HYPERLIQUID")) for coin in self.top_coins])
        
        data_cfg = HyperliquidDataClientConfig(
            environment=env,
            instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=target_ids),
        )
        
        exec_clients = {}
        if not self.paper:
            exec_cfg = HyperliquidExecClientConfig(
                environment=env,
                account_address=self.wallet_address,
                private_key=self._private_key,
                instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=target_ids),
            )
"""

code = code.replace(old_config, new_config)

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("patched!")

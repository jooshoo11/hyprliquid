import re

with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

# 1. Update create_surveillance_table signature
sig_old = """
    node: Optional[TradingNode],
    wallet_address: str,
    is_ephemeral: bool,
) -> Table:
"""

sig_new = """
    node: Optional[TradingNode],
    wallet_address: str,
    is_ephemeral: bool,
    is_paper: bool = False,
) -> Table:
"""
code = code.replace(sig_old.strip(), sig_new.strip())

# 2. Update caption logic inside create_surveillance_table
caption_old = """
    endpoint = "api.hyperliquid.xyz" if is_ephemeral else "api.hyperliquid-testnet.xyz"
    if hasattr(node.config, 'exec_clients') and not node.config.exec_clients:
        endpoint = "api.hyperliquid.xyz (MAINNET DATA + LOCAL EMULATOR)"
        
    table.caption = (
        f"Node Wallet: {wallet_address[:8]}...{wallet_address[-6:]} ({wallet_type}) | "
        f"Endpoint: {endpoint}\\n"
"""

caption_new = """
    endpoint = "api.hyperliquid.xyz (MAINNET DATA + LOCAL EMULATOR)" if is_paper else ("api.hyperliquid-testnet.xyz" if is_ephemeral else "api.hyperliquid-testnet.xyz")
    table.caption = (
        f"Node Wallet: {wallet_address[:8]}...{wallet_address[-6:]} ({wallet_type}) | "
        f"Endpoint: {endpoint}\\n"
"""
code = code.replace(caption_old.strip(), caption_new.strip())

# 3. Update the two places where create_surveillance_table is called in run()
run_old = """
                self.node,
                self.wallet_address,
                self.is_ephemeral,
            ),
"""

run_new = """
                self.node,
                self.wallet_address,
                self.is_ephemeral,
                self.paper,
            ),
"""
code = code.replace(run_old.strip(), run_new.strip())

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("UI paper patched!")

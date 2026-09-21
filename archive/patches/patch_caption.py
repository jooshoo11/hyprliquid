import re

with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

old_caption = """
    table.caption = (
        f"Node Wallet: {wallet_address[:8]}...{wallet_address[-6:]} ({wallet_type}) | "
        f"Endpoint: Hyperliquid Testnet (api.hyperliquid-testnet.xyz)\\n"
"""

new_caption = """
    endpoint = "api.hyperliquid.xyz" if is_ephemeral else "api.hyperliquid-testnet.xyz"
    if hasattr(node.config, 'exec_clients') and not node.config.exec_clients:
        endpoint = "api.hyperliquid.xyz (MAINNET DATA + LOCAL EMULATOR)"
        
    table.caption = (
        f"Node Wallet: {wallet_address[:8]}...{wallet_address[-6:]} ({wallet_type}) | "
        f"Endpoint: {endpoint}\\n"
"""

code = code.replace(old_caption.strip(), new_caption.strip())

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("caption patched!")

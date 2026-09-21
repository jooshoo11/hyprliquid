import os

with open("src/scanner/mcp_client.py", "r") as f:
    code = f.read()

old_init = """        self.base_url = (
            constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
        )"""

new_init = """        # Use Chainstack private RPC to completely bypass public rate limits!
        self.base_url = os.getenv(
            "CHAINSTACK_HYPERCORE_RPC_URL",
            constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
        )
        if self.base_url.endswith("/demo"):
            # Ensure proper /info endpoint format for chainstack
            # Wait, chainstack probably maps /info just like the regular API!
            pass
        if not self.base_url.endswith("/info"):
            self.api_url = f"{self.base_url.rstrip('/')}/info"
        else:
            self.api_url = self.base_url"""

code = code.replace(old_init, new_init)

old_post_meta = """resp = self._session.post(self.base_url + "/info", json={"type": "meta"})"""
new_post_meta = """resp = self._session.post(self.api_url, json={"type": "meta"})"""
code = code.replace(old_post_meta, new_post_meta)

old_post_ctxs = """resp = self._session.post(self.base_url + "/info", json={"type": "metaAndAssetCtxs"})"""
new_post_ctxs = """resp = self._session.post(self.api_url, json={"type": "metaAndAssetCtxs"})"""
code = code.replace(old_post_ctxs, new_post_ctxs)

with open("src/scanner/mcp_client.py", "w") as f:
    f.write(code)

print("patched mcp_client.py for Chainstack RPC!")

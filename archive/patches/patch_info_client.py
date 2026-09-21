with open("src/scanner/mcp_client.py", "r") as f:
    code = f.read()

import re

# Remove Info import
code = re.sub(r"from hyperliquid\.info import Info\n", "", code)

old_init = """    def __init__(self, network: Optional[str] = None):
        self.network = network or os.getenv("HYPERLIQUID_NETWORK", "mainnet")
        self.base_url = (
            constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
        )
        self._info = Info(self.base_url, skip_ws=True)"""

new_init = """    def __init__(self, network: Optional[str] = None):
        self.network = network or os.getenv("HYPERLIQUID_NETWORK", "mainnet")
        self.base_url = (
            constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
        )
        import requests
        self._session = requests.Session()"""

code = code.replace(old_init, new_init)

old_meta = """    def get_meta(self) -> Dict[str, Any]:
        \"\"\"Fetch market universe metadata.\"\"\"
        return self._info.meta()

    def get_meta_and_asset_ctxs(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        \"\"\"Fetch universe definitions and real-time asset contexts (volume, mark price, OI).\"\"\"
        contexts = self._info.meta_and_asset_ctxs()
        return contexts[0], contexts[1]"""

new_meta = """    def get_meta(self) -> Dict[str, Any]:
        \"\"\"Fetch market universe metadata.\"\"\"
        resp = self._session.post(self.base_url + "/info", json={"type": "meta"})
        return resp.json()

    def get_meta_and_asset_ctxs(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        \"\"\"Fetch universe definitions and real-time asset contexts (volume, mark price, OI).\"\"\"
        resp = self._session.post(self.base_url + "/info", json={"type": "metaAndAssetCtxs"})
        data = resp.json()
        return data[0], data[1]"""

code = code.replace(old_meta, new_meta)

with open("src/scanner/mcp_client.py", "w") as f:
    f.write(code)

print("Patched HyperliquidInfoClient to use direct requests!")

import re

with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

# 1. Add args.paper to __init__ and setup
init_old = """
    def __init__(
        self,
        top_n: int = 20,
        trader_id: str = "HL-TESTNET-001",
        risk_pct: float = 0.01,
        rr_ratio: float = 2.5,
    ):
"""

init_new = """
    def __init__(
        self,
        top_n: int = 20,
        trader_id: str = "HL-NODE-001",
        risk_pct: float = 0.01,
        rr_ratio: float = 2.5,
        paper: bool = False,
    ):
"""
code = code.replace(init_old, init_new)

# 2. Store self.paper
store_old = """
        self.risk_pct = risk_pct
        self.rr_ratio = rr_ratio

        self.info_client = HyperliquidInfoClient(network="testnet")
"""

store_new = """
        self.risk_pct = risk_pct
        self.rr_ratio = rr_ratio
        self.paper = paper

        self.info_client = HyperliquidInfoClient(network="mainnet" if paper else "testnet")
"""
code = code.replace(store_old, store_new)

# 3. Modify setup()
setup_old = """
        console.print(f"[bold cyan]🚀 Initializing Hyperliquid Multi-Strategy Testnet Node...[/bold cyan]")
        if self.is_ephemeral:
            console.print(f"[bold yellow]🔑 Generated ephemeral burner wallet: {self.wallet_address}[/bold yellow]")
        else:
            console.print(f"[bold green]🔑 Loaded wallet from environment: {self.wallet_address}[/bold green]")

        console.print(f"[dim]Endpoint: https://api.hyperliquid-testnet.xyz[/dim]")

        # Fetch top perpetuals via public unauthenticated API
        top_markets = self.info_client.get_top_perpetuals(top_n=self.top_n)
        self.top_coins = [m["name"] for m in top_markets]
        console.print(f"[green]Screened {len(self.top_coins)} top perpetuals for surveillance.[/green]")

        # Configure Hyperliquid Data & Exec Clients targeting Testnet
        data_cfg = HyperliquidDataClientConfig(
            environment=HyperliquidEnvironment.TESTNET,
            instrument_provider=InstrumentProviderConfig(load_all=False),
        )
        exec_cfg = HyperliquidExecClientConfig(
            environment=HyperliquidEnvironment.TESTNET,
            account_address=self.wallet_address,
            private_key=self._private_key,
            instrument_provider=InstrumentProviderConfig(load_all=False),
        )

        node_config = TradingNodeConfig(
            trader_id=self.trader_id,
            data_clients={"HYPERLIQUID_DATA": data_cfg},
            exec_clients={"HYPERLIQUID_EXEC": exec_cfg},
            emulator=OrderEmulatorConfig(),
            logging=LoggingConfig(log_level="INFO"),
        )

        self.node = TradingNode(config=node_config)
        del self._private_key
        self.node.add_data_client_factory("HYPERLIQUID_DATA", HyperliquidLiveDataClientFactory)
        self.node.add_exec_client_factory("HYPERLIQUID_EXEC", HyperliquidLiveExecClientFactory)
"""

setup_new = """
        mode_str = "MAINNET (EMULATED PAPER)" if self.paper else "TESTNET (BURNER KEY)"
        console.print(f"[bold cyan]🚀 Initializing Hyperliquid Multi-Strategy Node - {mode_str}...[/bold cyan]")
        if not self.paper:
            if self.is_ephemeral:
                console.print(f"[bold yellow]🔑 Generated ephemeral burner wallet: {self.wallet_address}[/bold yellow]")
            else:
                console.print(f"[bold green]🔑 Loaded wallet from environment: {self.wallet_address}[/bold green]")
        else:
            console.print(f"[bold yellow]🛡️ Running purely local emulated execution. No keys required![/bold yellow]")

        # Fetch top perpetuals via public unauthenticated API
        top_markets = self.info_client.get_top_perpetuals(top_n=self.top_n)
        self.top_coins = [m["name"] for m in top_markets]
        console.print(f"[green]Screened {len(self.top_coins)} top perpetuals for surveillance.[/green]")

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
            exec_clients["HYPERLIQUID_EXEC"] = exec_cfg

        node_config = TradingNodeConfig(
            trader_id=self.trader_id,
            data_clients={"HYPERLIQUID_DATA": data_cfg},
            exec_clients=exec_clients,
            emulator=OrderEmulatorConfig(),
            logging=LoggingConfig(log_level="INFO"),
        )

        self.node = TradingNode(config=node_config)
        del self._private_key
        self.node.add_data_client_factory("HYPERLIQUID_DATA", HyperliquidLiveDataClientFactory)
        if not self.paper:
            self.node.add_exec_client_factory("HYPERLIQUID_EXEC", HyperliquidLiveExecClientFactory)
"""

code = code.replace(setup_old, setup_new)

# 4. Modify args parsing in main()
main_old = """
    parser.add_argument("--top-n", type=int, default=20, help="Number of perpetual markets to monitor")
    parser.add_argument("--duration", type=int, default=None, help="Optional duration in seconds (for test/demo)")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="Risk percentage per trade")
    parser.add_argument("--rr-ratio", type=float, default=2.5, help="Reward-to-risk ratio")
    args = parser.parse_args()

    runner = HyperliquidNodeRunner(
        top_n=args.top_n,
        risk_pct=args.risk_pct,
        rr_ratio=args.rr_ratio,
    )
"""

main_new = """
    parser.add_argument("--top-n", type=int, default=20, help="Number of perpetual markets to monitor")
    parser.add_argument("--duration", type=int, default=None, help="Optional duration in seconds (for test/demo)")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="Risk percentage per trade")
    parser.add_argument("--rr-ratio", type=float, default=2.5, help="Reward-to-risk ratio")
    parser.add_argument("--paper", action="store_true", help="Use local OrderEmulator on Mainnet data instead of Testnet Execution")
    args = parser.parse_args()

    runner = HyperliquidNodeRunner(
        top_n=args.top_n,
        risk_pct=args.risk_pct,
        rr_ratio=args.rr_ratio,
        paper=args.paper,
    )
"""

code = code.replace(main_old, main_new)

# 5. Modify terminal output wording
table_title_old = 'title="HYPERLIQUID TESTNET — MULTI-STRATEGY QUANTITATIVE NODE SURVEILLANCE",'
table_title_new = 'title="HYPERLIQUID QUANTITATIVE NODE SURVEILLANCE",'
code = code.replace(table_title_old, table_title_new)

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("added --paper flag!")

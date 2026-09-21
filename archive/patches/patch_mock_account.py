with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

setup_str = """        console.print("[yellow]Building TradingNode core actor network and message bus...[/yellow]")
        self.node.build()"""

new_setup_str = """        console.print("[yellow]Building TradingNode core actor network and message bus...[/yellow]")
        self.node.build()

        if self.paper:
            import time
            from nautilus_trader.core.uuid import UUID4
            from nautilus_trader.accounting.accounts.margin import MarginAccount
            from nautilus_trader.model.identifiers import AccountId
            from nautilus_trader.model.enums import AccountType
            from nautilus_trader.model.currencies import USD
            from nautilus_trader.model.objects import MarginBalance, Money, AccountBalance
            from nautilus_trader.model.events.account import AccountState

            mock_state = AccountState(
                AccountId("PAPER-001"),
                AccountType.MARGIN,
                USD,
                False,
                [AccountBalance(Money(100.0, USD), Money(0.0, USD), Money(100.0, USD))],
                [MarginBalance(Money(100.0, USD), Money(100.0, USD))],
                {},
                UUID4(),
                int(time.time()*10**9),
                int(time.time()*10**9)
            )
            mock_account = MarginAccount(mock_state, True)
            self.node.trader.portfolio.add_account(mock_account)
"""

code = code.replace(setup_str, new_setup_str)

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("patched node_runner setup!")

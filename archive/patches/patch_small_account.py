import re

with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

# 1. Update PortfolioGuard instantiation
old_guard = "self.guard = PortfolioGuard()"
# For a $100 account aiming for high growth, we allow 10x leverage per strategy (1000% of equity)
# And allow more max positions
new_guard = "self.guard = PortfolioGuard(max_strategy_equity_pct=10.0, max_total_open_positions=10, max_daily_drawdown_pct=0.20)"

code = code.replace(old_guard, new_guard)

# 2. Add Account injection for paper trading
# We need to import Account, AccountState, AccountId, AccountType, USD, Money, MarginBalances
# Actually nautilus_trader.model.objects Account, etc.
old_setup = """        console.print("[yellow]Building TradingNode core actor network and message bus...[/yellow]")
        self.node.build()"""

new_setup = """        console.print("[yellow]Building TradingNode core actor network and message bus...[/yellow]")
        self.node.build()
        
        if self.paper:
            # Inject a $100 mock account for local emulator
            from nautilus_trader.model.objects import Account, MarginBalances
            from nautilus_trader.model.identifiers import AccountId
            from nautilus_trader.model.enums import AccountType
            from nautilus_trader.model.currencies import USD
            from nautilus_trader.model.data import Money
            
            mock_account = Account(
                venue=Venue("HYPERLIQUID"),
                account_id=AccountId("HYPERLIQUID-PAPER"),
                account_type=AccountType.MARGIN,
                base_currency=USD,
                margins=MarginBalances(initial=Money(100.0, USD)),
            )
            self.node.trader.portfolio.add_account(mock_account)
"""

code = code.replace(old_setup, new_setup)

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("patched!")

from nautilus_trader.core.uuid import UUID4
import time

from nautilus_trader.accounting.accounts.margin import MarginAccount
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import MarginBalance, Money, AccountBalance
from nautilus_trader.model.events.account import AccountState

account_id = AccountId("HYPERLIQUID-PAPER")

balance = AccountBalance(
    Money(100.0, USD),
    Money(0.0, USD),
    Money(100.0, USD)
)

margin = MarginBalance(
    Money(100.0, USD),
    Money(100.0, USD)
)

state = AccountState(
    account_id,
    AccountType.MARGIN,
    USD,
    False,
    [balance],
    [margin],
    {},
    UUID4(),
    int(time.time()*10**9),
    int(time.time()*10**9)
)

mock_account = MarginAccount(
    state,
    True
)
print("SUCCESS!")
print(mock_account)

import asyncio
import time
from decimal import Decimal
from nautilus_trader.live.node import TradingNode
from nautilus_trader.config import TradingNodeConfig, OrderEmulatorConfig, LoggingConfig, InstrumentProviderConfig
from nautilus_trader.adapters.hyperliquid.factories import HyperliquidLiveDataClientFactory
from nautilus_trader.adapters.hyperliquid.config import HyperliquidDataClientConfig, HyperliquidEnvironment
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.model.enums import OrderSide, OrderType, AccountType
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import MarginBalance, Money, AccountBalance
from nautilus_trader.model.events.account import AccountState
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.core.uuid import UUID4

class TestStrategy(Strategy):
    def on_start(self):
        inst = list(self.cache.instruments())[0]
        self.subscribe_quote_ticks(inst.id)
        print("Subscribed to quote ticks for", inst.id)

    def on_quote_tick(self, tick: QuoteTick):
        print(f"Received quote tick: bid={tick.bid_price} ask={tick.ask_price}")
        if self.portfolio.is_flat(tick.instrument_id):
            inst = self.cache.instrument(tick.instrument_id)
            tp_px = inst.make_price(Decimal("0.006"))
            sl_px = inst.make_price(Decimal("0.004"))
            try:
                bracket_list = self.order_factory.bracket(
                    instrument_id=inst.id,
                    order_side=OrderSide.BUY,
                    quantity=inst.make_qty(Decimal("100")),
                    entry_order_type=OrderType.MARKET,
                    tp_order_type=OrderType.LIMIT,
                    tp_price=tp_px,
                    sl_order_type=OrderType.STOP_MARKET,
                    sl_trigger_price=sl_px,
                )
                print("Submitting bracket list:", bracket_list)
                self.submit_order_list(bracket_list)
            except Exception as e:
                print("Error creating/submitting bracket:", e)

    def on_order_filled(self, event):
        print("ORDER FILLED!", event)

def main():
    target_ids = frozenset([InstrumentId(Symbol("PUMP-USD-PERP"), Venue("HYPERLIQUID"))])
    data_cfg = HyperliquidDataClientConfig(
        environment=HyperliquidEnvironment.MAINNET,
        instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=target_ids),
    )
    exec_cfg = SandboxExecutionClientConfig(
        venue="HYPERLIQUID",
        starting_balances=["100 USD"],
        account_type="MARGIN",
        base_currency="USD",
    )
    node_config = TradingNodeConfig(
        trader_id="TEST-001",
        data_clients={"HYPERLIQUID_DATA": data_cfg},
        exec_clients={"HYPERLIQUID_EXEC": exec_cfg},
        emulator=OrderEmulatorConfig(),
        logging=LoggingConfig(log_level="INFO"),
    )
    node = TradingNode(config=node_config)
    node.add_data_client_factory("HYPERLIQUID_DATA", HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory("HYPERLIQUID_EXEC", SandboxLiveExecClientFactory)
    node.build()

    mock_state = AccountState(
        AccountId("HYPERLIQUID-001"),
        AccountType.MARGIN,
        USD,
        False,
        [AccountBalance(Money(100.0, USD), Money(0.0, USD), Money(100.0, USD))],
        [MarginBalance(Money(100.0, USD), Money(100.0, USD))],
        {},
        UUID4(),
        int(time.time() * 10**9),
        int(time.time() * 10**9),
    )
    node.portfolio.update_account(mock_state)

    strat = TestStrategy(StrategyConfig())
    node.trader.add_strategy(strat)

    import threading
    t = threading.Thread(target=node.run, daemon=True)
    t.start()

    time.sleep(5)
    print("Open positions count:", node.cache.positions_open_count())
    print("Open orders count:", len(node.cache.orders_open()))
    print("All orders:", len(node.cache.orders()))
    node.stop()

if __name__ == "__main__":
    from nautilus_trader.model.identifiers import AccountId
    main()

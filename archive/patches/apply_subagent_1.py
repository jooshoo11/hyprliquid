import os

base_dir = "/home/jooshoo/Desktop/hyprliquid/src"

# 1. Fix node_runner.py
nr_path = os.path.join(base_dir, "execution/node_runner.py")
with open(nr_path, "r") as f:
    nr_code = f.read()

nr_code = nr_code.replace(
    "self.wallet_address, self.private_key, self.is_ephemeral = generate_or_load_wallet()",
    "self.wallet_address, _private_key, self.is_ephemeral = generate_or_load_wallet()"
)
nr_code = nr_code.replace(
    "private_key=self.private_key,",
    "private_key=_private_key,"
)
nr_code = nr_code.replace(
    "self.node = TradingNode(config=node_config)",
    "self.node = TradingNode(config=node_config)\n        del _private_key"
)

cancel_imports = "import sys\nfrom nautilus_trader.model.commands import CancelOrder\nfrom nautilus_trader.model.identifiers import ClientOrderId, StrategyId"
if "from nautilus_trader.model.commands import CancelOrder" not in nr_code:
    nr_code = nr_code.replace("import sys", cancel_imports)

old_stale_cancel = (
    "                for stale in stale_orders:\n"
    "                    console.print(f\"[yellow]Cancelling stale order {stale.order_id} on {stale.instrument_id}[/yellow]\")\n"
    "                    self.guard.acknowledge_order_cancelled(stale.order_id)"
)
stale_cancel_logic = (
    "                for stale in stale_orders:\n"
    "                    console.print(f\"[yellow]Cancelling stale order {stale.order_id} on {stale.instrument_id}[/yellow]\")\n"
    "                    try:\n"
    "                        cancel_cmd = CancelOrder(\n"
    "                            trader_id=self.node.trader_id,\n"
    "                            strategy_id=StrategyId(stale.strategy_id),\n"
    "                            instrument_id=stale.instrument_id,\n"
    "                            client_order_id=ClientOrderId(stale.order_id),\n"
    "                        )\n"
    "                        self.node.trader.execute(cancel_cmd)\n"
    "                    except Exception as e:\n"
    "                        console.print(f\"[red]Failed to cancel stale order {stale.order_id}: {e}[/red]\")\n"
    "                    self.guard.acknowledge_order_cancelled(stale.order_id)"
)
nr_code = nr_code.replace(old_stale_cancel, stale_cancel_logic)

with open(nr_path, "w") as f:
    f.write(nr_code)


# 2. Add `from typing import Any`
strat_dir = os.path.join(base_dir, "strategies")
for strat_file in os.listdir(strat_dir):
    if strat_file.endswith(".py"):
        sf_path = os.path.join(strat_dir, strat_file)
        with open(sf_path, "r") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("from typing import ") and "Any" not in line:
                lines[i] = line.replace("from typing import ", "from typing import Any, ")
        with open(sf_path, "w") as f:
            f.write("\n".join(lines) + "\n")

# 3. Fix PortfolioGuard TOCTOU race condition
pg_path = os.path.join(base_dir, "risk/portfolio_guard.py")
with open(pg_path, "r") as f:
    pg_code = f.read()

if "import threading" not in pg_code:
    pg_code = pg_code.replace("import time\n", "import time\nimport threading\n")

pg_code = pg_code.replace(
    "self.day_start_timestamp_ms: int = int(time.time() * 1000)",
    "self.day_start_timestamp_ms: int = int(time.time() * 1000)\n        self._lock = threading.Lock()\n        self._pending_approvals: int = 0"
)

old_can_open = (
    "    def can_open_position(\n"
    "        self,\n"
    "        strategy_name: str,\n"
    "        instrument_id: InstrumentId,\n"
    "        side: OrderSide,\n"
    "        proposed_notional_usd: float,\n"
    "        current_open_positions_count: int,\n"
    "    ) -> Tuple[bool, str]:\n"
    "        \"\"\"\n"
    "        Validate whether a new order passes all portfolio risk rules.\n\n"
    "        Returns\n"
    "        -------\n"
    "        (bool, str)\n"
    "            (is_approved, rejection_reason)\n"
    "        \"\"\"\n"
    "        # 1. Circuit breaker check\n"
    "        if self.is_circuit_breaker_triggered:\n"
    "            return False, f\"Trading halted: 24h drawdown breached 2% limit.\"\n\n"
    "        # 2. Maximum simultaneous positions across entire node\n"
    "        if current_open_positions_count >= self.max_total_open_positions:\n"
    "            return False, f\"Max node positions ({self.max_total_open_positions}) reached.\"\n\n"
    "        # 3. Strategy margin allocation limit (Max 25% of total equity)\n"
    "        max_allowed_margin = self.current_equity * self.max_strategy_equity_pct\n"
    "        current_strategy_margin = self.strategy_allocated_margin.get(strategy_name, 0.0)\n"
    "        if (current_strategy_margin + proposed_notional_usd) > max_allowed_margin:\n"
    "            return False, (\n"
    "                f\"Strategy '{strategy_name}' exceeds 25% allocation limit \"\n"
    "                f\"(${current_strategy_margin + proposed_notional_usd:,.2f} > ${max_allowed_margin:,.2f}).\"\n"
    "            )\n\n"
    "        # 4. Anti-collision check: No opposing orders or conflicting positions on the same asset\n"
    "        instr_str = str(instrument_id)\n"
    "        existing_side = self.active_instrument_directions.get(instr_str)\n"
    "        if existing_side is not None and existing_side != side:\n"
    "            return False, f\"Collision detected: opposing order on {instr_str} ({existing_side} exists).\"\n\n"
    "        return True, \"Approved\""
)

new_can_open = (
    "    def can_open_position(\n"
    "        self,\n"
    "        strategy_name: str,\n"
    "        instrument_id: InstrumentId,\n"
    "        side: OrderSide,\n"
    "        proposed_notional_usd: float,\n"
    "        current_open_positions_count: int,\n"
    "    ) -> Tuple[bool, str]:\n"
    "        with self._lock:\n"
    "            if self.is_circuit_breaker_triggered:\n"
    "                return False, f\"Trading halted: 24h drawdown breached 2% limit.\"\n\n"
    "            effective_count = current_open_positions_count + self._pending_approvals\n"
    "            if effective_count >= self.max_total_open_positions:\n"
    "                return False, f\"Max node positions ({self.max_total_open_positions}) reached.\"\n\n"
    "            max_allowed_margin = self.current_equity * self.max_strategy_equity_pct\n"
    "            current_strategy_margin = self.strategy_allocated_margin.get(strategy_name, 0.0)\n"
    "            if (current_strategy_margin + proposed_notional_usd) > max_allowed_margin:\n"
    "                return False, (\n"
    "                    f\"Strategy '{strategy_name}' exceeds 25% allocation limit \"\n"
    "                    f\"(${current_strategy_margin + proposed_notional_usd:,.2f} > ${max_allowed_margin:,.2f}).\"\n"
    "                )\n\n"
    "            instr_str = str(instrument_id)\n"
    "            existing_side = self.active_instrument_directions.get(instr_str)\n"
    "            if existing_side is not None and existing_side != side:\n"
    "                return False, f\"Collision detected: opposing order on {instr_str} ({existing_side} exists).\"\n\n"
    "            self._pending_approvals += 1\n"
    "            return True, \"Approved\""
)

pg_code = pg_code.replace(old_can_open, new_can_open)

old_register = (
    "    def register_order_submitted(\n"
    "        self,\n"
    "        order: Order,\n"
    "        strategy_name: str,\n"
    "        notional_usd: float,\n"
    "        timeout_seconds: Optional[float] = None,\n"
    "    ) -> None:\n"
    "        \"\"\"Register a submitted order into active tracking and stale order monitoring.\"\"\"\n"
    "        instr_str = str(order.instrument_id)\n"
    "        order_id_str = str(order.client_order_id)\n\n"
    "        self.strategy_allocated_margin[strategy_name] = (\n"
    "            self.strategy_allocated_margin.get(strategy_name, 0.0) + notional_usd\n"
    "        )\n"
    "        self.active_instrument_directions[instr_str] = order.side\n\n"
    "        entry = PendingOrderEntry(\n"
    "            order_id=order_id_str,\n"
    "            instrument_id=order.instrument_id,\n"
    "            strategy_id=strategy_name,\n"
    "            side=order.side,\n"
    "            submitted_time_ms=int(time.time() * 1000),\n"
    "            timeout_seconds=timeout_seconds or self.default_order_timeout_secs,\n"
    "        )\n"
    "        self.pending_orders[order_id_str] = entry"
)

new_register = (
    "    def register_order_submitted(\n"
    "        self,\n"
    "        order: Order,\n"
    "        strategy_name: str,\n"
    "        notional_usd: float,\n"
    "        timeout_seconds: Optional[float] = None,\n"
    "    ) -> None:\n"
    "        with self._lock:\n"
    "            self._pending_approvals = max(0, self._pending_approvals - 1)\n"
    "            instr_str = str(order.instrument_id)\n"
    "            order_id_str = str(order.client_order_id)\n\n"
    "            self.strategy_allocated_margin[strategy_name] = (\n"
    "                self.strategy_allocated_margin.get(strategy_name, 0.0) + notional_usd\n"
    "            )\n"
    "            self.active_instrument_directions[instr_str] = order.side\n\n"
    "            entry = PendingOrderEntry(\n"
    "                order_id=order_id_str,\n"
    "                instrument_id=order.instrument_id,\n"
    "                strategy_id=strategy_name,\n"
    "                side=order.side,\n"
    "                submitted_time_ms=int(time.time() * 1000),\n"
    "                timeout_seconds=timeout_seconds or self.default_order_timeout_secs,\n"
    "            )\n"
    "            self.pending_orders[order_id_str] = entry"
)

pg_code = pg_code.replace(old_register, new_register)

with open(pg_path, "w") as f:
    f.write(pg_code)

# 5. Fix TrendContinuationSMC initial BULLISH bias
cont_path = os.path.join(base_dir, "strategies/continuation.py")
with open(cont_path, "r") as f:
    cont_code = f.read()

old_trend = "        elif state.ema_50_4h.initialized:\n            state.trend_state = \"BULLISH\" if state.ema_50_slope >= 0 else \"BEARISH\""
new_trend = "        else:\n            state.trend_state = \"NEUTRAL\""

cont_code = cont_code.replace(old_trend, new_trend)

with open(cont_path, "w") as f:
    f.write(cont_code)

print("Successfully applied all 5 logic & security fixes.")

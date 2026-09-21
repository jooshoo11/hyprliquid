import re

with open("src/execution/node_app.py", "r") as f:
    code = f.read()

new_update_dashboard = """    def update_dashboard(self) -> None:
        if self.paused:
            return
            
        table = self.query_one("#market_table", DataTable)
        
        # Stale orders cancellation logic
        stale_orders = self.runner.guard.get_stale_orders_to_cancel()
        for stale in stale_orders:
            self.log_view.write_line(f"[yellow]Cancelling stale order {stale.order_id}[/yellow]")
            try:
                from nautilus_trader.execution.messages import CancelOrder
                cancel_cmd = CancelOrder(
                    trader_id=self.runner.node.trader_id,
                    strategy_id=stale.strategy_id,
                    instrument_id=stale.instrument_id,
                    client_order_id=stale.order_id,
                )
                self.runner.node.trader.execute(cancel_cmd)
            except Exception as e:
                pass
            self.runner.guard.acknowledge_order_cancelled(stale.order_id)
            
        try:
            meta, asset_ctxs = self.runner.info_client.get_meta_and_asset_ctxs()
            ctx_map = {m.get("name"): ctx for m, ctx in zip(meta.get("universe", []), asset_ctxs)}
        except Exception:
            ctx_map = {}
            
        rows_to_add = []
        for idx, coin in enumerate(self.runner.top_coins):
            instr_id_str = f"{coin}-USD-PERP.HYPERLIQUID"
            cont_state = self.runner.continuation_strat.states.get(instr_id_str) if self.runner.continuation_strat else None
            
            ctx = ctx_map.get(coin, {})
            current_px = float(ctx.get("oraclePx", 0.0))
            prev_day_px = float(ctx.get("prevDayPx", 0.0))
            
            price_str = f"${current_px:,.4f}" if current_px else "Awaiting"
            
            gain_24h = ((current_px - prev_day_px) / prev_day_px) * 100 if prev_day_px and current_px else 0.0
            g24_color = "green" if gain_24h >= 0 else "red"
            g24_str = f"[{g24_color}]{'+' if gain_24h>=0 else ''}{gain_24h:.2f}%[/{g24_color}]"

            gain_5m_str = "[dim]-[/dim]"
            gain_30m_str = "[dim]-[/dim]"
            if cont_state and current_px:
                if cont_state.last_5m_bar:
                    open_5m = cont_state.last_5m_bar.open.as_double()
                    g5 = ((current_px - open_5m) / open_5m) * 100
                    g5_color = "green" if g5 >= 0 else "red"
                    gain_5m_str = f"[{g5_color}]{'+' if g5>=0 else ''}{g5:.2f}%[/{g5_color}]"
                
                if cont_state.recent_30m_bars:
                    open_30m = cont_state.recent_30m_bars[-1].open.as_double()
                    g30 = ((current_px - open_30m) / open_30m) * 100
                    g30_color = "green" if g30 >= 0 else "red"
                    gain_30m_str = f"[{g30_color}]{'+' if g30>=0 else ''}{g30:.2f}%[/{g30_color}]"

            trend_str = "[dim]NEUTRAL[/dim]"
            if cont_state:
                if cont_state.trend_state == "BULLISH":
                    trend_str = "[bold green]BULL (50>200)[/bold green]"
                elif cont_state.trend_state == "BEARISH":
                    trend_str = "[bold red]BEAR (50<200)[/bold red]"
                    
            zones_str = f"{len(cont_state.smc.fvg_bullish)}D / {len(cont_state.smc.fvg_bearish)}S" if cont_state and hasattr(cont_state, 'smc') else "Scanning..."
            struct_str = "Scanning..." if not cont_state else f"{cont_state.smc.market_structure[-1]}" if hasattr(cont_state, 'smc') and cont_state.smc.market_structure else "Scanning..."
            
            funding_apr = float(ctx.get("funding", 0.0)) * 365 * 100 * 24 if ctx else 0.0
            fund_color = "red" if funding_apr > 50 else ("green" if funding_apr < -50 else "yellow")
            fund_str = f"[{fund_color}]{'+' if funding_apr>0 else ''}{funding_apr:.1f}%[/{fund_color}]"
            
            pos_str = "[dim]FLAT[/dim]"
            is_active = False
            if self.runner.node and self.runner.node.trader:
                from nautilus_trader.model.identifiers import InstrumentId
                try:
                    pos = self.runner.node.trader.portfolio.position(InstrumentId.from_str(instr_id_str))
                    if pos and not pos.is_closed:
                        is_active = True
                        pos_color = "bold green" if pos.is_long else "bold red"
                        side = "LONG" if pos.is_long else "SHORT"
                        pos_str = f"[{pos_color}]{side} {pos.quantity.as_double():.3f}[/{pos_color}]"
                except Exception:
                    pass
            
            rows_to_add.append({
                "is_active": is_active,
                "coin": coin,
                "idx": idx,
                "data": (
                    Text.from_markup(f"[bold]{coin}[/bold]"),
                    Text.from_markup(price_str),
                    Text.from_markup(gain_5m_str),
                    Text.from_markup(gain_30m_str),
                    Text.from_markup(g24_str),
                    Text.from_markup(trend_str),
                    Text.from_markup(zones_str),
                    Text.from_markup(struct_str),
                    Text.from_markup(fund_str),
                    Text.from_markup(pos_str)
                )
            })

        # Sort: Active positions first, then by original rank (idx)
        rows_to_add.sort(key=lambda x: (not x["is_active"], x["idx"]))
        
        try:
            # Clear and repopulate table to respect sorting
            if table.row_count != len(rows_to_add):
                table.clear()
                for r in rows_to_add:
                    table.add_row(*r["data"], key=r["coin"])
            else:
                # Update existing cells to prevent flickering
                for row_idx, r in enumerate(rows_to_add):
                    for col_idx, val in enumerate(r["data"]):
                        table.update_cell(str(r["coin"]), table.columns[list(table.columns.keys())[col_idx]].key, val)
        except Exception:
            pass

        # --- BRIDGE LOGIC: EXPORT ACTIVE TRADES & READ COMMANDS ---
        try:
            import json, time, os
            bridge_path = os.path.join(os.getcwd(), "bridge", "active_trades.json")
            
            active_positions = []
            equity = 100.0
            if self.runner.node and self.runner.node.trader:
                equity = self.runner.node.trader.portfolio.margin_balance().as_double()
                for pos in self.runner.node.trader.portfolio.positions():
                    if not pos.is_closed:
                        active_positions.append({
                            "coin": pos.instrument_id.symbol.value.split("-")[0],
                            "instrument_id": str(pos.instrument_id),
                            "side": "LONG" if pos.is_long else "SHORT",
                            "size": pos.quantity.as_double(),
                            "entry_price": pos.avg_px.as_double() if pos.avg_px else 0.0,
                            "unrealized_pnl": pos.unrealized_pnl.as_double() if pos.unrealized_pnl else 0.0
                        })
            
            state = {
                "timestamp": time.time(),
                "equity": equity,
                "positions": active_positions
            }
            with open(bridge_path, "w") as bf:
                json.dump(state, bf, indent=2)
                
            cmds_path = os.path.join(os.getcwd(), "bridge", "ai_commands.json")
            if os.path.exists(cmds_path):
                with open(cmds_path, "r") as cf:
                    try:
                        commands = json.load(cf)
                    except json.JSONDecodeError:
                        commands = []
                
                if commands:
                    for cmd in commands:
                        if cmd.get("action") == "CLOSE_POSITION":
                            coin_target = cmd.get("coin")
                            self.log_view.write_line(f"[bold magenta]AI COMMANDED CLOSE: {coin_target} - {cmd.get('reason')}[/bold magenta]")
                            # Execute Market Close
                            from nautilus_trader.execution.messages import SubmitOrder
                            from nautilus_trader.model.identifiers import InstrumentId, ClientOrderId
                            from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
                            from nautilus_trader.model.objects import Order
                            
                            instr_id = InstrumentId.from_str(f"{coin_target}-USD-PERP.HYPERLIQUID")
                            pos = self.runner.node.trader.portfolio.position(instr_id)
                            if pos and not pos.is_closed:
                                inst = self.runner.node.cache.instrument(instr_id)
                                side = OrderSide.SELL if pos.is_long else OrderSide.BUY
                                close_cmd = SubmitOrder(
                                    trader_id=self.runner.node.trader_id,
                                    strategy_id=self.runner.continuation_strat.id,
                                    instrument_id=instr_id,
                                    command_id=ClientOrderId(f"AI-CLOSE-{int(time.time())}"),
                                    order=Order(
                                        instrument_id=instr_id,
                                        client_order_id=ClientOrderId(f"AI-CLOSE-{int(time.time())}"),
                                        strategy_id=self.runner.continuation_strat.id,
                                        side=side,
                                        order_type=OrderType.MARKET,
                                        time_in_force=TimeInForce.FOK,
                                        quantity=pos.quantity,
                                        init_id=self.runner.node.trader_id,
                                    )
                                )
                                self.runner.node.trader.execute(close_cmd)
                            
                    with open(cmds_path, "w") as cf:
                        json.dump([], cf)
        except Exception as e:
            pass"""

start_idx = code.find("    def update_dashboard(self) -> None:")
end_idx = code.find("\n\nif __name__ == \"__main__\":")
code = code[:start_idx] + new_update_dashboard + code[end_idx:]

with open("src/execution/node_app.py", "w") as f:
    f.write(code)

print("patched node_app.py update_dashboard!")

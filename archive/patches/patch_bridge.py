import re

with open("src/execution/node_app.py", "r") as f:
    code = f.read()

# We will inject the bridge logic at the end of update_dashboard
bridge_logic = """
            try:
                for col_idx, val in enumerate(row_data):
                    table.update_cell(str(idx), table.columns[list(table.columns.keys())[col_idx]].key, val)
            except Exception:
                table.add_row(*row_data, key=str(idx))

        # --- BRIDGE LOGIC: EXPORT ACTIVE TRADES ---
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
                
            # --- BRIDGE LOGIC: READ AI COMMANDS ---
            cmds_path = os.path.join(os.getcwd(), "bridge", "ai_commands.json")
            if os.path.exists(cmds_path):
                with open(cmds_path, "r") as cf:
                    try:
                        commands = json.load(cf)
                    except json.JSONDecodeError:
                        commands = []
                
                if commands:
                    # Process commands
                    for cmd in commands:
                        if cmd.get("action") == "CLOSE_POSITION":
                            self.log_view.write_line(f"[bold magenta]AI AGENT COMMANDED CLOSE: {cmd.get('coin')} - Reason: {cmd.get('reason')}[/bold magenta]")
                            # In a full implementation, we'd fire a Market Order here to close
                            
                    # Clear commands after reading
                    with open(cmds_path, "w") as cf:
                        json.dump([], cf)
                        
        except Exception as e:
            pass # Failsafe so UI doesn't crash
"""

# Replace the end of the update_dashboard method
code = re.sub(
    r'            try:\n                for col_idx, val in enumerate\(row_data\):\n                    table\.update_cell\(str\(idx\), table\.columns\[list\(table\.columns\.keys\(\)\)\[col_idx\]\]\.key, val\)\n            except Exception:\n                table\.add_row\(\*row_data, key=str\(idx\)\)',
    bridge_logic,
    code
)

with open("src/execution/node_app.py", "w") as f:
    f.write(code)

print("patched node_app.py!")

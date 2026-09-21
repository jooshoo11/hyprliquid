with open("src/execution/node_app.py", "r") as f:
    code = f.read()

code = code.replace(
    "equity = self.runner.node.portfolio.margin_balance().as_double()",
    """try:
                    equity = self.runner.node.portfolio.account(AccountId("HYPERLIQUID-MARGIN")).margin_balance().as_double()
                except Exception:
                    equity = 100.0"""
)
code = code.replace(
    "pos = self.runner.node.portfolio.position(InstrumentId.from_str(instr_id_str))",
    """try:
                    pos = self.runner.node.portfolio.position(InstrumentId.from_str(instr_id_str))
                except Exception:
                    pos = None"""
)
code = code.replace(
    "for pos in self.runner.node.portfolio.positions():",
    """try:
                    positions = self.runner.node.portfolio.positions()
                except Exception:
                    positions = []
                for pos in positions:"""
)

with open("src/execution/node_app.py", "w") as f:
    f.write(code)

print("patched!")

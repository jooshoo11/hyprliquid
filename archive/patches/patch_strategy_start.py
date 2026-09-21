import re

with open("src/execution/node_app.py", "r") as f:
    code = f.read()

old_thread = """
        # Start background nautilus node
        import threading
        self.node_thread = threading.Thread(target=self.runner.node.run, daemon=True)
        self.node_thread.start()
        self.log_view.write_line("[bold green]TradingNode started in background thread.[/bold green]")
"""

new_thread = """
        # Start background nautilus node
        import threading
        self.node_thread = threading.Thread(target=self.runner.node.run, daemon=True)
        self.node_thread.start()
        
        def start_strats():
            import time
            time.sleep(3)
            self.runner.node.trader.start_strategy(self.runner.continuation_strat)
            self.runner.node.trader.start_strategy(self.runner.funding_strat)
            self.runner.node.trader.start_strategy(self.runner.scalp_strat)
            self.runner.node.trader.start_strategy(self.runner.vwap_strat)
            self.call_from_thread(self.log_view.write_line, "[bold green]All strategies explicitly started![/bold green]")
            
        threading.Thread(target=start_strats, daemon=True).start()
        self.log_view.write_line("[bold green]TradingNode started in background thread.[/bold green]")
"""

code = code.replace(old_thread, new_thread)

with open("src/execution/node_app.py", "w") as f:
    f.write(code)

print("patched!")

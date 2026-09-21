import re

with open("src/execution/node_app.py", "r") as f:
    code = f.read()

old_start = """
        # Start background nautilus node
        self.runner.node.start()
        self.log_view.write_line("[bold green]TradingNode started asynchronously.[/bold green]")
"""

new_start = """
        # Start background nautilus node
        import threading
        self.node_thread = threading.Thread(target=self.runner.node.run, daemon=True)
        self.node_thread.start()
        self.log_view.write_line("[bold green]TradingNode started in background thread.[/bold green]")
"""

code = code.replace(old_start, new_start)

with open("src/execution/node_app.py", "w") as f:
    f.write(code)
print("node app patched!")

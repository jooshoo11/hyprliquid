with open("src/execution/node_app.py", "r") as f:
    code = f.read()

code = code.replace("self.runner.node.portfolio.position(", "self.runner.node.cache.position(")
code = code.replace("self.runner.node.portfolio.positions()", "self.runner.node.cache.positions_open()")

with open("src/execution/node_app.py", "w") as f:
    f.write(code)

print("patched!")

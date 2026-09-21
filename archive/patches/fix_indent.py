import re

with open("src/execution/node_app.py", "r") as f:
    code = f.read()

bad_block = """                try:
                    try:
                    pos = self.runner.node.cache.position(InstrumentId.from_str(instr_id_str))
                except Exception:
                    pos = None
                    if pos and not pos.is_closed:"""

good_block = """                try:
                    pos = self.runner.node.cache.position(InstrumentId.from_str(instr_id_str))
                    if pos and not pos.is_closed:"""

code = code.replace(bad_block, good_block)

with open("src/execution/node_app.py", "w") as f:
    f.write(code)

print("fixed indentation!")

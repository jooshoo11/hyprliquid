with open("src/strategies/orderbook_scalp.py", "r") as f:
    code = f.read()

import re
code = re.sub(
    r'                if reason != "SILENT_BLOCK":\n                    # self\.log\.warning.*?\n',
    r'                pass\n',
    code
)

with open("src/strategies/orderbook_scalp.py", "w") as f:
    f.write(code)

print("patched!")

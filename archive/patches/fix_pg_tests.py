import os

pg_test_path = "/home/jooshoo/Desktop/hyprliquid/tests/test_portfolio_guard.py"
with open(pg_test_path, "r") as f:
    code = f.read()

import re
# The mess looks like: can_open, _ = guard._pending_approvals = 0; can_trade, reason = guard.can_open_position(...)
code = re.sub(r"([a-zA-Z0-9_, ]+)= guard\._pending_approvals = 0; can_trade, reason = guard\.can_open_position", 
              r"guard._pending_approvals = 0\n    \1= guard.can_open_position", code)

# Let's fix where we did this earlier
code = re.sub(r"guard\._pending_approvals = 0; can_trade, reason = guard\.can_open_position", 
              r"guard.can_open_position", code)

with open(pg_test_path, "w") as f:
    f.write(code)

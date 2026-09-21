import os

cont_path = "/home/jooshoo/Desktop/hyprliquid/src/strategies/continuation.py"
with open(cont_path, "r") as f:
    cont_code = f.read()

# 1. Any
if "from typing import " in cont_code and "Any" not in cont_code:
    cont_code = cont_code.replace("from typing import ", "from typing import Any, ")

# 2. Bullish Bias
old_trend = "        elif state.ema_50_4h.initialized:\n            state.trend_state = \"BULLISH\" if state.ema_50_slope >= 0 else \"BEARISH\""
new_trend = "        else:\n            state.trend_state = \"NEUTRAL\""
cont_code = cont_code.replace(old_trend, new_trend)

# 3. HighLevel -> Level
old_high_level = "        last_high = swing_data['HighLevel'].last_valid_index()\n        last_low = swing_data['LowLevel'].last_valid_index()\n        \n        if last_high is not None:\n            state.recent_swing_high = swing_data.loc[last_high, 'HighLevel']\n            state.recent_swing_wick_high = df.loc[last_high, 'high']\n        \n        if last_low is not None:\n            state.recent_swing_low = swing_data.loc[last_low, 'LowLevel']\n            state.recent_swing_wick_low = df.loc[last_low, 'low']"
new_high_level = "        highs = swing_data[swing_data['HighLow'] == 1]\n        lows = swing_data[swing_data['HighLow'] == -1]\n        \n        last_high = highs.last_valid_index() if not highs.empty else None\n        last_low = lows.last_valid_index() if not lows.empty else None\n        \n        if last_high is not None:\n            state.recent_swing_high = highs.loc[last_high, 'Level']\n            state.recent_swing_wick_high = df.loc[last_high, 'high']\n        \n        if last_low is not None:\n            state.recent_swing_low = lows.loc[last_low, 'Level']\n            state.recent_swing_wick_low = df.loc[last_low, 'low']"
cont_code = cont_code.replace(old_high_level, new_high_level)

with open(cont_path, "w") as f:
    f.write(cont_code)

print("Continuation fixed!")
